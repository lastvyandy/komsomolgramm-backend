# One-time photos — Android 1.9.2 / code 17

In the photo editor, toggle the circled 1 next to Send. It is off for every new
photo and shows a highlighted state and explanation when selected. Chat cards show
no image preview; recipients tap to open a separate full-screen viewer. Closing,
backgrounding or recreating that viewer ends the view. Android FLAG_SECURE is applied
to the viewer window to block normal screenshots, screen recording and task previews.
This cannot prevent an external camera or a compromised device from capturing content.

Only still JPEG/PNG/WebP photos are supported, not video, voice, GIF or stickers.
The receiver gets bytes into memory only; they do not go through the normal disk,
thumbnail, media gallery, auto-download, export or forwarding paths. The sender's
persisted outbox copy is removed after confirmation. A source photo already in the
sender's gallery is not deleted. Normal photo sending and the editor remain available.

POST /api/v1/dispatches/{chat}/view-once-photo is the client upload route.
A server without this route rejects the upload instead of saving it as a normal photo.
The common attachment handler also accepts view_once=true. The metadata
contains view_once and opened. Each original recipient can POST .../{id}/open-once
only once. PostgreSQL locks and a conditional recipient update claim access before
the response is sent. It is per account, not per device. Once the last intended
recipient claims it, server bytes are discarded. A lost response still consumes the
opening: retries cannot issue a second copy. New group members cannot open it.

The existing 24-hour relay expiry remains in force for unopened photos, unlike
WhatsApp's 14-day unopened-media window. No permanent server-media storage is added.
Auto-delete may remove the message earlier according to its assigned timer.
Opened receipts are private and respect the recipient's read-receipt audience;
recipients always see their own opened state. Receipt events are filtered in the
socket manager and synchronize through history after reconnecting.

Ordinary attachment downloads, confirmation calls and gallery listings reject or
exclude one-time media. Forwarding is rejected on the server and hidden in the UI.
Old clients must update to display these cards correctly; they cannot bypass the
special opening endpoint via normal downloads. No E2EE or secure-erasure guarantee
is introduced by this feature.

Deploy backend before Android. Startup adds dispatch_attachments.view_once BOOLEAN
DEFAULT false. Existing media retains its previous behavior. Build and publish on
the existing signing PC using code 17 / name 1.9.2; check release codes first.

Research: WhatsApp Help Center describes the circled 1 before Send, no gallery copy,
no forwarding, opening once, opened receipts conditional on read receipts, and a
14-day window for unopened media:
https://faq.whatsapp.com/578442220724722/
https://faq.whatsapp.com/1077018839582332

Device checks still required: secure viewer on Android versions used by friends,
background/rotation behavior, opening between two phones, interrupted transfer,
and one open per recipient in a group. Backend tests use isolated databases and
verify routing, permissions, expiry, receipts and payload retention rules.
