"""Hibrit arama: FTS5/BM25 (Türkçe kök + önek) + bge-m3 vektörleri, RRF ile birleşir."""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
from dataclasses import dataclass, field

import numpy as np

from . import graph
from .db import unpack
from .textnorm import fold, fts_query, stem, tokens

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


def bm25(con: sqlite3.Connection, q: str, f: Filtre, k: int = 100) -> list[tuple[int, float]]:
    fq = fts_query(q)
    if not fq:
        return []
    where, params = f.sql()
    sql = f"""SELECT k.rid, bm25(karar_fts) AS s FROM karar_fts
              JOIN karar k ON k.rid = karar_fts.rowid
              WHERE karar_fts MATCH ? {where} ORDER BY s LIMIT ?"""
    try:
        return [(r[0], -r[1]) for r in con.execute(sql, [fq, *params, k])]
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
        qv = torch.from_numpy(self.enc.encode([q], max_len=128)).to(self.mat.device).to(self.mat.dtype)
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
        lists["vektor"] = VEC.search(q, allowed=_allowed(con, f))
    fused: dict[int, float] = {}
    src: dict[int, list[str]] = {}
    for name, lst in lists.items():
        for rank, (rid, _) in enumerate(lst):
            fused[rid] = fused.get(rid, 0) + 1 / (RRF_K + rank + 1)
            src.setdefault(rid, []).append(name)
    if not fused:
        return []
    cand = sorted(fused, key=lambda r: -fused[r])[: k * 2]
    atif = graph.atif_sayisi(con, cand)
    # Otorite: kurul kararı ve çok atıf alan karar hafifçe öne çıkar.
    rows = {r["rid"]: r for r in con.execute(
        f"SELECT rid, daire, daire_turu, esas_no, karar_no, karar_tarihi, sonuc, dava_turu, metin "
        f"FROM karar WHERE rid IN ({','.join('?' * len(cand))})", cand)}
    out = []
    for rid in cand:
        r = rows[rid]
        s = fused[rid] * (1.15 if r["daire_turu"] == "kurul" else 1.0) * (1 + 0.05 * np.log1p(atif.get(rid, 0)))
        out.append(Sonuc(rid, s, r["daire"], r["esas_no"], r["karar_no"], r["karar_tarihi"], r["sonuc"],
                         r["dava_turu"], snippet(unpack(r["metin"]), q), atif.get(rid, 0), src[rid]))
    out.sort(key=lambda x: -x.skor)
    return out[:k]
