# Tesla Fleet API Setup for Pre-2021 Model S/X

Pre-2021 Model S and Model X vehicles do not support Tesla's new signed command protocol. They use the legacy Owner API for commands, which means **no command proxy, no keypair, and no domain registration** is needed. Commands go directly through Tesla's Fleet API REST endpoints.

This guide documents the setup for evcc and teslamate with a 2015 Model S 85D on Kubernetes (APPUiO Cloud / OpenShift).

## What You Need

- A Tesla Developer Account at [developer.tesla.com](https://developer.tesla.com)
- A [myteslamate.com](https://app.myteslamate.com) account (free tier, used for OAuth token generation)
- Your Tesla account credentials

## Step 1: Register a Tesla Developer App

Follow the [myteslamate.com](https://app.myteslamate.com) instructions to register your Tesla Developer App. Their guided flow handles the domain registration and OAuth setup:

1. Go to [developer.tesla.com](https://developer.tesla.com) and sign in
2. Create a new Fleet API application
3. When asked for client details, use the URLs provided by myteslamate.com:
   - Allowed Origin: `https://app-<ID>.myteslamate.com/`
   - Redirect URI: `https://app.myteslamate.com/auth/tesla/user/callback`
   - Return URL: `https://app.myteslamate.com/`
4. Select all API scopes (Vehicle Information, Location, Commands, Charging Management, Energy)
5. Save the **Client ID** and **Client Secret**

Registering your own domain directly with Tesla's developer portal is very difficult — Tesla rejects domains containing "tesla", domains behind reverse proxies (it detects load balancer cookies like OpenShift's HAProxy session cookie), and shared platform wildcard domains. The myteslamate.com URLs (behind Cloudflare) are accepted without issues.

## Step 2: Register via myteslamate.com

myteslamate.com handles the partner registration and OAuth flow. Their `tokens.sh` script:

1. Gets a partner token via client_credentials grant
2. Registers your app with Tesla's partner accounts API (NA + EU regions)
3. Generates an authorization URL for you to log in
4. Exchanges the auth code for access + refresh tokens

```bash
# Download and review the script first
curl -fsSL https://raw.githubusercontent.com/MyTeslaMate/tesla-fleet-api-tokens/refs/tags/v0.0.12/tokens.sh -o /tmp/tokens.sh

# Run it (needs interactive terminal for prompts)
bash /tmp/tokens.sh <YOUR_MYTESLAMATE_APP_ID>
```

The script will prompt for your Client ID and Client Secret, then give you a URL to authorize. After authorization, it outputs your **access token** and **refresh token**.

**Important:** Access tokens expire after 8 hours. Enter them into evcc/teslamate immediately after generation.

## Step 3: Configure evcc

### Vehicle Settings

In the evcc web UI, add a Tesla vehicle with:
- **Client ID**: your Fleet API client ID
- **Access Token**: from the tokens.sh output
- **Refresh Token**: from the tokens.sh output

### Command Proxy (Critical for Pre-2021 Vehicles)

Set the **Command Proxy** to Tesla's Fleet API directly:

```
https://fleet-api.prd.eu.vn.cloud.tesla.com
```

**Do NOT leave it empty** — evcc defaults to myteslamate's proxy which requires a paid subscription. Pre-2021 vehicles don't need signed commands, so pointing directly at Tesla's Fleet API works.

Use the correct regional endpoint:
- **EU**: `https://fleet-api.prd.eu.vn.cloud.tesla.com`
- **NA**: `https://fleet-api.prd.na.vn.cloud.tesla.com`

### Charger Configuration

Use the **"Vehicle API-only charger"** template — this controls charging via the Tesla vehicle API (start/stop charging, set amps) rather than through a physical charger's protocol.

### Polling

evcc polls the car API every hour by default when using the Vehicle API charger. This means:
- Up to 1 hour delay before evcc detects the car is plugged in
- Workaround: set the car's built-in scheduled charging to a late time (e.g., 06:00) as a fallback, and let evcc start charging earlier during cheap hours

## Step 4: Configure teslamate

### Required Environment Variables

```yaml
# Fleet API endpoints (defaults are wrong in teslamate 4.0.1)
TESLA_API_HOST: "https://fleet-api.prd.eu.vn.cloud.tesla.com"
TESLA_AUTH_HOST: "https://auth.tesla.com"
TESLA_AUTH_PATH: "/oauth2/v3"

# Your Fleet API app credentials
TESLA_AUTH_CLIENT_ID: "<your-client-id>"
TESLA_AUTH_CLIENT_SECRET: "<your-client-secret>"

# Database (from VSHNPostgreSQL connection secret)
DATABASE_HOST: <from secret>
DATABASE_PORT: <from secret>
DATABASE_NAME: <from secret>
DATABASE_USER: <from secret>
DATABASE_PASS: <from secret>

# Teslamate-specific
ENCRYPTION_KEY: "<random-string>"
DISABLE_MQTT: "true"
```

**Important:** teslamate 4.0.1 defaults to the old `auth.tesla.com/oauth2/v3/nts/token` endpoint which returns 404. You **must** set `TESLA_API_HOST`, `TESLA_AUTH_HOST`, and `TESLA_AUTH_PATH` explicitly.

### Token Entry

Enter the access and refresh tokens via the teslamate web UI at `/sign_in`.

### Streaming API

teslamate 4.0.1 has a known bug where the streaming API tokens expire in a loop ([teslamate-org/teslamate#5428](https://github.com/teslamate-org/teslamate/issues/5428)). Data collection via polling still works — the streaming errors are noisy but non-blocking.

## What You Don't Need (Pre-2021 Vehicles)

- **tesla-http-proxy** — only needed for vehicles that require signed commands (2021+ Model S/X, all Model 3/Y)
- **Fleet API keypair** (EC prime256v1) — only needed for signed commands
- **Public key hosting** — only needed for signed commands
- **Partner accounts API registration** with your own domain — myteslamate handles this
- **Tesla Wall Connector** — the Vehicle API charger template controls charging through the car itself

## Cost

- **Tesla Fleet API**: free ($10/month credit included, more than enough for personal use)
- **myteslamate.com**: free tier (token generation only, no paid proxy needed)
- **evcc**: free / open source
- **teslamate**: free / open source
