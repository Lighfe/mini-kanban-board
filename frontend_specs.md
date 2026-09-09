# Mini Kanban Board — Frontend Spec (Lovable, mocked backend)

This spec describes the frontend-only build for Lovable. It implements the
UI and interactions from [_docs/specs.md](_docs/specs.md) against a fully
mocked backend — **no real backend, network calls, or persistence in this
pass.** Every "backend" behavior described below lives behind one module so
a real API can be swapped in later without touching UI code.

## Stack

React + TypeScript + Tailwind + shadcn/ui.

## Mock layer

- A single module, `src/api/mockClient.ts`, is the *only* place that knows
  about backend operations. Every board/column/task/sharing action the UI
  needs is one async function here (e.g. `listBoards()`, `createBoard()`,
  `moveTask()`, `createColumn()`, `deleteColumn()`, `archiveTask()`,
  `createShareLink()`, `revokeShareLink()`, `redeemShareLink()`,
  `updateMemberRole()`, `removeMember()`, `transferOwnership()`). No
  component reads or writes mock state directly — everything goes through
  this module's functions, which return plain data (not references into
  the store), so a real HTTP client can later replace the internals of
  this one file with no changes elsewhere.
- The store is in-memory only: it resets on every page reload. No
  localStorage, no real persistence.
- Functions validate the caller's role themselves (not just the UI hiding
  buttons) and reject disallowed actions the same way a real API would —
  so permission logic isn't duplicated between the UI and a future
  backend.
- The mock client's data shapes are the frontend's own DTOs, not a literal
  copy of the backend data model in [_docs/specs.md](_docs/specs.md) —
  e.g. no `passwordHash` ever appears in anything the UI receives.

### Seed data

- One hardcoded "current user" (e.g. Alice). No real sign-up/sign-in
  gating — those screens exist as static UI (see Auth screens) but always
  proceed to this same user.
- Alice owns two boards, "Personal" and "Work", each seeded with the four
  default columns (Backlog, Today, Doing, Done) and a handful of sample
  cards across columns, including at least one overdue card and one
  archived card.
- At least one additional board owned by a different fixture user, with
  Alice already a member (e.g. as editor) and one other fixture member —
  this is required to demo the member list, role changes, and the
  share-link "upgrade never downgrades" rule, since a single real user
  can't otherwise exercise sharing.
- At least one seeded, valid share-link token for a board Alice is not
  yet a member of, so link redemption can be demoed end-to-end.

## Data model (mock)

TypeScript types mirroring the entities in [_docs/specs.md](_docs/specs.md)
(`Board`, `BoardMember`, `ShareLink`, `Column`, `Task`, plus `role` and
`priority` enums), minus backend-only fields like `passwordHash`. Task and
column `order` use the midpoint-insert-with-round-number-respace scheme
described in specs.md's Drag and Drop section — implement it once, in the
mock client, so it isn't thrown away when a real backend replaces this
module.

- Deleting a non-Done column with tasks archives those tasks (per spec).
  Since this is an in-memory mock with no real FK enforcement, an archived
  task keeps the `columnId` of the column it was deleted from purely for
  display/history purposes — it never reappears on a board.

## Screens & components

- **Auth screens**: static sign-up / sign-in forms. Submitting either
  always proceeds to the app as the single mock user — no real validation
  or gating.
- **Board switcher**: lists every board the current user is a member of
  (owned or shared), plus "+ New board".
- **Board view**: columns rendered left-to-right in order; a "+ New
  column" control appends a column to the end. The Done column shows an
  "Archive all in Done" action. Cards show title, priority indicator, and
  due date if set, with an overdue visual treatment per the rule below.
  An "+ Add card" control on each column creates a new task (title
  required, defaults: priority Medium, archived false, appended to the
  end of the column).
- **Card editor** (modal/drawer, opened by clicking a card): edit title,
  description, due date, priority. Viewers can open it read-only but see
  no save action.
- **Column controls**: non-Done columns can be renamed inline and
  deleted; deleting a column with tasks requires a confirmation dialog
  before archiving its tasks. The Done column has no rename/delete
  controls — its name is fixed and protected (no other column can be
  created or renamed to "Done").
- **Archive view** (per board): lists archived tasks; owners and editors
  can permanently delete a task from here (irreversible, no restore —
  restoring an archived task to the board is out of scope). Viewers can
  view this list but cannot delete.
- **Board settings** (owner only): rename or delete the board (delete
  requires confirmation and explains the cascade), member list with
  per-member role change and remove, share-link list (create new
  view/edit links, revoke existing ones independently), and ownership
  transfer to another existing member.
- **Share-link redemption** (`/share/:token`): simulates opening a share
  link — looks up the mock token, and on success adds/upgrades the
  current user's membership on that board per the redemption rule, then
  navigates to the board. An invalid or revoked token shows an error
  state instead.
- **Dev role switcher**: a small always-visible control ("Viewing as:
  Owner / Editor / Viewer") that overrides the effective role used for
  permission checks on the current board, purely to demo role-gated UI.
  It only changes what role is *simulated* for the current session — it
  never mutates the underlying `BoardMember` rows, so it has no effect on
  ownership, redemption upgrades, or the seeded data.

## Interactions

- **Drag and drop**: cards can be dragged between and within columns for
  the Owner/Editor simulated role; disabled (static cards) for Viewer.
  Dropping sets the task's `columnId` and recomputes `order` via the
  midpoint/re-spacing scheme in the mock client.
- **Overdue**: a card is shown as overdue when today's calendar date is
  after its `dueDate` (date-only comparison, no time-of-day/UTC math),
  it's not archived, and it's not in the Done column.
- **Archiving**: individual archive action on a card; bulk "Archive all
  in Done" archives every task currently in the Done column.
- **Sharing**: generate a view or edit share link from board settings;
  revoke any link independently; redeeming a link creates or upgrades
  membership (never downgrades an existing member's role); removing a
  member's access also revokes all of that board's currently active
  links.
- **Ownership**: transferring ownership promotes the chosen member to
  owner and demotes the current owner to editor; the owner cannot remove
  their own access or leave the board without transferring or deleting
  it first.
- **Permissions**: Viewer is read-only everywhere (no create/edit/move/
  archive/delete, no drag) and cannot access board settings. Editor can
  do all board content actions but not manage members, links, deletion,
  or ownership transfer. Owner can do everything.

## Explicitly out of scope for this pass

- Any real backend, network calls, or persistence beyond in-memory mock
  state (resets on reload).
- Real authentication (session/password handling, verification, reset).
- Column reordering, real-time multi-user sync, and restoring an archived
  task back onto the board — all out of scope per specs.md.
