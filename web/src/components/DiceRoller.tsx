import {
  Component,
  type ReactNode,
  type KeyboardEvent as ReactKeyboardEvent,
  lazy,
  Suspense,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import { Dices, Minus, Plus, Volume2, VolumeX, X } from "lucide-react";
import { api } from "../api";
import { primeDiceAudio, releaseDiceAudio } from "../diceAudio";
import type {
  CommandResponse,
  DiceRollPayload,
  DiceSides,
  DiceTheme,
  RollMode,
} from "../types";
import {
  isSheetRollIntent,
  SHEET_ROLL_EVENT,
  type SheetRollIntent,
} from "../rollIntent";
const Dice3DTray = lazy(() => import("./Dice3DTray"));

class DiceTrayBoundary extends Component<
  { children: ReactNode; fallback: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

const DICE: DiceSides[] = [4, 6, 8, 10, 12, 20, 100];
const MODES: { value: RollMode; label: string }[] = [
  { value: "normal", label: "Normal" },
  { value: "advantage", label: "Avantaj" },
  { value: "disadvantage", label: "Dezavantaj" },
];

function clampInteger(value: number, minimum: number, maximum: number) {
  if (!Number.isFinite(value)) return minimum;
  return Math.min(maximum, Math.max(minimum, Math.trunc(value)));
}

function expressionFor(
  sides: DiceSides,
  count: number,
  modifier: number,
  mode: RollMode,
) {
  const keep = mode === "advantage" ? "kh1" : mode === "disadvantage" ? "kl1" : "";
  const diceCount = mode === "normal" ? count : 2;
  const suffix = modifier === 0 ? "" : modifier > 0 ? `+${modifier}` : String(modifier);
  return `${diceCount}d${sides}${keep}${suffix}`;
}

function diceResultFrom(response: CommandResponse): DiceRollPayload {
  const payload = response.event?.payload;
  const candidate =
    payload && typeof payload === "object" && "roll" in payload
      ? payload.roll
      : payload;
  const result = candidate as Partial<DiceRollPayload> | null | undefined;
  if (
    !result
    || typeof result !== "object"
    || !Array.isArray(result.rolls)
    || !result.rolls.every(Number.isFinite)
    || !Array.isArray(result.kept)
    || !result.kept.every(Number.isFinite)
    || !Number.isFinite(result.modifier)
    || !Number.isFinite(result.total)
    || typeof result.expression !== "string"
  ) {
    throw new Error("Sunucu geçerli bir zar sonucu döndürmedi.");
  }
  return result as DiceRollPayload;
}

function keptIndexes(result: DiceRollPayload) {
  if (result.kept.length === result.rolls.length) {
    return new Set(result.rolls.map((_, index) => index));
  }
  const indexes = new Set<number>();
  const remaining = [...result.kept];
  result.rolls.forEach((value, index) => {
    const match = remaining.indexOf(value);
    if (match >= 0) {
      indexes.add(index);
      remaining.splice(match, 1);
    }
  });
  return indexes;
}

function StaticDiceTray({
  result,
  tossKey,
  reducedMotion = false,
}: {
  result: DiceRollPayload;
  tossKey: number;
  reducedMotion?: boolean;
}) {
  const kept = keptIndexes(result);
  return (
    <div
      className="dice-3d-tray"
      aria-hidden="true"
      data-reduced-motion={reducedMotion ? "true" : undefined}
      data-renderer-unavailable={reducedMotion ? undefined : "true"}
      data-testid="dice-3d-tray"
    >
      <div className="dice-3d-fallback">
        {result.rolls.slice(0, 12).map((value, index) => (
          <span
            className={kept.has(index) ? "kept" : "discarded"}
            key={`${tossKey}-${index}`}
          >
            {value}
          </span>
        ))}
      </div>
      <span className="dice-3d-overflow">
        {result.rolls.length > 12 ? `+${result.rolls.length - 12}` : ""}
      </span>
    </div>
  );
}

export default function DiceRoller({
  token,
  revision,
  actorCharacterId,
  onError,
  onRefresh,
}: {
  token: string;
  revision: number;
  actorCharacterId?: string;
  onError: (value: string) => void;
  onRefresh: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [sides, setSides] = useState<DiceSides>(20);
  const [count, setCount] = useState(1);
  const [modifier, setModifier] = useState(0);
  const [mode, setMode] = useState<RollMode>("normal");
  const [visibility, setVisibility] = useState<"party" | "private">("party");
  const [reducedMotion, setReducedMotion] = useState(
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  const [theme, setTheme] = useState<DiceTheme>("crimson");
  const [sound, setSound] = useState(true);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<DiceRollPayload | null>(null);
  const [resultSides, setResultSides] = useState<DiceSides>(20);
  const [tossKey, setTossKey] = useState(0);
  const [sheetIntent, setSheetIntent] = useState<SheetRollIntent | null>(null);
  const busyRef = useRef(false);
  const mountedRef = useRef(true);
  const pendingActionRef = useRef<{
    key: string;
    id: ReturnType<typeof crypto.randomUUID>;
  } | null>(null);
  const preferenceQueueRef = useRef<Promise<unknown>>(Promise.resolve());
  const preferenceGenerationRef = useRef(0);
  const preferenceTimerRef = useRef<number | null>(null);
  const desiredPreferencesRef = useRef({ theme, sound });
  const revisionRef = useRef(revision);
  const rawSettingsRef = useRef({ sides, count, modifier, mode });
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const firstControlRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const helpId = useId();
  const expression = expressionFor(sides, count, modifier, mode);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (preferenceTimerRef.current !== null) {
        window.clearTimeout(preferenceTimerRef.current);
        preferenceTimerRef.current = null;
      }
      releaseDiceAudio();
    };
  }, []);

  useEffect(() => {
    let disposed = false;
    const generation = preferenceGenerationRef.current;
    api.dicePreferences(token).then((preferences) => {
      if (
        disposed
        || !mountedRef.current
        || generation !== preferenceGenerationRef.current
      ) return;
      setTheme(preferences.theme);
      setSound(preferences.sound_enabled);
      desiredPreferencesRef.current = {
        theme: preferences.theme,
        sound: preferences.sound_enabled,
      };
    }).catch((reason) => {
      if (!disposed && mountedRef.current) {
        onError(
          reason instanceof Error
            ? reason.message
            : "Zar tercihleri yüklenemedi.",
        );
      }
    });
    return () => { disposed = true; };
  }, [onError, token]);

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReducedMotion(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    revisionRef.current = Math.max(revisionRef.current, revision);
  }, [revision]);

  useEffect(() => {
    if (!sheetIntent) {
      rawSettingsRef.current = { sides, count, modifier, mode };
    }
  }, [count, mode, modifier, sheetIntent, sides]);

  useEffect(() => {
    const receive = (event: Event) => {
      const intent = (event as CustomEvent<unknown>).detail;
      if (!isSheetRollIntent(intent)) {
        onError("Geçersiz karakter zarı isteği reddedildi.");
        return;
      }
      setSheetIntent(intent);
      setSides(20);
      setCount(1);
      setModifier(intent.modifier);
      setMode(intent.mode ?? "normal");
      setResult(null);
      setOpen(true);
    };
    window.addEventListener(SHEET_ROLL_EVENT, receive);
    return () => window.removeEventListener(SHEET_ROLL_EVENT, receive);
  }, [onError]);

  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    firstControlRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  function close() {
    setOpen(false);
    triggerRef.current?.focus();
  }

  function chooseMode(nextMode: RollMode) {
    setMode(nextMode);
    if (nextMode !== "normal") setSides(20);
  }

  function savePreferences(
    patch: Partial<{ theme: DiceTheme; sound: boolean }>,
  ) {
    const next = {
      ...desiredPreferencesRef.current,
      ...patch,
    };
    preferenceGenerationRef.current += 1;
    desiredPreferencesRef.current = next;
    if (preferenceTimerRef.current !== null) {
      window.clearTimeout(preferenceTimerRef.current);
    }
    preferenceTimerRef.current = window.setTimeout(() => {
      preferenceTimerRef.current = null;
      const desired = desiredPreferencesRef.current;
      preferenceQueueRef.current = preferenceQueueRef.current
        .catch(() => undefined)
        .then(() => api.updateDicePreferences(
          token, desired.theme, desired.sound,
        ))
        .catch((reason) => {
          if (mountedRef.current) {
            onError(
              reason instanceof Error
                ? reason.message
                : "Zar tercihleri kaydedilemedi.",
            );
          }
        });
    }, 180);
  }

  function keepFocusInside(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key !== "Tab" || !dialogRef.current) return;
    const controls = Array.from(
      dialogRef.current.querySelectorAll<HTMLElement>(
        "button:not([disabled]), input:not([disabled]), select:not([disabled])",
      ),
    );
    if (!controls.length) return;
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  async function roll() {
    if (busyRef.current) return;
    if (sound) primeDiceAudio();
    busyRef.current = true;
    setBusy(true);
    const commandType = sheetIntent?.command.type ?? "roll_intent";
    const commandPayload = sheetIntent
      ? { ...sheetIntent.command.payload, mode }
      : {
          actor_character_id: actorCharacterId ?? null,
          action: "custom_roll",
          visibility,
          context: "global_fab",
          dice: {
            count: mode === "normal" ? count : 2,
            sides,
            modifier,
            mode,
          },
        };
    const actionKey = JSON.stringify({
      type: commandType,
      payload: commandPayload,
    });
    if (pendingActionRef.current?.key !== actionKey) {
      pendingActionRef.current = {
        key: actionKey,
        id: crypto.randomUUID(),
      };
    }
    try {
      const response = await api.command<CommandResponse>(
        token,
        commandType,
        commandPayload,
        revisionRef.current,
        pendingActionRef.current.id,
      );
      pendingActionRef.current = null;
      revisionRef.current = Math.max(revisionRef.current, response.revision);
      if (!mountedRef.current) return;
      const resolved = diceResultFrom(response);
      setResult(resolved);
      setResultSides(sheetIntent ? 20 : sides);
      setTossKey((value) => value + 1);
      onError("");
      await onRefresh();
    } catch (reason) {
      if (mountedRef.current) {
        onError(reason instanceof Error ? reason.message : "Zar atılamadı.");
      }
    } finally {
      busyRef.current = false;
      if (mountedRef.current) setBusy(false);
    }
  }

  return (
    <>
      {result && (reducedMotion
        ? <StaticDiceTray result={result} tossKey={tossKey} reducedMotion />
        : (
          <DiceTrayBoundary
            key={tossKey}
            fallback={<StaticDiceTray result={result} tossKey={tossKey} />}
          >
            <Suspense
              fallback={
                <div className="dice-3d-loading" aria-hidden="true">
                  Zar tepsisi hazırlanıyor…
                </div>
              }
            >
              <Dice3DTray
                result={result}
                sides={resultSides}
                theme={theme}
                sound={sound}
                tossKey={tossKey}
              />
            </Suspense>
          </DiceTrayBoundary>
        )
      )}

      {open && (
        <div
          className="dice-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) close();
          }}
        >
          <div
            className="dice-panel"
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            aria-describedby={helpId}
            onKeyDown={keepFocusInside}
          >
            <header className="dice-panel-header">
              <div>
                <span className="eyebrow">Masa zarı</span>
                <h2 id={titleId}>{sheetIntent?.label ?? "Zar at"}</h2>
              </div>
              <button ref={firstControlRef} className="icon-button" onClick={close} aria-label="Zar panelini kapat">
                <X size={19} />
              </button>
            </header>

            <fieldset className="dice-picker">
              <legend>Zar türü</legend>
              {DICE.map((die) => (
                <button
                  type="button"
                  key={die}
                  className={sides === die ? "selected" : ""}
                  aria-pressed={sides === die}
                  disabled={sheetIntent ? die !== 20 : mode !== "normal" && die !== 20}
                  onClick={() => setSides(die)}
                >
                  <span className={`die-icon die-d${die}`}>d{die}</span>
                </button>
              ))}
            </fieldset>

            <fieldset className="roll-mode">
              <legend>Atış şekli</legend>
              {MODES.map((option) => (
                <button
                  type="button"
                  key={option.value}
                  className={mode === option.value ? "selected" : ""}
                  aria-pressed={mode === option.value}
                  onClick={() => chooseMode(option.value)}
                >
                  {option.label}
                </button>
              ))}
            </fieldset>

            <div className="dice-settings">
              <label>
                Zar adedi
                <span className="stepper">
                  <button
                    type="button"
                    aria-label="Zar adedini azalt"
                    disabled={Boolean(sheetIntent) || mode !== "normal" || count <= 1}
                    onClick={() => setCount((value) => Math.max(1, value - 1))}
                  >
                    <Minus size={16} />
                  </button>
                  <input
                    type="number"
                    min="1"
                    max="100"
                    inputMode="numeric"
                    value={mode === "normal" ? count : 2}
                    disabled={Boolean(sheetIntent) || mode !== "normal"}
                    onChange={(event) => setCount(clampInteger(event.target.valueAsNumber, 1, 100))}
                    aria-describedby={mode === "normal" ? undefined : helpId}
                  />
                  <button
                    type="button"
                    aria-label="Zar adedini artır"
                    disabled={Boolean(sheetIntent) || mode !== "normal" || count >= 100}
                    onClick={() => setCount((value) => Math.min(100, value + 1))}
                  >
                    <Plus size={16} />
                  </button>
                </span>
              </label>
              <label>
                Değiştirici
                <input
                  type="number"
                  min="-99999"
                  max="99999"
                  value={modifier}
                  readOnly={Boolean(sheetIntent)}
                  onChange={(event) => setModifier(clampInteger(event.target.valueAsNumber, -99999, 99999))}
                />
              </label>
              <label>
                Görünürlük
                <select
                  value={visibility}
                  disabled={Boolean(sheetIntent)}
                  onChange={(event) => setVisibility(
                    event.target.value as "party" | "private"
                  )}
                >
                  <option value="party">Masaya açık</option>
                  <option value="private">Yalnızca ben ve DM</option>
                </select>
              </label>
            </div>

            <div className="dice-presentation-settings">
              <label>
                Zar teması
                <select
                  value={theme}
                  onChange={(event) => {
                    const next = event.target.value as DiceTheme;
                    setTheme(next);
                    savePreferences({ theme: next });
                  }}
                >
                  <option value="crimson">Kızıl masa</option>
                  <option value="arcane">Arcane gece</option>
                  <option value="ivory">Fildişi</option>
                </select>
              </label>
              <button
                type="button"
                className="dice-sound-toggle"
                aria-pressed={sound}
                onClick={() => {
                  const next = !sound;
                  setSound(next);
                  savePreferences({ sound: next });
                }}
              >
                {sound ? <Volume2 size={17} /> : <VolumeX size={17} />}
                {sound ? "Çarpışma sesi açık" : "Çarpışma sesi kapalı"}
              </button>
            </div>

            <p className="dice-help" id={helpId}>
              {sheetIntent
                ? "Bu modifier karakter kağıdının authoritative derived stat değerinden gelir."
                : mode === "normal"
                ? "Adet 1–100 arasındadır. Örneğin adet 2 ve d6 seçimi 2d6 atar."
                : `${mode === "advantage" ? "Avantaj" : "Dezavantaj"} yalnız d20 kullanır: iki d20 atılır ve ${mode === "advantage" ? "yüksek" : "düşük"} olan tutulur. Adet bu sırada 2'ye sabitlenir.`}
            </p>

            <div className="dice-expression" aria-label={`Atış ifadesi ${expression}`}>
              <span>İfade</span>
              <code>{expression}</code>
            </div>

            {result && (
              <section className="dice-result" aria-live="polite" aria-atomic="true">
                <div>
                  <span>Sonuç</span>
                  <strong>{result.total}</strong>
                </div>
                <p>
                  Atışlar: {result.rolls.join(", ")}
                  {result.kept.length !== result.rolls.length && ` · Tutulan: ${result.kept.join(", ")}`}
                  {result.modifier !== 0 && ` · Değiştirici: ${result.modifier > 0 ? "+" : ""}${result.modifier}`}
                </p>
              </section>
            )}

            <button className="primary-button dice-roll-action" onClick={roll} disabled={busy}>
              <Dices size={20} className={busy ? "rolling-icon" : ""} />
              {busy ? "Atılıyor…" : sheetIntent ? `${sheetIntent.label} at` : `${expression} at`}
            </button>
          </div>
        </div>
      )}

      <button
        ref={triggerRef}
        type="button"
        className="dice-fab"
        aria-label="Zar atma panelini aç"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => {
          if (sheetIntent) {
            const raw = rawSettingsRef.current;
            setSides(raw.sides);
            setCount(raw.count);
            setModifier(raw.modifier);
            setMode(raw.mode);
            setResult(null);
          }
          setSheetIntent(null);
          setOpen(true);
        }}
      >
        <Dices size={25} />
        <span>Zar At</span>
      </button>
    </>
  );
}
