"""
WhatsApp-Archiv – liefert ein vorhandenes Archiv (erstellt mit der Mac-App „WhatsApp-Archiv“)
samt Medien-Viewer aus. Das Archiv liegt typischerweise auf einem NAS, das in Home Assistant
als Netzwerkspeicher eingebunden ist (/media/… oder /share/…).

Zugriff: über Ingress (Seitenleiste / HA-App) immer erlaubt, über den direkten Port nur mit
Zugriffs-Token (?t=… bzw. Cookie wa_token).
"""

import hmac
import json
import logging
import mimetypes
import os
from pathlib import Path

from aiohttp import web

OPTIONS = Path(os.environ.get("WA_OPTIONS", "/data/options.json"))
APP_DIR = Path(__file__).resolve().parent
SEARCH_ROOTS = [Path(p) for p in os.environ.get("WA_SEARCH_ROOTS", "/media:/share").split(":")]
INGRESS_IP = "172.30.32.2"
PORT = int(os.environ.get("WA_PORT", "8099"))
PUBLIC = {"/", "/viewer/", "/viewer/index.html", "/viewer/manifest.json", "/viewer/icon.png"}
SKIP_DIRS = {"@eaDir", "#recycle", "@Recycle", "@Recently-Snapshot", ".streams", "@__thumb", ".@__thumb"}

EXTRA_TYPES = {".opus": "audio/ogg", ".m4a": "audio/mp4", ".js": "text/javascript; charset=utf-8",
               ".html": "text/html; charset=utf-8", ".heic": "image/heic"}

log = logging.getLogger("whatsapp_archiv")


def load_options():
    try:
        return json.loads(OPTIONS.read_text())
    except Exception:
        return {}


def is_archive(p: Path):
    return (p / "viewer" / "data" / "chats.js").is_file()


def find_archive(max_depth=6):
    """Sucht unter /media und /share nach einem Archiv mit Viewer (erster Treffer)."""
    for root in SEARCH_ROOTS:
        if not root.is_dir():
            continue
        base = len(root.parts)
        for dirpath, dirs, _files in os.walk(root):
            p = Path(dirpath)
            if is_archive(p):
                return p
            depth = len(p.parts) - base
            dirs[:] = [] if depth >= max_depth else sorted(
                d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
            # Innerhalb eines Archivs nicht weiter absteigen (Medien-Ordner sind riesig)
            if p.name in ("Medien", "daten", "thumbs", "chats", "text"):
                dirs[:] = []
    return None


def resolve_archive(opts):
    path = (opts.get("archiv_pfad") or "").strip()
    if path:
        p = Path(path)
        if is_archive(p):
            return p, ""
        if is_archive(p / "WhatsApp-Archiv"):
            return p / "WhatsApp-Archiv", ""
        return None, (f"Im eingestellten Pfad wurde kein Archiv mit Viewer gefunden: {path}. "
                      "Erwartet wird der Ordner „WhatsApp-Archiv“ mit dem Unterordner „viewer“.")
    log.info("Kein Pfad eingestellt – suche Archiv unter %s …", ", ".join(map(str, SEARCH_ROOTS)))
    p = find_archive()
    if p:
        return p, ""
    return None, ("Kein Archiv gefunden. NAS in Home Assistant als Netzwerkspeicher einbinden "
                  "(Einstellungen → System → Speicher) und ggf. den Pfad in der Konfiguration eintragen.")


class Archive:
    def __init__(self, opts):
        self.opts = opts
        self.token = (opts.get("zugriffs_token") or "").strip()
        self.root, self.error = resolve_archive(opts)
        if self.root:
            log.info("Archiv: %s", self.root)
        else:
            log.warning(self.error)

    def refresh_if_missing(self):
        """NAS war beim Start evtl. noch nicht eingebunden – später erneut versuchen."""
        if self.root is None or not self.root.exists():
            self.root, self.error = resolve_archive(self.opts)
            if self.root:
                log.info("Archiv gefunden: %s", self.root)


def safe_path(root: Path, rel: str):
    rel = rel.lstrip("/")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None
    p = root.joinpath(*parts)
    try:
        p.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return p


def token_ok(request, token):
    if not token:
        return False
    given = request.query.get("t") or request.cookies.get("wa_token") or ""
    return hmac.compare_digest(given.encode(), token.encode())


@web.middleware
async def access(request, handler):
    arch = request.app["archive"]
    if request.remote == INGRESS_IP:
        return await handler(request)
    if request.path in PUBLIC:
        return await handler(request)
    if not arch.token:
        return web.Response(status=403, text="Direkter Zugriff ist gesperrt. Zugriffs-Token in der "
                                             "Add-on-Konfiguration setzen oder über Home Assistant öffnen.")
    if not token_ok(request, arch.token):
        return web.Response(status=401, text="Zugriffs-Token fehlt oder ist falsch.")
    resp = await handler(request)
    if request.query.get("t"):
        resp.set_cookie("wa_token", arch.token, max_age=31536000, path="/", samesite="Lax")
    return resp


def info_page(msg):
    return web.Response(content_type="text/html", text=f"""<!DOCTYPE html><html lang="de"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>WhatsApp-Archiv</title><style>body{{font:16px -apple-system,sans-serif;max-width:560px;margin:40px auto;
padding:0 16px;color:#333}}h1{{color:#075e54;font-size:20px}}</style></head><body>
<h1>WhatsApp-Archiv</h1><p>{msg}</p><p>Details stehen im Protokoll und in der Dokumentation des Add-ons.</p>
</body></html>""")


async def root(request):
    # relative Weiterleitung, damit es unter dem Ingress-Pfad funktioniert
    raise web.HTTPFound("index.html" if request.path == "/viewer/" else "viewer/index.html")


async def viewer_page(request):
    arch = request.app["archive"]
    arch.refresh_if_missing()
    if not arch.root:
        return info_page(arch.error)
    resp = web.FileResponse(APP_DIR / "viewer.html")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


async def static(request):
    arch = request.app["archive"]
    rel = request.match_info["path"]
    if rel in ("viewer/icon.png",):
        return web.FileResponse(APP_DIR / "icon.png")
    if not arch.root or not arch.root.exists():
        arch.refresh_if_missing()
        if not arch.root:
            raise web.HTTPServiceUnavailable(text=arch.error)
    p = safe_path(arch.root, rel)
    if p is None:
        raise web.HTTPForbidden()
    try:
        if p.is_dir():
            raise web.HTTPFound(request.path.rstrip("/") + "/index.html")
        if not p.is_file():
            raise web.HTTPNotFound()
    except OSError:
        raise web.HTTPServiceUnavailable(text="Archiv nicht erreichbar – ist das NAS online?")
    ctype = EXTRA_TYPES.get(p.suffix.lower()) or mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    # FileResponse unterstützt Range-Anfragen (nötig für Videos auf iOS)
    resp = web.FileResponse(p, chunk_size=256 * 1024, headers={"Content-Type": ctype})
    if rel.startswith("viewer/data/") or rel.endswith(".html"):
        resp.headers["Cache-Control"] = "no-cache"
    else:
        resp.headers["Cache-Control"] = "public, max-age=604800"
    return resp


def make_app(opts=None):
    app = web.Application(middlewares=[access])
    app["archive"] = Archive(load_options() if opts is None else opts)
    app.router.add_get("/", root)
    app.router.add_get("/viewer", root)
    app.router.add_get("/viewer/", root)
    app.router.add_get("/viewer/index.html", viewer_page)
    app.router.add_get("/{path:.+}", static)
    return app


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    web.run_app(make_app(), host="0.0.0.0", port=PORT, access_log=None)


if __name__ == "__main__":
    main()
