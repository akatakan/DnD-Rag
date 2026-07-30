import type { RollMode } from "./types";

type CharacterCheckCommand = {
  type: "roll_character_check";
  payload: {
    category: "ability" | "skill" | "save";
    key: string;
  };
};

type AttackCommand = {
  type: "use_attack";
  payload: {
    attack_id: string;
    target_character_id: string;
  };
};

export interface SheetRollIntent {
  label: string;
  modifier: number;
  mode?: RollMode;
  command: CharacterCheckCommand | AttackCommand;
}

export const SHEET_ROLL_EVENT = "tetsu:sheet-roll";

export function openSheetRoll(intent: SheetRollIntent) {
  window.dispatchEvent(
    new CustomEvent<SheetRollIntent>(SHEET_ROLL_EVENT, { detail: intent }),
  );
}

export function isSheetRollIntent(value: unknown): value is SheetRollIntent {
  if (!value || typeof value !== "object") return false;
  const intent = value as Partial<SheetRollIntent>;
  if (
    typeof intent.label !== "string"
    || !intent.label.trim()
    || intent.label.length > 100
    || typeof intent.modifier !== "number"
    || !Number.isFinite(intent.modifier)
    || (intent.mode !== undefined
      && !["normal", "advantage", "disadvantage"].includes(intent.mode))
    || !intent.command
    || typeof intent.command !== "object"
  ) {
    return false;
  }
  const command = intent.command;
  if (command.type === "roll_character_check") {
    return (
      ["ability", "skill", "save"].includes(command.payload?.category)
      && typeof command.payload?.key === "string"
      && command.payload.key.length > 0
      && command.payload.key.length <= 40
    );
  }
  return (
    command.type === "use_attack"
    && typeof command.payload?.attack_id === "string"
    && command.payload.attack_id.length > 0
    && command.payload.attack_id.length <= 80
    && typeof command.payload?.target_character_id === "string"
    && command.payload.target_character_id.length > 0
    && command.payload.target_character_id.length <= 64
  );
}
