import { FormEvent, useState } from "react";
import { Clock3, Shield, Swords, Trash2, UserRound, X } from "lucide-react";
import { api } from "../api";
import type { Credentials, DMMode, SavedCampaign } from "../types";

export default function JoinScreen({
  onAuthenticated,
  savedCampaigns,
  onResume,
  onForget,
  onDelete,
}: {
  onAuthenticated: (value: Credentials) => void;
  savedCampaigns: SavedCampaign[];
  onResume: (value: Credentials) => void;
  onForget: (gameId: string) => void;
  onDelete: (campaign: SavedCampaign, confirmation: string) => Promise<void>;
}) {
  const [mode, setMode] = useState<"join" | "create">("join");
  const [name, setName] = useState("");
  const [gameName, setGameName] = useState("Friday Night Adventure");
  const [invite, setInvite] = useState("");
  const [dmMode, setDmMode] = useState<DMMode>("human");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [deleteGameId, setDeleteGameId] = useState("");
  const [confirmation, setConfirmation] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = mode === "create"
        ? await api.createGame(gameName, name, dmMode)
        : await api.joinGame(invite, name);
      onAuthenticated(result);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "İşlem tamamlanamadı");
    } finally {
      setBusy(false);
    }
  }

  async function deleteCampaign(campaign: SavedCampaign) {
    setBusy(true);
    setError("");
    try {
      await onDelete(campaign, confirmation);
      setDeleteGameId("");
      setConfirmation("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Campaign silinemedi.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="join-layout">
      <section className="join-heading">
        <Swords size={34} />
        <h1>D&D Table</h1>
        <p>Canlı oyun masasına kendi rolünle bağlan.</p>
      </section>

      {savedCampaigns.length > 0 && (
        <section className="saved-campaigns" aria-labelledby="saved-campaigns-title">
          <div className="saved-campaigns-heading">
            <div>
              <span className="eyebrow">Bu cihaz</span>
              <h2 id="saved-campaigns-title">Kayıtlı campaignler</h2>
            </div>
            <small>Token geçerliyken kaldığın yerden devam edebilirsin.</small>
          </div>
          <div className="saved-campaign-list">
            {savedCampaigns.map((campaign) => {
              const deleting = deleteGameId === campaign.game_id;
              const expectedConfirmation = `${campaign.game_id}:${campaign.name}`;
              return (
                <article className="saved-campaign-card" key={campaign.game_id}>
                  <div>
                    <strong>{campaign.name}</strong>
                    <span>
                      <Clock3 size={14} />
                      {new Date(campaign.last_opened_at).toLocaleString("tr-TR")}
                    </span>
                  </div>
                  <div className="saved-campaign-actions">
                    <button
                      className="primary-button"
                      disabled={busy}
                      onClick={() => onResume(campaign)}
                    >
                      Devam et
                    </button>
                    <button
                      className="icon-button"
                      disabled={busy}
                      aria-label={`${campaign.name} kaydını bu cihazdan kaldır`}
                      title="Yalnız bu cihazdaki kaydı kaldır"
                      onClick={() => onForget(campaign.game_id)}
                    >
                      <X size={17} />
                    </button>
                    {campaign.is_owner && (
                      <button
                        className="icon-button danger"
                        disabled={busy}
                        aria-label={`${campaign.name} campaignini kalıcı olarak sil`}
                        title="Campaigni sunucudan kalıcı olarak sil"
                        onClick={() => {
                          setDeleteGameId(deleting ? "" : campaign.game_id);
                          setConfirmation("");
                        }}
                      >
                        <Trash2 size={17} />
                      </button>
                    )}
                  </div>
                  {deleting && (
                    <div className="saved-campaign-delete">
                      <p>
                        Bu işlem campaigni, üyelikleri ve oturumları kalıcı siler.
                        Onay için <code>{expectedConfirmation}</code> yaz.
                      </p>
                      <input
                        aria-label={`${campaign.name} silme onayı`}
                        value={confirmation}
                        onChange={(event) => setConfirmation(event.target.value)}
                        autoComplete="off"
                      />
                      <button
                        className="danger-button"
                        disabled={busy || confirmation !== expectedConfirmation}
                        onClick={() => void deleteCampaign(campaign)}
                      >
                        Kalıcı olarak sil
                      </button>
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        </section>
      )}

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
