export type Role = "dm" | "co_dm" | "player";
export type DMMode = "human" | "assisted" | "ai";
export type FallbackDMMode = "assisted" | "vote_ai";

export interface PublicCharacter {
  id: string;
  name: string;
  class_name?: string;
  level?: number;
  max_hp: number;
  hp: number;
  conditions: string[];
}

export interface Character extends PublicCharacter {
  schema_version: number;
  owner_id?: string;
  ruleset_version: string;
  class_id?: string | null;
  legacy_class_name?: string | null;
  species_id?: string | null;
  background_id?: string | null;
  ac?: number;
  temp_hp?: number;
  inventory?: string[];
  inventory_state: {
    schema_version: number;
    entries: Record<
      string,
      {
        id: string;
        catalog_id: string | null;
        name: string;
        quantity: number;
        unit_weight_lb: number;
        unit_cost_gp: number;
        equipment_slot: string | null;
        armor_training: string | null;
        armor_class_bonus: number;
        container_capacity_lb: number | null;
        container_id: string | null;
        requires_attunement: boolean;
        equipped: boolean;
        attuned: boolean;
      }
    >;
    currency: Record<"cp" | "sp" | "ep" | "gp" | "pp", number>;
    encumbrance_policy: "standard" | "ignore";
    derived: {
      item_weight_lb: number;
      coin_weight_lb: number;
      total_weight_lb: number;
      carrying_capacity_lb: number;
      over_capacity: boolean;
      attuned_count: number;
      armor_class_bonus: number;
      untrained_equipment: string[];
    };
  };
  action_state: {
    schema_version: number;
    spellcasting: {
      ability: CharacterAbility | null;
      known_spell_ids: string[];
      prepared_spell_ids: string[];
      slots: Record<string, { maximum: number; remaining: number }>;
    };
    attacks: Record<
      string,
      {
        id: string;
        name: string;
        ability: CharacterAbility;
        proficient: boolean;
        damage_dice: string;
        damage_type: string;
      }
    >;
  };
  inputs: {
    ability_scores: Record<CharacterAbility, number>;
    skill_proficiencies: CharacterSkill[];
    skill_expertise: CharacterSkill[];
    armor_class: {
      base: number;
      add_dexterity: boolean;
      dexterity_cap: number | null;
      bonus: number;
    };
    hit_points: {
      level_one_base: number | null;
      per_level_base: number;
      constitution_per_level: boolean;
      bonus: number;
    };
    speed: { base: number | null; bonus: number };
  };
  derived: {
    calculation_version: number;
    ability_modifiers: Record<CharacterAbility, number>;
    proficiency_bonus: number;
    saving_throws: Record<CharacterAbility, number>;
    skills: Record<CharacterSkill, number>;
    armor_class: number;
    initiative: number;
    max_hp: number;
    speed: number;
    passive_perception: number;
  };
  resource_state: {
    schema_version: number;
    hit_dice: { die_size: number; maximum: number; remaining: number };
    class_resources: Record<
      string,
      {
        source_id: string;
        maximum: number;
        remaining: number;
        short_rest_recovery: number;
        long_rest_recovery: "all";
      }
    >;
    death_saves: {
      successes: number;
      failures: number;
      status: "none" | "active" | "stable" | "dead";
      last_rolled_turn: number | null;
    };
  };
  effects: {
    concentration: { effect_id: string; name: string } | null;
    conditions: Array<{
      id: string;
      name: string;
      duration:
        | { kind: "permanent" | "short_rest" | "long_rest" }
        | { kind: "rounds"; remaining: number; tick: "end_turn" };
    }>;
  };
}

export type CharacterAbility =
  | "strength"
  | "dexterity"
  | "constitution"
  | "intelligence"
  | "wisdom"
  | "charisma";

export type CharacterSkill =
  | "acrobatics"
  | "animal_handling"
  | "arcana"
  | "athletics"
  | "deception"
  | "history"
  | "insight"
  | "intimidation"
  | "investigation"
  | "medicine"
  | "nature"
  | "perception"
  | "performance"
  | "persuasion"
  | "religion"
  | "sleight_of_hand"
  | "stealth"
  | "survival";

export interface Combatant {
  id: string;
  name: string;
  initiative: number;
  tie_breaker?: number;
  hp?: number;
  max_hp?: number;
  armor_class?: number;
  kind: string;
  hidden?: boolean;
  source?: { type: "manual" | "character"; id: string | null };
}

export interface GameEvent {
  id: number;
  type: string;
  actor_id: string;
  visibility: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface Member {
  id: string;
  name: string;
  role: Role;
  character_id?: string;
  online: boolean;
  is_owner: boolean;
  is_active_dm: boolean;
}

export interface Handover {
  status?: "grace" | "offered" | "assisted" | "vote_ai";
  offline_dm_id?: string;
  candidate_id?: string;
  deadline?: string;
  eligible_voters?: string[];
  votes?: string[];
  required?: number;
}

export interface CampaignSummary {
  id: string;
  name: string;
  status: "draft" | "active" | "archived";
  ruleset_version: string;
  language: string;
  play_style: string;
  public_notes: string;
  settings_version: number;
}

export interface SessionSummary {
  id: string;
  campaign_id: string;
  number: number;
  title: string;
  status: "preparing" | "live" | "paused" | "completed";
  scheduled_at?: string | null;
  started_at?: string;
  ended_at?: string;
}

export interface Snapshot {
  revision: number;
  event_cursor: number;
  game: {
    id: string;
    name: string;
    invite_code?: string;
    invite?: {
      id: string;
      expires_at: string;
      max_uses: number;
      use_count: number;
      created_at: string;
    };
    dm_mode: DMMode;
    owner_id: string;
    active_dm_id: string;
    fallback_dm_mode: FallbackDMMode;
    handover: Handover;
  };
  campaign: CampaignSummary;
  session: SessionSummary;
  me: {
    game_id: string;
    member_id: string;
    role: Role;
    character_id?: string;
    is_owner: boolean;
    character_creation_required: boolean;
  };
  members: Member[];
  state: {
    round: number;
    turn_index: number;
    turn_serial: number;
    turn_actions: Record<string, { action?: string; bonus_action?: string }>;
    encounter_status: string;
    active_encounter_id: string | null;
    active_encounter_revision: number | null;
    encounter_undo_available: boolean;
    combatants: Combatant[];
    characters: Record<string, Character | PublicCharacter>;
    scene: { title: string; description: string; public_notes: string };
  };
  map_scene: MapScene;
  own_character?: Character;
  pending_requests: { id: string; actor_id: string; type: string; payload: Record<string, unknown> }[];
  events: GameEvent[];
  lobby: CampaignLobby;
}

export interface CampaignLobbyMember {
  member_id: string;
  name: string;
  role: Role;
  readiness_status: "not_ready" | "ready";
  readiness_version: number;
  consent_status: "pending" | "accepted" | "declined";
  updated_at: string;
  safety_preferences?: {
    lines?: string[];
    veils?: string[];
    notes?: string;
  };
}

export interface CampaignLobby {
  campaign_id: string;
  settings: {
    schema_version: 1;
    house_rules: Array<{
      id: string;
      title: string;
      description: string;
      enabled: boolean;
    }>;
    safety_tools: Array<
      "x_card" | "lines_veils" | "open_door" | "stars_wishes"
    >;
    session_zero_agenda: string[];
  };
  settings_version: number;
  scheduled_at: string | null;
  members: CampaignLobbyMember[];
}

export interface Credentials {
  game_id: string;
  campaign_id?: string;
  session_id?: string;
  token: string;
  token_expires_at?: string;
  invite_code?: string;
  invite_expires_at?: string;
  role: Role;
}

export interface SavedCampaign extends Credentials {
  name: string;
  is_owner: boolean;
  last_opened_at: string;
}

export interface ServerCampaign {
  game_id: string;
  campaign_id: string;
  session_id: string;
  name: string;
  status: "draft" | "active" | "archived";
  role: "dm" | "co_dm";
  is_owner: boolean;
  updated_at: string;
}

export type DiceSides = 4 | 6 | 8 | 10 | 12 | 20 | 100;
export type RollMode = "normal" | "advantage" | "disadvantage";
export type DiceTheme = "crimson" | "arcane" | "ivory";

export interface DicePreferences {
  theme: DiceTheme;
  sound_enabled: boolean;
  updated_at: string;
}

export interface MapAsset {
  id: string;
  original_name: string;
  content_type?: "image/png" | "image/jpeg";
  byte_size?: number;
  width: number;
  height: number;
  created_at?: string;
  url: string;
}

export interface MapToken {
  id: string;
  combatant_id: string;
  owner_member_id: string | null;
  name: string;
  kind: string;
  initiative: number;
  hp?: number;
  max_hp?: number;
  x: number;
  y: number;
  size_px: number;
  revision: number;
  can_move: boolean;
}

export interface MapScene {
  name: string;
  asset_id: string | null;
  asset: MapAsset | null;
  grid_type: "none" | "square" | "hex";
  grid_size_px: number;
  distance_per_cell: number;
  distance_unit: "ft" | "m";
  viewport: { x: number; y: number; zoom: number };
  published: boolean;
  tokens: MapToken[];
  fog: {
    enabled: boolean;
    revision: number;
    mask_url: string | null;
    revealed_cells: [number, number][] | null;
    updated_at: string;
  };
  signals: Array<{
    id: string;
    kind: "ping" | "draw";
    actor_id: string;
    actor_name: string;
    payload: {
      x?: number;
      y?: number;
      points?: [number, number][];
    };
    expires_at: string;
    created_at: string;
  }>;
  revision: number;
  updated_at: string;
}

export interface DiceRollPayload {
  expression: string;
  rolls: number[];
  kept: number[];
  modifier: number;
  total: number;
}

export interface CommandResponse {
  event: GameEvent & {
    payload: DiceRollPayload | (Record<string, unknown> & { roll?: DiceRollPayload });
  };
  state: Snapshot["state"];
  revision: number;
  client_action_id?: string;
  replayed: boolean;
}

export interface EventPage {
  events: GameEvent[];
  next_cursor: number;
  has_more: boolean;
}

export interface SessionWorkspace {
  session: SessionSummary;
  notes: Array<{
    id: string;
    session_id: string;
    author_id: string;
    author_name: string;
    visibility: "party" | "dm_only" | `player:${string}`;
    content: string;
    created_at: string;
  }>;
  loot: Array<{
    id: string;
    session_id: string;
    name: string;
    quantity: number;
    status: "available" | "claimed";
    claimant_id: string | null;
    claimant_name: string | null;
    created_by: string;
    created_at: string;
    updated_at: string;
  }>;
  quests: Array<{
    id: string;
    session_id: string;
    title: string;
    description: string;
    status: "active" | "completed" | "failed";
    created_by: string;
    created_at: string;
    updated_at: string;
  }>;
  summary: {
    title: string;
    highlights: string[];
    next_steps: string[];
    published: boolean;
  } | null;
}

export interface EncounterCombatantDraft {
  id: string;
  source: { type: "manual" | "character"; id: string | null };
  name: string;
  kind: "monster" | "npc" | "player";
  initiative: number;
  hp: number;
  max_hp: number;
  armor_class: number;
  hidden: boolean;
}

export interface EncounterDraft {
  id: string;
  campaign_id: string;
  created_by: string;
  schema_version: 1;
  data: {
    schema_version: 1;
    name: string;
    description: string;
    combatants: EncounterCombatantDraft[];
  };
  revision: number;
  created_at: string;
  updated_at: string;
}

export type CharacterDraftStep =
  | "basics"
  | "abilities"
  | "class"
  | "species"
  | "background"
  | "proficiencies"
  | "equipment"
  | "spells"
  | "review";

export interface CharacterDraftData {
  schema_version: number;
  name: string;
  ability_scores: Record<CharacterAbility, number>;
  ability_score_method: "standard_array" | "point_cost" | "legacy_manual";
  background_ability_increases: Partial<Record<CharacterAbility, number>>;
  class_id: string | null;
  species_id: string | null;
  background_id: string | null;
  skill_proficiencies: CharacterSkill[];
  skill_expertise: CharacterSkill[];
  equipment_catalog_ids: string[];
  spellcasting: {
    ability: CharacterAbility | null;
    known_spell_ids: string[];
    prepared_spell_ids: string[];
    slots: Record<string, number>;
  };
  attacks: Character["action_state"]["attacks"][string][];
}

export interface CharacterDraft {
  game_id: string;
  character_id: string;
  owner_id: string;
  schema_version: number;
  data: CharacterDraftData;
  current_step: CharacterDraftStep;
  revision: number;
  status: "active" | "published";
  created_at: string;
  updated_at: string;
  published_at: string | null;
}

export type RulesCatalogEntityType =
  | "class"
  | "species"
  | "background"
  | "spell"
  | "feature"
  | "item"
  | "condition";

export interface RulesCatalogSource {
  document_id: string;
  title: string;
  version: string;
  url: string;
  document_url: string;
  published_at: string;
  document_sha256: string;
}

export interface RulesCatalogLicense {
  id: "CC-BY-4.0";
  url: string;
  attribution: string;
}

export interface RulesCatalogProvenance {
  document_id: string;
  document_sha256: string;
  page_labels: string[];
  section: string;
  method: "curated" | "derived";
}

export interface RulesCatalogEntry {
  id: string;
  type: RulesCatalogEntityType;
  slug: string;
  name: string;
  data: Record<string, unknown>;
  source: RulesCatalogSource;
  license: RulesCatalogLicense;
  provenance: RulesCatalogProvenance;
}

export interface DeveloperRulesetSummary {
  id: string;
  name: string;
  schema_version: number;
  status: "foundation" | "complete";
  publication_status: "draft" | "published";
  catalog_sha256: string;
  revision: number;
  is_default: boolean;
  based_on: string | null;
  entry_count: number;
  entity_types: RulesCatalogEntityType[];
  created_at: string;
  updated_at: string;
  published_at: string | null;
}

export interface DeveloperRulesetDetail {
  ruleset: DeveloperRulesetSummary & {
    source_json: string;
    license_json: string;
  };
  entries: RulesCatalogEntry[];
}

export type DeveloperCatalogEntry = Omit<
  RulesCatalogEntry,
  "source" | "license"
>;

export interface RulesetSummary {
  id: string;
  name: string;
  schema_version: number;
  status: "foundation" | "complete";
  entry_count: number;
  entity_types: RulesCatalogEntityType[];
  catalog_sha256: string;
  source: RulesCatalogSource;
  license: RulesCatalogLicense;
}

export interface RulesCatalogPage {
  ruleset: RulesetSummary;
  entries: RulesCatalogEntry[];
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
}
