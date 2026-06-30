# D&D RAG Chatbot

D&D kural kitaplarına soru soran bir RAG chatbot. LlamaIndex + Qdrant + Streamlit ile kurulu, LLM olarak Ollama (lokal) veya Gemini (API) seçilebilir.

## Mimari

Soru gelince `RouterQueryEngine` hangi kitabın ilgili olduğuna karar verir, o kitabın vektör indeksinden chunk'ları çeker ve LLM ile yanıt üretir.

```
Soru → Router (LLM) → Kitap seçimi → Qdrant retrieval → LLM → Cevap
```

- **Vektör DB:** Qdrant (Docker) — her kitap ayrı collection
- **Embedding:** nomic-embed-text (Ollama)
- **LLM:** llama3.2:3b (Ollama) veya gemini-2.0-flash-lite (Gemini API)
- **PDF okuma:** PyMuPDF

## Kurulum

**Gereksinimler**

- [uv](https://docs.astral.sh/uv/)
- [Ollama](https://ollama.com/) — `ollama pull llama3.2:3b && ollama pull nomic-embed-text`
- [Docker](https://www.docker.com/) — Qdrant için

**Adımlar**

```bash
git clone <repo-url>
cd Project

uv sync

# Qdrant başlat
docker run -d -p 6333:6333 -p 6334:6334 \
  -v qdrant_storage:/qdrant/storage qdrant/qdrant

# PDF'leri data/ klasörüne koy, ardından ingest et
uv run python ingestion.py

# Uygulamayı başlat
uv run streamlit run main.py
```

**Gemini kullanmak için** proje kökünde `.env` oluştur:

```
GEMINI_API_KEY=your_api_key_here
```

## Kullanım

Uygulama açılınca sol sidebar'dan **Ollama** veya **Gemini** seçilir. Sohbet kutusuna soru yazılır.

Yeni PDF eklemek için `data/` klasörüne koy, `ingestion.py`'ı tekrar çalıştır (mevcut collection'lar atlanır) ve `metadata.yaml`'a kitap açıklamasını ekle.
