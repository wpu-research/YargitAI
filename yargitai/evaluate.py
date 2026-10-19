"""Arama kalitesi ölçümü.

Gümüş set (otomatik): Bir karar başka bir karara atıf yaparken atfın hemen
önündeki cümleler bir hukuki önerme içerir. O metin (künyeler, tarihler ve
mahkeme adları silinerek) sorgu olur; doğru cevap atıf yapılan karardır.
Bu, "şu önermenin dayanağı olan emsali bul" görevinin ta kendisidir.

    python -m yargitai.evaluate build --n 300        # eval/gumus.jsonl
    python -m yargitai.evaluate run                  # bm25 / vektör / hibrit karşılaştırması
    python -m yargitai.evaluate avukat               # eval/avukat_inceleme.csv (avukat işaretler)
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import time
from pathlib import Path

from .db import ROOT, connect, unpack
from .embed import DIKEYLER
from .search import Filtre, VEC, bm25, hybrid

EVAL = ROOT / "eval"
_STRIP = [
    re.compile(r"\d{4}/(?:\d{1,2}-)?\d{1,6}\s*(?:E\.|Esas|K\.|Karar)[^,;)]*", re.I),
    re.compile(r"\d{1,2}[./]\d{1,2}[./]\d{4}\s*(?:tarihli|tarihinde|gün(?:lü)?)?\s*(?:ve)?", re.I),
    re.compile(r"(?:Yargıtay\s+)?(?:\d{1,2}\.\s*(?:Hukuk|Ceza)\s+Dairesi|Dairemiz|Hukuk Genel Kurulu|Ceza Genel Kurulu)\w*", re.I),
    re.compile(r"sayılı\s+(?:ilam|karar)\w*", re.I),
]


_HEADER = re.compile(r"İlgili Hukuk|İLK DERECE|BÖLGE ADLİYE|DAVA TÜRÜ|MAHKEMESİ\s*:|TEMYİZ EDEN|K\s*A\s*R\s*A\s*R")


def _query_from_context(text: str, pos: int) -> str | None:
    ctx = text[max(0, pos - 700):pos]
    # Önceki cümle sınırından başla, böylece yarım kelimeyle açılmasın.
    cut = max(ctx.find(". ") + 2, 0)
    q = ctx[cut:]
    for rx in _STRIP:
        q = rx.sub(" ", q)
    q = re.sub(r"\s+", " ", q).strip(" ,;:(")
    # Bölüm başlıkları ve mevzuat listeleri önerme değildir.
    if len(q) < 120 or _HEADER.search(q) or sum(c.isupper() for c in q) > 0.15 * len(q):
        return None
    return q


def build(n: int, seed: int = 7) -> None:
    con = connect()
    vertical = {r[0] for r in con.execute(DIKEYLER["is_hukuku"])}
    rows = con.execute(
        "SELECT kaynak_rid, hedef_rid, esas_no, karar_no FROM atif "
        "WHERE hedef_rid IS NOT NULL AND kaynak_rid != hedef_rid").fetchall()
    rows = [r for r in rows if r[1] in vertical and r[0] in vertical]
    random.Random(seed).shuffle(rows)
    out, used = [], set()
    for kaynak, hedef, esas, karar in rows:
        if hedef in used:  # her hedef karar en fazla bir kez: çok atıf alan kararlar sonucu çarpıtmasın
            continue
        text = unpack(con.execute("SELECT metin FROM karar WHERE rid=?", (kaynak,)).fetchone()[0])
        pos = text.find(esas)
        q = _query_from_context(text, pos) if pos > 0 else None
        if not q:
            continue
        used.add(hedef)
        out.append({"sorgu": q, "hedef": hedef, "kaynak": kaynak})
        if len(out) >= n:
            break
    EVAL.mkdir(exist_ok=True)
    with open(EVAL / "gumus.jsonl", "w") as f:
        for o in out:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    print(f"{len(out)} sorgu yazıldı → eval/gumus.jsonl")


def _rank(hits: list[int], gold: int, imza: dict[int, str | None]) -> int | None:
    """Hedefin kendisi ya da birebir aynı metinli (seri dava) kopyası isabet sayılır."""
    g = imza.get(gold)
    for i, h in enumerate(hits, 1):
        if h == gold or (g and imza.get(h) == g):
            return i
    return None


def run(k: int = 10) -> dict:
    con = connect()
    items = [json.loads(l) for l in open(EVAL / "gumus.jsonl")]
    # Atıf ortaklığı: hedefin kendisi ya da aynı hedefe atıf yapan kararlar ilgili sayılır.
    for it in items:
        it["ilgili"] = {it["hedef"]} | {r[0] for r in con.execute(
            "SELECT kaynak_rid FROM atif WHERE hedef_rid=?", (it["hedef"],))} - {it["kaynak"]}
    imza: dict[int, str | None] = {}

    def imzalar(rids):
        eksik = [r for r in rids if r not in imza]
        if eksik:
            imza.update(con.execute(f"SELECT rid, imza FROM karar WHERE rid IN ({','.join('?' * len(eksik))})",
                                    eksik).fetchall())
    imzalar([it["hedef"] for it in items])
    VEC.load()
    modes = {
        "bm25": lambda q: [r for r, _ in bm25(con, q, Filtre(), k=k + 1)],
        "vektor": lambda q: [r for r, _ in VEC.search(q, k=k + 1)],
        "hibrit": lambda q: [h.rid for h in hybrid(con, q, Filtre(), k=k + 1)],
    }
    report = {}
    for name, fn in modes.items():
        ranks, ortak, t0 = [], [], time.time()
        for it in items:
            hits = [h for h in fn(it["sorgu"]) if h != it["kaynak"]][:k]  # sorgunun alındığı kararı sayma
            imzalar(hits)
            ranks.append(_rank(hits, it["hedef"], imza))
            ortak.append((any(h in it["ilgili"] for h in hits[:5]), sum(h in it["ilgili"] for h in hits[:10]) / 10))
            if len(ranks) % 100 == 0:
                print(f"  {name}: {len(ranks)}/{len(items)}", flush=True)
        n = len(ranks)
        report[name] = {
            "recall@1": sum(1 for r in ranks if r == 1) / n,
            "recall@5": sum(1 for r in ranks if r and r <= 5) / n,
            "recall@10": sum(1 for r in ranks if r) / n,
            "mrr@10": sum(1 / r for r in ranks if r) / n,
            "ortak_hit@5": sum(o[0] for o in ortak) / n,
            "ortak_p@10": sum(o[1] for o in ortak) / n,
            "ms_sorgu": 1000 * (time.time() - t0) / n,
        }
    report["n"] = len(items)
    (EVAL / "sonuc.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"{'mod':8} {'R@1':>6} {'R@5':>6} {'R@10':>6} {'MRR':>6} {'O-H@5':>6} {'O-P@10':>6} {'ms':>6}   (n={len(items)})")
    for name in modes:
        m = report[name]
        print(f"{name:8} {m['recall@1']:6.3f} {m['recall@5']:6.3f} {m['recall@10']:6.3f} {m['mrr@10']:6.3f} "
              f"{m['ortak_hit@5']:6.3f} {m['ortak_p@10']:6.3f} {m['ms_sorgu']:6.0f}")
    return report


def avukat(k: int = 5) -> None:
    """Avukat sorularının ilk k sonucunu işaretlenmek üzere CSV'ye döker."""
    from .answer import kunye
    con = connect()
    sorular = [l.strip()[2:] for l in open(EVAL / "avukat_sorulari.md") if l.startswith("- ")]
    with open(EVAL / "avukat_inceleme.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["soru_no", "soru", "sira", "kunye", "sonuc", "pasaj", "ilgili_mi (2=tam emsal,1=kısmen,0=ilgisiz)"])
        for i, s in enumerate(sorular, 1):
            for j, h in enumerate(hybrid(con, s, Filtre(), k=k), 1):
                w.writerow([i, s if j == 1 else "", j, kunye(h), h.sonuc or "", h.snippet[:300].replace("\n", " "), ""])
    print(f"{len(sorular)} soru × {k} sonuç → eval/avukat_inceleme.csv")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "run", "avukat"])
    ap.add_argument("--n", type=int, default=300)
    a = ap.parse_args()
    {"build": lambda: build(a.n), "run": run, "avukat": avukat}[a.cmd]()


if __name__ == "__main__":
    main()
