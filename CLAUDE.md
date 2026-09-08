# Tetsu — Claude Context

Bu dosya bu depoda çalışan ajan içindir. Ürün hedefini, ölçütü, bugünkü gerçek
durumu, mimari kararları, değişmez kısıtları ve teknik borcu tek yerde tutar.
Buradaki her sayı 2026-09-08'de koddan veya çalışan sistemden ölçülmüştür;
tahmin yoktur. Sayılar eskir — güncellerken yeniden ölç, kopyalama.

## Ne inşa ediyoruz

Bir masanın kampanya açıp **başka hiçbir araca ihtiyaç duymadan** D&D
oynayabildiği bir yer. Ölçüt şu: DM "bu akşam oynuyoruz" dediğinde kimsenin
ikinci bir sekme, ikinci bir uygulama veya bir PDF açması gerekmemeli.

Bu "D&D Beyond'un klonu" demek değil. D&D Beyond bir **içerik mağazası** olup
etrafına araç dizmiştir; biz içerik satmıyoruz, o yüzden onların ürün
kararlarının yarısı bizim için geçersiz. Bizim avantajımız birleşik olmak:
onlarda karakter kağıdı, kampanya, VTT ve encounter aracı hâlâ ayrı ayrı
yerlerde yaşıyor.

## Ölçüt: D&D Beyond (2026-09-08, DM ve oyuncu olarak elle test edildi)

Araç seti: My Characters · My Campaigns · Maps VTT (beta) · Encounters ·
Homebrew · My Dice · mobil uygulama · Avrae Discord botu · Event Finder.

Kampanya sayfasında gördüklerimiz:
- **Link ile davet** (`/campaigns/join/<id>`) — okunacak kod yok
- Karakter kadrosu; her karakterde VIEW / EDIT / UNASSIGN / LEAVE
- **Game Log** — kampanya boyunca zar geçmişi
- **Premade campaign** ve **premade character** ile başlatma
- Abonelik başına "campaign sharing slot": DM'in satın aldığı kitaplar
  masadaki oyunculara açılıyor

Karakter kağıdında gördüklerimiz:
- XP çubuğu ve seviye aralığı (456/900, Lvl 2 → Lvl 3), multiclass (`Rogue 2`)
- Short Rest / Long Rest kağıdın üstünde, tek tık
- Heroic Inspiration, Temp HP, hızlı Heal/Damage kutuları
- Defenses (resistance/immunity/vulnerability) ve aktif Conditions
- Passive Perception / Investigation / Insight, yapılandırılabilir Senses
- Sekmeler: Actions · Inventory · Features & Traits · Background · Notes · Extras
- Actions filtreleri: Attack / Action / Bonus Action / Reaction / Other,
  saldırı tablosunda menzil, isabet, hasar; "Attacks per Action"
- Sınıfa göre uyarlanan kağıt (Rogue'da Spells sekmesi yok)

**Kendi hesabında bir test kampanyası kurup DM olarak yaşananlar:**
- Kampanya oluşturma formu yalnızca **ad + açıklama**. DM modu, kural kaynağı
  sabitleme, encumbrance politikası, subclass izni gibi hiçbir ayar yok --
  Tetsu'nun kampanya ayarları bu konuda belirgin biçimde daha zengin.
- **Davet linki sayfada her zaman görünür**, yanında COPY LINK ve ayrı bir
  RESET INVITE LINK var. Tetsu'da kodu görmenin tek yolu onu yenilemek.
- **DM Notes (Private)** ve **DM Notes (Public)** diye iki ayrı not yüzeyi.
  Tetsu'da yalnızca oyuncuya açık `world_notes` var; DM'e özel not yok.
- Encounter Builder filtrelenebilir tam canavar kütüphanesi + sağda **XP
  zorluk hesabı** (Easy/Medium/Hard/Deadly eşikleri, Adjusted XP ×1.5, Daily
  Budget) sunuyor ve **"Manage Characters" ile kampanyadaki oyuncu
  karakterlerini encounter'a dahil ediyor** -- yani PC encounter'ın birinci
  sınıf üyesi. Tetsu'nun en büyük oyun hatası tam burada.
- Kendi araçları da bölünmüş: Encounters (beta) zorluğu **2014** kurallarıyla
  hesaplıyor, 2024 için "Maps'te oluştur" diyor.

**Oyuncu olarak yaşananlar:**
- **Premade karakter tek tıkla oynanabilir geliyor.** Goliath Barbarian
  seviye 1: HP 14, AC 13, hız 35 ft ve saldırıları hazır -- Maul 2d6+2
  (Heavy/Two-Handed/Topple), Spear 1d6+2 / versatile 1d8+2 (Thrown 20/60),
  Unarmed Strike. Tetsu'da aynı seviyedeki Fighter'ın hiç saldırısı yok.
- **Hasar oyuncunun kendi kağıdından anında uygulanıyor** (14 → 9), DM onayı
  yok -- ve **oyun günlüğüne hiç düşmüyor**. Bu bir Tetsu *avantajı*: talep →
  DM onayı → olay kaydı zinciri masayı DM'in haberi olmadan değiştirilemez
  kılıyor. D&D Beyond'da DM, HP değişikliklerini hiç görmüyor.
- Zar görünürlüğü günlüğün üstünde "SEND TO: Everyone" olarak sürekli seçili
  duruyor; Tetsu'daki roll mode'un kalıcı hâli.
- Short Rest paneli kural metnini ve tam formülü ("Hit Die: 1d12+2") gösteriyor.
  Tetsu'nun hit dice akışı bununla aynı seviyede.
- Ücretsiz katman **6 karakterle sınırlı** ("Slots: 6/6 Used").

**Kopyalamayacağımız şeyler:** içerik hak sahipliği ve paylaşım slotları,
abonelik duvarı, ayrı ayrı yaşayan araçlar.

## Bugün nerede duruyoruz

Ölçülen: 52 HTTP route + 1 websocket · 69 komut · 61 pytest dosyası ·
7 Playwright spec · motorlar 3.028 satır · CI'da 13 adım (lint, pytest,
güvenlik istisna politikası, pip-audit, npm audit, build, e2e, diff hygiene).

Altyapı iyi. Eksik olan altyapı değil.

### En büyük üç eksik

**1. İçerik yok.** Sabitlenmiş katalogda **7 kayıt** var — her türden bir tane:
Fighter, Human, Acolyte, Blinded, Second Wind, Shield, Cure Wounds. Yani
Tetsu'da yaratılabilecek tek karakter **Acolyte geçmişli Human Fighter**.
Mimari (şema v2, provenance, tip doğrulama) hazır; içerik yok.
Tıkanma noktası: `api/rules_catalog.py` içeriği SHA-256 ile
`SRD_CC_v5.2.1.pdf`'e sabitliyor, o dosya depoda yok. Bu bir karar bekliyor
(PDF'i sağlamak ya da ruleset'i depodaki Basic Rules'a yeniden sabitlemek).
**SRD içeriği ezberden yazılmaz.**

**2. Seviye atlama yok.** `level_up`, `experience_points`, `multiclass` —
kodda sıfır eşleşme. Motor HP'yi 1–20 için hesaplıyor ama seviyeyi
değiştirecek hiçbir yol yok. Karakter 1. seviyede yaratılır ve orada kalır.
Seviye atlamayan bir kampanya kampanya değildir; bu, içerikten sonraki en
büyük engel.

**3. Depo kendini başka bir ürün olarak tanıtıyor.** README'nin başlığı
"D&D RAG Chatbot"; "Tetsu" kelimesi README'de **hiç geçmiyor**, VTT ise
"Multiplayer Uygulama" başlıklı bir alt bölüm. Bağımlılıklar da öyle:
`streamlit`, altı `llama-index-*`, `qdrant-client`, `fastembed`, `pymupdf`.
`pyproject.toml`'daki bir yorum bunu zaten itiraf ediyor: *"Neither is
imported by Tetsu"*. 55 açıklık ve süreli nltk istisnası buradan geliyor.

### D&D Beyond'a göre fark tablosu

| Alan | Onlarda | Bizde |
|---|---|---|
| Katalog içeriği | Tam SRD + satılan kitaplar | 7 kayıt |
| Seviye / XP | XP çubuğu, level-up, multiclass | yok |
| Hazır karakter | Premade character | yok |
| Hazır kampanya | Premade campaign | yok |
| Subclass | var | şema hazır, içerik yok |
| Rest | Kağıtta tek tık | `short_rest`/`long_rest` var |
| Conditions | Kağıtta ekle/çıkar | `add_condition`/`remove_condition` var |
| Defenses | Resistance/immunity/vulnerability | yok |
| Inspiration | Heroic Inspiration | yok |
| Reaction takibi | Actions'ta ayrı sekme | komut yok |
| Zar geçmişi | Kampanya Game Log | oturum içi log var, kampanya geçmişi yok |
| Encounter zorluğu | Partiye göre XP hesabı | yok |
| Encounter'da PC | Birinci sınıf üye | **sıraya hiç giremiyor** |
| DM özel notu | Private + Public ayrı | yalnızca public |
| Davet | Link her zaman görünür | kod yalnızca yenilenince görünür |
| VTT | Ayrı ürün (beta) | **entegre** ✅ |
| Fog of war | var | var ✅ |
| Encounter aracı | Ayrı sayfa | **entegre** ✅ |
| NPC kütüphanesi | Homebrew üzerinden | **entegre** ✅ |
| Davet | Link | 8 karakterli kod ✅ |
| HP değişikliği | Oyuncu tek başına uygular | **DM onayı + olay kaydı** ✅ |
| Karakter sayısı | Ücretsizde 6 | sınırsız ✅ |
| AI DM | yok | var ✅ |
| Kural sorgusu (RAG) | yok | var ✅ |

Entegre olduğumuz yerler gerçek avantaj. Fark, oynanabilirliğin temelinde.

## Oyun testi (2026-09-08, DM + oyuncu olarak masaya oturarak)

Bir masa kuruldu, Riva adında bir Fighter yaratıldı, encounter açıldı, hasar
alındı ve Second Wind kullanıldı. Aşağıdakilerin hepsi bu oturumda yaşandı.

**Çalışan ve iyi olan:** builder skill yetkinliklerinin *nereden geldiğini*
gösteriyor (Acolyte sabit / Class seçeneği) ve üç ayrı bütçeyi ayrı ayrı
sayıyor — bu konuda D&D Beyond'dan daha şeffaf. Background +2/+1 artışı ve
Standard Array / 27 puan seçimi kurallara uygun. Kalkan kuşanınca AC 12→14
sunucuda anında güncellendi. Oyuncunun hasar talebi DM onayından sonra
11/11 → 6/11 olarak oyuncunun ekranına anında yansıdı. Second Wind 2024
kurallarına göre seviye 1'de 2 kullanım veriyor ve tur dışında reddediliyor.
Onay döngüsü ürünün en sağlam parçası.

**Oyunu durduran bulgu — oyuncu karakteri sıraya hiç giremiyor.**
Encounter başladığında sırada yalnızca canavarlar var. Motor bunu destekliyor:
`add_combatant` payload'ında `character_id` verilirse combatant kimliği
karakter kimliği olur ve tur kapısı açılır (`combatants[turn_index]["id"] ==
character_id`). Bunu elle API'den gönderdim, Riva sıraya girdi ve Second Wind
çalıştı. Ama **hiçbir arayüz bu alanı göndermiyor**: DM konsolundaki ekleme
satırı yalnızca ad/HP/initiative yolluyor, encounter builder'ın tür literali
ise `monster|npc` — `player` yok. Sonuç: turla kısıtlı her yetenek (Second
Wind, death save, tur içi ekipman değişimi) canlı oyunda ulaşılamaz.
`test_command_reachability` bunu yakalayamaz, çünkü komut *adının* istemcide
geçip geçmediğine bakar, hangi payload şeklinin gönderildiğine değil.

**Fighter'ın saldırısı yok.** Actions sekmesi "DM henüz attack veya prepared
spell tanımlamadı" diyor; ekranda yalnızca Perception ve Dex Save var.
Sebep katalogda silah olmaması. Başlangıç ekipmanı tek bir Shield, başlangıç
parası 0. Yani seviye 1 Fighter savaşa silahsız ve parasız giriyor.

**Daha küçük ama gerçek pürüzler:**
- Davet kodu yalnızca *yenilendiği anda* görünüyor. DM sayfayı yenilerse kodu
  bir daha göremez; görmek için yenilemek zorunda, o da önceden kod verdiği
  oyuncuları dışarıda bırakır. Giriş ekranındaki rehber ise "kod DM'in
  ekranında görünür" diyor.
- Encounter başladıktan sonra combatant ekleme satırı hâlâ açık duruyor ama
  her kullanımı 400 dönüyor ("Canli encounter listesi builder disindan
  degistirilemez") ve **bu hata DM'e hiç gösterilmiyor**.
- DM'in onay kuyruğunda talep "damage 5 HP" olarak görünüyor, **hangi
  karakter olduğu yazmıyor**. Birden fazla oyuncuda ayırt edilemez.
- Oyuncu hasar talebi gönderince ekranda hiçbir geri bildirim yok.
- Oyun akışı "character check rolled" yazıyor; hangi kontrol ve sonuç ne
  belli değil. D&D Beyond'un Game Log'u "Perception: 11" gösterir.
- Builder "Seviye değişiklikleri aktif DM tarafından yönetilir" diyor. Öyle
  bir mekanizma yok; arayüz var olmayan bir söz veriyor.

**Bu testin sıralamaya etkisi:** karakteri sıraya sokmak, listedeki 3. maddeyi
(seviye) beklemeden yapılabilecek küçük bir iş ve oyunun oynanabilirliğini
tek başına en çok değiştiren düzeltme. Sıraya 1'den hemen sonra girer.

## Kullanıcı beklentileri araştırması (2026-09-08)

Kaynak notu: Reddit, Anthropic'in tarayıcısına kapalı olduğu için önce
okunamadı; Atakan'ın tarayıcısından girilerek okundu. Ayrıca D&D Beyond'un kendi
geri bildirim forumu (kullanıcıların doğrudan talepleri), DM araç derlemeleri ve
kampanya-ölümü yazıları. Satıcı bloglarının kendi ürünlerini haklı çıkaran bir
çerçevesi olduğu unutulmamalı.

### İkisinde de eksik olanlar

**1. Oturumlar arası hafıza.** DM araştırmalarında en yüksek etkili ihtiyaç
olarak çıkan şey oturum notu ve geri çağırma sistemi: otomatik özet, aranabilir
kampanya geçmişi, unutulan olay örgüsü ipleri. D&D Beyond'un Game Log'u yalnızca
zar tutuyor. Tetsu'da oturum özeti/not/quest/loot var ama özet üretimi, arama ve
oturumlar arası hafıza yok. **Tetsu bunu yapmaya benzersiz konumda**: RAG ve AI
DM zaten burada; eksik olan olayları besleyip özet üretmek.

**2. Takvim ve ivme.** Kampanyaların çoğu üçüncü ile beşinci oturum arasında
ölüyor; sebepler takvim çakışması, oyuncu kaybı, DM tükenmişliği ve ivme kaybı
("üst üste iki oturum kaçırılınca yeniden başlamanın psikolojik maliyeti
büyüyor"). D&D Beyond'da takvim özelliği hiç yok. Tetsu'da tek bir
`scheduled_at` alanı var -- uygunluk toplama, yoklama, "geçen sefer ne olmuştu"
ile geri dönen oyuncuyu içeri alma yok.

**3. Sohbet ve ses.** D&D Beyond kullanıcıları Maps için açıkça sohbet penceresi
ve ortam sesi/soundboard istiyor. Tetsu'da sohbet **hiç yok** (`chat` için sıfır
eşleşme), ses de yok. Yani her iki üründe de masa ikinci bir pencerede
(Discord) yaşıyor -- bu, ikisinin de "all-in-one" iddiasını delen tek şey.

**4. Dinamik ışık / görüş hattı.** D&D Beyond forumunda en çok istenen
maddelerden biri: karakter hareket ettikçe haritanın otomatik açılması, karaktere
özel görüş menzili, kişi başı fog. Tetsu'da yalnızca elle boyanan fog var.
İkisi de bu konuda Foundry ve Roll20'nin gerisinde.

**5. Haritada DM'e özel katman.** Forumda ayrı bir başlık açılmış: haritaya
oyunculardan gizli not/etiket koyabilmek. İkisinde de yok.

**6. Yeni oyuncunun sırası geldiğinde.** Yeni oyuncuların en sık dile getirdiği
korku, sıra kendilerine gelince ne yapacaklarını bilememek ve masayı
yavaşlatmak. İki ürün de bu anda **proaktif** değil: D&D Beyond kağıtta
"Actions in Combat" listesi gösteriyor, Tetsu'da kural sorusu sorulabiliyor ama
soran olmadan kimse konuşmuyor. "Sıra sende, şunları yapabilirsin" diyen bir
yüzey ikisinde de yok.

### Reddit'te DM'lerin kendi sözleriyle asıl acı

r/DMAcademy'de yüksek oylu bir hazırlık başlığı ve yorumları, sorunun VTT
özelliği olmadığını gösteriyor. Başlığı açan DM'in tespiti: yıllarca
**oynatılacak içerik değil düzyazı** yazmış; saatler kimsenin okumayacağı
metni cilalamaya gitmiş. Topluluğun ortak çözümü madde işaretleri ve kontrol
listeleri ("Lazy DM" yöntemi).

Ama yorumlarda buna karşı bir ihtiyaç da var: birkaç kişi doğaçlama betimleme
yapamadığını, mekânı anlatan cümleyi önceden yazmak zorunda kaldığını ve
zamanın asıl orada gittiğini söylüyor. Yani ihtiyaç "daha az yazı" değil,
**doğru anda hazır olan kısa betimleme**.

Diğer tekrar eden temalar:
- "Bütün o dünya detaylarını yazmadan aklımda tutamıyorum" -- hatırlama sorunu.
- Bir DM bölge başına 6 NPC / 6 mekân / 6 eşya / 6 geçmiş olay / 6 fraksiyon /
  6 tehdit hazırlayıp oyun sırasında zar atarak kullanıyor. **Üretici ve
  rastgele tablo** ihtiyacı.
- "Keşke biri WotC'ye de bunu öğretse; resmi modüller DM dostu değil" -- 115
  yanıt almış.
- DM'ler prep'i Notion gibi dış araçlarda tutuyor. Yani hem D&D Beyond hem
  Tetsu için masa zaten üçüncü bir pencerede yaşıyor.
- r/rpg'de yüksek oylu bir başlık: "iyi bir GM olmak esas olarak bir soft-skill
  problemi." Araç, işin zor kısmını çözmüyor.

### Sahiplik kaygısı -- Tetsu'nun konuşmadığı avantajı

r/dndbeyond'un yılın en çok oy alan kullanıcı başlıklarından biri: *satın
aldığın içeriği indirebilseydin oradan alışveriş yapmak seni daha rahat
ettirir miydi?* Gerekçe, dijital ürünlerin iade edilmeden silinmesi. Bir
diğeri, dokuz yıldır istenen bir arama filtresinin hâlâ eklenmemiş olması.
Kullanıcılar ayrıca resmi karakter kağıdını değiştiren kendi tarayıcı
eklentilerini yayınlıyor.

Tetsu kendi sunucusunda, kendi verisiyle, internetsiz LAN'da çalışıyor.
Bu, forumda dile getirilen kaygının doğrudan cevabı ve bugün hiçbir yerde
söylenmiyor.

### AI konumlandırması: iki ayrı kitle, iki ayrı norm

İlk okumamda "AI DM bir risk" demiştim; bu fazla genişti ve düzeltiyorum.

**Masa kitlesi (r/rpg, r/dndbeyond).** Açık anti-AI normlar var: r/dndbeyond
kurallarında "No AI-generated content", r/rpg'de AI gönderileri için kural
değişikliği ve LLM'le üretilmiş eleştirilere karşı yüksek oylu başlıklar.
Burada reddedilen şey **AI'nın masaya kurgu üretmesi**.

**Solo kitlesi (r/Solo_Roleplaying).** Tamamen farklı: subreddit'in başlığı
bile "How I Learned to Stop Worrying and Love A.I. along with Oracles". Yılın
en çok oy alan başlıklarından biri, topluluğun kendi ürettiği bir **kademe
modeli** sunuyor. Bu model Tetsu için doğrudan bir tasarım rehberi:

| Kademe | Ne yapar | Tetsu'daki karşılığı |
|---|---|---|
| 0 | AI yok | `dm_mode: human` |
| 1 | **Kayıt tutucu** — oturum özeti, NPC takibi, çözülmemiş iplerin listesi, "aradan sonra oyuna dönüş" özeti. *Sınır: oyuna içerik eklemez.* | **Bilgi grafiği + oturum brifingi** (B hattı) |
| 2 | **Gelişmiş oracle** — anlık tablo üretme ve sonuç yorumlama | Bölge tabloları (DM prep bulgusu) |
| 3 | **NPC emülatörü** — oyuncu kendi karakterini oynarken AI NPC'yi oynar. Solo oyunun en tatsız yanı olan diyaloğu çözer. Şablon gerekir: ne istiyor, ne biliyor/bilmiyor, oyuncuya karşı ne hissediyor. | `campaign_npcs` + grafikten "ne biliyor" |
| 4 | **Sahne/encounter asistanı** — yapılandırılmış girdiden tek bir sahne yürütür: durum, NPC'ler, gizli bilgi, baskı, çıkış koşulları, sonuçlar | `POST /api/ai-dm/step`, encounter kapsamlı |

Yazarın iki cümlesi mimarimizi birebir doğruluyor:

- *"Oyuncu kuralları ve zarları hâlâ kendi yürütür."* Tetsu'da bu zaten
  değişmez kural: **katalog veridir, motorlar koddur**; AI hiçbir mekaniğe
  karar vermez.
- *"AI'dan tüm kampanyayı değil, yapılandırılmış tek bir oyun birimini
  yürütmesi istenir. Bugün 'etkili AI GM'in pratik sınırı burasıdır."*
  Tetsu'nun AI DM'i zaten encounter kapsamlı.

Ve topluluğun hayal kırıklığı olarak işaret ettiği şey: *"AI'yı insan GM'in
yerine koymaya çalışıyorum ve berbat."* Yani **tam ikame satılmaz**; kademe
satılır.

**Sonuç:** Tetsu'nun üç DM modu (`human` / `assisted` / `ai`) bu kademe
modeliyle örtüşüyor — tesadüf değil, aynı problemi görmüşler. Dışa dönük
anlatım "AI DM" değil, kademeler olmalı: *kuralı bul · oturumu hatırla ·
NPC'yi canlandır · sahneyi yürüt*.

### Tetsu'da zaten olup D&D Beyond'da istenen şeyler

Bu liste rekabet açısından önemli: aşağıdakiler D&D Beyond forumunda talep
edilen maddeler ve Tetsu'da **zaten çalışıyor** (oyun testinde gözle görüldü).

- Son işlemi geri alma (Ctrl+Z talebi)
- Süreli condition ve tur sayacı ("condition time tracker")
- Tur ortasında initiative değiştirme
- Combatant HP'sini hızlı düzenleme
- HP değişikliğinin DM onayından geçmesi ve olay kaydına düşmesi

Yani Tetsu'nun encounter yönetimi, içerik ve PC-sıra bağlantısı düzeltilirse
D&D Beyond'un kullanıcılarının istediği yerde zaten daha ileride.

## Mimari kararlar

1. **Kuralların otoritesi sunucudur.** İstemci hiçbir kural değeri
   hesaplamaz; `derived` sunucudan gelir. Passive skorlar bunun için
   sunucuya taşındı.
2. **Katalog veridir, motorlar koddur.** Katalog kaydı *betimleyicidir*;
   yürütülebilir efekt taşıyamaz (`subclass` doğrulaması `effects` alanını
   reddeder). Zar, kaynak ve HP kararları motorlarda kalır.
3. **Gizli veri istemciye hiç gitmez.** Oyuncunun fog'u sunucuda piksele
   işlenir; istemcide gizlenmez. Yeni bir "DM'e özel" alan eklerken kural
   budur: *filtrelemeyi projeksiyonda yap, sunumda değil.*
4. **Tek düğüm, LAN öncelikli.** SQLite + WAL + `BEGIN IMMEDIATE`.
   Postgres değerlendirmesi `docs/sqlite-postgresql-evaluation.md`'de; bugün
   taşınma gerekçesi yok.
5. **Tema odayı değiştirir, sayfayı değil.** Paneller her temada parşömen
   kalır. Bu bir tercih değil zorunluluk (aşağıda: 445 sabit renk).
6. **Üretilen varlıklar tohumdan yeniden üretilebilir olmalı.**
   `tools/generate_textures.py` örnektir; depoya gizemli binary girmez.

### Verilmesi gereken mimari kararlar

- **Karakterler `games.state_json` içinde yaşıyor**, tablo değil. Bu yüzden
  sorgulanamıyor, indekslenemiyor, kampanyalar arası taşınamıyor ve yeni bir
  `derived` alanı eklemek her seferinde backfill migration'ı gerektiriyor
  (bkz. migration 032). Premade karakter, karakter kütüphanesi ve seviye
  geçmişi bu karar verilmeden düzgün yapılamaz.
- **İki ürün, tek bağımlılık ağacı.** RAG'ı ayrı bir opsiyonel bağımlılık
  grubuna almak, VTT'nin saldırı yüzeyini ve CI süresini birlikte küçültür.

## Kısıtlar (pazarlık yok)

- **SRD içeriği ezberden yazılmaz.** Her katalog kaydı belge kimliği,
  SHA-256 ve sayfa etiketiyle gelir. Lisans `CC-BY-4.0`.
- **v1 kayıtları yeniden yorumlanmaz.** Yayınlanmış bir şema sürümünün
  anlamı sonradan genişletilmez; yeni şekiller yeni şema sürümüne gider.
- **CSP dışarıya kapalı.** `default-src 'self'`; font, ikon ve varlıklar
  depoda barındırılır. Oyuncular internetsiz LAN'dan da bağlanır.
- **Migration'lar açılışta koşar.** Tek bozuk satır tüm dağıtımı
  düşürmemeli; okunamayan kayıt olduğu gibi bırakılır.
- **Türkçe arayüz.** Kullanıcıya görünen her metin Türkçe; latin-ext font
  aralığı zorunlu (ğ, ş, İ).
- **Yıkıcı işlem onay ister.** Kampanya silme, kod iptali, DM devri.

## Teknik borç sicili

Etkisine göre sıralı. Her madde neyi engellediğiyle birlikte.

1. **`web/src/styles.css` içinde 445 sabit hex renk** ve dosya 2.467 satır.
   Token katmanı var ama stylesheet'in çoğu onu kullanmıyor. **Engellediği:**
   gerçek dark mode. Temanın yalnızca zemini değiştirebilmesinin sebebi bu.
2. **Karakterler `state_json` içinde.** **Engellediği:** premade karakter,
   karakter kütüphanesi, seviye geçmişi, kampanyalar arası taşıma.
3. **RAG bağımlılıkları VTT ile aynı ağaçta.** **Engellediği:** küçük saldırı
   yüzeyi, hızlı CI, süreli güvenlik istisnasından kurtulmak.
4. **README ürünü yanlış tanıtıyor.** **Engellediği:** yeni katılan birinin
   depoyu anlaması.
5. **`CharacterBuilder.tsx` 1.237 satır.** Tek dosyada beş adımlı akış.
6. **Dal `main`'in 19 commit önünde**, `main` hiç güncellenmemiş, PR yok.
7. **`AGENTS.md` silinmiş ama commit'lenmemiş** (benim değişikliğim değil;
   Atakan'ın kararı bekleniyor).
8. **`docs/` Türkçe, kod yorumları İngilizce.** Bilinçli görünüyor ama
   yazılı değildi; artık burada yazılı.

## Sıradaki işler

İki hat var. Sol hat oyunu **oynanabilir** kılıyor ve sırayla ilerlemek
zorunda; sağ hat oyunu **hatırlanabilir** kılıyor ve `events` kendi tablosunda
olduğu için paralel ilerleyebilir.

**A — oynanabilirlik (sıralı)**

1. **Katalog provenance kararı** → içeriği doldur. Bundan önce yapılan her
   şey Human Fighter için yapılır.
2. **Oyuncu karakterini encounter sırasına bağla.** Motor destekliyor
   (`add_combatant` payload'ında `character_id`); hiçbir arayüz göndermiyor.
   Küçük iş, oynanabilirliği tek başına en çok değiştiren düzeltme.
3. **Karakterleri kendi tablosuna taşı.**
4. **Seviye ve XP** — `award_xp`, `level_up`, HP/proficiency/feature
   yeniden hesabı, seviye geçmişi.
5. **Premade karakter ve premade kampanya** (3 ve 4'ün üstüne oturur).
6. **Defenses ve Inspiration** — kağıttaki son büyük boşluklar.

**B — hatırlanabilirlik: bilgi grafiği (A ile paralel)**

Gerekçe ve teknik analiz: `docs/bilgi-grafigi-ve-altyapi-analizi.md`.
Araştırmanın en büyük boşluğu ve rakibin hiç dokunmadığı yer.

1. `graph_nodes` + `graph_edges` + FTS5 (migration 033), görünürlük
   projeksiyonu testleriyle.
2. **Olaylardan kenar türetici** + geri doldurma migration'ı. Ham madde
   zaten `events` tablosunda: tipli, failli, görünürlüklü, zamanlı.
3. **Backlink paneli** — "bu NPC nerelerde geçti". Obsidian'ın en çok
   kullanılan özelliği grafik görünümü değil, backlink panelidir.
4. Notlarda `[[bağ]]` yazımı ve otomatik tamamlama.
5. **Oturum brifingi** — "geçen sefer ne olmuştu" otomatik.
6. Grafik görünümü (kampanya çapı, tür renkleri).

Kampanya Game Log (oturum ötesi zar geçmişi) B hattının yan ürünü olarak gelir.

**C — solo oyun (A1 ve B'nin üstüne oturur)**

AI DM'in amacı masaya kurgu üretmek değil, **masası olmayana masa kurmak**.
Araştırmanın bulduğu en yapısal sorun buydu: grup ve DM bulamamak, kampanyaların
üçüncü-beşinci oturumda ölmesi. Solo hattı bu soruna doğrudan cevap ve
çok oyunculu yüzeyin (davet, takvim, onay kuyruğu) hiçbirine ihtiyaç duymaz.

Solo, r/Solo_Roleplaying'in kademe modeline göre sıralanır (yukarıdaki tablo):

1. **Kademe 1 zaten B hattı.** Oturum brifingi ve "aradan sonra oyuna dönüş"
   özeti, solo oyuncunun en çok ihtiyaç duyduğu şey — ve topluluğun en az
   tartışmalı bulduğu AI kullanımı. Ayrıca yapılacak iş yok.
2. **Kademe 3 için `campaign_npcs`'e üç alan:** ne istiyor, ne biliyor/bilmiyor,
   partiye karşı ne hissediyor. "Ne biliyor" kısmını grafik besleyebilir.
   Solo oyunun en tatsız yanı diyalog; bu onu çözüyor.
3. **Kademe 4 için sahne şeması:** durum, NPC'ler, gizli bilgi, baskı, çıkış
   koşulları, sonuçlar. Mevcut `POST /api/ai-dm/step` bunun yerini tutuyor ama
   girdisi yapılandırılmamış.
4. **Solo oturum kipi** — tek oyuncu, DM onay kuyruğu devre dışı, encounter
   sırasına PC otomatik girer (A2 zaten bunu getiriyor).

**Solo, içeriğe A hattından daha çok muhtaç.** İnsan DM eksik kuralı
doğaçlar; AI DM katalogda ne varsa onu yürütür. Tek sınıfı Fighter olan bir
katalogda solo oyun, çok oyunculu oyundan daha çabuk çöker. Yani C, A1'i
atlayamaz.

Market/shop sistemi kasten beklemede: katalogda tek item varken dükkân
tiyatrodur.

**Altyapı kararı:** dil ve veritabanı değişmiyor. Ölçüldü — snapshot p50
11,97 ms; grafik sorguları 50.000 düğüm / 200.000 kenarda komşuluk 0,02 ms,
3 hop 4,37 ms. SQLite ihtiyacın iki mertebe üstünde. Neo4j/Postgres bugün
saf maliyet; SQLite'ı terk etme tetikleyicisi değişmedi (çok kiracılı bulut
kurulumunda eşzamanlı yazar baskısı) ve grafik şeması o gün olduğu gibi taşınır.

## Bu depoda çalışma kuralları

Bunlar yaşanmış hatalardan çıktı; her biri en az bir kez bana zaman kaybettirdi.

**Sunucu.** `run_api.py` reload'u `API_RELOAD` ile okur ve **varsayılanı
kapalıdır**. Geliştirirken `API_RELOAD=1` ver; vermezsen backend'e her
dokunduğunda sunucuyu elle yeniden başlatmak zorundasın, yoksa eski kodu ve
eski şemayı servis eder. Bunu bir turda dört kez unuttum.

**Migration.** Hatayı üreten veritabanında sondaj yap. `based_on` self-FK
tuzağını ilk turda kaçırdım çünkü klonu olmayan bir DB'de denemiştim.
Migration 030 iki kez `ruleset_entries`'i silmişti (42 → 0).

**Test.** "En son X'tir" varsayan test yazma. `LATEST_SCHEMA_VERSION`'a veya
sabit bir tarihe bağlanan üç test, yeni bir şey eklenince kırıldı.
Yeni bir davranış kilitliyorsan **mutasyonla dişini kanıtla**: kodu no-op
yapıp testin kırmızıya düştüğünü gör.

**Doğrulama.** İddia etmeden önce ölç. Bu turda iki kez yanlış teşhis
koydum: beyaz ekranı "cache" sandım (sekmenin ağ katmanı bozuktu),
tema değiştirmeyi "bozuk" sandım (JS ile `data-theme` değiştirip
`getComputedStyle` okumak bayat değer veriyor; gerçek picker sorunsuzdu).
**Otomasyonun ölçümüne değil, kullanıcının yoluna bak.**

**CSS.** `body`'ye `background-color` verirsen negatif `z-index`'li
`body::before` katmanını örtersin — boyama sırası in-flow blok arka planlarını
negatif z-index çocuklarından sonra çizer. Zemin rengi `:root`'a aittir.
Koyu tema eklerken kırılan şey panel içi metin değil, **panel dışında doğrudan
zemine yazılmış** metindir; onlar `--on-ground` üzerinden gelir.

**Komutlar.**
```
uv run python -m pytest -q          # 61 dosya, ~2 dk
uv run ruff check .
cd web && npm run build             # e2e'den önce şart, API dist'i servis eder
cd web && npm run test:e2e
uv run python tools/generate_textures.py --check
```

**Commit.** Mesaj Türkçe, `tag: özet` biçiminde, en fazla beş kelimelik
özet. Gövdede *ne* değil *neden*. Ortak yazar satırı yok.

**Kod.** Yorumlar İngilizce, dokümanlar ve kullanıcıya görünen metin Türkçe.
Yorum "ne yaptığını" değil "neden böyle olduğunu" anlatır.
