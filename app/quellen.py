"""Abgleich mit offenen Daten-APIs.

- Scryfall (Magic), YGOPRODeck (Yu-Gi-Oh!), SWU-DB (Star Wars: Unlimited), The FaB Cube (Flesh and Blood),
  TCGdex (Pokémon, deutsche Ausgaben)
- TCGCSV, ein offener Spiegel des TCGplayer-Katalogs, für alle übrigen Spiele

Regeln:
- Neue Sets werden automatisch angelegt.
- Ein bestehender Termin wird nur geändert, wenn sich der Wert in der Quelle seit dem
  letzten Abgleich geändert hat. Manuelle Korrekturen in der Verwaltung bleiben so erhalten.
- Ein Termin, den du gelöscht hast, wird nicht wieder angelegt.
- Die Historie (alle vergangenen Sets) wird je Quelle einmalig importiert und bleibt danach bestehen.
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
HISTORIE_AKTIV = (os.environ.get("HISTORIE") or "1").strip().lower() not in ("0", "aus", "false", "nein")
USER_AGENT = "tcg-release-kalender/1.0 (+https://github.com/EmreWest/tcg-release-kalender)"
ANFANG = "1990-01-01"

_lock = threading.Lock()
status = {"laeuft": False, "letzte_meldung": None, "historie_laeuft": False}


def _laden(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode("utf-8"))


def _norm(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _iso(text: str | None) -> str | None:
    d = (text or "")[:10]
    return d if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) else None


# --- Quellen -------------------------------------------------------------------

SCRYFALL_TYPEN = {
    "expansion": "hauptset", "core": "hauptset",
    "masters": "spezialset", "draft_innovation": "spezialset", "funny": "spezialset",
    "commander": "deck", "starter": "deck", "duel_deck": "deck", "planechase": "deck", "archenemy": "deck",
    "from_the_vault": "sonstiges", "spellbook": "sonstiges", "premium_deck": "sonstiges",
}


def scryfall(ab: str, alle: bool = False) -> list[dict]:
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


def ygoprodeck(ab: str, alle: bool = False) -> list[dict]:
    daten = _laden("https://db.ygoprodeck.com/api/v7/cardsets.php")
    # Manche Set-Codes kommen mehrfach vor; je Code zählt der früheste Termin
    nach_id: dict[str, dict] = {}
    for s in daten:
        d = _iso(s.get("tcg_date"))
        if not d or d < ab or _YGO_AUSSCHLUSS.search(s.get("set_name", "")):
            continue
        eid = f"ygoprodeck:{s.get('set_code') or _norm(s['set_name'])}"
        if eid in nach_id and nach_id[eid]["datum"] <= d:
            continue
        nach_id[eid] = {
            "extern_id": eid,
            "spiel": "yugioh",
            "name": s["set_name"],
            "code": s.get("set_code") or None,
            "typ": _ygo_typ(s["set_name"], s.get("num_of_cards") or 0),
            "datum": d,
            "quelle": "https://ygoprodeck.com/",
            "notiz": "Termin laut YGOPRODeck (TCG-Termin Europa/Nordamerika).",
        }
    return list(nach_id.values())


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


def swudb(ab: str, alle: bool = False) -> list[dict]:
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


def fabcube(ab: str, alle: bool = False) -> list[dict]:
    daten = _laden("https://raw.githubusercontent.com/the-fab-cube/flesh-and-blood-cards/develop/json/english/set.json")
    sets = []
    for s in daten:
        if _FAB_AUSSCHLUSS.search(s.get("name", "")):
            continue
        termine = [p for p in s.get("printings", []) if p.get("initial_release_date")]
        if not termine:
            continue
        erster = min(termine, key=lambda p: p["initial_release_date"])
        d = _iso(erster["initial_release_date"])
        if not d or d < ab:
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


_TCGDEX_AUSSCHLUSS = re.compile(r"promo|mcdonald|trainer kit|pocket|energ", re.I)


def tcgdex(ab: str, alle: bool = False) -> list[dict]:
    """Deutsche Pokémon-Sets mit deutschem Erscheinungsdatum. Neue Sets erscheinen dort meist kurz vor Release."""
    de = _laden("https://api.tcgdex.net/v2/de/sets")
    try:
        en = {s["id"]: s["name"] for s in _laden("https://api.tcgdex.net/v2/en/sets")}
    except Exception:  # noqa: BLE001
        en = {}
    sets = []
    # Die Liste ist chronologisch; für den laufenden Abgleich reichen die letzten Einträge
    for s in (de if alle else de[-15:]):
        if re.fullmatch(r"[A-Z]\d.*|P-[A-Z]", s["id"]):  # TCG Pocket
            continue
        try:
            detail = _laden(f"https://api.tcgdex.net/v2/de/sets/{s['id']}")
        except Exception:  # noqa: BLE001
            continue
        if alle:
            time.sleep(0.1)
        if (detail.get("serie") or {}).get("id") == "tcgp":
            continue
        d = _iso(detail.get("releaseDate"))
        name_de = detail.get("name") or s.get("name") or ""
        name_en = en.get(s["id"]) or name_de
        if not d or d < ab:
            continue
        if _TCGDEX_AUSSCHLUSS.search(f"{name_de} {name_en}") or re.search(r"(^|[^a-z])p$|-p$", s["id"]):
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


# TCGplayer-Kategorien der Spiele ohne eigene Datenquelle
TCGCSV_KATEGORIEN = {
    "onepiece": 68, "lorcana": 71, "riftbound": 89, "gundam": 86, "digimon": 63, "dbfw": 80,
    "unionarena": 81, "weiss": 20, "vanguard": 16, "shadowverse": 73, "hololive": 87, "fftcg": 24,
    "sorcery": 77, "grandarchive": 74, "cyberpunk": 92,
}

_TCGCSV_AUSSCHLUSS = re.compile(
    r"promo|pre-?release|release event|event cards|celebration event|tournament|championship cards|judge|"
    r"prize|demo deck|revision pack|energy marker|alternate art reprint|\btokens?\b|box topper|gloryfinder|"
    r"learn together|exclusive cards|kickstarter|event pack|organized play|playmat|sleeve|participation|"
    r"launch party|- beta\b|brawl season|welcome kit|secret garden|\bstore\b|\bpr cards?\b", re.I)
_TCGCSV_PRAEFIX = re.compile(r"^(?=[A-Za-z\-/+]*\d)[A-Za-z0-9\-/+]{2,14}:\s+")


def _tcgcsv_typ(name: str, code: str) -> str:
    n, c = name.lower(), (code or "").upper()
    if re.match(r"^(ST|SD|TD|DZ-SD|DZ-SS|D-SD|D-SS|V-SD|SS|FS|GD-?ST)[-\d]", c) or re.search(
            r"starter|start deck|trial deck|deck ?set|structure|advanced deck|battle deck|master deck|\bdecks?\b", n):
        return "deck"
    if re.search(r"bundle|gift|box set|\btin\b|collection box|\bkit\b|playset", n):
        return "sonstiges"
    if re.match(r"^(EB|PRB|EX|RB|LM|AD|CP|ECP|PCS|HEB|TBP|TB|SP)\d", c.replace("-", "")) or re.search(
            r"extra booster|premium booster|special booster|precious booster|manga booster|story booster|"
            r"memorial|collection|anniversary|re: ?collection|limited|title booster|premium|reprint|alter edition|"
            r"pantheon|draft pack|supporter pack|vault|resurgence|advanced booster|illumineer|selection|"
            r"special series|lite\b", n):
        return "spezialset"
    return "hauptset"


def tcgcsv(ab: str, alle: bool = False) -> list[dict]:
    vorhanden = {s["id"] for s in db.spiele()}
    heute = date.today().isoformat()
    sets = []
    for spiel, kategorie in TCGCSV_KATEGORIEN.items():
        if spiel not in vorhanden:
            continue
        try:
            gruppen = _laden(f"https://tcgcsv.com/tcgplayer/{kategorie}/groups").get("results") or []
        except Exception:  # noqa: BLE001
            log.warning("TCGCSV: Kategorie %s nicht abrufbar", kategorie)
            continue
        time.sleep(0.2)
        gesehen = set()
        for g in sorted(gruppen, key=lambda g: g.get("publishedOn") or ""):
            d = _iso(g.get("publishedOn"))
            roh = (g.get("name") or "").strip()
            if not d or d < ab or not roh or _TCGCSV_AUSSCHLUSS.search(roh):
                continue
            code = (g.get("abbreviation") or "").strip().rstrip("/").strip() or None
            name = _TCGCSV_PRAEFIX.sub("", roh).strip() or roh
            name = re.sub(r"\s+-\s+retail\b", "", name, flags=re.I).strip()
            schluessel = (_norm(name), d)
            if schluessel in gesehen:
                continue
            gesehen.add(schluessel)
            sets.append({
                "extern_id": f"tcgcsv:{g['groupId']}",
                "spiel": spiel,
                "name": name,
                "code": code,
                "typ": _tcgcsv_typ(name, code or ""),
                "datum": d,
                # Künftige Termine im Katalog sind Händlerangaben, keine offizielle Bestätigung
                "status": "bestaetigt" if d < heute else "angekuendigt",
                "quelle": "https://tcgcsv.com/",
                "notiz": "Termin laut TCGplayer-Katalog (Release in Nordamerika, in Europa meist gleich).",
            })
    return sets


QUELLEN = {
    "scryfall": {"funktion": scryfall, "text": "Scryfall (Magic)", "spiele": ["magic"]},
    "ygoprodeck": {"funktion": ygoprodeck, "text": "YGOPRODeck (Yu-Gi-Oh!)", "spiele": ["yugioh"]},
    "swudb": {"funktion": swudb, "text": "SWU-DB (Star Wars: Unlimited)", "spiele": ["swu"]},
    "fabcube": {"funktion": fabcube, "text": "The FaB Cube (Flesh and Blood)", "spiele": ["fab"]},
    "tcgdex": {"funktion": tcgdex, "text": "TCGdex (Pokémon, deutsche Ausgaben)", "spiele": ["pokemon"]},
    "tcgcsv": {"funktion": tcgcsv, "text": "TCGplayer-Katalog über TCGCSV (alle übrigen Spiele)",
               "spiele": list(TCGCSV_KATEGORIEN)},
}
QUELLEN_TEXT = {k: v["text"] for k, v in QUELLEN.items()}
# Quellen, die kommende Sets zuverlässig früh genug kennen – für diese Spiele spart sich die
# Claude-Recherche den Aufwand. TCGdex und TCGplayer listen neue Sets oft erst kurz vor Release.
VOLLSTAENDIG = {"scryfall", "ygoprodeck", "swudb", "fabcube"}
# Bei diesen Quellen wird ein bestehender Termin beim ersten Treffer nur verknüpft, nicht angeglichen
NUR_VERKNUEPFEN = {"tcgcsv"}


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
    return {sp for q in aktive() if q in VOLLSTAENDIG for sp in QUELLEN[q]["spiele"]}


# --- Abgleich ------------------------------------------------------------------

def _stand(con, extern_id):
    return con.execute("SELECT * FROM quellen_stand WHERE extern_id = ?", (extern_id,)).fetchone()


_PLATZHALTER = re.compile(r"ohne namen|unbenannt|\btba\b", re.I)


def _passender_termin(s: dict, kandidaten: list[dict], verknuepft: set[int]) -> dict | None:
    frei = [r for r in kandidaten if r["id"] not in verknuepft]
    n = _norm(s["name"])
    code = _norm(s.get("code"))
    for r in frei:
        if _norm(r["name"]) == n or (code and r.get("code") and _norm(r["code"]) == code):
            return r
    sd = date.fromisoformat(s["datum"])
    for r in frei:
        abstand = abs((date.fromisoformat(r["datum"]) - sd).days)
        # Platzhalter ohne Namen am selben Tag mit gleichem Produkttyp
        if abstand == 0 and r["typ"] == s["typ"] and _PLATZHALTER.search(r["name"]):
            return r
        # Gleiches Set unter leicht anderem Namen, z. B. „Heroines Edition Vol. 2“ und
        # „Extra Booster: One Piece Heroines Edition Vol. 2“
        kurz, lang = sorted((_norm(r["name"]), n), key=len)
        if abstand <= 21 and len(kurz) >= 10 and kurz in lang:
            return r
    return None


def abgleich(sets: list[dict], historie: bool = False, angleichen: bool = True) -> dict:
    """Gleicht Sets (auch mehrerer Spiele) mit der Datenbank ab.
    historie=True: bestehende Termine nur verknüpfen und keine Benachrichtigungen verschicken."""
    ergebnis = {"neu": 0, "geaendert": 0}
    if not sets:
        return ergebnis
    vorhanden = {s["id"] for s in db.spiele()}
    heute = date.today().isoformat()
    with db.verbindung() as con:
        verknuepft = {r["release_id"] for r in con.execute("SELECT release_id FROM quellen_stand") if r["release_id"]}
    von = ANFANG if historie else (date.today() - timedelta(days=120)).isoformat()
    kandidaten_je_spiel: dict[str, list[dict]] = {}
    erledigt = set()

    token = db.STUMM.set(True) if historie else None
    try:
        for s in sorted(sets, key=lambda x: (x["spiel"], x["datum"])):
            if s["spiel"] not in vorhanden or s["extern_id"] in erledigt:
                continue
            erledigt.add(s["extern_id"])
            if s["spiel"] not in kandidaten_je_spiel:
                kandidaten_je_spiel[s["spiel"]] = db.releases(spiele_filter=[s["spiel"]], von=von)
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

            r = _passender_termin(s, kandidaten_je_spiel[s["spiel"]], verknuepft)
            if r:
                aenderung = {}
                if angleichen and not historie:
                    # Erstes Zusammentreffen: Termin an die Quelle angleichen, ohne ihn als verschoben zu markieren
                    if r["datum"] != s["datum"] or r["genauigkeit"] != "tag":
                        aenderung.update({"datum": s["datum"], "genauigkeit": "tag", "notiz": s["notiz"]})
                    if r["status"] in ("geruecht", "angekuendigt") and s.get("status", "bestaetigt") == "bestaetigt":
                        aenderung["status"] = "bestaetigt"
                    if _PLATZHALTER.search(r["name"]):
                        aenderung["name"] = s["name"]
                if not r.get("code") and s.get("code"):
                    aenderung["code"] = s["code"]
                if aenderung:
                    db.aendern(r["id"], aenderung, korrektur=True)
                    ergebnis["geaendert"] += 1
                rid = r["id"]
            else:
                daten = {k: s[k] for k in ("spiel", "name", "code", "typ", "datum", "quelle", "notiz")}
                daten |= {"genauigkeit": "tag", "status": s.get("status", "bestaetigt")}
                # Ins Änderungsprotokoll kommt nur, was noch bevorsteht
                rid = db.anlegen(daten, protokoll=s["datum"] >= heute)
                ergebnis["neu"] += 1
            verknuepft.add(rid)
            with db.verbindung() as con:
                con.execute("INSERT INTO quellen_stand (extern_id, release_id, datum, name, gesehen) VALUES (?,?,?,?,?)",
                            (s["extern_id"], rid, s["datum"], s["name"], jetzt))
    finally:
        if token is not None:
            db.STUMM.reset(token)
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
                e = abgleich(q["funktion"](ab), angleichen=name not in NUR_VERKNUEPFEN)
                teile.append(f"{QUELLEN[name]['text']}: {e['neu']} neu, {e['geaendert']} aktualisiert")
            except Exception as ex:  # noqa: BLE001
                log.exception("Abgleich mit %s fehlgeschlagen", name)
                teile.append(f"{QUELLEN[name]['text']}: Fehler – {ex}")
        db.meta_set("letzter_abgleich", datetime.now(timezone.utc).isoformat())
        msg = " | ".join(teile) or "Keine Datenquelle aktiv."
        db.meta_set("letzter_abgleich_meldung", msg)
        status["letzte_meldung"] = msg
        log.info("Abgleich: %s", msg)
        return msg
    finally:
        status["laeuft"] = False
        _lock.release()


# --- Historie ---------------------------------------------------------------------

def historie_stand() -> dict:
    try:
        return json.loads(db.meta_get("historie") or "{}")
    except ValueError:
        return {}


def historie_importieren(nur: str | None = None, erzwingen: bool = False) -> str:
    """Importiert einmalig alle vergangenen (und bekannten kommenden) Sets je Quelle.
    Läuft unabhängig davon, ob die Quelle für den regelmäßigen Abgleich aktiv ist."""
    if not _lock.acquire(blocking=False):
        return "Ein Abgleich läuft bereits."
    status["historie_laeuft"] = True
    try:
        stand = historie_stand()
        vorhanden = {s["id"] for s in db.spiele()}
        teile = []
        for name, q in QUELLEN.items():
            if nur and nur != name:
                continue
            if not set(q["spiele"]) & vorhanden:
                continue
            if stand.get(name) and not erzwingen:
                continue
            try:
                e = abgleich(q["funktion"](ANFANG, True), historie=True)
                stand[name] = {"zeit": datetime.now(timezone.utc).isoformat(),
                               "neu": (stand.get(name) or {}).get("neu", 0) + e["neu"]}
                db.meta_set("historie", json.dumps(stand))
                teile.append(f"{QUELLEN[name]['text']}: {e['neu']} Sets übernommen")
            except Exception as ex:  # noqa: BLE001
                log.exception("Historie von %s fehlgeschlagen", name)
                teile.append(f"{QUELLEN[name]['text']}: Fehler – {ex}")
        msg = " | ".join(teile) or "Historie ist bereits vollständig importiert."
        db.meta_set("historie_meldung", msg)
        log.info("Historie: %s", msg)
        return msg
    finally:
        status["historie_laeuft"] = False
        _lock.release()


def info() -> dict:
    return {
        "aktiv": sorted(aktive()),
        "texte": {k: v["text"] for k, v in QUELLEN.items()},
        "alle": [{"id": k, "text": v["text"], "spiele": v["spiele"]} for k, v in QUELLEN.items()],
        "intervall_stunden": INTERVALL_STUNDEN,
        "laeuft": status["laeuft"],
        "letzter_abgleich": db.meta_get("letzter_abgleich"),
        "letzte_meldung": status["letzte_meldung"] or db.meta_get("letzter_abgleich_meldung"),
        "historie": historie_stand(),
        "historie_laeuft": status["historie_laeuft"],
        "historie_meldung": db.meta_get("historie_meldung"),
        "historie_aktiv": HISTORIE_AKTIV,
    }


def starte_hintergrund():
    def faellig() -> bool:
        letzte = db.meta_get("letzter_abgleich")
        if not letzte:
            return True
        return datetime.now(timezone.utc) - datetime.fromisoformat(letzte) >= timedelta(hours=INTERVALL_STUNDEN)

    def schleife():
        time.sleep(15)
        if HISTORIE_AKTIV:
            try:
                historie_importieren()
            except Exception:  # noqa: BLE001
                log.exception("Historie-Import fehlgeschlagen")
        if INTERVALL_STUNDEN <= 0:
            log.info("Regelmäßiger Datenquellen-Abgleich deaktiviert.")
            return
        while True:
            try:
                if aktive() and faellig():
                    abgleichen()
            except Exception:  # noqa: BLE001
                log.exception("Geplanter Abgleich fehlgeschlagen")
            time.sleep(600)

    threading.Thread(target=schleife, daemon=True, name="quellen").start()
