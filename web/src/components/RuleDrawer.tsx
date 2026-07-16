import { FormEvent, useState } from "react";
import { BookOpen, Send } from "lucide-react";
import { api } from "../api";

export default function RuleDrawer({ token, compact = false }: { token: string; compact?: boolean }) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [sources, setSources] = useState<{ book: string; page: number }[]>([]);
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault(); if (!question.trim()) return; setBusy(true);
    try { const result = await api.rules(token, question, "rules"); setAnswer(result.answer); setSources(result.sources); }
    finally { setBusy(false); }
  }
  return (
    <section className={`rule-drawer ${compact ? "compact" : ""}`}>
      <h2><BookOpen size={19} /> Kurala Sor</h2>
      <form onSubmit={submit}><input value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Bu tur bonus action kullanabilir miyim?" /><button className="icon-button" title="Sor" disabled={busy}><Send size={18} /></button></form>
      {busy && <div className="muted">Kaynaklar taranıyor...</div>}
      {answer && <div className="rule-answer"><p>{answer}</p>{sources.length > 0 && <small>{sources.map((source) => `${source.book}, s. ${source.page}`).join(" · ")}</small>}</div>}
    </section>
  );
}
