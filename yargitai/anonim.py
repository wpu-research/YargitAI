"""KVKK için gösterim anında maskeleme.

Kaynak anonimleştirmesi bazı kişisel verileri kaçırıyor. Ham metin
veritabanında değişmeden kalır; API'den çıkan ve LLM'e giden her metin
buradan geçer. Kişi adları kaynakta çoğunlukla "..." ile maskelidir; kaçan
adları yakalamak bu katmanın kapsamı dışında (sonraki adım: NER modeli).
"""
from __future__ import annotations

import re


def _tckn_gecerli(s: str) -> bool:
    d = [int(c) for c in s]
    if d[0] == 0:
        return False
    if (sum(d[0:9:2]) * 7 - sum(d[1:8:2])) % 10 != d[9]:
        return False
    return sum(d[:10]) % 10 == d[10]


_TCKN = re.compile(r"(?<!\d)([1-9]\d{10})(?!\d)")
_IBAN = re.compile(r"\bTR\s?\d{2}(?:\s?\d{4}){5}\s?\d{2}\b", re.I)
_TEL = re.compile(r"(?<!\d)(?:\+90\s?|0)5\d{2}[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}(?!\d)")
_EPOSTA = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PLAKA = re.compile(r"\b(?:0[1-9]|[1-7]\d|8[01])\s?[A-PR-VYZ]{1,3}\s?\d{2,4}\b")


def maskele(text: str) -> str:
    text = _IBAN.sub("[IBAN]", text)
    text = _EPOSTA.sub("[E-POSTA]", text)
    text = _TCKN.sub(lambda m: "[TCKN]" if _tckn_gecerli(m.group(1)) else m.group(1), text)
    text = _TEL.sub("[TELEFON]", text)
    text = _PLAKA.sub("[PLAKA]", text)
    return text
