# Location picker and Telegram sticker import (Android 1.9.3 / 18)

## Location

The full-screen picker uses a map above a rounded action panel. Users can send
their current GPS fix (with its accuracy), select a point on the map, or explicitly
search for an address/place and select a result before sending. These are static
locations, not continuous live-location sharing.

Search uses Android's available Geocoder first. If unavailable or empty, the app
calls the authenticated `POST /api/v1/locations/search` endpoint. The fallback uses
Nominatim: Redis caches results for one hour and shares a global gate across API
workers allowing at most one upstream request per 1.1 seconds. There is no search
autocomplete. Coordinates used to bias fallback results are rounded to 0.1 degree;
no account credentials are forwarded upstream. Map attribution remains visible.
Redis must be available for fallback search. See the
[Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/).

## Telegram stickers

Import uses the official Telegram Bot API, so the server administrator needs a
valid BotFather token. This is an application server secret, not a token that each
user should enter. Import supports bare pack names, `https://t.me/addstickers/NAME`,
`https://telegram.me/addstickers/NAME` and `tg://addstickers?set=NAME`.

The app checks authenticated `GET /api/v1/stickers/telegram/status` before import.
`configured: true` means a nonempty token exists; it does not verify the token
with Telegram. Actual import distinguishes missing configuration (503), invalid
bot configuration (424), missing pack (404), rate limits (429) and upstream errors.
The Android import client has longer timeouts than the server requests, downloads
up to three stickers concurrently, and retries transient download failures.
Temporary files are removed on failure. Existing pack and size limits still apply.

Animated/video Telegram stickers currently use their static thumbnail previews.
They do not retain their Telegram animation.

### Configure on the VPS

From the existing backend checkout, with the existing `.env` present:

```sh
git pull --ff-only origin main
python3 tools/configure-stickers.py
docker compose -f docker-compose.yml -f compose.stickers.yml up -d --build komsomol_api
```

The helper requests the token using hidden input, verifies it through Telegram's
`getMe`, then atomically updates only `TELEGRAM_STICKER_BOT_TOKEN` in `.env`.
It does not print the token. If your deployment also uses push/calls overrides,
retain those files in your existing Compose invocation alongside
`compose.stickers.yml`. Do not paste the token into chat or commit `.env`.

Updating source does not automatically deploy the VPS or publish a signed Android
release. The Android and backend changes should both be installed. On a device,
check permission denial/retry, approximate GPS, place search, manual pin sending,
and import of both a static Telegram pack and an animated pack's previews.

## Validation

`tests/check_locations.py` covers authentication, input validation, coarse search
bounds, caching, global rate limiting and malformed upstream data.
`tests/check_sticker_import.py` covers links, configuration/error statuses,
owner-bound expiring tickets and valid Telegram file paths beyond old directory
assumptions. Network responses in these tests are mocked; a real bot import and
the on-device layout still require deployment/device verification.
