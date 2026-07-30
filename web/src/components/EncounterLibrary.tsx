import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CirclePause, CirclePlay, Copy, Library, Plus, Save, Square, Trash2, X,
} from "lucide-react";
import { api, ApiError } from "../api";
import type {
  CommandResponse, EncounterCombatantDraft, EncounterDraft, Snapshot,
} from "../types";

interface Props {
  snapshot: Snapshot;
  token: string;
  onClose: () => void;
  onRefresh: () => Promise<void>;
}

const manualDraft = (
  name: string,
  initiative: number,
  hp: number,
  armorClass: number,
  kind: "monster" | "npc",
  hidden: boolean,
): EncounterCombatantDraft => ({
  id: crypto.randomUUID(),
  source: { type: "manual", id: null },
  name: name.trim(),
  kind,
  initiative,
  hp,
  max_hp: hp,
  armor_class: armorClass,
  hidden,
});

export default function EncounterLibrary({
  snapshot, token, onClose, onRefresh,
}: Props) {
  const [encounters, setEncounters] = useState<EncounterDraft[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [draft, setDraft] = useState<EncounterDraft["data"] | null>(null);
  const [draftRevision, setDraftRevision] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [messageIsError, setMessageIsError] = useState(false);
  const [conflicted, setConflicted] = useState(false);
  const [newName, setNewName] = useState("New Encounter");
  const [manualName, setManualName] = useState("Goblin");
  const [manualInitiative, setManualInitiative] = useState(10);
  const [manualHp, setManualHp] = useState(7);
  const [manualAc, setManualAc] = useState(15);
  const [manualKind, setManualKind] = useState<"monster" | "npc">("monster");
  const [manualHidden, setManualHidden] = useState(false);
  const [characterId, setCharacterId] = useState("");
  const busyRef = useRef(false);
  const mountedRef = useRef(true);
  const loadSequenceRef = useRef(0);
  const revisionRef = useRef(snapshot.revision);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  useEffect(() => {
    revisionRef.current = Math.max(revisionRef.current, snapshot.revision);
  }, [snapshot.revision]);

  const selectDraft = useCallback((item: EncounterDraft) => {
    setSelectedId(item.id);
    setDraft(structuredClone(item.data));
    setDraftRevision(item.revision);
    setDirty(false);
    setConflicted(false);
  }, []);

  const load = useCallback(async (preferredId?: string, force = false) => {
    const sequence = ++loadSequenceRef.current;
    const result = await api.encounterLibrary(token);
    if (!mountedRef.current || sequence !== loadSequenceRef.current) return;
    revisionRef.current = Math.max(revisionRef.current, result.revision);
    setEncounters(result.encounters);
    const target = result.encounters.find(
      (item) => item.id === (preferredId || selectedId),
    ) || result.encounters[0];
    if (target && (force || !dirty)) selectDraft(target);
    if (!target) {
      setSelectedId("");
      setDraft(null);
      setDraftRevision(0);
    }
  }, [dirty, selectDraft, selectedId, token]);

  useEffect(() => {
    let active = true;
    const sequence = ++loadSequenceRef.current;
    api.encounterLibrary(token).then((result) => {
      if (
        !active
        || !mountedRef.current
        || sequence !== loadSequenceRef.current
      ) return;
      revisionRef.current = Math.max(revisionRef.current, result.revision);
      setEncounters(result.encounters);
      if (result.encounters[0]) selectDraft(result.encounters[0]);
    }).catch((error) => {
      if (
        active
        && mountedRef.current
        && sequence === loadSequenceRef.current
      ) {
        setMessageIsError(true);
        setMessage(error instanceof Error ? error.message : "Encounter library yüklenemedi");
      }
    });
    return () => { active = false; };
  }, [selectDraft, token]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const send = async (
    key: string,
    type: string,
    payload: Record<string, unknown>,
  ) => {
    if (busyRef.current) return null;
    busyRef.current = true;
    setBusy(key);
    setMessage("");
    setMessageIsError(false);
    try {
      const result = await api.command<CommandResponse>(
        token, type, payload, revisionRef.current,
      );
      revisionRef.current = result.revision;
      await onRefresh();
      if (!mountedRef.current) return null;
      return result;
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        await onRefresh();
        await load();
        if (mountedRef.current) setConflicted(true);
      }
      if (mountedRef.current) {
        setMessageIsError(true);
        setMessage(error instanceof Error ? error.message : "İşlem tamamlanamadı");
      }
      return null;
    } finally {
      busyRef.current = false;
      if (mountedRef.current) setBusy("");
    }
  };

  const mutateDraft = (next: EncounterDraft["data"]) => {
    if (busyRef.current || !canControl) return;
    setDraft(next);
    setDirty(true);
  };
  const characters = useMemo(
    () => Object.values(snapshot.state.characters),
    [snapshot.state.characters],
  );
  const active = snapshot.state.encounter_status === "active";
  const paused = snapshot.state.encounter_status === "paused";
  const canControl = snapshot.game.active_dm_id === snapshot.me.member_id;
  const locked = Boolean(busy) || !canControl;
  const rosterFull = Boolean(draft && draft.combatants.length >= 200);
  const duplicateCharacter = Boolean(
    draft
    && characterId
    && draft.combatants.some(
      (item) => item.source.type === "character" && item.source.id === characterId,
    ),
  );
  const manualValid = (
    manualName.trim().length > 0
    && Number.isInteger(manualInitiative)
    && manualInitiative >= -100
    && manualInitiative <= 100
    && Number.isInteger(manualHp)
    && manualHp >= 1
    && manualHp <= 1_000_000
    && Number.isInteger(manualAc)
    && manualAc >= 0
    && manualAc <= 100
  );

  const close = () => {
    if (!dirty || window.confirm("Kaydedilmemiş encounter değişiklikleri silinsin mi?")) {
      onClose();
    }
  };

  return (
    <main id="main-content" className="encounter-library">
      <header className="workspace-header">
        <div><span className="eyebrow">DM Workspace</span><h1><Library /> Encounter Library</h1></div>
        <div className="button-row">
          {active && <button disabled={locked} onClick={() => void send("pause", "pause_encounter", {})}><CirclePause /> Duraklat</button>}
          {paused && <button disabled={locked} onClick={() => void send("resume", "resume_encounter", {})}><CirclePlay /> Devam et</button>}
          {(active || paused) && <button className="danger-button" disabled={locked} onClick={() => {
            if (window.confirm("Canlı encounter tamamlansın mı?")) void send("complete", "complete_encounter", {});
          }}><Square /> Tamamla</button>}
          <button className="icon-button" aria-label="Encounter library ekranını kapat" disabled={Boolean(busy)} onClick={close}><X /></button>
        </div>
      </header>
      {message && <div className="campaign-message" role={messageIsError ? "alert" : "status"}>{message}</div>}
      {conflicted && draft && <div className="campaign-message" role="alert">
        Taslak sunucuda değişti. Yerel değişikliklerinizi inceleyin veya{" "}
        <button disabled={Boolean(busy)} onClick={() => void load(selectedId, true)}>
          sunucu sürümünü yükle
        </button>.
      </div>}
      <div className="encounter-layout">
        <aside className="encounter-sidebar">
          <form onSubmit={(event) => {
            event.preventDefault();
            void (async () => {
              const result = await send("create", "create_encounter_draft", { name: newName, description: "" });
              const id = result?.event.payload.encounter_id;
              if (typeof id === "string") {
                setDirty(false);
                await load(id, true);
              }
            })();
          }}>
            <label>Yeni encounter adı<input maxLength={120} required disabled={locked} value={newName} onChange={(event) => setNewName(event.target.value)} /></label>
            <button className="primary-button" disabled={locked || !newName.trim()}><Plus /> Oluştur</button>
          </form>
          <nav aria-label="Kayıtlı encounterlar">
            {encounters.map((item) => <button key={item.id} aria-current={item.id === selectedId ? "page" : undefined} className={item.id === selectedId ? "active" : ""} onClick={() => {
              if (!dirty || window.confirm("Kaydedilmemiş değişiklikler silinsin mi?")) selectDraft(item);
            }}>
              <strong>{item.data.name}</strong>
              <small>{item.data.combatants.length} combatant · v{item.revision}</small>
            </button>)}
          </nav>
          {!encounters.length && <p className="empty-state">Henüz kayıtlı encounter yok.</p>}
        </aside>

        <section className="encounter-builder">
          {!draft ? <div className="center-state"><Library /><p>Bir encounter oluşturun.</p></div> : <>
            <div className="builder-title-row">
              <div>
                <span className="eyebrow">Draft v{draftRevision}{dirty ? " · unsaved" : ""}</span>
                <input aria-label="Encounter adı" maxLength={120} disabled={locked} value={draft.name} onChange={(event) => mutateDraft({ ...draft, name: event.target.value })} />
              </div>
              <div className="button-row">
                <button disabled={locked || dirty} onClick={() => void (async () => {
                  const result = await send("duplicate", "duplicate_encounter_draft", { encounter_id: selectedId });
                  const id = result?.event.payload.encounter_id;
                  if (typeof id === "string") await load(id, true);
                })()}><Copy /> Duplicate</button>
                <button className="primary-button" disabled={locked || !dirty || !draft.name.trim()} onClick={() => void (async () => {
                  const result = await send("save", "update_encounter_draft", {
                    encounter_id: selectedId, draft_revision: draftRevision,
                    patch: { name: draft.name, description: draft.description, combatants: draft.combatants },
                  });
                  if (result) {
                    setDirty(false);
                    await load(selectedId, true);
                  }
                })()}><Save /> Kaydet</button>
                <button disabled={locked || dirty || active || paused || !draft.combatants.length} onClick={() => void send("start", "start_saved_encounter", {
                  encounter_id: selectedId, draft_revision: draftRevision,
                })}><CirclePlay /> Başlat</button>
              </div>
            </div>
            <label className="encounter-description">Açıklama<textarea maxLength={2000} disabled={locked} value={draft.description} onChange={(event) => mutateDraft({ ...draft, description: event.target.value })} /></label>

            <div className="encounter-source-grid">
              <form onSubmit={(event: FormEvent) => {
                event.preventDefault();
                if (!manualValid || rosterFull) return;
                mutateDraft({
                  ...draft,
                  combatants: [...draft.combatants, manualDraft(
                    manualName, manualInitiative, manualHp, manualAc,
                    manualKind, manualHidden,
                  )],
                });
              }}>
                <h2>Manual combatant</h2>
                <label>Ad<input required maxLength={80} disabled={locked} value={manualName} onChange={(event) => setManualName(event.target.value)} /></label>
                <div className="encounter-stat-inputs">
                  <label>Initiative<input disabled={locked} type="number" min={-100} max={100} value={manualInitiative} onChange={(event) => setManualInitiative(Number(event.target.value))} /></label>
                  <label>HP<input disabled={locked} type="number" min={1} max={1000000} value={manualHp} onChange={(event) => setManualHp(Number(event.target.value))} /></label>
                  <label>AC<input disabled={locked} type="number" min={0} max={100} value={manualAc} onChange={(event) => setManualAc(Number(event.target.value))} /></label>
                </div>
                <div className="inline-form">
                  <label>Tür<select disabled={locked} value={manualKind} onChange={(event) => setManualKind(event.target.value as typeof manualKind)}><option value="monster">Monster</option><option value="npc">NPC</option></select></label>
                  <label className="check-label"><input disabled={locked} type="checkbox" checked={manualHidden} onChange={(event) => setManualHidden(event.target.checked)} /> Hidden</label>
                  <button disabled={locked || !manualValid || rosterFull}><Plus /> Ekle</button>
                </div>
              </form>
              <form onSubmit={(event) => {
                event.preventDefault();
                const character = snapshot.state.characters[characterId];
                if (!character || draft.combatants.some((item) => item.source.type === "character" && item.source.id === characterId)) return;
                mutateDraft({
                  ...draft,
                  combatants: [...draft.combatants, {
                    id: crypto.randomUUID(),
                    source: { type: "character", id: characterId },
                    name: character.name, kind: "player",
                    initiative: "derived" in character ? character.derived.initiative : 0,
                    hp: character.hp, max_hp: character.max_hp,
                    armor_class: "ac" in character ? character.ac ?? 10 : 10,
                    hidden: false,
                  }],
                });
              }}>
                <h2>Character kaynağı</h2>
                <label>Karakter<select disabled={locked} value={characterId} onChange={(event) => setCharacterId(event.target.value)}>
                  <option value="">Seçin</option>
                  {characters.map((character) => <option key={character.id} value={character.id}>{character.name}</option>)}
                </select></label>
                <p>Başlatılırken güncel HP, AC ve initiative değerleri authoritative character sheet'ten alınır.</p>
                <button disabled={locked || !characterId || duplicateCharacter || rosterFull}><Plus /> Character ekle</button>
              </form>
            </div>

            <div className="encounter-roster">
              <h2>Combatant roster</h2>
              {draft.combatants.map((item) => <article key={item.id}>
                <span className="initiative">{item.initiative}</span>
                <div><strong>{item.name}</strong><small>{item.source.type} · {item.kind}{item.hidden ? " · hidden" : ""}</small></div>
                <span>{item.hp}/{item.max_hp} HP · AC {item.armor_class}</span>
                <button className="icon-button" aria-label={`${item.name} combatant'ını kaldır`} disabled={locked} onClick={() => mutateDraft({ ...draft, combatants: draft.combatants.filter((entry) => entry.id !== item.id) })}><Trash2 /></button>
              </article>)}
              {!draft.combatants.length && <p className="empty-state">Encounter'a combatant ekleyin.</p>}
            </div>
          </>}
        </section>
      </div>
    </main>
  );
}
