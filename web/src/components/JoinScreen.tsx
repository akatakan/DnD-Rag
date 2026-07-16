import { FormEvent, useState } from "react";
import { Shield, Swords, UserRound } from "lucide-react";
import { api } from "../api";
import type { Credentials, DMMode } from "../types";

export default function JoinScreen({ onAuthenticated }: { onAuthenticated: (value: Credentials) => void }) {
  const [mode, setMode] = useState<"join" | "create">("join");
  const [name, setName] = useState("");
  const [gameName, setGameName] = useState("Friday Night Adventure");
  const [invite, setInvite] = useState("");
  const [dmMode, setDmMode] = useState<DMMode>("human");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const result = mode === "create"
        ? await api.createGame(gameName, name, dmMode)
        : await api.joinGame(invite, name);
      onAuthenticated(result);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "İşlem tamamlanamadı");
    } finally { setBusy(false); }
  }

  return (
    <main className="join-layout">
      <section className="join-heading">
        <Swords size={34} />
        <h1>D&D Table</h1>
        <p>Canlı oyun masasına kendi rolünle bağlan.</p>
      </section>
      <form className="join-form" onSubmit={submit}>
        <div className="segmented">
          <button type="button" className={mode === "join" ? "active" : ""} onClick={() => setMode("join")}><UserRound size={17} /> Oyuncu olarak katıl</button>
          <button type="button" className={mode === "create" ? "active" : ""} onClick={() => setMode("create")}><Shield size={17} /> Oyun oluştur</button>
        </div>
        <label>Adın<input value={name} onChange={(event) => setName(event.target.value)} required /></label>
        {mode === "join" ? (
          <label>Davet kodu<input value={invite} onChange={(event) => setInvite(event.target.value.toUpperCase())} required /></label>
        ) : <>
          <label>Oyun adı<input value={gameName} onChange={(event) => setGameName(event.target.value)} required /></label>
          <label>DM modu<select value={dmMode} onChange={(event) => setDmMode(event.target.value as DMMode)}><option value="human">Human DM</option><option value="assisted">Assisted DM</option><option value="ai">AI DM</option></select></label>
        </>}
        {error && <div className="form-error">{error}</div>}
        <button className="primary-button" disabled={busy}>{busy ? "Bağlanıyor..." : mode === "join" ? "Masaya katıl" : "Oyunu başlat"}</button>
      </form>
    </main>
  );
}
