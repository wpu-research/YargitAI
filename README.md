# YargitAI

Avukatlar için emsal karar arama ve içtihat analizi platformu (prototip).
Her cevap gerçek bir künyeye dayanır, cevaptaki her künye veritabanında doğrulanır.

## Bileşenler

| Modül | İş |
|---|---|
| `yargitai/ingest_hf.py` | HF `altaidevorg/yargitay-karar-data-raw` setini **akış hâlinde** içe alır (ham veri diske yazılmaz, kaldığı bayttan devam eder) |
| `yargitai/crawl/` | Adalet Bakanlığı Bedesten API istemcisi (`bedesten.adalet.gov.tr/emsal-karar`). `--watch` ile servis kapalıysa 15 dk'da bir yoklar |
| `yargitai/parse.py` | HTML → metin, dava türü, mahkeme, sonuç (onama/bozma/...) |
| `yargitai/extract.py` | Deterministik atıf çıkarımı: karar→karar (daire, HGK, CGK, İBK) ve karar→kanun maddesi |
| `yargitai/db.py` | SQLite: zstd sıkıştırılmış metin + contentless FTS5 + atıf tabloları |
| `yargitai/graph.py` | Atıfları hedef karara bağlar, atıf alan/yapan sorguları, madde istatistiği |
| `yargitai/embed.py` | bge-m3 (GPU) ile dikey bazlı vektörleme (ilk dikey: iş hukuku) |
| `yargitai/search.py` | Hibrit arama: Türkçe kök + önek BM25 ve vektör, RRF birleştirme, kurul/atıf otoritesi |
| `yargitai/answer.py` | Claude ile atıf doğrulamalı cevap; anahtar yoksa çıkarımsal (alıntı) mod |
| `yargitai/api.py`, `web/` | FastAPI + tek sayfalık arayüz |

## Çalıştırma

```bash
python3 -m venv --system-site-packages .venv && .venv/bin/pip install -r requirements.txt

.venv/bin/python -m yargitai.ingest_hf 2023 2022 2021        # açık veri
.venv/bin/python -m yargitai.crawl.run --since 2024-01-01 --watch   # resmî kaynak (2024+)
.venv/bin/python -m yargitai.graph resolve                     # atıf grafiği
.venv/bin/python -m yargitai.embed --dikey is_hukuku           # vektörler
.venv/bin/python -m uvicorn yargitai.api:app --port 8765       # http://localhost:8765
.venv/bin/python -m pytest -q tests
```

`scripts/gece_hatti.sh`: ingest bitince graf + embedding adımlarını sırayla çalıştırır.

LLM cevapları için `ANTHROPIC_API_KEY` (veya `ant auth login`) gerekir; model `YARGITAI_MODEL` ile değişir
(varsayılan `claude-opus-5-5`). Anahtar yoksa `/api/sor` kararlardan doğrudan pasaj döndürür.

## Kaynak notları (19.10.2026)

- karararama.yargitay.gov.tr, karararama.danistay.gov.tr ve emsal.uyap.gov.tr aynı altyapıyı kullanıyor:
  `POST /aramalist`, `/aramadetaylist`, `GET /getDokuman?id=`. reCAPTCHA tetiklenebiliyor.
- Üçü de mevzuat.adalet.gov.tr'nin arka ucu olan Bedesten'deki `emsal-karar` servisine bağlı.
  Kurulum gecesi bu servis kapalıydı (gateway: `emsal-karar-service 404 NOT_FOUND`), mevzuat servisi
  çalışıyordu. Crawler bu yüzden bekleme modunda başlatıldı.
- Disk kısıtı (~20 GB boş) nedeniyle ilk faz 2021–2023 (~1,08 M karar, ~6,5 GB).

## Yasal

Kararlar FSEK m.31 kapsamında serbestçe çoğaltılabilir. Kaynak anonimleştirmesi eksik olabilir (KVKK);
yayına almadan önce ikinci anonimleştirme katmanı eklenmeli. Ürün avukatlara araştırma aracıdır, hukuki mütalaa değildir.
