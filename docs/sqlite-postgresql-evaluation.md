# SQLite, PostgreSQL ve Outbox Değerlendirmesi

Tarih: 2026-07-30
Karar: Tek-host local/LAN ve kontrollü beta için SQLite devam; multi-host veya
write-heavy production için PostgreSQL geçişi zorunlu kapı.

## Ölçülen profil

`python -m api.sqlite_scale_probe --concurrency 1,2,4,8 --operations 250
--write-ratio 0.2` komutu temiz, geçici bir Tetsu veritabanında çalıştırıldı. Her worker
aynı game aggregate'inde yüzde 20 kısa `BEGIN IMMEDIATE` fiziksel metadata write ve
yüzde 80 game read yaptı. Her thread ayrı SQLite connection kullandı; bu bir
multi-process throughput ölçümü değildir. SQLite `WAL`, `foreign_keys=ON`, 10 saniye
busy timeout ve `synchronous=FULL` kullandı. Ayrı regresyon testi üç process'in aynı
boş DB üzerinde eşzamanlı WAL kurulumu ve migration startup'ını doğrular.

| Eşzamanlı thread | İşlem | Hata | Başarılı ops/s | Genel p95 | Genel p99 | Write p95 | Write p99 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 250 | 0 | 110.50 | 21.075 | 24.300 | 24.300 | 30.157 |
| 2 | 500 | 0 | 76.87 | 49.403 | 81.548 | 62.602 | 88.340 |
| 4 | 1,000 | 0 | 128.75 | 58.269 | 82.942 | 62.803 | 81.777 |
| 8 | 2,000 | 0 | 170.30 | 88.751 | 208.401 | 106.779 | 269.505 |

Bu bir kapasite iddiası değildir. Windows üzerindeki tek koşu ve sıcak OS cache
kullanır; gerçek command JSON validation, event/outbox boyutu, disk
fsync karakteri, antivirüs, yedekleme ve birden fazla kampanya dağılımını modellemez.
Sekiz-thread throughput artışı bu nedenle lineer ölçek kanıtı sayılmaz. Güvenilir
bulgu şudur: lock hatası oluşmadı ancak sekiz eşzamanlı thread'de write p99 250 ms
guardrail'ını aştı ve genel maksimum 356 ms oldu; tek-writer kuyruğunun tail-latency
riski ölçülebilir. Bu yük profili SQLite production onayı alamaz; kontrollü beta kararı
daha düşük ölçülmüş concurrency içindir.

## Uygulanan SQLite sertleştirmesi

- `GameStore` her veritabanını kalıcı WAL moduna alır.
- Her connection `foreign_keys=ON`, `busy_timeout=10000` ve
  `synchronous=FULL` kullanır.
- Multi-table snapshot'lar read transaction, state/event/idempotency mutasyonları
  `BEGIN IMMEDIATE` altında kalır.
- `python -m api.db_admin backup` tek WAL read snapshot'ında verification + SQLite
  online backup yapar; geçici dosyaya yazılan kopyayı fsync + integrity-check sonrası
  no-overwrite hard-link ile atomik yayımlar. POSIX'te parent directory de fsync edilir;
  Python'ın portable directory flush sağlamadığı Windows'ta dosya flush edilir.
- `restore` var olan hedefi asla overwrite etmez; yeni bir DB yolu üretir. Operatör API
  kapalıyken `GAME_DB` yolunu doğrulanmış restore dosyasına geçirir.
- `verify` integrity ve kesintisiz/ismi eşleşen migration metadata'sını okur; gelecek
  schema'yı reddeder, eski ama bilinen schema backup'ının restore sonrası uygulama
  migration'larıyla yükseltilmesine izin verir.

Örnek:

```bash
python -m api.db_admin backup \
  --source runtime/multiplayer.db \
  --target backups/multiplayer-2026-07-30.db
python -m api.db_admin verify \
  --source backups/multiplayer-2026-07-30.db
python -m api.db_admin restore \
  --source backups/multiplayer-2026-07-30.db \
  --target runtime/restored-multiplayer.db
```

## PostgreSQL geçiş kapıları

Aşağıdakilerden biri gerçekleştiğinde SQLite deployment onaylanmamalı:

1. API ve object storage birden fazla hosta dağıtılacak.
2. Gerçek hedef yükte write p95 100 ms veya p99 250 ms üzerine çıkacak.
3. `database is locked` oranı yüzde 0.1'i aşacak.
4. Aynı game aggregate'ine sürdürülebilir 20 write/s veya toplam 100 write/s
   beklenecek.
5. DB iki GiB'ı aşacak, backup/restore penceresi operasyon hedefini karşılamayacak.
6. Online schema migration, read replica veya point-in-time recovery gerekecek.

Geçişte `GameStore` kontratı korunmalı; SQLite SQL'ini yerinde PostgreSQL'e benzetmek
yerine ayrı adapter ve contract test suite kullanılmalı. `BEGIN IMMEDIATE` yerine game
aggregate satırında `SELECT ... FOR UPDATE` ve mevcut revision CAS birlikte çalışmalı.
Global event ID sequence'e, JSON kolonları `jsonb`ye, tarih alanları `timestamptz`ye
dönüşmeli. Campaign/game/member foreign-key ve scope trigger'larının PostgreSQL
eşdeğerleri migration testleriyle doğrulanmalı.

Taşıma akışı: kaynak write freeze → online SQLite backup → checksum + integrity check →
versioned export manifest → boş PostgreSQL schema migration → transaction içinde import
→ tablo sayıları ve aggregate hash karşılaştırması → credential/auth smoke test →
read-only doğrulama → kontrollü cutover. Başarısızlıkta eski SQLite dosyası değiştirilmez.

## Normalized aggregate kararı

Character resource, inventory, draft, session workspace, encounter library, map asset,
token ve fog verileri zaten ayrı revision'lı tablolara taşındı. Canlı encounter/game
state'in kalan JSON aggregate'i tek command authority ve atomik undo için bilinçli
olarak korunuyor. Ölçüm olmadan bunu onlarca tabloya bölmek daha fazla join, migration
ve çift-source-of-truth riski yaratır. Yalnız query/lock profili belirli JSON alanını
hotspot olarak gösterirse o alan versioned migration ile ayrılmalı.

## Transactional outbox kararı

Kalıcı game event tablosu audit/catch-up kaynağıdır; Redis event ve snapshot mesajları
source of truth değil invalidation'dır. WebSocket reconnect event cursor ile catch-up
yapar, Redis subscriber reconnect'i authoritative snapshot resync eder. Bu nedenle
mevcut beta için ayrı outbox tablosu eklenmedi.

Kalan pencere: DB commit'inden sonra Redis publish'ten önce process ölürse başka
worker'daki hâlihazırda bağlı istemci bir sonraki snapshot/reconnect'e kadar stale
kalabilir. “Commit edilen her değişiklik bağlı istemcilere kesintisiz ve garantili
ulaşmalı” üretim SLO'su seçilirse PostgreSQL migration ile aynı fazda transactional
outbox zorunludur. Outbox en az bir kez teslim edilir; message key `event_id`, consumer
idempotent, claim `FOR UPDATE SKIP LOCKED`, retry/backoff ve dead-letter metriği içerir.

## Sonuç

SQLite artık daha güvenli tek-host çalışma, ölçüm ve kurtarma araçlarına sahip.
PostgreSQL bugün varsayılan yapılmadı; çünkü mevcut ürün yükü bu geçişin operasyonel
maliyetini doğrulamıyor. Buna karşılık multi-host ve strict-delivery üretim hedefleri
için geçiş/defer koşulları açık ve release gate olarak ölçülebilir.
