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
    # Deutsche Logos gibt es nur für einen Teil der Sets – sonst das englische Logo verwenden
    try:
        englisch = {s["id"]: s for s in quellen._laden("https://api.tcgdex.net/v2/en/sets")}
    except Exception:  # noqa: BLE001
        englisch = {}
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
        en = englisch.get(s["id"]) or {}
        # Kandidaten in Reihenfolge: deutsch vor englisch, WebP vor PNG. TCGdex nennt manchmal eine
        # Adresse, unter der nur eines der Formate liegt (z. B. deutsches Logo nur als PNG).
        symbole = [f"{u}.{e}" for u in dict.fromkeys(x for x in (symbol, en.get("symbol")) if x) for e in ("webp", "png")]
        logos_ = [f"{u}.{e}" for u in dict.fromkeys(x for x in (logo, en.get("logo")) if x) for e in ("webp", "png")]
        if symbole or logos_:
            ergebnis[eid] = {"symbol": symbole or None, "logo": logos_ or None}
    return ergebnis


# --- Dateien -------------------------------------------------------------------------

def _speichern(url: str, art: str) -> str:
    inhalt = karten._herunterladen(url)
    svg = url.split("?")[0].lower().endswith(".svg") or inhalt.lstrip()[:5] in (b"<svg ", b"<?xml")
    return _ablegen(inhalt, art, url, svg)


def _ablegen(inhalt: bytes, art: str, schluessel: str, svg: bool) -> str:
    """SVG geprüft unverändert, alles andere verkleinert als WebP speichern."""
    if svg:
        if b"<svg" not in inhalt[:4096] or logos._SVG_VERBOTEN.search(inhalt):
            raise ValueError("SVG abgelehnt")
        endung, daten = "svg", inhalt
    else:
        with Image.open(io.BytesIO(inhalt)) as bild:
            if bild.format not in ("JPEG", "PNG", "WEBP", "GIF"):
                raise ValueError(f"Unbekanntes Bildformat: {bild.format}")
            if bild.width * bild.height > 40_000_000:
                raise ValueError("Das Bild ist zu groß (mehr als 40 Megapixel)")
            bild.load()
            bild = bild.convert("RGBA")
            groesse = (SYMBOL_HOEHE * 4, SYMBOL_HOEHE) if art == "symbol" else (LOGO_BREITE, LOGO_HOEHE)
            bild.thumbnail(groesse, Image.LANCZOS)
            aus = io.BytesIO()
            bild.save(aus, "WEBP", quality=85, method=4)
            endung, daten = "webp", aus.getvalue()
    datei = hashlib.sha1(schluessel.encode()).hexdigest()[:24] + "." + endung
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


def _deutsche_namen_magic():
    """Deutsche Magic-Set-Namen aus MTGJSON (dort gepflegt bis etwa 2023), höchstens einmal pro Woche."""
    letzte = db.meta_get("mtgjson_stand")
    if letzte and datetime.now(timezone.utc) - datetime.fromisoformat(letzte) < timedelta(days=7):
        return
    if "magic" not in {s["id"] for s in db.spiele()}:
        return
    daten = quellen._laden("https://mtgjson.com/api/v5/SetList.json").get("data") or []
    deutsch = {s["code"].upper(): (s.get("translations") or {}).get("German") for s in daten if s.get("code")}
    with db.verbindung() as con:
        rows = con.execute("SELECT id, code FROM releases WHERE spiel = 'magic' AND code IS NOT NULL "
                           "AND (name_de IS NULL OR name_de = '')").fetchall()
    n = sum(db.name_de_ergaenzen(r["id"], deutsch.get(r["code"].upper())) for r in rows)
    db.meta_set("mtgjson_stand", datetime.now(timezone.utc).isoformat())
    log.info("Deutsche Magic-Set-Namen: %d ergänzt", n)


def _deutsche_namen_pokemon():
    """Deutsche Pokémon-Set-Namen aus TCGdex für zugeordnete Termine ohne deutschen Namen."""
    with db.verbindung() as con:
        rows = con.execute("SELECT r.id, q.extern_id FROM releases r JOIN quellen_stand q ON q.release_id = r.id "
                           "WHERE r.spiel = 'pokemon' AND q.extern_id LIKE 'tcgdex:%' "
                           "AND (r.name_de IS NULL OR r.name_de = '')").fetchall()
    if not rows:
        return
    namen = {f"tcgdex:{s['id']}": s.get("name") for s in quellen._laden("https://api.tcgdex.net/v2/de/sets")}
    for r in rows:
        db.name_de_ergaenzen(r["id"], namen.get(r["extern_id"]))


def abgleichen() -> str:
    if not _lock.acquire(blocking=False):
        return "Läuft bereits."
    status["laeuft"] = True
    try:
        _verknuepfen()
        for schritt in (_deutsche_namen_pokemon, _deutsche_namen_magic):
            try:
                schritt()
            except Exception as e:  # noqa: BLE001
                log.warning("Deutsche Set-Namen (%s) nicht abrufbar: %s", schritt.__name__, e)
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
            zeile = {"symbol_mono": 1 if k.get("mono") else 0}
            versucht = not alt
            for art in ("symbol", "logo"):
                wert = k.get(art)
                kandidaten_art = [u for u in (wert if isinstance(wert, list) else [wert]) if u]
                url, datei = (kandidaten_art[0] if kandidaten_art else None), None
                alt_url = alt.get(f"{art}_url")
                if alt_url in kandidaten_art and alt.get(f"{art}_datei") and pfad(alt[f"{art}_datei"]):
                    url, datei = alt_url, alt[f"{art}_datei"]
                elif kandidaten_art and (alt_url not in kandidaten_art or alt.get(f"{art}_datei")
                                         or alt_url != kandidaten_art[-1]  # noch nicht alle Kandidaten probiert
                                         or (alt.get("geprueft") or "") < grenze):
                    versucht = True
                    for kandidat in kandidaten_art:  # erster, der sich laden lässt
                        try:
                            url, datei = kandidat, _speichern(kandidat, art)
                            neu += 1
                            break
                        except Exception as e:  # noqa: BLE001
                            log.debug("Set-Bild %s: %s", kandidat, e)
                    else:
                        fehler += 1
                        url = kandidaten_art[-1]  # alle probiert – erst nach NEU_VERSUCHEN_TAGE wieder
                elif alt_url in kandidaten_art:
                    url = alt_url
                zeile[f"{art}_url"], zeile[f"{art}_datei"] = url, datei
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
        con.execute("DELETE FROM setbilder_manuell WHERE release_id NOT IN (SELECT id FROM releases)")
        genutzt = {r[0] for r in con.execute(
            "SELECT symbol_datei FROM setbilder UNION SELECT logo_datei FROM setbilder "
            "UNION SELECT symbol_datei FROM setbilder_manuell UNION SELECT logo_datei FROM setbilder_manuell") if r[0]}
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
    """Symbol und Logo je Termin (Adressen auf dem eigenen Server). Eigene Uploads haben Vorrang."""
    if not aktiv():
        return {}
    with db.verbindung() as con:
        rows = con.execute("SELECT release_id, symbol_datei, symbol_mono, logo_datei FROM setbilder "
                           "WHERE symbol_datei IS NOT NULL OR logo_datei IS NOT NULL").fetchall()
        manuell = con.execute("SELECT release_id, symbol_datei, logo_datei FROM setbilder_manuell").fetchall()
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
    for m in manuell:
        e = ergebnis.setdefault(m["release_id"], {})
        if m["symbol_datei"]:
            e["symbol"] = f"/setbild/{m['symbol_datei']}"
            e.pop("symbol_mono", None)
        if m["logo_datei"]:
            e["logo"] = f"/setbild/{m['logo_datei']}"
    return {rid: e for rid, e in ergebnis.items() if e}


# --- Eigene Uploads ------------------------------------------------------------------

ARTEN = ("logo", "symbol")


def manuell_speichern(rid: int, art: str, inhalt: bytes, medientyp: str) -> str:
    if art not in ARTEN:
        raise ValueError("Unbekannte Art")
    if not db.release(rid):
        raise KeyError(rid)
    endung = logos._typ_pruefen(inhalt, medientyp)  # Typ, Größe (1 MB) und SVG-Inhalt prüfen
    datei = _ablegen(inhalt, art, f"manuell|{rid}|{art}|{hashlib.sha256(inhalt).hexdigest()}", endung == "svg")
    with db.verbindung() as con:
        alt = con.execute(f"SELECT {art}_datei FROM setbilder_manuell WHERE release_id = ?", (rid,)).fetchone()
        con.execute(f"INSERT INTO setbilder_manuell (release_id, {art}_datei) VALUES (?, ?) "
                    f"ON CONFLICT(release_id) DO UPDATE SET {art}_datei = excluded.{art}_datei", (rid, datei))
    if alt and alt[0] and alt[0] != datei:
        _aufraeumen()
    return datei


def manuell_entfernen(rid: int, art: str):
    if art not in ARTEN:
        raise ValueError("Unbekannte Art")
    with db.verbindung() as con:
        con.execute(f"UPDATE setbilder_manuell SET {art}_datei = NULL WHERE release_id = ?", (rid,))
        con.execute("DELETE FROM setbilder_manuell WHERE symbol_datei IS NULL AND logo_datei IS NULL")
    _aufraeumen()


def fuer_release(rid: int) -> dict:
    """Für die Verwaltung: automatisch gefundene und eigene Bilder eines Termins."""
    with db.verbindung() as con:
        a = con.execute("SELECT symbol_datei, logo_datei FROM setbilder WHERE release_id = ?", (rid,)).fetchone()
        m = con.execute("SELECT symbol_datei, logo_datei FROM setbilder_manuell WHERE release_id = ?", (rid,)).fetchone()
    url = lambda d: f"/setbild/{d}" if d and pfad(d) else None  # noqa: E731
    return {art: {"automatisch": url(a[f"{art}_datei"]) if a else None, "eigenes": url(m[f"{art}_datei"]) if m else None}
            for art in ARTEN} | {"aktiv": aktiv()}


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
