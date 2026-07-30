# D&D Campaign ve Session İşleyişi

## Temel model

D&D'nin resmi Basic Rules anlatımında campaign, aynı maceracı grubunun izlediği
birbirine bağlı adventure dizisidir. Adventure bir veya daha fazla session sürer;
session ise masanın tek oyun buluşmasıdır. Kısa bir adventure tek session'lık
`one-shot` olabilir. Uzun campaign tekrar eden NPC'ler, temalar, sonuçları sonraki
session'lara taşınan oyuncu kararları ve uzun vadeli bir çatışma içerir.

Kaynaklar:

- [Playing the Game — Basic Rules 2024](https://www.dndbeyond.com/sources/dnd/br-2024/playing-the-game)
- [How to Run a Session 0](https://www.dndbeyond.com/posts/929-how-to-run-a-session-0-for-your-d-d-game)
- [How to Write a D&D Campaign](https://www.dndbeyond.com/posts/1671-how-to-write-a-d-d-campaign)

## Campaign yaşam döngüsü

1. DM campaign fikrini, başlangıç bölgesini, tonunu ve ilk problemi hazırlar.
2. Session Zero'da grup oyun tonu, combat/roleplay beklentisi, house rule'lar,
   güvenlik sınırları, takvim ve karakter bağlantıları üzerinde anlaşır.
3. Oyuncular campaign ruleset'ine uygun karakterlerini oluşturur ve hazır durumuna
   geçer.
4. DM ilk session'ı planlar. Campaign ilerledikçe yeni quest, NPC, location,
   encounter ve sonuçlar kalıcı ortak bağlama eklenir.
5. Her session sonunda özet ve sonraki adımlar yayınlanır; bir sonraki session bu
   bağlamdan hazırlanır.

Tetsu'daki karşılığı Campaign Hub, lobby/readiness, consent, safety preferences,
house rules, Session Zero agenda ve planlanan tarih alanlarıdır. Karakterini
yayınlamamış oyuncu normal campaign araçlarına girmeden önce zorunlu builder'ı
tamamlar.

## Bir session nasıl oynanır?

Resmi oyun ritmi üç adımı tekrarlar:

1. DM sahneyi ve mevcut durumu tarif eder.
2. Oyuncular karakterlerinin ne yaptığını söyler.
3. DM sonucu kuralla çözer ve sonucu anlatır.

Bu döngü social interaction, exploration ve combat boyunca devam eder. Ability
check, saving throw, attack ve damage gerektiğinde zar sunucuda çözülür. DM'nin
görevi oyunculara karşı kazanmak değil; anlamlı seçimleri, sonuçları ve ortak
hikâyeyi yönetmektir.

Tetsu session akışı:

```text
preparing -> live -> paused -> live -> completed
                  \-----------------> completed
```

- `preparing`: tarih, hazırlık, readiness ve Session Zero kararları kontrol edilir.
- `live`: sahne/encounter yönetilir; oyuncular sheet ve Dice FAB ile aksiyon alır.
  Party/private notlar, questler, loot ve Game Log session'a yazılır.
- `paused`: mevcut encounter ve session bağlamı korunur; oyun mutasyonları durur.
- `completed`: yeni note/loot/quest kabul edilmez. DM özeti yayınlar ve sonraki
  session için açık uçları kaydeder.

## Playtest edge case

**Durum:** Canlı session'da yalnız bir adet `Ember Crown Shard` vardır. Riva ve
Gareth iki ayrı istemciden aynı anda **Talep et** seçer.

**Beklenen davranış:**

- Yalnız bir `claim_session_loot` komutu başarılı olur.
- Diğer komut stale revision veya artık mevcut olmayan claim nedeniyle reddedilir.
- SQLite conditional update, event ve command receipt tek transaction'da kalır.
- Her iki oyuncu ekranı aynı tek claimant'a yakınsar.
- İki ayrı sahip, ikinci event veya hâlâ aktif **Talep et** butonu görünmez.

Bu senaryo `web/tests/campaign-session-edge.spec.ts` içinde üç görünür browser
context'iyle (DM + iki oyuncu) test edilir.
