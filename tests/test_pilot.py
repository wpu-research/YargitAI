import importlib


def test_davet_ile_giris(monkeypatch, tmp_path):
    monkeypatch.setenv("YARGITAI_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("YARGITAI_DAVETLER", "abc123:Av. Deneme")
    monkeypatch.setenv("YARGITAI_GIZLI", "test-gizli")
    import yargitai.db
    importlib.reload(yargitai.db)
    import yargitai.api as api
    importlib.reload(api)
    from fastapi.testclient import TestClient
    c = TestClient(api.app, base_url="https://test")
    assert c.get("/api/istatistik").status_code == 401
    assert c.get("/", follow_redirects=False).headers["location"] == "/giris"
    assert c.post("/giris", data={"kod": "yanlis"}).status_code == 401
    r = c.post("/giris", data={"kod": "abc123"}, follow_redirects=False)
    assert r.status_code == 303
    assert c.get("/api/istatistik").status_code == 200
    c.post("/api/geri-bildirim", json={"sorgu": "fesih", "rid": 1, "puan": 1})
    assert api.con.execute("SELECT kullanici, puan FROM geri_bildirim").fetchone()[:] == ("Av. Deneme", 1)
