# Basketball challenges — Android 1.10.0 / 22

Chats and groups have a small basketball button beside the attachment button.
The sender chooses one other member and sends an invitation. Only the invited
player can accept or decline; the challenger can cancel an unanswered invitation.
Other group members can watch the scores. Challenges are disabled in saved
messages and announcement councils.

After acceptance, each player has one turn. Swipe upward from the ball toward the
rim. Each consecutive basket adds one point; the first miss ends the turn. The hoop
moves horizontally from 10 points and faster from 20. A turn lasts at most three
minutes and is capped at 100 points. Leaving deliberately requires confirmation
and saves the current score. A started turn cannot be restarted. Opening it again
resumes the existing state and deadline. Invitations/challenges expire after 24
hours; expiration is applied when the card is next fetched.

Once both turns end, the server stores a separate result message with both scores,
the winner or a draw. The challenge card also shows its final result. Forwarded
challenge cards are snapshots; players use the original chat to participate.

## Backend

Authenticated routes under `/api/v1/basketball`:

- `POST /{chat}`: opponent UUID and client message UUID (idempotent creation).
- `GET /{chat}/{id}`: read state, applying elapsed turn/challenge deadlines.
- `POST /{chat}/{id}/action`: accept, decline, cancel, start or finish.
- `POST /{chat}/{id}/shot`: request UUID, sequence, normalized drag and turn phase.

Scores are computed by the server from projectile geometry; client score totals
are not accepted. Requests are checked against chat membership, the two players,
turn state, sequence and elapsed time. A request UUID makes the latest shot safely
retryable after a lost response. PostgreSQL locks the challenge dispatch row to
serialize concurrent actions/shots. The deterministic result UUID prevents duplicate
result messages. This is a casual game; validation is not a claim that modified
clients cannot automate aiming.

State lives in the existing `Dispatch.rich` JSON column: no schema migration is
needed. Messages follow normal deletion and auto-delete handling. Removing the
challenge makes its play endpoints unavailable. Durable result messages use the
normal chat history and WebSocket delivery. Cards poll while the app is resumed,
so a lost live event does not lose the stored outcome. Shot submission needs an
internet connection; a failed request exposes a retry action instead of discarding
or submitting a new shot.

Deploy this backend and install the new Android build together. Source commits do
not deploy the VPS or distribute a signed APK. Validate aiming/animation on real
phones, a direct challenge, a group challenge, an interrupted connection, and
background/resume before release. Automated HTTP tests mock live socket delivery.

`tests/check_basketball.py` covers authorization, scoring, misses/draws, replayed
requests, duplicate results, history, expiry and deleted challenges. Android's
`BasketPhysicsTest` checks shared geometry against server vectors.

## Notes strip

The hide control is now on the notes strip itself. Its collapsed state leaves a
Show notes control in the same place. The existing local preference is preserved.
Contacts without a visible active note are still shown with a No note label;
privacy rules are unchanged and no expired/hidden note text is exposed. Tapping a
contact with no note opens their profile; an active note keeps replies/reactions.
