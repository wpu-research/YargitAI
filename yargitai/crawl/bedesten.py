"""Adalet Bakanlığı Bedesten API istemcisi (mevzuat.adalet.gov.tr'nin arka ucu).

Yargıtay, Danıştay ve UYAP Emsal karar arama siteleri aynı "emsal-karar"
servisine dayanıyor. Bu istemci o servisi doğrudan, JSON olarak kullanır.

Nazik çalışır: istekler arası bekleme, hata/captcha işaretinde üstel geri
çekilme. Yanıt şekli beklenenden farklıysa ham yanıtı loglayıp durur —
sessizce yanlış veri yazmaz.
"""
from __future__ import annotations

import base64
import logging
import random
import time
from dataclasses import dataclass
from datetime import date, timedelta

import httpx

BASE = "https://bedesten.adalet.gov.tr/emsal-karar"
APP = "UyapMevzuat"
HEADERS = {
    "Content-Type": "application/json; charset=utf-8",
    "AdaletApplicationName": APP,
    "Origin": "https://mevzuat.adalet.gov.tr",
    "Referer": "https://mevzuat.adalet.gov.tr/",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) YargitAI-research/0.1 (+research@wpu.edu.tr)",
}

log = logging.getLogger("bedesten")


class ServiceDown(RuntimeError):
    """Gateway servisi kapalı (ör. 'emsal-karar-service 404')."""


class UnexpectedShape(RuntimeError):
    pass


@dataclass(slots=True)
class Hit:
    document_id: str
    daire: str
    esas_no: str | None
    karar_no: str | None
    karar_tarihi: str | None  # dd.mm.yyyy
    item_type: str


class Bedesten:
    def __init__(self, delay: float = 1.2):
        self.delay = delay
        self.client = httpx.Client(headers=HEADERS, timeout=httpx.Timeout(30, read=60))
        self._last = 0.0

    def _post(self, path: str, data: dict, paging: bool = False) -> dict:
        body = {"data": data, "applicationName": APP}
        if paging:
            body["paging"] = True
        backoff = 5.0
        for attempt in range(6):
            wait = self.delay - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait + random.uniform(0, 0.4))
            self._last = time.time()
            try:
                r = self.client.post(BASE + path, json=body)
            except httpx.HTTPError as e:
                log.warning("ağ hatası %s: %s", path, e)
                time.sleep(backoff)
                backoff *= 2
                continue
            if r.status_code == 429 or r.status_code >= 500:
                log.warning("HTTP %s, %0.fs bekleniyor", r.status_code, backoff)
                time.sleep(backoff)
                backoff *= 2
                continue
            js = r.json()
            meta = js.get("metadata") or {}
            if meta.get("FMTY") == "ERROR":
                msg = meta.get("FMTE") or ""
                if "404 NOT_FOUND" in msg or "service" in msg.lower():
                    raise ServiceDown(msg)
                raise RuntimeError(f"Bedesten hata: {msg}")
            return js
        raise RuntimeError(f"{path}: tekrar denemeler tükendi")

    def ping(self) -> bool:
        try:
            self.search(phrase="tazminat", page_size=1)
            return True
        except ServiceDown:
            return False

    def search(self, *, phrase: str = "karar", item_type: str = "YARGITAYKARARI",
               start: date | None = None, end: date | None = None,
               page: int = 1, page_size: int = 100, birim: str | None = None) -> tuple[list[Hit], int]:
        data: dict = {
            "pageSize": page_size,
            "pageNumber": page,
            "itemTypeList": [item_type],
            "phrase": phrase,
            "sortFields": ["KARAR_TARIHI"],
            "sortDirection": "desc",
        }
        if birim:
            data["birimAdi"] = birim
        if start:
            data["kararTarihiStart"] = f"{start.isoformat()}T00:00:00.000Z"
        if end:
            data["kararTarihiEnd"] = f"{end.isoformat()}T23:59:59.999Z"
        js = self._post("/searchDocuments", data, paging=True)
        d = js.get("data")
        if not isinstance(d, dict) or "emsalKararList" not in d:
            raise UnexpectedShape(str(js)[:1500])
        hits = []
        for it in d["emsalKararList"] or []:
            hits.append(Hit(
                document_id=str(it["documentId"]),
                daire=(it.get("birimAdi") or "").strip() or "Bilinmeyen",
                esas_no=it.get("esasNo"),
                karar_no=it.get("kararNo"),
                karar_tarihi=it.get("kararTarihiStr") or it.get("kararTarihi"),
                item_type=(it.get("itemType") or {}).get("name", item_type),
            ))
        return hits, int(d.get("total") or 0)

    def document(self, document_id: str) -> tuple[str, str]:
        """(mime_type, içerik) döner; HTML ise ham HTML."""
        js = self._post("/getDocumentContent", {"documentId": document_id})
        d = js.get("data") or {}
        if "content" not in d:
            raise UnexpectedShape(str(js)[:1500])
        raw = base64.b64decode(d["content"])
        mime = d.get("mimeType") or "text/html"
        if "html" in mime:
            return mime, raw.decode("utf-8", errors="replace")
        return mime, raw.decode("latin-1")  # PDF vb.: çağıran tarafa bırak


def days(start: date, end: date):
    d = end
    while d >= start:
        yield d
        d -= timedelta(days=1)
