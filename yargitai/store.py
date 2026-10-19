"""Tek bir kararı (hangi kaynaktan gelirse gelsin) veritabanına yazar."""
from __future__ import annotations

import sqlite3

from . import extract, parse
from .db import pack
from .textnorm import fold


def upsert(con: sqlite3.Connection, *, kaynak: str, kaynak_id: str, daire: str,
           esas_no: str | None, karar_no: str | None, karar_tarihi_tr: str | None,
           text: str, mahkeme: str = "Yargıtay") -> int | None:
    """Kararı ekler; zaten varsa None döner (idempotent)."""
    exists = con.execute(
        "SELECT 1 FROM karar WHERE kaynak=? AND kaynak_id=?", (kaynak, kaynak_id)
    ).fetchone()
    if exists or not text.strip():
        return None
    # Farklı kaynaktan aynı karar gelirse (aynı daire+E+K) tekrar ekleme.
    if esas_no and karar_no:
        dup = con.execute(
            "SELECT 1 FROM karar WHERE daire=? AND esas_no=? AND karar_no=?",
            (daire, esas_no, karar_no),
        ).fetchone()
        if dup:
            return None
    iso = parse.parse_date(karar_tarihi_tr)
    f = parse.fields(text)
    cur = con.execute(
        """INSERT INTO karar(kaynak, kaynak_id, mahkeme, daire, daire_turu, esas_no,
               karar_no, karar_tarihi, yil, dava_turu, suc, ilk_mahkeme, sonuc,
               uzunluk, metin)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (kaynak, kaynak_id, mahkeme, daire, parse.daire_turu(daire), esas_no, karar_no,
         iso, int(iso[:4]) if iso else None, f["dava_turu"], f["suc"],
         f["ilk_derece"] or f["mahkeme"], parse.sonuc(text), len(text), pack(text)),
    )
    rid = cur.lastrowid
    con.execute("INSERT INTO karar_fts(rowid, metin) VALUES(?, ?)",
                (rid, fold(daire + "\n" + text)))
    atiflar = extract.karar_atiflari(text, daire)
    if atiflar:
        con.executemany(
            "INSERT INTO atif(kaynak_rid, tur, hedef_daire, esas_no, karar_no, tarih) VALUES(?,?,?,?,?,?)",
            [(rid, a.tur, a.daire, a.esas, a.karar, parse.parse_date(a.tarih)) for a in atiflar],
        )
    maddeler = extract.madde_atiflari(text)
    if maddeler:
        con.executemany(
            "INSERT OR IGNORE INTO madde_atif(rid, kanun, madde) VALUES(?,?,?)",
            [(rid, m.kanun, m.madde) for m in maddeler],
        )
    return rid
