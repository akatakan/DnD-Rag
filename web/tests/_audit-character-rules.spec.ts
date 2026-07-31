import { expect, test } from "@playwright/test";

const API = process.env.E2E_API_URL || "http://localhost:8000";

test("Fighter draft discards stale client slot maxima before builder renders", async ({
  browser,
  request,
}) => {
  const created = await request.post(`${API}/api/games`, {
    data: { name: "Rules Audit", dm_name: "DM", dm_mode: "human" },
  });
  expect(created.ok()).toBeTruthy();
  const dm = await created.json();
  const joined = await request.post(`${API}/api/games/join`, {
    data: { invite_code: dm.invite_code, player_name: "Player" },
  });
  expect(joined.ok()).toBeTruthy();
  const player = await joined.json();
  const draftUrl = `${API}/api/characters/${player.character_id}/draft`;
  const headers = { Authorization: `Bearer ${player.token}` };
  const initialDraftResponse = await request.get(draftUrl, { headers });
  const initialDraft = await initialDraftResponse.json();
  const staleSpellState = await request.patch(draftUrl, {
    headers,
    data: {
      expected_revision: initialDraft.revision,
      patch: {
        spellcasting: {
          ability: "strength",
          known_spell_ids: ["spell:cure-wounds"],
          prepared_spell_ids: ["spell:cure-wounds"],
          slots: { "1": 16 },
        },
      },
    },
  });
  expect(staleSpellState.ok()).toBeTruthy();

  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  await context.addInitScript((credentials) => {
    localStorage.setItem("dnd-table-credentials", JSON.stringify(credentials));
  }, player);
  const page = await context.newPage();
  const reconciled = page.waitForResponse((response) =>
    response.url().endsWith(`/api/characters/${player.character_id}/draft`)
    && response.request().method() === "POST"
  );
  await page.goto("/");
  expect((await reconciled).ok()).toBeTruthy();
  await expect.poll(async () => {
    const response = await request.get(draftUrl, { headers });
    const draft = await response.json();
    return draft.data.spellcasting;
  }).toEqual({
    ability: null,
    known_spell_ids: [],
    prepared_spell_ids: [],
    slots: {},
  });

  await page.getByRole("button", { name: /Standart Builder/ }).click();
  const next = page.getByRole("button", { name: "İleri" });
  await next.click();
  await expect(page.getByRole("heading", { name: "Class seç" })).toBeVisible();

  await next.click();
  await next.click();
  await next.click();
  await next.click();
  await page.getByLabel("Athletics").check();
  await page.getByLabel("Perception").check();
  await page.getByLabel("Arcana").check();
  await next.click();
  await expect(page.getByRole("heading", { name: "Başlangıç ekipmanını seç" })).toBeVisible();
  await page.getByRole("button", { name: /Shield/ }).click();
  await next.click();
  await expect(page.getByRole("heading", { name: "Spellcasting" })).toBeVisible();
  await expect(page.getByText(/1. seviyede spell slot/)).toBeVisible();
  await expect(page.getByRole("spinbutton")).toHaveCount(0);
  await context.close();
});
