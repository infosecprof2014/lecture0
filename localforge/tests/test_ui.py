"""Phase 5: the web interface is served, and the browser protections work."""

from __future__ import annotations


def test_index_and_assets_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert '<meta name="color-scheme" content="light dark">' in r.text
    assert 'src="/ui/app.js"' in r.text
    for path, kind in (("/ui/app.js", "javascript"), ("/ui/style.css", "css")):
        a = client.get(path)
        assert a.status_code == 200 and kind in a.headers["content-type"]


def test_ui_never_uses_innerhtml():
    """Model output must be inserted as text, never parsed as HTML."""
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "ui" / "app.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js.replace("never innerHTML", "") and "insertAdjacentHTML" not in js
    assert "http://" not in js and "https://" not in js  # nothing loaded from the internet


def test_foreign_host_refused(client):
    r = client.get("/health", headers={"host": "evil.example"})
    assert r.status_code == 400 and r.json()["error"] == "bad_host"
    assert client.get("/health", headers={"host": "127.0.0.1:8000"}).status_code == 200
    assert client.get("/health", headers={"host": "localhost:8000"}).status_code == 200


def test_cross_site_post_refused(client, fake):
    r = client.post("/chat", json={"message": "hi"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["error"] == "bad_origin"
    r = client.post("/tasks", json={"request": "x"}, headers={"origin": "http://localhost:3000"})
    assert r.status_code == 403  # another local site on a different port
    assert fake.chat_requests == []  # nothing reached the model


def test_same_origin_post_allowed(client):
    with client.stream("POST", "/chat", json={"message": "hi"},
                       headers={"origin": "http://127.0.0.1:8000"}) as r:
        assert r.status_code == 200
        r.read()


def test_cross_site_get_still_allowed_but_harmless(client):
    # Reading is allowed (the browser's same-origin policy hides the answer from the other site).
    assert client.get("/health", headers={"origin": "https://evil.example"}).status_code == 200
