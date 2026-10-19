"""Ham karar HTML'ini temiz metne ve üst veriye çevirir."""
from __future__ import annotations

import html
import re
from datetime import date

_BR = re.compile(r"<\s*br\s*/?\s*>|</p\s*>|<p[^>]*>|</li\s*>", re.I)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t ]+")
_NL = re.compile(r"\n\s*\n\s*\n+")
_HEADER = re.compile(r"^.*?\"İçtihat Metni\"\s*", re.S)

_FIELD = {
    "mahkeme": re.compile(r"^\s*MAHKEMES[İI]\s*:\s*(.+)$", re.M),
    "dava_turu": re.compile(r"^\s*DAVA\s+TÜRÜ\s*:\s*(.+)$", re.M),
    "ilk_derece": re.compile(r"^\s*İLK\s+DERECE\s+MAHKEMES[İI]\s*:\s*(.+)$", re.M),
    "suc": re.compile(r"^\s*SUÇ\s*:\s*(.+)$", re.M),
}

# Hüküm fıkrasında geçen sonuç kalıpları. Sıra önemli: "düzeltilerek onanması"
# "onanması"ndan önce denenmeli.
_SONUC = [
    ("duzelterek_onama", re.compile(r"düzeltil(?:erek|mek suretiyle)\s+onanmas", re.I)),
    ("kismen_bozma", re.compile(r"kısmen\s+bozulmas", re.I)),
    ("bozma", re.compile(r"bozulmasına|BOZULMASINA|bozulmasına", re.I)),
    ("onama", re.compile(r"onanmasına|ONANMASINA", re.I)),
    ("esastan_ret", re.compile(r"esastan\s+reddine", re.I)),
    ("temyiz_ret", re.compile(r"temyiz\s+(?:isteminin|dilekçesinin|başvurusunun)\s+reddine", re.I)),
    ("kaldirma", re.compile(r"kaldırılmasına", re.I)),
]


def html_to_text(raw: str) -> str:
    s = _BR.sub("\n", raw)
    s = _TAG.sub(" ", s)
    s = html.unescape(s)
    s = _WS.sub(" ", s)
    s = "\n".join(line.strip() for line in s.split("\n"))
    s = _NL.sub("\n\n", s).strip()
    s = _HEADER.sub("", s, count=1)
    return s


def parse_date(tr: str | None) -> str | None:
    """'13.12.2023' -> '2023-12-13'."""
    if not tr:
        return None
    m = re.match(r"\s*(\d{1,2})[./](\d{1,2})[./](\d{4})", tr)
    if not m:
        return None
    d, mth, y = map(int, m.groups())
    try:
        return date(y, mth, d).isoformat()
    except ValueError:
        return None


def daire_turu(daire: str) -> str:
    d = daire.lower()
    if "genel kurul" in d or "büyük genel" in d or "birleştirme" in d:
        return "kurul"
    if "ceza" in d:
        return "ceza"
    if "hukuk" in d:
        return "hukuk"
    return "diger"


def sonuc(text: str) -> str | None:
    # Hüküm genelde metnin son kısmında; sadece son %30'a bak ki gerekçede
    # alıntılanan başka kararların sonucu karışmasın.
    tail = text[int(len(text) * 0.7):]
    for name, rx in _SONUC:
        if rx.search(tail):
            return name
    return None


def fields(text: str) -> dict[str, str | None]:
    head = text[:1500]
    out: dict[str, str | None] = {}
    for k, rx in _FIELD.items():
        m = rx.search(head)
        out[k] = m.group(1).strip()[:200] if m else None
    return out
