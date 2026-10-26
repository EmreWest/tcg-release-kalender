"""Abgleich mit offenen Daten-APIs: Scryfall (Magic), YGOPRODeck (Yu-Gi-Oh!), SWU-DB (Star Wars: Unlimited),
The FaB Cube (Flesh and Blood) und TCGdex (Pokémon, deutsche Ausgaben).

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

from . import db, einstellungen

log = logging.getLogger("tcg-kalender.quellen")

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


def _us_datum(text: str) -> str | None:
    """'11/20/26' bzw. '11/20/2026' -> '2026-11-20'"""
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})", (text or "").strip())
    if not m:
        return None
    monat, tag, jahr = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if jahr < 100:
        jahr += 2000
    try:
        return date(jahr, monat, tag).isoformat()
    except ValueError:
        return None


_SWU_AUSSCHLUSS = re.compile(r"promo|judge|exclusive|convention|gamegenic|costco|gift box|showdown|prize|event", re.I)


def swudb(ab: str) -> list[dict]:
    daten = _laden("https://api.swu-db.com/sets")
    sets = []
    for s in daten:
        d = _us_datum(s.get("releaseDate"))
        name = s.get("fullName") or ""
        if not d or d < ab or s.get("parentSetId") or _SWU_AUSSCHLUSS.search(name):
            continue
        n = name.lower()
        typ = "deck" if re.search(r"twin suns|intro battle|starter|deck", n) else (
            "spezialset" if "icons" in n else "hauptset")
        sets.append({
            "extern_id": f"swudb:{s['setId']}",
            "spiel": "swu",
            "name": name,
            "code": s["setId"],
            "typ": typ,
            "datum": d,
            "quelle": "https://www.swu-db.com/sets",
            "notiz": "Termin laut SWU-DB (weltweiter Release).",
        })
    return sets


_FAB_AUSSCHLUSS = re.compile(
    r"promo|token|prize|judge|pro tour|calling|world|nationals|battle hardened|skirmish|armory event|"
    r"gem pack|launch|playmat|sleeve|life counter|redemption|marvel|cold foil|xxx", re.I)


def _fab_typ(name: str) -> str:
    n = name.lower()
    if re.search(r"blitz deck|armory deck|demo deck|hero deck|silver age deck|starter|\bdeck\b", n):
        return "deck"
    if re.search(r"mastery pack|history pack|compendium|round the table|collection|box|anthology", n):
        return "spezialset"
    return "hauptset"


def fabcube(ab: str) -> list[dict]:
    daten = _laden("https://raw.githubusercontent.com/the-fab-cube/flesh-and-blood-cards/develop/json/english/set.json")
    sets = []
    for s in daten:
        if _FAB_AUSSCHLUSS.search(s.get("name", "")):
            continue
        termine = [p for p in s.get("printings", []) if p.get("initial_release_date")]
        if not termine:
            continue
        erster = min(termine, key=lambda p: p["initial_release_date"])
        d = erster["initial_release_date"][:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) or d < ab:
            continue
        quelle = erster.get("product_page") or erster.get("card_database") or "https://fabtcg.com/"
        sets.append({
            "extern_id": f"fabcube:{s['id']}",
            "spiel": "fab",
            "name": s["name"],
            "code": s["id"],
            "typ": _fab_typ(s["name"]),
            "datum": d,
            "quelle": quelle if quelle.startswith("https://") else "https://fabtcg.com/",
            "notiz": "Termin laut The FaB Cube (weltweiter Release).",
        })
    return sets


_TCGDEX_AUSSCHLUSS = re.compile(r"promo|mcdonald|trainer kit|pokémon go|pocket|energ", re.I)


def tcgdex(ab: str) -> list[dict]:
    """Deutsche Pokémon-Sets mit deutschem Erscheinungsdatum. Neue Sets erscheinen dort meist kurz vor Release."""
    de = _laden("https://api.tcgdex.net/v2/de/sets")
    try:
        en = {s["id"]: s["name"] for s in _laden("https://api.tcgdex.net/v2/en/sets")}
    except Exception:  # noqa: BLE001
        en = {}
    sets = []
    for s in de[-15:]:  # Liste ist chronologisch, für kommende Sets reichen die letzten Einträge
        try:
            detail = _laden(f"https://api.tcgdex.net/v2/de/sets/{s['id']}")
        except Exception:  # noqa: BLE001
            continue
        d = (detail.get("releaseDate") or "")[:10]
        name_de = detail.get("name") or s.get("name") or ""
        name_en = en.get(s["id"]) or name_de
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) or d < ab:
            continue
        if _TCGDEX_AUSSCHLUSS.search(f"{name_de} {name_en} {s['id']}") or s["id"].endswith(("p", "-p")):
            continue
        offiziell = (detail.get("cardCount") or {}).get("official") or 0
        typ = "hauptset" if offiziell >= 80 and not s["id"].endswith("-c") else "spezialset"
        sets.append({
            "extern_id": f"tcgdex:{s['id']}",
            "spiel": "pokemon",
            "name": name_en,
            "code": s["id"].upper(),
            "typ": typ,
            "datum": d,
            "quelle": "https://tcgdex.dev/",
            "notiz": f"Deutsche Ausgabe: „{name_de}“. Termin laut TCGdex." if name_de != name_en
                     else "Termin laut TCGdex (deutsche Ausgabe).",
        })
    return sets


QUELLEN = {
    "scryfall": {"funktion": scryfall, "text": "Scryfall (Magic)", "spiel": "magic"},
    "ygoprodeck": {"funktion": ygoprodeck, "text": "YGOPRODeck (Yu-Gi-Oh!)", "spiel": "yugioh"},
    "swudb": {"funktion": swudb, "text": "SWU-DB (Star Wars: Unlimited)", "spiel": "swu"},
    "fabcube": {"funktion": fabcube, "text": "The FaB Cube (Flesh and Blood)", "spiel": "fab"},
    "tcgdex": {"funktion": tcgdex, "text": "TCGdex (Pokémon, deutsche Ausgaben)", "spiel": "pokemon"},
}
QUELLEN_TEXT = {k: v["text"] for k, v in QUELLEN.items()}
# Spiele, bei denen die Datenquelle kommende Sets zuverlässig früh genug kennt – dort spart sich die
# Claude-Recherche den Aufwand. TCGdex listet neue Sets erst kurz vor Release, Pokémon bleibt daher drin.
VOLLSTAENDIG = {"scryfall", "ygoprodeck", "swudb", "fabcube"}


def _standard_aktiv() -> set[str]:
    wert = os.environ.get("QUELLEN")
    if wert is None or wert.strip().lower() == "alle":
        return set(QUELLEN)
    return {q.strip() for q in wert.split(",") if q.strip()} & set(QUELLEN)


def aktive() -> set[str]:
    gewaehlt = einstellungen.lesen().get("quellen_aktiv")
    if gewaehlt is None:
        return _standard_aktiv()
    return set(gewaehlt) & set(QUELLEN)


def spiele_mit_quelle() -> set[str]:
    return {QUELLEN[q]["spiel"] for q in aktive() if q in VOLLSTAENDIG}


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
        aktiv = aktive()
        for name, q in QUELLEN.items():
            if name not in aktiv or (nur and nur != name):
                continue
            try:
                e = abgleich_spiel(q["funktion"](ab))
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
        "aktiv": sorted(aktive()),
        "texte": QUELLEN_TEXT,
        "alle": [{"id": k, "text": v["text"], "spiel": v["spiel"]} for k, v in QUELLEN.items()],
        "intervall_stunden": INTERVALL_STUNDEN,
        "laeuft": status["laeuft"],
        "letzter_abgleich": db.meta_get("letzter_abgleich"),
        "letzte_meldung": status["letzte_meldung"] or db.meta_get("letzter_abgleich_meldung"),
    }


def starte_hintergrund():
    if INTERVALL_STUNDEN <= 0:
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
                if aktive() and faellig():
                    abgleichen()
            except Exception:  # noqa: BLE001
                log.exception("Geplanter Abgleich fehlgeschlagen")
            time.sleep(600)

    threading.Thread(target=schleife, daemon=True, name="quellen").start()
