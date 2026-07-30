import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const API = process.env.E2E_API_URL || "http://localhost:8000";

type Credentials = {
  token: string;
  game_id: string;
  member_id: string;
  character_id: string;
  invite_code?: string;
};

const auth = (token: string) => ({
  Authorization: `Bearer ${token}`,
});

async function publishDefaultCharacter(
  request: APIRequestContext,
  player: Credentials,
) {
  const route = `${API}/api/characters/${player.character_id}/draft`;
  let response = await request.get(route, {
    headers: auth(player.token),
  });
  expect(response.ok()).toBeTruthy();
  let draft = await response.json();
  while (draft.current_step !== "review") {
    response = await request.post(`${route}/navigate`, {
      headers: auth(player.token),
      data: {
        expected_revision: draft.revision,
        direction: "next",
      },
    });
    expect(response.ok()).toBeTruthy();
    draft = await response.json();
  }
  const snapshotResponse = await request.get(`${API}/api/snapshot`, {
    headers: auth(player.token),
  });
  const snapshot = await snapshotResponse.json();
  const published = await request.post(`${API}/api/commands`, {
    headers: auth(player.token),
    data: {
      type: "publish_character_draft",
      payload: { draft_revision: draft.revision },
      expected_revision: snapshot.revision,
      client_action_id: `edge-publish-${player.member_id}`,
    },
  });
  expect(published.ok()).toBeTruthy();
}

async function openAuthenticatedPage(
  page: Page,
  credentials: Credentials,
) {
  await page.addInitScript((value) => {
    localStorage.setItem("dnd-table-credentials", JSON.stringify(value));
  }, credentials);
  await page.goto("/");
}

const isLootClaimResponse = (response: {
  url(): string;
  request(): { method(): string; postDataJSON(): unknown };
}) => {
  if (
    !response.url().endsWith("/api/commands")
    || response.request().method() !== "POST"
  ) return false;
  try {
    const body = response.request().postDataJSON() as { type?: string };
    return body.type === "claim_session_loot";
  } catch {
    return false;
  }
};

test("two players racing for unique session loot converge on one claimant", async ({
  browser,
  request,
}) => {
  test.setTimeout(60_000);
  const createdResponse = await request.post(`${API}/api/games`, {
    data: {
      name: "The Ember Crown",
      dm_name: "Morgan",
      dm_mode: "human",
    },
  });
  expect(createdResponse.ok()).toBeTruthy();
  const dm = await createdResponse.json() as Credentials;
  const inviteCode = dm.invite_code;
  expect(inviteCode).toBeTruthy();

  const firstJoin = await request.post(`${API}/api/games/join`, {
    data: { invite_code: inviteCode, player_name: "Riva" },
  });
  const secondJoin = await request.post(`${API}/api/games/join`, {
    data: { invite_code: inviteCode, player_name: "Gareth" },
  });
  const riva = await firstJoin.json() as Credentials;
  const gareth = await secondJoin.json() as Credentials;
  await publishDefaultCharacter(request, riva);
  await publishDefaultCharacter(request, gareth);

  const dmContext = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  const rivaContext = await browser.newContext({
    viewport: { width: 1100, height: 850 },
  });
  const garethContext = await browser.newContext({
    viewport: { width: 1100, height: 850 },
  });
  const dmPage = await dmContext.newPage();
  const rivaPage = await rivaContext.newPage();
  const garethPage = await garethContext.newPage();

  try {
    await openAuthenticatedPage(dmPage, dm);
    await dmPage.getByRole("button", { name: "Session" }).click();
    await expect(
      dmPage.getByRole("heading", { name: "Session 1" }),
    ).toBeVisible();
    await dmPage.getByRole("button", { name: "Başlat" }).click();
    await expect(dmPage.getByText("live", { exact: true })).toBeVisible();
    await dmPage.getByLabel("Loot adı").fill("Ember Crown Shard");
    await dmPage.getByLabel("Loot adedi").fill("1");
    await dmPage.getByRole("button", { name: "Ekle", exact: true }).click();
    await expect(dmPage.getByText("Ember Crown Shard")).toBeVisible();
    await expect(
      dmPage.getByText(/Cannot read properties|undefined.*join/i),
    ).toHaveCount(0);

    await Promise.all([
      openAuthenticatedPage(rivaPage, riva),
      openAuthenticatedPage(garethPage, gareth),
    ]);
    await Promise.all([
      rivaPage.getByRole("button", { name: "Session" }).click(),
      garethPage.getByRole("button", { name: "Session" }).click(),
    ]);
    await Promise.all([
      expect(rivaPage.getByText("Ember Crown Shard")).toBeVisible(),
      expect(garethPage.getByText("Ember Crown Shard")).toBeVisible(),
    ]);

    const rivaClaim = rivaPage.waitForResponse(isLootClaimResponse);
    const garethClaim = garethPage.waitForResponse(isLootClaimResponse);
    await Promise.all([
      rivaPage.getByRole("button", { name: "Talep et" }).click(),
      garethPage.getByRole("button", { name: "Talep et" }).click(),
    ]);
    const claimResponses = await Promise.all([rivaClaim, garethClaim]);
    expect(
      claimResponses.filter((response) => response.status() === 200),
    ).toHaveLength(1);

    const rivaWorkspace = await request.get(
      `${API}/api/sessions/current/workspace`,
      { headers: auth(riva.token) },
    );
    const garethWorkspace = await request.get(
      `${API}/api/sessions/current/workspace`,
      { headers: auth(gareth.token) },
    );
    const rivaLoot = (await rivaWorkspace.json()).loot[0];
    const garethLoot = (await garethWorkspace.json()).loot[0];
    expect(rivaLoot.status).toBe("claimed");
    expect(garethLoot.status).toBe("claimed");
    expect(rivaLoot.claimant_id).toBe(garethLoot.claimant_id);
    expect([riva.member_id, gareth.member_id]).toContain(rivaLoot.claimant_id);

    await Promise.all([
      expect(
        rivaPage.getByText(rivaLoot.claimant_name, { exact: true }),
      ).toBeVisible(),
      expect(
        garethPage.getByText(rivaLoot.claimant_name, { exact: true }),
      ).toBeVisible(),
      expect(rivaPage.getByRole("button", { name: "Talep et" })).toHaveCount(0),
      expect(garethPage.getByRole("button", { name: "Talep et" })).toHaveCount(0),
    ]);
    await expect(
      dmPage.getByText(/Cannot read properties|undefined.*join/i),
    ).toHaveCount(0);
    await dmPage.screenshot({
      path: "test-results/campaign-session-edge-dm.png",
      fullPage: true,
    });
    await rivaPage.screenshot({
      path: "test-results/campaign-session-edge-player.png",
      fullPage: true,
    });
  } finally {
    await Promise.all([
      dmContext.close(),
      rivaContext.close(),
      garethContext.close(),
    ]);
  }
});
