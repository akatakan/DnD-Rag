import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  BookOpenText, Check, ChevronDown, CirclePause, CirclePlay, Flag,
  PackageOpen, ScrollText, Square, StickyNote, X,
} from "lucide-react";
import { api, ApiError } from "../api";
import type {
  CommandResponse,
  GameEvent,
  SessionWorkspace as Workspace,
  Snapshot,
} from "../types";

interface Props {
  snapshot: Snapshot;
  token: string;
  onClose: () => void;
  onRefresh: () => Promise<void>;
}

const lines = (value: string, max: number) =>
  value.split("\n").map((item) => item.trim()).filter(Boolean).slice(0, max);

const eventLabel = (event: GameEvent) => {
  if (event.type === "typed_roll_resolved") {
    const roll = event.payload.roll;
    if (
      roll
      && typeof roll === "object"
      && "total" in roll
      && typeof roll.total === "number"
    ) {
      return `Zar sonucu: ${roll.total}`;
    }
  }
  return event.type
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
};

export default function SessionWorkspace({ snapshot, token, onClose, onRefresh }: Props) {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [events, setEvents] = useState<GameEvent[]>([]);
  const [, setCursor] = useState(0);
  const [hasMore, setHasMore] = useState(true);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [summaryConflict, setSummaryConflict] = useState(false);
  const [note, setNote] = useState("");
  const [noteVisibility, setNoteVisibility] = useState<"party" | "dm_only" | "private">("party");
  const [lootName, setLootName] = useState("");
  const [lootQuantity, setLootQuantity] = useState(1);
  const [questTitle, setQuestTitle] = useState("");
  const [questDescription, setQuestDescription] = useState("");
  const [summaryTitle, setSummaryTitle] = useState("");
  const [highlights, setHighlights] = useState("");
  const [nextSteps, setNextSteps] = useState("");
  const summaryDirty = useRef(false);
  const summaryBaseRevision = useRef(snapshot.revision);
  const revisionRef = useRef(snapshot.revision);
  const busyRef = useRef(false);
  const mountedRef = useRef(true);
  const workspaceGeneration = useRef(0);
  const eventsGeneration = useRef(0);
  const eventCursorRef = useRef(0);
  const activeDm = snapshot.game.active_dm_id === snapshot.me.member_id;
  const mutable = snapshot.session.status !== "completed";
  const hasUnsavedChanges = Boolean(
    note.trim() || lootName.trim() || questTitle.trim()
    || questDescription.trim() || summaryDirty.current
  );

  useEffect(() => {
    revisionRef.current = Math.max(revisionRef.current, snapshot.revision);
  }, [snapshot.revision]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      workspaceGeneration.current += 1;
      eventsGeneration.current += 1;
    };
  }, []);

  const loadWorkspace = useCallback(async () => {
    const generation = ++workspaceGeneration.current;
    const next = await api.sessionWorkspace(token);
    if (!mountedRef.current || generation !== workspaceGeneration.current) return;
    setWorkspace(next);
    if (!summaryDirty.current) {
      setSummaryTitle(next.summary?.title ?? "");
      setHighlights(next.summary?.highlights.join("\n") ?? "");
      setNextSteps(next.summary?.next_steps.join("\n") ?? "");
      summaryBaseRevision.current = revisionRef.current;
    }
  }, [token]);

  const loadEvents = useCallback(async (reset = false) => {
    const generation = ++eventsGeneration.current;
    const after = reset ? 0 : eventCursorRef.current;
    const page = await api.events(token, after, 50);
    if (!mountedRef.current || generation !== eventsGeneration.current) return;
    setEvents((current) => reset ? page.events : [
      ...current,
      ...page.events.filter((event) => !current.some((item) => item.id === event.id)),
    ]);
    eventCursorRef.current = page.next_cursor;
    setCursor(page.next_cursor);
    setHasMore(page.has_more);
  }, [token]);

  useEffect(() => {
    Promise.all([loadWorkspace(), loadEvents(true)]).catch((error) => {
      if (mountedRef.current) {
        setMessage(error instanceof Error ? error.message : "Session yüklenemedi");
      }
    });
  }, [loadEvents, loadWorkspace, snapshot.revision]);

  const perform = async (key: string, action: () => Promise<unknown>) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(key);
    setMessage("");
    try {
      await action();
      await Promise.all([loadWorkspace(), onRefresh(), loadEvents(true)]);
      if (mountedRef.current) {
        setSummaryConflict(false);
        setMessage("Değişiklik kaydedildi.");
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        await Promise.all([loadWorkspace(), onRefresh(), loadEvents(true)]);
        if (key === "summary" && mountedRef.current) {
          setSummaryConflict(true);
        }
      }
      if (mountedRef.current) {
        setMessage(error instanceof Error ? error.message : "İşlem tamamlanamadı");
      }
    } finally {
      busyRef.current = false;
      if (mountedRef.current) setBusy("");
    }
  };

  const runCommand = async (
    type: string,
    payload: Record<string, unknown>,
    expectedRevision = revisionRef.current,
  ) => {
    const response = await api.command<CommandResponse>(
      token, type, payload, expectedRevision,
    );
    revisionRef.current = Math.max(revisionRef.current, response.revision);
    return response;
  };

  const runStatus = async (status: "live" | "paused" | "completed") => {
    const response = await api.updateSessionStatus(
      token, status, revisionRef.current,
    );
    revisionRef.current = Math.max(revisionRef.current, response.revision);
    return response;
  };

  const markSummaryDirty = () => {
    if (!summaryDirty.current) {
      summaryBaseRevision.current = revisionRef.current;
    }
    summaryDirty.current = true;
  };

  useEffect(() => {
    if (!hasUnsavedChanges) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [hasUnsavedChanges]);

  const memberNames = useMemo(
    () => new Map(snapshot.members.map((member) => [member.id, member.name])),
    [snapshot.members],
  );
  const statusAction =
    snapshot.session.status === "preparing"
      ? { status: "live" as const, label: "Başlat", icon: <CirclePlay /> }
      : snapshot.session.status === "live"
        ? { status: "paused" as const, label: "Duraklat", icon: <CirclePause /> }
        : snapshot.session.status === "paused"
          ? { status: "live" as const, label: "Devam et", icon: <CirclePlay /> }
          : null;

  return (
    <main id="main-content" className="session-workspace">
      <header className="workspace-header">
        <div>
          <span className="eyebrow">Session #{snapshot.session.number}</span>
          <h1><BookOpenText /> {snapshot.session.title}</h1>
          <span className={`session-status ${snapshot.session.status}`}>{snapshot.session.status}</span>
        </div>
        <div className="button-row">
          {activeDm && statusAction && (
            <button disabled={Boolean(busy)} onClick={() => perform("status", () => runStatus(statusAction.status))}>
              {statusAction.icon}{statusAction.label}
            </button>
          )}
          {activeDm && ["live", "paused"].includes(snapshot.session.status) && (
            <button className="danger-button" disabled={Boolean(busy)} onClick={() => {
              if (window.confirm("Session sonlandırılsın mı? Yeni not, loot ve quest eklenemez.")) {
                void perform("status", () => runStatus("completed"));
              }
            }}><Square /> Bitir</button>
          )}
          <button className="icon-button" disabled={Boolean(busy)} aria-label="Session ekranını kapat" onClick={() => {
            if (
              hasUnsavedChanges
              && !window.confirm("Kaydedilmemiş session alanları bırakılsın mı?")
            ) return;
            onClose();
          }}><X /></button>
        </div>
      </header>
      {message && <div className="campaign-message" role={summaryConflict ? "alert" : "status"}>
        <span>{message}</span>
        {summaryConflict && <button onClick={async () => {
          try {
            const fresh = await api.snapshot(token);
            revisionRef.current = fresh.revision;
            summaryDirty.current = false;
            summaryBaseRevision.current = fresh.revision;
            await loadWorkspace();
            if (!mountedRef.current) return;
            setSummaryConflict(false);
            setMessage("Sunucudaki özet taslağı yüklendi.");
          } catch (error) {
            if (mountedRef.current) {
              setMessage(error instanceof Error ? error.message : "Özet yüklenemedi");
            }
          }
        }}>Sunucu özetini yükle</button>}
      </div>}

      <div className="workspace-grid">
        <section className="workspace-card session-notes">
          <h2><StickyNote /> Notlar</h2>
          <form onSubmit={(event) => {
            event.preventDefault();
            void perform("note", async () => {
              await runCommand("add_session_note", { content: note, visibility: noteVisibility });
              setNote("");
            });
          }}>
            <textarea aria-label="Session notu" maxLength={4000} required disabled={!mutable || Boolean(busy)} value={note} onChange={(event) => setNote(event.target.value)} placeholder="Bu oturumda ne oldu?" />
            <div className="inline-form">
              <label>Görünürlük
                <select disabled={!mutable || Boolean(busy)} value={noteVisibility} onChange={(event) => setNoteVisibility(event.target.value as typeof noteVisibility)}>
                  <option value="party">Parti</option>
                  <option value="private">Sadece ben ve DM</option>
                  {snapshot.me.role !== "player" && <option value="dm_only">DM ekibi</option>}
                </select>
              </label>
              <button className="primary-button" disabled={!mutable || Boolean(busy) || !note.trim()}>Not ekle</button>
            </div>
          </form>
          <div className="workspace-list">
            {workspace?.notes.map((item) => (
              <article key={item.id}>
                <div><strong>{item.author_name}</strong><small>{item.visibility.startsWith("player:") ? "private" : item.visibility}</small></div>
                <p>{item.content}</p>
                <time>{new Date(item.created_at).toLocaleString()}</time>
              </article>
            ))}
            {!workspace?.notes.length && <p className="empty-state">Henüz görünür not yok.</p>}
          </div>
        </section>

        <section className="workspace-card">
          <h2><PackageOpen /> Loot</h2>
          {activeDm && <form className="inline-form" onSubmit={(event) => {
            event.preventDefault();
            void perform("loot", async () => {
              await runCommand("add_session_loot", { name: lootName, quantity: lootQuantity });
              setLootName(""); setLootQuantity(1);
            });
          }}>
            <input aria-label="Loot adı" maxLength={120} required disabled={!mutable || Boolean(busy)} value={lootName} onChange={(event) => setLootName(event.target.value)} placeholder="Eşya adı" />
            <input aria-label="Loot adedi" type="number" min={1} max={1000000} disabled={!mutable || Boolean(busy)} value={lootQuantity} onChange={(event) => setLootQuantity(Number(event.target.value))} />
            <button disabled={!mutable || Boolean(busy) || !lootName.trim()}>Ekle</button>
          </form>}
          <div className="compact-list">
            {workspace?.loot.map((item) => <div key={item.id}>
              <span><strong>{item.name}</strong><small> × {item.quantity}</small></span>
              {item.status === "available"
                ? <button disabled={!mutable || Boolean(busy)} onClick={() => perform(`claim-${item.id}`, () => runCommand("claim_session_loot", { loot_id: item.id }))}>Talep et</button>
                : <span className="claimed"><Check /> {item.claimant_name || "Alındı"}</span>}
            </div>)}
            {!workspace?.loot.length && <p className="empty-state">Loot havuzu boş.</p>}
          </div>
        </section>

        <section className="workspace-card">
          <h2><Flag /> Questler</h2>
          {activeDm && <form onSubmit={(event) => {
            event.preventDefault();
            void perform("quest", async () => {
              await runCommand("add_session_quest", { title: questTitle, description: questDescription });
              setQuestTitle(""); setQuestDescription("");
            });
          }}>
            <input aria-label="Quest başlığı" maxLength={160} required disabled={!mutable || Boolean(busy)} value={questTitle} onChange={(event) => setQuestTitle(event.target.value)} placeholder="Quest başlığı" />
            <textarea aria-label="Quest açıklaması" maxLength={2000} disabled={!mutable || Boolean(busy)} value={questDescription} onChange={(event) => setQuestDescription(event.target.value)} placeholder="Amaç ve ipuçları" />
            <button disabled={!mutable || Boolean(busy) || !questTitle.trim()}>Quest ekle</button>
          </form>}
          <div className="workspace-list">
            {workspace?.quests.map((item) => <article key={item.id}>
              <div><strong>{item.title}</strong><span className={`quest-status ${item.status}`}>{item.status}</span></div>
              {item.description && <p>{item.description}</p>}
              {activeDm && mutable && <select aria-label={`${item.title} durumu`} disabled={Boolean(busy)} value={item.status} onChange={(event) => perform(`quest-${item.id}`, () => runCommand("set_session_quest_status", { quest_id: item.id, status: event.target.value }))}>
                <option value="active">Aktif</option><option value="completed">Tamamlandı</option><option value="failed">Başarısız</option>
              </select>}
            </article>)}
            {!workspace?.quests.length && <p className="empty-state">Quest eklenmedi.</p>}
          </div>
        </section>

        <section className="workspace-card session-summary">
          <h2><ScrollText /> Oturum özeti</h2>
          {activeDm ? <form onSubmit={(event) => {
            event.preventDefault();
            const submitter = (event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null;
            const published = submitter?.value === "publish";
            void perform("summary", async () => {
              await runCommand("update_session_summary", {
                title: summaryTitle, highlights: lines(highlights, 50),
                next_steps: lines(nextSteps, 50), published,
              }, summaryBaseRevision.current);
              summaryDirty.current = false;
            });
          }}>
            <input aria-label="Özet başlığı" disabled={Boolean(busy)} maxLength={160} value={summaryTitle} onChange={(event) => { markSummaryDirty(); setSummaryTitle(event.target.value); }} placeholder="Özet başlığı" />
            <label>Öne çıkanlar<textarea disabled={Boolean(busy)} maxLength={5000} value={highlights} onChange={(event) => { markSummaryDirty(); setHighlights(event.target.value); }} placeholder="Her satıra bir madde" /></label>
            <label>Sonraki adımlar<textarea disabled={Boolean(busy)} maxLength={5000} value={nextSteps} onChange={(event) => { markSummaryDirty(); setNextSteps(event.target.value); }} placeholder="Her satıra bir madde" /></label>
            <div className="button-row">
              <button value="draft" disabled={Boolean(busy)}>Taslak kaydet</button>
              <button value="publish" className="primary-button" disabled={Boolean(busy)}>Yayınla</button>
            </div>
          </form> : workspace?.summary ? <article>
            <h3>{workspace.summary.title || snapshot.session.title}</h3>
            <strong>Öne çıkanlar</strong>
            <ul>{workspace.summary.highlights.map((item) => <li key={item}>{item}</li>)}</ul>
            <strong>Sonraki adımlar</strong>
            <ul>{workspace.summary.next_steps.map((item) => <li key={item}>{item}</li>)}</ul>
          </article> : <p className="empty-state">DM henüz bir özet yayınlamadı.</p>}
        </section>

        <section className="workspace-card game-log">
          <h2><ScrollText /> Game Log</h2>
          <div className="event-list" aria-live="polite">
            {events.map((item) => <article key={item.id}>
              <span className="event-marker" />
              <div>
                <strong>{eventLabel(item)}</strong>
                <small>{memberNames.get(item.actor_id) || "Sistem"} · {new Date(item.created_at).toLocaleString()}</small>
              </div>
              <code>#{item.id}</code>
            </article>)}
          </div>
          {hasMore && <button className="load-more" disabled={Boolean(busy)} onClick={() => loadEvents().catch((error) => setMessage(error instanceof Error ? error.message : "Log yüklenemedi"))}><ChevronDown /> Daha fazla yükle</button>}
          {!events.length && <p className="empty-state">Henüz görünür olay yok.</p>}
        </section>
      </div>
    </main>
  );
}
