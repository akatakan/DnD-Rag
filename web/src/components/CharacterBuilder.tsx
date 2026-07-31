import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Check,
  Cloud,
  CloudAlert,
  LoaderCircle,
  RefreshCw,
  Rocket,
  ShieldCheck,
  Sparkles,
  UserRound,
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
  { id: "basics", label: "Home" },
  { id: "class", label: "1 · Class" },
  { id: "background", label: "2 · Background" },
  { id: "species", label: "3 · Species" },
  { id: "abilities", label: "4 · Abilities" },
  { id: "proficiencies", label: "Skills" },
  { id: "equipment", label: "5 · Equipment" },
  { id: "spells", label: "Spells" },
  { id: "review", label: "What's Next" },
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
const POINT_COSTS: Record<number, number> = {
  8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9,
};
const STANDARD_ARRAY = [15, 14, 13, 12, 10, 8];
const FIGHTER_SKILL_OPTIONS = new Set<CharacterSkill>([
  "acrobatics",
  "animal_handling",
  "athletics",
  "history",
  "insight",
  "intimidation",
  "perception",
  "persuasion",
  "survival",
]);
const FIGHTER_STANDARD_ARRAY: Record<CharacterAbility, number> = {
  strength: 15,
  dexterity: 14,
  constitution: 13,
  intelligence: 8,
  wisdom: 10,
  charisma: 12,
};
const FIGHTER_POINT_COST: Record<CharacterAbility, number> = {
  strength: 15,
  dexterity: 14,
  constitution: 13,
  intelligence: 10,
  wisdom: 10,
  charisma: 10,
};
type SaveState = "saved" | "pending" | "saving" | "conflict" | "error";

export default function CharacterBuilder({
  snapshot,
  token,
  required = false,
  onClose,
  onPublished,
}: {
  snapshot: Snapshot;
  token: string;
  required?: boolean;
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
  const [builderMode, setBuilderMode] = useState<"standard" | "quick" | null>(null);
  const [quickName, setQuickName] = useState("");
  const [quickClassId, setQuickClassId] = useState("");
  const [quickSpeciesId, setQuickSpeciesId] = useState("");
  const [quickBuilding, setQuickBuilding] = useState(false);
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
      setQuickName(nextDraft.data.name);
      setQuickClassId(nextDraft.data.class_id ?? "");
      setQuickSpeciesId(nextDraft.data.species_id ?? "");
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
    if (saveState !== "conflict" && saveState !== "error") setError("");
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

  async function quickBuild() {
    if (
      !draft || transitionRef.current || !quickName.trim()
      || !quickClassId || !quickSpeciesId
    ) return;
    transitionRef.current = true;
    setTransitioning(true);
    setQuickBuilding(true);
    setError("");
    try {
      await flush();
      if (blockedRef.current) return;
      const built = await api.quickBuildCharacterDraft(
        token,
        characterId,
        revisionRef.current,
        {
          name: quickName.trim(),
          class_id: quickClassId,
          species_id: quickSpeciesId,
        },
      );
      if (!mountedRef.current) return;
      revisionRef.current = built.revision;
      pendingRef.current = {};
      setDraft(built);
      setBuilderMode("standard");
      setSaveState("saved");
    } catch (reason) {
      if (!mountedRef.current) return;
      if (reason instanceof ApiError && reason.status === 409) {
        blockedRef.current = true;
        setSaveState("conflict");
      }
      setError(reason instanceof Error ? reason.message : "Quick Build tamamlanamadı.");
    } finally {
      transitionRef.current = false;
      if (mountedRef.current) {
        setTransitioning(false);
        setQuickBuilding(false);
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

  if (builderMode === null) return (
    <main className="builder-shell builder-welcome" id="main-content">
      <header className="builder-header">
        <div>
          <span className="eyebrow">5.5e Character Builder</span>
          <h1>Kahramanını nasıl oluşturmak istersin?</h1>
          <p>Her iki yol da kampanyanın sabitlenmiş SRD 5.2.1 kataloğuyla sunucuda doğrulanır.</p>
        </div>
        {!required && (
          <button className="icon-button" onClick={onClose} aria-label="Builder'ı kapat">
            <X />
          </button>
        )}
      </header>
      <section className="builder-mode-grid" aria-label="Karakter oluşturma yöntemi">
        <button className="builder-mode-card" onClick={() => setBuilderMode("standard")}>
          <span className="builder-mode-icon"><BookOpen /></span>
          <span className="eyebrow">Tam kontrol</span>
          <strong>Standart Builder</strong>
          <p>Class, origin, ability, proficiency, ekipman ve detay seçimlerini adım adım yap.</p>
          <span className="builder-mode-action">Adım adım başla <ArrowRight size={18} /></span>
        </button>
        <button className="builder-mode-card featured" onClick={() => setBuilderMode("quick")}>
          <span className="builder-mode-icon"><Rocket /></span>
          <span className="eyebrow">Hızlı başlangıç</span>
          <strong>Quick Build</strong>
        <p>İsim, class ve species seç. Katalogta modellenen seviye 1 önerilerini sunucu hazırlasın; yayınlamadan önce kontrol et.</p>
          <span className="builder-mode-action">Hızlı oluştur <Sparkles size={18} /></span>
        </button>
      </section>
      <aside className="builder-source-note">
        <strong>5.5e akışı</strong>
        <span>Class → Origin → Ability Scores → Details → Review</span>
      </aside>
    </main>
  );

  if (builderMode === "quick") {
    const classes = byType.get("class") ?? [];
    const species = byType.get("species") ?? [];
    const ready = Boolean(quickName.trim() && quickClassId && quickSpeciesId);
    return (
      <main className="builder-shell builder-quick" id="main-content">
        <header className="builder-header">
          <div>
            <button className="builder-back-link" onClick={() => setBuilderMode(null)}>
              <ArrowLeft size={16} /> Yöntem seçimine dön
            </button>
            <span className="eyebrow">Quick Build · Seviye 1</span>
            <h1>Üç seçimle maceraya hazırlan</h1>
            <p>Önerilen Standard Array, background bonusları ve proficiency’ler sunucuda üretilir; katalog dışı seçim icat edilmez.</p>
          </div>
        </header>
        {error && <div className="builder-message" role="alert">{error}</div>}
        <section className="builder-card quick-build-form">
          <label>
            <span>1 · Karakter adı</span>
            <input
              autoFocus
              maxLength={80}
              placeholder="Örn. Riva"
              value={quickName}
              onChange={(event) => setQuickName(event.target.value)}
            />
          </label>
          <fieldset>
            <legend>2 · Class</legend>
            <div className="choice-grid">
              {classes.map((entry) => (
                <button
                  key={entry.id}
                  className={quickClassId === entry.id ? "selected" : ""}
                  aria-pressed={quickClassId === entry.id}
                  onClick={() => setQuickClassId(entry.id)}
                >
                  <strong>{entry.name}</strong>
                  <small>{String((entry.data.primary_abilities as string[] | undefined)?.join(" / ") ?? "SRD 5.2.1")}</small>
                </button>
              ))}
            </div>
          </fieldset>
          <fieldset>
            <legend>3 · Species</legend>
            <div className="choice-grid">
              {species.map((entry) => (
                <button
                  key={entry.id}
                  className={quickSpeciesId === entry.id ? "selected" : ""}
                  aria-pressed={quickSpeciesId === entry.id}
                  onClick={() => setQuickSpeciesId(entry.id)}
                >
                  <strong>{entry.name}</strong>
                  <small>{String(entry.data.speed ?? "—")} ft speed</small>
                </button>
              ))}
            </div>
          </fieldset>
          <div className="quick-build-summary">
            <Sparkles size={20} />
            <div>
              <strong>Son adımda kontrol sende</strong>
              <p>Quick Build doğrudan yayınlamaz. Oluşturulan seçimleri inceleyip değiştirebilirsin.</p>
            </div>
          </div>
        </section>
        <footer className="builder-actions">
          <button onClick={() => setBuilderMode(null)}><ArrowLeft size={17} /> Geri</button>
          <button
            className="primary-button"
            disabled={!ready || quickBuilding}
            onClick={() => { void quickBuild(); }}
          >
            {quickBuilding ? <LoaderCircle className="rolling-icon" size={17} /> : <Sparkles size={17} />}
            Önerilen karakteri oluştur
          </button>
        </footer>
      </main>
    );
  }

  const stepIndex = STEPS.findIndex((step) => step.id === draft.current_step);
  const localStepError = stepValidationMessage(
    draft.current_step, draft.data, byType,
  );
  return (
    <main className="builder-shell" id="main-content">
      <header className="builder-header">
        <div>
          <button className="builder-back-link" onClick={() => setBuilderMode(null)}>
            <ArrowLeft size={16} /> Yöntemi değiştir
          </button>
          <span className="eyebrow">5.5e Standard Builder</span>
          <h1>{draft.data.name.trim() || "Yeni Kahraman"}</h1>
          <SaveIndicator state={saveState} />
        </div>
        {!required && (
          <button
            className="icon-button"
            onClick={() => { void closeBuilder(); }}
            aria-label="Builder'ı kapat"
            disabled={transitioning || closing}
          >
            <X />
          </button>
        )}
      </header>
      {required && (
        <div className="builder-required" role="status">
          <Sparkles size={18} />
          <span>
            Masaya katılmadan önce karakterini tamamla. Seçimlerin
            kampanyanın sabitlenmiş SRD kurallarıyla sunucuda doğrulanır.
          </span>
        </div>
      )}
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
      <div className="builder-phase" aria-live="polite">
        <span>{stepIndex === 0 ? "Home · Character Preferences" : stepIndex === 1 ? "1 · Class" : stepIndex < 4 ? "2–3 · Origin" : stepIndex === 4 ? "4 · Ability Scores" : stepIndex < 8 ? "5 · Details" : "What's Next · Review"}</span>
        <small>SRD 5.2.1 · D&D 5.5e</small>
      </div>
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
          onQuickBuild={() => setBuilderMode("quick")}
        />
        {localStepError && (
          <p
            className="builder-inline-validation"
            id="builder-step-validation"
            role="status"
          >
            {localStepError}
          </p>
        )}
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
            aria-describedby={localStepError ? "builder-step-validation" : undefined}
            disabled={Boolean(localStepError) || transitioning || blockedRef.current}
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
  onQuickBuild,
}: {
  step: CharacterDraftStep;
  data: CharacterDraftData;
  byType: Map<string, RulesCatalogEntry[]>;
  change: <K extends keyof CharacterDraftData>(field: K, value: CharacterDraftData[K]) => void;
  onQuickBuild: () => void;
}) {
  if (step === "basics") return <div className="builder-step">
    <div className="builder-home-identity">
      <div className="builder-portrait" aria-hidden="true"><UserRound /></div>
      <label>
        Karakter adı
        <input
          autoFocus
          maxLength={80}
          value={data.name}
          onChange={(event) => change("name", event.target.value)}
        />
      </label>
      <button className="builder-suggestions" onClick={onQuickBuild}>
        <Sparkles size={16} /> Önerileri göster
      </button>
    </div>
    <div className="builder-preferences-heading">
      <StepHeading
        title="Karakter tercihleri"
        text="Bu kampanyada karakterine uygulanacak kurallar. Otoritatif politikalar oyuncu tarafından değiştirilemez."
      />
      <span><ShieldCheck size={16} /> Sunucu tarafından uygulanır</span>
    </div>
    <div className="builder-preference-list">
      <article>
        <div><strong>Kural kaynağı</strong><p>SRD 5.2.1 · D&D 5.5e</p></div>
        <span className="policy-pill">Kampanyaya sabitli</span>
      </article>
      <article>
        <div><strong>İlerleme türü</strong><p>Seviye değişiklikleri aktif DM tarafından yönetilir.</p></div>
        <span className="policy-pill">DM kontrollü</span>
      </article>
      <article>
        <div><strong>Hit Point yöntemi</strong><p>Class hit die ve sabit ortalama değer sunucuda hesaplanır.</p></div>
        <span className="policy-pill">Fixed</span>
      </article>
      <article>
        <div><strong>Ön koşullar</strong><p>Class, proficiency ve katalog bağıntıları yayınlamada tekrar doğrulanır.</p></div>
        <span className="policy-pill">Zorunlu</span>
      </article>
      <article>
        <div><strong>Encumbrance</strong><p>Taşıma kapasitesi Strength × 15 lb; 50 coin 1 lb kabul edilir.</p></div>
        <span className="policy-pill">Standard</span>
      </article>
      <article>
        <div><strong>Karakter gizliliği</strong><p>Tam sheet yalnız karakter sahibi ve yetkili DM rolleri tarafından görülür.</p></div>
        <span className="policy-pill">Campaign only</span>
      </article>
    </div>
  </div>;
  if (step === "abilities") {
    const spent = ABILITIES.reduce(
      (total, ability) => total + (POINT_COSTS[data.ability_scores[ability]] ?? 99),
      0,
    );
    return <div className="builder-step">
    <StepHeading title="Ability score'ları belirle" text="SRD 5.2.1 Standard Array veya 27 puanlık Point Cost yöntemini seç. Origin adımındaki background artışları ayrıca uygulanır." />
    <fieldset className="ability-method">
      <legend>Ability score yöntemi</legend>
      <button
        className={data.ability_score_method === "standard_array" ? "selected" : ""}
        aria-pressed={data.ability_score_method === "standard_array"}
        onClick={() => {
          change("ability_score_method", "standard_array");
          change("ability_scores", FIGHTER_STANDARD_ARRAY);
        }}
      >
        <strong>Standard Array</strong>
        <small>15, 14, 13, 12, 10, 8</small>
      </button>
      <button
        className={data.ability_score_method === "point_cost" ? "selected" : ""}
        aria-pressed={data.ability_score_method === "point_cost"}
        onClick={() => {
          change("ability_score_method", "point_cost");
          change("ability_scores", FIGHTER_POINT_COST);
        }}
      >
        <strong>Point Cost</strong>
        <small>27 puan, skor başına 8–15</small>
      </button>
    </fieldset>
    {data.ability_score_method === "legacy_manual" && (
      <p className="builder-rule-warning" role="alert">
        Eski manuel skorlar yayınlanamaz. Bir SRD yöntemi seç.
      </p>
    )}
    {data.ability_score_method === "point_cost" && (
      <p className={`point-cost ${spent === 27 ? "valid" : "invalid"}`}>
        Kullanılan puan: <strong>{spent > 99 ? "geçersiz" : `${spent}/27`}</strong>
      </p>
    )}
    <div className="ability-grid">{ABILITIES.map((ability) => <label key={ability}><span>{title(ability)}</span><input type="number" min={data.ability_score_method === "point_cost" ? 8 : 1} max={data.ability_score_method === "point_cost" ? 15 : 20} value={data.ability_scores[ability]} onChange={(event) => change("ability_scores", { ...data.ability_scores, [ability]: Number(event.target.value) })} /></label>)}</div>
  </div>;
  }
  if (step === "class" || step === "species" || step === "background") {
    const field = `${step}_id` as "class_id" | "species_id" | "background_id";
    return <div className="builder-step">
      <StepHeading title={`${title(step)} seç`} text="Seçimin pinned SRD kataloğundan doğrulanır." />
      <div className="choice-grid">{(byType.get(step) ?? []).map((entry) => <button key={entry.id} className={data[field] === entry.id ? "selected" : ""} aria-pressed={data[field] === entry.id} onClick={() => {
        change(field, entry.id);
        if (step === "class") {
          const policy = spellcastingPolicyForEntry(entry);
          change("spellcasting", policy ? {
            ability: policy.ability,
            known_spell_ids: [],
            prepared_spell_ids: [],
            slots: policy.slots,
          } : {
            ability: null,
            known_spell_ids: [],
            prepared_spell_ids: [],
            slots: {},
          });
        }
        if (step === "background") {
          const required = ((entry.data.skill_proficiencies as string[] | undefined) ?? []).map((skill) => skill.toLowerCase().replaceAll(" ", "_")) as CharacterSkill[];
          const backgroundSkills = new Set(
            (byType.get("background") ?? []).flatMap((background) =>
              ((background.data.skill_proficiencies as string[] | undefined) ?? [])
                .map((skill) => skill.toLowerCase().replaceAll(" ", "_")),
            ),
          );
          const retained = data.skill_proficiencies.filter(
            (skill) => !backgroundSkills.has(skill),
          );
          change(
            "skill_proficiencies",
            Array.from(new Set([...retained, ...required])),
          );
          const options = ((entry.data.ability_options as string[] | undefined) ?? [])
            .map((ability) => ability.toLowerCase() as CharacterAbility);
          change(
            "background_ability_increases",
            options.length >= 2 ? { [options[0]]: 2, [options[1]]: 1 } : {},
          );
        }
      }}><strong>{entry.name}</strong><small>{entry.provenance.section}</small></button>)}</div>
      {step === "background" && data.background_id && (
        <BackgroundAbilityIncreases
          data={data}
          entry={(byType.get("background") ?? []).find(
            (item) => item.id === data.background_id,
          )}
          change={change}
        />
      )}
      {!(byType.get(step) ?? []).length && <p className="muted">Bu ruleset için seçenek bulunamadı.</p>}
    </div>;
  }
  if (step === "proficiencies") {
    const policy = proficiencyPolicy(data, byType);
    const selected = new Set(data.skill_proficiencies);
    const extras = data.skill_proficiencies.filter(
      (skill) => !policy.backgroundSkills.has(skill),
    );
    const backgroundSelected = data.skill_proficiencies.filter(
      (skill) => policy.backgroundSkills.has(skill),
    ).length;
    const classEligible = extras.filter(
      (skill) => policy.classOptions.has(skill),
    ).length;
    return <div className="builder-step">
      <StepHeading
        title="Skill proficiency seç"
        text="Background sabit skill'leri, class seçenekleri ve species kaynaklı serbest seçimler birlikte doğrulanır."
      />
      <div className="proficiency-summary" role="status">
        <span>Background <strong>{backgroundSelected}/{policy.backgroundSkills.size} sabit</strong></span>
        <span>Ek seçim <strong>{extras.length}/{policy.extraChoiceCount}</strong></span>
        <span>Class uyumlu <strong>{Math.min(classEligible, policy.classChoiceCount)}/{policy.classChoiceCount}</strong></span>
      </div>
      <div className="check-grid">{SKILLS.map((skill) => {
        const required = policy.backgroundSkills.has(skill);
        const checked = selected.has(skill);
        const atLimit = extras.length >= policy.extraChoiceCount;
        return <label
          key={skill}
          className={`${required ? "required" : ""} ${checked ? "checked" : ""}`}
        >
          <input
            type="checkbox"
            aria-label={title(skill)}
            checked={checked}
            disabled={(required && checked) || (!required && !checked && atLimit)}
            onChange={() => change(
              "skill_proficiencies",
              toggle(data.skill_proficiencies, skill),
            )}
          />
          <span>
            {title(skill)}
            {required && <small>{policy.backgroundName}</small>}
            {!required && policy.classOptions.has(skill) && <small>Class seçeneği</small>}
          </span>
        </label>;
      })}</div>
    </div>;
  }
  if (step === "equipment") return <div className="builder-step">
    <StepHeading title="Başlangıç ekipmanını seç" text="Item'lar identity-based inventory kaydı olarak publish edilir." />
    <div className="choice-grid">{(byType.get("item") ?? []).map((entry) => <button key={entry.id} className={data.equipment_catalog_ids.includes(entry.id) ? "selected" : ""} aria-pressed={data.equipment_catalog_ids.includes(entry.id)} onClick={() => change("equipment_catalog_ids", toggle(data.equipment_catalog_ids, entry.id))}><strong>{entry.name}</strong><small>{String(entry.data.weight_lb ?? "—")} lb</small></button>)}</div>
  </div>;
  if (step === "spells") {
    const policy = spellcastingPolicy(data, byType);
    if (!policy) return <div className="builder-step">
      <StepHeading title="Spellcasting" text="Büyü kullanımı class ve level tarafından belirlenir." />
      <p className="builder-rule-warning" role="status">
        Seçili class 1. seviyede spell slot veya hazırlanabilir spell kazanmaz.
      </p>
    </div>;
    const selectionLimit = Math.min(
      policy.knownCount, policy.preparedCount,
    );
    return <div className="builder-step">
    <StepHeading title="Spell hazırlığını yap" text="Spellcasting ability ve slot maksimumları class tablosundan gelir; oyuncu bunları elle değiştiremez." />
    <dl className="review-grid">
      <div><dt>Spellcasting ability</dt><dd>{title(policy.ability)}</dd></div>
      {Object.entries(policy.slots).map(([level, maximum]) => (
        <div key={level}><dt>{level}. seviye slot</dt><dd>{maximum}</dd></div>
      ))}
      <div><dt>Hazırlanabilir spell</dt><dd>{data.spellcasting.prepared_spell_ids.length}/{selectionLimit}</dd></div>
    </dl>
    <div className="choice-grid">{(byType.get("spell") ?? [])
      .filter((entry) => policy.spellIds.has(entry.id))
      .map((entry) => {
      const selected = data.spellcasting.prepared_spell_ids.includes(entry.id);
      const atLimit = data.spellcasting.prepared_spell_ids.length >= selectionLimit;
      return <button key={entry.id} disabled={!selected && atLimit} className={selected ? "selected" : ""} aria-pressed={selected} onClick={() => {
        const prepared = toggle(data.spellcasting.prepared_spell_ids, entry.id);
        change("spellcasting", {
          ability: policy.ability,
          known_spell_ids: prepared,
          prepared_spell_ids: prepared,
          slots: policy.slots,
        });
      }}><strong>{entry.name}</strong><small>Seviye {String(entry.data.level)}</small></button>;
    })}</div>
  </div>;
  }
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
    <div className="ability-summary">{ABILITIES.map((ability) => {
      const bonus = data.background_ability_increases[ability] ?? 0;
      return <span key={ability}><small>{ability.slice(0, 3).toUpperCase()}</small><strong>{data.ability_scores[ability] + bonus}</strong>{bonus > 0 && <em>{data.ability_scores[ability]} + {bonus}</em>}</span>;
    })}</div>
  </div>;
}

function BackgroundAbilityIncreases({
  data,
  entry,
  change,
}: {
  data: CharacterDraftData;
  entry: RulesCatalogEntry | undefined;
  change: <K extends keyof CharacterDraftData>(
    field: K, value: CharacterDraftData[K]
  ) => void;
}) {
  const options = ((entry?.data.ability_options as string[] | undefined) ?? [])
    .map((ability) => ability.toLowerCase() as CharacterAbility);
  const primary = options.find(
    (ability) => data.background_ability_increases[ability] === 2,
  ) ?? "";
  const secondary = options.find(
    (ability) => data.background_ability_increases[ability] === 1,
  ) ?? "";
  const update = (nextPrimary: string, nextSecondary: string) => {
    const increases: Partial<Record<CharacterAbility, number>> = {};
    if (nextPrimary) increases[nextPrimary as CharacterAbility] = 2;
    if (nextSecondary && nextSecondary !== nextPrimary) {
      increases[nextSecondary as CharacterAbility] = 1;
    }
    change("background_ability_increases", increases);
  };
  return <fieldset className="background-abilities">
    <legend>Background ability artışları</legend>
    <p>SRD: listelenen ability'lerden birine +2, farklı birine +1 ver.</p>
    <label>+2
      <select value={primary} onChange={(event) => update(event.target.value, secondary)}>
        <option value="">Seç</option>
        {options.map((ability) => <option key={ability} value={ability}>{title(ability)}</option>)}
      </select>
    </label>
    <label>+1
      <select value={secondary} onChange={(event) => update(primary, event.target.value)}>
        <option value="">Seç</option>
        {options.filter((ability) => ability !== primary).map((ability) => <option key={ability} value={ability}>{title(ability)}</option>)}
      </select>
    </label>
  </fieldset>;
}

function StepHeading({ title: heading, text }: { title: string; text: string }) {
  return <header className="step-heading"><h2>{heading}</h2><p>{text}</p></header>;
}

interface ProficiencyPolicy {
  supported: boolean;
  backgroundName: string;
  backgroundSkills: Set<CharacterSkill>;
  classOptions: Set<CharacterSkill>;
  classChoiceCount: number;
  extraChoiceCount: number;
}

interface SpellcastingPolicy {
  ability: CharacterAbility;
  spellIds: Set<string>;
  knownCount: number;
  preparedCount: number;
  slots: Record<string, number>;
}

function spellcastingPolicyForEntry(
  entry: RulesCatalogEntry | undefined,
): SpellcastingPolicy | null {
  const raw = entry?.data.spellcasting as {
    ability?: string;
    spell_ids?: unknown;
    known_count_by_level?: Record<string, unknown>;
    prepared_count_by_level?: Record<string, unknown>;
    slots_by_level?: Record<string, unknown>;
  } | undefined;
  if (!raw || typeof raw.ability !== "string") return null;
  const knownCount = Number(raw.known_count_by_level?.["1"] ?? 0);
  const preparedCount = Number(raw.prepared_count_by_level?.["1"] ?? 0);
  const rawSlots = raw.slots_by_level?.["1"];
  const slots = rawSlots && typeof rawSlots === "object"
    ? Object.fromEntries(
      Object.entries(rawSlots).map(([level, maximum]) => [
        level, Number(maximum),
      ]),
    )
    : {};
  if (knownCount === 0 && preparedCount === 0 && !Object.keys(slots).length) {
    return null;
  }
  return {
    ability: raw.ability.toLowerCase() as CharacterAbility,
    spellIds: new Set(
      Array.isArray(raw.spell_ids)
        ? raw.spell_ids.map((spellId) => String(spellId))
        : [],
    ),
    knownCount,
    preparedCount,
    slots,
  };
}

function spellcastingPolicy(
  data: CharacterDraftData,
  byType: Map<string, RulesCatalogEntry[]>,
): SpellcastingPolicy | null {
  return spellcastingPolicyForEntry(
    (byType.get("class") ?? []).find((entry) => entry.id === data.class_id),
  );
}

function proficiencyPolicy(
  data: CharacterDraftData,
  byType: Map<string, RulesCatalogEntry[]>,
): ProficiencyPolicy {
  const background = (byType.get("background") ?? []).find(
    (entry) => entry.id === data.background_id,
  );
  const species = (byType.get("species") ?? []).find(
    (entry) => entry.id === data.species_id,
  );
  const classEntry = (byType.get("class") ?? []).find(
    (entry) => entry.id === data.class_id,
  );
  const backgroundSkills = new Set(
    ((background?.data.skill_proficiencies as string[] | undefined) ?? [])
      .map((skill) => skill.toLowerCase().replaceAll(" ", "_") as CharacterSkill),
  );
  const traits = ((species?.data.traits as string[] | undefined) ?? [])
    .map((trait) => trait.toLowerCase());
  const configuredClassCount = classEntry?.data.skill_proficiency_count;
  const configuredClassOptions = classEntry?.data.skill_proficiency_options;
  const classChoiceCount = typeof configuredClassCount === "number"
    ? configuredClassCount
    : data.class_id === "class:fighter" ? 2 : 0;
  const classOptions = Array.isArray(configuredClassOptions)
    ? new Set(
      configuredClassOptions.map(
        (skill) => String(skill).toLowerCase().replaceAll(" ", "_") as CharacterSkill,
      ),
    )
    : data.class_id === "class:fighter"
      ? FIGHTER_SKILL_OPTIONS
      : new Set<CharacterSkill>();
  const configuredSpeciesCount = species?.data.skill_choice_count;
  const speciesChoiceCount = typeof configuredSpeciesCount === "number"
    ? configuredSpeciesCount
    : traits.includes("skillful") ? 1 : 0;
  return {
    supported: Boolean(classEntry)
      && classChoiceCount >= 0
      && classOptions.size >= classChoiceCount,
    backgroundName: background?.name ?? "Background",
    backgroundSkills,
    classOptions,
    classChoiceCount,
    extraChoiceCount: classChoiceCount + speciesChoiceCount,
  };
}

function stepValidationMessage(
  step: CharacterDraftStep,
  data: CharacterDraftData,
  byType: Map<string, RulesCatalogEntry[]>,
): string | null {
  if (step === "abilities") {
    const scores = ABILITIES.map((ability) => data.ability_scores[ability]);
    if (data.ability_score_method === "legacy_manual") {
      return "Standard Array veya Point Cost yöntemini seç.";
    }
    if (data.ability_score_method === "standard_array") {
      const sorted = [...scores].sort((left, right) => right - left);
      if (sorted.some((score, index) => score !== STANDARD_ARRAY[index])) {
        return "Standard Array değerlerinin her birini tam bir kez kullan: 15, 14, 13, 12, 10, 8.";
      }
      return null;
    }
    if (scores.some((score) => !(score in POINT_COSTS))) {
      return "Point Cost skorları 8 ile 15 arasında olmalı.";
    }
    const spent = scores.reduce((total, score) => total + POINT_COSTS[score], 0);
    return spent === 27
      ? null
      : `Point Cost için tam 27 puan kullan; şu anda ${spent} puan kullanıldı.`;
  }
  if (step === "spells") {
    const policy = spellcastingPolicy(data, byType);
    const emptyCasting = data.spellcasting.ability === null
      && data.spellcasting.known_spell_ids.length === 0
      && data.spellcasting.prepared_spell_ids.length === 0
      && Object.keys(data.spellcasting.slots).length === 0;
    if (!policy) {
      return emptyCasting
        ? null
        : "Bu class 1. seviyede spellcasting kullanamaz.";
    }
    if (data.spellcasting.ability !== policy.ability) {
      return "Spellcasting ability class tarafından belirlenir.";
    }
    const selected = data.spellcasting.prepared_spell_ids;
    const limit = Math.min(policy.knownCount, policy.preparedCount);
    if (selected.length > limit) {
      return `En fazla ${limit} spell hazırlayabilirsin.`;
    }
    if (selected.some((spellId) => !policy.spellIds.has(spellId))) {
      return "Class spell listesinde olmayan bir spell seçildi.";
    }
    if (JSON.stringify(data.spellcasting.slots)
      !== JSON.stringify(policy.slots)) {
      return "Spell slot maksimumları class tablosuyla uyuşmuyor.";
    }
    return null;
  }
  if (step !== "proficiencies") return null;

  const policy = proficiencyPolicy(data, byType);
  if (!policy.supported) {
    return "Bu class için skill proficiency kuralı henüz desteklenmiyor.";
  }
  const selected = new Set(data.skill_proficiencies);
  if ([...policy.backgroundSkills].some((skill) => !selected.has(skill))) {
    return "Background tarafından verilen sabit skill proficiency seçimlerini tamamla.";
  }
  const extras = [...selected].filter(
    (skill) => !policy.backgroundSkills.has(skill),
  );
  if (extras.length !== policy.extraChoiceCount) {
    return `Background dışında tam ${policy.extraChoiceCount} skill seçmelisin: ${policy.classChoiceCount} class ve ${policy.extraChoiceCount - policy.classChoiceCount} species seçimi.`;
  }
  const classEligible = extras.filter(
    (skill) => policy.classOptions.has(skill),
  );
  if (classEligible.length < policy.classChoiceCount) {
    return `Ek seçimlerin en az ${policy.classChoiceCount} tanesi class skill listesinden olmalı.`;
  }
  return null;
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
