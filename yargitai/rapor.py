"""Veri sekmesi raporları ve ana sayfadaki 3D atıf ağı için toplu sorgular.

Veri statik (yalnız yeniden yükleme ile değişir); sonuçlar süreç ömrü boyunca önbelleğe alınır.
"""
from __future__ import annotations

import sqlite3

# Yalnız adından emin olduklarım; listede olmayan kanun numarası olduğu gibi gösterilir.
KANUN_ADI = {
    "5237": "TCK", "5271": "CMK", "6100": "HMK", "4857": "İş Kanunu", "6098": "TBK",
    "1412": "CMUK (mülga)", "5320": "CMK Uygulama K.", "2004": "İİK", "4721": "TMK",
    "5510": "Sosyal Sigortalar K.", "6102": "TTK", "2942": "Kamulaştırma K.",
}

_BOZMA = ("bozma", "kismen_bozma")
_ONAMA = ("onama", "duzelterek_onama")


def _q(con: sqlite3.Connection, sql: str, *a) -> list[dict]:
    return [dict(r) for r in con.execute(sql, a)]


def raporlar(con: sqlite3.Connection) -> dict:
    tum = con.execute("SELECT COUNT(*) FROM karar").fetchone()[0]
    bilinen = con.execute(
        "SELECT COUNT(*) FROM karar WHERE sonuc IN ('bozma','kismen_bozma','onama','duzelterek_onama')").fetchone()[0]

    # 1) Daire bazında bozma oranı: yalnız sonucu bilinen kararlar üzerinden.
    daire_oran = _q(con, """
        SELECT daire, daire_turu turu, COUNT(*) n,
               SUM(sonuc IN ('bozma','kismen_bozma')) bozma,
               SUM(sonuc IN ('onama','duzelterek_onama')) onama
        FROM karar WHERE sonuc IN ('bozma','kismen_bozma','onama','duzelterek_onama') AND daire_turu!='kurul'
        GROUP BY daire HAVING n>=1000""")
    for d in daire_oran:
        d["oran"] = d["bozma"] / d["n"]
    daire_oran.sort(key=lambda d: d["oran"], reverse=True)

    # 2) Yıl × tür bozma oranı.
    yil_tur = _q(con, """
        SELECT yil, daire_turu turu, COUNT(*) n, SUM(sonuc IN ('bozma','kismen_bozma')) bozma
        FROM karar WHERE yil IS NOT NULL AND daire_turu IN ('hukuk','ceza')
          AND sonuc IN ('bozma','kismen_bozma','onama','duzelterek_onama')
        GROUP BY yil, daire_turu ORDER BY yil""")

    # 3) Aylık karar hacmi.
    ay = _q(con, """SELECT substr(karar_tarihi,1,7) ay, COUNT(*) n FROM karar
                    WHERE karar_tarihi IS NOT NULL AND length(karar_tarihi)>=7 GROUP BY 1 ORDER BY 1""")

    # 4) En çok dayanılan kanun maddeleri.
    madde = _q(con, """SELECT kanun, madde, COUNT(*) n FROM madde_atif
                       GROUP BY kanun, madde ORDER BY n DESC LIMIT 16""")
    for m in madde:
        m["kanun_adi"] = KANUN_ADI.get(m["kanun"], "")

    # 5) Daireler arası atıf akışı; aynı dairenin kendi içtihadına atfı ayrı sayılır.
    akis = _q(con, """SELECT s.daire kaynak, h.daire hedef, COUNT(*) n
                      FROM atif a JOIN karar s ON s.rid=a.kaynak_rid JOIN karar h ON h.rid=a.hedef_rid
                      GROUP BY 1,2""")
    toplam_bagli = sum(x["n"] for x in akis)
    kendi = sum(x["n"] for x in akis if x["kaynak"] == x["hedef"])
    capraz = sorted((x for x in akis if x["kaynak"] != x["hedef"]), key=lambda x: x["n"], reverse=True)[:12]
    kendi_daire = sorted((x for x in akis if x["kaynak"] == x["hedef"]), key=lambda x: x["n"], reverse=True)[:8]

    # 6) En sık suç ve dava türleri.
    suc = _q(con, "SELECT suc ad, COUNT(*) n FROM karar WHERE suc IS NOT NULL AND suc!='' GROUP BY 1 ORDER BY n DESC LIMIT 10")
    dava = _q(con, "SELECT dava_turu ad, COUNT(*) n FROM karar WHERE dava_turu IS NOT NULL AND dava_turu!='' GROUP BY 1 ORDER BY n DESC LIMIT 10")

    return {
        "toplam": tum, "bilinen_sonuc": bilinen,
        "daire_oran": daire_oran, "yil_tur": yil_tur, "ay": ay, "madde": madde,
        "akis": {"toplam": toplam_bagli, "kendi": kendi, "capraz": capraz, "kendi_daire": kendi_daire},
        "suc": suc, "dava": dava,
    }


def ag(con: sqlite3.Connection, hedef_sayisi: int = 70, atif_yapan: int = 7, hub_akis: int = 60) -> dict:
    """3D ağ için gerçek atıf verisi.

    - hub: her daire/kurul (boyut = karar sayısı)
    - yildiz: en çok atıf alan kararlar
    - yaprak: o kararlara atıf yapan kararlardan örnek (en fazla `atif_yapan` tane)
    - akis: daire→daire atıf hacimleri
    """
    hubs = _q(con, "SELECT daire ad, daire_turu turu, COUNT(*) n FROM karar GROUP BY daire")
    hedef = _q(con, """
        SELECT k.rid, k.daire, k.esas_no, k.karar_no, k.karar_tarihi, k.sonuc, COUNT(*) n
        FROM atif a JOIN karar k ON k.rid=a.hedef_rid
        GROUP BY a.hedef_rid ORDER BY n DESC LIMIT ?""", hedef_sayisi)
    ids = [h["rid"] for h in hedef]
    ph = ",".join("?" * len(ids))
    yaprak = _q(con, f"""
        SELECT hedef_rid, rid, daire, daire_turu turu, esas_no, karar_no, karar_tarihi FROM (
          SELECT a.hedef_rid, s.rid, s.daire, s.daire_turu, s.esas_no, s.karar_no, s.karar_tarihi,
                 ROW_NUMBER() OVER (PARTITION BY a.hedef_rid ORDER BY s.karar_tarihi DESC, s.rid) sira
          FROM atif a JOIN karar s ON s.rid=a.kaynak_rid
          WHERE a.hedef_rid IN ({ph}) AND s.rid NOT IN ({ph})
        ) WHERE sira<=?""", *ids, *ids, atif_yapan)
    akis = _q(con, """SELECT s.daire kaynak, h.daire hedef, COUNT(*) n
                      FROM atif a JOIN karar s ON s.rid=a.kaynak_rid JOIN karar h ON h.rid=a.hedef_rid
                      WHERE s.daire!=h.daire GROUP BY 1,2 ORDER BY n DESC LIMIT ?""", hub_akis)
    return {"hub": hubs, "hedef": hedef, "yaprak": yaprak, "akis": akis}
