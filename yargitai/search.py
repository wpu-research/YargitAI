"""Hibrit arama: FTS5/BM25 (Türkçe kök + önek) + bge-m3 vektörleri, RRF ile birleşir."""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
from dataclasses import dataclass, field

import numpy as np

from . import graph
from .anonim import maskele
from .db import unpack
from .textnorm import fold, fts_from, sorgu_terimleri, stem, tokens

log = logging.getLogger("search")
RRF_K = 60


@dataclass
class Filtre:
    daire: str | None = None          # tam ad: "9. Hukuk Dairesi"
    daire_turu: str | None = None     # hukuk | ceza | kurul
    yil_min: int | None = None
    yil_max: int | None = None
    sonuc: str | None = None
    kanun: str | None = None          # "4857"
    madde: str | None = None          # "17"

    def sql(self) -> tuple[str, list]:
        w, p = [], []
        if self.daire:
            w.append("k.daire = ?"); p.append(self.daire)
        if self.daire_turu:
            w.append("k.daire_turu = ?"); p.append(self.daire_turu)
        if self.yil_min:
            w.append("k.yil >= ?"); p.append(self.yil_min)
        if self.yil_max:
            w.append("k.yil <= ?"); p.append(self.yil_max)
        if self.sonuc:
            w.append("k.sonuc = ?"); p.append(self.sonuc)
        if self.kanun:
            if self.madde:
                w.append("k.rid IN (SELECT rid FROM madde_atif WHERE kanun=? AND madde=?)"); p += [self.kanun, self.madde]
            else:
                w.append("k.rid IN (SELECT rid FROM madde_atif WHERE kanun=?)"); p.append(self.kanun)
        return (" AND " + " AND ".join(w)) if w else "", p


@dataclass
class Sonuc:
    rid: int
    skor: float
    daire: str
    esas_no: str | None
    karar_no: str | None
    karar_tarihi: str | None
    sonuc: str | None
    dava_turu: str | None
    snippet: str = ""
    atif_alan: int = 0
    kaynaklar: list[str] = field(default_factory=list)
    seri: int = 1  # aynı şablondan çıkan karar sayısı (kendisi dahil)


MAX_TERIM = 12
_DF: dict[str, int] = {}


def _df(con: sqlite3.Connection, st: str) -> int:
    if st not in _DF:
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS karar_fts_v USING fts5vocab(karar_fts, row)")
        _DF[st] = con.execute("SELECT COALESCE(SUM(doc),0) FROM karar_fts_v WHERE term >= ? AND term < ?",
                              (st, st + "\uffff")).fetchone()[0]
    return _DF[st]


def secili_terimler(con: sqlite3.Connection, q: str) -> tuple[list[str], list[str]]:
    """Uzun sorgularda (dilekçe paragrafı, önerme) yalnız en ayırt edici kökler tutulur.

    70 terimlik bir önek araması bir milyon kararda ~30 sn sürüyor; en nadir 12 kök
    hem hızı hem isabeti korur. Hiç geçmeyen kökler (yazım hatası) atılır.
    """
    phrases, stems = sorgu_terimleri(q)
    if len(stems) <= MAX_TERIM:
        return phrases, stems
    scored = [(d, s) for s in stems if (d := _df(con, s)) > 0]
    keep = {s for _, s in sorted(scored)[:MAX_TERIM]}
    return phrases, [s for s in stems if s in keep]


def bm25(con: sqlite3.Connection, q: str, f: Filtre, k: int = 100) -> list[tuple[int, float]]:
    phrases, stems = secili_terimler(con, q)
    fq = fts_from(phrases, stems)
    if not fq:
        return []
    where, params = f.sql()
    sql = f"""SELECT k.rid, bm25(karar_fts) AS s FROM karar_fts
              JOIN karar k ON k.rid = karar_fts.rowid
              WHERE karar_fts MATCH ? {where} ORDER BY s LIMIT ?"""
    try:
        hits = [(r[0], -r[1]) for r in con.execute(sql, [fq, *params, k])]
        # Tüm terimleri içeren karar azsa, terimlerden herhangi birini içerenlerle tamamla.
        if len(hits) < k // 5 and " AND " in fq:
            seen = {h[0] for h in hits}
            hits += [(r[0], -r[1]) for r in con.execute(sql, [fq.replace(" AND ", " OR "), *params, k])
                     if r[0] not in seen]
        return hits[:k]
    except sqlite3.OperationalError as e:
        log.warning("FTS sorgu hatası %r: %s", fq, e)
        return []


class VectorIndex:
    """vectors.db'deki tüm vektörleri belleğe (mümkünse GPU'ya) alır; kaba kuvvet arama."""

    def __init__(self):
        self._lock = threading.Lock()
        self.loaded = False
        self.enc = None

    def load(self) -> bool:
        with self._lock:
            if self.loaded:
                return True
            from .embed import VEC_PATH, Encoder, vec_connect
            if not VEC_PATH.exists():
                return False
            import torch
            vcon = vec_connect()
            rows = vcon.execute("SELECT rid, emb FROM vec").fetchall()
            if not rows:
                return False
            self.rids = np.array([r[0] for r in rows], dtype=np.int64)
            mat = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float16).reshape(len(rows), -1)
            dev = "cuda" if torch.cuda.is_available() else "cpu"
            self.mat = torch.from_numpy(mat.copy()).to(dev)
            if dev == "cpu":
                self.mat = self.mat.float()
            self.enc = Encoder()
            self.loaded = True
            log.info("vektör indeksi yüklendi: %d parça", len(rows))
            return True

    def search(self, q: str, k: int = 100, allowed: set[int] | None = None) -> list[tuple[int, float]]:
        if not self.load():
            return []
        import torch
        qv = torch.from_numpy(self.enc.encode([q], max_len=512)).to(self.mat.device).to(self.mat.dtype)
        sims = (self.mat @ qv[0]).float()
        top = torch.topk(sims, min(len(sims), k * 6))
        best: dict[int, float] = {}
        for i, s in zip(top.indices.tolist(), top.values.tolist()):
            rid = int(self.rids[i])
            if allowed is not None and rid not in allowed:
                continue
            if s > best.get(rid, -1):
                best[rid] = s
        return sorted(best.items(), key=lambda x: -x[1])[:k]


VEC = VectorIndex()


def _allowed(con: sqlite3.Connection, f: Filtre) -> set[int] | None:
    where, params = f.sql()
    if not where:
        return None
    return {r[0] for r in con.execute(f"SELECT k.rid FROM karar k WHERE 1=1 {where}", params)}


def snippet(text: str, q: str, width: int = 420) -> str:
    stems = [fold(stem(t))[:6] for t in tokens(q) if len(t) > 2]
    if not stems:
        return text[:width]
    low = fold(text)
    best, best_pos = -1, 0
    for m in re.finditer(r"[^.!?\n]{40,}[.!?\n]", low):
        seg = m.group(0)
        s = sum(seg.count(st) for st in stems) + 0.5 * len({st for st in stems if st in seg})
        if s > best:
            best, best_pos = s, m.start()
    start = max(0, best_pos - 60)
    out = text[start:start + width].strip()
    return ("…" if start else "") + out + "…"


def hybrid(con: sqlite3.Connection, q: str, f: Filtre | None = None, k: int = 20,
           use_vectors: bool = True) -> list[Sonuc]:
    f = f or Filtre()
    lists: dict[str, list[tuple[int, float]]] = {"bm25": bm25(con, q, f)}
    if use_vectors:
        try:
            lists["vektor"] = VEC.search(q, allowed=_allowed(con, f))
        except Exception as e:  # vektör katmanı düşerse BM25 ile devam
            log.warning("vektör araması atlandı: %s", e)
    fused: dict[int, float] = {}
    src: dict[int, list[str]] = {}
    for name, lst in lists.items():
        for rank, (rid, _) in enumerate(lst):
            fused[rid] = fused.get(rid, 0) + 1 / (RRF_K + rank + 1)
            src.setdefault(rid, []).append(name)
    if not fused:
        return []
    cand = sorted(fused, key=lambda r: -fused[r])[: k * 5]
    rows = {r["rid"]: r for r in con.execute(
        f"SELECT rid, daire, daire_turu, esas_no, karar_no, karar_tarihi, sonuc, dava_turu, imza "
        f"FROM karar WHERE rid IN ({','.join('?' * len(cand))})", cand)}
    # Seri davalar: aynı şablondan çıkan kararlardan yalnız en iyi sıradaki gösterilir.
    temsil: dict[str, int] = {}
    secilen = []
    for rid in cand:
        im = rows[rid]["imza"] or f"rid:{rid}"
        if im in temsil:
            continue
        temsil[im] = rid
        secilen.append(rid)
    secilen = secilen[: k * 2]
    imzalar = [rows[r]["imza"] for r in secilen if rows[r]["imza"]]
    seri = dict(con.execute(
        f"SELECT imza, COUNT(*) FROM karar WHERE imza IN ({','.join('?' * len(imzalar))}) GROUP BY imza",
        imzalar).fetchall()) if imzalar else {}
    atif = graph.atif_sayisi(con, secilen)
    out = []
    for rid in secilen:
        r = rows[rid]
        # Otorite: kurul kararı ve çok atıf alan karar hafifçe öne çıkar.
        s = fused[rid] * (1.15 if r["daire_turu"] == "kurul" else 1.0) * (1 + 0.05 * np.log1p(atif.get(rid, 0)))
        out.append(Sonuc(rid, s, r["daire"], r["esas_no"], r["karar_no"], r["karar_tarihi"], r["sonuc"],
                         r["dava_turu"], "", atif.get(rid, 0), src[rid], seri.get(r["imza"], 1)))
    out.sort(key=lambda x: -x.skor)
    out = out[:k]
    metin = dict(con.execute(f"SELECT rid, metin FROM karar WHERE rid IN ({','.join('?' * len(out))})",
                             [o.rid for o in out]).fetchall()) if out else {}
    for o in out:
        o.snippet = snippet(maskele(unpack(metin[o.rid])), q)
    return out
