export type Role = "dm" | "co_dm" | "player";
export type DMMode = "human" | "assisted" | "ai";
export type FallbackDMMode = "assisted" | "vote_ai";

export interface Character {
  id: string;
  owner_id?: string;
  name: string;
  class_name?: string;
  level?: number;
  ac?: number;
  max_hp: number;
  hp: number;
  temp_hp?: number;
  conditions: string[];
  inventory?: string[];
}

export interface Combatant {
  id: string;
  name: string;
  initiative: number;
  hp?: number;
  kind: string;
  hidden?: boolean;
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

export interface Snapshot {
  game: {
    id: string;
    name: string;
    invite_code?: string;
    dm_mode: DMMode;
    owner_id: string;
    active_dm_id: string;
    fallback_dm_mode: FallbackDMMode;
    handover: Handover;
  };
  me: { game_id: string; member_id: string; role: Role; character_id?: string; is_owner: boolean };
  members: Member[];
  state: {
    round: number;
    turn_index: number;
    encounter_status: string;
    combatants: Combatant[];
    characters: Record<string, Character>;
    scene: { title: string; description: string; public_notes: string };
  };
  own_character?: Character;
  pending_requests: { id: string; actor_id: string; type: string; payload: Record<string, unknown> }[];
  events: GameEvent[];
}

export interface Credentials {
  game_id: string;
  token: string;
  role: Role;
}
