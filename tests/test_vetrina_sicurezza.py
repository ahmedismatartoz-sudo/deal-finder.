"""6/11: i file della vetrina non devono uscire dalla cartella js e il proxy foto accetta solo AutoScout."""
from starlette.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Route

from dealfinder.web.app import vetrina_js
from dealfinder.web.vetrina import foto_vetrina

app = Starlette(routes=[Route("/api/vetrina-foto", foto_vetrina), Route("/vetrina/{nome}/js/{p:path}", vetrina_js)])
c = TestClient(app)


def test_js_valido():
    r = c.get("/vetrina/astro/js/build/three.module.js")
    assert r.status_code == 200


def test_js_percorso_assoluto_bloccato():
    for u in ["/vetrina/astro/js//etc/passwd.json", "/vetrina/astro/js//etc/secrets/x.json",
              "/vetrina/astro/js/../../../../etc/x.js", "/vetrina/astro/js/%2Fetc%2Fx.json"]:
        assert c.get(u).status_code == 404, u


def test_foto_host_non_ammessi():
    for u in ["http://img.autoscout24.net/a.jpg", "https://evil.com/a.jpg", "https://autoscout24.net.evil.com/a.jpg",
              "https://user:pw@img.autoscout24.net/a.jpg", "https://img.autoscout24.net:8080/a.jpg", ""]:
        assert c.get("/api/vetrina-foto", params={"u": u}).status_code == 400, u
