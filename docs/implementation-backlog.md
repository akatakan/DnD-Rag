# Tetsu Ürün Uygulama Backlog'u

Bu backlog, Tetsu'yu gelişmiş karakter kağıdı, kampanya araçları, 3B zar ve VTT
ekosistemine adım adım taşır. Task'lar bağımlılık sırasındadır. Her task kendi
migration'ı, testleri, dokümantasyonu ve kullanıcıya gösterilebilir kabul kriterleriyle
tamamlanmadan sonraki bağımlı task'a geçilmez.

Durumlar: `pending`, `in_progress`, `completed`, `blocked`.

## Faz A — Güvenilir platform temeli

### FND-01 — Versioned SQLite migration altyapısı

**Durum:** `completed`

**Çıktı:** Sıralı migration registry, `schema_migrations` tablosu, boş ve legacy DB
upgrade testleri.

**Bitti sayılması için:**

- Yeni DB son şemaya tek çalıştırmada gelir.
- Eski multiplayer DB verisi korunarak son şemaya yükselir.
- Tekrar çalıştırma idempotent olur.
- Başarısız migration version kaydı bırakmaz.
- Tüm Python testleri geçer.

### FND-02 — Campaign ve session domain'i

**Durum:** `completed`
**Bağımlılık:** FND-01

`campaigns`, `sessions`, üyelik, campaign ayarları ve mevcut `games` verisinin
geriye uyumlu geçişi. Campaign status ile live session status ayrılır.

**Çıktı:** Migration v3; campaign/session/membership tabloları; legacy backfill;
aktif-DM kontrollü session yaşam döngüsü API'si; create/join/snapshot sözleşmesi ve
eşzamanlı session oluşturma testleri.

### FND-03 — Revision, idempotency ve event cursor

**Durum:** `completed`
**Bağımlılık:** FND-02

State revision, `client_action_id`, duplicate command koruması, cursor tabanlı event
sayfalama ve reconnect catch-up sözleşmesi.

**Çıktı:** Migration v4; aggregate revision ve optimistic concurrency; kalıcı command
receipt'leri; eşzamanlı duplicate replay; rol filtreli `/api/events` cursor sayfalaması;
WebSocket `catch_up` mesajı ve frontend reconnect high-water takibi.

### FND-04 — Public auth ve davet yaşam döngüsü

**Durum:** `completed`
**Bağımlılık:** FND-02

Hashli ve süreli token/davet, rotation, revocation, logout, origin kontrolü ve audit.
Yerel/LAN modunun kolay kullanımı korunur.

**Çıktı:** Migration v5-v7; purpose-separated HMAC credential storage ve pepper
fingerprint; süreli token/davet, rotation/revocation/logout ve audit API'leri; 128-bit
tek gösterimli davetler; auth token'a bağlı tek kullanımlık WebSocket ticket; açık
socketlerde revocation/expiry doğrulaması; public HTTPS origin/pepper fail-fast ayarları.

## Faz B — Karakter ve kurallar motoru

### CHAR-01 — Ruleset catalog ve provenance

**Durum:** `completed`
**Bağımlılık:** FND-01

SRD 5.2.1 tabanlı versioned class, species, background, spell, feature, item ve condition
kataloğu. Her kayıt kaynak/lisans/provenance taşır; kapalı D&D Beyond içeriği kopyalanmaz.

**Çıktı:** Audit geçmişi ve güncelleme zamanı taşıyan Migration v8 campaign ruleset
yükseltmesi; dosya tabanlı immutable
`foundation` katalog; yedi zorunlu varlık tipi; kayıt başına resmî kaynak, sayfa/section,
CC BY 4.0 attribution ve belge SHA-256 provenance zinciri; auth ve rate-limit korumalı
sürüm/liste/arama/detail API'leri; exact source/hash/attribution ve kimlik, ad, veri,
lisans, kaynak, provenance alanlarını kapsayan full-entry extraction hash allowlist'i;
DB migration öncesi fail-fast asset doğrulaması; strict entity schema/cross-reference
doğrulaması; boyut/sorgu sınırları ve immutable arama indeksleri; React transport ve tip
sözleşmesi.

### CHAR-02 — Character aggregate ve derived-stat motoru

**Durum:** `completed`
**Bağımlılık:** CHAR-01, FND-02

Ability, modifier, proficiency, skill/save, AC, initiative, HP ve speed hesaplamaları.
Hesaplanan alanlarla kullanıcı girdileri ayrılır.

**Çıktı:** Character schema v2 ve Migration v9 backfill/audit; legacy HP, AC, temp HP,
condition, inventory ve bilinmeyen class adını koruyan idempotent dönüşüm; katalog bağlı
class/species/background referansları; ability modifier, tüm proficiency tier'ları,
save, 18 skill, expertise, AC/Dex cap, initiative, level başına minimum 1 HP, speed ve
passive perception hesaplamaları; `inputs`/`derived` ayrımı ve read-only compatibility
projection'ları; revision/idempotency transaction'ı içinde authoritative update command;
manuel AC/HP/speed write koruması; redacted `PublicCharacter`; 256 KiB pre-parse body
limiti ve eşzamanlı update/sızıntı/migration regresyon testleri.

### CHAR-03 — Resource, rest ve condition motoru

**Durum:** `completed`
**Bağımlılık:** CHAR-02

Short/long rest, hit dice, spell dışı sınıf kaynakları, death saves, concentration ve
condition süreleri.

**Çıktı:** Resource schema v2; Migration v10-v12 backfill/audit, turn action ledger ve eski round bazlı
death-save kaydını monotonik turn serial'a dönüştürme; level/class değişiminde
harcanmış kullanımları koruyan Hit Point Dice ve Second Wind uzlaştırması; typed,
level-scaled Second Wind; HP ve kaynak yenileyen short/long rest; encounter içinde rest
koruması; sıra ve tur başına Bonus Action kontrollü Second Wind; authoritative death
saves, massive damage/zero-HP failure ve ölü karaktere
normal healing koruması; Constitution concentration save'i; typed ve süreli condition
yaşam döngüsü; future/corrupt resource state için fail-closed migration.

### CHAR-04 — Inventory ve equipment

**Durum:** `completed`
**Bağımlılık:** CHAR-02

Identity-based inventory, quantity, equip, container, currency, attunement ve
encumbrance politikası.

**Çıktı:** Inventory schema v1 ve fail-closed Migration v13 backfill/audit; legacy
isim listesini koruyan identity entry kayıtları; katalog bağlı ve custom item ayrımı;
quantity, benzersiz item mutation, iç içe container cycle/capacity doğrulaması; CP/SP/EP/
GP/PP kesesi ve 50 coin/lb ağırlık hesabı; Strength×15 taşıma kapasitesi ve DM kontrollü
standard/ignore politikası; equipment slot, training ve Shield AC entegrasyonu; üç slotlu
attunement, aynı katalog kopyası ve ölüm/rest yaşam döngüsü; encounter sırasında sıra ve
Action ledger kontrollü equip; role-redacted command response; oyuncu inventory paneli.

### CHAR-05 — Spell, feature ve action sözleşmesi

**Durum:** `completed`
**Bağımlılık:** CHAR-01, CHAR-02, CHAR-03

Known/prepared spells, slot kullanımı, attacks, saves, damage/heal ve feature actions.
Her rollable action typed bir roll intent üretir.

**Çıktı:** Action schema v1 ve fail-closed Migration v14 backfill/audit; DM-authoritative
known/prepared spell, slot ve attack repertuvarı; yeniden yapılandırmada harcanmış slotu
koruma ve Long Rest'te slot yenileme; catalog-backed Cure Wounds için slot ölçekli
authoritative healing; ability/skill/save, attack, concentration, death save ve Second
Wind için typed roll intent; derived modifier, advantage/disadvantage, doğal 1/20,
critical damage, target AC ve turn Action ledger çözümlemesi; client-authored dice/damage
alanlarını reddeden strict komut sözleşmesi; idempotent event/receipt ve oyuncu Actions
paneli.

### CHAR-06 — Character draft/builder API

**Durum:** `completed`
**Bağımlılık:** CHAR-02, CHAR-04, CHAR-05

Autosave edilen draft, adım doğrulaması, ileri/geri geçiş ve atomik publish.

**Çıktı:** Migration v15 `character_drafts` tablosu; canlı aggregate'den ayrı private
draft JSON'u, monotonik draft revision ve active/published yaşam döngüsü; strict schema
ve boyut sınırları; basics, abilities, class, species, background, proficiencies,
equipment, spells ve review adımları; yalnız ileri geçişte mevcut adım doğrulaması,
serbest geri geçiş ve shared game revision'ından bağımsız autosave; katalog entity tipi,
background proficiency, expertise, item, spell/slot/attack çapraz doğrulaması; publish
sırasında fresh authoritative character üretimi ve draft status, game state, revision,
event, idempotency receipt'in tek transaction'da commit/rollback davranışı.

### CHAR-07 — Rehberli character builder UI

**Durum:** `completed`
**Bağımlılık:** CHAR-06

Temel bilgiler, ability, class, species, background, proficiency, equipment, spell ve
review adımları; loading/empty/error/conflict halleri.

**Çıktı:** Oyuncu konsolundan açılan odaklı, responsive dokuz adımlı builder workspace;
pinned catalog-backed class/species/background/item/spell kartları; ability ve skill
formları, background zorunlu proficiency uzlaştırması, equipment, spellcasting ability,
known/prepared ve slot seçimleri; review özeti ve atomik publish; 650 ms serialized
autosave kuyruğu, görünür saved/pending/saving/error/conflict durumları, stale revision'da
yerel girdiyi sessizce ezmeyen explicit reload; ileri adım doğrulama hatası, serbest geri
geçiş, loading/empty/published/retry ekranları; mobile progress scroller, keyboard focus,
ARIA status/alert ve reduced-motion uyumu; Playwright happy-path regresyonu.

### CHAR-08 — Gelişmiş character sheet UI

**Durum:** `completed`
**Bağımlılık:** CHAR-03, CHAR-04, CHAR-05

Overview, actions, spells, inventory, features ve notes sekmeleri. Sheet üzerindeki
skill/attack/save butonları global zar tepsisini doğru modifier ile açar.

**Çıktı:** Overview/Actions/Spells/Inventory/Features/Notes sekmeli responsive character
sheet; authoritative AC, HP, initiative, speed, passive perception, ability, save ve skill
sunumu; proficiency/expertise işaretleri; attacks, prepared spells, slotlar, inventory,
rest/Hit Dice, class resources, condition ve concentration çalışma alanları; skill,
ability, save ve attack butonlarından global zar dialoguna actor/action bağlamı ve
read-only derived modifier taşıyan typed command; advantage/disadvantage ile authoritative
sonuç ve mevcut zar animasyonu; private, 20K sınırlı cihaz-içi Notes autosave'i ve açık
senkronizasyon uyarısı; mobile tab scroller/grid dönüşümü, tablist/tabpanel ve keyboard
focus sözleşmesi; Playwright tab/roll happy-path regresyonu.

## Faz C — Kampanya ve oyun araçları

### CAMP-01 — Campaign dashboard, lobi ve Session Zero

**Durum:** `completed`
**Bağımlılık:** FND-02, FND-04

Kampanya seçimi, davet, readiness, planlanan oturum, house rules, güvenlik tercihleri ve
oyuncu onayı.

**Çıktı:** Migration v16 Session Zero üyelik alanları ve schema v1 campaign settings
backfill'i; current campaign seçim/list API'si; optimistic `settings_version`,
`readiness_version` ve game revision ile house rules, safety tools, agenda, kişisel
readiness/consent ve preparing session planlama; ready için accepted consent invariant'ı;
lines/veils/private note'u yalnız sahip ve DM rollerine açan role projection, party
event'lerinden hassas veri redaction'ı; corrupt settings fail-closed migration rollback;
Campaign Hub UI'da party readiness/consent özeti, house rule editor, safety tools/agenda,
kişisel Session Zero formu, planlanan oturum ve tek-gösterimli invite rotation; mobile
dashboard ve Playwright lobby happy-path sözleşmesi.

### CAMP-02 — Session lifecycle, Game Log ve özet

**Durum:** `completed`
**Bağımlılık:** FND-03, CAMP-01

Prepare/start/pause/end akışı, cursor'lı Game Log, açık/gizli notlar, loot/quest ve
yayınlanabilir oturum özeti.

**Çıktı:** Migration v17 ile session notu, loot ve quest persistence'ı; revision
korumalı lifecycle endpoint'leri; bounded ve rol filtreli workspace; private/DM/party
not görünürlüğü; yarışa dayanıklı atomik loot claim; quest durumları; taslak/yayınlanmış
özet redaksiyonu; cursor'lı Game Log ve responsive Session Workspace. Corrupt summary,
cross-game ID, stale revision, duplicate status, eşzamanlı claim ve async UI yarışları
regresyon testleriyle korunur.

### ENC-01 — Encounter library ve builder

**Durum:** `completed`
**Bağımlılık:** CHAR-02, CAMP-02

Kaydedilebilir encounter taslağı, combatant kaynakları, manual entry, duplicate,
başlatma, pause/resume ve canlı encounter ile tek domain modeli.

**Çıktı:** Migration v18; campaign-scoped ve optimistic revision'lı encounter
draft'ları; manual/character kaynaklı validated combatant schema; duplicate ve
authoritative sheet hydration; atomik saved-encounter start; round/turn state'ini
koruyan pause/resume; hidden turn index ve monster HP redaksiyonu; responsive Encounter
Library/Builder. Cross-campaign referans, corrupt migration, stale update, paralel
update/start ve async UI conflict yarışları regresyon testleriyle korunur.

### ENC-02 — Gelişmiş canlı encounter

**Durum:** `completed`
**Bağımlılık:** ENC-01, CHAR-03

Initiative ties, condition süreleri, concentration, lair/environment entries, undo ve
karakter kaynaklarıyla atomik senkronizasyon.

**Çıktı:** Migration v19; deterministik explicit tie-break sırası; current actor'ı
koruyan lair/environment turn entry'leri; character/combatant HP-AC senkronizasyonu;
condition duration ve concentration live araçları; 20 kayıtla bounded, atomik ve
fail-closed undo history. Dead-heal, paused mutation, environment turn resource,
hidden event sızıntısı, undo rollback/corruption ve UI request-storm regresyonları
bağımsız review ile kapatıldı.

## Faz D — Zar deneyimi

### DICE-01 — Typed roll intent ve karakter entegrasyonu

**Durum:** `completed`
**Bağımlılık:** CHAR-05, FND-03

Mevcut global zar FAB'ını raw expression yerine actor/action/visibility/context taşıyan
typed intent ile besler. Sonuç Game Log'a idempotent düşer.

Tamamlanan kapsam: migration v20 typed event metadata backfill/index/trigger doğrulaması;
strict `roll_intent`; server-derived expression/RNG/context; actor spoof ve role-aware
context redaction; party/private FAB seçimi; response-lost retry'da payload-bound
`client_action_id`; tek Game Log event'i için transaction/race regresyonları. Bağımsız
review PASS: 200 Python test + 29 subtest ve frontend production build.

### DICE-02 — 3B zar renderer ve fizik sunumu

**Durum:** `completed`
**Bağımlılık:** DICE-01

Gerçek d4–d100 geometrileri, tray, çarpışma/sekme, ses ve tema. RNG sunucuda kalır;
animasyon authoritative sonuca görsel olarak ulaşır. Reduced-motion korunur.

Tamamlanan kapsam: lazy Three.js/cannon-es renderer; d4–d100 mesh/tray/physics;
authoritative kept/discarded settle değerleri; bounded 12-die scene; collision audio ve
üç tema; migration v21 üye tercihleri; reduced-motion'da vendor yüklemeyen static
fallback; WebGL/context-loss/lazy import error boundary; tam RAF/body/material/context
cleanup. Bağımsız review PASS: 207 Python test + 29 subtest ve frontend production
build. Browser runtime bulunmadığı için gerçek WebGL/ses/reduced-motion Playwright
akışı çalıştırılmadı ve manuel gate olarak açıkça kaldı.

## Faz E — Maps ve VTT

### VTT-01 — Map asset, scene ve grid

**Durum:** `completed`
**Bağımlılık:** FND-03, CAMP-02

Dosya doğrulamalı harita yükleme, object storage sınırı, scene, grid tipi, ölçek ve
viewport.

Tamamlanan kapsam: migration v22 campaign-scoped map asset/scene metadata; yapısal
PNG/JPEG doğrulaması; 10 MiB dosya, 100 MiB campaign kota ve content-addressed yerel
object store; aktif-DM upload/scene CAS; unpublished player projection ve content
redaction; square/hex grid, ölçek ve viewport içeren DM workspace ile yayınlanmış
oyuncu haritası. Upload body/member rate limitleri, quota yarış serileştirmesi,
structured scene `409`, object/metadata rollback sırası ve React stale request/Blob
cleanup review sırasında sertleştirildi. Bağımsız review PASS: 220 Python test +
29 subtest, 13 focused VTT testi ve 1796 modüllü frontend production build.
Browser/Playwright bulunmadığı için gerçek upload/yayın/responsive akışı manuel gate
olarak kaldı.

### VTT-02 — Token ve encounter entegrasyonu

**Durum:** `completed`
**Bağımlılık:** VTT-01, ENC-02

Token placement/movement, sahiplik/izin, character/combatant bağlantısı ve aynı
encounter state'inin liste/harita sunumları.

Tamamlanan kapsam: migration v23 campaign/game scoped spatial token metadata; canlı
combatant listesinden DM sync placement; aktif DM'nin tüm, oyuncunun yalnız kendi
character tokenını sürükleme ve klavye ile taşıma yetkisi; hidden/HP/player
projection redaksiyonu; global command idempotency ile token-level CAS; map/grid
değişiminde boyut ve bounds reconcile; aktif turn vurgusu. Harita ad/init/HP/gizlilik
için ikinci encounter aggregate tutmaz. Bağımsız review PASS: 231 Python test +
29 subtest, 70 focused VTT/API testi ve 1796 modüllü frontend production build.
Review snapshot/token revision atomikliği, remove CAS, unpublished event sızıntısı,
v23 fail-closed duplicate doğrulaması, 200-combatant DoS sınırı ve pointer/stale
request yaşam döngüsünü sertleştirdi. Browser olmadığı için drag/keyboard/mobile
Playwright akışı manuel gate olarak kaldı.

### VTT-03 — Fog-of-war, ruler, ping ve draw

**Durum:** `completed`
**Bağımlılık:** VTT-02

Server-authoritative görünürlük, fog region/mask, ölçüm, ping ve geçici çizim
katmanları. Oyuncuya gizli geometri/veri gönderilmez.

Migration v24 oyun kapsamlı fog state/cell verisini ve TTL-bound ping/çizim
sinyallerini ekledi. Fog açıkken oyuncu ham asset'i veya revealed-cell geometrisini
almaz; game+asset+fog+scene+grid kapsamlı, atomik üretilen siyahlanmış projection ve
raster mask alır. Gizli hücredeki geçici sinyaller server-side filtrelenir; kalıcı
event payload'ları fog/draw koordinatı taşımaz. DM fog editörü, ölçüm cetveli, ping ve
geçici çizim UI'ı klavye/pointer yaşam döngüsü ve reduced-motion uyumuyla eklendi.
Bağımsız review PASS: 245 Python test + 29 subtest, 39 focused test ve 1796 modüllü
frontend production build. Review cross-game cache çakışmasını, raw asset bypass'ını,
hidden transient sızıntısını, paint CAS/rate-limit eksiklerini ve render stampede
yarışını kapattı. Browser bulunmadığı için fog katman sırası ve gesture akışları manuel
Playwright gate olarak kaldı.

## Faz F — Ölçek ve yayın

### SCALE-01 — Shared realtime ve rate limit

**Durum:** `completed`
**Bağımlılık:** FND-03

Redis tabanlı presence, pub/sub, grace timer ve rate limit; multi-worker doğruluğu.

`REDIS_URL` etkin olduğunda connection TTL/presence, event-snapshot invalidation,
remote disconnect, recoverable DM grace scheduler ve sliding-window kotalar worker'lar
arasında paylaşılır. Redis TIME tabanlı atomik Lua, subscriber ACK/readiness, reconnect
sonrası authoritative snapshot resync, startup grace recovery, idempotent DB handover
kararı ve graceful presence cleanup eklendi. Redis yoksa tek-worker local/LAN fallback
korunur; `WEB_CONCURRENCY>1` Redis olmadan fail-fast eder. Bağımsız review PASS:
257 Python test + 29 subtest, 62 focused + 12 son lifecycle testi ve 1796 modüllü
frontend build. Docker daemon kapalı olduğundan gerçek Redis Lua/pubsub/failover testi
hazır ama manuel/CI gate olarak skip kaldı.

### SCALE-02 — PostgreSQL ve outbox değerlendirmesi

**Durum:** `completed`
**Bağımlılık:** Campaign/character/VTT gerçek yük ölçümleri

SQLite sınırları ölçülür; gerekiyorsa normalized aggregate tabloları, PostgreSQL,
transactional outbox, backup/restore ve veri taşıma aracı eklenir.

SQLite WAL + `synchronous=FULL` + busy timeout profiline alındı; migration scriptleri
implicit commit olmadan atomik çalışır ve multi-process startup yarışı testlidir.
Integrity/schema/FK doğrulamalı, source/target TOCTOU'ya dayanıklı online backup ve
overwrite etmeyen new-path restore aracı eklendi. Bounded scale probe read/write,
başarı/hata ve latency dağılımını ayrı ölçer. 1/2/4/8×250 son ölçümde hata yok, ancak
8-thread write p99 269.505 ms ile 250 ms PostgreSQL guardrail'ını aştı; bu profil
SQLite production onayı alamaz. Tek-host kontrollü beta SQLite'ta kalır; multi-host,
strict-delivery veya eşik aşımında PostgreSQL + transactional outbox zorunludur.
Bağımsız review PASS: 271 Python test + 29 subtest, 23 focused test ve 1796 modüllü
frontend build. Gerçek hedef disk/antivirüs yükü, POSIX fsync ve PostgreSQL cutover
harici release gate olarak kaldı.

### SCALE-03 — Release, güvenlik ve gözlemlenebilirlik

**Durum:** `completed`
**Bağımlılık:** FND-04, SCALE-01

TLS, retention/export/delete, structured logs, metrics, tracing, dependency/upload
scanning, load testleri ve release gates.

Tamamlanan kapsam: public HTTPS/WSS fail-closed siniri ve trusted-proxy runbook'u;
owner-only, credential-redacted campaign export ile exact-confirmation atomik delete;
dry-run-first retention; hassas veri yazmayan JSON loglari; bounded route-template
Prometheus metrikleri; W3C trace/request correlation; yapisal upload dogrulamasi ve
fail-closed ClamAV INSTREAM; localhost-first bounded load probe; immutable Action
referansli CI, expiring vulnerability exception ve kilitli Python/npm audit gate'leri.
GitPython/setuptools audit bulgulari fixli surumlere yukseltildi; fixsiz NLTK downloader
advisory'si kullanilmayan kod yolu icin 2026-08-30'da otomatik sona eren tek-ID istisna
ile kaydedildi. Bagimsiz review PASS: 285 Python test + 29 subtest, 1 gercek Redis
environment skip, 37 focused test ve 1796 modullu frontend production build. npm audit
0 vulnerability; Python audit belgeli tek gecici istisna disinda temiz. Canli Redis
failover, gercek ClamAV, TLS edge, browser ve production load profili harici release
gate olarak kaldi.

## Çalışma kuralı

Her turda yalnız bir ana task `in_progress` olur. Task tamamlandığında:

1. Kabul kriterleri testlerle doğrulanır.
2. Bu dosyadaki durum `completed` yapılır.
3. Kalan riskler ve manuel test kapıları raporlanır.
4. Bağımlılığı açılan bir sonraki task `in_progress` yapılır.
