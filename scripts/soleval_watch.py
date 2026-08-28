#!/usr/bin/env python3
"""Check apartment availability at Soleval Lenzerheide (soleval.ch).

soleval.ch itself is a brochure site; bookings run through a hosted GMS booking
engine at gob6.gms.info, hotel_id=91. This script drives that engine directly.

Usage:
    python3 scripts/soleval_watch.py 20.02.2027

Takes the arrival date as DD.MM.YYYY, checks the Saturday-to-Saturday week
starting on it, and prints one line per free apartment on stdout. A summary
goes to stderr so stdout stays machine-readable.

Exit codes:
    0  the check ran and the answer is trustworthy (zero free units included)
    1  the check is blind: the engine changed shape, or it could not be reached

Two quirks of this engine, verified 28.08.2026, worth not re-deriving:

  * The occupancy filter is advisory. The engine returns every free unit for
    the week regardless of the search[adults] value, so a single query with
    adults=2 covers all 98 apartments in every size class. There is no need to
    loop over party sizes.

  * Winter weeks are strictly Saturday to Saturday. An arrival date on any
    other weekday returns zero offers even when the house is half empty, so a
    zero result on a non-Saturday says nothing about availability.
"""

import html
import http.cookiejar
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

BASE = "https://gob6.gms.info/soleval/de"
SEARCH_URL = BASE + "/search"
ROOMS_URL = BASE + "/rooms"
# Where the engine sends you when the search matched nothing at all.
ALTERNATIVE_PATH = "/alternative/rooms"
HOTEL_ID = "91"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)

# Each offer on the results page is a block opening with id="room-N".
ROOM_SPLIT_RE = re.compile(r'id="room-\d+"')
# "rooms-wrapper" is the container the offers are rendered into. It is what
# tells a real (possibly empty) result page apart from an error page.
RESULTS_MARKER = "rooms-wrapper"

TOKEN_RE = re.compile(r'<input[^>]*\bname="_token"[^>]*\bvalue="([^"]*)"[^>]*>')
SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1>", re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")

NAME_RE = re.compile(r"\d+(?:½)?-\s*Zimmer-Wohnung Nr\.?\s*[0-9A-Za-z]+")
PRICE_RE = re.compile(r"ab\s*CHF\s*([\d.,']+)")
OCCUPANCY_RE = re.compile(r"min\.\s*(\d+)\s*/\s*max\.\s*(\d+)")
HOUSE_RE = re.compile(r"im\s+(Haus\s+\w+)")
# A few units are listed with a half metre ("ca. 46,5 m2"), so the size is
# not always an integer.
SIZE_RE = re.compile(r"ca\.\s*(\d+(?:[.,]\d+)?)\s*m")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Turn the 302 off the POST into an HTTPError instead of following it.

    The POST stores the search server-side against the session cookie and then
    redirects; following it would just re-enter the search form. The results
    live at /rooms, which we fetch ourselves.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def build_opener():
    jar = http.cookiejar.CookieJar()
    # HTTPCookieProcessor (handler_order 500) runs before HTTPErrorProcessor
    # (1000), so the session cookie is still recorded when the POST's 302 is
    # raised as an error.
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar), _NoRedirect()
    )
    opener.addheaders = [
        ("User-Agent", USER_AGENT),
        ("Accept", "text/html,application/xhtml+xml"),
        ("Accept-Language", "de-CH,de;q=0.9"),
    ]
    return opener


def fetch(opener, url, data=None):
    """Return (status, location, body). Redirects are reported, never followed."""
    req = urllib.request.Request(url, data=data)
    if data is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("Referer", SEARCH_URL)
    try:
        with opener.open(req, timeout=60) as resp:
            return resp.status, "", resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return exc.code, exc.headers.get("Location", ""), body


def normalise_price(raw):
    """'1.288,00' -> "1'288"; the engine prints German, Soleval quotes Swiss."""
    integer, _, cents = raw.rpartition(",")
    if not integer:  # no decimal part at all
        integer, cents = raw, ""
    digits = re.sub(r"\D", "", integer)
    if not digits:
        return raw
    grouped = f"{int(digits):,}".replace(",", "'")
    if cents and cents.strip("0"):
        return f"{grouped}.{cents}"
    return grouped


def parse_offers(page):
    """Pull one record per offer block out of the results page."""
    offers = []
    seen = set()
    for block in ROOM_SPLIT_RE.split(page)[1:]:
        text = SCRIPT_RE.sub(" ", block)
        text = html.unescape(TAG_RE.sub(" ", text))
        text = re.sub(r"\s+", " ", text).strip()

        name = NAME_RE.search(text)
        if not name:
            continue
        name = re.sub(r"\s+", " ", name.group(0))
        if name in seen:
            continue
        seen.add(name)

        price = PRICE_RE.search(text)
        occupancy = OCCUPANCY_RE.search(text)
        house = HOUSE_RE.search(text)
        size = SIZE_RE.search(text)
        offers.append(
            {
                "name": name,
                "house": house.group(1) if house else "?",
                "size": size.group(1) if size else None,
                "min": occupancy.group(1) if occupancy else None,
                "max": occupancy.group(2) if occupancy else None,
                "price": normalise_price(price.group(1)) if price else None,
            }
        )
    return offers


def sort_key(offer):
    rooms = re.match(r"(\d+)(½)?", offer["name"])
    number = re.search(r"Nr\.?\s*([0-9A-Za-z]+)$", offer["name"])
    digits = re.sub(r"\D", "", number.group(1)) if number else ""
    return (
        int(rooms.group(1)) if rooms else 0,
        bool(rooms and rooms.group(2)),
        int(digits) if digits else 0,
        offer["name"],
    )


def check(arrival):
    departure = arrival + timedelta(days=7)
    opener = build_opener()

    # 1. GET the search form: this hands out the session cookie and the CSRF
    #    token that the POST below has to echo back.
    _, _, form = fetch(opener, SEARCH_URL)
    token = TOKEN_RE.search(form)
    if not token:
        print(
            "FEHLER: kein <input name=\"_token\"> auf der Suchseite - "
            "die Buchungsmaschine hat sich geaendert.",
            file=sys.stderr,
        )
        return None
    token = token.group(1)

    # 2. POST the search. The engine keeps the criteria in the session; the
    #    response is a 302 we deliberately do not follow.
    payload = urllib.parse.urlencode(
        {
            "_token": token,
            "typ": "loadSearch",
            "step": "loadSearch",
            "default_language": "de",
            "hotel_id": HOTEL_ID,
            "isaktiv": "1",
            "arrangement": "0",
            "dateFormat": "d.m.Y",
            "ui_calendar": "1",
            "search[adults]": "2",  # advisory only, see module docstring
            "search[date_from]": arrival.strftime("%d.%m.%Y"),
            "search[date_to]": departure.strftime("%d.%m.%Y"),
            "search[kat_id]": "",
            "search[arrangement_id]": "",
            "search[filter_id]": "",
            "search[rule_id]": "",
            "search[rabattcode]": "",
        }
    ).encode()
    fetch(opener, SEARCH_URL, data=payload)

    # 3. GET the results for the search we just stored. Two answers are
    #    legitimate here, and telling them apart is the whole point:
    #
    #      * 200 with a rooms-wrapper -> the offer list, possibly a short one.
    #      * 302 to /alternative/rooms -> the engine found nothing for this
    #        week and is offering other dates instead. Verified 28.08.2026 on
    #        25.12.2027 and 20.02.2032 (both Saturdays outside the open
    #        calendar). This is a real "fully booked", not a broken scrape, so
    #        it must not be reported as a failure - otherwise the watcher would
    #        cry wolf on exactly the days the answer is "still nothing".
    #
    #    Anything else means the engine changed shape and we are blind.
    status, location, rooms = fetch(opener, ROOMS_URL)
    if 300 <= status < 400:
        if ALTERNATIVE_PATH in location:
            return []
        print(
            f"FEHLER: /rooms verweist unerwartet auf '{location}' - "
            "die Buchungsmaschine hat sich geaendert.",
            file=sys.stderr,
        )
        return None
    if status != 200 or RESULTS_MARKER not in rooms:
        print(
            f"FEHLER: HTTP {status} und '{RESULTS_MARKER}' fehlt auf der "
            "Ergebnisseite - die Buchungsmaschine hat sich geaendert.",
            file=sys.stderr,
        )
        return None

    return sorted(parse_offers(rooms), key=sort_key)


def format_offer(offer):
    parts = [offer["name"], offer["house"]]
    parts.append(f"{offer['size']} m2" if offer["size"] else "? m2")
    parts.append(f"max {offer['max']}" if offer["max"] else "max ?")
    parts.append(f"CHF {offer['price']}" if offer["price"] else "CHF ?")
    return " | ".join(parts)


def main(argv):
    if len(argv) != 2:
        print(f"Usage: {argv[0]} DD.MM.YYYY", file=sys.stderr)
        return 1
    try:
        arrival = datetime.strptime(argv[1], "%d.%m.%Y")
    except ValueError:
        print(f"FEHLER: '{argv[1]}' ist kein Datum im Format DD.MM.YYYY", file=sys.stderr)
        return 1

    departure = arrival + timedelta(days=7)
    if arrival.weekday() != 5:
        # Not fatal, but a zero result here means nothing. See module docstring.
        print(
            f"WARNUNG: {argv[1]} ist kein Samstag. Im Winter laeuft Soleval "
            "strikt Samstag bis Samstag; ein leeres Ergebnis ist dann "
            "aussagelos.",
            file=sys.stderr,
        )

    try:
        offers = check(arrival)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        print(f"FEHLER: Buchungsmaschine nicht erreichbar: {exc}", file=sys.stderr)
        return 1

    if offers is None:
        return 1

    for offer in offers:
        print(format_offer(offer))

    window = f"{arrival:%d.%m.%Y} - {departure:%d.%m.%Y}"
    if offers:
        print(f"{len(offers)} freie Wohnung(en) fuer {window}", file=sys.stderr)
    else:
        print(f"Keine freien Wohnungen fuer {window} (ausgebucht)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
