"""Tests für das Add-on whatsapp_archiv (Server, Zugriffsschutz, Archivsuche)."""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="wa_test_"))
os.environ["WA_SEARCH_ROOTS"] = f"{TMP / 'media'}:{TMP / 'share'}"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "whatsapp_archiv"))

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from app import server  # noqa: E402


def baue_archiv(basis: Path):
    a = basis / "Backup" / "iPhone" / "WhatsApp-Archiv"
    (a / "viewer" / "data").mkdir(parents=True)
    (a / "viewer" / "data" / "chats.js").write_text('WA_CHATS=[{"id":1,"n":"Test"}];')
    (a / "Medien" / "Test" / "2025").mkdir(parents=True)
    (a / "Medien" / "Test" / "2025" / "bild.jpg").write_bytes(b"\xff\xd8" + b"x" * 5000)
    (a / "Medien" / "Test" / "2025" / "ton.opus").write_bytes(b"OggS" + b"y" * 100)
    (a / "chats").mkdir()
    (a / "chats" / "1_Test.html").write_text("<html>Chat</html>")
    (basis / "geheim.txt").write_text("nicht ausliefern")
    return a


ARCHIV = baue_archiv(TMP / "media")
ok = 0


def pruefe(bedingung, text):
    global ok
    if not bedingung:
        raise AssertionError(text)
    ok += 1


async def client(opts):
    c = TestClient(TestServer(server.make_app(opts)))
    await c.start_server()
    return c


async def main():
    # Archivsuche ohne eingestellten Pfad
    pruefe(server.find_archive() == ARCHIV, "Archiv wird automatisch gefunden")
    p, err = server.resolve_archive({"archiv_pfad": str(ARCHIV.parent)})
    pruefe(p == ARCHIV, "Pfad zum Elternordner wird akzeptiert")
    p, err = server.resolve_archive({"archiv_pfad": str(TMP / "gibtsnicht")})
    pruefe(p is None and "kein Archiv" in err, "falscher Pfad gibt Hinweis")

    # Ingress: alles erlaubt
    server.INGRESS_IP = "127.0.0.1"
    c = await client({"archiv_pfad": ""})
    r = await c.get("/", allow_redirects=False)
    pruefe(r.status == 302 and r.headers["Location"] == "viewer/index.html", "Startseite leitet relativ weiter")
    r = await c.get("/viewer/", allow_redirects=False)
    pruefe(r.headers["Location"] == "index.html", "viewer/ leitet relativ weiter")
    r = await c.get("/viewer/index.html")
    pruefe(r.status == 200 and "WA_LOAD" in await r.text(), "Viewer-Seite kommt aus dem Add-on")
    r = await c.get("/viewer/data/chats.js")
    pruefe(r.status == 200 and r.headers["Cache-Control"] == "no-cache", "Daten ohne Cache")
    r = await c.get("/Medien/Test/2025/bild.jpg", headers={"Range": "bytes=0-99"})
    pruefe(r.status == 206 and len(await r.read()) == 100, "Range-Anfragen (Videos auf iOS)")
    r = await c.get("/Medien/Test/2025/ton.opus")
    pruefe(r.headers["Content-Type"].startswith("audio/ogg"), "Opus als audio/ogg")
    r = await c.get("/viewer/icon.png")
    pruefe(r.status == 200, "Symbol")
    r = await c.get("/../geheim.txt")
    pruefe(r.status in (403, 404), "kein Ausbruch aus dem Archiv (direkt)")
    r = await c.get("/Medien/%2e%2e/%2e%2e/%2e%2e/geheim.txt")
    pruefe(r.status in (403, 404), "kein Ausbruch aus dem Archiv (kodiert)")
    r = await c.get("/gibtsnicht.jpg")
    pruefe(r.status == 404, "404 für fehlende Datei")
    await c.close()

    # Direkter Zugriff ohne Token: gesperrt (nur die öffentliche Seite)
    server.INGRESS_IP = "172.30.32.2"
    c = await client({"archiv_pfad": str(ARCHIV), "zugriffs_token": ""})
    r = await c.get("/viewer/index.html")
    pruefe(r.status == 200, "Viewer-Seite ist öffentlich (enthält keine Daten)")
    r = await c.get("/viewer/data/chats.js")
    pruefe(r.status == 403, "ohne Token gesperrt")
    await c.close()

    # Direkter Zugriff mit Token
    c = await client({"archiv_pfad": str(ARCHIV), "zugriffs_token": "geheim123"})
    r = await c.get("/viewer/data/chats.js")
    pruefe(r.status == 401, "falscher/fehlender Token abgewiesen")
    r = await c.get("/viewer/data/chats.js?t=falsch")
    pruefe(r.status == 401, "falscher Token abgewiesen")
    r = await c.get("/viewer/data/chats.js?t=geheim123")
    pruefe(r.status == 200 and "wa_token" in r.cookies, "Token per URL setzt Cookie")
    r = await c.get("/Medien/Test/2025/bild.jpg", cookies={"wa_token": "geheim123"})
    pruefe(r.status == 200, "Cookie reicht danach")
    await c.close()

    # Archiv nicht erreichbar
    server.INGRESS_IP = "127.0.0.1"
    c = await client({"archiv_pfad": str(TMP / "weg")})
    r = await c.get("/viewer/index.html")
    pruefe(r.status == 200 and "kein Archiv" in await r.text(), "Hinweisseite wenn Archiv fehlt")
    await c.close()


asyncio.run(main())
print(f"test_whatsapp_archiv: {ok} Prüfungen ok")
