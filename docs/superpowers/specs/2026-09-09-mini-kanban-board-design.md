# Mini Kanban Board — Design Spec

## Overview

A multi-user kanban board application. Each user gets personal boards to
organize their own tasks, and can share individual boards with other users
for collaborative work (e.g. a board for a specific work project), with
either view or edit access.

## Data Model

- **User**: `{ id, email, passwordHash, name, createdAt }`
- **Board**: `{ id, name, createdAt }`
- **BoardMember**: `{ boardId, userId, role: "owner" | "editor" | "viewer" }`
  — governs both ownership of a user's own boards and access granted to
  collaborators.
- **ShareLink**: `{ id, boardId, role: "editor" | "viewer", token, createdBy, revoked }`
- **Column**: `{ id, boardId, name, order }`
- **Task**: `{ id, boardId, columnId, title, description, dueDate, priority, order, archived, createdAt, createdBy }`
  — `priority` is an enum: `Low | Medium | High`.

## Boards

- On sign-up, a user is seeded with two owned boards: "Personal" and "Work".
  Both are renameable.
- Users can create additional boards via a "+ New board" action; the
  creator becomes that board's owner.
- A board switcher lists every board the current user is a member of,
  whether owned or shared with them.

## Columns

- Each board is seeded with four default columns: Backlog, Today, Doing,
  Done, ordered left to right via `order`.
- Column names are editable in place.
- New columns can be added to a board; they are appended to the right.
- A column can be deleted; if it contains tasks, deletion requires
  confirmation.
- Columns are not user-reorderable in this version — new columns always
  append to the end.

## Cards (Tasks)

- A card displays its title, a compact priority indicator, and its due
  date if set.
- A card whose `dueDate` is in the past, and which is not archived and not
  in a "Done"-type column, is shown with an overdue visual treatment.
- Clicking a card opens an editor for title, description, due date, and
  priority.
- A card can be archived individually. Bulk archiving is available for the
  Done column ("Archive all in Done").
- Archived cards are hidden from the board and are only visible in a
  dedicated archive view, from which they can be permanently deleted.

## Sharing & Permissions

- Roles: **owner** (full control, including managing membership and share
  links), **editor** (create/edit/move/archive cards and columns), and
  **viewer** (read-only).
- A board owner generates a share link scoped to a role (view or edit)
  from the board's settings.
- Opening a share link requires the recipient to be signed in; if they
  don't have an account, they are prompted to create one. On first visit
  with a valid, non-revoked link, a `BoardMember` row is created for that
  user at the link's role, and they are taken to the board.
- The owner can view the board's member list, change a member's role, and
  remove a member's access individually.
- The owner can revoke or regenerate a share link. Revoking a link
  prevents new redemptions but does not remove access already granted to
  users who previously redeemed it — removing an existing member's access
  is done via the member list.
- Viewers cannot edit, move, archive, or create cards or columns, and
  cannot drag cards. Editors can perform all board content actions but
  cannot manage members or share links.

## Drag and Drop

- Cards are dragged between and within columns. Dropping a card sets its
  `columnId` to the target column and recomputes `order` among the cards
  in that column.
- Drag-and-drop is available to owners and editors; viewers see static
  cards.

## Authentication

- Email/password sign-up and sign-in with session-based authentication.
- No email verification or additional account roles beyond board
  membership are in scope.

## Testing Approach

- **Data/permission layer**: unit tests for task and column CRUD,
  ordering on drag-and-drop, overdue-date calculation, share-link
  redemption and revocation, and role-boundary enforcement
  (owner/editor/viewer).
- **Interaction layer**: tests for drag-and-drop behavior gated by role,
  board switching, and the share-link redemption flow (including the
  sign-up-then-redeem path).

## Out of Scope (v1)

- Column reordering.
- Anonymous (non-authenticated) board access.
- Real-time multi-user sync (concurrent edits use last-write-wins).
- Email verification, password reset, and account roles beyond board
  membership.
