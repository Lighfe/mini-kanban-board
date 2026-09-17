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
  await expect(pageB.getByText("editor", { exact: true })).toBeVisible();

  // --- User B moves the card from Backlog to Doing ---
  await expect(card(pageB, "Write the report")).toBeVisible();
  await card(pageB, "Write the report").dragTo(column(pageB, "Doing"));
  await expect(card(column(pageB, "Doing"), "Write the report")).toBeVisible();
  await expect(card(column(pageB, "Backlog"), "Write the report")).toHaveCount(0);

  // --- User A reloads; the move is visible (persistence after reload, no live push) ---
  // createShareLink() left pageA on the board's Settings tab; go back to the
  // board view first, then reload it, to actually re-fetch board state.
  await pageA.goto(`/boards/${boardId}`);
  await pageA.reload();
  await expect(card(column(pageA, "Doing"), "Write the report")).toBeVisible();
  await expect(card(column(pageA, "Backlog"), "Write the report")).toHaveCount(0);

  await ctxA.close();
  await ctxB.close();
});
