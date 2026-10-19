import sqlite3

from yargitai import extract, parse
from yargitai.answer import verify
from yargitai.db import SCHEMA, pack
from yargitai.textnorm import fold, fts_query

HTML = (
    '<html><body><b><font>9. Hukuk Dairesi 2021/5861 E. , 2023/6446 K.</font></b><br>'
    '<b>"İçtihat Metni"</b><p>MAHKEMESİ\t: İş Mahkemesi<br>DAVA TÜRÜ\t: Alacak<br><br>'
    "Dairemizin 03.05.2023 tarihli ve 2023/3001 Esas, 2023/6593 Karar sayılı kararında açıklandığı üzere; "
    "Yargıtay Hukuk Genel Kurulunun 12.03.2019 tarihli 2016/9-1234 E., 2019/567 K. sayılı kararı; "
    "İstanbul 7. İş Mahkemesinin 2015/61 E. ve 2016/374 K. sayılı dosyası; "
    "4857 sayılı İş Kanunu'nun 17. maddesi ve HMK'nın 370/1. maddesi uyarınca "
    "hükmün ONANMASINA, 13.12.2023 tarihinde oy birliğiyle karar verildi.</p></body></html>"
)


def test_html_to_text_strips_header_and_tags():
    t = parse.html_to_text(HTML)
    assert t.startswith("MAHKEMESİ")
    assert "<" not in t
    assert parse.fields(t)["dava_turu"] == "Alacak"


def test_sonuc_and_dates():
    t = parse.html_to_text(HTML)
    assert parse.sonuc(t) == "onama"
    assert parse.parse_date("13.12.2023") == "2023-12-13"
    assert parse.parse_date("31.02.2023") is None
    assert parse.daire_turu("Hukuk Genel Kurulu") == "kurul"


def test_karar_atiflari_courts():
    t = parse.html_to_text(HTML)
    atif = {(a.tur, a.daire, a.esas, a.karar) for a in extract.karar_atiflari(t, "9. Hukuk Dairesi")}
    assert ("daire", "9. Hukuk Dairesi", "2023/3001", "2023/6593") in atif
    assert ("HGK", "Hukuk Genel Kurulu", "2016/9-1234", "2019/567") in atif
    # Alt derece mahkeme dosyası Yargıtay atfı sayılmamalı.
    assert not any(a[2] == "2015/61" for a in atif)


def test_ibk_detection():
    t = ("Yargıtay İçtihatları Birleştirme Büyük Genel Kurulu, 09.05.1960 tarihli ve "
         "1960/21 Esas, 1960/9 Karar sayılı karar")
    a = extract.karar_atiflari(t, "9. Hukuk Dairesi")
    assert a and a[0].tur == "IBK"


def test_bam_daire_not_yargitay():
    t = "Ankara Bölge Adliye Mahkemesi 9. Hukuk Dairesinin 2022/815 Esas, 2022/1226 Karar sayılı kararı"
    assert extract.karar_atiflari(t, "9. Hukuk Dairesi") == []


def test_madde_atiflari():
    t = parse.html_to_text(HTML)
    m = {(x.kanun, x.madde) for x in extract.madde_atiflari(t)}
    assert ("4857", "17") in m
    assert ("6100", "370") in m


def test_turkish_fold_and_query():
    assert fold("IŞIK İşçi") == "işik işçi"
    q = fts_query('işçinin "haklı nedenle" fesih')
    assert '"hakli nedenle"' in q
    assert '"işçi"*' in q


def test_verify_flags_unknown_kunye():
    con = sqlite3.connect(":memory:")
    con.executescript(SCHEMA)
    con.execute("INSERT INTO karar(kaynak,kaynak_id,daire,daire_turu,esas_no,karar_no,metin) VALUES('t','1','9. Hukuk Dairesi','hukuk','2023/1','2023/2',?)", (pack("x"),))
    issues = verify(con, "Bkz. [K1] ve 2023/1 E., 2023/2 K. ile 2020/9 E., 2020/10 K. ve [K9]", n_ctx=3)
    kinds = sorted(i["tur"] for i in issues)
    assert kinds == ["dogrulanamayan_kunye", "gecersiz_referans"]
