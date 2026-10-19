#!/usr/bin/env bash
# Sunucuda veri tabanını sıfırdan kurar (kalıcı disk /data altında olmalı).
# Ham veri diske yazılmaz; HF'den akış hâlinde okunur. 2015–2023 tamamı ≈ 27 GB DB.
set -eu
YILLAR="${YILLAR:-2023 2022 2021 2020 2019 2018 2017 2016 2015}"
python -m yargitai.ingest_hf $YILLAR
python -m yargitai.graph reextract
python -m yargitai.graph resolve
# Vektörler GPU'da üretilip /data/vectors.db olarak kopyalanmalı (CPU'da 121 bin karar saatler sürer).
