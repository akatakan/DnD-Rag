import { useEffect, useMemo, useRef, useState } from "react";
import {
  CalendarClock,
  Check,
  ClipboardList,
  Copy,
  Plus,
  ShieldCheck,
  Trash2,
  Users,
  X,
} from "lucide-react";
import { api } from "../api";
import type { CampaignLobby, Snapshot } from "../types";

const SAFETY_TOOLS: Array<{
  id: CampaignLobby["settings"]["safety_tools"][number];
  label: string;
}> = [
  { id: "x_card", label: "X-Card" },
  { id: "lines_veils", label: "Lines & Veils" },
  { id: "open_door", label: "Open Door" },
  { id: "stars_wishes", label: "Stars & Wishes" },
];

export default function CampaignDashboard({
  snapshot,
  token,
  onClose,
  onRefresh,
}: {
  snapshot: Snapshot;
  token: string;
  onClose: () => void;
  onRefresh: () => Promise<void>;
}) {
  const [settings, setSettings] = useState(snapshot.lobby.settings);
  const [settingsVersion, setSettingsVersion] = useState(
    snapshot.lobby.settings_version,
  );
  const [settingsDirty, setSettingsDirty] = useState(false);
  const ownMember = snapshot.lobby.members.find(
    (member) => member.member_id === snapshot.me.member_id,
  );
  const own = ownMember ?? {
    member_id: "",
    name: "",
    role: snapshot.me.role,
    readiness_status: "not_ready" as const,
    readiness_version: 1,
    consent_status: "pending" as const,
    updated_at: "",
  };
  const [readinessVersion, setReadinessVersion] = useState(
    own.readiness_version,
  );
  const [personalDirty, setPersonalDirty] = useState(false);
  const [consent, setConsent] = useState(own.consent_status);
  const [readiness, setReadiness] = useState(own.readiness_status);
  const [lines, setLines] = useState((own.safety_preferences?.lines ?? []).join("\n"));
  const [veils, setVeils] = useState((own.safety_preferences?.veils ?? []).join("\n"));
  const [notes, setNotes] = useState(own.safety_preferences?.notes ?? "");
  const [scheduledAt, setScheduledAt] = useState(
    toLocalDateTimeInput(snapshot.lobby.scheduled_at),
  );
  const [scheduleRevision, setScheduleRevision] = useState(snapshot.revision);
  const [scheduleDirty, setScheduleDirty] = useState(false);
  const [busy, setBusy] = useState("");
  const busyRef = useRef(false);
  const [message, setMessage] = useState("");
  const [hasConflict, setHasConflict] = useState(false);
  const [inviteCode, setInviteCode] = useState("");
  const activeDm = snapshot.game.active_dm_id === snapshot.me.member_id
    && snapshot.me.role !== "player";
  const readyCount = snapshot.lobby.members.filter(
    (member) => member.readiness_status === "ready",
  ).length;
  const acceptedCount = snapshot.lobby.members.filter(
    (member) => member.consent_status === "accepted",
  ).length;
  const allReady = readyCount === snapshot.lobby.members.length;
  const houseRules = settings.house_rules;
  const hasUnsavedChanges =
    settingsDirty || personalDirty || scheduleDirty;

  useEffect(() => {
    if (!settingsDirty) {
      setSettings(snapshot.lobby.settings);
      setSettingsVersion(snapshot.lobby.settings_version);
    }
  }, [settingsDirty, snapshot.lobby.settings, snapshot.lobby.settings_version]);

  useEffect(() => {
    if (!personalDirty) {
      setConsent(own.consent_status);
      setReadiness(own.readiness_status);
      setLines((own.safety_preferences?.lines ?? []).join("\n"));
      setVeils((own.safety_preferences?.veils ?? []).join("\n"));
      setNotes(own.safety_preferences?.notes ?? "");
      setReadinessVersion(own.readiness_version);
    }
  }, [own, personalDirty]);

  useEffect(() => {
    if (!scheduleDirty) {
      setScheduledAt(toLocalDateTimeInput(snapshot.lobby.scheduled_at));
      setScheduleRevision(snapshot.revision);
    }
  }, [scheduleDirty, snapshot.lobby.scheduled_at, snapshot.revision]);

  useEffect(() => {
    if (!activeDm) {
      setInviteCode("");
      setSettings(snapshot.lobby.settings);
      setSettingsVersion(snapshot.lobby.settings_version);
      setSettingsDirty(false);
    }
  }, [activeDm, snapshot.lobby.settings, snapshot.lobby.settings_version]);

  useEffect(() => {
    if (!hasUnsavedChanges) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [hasUnsavedChanges]);

  async function perform(label: string, action: () => Promise<unknown>) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(label);
    setMessage("");
    try {
      await action();
      await onRefresh();
      setHasConflict(false);
      setMessage("Değişiklikler kaydedildi.");
    } catch (reason) {
      const detail = reason instanceof Error
        ? reason.message
        : "İşlem tamamlanamadı.";
      if (detail.toLocaleLowerCase("tr").includes("conflict")) {
        setHasConflict(true);
        await onRefresh();
      }
      setMessage(detail);
    } finally {
      busyRef.current = false;
      setBusy("");
    }
  }

  function updateRule(
    index: number,
    patch: Partial<CampaignLobby["settings"]["house_rules"][number]>,
  ) {
    setSettingsDirty(true);
    setSettings({
      ...settings,
      house_rules: houseRules.map((rule, ruleIndex) =>
        ruleIndex === index ? { ...rule, ...patch } : rule
      ),
    });
  }

  const nextSession = useMemo(
    () => snapshot.lobby.scheduled_at
      ? new Intl.DateTimeFormat("tr-TR", {
          dateStyle: "medium",
          timeStyle: "short",
        }).format(new Date(snapshot.lobby.scheduled_at))
      : "Henüz planlanmadı",
    [snapshot.lobby.scheduled_at],
  );

  if (!ownMember) {
    return <main className="center-state">Campaign üyeliği yüklenemedi.</main>;
  }

  return (
    <main className="campaign-dashboard" id="main-content">
      <header className="campaign-dashboard-header">
        <div>
          <span className="eyebrow">Campaign Hub</span>
          <h1>{snapshot.campaign.name}</h1>
          <p>{snapshot.campaign.ruleset_version} · {snapshot.campaign.play_style}</p>
        </div>
        <button className="icon-button" disabled={Boolean(busy)} onClick={() => {
          if (
            hasUnsavedChanges
            && !window.confirm("Kaydedilmemiş Campaign değişiklikleri bırakılsın mı?")
          ) return;
          onClose();
        }} aria-label="Campaign dashboard'u kapat"><X /></button>
      </header>
      {message && <div className="campaign-message" role={hasConflict ? "alert" : "status"}>
        <span>{message}</span>
        {hasConflict && <button onClick={() => {
          setSettings(snapshot.lobby.settings);
          setSettingsVersion(snapshot.lobby.settings_version);
          setSettingsDirty(false);
          setConsent(own.consent_status);
          setReadiness(own.readiness_status);
          setLines((own.safety_preferences?.lines ?? []).join("\n"));
          setVeils((own.safety_preferences?.veils ?? []).join("\n"));
          setNotes(own.safety_preferences?.notes ?? "");
          setReadinessVersion(own.readiness_version);
          setPersonalDirty(false);
          setScheduledAt(toLocalDateTimeInput(snapshot.lobby.scheduled_at));
          setScheduleRevision(snapshot.revision);
          setScheduleDirty(false);
          setHasConflict(false);
          setMessage("Sunucu sürümü yüklendi.");
        }}>Sunucu sürümünü yükle</button>}
      </div>}

      <section className="campaign-kpis">
        <div><Users /><span>Party</span><strong>{snapshot.lobby.members.length}</strong></div>
        <div className={allReady ? "complete" : ""}><Check /><span>Ready</span><strong>{readyCount}/{snapshot.lobby.members.length}</strong></div>
        <div><ShieldCheck /><span>Consent</span><strong>{acceptedCount}/{snapshot.lobby.members.length}</strong></div>
        <div><CalendarClock /><span>Sonraki oturum</span><strong>{nextSession}</strong></div>
      </section>

      <div className="campaign-dashboard-grid">
        <div className="campaign-dashboard-main">
          <section className="campaign-card">
            <h2><Users /> Lobi ve readiness</h2>
            <div className="lobby-member-list">
              {snapshot.lobby.members.map((member) => (
                <div key={member.member_id}>
                  <span className="member-avatar">{member.name.slice(0, 1).toUpperCase()}</span>
                  <div><strong>{member.name}</strong><small>{member.role}</small></div>
                  <span className={`consent-pill ${member.consent_status}`}>{member.consent_status}</span>
                  <span className={`ready-pill ${member.readiness_status}`}>{member.readiness_status === "ready" ? "Ready" : "Not ready"}</span>
                </div>
              ))}
            </div>
          </section>

          <section className="campaign-card">
            <h2><ClipboardList /> House rules</h2>
            {houseRules.length === 0 && <p className="muted">House rule tanımlanmadı; pinned SRD kuralları geçerli.</p>}
            <div className="house-rule-list">
              {houseRules.map((rule, index) => (
                <article key={rule.id}>
                  {activeDm ? <>
                    <input disabled={Boolean(busy)} aria-label={`House rule ${index + 1} başlığı`} value={rule.title} maxLength={120} onChange={(event) => updateRule(index, { title: event.target.value })} />
                    <textarea disabled={Boolean(busy)} aria-label={`House rule ${index + 1} açıklaması`} value={rule.description} maxLength={1000} onChange={(event) => updateRule(index, { description: event.target.value })} />
                    <label><input disabled={Boolean(busy)} type="checkbox" checked={rule.enabled} onChange={(event) => updateRule(index, { enabled: event.target.checked })} /> Etkin</label>
                    <button disabled={Boolean(busy)} aria-label={`${rule.title} kuralını sil`} onClick={() => { setSettingsDirty(true); setSettings({ ...settings, house_rules: houseRules.filter((_, ruleIndex) => ruleIndex !== index) }); }}><Trash2 size={16} /></button>
                  </> : <>
                    <div><strong>{rule.title}</strong><p>{rule.description}</p></div>
                    <span>{rule.enabled ? "Etkin" : "Kapalı"}</span>
                  </>}
                </article>
              ))}
            </div>
            {activeDm && <button className="secondary-action" disabled={Boolean(busy) || houseRules.length >= 50} onClick={() => { setSettingsDirty(true); setSettings({ ...settings, house_rules: [...houseRules, { id: crypto.randomUUID(), title: "Yeni house rule", description: "", enabled: true }] }); }}><Plus size={16} /> Kural ekle</button>}
          </section>

          <section className="campaign-card">
            <h2><ShieldCheck /> Session Zero güvenlik araçları</h2>
            <div className="safety-tools">{SAFETY_TOOLS.map((tool) => <label key={tool.id}><input type="checkbox" disabled={!activeDm || Boolean(busy)} checked={settings.safety_tools.includes(tool.id)} onChange={() => { setSettingsDirty(true); setSettings({ ...settings, safety_tools: toggle(settings.safety_tools, tool.id) }); }} /> {tool.label}</label>)}</div>
            <label>Session Zero agenda<textarea disabled={!activeDm || Boolean(busy)} value={settings.session_zero_agenda.join("\n")} onChange={(event) => { setSettingsDirty(true); setSettings({ ...settings, session_zero_agenda: linesOf(event.target.value, 30) }); }} placeholder="Ton ve tema&#10;Karakter bağları&#10;Masa kuralları" /></label>
            {activeDm && <button className="primary-button" disabled={Boolean(busy)} onClick={() => perform("settings", async () => {
              const response = await api.updateCampaignSettings(token, settingsVersion, settings);
              setSettings(response.lobby.settings);
              setSettingsVersion(response.lobby.settings_version);
              setSettingsDirty(false);
            })}>{busy === "settings" ? "Kaydediliyor…" : "Campaign ayarlarını kaydet"}</button>}
          </section>
        </div>

        <aside className="campaign-dashboard-side">
          <section className="campaign-card personal-zero">
            <h2>Benim Session Zero durumum</h2>
            <label>Onay<select disabled={Boolean(busy)} value={consent} onChange={(event) => {
              const next = event.target.value as typeof consent;
              setPersonalDirty(true);
              setConsent(next);
              if (next !== "accepted") setReadiness("not_ready");
            }}><option value="pending">Bekliyor</option><option value="accepted">Kabul ediyorum</option><option value="declined">Kabul etmiyorum</option></select></label>
            <label><input type="checkbox" checked={readiness === "ready"} disabled={Boolean(busy) || consent !== "accepted"} onChange={(event) => { setPersonalDirty(true); setReadiness(event.target.checked ? "ready" : "not_ready"); }} /> Oynamaya hazırım</label>
            <label>Lines<textarea disabled={Boolean(busy)} value={lines} onChange={(event) => { setPersonalDirty(true); setLines(event.target.value); }} maxLength={12_000} placeholder="Oyunda hiç yer almamasını istediğin içerikler" /></label>
            <label>Veils<textarea disabled={Boolean(busy)} value={veils} onChange={(event) => { setPersonalDirty(true); setVeils(event.target.value); }} maxLength={12_000} placeholder="Sahne dışında kalmasını istediğin içerikler" /></label>
            <label>DM için özel not<textarea disabled={Boolean(busy)} value={notes} onChange={(event) => { setPersonalDirty(true); setNotes(event.target.value); }} maxLength={2000} /></label>
            <small>Lines, veils ve özel not yalnız sana ve DM rollerine görünür.</small>
            <button className="primary-button" disabled={Boolean(busy)} onClick={() => perform("personal", () => api.updateSessionZero(token, {
              expected_version: readinessVersion,
              readiness_status: readiness,
              consent_status: consent,
              lines: linesOf(lines, 50),
              veils: linesOf(veils, 50),
              notes,
            }).then((response) => {
              const member = response.lobby.members.find(
                (item) => item.member_id === snapshot.me.member_id,
              );
              if (!member) throw new Error("Güncel üyelik durumu alınamadı.");
              setReadinessVersion(member.readiness_version);
              setConsent(member.consent_status);
              setReadiness(member.readiness_status);
              setLines((member.safety_preferences?.lines ?? []).join("\n"));
              setVeils((member.safety_preferences?.veils ?? []).join("\n"));
              setNotes(member.safety_preferences?.notes ?? "");
              setPersonalDirty(false);
            }))}>{busy === "personal" ? "Kaydediliyor…" : "Durumumu kaydet"}</button>
          </section>

          <section className="campaign-card">
            <h2><CalendarClock /> Planlanan oturum</h2>
            <p>{nextSession}</p>
            {activeDm && <><label>Tarih ve saat<input disabled={Boolean(busy)} type="datetime-local" value={scheduledAt} onChange={(event) => { setScheduleDirty(true); setScheduledAt(event.target.value); }} /></label><button disabled={Boolean(busy)} onClick={() => perform("schedule", async () => {
              const response = await api.scheduleSession(token, scheduleRevision, scheduleIso(scheduledAt));
              setScheduledAt(toLocalDateTimeInput(response.session.scheduled_at ?? null));
              setScheduleRevision(response.revision);
              setScheduleDirty(false);
            })}>Planı kaydet</button></>}
          </section>

          <section className="campaign-card">
            <h2>Davet</h2>
            {activeDm && inviteCode ? <div className="one-time-invite"><code>{inviteCode}</code><button disabled={Boolean(busy)} onClick={async () => {
              try {
                await navigator.clipboard.writeText(inviteCode);
                setMessage("Davet kodu panoya kopyalandı.");
              } catch {
                setMessage("Davet kodu panoya kopyalanamadı; kodu elle seçin.");
              }
            }} aria-label="Davet kodunu kopyala"><Copy size={16} /></button></div> : <p className="muted">Güvenlik nedeniyle davet kodu yalnız oluşturulduğu anda gösterilir.</p>}
            {activeDm && <button onClick={() => perform("invite", async () => {
              const invite = await api.rotateInvite(token);
              setInviteCode(invite.invite_code);
            })}>Yeni davet oluştur</button>}
          </section>
        </aside>
      </div>
    </main>
  );
}

function linesOf(value: string, limit: number) {
  return value.split("\n").map((item) => item.trim()).filter(Boolean).slice(0, limit);
}
function toLocalDateTimeInput(value: string | null | undefined) {
  if (!value) return "";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "";
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}
function scheduleIso(value: string) {
  if (!value) return null;
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) {
    throw new Error("Planlanan oturum tarihi geçersiz.");
  }
  return date.toISOString();
}
function toggle<T>(values: T[], value: T) {
  return values.includes(value) ? values.filter((item) => item !== value) : [...values, value];
}
