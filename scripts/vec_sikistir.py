"""vectors.db'yi rowid tablosuna yeniden yazar: ~1 GB → ~0,5 GB (Railway Hobby 5 GB volume için).

    python scripts/vec_sikistir.py data/vectors.db hedef/vectors.db

2 KB'lık float16 blob'lar 4 KB sayfada satır başına bir sayfa harcıyor (WITHOUT ROWID'de taşma
sayfası, rowid tablosunda iki satır sığmıyor). 64 KB sayfalı rowid tablosunda bir sayfaya ~31 satır girer.
Şema (rid, parca) birincil anahtarını korur; okuyan kod değişmez.
"""
import sqlite3
import sys

src, dst = sys.argv[1], sys.argv[2]
d = sqlite3.connect(dst)
d.execute("PRAGMA page_size=65536")
d.execute("PRAGMA journal_mode=OFF")
d.execute("CREATE TABLE vec (rid INTEGER, parca INTEGER, emb BLOB, PRIMARY KEY(rid, parca))")
d.execute("ATTACH ? AS s", (src,))
d.execute("INSERT INTO vec SELECT rid, parca, emb FROM s.vec ORDER BY rid, parca")
d.commit()
d.execute("DETACH s")
d.execute("VACUUM")
print(d.execute("SELECT COUNT(*) FROM vec").fetchone()[0], "parça")
