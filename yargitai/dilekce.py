"""Dilekçe kontrolü: dilekçedeki künyeleri doğrular, paragraflara emsal bulur.

"Bulunamadı" ile "uydurma" aynı şey değildir: veritabanının kapsamı sınırlı
(şu an 2020–2023 Yargıtay daireleri ve CGK; HGK yok). Bu yüzden bulunamayan
künye, kapsam içindeyse "şüpheli", kapsam dışındaysa "kapsam dışı" sayılır.
"""
from __future__ import annotations

import io
import re
import sqlite3
import zipfile

from . import extract, parse
from .answer import kunye
from .search import Filtre, hybrid


def metin_cikar(dosya_adi: str, veri: bytes) -> str:
    ad = dosya_adi.lower()
    if ad.endswith(".udf"):
        # UYAP doküman formatı: içinde content.xml olan bir zip; metin CDATA içinde.
        with zipfile.ZipFile(io.BytesIO(veri)) as z:
            xml = z.read("content.xml").decode("utf-8", errors="replace")
        m = re.search(r"<content>\s*<!\[CDATA\[(.*?)\]\]>\s*</content>", xml, re.S)
        return m.group(1) if m else re.sub(r"<[^>]+>", " ", xml)
    if ad.endswith(".docx"):
        import docx
        return "\n".join(p.text for p in docx.Document(io.BytesIO(veri)).paragraphs)
    if ad.endswith(".pdf"):
        import pypdf
        return "\n".join(p.extract_text() or "" for p in pypdf.PdfReader(io.BytesIO(veri)).pages)
    return veri.decode("utf-8", errors="replace")


def _kapsam(con: sqlite3.Connection) -> tuple[set[int], set[str]]:
    yillar = {r[0] for r in con.execute("SELECT DISTINCT yil FROM karar WHERE yil IS NOT NULL")}
    daireler = {r[0] for r in con.execute("SELECT DISTINCT daire FROM karar")}
    return yillar, daireler


def kunye_kontrol(con: sqlite3.Connection, metin: str) -> list[dict]:
    yillar, daireler = _kapsam(con)
    out = []
    for a in extract.karar_atiflari(metin, own_daire="(dilekçe)"):
        iddia = f"{a.daire or a.tur} {a.esas} E., {a.karar} K." + (f" ({a.tarih})" if a.tarih else "")
        rows = con.execute("SELECT rid, daire, esas_no, karar_no, karar_tarihi FROM karar WHERE esas_no=? AND karar_no=?",
                           (a.esas, a.karar)).fetchall()
        kayit = {"iddia": iddia}
        if rows:
            r = dict(rows[0])
            sorunlar = []
            hedef_daire = a.daire if a.tur == "daire" else {"HGK": "Hukuk Genel Kurulu", "CGK": "Ceza Genel Kurulu"}.get(a.tur)
            if hedef_daire and hedef_daire != "(dilekçe)" and not any(x["daire"] == hedef_daire for x in rows):
                sorunlar.append(f"daire uyuşmuyor: kayıtta {r['daire']}")
            iso = parse.parse_date(a.tarih)
            if iso and r["karar_tarihi"] and iso != r["karar_tarihi"]:
                y, m, g = r['karar_tarihi'].split('-')
                sorunlar.append(f"tarih uyuşmuyor: kayıtta {g}.{m}.{y}")
            kayit |= {"durum": "uyusmazlik" if sorunlar else "dogrulandi", "rid": r["rid"],
                      "kunye": kunye(r), "sorunlar": sorunlar}
        else:
            yil = int(a.karar[:4])
            hedef_daire = a.daire if a.tur == "daire" else {"HGK": "Hukuk Genel Kurulu", "CGK": "Ceza Genel Kurulu"}.get(a.tur)
            kapsam_ici = yil in yillar and (hedef_daire in daireler if hedef_daire else False)
            kayit |= {"durum": "supheli" if kapsam_ici else "kapsam_disi",
                      "aciklama": ("Bu yıl ve daire veritabanında var ama bu esas/karar çifti yok. Künyeyi UYAP'tan kontrol edin."
                                   if kapsam_ici else "Bu yıl veya daire henüz veritabanında değil; doğrulanamadı.")}
        out.append(kayit)
    return out


def paragraflar(metin: str, en_fazla: int = 6) -> list[str]:
    """Emsal aranacak, hukuki iddia içeren paragraflar (en uzun olanlar, metin sırasıyla)."""
    ps = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n|\n(?=\d+[.)-]\s)", metin)]
    ps = [p for p in ps if len(p) > 160 and not re.match(r"^(SONUÇ|NETİCE|DAVACI|DAVALI|VEKİLİ|KONU)\b", p, re.I)]
    secilen = set(sorted(ps, key=len, reverse=True)[:en_fazla])
    return [p for p in ps if p in secilen]


def kontrol(con: sqlite3.Connection, metin: str, k: int = 3) -> dict:
    kunyeler = kunye_kontrol(con, metin)
    iddialar = []
    for p in paragraflar(metin):
        hits = hybrid(con, p[:1200], Filtre(), k=k)
        iddialar.append({"paragraf": p[:600], "emsaller": [
            {"rid": h.rid, "kunye": kunye(h), "sonuc": h.sonuc, "snippet": h.snippet, "atif_alan": h.atif_alan}
            for h in hits]})
    ozet = {d: sum(1 for x in kunyeler if x["durum"] == d) for d in ("dogrulandi", "uyusmazlik", "supheli", "kapsam_disi")}
    return {"kunyeler": kunyeler, "ozet": ozet, "iddialar": iddialar}
