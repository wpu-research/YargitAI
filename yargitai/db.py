"""SQLite şeması ve bağlantı yardımcıları.

Metin zstd ile sıkıştırılmış saklanır; FTS5 indeksi contentless'tır (metnin
ikinci kopyasını tutmaz) — disk kısıtı nedeniyle bilinçli tercih.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import zstandard

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("YARGITAI_DB", ROOT / "data" / "yargitai.db"))

_cctx = zstandard.ZstdCompressor(level=9)
_dctx = zstandard.ZstdDecompressor()

SCHEMA = """
CREATE TABLE IF NOT EXISTS karar (
    rid INTEGER PRIMARY KEY,
    kaynak TEXT NOT NULL,            -- hf-altaidev | karararama | bedesten
    kaynak_id TEXT NOT NULL,         -- kaynaktaki belge id'si
    mahkeme TEXT NOT NULL DEFAULT 'Yargıtay',
    daire TEXT NOT NULL,
    daire_turu TEXT NOT NULL,        -- hukuk | ceza | kurul | diger
    esas_no TEXT,
    karar_no TEXT,
    karar_tarihi TEXT,               -- ISO
    yil INTEGER,
    dava_turu TEXT,
    suc TEXT,
    ilk_mahkeme TEXT,
    sonuc TEXT,
    uzunluk INTEGER,
    metin BLOB NOT NULL,             -- zstd(utf-8)
    eklendi TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (kaynak, kaynak_id)
);
CREATE INDEX IF NOT EXISTS karar_ek ON karar(esas_no, karar_no);
CREATE INDEX IF NOT EXISTS karar_daire_tarih ON karar(daire, karar_tarihi);

CREATE VIRTUAL TABLE IF NOT EXISTS karar_fts USING fts5(
    metin, content='', contentless_delete=1,
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS atif (
    kaynak_rid INTEGER NOT NULL,
    tur TEXT NOT NULL,               -- daire | HGK | CGK | IBK | BGK
    hedef_daire TEXT,
    esas_no TEXT NOT NULL,
    karar_no TEXT NOT NULL,
    tarih TEXT,
    hedef_rid INTEGER                -- çözümlenince dolar
);
CREATE INDEX IF NOT EXISTS atif_kaynak ON atif(kaynak_rid);
CREATE INDEX IF NOT EXISTS atif_hedef ON atif(hedef_rid);
CREATE INDEX IF NOT EXISTS atif_ek ON atif(esas_no, karar_no);

CREATE TABLE IF NOT EXISTS madde_atif (
    rid INTEGER NOT NULL,
    kanun TEXT NOT NULL,
    madde TEXT NOT NULL,
    PRIMARY KEY (rid, kanun, madde)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS madde_atif_km ON madde_atif(kanun, madde);

CREATE TABLE IF NOT EXISTS ingest_durum (
    anahtar TEXT PRIMARY KEY,
    deger TEXT
);
"""


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=60, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA cache_size=-200000")
    con.executescript(SCHEMA)
    return con


def pack(text: str) -> bytes:
    return _cctx.compress(text.encode("utf-8"))


def unpack(blob: bytes) -> str:
    return _dctx.decompress(blob).decode("utf-8")


def get_state(con: sqlite3.Connection, key: str) -> str | None:
    row = con.execute("SELECT deger FROM ingest_durum WHERE anahtar=?", (key,)).fetchone()
    return row[0] if row else None


def set_state(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO ingest_durum(anahtar, deger) VALUES(?, ?) "
        "ON CONFLICT(anahtar) DO UPDATE SET deger=excluded.deger",
        (key, value),
    )
