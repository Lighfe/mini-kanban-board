# CI (GitHub Actions) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add GitHub Actions CI that runs frontend + backend tests in parallel, then builds the Docker image, boots it with Compose, and runs Playwright e2e tests (sharing flow + viewer read-only) against the running stack.

**Architecture:** Three jobs. `backend-test` and `frontend-test` run in parallel with no dependency between them. `e2e` depends on both, builds and starts the Compose stack (`docker compose up --build -d`), waits for `/api/health`, runs a new `e2e/` Playwright project (plain npm project, Node-based, independent of the `frontend/` submodule's Bun toolchain) against `http://localhost:8000`, then tears the stack down and uploads the Playwright HTML report as an artifact on failure.

**Tech Stack:** GitHub Actions, `astral-sh/setup-uv` (backend), `oven-sh/setup-bun` (frontend lint), Node.js + `@playwright/test` (e2e), Docker Compose (existing `docker-compose.yml`/`Dockerfile`).

**Spec:** [_docs/deployment-plan.md](../../_docs/deployment-plan.md) (step 5), [_docs/specs.md](../../_docs/specs.md) (sharing/permissions model), [_docs/frontend_specs.md](../../_docs/frontend_specs.md) (screens/components referenced by the e2e selectors).

## Global Constraints

- The e2e test lives in `e2e/` at the repo root (per the deployment plan), not inside `frontend/` — `frontend/` is a Lovable-managed git submodule and must not be edited locally (per [AGENTS.md](../../../AGENTS.md)).
- `frontend/` has no unit-test runner configured (no vitest/jest, no `test` script in `frontend/package.json` — only `dev`, `build`, `build:dev`, `preview`, `lint`, `format`). "Frontend tests" in this plan means `bun run lint` (eslint), the only automated check the submodule currently exposes. Do not add a test script inside `frontend/` — that would require a submodule change.
- Backend tests run via `uv run pytest` from `backend/`, using the default SQLite-in-memory config already wired in `backend/tests/conftest.py` — no Postgres needed for this job (that's the separate `make test-pg` path, out of scope here).
- The compose stack the e2e job drives is exactly `docker-compose.yml` at the repo root (`db` + `app`, `KANBAN_SECURE_COOKIES=false`, port 8000 published) — do not fork a parallel config.
- Session cookies are `SameSite=Lax` (not `Secure`) in this stack (`KANBAN_SECURE_COOKIES=false`), so two separate Playwright browser contexts (not just two tabs) are required to simulate two independent user sessions — verified in `backend/src/kanban/routers/auth.py`.
- Drag-and-drop in the frontend is native HTML5 DnD (`draggable` + `onDragStart`/`onDragOver`/`onDrop` in `frontend/src/components/kanban/TaskCard.tsx` and `ColumnView.tsx`), not a JS DnD library — use Playwright's `locator.dragTo()`, which Chromium supports for native HTML5 DnD.
- The frontend has **no built-in "sign up then auto-redeem" flow**: `frontend/src/routes/share.$token.tsx` calls `redeemShareLink(token)` once on mount and shows an error state on 401 with no retry. The e2e test must explicitly: (1) visit `/share/:token` unauthenticated and observe the error state, (2) sign up via `/`, (3) visit `/share/:token` again to actually redeem. This is what "covers sign-up-then-redeem" means operationally — do not treat a single visit as sufficient.
- Card editor viewer read-only state per `frontend/src/components/kanban/TaskEditorDialog.tsx`: when `canEdit` is false, the dialog renders **only** a "Close" button — no "Save changes" and no "Archive card" button. Assert their absence, not just that inputs are disabled.
- `Done` column and non-Done columns: default seeded columns on every new board are `Backlog, Today, Doing, Done` (specs.md). Use these exact names in selectors.

---

### Task 1: Scaffold the `e2e/` Playwright project

**Files:**
- Create: `e2e/package.json`
- Create: `e2e/playwright.config.ts`
- Create: `e2e/tsconfig.json`
- Create: `e2e/.gitignore`
- Create: `e2e/tests/helpers.ts`
- Modify: `.dockerignore` (exclude `e2e/` from the Docker build context)

**Interfaces:**
- Produces: `e2e/tests/helpers.ts` exports used by both spec files in Tasks 2–3:
  - `signUp(page: Page, opts: { name: string; email: string; password: string }): Promise<void>` — fills and submits the sign-up form on `/`, waits for navigation to `/boards`.
  - `createBoard(page: Page, name: string): Promise<string>` — uses the "+ New board" dialog, returns the new board's id (parsed from the resulting URL `/boards/:boardId`).
  - `addCard(page: Page, columnName: string, title: string): Promise<void>` — opens "+ Add card" in the named column and submits it.
  - `createShareLink(page: Page, role: "editor" | "viewer"): Promise<string>` — on the board's Settings page, clicks the matching "New … link" button and returns the token (parsed from the newly added `<code>/share/{token}</code>` entry).
  - `column(page: Page, name: string): Locator` — returns `page.locator('section[aria-label="' + name + ' column"]')`.
  - `card(scope: Locator | Page, title: string): Locator` — returns the `div[role="button"]` task card locator filtered by title text, scoped to `scope`.

- [ ] **Step 1: Create `e2e/package.json`**

```json
{
  "name": "kanban-e2e",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": {
    "test": "playwright test"
  },
  "devDependencies": {
    "@playwright/test": "^1.48.0"
  }
}
```

- [ ] **Step 2: Create `e2e/playwright.config.ts`**

```ts
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  retries: process.env["CI"] ? 1 : 0,
  reporter: process.env["CI"] ? [["html", { open: "never" }], ["list"]] : "list",
  use: {
    baseURL: process.env["E2E_BASE_URL"] ?? "http://localhost:8000",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
```

- [ ] **Step 3: Create `e2e/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM"],
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "strict": true,
    "noEmit": true,
    "types": ["@playwright/test"]
  },
  "include": ["tests/**/*.ts", "playwright.config.ts"]
}
```

- [ ] **Step 4: Create `e2e/.gitignore`**

```
node_modules/
test-results/
playwright-report/
blob-report/
playwright/.cache/
```

- [ ] **Step 5: Create `e2e/tests/helpers.ts`**

```ts
import type { Locator, Page } from "@playwright/test";
import { expect } from "@playwright/test";

export async function signUp(
  page: Page,
  opts: { name: string; email: string; password: string },
): Promise<void> {
  await page.goto("/");
  await page.getByRole("tab", { name: "Create account" }).click();
  await page.locator("#su-name").fill(opts.name);
  await page.locator("#su-email").fill(opts.email);
  await page.locator("#su-pass").fill(opts.password);
  await page.getByRole("button", { name: "Create account" }).click();
  await page.waitForURL("**/boards");
}

export async function createBoard(page: Page, name: string): Promise<string> {
  await page.goto("/boards");
  await page.getByRole("button", { name: "New board" }).click();
  await page.getByPlaceholder("Board name").fill(name);
  await page.getByRole("button", { name: "Create board" }).click();
  await page.waitForURL(/\/boards\/[^/]+$/);
  const match = /\/boards\/([^/?#]+)/.exec(page.url());
  if (!match) throw new Error(`Could not parse board id from ${page.url()}`);
  return match[1];
}

export function column(page: Page, name: string): Locator {
  return page.locator(`section[aria-label="${name} column"]`);
}

export function card(scope: Locator | Page, title: string): Locator {
  return scope.locator('div[role="button"]').filter({ hasText: title });
}

export async function addCard(page: Page, columnName: string, title: string): Promise<void> {
  const col = column(page, columnName);
  await col.getByRole("button", { name: "Add card" }).click();
  await col.getByPlaceholder("Card title").fill(title);
  await col.getByRole("button", { name: "Add card" }).click();
  await expect(card(col, title)).toBeVisible();
}

export async function createShareLink(page: Page, role: "editor" | "viewer"): Promise<string> {
  await page.getByRole("link", { name: "Settings" }).click();
  await page.waitForURL(/\/settings$/);
  const label = role === "editor" ? "New edit link" : "New view link";
  const before = await page.locator("section", { has: page.getByText("Invite links") }).locator("code").count();
  await page.getByRole("button", { name: label }).click();
  const links = page.locator("section", { has: page.getByText("Invite links") }).locator("code");
  await expect(links).toHaveCount(before + 1);
  const text = await links.last().innerText();
  const token = text.replace("/share/", "").trim();
  if (!token) throw new Error(`Could not parse share token from "${text}"`);
  return token;
}
```

- [ ] **Step 6: Exclude `e2e/` from the Docker build context**

In `.dockerignore`, add a line `e2e` (anywhere in the file — it's a flat ignore list). The Dockerfile never references `e2e/`, so this only prevents Docker from needlessly hashing/sending it as build context.

- [ ] **Step 7: Verify the scaffold installs cleanly**

Run: `cd e2e && npm install && npx playwright install --with-deps chromium`
Expected: installs without error (no tests exist yet, so there's nothing to run).

- [ ] **Step 8: Commit**

```bash
git add e2e/package.json e2e/playwright.config.ts e2e/tsconfig.json e2e/.gitignore e2e/tests/helpers.ts .dockerignore
git commit -m "e2e: scaffold Playwright project and shared test helpers"
```

---

### Task 2: Sharing-flow e2e test (editor link, cross-session, persistence-after-reload)

**Files:**
- Create: `e2e/tests/sharing-flow.spec.ts`

**Interfaces:**
- Consumes: `signUp`, `createBoard`, `addCard`, `column`, `card`, `createShareLink` from `e2e/tests/helpers.ts` (Task 1).
- Produces: nothing consumed by later tasks; this is a leaf test file.

**Requires the app stack already running** at `E2E_BASE_URL` (default `http://localhost:8000`) — this task is written and can be run locally against `make up` before CI wiring exists, so it's testable in isolation.

- [ ] **Step 1: Write the test**

```ts
import { test, expect, type BrowserContext } from "@playwright/test";
import { addCard, card, column, createBoard, createShareLink, signUp } from "./helpers";

test("editor share link: sign-up-then-redeem, cross-session move, persists after reload", async ({
  browser,
}) => {
  const stamp = Date.now();
  const ownerEmail = `owner-${stamp}@example.com`;
  const editorEmail = `editor-${stamp}@example.com`;

  // --- Session 1: user A creates a board, a card, and an editor share link ---
  const ctxA: BrowserContext = await browser.newContext();
  const pageA = await ctxA.newPage();
  await signUp(pageA, { name: "Board Owner", email: ownerEmail, password: "hunter2-A" });

  const boardId = await createBoard(pageA, `Shared Board ${stamp}`);
  await addCard(pageA, "Backlog", "Write the report");

  const token = await createShareLink(pageA, "editor");

  // --- Session 2: user B opens the link unauthenticated first ---
  const ctxB: BrowserContext = await browser.newContext();
  const pageB = await ctxB.newPage();
  await pageB.goto(`/share/${token}`);
  await expect(pageB.getByRole("heading", { name: "Invite not valid" })).toBeVisible();

  // Sign up, then redeem the same link now that session 2 is authenticated.
  await signUp(pageB, { name: "Board Editor", email: editorEmail, password: "hunter2-B" });
  await pageB.goto(`/share/${token}`);
  await expect(pageB.getByRole("heading", { name: "You're in" })).toBeVisible();
  await pageB.waitForURL(new RegExp(`/boards/${boardId}$`));
  await expect(pageB.getByText("Editor", { exact: true })).toBeVisible();

  // --- User B moves the card from Backlog to Doing ---
  await expect(card(pageB, "Write the report")).toBeVisible();
  await card(pageB, "Write the report").dragTo(column(pageB, "Doing"));
  await expect(card(column(pageB, "Doing"), "Write the report")).toBeVisible();
  await expect(card(column(pageB, "Backlog"), "Write the report")).toHaveCount(0);

  // --- User A reloads; the move is visible (persistence after reload, no live push) ---
  await pageA.reload();
  await expect(card(column(pageA, "Doing"), "Write the report")).toBeVisible();
  await expect(card(column(pageA, "Backlog"), "Write the report")).toHaveCount(0);

  await ctxA.close();
  await ctxB.close();
});
```

- [ ] **Step 2: Run it against a local stack to verify it passes**

Run:
```bash
cd /path/to/mini-kanban-board
make up   # in one terminal; wait for it to become healthy
cd e2e
E2E_BASE_URL=http://localhost:8000 npx playwright test sharing-flow.spec.ts
```
Expected: PASS (1 test). If it fails, inspect the HTML report (`npx playwright show-report`) — common culprits: the "Settings" nav link only renders for the `owner` role (already handled since user A stays owner throughout), and `dragTo` needs the source element fully visible before dragging (Playwright auto-waits, but if this flakes, add an explicit `await expect(...).toBeVisible()` immediately before the `dragTo` call, already present above).

Tear down: `make down` (in the other terminal, or `docker compose down`).

- [ ] **Step 3: Commit**

```bash
git add e2e/tests/sharing-flow.spec.ts
git commit -m "e2e: add sharing-flow test (editor link, cross-session move, reload persistence)"
```

---

### Task 3: Viewer share-link e2e test (read-only card, no save, no drag)

**Files:**
- Create: `e2e/tests/viewer-share.spec.ts`

**Interfaces:**
- Consumes: `signUp`, `createBoard`, `addCard`, `column`, `card`, `createShareLink` from `e2e/tests/helpers.ts` (Task 1).

- [ ] **Step 1: Write the test**

```ts
import { test, expect, type BrowserContext } from "@playwright/test";
import { addCard, card, column, createBoard, createShareLink, signUp } from "./helpers";

test("viewer share link: card opens read-only, no save action, no drag", async ({ browser }) => {
  const stamp = Date.now();
  const ownerEmail = `viewer-owner-${stamp}@example.com`;
  const viewerEmail = `viewer-${stamp}@example.com`;

  const ctxA: BrowserContext = await browser.newContext();
  const pageA = await ctxA.newPage();
  await signUp(pageA, { name: "Viewer Board Owner", email: ownerEmail, password: "hunter2-A" });
  await createBoard(pageA, `Viewer Board ${stamp}`);
  await addCard(pageA, "Backlog", "Read this only");
  const token = await createShareLink(pageA, "viewer");

  const ctxV: BrowserContext = await browser.newContext();
  const pageV = await ctxV.newPage();
  await signUp(pageV, { name: "Read Only", email: viewerEmail, password: "hunter2-V" });
  await pageV.goto(`/share/${token}`);
  await expect(pageV.getByRole("heading", { name: "You're in" })).toBeVisible();

  // Redirects to the board; confirm the viewer role badge.
  await pageV.waitForURL(/\/boards\/[^/]+$/);
  await expect(pageV.getByText("Viewer", { exact: true })).toBeVisible();

  // No "+ New column", no "+ Add card", no drag handle in any column header.
  await expect(pageV.getByRole("button", { name: "New column" })).toHaveCount(0);
  await expect(pageV.getByRole("button", { name: "Add card" })).toHaveCount(0);

  // Card is not draggable.
  const theCard = card(pageV, "Read this only");
  await expect(theCard).toBeVisible();
  await expect(theCard).toHaveAttribute("draggable", "false");

  // Opening the card shows read-only details with no save action.
  await theCard.click();
  await expect(pageV.getByRole("heading", { name: "Card details" })).toBeVisible();
  await expect(pageV.getByRole("button", { name: "Save changes" })).toHaveCount(0);
  await expect(pageV.getByRole("button", { name: "Archive card" })).toHaveCount(0);
  await expect(pageV.getByLabel("Title")).toBeDisabled();
  await pageV.getByRole("button", { name: "Close" }).click();

  await ctxA.close();
  await ctxV.close();
});
```

- [ ] **Step 2: Run it against a local stack to verify it passes**

Run (with `make up` already running from Task 2, or restarted):
```bash
cd e2e
E2E_BASE_URL=http://localhost:8000 npx playwright test viewer-share.spec.ts
```
Expected: PASS (1 test).

- [ ] **Step 3: Commit**

```bash
git add e2e/tests/viewer-share.spec.ts
git commit -m "e2e: add viewer share-link test (read-only card, no save, no drag)"
```

---

### Task 4: GitHub Actions workflow

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `backend/pyproject.toml`/`backend/uv.lock` (existing), `frontend/package.json`/`frontend/bun.lock` (existing, submodule), `docker-compose.yml` + `Dockerfile` (existing, repo root), `e2e/package.json` + `e2e/playwright.config.ts` + `e2e/tests/*.spec.ts` (Tasks 1–3), `GET /api/health` (existing, `backend/src/kanban/main.py`).

- [ ] **Step 1: Write `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  push:
  pull_request:

concurrency:
  group: ci-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  backend-test:
    name: Backend tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          working-directory: backend
      - name: Install dependencies
        working-directory: backend
        run: uv sync
      - name: Run pytest
        working-directory: backend
        run: uv run pytest

  frontend-test:
    name: Frontend lint
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: true
      - uses: oven-sh/setup-bun@v2
        with:
          bun-version: latest
      - name: Install dependencies
        working-directory: frontend
        run: bun install --frozen-lockfile
      - name: Lint
        working-directory: frontend
        run: bun run lint

  e2e:
    name: End-to-end (Docker Compose + Playwright)
    runs-on: ubuntu-latest
    needs: [backend-test, frontend-test]
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: true

      - name: Build and start the stack
        run: docker compose up --build -d

      - name: Wait for the app to become healthy
        run: |
          for i in $(seq 1 60); do
            if curl -sf http://localhost:8000/api/health >/dev/null; then
              echo "App is up"
              exit 0
            fi
            sleep 2
          done
          echo "App did not become healthy in time" >&2
          docker compose logs
          exit 1

      - uses: actions/setup-node@v4
        with:
          node-version: 22

      - name: Install e2e dependencies
        working-directory: e2e
        run: npm install

      - name: Install Playwright browsers
        working-directory: e2e
        run: npx playwright install --with-deps chromium

      - name: Run Playwright tests
        working-directory: e2e
        env:
          E2E_BASE_URL: http://localhost:8000
        run: npx playwright test

      - name: Upload Playwright report
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: playwright-report
          path: e2e/playwright-report/
          retention-days: 7

      - name: Compose logs on failure
        if: failure()
        run: docker compose logs

      - name: Tear down the stack
        if: always()
        run: docker compose down -v
```

- [ ] **Step 2: Verify the workflow YAML is well-formed**

Run: `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml'))" 2>/dev/null || python3 -c "import json,yaml" 2>/dev/null; cat .github/workflows/ci.yml | head -1`

If `python3 -c "import yaml"` fails (module not installed), instead verify with: `docker run --rm -v "$PWD:/w" -w /w mikefarah/yq eval '.jobs | keys' .github/workflows/ci.yml` (or any locally available YAML linter) — the goal is just to catch indentation/syntax errors before pushing, since GitHub Actions has no fully offline validator. If neither is available, visually re-check indentation against the block above; this is a plain declarative file with no logic, so a careful re-read is an acceptable fallback.

- [ ] **Step 3: Run the full local pipeline once, end to end, exactly as CI will**

```bash
cd /path/to/mini-kanban-board
docker compose up --build -d
for i in $(seq 1 60); do curl -sf http://localhost:8000/api/health && break; sleep 2; done
cd e2e && npm install && npx playwright install --with-deps chromium
E2E_BASE_URL=http://localhost:8000 npx playwright test
cd ..
docker compose down -v
```
Expected: both Playwright tests (Tasks 2–3) PASS against the freshly built image, not just a dev server.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add GitHub Actions workflow (backend + frontend tests, e2e against Compose)"
```

---

### Task 5: Update the deployment plan doc

**Files:**
- Modify: `_docs/deployment-plan.md`

- [ ] **Step 1: Mark step 5 done and record what was verified**

Change the `### 5. CI (GitHub Actions)` heading to `### 5. CI (GitHub Actions) — done` (matching the style of steps 1–4), and append a short paragraph after the existing bullet list, in the same voice as the "Verified locally" notes on steps 3–4, e.g.:

```markdown
Implemented in [.github/workflows/ci.yml](../.github/workflows/ci.yml): `backend-test`
(`uv run pytest`) and `frontend-test` (`bun run lint` — the submodule has no
unit-test runner configured, only eslint) run in parallel; `e2e` waits on
both, then runs `docker compose up --build -d`, polls `/api/health`, and
runs the Playwright suite in [e2e/](../e2e/) against the running container.
Verified locally by running the same sequence outside of CI (`docker
compose up --build`, then `npx playwright test` from `e2e/` against
`http://localhost:8000`) — both the editor sharing-flow test and the viewer
read-only test pass against the built image.
```

- [ ] **Step 2: Commit**

```bash
git add _docs/deployment-plan.md
git commit -m "docs: mark deployment-plan step 5 (CI) done"
```

## Self-Review Notes

- **Spec coverage:** deployment-plan.md step 5's 5-point sharing flow → Task 2. Viewer case → Task 3. Parallel frontend/backend tests → Task 4 (`backend-test`, `frontend-test` jobs, no `needs` between them). Build image + Compose → Task 4 (`docker compose up --build -d`). Run e2e against that stack → Task 4 (`needs: [backend-test, frontend-test]`, health-poll, then `npx playwright test`).
- **Frontend test ambiguity resolved:** confirmed via `frontend/package.json` (no test runner present) that "frontend tests" can only mean the existing `bun run lint` script; documented as a Global Constraint so the executing agent doesn't try to add a test framework to the submodule.
- **Selectors verified against actual component source**, not guessed: sign-up form field ids (`su-name`/`su-email`/`su-pass`) and tab label from `frontend/src/routes/index.tsx`; "+ New board" / "Board name" / "Create board" from `frontend/src/routes/boards.index.tsx`; column `aria-label` and "Add card"/"New column" button text from `frontend/src/components/kanban/ColumnView.tsx`; task card `role="button"` + `draggable` attribute from `frontend/src/components/kanban/TaskCard.tsx`; viewer-mode dialog (no Save/Archive buttons, disabled inputs, "Card details" title) from `frontend/src/components/kanban/TaskEditorDialog.tsx`; Settings link visibility (owner-only) and role badge from `frontend/src/routes/boards.$boardId.tsx`; share-link creation buttons and `<code>/share/{token}</code>` rendering from `frontend/src/routes/boards.$boardId.settings.tsx`; redemption page headings ("You're in" / "Invite not valid") from `frontend/src/routes/share.$token.tsx`.
