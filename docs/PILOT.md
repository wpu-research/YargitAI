# Pilot ve sunucu planı

## 1. Pilot erişimi

- Giriş davet koduyla: `YARGITAI_DAVETLER="kod1:Av. Ad Soyad,kod2:..."` ve `YARGITAI_GIZLI=<uzun rastgele dize>`.
  Davetler tanımlı değilse giriş kapalıdır (yerel geliştirme).
- Her arama ve soru `sorgu_log` tablosuna yazılır (kullanıcı, uç, sorgu, sonuç sayısı, süre).
  Dilekçe metinleri müvekkil bilgisi içerebileceği için **kaydedilmez**, yalnız uzunluğu yazılır.
- Sonuçlardaki "İşe yaradı / İlgisiz" düğmeleri `geri_bildirim` tablosuna gider. Bu işaretler
  avukat ölçüm setine eklenir (`eval/avukat_inceleme.csv` ile aynı amaç).
- Pilot sözleşmesine eklenmesi gerekenler: sorguların ürün geliştirme için kaydedildiği (KVKK aydınlatma),
  çıktının hukuki mütalaa olmadığı, müvekkil kimliğini sorguya yazmama ricası.

## 2. Pilot süreci (önerilen, 4 hafta)

| Hafta | İş |
|---|---|
| 0 | 3–5 iş hukukçusu avukat; 30 dk tanıtım, davet kodu |
| 1–3 | Gerçek dosyalarda kullanım; haftalık 20 dk görüşme. `sorgu_log` ve `geri_bildirim` haftalık incelenir |
| 2 | Avukatlar `eval/avukat_inceleme.csv` dosyasını işaretler (40 soru × 5 sonuç ≈ 1,5 saat) |
| 4 | Sonuç: isabet oranı, hız, en çok istenen özellik, ödeme isteği (fiyat görüşmesi) |

## 3. Sunucu

Bu makinede ~7 GB boş disk kaldı; 2015–2020 kararları (≈3,5 M karar, ≈20 GB) buraya sığmıyor.
Sunucu ihtiyacı:

| Bileşen | Boyut |
|---|---|
| Karar DB (2015–2023, FTS dahil) | ≈ 27 GB |
| Vektörler (iş hukuku, 121 bin karar) | 1 GB; tüm Yargıtay için ≈ 8 GB |
| bge-m3 modeli | 2,3 GB |
| RAM | ≥ 8 GB (vektör indeksi + SQLite önbelleği) |

Seçenekler:
- **Railway** (mevcut altyapı): bu repo `Dockerfile` ile çalışır; `/data` kalıcı volume'a bağlanır.
  Volume boyutu ve fiyatı planına bağlı; 40 GB+ gerekli.
- **VPS (ör. 4 vCPU / 16 GB / 160 GB disk)**: aynı Docker imajı; büyük veri için genelde daha ucuz.

Kurulum sırası: imajı deploy et → sunucu kabuğunda `scripts/sunucu_veri.sh` (HF'den akış, veri sunucuya
hiç bu makineden geçmez) → bu makinede GPU ile üretilen `data/vectors.db` dosyasını `/data/`'ya kopyala →
`YARGITAI_DAVETLER`, `YARGITAI_GIZLI`, `ANTHROPIC_API_KEY` ortam değişkenlerini tanımla.

Bedesten crawler'ı (`python -m yargitai.crawl.run --since 2024-01-01 --watch`) sunucuda ayrı bir süreç
olarak çalışmalı; resmî servis açıldığında 2024 sonrası kararları ve eksik HGK kararlarını getirecek.
