import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { CornerUpLeft, HeartPulse, Plus, Sparkles } from "lucide-react";
import { api } from "../api";
import type { CommandResponse, Snapshot } from "../types";

export default function LiveEncounterTools({
  snapshot,
  token,
  onError,
}: {
  snapshot: Snapshot;
  token: string;
  onError: (value: string) => void;
}) {
  const [busy, setBusy] = useState("");
  const busyRef = useRef(false);
  const mountedRef = useRef(true);
  const revisionRef = useRef(snapshot.revision);
  const [entryName, setEntryName] = useState("Lair Action");
  const [entryKind, setEntryKind] = useState<"lair" | "environment">("lair");
  const [entryInitiative, setEntryInitiative] = useState(20);
  const [entryTie, setEntryTie] = useState(0);
  const characters = useMemo(
    () => Object.values(snapshot.state.characters).filter(
      (character) => "effects" in character,
    ),
    [snapshot.state.characters],
  );
  const [characterId, setCharacterId] = useState(characters[0]?.id || "");
  const [conditionId, setConditionId] = useState("condition:blinded");
  const [durationKind, setDurationKind] = useState<
    "permanent" | "rounds" | "short_rest" | "long_rest"
  >("rounds");
  const [rounds, setRounds] = useState(1);
  const [concentrationName, setConcentrationName] = useState("Ongoing Spell");

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  useEffect(() => {
    revisionRef.current = Math.max(revisionRef.current, snapshot.revision);
  }, [snapshot.revision]);
  useEffect(() => {
    if (!characters.some((character) => character.id === characterId)) {
      setCharacterId(characters[0]?.id || "");
    }
  }, [characterId, characters]);

  const run = async (
    key: string,
    type: string,
    payload: Record<string, unknown> = {},
  ) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(key);
    try {
      const result = await api.command<CommandResponse>(
        token, type, payload, revisionRef.current,
      );
      revisionRef.current = result.revision;
      if (mountedRef.current) onError("");
    } catch (error) {
      if (mountedRef.current) {
        onError(error instanceof Error ? error.message : "Encounter işlemi tamamlanamadı");
      }
    } finally {
      busyRef.current = false;
      if (mountedRef.current) setBusy("");
    }
  };
  const active = snapshot.state.encounter_status === "active";
  const live = active || snapshot.state.encounter_status === "paused";
  const character = snapshot.state.characters[characterId];
  const fullCharacter = character && "effects" in character ? character : null;
  const entryValid = (
    entryName.trim().length > 0
    && Number.isInteger(entryInitiative)
    && entryInitiative >= -100
    && entryInitiative <= 100
    && Number.isInteger(entryTie)
    && entryTie >= -100
    && entryTie <= 100
  );
  const roundsValid = (
    Number.isInteger(rounds) && rounds >= 1 && rounds <= 1000
  );

  return (
    <section className="live-encounter-tools">
      <div className="advanced-encounter-heading">
        <div><span className="eyebrow">Advanced encounter</span><h2>Turn & effects</h2></div>
        <button
          disabled={!snapshot.state.encounter_undo_available || Boolean(busy)}
          onClick={() => void run("undo", "undo_encounter")}
        ><CornerUpLeft /> Son işlemi geri al</button>
      </div>

      <form className="environment-entry-form" onSubmit={(event: FormEvent) => {
        event.preventDefault();
        if (!entryValid) return;
        void run("entry", "add_environment_entry", {
          name: entryName, kind: entryKind,
          initiative: entryInitiative, tie_breaker: entryTie,
        });
      }}>
        <label>Turn entry<input maxLength={80} required disabled={!live || Boolean(busy)} value={entryName} onChange={(event) => setEntryName(event.target.value)} /></label>
        <label>Tür<select disabled={!live || Boolean(busy)} value={entryKind} onChange={(event) => setEntryKind(event.target.value as typeof entryKind)}><option value="lair">Lair</option><option value="environment">Environment</option></select></label>
        <label>Init<input aria-label="Environment initiative" type="number" min={-100} max={100} disabled={!live || Boolean(busy)} value={entryInitiative} onChange={(event) => setEntryInitiative(Number(event.target.value))} /></label>
        <label>Tie<input aria-label="Environment tie breaker" type="number" min={-100} max={100} disabled={!live || Boolean(busy)} value={entryTie} onChange={(event) => setEntryTie(Number(event.target.value))} /></label>
        <button disabled={!live || Boolean(busy) || !entryValid}><Plus /> Ekle</button>
      </form>

      <div className="live-roster-tools">
        {snapshot.state.combatants.map((item) => <div key={item.id}>
          <span className="initiative">{item.initiative}</span>
          <div><strong>{item.name}</strong><small>{item.kind} · tie {item.tie_breaker ?? 0}</small></div>
          <input
            key={`${item.id}:${item.tie_breaker ?? 0}`}
            aria-label={`${item.name} tie breaker`}
            type="number" min={-100} max={100}
            disabled={!live || Boolean(busy)}
            defaultValue={item.tie_breaker ?? 0}
            onBlur={(event) => {
              const tieBreaker = event.currentTarget.valueAsNumber;
              if (
                Number.isInteger(tieBreaker)
                && tieBreaker >= -100
                && tieBreaker <= 100
                && tieBreaker !== (item.tie_breaker ?? 0)
              ) {
                void run(`tie-${item.id}`, "set_initiative_tiebreaker", {
                  combatant_id: item.id, tie_breaker: tieBreaker,
                });
              }
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") event.currentTarget.blur();
            }}
          />
          {typeof item.hp === "number" ? <div className="hp-stepper">
            <button aria-label={`${item.name} 1 hasar`} disabled={!active || Boolean(busy)} onClick={() => void run(`hp-${item.id}`, "adjust_combatant_hp", { combatant_id: item.id, delta: -1 })}>−</button>
            <span>{item.hp}/{item.max_hp ?? "?"}</span>
            <button aria-label={`${item.name} 1 iyileştir`} disabled={!active || Boolean(busy)} onClick={() => void run(`hp-${item.id}`, "adjust_combatant_hp", { combatant_id: item.id, delta: 1 })}>+</button>
          </div> : <span>—</span>}
        </div>)}
      </div>

      <div className="effect-manager">
        <label>Character<select disabled={!active || Boolean(busy)} value={characterId} onChange={(event) => setCharacterId(event.target.value)}>
          {characters.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select></label>
        <form onSubmit={(event) => {
          event.preventDefault();
          if (durationKind === "rounds" && !roundsValid) return;
          const duration = durationKind === "rounds"
            ? { kind: "rounds", remaining: rounds, tick: "end_turn" }
            : { kind: durationKind };
          void run("condition", "add_condition", {
            character_id: characterId, condition_id: conditionId, duration,
          });
        }}>
          <label>Condition ID<input maxLength={120} disabled={!active || Boolean(busy)} value={conditionId} onChange={(event) => setConditionId(event.target.value)} /></label>
          <label>Süre<select disabled={!active || Boolean(busy)} value={durationKind} onChange={(event) => setDurationKind(event.target.value as typeof durationKind)}><option value="rounds">Rounds</option><option value="permanent">Permanent</option><option value="short_rest">Short Rest</option><option value="long_rest">Long Rest</option></select></label>
          {durationKind === "rounds" && <label>Round<input type="number" min={1} max={1000} disabled={!active || Boolean(busy)} value={rounds} onChange={(event) => setRounds(Number(event.target.value))} /></label>}
          <button disabled={!active || Boolean(busy) || !characterId || !conditionId.trim() || (durationKind === "rounds" && !roundsValid)}><HeartPulse /> Condition ekle</button>
        </form>
        <form onSubmit={(event) => {
          event.preventDefault();
          void run("concentration", "start_concentration", {
            character_id: characterId,
            effect_id: `manual:${crypto.randomUUID()}`,
            name: concentrationName,
          });
        }}>
          <label>Concentration<input maxLength={120} disabled={!active || Boolean(busy)} value={concentrationName} onChange={(event) => setConcentrationName(event.target.value)} /></label>
          {fullCharacter?.effects.concentration
            ? <button type="button" disabled={!active || Boolean(busy)} onClick={() => void run("concentration", "end_concentration", { character_id: characterId })}>Bitir: {fullCharacter.effects.concentration.name}</button>
            : <button disabled={!active || Boolean(busy) || !characterId || !concentrationName.trim()}><Sparkles /> Başlat</button>}
        </form>
      </div>
    </section>
  );
}
