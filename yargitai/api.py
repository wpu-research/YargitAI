"""FastAPI uygulaması.  uvicorn yargitai.api:app --port 8765"""
from __future__ import annotations

from dataclasses import asdict

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import dilekce, graph
from .anonim import maskele
from .answer import answer, kunye
from .db import ROOT, connect, unpack
from .search import Filtre, hybrid

app = FastAPI(title="YargitAI", version="0.1")
con = connect()


def _filtre(daire, daire_turu, yil_min, yil_max, sonuc, kanun, madde) -> Filtre:
    return Filtre(daire or None, daire_turu or None, yil_min, yil_max, sonuc or None, kanun or None, madde or None)


@app.get("/api/ara")
def ara(q: str, daire: str = "", daire_turu: str = "", yil_min: int | None = None, yil_max: int | None = None,
        sonuc: str = "", kanun: str = "", madde: str = "", vektor: bool = True, k: int = 20):
    hits = hybrid(con, q, _filtre(daire, daire_turu, yil_min, yil_max, sonuc, kanun, madde), k=min(k, 50),
                  use_vectors=vektor)
    return [asdict(h) | {"kunye": kunye(h)} for h in hits]


@app.get("/api/karar/{rid}")
def karar(rid: int):
    r = con.execute("SELECT * FROM karar WHERE rid=?", (rid,)).fetchone()
    if not r:
        raise HTTPException(404)
    d = {k: r[k] for k in r.keys() if k != "metin"}
    d["metin"] = maskele(unpack(r["metin"]))
    d["kunye"] = kunye(dict(r))
    atiflar = []
    for a in graph.cites(con, rid):
        if a["hedef_rid"]:
            h = con.execute("SELECT daire, karar_tarihi, sonuc FROM karar WHERE rid=?", (a["hedef_rid"],)).fetchone()
            a |= {"hedef_karar_tarihi": h["karar_tarihi"], "hedef_sonuc": h["sonuc"]}
        atiflar.append(a)
    d["atif_yaptigi"] = atiflar
    d["atif_alan"] = [dict(x) | {"kunye": kunye(dict(x))} for x in graph.cited_by(con, rid)]
    d["maddeler"] = [dict(x) for x in con.execute("SELECT kanun, madde FROM madde_atif WHERE rid=?", (rid,))]
    d["durum"] = graph.durum(con, rid)
    return d


class Soru(BaseModel):
    soru: str
    daire: str = ""
    yil_min: int | None = None
    yil_max: int | None = None


@app.post("/api/sor")
def sor(s: Soru):
    return answer(con, s.soru, Filtre(daire=s.daire or None, yil_min=s.yil_min, yil_max=s.yil_max))


class Dilekce(BaseModel):
    metin: str


@app.post("/api/dilekce")
def dilekce_metin(d: Dilekce):
    return dilekce.kontrol(con, d.metin)


@app.post("/api/dilekce/dosya")
async def dilekce_dosya(dosya: UploadFile):
    veri = await dosya.read()
    if len(veri) > 15_000_000:
        raise HTTPException(413, "Dosya 15 MB'tan büyük.")
    try:
        metin = dilekce.metin_cikar(dosya.filename or "", veri)
    except Exception as e:
        raise HTTPException(400, f"Dosya okunamadı ({e}). .udf, .docx, .pdf veya .txt yükleyin.")
    return dilekce.kontrol(con, metin) | {"metin_uzunlugu": len(metin)}


@app.get("/api/madde/{kanun}/{madde}")
def madde(kanun: str, madde: str):
    return graph.madde_istatistik(con, kanun, madde)


@app.get("/api/istatistik")
def istatistik():
    q = lambda sql: [dict(r) for r in con.execute(sql)]  # noqa: E731
    return {
        "toplam": con.execute("SELECT COUNT(*) FROM karar").fetchone()[0],
        "atif": con.execute("SELECT COUNT(*), COUNT(hedef_rid) FROM atif").fetchone()[:],
        "yil": q("SELECT yil, COUNT(*) n FROM karar WHERE yil IS NOT NULL GROUP BY yil ORDER BY yil"),
        "daire": q("SELECT daire, COUNT(*) n FROM karar GROUP BY daire ORDER BY n DESC LIMIT 25"),
        "sonuc": q("SELECT COALESCE(sonuc,'belirsiz') sonuc, COUNT(*) n FROM karar GROUP BY sonuc ORDER BY n DESC"),
        "kaynak": q("SELECT kaynak, COUNT(*) n FROM karar GROUP BY kaynak"),
        "en_cok_atif": q("""SELECT k.rid, k.daire, k.esas_no, k.karar_no, k.karar_tarihi, COUNT(*) n
                             FROM atif a JOIN karar k ON k.rid=a.hedef_rid
                             GROUP BY a.hedef_rid ORDER BY n DESC LIMIT 15"""),
        "daireler": [r[0] for r in con.execute("SELECT DISTINCT daire FROM karar ORDER BY daire")],
    }


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "index.html")
