import { expect, test } from "@playwright/test";

const API = process.env.E2E_API_URL || "http://localhost:8000";

test("5.5e standard and quick builder are clear, legal, and responsive", async ({
  browser,
  request,
}) => {
  test.setTimeout(90_000);
  const created = await request.post(`${API}/api/games`, {
    data: { name: "Builder UX Audit", dm_name: "Morgan", dm_mode: "human" },
  });
  expect(created.ok()).toBeTruthy();
  const dm = await created.json();
  const joined = await request.post(`${API}/api/games/join`, {
    data: { invite_code: dm.invite_code, player_name: "Riva" },
  });
  expect(joined.ok()).toBeTruthy();
  const player = await joined.json();

  const desktop = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  await desktop.addInitScript((credentials) => {
    localStorage.setItem("dnd-table-credentials", JSON.stringify(credentials));
  }, player);
  const page = await desktop.newPage();
  await page.goto("/");

  await expect(
    page.getByRole("heading", {
      name: "Kahramanını nasıl oluşturmak istersin?",
    }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /Standart Builder/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Quick Build/ })).toBeVisible();
  await expect(page.locator("html")).toHaveJSProperty(
    "scrollWidth",
    await page.locator("html").evaluate((element) => element.clientWidth),
  );
  await page.screenshot({
    path: "test-results/character-builder-methods-desktop.png",
    fullPage: true,
  });

  await page.getByRole("button", { name: /Standart Builder/ }).click();
  await expect(
    page.getByRole("heading", { name: "Karakter tercihleri" }),
  ).toBeVisible();
  await expect(page.getByText("Home · Character Preferences")).toBeVisible();
  await page.screenshot({
    path: "test-results/character-builder-standard-home-desktop.png",
    fullPage: true,
  });
  await page.getByLabel("Karakter adı").fill("Riva");
  await page.getByRole("button", { name: "İleri" }).click();
  await expect(page.getByRole("heading", { name: "Class seç" })).toBeVisible();
  await expect(page.getByText("1 · Class", { exact: true })).toBeVisible();
  await page.screenshot({
    path: "test-results/character-builder-standard-class-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Yöntemi değiştir" }).click();
  await page.getByRole("button", { name: /Quick Build/ }).click();
  await expect(
    page.getByRole("heading", { name: "Üç seçimle maceraya hazırlan" }),
  ).toBeVisible();
  await page.getByLabel("1 · Karakter adı").fill("Riva Swift");
  await page.getByRole("button", { name: /Fighter/ }).click();
  await page.getByRole("button", { name: /Human/ }).click();
  await page.screenshot({
    path: "test-results/character-builder-quick-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Önerilen karakteri oluştur" }).click();
  await expect(
    page.getByRole("heading", { name: "Karakterini kontrol et" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Riva Swift" }),
  ).toBeVisible();
  await expect(page.getByText("Adım 9 / 9")).toBeVisible();

  const draftResponse = await request.get(
    `${API}/api/characters/${player.character_id}/draft`,
    { headers: { Authorization: `Bearer ${player.token}` } },
  );
  expect(draftResponse.ok()).toBeTruthy();
  const draft = await draftResponse.json();
  expect(draft.current_step).toBe("review");
  expect(Object.values(draft.data.ability_scores).sort((a, b) => b - a)).toEqual(
    [15, 14, 13, 12, 10, 8],
  );
  expect(
    Object.values(draft.data.background_ability_increases).sort(
      (a, b) => b - a,
    ),
  ).toEqual([2, 1]);
  await desktop.close();

  const joinedMobile = await request.post(`${API}/api/games/join`, {
    data: { invite_code: dm.invite_code, player_name: "Mira" },
  });
  const mobilePlayer = await joinedMobile.json();
  const mobile = await browser.newContext({
    viewport: { width: 390, height: 844 },
  });
  await mobile.addInitScript((credentials) => {
    localStorage.setItem("dnd-table-credentials", JSON.stringify(credentials));
  }, mobilePlayer);
  const mobilePage = await mobile.newPage();
  await mobilePage.goto("/");
  await expect(mobilePage.locator("html")).toHaveJSProperty(
    "scrollWidth",
    await mobilePage.locator("html").evaluate((element) => element.clientWidth),
  );
  await mobilePage.screenshot({
    path: "test-results/character-builder-methods-mobile.png",
    fullPage: true,
  });
  await mobile.close();
});
