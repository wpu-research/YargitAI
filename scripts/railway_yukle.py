"""Büyük dosyayı `railway ssh` üzerinden Railway volume'una yükler; kopmaya dayanıklı.

    python scripts/railway_yukle.py data/yargitai.db /data/yargitai.db.yeni --service yargitai

Dosya parçalara bölünür, her parça zstd ile sıkıştırılıp ayrı bir ssh oturumuyla gönderilir.
Sunucu parçayı açar, sha256'sını doğrular ve hedef dosyada kendi ofsetine yazar: sunucuda
sıkıştırılmış ara kopya tutulmaz (5 GB'lık Hobby volume'una ikinci kopya sığmaz).
Biten parçalar `<yerel>.yukleme.json`'a yazılır; betik yeniden çalıştırılırsa kaldığı yerden devam
eder. Sonda iki taraftaki parça özetleri karşılaştırılır, farklı olanlar yeniden gönderilir.
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures as cf
import hashlib
import json
import os
import subprocess
import sys
import time

import zstandard

# Sunucuda çalışan kod: argv = hedef, ofset, beklenen sha256. stdin = zstd parça.
_YAZ = """
import hashlib, os, sys, zstandard
hedef, ofset, beklenen = sys.argv[1], int(sys.argv[2]), sys.argv[3]
veri = zstandard.ZstdDecompressor().stream_reader(sys.stdin.buffer).read()
if hashlib.sha256(veri).hexdigest() != beklenen:
    print("SHA_HATA", len(veri)); sys.exit(1)
fd = os.open(hedef, os.O_WRONLY)
os.pwrite(fd, veri, ofset); os.fsync(fd); os.close(fd)
print("TAMAM")
"""
# argv = hedef, boyut: dosyayı yoksa oluşturur, boyutunu ayarlar (seyrek dosya).
_HAZIRLA = """
import os, sys
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT, 0o644); os.ftruncate(fd, int(sys.argv[2])); os.close(fd)
print("TAMAM")
"""
# argv = hedef, parça boyutu: her parçanın sha256'sı, satır satır.
_OZET = """
import hashlib, sys
with open(sys.argv[1], "rb") as f:
    while b := f.read(int(sys.argv[2])):
        print(hashlib.sha256(b).hexdigest())
"""


def uzak(service: str, kod: str, *args: str, stdin: bytes | None = None, timeout: int = 3600) -> str:
    py = f"import base64;exec(base64.b64decode('{base64.b64encode(kod.encode()).decode()}'))"
    r = subprocess.run(["railway", "ssh", "--service", service, "--", "python", "-c", py, *args],
                       input=stdin, capture_output=True, timeout=timeout)
    return r.stdout.decode(errors="replace")


def parca_oku(yol: str, i: int, boy: int) -> bytes:
    with open(yol, "rb") as f:
        f.seek(i * boy)
        return f.read(boy)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("yerel")
    ap.add_argument("hedef")
    ap.add_argument("--service", required=True)
    ap.add_argument("--parca-mb", type=int, default=128)
    ap.add_argument("--paralel", type=int, default=3)
    a = ap.parse_args()

    boy = a.parca_mb << 20
    toplam = os.path.getsize(a.yerel)
    n = (toplam + boy - 1) // boy
    durum_yolu = a.yerel + ".yukleme.json"
    durum = json.load(open(durum_yolu)) if os.path.exists(durum_yolu) else {}
    if durum.get("hedef") != a.hedef or durum.get("boy") != boy or durum.get("toplam") != toplam:
        durum = {"hedef": a.hedef, "boy": boy, "toplam": toplam, "biten": {}}
    if "TAMAM" not in uzak(a.service, _HAZIRLA, a.hedef, str(toplam), timeout=300):
        sys.exit("hedef dosya hazırlanamadı")

    def gonder(i: int) -> tuple[int, str | None]:
        veri = parca_oku(a.yerel, i, boy)
        sha = hashlib.sha256(veri).hexdigest()
        paket = zstandard.ZstdCompressor(level=6, threads=-1).compress(veri)
        for deneme in range(4):
            try:
                if "TAMAM" in uzak(a.service, _YAZ, a.hedef, str(i * boy), sha, stdin=paket):
                    return i, sha
            except subprocess.TimeoutExpired:
                pass
            time.sleep(5 * (deneme + 1))
        return i, None

    t0 = time.time()
    for tur in range(3):
        bekleyen = [i for i in range(n) if str(i) not in durum["biten"]]
        if bekleyen:
            print(f"tur {tur + 1}: {len(bekleyen)}/{n} parça gönderilecek", flush=True)
        with cf.ThreadPoolExecutor(a.paralel) as ex:
            for i, sha in ex.map(gonder, bekleyen):
                if sha:
                    durum["biten"][str(i)] = sha
                    json.dump(durum, open(durum_yolu, "w"))
                biten = len(durum["biten"])
                print(f"  parça {i + 1}/{n} {'tamam' if sha else 'BAŞARISIZ'} · {biten}/{n} · "
                      f"{(time.time() - t0) / 60:.0f} dk", flush=True)
        # Doğrulama: yerel dosya yükleme sırasında değişmiş olabilir; iki tarafı parça parça karşılaştır.
        uzak_ozet = uzak(a.service, _OZET, a.hedef, str(boy), timeout=1800).split()
        yerel_ozet = [hashlib.sha256(parca_oku(a.yerel, i, boy)).hexdigest() for i in range(n)]
        fark = [i for i in range(n) if i >= len(uzak_ozet) or uzak_ozet[i] != yerel_ozet[i]]
        if not fark:
            print(f"DOĞRULANDI: {a.hedef} ({toplam:,} bayt, {n} parça)")
            os.remove(durum_yolu)
            return
        print(f"{len(fark)} parça farklı, yeniden gönderilecek: {fark}")
        for i in fark:
            durum["biten"].pop(str(i), None)
        json.dump(durum, open(durum_yolu, "w"))
    sys.exit("3 turda doğrulanamadı")


if __name__ == "__main__":
    main()
