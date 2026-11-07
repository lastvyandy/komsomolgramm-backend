# Attachment menu and structured chat messages

The clip opens Gallery, Camera, Location, Contact, Document, Poll and Event.
Labels and forms are available in Russian and Spanish.

## Android behavior

- Gallery uses Android's photo picker and falls back through its ActivityResult contract.
- Camera opens the installed camera app and passes a scoped FileProvider output URI.
  A captured photo goes through the existing full-screen editor.
- Location requests foreground precise/approximate permission only when chosen.
  A recent device fix or one-shot network/GPS request supplies coordinates. A user can
  also enter coordinates manually and preview them in a map before sending.
  This is a static point, not continuous live tracking.
- A location card first tries Yandex Maps, then another installed map application,
  then the Yandex Maps website. No Google Play Services, map SDK/API key or server map
  image storage is required.
- Contact opens Android's phone-number picker, which grants access only to the selected
  record. The user sees and confirms the name and phone before sharing. Manual entry
  is available if there is no contact picker. A received card opens Android's add-contact form.
- Documents use the system file picker, existing durable local media storage and temporary
  delivery relay. The existing 20 MiB per-file limit still applies. Open-document actions use
  a scoped cache copy and a read-only URI grant.
- Polls support 2–12 distinct options, single/multiple answers, changing votes and removing
  them. Counts are refreshed every five seconds for displayed cards.
- Events include a title, local date/time, optional place and description. Responses are
  Going, Maybe and Not going. Add to calendar opens the system calendar's confirmation form;
  reminders are managed by that calendar app.

## Backend and compatibility

Dispatch.rich is a nullable JSON column. DispatchChoice stores the current selections per
message and user, with a composite primary key. Choices are replaced with an atomic upsert.
Responses are restricted to visible messages in chats the authenticated user belongs to.
Counts include current members; the sender cannot submit a vote on another person's behalf.

Rich message creation accepts a client UUID for idempotent retries. Messages retain readable
fallback text, including a Yandex link for locations, for clients without rich-card support.
History, replies and forwarding retain structured data. Forwarded polls/events start new
response sets. Structured messages cannot be changed through the plain text edit endpoint.

The startup migration adds the JSON column and creates the response table. It does not delete
messages. Deploy the backend before distributing the new Android APK:

```bash
git pull --ff-only
docker compose -f compose.vps.yml -f compose.https.yml -f compose.push.yml up -d --build
```

No binary storage policy changes: document bytes use the same delivery acknowledgments,
quota, expiration and local ownership as photos and voice messages.

## Validation

```bash
PYTHONPATH=. python tests/check_rich_messages.py
```

The HTTP checks use an isolated SQLite database. PostgreSQL-specific relay quota admission is
stubbed only in the binary document test. Membership, schema validation, idempotency, history,
socket payloads, response upserts and counters use production endpoint code.

Android device checks still required: photo picker, camera handoff, approximate location permission,
denied permission/manual coordinates, Yandex installed/absent, phone picker, calendar handoff,
opening downloaded PDFs, and poll synchronization between two accounts.

## References consulted

- WhatsApp current vs live location: https://faq.whatsapp.com/6780014865351544
- WhatsApp polls: https://faq.whatsapp.com/796470361614974/
- WhatsApp events: https://faq.whatsapp.com/3313983622238973/
- Yandex Maps launch URLs: https://yandex.com/dev/yandex-apps-launch/maps/
- Android contact/data picker intents: https://developer.android.com/guide/components/intents-common
- Android one-shot location: https://developer.android.com/reference/androidx/core/location/LocationManagerCompat

This change implements the menu and the functions above. It does not introduce live background
location sharing, AI images, in-app event reminders or event editing.
