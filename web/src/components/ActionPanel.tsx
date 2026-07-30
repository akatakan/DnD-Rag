import { useEffect, useMemo, useState } from "react";
import { Sparkles, Swords } from "lucide-react";
import type { Character, PublicCharacter, RollMode } from "../types";
import { openSheetRoll } from "../rollIntent";

export default function ActionPanel({
  character,
  targets,
  run,
  showSpells = true,
  validAttackTargetIds,
}: {
  character: Character;
  targets: Record<string, Character | PublicCharacter>;
  run: (type: string, payload?: Record<string, unknown>) => Promise<void>;
  showSpells?: boolean;
  validAttackTargetIds?: string[];
}) {
  const [mode, setMode] = useState<RollMode>("normal");
  const [targetId, setTargetId] = useState(character.id);
  const casting = character.action_state?.spellcasting ?? {
    prepared_spell_ids: [],
    slots: {},
  };
  const attacks = Object.values(character.action_state?.attacks ?? {});
  const attackTargets = useMemo(() => {
    const allowed = validAttackTargetIds
      ? new Set(validAttackTargetIds)
      : null;
    return Object.values(targets).filter(
      (target) => !allowed || allowed.has(target.id),
    );
  }, [targets, validAttackTargetIds]);
  const targetKey = attackTargets.map((target) => target.id).join("\0");

  useEffect(() => {
    if (!attackTargets.some((target) => target.id === targetId)) {
      setTargetId(attackTargets[0]?.id ?? "");
    }
  }, [attackTargets, targetId, targetKey]);

  return (
    <section className="tool-panel action-panel">
      <h2><Swords size={19} /> Actions</h2>
      <label>
        Roll modu
        <select value={mode} onChange={(event) => setMode(event.target.value as RollMode)}>
          <option value="normal">Normal</option>
          <option value="advantage">Avantaj</option>
          <option value="disadvantage">Dezavantaj</option>
        </select>
      </label>
      <div className="button-row">
        <button onClick={() => run("roll_character_check", { category: "skill", key: "perception", mode })}>
          Perception
        </button>
        <button onClick={() => run("roll_character_check", { category: "save", key: "dexterity", mode })}>
          Dex Save
        </button>
      </div>
      {((attacks.length > 0 && attackTargets.length > 0)
        || casting.prepared_spell_ids.length > 0) && (
        <label>
          Hedef
          <select value={targetId} onChange={(event) => setTargetId(event.target.value)}>
            {attackTargets.map((target) => (
              <option key={target.id} value={target.id}>{target.name}</option>
            ))}
          </select>
        </label>
      )}
      {attacks.map((attack) => (
        <button
          key={attack.id}
          disabled={!targetId}
          onClick={() => {
            const modifier =
              character.derived.ability_modifiers[attack.ability]
              + (attack.proficient ? character.derived.proficiency_bonus : 0);
            openSheetRoll({
              label: attack.name,
              modifier,
              mode,
              command: {
                type: "use_attack",
                payload: {
                  attack_id: attack.id,
                  target_character_id: targetId,
                },
              },
            });
          }}
        >
          <Swords size={16} /> {attack.name} · {attack.damage_dice}
        </button>
      ))}
      {showSpells && casting.prepared_spell_ids.includes("spell:cure-wounds") && (
        <div className="spell-action">
          <strong><Sparkles size={16} /> Cure Wounds</strong>
          {Object.entries(casting.slots)
            .filter(([, pool]) => pool.remaining > 0)
            .map(([level, pool]) => (
              <button
                key={level}
                onClick={() => run("cast_spell", {
                  spell_id: "spell:cure-wounds",
                  slot_level: Number(level),
                  target_character_id: targetId,
                })}
              >
                Seviye {level} slot · {pool.remaining}/{pool.maximum}
              </button>
            ))}
        </div>
      )}
      {attacks.length > 0 && attackTargets.length === 0 && (
        <p className="muted">Saldırı için görünür ve geçerli bir hedef yok.</p>
      )}
      {!attacks.length && (!showSpells || !casting.prepared_spell_ids.length) && (
        <p className="muted">DM henüz attack veya prepared spell tanımlamadı.</p>
      )}
    </section>
  );
}
