import { useCallback, useEffect, useState } from "react";
import { LogOut, Shield, Swords, UserRound } from "lucide-react";
import { api } from "./api";
import DMConsole from "./components/DMConsole";
import JoinScreen from "./components/JoinScreen";
import PlayerConsole from "./components/PlayerConsole";
import type { Credentials, Snapshot } from "./types";

const STORAGE_KEY = "dnd-table-credentials";

export default function App() {
  const [credentials, setCredentials] = useState<Credentials | null>(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored ? JSON.parse(stored) : null;
  });
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [connected, setConnected] = useState(false);

  const refresh = useCallback(async () => {
    if (!credentials) return;
    try {
      setSnapshot(await api.snapshot(credentials.token));
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Baglanti kurulamadi");
    }
  }, [credentials]);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    if (!credentials) return;
    let retry: number | undefined;
    let socket: WebSocket;
    const connect = () => {
      socket = new WebSocket(api.websocketUrl(credentials));
      socket.onopen = () => setConnected(true);
      socket.onmessage = (message) => {
        const data = JSON.parse(message.data);
        if (data.kind === "snapshot") setSnapshot(data.snapshot);
        if (data.kind === "event") refresh();
      };
      socket.onclose = () => {
        setConnected(false);
        retry = window.setTimeout(connect, 1500);
      };
    };
    connect();
    return () => { if (retry) clearTimeout(retry); socket?.close(); };
  }, [credentials, refresh]);

  const authenticate = (value: Credentials) => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
    setCredentials(value);
  };
  const logout = () => {
    localStorage.removeItem(STORAGE_KEY);
    setCredentials(null);
    setSnapshot(null);
  };

  if (!credentials) return <JoinScreen onAuthenticated={authenticate} />;
  if (!snapshot) return <div className="center-state"><Swords size={30} /><p>Oyun masasi yukleniyor...</p>{error && <span>{error}</span>}</div>;

  const dmWorkspace = snapshot.me.role !== "player";
  const roleLabel = snapshot.me.role === "dm" ? "Dungeon Master" : snapshot.me.role === "co_dm" ? "Co-DM" : "Player";

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand"><Swords size={22} /><strong>{snapshot.game.name}</strong></div>
        <div className="topbar-meta">
          <span className={`connection ${connected ? "online" : ""}`}>{connected ? "Canli" : "Baglaniyor"}</span>
          <span className="role-label">{dmWorkspace ? <Shield size={16} /> : <UserRound size={16} />}{roleLabel}</span>
          <button className="icon-button" onClick={logout} title="Oturumdan cik"><LogOut size={18} /></button>
        </div>
      </header>
      {error && <div className="error-banner">{error}</div>}
      {dmWorkspace ? (
        <DMConsole snapshot={snapshot} token={credentials.token} onError={setError} />
      ) : (
        <PlayerConsole snapshot={snapshot} token={credentials.token} onError={setError} />
      )}
    </div>
  );
}
