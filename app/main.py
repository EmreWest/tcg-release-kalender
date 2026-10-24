import logging
import os
import secrets
import threading
from contextlib import asynccontextmanager
from datetime import date, timedelta

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from . import atom, checker, db, ics, notify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("tcg-kalender")

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORT = os.environ.get("ADMIN_PASSWORT", "")
UID_DOMAIN = os.environ.get("UID_DOMAIN", "tcg-kalender.local")
OEFFENTLICHE_URL = os.environ.get("OEFFENTLICHE_URL", "").rstrip("/")
SEITENTITEL = os.environ.get("SEITENTITEL", "TCG-Release-Kalender")
STATIC = os.path.join(os.path.dirname(__file__), "static")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    notify.registrieren()
    if not ADMIN_PASSWORT:
        log.warning("ADMIN_PASSWORT ist nicht gesetzt – der Admin-Bereich ist gesperrt.")
    checker.starte_hintergrund()
    yield


app = FastAPI(title="TCG-Release-Kalender", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
security = HTTPBasic(auto_error=False)


@app.middleware("http")
async def schutz(request: Request, call_next):
    # Schutz vor Cross-Site-Requests auf schreibende Admin-Endpunkte: Ein eigener Header
    # lässt sich von fremden Seiten nicht ohne CORS-Freigabe setzen.
    if request.url.path.startswith("/api/admin/") and request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get("x-admin-anfrage") != "1":
            return JSONResponse({"detail": "Header X-Admin-Anfrage fehlt"}, status_code=403)
    antwort = await call_next(request)
    antwort.headers.setdefault("X-Content-Type-Options", "nosniff")
    antwort.headers.setdefault("Referrer-Policy", "same-origin")
    return antwort


def admin(cred: HTTPBasicCredentials | None = Depends(security)):
    ok = cred is not None and bool(ADMIN_PASSWORT) \
        and secrets.compare_digest(cred.username.encode(), ADMIN_USER.encode()) \
        and secrets.compare_digest(cred.password.encode(), ADMIN_PASSWORT.encode())
    if not ok:
        raise HTTPException(401, "Nicht angemeldet", headers={"WWW-Authenticate": 'Basic realm="Admin"'})
    return cred.username


def basis(request: Request) -> str:
    return OEFFENTLICHE_URL or str(request.base_url).rstrip("/")


def _liste(wert: str | None) -> list[str] | None:
    if not wert:
        return None
    return [w.strip() for w in wert.split(",") if w.strip()] or None


def _fehler(e: Exception):
    raise HTTPException(400, str(e))


# --- Öffentlich -----------------------------------------------------------------

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(STATIC, "index.html"), headers={"Cache-Control": "no-cache"})


@app.get("/api/meta")
def meta():
    return {
        "titel": SEITENTITEL,
        "spiele": [{k: s[k] for k in ("id", "name", "farbe")} for s in db.spiele()],
        "status": db.STATUS_FILTER,
        "typen": db.TYPEN,
        "genauigkeit": db.GENAUIGKEIT,
        "heute": date.today().isoformat(),
    }


@app.get("/api/releases")
def api_releases(spiele: str | None = None, status: str | None = None, typ: str | None = None,
                 rueckblick: int = Query(60, ge=0, le=3650)):
    von = (date.today() - timedelta(days=rueckblick)).isoformat()
    return db.releases(_liste(spiele), _liste(status), _liste(typ), von)


@app.get("/api/releases/{rid}")
def api_release(rid: int):
    r = db.release(rid, mit_spiel=True)
    if not r:
        raise HTTPException(404, "Nicht gefunden")
    return r


@app.get("/api/aenderungen")
def api_aenderungen(spiele: str | None = None, limit: int = Query(30, ge=1, le=200)):
    eintraege = db.aenderungen(limit, _liste(spiele))
    for e in eintraege:
        e["text"] = notify.beschreibung(e)
    return eintraege


@app.get("/kalender.ics")
def kalender(request: Request, spiele: str | None = None, status: str | None = None, typ: str | None = None,
             prerelease: int = 0, ungenau: int = 1,
             rueckblick: int = Query(90, ge=0, le=3650), name: str | None = None):
    von = (date.today() - timedelta(days=rueckblick)).isoformat()
    rows = db.releases(_liste(spiele), _liste(status), _liste(typ), von, mit_ungenauen=bool(ungenau))
    if name:
        titel = name[:60]
    else:
        alle = {s["id"]: s["name"] for s in db.spiele()}
        gewaehlt = _liste(spiele) or []
        titel = SEITENTITEL
        if gewaehlt and len(gewaehlt) <= 3:
            titel += ": " + ", ".join(alle.get(g, g) for g in gewaehlt)
    body = ics.feed(rows, titel, UID_DOMAIN, bool(prerelease), basis(request))
    return Response(body, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'inline; filename="tcg-kalender.ics"',
                             "Cache-Control": "public, max-age=900"})


@app.get("/release/{rid}.ics")
def release_ics(rid: int, request: Request):
    r = db.release(rid, mit_spiel=True)
    if not r:
        raise HTTPException(404, "Nicht gefunden")
    body = ics.feed([r], r["name"], UID_DOMAIN, True, basis(request))
    dateiname = "".join(c if c.isalnum() else "-" for c in r["name"]).strip("-").lower() or "release"
    return Response(body, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{dateiname}.ics"'})


@app.get("/feed.xml")
def feed_xml(request: Request, spiele: str | None = None):
    eintraege = db.aenderungen(50, _liste(spiele))
    selbst = str(request.url)
    body = atom.feed(eintraege, basis(request), f"{SEITENTITEL} – Änderungen", selbst)
    return Response(body, media_type="application/atom+xml; charset=utf-8",
                    headers={"Cache-Control": "public, max-age=900"})


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


# --- Admin ------------------------------------------------------------------------

@app.get("/admin", include_in_schema=False)
def admin_seite(_=Depends(admin)):
    return FileResponse(os.path.join(STATIC, "admin.html"), headers={"Cache-Control": "no-cache"})


@app.get("/api/admin/releases")
def admin_releases(_=Depends(admin)):
    return db.releases(von="1900-01-01")


@app.post("/api/admin/releases")
def admin_anlegen(daten: dict = Body(...), _=Depends(admin)):
    try:
        return {"id": db.anlegen(daten)}
    except Exception as e:  # noqa: BLE001
        _fehler(e)


@app.put("/api/admin/releases/{rid}")
def admin_aendern(rid: int, daten: dict = Body(...), _=Depends(admin)):
    try:
        db.aendern(rid, daten)
    except KeyError:
        raise HTTPException(404, "Nicht gefunden")
    except Exception as e:  # noqa: BLE001
        _fehler(e)
    return {"ok": True}


@app.delete("/api/admin/releases/{rid}")
def admin_loeschen(rid: int, _=Depends(admin)):
    db.loeschen(rid)
    return {"ok": True}


@app.get("/api/admin/spiele")
def admin_spiele(_=Depends(admin)):
    return db.spiele()


@app.post("/api/admin/spiele")
def admin_spiel(daten: dict = Body(...), _=Depends(admin)):
    try:
        db.spiel_anlegen(daten.get("id", ""), daten.get("name", ""), daten.get("farbe") or "#6b7280")
    except Exception as e:  # noqa: BLE001
        _fehler(e)
    return {"ok": True}


@app.put("/api/admin/spiele/{sid}")
def admin_spiel_aendern(sid: str, daten: dict = Body(...), _=Depends(admin)):
    try:
        db.spiel_aendern(sid, daten)
    except KeyError:
        raise HTTPException(404, "Nicht gefunden")
    except Exception as e:  # noqa: BLE001
        _fehler(e)
    return {"ok": True}


@app.delete("/api/admin/spiele/{sid}")
def admin_spiel_loeschen(sid: str, _=Depends(admin)):
    try:
        db.spiel_loeschen(sid)
    except Exception as e:  # noqa: BLE001
        _fehler(e)
    return {"ok": True}


@app.get("/api/admin/vorschlaege")
def admin_vorschlaege(_=Depends(admin)):
    return db.vorschlaege("offen")


@app.post("/api/admin/vorschlaege/{vid}/uebernehmen")
def admin_uebernehmen(vid: int, daten: dict | None = Body(None), _=Depends(admin)):
    try:
        db.vorschlag_entscheiden(vid, True, daten or None)
    except KeyError:
        raise HTTPException(404, "Vorschlag nicht gefunden oder schon erledigt")
    except Exception as e:  # noqa: BLE001
        _fehler(e)
    return {"ok": True}


@app.post("/api/admin/vorschlaege/{vid}/ablehnen")
def admin_ablehnen(vid: int, _=Depends(admin)):
    try:
        db.vorschlag_entscheiden(vid, False)
    except KeyError:
        raise HTTPException(404, "Vorschlag nicht gefunden oder schon erledigt")
    return {"ok": True}


@app.post("/api/admin/import")
def admin_import(liste: list = Body(...), _=Depends(admin)):
    n, fehler = checker.vorschlaege_einspielen(liste, "import")
    return {"angelegt": n, "fehler": fehler}


@app.get("/api/admin/status")
def admin_status(_=Depends(admin)):
    return {
        "claude_aktiv": checker.aktiv(),
        "modell": checker.MODELL if checker.aktiv() else None,
        "intervall_tage": checker.INTERVALL_TAGE,
        "laeuft": checker.status["laeuft"],
        "letzte_meldung": checker.status["letzte_meldung"],
        "letzte_pruefung": db.meta_get("letzte_pruefung"),
        "benachrichtigungen": {"discord": bool(notify.DISCORD_URL), "webhook": bool(notify.WEBHOOK_URL)},
    }


@app.post("/api/admin/pruefen")
def admin_pruefen(spiel: str | None = None, _=Depends(admin)):
    if not checker.aktiv():
        raise HTTPException(400, "Kein ANTHROPIC_API_KEY gesetzt.")
    if checker.status["laeuft"]:
        return {"gestartet": False, "meldung": "Prüfung läuft bereits."}
    threading.Thread(target=checker.pruefen, args=(spiel,), daemon=True).start()
    return {"gestartet": True}


@app.get("/api/admin/export")
def admin_export(_=Depends(admin)):
    rows = db.releases(von="1900-01-01")
    felder = ["id"] + db.FELDER
    return JSONResponse(
        {"spiele": [{k: s[k] for k in ("id", "name", "farbe")} for s in db.spiele()],
         "releases": [{k: r[k] for k in felder} for r in rows]},
        headers={"Content-Disposition": 'attachment; filename="tcg-kalender-export.json"'},
    )
