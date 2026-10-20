"""FastAPI uygulaması.  uvicorn yargitai.api:app --port 8765"""
from __future__ import annotations

import time
from dataclasses import asdict

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from . import dilekce, graph, pilot, rapor
from .anonim import maskele
from .answer import answer, kunye
from .db import ROOT, connect, unpack
from .search import Filtre, hybrid

app = FastAPI(title="YargitAI", version="0.2")
con = connect()
con.executescript(pilot.SCHEMA)


@app.on_event("startup")
def _isit():
    # Vektör indeksi ve sorgu modeli ilk istekte ~1 dk yükleniyor; açılışta arka planda yükle.
    import threading
    from .search import VEC
    threading.Thread(target=VEC.load, daemon=True).start()
    threading.Thread(target=lambda: [_onbellek(a) for a in ("rapor", "ag")], daemon=True).start()


@app.middleware("http")
async def giris_kontrolu(request: Request, call_next):
    if pilot.acik() and request.url.path not in ("/giris", "/saglik"):
        kullanici = pilot.dogrula(request.cookies.get(pilot.COOKIE))
        if not kullanici:
            if request.url.path.startswith("/api/"):
                return JSONResponse({"detail": "Oturum açın."}, status_code=401)
            return RedirectResponse("/giris")
        request.state.kullanici = kullanici
    else:
        request.state.kullanici = None
    return await call_next(request)


GIRIS_HTML = """<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>YargitAI · Giriş</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Young+Serif&family=Figtree:wght@400;700&display=swap"><style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0C3F48;color:#EAF6F4;font:16px Figtree,system-ui,sans-serif;padding:16px}
form{background:#0A2F36;border:1px solid #1E4A52;border-radius:16px;padding:28px;display:grid;gap:14px;width:min(360px,100%)}
h1{margin:0;font-family:"Young Serif",Georgia,serif;font-weight:400;font-size:32px}h1 b{color:#4BE0BE;font-weight:400}
input{padding:12px;border-radius:10px;border:1px solid #1E4A52;background:#06252B;color:inherit;font:inherit}
button{padding:12px;border:0;border-radius:999px;background:#4BE0BE;color:#062A2F;font-weight:700;font:inherit;font-weight:700;cursor:pointer}
p{margin:0;color:#9CC3C2;font-size:14px}.e{color:#FF8A6E}label{font-size:13px;color:#9CC3C2}</style></head><body>
<form method="post" action="/giris"><h1>Yargit<b>AI</b></h1><p>Pilot erişimi için size verilen davet kodunu girin.</p>
<label for="kod" class="sr">Davet kodu</label><input id="kod" name="kod" autocomplete="one-time-code" required>{hata}<button>Giriş</button></form></body></html>"""


@app.get("/giris", response_class=HTMLResponse)
def giris_sayfasi():
    return GIRIS_HTML.replace("{hata}", "")


@app.post("/giris")
def giris(kod: str = Form(...)):
    if kod.strip() not in pilot.davetler():
        return HTMLResponse(GIRIS_HTML.replace("{hata}", '<p class="e">Kod geçersiz.</p>'), status_code=401)
    r = RedirectResponse("/", status_code=303)
    r.set_cookie(pilot.COOKIE, pilot.imzala(kod.strip()), max_age=60 * 60 * 24 * 30, httponly=True,
                 samesite="lax", secure=True)
    return r


@app.get("/saglik")
def saglik():
    return {"durum": "ok"}


def _filtre(daire, daire_turu, yil_min, yil_max, sonuc, kanun, madde) -> Filtre:
    return Filtre(daire or None, daire_turu or None, yil_min, yil_max, sonuc or None, kanun or None, madde or None)


@app.get("/api/ara")
def ara(request: Request, q: str, daire: str = "", daire_turu: str = "", yil_min: int | None = None,
        yil_max: int | None = None, sonuc: str = "", kanun: str = "", madde: str = "", vektor: bool = True, k: int = 20):
    t0 = time.time()
    hits = hybrid(con, q, _filtre(daire, daire_turu, yil_min, yil_max, sonuc, kanun, madde), k=min(k, 50),
                  use_vectors=vektor)
    pilot.kaydet_sorgu(con, request.state.kullanici, "ara", q, len(hits), t0)
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
        a.pop("baglam", None)
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
def sor(request: Request, s: Soru):
    t0 = time.time()
    r = answer(con, s.soru, Filtre(daire=s.daire or None, yil_min=s.yil_min, yil_max=s.yil_max))
    pilot.kaydet_sorgu(con, request.state.kullanici, "sor", s.soru, len(r["kararlar"]), t0)
    return r


class Dilekce(BaseModel):
    metin: str


@app.post("/api/dilekce")
def dilekce_metin(request: Request, d: Dilekce):
    t0 = time.time()
    r = dilekce.kontrol(con, d.metin)
    # Dilekçe metni müvekkil bilgisi içerebilir: günlüğe yalnız uzunluğu yazılır.
    pilot.kaydet_sorgu(con, request.state.kullanici, "dilekce", f"<{len(d.metin)} karakter>", len(r["kunyeler"]), t0)
    return r


@app.post("/api/dilekce/dosya")
async def dilekce_dosya(request: Request, dosya: UploadFile):
    veri = await dosya.read()
    if len(veri) > 15_000_000:
        raise HTTPException(413, "Dosya 15 MB'tan büyük.")
    try:
        metin = dilekce.metin_cikar(dosya.filename or "", veri)
    except Exception as e:
        raise HTTPException(400, f"Dosya okunamadı ({e}). .udf, .docx, .pdf veya .txt yükleyin.")
    t0 = time.time()
    r = dilekce.kontrol(con, metin)
    pilot.kaydet_sorgu(con, request.state.kullanici, "dilekce", f"<dosya {len(metin)} karakter>", len(r["kunyeler"]), t0)
    return r | {"metin_uzunlugu": len(metin)}


class GeriBildirim(BaseModel):
    sorgu: str
    rid: int
    puan: int  # 1 = işe yaradı, -1 = ilgisiz
    not_: str = ""


@app.post("/api/geri-bildirim")
def geri_bildirim(request: Request, g: GeriBildirim):
    con.execute("INSERT INTO geri_bildirim(kullanici, sorgu, rid, puan, not_) VALUES(?,?,?,?,?)",
                (request.state.kullanici, g.sorgu[:2000], g.rid, max(-1, min(1, g.puan)), g.not_[:1000]))
    con.commit()
    return {"durum": "kaydedildi"}


@app.get("/api/madde/{kanun}/{madde}")
def madde(kanun: str, madde: str):
    return graph.madde_istatistik(con, kanun, madde)


_ISTATISTIK: dict = {}


@app.get("/api/istatistik")
def istatistik():
    # Bir milyon satırı her sayfa açılışında taramamak için 10 dk önbellek.
    if _ISTATISTIK and time.time() - _ISTATISTIK["_t"] < 600:
        return _ISTATISTIK["veri"]
    q = lambda sql: [dict(r) for r in con.execute(sql)]  # noqa: E731
    veri = {
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
    _ISTATISTIK.update(_t=time.time(), veri=veri)
    return veri


_RAPOR: dict = {}
_RAPOR_KILIT = __import__("threading").Lock()


def _onbellek(ad: str) -> dict:
    # Veri yalnız yeniden yüklemeyle değişir; ilk hesaplamadan sonra süreç ömrü boyunca tutulur.
    # Ayrı bağlantı: ağır sorgular ana bağlantıyı bloklamasın.
    with _RAPOR_KILIT:
        if ad not in _RAPOR:
            c = connect()
            try:
                _RAPOR[ad] = {"rapor": rapor.raporlar, "ag": rapor.ag}[ad](c)
            finally:
                c.close()
    return _RAPOR[ad]


@app.get("/api/rapor")
def rapor_api():
    return _onbellek("rapor")


@app.get("/api/ag")
def ag_api():
    return _onbellek("ag")


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "index.html")
