import { FormEvent, useState } from "react";
import { Backpack, Bot, Dices, Heart, Shield, Swords } from "lucide-react";
import { api } from "../api";
import type { Snapshot } from "../types";
import RuleDrawer from "./RuleDrawer";

export default function PlayerConsole({ snapshot, token, onError }: { snapshot: Snapshot; token: string; onError: (value: string) => void }) {
  const character = snapshot.own_character;
  const [rollExpression, setRollExpression] = useState("1d20+5");
  const [rollResult, setRollResult] = useState("");
  const [amount, setAmount] = useState(1);
  const handover = snapshot.game.handover;
  const hasVoted = handover.votes?.includes(snapshot.me.member_id);
  const mayVote = handover.status === "vote_ai" && handover.eligible_voters?.includes(snapshot.me.member_id);
  if (!character) return <main className="center-state">Karakter hazirlaniyor...</main>;
  const current = snapshot.state.combatants[snapshot.state.turn_index];

  async function run(type: string, payload: Record<string, unknown> = {}) {
    try { await api.command(token, type, payload); onError(""); }
    catch (reason) { onError(reason instanceof Error ? reason.message : "Islem tamamlanamadi"); }
  }
  async function rollDice(event: FormEvent) {
    event.preventDefault();
    try {
      const result = await api.command(token, "roll", { expression: rollExpression, visibility: "public" }) as { event: { payload: { total: number; rolls: number[] } } };
      setRollResult(`${result.event.payload.rolls.join(", ")} = ${result.event.payload.total}`);
    } catch (reason) { onError(reason instanceof Error ? reason.message : "Zar atilamadi"); }
  }

  return (
    <main className="player-layout">
      {handover.status && <section className="player-handover"><Bot size={19} /><div><strong>{handover.status === "grace" ? "DM baglantisi bekleniyor" : handover.status === "vote_ai" ? "AI DM gecis oylamasi" : handover.status === "assisted" ? "Assisted mod etkin" : "Co-DM devri bekleniyor"}</strong>{handover.status === "vote_ai" && <small>{handover.votes?.length || 0}/{handover.required || 1} onay</small>}</div>{mayVote && <button disabled={hasVoted} className="primary-button" onClick={() => run("vote_ai_takeover")}>{hasVoted ? "Onaylandi" : "AI DM'yi onayla"}</button>}</section>}
      <section className="character-band">
        <div><span className="eyebrow">Karakterim</span><h1>{character.name}</h1><p>{character.class_name} · Seviye {character.level}</p></div>
        <div className="stat-row"><div className="stat"><Shield size={19} /><span>AC</span><strong>{character.ac}</strong></div><div className="stat hp"><Heart size={19} /><span>HP</span><strong>{character.hp}/{character.max_hp}</strong></div><div className="stat"><span>Temp</span><strong>{character.temp_hp || 0}</strong></div></div>
      </section>
      <div className="player-grid">
        <div className="primary-column">
          <section className="scene-panel"><span className="eyebrow">Aktif Sahne</span><h2>{snapshot.state.scene.title}</h2><p>{snapshot.state.scene.description || "DM sahneyi hazirliyor."}</p><div className="turn-strip"><Swords size={18} /><span>Round {snapshot.state.round || "-"}</span><strong>{current ? `Sira: ${current.name}` : "Encounter aktif degil"}</strong></div></section>
          <section className="party-panel"><h2>Party ve Encounter</h2><div className="combatant-list">{snapshot.state.combatants.map((item, index) => <div className={`combatant ${index === snapshot.state.turn_index ? "current" : ""}`} key={item.id}><span className="initiative">{item.initiative}</span><strong>{item.name}</strong><small>{item.kind}</small></div>)}</div></section>
          <RuleDrawer token={token} compact />
        </div>
        <aside className="action-rail">
          <section className="tool-panel"><h2><Dices size={19} /> Zar At</h2><form onSubmit={rollDice}><input value={rollExpression} onChange={(event) => setRollExpression(event.target.value)} /><button className="primary-button">At</button></form>{rollResult && <div className="roll-result">{rollResult}</div>}<div className="quick-rolls">{["1d20", "2d20kh1", "2d20kl1", "1d6", "1d8"].map((value) => <button key={value} onClick={() => setRollExpression(value)}>{value}</button>)}</div></section>
          <section className="tool-panel"><h2><Heart size={19} /> HP Talebi</h2><input type="number" min="1" value={amount} onChange={(event) => setAmount(Number(event.target.value))} /><div className="button-row"><button onClick={() => run("request_damage", { amount })}>Hasar</button><button onClick={() => run("request_heal", { amount })}>Iyilesme</button></div><small>DM onayindan sonra uygulanir.</small></section>
          <section className="tool-panel"><h2><Backpack size={19} /> Envanter</h2>{character.inventory?.length ? <ul>{character.inventory.map((item) => <li key={item}>{item}</li>)}</ul> : <p className="muted">Envanter bos.</p>}</section>
          <section className="tool-panel"><h2>Conditions</h2>{character.conditions.length ? <div className="condition-list">{character.conditions.map((item) => <span key={item}>{item}</span>)}</div> : <p className="muted">Aktif condition yok.</p>}</section>
        </aside>
      </div>
    </main>
  );
}
