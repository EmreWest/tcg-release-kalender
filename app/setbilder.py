"""Set-Symbole und Set-Logos aus den Datenquellen.

- Magic: Set-Symbol als SVG (Scryfall), einfarbig – im dunklen Design wird es hell dargestellt
- Pokémon: Set-Logo und, sofern vorhanden, Set-Symbol (TCGdex)
- Yu-Gi-Oh!: Produktbild des Sets (YGOPRODeck)
- Flesh and Blood: Set-Logo, wo die Quelle eins hat (The FaB Cube)
Die übrigen Quellen liefern keine Symbole. Alle Dateien werden einmal geladen, unter /data/setbilder
gespeichert und nur von dort ausgeliefert.
"""
import hashlib
import io
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from PIL import Image

from . import db, einstellungen, karten, logos, quellen

log = logging.getLogger("tcg-kalender.setbilder")

INTERVALL_STUNDEN = 24
NEU_VERSUCHEN_TAGE = 7
SYMBOL_HOEHE = 96
LOGO_BREITE, LOGO_HOEHE = 480, 200
# Reihenfolge, in der Quellen je Spiel herangezogen werden
QUELLEN_JE_SPIEL = {"magic": ["scryfall"], "pokemon": ["tcgdex"], "yugioh": ["ygoprodeck"], "fab": ["fabcube"]}

status = {"laeuft": False, "meldung": None}
_lock = threading.Lock()
_DATEINAME = re.compile(r"^[0-9a-f]{24}\.(svg|webp)$")


def aktiv() -> bool:
    return bool(einstellungen.lesen().get("set_bilder"))


def verzeichnis() -> str:
    return os.path.join(os.path.dirname(db.DB_PFAD) or ".", "setbilder")


def pfad(datei: str) -> str | None:
    if not _DATEINAME.match(datei or ""):
        return None
    p = os.path.join(verzeichnis(), datei)
    return p if os.path.isfile(p) else None


# --- Kandidaten aus den Quellen ------------------------------------------------------

def _scryfall() -> dict:
    return {f"scryfall:{s['id']}": {"symbol": s.get("icon_svg_uri"), "mono": True}
            for s in quellen._laden("https://api.scryfall.com/sets").get("data") or [] if s.get("icon_svg_uri")}


def _ygoprodeck() -> dict:
    ergebnis = {}
    for s in quellen._laden("https://db.ygoprodeck.com/api/v7/cardsets.php"):
        eid = f"ygoprodeck:{s.get('set_code') or quellen._norm(s.get('set_name'))}"
        if s.get("set_image") and eid not in ergebnis:
            ergebnis[eid] = {"logo": s["set_image"]}
    return ergebnis


def _fabcube() -> dict:
    url = "https://raw.githubusercontent.com/the-fab-cube/flesh-and-blood-cards/develop/json/english/set.json"
    ergebnis = {}
    for s in quellen._laden(url):
        logo = next((p.get("set_logo") for p in s.get("printings") or [] if p.get("set_logo")), None)
        if logo:
            ergebnis[f"fabcube:{s['id']}"] = {"logo": logo}
    return ergebnis


def _tcgdex(verknuepft: set[str], bekannt: dict) -> dict:
    ergebnis = {}
    for s in quellen._laden("https://api.tcgdex.net/v2/de/sets"):
        eid = f"tcgdex:{s['id']}"
        if eid not in verknuepft:
            continue
        symbol, logo = s.get("symbol"), s.get("logo")
        # Die Liste kennt nicht alle Logos – für zugeordnete Sets einmal die Einzeldaten nachsehen
        if not (symbol and logo) and not bekannt.get(eid):
            try:
                d = quellen._laden(f"https://api.tcgdex.net/v2/de/sets/{s['id']}")
                symbol, logo = symbol or d.get("symbol"), logo or d.get("logo")
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.1)
        if symbol or logo:
            ergebnis[eid] = {"symbol": f"{symbol}.webp" if symbol else None, "logo": f"{logo}.webp" if logo else None}
    return ergebnis


# --- Dateien -------------------------------------------------------------------------

def _speichern(url: str, art: str) -> str:
    inhalt = karten._herunterladen(url)
    if url.split("?")[0].lower().endswith(".svg") or inhalt.lstrip()[:5] in (b"<svg ", b"<?xml"):
        if b"<svg" not in inhalt[:4096] or logos._SVG_VERBOTEN.search(inhalt):
            raise ValueError("SVG abgelehnt")
        endung, daten = "svg", inhalt
    else:
        with Image.open(io.BytesIO(inhalt)) as bild:
            if bild.format not in ("JPEG", "PNG", "WEBP", "GIF"):
                raise ValueError(f"Unbekanntes Bildformat: {bild.format}")
            bild.load()
            bild = bild.convert("RGBA")
            groesse = (SYMBOL_HOEHE * 4, SYMBOL_HOEHE) if art == "symbol" else (LOGO_BREITE, LOGO_HOEHE)
            bild.thumbnail(groesse, Image.LANCZOS)
            aus = io.BytesIO()
            bild.save(aus, "WEBP", quality=85, method=4)
            endung, daten = "webp", aus.getvalue()
    datei = hashlib.sha1(url.encode()).hexdigest()[:24] + "." + endung
    os.makedirs(verzeichnis(), exist_ok=True)
    ziel = os.path.join(verzeichnis(), datei)
    with open(ziel + ".tmp", "wb") as f:
        f.write(daten)
    os.replace(ziel + ".tmp", ziel)
    return datei


# --- Ablauf --------------------------------------------------------------------------

def _verknuepfen():
    """Termine ohne Verknüpfung den Sets der Quellen zuordnen (nur Quellen mit einer einzigen Abfrage)."""
    vorhanden = {s["id"] for s in db.spiele()}
    for name in ("scryfall", "ygoprodeck", "fabcube"):
        if not set(quellen.QUELLEN[name]["spiele"]) & vorhanden:
            continue
        try:
            sets = quellen.QUELLEN[name]["funktion"](quellen.ANFANG, True)
            with quellen._lock:
                quellen.verknuepfen(sets)
        except Exception:  # noqa: BLE001
            log.warning("Set-Bilder: Zuordnung über %s fehlgeschlagen", name)


def abgleichen() -> str:
    if not _lock.acquire(blocking=False):
        return "Läuft bereits."
    status["laeuft"] = True
    try:
        _verknuepfen()
        vorhanden = {s["id"] for s in db.spiele()}
        with db.verbindung() as con:
            links = con.execute("SELECT q.extern_id, q.release_id, r.spiel FROM quellen_stand q "
                                "JOIN releases r ON r.id = q.release_id").fetchall()
            bisher = {r["release_id"]: dict(r) for r in con.execute("SELECT * FROM setbilder")}
        verknuepft = {r["extern_id"] for r in links}
        bekannt = {b["extern_id"]: b for b in bisher.values()}
        kandidaten: dict[str, dict] = {}
        abrufe = {"scryfall": _scryfall, "ygoprodeck": _ygoprodeck, "fabcube": _fabcube,
                  "tcgdex": lambda: _tcgdex(verknuepft, bekannt)}
        for name, funktion in abrufe.items():
            if not set(quellen.QUELLEN[name]["spiele"]) & vorhanden:
                continue
            try:
                kandidaten.update(funktion())
            except Exception as e:  # noqa: BLE001
                log.warning("Set-Bilder von %s nicht abrufbar: %s", name, e)

        # Je Termin die erste passende Quelle seines Spiels
        je_release: dict[int, tuple[str, dict]] = {}
        for link in sorted(links, key=lambda x: x["extern_id"]):
            quell = link["extern_id"].split(":", 1)[0]
            if quell in QUELLEN_JE_SPIEL.get(link["spiel"], []) and link["extern_id"] in kandidaten \
                    and link["release_id"] not in je_release:
                je_release[link["release_id"]] = (link["extern_id"], kandidaten[link["extern_id"]])

        grenze = (datetime.now(timezone.utc) - timedelta(days=NEU_VERSUCHEN_TAGE)).isoformat()
        neu = fehler = 0
        for rid, (eid, k) in je_release.items():
            alt = bisher.get(rid) or {}
            zeile = {"symbol_url": k.get("symbol"), "logo_url": k.get("logo"), "symbol_mono": 1 if k.get("mono") else 0}
            versucht = not alt
            for art in ("symbol", "logo"):
                url, datei = zeile[f"{art}_url"], None
                if url and url == alt.get(f"{art}_url") and alt.get(f"{art}_datei") and pfad(alt[f"{art}_datei"]):
                    datei = alt[f"{art}_datei"]
                elif url and (url != alt.get(f"{art}_url") or (alt.get("geprueft") or "") < grenze):
                    versucht = True
                    try:
                        datei = _speichern(url, art)
                        neu += 1
                    except Exception as e:  # noqa: BLE001
                        fehler += 1
                        log.debug("Set-Bild %s: %s", url, e)
                zeile[f"{art}_datei"] = datei
            with db.verbindung() as con:
                con.execute(
                    "INSERT INTO setbilder (release_id, extern_id, symbol_url, symbol_datei, symbol_mono, logo_url, logo_datei, geprueft) "
                    "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(release_id) DO UPDATE SET extern_id = excluded.extern_id, "
                    "symbol_url = excluded.symbol_url, symbol_datei = excluded.symbol_datei, symbol_mono = excluded.symbol_mono, "
                    "logo_url = excluded.logo_url, logo_datei = excluded.logo_datei, geprueft = excluded.geprueft",
                    (rid, eid, zeile["symbol_url"], zeile["symbol_datei"], zeile["symbol_mono"], zeile["logo_url"],
                     zeile["logo_datei"], datetime.now(timezone.utc).isoformat() if versucht else alt.get("geprueft")))
        _aufraeumen()
        z = zahlen()
        msg = f"{z['symbole']} Symbole und {z['logos']} Logos" + (f", {fehler} nicht abrufbar" if fehler else "")
        db.meta_set("setbilder_stand", json.dumps({"zeit": datetime.now(timezone.utc).isoformat(), "meldung": msg}))
        status["meldung"] = msg
        log.info("Set-Bilder: %s (%d neu geladen)", msg, neu)
        return msg
    finally:
        status["laeuft"] = False
        _lock.release()


def _aufraeumen():
    with db.verbindung() as con:
        con.execute("DELETE FROM setbilder WHERE release_id NOT IN (SELECT id FROM releases)")
        genutzt = {r[0] for r in con.execute("SELECT symbol_datei FROM setbilder UNION SELECT logo_datei FROM setbilder") if r[0]}
    try:
        for datei in os.listdir(verzeichnis()):
            if _DATEINAME.match(datei) and datei not in genutzt:
                os.remove(os.path.join(verzeichnis(), datei))
    except FileNotFoundError:
        pass


# --- Abfragen ------------------------------------------------------------------------

def zahlen() -> dict:
    with db.verbindung() as con:
        r = con.execute("SELECT COUNT(symbol_datei) AS s, COUNT(logo_datei) AS l FROM setbilder").fetchone()
    return {"symbole": r["s"], "logos": r["l"]}


def info() -> dict:
    try:
        stand = json.loads(db.meta_get("setbilder_stand") or "null")
    except ValueError:
        stand = None
    return {"aktiv": aktiv(), "laeuft": status["laeuft"], "stand": stand, **zahlen()}


def zuordnung() -> dict[int, dict]:
    """Symbol und Logo je Termin (Adressen auf dem eigenen Server)."""
    if not aktiv():
        return {}
    with db.verbindung() as con:
        rows = con.execute("SELECT release_id, symbol_datei, symbol_mono, logo_datei FROM setbilder "
                           "WHERE symbol_datei IS NOT NULL OR logo_datei IS NOT NULL").fetchall()
    ergebnis = {}
    for r in rows:
        e = {}
        if r["symbol_datei"]:
            e["symbol"] = f"/setbild/{r['symbol_datei']}"
            if r["symbol_mono"]:
                e["symbol_mono"] = True
        if r["logo_datei"]:
            e["logo"] = f"/setbild/{r['logo_datei']}"
        ergebnis[r["release_id"]] = e
    return ergebnis


def starte_hintergrund():
    def schleife():
        time.sleep(90)  # nach dem Historien-Import, damit möglichst viele Sets zugeordnet sind
        while True:
            try:
                if aktiv():
                    letzte = json.loads(db.meta_get("setbilder_stand") or "null")
                    if not letzte or datetime.now(timezone.utc) - datetime.fromisoformat(letzte["zeit"]) \
                            >= timedelta(hours=INTERVALL_STUNDEN):
                        abgleichen()
            except Exception:  # noqa: BLE001
                log.exception("Set-Bilder fehlgeschlagen")
            time.sleep(3600)

    threading.Thread(target=schleife, daemon=True, name="setbilder").start()
