# Saved Messages and message actions

A direct chat with the current account is the private Saved Messages chat. Repeated calls to
`POST /soviets/direct/{own_id}` return the same chat, protected by the existing transaction lock.
Only that account has membership. Existing direct chats with other users are unchanged.

`POST /dispatches/{source_id}/forward` accepts `message_ids` (1–100) and `target_chat_id`.
The caller must belong to both chats and all source messages must be visible. Messages are
copied in chronological order in one transaction. Media bytes are copied into the destination,
so removing the original does not break the saved copy. Original author names survive repeated
forwarding. Live events are published after commit.

`POST /dispatches/{chat_id}/delete-messages` accepts `message_ids` (1–100) and `for_everyone`.
The default hides messages only for the caller, including previews, unread counts, history,
reply context and media galleries. Shared deletion is permitted only when every selected
message belongs to the caller. Invalid requests are rejected before changing any messages.

Startup creates the new `dispatch_forwards` and `dispatch_hidden` tables through the existing
`Base.metadata.create_all` path. No destructive schema migration is required.

Update the backend before installing the Android update. The Android client offers tap actions,
long-press selection, batch copy/forward/delete, and single-message editing in the composer.
Older device bookmarks are imported when opening Saved Messages; a local bookmark is removed
only after the server confirms its forward. Unavailable originals remain in local storage.

Run `python tests/check_message_actions.py` using requirements-dev.txt for isolated SQLite
integration checks. The test substitutes SQLite advisory-lock handling; PostgreSQL concurrency
and Android gestures still require environment-specific checks.
