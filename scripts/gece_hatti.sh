#!/usr/bin/env bash
# Gece hattı: HF ingest bitince atıf grafiğini çöz, ardından iş hukuku dikeyini vektörle.
# Her adım idempotent; yarıda kalırsa yeniden çalıştırmak güvenli.
set -u
cd "$(dirname "$0")/.."
PY=.venv/bin/python
log() { echo "$(date '+%F %T') $*" >> logs/gece_hatti.log; }

log "başladı; ingest bekleniyor"
while pgrep -f "yargitai.ingest_hf" >/dev/null; do sleep 60; done
log "ingest bitti; atıf grafiği çözülüyor"
$PY -m yargitai.graph resolve >> logs/graph.log 2>&1
log "graf tamam; embedding modeli bekleniyor"
until grep -q '^OK' logs/model_download.log 2>/dev/null; do sleep 60; done
log "iş hukuku dikeyi vektörleniyor"
$PY -m yargitai.embed --dikey is_hukuku >> logs/embed.log 2>&1
log "hat tamam (çıkış kodu $?)"
