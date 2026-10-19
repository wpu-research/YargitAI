"""bge-m3 ile karar vektörleri (GPU). Artımlı: yalnız henüz vektörü olmayanları işler.

Her karar için en fazla iki parça vektörlenir: başlangıç (dava/iddia) ve
"gereği düşünüldü"den sonraki gerekçe kısmı — hukuki ilke çoğunlukla orada.

    python -m yargitai.embed --dikey is_hukuku
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sqlite3
import time
from pathlib import Path

import numpy as np
import torch
from transformers import AutoModel, AutoTokenizer

from .db import ROOT, connect, unpack

MODEL = "BAAI/bge-m3"
VEC_PATH = Path(os.environ.get("YARGITAI_VEC", ROOT / "data" / "vectors.db"))
MAX_TOK = 512

# Dikey: hangi kararlar vektörlenecek. İlk faz iş hukuku.
DIKEYLER = {
    "is_hukuku": """
        SELECT rid FROM karar WHERE daire IN
            ('9. Hukuk Dairesi','10. Hukuk Dairesi','7. Hukuk Dairesi','22. Hukuk Dairesi','Hukuk Genel Kurulu')
        UNION
        SELECT DISTINCT rid FROM madde_atif WHERE kanun IN ('4857','1475','5510','6356','2821','5953')
    """,
    "hepsi": "SELECT rid FROM karar",
}

_GEREKCE = re.compile(r"gereği\s+(?:düşünüldü|görüşülüp\s+düşünüldü)|G\s*E\s*R\s*E\s*K\s*Ç\s*E|K\s+A\s+R\s+A\s+R", re.I)
log = logging.getLogger("embed")


def vec_connect(path: Path = VEC_PATH) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=60, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS vec (rid INTEGER, parca INTEGER, emb BLOB, PRIMARY KEY(rid, parca)) WITHOUT ROWID")
    return con


def chunks(text: str) -> list[str]:
    head = text[:2200]
    m = _GEREKCE.search(text, 300)
    parts = [head]
    if m and m.end() < len(text) - 300:
        parts.append(text[m.end():m.end() + 2200])
    return parts


class Encoder:
    def __init__(self, device: str | None = None):
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.tok = AutoTokenizer.from_pretrained(MODEL)
        self.model = AutoModel.from_pretrained(MODEL, dtype=torch.float16 if self.device == "cuda" else torch.float32)
        self.model.to(self.device).eval()

    @torch.inference_mode()
    def encode(self, texts: list[str], max_len: int = MAX_TOK) -> np.ndarray:
        b = self.tok(texts, padding=True, truncation=True, max_length=max_len, return_tensors="pt").to(self.device)
        out = self.model(**b).last_hidden_state[:, 0]  # bge-m3 dense: CLS
        out = torch.nn.functional.normalize(out.float(), dim=-1)
        return out.cpu().numpy().astype(np.float16)


def run(dikey: str, batch: int = 32) -> None:
    con = connect()
    vcon = vec_connect()
    done = {r[0] for r in vcon.execute("SELECT DISTINCT rid FROM vec")}
    rids = [r[0] for r in con.execute(DIKEYLER[dikey]) if r[0] not in done]
    log.info("%s: %d karar vektörlenecek (%d zaten var)", dikey, len(rids), len(done))
    if not rids:
        return
    enc = Encoder()
    t0 = time.time()
    # Uzunluğa göre sıralamak padding israfını azaltır.
    for i in range(0, len(rids), 2000):
        block = rids[i:i + 2000]
        q = f"SELECT rid, metin FROM karar WHERE rid IN ({','.join('?' * len(block))})"
        items: list[tuple[int, int, str]] = []
        for rid, blob in con.execute(q, block):
            for j, c in enumerate(chunks(unpack(blob))):
                items.append((rid, j, c))
        items.sort(key=lambda x: len(x[2]))
        for k in range(0, len(items), batch):
            sl = items[k:k + batch]
            embs = enc.encode([x[2] for x in sl])
            vcon.executemany("INSERT OR REPLACE INTO vec(rid, parca, emb) VALUES(?,?,?)",
                             [(x[0], x[1], e.tobytes()) for x, e in zip(sl, embs)])
        vcon.commit()
        n = i + len(block)
        log.info("%d/%d karar, %.1f karar/sn", n, len(rids), n / (time.time() - t0))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dikey", default="is_hukuku", choices=list(DIKEYLER))
    ap.add_argument("--batch", type=int, default=32)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    run(args.dikey, args.batch)


if __name__ == "__main__":
    main()
