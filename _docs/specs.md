# Mini Kanban Board — Design Spec

## Overview

A multi-user kanban board application. Each user gets personal boards to
organize their own tasks, and can share individual boards with other users
for collaborative work (e.g. a board for a specific work project), with
either view or edit access.

## Data Model

- **User**: `{ id, email (unique), passwordHash, name, createdAt }`
- **Board**: `{ id, name, createdAt }`
- **BoardMember**: `{ boardId, userId, role: "owner" | "editor" | "viewer" }`
  — unique on `(boardId, userId)`. Exactly one `owner` row per board is
  enforced at all times.
- **ShareLink**: `{ id, boardId, role: "editor" | "viewer", token, createdBy, revoked }`
- **Column**: `{ id, boardId, name, order }` — `order` is a sortable
  number, unique within a board.
- **Task**: `{ id, boardId, columnId, title, description, dueDate, priority, order, archived, createdAt, createdBy }`
  — `priority` is an enum: `Low | Medium | High`, default `Medium`.
  `archived` defaults to `false`. `order` is a sortable number, unique
  within a column. `columnId` must reference a column belonging to the
  same `boardId`; this is enforced, not just assumed.

## Boards

- On sign-up, a user is seeded with two owned boards: "Personal" and
  "Work". Both are renameable.
- Users can create additional boards via a "+ New board" action; the
  creator becomes that board's owner.
- A board switcher lists every board the current user is a member of,
  whether owned or shared with them.
- The owner can delete a board (with confirmation). Deletion cascades:
  all columns, tasks, memberships, and share links for that board are
  deleted along with it. This is irreversible.
- The owner can transfer ownership to another existing member, which
  promotes that member to `owner` and demotes the current owner to
  `editor`. An owner cannot remove their own access or leave a board
  without first transferring ownership or deleting the board.

## Columns

- Each board is seeded with four default columns, in order: Backlog,
  Today, Doing, Done.
- The column named "Done" is fixed: it cannot be renamed or deleted, and
  every board always has exactly one. It is the column used to determine
  task completion (for overdue exclusion and bulk archiving).
- All other columns are freely renameable, and new ones can be added,
  appended to the right of the existing columns.
- A non-Done column can be deleted. If it contains tasks, deletion
  requires confirmation; on confirmation, its tasks are archived (not
  hard-deleted).
- Columns are not user-reorderable in this version — new columns always
  append to the end.

## Cards (Tasks)

- A card displays its title, a compact priority indicator, and its due
  date if set.
- `dueDate` is a date (no time-of-day). A task becomes overdue starting
  the day after its due date. A card is shown with an overdue visual
  treatment when it's overdue, not archived, and not in the Done column.
- Clicking a card opens an editor for title, description, due date, and
  priority.
- A card can be archived individually. Bulk archiving is available for
  the Done column ("Archive all in Done").
- Archived cards are hidden from the board and are only visible in a
  dedicated archive view.
- From the archive view, owners and editors can permanently delete a
  task. This is irreversible.

## Sharing & Permissions

- Roles: **owner** (full control, including membership, share links, and
  board deletion/ownership transfer), **editor** (create/edit/move/
  archive/permanently-delete cards, create/rename/delete columns other
  than Done), and **viewer** (read-only: can view the board and archive,
  cannot perform any write action).
- A board owner generates a share link scoped to a role (view or edit)
  from the board's settings. A board may have multiple active links at
  once, each independently revocable.
- Opening a share link requires the recipient to be signed in; if they
  don't have an account, they are prompted to create one. On a valid,
  non-revoked link, a `BoardMember` row is created for that user at the
  link's role, and they are taken to the board. If the user is already a
  member of that board, their role is set to whichever is higher between
  their current role and the link's role — redeeming a link never
  downgrades an existing member.
- The owner can view the board's member list, change a member's role
  (other than their own), and remove a member's access individually.
  Removing a member's access also revokes all currently active share
  links for that board, so the removed member cannot regain access by
  reusing a link the owner has not explicitly reissued. The owner
  generates a new link to reshare.
- Viewers cannot edit, move, archive, permanently delete, or create cards
  or columns, and cannot drag cards. Editors can perform all board
  content actions but cannot manage members, share links, board deletion,
  or ownership transfer.

## Drag and Drop

- Cards are dragged between and within columns. Dropping a card sets its
  `columnId` to the target column.
- Card ordering uses a sortable numeric `order` value: on drop, the new
  value is the midpoint between its new neighbors' `order` values, so a
  move only touches the moved row. When neighboring values are too close
  to split further, the column's cards are re-spaced to round numbers
  (0, 1000, 2000, ...) as a lazy cleanup on that write. The same scheme
  is used for column ordering on a board.
- Drag-and-drop is available to owners and editors; viewers see static,
  non-draggable cards.

## Authentication

- Email/password sign-up and sign-in with session-based authentication.
- No email verification, password reset, or account roles beyond board
  membership are in scope.

## Concurrency

- Edits are last-write-wins at the individual task/column level (not at
  the whole-board level) — two people editing different tasks never
  conflict, and two edits to the same task resolve to whichever was
  saved last.

## Testing Approach

- **Data/permission layer**: unit tests for task and column CRUD,
  ordering on drag-and-drop (including re-spacing), overdue-date
  calculation, share-link redemption (including the existing-member
  upgrade rule) and revocation-on-removal, and role-boundary enforcement
  (owner/editor/viewer), including the single-owner and ownership
  transfer invariants.
- **Interaction layer**: tests for drag-and-drop behavior gated by role,
  board switching, and the share-link redemption flow (including the
  sign-up-then-redeem path).

## Out of Scope (v1)

- Column reordering.
- Anonymous (non-authenticated) board access.
- Real-time multi-user sync (concurrent edits use last-write-wins as
  described above).
- Email verification, password reset, and account roles beyond board
  membership.
- Archived-task restoration back onto the board.
