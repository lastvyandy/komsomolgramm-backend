# Notes and granular privacy (Android 1.9.1, code 16)

Notes appear as a horizontal strip above the chat list, with circular avatars and
text bubbles. The first entry is your own note. Text and emojis use a 60-Unicode-code-
point limit, matching Android's counter. Publishing replaces the previous note and
starts a new 24-hour period. You can delete it early. The feed excludes expired
notes immediately; the existing cleanup worker deletes expired note rows every 30s.
Android hides expired notes locally while the strip is visible and refreshes every
30s in the foreground. Publishing a note does not send a push notification.

Default audience is the author's saved contacts. Everyone and Nobody can be selected
under Settings > Privacy > Notes. Nobody still lets the owner view/edit their note.
Visibility and replying are enforced by the API. Replies and six quick emoji reactions
are private direct messages quoting the original note, with a stable client UUID to
avoid duplicates on retry. They reuse existing message notifications, history,
read cursors and auto-delete. Replacing/deleting/expiring a note does not remove a
reply already sent into a private conversation. There are no public reaction counts,
viewer lists, music clips or stories in this text-note implementation.

## Privacy

GET /api/v1/privacy; PUT with {key, audience: all|contacts|nobody}.
Keys: photo, bio, birthday, country, nationality, relationship, song, app_usage,
last_seen, read_receipts, battery, sleep, notes, calls, group_invites.
Contacts always means users explicitly saved by the owner, rather than strangers
who saved the owner. Defaults preserve earlier visibility; notes default to contacts.
Existing opt-in controls (song/relationship/app sharing/sleep schedule) continue to
apply. Audience selection does not enable Android usage access or invent profile data.

Profile privacy applies in the common ComradeOut serializer, including nested users
in contact lists, conversation members and message history. Birth date and derived
zodiac are hidden together. Photo, photo-history and app-icon bytes enforce the same
rule before cache validators are considered. Android honors avatar_visible to avoid
continuing to show a cached avatar after learning it is hidden.

Last seen/online and read receipts have separate policies. A private read still
updates your own unread cursor but its WebSocket event is restricted at the socket
manager and history readers omit unauthorized readers. Receipt privacy is not based
on the legacy last-seen switch. Battery has its own audience; last-seen privacy does
not automatically suppress an otherwise shared battery display. Call and group-
invitation restrictions are enforced by the recipient's policy. Mandatory announcement
council enrollment remains unchanged.

Changing privacy governs future access; it cannot retract screenshots, exported data
or messages already delivered. No account-deletion or security claims are added.

## Deployment

Pull and rebuild the backend first using the existing Compose files (including calls
if configured). Startup adds comrades.privacy JSON and creates status_notes without
resetting user data. No additional token or provider configuration is needed for notes
or privacy. TURN remains necessary only for voice calls.

Then pull Android and publish on the existing signing PC with code 16 / name 1.9.1.
Check published release codes before reusing a number. The app contains Spanish and
Russian strings. The developer build does not replace a two-phone device test.

Research reference for text notes: Meta's 13 December 2022 announcement describes
60-character text/emoji notes at the top of the inbox, 24-hour visibility and private
DM replies. The visual layout also uses the user's supplied Instagram screenshot.
https://about.fb.com/news/2022/12/sharing-features-on-instagram-Notes-group-profiles-and-more/
