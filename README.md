# D&D RAG Chatbot

D&D temel kural kitaplarını kaynak ve sayfa numarasıyla yanıtlayan Streamlit RAG
uygulaması. LlamaIndex, Qdrant, Ollama ve isteğe bağlı Gemini kullanır.

## Özellikler

- Oyuncu ve DM kitaplarında tekli veya çoklu kitap retrieval
- Qdrant dense retrieval ve seçilebilir BM25 hybrid search
- Kitap ve sayfa aralığı metadata filtreleri
- Yanıtta kullanılan kitap ve PDF sayfalarının gösterimi
- Canlı kaynak seçimi, okuma, reranking ve yanıt hazırlama durumları
- Seçilebilir Ollama tabanlı LLM reranking
- 40 soruluk router/retrieval evaluation seti
- PDF hash ve indeks sürümü tabanlı yeniden ingestion

## Mimari

```text
Soru -> kitap router -> bir veya iki Qdrant koleksiyonu
      -> dense/hybrid retrieval -> opsiyonel rerank
      -> kaynak odaklı RAG promptu -> birleşik yanıt
```

İki mevcut kitap için router deterministiktir: monster/stat-block terimleri DM
kitabına, karşılaştırma soruları iki kitaba, diğer kurallar oyuncu kitabına gider.
Farklı kitap kataloglarında LLM selector fallback'i kullanılır.

## Kurulum

Gereksinimler: Python 3.10+, [uv](https://docs.astral.sh/uv/), Docker ve Ollama.

```bash
uv sync
ollama pull llama3.2:3b
ollama pull nomic-embed-text
docker compose up -d
uv run python ingestion.py
uv run streamlit run main.py
```

İlk Aşama 2 ingestion çalıştırması koleksiyonları dense + BM25 sparse vektör
şemasında yeniden oluşturur. Sonraki çalıştırmalarda PDF hash'i, embedding modeli,
sparse model ve indeks sürümü değişmediyse kitap atlanır.

Gemini kullanmak için `.env.example` dosyasını temel alan bir `.env` oluşturup
`GEMINI_API_KEY` değerini tanımlayın. Retrieval ve reranking varsayılanları da aynı
dosyadan değiştirilebilir.

## Kullanım

```bash
uv run streamlit run main.py
```

Sidebar üzerinden kitap kapsamı, Dense/Hybrid modu, reranking ve PDF sayfa aralığı
seçilebilir. Dense retrieval varsayılandır. Hybrid ve reranking ölçülmüş ancak bu
küçük lokal model/veri setinde varsayılan açık tutulmamıştır.

## Oyun Oturumları

Sohbetler ve oyun state'i `runtime/sessions.db` SQLite dosyasında kalıcıdır. Her
oturum kendi konuşma hafızasını, karakterini, HP/envanterini, encounter initiative
durumunu ve notlarını tutar. Sohbette `/roll 2d6+3`, avantaj için
`/roll 2d20kh1+5`, dezavantaj için `/roll 2d20kl1+5` kullanılabilir.

`Kural` modu kaynaklı cevabı doğrudan verir. `Anlatım` modu önce aynı kaynaklı
kural cevabını üretir, sonra ikinci LLM çağrısıyla mevcut karakter/encounter state'ine
uygun sahne anlatımına dönüştürür. İkinci aşama kaynakları ve kural temelini korur.

## Evaluation

Set 20 oyuncu, 15 DM ve 5 multi-book olmak üzere 40 Türkçe/İngilizce sorudan
oluşur. Beklenen kitap ve PDF sayfaları `evaluation/questions.yaml` içindedir.

```bash
# Router + varsayılan hybrid retrieval
uv run python evaluate.py

# Ayrı deneyler
uv run python evaluate.py --mode router
uv run python evaluate.py --mode retrieval --retrieval dense
uv run python evaluate.py --mode retrieval --retrieval hybrid
```

Mevcut raporlar `evaluation/results/` dizinindedir:

| Ölçüm | Sonuç |
|---|---:|
| Router exact accuracy | 100% |
| Multi-book exact accuracy | 100% |
| Dense page hit@10 | 95.56% |
| Dense MRR | 0.7506 |
| Hybrid page hit@10 | 93.33% |
| Hybrid MRR | 0.7654 |
| Kaynak metadata bütünlüğü | 100% |

Hybrid daha iyi ortalama sıra üretse de page-hit oranını düşürdüğü için dense
varsayılan bırakıldı. Ollama LLM reranking doğru kaynağı korudu fakat tek sorguya
yaklaşık 2.5 dakika ekledi; daha güçlü/hızlı bir modelle tekrar ölçülmelidir.


## Multiplayer Uygulama

Gerçek oyun istemcisi React, ortak oyun otoritesi FastAPI'dir. Her oyuncu davet
koduyla kendi cihazından bağlanır. DM ve Player tamamen ayrı çalışma yüzeyleri
alır; Streamlit yalnızca RAG/evaluation laboratuvarı olarak tutulur.

```bash
+# Frontend bağımlılıkları ve production build
+cd web
+npm install
+npm run build
+cd ..
+
+# API + build edilmiş React, tek adres
+uv run python run_api.py
+```

Uygulama `http://localhost:8000` adresindedir. Aynı ağdaki oyuncular sunucunun LAN
IP adresini ve DM'nin ekranda gördüğü davet kodunu kullanır. Frontend geliştirirken
ayrı terminalde `cd web && npm run dev` ile `http://localhost:5173` açılabilir.

### Roller ve Yetki

- **Player:** Kendi karakter detayları, görünür encounter bilgisi, zar, HP değişiklik
  talebi ve dar Kurala Sor paneli.
- **Human DM:** Tüm state, gizli canavarlar/HP, initiative, sahne, onay kuyruğu.
- **Assisted DM:** Human DM yetkili kalır; AI yapılandırılmış plan üretir, DM uygular.
- **AI DM:** AI planındaki doğrulanabilir komutlar backend tarafından uygulanır.

Oyuncu HP'yi doğrudan değiştiremez. Talep DM kuyruğuna gider; onaydan sonra backend
transaction'ı state'i değiştirir ve WebSocket tüm istemcileri günceller. Event
visibility değerleri `public`, `party`, `dm_only` veya `player:<id>` olabilir.

### Multiplayer Mimarisi

```text
React Player/DM clients
        ↕ REST + WebSocket
FastAPI authoritative server
  ├─ bearer token ve rol doğrulama
  ├─ command/game engine
  ├─ visibility/redaction
  ├─ AI DM structured plan
  ├─ RAG rule service
  └─ SQLite event/state store
+```

Yerel MVP `runtime/multiplayer.db` kullanır. `GameStore` sınırı PostgreSQL'e geçiş
için veri erişimini API ve game engine'den ayırır. İnternet üzerinden yayınlamadan
önce TLS, token expiry/rotation ve rate limiting eklenmelidir.

## Yol Haritası

### Aşama 1 - Güvenilir MVP

- [x] Kaynak kitap ve sayfa numarası
- [x] RAG sistem promptu ve servis hata yönetimi
- [x] PDF hash tabanlı ingestion
- [x] Ortam ve Docker yapılandırması

### Aşama 2 - Retrieval Kalitesi

- [x] 40 soruluk evaluation seti
- [x] Router doğruluk ölçümü
- [x] Multi-book retrieval
- [x] Metadata ve sayfa filtreleri
- [x] BM25 hybrid search
- [x] Deneysel LLM reranking

### Aşama 3 - Gerçek D&D Asistanı

- [x] Konuşma ve session memory
- [x] Dice roller tool (`/roll 2d20kh1+5`)
- [x] Character ve encounter state machine
- [x] Inventory, HP ve session yönetimi
- [x] Kural yanıtı ile yaratıcı anlatımı ayıran iki aşamalı pipeline
