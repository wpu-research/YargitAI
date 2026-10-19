"""Atıf grafiği: atıfları hedef kararlara bağlar ve graf sorgularını sunar.

    python -m yargitai.graph resolve
"""
from __future__ import annotations

import logging
import sqlite3
import sys

log = logging.getLogger("graph")

# Kurul atıflarında hedef 'daire' alanı DB'deki adla eşleşmeli.
_KURUL_DAIRE = {
    "HGK": ("Hukuk Genel Kurulu",),
    "CGK": ("Ceza Genel Kurulu",),
    "IBK": ("Hukuk Genel Kurulu", "Ceza Genel Kurulu", "Büyük Genel Kurulu",
            "İçtihadı Birleştirme Kurulu", "Hukuk Daireleri Başkanlar Kurulu"),
}


def resolve(con: sqlite3.Connection) -> int:
    """hedef_rid'i boş atıfları (daire, esas, karar) üzerinden çözer."""
    n = 0
    rows = con.execute(
        "SELECT rowid, tur, hedef_daire, esas_no, karar_no FROM atif WHERE hedef_rid IS NULL"
    ).fetchall()
    for rowid, tur, daire, esas, karar in rows:
        if tur == "daire" and daire:
            r = con.execute("SELECT rid FROM karar WHERE esas_no=? AND karar_no=? AND daire=?",
                            (esas, karar, daire)).fetchone()
        else:
            cands = _KURUL_DAIRE.get(tur, ())
            r = None
            if cands:
                q = f"SELECT rid FROM karar WHERE esas_no=? AND karar_no=? AND daire IN ({','.join('?' * len(cands))})"
                r = con.execute(q, (esas, karar, *cands)).fetchone()
        if r:
            con.execute("UPDATE atif SET hedef_rid=? WHERE rowid=?", (r[0], rowid))
            n += 1
    con.commit()
    return n


def cited_by(con: sqlite3.Connection, rid: int, limit: int = 50) -> list[sqlite3.Row]:
    """Bu karara atıf yapan (sonraki) kararlar."""
    return con.execute(
        """SELECT k.rid, k.daire, k.esas_no, k.karar_no, k.karar_tarihi, k.sonuc
           FROM atif a JOIN karar k ON k.rid = a.kaynak_rid
           WHERE a.hedef_rid = ? ORDER BY k.karar_tarihi DESC LIMIT ?""", (rid, limit)).fetchall()


def cites(con: sqlite3.Connection, rid: int) -> list[dict]:
    """Bu kararın atıf yaptığı kararlar (çözülmüş olanlar rid ile)."""
    out = []
    for a in con.execute("SELECT * FROM atif WHERE kaynak_rid=?", (rid,)):
        out.append(dict(a))
    return out


def atif_sayisi(con: sqlite3.Connection, rids: list[int]) -> dict[int, int]:
    if not rids:
        return {}
    q = f"SELECT hedef_rid, COUNT(*) FROM atif WHERE hedef_rid IN ({','.join('?' * len(rids))}) GROUP BY hedef_rid"
    return dict(con.execute(q, rids).fetchall())


def madde_istatistik(con: sqlite3.Connection, kanun: str, madde: str) -> dict:
    """Bir kanun maddesine dayanan kararların daire, yıl ve sonuç dağılımı."""
    base = "FROM madde_atif m JOIN karar k ON k.rid=m.rid WHERE m.kanun=? AND m.madde=?"
    return {
        "toplam": con.execute(f"SELECT COUNT(*) {base}", (kanun, madde)).fetchone()[0],
        "daire": [dict(r) for r in con.execute(
            f"SELECT k.daire AS daire, COUNT(*) AS n {base} GROUP BY k.daire ORDER BY n DESC LIMIT 10", (kanun, madde))],
        "yil_sonuc": [dict(r) for r in con.execute(
            f"SELECT k.yil AS yil, k.sonuc AS sonuc, COUNT(*) AS n {base} GROUP BY k.yil, k.sonuc ORDER BY k.yil", (kanun, madde))],
    }


if __name__ == "__main__":
    from .db import connect
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if sys.argv[1:] == ["resolve"]:
        c = connect()
        log.info("%d atıf çözüldü", resolve(c))
        tot, ok = c.execute("SELECT COUNT(*), COUNT(hedef_rid) FROM atif").fetchone()
        log.info("toplam %d atıf, %d'i veritabanındaki bir karara bağlı", tot, ok)
