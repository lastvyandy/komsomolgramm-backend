# Voice calls and automatic deletion

## Auto-delete

Each member has their own policy for each chat. Durations: off (0), or 600–315360000
seconds. Android offers 1 day, 1 week, 30 days, or a custom number of minutes.
For-me hides new incoming and outgoing messages only for that member. For-everyone
purges only new messages authored by the member who enabled it. Channel subscribers
cannot enable global deletion. Policies do not change deadlines already assigned.
No old history is deleted when enabling a policy.

Deadlines are inserted in the same SQL transaction as each new message, including
HTTP, legacy WebSocket, attachments and forwards. The API worker processes durable
deadlines every five seconds; restarts do not lose them. Global deletion removes
message content/rich content and temporary attachment bytes, retaining a tombstone.
Private deletion preserves the other members' copy. It is not a secure erasure
promise for backups, exports or screenshots.

Android fetches its private and global deadlines in pages. Deadlines are cached in
SQLite and checked each second while a chat is visible, even without network. On
opening another chat or reconnecting it fetches all its deadlines, including already
expired ones, to remove old cached pages and local media. Local deletion tombstones
prevent history synchronization from restoring them. When the app is stopped, the
server continues; local cleanup occurs when the app is used again. Older clients
need updating for reliable removal of cached history.

Routes: GET/PUT /api/v1/auto-delete/{chat}; GET .../{chat}/deadlines?after=UUID.

## Voice calls

One-to-one, audio only. Android uses WebRTC (io.github.webrtc-sdk:android:144.7559.15).
HTTP coordinates SDP/ICE, acceptance, rejection and hangup. Audio is not uploaded as
chat media or recorded. The server maintains call history and clears SDP/ICE after
termination or timeout. Only the two authenticated chat members can access signaling.
Call state is polled every second during a call, including in the foreground service;
foreground idle discovery is every three seconds. FCM wakes a notification for an
incoming call when the UI is absent. Tapping opens the incoming screen; Android may
require microphone permission. There is no silent microphone start, full-screen
notification bypass, video calling, group calling or automatic call recovery after
process death.

The active call runs with a microphone foreground service and an ongoing hangup
notification. Ringing expires after 60 seconds. Active calls expire after 60 seconds
without signaling heartbeats, or after four hours. Muting and speaker controls are
in the call screen. Chat header has a call button and overflow has call history.
Bluetooth route selection is left to Android. Physical phone testing remains required
for NAT traversal, ringing, speaker routing and manufacturer background restrictions.

## VPS setup (before distributing Android code 15)

Keep all existing Compose files and volumes. Add these to the existing .env once:

- CALL_TURN_HOST: public DNS name or public IPv4 of this VPS (without https://).
- CALL_TURN_EXTERNAL_IP: public VPS IPv4.
- CALL_TURN_SECRET: a long random secret generated on the server, never in Git.

The API returns short-lived TURN credentials derived from that secret; the shared
secret is not embedded in the APK. The same secret is supplied to coturn by Compose.

From /opt/komsomolgram-backend:

```bash
git pull --ff-only origin main
# The helper preserves existing settings and prints no secret:
python3 tools/configure-calls.py --host komsomolgram.46-173-218-131.sslip.io --external-ip 46.173.218.131
# If the VPS address changes, use its actual DNS name and public IPv4 instead.
docker compose -f compose.vps.yml -f compose.https.yml -f compose.push.yml -f compose.stickers.yml -f compose.calls.yml up -d --build
docker compose -f compose.vps.yml -f compose.https.yml -f compose.push.yml -f compose.stickers.yml -f compose.calls.yml ps
```

Allow 3478 TCP/UDP and 49160–49200 UDP in the VPS and provider firewalls. Coturn uses
host networking on Linux. Do not expose PostgreSQL or Redis for calls. The API's
existing HTTPS port remains unchanged. TURN relays audio when direct connectivity
fails, so calls use VPS bandwidth. Deploying without the calls overlay keeps messaging
and auto-delete working, but /calls/ice returns 503 and new calls are disabled with
an error instead of claiming a working connection. TCP 3478 is a fallback, but a
network allowing only HTTPS 443 may still block this initial TURN deployment.

New tables are created by existing Base.metadata.create_all at API startup. No data
reset is added. The historical one-time test-media reset in main.py is unchanged.

Build on the user's Windows PC with the same existing signing identity:

```powershell
Set-Location 'C:\Users\corpo\AndroidStudioProjects\Komsomolgram'
git pull --ff-only origin master
powershell -ExecutionPolicy Bypass -File .\tools\publish-update.ps1 -VersionCode 15 -VersionName 1.9.0
```

Verify current releases first; code 15 must not already be published. APKs built by
the assistant use a different debug signing identity and must not replace testers'
installed apps. Publishing and VPS deployment are distinct from pushing source.
