import { useCallback, useEffect, useRef, useState } from "react";
import { BookOpenText, Castle, Library, LogOut, RefreshCw, Shield, Sparkles, Swords, UserRound } from "lucide-react";
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
import type { Credentials, Snapshot } from "./types";

const STORAGE_KEY = "dnd-table-credentials";

function storedCredentials(): Credentials | null {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (!stored) return null;
    const value = JSON.parse(stored) as Partial<Credentials>;
    if (
      typeof value.game_id !== "string" ||
      typeof value.token !== "string" ||
      !["dm", "co_dm", "player"].includes(value.role ?? "")
    ) {
      localStorage.removeItem(STORAGE_KEY);
      return null;
    }
    return value as Credentials;
  } catch {
    localStorage.removeItem(STORAGE_KEY);
    return null;
  }
}

export default function App() {
  if (window.location.pathname === "/__developer/catalog") {
    return <DeveloperCatalog />;
  }
  return <GameApplication />;
}

function GameApplication() {
  const [credentials, setCredentials] = useState<Credentials | null>(storedCredentials);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [connected, setConnected] = useState(false);
  const [builderOpen, setBuilderOpen] = useState(false);
  const [campaignOpen, setCampaignOpen] = useState(false);
  const [sessionOpen, setSessionOpen] = useState(false);
  const [encounterOpen, setEncounterOpen] = useState(false);
  const eventCursor = useRef(0);

  const acceptSnapshot = useCallback((next: Snapshot) => {
    eventCursor.current = Math.max(eventCursor.current, next.event_cursor);
    setSnapshot((current) =>
      !current || next.revision >= current.revision ? next : current
    );
  }, []);

  const refresh = useCallback(async () => {
    if (!credentials) return;
    try {
      acceptSnapshot(await api.snapshot(credentials.token));
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Baglanti kurulamadi");
    }
  }, [acceptSnapshot, credentials]);

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
    const {
      invite_code: _inviteCode,
      invite_expires_at: _inviteExpiresAt,
      ...persisted
    } = value;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(persisted));
    setCredentials(value);
  };
  const clearLocalCredentials = () => {
    localStorage.removeItem(STORAGE_KEY);
    setCredentials(null);
    setSnapshot(null);
    eventCursor.current = 0;
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

  if (!credentials) return <JoinScreen onAuthenticated={authenticate} />;
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
