import hashlib
import hmac
import html
import logging
import os
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager
from datetime import date, timedelta

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from . import atom, checker, db, einstellungen, ics, karten, logos, notify, preise, quellen, rechtliches, setbilder
from . import produkte as produkte_modul

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("tcg-kalender")

ADMIN_USER = (os.environ.get("ADMIN_USER") or "admin")
ADMIN_PASSWORT = os.environ.get("ADMIN_PASSWORT", "")
UID_DOMAIN = (os.environ.get("UID_DOMAIN") or "tcg-kalender.local")
OEFFENTLICHE_URL = os.environ.get("OEFFENTLICHE_URL", "").rstrip("/")
STATIC = os.path.join(os.path.dirname(__file__), "static")


def _static_version() -> str:
    """Prüfsumme über alle Skripte und Stylesheets – ändert sich mit jedem Update."""
    h = hashlib.sha256()
    for name in sorted(os.listdir(STATIC)):
        if name.endswith((".js", ".css")):
            with open(os.path.join(STATIC, name), "rb") as f:
                h.update(name.encode() + f.read())
    return h.hexdigest()[:10]


STATIC_VERSION = _static_version()
_STATIC_VERWEIS = re.compile(r'(/static/[\w.-]+\.(?:js|css))(?=")')


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    notify.registrieren()
    if not ADMIN_PASSWORT:
        log.warning("ADMIN_PASSWORT ist nicht gesetzt – der Admin-Bereich ist gesperrt.")
    elif len(ADMIN_PASSWORT) < 16:
        log.warning("ADMIN_PASSWORT ist kürzer als 16 Zeichen – für eine öffentlich erreichbare Seite zu schwach.")
    checker.starte_hintergrund()
    quellen.starte_hintergrund()
    karten.starte_hintergrund()
    setbilder.starte_hintergrund()
    preise.starte_hintergrund()
    yield


app = FastAPI(title="TCG-Release-Kalender", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.add_middleware(GZipMiddleware, minimum_size=1500)
security = HTTPBasic(auto_error=False)


@app.middleware("http")
async def schutz(request: Request, call_next):
    # Schutz vor Cross-Site-Requests auf schreibende Admin-Endpunkte: Ein eigener Header
    # lässt sich von fremden Seiten nicht ohne CORS-Freigabe setzen.
    pfad = request.url.path
    if (pfad.startswith("/api/admin/") or pfad in ("/api/login", "/api/logout")) \
            and request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get("x-admin-anfrage") != "1":
            return JSONResponse({"detail": "Header X-Admin-Anfrage fehlt"}, status_code=403)
    antwort = await call_next(request)
    if request.url.path.startswith("/static/"):
        # Mit Versionsnummer dauerhaft cachen, ohne immer erst beim Server nachfragen –
        # so passen HTML, Skripte und Styles nach einem Update immer zusammen.
        antwort.headers["Cache-Control"] = ("public, max-age=31536000, immutable"
                                            if request.query_params.get("v") == STATIC_VERSION else "no-cache")
    antwort.headers.setdefault("X-Content-Type-Options", "nosniff")
    antwort.headers.setdefault("Referrer-Policy", "same-origin")
    return antwort


# --- Anmeldung ---------------------------------------------------------------------
# Die Verwaltung nutzt eine Anmeldeseite mit Sitzungs-Cookie. Benutzer und Passwort per
# HTTP Basic Auth funktionieren für Skripte weiterhin, lösen aber kein Browser-Fenster mehr aus.

SITZUNG_COOKIE = "tcg_sitzung"
SITZUNG_STUNDEN = 12
SITZUNG_MERKEN_TAGE = 30
_FEHLVERSUCHE: dict[str, list[float]] = {}
FEHLVERSUCHE_MAX, SPERRE_SEKUNDEN = 5, 15 * 60
# Obergrenze über alle Adressen: verteilte Versuche (viele IPs) pausieren die Anmeldung für alle.
# Bestehende Sitzungen bleiben davon unberührt.
FEHLVERSUCHE_GESAMT_MAX = 30
_GESAMT = "*"
_sperre_lock = threading.Lock()


def _zugang_ok(benutzer: str, passwort: str) -> bool:
    return bool(ADMIN_PASSWORT) \
        and secrets.compare_digest(benutzer.encode(), ADMIN_USER.encode()) \
        and secrets.compare_digest(passwort.encode(), ADMIN_PASSWORT.encode())


def _sitzungs_schluessel() -> bytes:
    # Zufälliger Schlüssel in der Datenbank plus Passwort: Sitzungen überleben Neustarts,
    # werden aber ungültig, sobald das Passwort geändert wird.
    basis = db.meta_get("sitzungs_schluessel")
    if not basis:
        basis = secrets.token_hex(32)
        db.meta_set("sitzungs_schluessel", basis)
    return hashlib.sha256(f"{basis}|{ADMIN_USER}|{ADMIN_PASSWORT}".encode()).digest()


def _kennung(token: str) -> str:
    # In der Datenbank steht nur ein Hash: Ein Datenbank-Backup verrät keine gültige Sitzung.
    return hmac.new(_sitzungs_schluessel(), token.encode(), hashlib.sha256).hexdigest()


def _sitzung_anlegen(dauer: int) -> str:
    token = secrets.token_urlsafe(32)
    jetzt, kennung = int(time.time()), _kennung(token)  # vor der Transaktion: legt ggf. den Schlüssel an
    with db.verbindung() as con:
        con.execute("DELETE FROM sitzungen WHERE ablauf < ?", (jetzt,))
        con.execute("INSERT INTO sitzungen (kennung, ablauf, erstellt) VALUES (?,?,?)", (kennung, jetzt + dauer, db.jetzt()))
    return token


def _sitzung_gueltig(wert: str | None) -> bool:
    if not wert or not ADMIN_PASSWORT or len(wert) > 200:
        return False
    kennung = _kennung(wert)
    with db.verbindung() as con:
        r = con.execute("SELECT ablauf FROM sitzungen WHERE kennung = ?", (kennung,)).fetchone()
    return bool(r) and r["ablauf"] > time.time()


def _sitzung_beenden(wert: str | None):
    if wert and len(wert) <= 200:
        kennung = _kennung(wert)
        with db.verbindung() as con:
            con.execute("DELETE FROM sitzungen WHERE kennung = ?", (kennung,))


def _client_ip(request: Request) -> str:
    """Adresse für die Sperre nach Fehlversuchen. Der Reverse-Proxy hängt die echte Adresse hinten an
    X-Forwarded-For an; weiter vorne stehende Einträge kann ein Besucher selbst setzen."""
    kette = ",".join(request.headers.getlist("x-forwarded-for"))
    eintraege = [e.strip() for e in kette.split(",") if e.strip()]
    if eintraege:
        return eintraege[-1][:64]
    return request.client.host if request.client else "?"


def _fehlversuch(ip: str):
    jetzt = time.time()
    with _sperre_lock:
        _FEHLVERSUCHE.setdefault(ip, []).append(jetzt)
        _FEHLVERSUCHE.setdefault(_GESAMT, []).append(jetzt)
        if len(_FEHLVERSUCHE) > 10000:  # Speicher begrenzen
            for k in [k for k, v in _FEHLVERSUCHE.items() if k != _GESAMT and all(jetzt - t >= SPERRE_SEKUNDEN for t in v)]:
                del _FEHLVERSUCHE[k]


def _angemeldet(request: Request, cred: HTTPBasicCredentials | None = None) -> bool:
    if _sitzung_gueltig(request.cookies.get(SITZUNG_COOKIE)):
        return True
    if cred is None:
        return False
    # Basic Auth unterliegt derselben Sperre wie die Anmeldeseite
    ip = _client_ip(request)
    if _gesperrt(ip):
        return False
    if _zugang_ok(cred.username, cred.password):
        return True
    _fehlversuch(ip)
    log.warning("Fehlgeschlagene Anmeldung (Basic Auth) von %s", ip)
    time.sleep(1)
    return False


def admin(request: Request, cred: HTTPBasicCredentials | None = Depends(security)):
    if not _angemeldet(request, cred):
        raise HTTPException(401, "Nicht angemeldet")
    return ADMIN_USER


def _gesperrt(ip: str) -> bool:
    jetzt = time.time()
    with _sperre_lock:
        for k in (ip, _GESAMT):
            _FEHLVERSUCHE[k] = [t for t in _FEHLVERSUCHE.get(k, []) if jetzt - t < SPERRE_SEKUNDEN]
        return len(_FEHLVERSUCHE[ip]) >= FEHLVERSUCHE_MAX or len(_FEHLVERSUCHE[_GESAMT]) >= FEHLVERSUCHE_GESAMT_MAX


def _sicheres_ziel(weiter: str | None) -> str:
    """Nur Pfade dieser Seite als Ziel nach der Anmeldung (kein Weiterleiten auf fremde Seiten)."""
    w = (weiter or "").strip()
    if w.startswith("/") and not w.startswith(("//", "/\\")) and "\\" not in w and len(w) < 500 \
            and not any(ord(c) < 32 for c in w) and not w.startswith("/login"):
        return w
    return "/admin"


def basis(request: Request) -> str:
    return OEFFENTLICHE_URL or str(request.base_url).rstrip("/")


def _liste(wert: str | None) -> list[str] | None:
    if not wert:
        return None
    return [w.strip() for w in wert.split(",") if w.strip()] or None


def _fehler(e: Exception):
    raise HTTPException(400, str(e))


# --- Öffentlich -----------------------------------------------------------------

def _seite(datei: str, extra: dict | None = None, status_code: int = 200) -> Response:
    """Liefert eine HTML-Seite mit Titel, Untertitel, Logo und Favicon aus den Einstellungen."""
    e = einstellungen.lesen()
    with open(os.path.join(STATIC, datei), encoding="utf-8") as f:
        inhalt = f.read()
    titel = html.escape(e["titel"])
    favicon = _favicon_datei(e)
    kopf_logo = (f'<img class="seiten-logo" src="/logos/{html.escape(e["logo"])}" alt="{titel}">'
                 if e["logo"] else "")
    apple = ""
    if favicon and favicon.rsplit(".", 1)[1] in ("png", "jpg"):
        apple = f'<link rel="apple-touch-icon" href="/logos/{html.escape(favicon)}">'
    fuss = ['<a href="/impressum">Impressum</a>' if e["impressum"] else "",
            '<a href="/datenschutz">Datenschutz</a>' if e["datenschutz"] else "",
            '<button type="button" class="link-knopf" data-speicher-einstellungen>Speicher-Einstellungen</button>',
            '<a href="/login" class="fuss-login" data-login rel="nofollow">Anmelden</a>' if ADMIN_PASSWORT else ""]
    ersetzungen = {
        "{{TITEL}}": titel,
        "{{UNTERTITEL}}": html.escape(e["untertitel"]),
        "{{KOPF_LOGO}}": kopf_logo,
        "{{TITEL_KLASSE}}": "" if e["titel_zeigen"] or not e["logo"] else "nur-screenreader",
        "{{FAVICON}}": f"/favicon?v={html.escape(favicon or 'standard')}",
        "{{FAVICON_TYP}}": logos.MEDIENTYP.get((favicon or "x.svg").rsplit(".", 1)[1], "image/svg+xml"),
        "{{APPLE_ICON}}": apple,
        "{{FUSS}}": "".join(x for x in fuss if x),
        "{{DATENSCHUTZ_URL}}": "/datenschutz" if e["datenschutz"] else "",
        "{{KARTENLISTEN_LINK}}": _KARTENLISTEN_LINK if karten.freigeschaltet() else "",
        **(extra or {}),
    }
    for k, v in ersetzungen.items():
        inhalt = inhalt.replace(k, v)
    inhalt = _STATIC_VERWEIS.sub(rf"\1?v={STATIC_VERSION}", inhalt)
    return Response(inhalt, media_type="text/html; charset=utf-8", status_code=status_code,
                    headers={"Cache-Control": "no-cache"})


_KARTENLISTEN_LINK = (
    '<a class="icon-knopf kartenlisten-knopf" href="/karten" title="Kartenlisten">'
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true"><rect x="7" y="3" width="13" height="17" rx="2"/>'
    '<path d="M4 7v12a2 2 0 0 0 2 2h9"/></svg><span>Kartenlisten</span></a>')


def _favicon_datei(e: dict) -> str | None:
    for kandidat in (e.get("favicon"), e.get("logo")):
        if kandidat and logos.pfad(kandidat):
            return kandidat
    return None


@app.get("/", include_in_schema=False)
def index():
    return _seite("index.html")


def _rechtliches(art: str, ueberschrift: str) -> Response:
    text = einstellungen.lesen()[art]
    if not text:
        inhalt = "<p>Diese Seite wurde noch nicht eingerichtet.</p>"
    else:
        inhalt = rechtliches.formatieren(text)
    return _seite("rechtliches.html", {"{{SEITE}}": ueberschrift, "{{INHALT}}": inhalt},
                  status_code=200 if text else 404)


@app.get("/impressum", include_in_schema=False)
def impressum():
    return _rechtliches("impressum", "Impressum")


@app.get("/datenschutz", include_in_schema=False)
def datenschutz():
    return _rechtliches("datenschutz", "Datenschutzerklärung")


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    e = einstellungen.lesen()
    favicon = _favicon_datei(e)
    typ = logos.MEDIENTYP.get((favicon or "x.svg").rsplit(".", 1)[1], "image/svg+xml")
    icon = f"/favicon?v={favicon or 'standard'}"
    daten = {
        "name": e["titel"],
        "short_name": e["titel"] if len(e["titel"]) <= 15 else e["titel"][:15].rstrip(" -–"),
        "description": e["untertitel"],
        "lang": "de",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#f3f4f7",
        "theme_color": "#2a55c9",
        "icons": [{"src": icon, "type": typ, "sizes": "any", "purpose": "any"}],
    }
    return JSONResponse(daten, media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


@app.get("/favicon", include_in_schema=False)
@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    datei = _favicon_datei(einstellungen.lesen())
    if datei:
        endung = datei.rsplit(".", 1)[1]
        return FileResponse(logos.pfad(datei), media_type=logos.MEDIENTYP[endung],
                            headers={"Cache-Control": "public, max-age=3600",
                                     "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})
    return FileResponse(os.path.join(STATIC, "favicon.svg"), media_type="image/svg+xml",
                        headers={"Cache-Control": "public, max-age=3600"})


def _logo_url(datei: str | None) -> str | None:
    return f"/logos/{datei}" if datei else None


@app.get("/api/meta")
def meta():
    return {
        "titel": (e := einstellungen.lesen())["titel"],
        "untertitel": e["untertitel"],
        "spiele": [{**{k: s[k] for k in ("id", "name", "farbe")}, "logo": _logo_url(s["logo"])}
                   for s in db.spiele()],
        "status": db.STATUS_FILTER,
        "typen": db.TYPEN,
        "genauigkeit": db.GENAUIGKEIT,
        "heute": date.today().isoformat(),
    }


@app.get("/api/releases")
def api_releases(spiele: str | None = None, status: str | None = None, typ: str | None = None,
                 rueckblick: int = Query(60, ge=0, le=3650)):
    von = (date.today() - timedelta(days=rueckblick)).isoformat()
    return _mit_karten(db.releases(_liste(spiele), _liste(status), _liste(typ), von))


def _mit_karten(rows: list[dict]) -> list[dict]:
    """Ergänzt Set-Symbol und -Logo sowie die Anzahl der Karten, wenn für das Spiel Kartenlisten freigeschaltet sind."""
    anzahl = karten.anzahlen()
    bilder = setbilder.zuordnung()
    produkte = produkte_modul.anzahlen(karten.freigeschaltet())
    if anzahl or bilder or produkte:
        for r in rows:
            if anzahl.get(r["id"]):
                r["karten"] = anzahl[r["id"]]
            if produkte.get(r["id"]):
                r["produkte"] = produkte[r["id"]]
            if r["id"] in bilder:
                r.update(bilder[r["id"]])
    return rows


@app.get("/api/releases/{rid}")
def api_release(rid: int):
    r = db.release(rid, mit_spiel=True)
    if not r:
        raise HTTPException(404, "Nicht gefunden")
    return _mit_karten([r])[0]


@app.get("/api/archiv")
def api_archiv(spiele: str | None = None, typ: str | None = None):
    """Alle vergangenen Releases, neueste zuerst. Spielname und Farbe ergänzt die Seite aus /api/meta."""
    rows = _mit_karten(db.archiv(_liste(spiele), _liste(typ)))
    return {"anzahl": len(rows), "releases": rows}


# Cardmarket-Preise und -Links nur für die angemeldete Verwaltung: Ohne Anmeldung verlassen sie den
# Server gar nicht. Die Antworten dürfen deshalb nirgends zwischengespeichert werden.
_PRIVAT = {"Cache-Control": "private, no-store", "Vary": "Cookie, Authorization"}


def _preise_sichtbar(request: Request, cred: HTTPBasicCredentials | None) -> bool:
    return _angemeldet(request, cred)


@app.get("/api/karten/{rid}")
def api_karten(rid: int, request: Request, cred: HTTPBasicCredentials | None = Depends(security)):
    daten = karten.liste(rid, mit_preisen=_preise_sichtbar(request, cred))
    if not daten:
        raise HTTPException(404, "Keine Kartenliste vorhanden")
    return JSONResponse(daten, headers=_PRIVAT)


@app.get("/api/produkte/{rid}")
def api_produkte(rid: int, request: Request, cred: HTTPBasicCredentials | None = Depends(security)):
    daten = karten.produkte_liste(rid, mit_preisen=_preise_sichtbar(request, cred))
    if daten is None:
        raise HTTPException(404, "Keine Produkte vorhanden")
    return JSONResponse({"produkte": daten}, headers=_PRIVAT)


@app.get("/api/kartenlisten")
def api_kartenlisten():
    """Alle Sets mit Kartenliste (nur freigeschaltete Spiele), neueste zuerst."""
    anzahl = karten.anzahlen(eigene_nur=True)  # jede Liste nur einmal, auch wenn es mehrere Wellen gibt
    if not anzahl:
        return {"spiele": [], "sets": []}
    felder = ("id", "spiel", "name", "code", "typ", "datum", "genauigkeit")
    rows = _mit_karten([{k: r[k] for k in felder} for r in db.releases(von="1900-01-01") if r["id"] in anzahl])
    rows.sort(key=lambda r: r["datum"], reverse=True)
    im_einsatz = {r["spiel"] for r in rows}
    spiele = [{**{k: s[k] for k in ("id", "name", "farbe")}, "logo": _logo_url(s["logo"])}
              for s in db.spiele() if s["id"] in im_einsatz]
    return {"spiele": spiele, "sets": rows}


@app.get("/karten", include_in_schema=False)
def kartenlisten_seite():
    return _seite("kartenlisten.html", status_code=200 if karten.freigeschaltet() else 404)


@app.get("/setbild/{datei}", include_in_schema=False)
def set_bild(datei: str):
    pfad = setbilder.pfad(datei) if setbilder.aktiv() else None
    if not pfad:
        raise HTTPException(404, "Nicht gefunden")
    return FileResponse(pfad, media_type="image/svg+xml" if datei.endswith(".svg") else "image/webp", headers={
        "Cache-Control": "public, max-age=31536000, immutable",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
    })


@app.get("/karten/{rid}", include_in_schema=False)
def karten_seite(rid: int):
    return _seite("karten.html", status_code=200 if karten.liste(rid) else 404)


@app.get("/karten/bild/{spiel}/{datei}", include_in_schema=False)
def karten_bild(spiel: str, datei: str):
    pfad = karten.bild_pfad(spiel, datei) if karten.ist_aktiv(spiel) else None
    if not pfad:
        raise HTTPException(404, "Nicht gefunden")
    return FileResponse(pfad, media_type="image/webp", headers={
        # Der Dateiname ist ein Hash der Bildadresse – ein neuer Scan bekommt einen neuen Namen
        "Cache-Control": "public, max-age=31536000, immutable",
        "Content-Security-Policy": "default-src 'none'",
    })


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
        titel = einstellungen.lesen()["titel"]
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
    body = atom.feed(eintraege, basis(request), f"{einstellungen.lesen()['titel']} – Änderungen", selbst)
    return Response(body, media_type="application/atom+xml; charset=utf-8",
                    headers={"Cache-Control": "public, max-age=900"})


@app.get("/logos/{datei}", include_in_schema=False)
def logo(datei: str):
    pfad = logos.pfad(datei)
    if not pfad:
        raise HTTPException(404, "Nicht gefunden")
    endung = datei.rsplit(".", 1)[1]
    return FileResponse(pfad, media_type=logos.MEDIENTYP[endung], headers={
        # Dateinamen enthalten einen Hash, ein geändertes Logo bekommt einen neuen Namen
        "Cache-Control": "public, max-age=31536000, immutable",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:",
    })


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


# --- Admin ------------------------------------------------------------------------

@app.get("/admin", include_in_schema=False)
def admin_seite(request: Request, cred: HTTPBasicCredentials | None = Depends(security)):
    if not _angemeldet(request, cred):
        return RedirectResponse("/login", status_code=303)
    return _seite("admin.html")


@app.get("/login", include_in_schema=False)
def login_seite(request: Request, weiter: str | None = None):
    if _sitzung_gueltig(request.cookies.get(SITZUNG_COOKIE)):
        return RedirectResponse(_sicheres_ziel(weiter), status_code=303)
    hinweis = "" if ADMIN_PASSWORT else (
        '<p class="login-fehler">Der Verwaltungsbereich ist gesperrt, weil <code>ADMIN_PASSWORT</code> nicht gesetzt ist.</p>')
    return _seite("login.html", {"{{LOGIN_HINWEIS}}": hinweis})


@app.post("/api/login", include_in_schema=False)
def login(request: Request, daten: dict = Body(...)):
    ip = _client_ip(request)
    if _gesperrt(ip):
        raise HTTPException(429, "Zu viele Fehlversuche. Bitte in 15 Minuten erneut versuchen.")
    if not _zugang_ok(str(daten.get("benutzer") or "")[:200], str(daten.get("passwort") or "")[:500]):
        _fehlversuch(ip)
        log.warning("Fehlgeschlagene Anmeldung von %s", ip)
        time.sleep(1)  # bremst das Durchprobieren von Passwörtern
        raise HTTPException(401, "Benutzername oder Passwort falsch")
    with _sperre_lock:
        _FEHLVERSUCHE.pop(ip, None)
    log.info("Anmeldung von %s", ip)
    dauer = SITZUNG_MERKEN_TAGE * 86400 if daten.get("merken") else SITZUNG_STUNDEN * 3600
    _sitzung_beenden(request.cookies.get(SITZUNG_COOKIE))  # alte Sitzung dieses Browsers ersetzen
    antwort = JSONResponse({"ok": True, "weiter": _sicheres_ziel(daten.get("weiter"))},
                           headers={"Cache-Control": "no-store"})
    antwort.set_cookie(SITZUNG_COOKIE, _sitzung_anlegen(dauer), max_age=dauer if daten.get("merken") else None,
                       httponly=True, samesite="strict", secure=request.url.scheme == "https", path="/")
    return antwort


@app.post("/api/logout", include_in_schema=False)
def logout(request: Request):
    _sitzung_beenden(request.cookies.get(SITZUNG_COOKIE))
    antwort = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
    antwort.delete_cookie(SITZUNG_COOKIE, path="/", httponly=True, samesite="strict",
                          secure=request.url.scheme == "https")
    return antwort


@app.get("/api/sitzung", include_in_schema=False)
def sitzung(request: Request):
    """Nur für die Oberfläche: ob Verwaltungs-Link und Preise angezeigt werden."""
    return JSONResponse({"angemeldet": _sitzung_gueltig(request.cookies.get(SITZUNG_COOKIE))},
                        headers={"Cache-Control": "no-store"})


@app.get("/api/admin/einstellungen")
def admin_einstellungen(_=Depends(admin)):
    e = einstellungen.lesen()
    return {**e, "logo_url": _logo_url(e["logo"]), "favicon_url": _logo_url(e["favicon"]),
            "auto_modi": einstellungen.AUTO_MODI, "standard_domains": einstellungen.STANDARD_DOMAINS,
            "vorlage_impressum": rechtliches.VORLAGE_IMPRESSUM, "vorlage_datenschutz": rechtliches.VORLAGE_DATENSCHUTZ}


@app.put("/api/admin/einstellungen")
def admin_einstellungen_aendern(daten: dict = Body(...), _=Depends(admin)):
    try:
        einstellungen.aendern(daten)
    except ValueError as e:
        _fehler(e)
    return admin_einstellungen()


@app.put("/api/admin/darstellung/{art}")
async def admin_darstellung_hochladen(art: str, request: Request, _=Depends(admin)):
    if art not in ("logo", "favicon"):
        raise HTTPException(404, "Nicht gefunden")
    if int(request.headers.get("content-length") or 0) > logos.MAX_GROESSE:
        raise HTTPException(413, "Die Datei ist größer als 1 MB")
    try:
        datei = logos.datei_speichern(f"_seite-{art}", await request.body(), request.headers.get("content-type", ""))
    except ValueError as e:
        _fehler(e)
    alt = einstellungen.datei_setzen(art, datei)
    if alt and alt != datei:
        logos.entfernen_datei(alt)
    return {"url": _logo_url(datei)}


@app.delete("/api/admin/darstellung/{art}")
def admin_darstellung_entfernen(art: str, _=Depends(admin)):
    if art not in ("logo", "favicon"):
        raise HTTPException(404, "Nicht gefunden")
    logos.entfernen_datei(einstellungen.datei_setzen(art, None))
    return {"ok": True}


@app.post("/api/admin/quellen/historie")
def admin_historie(quelle: str | None = None, _=Depends(admin)):
    if quellen.status["laeuft"] or quellen.status["historie_laeuft"]:
        return {"gestartet": False, "meldung": "Ein Abgleich läuft bereits."}
    threading.Thread(target=quellen.historie_importieren, args=(quelle, True), daemon=True).start()
    return {"gestartet": True}


@app.post("/api/admin/quellen/abgleich")
def admin_abgleich(quelle: str | None = None, _=Depends(admin)):
    if quellen.status["laeuft"] or quellen.status["historie_laeuft"]:
        return {"gestartet": False, "meldung": "Abgleich läuft bereits."}
    threading.Thread(target=quellen.abgleichen, args=(quelle,), daemon=True).start()
    return {"gestartet": True}


@app.get("/api/admin/setbilder")
def admin_setbilder(_=Depends(admin)):
    return setbilder.info()


@app.post("/api/admin/setbilder")
def admin_setbilder_abrufen(_=Depends(admin)):
    if not setbilder.aktiv():
        raise HTTPException(400, "Set-Symbole sind ausgeschaltet")
    if setbilder.status["laeuft"]:
        return {"gestartet": False, "meldung": "Läuft bereits."}
    threading.Thread(target=setbilder.abgleichen, daemon=True).start()
    return {"gestartet": True}


@app.get("/api/admin/preise")
def admin_preise(_=Depends(admin)):
    return preise.info()


@app.post("/api/admin/preise")
def admin_preise_abrufen(_=Depends(admin)):
    if not preise.aktiv():
        raise HTTPException(400, "Cardmarket-Preise sind ausgeschaltet")
    if preise.status["laeuft"]:
        return {"gestartet": False, "meldung": "Läuft bereits."}
    threading.Thread(target=preise.aktualisieren, daemon=True).start()
    return {"gestartet": True}


@app.get("/api/admin/karten")
def admin_karten(_=Depends(admin)):
    return karten.uebersicht()


@app.put("/api/admin/karten/{spiel}")
def admin_karten_freischalten(spiel: str, daten: dict = Body(...), _=Depends(admin)):
    try:
        karten.freischalten(spiel, bool(daten.get("aktiv")))
    except ValueError as e:
        _fehler(e)
    return karten.uebersicht()


@app.post("/api/admin/karten/{spiel}/abrufen")
def admin_karten_abrufen(spiel: str, alle: bool = False, _=Depends(admin)):
    if not karten.ist_aktiv(spiel):
        raise HTTPException(400, "Kartenlisten sind für dieses Spiel nicht freigeschaltet")
    if not karten.anstossen(spiel, alle):
        return {"gestartet": False, "meldung": "Für dieses Spiel läuft bereits ein Abruf."}
    return {"gestartet": True}


@app.post("/api/admin/karten-abbrechen")
def admin_karten_abbrechen(_=Depends(admin)):
    karten.abbrechen()
    return {"ok": True}


@app.delete("/api/admin/karten/{spiel}")
def admin_karten_loeschen(spiel: str, _=Depends(admin)):
    try:
        karten.loeschen(spiel)
    except ValueError as e:
        _fehler(e)
    return karten.uebersicht()


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
        logos.entfernen_datei(db.spiel_loeschen(sid))
    except Exception as e:  # noqa: BLE001
        _fehler(e)
    return {"ok": True}


@app.put("/api/admin/spiele/{sid}/logo")
async def admin_logo_hochladen(sid: str, request: Request, _=Depends(admin)):
    if int(request.headers.get("content-length") or 0) > logos.MAX_GROESSE:
        raise HTTPException(413, "Die Datei ist größer als 1 MB")
    inhalt = await request.body()
    try:
        datei = logos.speichern(sid, inhalt, request.headers.get("content-type", ""))
    except KeyError:
        raise HTTPException(404, "Spiel nicht gefunden")
    except ValueError as e:
        _fehler(e)
    return {"logo": _logo_url(datei)}


@app.delete("/api/admin/spiele/{sid}/logo")
def admin_logo_entfernen(sid: str, _=Depends(admin)):
    try:
        logos.entfernen(sid)
    except KeyError:
        raise HTTPException(404, "Spiel nicht gefunden")
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
        "quellen": quellen.info(),
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
