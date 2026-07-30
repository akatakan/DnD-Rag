# D&D'yi PC Üzerinden Oynama ve Tetsu Entegrasyon Raporu

**Tarih:** 29 Temmuz 2026
**Kapsam:** Masaüstü web deneyimi, oyuncu ve DM yaşam döngüsü, D&D Beyond karşılaştırması ve bu deponun mevcut FastAPI/React mimarisine uygulanabilir ürün-teknik tasarım.
**Durum:** Araştırma ve öneri dokümanı; bu rapor kod değişikliği yapmaz.

## 1. Yönetici özeti

D&D'yi PC üzerinden oynamak, bir video oyununu dijitalleştirmekten çok paylaşılan bir
masa deneyimini desteklemektir. Asgari çevrim içi oyun; sesli iletişim, karakter
kağıdı, ortak zar sonuçları ve DM'nin sahneyi yönetebildiği güvenilir bir ortak durum
gerektirir. Harita/VTT taktik oyun için değerlidir fakat "theater of the mind" oynayan
gruplar için zorunlu değildir. D&D Beyond da çevrim içi oyun rehberinde sesli iletişimi
temel, VTT'yi isteğe bağlı olarak konumlandırır.

Tetsu'nun mevcut multiplayer çekirdeği doğru bir başlangıçtır:

- `api/game_engine.py` authoritative komut sınırıdır.
- `api/store.py` SQLite üzerinde oyun, üyelik, event ve onay taleplerini tutar.
- `api/app.py` rol bazlı snapshot üretir ve gizli yaratıkları/monster HP'sini oyuncudan
  redakte eder.
- `api/realtime.py` WebSocket bağlantılarını ve DM devir süresini yönetir.
- `dice.py` zar sonucunu sunucuda, `secrets` tabanlı ve sınırları doğrulanmış biçimde
  üretir.
- `web/src/components/DMConsole.tsx` ve `PlayerConsole.tsx` canlı masanın iki rol
  görünümüdür.
- `agent.py` ve `/api/rules` kaynaklı kural yanıtı sağlar; Ollama/Qdrant isteğe bağlıdır.

Ancak mevcut ürün "tek kullanımlık canlı oda" seviyesindedir. Oyuncu katılınca otomatik
Fighter karakteri oluşur; kampanya listesi, session zero, yapılandırılmış karakter
kağıdı, spell/feature/action kaynakları, seviye atlama, planlanmış encounter, oturum
kaydı ve geçmişe dönüş yoktur. Bunları bir kerede VTT'ye dönüştürmek yerine üç dikey
dilim önerilir:

1. **MVP / güvenilir dijital masa:** kampanya lobisi, hafif karakter oluşturma ve
   kağıdı, canlı masa, encounter, kaynaklı kural çekmecesi, global zar FAB'ı ve oturum
   özeti. Sekiz tam ekran.
2. **Oyun derinliği:** spell slotları, rests, conditions süreleri, level-up,
   encounter hazırlığı, notlar ve event pagination.
3. **İleri VTT:** harita/token/fog, medya depolama ve ölçeklenebilir realtime. Bu,
   çekirdek oyun akışı doğrulanmadan başlanmamalıdır.

En önemli ürün kararı şudur: D&D Beyond'un görünümünü, markasını, ücretli içeriğini veya
kapalı veri modelini kopyalamayın. Referans alınması gereken şey ürün deseni—kampanya
merkezi, hesaplanan karakter kağıdı, karakterden tetiklenen zarlar, gerçek zamanlı oyun
günlüğü ve hazırlık/oyun ayrımıdır. İçerik için yalnızca projede kullanım hakkı bulunan
PDF'ler, açık lisanslı SRD 5.2 içeriği veya kullanıcının kendi verisi kullanılmalıdır.

## 2. Araştırma bulguları: D&D PC'de gerçekte nasıl oynanıyor?

### 2.1 Temel bileşenler

D&D Beyond'un resmi çevrim içi oyun rehberi internet bağlantısı ile metin, ses veya
video iletişimini temel ihtiyaç olarak gösterir. Karakter oluşturucu/kağıdı, kural
erişimi, encounter tracker ve VTT ise oyunu kolaylaştıran araçlardır. Bu nedenle Tetsu:

- ses/video sağlayıcısı olmak zorunda değildir; MVP'de Discord/Teams benzeri harici
  görüşmeye bağlantı alanı yeterlidir;
- karakter ve kampanya durumunu kendi authoritative backend'inde tutmalıdır;
- map olmadan oynanabilir olmalı, map eklendiğinde aynı encounter modelini kullanmalıdır;
- dijital aracın masa sohbetini bastırmaması için oyuncu ekranındaki sürekli aksiyon
  sayısını sınırlamalıdır.

Resmi D&D akışı iki aktöre dayanır. Oyuncu, karakter adına karar verir ve belirsiz
sonuçlar için zar atar. DM dünyayı/NPC'leri sunar, engelleri yönetir, hedef sayıyı
belirler ve sonucu yorumlar. SRD 5.2'ye göre d20 testleri ability check, saving throw ve
attack roll'dur; advantage iki d20'nin yükseğini, disadvantage düşüğünü kullanır. Bu
ayrım zar UI'sında "avantaj/dezavantaj"ı tüm zar türlerine gelişigüzel uygulamama
gereğini doğurur.

### 2.2 D&D Beyond'dan doğrulanan ürün desenleri

D&D Beyond bugün şu yetenekleri bir araya getirir:

- Rehberli Quick Build/Standard karakter oluşturma ve yardım metni.
- Otomatik bonus hesaplayan; HP, spells, class features ve inventory izleyen dijital
  karakter kağıdı.
- Karakter kağıdından 3B zar atma ve sonucu gerçek zamanlı paylaşma.
- Karakterleri, ortak/açık ve DM'ye özel notları bir araya getiren kampanya merkezi.
- Kaydedilebilir encounter hazırlığı; initiative, HP, tur/round, ileri/geri tur ve
  devam ettirme.
- Game Log içinde aksiyon, modifier, zarlar ve sonuç paylaşımı.
- Maps içinde tarayıcı tabanlı harita, token, fog of war, ruler/ping/draw ve encounter
  takibi.

Bunlardan çıkarılacak ilke "birçok araç" değil, **bağlamı kaybetmeden araçlar arası
geçiştir**. Karakterden yapılan attack roll oyun günlüğüne düşer; encounter karakterin
güncel HP'sini kullanır; campaign DM'ye tüm karakterleri gösterir.

### 2.3 Neyi kopyalamamalıyız?

1. **Görsel taklit:** D&D Beyond'ın gridini, renklerini, ikonlarını, metinlerini,
   animasyonlarını veya marka öğelerini bire bir üretmeyin. Tetsu özgün tema ve
   bileşen sistemi kullanmalıdır.
2. **İçerik/entitlement modeli:** Marketplace, abonelik ve içerik paylaşımını yeniden
   üretmek MVP hedefi değildir. Kullanıcının sahip olduğu içeriği otomatik olarak
   D&D Beyond'dan çekmeye çalışmak için doğrulanmış açık API sözleşmesi yoktur.
3. **Kapalı compendium verisi:** Monster, spell ve seçeneklerin tamamını scrape etmeyin.
   SRD 5.2/open-license katalog veya kullanıcının sağladığı lisanslı kaynaklar ayrı
   provenance ile tutulmalıdır.
4. **Tek dev ekran:** D&D Beyond'un tarih içinde ayrı gelişen Encounters ve Maps
   yüzeylerini aynen çoğaltmak yerine Tetsu'da tek `Encounter` domain modeli ve farklı
   sunumlar kullanılmalıdır.
5. **Animasyonu otorite yapmak:** Zarın fizik animasyonu sonucu belirlememeli. Sonucu
   sunucu üretmeli; istemci yalnızca event payload'ındaki sonuçla biten animasyonu
   göstermelidir.
6. **Ses/video inşa etmek:** WebRTC, TURN ve moderasyon başlı başına üründür. İlk
   fazlarda harici toplantı bağlantısı "build" seçeneğinden daha iyi trade-off'tur.
7. **Her kuralı otomatikleştirmek:** D&D istisna ve DM kararı içerir. Otomasyon;
   hesaplama, kaynak tüketimi ve görünürlükte yardımcı olmalı, DM kararını gizlice
   ikame etmemelidir.

## 3. Uçtan uca kullanıcı akışları

### 3.1 DM akışı

1. DM kampanya oluşturur; ad, kural sürümü (`2024`/gelecekte `2014`), oyun dili,
   oyun tarzı (theater/tactical), DM modu ve güvenlik seçeneklerini seçer.
2. Session zero sayfasında ton, sınırlar/lines-veils, house rules, başlangıç seviyesi,
   ability score yöntemi ve dış sesli görüşme bağlantısını yayınlar.
3. Davet bağlantısı/kodu üretir; bekleyen üyeleri ve karakterleri onaylar. Mevcut
   `invite_code` korunabilir, fakat public deployment'ta süreli/iptal edilebilir davete
   dönmelidir.
4. Oyuncu karakterlerini readiness checklist ile izler; gizli DM notu ekleyebilir.
5. Oturum öncesi sahne ve encounter taslağı hazırlar; katılımcıları seçer, monster/NPC
   verisini SRD/kullanıcı kataloğundan ekler.
6. Oturumu başlatır. Canlı masada sahne yayınlar, istekleri sonuçlandırır, encounter
   başlatır, initiative/tur/round/condition/HP kaynaklarını yönetir.
7. Gizli veya açık zar atar. AI assisted modda plan önerisi alır; planı görmeden
   uygulanmaması varsayılan olmalıdır.
8. Oturumu bitirir; XP/milestone, loot, quest ve özet taslağını gözden geçirip yayınlar.
9. Level-up açarsa oyuncuların seçimlerini bekler; değişikliklerin bir sonraki oturumda
   geçerli olmasını onaylar.

### 3.2 Oyuncu akışı

1. Oyuncu davet bağlantısını açar, görünen adını girer ve kampanyaya katılır.
2. Hazır karakter seçer veya rehberli karakter oluşturur: temel bilgiler, ability
   scores, class/species/background, proficiency, equipment/spells ve gözden geçirme.
3. Session zero sözleşmesini okur/onaylar; özel erişilebilirlik veya sınır notunu yalnız
   DM'ye iletebilir.
4. Lobide diğer üyeleri, planlanan saati, readiness durumunu ve açık kampanya notlarını
   görür.
5. Oyun masasında sahne, party özeti, kendi karakter kağıdı ve olay günlüğüyle oynar.
   Skill/attack/save veya sağ alttaki global zar düğmesinden roll tetikler.
6. HP gibi authoritative değişiklik için mevcut `request_damage`/`request_heal`
   mekanizmasını kullanır; inventory/spell slot gibi izinli kaynaklar domain politikasına
   göre doğrudan veya DM onayıyla değişir.
7. Encounter sırasında sıra bildirimi alır; aksiyon, bonus action, reaction ve movement
   sayaçları yalnız kolaylaştırıcıdır—kuralların tüm istisnalarını zorla uygulamaz.
8. Oturum sonunda özeti, kazanımları ve açık görevleri görür; DM izin verdiyse level-up
   wizard'ını tamamlar.

### 3.3 Yaşam döngüsü durum makinesi

```text
draft campaign
  -> session_zero
  -> lobby/preparing
  -> live_session
      -> exploration | social | encounter(active <-> paused)
  -> session_wrap_up
  -> between_sessions
  -> live_session ...
  -> archived
```

`games.state_json` içindeki yalnız `encounter_status` bu yaşam döngüsünü ifade etmeye
yetmez. `campaign.status`, `session.status` ve `encounter.status` ayrı olmalıdır.

## 4. Önerilen bilgi mimarisi ve ekran sayısı

### 4.1 Navigasyon

```text
Kampanyalar
  └─ Kampanya
      ├─ Lobi / Genel Bakış
      ├─ Karakterler
      │   ├─ Karakter Oluştur
      │   └─ Karakter Kağıdı
      ├─ Oyun Masası
      ├─ Encounter Hazırlığı (DM)
      ├─ Oturumlar / Özetler
      └─ Ayarlar / Session Zero (DM; oyuncu için salt-okunur)

Global katmanlar: Zar FAB + zar tepsisi, Rule Drawer, bildirimler, bağlantı durumu
```

### 4.2 MVP: sekiz tam ekran

Drawer, modal ve FAB tam ekran sayısına dahil değildir.

| # | Ekran | Amaç ve aktör | Gerekli state | Ana aksiyonlar | Boş / loading / hata |
|---|---|---|---|---|---|
| 1 | Giriş + Kampanya seçimi | DM/oyuncunun kampanya oluşturması veya davete katılması | local credential, kampanya özeti | oluştur, kod/link ile katıl, son oyuna dön | kampanya yok CTA; submit skeleton; 404 invite, 429 retry |
| 2 | Session Zero / Kampanya ayarı | DM ilkeler yayımlar, oyuncu okur/onaylar | campaign settings, consent revision, public/private notes | ayar kaydet, onayla, DM'ye özel not | henüz yayınlanmadı; revision loading; stale revision/403 |
| 3 | Kampanya lobisi | Oturum öncesi readiness ve presence | members, characters summary, session schedule, invite | karakter seç/oluştur, hazır ol, link kopyala, oturumu başlat | yalnız DM; reconnect; süresi dolmuş davet |
| 4 | Karakter oluşturma | Oyuncuya güvenli, rehberli minimum sheet | draft, ruleset catalog, validation | ileri/geri, taslağı kaydet, tamamla | katalog yoksa SRD-minimal; autosave; seçim çakışması |
| 5 | Karakter kağıdı | Tek oyuncunun oyun kaynaklarını yönetmek; DM salt-okuma/izinli düzenleme | computed stats, resources, actions, spells, inventory, conditions | roll, HP/resource işlemi, equip, rest, level-up çağrısı | bölüm boş CTA; optimistic indicator; conflict ve invalid resource |
| 6 | Canlı oyun masası | Ortak ana oyun yüzeyi | scene, members/presence, encounter summary, events, own sheet summary | sahne/aksiyon, rule drawer, event log, encounter'a odaklan | oturum başlamadı; snapshot skeleton; offline/reconnecting |
| 7 | Encounter masası | Aktif combat'ı düşük hata riskiyle yürütmek | initiative entries, round/turn, HP, conditions, hidden flags | initiative, start/pause/undo/next, damage/heal, condition | combatant yok; command pending; 409 stale/403 role |
| 8 | Oturum özeti | Sonuçları kalıcılaştırmak ve sonraki oturuma bağlamak | session events, summary, loot, quests, advancement | DM yayınla; oyuncu oku; level-up aç | özet hazırlanıyor; AI yoksa manuel; publish conflict |

MVP'nin tam ekran olmayan ortak yüzeyleri:

- **Global zar FAB/tepsi:** her authenticated ekranda sağ altta.
- **Kural çekmecesi:** mevcut `RuleDrawer.tsx` geliştirilir; servis hazır değilse açık
  biçimde "Yerel RAG yapılandırılmadı" gösterir.
- **Event/Game Log çekmecesi:** cursor ile eski olayları yükler.
- **Connection banner:** online/reconnecting/offline/read-only durumunu gösterir.
- **Karakter seçim ve hızlı HP modalları.**

### 4.3 İleri faz: yedi ek tam ekran, toplam on beş

| # | Ekran | Faz | Gerekçe |
|---|---|---|---|
| 9 | Encounter kütüphanesi/builder | 2 | Canlı encounter ile hazırlığı ayırır; duplicate/resume sağlar |
| 10 | Spellbook ve hazırlama | 2 | Hazırlanan/bilinen spell, slot ve concentration için odaklı UX |
| 11 | Inventory ve ekipman | 2 | quantity, container, currency, attunement ve paylaşım |
| 12 | Level-up wizard | 2 | class/subclass/feat/HP/spell seçimlerini atomik taslak olarak uygular |
| 13 | Kampanya günlüğü/quest/notlar | 2 | session sonrası süreklilik ve public/private notlar |
| 14 | Harita/VTT | 3 | token, grid, fog, ping ve tactical movement |
| 15 | Yönetim/audit/güvenlik | 3 | davet iptali, token session'ları, export, retention ve audit |

### 4.4 Ekran davranış standardı

Her tam ekran şu durumları açıkça tasarlamalıdır:

- **Initial loading:** eski snapshot'ı yanlışmış gibi sıfırlamak yerine skeleton.
- **Empty:** neden boş olduğunu ve tek sonraki aksiyonu söyleyen CTA.
- **Mutation pending:** aynı komutu tekrar göndermeyi engelle; diğer alanları gereksiz
  kilitleme.
- **Recoverable error:** hata yanında retry ve `request_id`; 429 için geri sayım.
- **Offline:** son doğrulanmış snapshot salt-okunur; zar/komut kuyruğa alınmaz.
- **Reconnect:** son `event_id` ile delta istenir; snapshot version uyuşmazsa full sync.
- **Unauthorized:** gizli veriyi bir an bile render etmeden rol uyumlu rotaya yönlendir.
- **Conflict (409):** değişikliğin başkası tarafından güncellendiğini söyle, güncel
  kaydı göster; sessiz overwrite yapma.

## 5. Zar deneyimi: sağ alt floating action button

### 5.1 Etkileşim tasarımı

Sağ altta tüm authenticated ekranlarda 48–56 px, klavye erişimli bir **Zar FAB** bulunur.
Tıklanınca yukarı açılan kompakt zar tepsisi:

1. Zar türü: d4, d6, d8, d10, d12, d20, d100.
2. Adet stepper'ı: 1–20 (backend genel parser sınırı 100 kalabilir).
3. Modifier: -99…+99.
4. d20 için mod: Normal / Avantaj / Dezavantaj.
5. Görünürlük: Herkes / Yalnız DM / Yalnız ben; DM için Herkes / Gizli.
6. Son kullanılanlar ve karakter kağıdından gelen anlamlı preset'ler.
7. "At" birincil aksiyonu; Space/Enter, Escape ve focus trap desteği.

"2d mi" ifadesi iki farklı ihtiyacı karıştırabilir: iki zar atmak ve d20'de advantage.
UI bunları ayrı sunmalıdır. Zar adedi `2`, tür `d6` ise `2d6`; d20 Advantage ise
`2d20kh1`; Disadvantage ise `2d20kl1` üretilir. Advantage/Disadvantage yalnız d20
testlerinde açılır. Hasar zarlarında "yükseğini tut" gibi özel roll gerekirse Advanced
mode altında açık formül olarak sunulur.

### 5.2 Authoritative animasyon sözleşmesi

1. İstemci `POST /api/rolls` isteğini benzersiz `client_roll_id` ile yollar.
2. Sunucu `dice.py` ile sonucu üretir ve tek transaction'da `dice_rolled` event'i yazar.
3. HTTP yanıtı gönderen istemciye düşük gecikmeli ack verir; WebSocket event'i tüm
   yetkili izleyicilere ulaşır.
4. İstemciler event'teki `rolls`, `kept`, `modifier`, `total` ile animasyonu başlatır.
5. 600–1200 ms animasyon, sunucu sonucuna deterministik biçimde yerleşir. Physics
   sonucu yeniden hesaplamaz.
6. `prefers-reduced-motion` için animasyonsuz sonuç; tab arka plandaysa doğrudan sonuç.
7. Event tekrar gelirse `event.id`/`client_roll_id` ile dedupe edilir.

Canvas/WebGL için küçük bir kütüphane değerlendirilse bile ilk sürüm CSS transform +
SVG/CSS 2.5D olabilir. Dört, altı, sekiz, on, on iki ve yirmi yüzlü zarın gerçekçi 3B
fiziğini sıfırdan üretmek; erişilebilirlik, bundle boyutu, GPU ve mobil davranış
maliyetini artırır. Animasyon uygulama yüklenmesini veya roll sonucunu bloke etmemelidir.

Mevcut `dice.py`, `2d20kh1+5` ve `2d20kl1+5` ifadelerini zaten destekler; server-side
`secrets.randbelow` kullanır. Mevcut `roll` command payload'ı `expression` ve
`visibility` alır. MVP'de bu sözleşme genişletilebilir, ancak UI domain alanlarını
string birleştirmek yerine server'ın typed `RollRequest` modeline göndermelidir.

### 5.3 Karakterden roll

Karakter kağıdındaki attack, save, skill ve spell damage satırları aynı roll servisini
kullanır; ayrıca şu semantik metadata'yı yollar:

```json
{
  "client_roll_id": "uuid",
  "kind": "attack",
  "label": "Longsword",
  "expression": "1d20+5",
  "mode": "normal",
  "visibility": "party",
  "character_id": "..."
}
```

Backend modifier'ın karakter kağıdından geldiği iddia ediliyorsa onu authoritative
sheet'ten tekrar hesaplamalıdır. Serbest zar için expression yeterlidir. Bu ayrım oyun
günlüğünde "1d20+5" yerine "Longsword saldırısı" gösterebilmek ve payload spoofing'i
önlemek için gereklidir.

## 6. Domain modeli

### 6.1 Kampanya ve oturum

- `Campaign`: id, owner_id, name, ruleset_version, language, play_style, status,
  public_notes, settings_version, created_at, updated_at.
- `CampaignMember`: campaign_id, member_id, role, display_name, status, joined_at.
- `Invite`: id, campaign_id, token_hash, role, expires_at, max_uses, uses, revoked_at.
- `Session`: id, campaign_id, number, title, scheduled_at, started_at, ended_at, status,
  scene_id, summary_status.
- `SessionZeroRevision`: id, campaign_id, version, public_content, created_by.
- `SessionZeroConsent`: revision_id, member_id, accepted_at, private_note.
- `Scene`: id, campaign_id, title, public_description, dm_notes, order, status.

### 6.2 Karakter

- `Character`: identity, owner, campaign, ruleset, name, level, class/subclass,
  species, background, alignment/optional profile, XP/milestone, revision.
- `AbilityScores`: str/dex/con/int/wis/cha ve hesaplanan modifier.
- `CharacterProficiency`, `CharacterFeature`, `CharacterAction`.
- `CharacterResource`: current/max, reset_on (`short_rest`, `long_rest`, `manual`).
- `CharacterSpell`: spell_ref, prepared/known, always_prepared, source.
- `SpellSlot`: level, current, max.
- `InventoryEntry`: catalog_ref veya custom name, quantity, weight, equipped, attuned,
  container_id, notes.
- `CharacterCondition`: condition_ref, source_actor, started_round, duration,
  concentration_link, visibility.
- `CharacterDraft`: wizard step, payload, validation errors, updated_at.
- `Advancement`: from_level, to_level, draft, status, approved_by, applied_at.

Hesaplanan AC, proficiency bonus, save/skill/attack modifier ve spell DC tek tek client
alanı olarak güvenilmemeli; ruleset service tarafından üretilmelidir. MVP tüm D&D
istisnalarını modelleyemiyorsa `calculation_overrides` provenance ve DM audit notuyla
açıkça tutulmalıdır.

### 6.3 Encounter

- `EncounterTemplate`: campaign_id, name, notes, difficulty metadata, version.
- `Encounter`: template_id, session_id, status, round, turn_cursor, revision.
- `EncounterEntry`: character/monster/npc/manual ref, display_name, initiative,
  tiebreaker, current/max/temp HP, AC, hidden, sort_key.
- `EncounterCondition`: entry_id, condition, duration/value, source_entry_id.
- `TurnHistory`: encounter revision, from/to cursor, actor, timestamp; Undo için.
- İleri faz `Map`, `MapLayer`, `Token`, `FogRegion`.

Mevcut `state_json` içindeki `combatants`, `round`, `turn_index` ve
`encounter_status` ilk migration'da bu tablolara backfill edilir. Bir geçiş süresinde
çift yazım yapmak race ve drift üretir; feature flag ile tek authoritative store
seçilmelidir.

## 7. Kalıcılık ve migration planı

Mevcut tablolar: `games`, `members`, `events`, `requests`. `games.state_json` karakter,
sahne ve encounter'ı tek JSON snapshot'ta birleştirir. Bu yerel MVP için basittir ve
`BEGIN IMMEDIATE` lost-update'i engeller, ancak karakter taslağı, event history ve
parçalı sorgular büyüdükçe her komutta tüm JSON'u yazmak pahalılaşır.

Önerilen sürümlü migration'lar:

1. `schema_migrations(version, applied_at, checksum)` ekle; mevcut ad-hoc column
   kontrollerini sürümlü hale getir.
2. `campaign_settings`, `sessions`, `session_zero_revisions`,
   `session_zero_consents`, `invites` ekle.
3. `characters`, `character_drafts`, `character_resources`, `inventory_entries`,
   `character_spells`, `spell_slots`, `character_conditions` ekle; mevcut JSON
   characters verisini idempotent backfill et.
4. `encounters`, `encounter_entries`, `encounter_conditions`, `turn_history` ekle;
   aktif JSON encounter'ı backfill et.
5. `events` tablosuna `session_id`, `correlation_id`, `client_action_id`,
   `schema_version`; `(game_id, client_action_id)` unique partial index ekle.
6. Token'ı plaintext saklamak yerine `auth_sessions` ve hashlenmiş token; expiry,
   revoked_at, last_used_at ekle.

Her migration yeni ve önceki şemadan test edilmeli; backup ve rollback prosedürü
belgelenmelidir. SQLite'ta tablo yeniden kurma gereken migration için transaction,
foreign key check ve uygulama sürümü guard'ı kullanılmalıdır.

## 8. API sözleşmesi

### 8.1 Mevcut sözleşmeyi koruyan kısa dönem

Mevcut endpoint'ler:

- `POST /api/games`, `POST /api/games/join`
- `GET /api/snapshot`
- `POST /api/commands`
- `POST /api/rules`
- `POST /api/ai-dm/step`
- `WS /ws/games/{game_id}?token=...`

Hızlı MVP'de `POST /api/commands` geriye uyumlu kalabilir. Fakat büyüyen
command-specific `dict` doğrulaması yerine typed request modelleri ve domain
router'ları tercih edilmelidir.

### 8.2 Önerilen REST endpoint'leri

**Kampanya/üyelik**

- `GET /api/campaigns`
- `POST /api/campaigns`
- `GET/PATCH /api/campaigns/{campaign_id}`
- `POST /api/campaigns/{id}/invites`
- `DELETE /api/campaigns/{id}/invites/{invite_id}`
- `POST /api/invites/{token}/join`
- `GET/PATCH /api/campaigns/{id}/members/{member_id}`
- `GET/PUT /api/campaigns/{id}/session-zero`
- `POST /api/campaigns/{id}/session-zero/consent`

**Karakter**

- `GET/POST /api/campaigns/{id}/characters`
- `GET/PATCH /api/characters/{character_id}`
- `POST /api/characters/{id}/draft`
- `POST /api/characters/{id}/finalize`
- `POST /api/characters/{id}/resources/{resource_id}/spend`
- `POST /api/characters/{id}/rest`
- `POST /api/characters/{id}/inventory`
- `PATCH/DELETE /api/characters/{id}/inventory/{entry_id}`
- `POST /api/characters/{id}/advancements`
- `POST /api/characters/{id}/advancements/{advancement_id}/apply`

**Oturum/encounter**

- `GET/POST /api/campaigns/{id}/sessions`
- `POST /api/sessions/{id}/start`, `/end`
- `GET/POST /api/sessions/{id}/encounters`
- `PATCH /api/encounters/{id}`
- `POST /api/encounters/{id}/entries`
- `POST /api/encounters/{id}/start|pause|resume|next-turn|undo|complete`
- `POST /api/encounters/{id}/entries/{entry_id}/damage|heal|conditions`
- `GET /api/campaigns/{id}/events?after_id=&limit=`
- `GET/PATCH /api/sessions/{id}/summary`
- `POST /api/sessions/{id}/summary/publish`

**Zar/kural**

- `POST /api/rolls` — typed, idempotent, visibility-aware.
- `POST /api/rules` — mevcut; provider unavailable için `503` ve yapılandırma ipucu.
- `GET /api/rules/health` — core API health'ten ayrı optional dependency durumu.

State-changing endpoint'lerde `Idempotency-Key` veya `client_action_id` zorunlu,
versioned kaynaklarda `If-Match`/`expected_revision` önerilir. Hata gövdesi
`{code, message, field_errors, request_id, retry_after}` biçimine standardize edilmelidir.

### 8.3 WebSocket protokolü

Token'ın URL query'sinde taşınması erişim loglarına sızabilir. Public sürümde kısa
ömürlü WebSocket ticket'ı veya secure HttpOnly cookie + origin/CSRF stratejisi kullanın.

İstemci mesajları:

```json
{"kind":"hello","last_event_id":421,"snapshot_revision":18}
{"kind":"ping","sent_at":"..."}
```

Sunucu mesajları:

```json
{"kind":"snapshot","revision":19,"snapshot":{}}
{"kind":"event","event":{"id":422,"type":"dice_rolled"}}
{"kind":"presence","member_id":"...","online":true}
{"kind":"resync_required","reason":"history_gap"}
{"kind":"error","code":"RATE_LIMITED","request_id":"..."}
```

WebSocket yalnız dağıtım kanalıdır; authoritative mutation REST/command engine'den
geçmeye devam eder. Event order campaign-scoped monotonik id ile belirlenir.

## 9. Authorization, görünürlük ve redaction

Rol matrisi:

| İşlem/veri | Owner DM | Aktif DM | Co-DM izleyici | Oyuncu |
|---|---:|---:|---:|---:|
| Kampanya güvenlik/üyelik | yaz | sınırlı | hayır | hayır |
| Sahne/encounter mutation | aktifse | yaz | aktifse yaz | talep/roll |
| Tüm karakter kağıdı | yaz/oku | yaz/oku | oku | yalnız kendi |
| Monster stat/HP | gör | gör | gör | redacted |
| DM özel not/event | gör | gör | gör (politika seçimi) | görmez |
| Oyuncu özel not | politika ile | aktif DM | hayır varsayılan | yalnız sahibi |
| Level-up apply | onay | onay | aktifse onay | taslak |

Mevcut `snapshot()` oyuncular için hidden combatant'ı kaldırır ve monster HP'sini
siler; bu invariant korunmalıdır. Yeni alanlar için allow-list DTO kullanmak,
`dict.pop` redaction'dan güvenlidir. Özellikle şunlar sızmamalıdır:

- monster stat block, gizli initiative ve private roll;
- DM notes, upcoming scene/encounter ve AI planı;
- başka oyuncunun private note, draft ve kaynak ayrıntısı;
- invite token, auth token ve moderation/audit metadata.

Event görünürlükleri mevcut `public`, `party`, `dm_only`, `player:<member_id>` ile
uyumludur. `public` internet-public anlamına gelmemeli; bugün `party` ile semantik
çakışması giderilmeli. Öneri: `campaign`, `dm`, `member`, `system` scope ve
`recipient_id`.

## 10. Realtime, offline ve reconnect

Mevcut `ConnectionManager` process-local'dir. Tek Uvicorn worker/LAN için uygundur;
çoklu worker'da presence ve DM grace bölünür. Aşamalar:

1. MVP: tek worker'ı açıkça enforce et; heartbeat, exponential backoff + jitter,
   `last_event_id`, full snapshot fallback ekle.
2. Ölçek: Redis pub/sub veya streams, shared presence TTL, distributed lock ve durable
   grace job.
3. Gelişmiş: event cursor/history API; snapshot revision ve compact delta.

Offline politika:

- Son snapshot IndexedDB'de yalnız görüntüleme amacıyla tutulabilir.
- Authoritative komut, roll ve HP değişikliği offline kuyruğa alınmamalıdır; geri
  geldiğinde geçmiş anda atılmış zarın yayınlanması güveni bozar.
- Karakter biyografi/not taslakları local draft olarak saklanabilir ve revision check
  ile merge edilebilir.
- Reconnect sırasında UI açıkça salt-okunur olur; "Canlı" etiketi yalnız WS heartbeat
  doğrulanınca gösterilir.
- Zar HTTP ack'i geldi ama WS event'i gelmediyse HTTP event id ekrana bir kez
  gösterilir; reconnect'te aynı id dedupe edilir.

## 11. Backend servis sınırları

`api/app.py` daha fazla domain davranışı taşımamalıdır. Önerilen paketler:

```text
api/
  routers/{campaigns,characters,sessions,encounters,rolls,rules}.py
  schemas/{campaign,character,encounter,roll}.py
domain/
  campaigns/service.py
  characters/{service,calculations,advancement}.py
  encounters/service.py
  dice/service.py
  authorization/policy.py
persistence/
  migrations/
  repositories/{campaigns,characters,encounters,events}.py
realtime/
  gateway.py
```

Mevcut `GameEngine` geçiş döneminde facade kalır. Her mutation şu sırayı izler:

`authenticate -> authorize -> validate typed input -> BEGIN IMMEDIATE -> load/revision
check -> domain transition -> persist -> event/outbox -> commit -> broadcast`.

SQLite commit ile WebSocket broadcast arasında process çökmesi event'i client'a
ulaştırmayabilir. Event kaydı aynı transaction'da olduğu için reconnect cursor bunu
telafi eder. Çoklu süreçte transactional outbox + publisher gerekir.

RAG çağrısı async route event loop'unu bloke etmemeli; threadpool/job ve
configuration-keyed engine cache kullanılmalıdır. Ollama modeli otomatik indirilmez ve
`OLLAMA_LLM_MODEL` kullanıcı seçimidir. RAG yokluğu campaign/character/dice akışını
bozmamalıdır.

## 12. Karakter, spell, inventory, condition ve encounter kuralları

### Karakter kağıdı

MVP sekmeleri: Overview, Actions, Spells, Inventory, Features/Notes. Üst bantta HP,
AC, speed, proficiency bonus, inspiration ve six saves. Her rollable değer aynı global
roll service'e gider. Client hesaplaması yalnız sunum/cache; server snapshot hesaplanan
değerleri verir.

### Spells

Spell katalog kaydı ile karakter seçimi ayrılmalıdır. `prepared`, `known`,
`always_prepared`, `uses`, material/cost ve concentration ayrı alanlardır. Slot harcama
atomik ve idempotent olmalı; cantrip slot tüketmez. Upcast UI önce slot seviyesini
seçtirir. SRD dışı spell metni hak/provenance olmadan depolanmamalıdır.

### Inventory

String listesi olan mevcut `inventory` quantity, equipped, attuned ve custom/catalog
ayrımını taşıyamaz. Inventory entry kimlikli olmalı; aynı adlı iki eşyanın silinmesi
yanlış satırı etkilememelidir. Currency ve container Faz 2'de.

### Conditions

Mevcut string array gösterim için yeterli, lifecycle için yetersizdir. Condition kaydı
kaynak, süre, başlangıç round/turn ve visibility taşımalıdır. Tur ilerletme expired
condition önerisi üretir; DM onayı olmadan karmaşık condition etkilerini otomatik
uygulamak risklidir.

### Encounter

Initiative tie için stabil tiebreaker, undo ve pause/resume gereklidir. `turn_index`
combatant silinince yanlış aktöre kayabilir; cursor entry id + revision daha güvenlidir.
Monster HP oyuncuya redacted kalır; oyuncuya "healthy/bloodied" gibi eşik göstermek
ayrı campaign ayarı olabilir. Manual entries lair action ve çevresel hatırlatıcıları
destekler.

## 13. Observability, rate limit ve güvenlik

### Gözlemlenebilirlik

- Her HTTP/WS bağlantısına `request_id`, her mutation'a `correlation_id` ve
  `client_action_id`.
- Structured log: route, status, latency, game hash, member hash, command type;
  token, prompt, private note ve raw state yok.
- Metrikler: active WS, reconnect oranı, command latency/error/conflict, SQLite lock
  süresi, roll/sec, rule latency/provider error, snapshot byte, event lag.
- Audit: role/invite/settings/level-up/DM-handover değişiklikleri; retention/export.
- Health: liveness, DB readiness ve ayrı optional RAG readiness.

### Rate limit

Mevcut `api/rate_limit.py` process-local sliding window create/join/snapshot/command/
rules/AI/WS'yi korur. İyileştirme:

- IP + account/member + campaign bileşik anahtar.
- Join/invite için daha sert limit ve başarısız deneme backoff.
- Roll için kısa burst (ör. 10/5 sn) + dakika kotası; animasyon spam'ini de client'ta
  sırala.
- Rule/AI için pahalı işlem concurrency limiti ve queue timeout.
- 429'da standart `Retry-After`; UI geri sayım.
- Multi-worker/public sürümde Redis tabanlı atomik kota.

### Güvenlik

- TLS zorunlu; token query string kaldırılır.
- Kısa ömürlü access + rotation/revocation veya secure session cookie.
- Token'lar DB'de hashli; invite ayrı hashli ve süreli.
- Origin allow-list; WS Origin kontrolü; CORS wildcard yok.
- Pydantic typed payload, string/array/quantity sınırları ve content-size limiti.
- HTML/Markdown sanitize; public note ve AI çıktısında XSS önleme.
- CSRF (cookie auth varsa), login/join brute force, dependency scanning ve backup
  encryption.
- RAG prompt'una tüm multiplayer state'i basmak yerine role-redacted, boyut sınırlı
  context; private veri/DM secret'ın model sağlayıcısına sızmasını önle.
- AI planı veri kalır; `GameEngine` doğrular. Destructive/secret-revealing komutlar
  auto-apply allow-list'inde olmamalıdır.

## 14. Build vs buy ve trade-off'lar

| Yetenek | Öneri | Gerekçe / trade-off |
|---|---|---|
| Ses/video | Buy/link (Discord vb.) | WebRTC/TURN, moderasyon ve kalite yükü çekirdek değeri geciktirir |
| Dice RNG | Build (mevcut) | `dice.py` küçük, denetlenebilir ve authoritative; animasyon ayrı katman |
| 3B dice physics | Önce hafif build, sonra kütüphane değerlendir | Bundle/GPU/lisans maliyeti; sonuçtan ayrıştırılmalı |
| Character calculations | SRD kapsamını build | Güvenilirlik önemli; tam ticari compendium'u kopyalama |
| Rules search | Mevcut RAG | Kaynaklı cevap avantajı; Ollama/Qdrant optional ve yüksek latency |
| Maps/VTT | Faz 3 build veya entegrasyon | En pahalı yüzey; theater-of-mind MVP'yi bloke etmemeli |
| Realtime fan-out | MVP process-local, ölçeklemede Redis | Yerel basitlik vs çoklu worker doğruluğu |
| Persistence | MVP SQLite, büyümede Postgres | Taşınabilirlik vs concurrency, query ve migration kapasitesi |
| Object/media storage | Faz 3 S3-compatible | Harita upload'u SQLite/blob içine konmamalı |
| Auth | Local MVP mevcut token; public'te olgun auth/session | Mevcut yaklaşım hızlı ama expiry/revocation yok |

SQLite JSON snapshot'ı hemen tamamen sökmek de yanlış olabilir. Önce screen/API
acceptance'ı doğrulayın; karakter ve encounter gibi çatışma/arama ihtiyacı yüksek
aggregate'leri normalize edin. Küçük campaign settings JSON kalabilir.

## 15. Fazlı roadmap

### Faz 0 — Sözleşme ve güven temeli

- Versioned migration altyapısı.
- Typed errors, request/correlation id, auth token iyileştirme planı.
- Snapshot revision, event cursor ve reconnect protokolü.
- Özgün design tokens; D&D Beyond marka varlıklarından bağımsız görsel dil.

**Kabul:** Eski DB migration sonrası açılır; rol redaction regression testleri geçer;
aynı `client_action_id` iki kez state değiştirmez; disconnect/reconnect event kaybetmez.

### Faz 1 — Sekiz ekranlık MVP

- Kampanya/session zero/lobi.
- Minimal guided character builder ve detaylı sheet.
- Mevcut player/DM console'un canlı masa + encounter ekranlarına ayrılması.
- Sağ alt zar FAB/tepsi, advantage/disadvantage, visibility ve accessible animasyon.
- Oturum başlat/bitir ve manuel özet.

**Kabul:**

- Yeni DM kampanya oluşturup session zero yayınlar, iki oyuncu katılır ve karakter
  tamamlar.
- DM oturum/encounter başlatır; oyuncu sheet/FAB üzerinden zar atar; yetkili herkes
  aynı sonucu bir kez görür.
- Advantage `2d20kh1`, disadvantage `2d20kl1`; modifier doğru; reduced motion çalışır.
- Oyuncu hidden monster veya HP/stat/DM note göremez.
- RAG/Ollama kapalıyken tüm oyun akışı çalışır, Rule Drawer anlaşılır 503 gösterir.
- Refresh/reconnect sonrası karakter, tur ve event sırası korunur.

### Faz 2 — Oyun derinliği

- Spell/slot/rest/resource, identity-based inventory, duration condition.
- Encounter builder/library, pause/resume/undo.
- Advancement/level-up draft + DM approval.
- Oturum özeti, loot/quest ve campaign notes.

**Kabul:** Long rest tanımlı kaynakları atomik resetler; aynı inventory satırı güvenli
güncellenir; encounter resume aynı cursor/round'u açar; level-up yarıda kalırsa canlı
sheet değişmez, apply tek transaction'dır.

### Faz 3 — VTT ve public ölçek

- Map upload, token, grid/scale, fog, ruler/ping; encounter modeline referans.
- Redis presence/rate limit/pubsub, Postgres değerlendirmesi, outbox worker.
- Object storage, malware/type/size doğrulaması.
- Public auth, TLS, retention/export/delete ve güvenlik incelemesi.

**Kabul:** İki worker'da aynı event order/presence; DM fog dışındaki alanı açmadan
oyuncu göremez; 100 eşzamanlı socket hedef SLO'yu karşılar; restart grace/handover
durumunu kaybetmez.

## 16. Risk kaydı

| Risk | Olasılık/etki | Azaltım |
|---|---|---|
| Kapsamın tam VTT'ye taşması | Yüksek/yüksek | Sekiz ekran MVP, map Faz 3 |
| D&D içeriği/IP ihlali | Orta/çok yüksek | SRD/provenance, hukuk incelemesi, özgün marka/UI |
| Character rules motorunun istisnalarda yanlış hesaplaması | Yüksek/yüksek | SRD test fixtures, override provenance, DM nihai otorite |
| JSON state büyümesi/lost update | Orta/yüksek | aggregate tablolar, revision, transaction, concurrency test |
| WS event kaçırma/çift animasyon | Yüksek/orta | cursor, idempotency, dedupe, resync |
| Gizli DM/monster verisinin sızması | Orta/çok yüksek | allow-list DTO, rol test matrisi, event redaction |
| Zar animasyonu sonucu geciktirir veya erişilemez olur | Orta/orta | server result, kısa/cancel edilebilir motion, reduced-motion |
| Optional RAG canlı oyunu bloke eder | Orta/yüksek | ayrı health, timeout/cache/threadpool; core'dan izolasyon |
| Process-local rate/presence ölçeklenir sanılır | Yüksek/yüksek | tek-worker guard; Faz 3 Redis |
| Token/invite çalınması | Orta/yüksek | TLS, hash, expiry, revocation, query token kaldırma |
| AI uygunsuz/secret-revealing davranış | Orta/yüksek | opt-in, allow-list, DM approval, redacted context/audit |
| Çok yoğun masaüstü UI oyun sohbetini bastırır | Yüksek/orta | progressive disclosure, tek FAB, rol bazlı yüzey, UX test |

## 17. Test stratejisi

- **Domain unit:** dice parser ve modes; ability/AC/proficiency; spell slots/rest;
  condition expiry; initiative tie/undo.
- **Store/migration:** boş DB ve her önceki şemadan migration; rollback; foreign key;
  concurrent writers.
- **API/auth:** her endpoint için rol matrisi, IDOR, hidden/redacted fields, invalid
  bounds, 409/429/idempotency.
- **Realtime:** disconnect sırasında event, reconnect cursor, duplicate delivery,
  logout/unmount reconnect cancellation, DM grace/handover.
- **Frontend:** keyboard-only FAB, screen reader labels/focus, reduced-motion, empty/
  loading/error/offline, responsive 1280×720 ve 1920×1080.
- **E2E:** DM create → iki player join → character ready → session start → encounter →
  advantage roll → damage approval → reconnect → session summary.
- **Security:** XSS in scene/notes/AI, token/log leakage, invite brute force, oversized
  payload, dependency and upload scan.
- **RAG:** yalnız ilgili kaynak servisleri hazırken mevcut router/retrieval evaluation;
  optional servis yokluğunu ayrı contract testi.

## 18. Doğrulanmış bulgu ile öneri/çıkarım ayrımı

**Doğrulanmış:** D&D Beyond resmi sayfaları character builder/sheet, otomatik hesaplama,
HP/spell/inventory, 3B zar, campaign hub/notlar, Game Log, encounter tracker ve Maps
özelliklerini açıklar. Resmi çevrim içi oyun rehberi sesli/video iletişim ile dijital
araç/VTT ayrımını yapar. SRD 5.2 advantage/disadvantage davranışını tanımlar.

**Bu raporun önerisi/çıkarımı:** Sekiz MVP ekranı, proposed endpoint ve tablolar,
session state machine, typed roll contract, Redis/Postgres fazı, ekran durumları ve
acceptance kriterleri Tetsu'nun mevcut kodu ile araştırılan davranışlardan türetilen
tasarımdır; D&D Beyond'ın kamuya açıklanmış iç mimarisi değildir. D&D Beyond'ın kapalı
API'leri, veri şeması, altyapısı veya ticari entitlement kuralları hakkında varsayım
yapılmamıştır.

## 19. Kaynakça

Kaynaklar 29 Temmuz 2026 tarihinde erişilen resmi/primary sayfalardır.

1. D&D Beyond, [How to Play Dungeons & Dragons Using D&D Beyond](https://www.dndbeyond.com/posts/754-how-to-play-dungeons-dragons-using-d-d-beyond) — karakter oluşturma, campaign, notlar, entegre dice ve encounter özeti.
2. D&D Beyond, [New Player's Guide: How to Play D&D Online](https://www.dndbeyond.com/posts/750-new-players-guide-how-to-play-d-d-online) — internet, ses/video, karakter aracı, Game Log ve VTT'nin rolü.
3. D&D Beyond, [The Official D&D Character Builder and Digital Character Sheet](https://www.dndbeyond.com/en/players) — HP, spells, features, inventory, otomatik hasar/iyileşme, 3B dice ve cross-device sync.
4. D&D Beyond, [Share Your Dice Results with the Brand New Game Log](https://www.dndbeyond.com/posts/939-share-your-dice-results-with-the-brand-new-game) — gerçek zamanlı roll, modifier, dice visual ve Game Log yüzeyleri.
5. D&D Beyond, [Tutorial: How to Build Encounters and Run Them](https://www.dndbeyond.com/posts/1135-tutorial-how-to-build-encounters-and-run-them-on-d) — saved encounter, initiative, HP, manual entry, turn/round, undo ve resume.
6. D&D Beyond, [D&D Beyond Maps: How to Start Playing Today](https://www.dndbeyond.com/posts/1570-d-d-beyond-maps-how-to-start-playing-today) — browser VTT, map/token, fog, ruler/ping/draw, Game Log ve combat.
7. D&D Beyond, [Roll for Initiative! Combat Tracking Comes to the Maps VTT](https://www.dndbeyond.com/posts/1841-roll-for-initiative-combat-tracking-comes-to-the) — Maps/encounter combat entegrasyonu.
8. Wizards of the Coast, [System Reference Document 5.2 (PDF)](https://media.dndbeyond.com/compendium-images/srd/5.2/SRD_CC_v5.2.pdf) — d20 test, advantage/disadvantage ve açık lisanslı kurallar için primary referans.
9. Wizards of the Coast, [Fan Content Policy](https://company.wizards.com/en/legal/fancontentpolicy) — marka, logo, IP ve fan content sınırları. Bu rapor hukuki görüş değildir; ticari yayın öncesi lisans/hukuk incelemesi gerekir.
