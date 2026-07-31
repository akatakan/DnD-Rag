import { expect, test } from "@playwright/test";
import { randomBytes } from "node:crypto";

const API = process.env.E2E_API_URL || "http://localhost:8000";

test("DM resumes a server-listed campaign after the old token is revoked", async ({
  browser,
  request,
}) => {
  const createdResponse = await request.post(`${API}/api/games`, {
    data: {
      name: "A Week Later",
      dm_name: "Morgan",
      dm_mode: "human",
    },
  });
  expect(createdResponse.ok()).toBeTruthy();
  const created = await createdResponse.json();
  const vaultSecret = randomBytes(32).toString("hex");

  const attachResponse = await request.post(
    `${API}/api/campaign-vault/attach`,
    {
      headers: {
        Authorization: `Bearer ${created.token}`,
        "X-Campaign-Vault": vaultSecret,
      },
    },
  );
  expect(attachResponse.ok()).toBeTruthy();
  const logoutResponse = await request.post(`${API}/api/auth/logout`, {
    headers: { Authorization: `Bearer ${created.token}` },
  });
  expect(logoutResponse.ok()).toBeTruthy();

  const context = await browser.newContext({
    viewport: { width: 1100, height: 850 },
  });
  await context.addInitScript((secret) => {
    localStorage.setItem("dnd-table-campaign-vault-v1", secret);
    localStorage.removeItem("dnd-table-credentials");
    localStorage.removeItem("dnd-table-saved-campaigns-v1");
  }, vaultSecret);
  const page = await context.newPage();
  await page.goto("/");

  await expect(
    page.getByRole("heading", { name: "Kampanyalarım" }),
  ).toBeVisible();
  await expect(page.getByText("A Week Later", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Devam et" }).click();
  await expect(page.getByText("Encounter Control")).toBeVisible();

  const stored = await page.evaluate(() =>
    JSON.parse(localStorage.getItem("dnd-table-credentials") || "{}")
  );
  expect(stored.game_id).toBe(created.game_id);
  expect(stored.token).not.toBe(created.token);
  await context.close();
});
