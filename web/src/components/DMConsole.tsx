import { FormEvent, useState } from "react";
import { Bot, Check, Copy, EyeOff, Heart, Plus, RotateCcw, ShieldCheck, SkipForward, Swords, UserCog, X } from "lucide-react";
import { api } from "../api";
import type { DMMode, FallbackDMMode, Snapshot } from "../types";
import RuleDrawer from "./RuleDrawer";
import LiveEncounterTools from "./LiveEncounterTools";
import MapWorkspace from "./MapWorkspace";

export default function DMConsole({ snapshot, token, initialInviteCode, onError }: { snapshot: Snapshot; token: string; initialInviteCode?: string; onError: (value: string) => void }) {
  const [name, setName] = useState("Goblin");
  const [initiative, setInitiative] = useState(10);
  const [hp, setHp] = useState(7);
  const [aiObjective, setAiObjective] = useState("Continue the encounter tactically");
  const [aiPlan, setAiPlan] = useState<Record<string, unknown> | null>(null);
  const [sceneTitle, setSceneTitle] = useState(snapshot.state.scene.title);
  const [sceneDescription, setSceneDescription] = useState(snapshot.state.scene.description);
  const [inviteCode, setInviteCode] = useState(initialInviteCode || "");
  const current = snapshot.state.combatants[snapshot.state.turn_index];
  const activeDM = snapshot.members.find((member) => member.id === snapshot.game.active_dm_id);
  const coDM = snapshot.members.find((member) => member.role === "co_dm");
  const candidates = snapshot.members.filter((member) => member.role === "player");
  const canControl = snapshot.me.member_id === snapshot.game.active_dm_id;
  const canAccept = snapshot.game.handover.status === "offered" && snapshot.game.handover.candidate_id === snapshot.me.member_id;

  async function run(type: string, payload: Record<string, unknown> = {}) {
    try { await api.command(token, type, payload, snapshot.revision); onError(""); }
    catch (reason) { onError(reason instanceof Error ? reason.message : "Islem tamamlanamadi"); }
  }
  async function addCombatant(event: FormEvent) {
    event.preventDefault();
    await run("add_combatant", { name, initiative, hp, kind: "monster" });
  }
  async function requestAI(autoApply: boolean) {
    try {
      const result = await api.aiStep(token, aiObjective, autoApply) as { plan: Record<string, unknown> };
      setAiPlan(result.plan);
      onError("");
    } catch (reason) { onError(reason instanceof Error ? reason.message : "AI plani olusturulamadi"); }
  }
  async function rotateInvite() {
    try {
      const result = await api.rotateInvite(token);
      setInviteCode(result.invite_code);
      onError("");
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Davet yenilenemedi");
    }
  }
  async function revokeInvite() {
    try {
      await api.revokeInvites(token);
      setInviteCode("");
      onError("");
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Davet iptal edilemedi");
    }
  }

  return (
    <main className="dm-layout" id="main-content">
      {!canControl && <div className="control-banner"><ShieldCheck size={18} /><span><strong>Izleme modu.</strong> Aktif kontrol {activeDM?.name || "AI DM"} tarafinda.</span>{canAccept && <button className="primary-button" onClick={() => run("accept_dm_handover")}>Kontrolu devral</button>}{snapshot.me.is_owner && <button onClick={() => run("reclaim_dm_control")}><RotateCcw size={16} /> Geri al</button>}</div>}
      <aside className="dm-left">
        <section className="invite-panel"><span className="eyebrow">Davet kodu</span><div><strong>{inviteCode || "Gizli"}</strong>{inviteCode && <button className="icon-button" title="Kodu kopyala" onClick={() => navigator.clipboard.writeText(inviteCode)}><Copy size={17} /></button>}</div><div className="button-row"><button onClick={rotateInvite}>{inviteCode ? "Yenile" : "Yeni kod"}</button>{snapshot.game.invite && <button onClick={revokeInvite}>İptal et</button>}</div></section>
        <section className="dm-nav"><h2>Oyuncular</h2>{Object.values(snapshot.state.characters).map((character) => <div className="player-row" key={character.id}><div><strong>{character.name}</strong><small>{character.class_name} · L{character.level}</small></div><span>{character.hp}/{character.max_hp} HP</span><button disabled={!canControl} className="icon-button add-player" title="Initiative listesine ekle" onClick={() => run("add_combatant", { id: character.id, name: character.name, initiative: 10, hp: character.hp, kind: "player" })}><Plus size={15} /></button></div>)}</section>
        <RuleDrawer token={token} compact />
      </aside>
      <section className="dm-center">
        <div className="encounter-header"><div><span className="eyebrow">Encounter Control</span><h1>{snapshot.state.scene.title}</h1></div><div className="round-display"><span>Round</span><strong>{snapshot.state.round || "-"}</strong></div></div>
        <form className="scene-editor" onSubmit={(event) => { event.preventDefault(); run("update_scene", { title: sceneTitle, description: sceneDescription }); }}><input disabled={!canControl} value={sceneTitle} onChange={(event) => setSceneTitle(event.target.value)} placeholder="Sahne basligi" /><input disabled={!canControl} value={sceneDescription} onChange={(event) => setSceneDescription(event.target.value)} placeholder="Oyunculara acik sahne aciklamasi" /><button disabled={!canControl}>Yayinla</button></form>
        <MapWorkspace initialScene={snapshot.map_scene} gameRevision={snapshot.revision} token={token} canControl={canControl} activeCombatantId={current?.id} onError={onError} />
        <div className="turn-control"><div><span className="eyebrow">Aktif sira</span><strong>{current?.name || "Encounter baslamadi"}</strong></div><div className="button-row"><button className="primary-button" disabled={!canControl || !snapshot.state.combatants.length || snapshot.state.encounter_status === "active"} onClick={() => run("start_encounter")}><Swords size={17} /> Baslat</button><button disabled={!canControl || snapshot.state.encounter_status !== "active"} onClick={() => run("next_turn")}><SkipForward size={17} /> Sonraki</button></div></div>
        <div className="initiative-board">{snapshot.state.combatants.map((item, index) => <div className={`initiative-row ${index === snapshot.state.turn_index && snapshot.state.encounter_status === "active" ? "current" : ""}`} key={item.id}><span className="initiative">{item.initiative}</span><div><strong>{item.name}</strong><small>{item.kind}{item.hidden ? " · hidden" : ""}</small></div><span className="hp-value">{item.hp ?? "?"} HP</span>{item.hidden && <EyeOff size={16} />}</div>)}</div>
        <form className="add-combatant" onSubmit={addCombatant}><input disabled={!canControl} value={name} onChange={(event) => setName(event.target.value)} placeholder="Combatant" /><input disabled={!canControl} type="number" value={initiative} onChange={(event) => setInitiative(Number(event.target.value))} aria-label="Initiative" /><input disabled={!canControl} type="number" value={hp} onChange={(event) => setHp(Number(event.target.value))} aria-label="HP" /><button disabled={!canControl} className="icon-button" title="Katilimci ekle"><Plus size={19} /></button></form>
        {canControl && <LiveEncounterTools snapshot={snapshot} token={token} onError={onError} />}
        <section className="event-log"><h2>Oyun Akisi</h2>{snapshot.events.slice(-12).reverse().map((event) => <div key={event.id}><span>{new Date(event.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span><strong>{event.type.replaceAll("_", " ")}</strong><small>{event.visibility}</small></div>)}</section>
      </section>
      <aside className="dm-right">
        <section className="handover-panel"><h2><UserCog size={19} /> DM Kontrolu</h2><div className="dm-status"><span className={activeDM?.online ? "presence online" : "presence"} /><div><strong>{activeDM?.name || "AI DM"}</strong><small>{activeDM?.online ? "Cevrimici" : "Cevrimdisi"} · aktif DM</small></div></div>{snapshot.me.is_owner && <><label>Co-DM<select value={coDM?.id || ""} onChange={(event) => event.target.value ? run("assign_co_dm", { member_id: event.target.value }) : run("remove_co_dm")}><option value="">Atanmadi</option>{coDM && <option value={coDM.id}>{coDM.name}</option>}{candidates.map((member) => <option key={member.id} value={member.id}>{member.name}</option>)}</select></label><label>Baglanti kesilirse<select value={snapshot.game.fallback_dm_mode} onChange={(event) => run("set_fallback_mode", { mode: event.target.value as FallbackDMMode })}><option value="assisted">Assisted modda bekle</option><option value="vote_ai">AI DM icin oylama</option></select></label></>}{snapshot.game.handover.status === "grace" && <p className="handover-note">DM yeniden baglanmasi bekleniyor.</p>}{canAccept && <button className="primary-button full-button" onClick={() => run("accept_dm_handover")}>DM kontrolunu devral</button>}</section>
        <section className="mode-panel"><h2>DM Modu</h2><div className="mode-switch">{(["human", "assisted", "ai"] as DMMode[]).map((mode) => <button disabled={!canControl} key={mode} className={snapshot.game.dm_mode === mode ? "active" : ""} onClick={() => run("set_dm_mode", { mode })}>{mode}</button>)}</div></section>
        {snapshot.pending_requests.length > 0 && <section className="request-panel"><h2>Onay Bekleyenler</h2>{snapshot.pending_requests.map((request) => <div className="request-row" key={request.id}><div><strong>{request.type}</strong><small>{String(request.payload.amount)} HP</small></div><button disabled={!canControl} className="icon-button approve" title="Onayla" onClick={() => run("approve_request", { request_id: request.id })}><Check size={17} /></button><button disabled={!canControl} className="icon-button reject" title="Reddet" onClick={() => run("reject_request", { request_id: request.id })}><X size={17} /></button></div>)}</section>}
        <section className="ai-panel"><h2><Bot size={19} /> AI DM</h2><textarea value={aiObjective} onChange={(event) => setAiObjective(event.target.value)} /><div className="button-row"><button disabled={snapshot.game.dm_mode === "human" || (!canControl && snapshot.game.dm_mode !== "ai")} onClick={() => requestAI(false)}>Plan uret</button><button className="primary-button" disabled={snapshot.game.dm_mode === "human" || (!canControl && snapshot.game.dm_mode !== "ai")} onClick={() => requestAI(true)}>Uygula</button></div>{snapshot.game.dm_mode === "human" && <small>Human modunda AI kapalidir.</small>}{aiPlan && <pre>{JSON.stringify(aiPlan, null, 2)}</pre>}</section>
        <section className="quick-state"><h2>Hizli HP</h2>{Object.values(snapshot.state.characters).map((character) => <div key={character.id}><span>{character.name}</span><button disabled={!canControl} onClick={() => run("apply_damage", { character_id: character.id, amount: 1 })}>-1</button><Heart size={15} /><button disabled={!canControl} onClick={() => run("apply_heal", { character_id: character.id, amount: 1 })}>+1</button></div>)}</section>
      </aside>
    </main>
  );
}
