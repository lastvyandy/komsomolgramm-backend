# Device-owned media and temporary delivery

Chat files are stored permanently in the Android app private no-backup directory. They are not evicted by Android cache cleanup. Uninstalling the app or clearing its data removes the local copies. This release does not implement phone-to-phone history migration or media backup.

## Deployment action explicitly requested for test data

On the first startup of this release, the transaction identified by `test-media-reset-local-delivery-v1` clears the binary content of ALL existing chat attachments and expires their delivery. Text messages, attachment metadata and profile photographs remain. This action is irreversible for files that do not exist on a phone. The marker in `applied_data_actions` makes the action run only once, including across API replicas.

New uploads are retained for at most 24 hours, with expiry cleanup every 30 seconds and expiry enforced by the download route. A snapshot of recipient accounts is created at upload. After every recipient account acknowledges its durable local copy, the server clears the binary. New group members and newly linked devices cannot restore already removed media; the sender must resend from its phone.

Only one download per recipient account is required. This release does not guarantee media synchronization to every device sharing the same account.

The relay has a default 256 MiB total binary quota (MEDIA_RELAY_QUOTA_BYTES). New uploads receive HTTP 507 when full, rather than filling the VPS disk. PostgreSQL reuses vacuumed space; clearing BYTEA values does not immediately shrink the database files on the OS.

The Android JobScheduler requests a network connection and persists pending work across reboot. Firebase and app foreground entry trigger recovery of the authenticated pending-media queue. Android may defer jobs; users must receive files before the 24-hour deadline. Disabling automatic audio downloads leaves audio pending for manual download.

## Endpoints
- GET /api/v1/dispatches/pending-media
- POST /api/v1/dispatches/{chat}/attachments/{message}/received
- GET /api/v1/dispatches/{chat}/messages/{message}
- Existing attachment download returns HTTP 410 after removal/expiry.

Acknowledgements are issued only after the Android file is closed and synced. The sender persists its own copy under a client-generated message ID before upload. Forwarding media uploads the local copy as a new temporary delivery.
