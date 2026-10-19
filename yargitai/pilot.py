"""Pilot erişimi: davet koduyla giriş, sorgu günlüğü ve sonuç geri bildirimi.

Davetler ortam değişkeninden okunur:
    YARGITAI_DAVETLER="kod1:Av. Ayşe Yılmaz,kod2:Av. Mehmet Kaya"
    YARGITAI_GIZLI=<oturum çerezini imzalayan rastgele dize>
YARGITAI_DAVETLER tanımlı değilse giriş kapalıdır (yerel geliştirme).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
import time

COOKIE = "yargitai_oturum"
SCHEMA = """
CREATE TABLE IF NOT EXISTS sorgu_log (
    zaman TEXT NOT NULL DEFAULT (datetime('now')),
    kullanici TEXT, uc TEXT, sorgu TEXT, sonuc_sayisi INTEGER, sure_ms INTEGER
);
CREATE TABLE IF NOT EXISTS geri_bildirim (
    zaman TEXT NOT NULL DEFAULT (datetime('now')),
    kullanici TEXT, sorgu TEXT, rid INTEGER, puan INTEGER, not_ TEXT
);
"""


def davetler() -> dict[str, str]:
    raw = os.environ.get("YARGITAI_DAVETLER", "")
    out = {}
    for parca in raw.split(","):
        if ":" in parca:
            kod, ad = parca.split(":", 1)
            out[kod.strip()] = ad.strip()
    return out


def acik() -> bool:
    return bool(davetler())


def _gizli() -> bytes:
    g = os.environ.get("YARGITAI_GIZLI")
    if acik() and not g:
        raise RuntimeError("YARGITAI_DAVETLER tanımlıyken YARGITAI_GIZLI de tanımlanmalı")
    return (g or "yerel").encode()


def imzala(kod: str) -> str:
    return kod + "." + hmac.new(_gizli(), kod.encode(), hashlib.sha256).hexdigest()[:32]


def dogrula(cerez: str | None) -> str | None:
    """Geçerli çerez için kullanıcının adını döner."""
    if not cerez or "." not in cerez:
        return None
    kod, _ = cerez.rsplit(".", 1)
    if not hmac.compare_digest(imzala(kod), cerez):
        return None
    return davetler().get(kod)


def kaydet_sorgu(con: sqlite3.Connection, kullanici: str | None, uc: str, sorgu: str, n: int, t0: float) -> None:
    con.execute("INSERT INTO sorgu_log(kullanici, uc, sorgu, sonuc_sayisi, sure_ms) VALUES(?,?,?,?,?)",
                (kullanici, uc, sorgu[:2000], n, int(1000 * (time.time() - t0))))
    con.commit()
