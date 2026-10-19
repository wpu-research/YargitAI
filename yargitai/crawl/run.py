"""Resmî kaynaktan yeni kararları çeken, servis kapalıysa bekleyen süreç.

    python -m yargitai.crawl.run --since 2024-01-01            # tek seferlik
    python -m yargitai.crawl.run --since 2024-01-01 --watch    # servis açılana kadar bekle, sonra çek

Gün gün geriye doğru tarar; her gün tamamlandığında ingest_durum'a yazar ve
yeniden başlatılınca tamamlanan günleri atlar.
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import date

from ..db import connect, get_state, set_state
from ..parse import html_to_text
from ..store import upsert
from .bedesten import Bedesten, ServiceDown, UnexpectedShape, days

log = logging.getLogger("crawl")
KAYNAK = "bedesten"


def crawl_day(api: Bedesten, con, day: date, item_type: str) -> int:
    added = 0
    page = 1
    while True:
        hits, total = api.search(item_type=item_type, start=day, end=day, page=page, page_size=100)
        for h in hits:
            if con.execute("SELECT 1 FROM karar WHERE kaynak=? AND kaynak_id=?",
                           (KAYNAK, h.document_id)).fetchone():
                continue
            mime, body = api.document(h.document_id)
            if "html" not in mime:
                log.info("HTML olmayan belge atlandı: %s (%s)", h.document_id, mime)
                continue
            if upsert(con, kaynak=KAYNAK, kaynak_id=h.document_id, daire=h.daire,
                      esas_no=h.esas_no, karar_no=h.karar_no, karar_tarihi_tr=h.karar_tarihi,
                      text=html_to_text(body),
                      mahkeme="Danıştay" if item_type == "DANISTAYKARAR" else "Yargıtay"):
                added += 1
        con.commit()
        if page * 100 >= total or not hits:
            return added
        page += 1


def run(since: date, until: date, item_type: str) -> None:
    api = Bedesten()
    con = connect()
    for day in days(since, until):
        key = f"bedesten:{item_type}:{day.isoformat()}"
        if get_state(con, key) == "done" and day < date.today():
            continue
        n = crawl_day(api, con, day, item_type)
        set_state(con, key, "done")
        con.commit()
        log.info("%s %s: %d yeni karar", item_type, day, n)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=date.fromisoformat, default=date(2024, 1, 1))
    ap.add_argument("--until", type=date.fromisoformat, default=date.today())
    ap.add_argument("--item-type", default="YARGITAYKARARI")
    ap.add_argument("--watch", action="store_true", help="servis kapalıysa 15 dk'da bir yokla")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    while True:
        try:
            run(args.since, args.until, args.item_type)
            return
        except ServiceDown as e:
            if not args.watch:
                raise SystemExit(f"Servis kapalı: {e}")
            log.warning("Servis kapalı (%s); 15 dk sonra tekrar denenecek", e)
            time.sleep(900)
        except UnexpectedShape as e:
            # API şekli değişmiş: yanlış veri yazmaktansa dur ve ham yanıtı bırak.
            log.error("Beklenmeyen yanıt şekli, durduruldu. Ham yanıt: %s", e)
            raise SystemExit(2)


if __name__ == "__main__":
    main()
