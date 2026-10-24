"""Abgleich mit offenen Daten-APIs: Scryfall (Magic) und YGOPRODeck (Yu-Gi-Oh!).

Regeln:
- Neue Sets werden automatisch angelegt.
- Ein bestehender Termin wird nur geändert, wenn sich der Wert in der Quelle seit dem
  letzten Abgleich geändert hat. Manuelle Korrekturen in der Verwaltung bleiben so erhalten.
- Ein Termin, den du gelöscht hast, wird nicht wieder angelegt.
"""
import json
import logging
import os
import re
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone

from . import db

log = logging.getLogger("tcg-kalender.quellen")

AKTIV = {q.strip() for q in (os.environ.get("QUELLEN") if os.environ.get("QUELLEN") is not None
                             else "scryfall,ygoprodeck").split(",") if q.strip()}
INTERVALL_STUNDEN = float(os.environ.get("QUELLEN_INTERVALL_STUNDEN") or "12")
USER_AGENT = "tcg-release-kalender/1.0 (+https://github.com/EmreWest/tcg-release-kalender)"

_lock = threading.Lock()
status = {"laeuft": False, "letzte_meldung": None}

SCHEMA = """
CREATE TABLE IF NOT EXISTS quellen_stand (
    extern_id TEXT PRIMARY KEY,
    release_id INTEGER,
    datum TEXT,
    name TEXT,
    gesehen TEXT NOT NULL
);
"""


def _laden(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


# --- Quellen -------------------------------------------------------------------

SCRYFALL_TYPEN = {
    "expansion": "hauptset", "core": "hauptset",
    "masters": "spezialset", "draft_innovation": "spezialset",
    "commander": "deck", "starter": "deck", "duel_deck": "deck",
}


def scryfall(ab: str) -> list[dict]:
    daten = _laden("https://api.scryfall.com/sets")["data"]
    sets = []
    for s in daten:
        if s.get("digital") or s.get("set_type") not in SCRYFALL_TYPEN:
            continue
        if not s.get("released_at") or s["released_at"] < ab:
            continue
        sets.append({
            "extern_id": f"scryfall:{s['id']}",
            "spiel": "magic",
            "name": s["name"],
            "code": s["code"].upper(),
            "typ": SCRYFALL_TYPEN[s["set_type"]],
            "datum": s["released_at"],
            "quelle": s.get("scryfall_uri") or "https://scryfall.com/sets",
            "notiz": "Termin laut Scryfall (weltweiter Release).",
        })
    return sets


_YGO_AUSSCHLUSS = re.compile(
    r"tournament pack|\bots\b|winner'?s pack|thank you|promo|prize|participation|sneak peek|"
    r"championship|duelist league|judge|celebration pack|demo|retro pack|special edition", re.I)


def _ygo_typ(name: str, anzahl: int) -> str:
    n = name.lower()
    if re.search(r"structure deck|starter deck|speed duel|\bdeck\b", n):
        return "deck"
    if re.search(r"\btin\b|box|advent|mega pack|binder", n):
        return "sonstiges"
    if 90 <= (anzahl or 0) <= 110:
        return "hauptset"
    return "spezialset"


def ygoprodeck(ab: str) -> list[dict]:
    daten = _laden("https://db.ygoprodeck.com/api/v7/cardsets.php")
    sets = []
    for s in daten:
        d = s.get("tcg_date") or ""
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) or d < ab:
            continue
        if _YGO_AUSSCHLUSS.search(s.get("set_name", "")):
            continue
        sets.append({
            "extern_id": f"ygoprodeck:{s.get('set_code') or _norm(s['set_name'])}",
            "spiel": "yugioh",
            "name": s["set_name"],
            "code": s.get("set_code") or None,
            "typ": _ygo_typ(s["set_name"], s.get("num_of_cards") or 0),
            "datum": d,
            "quelle": "https://ygoprodeck.com/",
            "notiz": "Termin laut YGOPRODeck (TCG-Termin Europa/Nordamerika).",
        })
    return sets


QUELLEN = {"scryfall": scryfall, "ygoprodeck": ygoprodeck}
QUELLEN_TEXT = {"scryfall": "Scryfall (Magic)", "ygoprodeck": "YGOPRODeck (Yu-Gi-Oh!)"}


# --- Abgleich ------------------------------------------------------------------

def _stand(con, extern_id):
    return con.execute("SELECT * FROM quellen_stand WHERE extern_id = ?", (extern_id,)).fetchone()


def _passender_termin(s: dict, kandidaten: list[dict], verknuepft: set[int]) -> dict | None:
    frei = [r for r in kandidaten if r["id"] not in verknuepft]
    n = _norm(s["name"])
    for r in frei:
        if _norm(r["name"]) == n or (r.get("code") and s.get("code") and _norm(r["code"]) == _norm(s["code"])):
            return r
    # Platzhalter ohne Namen, gleicher Tag und gleicher Produkttyp
    for r in frei:
        if r["datum"] == s["datum"] and r["typ"] == s["typ"] and re.search(r"ohne namen|unbenannt|tba", r["name"], re.I):
            return r
    return None


def abgleich_spiel(sets: list[dict]) -> dict:
    ergebnis = {"neu": 0, "geaendert": 0}
    if not sets:
        return ergebnis
    spiel = sets[0]["spiel"]
    if spiel not in {s["id"] for s in db.spiele()}:
        return ergebnis
    with db.verbindung() as con:
        con.executescript(SCHEMA)
        verknuepft = {r["release_id"] for r in con.execute("SELECT release_id FROM quellen_stand") if r["release_id"]}
    kandidaten = db.releases(spiele_filter=[spiel], von=(date.today() - timedelta(days=120)).isoformat())

    for s in sets:
        with db.verbindung() as con:
            st = _stand(con, s["extern_id"])
        jetzt = db.jetzt()
        if st:
            r = db.release(st["release_id"]) if st["release_id"] else None
            if not r:
                continue  # in der Verwaltung gelöscht: nicht wieder anlegen
            if st["datum"] != s["datum"]:
                # Termin hat sich in der Quelle geändert -> übernehmen (gilt automatisch als verschoben)
                db.aendern(r["id"], {"datum": s["datum"], "genauigkeit": "tag"})
                ergebnis["geaendert"] += 1
            with db.verbindung() as con:
                con.execute("UPDATE quellen_stand SET datum = ?, name = ?, gesehen = ? WHERE extern_id = ?",
                            (s["datum"], s["name"], jetzt, s["extern_id"]))
            continue

        r = _passender_termin(s, kandidaten, verknuepft)
        if r:
            # Erstes Zusammentreffen: Termin an die Quelle angleichen, ohne ihn als verschoben zu markieren
            aenderung = {}
            if r["datum"] != s["datum"] or r["genauigkeit"] != "tag":
                aenderung.update({"datum": s["datum"], "genauigkeit": "tag", "notiz": s["notiz"]})
            if r["status"] in ("geruecht", "angekuendigt"):
                aenderung["status"] = "bestaetigt"
            if re.search(r"ohne namen|unbenannt|tba", r["name"], re.I):
                aenderung["name"] = s["name"]
            if not r.get("code") and s.get("code"):
                aenderung["code"] = s["code"]
            if aenderung:
                db.aendern(r["id"], aenderung, korrektur=True)
                ergebnis["geaendert"] += 1
            rid = r["id"]
        else:
            rid = db.anlegen({k: s[k] for k in ("spiel", "name", "code", "typ", "datum", "quelle", "notiz")}
                             | {"genauigkeit": "tag", "status": "bestaetigt"})
            ergebnis["neu"] += 1
        verknuepft.add(rid)
        with db.verbindung() as con:
            con.execute("INSERT INTO quellen_stand (extern_id, release_id, datum, name, gesehen) VALUES (?,?,?,?,?)",
                        (s["extern_id"], rid, s["datum"], s["name"], jetzt))
    return ergebnis


def abgleichen(nur: str | None = None) -> str:
    if not _lock.acquire(blocking=False):
        return "Abgleich läuft bereits."
    status["laeuft"] = True
    try:
        ab = (date.today() - timedelta(days=30)).isoformat()
        teile = []
        for name, funktion in QUELLEN.items():
            if name not in AKTIV or (nur and nur != name):
                continue
            try:
                e = abgleich_spiel(funktion(ab))
                teile.append(f"{QUELLEN_TEXT[name]}: {e['neu']} neu, {e['geaendert']} aktualisiert")
            except Exception as ex:  # noqa: BLE001
                log.exception("Abgleich mit %s fehlgeschlagen", name)
                teile.append(f"{QUELLEN_TEXT[name]}: Fehler – {ex}")
        db.meta_set("letzter_abgleich", datetime.now(timezone.utc).isoformat())
        msg = " | ".join(teile) or "Keine Datenquelle aktiv."
        db.meta_set("letzter_abgleich_meldung", msg)
        status["letzte_meldung"] = msg
        log.info("Abgleich: %s", msg)
        return msg
    finally:
        status["laeuft"] = False
        _lock.release()


def info() -> dict:
    return {
        "aktiv": sorted(AKTIV & set(QUELLEN)),
        "texte": QUELLEN_TEXT,
        "intervall_stunden": INTERVALL_STUNDEN,
        "laeuft": status["laeuft"],
        "letzter_abgleich": db.meta_get("letzter_abgleich"),
        "letzte_meldung": status["letzte_meldung"] or db.meta_get("letzter_abgleich_meldung"),
    }


def starte_hintergrund():
    if not (AKTIV & set(QUELLEN)) or INTERVALL_STUNDEN <= 0:
        log.info("Datenquellen-Abgleich deaktiviert.")
        return

    def faellig() -> bool:
        letzte = db.meta_get("letzter_abgleich")
        if not letzte:
            return True
        return datetime.now(timezone.utc) - datetime.fromisoformat(letzte) >= timedelta(hours=INTERVALL_STUNDEN)

    def schleife():
        time.sleep(15)
        while True:
            try:
                if faellig():
                    abgleichen()
            except Exception:  # noqa: BLE001
                log.exception("Geplanter Abgleich fehlgeschlagen")
            time.sleep(600)

    threading.Thread(target=schleife, daemon=True, name="quellen").start()
