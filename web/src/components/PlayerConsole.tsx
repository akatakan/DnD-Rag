import {
  type KeyboardEvent as ReactKeyboardEvent,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  Backpack,
  Bot,
  BookOpen,
  Brain,
  CircleUserRound,
  Dices,
  Feather,
  Heart,
  NotebookPen,
  Shield,
  Sparkles,
  Swords,
} from "lucide-react";
import { api } from "../api";
import { openSheetRoll } from "../rollIntent";
import type {
  Character,
  CharacterAbility,
  CharacterSkill,
  CommandResponse,
  PublicCharacter,
  Snapshot,
} from "../types";
import RuleDrawer from "./RuleDrawer";
import InventoryPanel from "./InventoryPanel";
import ActionPanel from "./ActionPanel";
import MapBoard from "./MapBoard";

type SheetTab = "overview" | "actions" | "spells" | "inventory" | "features" | "notes";
const TABS: Array<{ id: SheetTab; label: string; icon: typeof CircleUserRound }> = [
  { id: "overview", label: "Overview", icon: CircleUserRound },
  { id: "actions", label: "Actions", icon: Swords },
  { id: "spells", label: "Spells", icon: BookOpen },
  { id: "inventory", label: "Inventory", icon: Backpack },
  { id: "features", label: "Features", icon: Sparkles },
  { id: "notes", label: "Notes", icon: NotebookPen },
];
const ABILITIES: CharacterAbility[] = [
  "strength", "dexterity", "constitution",
  "intelligence", "wisdom", "charisma",
];

export default function PlayerConsole({
  snapshot,
  token,
  onError,
  onRefresh,
}: {
  snapshot: Snapshot;
  token: string;
  onError: (value: string) => void;
  onRefresh: () => Promise<void>;
}) {
  const character = snapshot.own_character;
  const [tab, setTab] = useState<SheetTab>("overview");
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const commandInFlightRef = useRef(false);
  const revisionRef = useRef(snapshot.revision);
  const commandGenerationRef = useRef(0);
  const handover = snapshot.game.handover;
  const hasVoted = handover.votes?.includes(snapshot.me.member_id);
  const mayVote = handover.status === "vote_ai"
    && handover.eligible_voters?.includes(snapshot.me.member_id);
  useEffect(() => {
    revisionRef.current = Math.max(revisionRef.current, snapshot.revision);
  }, [snapshot.revision]);
  useEffect(() => {
    commandGenerationRef.current += 1;
    revisionRef.current = snapshot.revision;
    commandInFlightRef.current = false;
    return () => {
      commandGenerationRef.current += 1;
    };
  }, [token]);
  if (!character) return <main className="center-state">Karakter hazırlanıyor...</main>;
  const current = snapshot.state.combatants[snapshot.state.turn_index];

  async function run(type: string, payload: Record<string, unknown> = {}) {
    if (commandInFlightRef.current) {
      onError("Önceki işlem tamamlanıyor.");
      return;
    }
    commandInFlightRef.current = true;
    const generation = commandGenerationRef.current;
    try {
      const response = await api.command<CommandResponse>(
        token,
        type,
        payload,
        revisionRef.current,
      );
      if (generation !== commandGenerationRef.current) return;
      revisionRef.current = Math.max(revisionRef.current, response.revision);
      onError("");
      await onRefresh();
    } catch (reason) {
      if (generation === commandGenerationRef.current) {
        onError(reason instanceof Error ? reason.message : "İşlem tamamlanamadı");
        if (type === "move_map_token") {
          await onRefresh().catch(() => undefined);
        }
      }
    } finally {
      if (generation === commandGenerationRef.current) {
        commandInFlightRef.current = false;
      }
    }
  }

  function moveTabFocus(
    event: ReactKeyboardEvent<HTMLButtonElement>,
    index: number,
  ) {
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % TABS.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + TABS.length) % TABS.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = TABS.length - 1;
    else return;
    event.preventDefault();
    setTab(TABS[next].id);
    tabRefs.current[next]?.focus();
  }

  return (
    <main className="sheet-layout" id="main-content">
      {handover.status && (
        <section className="player-handover">
          <Bot size={19} />
          <div>
            <strong>{handover.status === "grace"
              ? "DM bağlantısı bekleniyor"
              : handover.status === "vote_ai"
                ? "AI DM geçiş oylaması"
                : handover.status === "assisted"
                  ? "Assisted mod etkin"
                  : "Co-DM devri bekleniyor"}</strong>
            {handover.status === "vote_ai" && <small>{handover.votes?.length || 0}/{handover.required || 1} onay</small>}
          </div>
          {mayVote && <button disabled={hasVoted} className="primary-button" onClick={() => run("vote_ai_takeover")}>{hasVoted ? "Onaylandı" : "AI DM'yi onayla"}</button>}
        </section>
      )}
      <section className="sheet-hero">
        <div className="sheet-identity">
          <span className="eyebrow">Character Sheet</span>
          <h1>{character.name}</h1>
          <p>{character.class_name} · Seviye {character.level} · {character.ruleset_version}</p>
        </div>
        <div className="sheet-vitals">
          <Vital icon={Shield} label="Armor Class" value={character.ac ?? character.derived.armor_class} />
          <Vital icon={Heart} label="Hit Points" value={`${character.hp}/${character.max_hp}`} tone="hp" />
          <Vital icon={Dices} label="Initiative" value={signed(character.derived.initiative)} />
          <Vital icon={Feather} label="Speed" value={`${character.derived.speed} ft`} />
          <Vital icon={Brain} label="Passive Perception" value={character.derived.passive_perception} />
        </div>
      </section>
      <div className="sheet-context">
        <span><Swords size={16} /> {current ? `Round ${snapshot.state.round} · Sıra: ${current.name}` : "Encounter aktif değil"}</span>
        <span>{snapshot.state.scene.title}</span>
      </div>
      {snapshot.map_scene.published && snapshot.map_scene.asset && (
        <section className="player-map-panel" aria-label="Yayınlanmış kampanya haritası">
          <MapBoard
            scene={snapshot.map_scene}
            token={token}
            compact
            activeCombatantId={current?.id}
            onMoveToken={(mapToken, x, y) => {
              void run("move_map_token", {
                token_id: mapToken.id,
                token_revision: mapToken.revision,
                x,
                y,
              });
            }}
            onMapPing={(x, y) => {
              void run("map_ping", { x, y });
            }}
          />
        </section>
      )}
      <nav className="sheet-tabs" role="tablist" aria-label="Karakter kağıdı bölümleri">
        {TABS.map(({ id, label, icon: Icon }, index) => (
          <button
            key={id}
            id={`sheet-tab-${id}`}
            ref={(node) => { tabRefs.current[index] = node; }}
            role="tab"
            aria-selected={tab === id}
            aria-controls="sheet-panel"
            tabIndex={tab === id ? 0 : -1}
            className={tab === id ? "active" : ""}
            onClick={() => setTab(id)}
            onKeyDown={(event) => moveTabFocus(event, index)}
          >
            <Icon size={17} /> {label}
          </button>
        ))}
      </nav>
      <section
        className="sheet-content"
        id="sheet-panel"
        role="tabpanel"
        aria-labelledby={`sheet-tab-${tab}`}
        tabIndex={0}
      >
        {tab === "overview" && (
          <Overview character={character} token={token} run={run} />
        )}
        {tab === "actions" && (
          <div className="sheet-two-column">
            <ActionPanel
              character={character}
              targets={snapshot.state.characters}
              run={run}
              showSpells={false}
              validAttackTargetIds={
                snapshot.state.encounter_status === "active"
                  ? snapshot.state.combatants
                    .filter((combatant) => !combatant.hidden)
                    .map((combatant) => combatant.id)
                  : [character.id]
              }
            />
            <Conditions character={character} />
          </div>
        )}
        {tab === "spells" && (
          <Spells character={character} targets={snapshot.state.characters} run={run} />
        )}
        {tab === "inventory" && (
          <InventoryPanel character={character} run={run} />
        )}
        {tab === "features" && (
          <Features character={character} run={run} />
        )}
        {tab === "notes" && (
          <Notes
            key={`${snapshot.game.id}:${character.id}`}
            gameId={snapshot.game.id}
            characterId={character.id}
          />
        )}
      </section>
    </main>
  );
}

function Vital({
  icon: Icon,
  label,
  value,
  tone = "",
}: {
  icon: typeof Shield;
  label: string;
  value: string | number;
  tone?: string;
}) {
  return <div className={`sheet-vital ${tone}`}><Icon size={18} /><small>{label}</small><strong>{value}</strong></div>;
}

function Overview({
  character,
  token,
  run,
}: {
  character: Character;
  token: string;
  run: (type: string, payload?: Record<string, unknown>) => Promise<void>;
}) {
  const [amount, setAmount] = useState(1);
  return <div className="overview-grid">
    <div className="overview-main">
      <section className="sheet-panel">
        <h2>Abilities & Saves</h2>
        <div className="ability-sheet-grid">
          {ABILITIES.map((ability) => (
            <div key={ability}>
              <strong>{title(ability)}</strong>
              <button onClick={() => launchCheck("ability", ability, character.derived.ability_modifiers[ability], `${title(ability)} Check`)}>
                <span>Modifier</span><b>{signed(character.derived.ability_modifiers[ability])}</b>
              </button>
              <button onClick={() => launchCheck("save", ability, character.derived.saving_throws[ability], `${title(ability)} Save`)}>
                <span>Save</span><b>{signed(character.derived.saving_throws[ability])}</b>
              </button>
              <small>Score {character.inputs.ability_scores[ability]}</small>
            </div>
          ))}
        </div>
      </section>
      <section className="sheet-panel">
        <h2>Skills</h2>
        <div className="skill-sheet-grid">
          {Object.entries(character.derived.skills).map(([skill, modifier]) => (
            <button
              key={skill}
              onClick={() => launchCheck("skill", skill, modifier, title(skill))}
            >
              <span>{character.inputs.skill_expertise.includes(skill as CharacterSkill) ? "◆" : character.inputs.skill_proficiencies.includes(skill as CharacterSkill) ? "●" : "○"}</span>
              <strong>{title(skill)}</strong>
              <b>{signed(modifier)}</b>
            </button>
          ))}
        </div>
      </section>
    </div>
    <aside className="overview-side">
      <section className="sheet-panel">
        <h2><Heart size={18} /> HP Talebi</h2>
        <input type="number" min={1} value={amount} onChange={(event) => setAmount(Number(event.target.value))} />
        <div className="button-row">
          <button onClick={() => run("request_damage", { amount })}>Hasar</button>
          <button onClick={() => run("request_heal", { amount })}>İyileşme</button>
        </div>
        <small>DM onayından sonra uygulanır.</small>
      </section>
      <RuleDrawer token={token} compact />
    </aside>
  </div>;
}

function Spells({
  character,
  targets,
  run,
}: {
  character: Character;
  targets: Record<string, Character | PublicCharacter>;
  run: (type: string, payload?: Record<string, unknown>) => Promise<void>;
}) {
  const casting = character.action_state?.spellcasting;
  const [targetId, setTargetId] = useState(character.id);
  const targetOptions = Object.values(targets);
  const targetKey = targetOptions.map((target) => target.id).join("\0");
  useEffect(() => {
    if (!targetOptions.some((target) => target.id === targetId)) {
      setTargetId(targetOptions[0]?.id ?? "");
    }
  }, [targetId, targetKey]);
  if (!casting || !casting.known_spell_ids.length) return (
    <div className="sheet-empty"><BookOpen /><h2>Henüz spell yok</h2><p>Builder veya DM yapılandırmasıyla known ve prepared spell eklenebilir.</p></div>
  );
  return <div className="spell-sheet">
    <section className="sheet-panel spell-summary">
      <div><span>Spellcasting</span><strong>{casting.ability ? title(casting.ability) : "—"}</strong></div>
      {Object.entries(casting.slots).map(([level, pool]) => <div key={level}><span>Level {level}</span><strong>{pool.remaining}/{pool.maximum}</strong></div>)}
    </section>
    <label className="spell-target">Spell hedefi<select value={targetId} onChange={(event) => setTargetId(event.target.value)}>{targetOptions.map((target) => <option key={target.id} value={target.id}>{target.name}</option>)}</select></label>
    <section className="spell-list">
      {casting.known_spell_ids.map((spellId) => {
        const prepared = casting.prepared_spell_ids.includes(spellId);
        return <article key={spellId} className="sheet-panel">
          <div><span className="eyebrow">{prepared ? "Prepared" : "Known"}</span><h2>{spellId === "spell:cure-wounds" ? "Cure Wounds" : spellId}</h2></div>
          {prepared && spellId === "spell:cure-wounds" ? <div className="spell-slot-actions">{Object.entries(casting.slots).map(([level, pool]) => <button key={level} disabled={pool.remaining < 1 || !targetId} onClick={() => run("cast_spell", { spell_id: spellId, slot_level: Number(level), target_character_id: targetId })}>Level {level} · {pool.remaining} slot</button>)}</div> : <small>{prepared ? "Typed resolver bekleniyor." : "Cast etmek için prepare edilmeli."}</small>}
        </article>;
      })}
    </section>
  </div>;
}

function Features({
  character,
  run,
}: {
  character: Character;
  run: (type: string, payload?: Record<string, unknown>) => Promise<void>;
}) {
  const resources = character.resource_state?.class_resources ?? {};
  const hitDice = character.resource_state?.hit_dice;
  return <div className="feature-grid">
    <section className="sheet-panel">
      <h2>Rest & Hit Dice</h2>
      <p>{hitDice ? `${hitDice.remaining}/${hitDice.maximum} d${hitDice.die_size}` : "Hit Dice verisi yok"}</p>
      <div className="button-row"><button onClick={() => run("short_rest", { hit_dice: 0 })}>Short Rest</button><button onClick={() => run("long_rest")}>Long Rest</button></div>
    </section>
    {Object.entries(resources).map(([id, resource]) => <section className="sheet-panel" key={id}><h2>{id === "second-wind" ? "Second Wind" : title(id)}</h2><p>{resource.remaining}/{resource.maximum} kullanım</p>{id === "second-wind" && <button className="primary-button" disabled={resource.remaining < 1} onClick={() => run("use_second_wind")}>Bonus Action kullan</button>}</section>)}
    <Conditions character={character} />
  </div>;
}

function Conditions({ character }: { character: Character }) {
  return <section className="sheet-panel">
    <h2>Conditions & Concentration</h2>
    {character.effects?.concentration && <p><strong>Concentration:</strong> {character.effects.concentration.name}</p>}
    {character.conditions.length ? <div className="condition-list">{character.conditions.map((item) => <span key={item}>{item}</span>)}</div> : <p className="muted">Aktif condition yok.</p>}
  </section>;
}

function Notes({ gameId, characterId }: { gameId: string; characterId: string }) {
  const storageKey = `tetsu:notes:${gameId}:${characterId}`;
  const [notes, setNotes] = useState(() => {
    try { return localStorage.getItem(storageKey) ?? ""; } catch { return ""; }
  });
  const [saveState, setSaveState] = useState<"saved" | "saving" | "error">("saved");
  const notesRef = useRef(notes);
  useEffect(() => {
    notesRef.current = notes;
  }, [notes]);
  useEffect(() => {
    setSaveState("saving");
    const timer = window.setTimeout(() => {
      try {
        localStorage.setItem(storageKey, notes);
        setSaveState("saved");
      } catch {
        setSaveState("error");
      }
    }, 400);
    return () => window.clearTimeout(timer);
  }, [notes, storageKey]);
  useEffect(() => {
    const persistLatest = () => {
      try {
        localStorage.setItem(storageKey, notesRef.current);
      } catch {
        // The mounted autosave reports quota/privacy failures in the UI.
      }
    };
    window.addEventListener("pagehide", persistLatest);
    return () => {
      window.removeEventListener("pagehide", persistLatest);
      persistLatest();
    };
  }, [storageKey]);
  return <section className="sheet-panel notes-panel">
    <div>
      <h2>Campaign Notes</h2>
      <span role="status">
        {saveState === "saved"
          ? "Bu cihazda kaydedildi"
          : saveState === "saving"
            ? "Kaydediliyor…"
            : "Kaydedilemedi; tarayıcı depolama alanını kontrol edin"}
      </span>
    </div>
    <textarea value={notes} maxLength={20_000} onChange={(event) => setNotes(event.target.value)} placeholder="NPC isimleri, ipuçları, planlar…" />
    <small>Bu notlar şimdilik yalnız bu tarayıcıda saklanır; diğer oyunculara veya DM'ye gönderilmez.</small>
  </section>;
}

function launchCheck(category: "ability" | "skill" | "save", key: string, modifier: number, label: string) {
  openSheetRoll({
    label,
    modifier,
    command: {
      type: "roll_character_check",
      payload: { category, key },
    },
  });
}
function signed(value: number) { return value >= 0 ? `+${value}` : String(value); }
function title(value: string) { return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }
