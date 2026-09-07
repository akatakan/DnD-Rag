import { useCallback, useEffect, useState } from "react";
import { Plus, Swords, Trash2, UserRoundPlus } from "lucide-react";
import { api } from "../api";
import type { CampaignNpc, CommandResponse } from "../types";

type Draft = Omit<CampaignNpc, "id" | "updated_at">;

const BLANK: Draft = {
  name: "",
  kind: "monster",
  armor_class: 12,
  max_hp: 7,
  initiative_modifier: 0,
  speed: 30,
  notes: "",
};

/**
 * The DM's reusable stat blocks.
 *
 * Adding one to an encounter sends only its id: the server fills the name,
 * kind and hit points from the stored record and rolls initiative from the
 * saved modifier, so a block cannot drift from the one the table agreed on.
 */
export default function NpcLibrary({
  token,
  revision,
  onError,
  onRefresh,
}: {
  token: string;
  revision: number;
  onError: (value: string) => void;
  onRefresh: () => Promise<void>;
}) {
  const [npcs, setNpcs] = useState<CampaignNpc[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(BLANK);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setNpcs((await api.campaignNpcs(token)).npcs);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "NPC listesi alınamadı.");
    }
  }, [onError, token]);

  useEffect(() => { void load(); }, [load]);

  async function guard(work: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    try {
      await work();
      onError("");
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "İşlem tamamlanamadı.");
    } finally {
      setBusy(false);
    }
  }

  const save = () => guard(async () => {
    await api.saveCampaignNpc(token, draft, editingId ?? undefined);
    setDraft(BLANK);
    setEditingId(null);
    await load();
  });

  const addToEncounter = (npc: CampaignNpc) => guard(async () => {
    await api.command<CommandResponse>(
      token, "add_combatant", { npc_id: npc.id }, revision,
    );
    await onRefresh();
  });

  const number = (
    label: string,
    key: "armor_class" | "max_hp" | "initiative_modifier" | "speed",
    min: number,
    max: number,
  ) => (
    <label>
      {label}
      <input
        type="number"
        min={min}
        max={max}
        value={draft[key]}
        onChange={(event) => setDraft({
          ...draft,
          [key]: Math.max(min, Math.min(max, Math.trunc(Number(event.target.value) || 0))),
        })}
      />
    </label>
  );

  return (
    <section className="npc-library">
      <h2><Swords size={16} /> NPC kütüphanesi</h2>

      <div className="npc-form">
        <label>
          Ad
          <input
            maxLength={80}
            value={draft.name}
            onChange={(event) => setDraft({ ...draft, name: event.target.value })}
          />
        </label>
        <label>
          Tür
          <select
            value={draft.kind}
            onChange={(event) => setDraft({ ...draft, kind: event.target.value as Draft["kind"] })}
          >
            <option value="monster">Canavar</option>
            <option value="npc">NPC</option>
          </select>
        </label>
        {number("Armor Class", "armor_class", 0, 100)}
        {number("Max HP", "max_hp", 1, 1000000)}
        {number("Initiative mod", "initiative_modifier", -20, 20)}
        {number("Hız (ft)", "speed", 0, 1000)}
        <label className="npc-notes">
          Notlar
          <textarea
            maxLength={2000}
            value={draft.notes}
            onChange={(event) => setDraft({ ...draft, notes: event.target.value })}
          />
        </label>
        <div className="button-row">
          <button
            className="primary-button"
            disabled={busy || !draft.name.trim()}
            onClick={save}
          >
            <Plus size={16} /> {editingId ? "Değişikliği kaydet" : "NPC ekle"}
          </button>
          {editingId && (
            <button
              disabled={busy}
              onClick={() => { setEditingId(null); setDraft(BLANK); }}
            >
              Vazgeç
            </button>
          )}
        </div>
      </div>

      {npcs.length === 0
        ? <p className="empty-state">Henüz kayıtlı NPC yok.</p>
        : (
          <ul className="npc-list">
            {npcs.map((npc) => (
              <li key={npc.id}>
                <div>
                  <strong>{npc.name}</strong>
                  <small>
                    {npc.kind === "monster" ? "Canavar" : "NPC"} · AC {npc.armor_class}
                    {" · "}{npc.max_hp} HP · init {npc.initiative_modifier >= 0 ? "+" : ""}
                    {npc.initiative_modifier} · {npc.speed} ft
                  </small>
                  {npc.notes && <p>{npc.notes}</p>}
                </div>
                <div className="button-row">
                  <button disabled={busy} onClick={() => addToEncounter(npc)}>
                    <UserRoundPlus size={16} /> Encounter'a ekle
                  </button>
                  <button
                    disabled={busy}
                    onClick={() => { setEditingId(npc.id); setDraft({ ...npc }); }}
                  >
                    Düzenle
                  </button>
                  <button
                    className="icon-button"
                    aria-label={`${npc.name} kaydını sil`}
                    disabled={busy}
                    onClick={() => guard(async () => {
                      await api.deleteCampaignNpc(token, npc.id);
                      if (editingId === npc.id) { setEditingId(null); setDraft(BLANK); }
                      await load();
                    })}
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
    </section>
  );
}
