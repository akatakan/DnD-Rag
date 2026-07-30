import type {
  Credentials,
  CharacterDraft,
  CampaignLobby,
  DicePreferences,
  DeveloperCatalogEntry,
  DeveloperRulesetDetail,
  DeveloperRulesetSummary,
  EventPage,
  EncounterDraft,
  MapAsset,
  MapScene,
  RulesCatalogEntityType,
  RulesCatalogEntry,
  RulesCatalogPage,
  RulesetSummary,
  SessionWorkspace,
  Snapshot,
} from "./types";

const defaultApi =
  window.location.port === "5173"
    ? `${window.location.protocol}//${window.location.hostname}:8000`
    : window.location.origin;
const API = import.meta.env.VITE_API_URL || defaultApi;

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, options: RequestInit = {}, token?: string): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    const detail = body.detail;
    const message =
      typeof detail === "string"
        ? detail
        : typeof detail?.message === "string"
          ? detail.message
          : "İşlem tamamlanamadı";
    throw new ApiError(message, response.status);
  }
  return response.json();
}

const developerRequest = <T>(
  path: string,
  developerToken: string,
  options: RequestInit = {},
) => request<T>(path, {
  ...options,
  headers: {
    "X-Developer-Token": developerToken,
    ...options.headers,
  },
});

export const api = {
  developerRulesets: (developerToken: string) =>
    developerRequest<{ rulesets: DeveloperRulesetSummary[] }>(
      "/api/developer/catalog/rulesets",
      developerToken,
    ),
  developerRuleset: (developerToken: string, version: string) =>
    developerRequest<DeveloperRulesetDetail>(
      `/api/developer/catalog/rulesets/${encodeURIComponent(version)}`,
      developerToken,
    ),
  cloneDeveloperRuleset: (
    developerToken: string,
    sourceVersion: string,
    version: string,
    name: string,
  ) => developerRequest<DeveloperRulesetDetail>(
    "/api/developer/catalog/rulesets/clone",
    developerToken,
    {
      method: "POST",
      body: JSON.stringify({
        source_version: sourceVersion,
        version,
        name,
      }),
    },
  ),
  saveDeveloperCatalogEntry: (
    developerToken: string,
    version: string,
    expectedRevision: number,
    entry: DeveloperCatalogEntry,
  ) => developerRequest<DeveloperRulesetDetail>(
    `/api/developer/catalog/rulesets/${encodeURIComponent(version)}/entries`,
    developerToken,
    {
      method: "PUT",
      body: JSON.stringify({
        expected_revision: expectedRevision,
        entry,
      }),
    },
  ),
  deleteDeveloperCatalogEntry: (
    developerToken: string,
    version: string,
    entryId: string,
    expectedRevision: number,
  ) => developerRequest<DeveloperRulesetDetail>(
    `/api/developer/catalog/rulesets/${encodeURIComponent(version)}/entries/${encodeURIComponent(entryId)}`,
    developerToken,
    {
      method: "DELETE",
      body: JSON.stringify({ expected_revision: expectedRevision }),
    },
  ),
  publishDeveloperRuleset: (
    developerToken: string,
    version: string,
    expectedRevision: number,
    makeDefault: boolean,
  ) => developerRequest<DeveloperRulesetDetail>(
    `/api/developer/catalog/rulesets/${encodeURIComponent(version)}/publish`,
    developerToken,
    {
      method: "POST",
      body: JSON.stringify({
        expected_revision: expectedRevision,
        make_default: makeDefault,
      }),
    },
  ),
  createGame: (name: string, dmName: string, dmMode: string) =>
    request<Credentials & { invite_code: string }>("/api/games", {
      method: "POST",
      body: JSON.stringify({ name, dm_name: dmName, dm_mode: dmMode }),
    }),
  joinGame: (inviteCode: string, playerName: string) =>
    request<Credentials>("/api/games/join", {
      method: "POST",
      body: JSON.stringify({ invite_code: inviteCode, player_name: playerName }),
    }),
  snapshot: (token: string) => request<Snapshot>("/api/snapshot", {}, token),
  dicePreferences: (token: string) =>
    request<DicePreferences>("/api/me/dice-preferences", {}, token),
  updateDicePreferences: (
    token: string,
    theme: DicePreferences["theme"],
    soundEnabled: boolean,
  ) =>
    request<DicePreferences>(
      "/api/me/dice-preferences",
      {
        method: "PATCH",
        body: JSON.stringify({
          theme,
          sound_enabled: soundEnabled,
        }),
      },
      token,
    ),
  mapScene: (token: string) =>
    request<MapScene>("/api/maps/scene", {}, token),
  mapAssets: (token: string) =>
    request<{ assets: MapAsset[] }>("/api/maps/assets", {}, token),
  uploadMapAsset: async (token: string, file: File) => {
    const safeName = file.name.replace(/[^\x20-\x7e]/g, "_").slice(0, 160);
    const response = await fetch(`${API}/api/maps/assets`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": file.type,
        "X-Filename": safeName || "map",
      },
      body: file,
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({
        detail: response.statusText,
      }));
      throw new ApiError(
        typeof body.detail === "string" ? body.detail : "Harita yüklenemedi.",
        response.status,
      );
    }
    return response.json() as Promise<MapAsset>;
  },
  mapAssetBlob: async (token: string, url: string, signal?: AbortSignal) => {
    const response = await fetch(`${API}${url}`, {
      headers: { Authorization: `Bearer ${token}` },
      signal,
    });
    if (!response.ok) {
      throw new ApiError("Harita görseli alınamadı.", response.status);
    }
    return response.blob();
  },
  rotateToken: (token: string) =>
    request<{ token: string; token_expires_at: string }>(
      "/api/auth/rotate", { method: "POST" }, token,
    ),
  logout: (token: string) =>
    request<{ revoked: boolean }>("/api/auth/logout", { method: "POST" }, token),
  websocketTicket: (token: string) =>
    request<{ ticket: string; expires_at: string }>(
      "/api/ws-ticket", { method: "POST" }, token,
    ),
  rotateInvite: (token: string, maxUses = 50) =>
    request<{
      invite_code: string;
      invite_id: string;
      expires_at: string;
      max_uses: number;
      use_count: number;
    }>("/api/invites/rotate", {
      method: "POST",
      body: JSON.stringify({ max_uses: maxUses }),
    }, token),
  revokeInvites: (token: string) =>
    request<{ revoked: number }>(
      "/api/invites/revoke", { method: "POST" }, token,
    ),
  events: (token: string, after: number, limit = 100) =>
    request<EventPage>(`/api/events?after=${after}&limit=${limit}`, {}, token),
  encounterLibrary: (token: string) =>
    request<{ encounters: EncounterDraft[]; revision: number }>(
      "/api/encounters", {}, token,
    ),
  rulesets: (token: string) =>
    request<{ rulesets: RulesetSummary[] }>("/api/rulesets", {}, token),
  catalogEntries: (
    token: string,
    version: string,
    options: {
      type?: RulesCatalogEntityType;
      query?: string;
      offset?: number;
      limit?: number;
    } = {},
  ) => {
    const params = new URLSearchParams();
    if (options.type) params.set("type", options.type);
    if (options.query) params.set("q", options.query);
    params.set("offset", String(options.offset ?? 0));
    params.set("limit", String(options.limit ?? 50));
    return request<RulesCatalogPage>(
      `/api/rulesets/${encodeURIComponent(version)}/entries?${params.toString()}`,
      {},
      token,
    );
  },
  catalogEntry: (token: string, version: string, entryId: string) =>
    request<{ ruleset: RulesetSummary; entry: RulesCatalogEntry }>(
      `/api/rulesets/${encodeURIComponent(version)}/entries/${encodeURIComponent(entryId)}`,
      {},
      token,
    ),
  createSession: (token: string, title?: string) =>
    request<Snapshot["session"]>("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ title: title || null }),
    }, token),
  updateSessionStatus: (
    token: string,
    status: "live" | "paused" | "completed",
    expectedRevision: number,
  ) =>
    request<Snapshot["session"] & { revision: number }>("/api/sessions/status", {
      method: "POST",
      body: JSON.stringify({ status, expected_revision: expectedRevision }),
    }, token),
  sessionWorkspace: (token: string) =>
    request<SessionWorkspace>("/api/sessions/current/workspace", {}, token),
  campaignLobby: (token: string) =>
    request<CampaignLobby>("/api/campaigns/current/lobby", {}, token),
  updateCampaignSettings: (
    token: string,
    expectedVersion: number,
    settings: CampaignLobby["settings"],
  ) =>
    request<{ lobby: CampaignLobby; revision: number }>(
      "/api/campaigns/current/settings",
      {
        method: "PATCH",
        body: JSON.stringify({
          expected_version: expectedVersion,
          house_rules: settings.house_rules,
          safety_tools: settings.safety_tools,
          session_zero_agenda: settings.session_zero_agenda,
        }),
      },
      token,
    ),
  updateSessionZero: (
    token: string,
    payload: {
      expected_version: number;
      readiness_status: "not_ready" | "ready";
      consent_status: "pending" | "accepted" | "declined";
      lines: string[];
      veils: string[];
      notes: string;
    },
  ) =>
    request<{ lobby: CampaignLobby; revision: number }>(
      "/api/campaigns/current/session-zero",
      { method: "PATCH", body: JSON.stringify(payload) },
      token,
    ),
  scheduleSession: (
    token: string,
    expectedRevision: number,
    scheduledAt: string | null,
  ) =>
    request<{ revision: number; session: Snapshot["session"] }>(
      "/api/sessions/schedule",
      {
        method: "PATCH",
        body: JSON.stringify({
          expected_revision: expectedRevision,
          scheduled_at: scheduledAt,
        }),
      },
      token,
    ),
  command: <T = unknown>(
    token: string,
    type: string,
    payload: Record<string, unknown> = {},
    expectedRevision?: number,
    clientActionId = crypto.randomUUID(),
  ) =>
    request<T>("/api/commands", {
      method: "POST",
      body: JSON.stringify({
        type,
        payload,
        expected_revision: expectedRevision,
        client_action_id: clientActionId,
      }),
    }, token),
  createCharacterDraft: (token: string, characterId: string) =>
    request<CharacterDraft>(
      `/api/characters/${encodeURIComponent(characterId)}/draft`,
      { method: "POST" },
      token,
    ),
  getCharacterDraft: (token: string, characterId: string) =>
    request<CharacterDraft>(
      `/api/characters/${encodeURIComponent(characterId)}/draft`,
      {},
      token,
    ),
  saveCharacterDraft: (
    token: string,
    characterId: string,
    expectedRevision: number,
    patch: Partial<CharacterDraft["data"]>,
  ) =>
    request<CharacterDraft>(
      `/api/characters/${encodeURIComponent(characterId)}/draft`,
      {
        method: "PATCH",
        body: JSON.stringify({
          expected_revision: expectedRevision,
          patch,
        }),
      },
      token,
    ),
  navigateCharacterDraft: (
    token: string,
    characterId: string,
    expectedRevision: number,
    direction: "next" | "previous",
  ) =>
    request<CharacterDraft>(
      `/api/characters/${encodeURIComponent(characterId)}/draft/navigate`,
      {
        method: "POST",
        body: JSON.stringify({
          expected_revision: expectedRevision,
          direction,
        }),
      },
      token,
    ),
  rules: (token: string, question: string, mode: string) =>
    request<{ answer: string; sources: { book: string; page: number }[] }>(
      "/api/rules", { method: "POST", body: JSON.stringify({ question, mode }) }, token,
    ),
  aiStep: (token: string, objective: string, autoApply: boolean) =>
    request("/api/ai-dm/step", {
      method: "POST", body: JSON.stringify({ objective, auto_apply: autoApply }),
    }, token),
  websocketUrl: (credentials: Credentials, ticket: string, after = 0) => {
    const base = API.replace(/^http/, "ws");
    return `${base}/ws/games/${credentials.game_id}?ticket=${encodeURIComponent(ticket)}&after=${after}`;
  },
};
