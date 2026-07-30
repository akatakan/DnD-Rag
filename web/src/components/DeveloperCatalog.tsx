import { useMemo, useState } from "react";
import {
  Copy,
  Database,
  LockKeyhole,
  Plus,
  RefreshCw,
  Save,
  Trash2,
  Upload,
} from "lucide-react";
import { api } from "../api";
import type {
  DeveloperCatalogEntry,
  DeveloperRulesetDetail,
  DeveloperRulesetSummary,
  RulesCatalogEntityType,
  RulesCatalogEntry,
} from "../types";

const ENTITY_TYPES: RulesCatalogEntityType[] = [
  "class",
  "species",
  "background",
  "spell",
  "feature",
  "item",
  "condition",
];

const DATA_TEMPLATES: Record<RulesCatalogEntityType, Record<string, unknown>> = {
  class: {
    hit_die: 8,
    primary_abilities: ["Wisdom"],
    saving_throw_proficiencies: ["Wisdom", "Charisma"],
    armor_training: ["Light"],
    starting_feature_ids: ["feature:replace-me"],
    skill_proficiency_count: 2,
    skill_proficiency_options: ["Insight", "Medicine", "Perception"],
    average_hp_per_level: 5,
  },
  species: {
    creature_type: "Humanoid",
    size_options: ["Medium"],
    speed: 30,
    traits: ["Replace Me"],
    skill_choice_count: 0,
  },
  background: {
    ability_options: ["Strength", "Dexterity", "Constitution"],
    feat: "Replace Me",
    skill_proficiencies: ["Athletics", "Survival"],
    tool_proficiency: "Replace Me",
  },
  spell: {
    level: 1,
    school: "Evocation",
    casting_time: "Action",
    range: "60 feet",
    components: ["V", "S"],
    duration: "Instantaneous",
    healing: "None",
    higher_slot: "None",
  },
  feature: {
    class_id: "class:replace-me",
    level: 1,
    activation: "Action",
    effect: "Replace Me",
    initial_uses: 0,
    uses_by_level: { "1": 2, "4": 3, "10": 4 },
    recovery: "Long Rest",
  },
  item: {
    category: "Adventuring Gear",
    armor_class_bonus: 0,
    strength_requirement: null,
    stealth_disadvantage: false,
    weight_lb: 1,
    cost_gp: 1,
    equipment_slot: null,
    armor_training: null,
    requires_attunement: false,
    container_capacity_lb: null,
  },
  condition: {
    effects: ["Replace Me"],
  },
};

interface EditorState {
  existingId: string | null;
  type: RulesCatalogEntityType;
  slug: string;
  name: string;
  data: string;
  pageLabels: string;
  section: string;
  method: "curated" | "derived";
}

function blankEditor(type: RulesCatalogEntityType = "item"): EditorState {
  return {
    existingId: null,
    type,
    slug: "",
    name: "",
    data: JSON.stringify(DATA_TEMPLATES[type], null, 2),
    pageLabels: "",
    section: "",
    method: "curated",
  };
}

function editorFromEntry(entry: RulesCatalogEntry): EditorState {
  return {
    existingId: entry.id,
    type: entry.type,
    slug: entry.slug,
    name: entry.name,
    data: JSON.stringify(entry.data, null, 2),
    pageLabels: entry.provenance.page_labels.join(", "),
    section: entry.provenance.section,
    method: entry.provenance.method,
  };
}

export default function DeveloperCatalog() {
  const [token, setToken] = useState("");
  const [authenticated, setAuthenticated] = useState(false);
  const [rulesets, setRulesets] = useState<DeveloperRulesetSummary[]>([]);
  const [detail, setDetail] = useState<DeveloperRulesetDetail | null>(null);
  const [editor, setEditor] = useState<EditorState>(blankEditor);
  const [query, setQuery] = useState("");
  const [cloneVersion, setCloneVersion] = useState("");
  const [cloneName, setCloneName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const filteredEntries = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return (detail?.entries ?? []).filter((entry) =>
      !normalized
      || entry.id.toLowerCase().includes(normalized)
      || entry.name.toLowerCase().includes(normalized)
    );
  }, [detail, query]);

  const refreshRulesets = async (nextToken = token) => {
    const response = await api.developerRulesets(nextToken);
    setRulesets(response.rulesets);
    setAuthenticated(true);
    return response.rulesets;
  };

  const run = async (operation: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await operation();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "İşlem başarısız.");
    } finally {
      setBusy(false);
    }
  };

  const login = () => run(async () => {
    await refreshRulesets(token);
    setNotice("Developer catalog erişimi doğrulandı.");
  });

  const openRuleset = (version: string) => run(async () => {
    const next = await api.developerRuleset(token, version);
    setDetail(next);
    setEditor(blankEditor());
  });

  const clone = () => run(async () => {
    const source = detail?.ruleset.publication_status === "published"
      ? detail.ruleset.id
      : rulesets.find((ruleset) => ruleset.is_default)?.id;
    if (!source) throw new Error("Klonlanacak published ruleset seç.");
    const next = await api.cloneDeveloperRuleset(
      token, source, cloneVersion.trim(), cloneName.trim(),
    );
    setDetail(next);
    setCloneVersion("");
    setCloneName("");
    await refreshRulesets();
    setNotice("Draft ruleset oluşturuldu.");
  });

  const saveEntry = () => run(async () => {
    if (!detail || detail.ruleset.publication_status !== "draft") {
      throw new Error("Kayıt eklemek için draft ruleset seç.");
    }
    let data: Record<string, unknown>;
    try {
      data = JSON.parse(editor.data) as Record<string, unknown>;
    } catch {
      throw new Error("Data alanı geçerli JSON olmalı.");
    }
    const source = JSON.parse(detail.ruleset.source_json) as {
      document_id: string;
      document_sha256: string;
    };
    const entry: DeveloperCatalogEntry = {
      id: `${editor.type}:${editor.slug.trim()}`,
      type: editor.type,
      slug: editor.slug.trim(),
      name: editor.name.trim(),
      data,
      provenance: {
        document_id: source.document_id,
        document_sha256: source.document_sha256,
        page_labels: editor.pageLabels
          .split(",")
          .map((value) => value.trim())
          .filter(Boolean),
        section: editor.section.trim(),
        method: editor.method,
      },
    };
    const next = await api.saveDeveloperCatalogEntry(
      token,
      detail.ruleset.id,
      detail.ruleset.revision,
      entry,
    );
    setDetail(next);
    setEditor(editorFromEntry(
      next.entries.find((item) => item.id === entry.id)!,
    ));
    await refreshRulesets();
    setNotice("Katalog kaydı DB'ye kaydedildi.");
  });

  const deleteEntry = () => run(async () => {
    if (!detail || !editor.existingId) return;
    if (!window.confirm(`${editor.existingId} kaydı draft'tan silinsin mi?`)) return;
    const next = await api.deleteDeveloperCatalogEntry(
      token,
      detail.ruleset.id,
      editor.existingId,
      detail.ruleset.revision,
    );
    setDetail(next);
    setEditor(blankEditor());
    await refreshRulesets();
    setNotice("Katalog kaydı silindi.");
  });

  const publish = () => run(async () => {
    if (!detail) return;
    if (!window.confirm(
      "Bu ruleset immutable olarak yayınlansın ve yeni kampanyalar için default yapılsın mı?",
    )) return;
    const next = await api.publishDeveloperRuleset(
      token,
      detail.ruleset.id,
      detail.ruleset.revision,
      true,
    );
    setDetail(next);
    await refreshRulesets();
    setNotice("Ruleset yayınlandı ve yeni kampanyalar için default oldu.");
  });

  if (!authenticated) {
    return <main className="developer-login" id="main-content">
      <form onSubmit={(event) => { event.preventDefault(); void login(); }}>
        <LockKeyhole size={34} />
        <span className="eyebrow">Restricted Development Surface</span>
        <h1>Catalog Developer</h1>
        <p>Bu ekran navigasyonda görünmez ve backend developer token olmadan yanıt vermez.</p>
        <label>Developer token
          <input
            type="password"
            autoComplete="off"
            value={token}
            onChange={(event) => setToken(event.target.value)}
          />
        </label>
        {error && <p className="developer-error" role="alert">{error}</p>}
        <button className="primary-button" disabled={busy || token.length < 32}>
          <LockKeyhole size={17} /> Erişimi doğrula
        </button>
      </form>
    </main>;
  }

  return <main className="developer-shell" id="main-content">
    <header className="developer-header">
      <div>
        <span className="eyebrow">Restricted Development Surface</span>
        <h1><Database size={25} /> Rules Catalog DB</h1>
        <p>Draft üzerinde çalış, doğrula, immutable sürüm olarak yayınla.</p>
      </div>
      <button onClick={() => { void run(async () => { await refreshRulesets(); }); }} disabled={busy}>
        <RefreshCw size={16} /> Yenile
      </button>
    </header>
    {error && <div className="developer-message error" role="alert">{error}</div>}
    {notice && <div className="developer-message" role="status">{notice}</div>}
    <div className="developer-layout">
      <aside className="developer-sidebar">
        <h2>Ruleset sürümleri</h2>
        <div className="developer-rulesets">
          {rulesets.map((ruleset) => <button
            key={ruleset.id}
            className={detail?.ruleset.id === ruleset.id ? "selected" : ""}
            onClick={() => { void openRuleset(ruleset.id); }}
          >
            <strong>{ruleset.name}</strong>
            <span>{ruleset.id}</span>
            <small>
              {ruleset.publication_status} · r{ruleset.revision}
              {ruleset.is_default ? " · default" : ""}
            </small>
          </button>)}
        </div>
        <fieldset>
          <legend><Copy size={14} /> Yeni draft klonla</legend>
          <label>Sürüm ID
            <input value={cloneVersion} onChange={(event) => setCloneVersion(event.target.value)} placeholder="srd-5.2.1-custom.1" />
          </label>
          <label>Görünen ad
            <input value={cloneName} onChange={(event) => setCloneName(event.target.value)} placeholder="SRD Custom 1" />
          </label>
          <button disabled={busy || !cloneVersion.trim() || !cloneName.trim()} onClick={() => { void clone(); }}>
            <Copy size={15} /> Seçileni klonla
          </button>
        </fieldset>
      </aside>
      <section className="developer-content">
        {!detail ? <div className="developer-empty">
          <Database size={34} />
          <h2>Bir ruleset seç</h2>
          <p>Published sürümler salt okunur; düzenleme için draft klon oluştur.</p>
        </div> : <>
          <header className="developer-ruleset-header">
            <div>
              <span>{detail.ruleset.publication_status}</span>
              <h2>{detail.ruleset.name}</h2>
              <p>{detail.ruleset.entry_count} kayıt · revision {detail.ruleset.revision}</p>
            </div>
            {detail.ruleset.publication_status === "draft" && <button className="publish-button" onClick={() => { void publish(); }} disabled={busy}>
              <Upload size={16} /> Yayınla ve default yap
            </button>}
          </header>
          <div className="developer-workspace">
            <section className="developer-entry-list">
              <div className="developer-entry-toolbar">
                <input aria-label="Katalog kayıtlarında ara" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="ID veya isim ara" />
                <button onClick={() => setEditor(blankEditor())} disabled={detail.ruleset.publication_status !== "draft"}>
                  <Plus size={16} /> Yeni
                </button>
              </div>
              <div>
                {filteredEntries.map((entry) => <button
                  key={entry.id}
                  className={editor.existingId === entry.id ? "selected" : ""}
                  onClick={() => setEditor(editorFromEntry(entry))}
                >
                  <strong>{entry.name}</strong>
                  <span>{entry.id}</span>
                </button>)}
              </div>
            </section>
            <EntryEditor
              editor={editor}
              readOnly={detail.ruleset.publication_status !== "draft"}
              busy={busy}
              onChange={setEditor}
              onSave={() => { void saveEntry(); }}
              onDelete={() => { void deleteEntry(); }}
            />
          </div>
        </>}
      </section>
    </div>
  </main>;
}

function EntryEditor({
  editor,
  readOnly,
  busy,
  onChange,
  onSave,
  onDelete,
}: {
  editor: EditorState;
  readOnly: boolean;
  busy: boolean;
  onChange: (value: EditorState) => void;
  onSave: () => void;
  onDelete: () => void;
}) {
  const patch = <K extends keyof EditorState>(key: K, value: EditorState[K]) => {
    onChange({ ...editor, [key]: value });
  };
  return <form className="developer-editor" onSubmit={(event) => {
    event.preventDefault();
    onSave();
  }}>
    <h3>{editor.existingId ? "Kaydı düzenle" : "Yeni katalog kaydı"}</h3>
    <div className="developer-editor-grid">
      <label>Tür
        <select
          value={editor.type}
          disabled={readOnly || Boolean(editor.existingId)}
          onChange={(event) => {
            const type = event.target.value as RulesCatalogEntityType;
            onChange(blankEditor(type));
          }}
        >
          {ENTITY_TYPES.map((type) => <option key={type}>{type}</option>)}
        </select>
      </label>
      <label>Slug
        <input value={editor.slug} disabled={readOnly || Boolean(editor.existingId)} onChange={(event) => patch("slug", event.target.value)} placeholder="canonical-slug" />
      </label>
    </div>
    <label>Ad
      <input value={editor.name} disabled={readOnly} onChange={(event) => patch("name", event.target.value)} />
    </label>
    <label>Data JSON
      <textarea rows={15} spellCheck={false} value={editor.data} disabled={readOnly} onChange={(event) => patch("data", event.target.value)} />
    </label>
    <div className="developer-editor-grid">
      <label>SRD sayfa etiketleri
        <input value={editor.pageLabels} disabled={readOnly} onChange={(event) => patch("pageLabels", event.target.value)} placeholder="47-54, 86" />
      </label>
      <label>Provenance yöntemi
        <select value={editor.method} disabled={readOnly} onChange={(event) => patch("method", event.target.value as EditorState["method"])}>
          <option value="curated">curated</option>
          <option value="derived">derived</option>
        </select>
      </label>
    </div>
    <label>Kaynak bölümü
      <input value={editor.section} disabled={readOnly} onChange={(event) => patch("section", event.target.value)} placeholder="Character Classes: ..." />
    </label>
    {!readOnly && <div className="developer-editor-actions">
      {editor.existingId && <button type="button" className="danger-button" onClick={onDelete} disabled={busy}>
        <Trash2 size={16} /> Sil
      </button>}
      <button className="primary-button" disabled={busy || !editor.slug.trim() || !editor.name.trim()}>
        <Save size={16} /> DB'ye kaydet
      </button>
    </div>}
  </form>;
}
