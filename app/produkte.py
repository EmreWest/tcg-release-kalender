"""Versiegelte Produkte je Set (Display, Booster, Bundle, Top-Trainer-Box …) mit Bild.

Quelle ist der TCGplayer-Katalog über TCGCSV: Er führt zu jedem Set auch die versiegelten Produkte
mit Produktbild. Bilder werden wie die Kartenbilder einmal geladen und lokal gespeichert.
Preistrend und Link kommen aus dem Cardmarket-Katalog (siehe preise.py).
"""
import logging
import re
import time
from datetime import date
from urllib.parse import quote

from . import db, quellen

log = logging.getLogger("tcg-kalender.produkte")

# TCGplayer-Kategorien der Spiele, deren Kartenliste aus einer anderen Quelle kommt
TCG_KATEGORIEN = {"magic": 1, "yugioh": 2, "pokemon": 3, "fab": 62, "swu": 79, **quellen.TCGCSV_KATEGORIEN}
MAX_PRODUKTE = 16

# Was in einer Übersicht für Kundschaft nur stört: Codes, Kartons, Händler-Sonderausgaben, Sets aus mehreren Stück
_WEG = re.compile(
    r"code card|\bcostco\b|\bwalmart\b|\bsam's club\b|\bcase\b|set of \d|\[set of|fun pack|checklane|pokemon center|exclusive|art bundle|"
    r"prerelease|pre-release|\bpromo|jumbo|\bsealed\b.*\blot\b|\bkit\b.*\bstore\b|\bmystery\b|"
    r"\bsample\b|\bdemo\b|tournament|\bplaymat\b|\bsleeves?\b|\bdeck box\b|\bbinder\b|\bportfolio\b|\bdice\b|"
    r"don!! card|\btoken\b|energy card|life counter", re.I)

# Reihenfolge in der Anzeige: große Produkte zuerst
_RANG = [r"(?<!half )booster box|display", r"half booster box", r"elite trainer|top-trainer", r"collector booster", r"bundle", r"build & battle",
         r"booster pack|^.*booster$|sleeved", r"blister", r"deck", r"tin", r"collection|kollektion"]


def _rang(name: str) -> int:
    n = name.lower()
    return next((i for i, muster in enumerate(_RANG) if re.search(muster, n)), len(_RANG))

# Englische Produktbezeichnungen und ihre deutsche Entsprechung (längere zuerst)
_DEUTSCH = [
    ("elite trainer box", "Top-Trainer-Box"), ("half booster box", "Display (18 Booster)"), ("play booster display", "Play-Booster-Display"),
    ("collector booster display", "Collector-Booster-Display"), ("collector booster pack", "Collector-Booster"),
    ("sleeved play booster pack", "Sleeved Play-Booster"), ("play booster pack", "Play-Booster"),
    ("draft booster pack", "Draft-Booster"), ("set booster pack", "Set-Booster"), ("sleeved booster pack", "Sleeved Booster"),
    ("booster pack", "Booster"), ("booster box", "Display"), ("booster display", "Display"),
    ("booster bundle", "Booster-Bundle"), ("build & battle box", "Build & Battle Box"),
    ("collector booster box", "Collector-Booster-Display"), ("collector booster", "Collector-Booster"),
    ("play booster box", "Play-Booster-Display"), ("play booster", "Play-Booster"),
    ("draft booster box", "Draft-Booster-Display"), ("set booster box", "Set-Booster-Display"),
    ("premium collection", "Premium-Kollektion"), ("collection box", "Kollektion"), ("collection", "Kollektion"),
    ("3 pack blisters", "3er-Blister"), ("3 pack blister", "3er-Blister"), ("single pack blister", "1er-Blister"),
    ("starter deck", "Starterdeck"), ("structure deck", "Structure Deck"), ("commander deck", "Commander-Deck"),
    ("theme deck", "Themendeck"), ("tin", "Tin-Box"), ("bundle", "Bundle"),
]


def _norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _ohne_praefix(name: str) -> str:
    name = quellen._TCGCSV_PRAEFIX.sub("", name or "").strip()
    return re.sub(r"^[A-Z]{1,4}:\s+", "", name)  # z. B. „ME: 30th Celebration“


def sealed(produkte: list[dict]) -> list[dict]:
    """Versiegelte Produkte aus einer TCGCSV-Produktliste (ohne Einzelkarten und Kleinkram)."""
    ergebnis, gesehen = [], set()
    for p in produkte:
        ext = {e.get("name"): e.get("value") for e in p.get("extendedData") or []}
        name = (p.get("name") or "").strip()
        if ext.get("Number") or not name or _WEG.search(name):
            continue
        schluessel = _norm(re.sub(r"\s*\((lgs|retail|wpn|store|local game store)\)", "", name, flags=re.I))
        if schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        bild = p.get("imageUrl") or None
        ergebnis.append({"name": name, "tcg_id": p.get("productId"),
                         "bild_url": bild.replace("_200w.", "_400w.") if bild else None})
    ergebnis.sort(key=lambda p: _rang(p["name"]))
    return ergebnis[:MAX_PRODUKTE]


def name_deutsch(produkt: str, set_en: str, set_de: str | None) -> str | None:
    """„Surging Sparks Elite Trainer Box“ -> „Stürmische Funken Top-Trainer-Box“ (wenn möglich)."""
    rest = produkt
    if set_en and produkt.lower().startswith(set_en.lower()):
        rest = produkt[len(set_en):].lstrip(" :-–")
    elif set_en:
        return None  # Name folgt nicht dem Muster „Set + Produkt“ – lieber unverändert lassen
    zusatz = ""
    m = re.search(r"\s*(\[[^\]]+\]|\([^)]+\))\s*$", rest)
    if m:
        zusatz, rest = " " + m.group(1).strip("[]()"), rest[:m.start()]
    deutsch = None
    for en, de in _DEUTSCH:
        if rest.strip().lower() == en:
            deutsch = de
            break
    if not deutsch and not set_de:
        return None
    teil = deutsch or rest.strip()
    ergebnis = f"{set_de or set_en} {teil}{zusatz}".strip()
    return None if _norm(ergebnis) == _norm(produkt) else ergebnis


def _gruppe_finden(spiel: str, release: dict, kontext: dict) -> int | None:
    kategorie = TCG_KATEGORIEN.get(spiel)
    if not kategorie:
        return None
    cache = kontext.setdefault("tcg_gruppen", {})
    if kategorie not in cache:
        try:
            cache[kategorie] = quellen._laden(f"https://tcgcsv.com/tcgplayer/{kategorie}/groups").get("results") or []
        except Exception:  # noqa: BLE001
            cache[kategorie] = []
        time.sleep(0.2)
    name, code = _norm(release.get("name")), _norm(release.get("code"))
    woerter = set(re.findall(r"[a-z0-9]+", (release.get("name") or "").lower()))
    d = date.fromisoformat(release["datum"])
    beste = None
    for g in cache[kategorie]:
        ohne = _ohne_praefix(g.get("name"))
        gname, gwoerter = _norm(ohne), set(re.findall(r"[a-z0-9]+", ohne.lower()))
        gcode = _norm(g.get("abbreviation"))
        try:
            abstand = abs((date.fromisoformat((g.get("publishedOn") or "")[:10]) - d).days)
        except ValueError:
            abstand = 9999
        if gname == name:
            wert = (3, 0, -abstand)
        elif len(woerter) >= 2 and woerter <= gwoerter and abstand <= 45:
            # z. B. „30th Classic Collection“ in „30th Celebration Classic Collection“
            wert = (2, -len(gwoerter - woerter), -abstand)
        elif code and gcode == code and abstand <= 120:
            wert = (1, 0, -abstand)
        else:
            continue
        if beste is None or wert > beste[0]:
            beste = (wert, g["groupId"])
    return beste[1] if beste else None


def tcg_daten(spiel: str, release: dict, extern_id: str, kontext: dict) -> dict:
    """TCGplayer-Daten eines Sets: versiegelte Produkte, Einzelkarten (für Bilder und Zuordnung) und
    Marktpreise in USD (nur für die Reihenfolge mehrerer Versionen einer Karte; angezeigt wird Cardmarket)."""
    kategorie = TCG_KATEGORIEN.get(spiel)
    if extern_id.startswith("tcgcsv:"):
        gruppe, alle = extern_id.split(":", 1)[1], kontext.pop("tcg_alle", None)
    else:
        gruppe, alle = _gruppe_finden(spiel, release, kontext), None
    if not kategorie or not gruppe:
        return {"sealed": [], "karten": [], "preise": {}}
    if alle is None:
        time.sleep(0.2)
        alle = quellen._laden(f"https://tcgcsv.com/tcgplayer/{kategorie}/{quote(str(gruppe))}/products").get("results") or []
    preise: dict[int, float] = {}
    try:
        time.sleep(0.2)
        for p in quellen._laden(f"https://tcgcsv.com/tcgplayer/{kategorie}/{quote(str(gruppe))}/prices").get("results") or []:
            wert = p.get("marketPrice") or p.get("midPrice")
            if wert and wert > preise.get(p["productId"], 0):
                preise[p["productId"]] = wert
    except Exception:  # noqa: BLE001
        pass
    karten = []
    for p in alle:
        ext = {e.get("name"): e.get("value") for e in p.get("extendedData") or []}
        if ext.get("Number"):
            bild = p.get("imageUrl") or None
            karten.append({"tcg_id": p.get("productId"), "nummer": ext["Number"], "name": p.get("name") or "",
                           "bild_url": bild.replace("_200w.", "_400w.") if bild else None})
    return {"sealed": sealed(alle), "karten": karten, "preise": preise}


def _nummer(text: str | None) -> str:
    teil = (text or "").split("/")[0].strip().upper()
    return re.sub(r"^0+(?=\d)", "", re.sub(r"[^A-Z0-9-]", "", teil))


def karten_ergaenzen(karten: list[dict], daten: dict):
    """TCGplayer-Produktnummer, -Marktpreis und (falls unser Bild fehlt) -Bild zu den Karten ergänzen."""
    tcg = daten.get("karten") or []
    preise = daten.get("preise") or {}
    nach_nummer: dict[str, list] = {}
    nach_name: dict[str, list] = {}
    for t in tcg:
        nach_nummer.setdefault(_nummer(t["nummer"]), []).append(t)
        nach_name.setdefault(_norm(re.sub(r"\s+-\s+[\w/]+$", "", t["name"])), []).append(t)
    bekannt = {t["tcg_id"]: t for t in tcg}
    for k in karten:
        t = bekannt.get(k.get("tcg_id"))
        if not t:
            treffer = nach_nummer.get(_nummer(k.get("nummer")), [])
            if len(treffer) != 1 and k.get("name_en"):
                treffer = nach_name.get(_norm(k["name_en"]), [])  # z. B. Sammlungen mit Originalnummern
            t = treffer[0] if len(treffer) == 1 else None
        if not t:
            continue
        k["tcg_id"] = t["tcg_id"]
        if preise.get(t["tcg_id"]):
            k["tcg_preis"] = preise[t["tcg_id"]]
        if not k.get("bild_url") and t.get("bild_url"):
            k["bild_url"] = t["bild_url"]


def speichern(rid: int, spiel: str, produkte: list[dict] | None):
    if produkte is None:
        return
    from .karten import ERLAUBTE_HOSTS
    r = db.release(rid) or {}
    zeilen, urls = [], set()
    for pos, p in enumerate(produkte):
        url = p.get("bild_url")
        if url and not (url.startswith("https://") and url.split("/")[2] in ERLAUBTE_HOSTS):
            url = None
        if url:
            urls.add(url)
        zeilen.append((rid, spiel, pos, p["name"][:200], (name_deutsch(p["name"], r.get("name"), r.get("name_de")) or "")[:200] or None,
                       p.get("tcg_id"), url))
    with db.verbindung() as con:
        con.execute("DELETE FROM setprodukte WHERE release_id = ?", (rid,))
        con.executemany("INSERT INTO setprodukte (release_id, spiel, pos, name, name_de, tcg_id, bild_url) VALUES (?,?,?,?,?,?,?)",
                        zeilen)
        con.executemany("INSERT OR IGNORE INTO kartenbilder (url, spiel) VALUES (?, ?)", [(u, spiel) for u in urls])


def fuer_release(rid: int) -> list[dict]:
    with db.verbindung() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT p.name, p.name_de, p.cm_id, p.spiel, b.datei FROM setprodukte p "
            "LEFT JOIN kartenbilder b ON b.url = p.bild_url WHERE p.release_id = ? ORDER BY p.pos", (rid,))]
    return rows


def anzahlen(spiele: set[str]) -> dict[int, int]:
    if not spiele:
        return {}
    with db.verbindung() as con:
        rows = con.execute(f"SELECT release_id, COUNT(*) AS n FROM setprodukte WHERE spiel IN "
                           f"({','.join('?' * len(spiele))}) GROUP BY release_id", sorted(spiele)).fetchall()
    return {r["release_id"]: r["n"] for r in rows}
