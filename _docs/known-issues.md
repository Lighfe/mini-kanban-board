# Known Issues

Tracked issues found during manual end-to-end testing (frontend +
backend together, in a browser). Update this file as issues are
fixed or new ones are found; it's the informal backlog until we need
something heavier.

## Frontend (Lovable-managed)

Status: sent to Lovable and fixed 2026-09-10 (commit
`15c052064328a45d33608aaad360a6d32a5feb5d` in the frontend submodule).
The custom background color (item below) shipped as **local-only**
(stored in the browser via `localStorage`, per device) — the backend
board model has no `color` field yet, so it is not shared across
members or devices. Revisit if/when that should become a real,
synced board setting (would need a backend change: add `color` to
the Board model + support it in the board update endpoint).

- **Leftover demo artifact** — the board list shows a hardcoded "Try an
  invite link" card ("Dave shared his Marketing Launch board...",
  `/share/DEMO-EDIT-TOKEN`) left over from the mocked-backend stage.
  Should be removed now that the app talks to the real backend.
  ([frontend/src/routes/boards.index.tsx:113-122](../frontend/src/routes/boards.index.tsx#L113-L122))

- **Column min-height / slot growth (medium)** — a column only reserves
  space for the cards it currently has, so with e.g. 2 cards it looks
  "full" and doesn't visibly grow until a card is actually added.
  Columns should reserve ~3 card slots by default and grow by one slot
  per card added.

- **Drag-and-drop drop-zone precision (medium)** — dropping a card into
  another column requires the mouse to be very precisely positioned
  (a small area near the top of the column); dropping slightly off
  silently returns the card to its original column. The whole column
  area should act as a valid drop target, not just a small zone near
  the cursor.

- **New feature: custom board background color** — not yet supported;
  requested during manual testing.

## Backend / spec clarification (not a bug)

- **Restored tasks land in Backlog after their original column is
  deleted** — when a column is deleted, its tasks are archived; when
  later restored from the archive view, they land at the end of
  Backlog rather than back in a recreated version of the deleted
  column. This is the specified behavior
  ([_docs/specs.md:73-77](specs.md#L73-L77)), not a bug. Noting it here
  since it can look surprising/random from the UI with no indication
  of why the task landed in a different column.
