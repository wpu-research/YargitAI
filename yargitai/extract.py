"""Karar metninden deterministik atıf çıkarımı (LLM yok).

İki tür kenar üretir:
- karar -> karar atıfları (Yargıtay daireleri, HGK, CGK, İBK)
- karar -> kanun maddesi atıfları
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_NUM = r"(\d{4}/(?:\d{1,2}-)?\d{1,6})"
_DATE = r"(\d{1,2}[./]\d{1,2}[./]\d{4})"
_CITE = re.compile(
    r"(?:" + _DATE + r"\s*(?:tarih(?:li|inde)?\s*(?:ve\s*)?)?)?"
    + _NUM + r"\s*(?:E\.|Esas)[\s,]*(?:ve\s*)?" + _NUM + r"\s*(?:K\.|Karar)",
    re.I,
)
_CTX_WINDOW = 120

_DAIRE = re.compile(r"(\d{1,2})\s*\.?\s*(Hukuk|Ceza)\s+Daire", re.I)


@dataclass(slots=True)
class KararAtfi:
    tur: str  # daire | HGK | CGK | IBK | BGK
    daire: str | None
    esas: str
    karar: str
    tarih: str | None


@dataclass(slots=True)
class MaddeAtfi:
    kanun: str
    madde: str


def _court(ctx: str, own_daire: str) -> tuple[str, str | None] | None:
    c = ctx.lower()
    # Bağlamın sonuna en yakın mahkeme ifadesi geçerli: "...Mahkemesinin ...
    # kararı Dairemizin ..." gibi durumlarda son geçen ifade belirleyici.
    cands: list[tuple[int, tuple[str, str | None] | None]] = []
    for kw, val in (
        ("hukuk genel kurul", ("HGK", "Hukuk Genel Kurulu")),
        ("ceza genel kurul", ("CGK", "Ceza Genel Kurulu")),
        ("içtihadı birleştirme", ("IBK", "İçtihadı Birleştirme")),
        ("içtihatları birleştirme", ("IBK", "İçtihadı Birleştirme")),
        ("büyük genel kurul", ("BGK", "Büyük Genel Kurul")),
        ("dairemiz", ("daire", own_daire)),
    ):
        i = c.rfind(kw)
        if i >= 0:
            cands.append((i, val))
    for m in _DAIRE.finditer(ctx):
        pre = c[max(0, m.start() - 40):m.start()]
        # "Bölge Adliye Mahkemesi 3. Hukuk Dairesi" Yargıtay değildir.
        if "bölge" in pre or "adliye" in pre:
            cands.append((m.start(), None))
        else:
            cands.append((m.start(), ("daire", f"{m.group(1)}. {m.group(2).title()} Dairesi")))
    for kw in ("mahkemesi", "müdürlüğü", "savcılığı", "idare"):
        i = c.rfind(kw)
        if i >= 0:
            cands.append((i, None))
    if not cands:
        return None
    best = max(cands, key=lambda x: x[0])[1]
    # "İçtihadı Birleştirme Büyük Genel Kurulu" bir İBK'dır, idari BGK kararı değil.
    if best and best[0] == "BGK" and "birleştirme" in c[-90:]:
        return ("IBK", "İçtihadı Birleştirme")
    return best


def karar_atiflari(text: str, own_daire: str) -> list[KararAtfi]:
    out: list[KararAtfi] = []
    seen: set[tuple[str, str]] = set()
    for m in _CITE.finditer(text):
        ctx = text[max(0, m.start() - _CTX_WINDOW):m.start()]
        court = _court(ctx, own_daire)
        if court is None:
            continue
        esas, karar = m.group(2), m.group(3)
        if (esas, karar) in seen:
            continue
        seen.add((esas, karar))
        out.append(KararAtfi(court[0], court[1], esas, karar, m.group(1)))
    return out


KISALTMA = {
    "tbk": "6098", "tmk": "4721", "hmk": "6100", "cmk": "5271", "tck": "5237",
    "iik": "2004", "ttk": "6102", "hukk": "1086", "cmuk": "1412", "bk": "818",
    "ik": "4857", "aak": "1136", "kmk": "634", "tkhk": "6502", "iyuk": "2577",
}
_KANUN_NO = re.compile(r"\b(\d{3,4})\s+sayılı\b", re.I)
_KISA = re.compile(r"\b(TBK|TMK|HMK|CMK|TCK|İİK|TTK|HUMK|CMUK|BK|AAK|KMK|TKHK|İYUK)\b")
_MADDE = re.compile(
    r"(?<![\d/-])(\d{1,4})(?:\s*/\s*(?:\d{1,2}|[IVX]{1,5}))?(?:\s*-\s*(?:[a-zçğışöü]|\d{1,2})(?![a-zçğışöü]))*\s*(?:\.|'?\s*(?:inci|ıncı|nci|ncı|üncü|uncu|nın|nin|nun|nün|ün|in|ın|un))?\s*madde",
    re.I,
)


def _ascii_key(s: str) -> str:
    return s.lower().replace("i̇", "i").replace("ı", "i").replace("İ", "i")


def madde_atiflari(text: str) -> list[MaddeAtfi]:
    out: set[tuple[str, str]] = set()
    anchors: list[tuple[int, str]] = [(m.end(), m.group(1)) for m in _KANUN_NO.finditer(text)]
    for m in _KISA.finditer(text):
        no = KISALTMA.get(_ascii_key(m.group(1)))
        if no:
            anchors.append((m.end(), no))
    for pos, no in anchors:
        window = text[pos:pos + 90]
        mm = _MADDE.search(window)
        # Başka bir kanun adı araya girmişse bu madde o kanuna aittir.
        if mm and not _KANUN_NO.search(window[:mm.start()]):
            out.add((no, mm.group(1)))
        else:
            out.add((no, ""))
    return [MaddeAtfi(k, m) for k, m in sorted(out)]
