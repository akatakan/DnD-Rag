import type { Credentials, Snapshot } from "./types";

const defaultApi =
  window.location.port === "5173"
    ? `${window.location.protocol}//${window.location.hostname}:8000`
    : window.location.origin;
const API = import.meta.env.VITE_API_URL || defaultApi;

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
    throw new Error(body.detail || "İşlem tamamlanamadı");
  }
  return response.json();
}

export const api = {
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
  command: (token: string, type: string, payload: Record<string, unknown> = {}) =>
    request("/api/commands", { method: "POST", body: JSON.stringify({ type, payload }) }, token),
  rules: (token: string, question: string, mode: string) =>
    request<{ answer: string; sources: { book: string; page: number }[] }>(
      "/api/rules", { method: "POST", body: JSON.stringify({ question, mode }) }, token,
    ),
  aiStep: (token: string, objective: string, autoApply: boolean) =>
    request("/api/ai-dm/step", {
      method: "POST", body: JSON.stringify({ objective, auto_apply: autoApply }),
    }, token),
  websocketUrl: (credentials: Credentials) => {
    const base = API.replace(/^http/, "ws");
    return `${base}/ws/games/${credentials.game_id}?token=${encodeURIComponent(credentials.token)}`;
  },
};
