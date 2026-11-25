# Load management: keep the main fuse from tripping

On 2026-11-23 one phase of the 35A main fuse tripped: the Tesla, the water
heater and the spa together drew over 40A on that phase. evcc now throttles the
Tesla so the house stays under the limit.

## How it works

```
IoTaWatt (LAN) --10s--> emoncms --HTTP, in-cluster--> evcc circuit "main"
                                                         |
                                       Tesla Fleet API <-+ set charge amps
```

- An evcc **circuit** has `maxCurrent: 32` (35A fuse, minus a margin for
  measurement lag and the W-to-A approximation) and a **meter** that reports the
  whole house's per-phase current, including the car.
- Each cycle (60s) evcc caps the Tesla's current so the highest phase stays
  under 32A. If there isn't room even for the car's minimum current (6A), evcc
  pauses charging and resumes when there is.
- **Fail-safe:** if the meter errors for longer than the circuit `timeout`
  (default 1 minute), evcc sets the circuit's current to MaxFloat64 and allows
  0A, so charging stops (`core/circuit/circuit.go`, `overloadOnError`). The
  `jq` below turns emoncms data older than 60s into an error, so a silent
  IoTaWatt stops charging after about 2 minutes instead of evcc running on an
  old reading.
- Response time is roughly 10s (IoTaWatt post) + 60s (evcc cycle) + a few
  seconds (Tesla command). A 35A fuse at 40–45A trips thermally in minutes, so
  that is fast enough. A short spike is not what tripped it.

## evcc configuration

The meter and circuit live in `evcc.yaml` inside `evcc/secret.sops.yaml`
(`sops evcc/secret.sops.yaml`), because the meter URLs carry the emoncms read
API key.

### emoncms feeds

IoTaWatt posts every 10s, all values in watts except the voltage.

| Feed | Input | Meaning |
|---|---|---|
| 17 | `sync` | IoTaWatt reference voltage (226–228 V) |
| 18, 19, 20 | `L1`, `L2`, `L3` | Mains per phase |
| 29 | `autoL1` | Tesla (CT on L1 only; it charges on 3 phases) |
| 28 | `poreallasL1` | Spa |
| 26 | `lvvL1` | Water heater, switches on at 02:00 every night |
| 27 | `lattiaETL1` | Floor heating ET |
| 24 | `lattiaSaunaL1` | Floor heating sauna |
| 21, 30 | `ILPL3`, `ILP2L2` | Heat pumps |
| 22, 23, 25 | `patteriOHL2`, `lattiaWCl3`, `patteriTKL3` | Other heaters |

Every heavy load sits on L1, which is why L1 is the phase that trips. On
2026-11-25 at 21:14 the floor heating switched on while the car and spa were
running, and L1 held 8.76 kW (38.7 A) for over 10 minutes.

### Meter and circuit

Every reading uses `feed/list.json`, which returns all feeds with their latest
`time` and `value` in one response. Current per phase is the phase's watts
divided by the measured voltage (feed 17). The `fabs` keeps a CT mounted the
wrong way round from hiding load. Data older than 60s becomes an error, which
evcc turns into "circuit overloaded" after its 1-minute timeout, so charging
stops.

```yaml
meters:
  - name: house
    type: custom
    power:
      source: http
      uri: http://emoncms.arska-teslacontrol.svc.cluster.local:8080/feed/list.json?apikey=<READ_KEY>
      jq: '(map({key: .id, value: .}) | from_entries) as $f | if (now - $f["18"].time) > 60 then error("emoncms feeds stale") else ($f["18"].value + $f["19"].value + $f["20"].value) end'
    currents:   # same uri, feed 18 / 19 / 20 divided by feed 17
      - source: http
        uri: http://emoncms.arska-teslacontrol.svc.cluster.local:8080/feed/list.json?apikey=<READ_KEY>
        jq: '(map({key: .id, value: .}) | from_entries) as $f | if (now - $f["18"].time) > 60 then error("emoncms feeds stale") else ($f["18"].value / $f["17"].value | fabs) end'
      # ... L2 with $f["19"], L3 with $f["20"]

circuits:
  - name: main
    title: Hauptsicherung
    maxCurrent: 32
    meter: house
```

The URL goes to the emoncms Service directly, so it bypasses oauth2-proxy and
works over plain HTTP inside the cluster. The read key is `users.apikey_read`
in the emoncms database.

**The loadpoint must reference the circuit.** The Tesla loadpoint is configured
in the evcc UI (stored in evcc's database), so select circuit `main` in its
settings there. evcc does not assign loadpoints to the root circuit by itself
(`core/loadpoint.go`, `CircuitRef`). Without that, the circuit only measures
and nothing is throttled, while `EvccCircuitMissing` stays silent because the
circuit exists.

Notes:

- `interval: 60s` (top of `evcc.yaml`) sets how often evcc reads the meter and
  the car. Each cycle costs one billed Tesla `vehicle_data` call, because the
  Vehicle API-only charger reads its status from the car.
- Every change of charge current is a paid Fleet API command (about €0.001).

## Checking it works

1. evcc UI → the loadpoint shows the circuit, and the evcc log shows
   `circuit` lines with `current: ...A` at debug level.
2. `curl -s http://evcc...:7070/api/state | jq .circuits` (from a pod in the
   namespace) shows `main` with `current` close to the IoTaWatt reading.
3. Prometheus has `evcc_circuit_current{circuit="main"}`.
4. While charging, switch on the water heater: within about a minute the
   Tesla's charge current drops.

## Alerts

In `monitoring/prometheusrule.yaml`, fed by `evcc_circuit_*` from
json_exporter (`monitoring/json-exporter.yaml`):

- `EvccCircuitOverCurrent` (critical): over `maxCurrent` for 2 minutes. evcc
  couldn't fix it: the car didn't respond, or the spa and water heater alone are
  over the limit.
- `EvccCircuitMeterStale`: evcc has no fresh reading and has stopped charging.
- `EvccCircuitMissing`: evcc has no current-limited circuit at all, for example
  after a config change lost it.
