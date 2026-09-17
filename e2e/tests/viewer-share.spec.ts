import { test, expect, type BrowserContext } from "@playwright/test";
import { addCard, card, createBoard, createShareLink, signUp } from "./helpers";

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
  await expect(pageV.getByText("viewer", { exact: true })).toBeVisible();

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
  // Two elements share the accessible name "Close": the dialog's footer
  // button and the shadcn primitive's sr-only-labeled corner "X" — the
  // footer one renders first in the DOM.
  await pageV.getByRole("button", { name: "Close", exact: true }).first().click();

  await ctxA.close();
  await ctxV.close();
});
