import { expect, test } from "@playwright/test";

const API = "http://localhost:8000";

test("DM and player receive separate live workspaces", async ({ browser, request }) => {
  const created = await request.post(`${API}/api/games`, {
    data: { name: "Ravenloft", dm_name: "Morgan", dm_mode: "assisted" },
  });
  expect(created.ok()).toBeTruthy();
  const dm = await created.json();
  const joined = await request.post(`${API}/api/games/join`, {
    data: { invite_code: dm.invite_code, player_name: "Riva" },
  });
  const player = await joined.json();

  const dmContext = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await dmContext.addInitScript((credentials) => {
    localStorage.setItem("dnd-table-credentials", JSON.stringify(credentials));
  }, dm);
  const dmPage = await dmContext.newPage();
  await dmPage.goto("/");
  await expect(dmPage.getByText("Encounter Control")).toBeVisible();
  await expect(dmPage.getByText(dm.invite_code)).toBeVisible();
  await expect(dmPage.getByRole("heading", { name: "AI DM" })).toBeVisible();

  const playerContext = await browser.newContext({ viewport: { width: 390, height: 844 } });
  await playerContext.addInitScript((credentials) => {
    localStorage.setItem("dnd-table-credentials", JSON.stringify(credentials));
  }, player);
  const playerPage = await playerContext.newPage();
  await playerPage.goto("/");
  await expect(playerPage.getByText("Karakterim")).toBeVisible();
  await expect(playerPage.getByText("HP Talebi")).toBeVisible();
  await expect(playerPage.getByText("Kurala Sor")).toBeVisible();

  await playerPage.getByRole("button", { name: "Hasar" }).click();
  await expect(dmPage.getByText("Onay Bekleyenler")).toBeVisible();
  await dmPage.getByTitle("Onayla").click();
  await expect(playerPage.getByText("9/10")).toBeVisible();

  await dmPage.getByLabel("Co-DM").selectOption(player.member_id);
  await expect(playerPage.getByText("Encounter Control")).toBeVisible();
  await expect(playerPage.getByText("Izleme modu.")).toBeVisible();

  const blocked = await request.post(`${API}/api/commands`, {
    headers: { Authorization: `Bearer ${player.token}` },
    data: { type: "update_scene", payload: { title: "Unauthorized" } },
  });
  expect(blocked.status()).toBe(400);

  await dmPage.screenshot({ path: "test-results/dm-desktop.png", fullPage: true });
  await playerPage.screenshot({ path: "test-results/co-dm-mobile.png", fullPage: true });
  await dmContext.close();
  await playerContext.close();
});
