# Bilgi Grafiği ve Altyapı Analizi

**Tarih:** 2026-09-08 · **Soru:** Obsidian vault grafiği gibi bir yapı kurmak
mantıklı mı, ve bunun için dil ya da veritabanı değişmeli mi?

**Kısa cevap:** Grafik evet, kurulmalı — ve araştırmanın işaret ettiği en büyük
boşluğu tam olarak o kapatıyor. Altyapı hayır, değişmemeli; ölçtüm, mevcut yığın
ihtiyacın iki mertebe üstünde. Asıl darboğaz veritabanı değil, **kimsenin bu
kenarları yazmıyor olması**.

---

## 1. Neden grafik

2026-09-08 kullanıcı araştırması (bkz. CLAUDE.md) üç şeyi birden söylüyor:

- DM'ler oynatılacak içerik yerine düzyazı yazıyor ve saatlerini oraya gömüyor.
- "Bütün o dünya detaylarını yazmadan aklımda tutamıyorum" — hatırlama sorunu.
- Prep, Notion gibi üçüncü bir pencerede yaşıyor.

Bunların ortak kökü not tutma değil, **bağ kurma**. DM'in kaybettiği şey
"Alvera kimdi" değil, "Alvera'nın ölen kardeşiyle ilgili yemin hangi oturumda
edilmişti ve hangi eşyaya bağlıydı". Düz metin bunu geri veremez; grafik verir.

Obsidian benzetmesi doğru ama bir farkla: Obsidian'da kenarları **insan** yazar.
Tetsu'da kenarların çoğunu **sistem zaten üretiyor** — sadece kaydetmiyor.

---

## 2. Ham madde çoktan orada

`events` tablosu bugün 440 satır ve şu şekle sahip:

```
events(id, game_id, type, actor_id, visibility, payload_json,
       created_at, typed_intent_id, intent_schema_version)
```

Yani her olay zaten **tipli**, **failli**, **görünürlüklü** ve **zamanlı**.
Grafiğin kenarları bunlardan türetilir; sıfırdan veri girişi gerekmez:

| Olay tipi | Ürettiği kenar |
|---|---|
| `combatant_added` (npc kaynaklı) | `oturum --içerdi--> npc` |
| `character_damaged` | `npc --zarar_verdi--> karakter` (encounter bağlamıyla) |
| `session_loot_claimed` | `karakter --sahiplendi--> eşya`, `eşya --bulundu--> oturum` |
| `session_note_added` | `not --ait--> oturum` + metindeki `[[bağlar]]` |
| `map_scene_published` | `oturum --geçti--> mekân` |
| `character_draft_published` | `karakter --katıldı--> kampanya` |

Buna bir de DM'in elle kurduğu bağlar eklenir: notlarda `[[Alvera Elk-Ears]]`
yazınca kenar oluşur. Obsidian'ın tek mekanizması bu; bizde ikinci mekanizma.

---

## 3. Veri modeli

İki tablo yeter. Repo'nun mevcut deseni ile tutarlı: **kenarlar türetilmiş
veridir, kalıcı yazılır, kaynağı değişince yeniden üretilir** (migration 032'nin
dersi: türetilmiş veriyi saklıyorsan geri doldurma yolunu da yaz).

```sql
CREATE TABLE graph_nodes (
    id           TEXT PRIMARY KEY,         -- "npc:<uuid>", "session:<id>"
    campaign_id  TEXT NOT NULL,
    kind         TEXT NOT NULL,            -- npc | character | session | quest
                                           -- | item | location | faction | note
    title        TEXT NOT NULL,
    visibility   TEXT NOT NULL,            -- party | dm | player:<member_id>
    source_json  TEXT NOT NULL,            -- neyden türedi
    updated_at   TEXT NOT NULL
);

CREATE TABLE graph_edges (
    campaign_id  TEXT NOT NULL,
    src          TEXT NOT NULL,
    dst          TEXT NOT NULL,
    relation     TEXT NOT NULL,
    visibility   TEXT NOT NULL,
    event_id     INTEGER,                  -- kanıt: hangi olaydan doğdu
    PRIMARY KEY (campaign_id, src, dst, relation)
) WITHOUT ROWID;

CREATE INDEX graph_edges_dst ON graph_edges(campaign_id, dst, src);
CREATE VIRTUAL TABLE graph_fts USING fts5(
    title, body, content='graph_nodes', content_rowid='rowid');
```

`event_id` sütunu önemli: her kenarın **kanıtı** var. "Bu bağ nereden geldi?"
sorusunun cevabı bir olay kaydı. Uydurulmuş bağ olamaz.

### Görünürlük mimarinin mevcut kuralına uyar

CLAUDE.md'deki değişmez kural: *gizli veri istemciye hiç gitmez; filtrelemeyi
projeksiyonda yap, sunumda değil.* Grafik bunu **ihlal etmeye en açık** yüzey —
DM'in gizli NPC'si bir kenarın ucunda sızabilir. Bu yüzden:

- Kenarın görünürlüğü, iki ucunun görünürlüğünün **kesişimidir**.
- Oyuncu projeksiyonu, gizli düğümleri istemciye **hiç göndermez**; grafik
  sorgusu sunucuda role göre filtrelenir.
- Fog of war'da olduğu gibi: istemcide "gizle" yok, sunucuda "gönderme" var.

---

## 4. Ölçüm: SQLite bunu taşır mı?

Sentetik kampanya grafiği kurup gerçek sorguları ölçtüm
(`tools/` dışında, tek seferlik betik; SQLite 3.50.4, FTS5 derli).

| Ölçek | Komşuluk | 2 hop | 3 hop | FTS + derece | Boyut |
|---|---|---|---|---|---|
| 5.000 düğüm / 20.000 kenar | 0,02 ms | 0,14 ms | 1,06 ms | 0,10 ms | <1 MB |
| 50.000 düğüm / 200.000 kenar | 0,02 ms | 0,18 ms | 4,37 ms | 0,12 ms | 31 MB |

Ölçek bağlamı: 5.000 düğüm ≈ 50 oturumluk bir kampanyada oturum başına 100
varlık. 50.000 düğüm, tek kurulumda yüzlerce kampanya demek.

Karşılaştırma için API'nin bugünkü sıcak yolu, süreç içi ölçümle:

```
GET /api/snapshot        p50 11,97 ms   p95 13,08 ms
POST /api/games          p50  4,25 ms
```

**Grafik sorgusu, zaten var olan snapshot'ın yanında gürültü kalıyor.** 3 hop
genişleme en kötü durumda snapshot'ın üçte biri.

---

## 5. Veritabanı alternatifleri ve neden hayır

| Seçenek | Kazandırdığı | Maliyeti | Karar |
|---|---|---|---|
| **SQLite (mevcut)** | Sıfır yeni bağımlılık, WAL, FTS5, özyinelemeli CTE, tek dosya, LAN'da çalışır | Tek yazar | **Evet** |
| Neo4j | Cypher, gerçek grafik motoru | JVM süreci, ayrı yedekleme, LAN kurulumu çöker, ölçümde hiçbir kazanç yok | Hayır |
| Postgres + Apache AGE | Cypher + ilişkisel | Sunucu süreci, eklenti derleme, işletim yükü | Şimdilik hayır |
| RDF / triple store | Ontoloji, SPARQL | Ekibin bilmediği model, D&D için aşırı | Hayır |
| Gömülü grafik (KùzuDB vb.) | Gömülü + Cypher | İkinci bir veri deposu, iki yerde tutarlılık | Hayır |

Grafik veritabanları **derin traversal** için vardır (arkadaşın arkadaşının
arkadaşı, 6+ hop). Bir kampanya grafiğinde kimse 6 hop gezmez; kullanıcı bir
NPC'ye tıklayıp komşularını ve 2 hop ötesini okur. Bu, ilişkisel bir JOIN'in
tam olarak iyi olduğu iştir.

**SQLite'ı terk etme koşulu** (şimdi değil, tetiklendiğinde):
`docs/sqlite-postgresql-evaluation.md` zaten bu değerlendirmeyi taşıyor. Grafik
bu kararı değiştirmiyor. Tetikleyici hâlâ aynı: çok kiracılı bulut kurulumunda
eşzamanlı yazar sayısı tek yazarı sıkıştırdığında Postgres'e geçilir — ve o
gün grafik şeması **olduğu gibi** taşınır, çünkü sadece iki tablo ve bir FTS
indeksi.

---

## 5b. SaaS senaryosu: duvar nerede? (2026-09-08 ölçümü)

Yukarıdaki karar tek kurulum/LAN varsayımıyla verilmişti. Hedef SaaS ise soru
değişir: **çok kiracılı bir kurulumda ilk ne kırılır?**

Tetsu'nun gerçek yazma şekli ölçüldü — `BEGIN IMMEDIATE`, `state_json`
güncellemesi ve olay eklemesi, tek WAL dosyasında, N eşzamanlı masayla:

| Eşzamanlı masa | Yazma/sn | p50 | p95 | Kilit hatası |
|---|---:|---:|---:|---:|
| 1 | 247 | 3,51 ms | 4,84 ms | 0 |
| 10 | 215 | 3,40 ms | 5,83 ms | 0 |
| 50 | 208 | 3,45 ms | 6,49 ms | 0 |
| 200 | 228 | 3,52 ms | 1.283 ms | 218 |
| 500 | 264 | 3,68 ms | 10.878 ms | 1.608 |

Üç şey okunuyor:

1. **Aktarım hızı eşzamanlılıktan bağımsız olarak ~220 yazma/sn'de sabit.**
   Bu tek yazar tavanı; ölçeklenmiyor.
2. **Ortanca istek her ölçekte iyi** (3,5 ms). Bozulan kuyruk.
3. **200 masada kilit hataları başlıyor, 500'de p95 11 saniyeye çıkıyor.**

Pratik sınır: **elli civarı eşzamanlı aktif masa.** İki yüzde bozuluyor.

**Bu bir SQLite sınırı, Python sınırı değil.** Yazmaları seri hale getiren şey
GIL değil, veritabanı dosyasının kendisi. Aynı kod Go'da yazılsaydı aynı
duvara aynı yerde çarpardı — çünkü Go da tek bir SQLite dosyasıyla konuşurdu.

**Sonuç:** SaaS hedefi Postgres'i tetikleyici beklenen bir iş olmaktan
çıkarıp **ilk iş** yapar. Go'yu ise hâlâ haklı çıkarmaz; bağlayıcı kısıt
veritabanı katmanında ve Go orayı değiştirmiyor.

## 6. Dil değişmeli mi?

Hayır, ve gerekçesi ölçüm:

- Snapshot p50 12 ms. Bir masada 6 kişi var; saniyede birkaç istek.
- Grafik sorguları milisaniye altı.
- Gerçek gecikme kaynağı ağ ve LLM çağrıları; ikisi de dilden bağımsız.

Go veya Rust'a geçmek 3.028 satır kural motorunu, 32 migration'ı, 373 testi ve
altı yıllık D&D kural kararlarını sıfırlar. Kazanç: kimsenin hissetmeyeceği
milisaniyeler. Bu takas savunulamaz.

**Python'un gerçek maliyeti başka yerde** ve üçü de zaten dosyada:

1. Bağımlılık ağırlığı — VTT, Streamlit ve altı `llama-index-*` paketini
   taşıyor. Bu bir dil sorunu değil, paketleme sorunu; opsiyonel bağımlılık
   grubu çözer.
2. Event loop bloklaması — 60 çağrı bulunup düzeltildi, `tests/test_event_loop_blocking.py`
   nöbet tutuyor. Bu sınıfın tekrar etmesi mümkün ama tespit edilmiş.
3. Tek yazar SQLite + tek süreç — yatay ölçekleme günü geldiğinde Postgres.

Yani "dil yetersiz" tanısı yanlış; doğru tanı "paketleme dağınık".

---

## 7. Kullanıcının göreceği yüzey

Grafiğin kendisi ürün değil; **ürün, doğru anda doğru bağı göstermek**.
Araştırmada DM'lerin istediği buydu.

1. **Backlink paneli (asıl değer).** Bir NPC, mekân veya oturum açıkken
   "burası nerelerde geçti" listesi. Obsidian'ın en çok kullanılan özelliği
   grafik görünümü değil, backlink panelidir. Önce bu.
2. **Oturum brifingi.** Oturum başlarken: geçen sefer dokunulan düğümler,
   açık quest'ler, bu bölgedeki NPC'ler. "Geçen sefer ne olmuştu" sorusunun
   otomatik cevabı.
3. **Grafik görünümü.** Kampanya çapında pan/zoom, türe göre renk, tıklayınca
   düğüme git. Etkileyici ama üçüncü sırada — çünkü haftalık faydayı 1 ve 2
   veriyor.
4. **`[[bağ]]` yazımı.** Not alanlarında otomatik tamamlama; DM yazarken bağ
   kuruyor, ayrıca iş yapmıyor.

**AI konumlandırması:** Araştırma, topluluğun en büyük mecralarında açık
anti-AI normlar olduğunu gösterdi. Bu yüzey **"notların, aranabilir"** diye
anlatılır. RAG grafiğin üstünde arama olarak durur; "AI senin yerine kurgu
üretsin" olarak değil.

---

## 8. Uygulama sırası

Her adım kendi migration'ı ve testleriyle, öncekine bağımlı.

1. **`graph_nodes` + `graph_edges` + FTS** (migration 033). Boş tablolar,
   görünürlük kuralı ve projeksiyon testleri.
2. **Olaylardan türetici.** Mevcut `events` tablosunu okuyup kenar üreten
   saf fonksiyon + geri doldurma migration'ı. Türetilmiş veri saklandığı için
   geri doldurma zorunlu (032'nin dersi).
3. **Backlink API'si ve paneli.** `GET /api/graph/nodes/{id}` — komşuluk,
   role göre filtreli.
4. **Notlarda `[[bağ]]`.** Yazım, otomatik tamamlama, kırık bağ hoşgörüsü
   (Obsidian gibi: olmayan düğüme bağ hata değil, niyet).
5. **Oturum brifingi.** Grafik + son oturum olayları.
6. **Grafik görünümü.** Kampanya çapı, tür renkleri.

---

## 9. Bu, yol haritasının neresine giriyor

Mevcut sıra (CLAUDE.md) değişmiyor ama araya giriyor:

1. Katalog provenance kararı → içeriği doldur
2. **Oyuncu karakterini encounter sırasına bağla** (küçük iş, oynanabilirliği
   en çok değiştiren düzeltme)
3. Karakterleri kendi tablosuna taşı
4. Seviye ve XP
5. **Bilgi grafiği: 1–3. adımlar (tablolar, türetici, backlink paneli)** ←
   yeni. Rakibin hiç dokunmadığı yer ve araştırmanın en büyük boşluğu.
6. Premade karakter ve kampanya
7. Defenses ve Inspiration
8. **Bilgi grafiği: 4–6. adımlar** (bağ yazımı, brifing, grafik görünümü)

Grafiğin ilk üç adımı 3. maddeden (karakter tablosu) bağımsızdır; `events`
zaten kendi tablosunda. Yani 1 ve 2 beklerken paralel ilerleyebilir.
