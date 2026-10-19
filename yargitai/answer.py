"""Atıf doğrulamalı cevap üretimi.

Kural: cevapta geçen her karar künyesi veritabanında bulunmalı. Bulunmayan
künye cevaptan çıkarılmaz ama açıkça "DOĞRULANAMADI" diye işaretlenir.
Sağlayıcı: ANTHROPIC_API_KEY (veya `ant auth login` profili) varsa Claude, yoksa
NVIDIA_API_KEY varsa NVIDIA API (OpenAI uyumlu); YARGITAI_LLM=anthropic|nvidia ile
zorlanabilir. Anahtarlar ortamdan ya da proje kökündeki .env'den okunur. Hiçbiri
yoksa LLM'siz çıkarımsal özet döner — uydurma riski sıfır.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
from pathlib import Path

from .anonim import maskele
from .db import unpack
from .search import Filtre, Sonuc, hybrid, snippet

log = logging.getLogger("answer")


def _load_env(path: Path = Path(__file__).resolve().parent.parent / ".env") -> None:
    """KEY=deger satırlarını okur; ortamda zaten olan değişkenleri ezmez."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        k, sep, v = line.strip().partition("=")
        if sep and not k.startswith("#"):
            os.environ.setdefault(k.strip(), v.strip().strip('"\''))


_load_env()
MODEL = os.environ.get("YARGITAI_MODEL", "claude-opus-5-5")
NVIDIA_MODEL = os.environ.get("YARGITAI_NVIDIA_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")
NVIDIA_URL = "https://integrate.api.nvidia.com/v1"
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
_TIRE = str.maketrans({c: "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2212"})  # LLM'ler künyede Unicode tire yazabiliyor


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
    text = text.translate(_TIRE)
    for m in _REF.finditer(text):
        if not 1 <= int(m.group(1)) <= n_ctx:
            issues.append({"tur": "gecersiz_referans", "metin": m.group(0)})
    for m in _KUNYE.finditer(text):
        ok = con.execute("SELECT 1 FROM karar WHERE esas_no=? AND karar_no=?", m.groups()).fetchone()
        if not ok:
            issues.append({"tur": "dogrulanamayan_kunye", "metin": m.group(0)})
    return issues


def _provider() -> str | None:
    forced = os.environ.get("YARGITAI_LLM")
    if forced:
        return forced
    if (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
            or os.path.exists(os.path.expanduser("~/.config/anthropic"))):
        return "anthropic"
    if os.environ.get("NVIDIA_API_KEY"):
        return "nvidia"
    return None


def _llm_available() -> bool:
    return _provider() is not None


def _docs(ctx: list[tuple[str, str]]) -> str:
    return "\n\n".join(f'<karar id="K{i + 1}" kunye="{k}">\n{t}\n</karar>' for i, (k, t) in enumerate(ctx))


def _call_claude(question: str, ctx: list[tuple[str, str]]) -> str:
    import anthropic
    client = anthropic.Anthropic()
    docs = _docs(ctx)
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


def _call_nvidia(question: str, ctx: list[tuple[str, str]]) -> str:
    from openai import OpenAI
    client = OpenAI(base_url=NVIDIA_URL, api_key=os.environ["NVIDIA_API_KEY"], timeout=180)
    resp = client.chat.completions.create(
        model=NVIDIA_MODEL,
        max_tokens=4000,
        temperature=0.2,
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": f"{_docs(ctx)}\n\nSoru: {question}"}],
    )
    choice = resp.choices[0]
    if choice.finish_reason == "content_filter" or not choice.message.content:
        raise RuntimeError(f"Boş/filtreli cevap ({choice.finish_reason})")
    return choice.message.content


def answer(con: sqlite3.Connection, question: str, f: Filtre | None = None, k: int = 8) -> dict:
    hits = hybrid(con, question, f, k=k)
    if not hits:
        return {"cevap": "Bu soruya uyan karar bulunamadı.", "kararlar": [], "mod": "bos", "dogrulama": []}
    ctx = []
    for h in hits:
        text = unpack(con.execute("SELECT metin FROM karar WHERE rid=?", (h.rid,)).fetchone()[0])
        ctx.append((kunye(h), maskele(text[:CTX_CHARS])))  # LLM'e kişisel veri gitmez
    kararlar = [{"ref": f"K{i + 1}", "rid": h.rid, "kunye": kunye(h), "sonuc": h.sonuc,
                 "snippet": h.snippet, "atif_alan": h.atif_alan} for i, h in enumerate(hits)]
    provider = _provider()
    if provider:
        try:
            if provider == "nvidia":
                text, model = _call_nvidia(question, ctx), NVIDIA_MODEL
            else:
                text, model = _call_claude(question, ctx), MODEL
            return {"cevap": text, "kararlar": kararlar, "mod": "llm", "model": model,
                    "dogrulama": verify(con, text, len(ctx))}
        except Exception as e:  # LLM hatası aramayı düşürmesin
            log.warning("LLM çağrısı başarısız, çıkarımsal moda düşülüyor: %s", e)
    # Çıkarımsal mod: her karardan soruya en yakın pasaj, künyesiyle.
    lines = [f"[{c['ref']}] {c['kunye']} — {snippet(t, question, 320)}" for c, (_, t) in zip(kararlar, ctx)]
    return {"cevap": "\n\n".join(lines), "kararlar": kararlar, "mod": "cikarimsal", "dogrulama": []}
