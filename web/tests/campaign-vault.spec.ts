import { expect, test } from "@playwright/test";

const API = process.env.E2E_API_URL || "http://localhost:8000";

test("DM can resume and owner-delete campaigns saved on this device", async ({
  browser,
  request,
}) => {
  const firstResponse = await request.post(`${API}/api/games`, {
    data: { name: "Northwatch", dm_name: "Morgan", dm_mode: "human" },
  });
  const secondResponse = await request.post(`${API}/api/games`, {
    data: { name: "Sunless Citadel", dm_name: "Morgan", dm_mode: "human" },
  });
  expect(firstResponse.ok()).toBeTruthy();
  expect(secondResponse.ok()).toBeTruthy();
  const first = await firstResponse.json();
  const second = await secondResponse.json();
  const saved = [
    {
      ...first,
      name: "Northwatch",
      is_owner: true,
      last_opened_at: new Date().toISOString(),
    },
    {
      ...second,
      name: "Sunless Citadel",
      is_owner: true,
      last_opened_at: new Date(Date.now() - 1000).toISOString(),
    },
  ];

  const context = await browser.newContext({ viewport: { width: 1100, height: 850 } });
  await context.addInitScript(({ active, campaigns }) => {
    localStorage.setItem("dnd-table-credentials", JSON.stringify(active));
    localStorage.setItem(
      "dnd-table-saved-campaigns-v1",
      JSON.stringify(campaigns),
    );
  }, { active: first, campaigns: saved });
  const page = await context.newPage();
  await page.goto("/");
  await expect(page.getByText("Encounter Control")).toBeVisible();

  await page.getByRole("button", { name: "Campaignler" }).click();
  await expect(page.getByRole("heading", { name: "Kayıtlı campaignler" })).toBeVisible();
  await expect(page.getByText("Northwatch", { exact: true })).toBeVisible();
  await expect(page.getByText("Sunless Citadel", { exact: true })).toBeVisible();

  await page.getByRole("button", {
    name: "Sunless Citadel campaignini kalıcı olarak sil",
  }).click();
  await page.getByLabel("Sunless Citadel silme onayı").fill(
    `${second.game_id}:Sunless Citadel`,
  );
  await page.getByRole("button", {
    name: "Kalıcı olarak sil",
    exact: true,
  }).click();
  await expect(page.getByText("Sunless Citadel", { exact: true })).toHaveCount(0);

  const deletedSnapshot = await request.get(`${API}/api/snapshot`, {
    headers: { Authorization: `Bearer ${second.token}` },
  });
  expect(deletedSnapshot.ok()).toBeFalsy();

  await page.getByRole("button", { name: "Devam et" }).click();
  await expect(page.getByText("Encounter Control")).toBeVisible();
  await context.close();
});
