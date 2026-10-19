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


def durum(con: sqlite3.Connection, rid: int) -> dict:
    """Kararın güncelliğine dair sinyaller: mülga kanun, atıf güncelliği, içtihat değişikliği bağlamı."""
    from .anonim import maskele
    from .answer import kunye
    from .extract import MULGA
    mulga = []
    maddeler: dict[str, set[str]] = {}
    for k, m in con.execute("SELECT kanun, madde FROM madde_atif WHERE rid=?", (rid,)):
        maddeler.setdefault(k, set()).add(m)
    for kanun, mset in maddeler.items():
        if kanun in MULGA:
            ad, yerine, istisna = MULGA[kanun]
            if istisna and mset - {""} and mset - {""} <= istisna:
                continue  # yalnız yürürlükteki maddelere dayanıyor
            mulga.append({"kanun": kanun, "ad": ad, "yerine": yerine})
    ozet = con.execute(
        """SELECT COUNT(*), MAX(k.yil), SUM(k.daire_turu='kurul'), SUM(a.isaret='teyit')
           FROM atif a JOIN karar k ON k.rid=a.kaynak_rid WHERE a.hedef_rid=?""", (rid,)).fetchone()
    degisiklik = [
        {"rid": r["rid"], "kunye": kunye(dict(r)), "baglam": maskele(r["baglam"] or "")}
        for r in con.execute(
            """SELECT k.rid, k.daire, k.esas_no, k.karar_no, k.karar_tarihi, a.baglam
               FROM atif a JOIN karar k ON k.rid=a.kaynak_rid
               WHERE a.hedef_rid=? AND a.isaret='degisiklik' ORDER BY k.karar_tarihi DESC LIMIT 5""", (rid,))]
    return {
        "mulga": mulga,
        "atif_toplam": ozet[0] or 0,
        "son_atif_yili": ozet[1],
        "kurul_atfi": ozet[2] or 0,
        "teyit": ozet[3] or 0,
        "degisiklik": degisiklik,
        # Mülga kanun atfı tek başına sorun değildir (geçiş hükümleri, olay tarihi): yalnız bilgi.
        "seviye": "dikkat" if degisiklik else "bilgi",
    }


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


def reextract(con: sqlite3.Connection, batch: int = 5000) -> None:
    """Çıkarım kodu değiştiğinde tüm kararların atıflarını saklı metinden yeniden üretir."""
    from . import extract, parse
    from .db import unpack
    last = 0
    while True:
        rows = con.execute("SELECT rid, daire, metin FROM karar WHERE rid > ? ORDER BY rid LIMIT ?",
                           (last, batch)).fetchall()
        if not rows:
            break
        rids = [r[0] for r in rows]
        ph = ",".join("?" * len(rids))
        con.execute(f"DELETE FROM atif WHERE kaynak_rid IN ({ph})", rids)
        con.execute(f"DELETE FROM madde_atif WHERE rid IN ({ph})", rids)
        for rid, daire, blob in rows:
            text = unpack(blob)
            con.executemany(
                "INSERT INTO atif(kaynak_rid, tur, hedef_daire, esas_no, karar_no, tarih, baglam, isaret) VALUES(?,?,?,?,?,?,?,?)",
                [(rid, a.tur, a.daire, a.esas, a.karar, parse.parse_date(a.tarih), a.baglam, a.isaret)
                 for a in extract.karar_atiflari(text, daire)])
            con.executemany("INSERT OR IGNORE INTO madde_atif(rid, kanun, madde) VALUES(?,?,?)",
                            [(rid, m.kanun, m.madde) for m in extract.madde_atiflari(text)])
        con.commit()
        last = rids[-1]
        log.info("yeniden çıkarım: rid %d'e kadar", last)


if __name__ == "__main__":
    from .db import connect
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if sys.argv[1:] == ["reextract"]:
        reextract(connect())
    if sys.argv[1:] == ["resolve"]:
        c = connect()
        log.info("%d atıf çözüldü", resolve(c))
        tot, ok = c.execute("SELECT COUNT(*), COUNT(hedef_rid) FROM atif").fetchone()
        log.info("toplam %d atıf, %d'i veritabanındaki bir karara bağlı", tot, ok)
