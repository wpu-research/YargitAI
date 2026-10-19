"""Hugging Face'teki altaidevorg/yargitay-karar-data-raw setini akış hâlinde içe alır.

Ham JSONL diske hiç yazılmaz: satırlar HTTP akışından okunup doğrudan
ayrıştırılır. Kaldığı bayt ofseti saklandığı için kesilirse kaldığı yerden
(HTTP Range ile) devam eder.

    python -m yargitai.ingest_hf 2023 2022 2021
"""
from __future__ import annotations

import argparse
import logging
import shutil
import time

import httpx
import orjson

from . import parse
from .db import DB_PATH, connect, get_state, set_state
from .store import upsert

URL = "https://huggingface.co/datasets/altaidevorg/yargitay-karar-data-raw/resolve/main/yargitay_data_{year}.jsonl"
KAYNAK = "hf-altaidev"
MIN_FREE_GB = 5.0
COMMIT_EVERY = 2000

log = logging.getLogger("ingest_hf")


def free_gb() -> float:
    return shutil.disk_usage(DB_PATH.parent).free / 1e9


def ingest_year(con, year: int, limit: int | None = None) -> None:
    key = f"hf:{year}:offset"
    if get_state(con, f"hf:{year}:done") == "1":
        log.info("%s zaten tamam", year)
        return
    offset = int(get_state(con, key) or 0)
    added = seen = 0
    t0 = time.time()
    while True:
        try:
            headers = {"Range": f"bytes={offset}-"} if offset else {}
            with httpx.stream("GET", URL.format(year=year), headers=headers,
                              follow_redirects=True, timeout=httpx.Timeout(60, read=120)) as r:
                if r.status_code == 416:  # ofset dosya sonunda
                    break
                r.raise_for_status()
                buf = b""
                for chunk in r.iter_bytes(1 << 20):
                    buf += chunk
                    *lines, buf = buf.split(b"\n")
                    for line in lines:
                        offset += len(line) + 1
                        if not line.strip():
                            continue
                        rec = orjson.loads(line)
                        seen += 1
                        text = parse.html_to_text(rec.get("content") or "")
                        if upsert(con, kaynak=KAYNAK, kaynak_id=str(rec["id"]),
                                  daire=(rec.get("daire") or "").strip() or "Bilinmeyen",
                                  esas_no=rec.get("esasNo"), karar_no=rec.get("kararNo"),
                                  karar_tarihi_tr=rec.get("kararTarihi"), text=text):
                            added += 1
                        if seen % COMMIT_EVERY == 0:
                            set_state(con, key, str(offset))
                            con.commit()
                            rate = seen / (time.time() - t0)
                            log.info("%s: %d okundu, %d eklendi, %.0f/sn, boş disk %.1f GB",
                                     year, seen, added, rate, free_gb())
                            if free_gb() < MIN_FREE_GB:
                                raise SystemExit(f"Disk {MIN_FREE_GB} GB altına indi, durduruldu.")
                            if limit and seen >= limit:
                                return
                # buf'ta kalan son satır (sonda \n yoksa)
                if buf.strip():
                    rec = orjson.loads(buf)
                    text = parse.html_to_text(rec.get("content") or "")
                    upsert(con, kaynak=KAYNAK, kaynak_id=str(rec["id"]),
                           daire=(rec.get("daire") or "").strip() or "Bilinmeyen",
                           esas_no=rec.get("esasNo"), karar_no=rec.get("kararNo"),
                           karar_tarihi_tr=rec.get("kararTarihi"), text=text)
                    offset += len(buf)
            break
        except (httpx.HTTPError, httpx.StreamError) as e:
            con.commit()
            log.warning("%s bağlantı hatası (%s), ofset %d'den 20 sn sonra devam", year, e, offset)
            time.sleep(20)
            # Yarım kalan satır tekrar indirileceği için ofset zaten tam satır sınırında.
    set_state(con, key, str(offset))
    set_state(con, f"hf:{year}:done", "1")
    con.commit()
    log.info("%s bitti: %d okundu, %d eklendi", year, seen, added)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("years", nargs="+", type=int)
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    con = connect()
    for y in args.years:
        ingest_year(con, y, args.limit)


if __name__ == "__main__":
    main()
