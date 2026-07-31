import { useCallback, useEffect, useRef, useState } from "react";
import { BookOpenText, Castle, FolderClock, Library, LogOut, RefreshCw, Shield, Sparkles, Swords, UserRound } from "lucide-react";
import { api, ApiError } from "./api";
import DMConsole from "./components/DMConsole";
import JoinScreen from "./components/JoinScreen";
import PlayerConsole from "./components/PlayerConsole";
import DiceRoller from "./components/DiceRoller";
import CharacterBuilder from "./components/CharacterBuilder";
import CampaignDashboard from "./components/CampaignDashboard";
import SessionWorkspace from "./components/SessionWorkspace";
import EncounterLibrary from "./components/EncounterLibrary";
import DeveloperCatalog from "./components/DeveloperCatalog";
import type {
  Credentials,
  SavedCampaign,
  ServerCampaign,
  Snapshot,
} from "./types";

const STORAGE_KEY = "dnd-table-credentials";
const CAMPAIGN_STORAGE_KEY = "dnd-table-saved-campaigns-v1";
const CAMPAIGN_VAULT_KEY = "dnd-table-campaign-vault-v1";

function campaignVaultSecret(): string {
  try {
    const stored = localStorage.getItem(CAMPAIGN_VAULT_KEY);
    if (stored && /^[a-f0-9]{64}$/.test(stored)) return stored;
  } catch {
    // A privacy-restricted browser can still use the current page session.
  }
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  const secret = Array.from(
    bytes,
    (value) => value.toString(16).padStart(2, "0"),
  ).join("");
  try {
    localStorage.setItem(CAMPAIGN_VAULT_KEY, secret);
  } catch {
    // Persistence is unavailable, but the in-memory secret remains usable.
  }
  return secret;
}

const DEVICE_VAULT_SECRET = campaignVaultSecret();

function validCredentials(value: Partial<Credentials>): value is Credentials {
  return (
    typeof value.game_id === "string" &&
    typeof value.token === "string" &&
    ["dm", "co_dm", "player"].includes(value.role ?? "")
  );
}

function storedCredentials(): Credentials | null {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (!stored) return null;
    const value = JSON.parse(stored) as Partial<Credentials>;
    if (!validCredentials(value)) {
      localStorage.removeItem(STORAGE_KEY);
      return null;
    }
    return value;
  } catch {
    localStorage.removeItem(STORAGE_KEY);
    return null;
  }
}

function storedCampaigns(): SavedCampaign[] {
  try {
    const stored = localStorage.getItem(CAMPAIGN_STORAGE_KEY);
    if (!stored) return [];
    const values = JSON.parse(stored);
    if (!Array.isArray(values)) throw new Error("invalid campaign vault");
    return values
      .filter((value): value is SavedCampaign => {
        const candidate = value as Partial<SavedCampaign>;
        const hasMetadata = (
          typeof candidate.name === "string"
          && typeof candidate.is_owner === "boolean"
          && typeof candidate.last_opened_at === "string"
        );
        return (
          hasMetadata
          && validCredentials(candidate)
          && candidate.role !== "player"
        );
      })
      .slice(0, 12);
  } catch {
    localStorage.removeItem(CAMPAIGN_STORAGE_KEY);
    return [];
  }
}

function writeCampaigns(values: SavedCampaign[]) {
  localStorage.setItem(CAMPAIGN_STORAGE_KEY, JSON.stringify(values.slice(0, 12)));
}

export default function App() {
  if (window.location.pathname === "/__developer/catalog") {
    return <DeveloperCatalog />;
  }
  return <GameApplication />;
}

function GameApplication() {
  const [credentials, setCredentials] = useState<Credentials | null>(storedCredentials);
  const [savedCampaigns, setSavedCampaigns] = useState<SavedCampaign[]>(storedCampaigns);
  const [serverCampaigns, setServerCampaigns] = useState<ServerCampaign[]>([]);
  const [campaignVaultLoading, setCampaignVaultLoading] = useState(true);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [connected, setConnected] = useState(false);
  const [builderOpen, setBuilderOpen] = useState(false);
  const [campaignOpen, setCampaignOpen] = useState(false);
  const [sessionOpen, setSessionOpen] = useState(false);
  const [encounterOpen, setEncounterOpen] = useState(false);
  const eventCursor = useRef(0);

  const loadServerCampaigns = useCallback(async () => {
    setCampaignVaultLoading(true);
    try {
      const result = await api.campaignVault(DEVICE_VAULT_SECRET);
      setServerCampaigns(result.campaigns);
    } catch {
      setServerCampaigns([]);
    } finally {
      setCampaignVaultLoading(false);
    }
  }, []);

  const acceptSnapshot = useCallback((next: Snapshot) => {
    eventCursor.current = Math.max(eventCursor.current, next.event_cursor);
    setSnapshot((current) =>
      !current || next.revision >= current.revision ? next : current
    );
    if (credentials && credentials.role !== "player") {
      setSavedCampaigns((current) => {
        const saved: SavedCampaign = {
          ...credentials,
          name: next.game.name,
          is_owner: next.me.is_owner,
          last_opened_at: new Date().toISOString(),
        };
        const updated = [
          saved,
          ...current.filter((item) => item.game_id !== saved.game_id),
        ].slice(0, 12);
        writeCampaigns(updated);
        return updated;
      });
    }
  }, [credentials]);

  useEffect(() => { void loadServerCampaigns(); }, [loadServerCampaigns]);
  useEffect(() => {
    if (!credentials || credentials.role === "player") return;
    void api.attachCampaignVault(
      credentials.token,
      DEVICE_VAULT_SECRET,
    ).then(loadServerCampaigns).catch(() => {
      setError(
        "Campaign cihaz kasasına bağlanamadı; mevcut oturum çalışmaya devam ediyor.",
      );
    });
  }, [credentials, loadServerCampaigns]);

  const refresh = useCallback(async () => {
    if (!credentials) return;
    try {
      acceptSnapshot(await api.snapshot(credentials.token));
      setError("");
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) {
        if (credentials.role !== "player") {
          try {
            const resumed = await api.resumeCampaignVault(
              DEVICE_VAULT_SECRET, credentials.game_id,
            );
            localStorage.setItem(STORAGE_KEY, JSON.stringify(resumed));
            setCredentials(resumed);
            setError("");
            return;
          } catch {
            // Fall through to the signed-out server campaign vault.
          }
        }
        localStorage.removeItem(STORAGE_KEY);
        setSavedCampaigns((current) => {
          const updated = current.filter(
            (item) => item.token !== credentials.token,
          );
          writeCampaigns(updated);
          return updated;
        });
        setCredentials(null);
        setSnapshot(null);
        await loadServerCampaigns();
        setError(
          "Oturum yenilenemedi. Sunucudaki Kampanyalarım listesinden tekrar dene.",
        );
        return;
      }
      setError(reason instanceof Error ? reason.message : "Baglanti kurulamadi");
    }
  }, [acceptSnapshot, credentials, loadServerCampaigns]);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    if (!credentials) return;
    let retry: number | undefined;
    let socket: WebSocket | undefined;
    let disposed = false;
    const connect = async () => {
      if (disposed) return;
      try {
        const { ticket } = await api.websocketTicket(credentials.token);
        if (disposed) return;
        socket = new WebSocket(
          api.websocketUrl(credentials, ticket, eventCursor.current),
        );
      } catch (reason) {
        if (disposed) return;
        setConnected(false);
        setError(
          reason instanceof Error ? reason.message : "Canlı bağlantı kurulamadı",
        );
        retry = window.setTimeout(connect, 1500);
        return;
      }
      socket.onopen = () => {
        if (!disposed) setConnected(true);
      };
      socket.onmessage = (message) => {
        if (disposed) return;
        try {
          const data = JSON.parse(message.data);
          if (data.kind === "snapshot") acceptSnapshot(data.snapshot);
          if (data.kind === "event") {
            eventCursor.current = Math.max(eventCursor.current, data.event.id);
            refresh();
          }
          if (data.kind === "catch_up") {
            eventCursor.current = Math.max(
              eventCursor.current,
              Number(data.next_cursor) || 0,
            );
            if (data.events?.length) refresh();
          }
        } catch {
          setError("Sunucudan gecersiz canli veri alindi");
        }
      };
      socket.onclose = () => {
        if (disposed) return;
        setConnected(false);
        retry = window.setTimeout(connect, 1500);
      };
    };
    connect();
    return () => {
      disposed = true;
      if (retry) clearTimeout(retry);
      if (socket) socket.onclose = null;
      socket?.close();
    };
  }, [acceptSnapshot, credentials, refresh]);

  const authenticate = (value: Credentials) => {
    const persisted: Credentials = {
      game_id: value.game_id,
      campaign_id: value.campaign_id,
      session_id: value.session_id,
      token: value.token,
      token_expires_at: value.token_expires_at,
      role: value.role,
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(persisted));
    if (persisted.role !== "player") {
      setSavedCampaigns((current) => {
        const previous = current.find(
          (item) => item.game_id === persisted.game_id,
        );
        const saved: SavedCampaign = {
          ...persisted,
          name: previous?.name ?? persisted.game_id,
          is_owner: previous?.is_owner ?? persisted.role === "dm",
          last_opened_at: new Date().toISOString(),
        };
        const updated = [
          saved,
          ...current.filter((item) => item.game_id !== saved.game_id),
        ].slice(0, 12);
        writeCampaigns(updated);
        return updated;
      });
    }
    setCredentials(persisted);
  };
  const clearActiveCredentials = () => {
    localStorage.removeItem(STORAGE_KEY);
    setCredentials(null);
    setSnapshot(null);
    eventCursor.current = 0;
  };
  const forgetSavedCampaign = (gameId: string) => {
    setSavedCampaigns((current) => {
      const updated = current.filter((item) => item.game_id !== gameId);
      writeCampaigns(updated);
      return updated;
    });
  };
  const clearLocalCredentials = () => {
    if (credentials) forgetSavedCampaign(credentials.game_id);
    clearActiveCredentials();
  };
  const deleteSavedCampaign = async (
    saved: SavedCampaign,
    confirmation: string,
  ) => {
    const current = await api.snapshot(saved.token);
    if (!current.me.is_owner) {
      throw new Error("Campaign'i yalnızca kalıcı sahibi silebilir.");
    }
    const expected = `${current.game.id}:${current.game.name}`;
    if (confirmation !== expected) {
      throw new Error(`Silmek için tam olarak “${expected}” yaz.`);
    }
    await api.deleteCampaign(saved.token, confirmation);
    forgetSavedCampaign(saved.game_id);
    if (credentials?.game_id === saved.game_id) clearActiveCredentials();
  };
  const logout = async () => {
    const current = credentials;
    if (!current) return;
    try {
      await api.logout(current.token);
      clearLocalCredentials();
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) {
        clearLocalCredentials();
        return;
      }
      setError(
        reason instanceof Error
          ? `${reason.message} Token sunucuda iptal edilmedi; tekrar deneyin.`
          : "Token sunucuda iptal edilmedi; tekrar deneyin.",
      );
    }
  };
  const rotateSession = async () => {
    const current = credentials;
    if (!current) return;
    try {
      const rotated = await api.rotateToken(current.token);
      authenticate({ ...current, ...rotated });
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Oturum yenilenemedi");
    }
  };

  const resumeServerCampaign = async (campaign: ServerCampaign) => {
    const resumed = await api.resumeCampaignVault(
      DEVICE_VAULT_SECRET, campaign.game_id,
    );
    authenticate(resumed);
  };

  const detachServerCampaign = async (gameId: string) => {
    await api.detachCampaignVault(DEVICE_VAULT_SECRET, gameId);
    await loadServerCampaigns();
  };

  if (!credentials) return (
    <JoinScreen
      onAuthenticated={authenticate}
      savedCampaigns={savedCampaigns.filter(
        (saved) => !serverCampaigns.some(
          (campaign) => campaign.game_id === saved.game_id,
        ),
      )}
      serverCampaigns={serverCampaigns}
      serverCampaignsLoading={campaignVaultLoading}
      onResumeServer={resumeServerCampaign}
      onDetachServer={detachServerCampaign}
      onResume={authenticate}
      onForget={forgetSavedCampaign}
      onDelete={deleteSavedCampaign}
    />
  );
  if (!snapshot) return <div className="center-state"><Swords size={30} /><p>Oyun masasi yukleniyor...</p>{error && <span>{error}</span>}</div>;

  const dmWorkspace = snapshot.me.role !== "player";
  const roleLabel = snapshot.me.role === "dm" ? "Dungeon Master" : snapshot.me.role === "co_dm" ? "Co-DM" : "Player";
  const characterCreationRequired =
    !dmWorkspace && snapshot.me.character_creation_required;
  const builderVisible =
    !dmWorkspace && (characterCreationRequired || builderOpen);
  const workspaceLocked =
    builderVisible || campaignOpen || sessionOpen || encounterOpen;

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Ana içeriğe geç</a>
      <header className="topbar">
        <div className="brand"><Swords size={22} /><strong>{snapshot.game.name}</strong></div>
        <div className="topbar-meta">
          <span role="status" className={`connection ${connected ? "online" : ""}`}>{connected ? "Canli" : "Baglaniyor"}</span>
          <span className="role-label">{dmWorkspace ? <Shield size={16} /> : <UserRound size={16} />}{roleLabel}</span>
          <button className="campaign-launch" disabled={workspaceLocked} onClick={() => setCampaignOpen(true)}><Castle size={16} /> Campaign</button>
          <button className="session-launch" disabled={workspaceLocked} onClick={() => setSessionOpen(true)}><BookOpenText size={16} /> Session</button>
          {dmWorkspace && <button className="encounter-launch" disabled={workspaceLocked} onClick={() => setEncounterOpen(true)}><Library size={16} /> Encounters</button>}
          {dmWorkspace && (
            <button
              className="campaign-switcher"
              disabled={workspaceLocked}
              onClick={clearActiveCredentials}
            >
              <FolderClock size={16} /> Campaignler
            </button>
          )}
          {!dmWorkspace && !characterCreationRequired && <button className="builder-launch" disabled={campaignOpen || sessionOpen || encounterOpen} onClick={() => setBuilderOpen(true)}><Sparkles size={16} /> Karakter oluştur</button>}
          <button
            className="icon-button"
            onClick={rotateSession}
            title={workspaceLocked ? "Açık düzenleyici kapatıldıktan sonra oturumu yenile" : "Oturum token'ını yenile"}
            aria-label="Oturum token'ını yenile"
            disabled={workspaceLocked && !characterCreationRequired}
          >
            <RefreshCw size={18} />
          </button>
          <button
            className="icon-button"
            onClick={logout}
            title={workspaceLocked ? "Açık düzenleyici kapatıldıktan sonra oturumdan çık" : "Oturumdan çık"}
            aria-label="Oturumdan çık"
            disabled={workspaceLocked && !characterCreationRequired}
          >
            <LogOut size={18} />
          </button>
        </div>
      </header>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {builderVisible ? (
        <CharacterBuilder
          snapshot={snapshot}
          token={credentials.token}
          required={characterCreationRequired}
          onClose={() => {
            if (!characterCreationRequired) setBuilderOpen(false);
          }}
          onPublished={refresh}
        />
      ) : campaignOpen ? (
        <CampaignDashboard
          snapshot={snapshot}
          token={credentials.token}
          onClose={() => setCampaignOpen(false)}
          onRefresh={refresh}
        />
      ) : sessionOpen ? (
        <SessionWorkspace
          snapshot={snapshot}
          token={credentials.token}
          onClose={() => setSessionOpen(false)}
          onRefresh={refresh}
        />
      ) : encounterOpen && dmWorkspace ? (
        <EncounterLibrary
          snapshot={snapshot}
          token={credentials.token}
          onClose={() => setEncounterOpen(false)}
          onRefresh={refresh}
        />
      ) : dmWorkspace ? (
        <DMConsole
          snapshot={snapshot}
          token={credentials.token}
          initialInviteCode={credentials.invite_code}
          onError={setError}
        />
      ) : (
        <PlayerConsole
          snapshot={snapshot}
          token={credentials.token}
          onError={setError}
          onRefresh={refresh}
        />
      )}
      {!builderVisible && !campaignOpen && !sessionOpen && !encounterOpen && <DiceRoller
      token={credentials.token}
      revision={snapshot.revision}
      actorCharacterId={snapshot.me.character_id}
      onError={setError}
      onRefresh={refresh}
      />}
    </div>
  );
}
