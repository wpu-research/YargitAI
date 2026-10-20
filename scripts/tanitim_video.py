"""Canlı siteyi gerçekten kullanarak sesli tanıtım videosu üretir.

    python scripts/tanitim_video.py [--url https://www.yargitai.online] [--out media/yargitai-tanitim.mp4]

Akış: senaryo -> edge-tts ile Türkçe seslendirme (Microsoft'a yalnız senaryo metni gider) ->
Playwright ile her sahnenin ekran kaydı (sahte imleç, gerçek tıklama/yazma) -> ffmpeg ile
sahne süresini ses süresine uydurma, altyazı yakma, birleştirme.

Not: Senaryodaki kapsam/sayı cümleleri o anki canlı veriye göre elle güncellenmelidir (SAHNELER).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

W, H = 1280, 720
VOICE = "tr-TR-EmelNeural"
DILEKCE = ("Davacı vekili olarak sunduğumuz savunmada, fazla çalışma alacağının tanık beyanlarıyla ispatlanabileceği "
           "hususu Yargıtay 9. Hukuk Dairesi'nin 2023/3001 E., 2023/6593 K. sayılı kararında açıkça kabul edilmiştir. "
           "Aynı yönde Yargıtay 9. Hukuk Dairesi'nin 2021/99999 E., 2021/88888 K. sayılı kararı da mevcuttur.")

SAHNELER = [
    ("giris", "YargitAI, Yargıtay kararlarını arayıp aralarındaki atıf ağını görebileceğiniz bağımsız bir araştırma aracı. "
              "Şu an 2020 ile 2023 arasındaki bir milyondan fazla kararı kapsıyor. "
              "Sağdaki ağ gerçek veriyle çiziliyor: her nokta bir karar, her çizgi bir atıf."),
    ("emsal", "Emsal Ara'da olayı kendi cümlenizle yazın. Sistem kök, kelime ve anlamsal aramayı birleştirir; "
              "aynı şablondaki seri kararlar tek satırda toplanır. Bir kararı açınca metnini ve sonucunu görürsünüz."),
    ("asistan", "Sağ alttaki yapay zekâ asistanına soru sorabilirsiniz. Cevap yalnızca veritabanındaki kararlara dayanır "
                "ve cevaptaki her künye kayıtlarla karşılaştırılarak doğrulanır."),
    ("dilekce", "Dilekçe Kontrol, metindeki her karar künyesini veritabanında arar. Kayıtlı olanı doğrular, "
                "olmayanı kayıtta yok diye işaretler; hatalı atıf dilekçeye girmeden yakalanır."),
    ("veri", "Veri sekmesi daire bazında bozma oranlarını, aylık karar hacmini, en çok dayanılan kanun maddelerini "
             "ve daireler arasındaki atıf akışını gösterir."),
    ("kapanis", "YargitAI: atıf ağını görün, her künyeyi doğrulayın. "
                "Hukuki görüş değildir; künyeleri her zaman resmî kaynaktan teyit edin."),
]

IMLEC_JS = """
(() => {
  const c = document.createElement('div');
  c.style.cssText = 'position:fixed;left:-50px;top:-50px;width:22px;height:22px;margin:-11px 0 0 -11px;border-radius:50%;'
    + 'background:rgba(255,255,255,.92);border:2px solid #0C3F48;box-shadow:0 2px 10px rgba(0,0,0,.4);z-index:2147483647;pointer-events:none;transition:transform .12s';
  document.addEventListener('DOMContentLoaded', () => document.body.appendChild(c));
  if (document.body) document.body.appendChild(c);
  addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; }, true);
  addEventListener('mousedown', () => c.style.transform = 'scale(.7)', true);
  addEventListener('mouseup', () => c.style.transform = 'scale(1)', true);
})();
"""


def sh(*a):
    subprocess.run(a, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def sure(p: Path) -> float:
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                          "-of", "csv=p=0", str(p)]).decode())


def seslendir(work: Path) -> dict[str, float]:
    import edge_tts

    async def uret(ad, metin):
        await edge_tts.Communicate(metin, VOICE, rate="-4%").save(str(work / f"{ad}.mp3"))
    async def hepsi():
        await asyncio.gather(*(uret(a, m) for a, m in SAHNELER))
    asyncio.run(hepsi())
    return {a: sure(work / f"{a}.mp3") for a, _ in SAHNELER}


class Sahne:
    """Bir sahnenin kaydı: imleç hareketi + gecikmeler; hızlandırılacak bekleme aralıklarını işaretler."""

    def __init__(self, pg):
        self.pg, self.t0, self.hizli = pg, time.time(), []

    def t(self): return time.time() - self.t0

    def etkin(self):
        """Hızlandırılacak aralıklar çıkarılmış (kurgudaki) süre."""
        return self.t() - sum((e - a) - hizli_sure(e - a) for a, e in self.hizli)

    def doldur(self, hedef):
        """Kurgudaki süre `hedef`e varana kadar bekle (anlatım bitmeden sahne bitmesin)."""
        while self.etkin() < hedef:
            self.bekle(.25)

    def kaydir(self, y):
        self.pg.evaluate(f"window.scrollTo({{top:{y},behavior:'smooth'}})")

    def bekle(self, sn): self.pg.wait_for_timeout(int(sn * 1000))

    def git(self, sel, yav=True):
        b = self.pg.locator(sel).first.bounding_box()
        self.pg.mouse.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2, steps=28 if yav else 4)

    def tikla(self, sel):
        self.git(sel); self.bekle(.25); self.pg.locator(sel).first.click(); self.bekle(.3)

    def yaz(self, sel, metin, gecikme=62):
        self.tikla(sel); self.pg.keyboard.type(metin, delay=gecikme)


def hizli_sure(uzun: float) -> float:
    return max(2.0, min(4.0, uzun / 8))


def kaydet(url: str, work: Path, sure_hedef: dict[str, float]) -> dict[str, dict]:
    bilgi: dict[str, dict] = {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        for ad, _ in SAHNELER:
            ctx = b.new_context(viewport={"width": W, "height": H}, record_video_dir=str(work / "ham"),
                                record_video_size={"width": W, "height": H})
            ctx.add_init_script(IMLEC_JS)
            pg = ctx.new_page(); s = Sahne(pg)
            pg.goto(url, wait_until="domcontentloaded"); s.bekle(1.2)
            pg.wait_for_function("document.querySelector('#st-karar').textContent.trim()!=='—'", timeout=90000)
            s.bekle(.3); hazir = max(0.0, s.t() - 0.2)

            bas = s.t(); hedef = sure_hedef[ad] + 0.5 + 0.5
            if ad == "giris":
                s.bekle(1.6)
                pg.mouse.move(1040, 200, steps=10)
                pg.mouse.down(); pg.mouse.move(900, 230, steps=45); pg.mouse.up(); s.bekle(1.6)
                pg.mouse.down(); pg.mouse.move(1130, 170, steps=50); pg.mouse.up(); s.bekle(2.2)
                pg.mouse.move(1100, 240, steps=25); s.bekle(2.0)       # düğüm üstünde künye ipucu
                pg.mouse.down(); pg.mouse.move(980, 150, steps=50); pg.mouse.up(); s.bekle(1.5)
                pg.mouse.move(640, 300, steps=30); s.bekle(0.5)
                s.kaydir(380); s.bekle(3.0); s.kaydir(0)
            elif ad == "emsal":
                s.yaz("#q", "haklı nedenle fesih fazla mesai ispat")
                s.tikla("#f-ara .btn"); gonder = s.t()
                pg.wait_for_selector(".hit", timeout=90000); cevap = s.t(); s.bekle(1.2)
                if cevap - gonder > 4: s.hizli.append((gonder + 1.0, cevap - 0.3))
                s.kaydir(470); s.bekle(3.0)
                s.tikla(".hit >> nth=1"); s.bekle(3.0)
                pg.mouse.wheel(0, 260); s.bekle(2.0)
            elif ad == "asistan":
                s.tikla("#as-btn"); s.bekle(1.0)
                s.yaz("#as-q", "İşçi fazla mesaiyi tanıkla ispatlayabilir mi?", 55)
                pg.keyboard.press("Enter"); gonder = s.t()
                pg.wait_for_selector(".as-b.ai .as-ek", timeout=170000); cevap = s.t()
                s.bekle(1.0)
                if cevap - gonder > 4: s.hizli.append((gonder + 1.2, cevap - 0.3))
                s.bekle(2.2)
                pg.evaluate("document.querySelector('#as-ms').scrollTo({top:99999,behavior:'smooth'})")  # mühür ve künyeler
                s.bekle(4.0)
            elif ad == "dilekce":
                s.tikla("nav button[data-tab=dilekce]"); s.bekle(.8)
                s.tikla("#dilekce-metin"); pg.fill("#dilekce-metin", DILEKCE); s.bekle(1.4)
                s.tikla("#f-dilekce .btn")
                pg.wait_for_selector("#dilekce-kunye .chip", timeout=90000); s.bekle(.8)
                s.kaydir(440); s.bekle(3.5)
                pg.mouse.move(420, 520, steps=25)
            elif ad == "veri":
                s.tikla("nav button[data-tab=istatistik]")
                pg.wait_for_selector("#stats .card.wide", timeout=90000); s.bekle(1.5)
                for _ in range(7):
                    pg.mouse.wheel(0, 260); s.bekle(.55)
                s.bekle(.6)
                for _ in range(4):
                    pg.mouse.wheel(0, 340); s.bekle(.55)
            elif ad == "kapanis":
                s.tikla("nav button[data-tab=ara]"); s.bekle(1.0)
                pg.evaluate("scrollTo(0,0)"); s.bekle(1.0)
                pg.mouse.down(); pg.mouse.move(1000, 200, steps=10); pg.mouse.move(1150, 230, steps=40); pg.mouse.up()
            s.doldur(hedef - 0.2 + hazir)
            s.bekle(0.6)
            bilgi[ad] = {"sure": s.t(), "hizli": s.hizli, "bas": hazir}
            ctx.close()
            ham = sorted((work / "ham").glob("*.webm"), key=lambda f: f.stat().st_mtime)[-1]
            ham.rename(work / f"{ad}.webm")
        b.close()
    return bilgi


def srt_zaman(t: float) -> str:
    return f"{int(t//3600):02d}:{int(t%3600//60):02d}:{int(t%60):02d},{int(t%1*1000):03d}"


def altyazi(metin: str, toplam: float, bas: float) -> list[tuple[float, float, str]]:
    """Cümleleri karakter oranında zamanlar; 2 satıra böler."""
    import re
    parca = [x.strip() for x in re.split(r"(?<=[.;:])\s+", metin) if x.strip()]
    n = sum(len(x) for x in parca); t = bas; out = []
    for x in parca:
        d = toplam * len(x) / n
        words = x.split(); mid = len(words) // 2
        satir = x if len(x) < 60 else " ".join(words[:mid]) + "\n" + " ".join(words[mid:])
        out.append((t, t + d, satir)); t += d
    return out


def birlestir(work: Path, out: Path, ses: dict[str, float], bilgi: dict[str, dict]):
    LEAD, TAIL = 0.5, 0.5
    parcalar, srt, toplam = [], [], 0.0
    for i, (ad, metin) in enumerate(SAHNELER):
        hedef = LEAD + ses[ad] + TAIL
        vid, vsure = work / f"{ad}.webm", sure(work / f"{ad}.webm")
        # bekleme aralıklarını hızlandır: [0,a] + [a,e]/hız + [e,son]
        bas = bilgi[ad]["bas"]
        flt = f"[0:v]trim=start={bas:.2f},setpts=PTS-STARTPTS,fps=25,scale=1280:720,setsar=1,split=3[a][b][c];"
        hz = bilgi[ad]["hizli"]
        if hz:
            a, e = hz[0][0] - bas, min(hz[0][1], vsure - .1) - bas
            hedef_bek = hizli_sure(e - a)
            flt += (f"[a]trim=0:{a},setpts=PTS-STARTPTS[v1];[b]trim={a}:{e},setpts=(PTS-STARTPTS)*{hedef_bek/(e-a):.4f}[v2];"
                    f"[c]trim={e},setpts=PTS-STARTPTS[v3];[v1][v2][v3]concat=n=3:v=1:a=0[v]")
        else:
            flt = f"[0:v]trim=start={bas:.2f},setpts=PTS-STARTPTS,fps=25,scale=1280:720,setsar=1[v]"
        norm = work / f"{ad}_n.mp4"
        sh("ffmpeg", "-y", "-i", str(vid), "-filter_complex", flt, "-map", "[v]", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "20", "-pix_fmt", "yuv420p", str(norm))
        nsure = sure(norm)
        # süreyi ses süresine uydur: kısaysa son kareyi tut, uzunsa 1.35x'e kadar hızlandır, sonra kes
        f = max(1.0, min(1.35, nsure / hedef)) if nsure > hedef else 1.0
        vf = f"setpts=PTS/{f:.4f},tpad=stop_mode=clone:stop_duration={hedef:.2f}"
        fin = work / f"{ad}_f.mp4"
        sh("ffmpeg", "-y", "-i", str(norm), "-vf", vf, "-t", f"{hedef:.2f}", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "20", "-pix_fmt", "yuv420p", "-r", "25", str(fin))
        aud = work / f"{ad}_a.m4a"
        sh("ffmpeg", "-y", "-f", "lavfi", "-t", f"{LEAD}", "-i", "anullsrc=r=44100:cl=mono", "-i", str(work / f"{ad}.mp3"),
           "-filter_complex", f"[1:a]aresample=44100,apad=pad_dur={TAIL}[x];[0:a][x]concat=n=2:v=0:a=1[o]",
           "-map", "[o]", "-t", f"{hedef:.2f}", "-c:a", "aac", "-b:a", "160k", str(aud))
        seg = work / f"{ad}_s.mp4"
        sh("ffmpeg", "-y", "-i", str(fin), "-i", str(aud), "-c:v", "copy", "-c:a", "copy", "-shortest", str(seg))
        parcalar.append(seg)
        for a, b_, t in altyazi(metin, ses[ad], toplam + LEAD):
            srt.append((a, b_, t))
        toplam += hedef
    liste = work / "liste.txt"
    liste.write_text("".join(f"file '{p}'\n" for p in parcalar))
    ham = work / "birlesik.mp4"
    sh("ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(liste), "-c", "copy", str(ham))
    (work / "alt.srt").write_text("".join(f"{i}\n{srt_zaman(a)} --> {srt_zaman(b)}\n{t}\n\n" for i, (a, b, t) in enumerate(srt, 1)))
    out.parent.mkdir(parents=True, exist_ok=True)
    stil = "FontName=DejaVu Sans,FontSize=12,PrimaryColour=&HFFFFFF&,OutlineColour=&H3A2A06&,BorderStyle=1,Outline=2,Shadow=0,MarginV=34"
    sh("ffmpeg", "-y", "-i", str(ham), "-vf", f"subtitles={work/'alt.srt'}:force_style='{stil}'", "-c:v", "libx264",
       "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", str(out))
    return toplam


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="https://www.yargitai.online")
    ap.add_argument("--out", default="media/yargitai-tanitim.mp4")
    ap.add_argument("--work", default="/tmp/yargitai-video")
    a = ap.parse_args()
    work = Path(a.work); (work / "ham").mkdir(parents=True, exist_ok=True)
    ses = seslendir(work); print("ses süreleri:", {k: round(v, 1) for k, v in ses.items()})
    bilgi = kaydet(a.url, work, ses); print("kayıt:", json.dumps({k: round(v["sure"], 1) for k, v in bilgi.items()}))
    t = birlestir(work, Path(a.out), ses, bilgi)
    print(f"bitti: {a.out} ({t:.0f} sn)")


if __name__ == "__main__":
    main()
