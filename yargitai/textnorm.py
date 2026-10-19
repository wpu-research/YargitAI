"""Türkçe metin normalizasyonu: indeks ve sorgu aynı fonksiyondan geçer."""
from __future__ import annotations

import re

import snowballstemmer

_LOWER = str.maketrans({"I": "ı", "İ": "i"})
# Arama için katlama: ı/i ve şapkalı harfler tek biçime iner. Diakritikler
# FTS5 tokenizer'da (remove_diacritics 2) da katlanıyor; ı ise diakritik
# sayılmadığı için burada elle i'ye çevriliyor.
_FOLD = str.maketrans({"ı": "i", "â": "a", "î": "i", "û": "u"})
_TOKEN = re.compile(r"[0-9a-zçğıöşüâîû]+")
_stemmer = snowballstemmer.stemmer("turkish")

STOP = set(
    "ve veya ile de da ki bu şu o bir için olarak olan gibi daha en çok ne ya "
    "mi mı mu mü ise ancak fakat ama hem her ilgili göre kadar sonra önce".split()
)


def tr_lower(s: str) -> str:
    return s.translate(_LOWER).lower()


def fold(s: str) -> str:
    return tr_lower(s).translate(_FOLD)


def tokens(s: str) -> list[str]:
    return _TOKEN.findall(tr_lower(s))


def stem(tok: str) -> str:
    return _stemmer.stemWord(tok)


def fts_query(q: str) -> str:
    """Kullanıcı sorgusunu FTS5 sorgusuna çevirir.

    "tırnak içi" ifadeler aynen (phrase) aranır; diğer kelimeler köke indirilip
    önek araması yapılır (tazminatının -> tazminat*). Terimler AND ile bağlanır.
    """
    parts: list[str] = []
    for phrase in re.findall(r'"([^"]+)"', q):
        toks = [fold(t) for t in tokens(phrase)]
        if toks:
            parts.append('"' + " ".join(toks) + '"')
    rest = re.sub(r'"[^"]*"', " ", q)
    for t in tokens(rest):
        if t in STOP or len(t) < 2:
            continue
        st = fold(stem(t)) if not t.isdigit() else t
        if len(st) < 3:
            st = fold(t)
        parts.append(f'"{st}"*')
    return " AND ".join(parts)
