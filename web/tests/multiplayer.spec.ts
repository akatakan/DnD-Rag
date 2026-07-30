import { expect, test } from "@playwright/test";

const API = process.env.E2E_API_URL || "http://localhost:8000";

test("DM and player receive separate live workspaces", async ({ browser, request }) => {
  test.setTimeout(60_000);
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
  await expect(dmPage.getByText("Advanced encounter")).toBeVisible();
  await expect(dmPage.getByRole("button", { name: "Son işlemi geri al" })).toBeDisabled();
  await expect(dmPage.getByText(dm.invite_code)).toBeVisible();
  await expect(dmPage.locator("html")).toHaveJSProperty(
    "scrollWidth",
    await dmPage.locator("html").evaluate((element) => element.clientWidth),
  );
  await expect(dmPage.getByRole("heading", { name: "AI DM" })).toBeVisible();
  await dmPage.getByRole("button", { name: "Encounters" }).click();
  await expect(dmPage.getByRole("heading", { name: "Encounter Library" })).toBeVisible();
  await dmPage.getByRole("button", { name: "Encounter library ekranını kapat" }).click();

  const playerContext = await browser.newContext({ viewport: { width: 390, height: 844 } });
  await playerContext.addInitScript((credentials) => {
    localStorage.setItem("dnd-table-credentials", JSON.stringify(credentials));
  }, player);
  const playerPage = await playerContext.newPage();
  await playerPage.goto("/");
  await expect(playerPage.locator("html")).toHaveJSProperty(
    "scrollWidth",
    await playerPage.locator("html").evaluate((element) => element.clientWidth),
  );
  await expect(playerPage.getByRole("heading", { name: "Kahramanını adlandır" })).toBeVisible();
  await expect(playerPage.getByText(/Masaya katılmadan önce karakterini tamamla/)).toBeVisible();
  await expect(playerPage.getByRole("button", { name: "Campaign" })).toBeDisabled();
  await expect(playerPage.getByRole("button", { name: "Session" })).toBeDisabled();
  await expect(playerPage.getByRole("button", { name: "Builder'ı kapat" })).toHaveCount(0);
  await expect(playerPage.getByRole("button", { name: /Zar atma panelini aç/ })).toHaveCount(0);
  await expect(playerPage.getByRole("button", { name: "Oturum token'ını yenile" })).toBeEnabled();
  await expect(playerPage.getByRole("button", { name: "Oturumdan çık" })).toBeEnabled();
  await playerPage.getByLabel("Karakter adı").fill("Riva Revised");
  await playerPage.getByRole("button", { name: "İleri" }).click();
  await expect(playerPage.getByRole("heading", { name: "Ability score'ları belirle" })).toBeVisible();
  await expect(playerPage.getByRole("button", { name: /Standard Array/ })).toHaveAttribute("aria-pressed", "true");
  await playerPage.screenshot({
    path: "test-results/character-builder-mobile.png",
    fullPage: true,
  });
  for (let step = 0; step < 7; step += 1) {
    await playerPage.getByRole("button", { name: "İleri" }).click();
  }
  await expect(playerPage.getByRole("heading", { name: "Karakterini kontrol et" })).toBeVisible();
  await playerPage.getByRole("button", { name: "Karakteri yayınla" }).click();
  await expect(playerPage.getByText("Character Sheet")).toBeVisible();
  await expect(playerPage.getByText("Riva Revised")).toBeVisible();
  await expect(playerPage.getByText("HP Talebi")).toBeVisible();
  await expect(playerPage.getByText("Kurala Sor")).toBeVisible();
  await playerPage.getByRole("button", { name: "Campaign" }).click();
  await expect(playerPage.getByText("Campaign Hub")).toBeVisible();
  await expect(playerPage.getByRole("heading", { name: /Lobi ve readiness/ })).toBeVisible();
  await playerPage.getByRole("button", { name: /Campaign dashboard.*kapat/ }).click();
  await playerPage.getByRole("button", { name: "Session" }).click();
  await expect(playerPage.getByRole("heading", { name: /Session 1/ })).toBeVisible();
  await expect(playerPage.getByRole("heading", { name: "Game Log" })).toBeVisible();
  await playerPage.getByPlaceholder("Bu oturumda ne oldu?").fill("Kapıdaki rünleri inceledim.");
  await playerPage.getByRole("button", { name: "Not ekle" }).click();
  await expect(playerPage.getByText("Kapıdaki rünleri inceledim.")).toBeVisible();
  await playerPage.getByRole("button", { name: "Session ekranını kapat" }).click();
  await playerPage.getByRole("button", { name: /Perception/ }).click();
  await expect(playerPage.getByRole("dialog", { name: "Perception" })).toBeVisible();
  await playerPage.getByRole("button", { name: /Zar panelini kapat/ }).click();
  await playerPage.getByRole("button", { name: /Zar atma panelini aç/ }).click();
  await expect(playerPage.getByRole("dialog", { name: "Zar at" })).toBeVisible();
  const preferenceSaved = playerPage.waitForResponse((response) =>
    response.url().includes("/api/me/dice-preferences")
    && response.request().method() === "PATCH"
    && response.ok()
  );
  await playerPage.getByLabel(/Zar temas/).selectOption("ivory");
  await playerPage.getByRole("button", { name: /Çarpışma sesi açık/ }).click();
  await preferenceSaved;
  await playerPage.getByRole("button", { name: "d6" }).click();
  await playerPage.locator('.dice-settings input[type="number"]').first().fill("2");
  await playerPage.getByRole("button", { name: "2d6 at" }).click();
  const diceTray = playerPage.getByTestId("dice-3d-tray");
  await expect(diceTray).toBeAttached();
  await expect(diceTray.locator("canvas")).toHaveCount(1);
  await expect(diceTray).toHaveAttribute("data-renderer", "webgl");
  await expect(diceTray).toHaveAttribute("data-animation-state", /running|settled/);
  await expect(diceTray).toHaveAttribute("data-result-faces", /^\d+,\d+$/);
  await expect(diceTray).toBeVisible();
  await expect(diceTray.locator(".dice-3d-value")).toHaveCount(0);
  await playerPage.waitForTimeout(1_350);
  await playerPage.screenshot({ path: "test-results/dice-3d-mobile.png" });
  await expect(playerPage.getByLabel("Değiştirici")).toHaveValue("0");
  await playerPage.getByRole("button", { name: /Zar panelini kapat/ }).click();
  await playerPage.getByRole("tab", { name: "Inventory" }).click();
  await expect(playerPage.getByRole("heading", { name: "Envanter" })).toBeVisible();
  await playerPage.getByRole("tab", { name: "Inventory" }).press("End");
  await expect(playerPage.getByRole("tab", { name: "Notes" })).toHaveAttribute("aria-selected", "true");
  await playerPage.getByRole("tab", { name: "Notes" }).press("Home");
  await expect(playerPage.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");

  await playerPage.getByRole("button", { name: "Hasar" }).click();
  await expect(dmPage.getByText("Onay Bekleyenler")).toBeVisible();
  await dmPage.getByTitle("Onayla").click();
  await expect(playerPage.getByText("10/11")).toBeVisible();
  await expect(diceTray).toHaveAttribute("data-animation-state", "released", {
    timeout: 5_000,
  });

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
