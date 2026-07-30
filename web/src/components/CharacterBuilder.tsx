import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Cloud,
  CloudAlert,
  LoaderCircle,
  RefreshCw,
  Sparkles,
  X,
} from "lucide-react";
import { api, ApiError } from "../api";
import type {
  CharacterAbility,
  CharacterDraft,
  CharacterDraftData,
  CharacterDraftStep,
  CharacterSkill,
  RulesCatalogEntry,
  RulesCatalogEntityType,
  Snapshot,
} from "../types";

const STEPS: Array<{ id: CharacterDraftStep; label: string }> = [
  { id: "basics", label: "Temel" },
  { id: "abilities", label: "Ability" },
  { id: "class", label: "Class" },
  { id: "species", label: "Species" },
  { id: "background", label: "Background" },
  { id: "proficiencies", label: "Yetenekler" },
  { id: "equipment", label: "Ekipman" },
  { id: "spells", label: "Spells" },
  { id: "review", label: "Kontrol" },
];
const ABILITIES: CharacterAbility[] = [
  "strength", "dexterity", "constitution",
  "intelligence", "wisdom", "charisma",
];
const SKILLS: CharacterSkill[] = [
  "acrobatics", "animal_handling", "arcana", "athletics", "deception",
  "history", "insight", "intimidation", "investigation", "medicine",
  "nature", "perception", "performance", "persuasion", "religion",
  "sleight_of_hand", "stealth", "survival",
];
type SaveState = "saved" | "pending" | "saving" | "conflict" | "error";

export default function CharacterBuilder({
  snapshot,
  token,
  onClose,
  onPublished,
}: {
  snapshot: Snapshot;
  token: string;
  onClose: () => void;
  onPublished: () => Promise<void>;
}) {
  const characterId = snapshot.me.character_id!;
  const [draft, setDraft] = useState<CharacterDraft | null>(null);
  const [catalog, setCatalog] = useState<RulesCatalogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saveState, setSaveState] = useState<SaveState>("saved");
  const [publishing, setPublishing] = useState(false);
  const [transitioning, setTransitioning] = useState(false);
  const [closing, setClosing] = useState(false);
  const revisionRef = useRef(0);
  const pendingRef = useRef<Partial<CharacterDraftData>>({});
  const savingRef = useRef<Promise<void> | null>(null);
  const blockedRef = useRef(false);
  const timerRef = useRef<number | null>(null);
  const mountedRef = useRef(true);
  const loadGenerationRef = useRef(0);
  const transitionRef = useRef(false);

  const load = useCallback(async () => {
    const generation = ++loadGenerationRef.current;
    setLoading(true);
    setError("");
    try {
      const [nextDraft, ...pages] = await Promise.all([
        api.createCharacterDraft(token, characterId),
        ...(["class", "species", "background", "item", "spell"] as RulesCatalogEntityType[])
          .map((type) => api.catalogEntries(
            token,
            snapshot.campaign.ruleset_version,
            { type, limit: 100 },
          )),
      ]);
      if (!mountedRef.current || generation !== loadGenerationRef.current) return;
      revisionRef.current = nextDraft.revision;
      setDraft(nextDraft);
      setCatalog(pages.flatMap((page) => page.entries));
      setSaveState("saved");
      blockedRef.current = false;
    } catch (reason) {
      if (!mountedRef.current || generation !== loadGenerationRef.current) return;
      setError(reason instanceof Error ? reason.message : "Builder yüklenemedi.");
    } finally {
      if (mountedRef.current && generation === loadGenerationRef.current) {
        setLoading(false);
      }
    }
  }, [characterId, snapshot.campaign.ruleset_version, token]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    mountedRef.current = true;
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      if (
        Object.keys(pendingRef.current).length > 0
        || savingRef.current !== null
      ) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", warnBeforeUnload);
    return () => {
      mountedRef.current = false;
      loadGenerationRef.current += 1;
      if (timerRef.current) window.clearTimeout(timerRef.current);
      window.removeEventListener("beforeunload", warnBeforeUnload);
    };
  }, []);

  const flush = useCallback(async (): Promise<void> => {
    if (savingRef.current) {
      await savingRef.current;
      if (Object.keys(pendingRef.current).length) await flush();
      return;
    }
    const patch = pendingRef.current;
    if (!Object.keys(patch).length || blockedRef.current) return;
    pendingRef.current = {};
    if (mountedRef.current) setSaveState("saving");
    const operation = (async () => {
      try {
        const saved = await api.saveCharacterDraft(
          token, characterId, revisionRef.current, patch,
        );
        revisionRef.current = saved.revision;
        if (mountedRef.current) {
          setDraft((current) => current ? {
            ...saved,
            data: { ...saved.data, ...pendingRef.current },
          } : saved);
          setSaveState(Object.keys(pendingRef.current).length ? "pending" : "saved");
        }
      } catch (reason) {
        pendingRef.current = { ...patch, ...pendingRef.current };
        if (reason instanceof ApiError && reason.status === 409) {
          blockedRef.current = true;
          if (mountedRef.current) setSaveState("conflict");
          if (mountedRef.current) {
            setError("Taslak başka bir sekmede değişti. Yerel seçimlerin üzerine yazılmadı.");
          }
        } else {
          blockedRef.current = true;
          if (mountedRef.current) {
            setSaveState("error");
            setError(reason instanceof Error ? reason.message : "Autosave başarısız.");
          }
        }
      }
    })();
    savingRef.current = operation;
    await operation;
    savingRef.current = null;
    if (Object.keys(pendingRef.current).length && !blockedRef.current) {
      await flush();
    }
  }, [characterId, token]);

  function change<K extends keyof CharacterDraftData>(
    field: K, value: CharacterDraftData[K],
  ) {
    if (!draft || draft.status === "published") return;
    setDraft((current) => current ? {
      ...current,
      data: { ...current.data, [field]: value },
    } : current);
    pendingRef.current = { ...pendingRef.current, [field]: value };
    setSaveState("pending");
    if (timerRef.current) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => { void flush(); }, 650);
  }

  async function navigate(direction: "next" | "previous") {
    if (!draft || transitionRef.current) return;
    transitionRef.current = true;
    setTransitioning(true);
    if (timerRef.current) window.clearTimeout(timerRef.current);
    try {
      await flush();
      if (blockedRef.current) return;
      const next = await api.navigateCharacterDraft(
        token, characterId, revisionRef.current, direction,
      );
      if (!mountedRef.current) return;
      revisionRef.current = next.revision;
      setDraft(next);
      setError("");
      setSaveState("saved");
    } catch (reason) {
      if (!mountedRef.current) return;
      if (reason instanceof ApiError && reason.status === 409) {
        setSaveState("conflict");
        blockedRef.current = true;
      }
      setError(reason instanceof Error ? reason.message : "Adım değiştirilemedi.");
    } finally {
      transitionRef.current = false;
      if (mountedRef.current) setTransitioning(false);
    }
  }

  async function useServerDraft() {
    if (transitionRef.current) return;
    transitionRef.current = true;
    setTransitioning(true);
    setLoading(true);
    try {
      const fresh = await api.getCharacterDraft(token, characterId);
      if (!mountedRef.current) return;
      pendingRef.current = {};
      revisionRef.current = fresh.revision;
      setDraft(fresh);
      setSaveState("saved");
      blockedRef.current = false;
      setError("");
    } catch (reason) {
      if (mountedRef.current) {
        setError(reason instanceof Error ? reason.message : "Taslak yenilenemedi.");
      }
    } finally {
      transitionRef.current = false;
      if (mountedRef.current) {
        setLoading(false);
        setTransitioning(false);
      }
    }
  }

  async function retryPendingChanges() {
    if (transitionRef.current) return;
    transitionRef.current = true;
    setTransitioning(true);
    try {
      if (!Object.keys(pendingRef.current).length) {
        blockedRef.current = false;
        setSaveState("saved");
        setError("");
        return;
      }
      if (saveState === "conflict") {
        const fresh = await api.getCharacterDraft(token, characterId);
        if (!mountedRef.current) return;
        if (fresh.status === "published") {
          setDraft(fresh);
          pendingRef.current = {};
          blockedRef.current = true;
          setSaveState("conflict");
          setError("Taslak bu sırada yayınlandı; yerel değişiklikler uygulanamaz.");
          return;
        }
        revisionRef.current = fresh.revision;
        setDraft({
          ...fresh,
          data: { ...fresh.data, ...pendingRef.current },
        });
      }
      blockedRef.current = false;
      setError("");
      setSaveState("pending");
      await flush();
    } catch (reason) {
      if (!mountedRef.current) return;
      blockedRef.current = true;
      setSaveState("error");
      setError(
        reason instanceof Error ? reason.message : "Autosave yeniden denenemedi.",
      );
    } finally {
      transitionRef.current = false;
      if (mountedRef.current) setTransitioning(false);
    }
  }

  async function closeBuilder() {
    if (transitionRef.current) return;
    transitionRef.current = true;
    setClosing(true);
    if (timerRef.current) window.clearTimeout(timerRef.current);
    try {
      await flush();
      if (!blockedRef.current) onClose();
    } finally {
      transitionRef.current = false;
      if (mountedRef.current) setClosing(false);
    }
  }

  async function publish() {
    if (!draft || transitionRef.current) return;
    transitionRef.current = true;
    setTransitioning(true);
    setPublishing(true);
    try {
      await flush();
      if (blockedRef.current) return;
      await api.command(
        token,
        "publish_character_draft",
        { draft_revision: revisionRef.current },
        snapshot.revision,
      );
      await onPublished();
      onClose();
    } catch (reason) {
      if (mountedRef.current) {
        setError(reason instanceof Error ? reason.message : "Karakter yayınlanamadı.");
      }
    } finally {
      transitionRef.current = false;
      if (mountedRef.current) {
        setPublishing(false);
        setTransitioning(false);
      }
    }
  }

  const byType = useMemo(() => {
    const result = new Map<string, RulesCatalogEntry[]>();
    for (const entry of catalog) {
      result.set(entry.type, [...(result.get(entry.type) ?? []), entry]);
    }
    return result;
  }, [catalog]);

  if (loading) return (
    <main className="builder-state" id="main-content">
      <LoaderCircle className="rolling-icon" />
      <h1>Karakter oluşturucu hazırlanıyor</h1>
      <p>Kurallar kataloğu ve taslağın getiriliyor.</p>
    </main>
  );
  if (!draft) return (
    <main className="builder-state" id="main-content">
      <CloudAlert />
      <h1>Builder açılamadı</h1>
      <p>{error || "Taslak bulunamadı."}</p>
      <button className="primary-button" onClick={load}><RefreshCw size={17} /> Tekrar dene</button>
    </main>
  );
  if (draft.status === "published") return (
    <main className="builder-state" id="main-content">
      <Check />
      <h1>Karakter yayınlandı</h1>
      <p>Bu taslak tamamlandı. Karakter kağıdından oynamaya devam edebilirsin.</p>
      <button className="primary-button" onClick={onClose}>Karakter kağıdına dön</button>
    </main>
  );

  const stepIndex = STEPS.findIndex((step) => step.id === draft.current_step);
  return (
    <main className="builder-shell" id="main-content">
      <header className="builder-header">
        <div>
          <span className="eyebrow">Character Builder</span>
          <h1>{draft.data.name.trim() || "Yeni Kahraman"}</h1>
          <SaveIndicator state={saveState} />
        </div>
        <button
          className="icon-button"
          onClick={() => { void closeBuilder(); }}
          aria-label="Builder'ı kapat"
          disabled={transitioning || closing}
        >
          <X />
        </button>
      </header>
      <nav className="builder-progress" aria-label="Karakter oluşturma adımları">
        {STEPS.map((step, index) => (
          <div
            key={step.id}
            className={`${index === stepIndex ? "current" : ""} ${index < stepIndex ? "done" : ""}`}
            aria-current={index === stepIndex ? "step" : undefined}
          >
            <span>{index < stepIndex ? <Check size={14} /> : index + 1}</span>
            <small>{step.label}</small>
          </div>
        ))}
      </nav>
      {error && (
        <div className={`builder-message ${saveState === "conflict" ? "conflict" : ""}`} role="alert">
          <span>{error}</span>
          {(saveState === "conflict" || saveState === "error") && (
            <div className="builder-message-actions">
              <button onClick={() => { void retryPendingChanges(); }}>
                <RefreshCw size={15} /> Yerel değişiklikleri yeniden dene
              </button>
              <button onClick={() => { void useServerDraft(); }}>
                Sunucu sürümünü kullan
              </button>
            </div>
          )}
        </div>
      )}
      <section className="builder-card" aria-live="polite">
        <StepContent
          step={draft.current_step}
          data={draft.data}
          byType={byType}
          change={change}
        />
      </section>
      <footer className="builder-actions">
        <button
          disabled={stepIndex === 0 || transitioning || blockedRef.current}
          onClick={() => { void navigate("previous"); }}
        >
          <ArrowLeft size={17} /> Geri
        </button>
        <span>Adım {stepIndex + 1} / {STEPS.length}</span>
        {draft.current_step === "review" ? (
          <button
            className="primary-button"
            disabled={publishing || transitioning || blockedRef.current}
            onClick={() => { void publish(); }}
          >
            {publishing ? <LoaderCircle className="rolling-icon" size={17} /> : <Sparkles size={17} />}
            Karakteri yayınla
          </button>
        ) : (
          <button
            className="primary-button"
            disabled={transitioning || blockedRef.current}
            onClick={() => { void navigate("next"); }}
          >
            İleri <ArrowRight size={17} />
          </button>
        )}
      </footer>
    </main>
  );
}

function SaveIndicator({ state }: { state: SaveState }) {
  const labels: Record<SaveState, string> = {
    saved: "Tüm değişiklikler kaydedildi",
    pending: "Kaydedilmeyi bekliyor",
    saving: "Kaydediliyor",
    conflict: "Sürüm çakışması",
    error: "Kaydetme hatası",
  };
  return <span className={`save-indicator ${state}`} role="status">
    {state === "saving" ? <LoaderCircle className="rolling-icon" size={14} /> : state === "conflict" || state === "error" ? <CloudAlert size={14} /> : <Cloud size={14} />}
    {labels[state]}
  </span>;
}

function StepContent({
  step,
  data,
  byType,
  change,
}: {
  step: CharacterDraftStep;
  data: CharacterDraftData;
  byType: Map<string, RulesCatalogEntry[]>;
  change: <K extends keyof CharacterDraftData>(field: K, value: CharacterDraftData[K]) => void;
}) {
  if (step === "basics") return <div className="builder-step">
    <StepHeading title="Kahramanını adlandır" text="Bu isim masada ve Game Log'da görünecek." />
    <label>Karakter adı<input autoFocus maxLength={80} value={data.name} onChange={(event) => change("name", event.target.value)} /></label>
  </div>;
  if (step === "abilities") return <div className="builder-step">
    <StepHeading title="Ability score'ları belirle" text="Değerler 1–20 arasında olmalı; modifier ve derived stat'lar backend'de hesaplanır." />
    <div className="ability-grid">{ABILITIES.map((ability) => <label key={ability}><span>{title(ability)}</span><input type="number" min={1} max={20} value={data.ability_scores[ability]} onChange={(event) => change("ability_scores", { ...data.ability_scores, [ability]: Number(event.target.value) })} /></label>)}</div>
  </div>;
  if (step === "class" || step === "species" || step === "background") {
    const field = `${step}_id` as "class_id" | "species_id" | "background_id";
    return <div className="builder-step">
      <StepHeading title={`${title(step)} seç`} text="Seçimin pinned SRD kataloğundan doğrulanır." />
      <div className="choice-grid">{(byType.get(step) ?? []).map((entry) => <button key={entry.id} className={data[field] === entry.id ? "selected" : ""} aria-pressed={data[field] === entry.id} onClick={() => {
        change(field, entry.id);
        if (step === "background") {
          const required = ((entry.data.skill_proficiencies as string[] | undefined) ?? []).map((skill) => skill.toLowerCase().replaceAll(" ", "_")) as CharacterSkill[];
          change("skill_proficiencies", Array.from(new Set([...data.skill_proficiencies, ...required])));
        }
      }}><strong>{entry.name}</strong><small>{entry.provenance.section}</small></button>)}</div>
      {!(byType.get(step) ?? []).length && <p className="muted">Bu ruleset için seçenek bulunamadı.</p>}
    </div>;
  }
  if (step === "proficiencies") return <div className="builder-step">
    <StepHeading title="Skill proficiency seç" text="Background tarafından zorunlu kılınan seçimler korunmalıdır." />
    <div className="check-grid">{SKILLS.map((skill) => <label key={skill}><input type="checkbox" checked={data.skill_proficiencies.includes(skill)} onChange={() => change("skill_proficiencies", toggle(data.skill_proficiencies, skill))} /><span>{title(skill)}</span></label>)}</div>
  </div>;
  if (step === "equipment") return <div className="builder-step">
    <StepHeading title="Başlangıç ekipmanını seç" text="Item'lar identity-based inventory kaydı olarak publish edilir." />
    <div className="choice-grid">{(byType.get("item") ?? []).map((entry) => <button key={entry.id} className={data.equipment_catalog_ids.includes(entry.id) ? "selected" : ""} aria-pressed={data.equipment_catalog_ids.includes(entry.id)} onClick={() => change("equipment_catalog_ids", toggle(data.equipment_catalog_ids, entry.id))}><strong>{entry.name}</strong><small>{String(entry.data.weight_lb ?? "—")} lb</small></button>)}</div>
  </div>;
  if (step === "spells") return <div className="builder-step">
    <StepHeading title="Spell hazırlığını yap" text="Known ve prepared listeleri ile slot maximum'ları birlikte doğrulanır." />
    <label>Spellcasting ability<select value={data.spellcasting.ability ?? ""} onChange={(event) => change("spellcasting", { ...data.spellcasting, ability: (event.target.value || null) as CharacterAbility | null })}><option value="">Spellcasting yok</option>{ABILITIES.map((ability) => <option key={ability} value={ability}>{title(ability)}</option>)}</select></label>
    <div className="choice-grid">{(byType.get("spell") ?? []).map((entry) => {
      const selected = data.spellcasting.prepared_spell_ids.includes(entry.id);
      return <button key={entry.id} className={selected ? "selected" : ""} aria-pressed={selected} onClick={() => {
        const prepared = toggle(data.spellcasting.prepared_spell_ids, entry.id);
        change("spellcasting", {
          ...data.spellcasting,
          ability: prepared.length ? data.spellcasting.ability ?? "wisdom" : data.spellcasting.ability,
          known_spell_ids: prepared,
          prepared_spell_ids: prepared,
          slots: prepared.length ? { ...data.spellcasting.slots, "1": Math.max(1, data.spellcasting.slots["1"] ?? 0) } : data.spellcasting.slots,
        });
      }}><strong>{entry.name}</strong><small>Seviye {String(entry.data.level)}</small></button>;
    })}</div>
    {data.spellcasting.prepared_spell_ids.length > 0 && <label>1. seviye slot<input type="number" min={0} max={99} value={data.spellcasting.slots["1"] ?? 0} onChange={(event) => change("spellcasting", { ...data.spellcasting, slots: { ...data.spellcasting.slots, "1": Number(event.target.value) } })} /></label>}
  </div>;
  return <div className="builder-step">
    <StepHeading title="Karakterini kontrol et" text="Publish tüm adımları yeniden doğrular ve authoritative sheet'i atomik üretir." />
    <dl className="review-grid">
      <div><dt>İsim</dt><dd>{data.name}</dd></div>
      <div><dt>Class</dt><dd>{entryName(byType, "class", data.class_id)}</dd></div>
      <div><dt>Species</dt><dd>{entryName(byType, "species", data.species_id)}</dd></div>
      <div><dt>Background</dt><dd>{entryName(byType, "background", data.background_id)}</dd></div>
      <div><dt>Proficiencies</dt><dd>{data.skill_proficiencies.length}</dd></div>
      <div><dt>Equipment</dt><dd>{data.equipment_catalog_ids.length}</dd></div>
      <div><dt>Prepared spells</dt><dd>{data.spellcasting.prepared_spell_ids.length}</dd></div>
    </dl>
    <div className="ability-summary">{ABILITIES.map((ability) => <span key={ability}><small>{ability.slice(0, 3).toUpperCase()}</small><strong>{data.ability_scores[ability]}</strong></span>)}</div>
  </div>;
}

function StepHeading({ title: heading, text }: { title: string; text: string }) {
  return <header className="step-heading"><h2>{heading}</h2><p>{text}</p></header>;
}
function toggle<T>(values: T[], value: T): T[] {
  return values.includes(value) ? values.filter((item) => item !== value) : [...values, value];
}
function title(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}
function entryName(byType: Map<string, RulesCatalogEntry[]>, type: string, id: string | null): string {
  return byType.get(type)?.find((entry) => entry.id === id)?.name ?? "Seçilmedi";
}
