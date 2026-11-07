# Android push notifications

## Android

1. Create a Firebase project and register Android package `ru.komsomol.gram`.
2. Download its client configuration to `app/google-services.json` in the Android repository.
3. Rebuild and install the APK. Sign in and allow Android notification permission.
   The Google Services plugin applies automatically when this file is present.
4. Phones need compatible Google Play services. Without Firebase configuration,
   Android notifications can only be delivered by an existing live chat connection.

## VPS

Create a Firebase Admin service-account key for the same project. Store it outside
the repository at `/opt/komsomolgram-secrets/firebase-admin.json`, readable only by
the server administrator. Never put this private key in the APK or commit it.

Add to the VPS `.env`:

```dotenv
FIREBASE_CREDENTIALS=/run/secrets/firebase-admin.json
```

Add a deployment overlay named `compose.push.yml`:

```yaml
services:
  komsomol_api:
    volumes:
      - /opt/komsomolgram-secrets/firebase-admin.json:/run/secrets/firebase-admin.json:ro
```

From the backend directory, deploy with all the existing overlays:

```bash
git pull
docker compose -f compose.vps.yml -f compose.https.yml -f compose.push.yml up -d --build
```

The new device table is created on startup. Reopen the updated Android app after
deploying so it registers its device token.

## Check delivery

Use two accounts on two phones. Put the receiving app in the background and send
text, a photo and a voice message from the other account. Check the custom sound,
tap-to-open, muted chats and logout. Repeat with the receiving process removed
from recents. Android force-stop blocks delivery until the app is reopened.

The push payload contains message, sender, recipient and chat identifiers only.
Message content is not sent to Firebase. The notification uses a generic preview.
