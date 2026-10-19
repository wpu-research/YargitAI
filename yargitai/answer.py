"""Atıf doğrulamalı cevap üretimi.

Kural: cevapta geçen her karar künyesi veritabanında bulunmalı. Bulunmayan
künye cevaptan çıkarılmaz ama açıkça "DOĞRULANAMADI" diye işaretlenir.
ANTHROPIC_API_KEY (veya `ant auth login` profili) yoksa LLM'siz çıkarımsal
özet döner — uydurma riski sıfır.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3

from .db import unpack
from .search import Filtre, Sonuc, hybrid, snippet

log = logging.getLogger("answer")
MODEL = os.environ.get("YARGITAI_MODEL", "claude-opus-5-5")
CTX_CHARS = 6000

SYSTEM = """Sen Türk hukukunda uzman bir araştırma asistanısın ve avukatlara Yargıtay içtihadı konusunda yardım ediyorsun.
Yalnızca sana verilen karar metinlerine dayan. Kendi bilginden karar künyesi, tarih ya da esas/karar numarası yazma.
Her hukuki tespitin sonuna dayandığın kararı köşeli parantezle [K1], [K2] biçiminde ekle.
Kararlar soruyu karşılamıyorsa bunu açıkça söyle.
Önce 2-4 cümlelik kısa bir cevap ver, sonra "İlkeler" başlığı altında maddeler hâlinde kararlardan çıkan ilkeleri yaz.
Kararlar arasında çelişki veya zaman içinde görüş değişikliği varsa ayrıca belirt.
Bu bir hukuki mütalaa değildir; avukatın kendi değerlendirmesine yardımcı bir araştırma özetidir."""

_KUNYE = re.compile(r"(\d{4}/(?:\d{1,2}-)?\d{1,6})\s*(?:E\.|Esas)[\s,]*(?:ve\s*)?(\d{4}/\d{1,6})\s*(?:K\.|Karar)", re.I)
_REF = re.compile(r"\[K(\d+)\]")


def kunye(s: Sonuc | sqlite3.Row) -> str:
    g = s if isinstance(s, dict) else s.__dict__ if hasattr(s, "__dict__") else dict(s)
    t = g.get("karar_tarihi") or ""
    if t:
        y, m, d = t.split("-")
        t = f", {d}.{m}.{y} T."
    return f"Yargıtay {g['daire']}, {g['esas_no']} E., {g['karar_no']} K.{t}"


def verify(con: sqlite3.Connection, text: str, n_ctx: int) -> list[dict]:
    """Cevaptaki [K#] referansları ve serbest künyeleri kontrol eder."""
    issues = []
    for m in _REF.finditer(text):
        if not 1 <= int(m.group(1)) <= n_ctx:
            issues.append({"tur": "gecersiz_referans", "metin": m.group(0)})
    for m in _KUNYE.finditer(text):
        ok = con.execute("SELECT 1 FROM karar WHERE esas_no=? AND karar_no=?", m.groups()).fetchone()
        if not ok:
            issues.append({"tur": "dogrulanamayan_kunye", "metin": m.group(0)})
    return issues


def _llm_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or os.path.exists(os.path.expanduser("~/.config/anthropic")))


def _call_claude(question: str, ctx: list[tuple[str, str]]) -> str:
    import anthropic
    client = anthropic.Anthropic()
    docs = "\n\n".join(f'<karar id="K{i + 1}" kunye="{k}">\n{t}\n</karar>' for i, (k, t) in enumerate(ctx))
    resp = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"],
        extra_body={"fallbacks": "default"},
        messages=[{"role": "user", "content": f"{docs}\n\nSoru: {question}"}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("Model isteği reddetti")
    return "".join(b.text for b in resp.content if b.type == "text")


def answer(con: sqlite3.Connection, question: str, f: Filtre | None = None, k: int = 8) -> dict:
    hits = hybrid(con, question, f, k=k)
    if not hits:
        return {"cevap": "Bu soruya uyan karar bulunamadı.", "kararlar": [], "mod": "bos", "dogrulama": []}
    ctx = []
    for h in hits:
        text = unpack(con.execute("SELECT metin FROM karar WHERE rid=?", (h.rid,)).fetchone()[0])
        ctx.append((kunye(h), text[:CTX_CHARS]))
    kararlar = [{"ref": f"K{i + 1}", "rid": h.rid, "kunye": kunye(h), "sonuc": h.sonuc,
                 "snippet": h.snippet, "atif_alan": h.atif_alan} for i, h in enumerate(hits)]
    if _llm_available():
        try:
            text = _call_claude(question, ctx)
            return {"cevap": text, "kararlar": kararlar, "mod": "llm", "model": MODEL,
                    "dogrulama": verify(con, text, len(ctx))}
        except Exception as e:  # LLM hatası aramayı düşürmesin
            log.warning("LLM çağrısı başarısız, çıkarımsal moda düşülüyor: %s", e)
    # Çıkarımsal mod: her karardan soruya en yakın pasaj, künyesiyle.
    lines = [f"[{c['ref']}] {c['kunye']} — {snippet(t, question, 320)}" for c, (_, t) in zip(kararlar, ctx)]
    return {"cevap": "\n\n".join(lines), "kararlar": kararlar, "mod": "cikarimsal", "dogrulama": []}
