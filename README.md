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

## Gelecek Geliştirmeler (Future Roadmap)
Bu proje, temel bir RAG yapısından tam teşekküllü bir "AI Agentic Framework" yapısına evrilmektedir. Planlanan geliştirmeler şunlardır:

🛠️ Sistem Mimarisi ve Backend
[ ] WebSocket Entegrasyonu: Streamlit'in statik yapısından kurtularak, çok oyunculu (multiplayer) senaryoları destekleyen, gerçek zamanlı veri akışı sağlayan bir WebSocket altyapısına geçiş.

[ ] Asenkron Dönüşüm: Tüm I/O işlemleri (Qdrant sorguları, LLM API çağrıları) async yapılarla optimize edilerek UI engellenmesinin (blocking) önüne geçilecek.

[ ] State Machine: Oyunun durumunu (kimin sırası, oyuncu/canavar statları) merkezi bir Game Engine üzerinden yöneten bir durum makinesi entegrasyonu.

🧠 RAG ve Veri İşleme
[ ] Multi-Vector İndeksleme: Tabloları, şemaları ve kuralları daha iyi anlamlandırmak için dokümanların özetleri ve detaylarını içeren hiyerarşik indeksleme yapısına geçiş.

[ ] Reranking: Cohere Rerank veya lokal bir Cross-Encoder entegrasyonu ile retriever sonucunda gelen verilerin alaka düzeyini optimize ederek halüsinasyonları minimize etme.

[ ] Gelişmiş Parser: PDF içerisindeki tabloları ve karmaşık D&D biçimlendirmelerini korumak için LlamaParse entegrasyonu.

🤖 Ajan Yetenekleri
[ ] Tool Use (Function Calling): LLM'e zar atma, HP takip etme ve envanter yönetimi gibi görevleri yerine getirebilmesi için Python fonksiyonlarını kullanma yetkisi verilmesi.

[ ] Multi-Selector: LLMSingleSelector yerine LLMMultiSelector kullanılarak, oyuncunun sorusuna göre birden fazla kural kitabının (örn: PHB + DMG) aynı anda sorgulanması.

💾 Hafıza ve Bağlam
[ ] ContextChatEngine: Chat geçmişini sadece metin olarak değil, özetlenmiş bir bağlam penceresi olarak tutan ContextChatEngine yapısına geçiş.

[ ] Long-Term Memory: Oyun seanslarının özetlerini tutan, geçmişte yaşanan olayları (NPC isimleri, alınan kararlar) hatırlayan vektörel bir "Oyun Hafızası" koleksiyonunun Qdrant'a eklenmesi.

🎭 Creative LLM
[ ] Kural vs. Hikaye Ayrımı: Teknik kural bilgisini (RAG) ve yaratıcı betimlemeyi (Creative LLM) ayıran çift aşamalı bir yanıt oluşturma pipeline'ı.
