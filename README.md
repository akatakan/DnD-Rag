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

Temel multiplayer API ve web uygulaması için Python 3.10+,
[uv](https://docs.astral.sh/uv/) ve Node.js/npm yeterlidir. Ollama ve yerel modeller
zorunlu değildir; yalnızca yerel RAG, Kurala Sor veya evaluation özellikleri
kullanılacaksa gerekir.

```bash
uv sync
cd web
npm install
npm run build
cd ..
uv run python run_api.py
```

Yerel RAG özelliklerini kullanmak isteyenler ayrıca Docker ve Ollama kurabilir.
LLM modeli sabit değildir: Kullanıcı istediği Ollama sohbet modelini indirip `.env`
içindeki `OLLAMA_LLM_MODEL` alanına aynı model adını yazar. Uncensored ve anlatım
ağırlıklı RPG oturumları için opsiyonel başlangıç önerisi
[`dolphin-llama3:8b`](https://ollama.com/library/dolphin-llama3)'dir; bu yalnızca
öneridir ve otomatik indirilmez.

```bash
docker compose up -d
# Örnek öneri; bunun yerine istediğiniz Ollama modelini kullanabilirsiniz.
ollama pull dolphin-llama3:8b
ollama pull nomic-embed-text
uv run python ingestion.py
uv run streamlit run main.py
```

```dotenv
OLLAMA_LLM_MODEL=dolphin-llama3:8b
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
  ├─ Redis shared runtime (opsiyonel; multi-worker için zorunlu)
  ├─ AI DM structured plan
  ├─ RAG rule service
  └─ SQLite event/state store
```

Yerel MVP `runtime/multiplayer.db` kullanır. `GameStore` sınırı PostgreSQL'e geçiş
için veri erişimini API ve game engine'den ayırır. Token ve davetler HMAC hash ile
saklanır; süreli, döndürülebilir ve iptal edilebilirdir. WebSocket bağlantısı bearer
token yerine 60 saniyelik tek kullanımlık ticket kullanır.

`REDIS_URL` ayarlandığında presence kayıtları bağlantı başına TTL ile Redis'te tutulur;
event/snapshot invalidation ve uzak token iptali pub/sub ile bütün worker'lara taşınır.
DM grace deadline'ları recoverable sorted-set scheduler ve lease lock ile yalnız bir
worker tarafından işlenir. HTTP/WebSocket rate limit pencereleri Redis `TIME` kullanan
atomik Lua script'iyle ortak kota uygular. Redis başlangıçta erişilemiyorsa servis fail
fast olur; subscriber ve durable grace tarayıcısı uygulama lifespan başlangıcında,
ilk WebSocket beklenmeden hazır edilir. Kısa failover sırasında pub/sub worker'ı tekrar
bağlanmayı dener; canlı socket heartbeat'i kaybolmuş presence lease'ini geri kurar.
Async endpoint'lerde senkron Redis sürücüsü worker thread'e aktarılır. `/api/health`,
Redis yapılandırılmışken subscriber veya Redis erişimi sağlıklı değilse `503` döndürür.
Pub/sub yeniden bağlandığında, kesinti sırasında kaçmış ephemeral mesajlar için worker
üzerindeki açık oyunlara authoritative snapshot resync gönderilir.

`REDIS_URL` boşken sıfır ayarlı local/LAN davranışı korunur: presence, broadcast, grace
timer ve rate limit process-local çalışır. Birden fazla worker başlatılacaksa
`WEB_CONCURRENCY` gerçek worker sayısına ayarlanmalı ve `REDIS_URL` verilmelidir;
uygulama `WEB_CONCURRENCY>1` olup Redis yoksa başlamaz. Docker Compose içindeki Redis
servisi `docker compose up -d redis` ile açılabilir. Canlı entegrasyon testi
`TEST_REDIS_URL=redis://localhost:6379/0` verilerek çalıştırılır.

### SQLite Ölçek, Backup ve Restore

Authoritative store tek-host dağıtımda SQLite kullanır. Veritabanı WAL,
`busy_timeout=10000`, foreign key enforcement ve kayıp riskini performansa tercih eden
`synchronous=FULL` ile açılır. Sentetik concurrent probe:

```bash
python -m api.sqlite_scale_probe \
  --concurrency 1,2,4,8 --operations 250 --write-ratio 0.2
```

Probe ayrı connection kullanan thread'leri ve gerçek minimal metadata write'larını
ölçer; multi-process kapasite iddiası değildir ve mevcut bir DB yolunu değiştirmeyi
reddeder. Sekiz-thread örnek profilinde write p99 guardrail'ı aşıldığı için bu profil
SQLite production onayı sayılmaz.

Online backup, doğrulama ve overwrite etmeyen restore:

```bash
python -m api.db_admin backup --source runtime/multiplayer.db --target backups/game.db
python -m api.db_admin verify --source backups/game.db
python -m api.db_admin restore --source backups/game.db --target runtime/restored.db
```

Backup doğrulama ve kopyalamayı aynı WAL read snapshot'ında yapar; atomik yayın başka
bir process'in oluşturduğu hedefi overwrite etmez. Restore var olan hedefi değiştirmez;
API durdurulduktan sonra doğrulanmış yeni dosya `GAME_DB` olarak seçilir. Ölçüm
sonuçları, PostgreSQL geçiş eşikleri, normalized
aggregate ve transactional outbox kararı
[`docs/sqlite-postgresql-evaluation.md`](docs/sqlite-postgresql-evaluation.md)
dosyasındadır. Redis multi-worker coordination aynı hosttaki SQLite writer sınırını
kaldırmaz; multi-host production PostgreSQL geçiş kapısıdır.
`run_api.py` geliştirme/reload runner'ıdır ve tek worker içindir. Çok-worker çalıştırma
gerçek worker sayısı environment ile aynı olacak şekilde, örneğin
`WEB_CONCURRENCY=2 uvicorn api.app:app --workers 2` ile yapılmalıdır. Compose Redis
portu varsayılan olarak yalnız `127.0.0.1` üzerinde yayınlanır.

### Map Asset ve Scene

VTT haritaları SQLite içine binary olarak yazılmaz. API doğrulanmış PNG/JPEG
dosyalarını SHA-256 içerik anahtarıyla `MAP_OBJECT_ROOT` altında saklar; SQLite
yalnız metadata, campaign sahipliği ve scene/grid/viewport revision bilgisini tutar.
Varsayılan tek dosya sınırı 10 MiB (`MAX_MAP_UPLOAD_BYTES`), boyut sınırı
64–8192 px ve campaign başına mantıksal metadata kotası 100 MiB'dir.

Harita yükleme ve scene değiştirme yalnız aktif DM tarafından yapılır. Oyuncu,
asset içeriğine ancak kendi oyununun scene'i yayınlandığında erişebilir; yayınlanmamış
scene adı, grid ve viewport bilgileri de oyuncu projection'ından çıkarılır. Görseller
Bearer token ile alınır ve frontend'de kısa ömürlü Blob URL olarak gösterilir; token
URL query parametresine yazılmaz.

Yerel object store üretimde tek process/tek disk varsayımı taşır. Çok worker veya
yatay ölçek için VTT asset katmanı S3 uyumlu object storage'a taşınmalı; metadata
transaction'ı ile binary write arasında oluşabilecek erişilemeyen content-addressed
orphan dosyalar periyodik garbage collection ile temizlenmelidir.

Encounter tokenları `map_tokens` tablosunda yalnız konum, boyut, sahiplik ve
optimistic-lock revision'ı saklar. Ad, initiative, HP ve gizlilik canlı
`state.combatants` kaydından türetilir; liste ile harita için ikinci bir encounter
state'i oluşturulmaz. Aktif DM canlı encounter listesini scene'e senkronize eder ve
tüm tokenları taşıyabilir. Oyuncu yalnız kendi `character_id` kaydına bağlı tokenı
taşıyabilir. Gizli combatant tokenları ve hareket event'leri player projection'ına
girmez. Hareket ve silme işlemleri token revision CAS; tüm token komutları global
game revision/idempotency receipt ile korunur. Grid veya map boyutu değiştikten sonra
senkronizasyon mevcut token boyutlarını günceller ve merkezlerini yeni asset sınırları
içine taşır.

### Açık Ruleset Kataloğu

Yeni campaign'ler `srd-5.2.1` ruleset sürümünü kullanır. `data/rulesets/srd-5.2.1`
altındaki katalog class, species, background, spell, feature, item ve condition için
başlangıç kayıtları sağlar. Bu katalog tam SRD değildir; `foundation` durumundadır.
Her kayıt resmî SRD belge sürümünü ve SHA-256 özetini, sayfa/section provenance'ını,
CC BY 4.0 lisansını ve gerekli attribution bilgisini taşır. Uygulama başlangıçta
kataloğu veritabanını açıp migration çalıştırmadan önce doğrular; resmî
URL/hash/attribution, entity şeması, referanslar veya kimlik/ad/veri/provenance dahil
onaylı full-entry hash'leri uyuşmazsa hatalı ruleset ile hizmete başlamaz.

Kimliği doğrulanmış istemciler sürümleri `GET /api/rulesets`, filtreli kayıtları
`GET /api/rulesets/{version}/entries` ve tek kaydı
`GET /api/rulesets/{version}/entries/{entry_id}` üzerinden okuyabilir. Liste endpoint'i
`type`, `q`, `offset` ve en fazla 100 olan `limit` parametrelerini kabul eder. Bunlar
statik katalog sorgularıdır; Ollama, Qdrant veya model indirmesi gerektirmez.

### Character Aggregate ve Hesaplamalar

Multiplayer karakterleri schema v2 kullanır. Oyuncu seçimleri ve ham değerler `inputs`,
sunucunun deterministik sonuçları `derived` altında tutulur. Motor ability modifier,
proficiency, saving throw, 18 skill, expertise, AC, initiative, maksimum HP, speed ve
passive perception değerlerini hesaplar. Eski `ac`, `max_hp` ve `class_name` alanları
mevcut UI uyumluluğu için sunulur ancak her hesaplamada backend tarafından yeniden
üretilir ve istemciden yazılamaz.

Genel karakter güncellemesi ability score ve skill seçimlerini kabul eder; AC, HP ve
speed politikaları ilerideki typed equipment/level-up işlemlerine ayrılmıştır. Migration
v9 eski karakterlerin mevcut HP/AC, condition, inventory ve özel class adlarını korur.
HTTP JSON gövdeleri FastAPI doğrulamasından önce varsayılan 256 KiB ile sınırlandırılır;
internet deployment'ında reverse proxy aynı veya daha düşük limit kullanmalıdır.

### Resource, Rest ve Condition Motoru

Karakter resource schema v2; Hit Point Dice, sınıf kaynakları ve death save durumunu
`resource_state`, concentration ile typed/süreli condition kayıtlarını `effects`
altında tutar. `ResourceEngine` level veya class değiştiğinde harcanmış kullanımları
koruyarak maximum değerleri katalogdan yeniden uzlaştırır. Fighter Second Wind typed
bir komuttur; 1d10 + Fighter level iyileştirir ve katalogdaki level tier kullanımlarını
harcar. Generic resource komutu bu özelliği atlayamaz.

Short ve long rest yalnızca pozitif HP ile ve aktif encounter dışında yapılabilir.
Death save yalnızca karakterin aktif turunda bir kez yapılır; tur kimliği round
numarası yerine monotonik `turn_serial` ile izlenir. Encounter içindeki Second Wind
yalnızca karakterin sırasında ve o turun tek Bonus Action hakkıyla kullanılabilir;
`turn_actions` ledger'ı her tur geçişinde atomik olarak sıfırlanır. Ölü durumundaki karakter normal
healing komutuyla geri döndürülemez. Hasar concentration için authoritative
Constitution save üretir; incapacitated, bilinçsizlik ve başarısız save concentration'ı
bitirir. Migration v10-v12 eski karakterleri geri doldurur, eski death-save alanını
dönüştürür ve daha yeni ya da bozuk resource şemasını sessizce ezmeden fail-fast olur.

### Identity Inventory ve Equipment

Karakter inventory schema v1, her eşyayı kalıcı bir `id` ile tutar; eski string
`inventory` alanı yalnız uyumluluk projeksiyonudur. Kayıtlar quantity, katalog kimliği,
ağırlık/değer, equipment slot, parent container, attunement ve equip durumunu taşır.
Container hareketleri kayıp parent, cycle ve kapasite aşımını; silme işlemi dolu container,
equipped veya attuned item'ı reddeder. Migration v13 legacy isimleri korur, eşleşen Shield
kaydını pinned SRD kataloğuna bağlar ve current/future/corrupt state'i fail-closed doğrular.

Para kesesi CP/SP/EP/GP/PP sayılarını tam sayı tutar; her 50 coin 1 lb olarak toplam
ağırlığa katılır. Standard encumbrance Small/Medium karakter için Strength×15 lb taşıma
kapasitesini hesaplar; `ignore` politikası yalnız aktif DM tarafından seçilebilir ve
ağırlığı gizlemeden uyarıyı kapatır. Fighter'ın katalogdaki Shield training'i ve equipped
Shield'ın +2 AC katkısı her hesaplamada backend tarafından yeniden üretilir. Encounter
içinde equip/unequip yalnız karakterin turunda bir Action tüketir.

Attunement en fazla üç item ile sınırlıdır; aynı katalog item'ının iki kopyasına attune
olunamaz. Manuel attune/unattune, encounter dışında 0 Hit Dice harcanan Short Rest ile
atomik yürür; ölüm bütün attunement bağlarını bitirir. Oyuncu kendi tam inventory state'ini
görür; başka karakterlerin detayları ve hidden monster verisi snapshot ve command
cevaplarında aynı role-aware projeksiyonla redacted edilir.

### Typed Spell, Feature ve Action Sözleşmesi

`ActionEngine`, karakter başına versioned known/prepared spell, spell slot ve attack
repertuvarı tutar. Repertuvarı yalnız aktif DM yapılandırır; oyuncu yalnızca kayıtlı
kimlikleri çalıştırabilir. Ability/skill/save, attack, Cure Wounds, concentration,
death save ve Second Wind akışları actor/source/action-cost/mode/roll bilgisi taşıyan
typed intent üretir. Attack bonusu, save/skill modifier'ı, hedef AC ve healing sunucuda
hesaplanır; istemci damage dice veya modifier enjekte edemez. Slot ve sonuç aynı revision
transaction'ında yazılır, duplicate `client_action_id` aynı authoritative sonucu replay
eder. Long Rest tüm spell slotlarını yeniler. Migration v14 eski karakterlere boş ve
geçerli action state ekler; future/corrupt state'te fail closed davranır.

### Character Draft ve Builder API

Migration v15, karakter taslaklarını canlı game aggregate'inden ayrı
`character_drafts` tablosunda tutar. `POST/GET/PATCH
/api/characters/{character_id}/draft` private taslağı başlatır, okur ve optimistic
`expected_revision` ile autosave eder; stale sekme `409` alır. `/draft/navigate`,
`basics → abilities → class → species → background → proficiencies → equipment →
spells → review` sırasında ileri giderken mevcut adımı doğrular, geri dönüşe izin verir.

`publish_character_draft` komutu bütün adımları yeniden doğrular ve karakteri catalog,
derived stats, resource, inventory ve action motorlarından fresh üretir. Draft status,
character state, game revision, event ve idempotency receipt tek SQLite transaction'ında
yazılır; validation veya revision çatışmasında hiçbir parça commit olmaz. Oyuncu yalnız
kendi draft'ını, aktif DM ise kampanyadaki draft'ları yönetebilir.

Migration v26 yeni katılan oyuncuyu `character_ready=0` olarak kaydeder ve private
oluşturma taslağını üyelikle aynı transaction içinde açar. Snapshot bu durumu
`character_creation_required` olarak taşır; oyuncu geçerli karakterini yayınlayana kadar
normal player workspace, Campaign/Session ekranları ve zar FAB'ı yerine zorunlu builder
görür. Migration, mevcut oyunculardan yalnız published draft'ı bulunanları hazır kabul
eder; draft'ı hiç olmayan veya hâlâ active olan oyuncular da oluşturma akışına alınır.

Draft schema v2, ability score'ları pinned SRD kurallarıyla doğrular: Standard Array
tam olarak `15, 14, 13, 12, 10, 8` çoklu kümesini; Point Cost ise 8–15 aralığı ve tam
27 puan bütçesini gerektirir. Background ability artışları katalogdaki izinli
ability'lerle sınırlıdır ve `+2/+1` ya da `+1/+1/+1` dağılımıyla uygulanır. Rastgele ability üretimi
henüz authoritative roll provenance taşımadığı için builder seçeneği değildir.

Oyuncu zorunlu akış dışında üst bardaki **Karakter oluştur** eylemiyle dokuz adımlı
builder workspace'ini açar. Değişiklikler kısa bir debounce sonrasında sırayla autosave edilir; ekrandaki cloud
durumu saved, pending, saving, error ve conflict hallerini ayırır. Başka sekme draft
revision'ını ilerletirse yerel form sessizce ezilmez, kullanıcıya sunucudaki taslağı açıkça
yeniden yükleme seçeneği verilir. İleri geçiş backend adım doğrulamasını, geri geçiş
non-destructive navigasyonu kullanır; review ekranı publish komutunu çalıştırır.

### Gelişmiş Character Sheet

Oyuncu ekranı Overview, Actions, Spells, Inventory, Features ve Notes sekmelerine
ayrılmıştır. Overview authoritative derived stat'ları, save ve skill modifier'larını;
Actions attack repertuvarını; Spells known/prepared ve slot durumunu; Features rest,
Hit Dice, class resource, condition ve concentration bilgisini birlikte gösterir.
Ability, skill, save ve attack eylemleri global zar dialogunu doğru derived modifier ile
açar; dialog modifier'ı read-only tutar ve sonucu raw client toplamı yerine ilgili typed
backend komutundan alır. Advantage/dezavantaj seçimi typed payload'a taşınır.

Notes alanı bu aşamada 20.000 karakter sınırıyla yalnız tarayıcı cihazında saklanır ve
arayüz bunu açıkça belirtir. Campaign senkronizasyonu/backup ayrı bir persistence task'ı
olarak ele alınmalıdır; private notlar snapshot veya event stream'e gönderilmez.

### Typed Global Zar Intent

Global zar FAB'ı serbest expression göndermek yerine `roll_intent` komutunu kullanır.
İstemci actor character, `custom_roll` action, party/private görünürlük,
`global_fab` context ve sınırlandırılmış count/sides/modifier/mode alanlarını taşır;
expression, encounter bağlamı, intent kimliği ve RNG sonucu sunucuda üretilir.
Oyuncu başka bir karakteri actor gösteremez. Private sonuç yalnız atan oyuncu ile
DM/co-DM rollerine görünür.

Migration v20, event satırlarına typed intent kimliği ve schema version metadata'sı
ekler; game başına benzersiz intent index'i ile INSERT/UPDATE validation trigger'ları
bozuk metadata'yı reddeder ve mevcut typed action eventlerini backfill eder.
`client_action_id` receipt'i aynı komutun eşzamanlı, tekrarlı veya response kaybı sonrası
yeniden tesliminde tek `typed_roll_resolved` Game Log olayı ve aynı authoritative
sonucu üretir. Player-visible intent context internal encounter ID/turn index taşımaz.
FAB'daki görünürlük seçimi ile mevcut d4–d100, adet,
advantage/disadvantage ve sonuç animasyonu bu sözleşmeye bağlıdır.

### 3B Zar ve Fizik Sunumu

Authoritative zar sonucu geldikten sonra lazy-loaded `Dice3DTray`, Three.js ile d4,
d6, d8, d10, d12, d20 ve tam 100 triangle yüzlü d100 meshini üretir. cannon-es world;
gravity, friction, restitution ve görünmeyen fizik sınırlarıyla çarpışma/sekme
koreografisini çalıştırır. Görsel masa çizilmez; canvas yüksek z-index'li, şeffaf bir
ekran katmanıdır. Görsel fizik RNG değildir: sonuç, kept/discarded ayrımı ve toplam
backend payload'ından gelir. Her mesh settle olduğunda authoritative sonuç kameraya,
yani kullanıcıya en çok bakan gerçek zar yüzüne yerleşir. d4–d20'nin diğer yüz sayıları
geometrinin yüz merkezine ve yüz normaline bağlı, ışık alan kazıma decal'larıdır; d100
mobil GPU yükünü sınırlamak için görünür yüzlerde bounded etiket kullanır. Sonuç ayrı
bir rozet/yuvarlak içinde tekrarlanmaz.
İlk 12 zar 3B çizilir, kalanı bounded overflow sayacıyla belirtilir.

Migration v21 üye kapsamlı `crimson`, `arcane`, `ivory` tema ve ses tercihini saklar.
`GET/PATCH /api/me/dice-preferences` strict payload, member/game scope trigger'ı ve
rate limit ile çalışır; UI hızlı değişiklikleri sıraya alır. Web Audio yalnız kullanıcı
etkileşimiyle prime edilir ve collision sesleri throttle edilir.

Three ve cannon ana uygulamadan ayrı lazy vendor chunk'larıdır; masa ilk açılışta 3B
motoru indirmez. Renderer 3.3 saniye sonra animation frame, geometry, material, body ve
WebGL kaynaklarını dispose eder; context açıkça kaybedilir ve audio node/context yaşam
döngüsü kapanır. `prefers-reduced-motion` istemcide 3B vendor chunk'ları hiç yüklenmez.
WebGL init/context-loss veya lazy chunk hatasında error boundary authoritative değerleri
statik fallback olarak gösterir. Performans trade-off'u
olarak d6 box, diğer zarlar bounded sphere physics collider kullanır; gerçek polyhedral
görsel mesh korunurken mobil collision solver maliyeti sınırlanır.

### Campaign Hub ve Session Zero

Migration v16 campaign settings'i house rules, safety tools ve Session Zero agenda taşıyan
schema v1'e yükseltir; üyeliklere optimistic readiness version, consent ve private safety
preferences ekler. Oyuncu `ready` olmadan önce consent'i kabul etmelidir. Lines, veils ve
özel not yalnız ilgili üye ile DM/co-DM rollerine döner; party event'i sadece readiness ve
consent durumunu taşır.

Üst bardaki Campaign Hub, mevcut kampanyayı, lobi readiness özetini, planlanan preparing
session'ı ve davet yaşam döngüsünü gösterir. Aktif DM house rules, safety tools, agenda ve
oturum tarihini düzenler; her üye kendi consent/readiness ve safety tercihlerini yönetir.
Settings/member/game revision çatışmaları `409` ile fail closed olur.

### Session Workspace, Game Log ve Özet

Migration v17, aktif oturuma bağlı rol filtreli notları, loot havuzunu ve quest listesini
kalıcılaştırır. Üst bardaki **Session** ekranı aktif DM'e
`preparing → live → paused/completed` yaşam döngüsü kontrollerini; bütün üyelere cursor
tabanlı Game Log, oturum notları ve loot claim akışını sunar. Aktif DM loot ve quest
oluşturur, quest durumunu değiştirir ve oturum özetini taslak veya yayınlanmış olarak
kaydeder.

Not görünürlüğü sunucuda uygulanır: party notları tüm masaya, DM-only notlar DM ekibine,
private notlar yalnızca yazarı ile DM/co-DM rollerine görünür. Loot claim işlemi koşullu
tek update ile yarışa dayanıklıdır; command receipt, state revision ve olay kaydı aynı
transaction içinde tamamlanır. Tamamlanan oturuma yeni not, loot veya quest eklenemez;
aktif DM kapanış özetini daha sonra düzenleyip yayınlayabilir. Yayınlanmamış özet içeriği
oyuncu workspace'ine veya party event stream'ine girmez.

### Encounter Library ve Builder

Migration v18 kampanya kapsamlı, revision'lı encounter draft'larını kalıcılaştırır.
DM üst bardaki **Encounters** workspace'inde manuel monster/NPC ekleyebilir, kampanyadaki
character sheet'lerini kaynak olarak bağlayabilir, roster'ı kaydedebilir ve encounter'ı
duplicate edebilir. Draft güncellemeleri hem game aggregate revision hem draft revision
ile korunur; stale builder sekmesi `409` alır.

Kaydedilmiş encounter başlatıldığında manuel combatant kaynakları doğrulanmış HP, max HP,
AC ve initiative değerlerini taşır; character kaynakları ise o andaki authoritative
sheet name/HP/AC/initiative değerlerinden yeniden hydrate edilir. Aynı doğrulanmış roster
canlı encounter state'ine geçer ve kaynak encounter kimliği/revision'ı korunur.
Pause/resume round, turn ve combatant durumunu sıfırlamaz; paused durumda gameplay,
inventory, resource ve builder publish mutasyonları kapalıdır. Hidden combatant'lar
oyuncudan tamamen, görünür monster HP/max HP bilgisi ise player projection'ından
redacted edilir.

### Gelişmiş Canlı Encounter

Migration v19 canlı roster'a açık initiative tie-break metadata'sı ve kampanya başına
20 adımlık atomik encounter undo geçmişi ekler. Eşit initiative sırası önce DM'in verdiği
tie-break değerine, sonra isim ve stable ID'ye göre deterministik çözülür. Aktif aktörün
kimliği tie düzenlemesi veya lair/environment turn entry eklenmesi sırasında korunur.
Lair/environment entry'leri sıra alır ancak HP veya character resource'u taşımaz.

DM canlı encounter panelinden combatant HP'sini, tie sırasını, round/permanent/rest
süreli condition'ları ve concentration'ı yönetebilir. Character'a bağlı combatant HP,
max HP, AC ve ad bilgisi her command sonunda authoritative character aggregate'inden
aynı transaction içinde yeniden projekte edilir; sheet ile initiative board ayrı HP
kaynaklarına dönüşmez. End-turn condition tick'i, concentration damage save'i, state,
event, revision ve idempotency receipt birlikte commit olur.

**Son işlemi geri al** yalnız state-only encounter mutasyonlarını geri alır. Önceki state
snapshot'ı, undo stack pop'u, restore, event ve yeni revision tek `BEGIN IMMEDIATE`
transaction'ındadır; event yazımı veya state save başarısızsa undo kaydı tüketilmez.
Corrupt undo JSON'u fail closed davranır.

### Harita Fog-of-War ve Masa Araçları

Migration v24, oyun kapsamlı fog revision'ını, açılmış grid hücrelerini ve kısa ömürlü
ping/çizim sinyallerini saklar. Fog açma-kapama ve boyama yalnız aktif DM tarafından,
fog revision CAS kontrolüyle yapılır. Oyuncu snapshot'ı açılmış hücre koordinatlarını
almaz; yalnız yetkili raster mask URL'sini ve tüm geometrisi açılmış hücrelerde kalan,
süresi dolmamış masa sinyallerini görür. Kalıcı event payload'ları fog hücrelerini veya
çizim geometrisini taşımaz; kalıcı transient payload'ları her okumada fail-closed
yeniden doğrulanır.

Fog etkin olduğunda oyuncuya özgün harita dosyası verilmez. Yetkili content endpoint'i,
sunucuda açılmış hücreleri kaynak görselle birleştirip siyahlanmış PNG projection üretir.
Cache anahtarı oyun, asset, fog ve scene revision ile grid boyutunu kapsar; eşzamanlı
aynı-target üretimleri atomik tekilleştirilir. Bu türetilmiş dosyalar
`MAP_FOG_CACHE_ROOT` altında tutulur, kaynak asset değildir ve servis kapalıyken güvenle
temizlenebilir. DM görünümünde fog editörü tokenların altında;
oyuncu görünümünde fog katmanı token ve sinyallerin üstündedir, böylece gizli bölgedeki
authoritative nesneler CSS katman sırasıyla sızmaz.

Masa araç çubuğu yerel ölçüm cetveli, altı saniyelik oyuncu/DM ping'i ve yalnız DM'e açık
otuz saniyelik çizim katmanı sunar. Cetvel sonucu haritanın `grid_size_px`,
`distance_per_cell` ve `distance_unit` değerlerinden istemcide hesaplanır; oyun state'ini
değiştirmez. Ping/çizim payload'ları strict koordinat ve boyut sınırlarından geçer.

İnternet yayını için `PUBLIC_MODE=true`, yalnızca HTTPS adreslerden oluşan
`WEB_ORIGIN` ve en az 32 karakterlik rastgele `AUTH_PEPPER` zorunludur. TLS reverse
proxy zorunludur; çok-worker yayınında Redis shared runtime etkinleştirilmelidir.
Yerel/LAN kullanımında `PUBLIC_MODE=false` ile sıfır ayar davranışı korunur.
Pepper ilk credential oluşturulmadan belirlenmeli ve değiştirilmemelidir. Mevcut v5/v6
veritabanını aynı pepper ile ilk kez v7'ye bağlarken bir defaya mahsus
`AUTH_PEPPER_BIND_EXISTING=true` onayı gerekir; farklı pepper credential reissue ister.

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
### Production operations

Production TLS, export/delete, retention, JSON log/metrics/trace correlation, ClamAV
upload taraması, bounded load probe ve zorunlu release kapıları
[production operations runbook](docs/production-operations.md) içinde tanımlıdır.
Ollama ve herhangi bir yerel model opsiyoneldir; kullanıcı istediği modeli seçer ve
çekirdek multiplayer API model indirmeden çalışır.
