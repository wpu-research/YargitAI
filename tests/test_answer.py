import sqlite3
import sys
import types

import pytest

from yargitai import answer
from yargitai.db import SCHEMA, pack
from yargitai.textnorm import fold

METIN = ("MAHKEMESİ : İş Mahkemesi\nDAVA TÜRÜ : Alacak\n"
         "Fazla çalışmanın tanık beyanıyla ispatı halinde hakkaniyet indirimi yapılması gerekir. "
         "Hükmün BOZULMASINA karar verildi.")


@pytest.fixture
def con():
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    cur = c.execute(
        "INSERT INTO karar(kaynak,kaynak_id,daire,daire_turu,esas_no,karar_no,karar_tarihi,yil,sonuc,metin) "
        "VALUES('t','1','9. Hukuk Dairesi','hukuk','2023/1','2023/2','2023-05-03',2023,'bozma',?)", (pack(METIN),))
    c.execute("INSERT INTO karar_fts(rowid, metin) VALUES(?,?)", (cur.lastrowid, fold(METIN)))
    return c


class FakeClient:
    calls: list = []
    reply = ""
    stop_reason = "end_turn"

    def __init__(self, *a, **k):
        self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        FakeClient.calls.append(kw)
        return types.SimpleNamespace(stop_reason=FakeClient.stop_reason,
                                     content=[types.SimpleNamespace(type="text", text=FakeClient.reply)])


@pytest.fixture
def fake_anthropic(monkeypatch):
    mod = types.ModuleType("anthropic")
    mod.Anthropic = FakeClient
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    monkeypatch.setattr(answer, "_llm_available", lambda: True)
    FakeClient.calls.clear()
    FakeClient.stop_reason = "end_turn"
    return FakeClient


def _no_vectors(monkeypatch):
    from yargitai import search
    monkeypatch.setattr(search.VEC, "search", lambda *a, **k: [])


def test_llm_mode_flags_fabricated_kunye(con, fake_anthropic, monkeypatch):
    _no_vectors(monkeypatch)
    fake_anthropic.reply = ("Tanıkla ispatta hakkaniyet indirimi yapılır [K1]. "
                            "Ayrıca 2020/999 E., 2021/888 K. sayılı karar da aynı yöndedir [K7].")
    r = answer.answer(con, "fazla çalışma tanık hakkaniyet indirimi")
    assert r["mod"] == "llm"
    kinds = sorted(i["tur"] for i in r["dogrulama"])
    assert kinds == ["dogrulanamayan_kunye", "gecersiz_referans"]
    kw = fake_anthropic.calls[0]
    assert kw["model"] == answer.MODEL
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["extra_body"] == {"fallbacks": "default"}
    assert "9. Hukuk Dairesi, 2023/1 E., 2023/2 K." in kw["messages"][0]["content"]


def test_llm_refusal_falls_back_to_extractive(con, fake_anthropic, monkeypatch):
    _no_vectors(monkeypatch)
    fake_anthropic.stop_reason = "refusal"
    r = answer.answer(con, "fazla çalışma tanık hakkaniyet indirimi")
    assert r["mod"] == "cikarimsal"
    assert "[K1]" in r["cevap"]


def test_seri_davalar_tek_satir(monkeypatch):
    from yargitai import search
    from yargitai.parse import imza
    _no_vectors(monkeypatch)
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    metinler = [METIN.replace("Fazla", f"{n} TL fazla") for n in (100, 250, 999)] + ["Kıdem tazminatı tanık ispat hakkaniyet farklı bir karar."]
    for i, m in enumerate(metinler):
        cur = c.execute("INSERT INTO karar(kaynak,kaynak_id,daire,daire_turu,esas_no,karar_no,metin,imza) VALUES('t',?,?,?,?,?,?,?)",
                        (str(i), "9. Hukuk Dairesi", "hukuk", f"2023/{i}", f"2023/{i + 10}", pack(m), imza(m)))
        c.execute("INSERT INTO karar_fts(rowid, metin) VALUES(?,?)", (cur.lastrowid, fold(m)))
    hits = search.hybrid(c, "tanık hakkaniyet", k=10)
    assert sorted(h.seri for h in hits) == [1, 3]
