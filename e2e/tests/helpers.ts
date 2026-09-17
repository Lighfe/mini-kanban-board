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
  const section = page.locator("section", { has: page.getByText("Invite links") });
  const links = section.locator("code");
  const before = await links.count();
  await page.getByRole("button", { name: label }).click();
  await expect(links).toHaveCount(before + 1);
  const text = await links.last().innerText();
  const token = text.replace("/share/", "").trim();
  if (!token) throw new Error(`Could not parse share token from "${text}"`);
  return token;
}
