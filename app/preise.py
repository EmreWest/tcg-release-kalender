"""Cardmarket-Preise für die Kartenlisten.

Cardmarket veröffentlicht täglich frei herunterladbare Dateien: einen Produktkatalog und einen
Preisführer je Spiel (downloads.s3.cardmarket.com). Der Container lädt beide einmal am Tag für
die Spiele mit freigeschalteten Kartenlisten, speichert sie in der Datenbank und ordnet die
Karten den Cardmarket-Produkten zu:

- Magic (Scryfall) und Pokémon (TCGdex) liefern die Cardmarket-Produktnummer direkt mit.
- Bei allen anderen Spielen wird erst die passende Cardmarket-Erweiterung ermittelt (die mit den
  meisten gleichen Kartennamen bzw. -nummern) und dann je Karte über Nummer oder Namen zugeordnet.
- Gibt es in der Erweiterung mehrere Versionen einer Karte (z. B. normale und Alternate-Art-Version),
  lässt sich nicht sicher sagen, welche gemeint ist. Dann zeigt die Seite die Preisspanne aller
  Versionen und verlinkt auf die Cardmarket-Suche statt auf ein einzelnes Produkt.
"""
import json
import logging
import re
import threading
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from . import db, einstellungen, karten, quellen

log = logging.getLogger("tcg-kalender.preise")

INTERVALL_STUNDEN = 24
BASIS = "https://downloads.s3.cardmarket.com/productCatalog"
# Spiel -> (Cardmarket-Spielnummer, Pfad auf cardmarket.com)
SPIELE = {
    "magic": (1, "Magic"), "yugioh": (3, "YuGiOh"), "pokemon": (6, "Pokemon"), "vanguard": (8, "Vanguard"),
    "fftcg": (9, "FinalFantasy"), "weiss": (10, "WeissSchwarz"), "dbfw": (13, "DragonBallSuper"),
    "fab": (16, "FleshAndBlood"), "digimon": (17, "Digimon"), "onepiece": (18, "OnePiece"), "lorcana": (19, "Lorcana"),
    "swu": (21, "StarWarsUnlimited"), "riftbound": (22, "Riftbound"), "cyberpunk": (23, "Cyberpunk"),
    "gundam": (24, "Gundam"), "sorcery": (25, "Sorcery"),
}
# Spiele, bei denen Cardmarket die Kartennummer im Namen führt, z. B. „Franky (OP18-021)“
MIT_NUMMER = {"onepiece", "digimon", "gundam", "fftcg", "dbfw"}
# Hinweise im Kartennamen, die auf eine besondere Version deuten
_VARIANTE = re.compile(r"\b(parallel|alternate art|alt art|manga|sp\b|gold|special|showcase|hyperspace|prestige|"
                       r"serialized|full art|borderless|extended art|foil|treasure|textured|enchanted|iconic|epic)", re.I)
_FAB_PITCH = {"rot": "red", "gelb": "yellow", "blau": "blue"}

status = {"laeuft": False, "meldung": None}
_lock = threading.Lock()


def aktiv() -> bool:
    return bool(einstellungen.lesen().get("cardmarket_preise"))


def unterstuetzt(spiel: str) -> bool:
    return spiel in SPIELE


# --- Hilfsfunktionen ------------------------------------------------------------------

def _laden(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": quellen.USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def _norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower().replace("&", "and"))


def _ohne_klammern(name: str) -> str:
    return re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", name or "").strip()


_NUMMER = re.compile(r"^([A-Z]*?)(\d+)-(\d+)[A-Z]?$")


def _nummer(text: str | None) -> str | None:
    """Kartennummer vereinheitlichen: „BT06-011“, „BT6-011“ und „BT6-11“ ergeben denselben Schlüssel."""
    t = re.sub(r"\s+[A-Z]{1,4}\+?$", "", (text or "").strip().upper())  # Seltenheit hinter der Nummer, z. B. „BT20-013 C“
    t = re.sub(r"[^A-Z0-9-]", "", t)
    m = _NUMMER.match(t)
    if m:
        return f"{m.group(1).lower()}{int(m.group(2))}-{int(m.group(3))}"
    return _norm(t) or None


def _nummer_aus_cm(name: str) -> str | None:
    m = re.search(r"\(([A-Z0-9]{1,6}-?[A-Z0-9]*-\d{1,4}[A-Z]?|\d{1,2}-\d{3})\)", name or "")
    return _nummer(m.group(1)) if m else None


def _nummer_unsere(spiel: str, nummer: str | None) -> str | None:
    return _nummer(nummer)


def _schluessel_unser(spiel: str, karte: dict) -> str | None:
    if spiel in MIT_NUMMER:
        return _nummer_unsere(spiel, karte.get("nummer"))
    name = karte.get("name") or ""
    if spiel == "fab":
        name = re.sub(r"\((Rot|Gelb|Blau)\)", lambda m: f"({_FAB_PITCH[m.group(1).lower()]})", name)
        return _norm(name)
    return _norm(_ohne_klammern(name))


def _schluessel_cm(spiel: str, name: str) -> str | None:
    if spiel in MIT_NUMMER:
        return _nummer_aus_cm(name)
    if spiel == "fab":
        return _norm(name)
    return _norm(_ohne_klammern(name))


def cm_url(spiel: str, produkt: int | None = None, suche: str | None = None, erweiterung: int | None = None) -> str | None:
    """Link zu Cardmarket. Die allgemeine Suche (Products/Search) verlangt eine Anmeldung, die gefilterte
    Liste der Einzelkarten (Products/Singles) nicht – deshalb wird bei mehreren Versionen dorthin verlinkt."""
    if spiel not in SPIELE:
        return None
    pfad = SPIELE[spiel][1]
    if produkt:
        return f"https://www.cardmarket.com/de/{pfad}/Products?idProduct={produkt}"
    if suche:
        filter_exp = f"idExpansion={erweiterung}&" if erweiterung else ""
        return f"https://www.cardmarket.com/de/{pfad}/Products/Singles?{filter_exp}searchString={quote(suche)}"
    return None


# --- Abruf ----------------------------------------------------------------------------

def _dateien_laden(spiel: str) -> int:
    nr = SPIELE[spiel][0]
    produkte = _laden(f"{BASIS}/productList/products_singles_{nr}.json").get("products") or []
    try:
        versiegelt = _laden(f"{BASIS}/productList/products_nonsingles_{nr}.json").get("products") or []
    except Exception:  # noqa: BLE001
        versiegelt = []
    preise = _laden(f"{BASIS}/priceGuide/price_guide_{nr}.json").get("priceGuides") or []
    ids = {p["idProduct"] for p in produkte} | {p["idProduct"] for p in versiegelt}
    with db.verbindung() as con:
        con.execute("DELETE FROM cm_produkte WHERE spiel = ?", (spiel,))
        con.execute("DELETE FROM cm_preise WHERE spiel = ?", (spiel,))
        con.executemany("INSERT OR REPLACE INTO cm_produkte (id, spiel, name, expansion, art) VALUES (?,?,?,?,?)",
                        [(p["idProduct"], spiel, p.get("name") or "", p.get("idExpansion"), "einzel") for p in produkte]
                        + [(p["idProduct"], spiel, p.get("name") or "", p.get("idExpansion"), "sealed") for p in versiegelt])
        con.executemany(
            "INSERT OR REPLACE INTO cm_preise (id, spiel, trend, avg30, avg7, low, trend_foil, avg30_foil) VALUES (?,?,?,?,?,?,?,?)",
            [(p["idProduct"], spiel, p.get("trend"), p.get("avg30"), p.get("avg7"), p.get("low"),
              p.get("trend-foil") or p.get("trend-holo") or None, p.get("avg30-foil") or p.get("avg30-holo") or None)
             for p in preise if p.get("idProduct") in ids])
    return len(produkte)


# Typische Wertigkeit von Seltenheiten und Versionen (nur die Reihenfolge innerhalb einer Karte zählt)
_SELTENHEIT = [
    (r"mega hyper", 9.5), (r"besondere illustration|special illustration", 9), (r"hyper", 8.5),
    (r"ultra selten|ultra rare", 7), (r"illustration", 6), (r"glänzend|shiny", 6.5), (r"ace spec", 5),
    (r"doppelselten|double rare", 5), (r"starlight", 9.5), (r"quarter century", 9), (r"ghost", 8.5),
    (r"collector|prismatic secret|platinum secret|ultimate", 8), (r"secret|\bsec\b", 7), (r"\bsuper\b|\bsr\b", 5),
    (r"grand master", 8.8), (r"mythic", 6), (r"fabled|enchanted", 9), (r"legend", 7), (r"iconic|showcase", 8), (r"epic|prestige", 7),
    (r"majestic|special", 5.5), (r"hyperspace", 4), (r"\bl\b|leader", 4), (r"selten|\brare\b|\br\b", 3),
    (r"ungewöhnlich|uncommon|\buc\b", 2), (r"häufig|common|\bc\b", 1),
]
_ZUSATZ = [(r"serialized|serial", 5), (r"manga", 3), (r"gold", 2.5), (r"\bsp\b", 2), (r"treasure", 2),
           (r"showcase", 2), (r"prestige", 2), (r"alternate art|alt art|parallel", 1.5), (r"full art", 1),
           (r"borderless|extended art", 0.8), (r"hyperspace", 0.5)]


def _stufe(karte) -> float:
    selten = (karte["seltenheit"] or "").lower()
    wert = next((w for muster, w in _SELTENHEIT if re.search(muster, selten)), 3) + selten.count("+")  # „LR+“, „LR++“
    text = f"{karte['name'] or ''} {karte['variante'] or ''}".lower()
    return wert + sum(w for muster, w in _ZUSATZ if re.search(muster, text))


def _nach_rang(mitglieder: list, versionen: list[int], preis: dict[int, float]) -> dict[int, tuple]:
    """Mehrere Versionen einer Karte: Karten nach Seltenheit/Version und Cardmarket-Versionen nach Preis
    ordnen und paarweise zuordnen (die gewöhnliche Version ist fast immer die günstigste).
    Ergebnis je Karte: (Produktnummer, None, geschätzt) oder (None, Kandidaten, False)."""
    # Versionen ohne Preis (noch keine Verkäufe) sind meist die seltensten – sie kommen ans Ende
    bepreist = sorted((v for v in versionen if preis.get(v)), key=lambda v: preis[v]) + \
        [v for v in versionen if not preis.get(v)]
    # Reihenfolge der eigenen Karten: am besten nach TCGplayer-Marktpreis des Sets (zeigt, welche Version am
    # Markt begehrter ist, z. B. Special Illustration Rare vor Mega Hyper Rare), sonst nach Seltenheit
    tcg = [k.get("tcg_preis") if hasattr(k, "get") else k["tcg_preis"] for k in mitglieder]
    if all(tcg) and len(set(tcg)) == len(tcg):
        rang = {k["id"]: t for k, t in zip(mitglieder, tcg)}
    else:
        rang = {k["id"]: _stufe(k) for k in mitglieder}
    karten = sorted(mitglieder, key=lambda k: (rang[k["id"]], k["pos"]))
    stufen = [rang[k["id"]] for k in karten]
    ergebnis = {k["id"]: (None, versionen, False) for k in karten}
    if not any(preis.get(v) for v in versionen) or len(karten) > len(bepreist):
        return ergebnis
    if len(karten) == len(bepreist):
        paare = list(zip(karten, bepreist))
    else:
        # Weniger Karten als Versionen: die niedrigste bekommt die günstigste, die höchste die teuerste
        paare = [(karten[0], bepreist[0])]
        if len(karten) > 1:
            paare.append((karten[-1], bepreist[-1]))
        if len(karten) == 1 and _stufe(karten[0]) > 3.5:
            return ergebnis  # nur eine besondere Version in der Liste – welche, ist offen
    for karte, version in paare:
        gleich = stufen.count(rang[karte["id"]]) > 1
        if not gleich:  # zwei Karten mit gleicher Stufe lassen sich nicht unterscheiden
            ergebnis[karte["id"]] = (version, None, len(versionen) > 1)
    return ergebnis


def zuordnen(spiel: str) -> dict:
    """Ordnet alle Karten eines Spiels den Cardmarket-Produkten zu."""
    with db.verbindung() as con:
        produkte = con.execute("SELECT id, name, expansion FROM cm_produkte WHERE spiel = ? AND art = 'einzel'",
                               (spiel,)).fetchall()
        karten_rows = con.execute("SELECT id, release_id, pos, nummer, name, variante, seltenheit, cm_quelle, tcg_preis FROM karten "
                                  "WHERE spiel = ?", (spiel,)).fetchall()
        preis = {r["id"]: r["trend"] or r["avg30"] or r["low"] for r in con.execute(
            "SELECT id, trend, avg30, low FROM cm_preise WHERE spiel = ?", (spiel,))}
    if not produkte:
        return {"eindeutig": 0, "mehrdeutig": 0, "produkte": produkte_zuordnen(spiel)}
    bekannt = {p["id"] for p in produkte}
    info = {p["id"]: (p["expansion"], _schluessel_cm(spiel, p["name"])) for p in produkte}
    je_exp: dict[int, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for p in produkte:
        s = _schluessel_cm(spiel, p["name"])
        if s:
            je_exp[p["expansion"]][s].append(p["id"])
    je_release: dict[int, list] = defaultdict(list)
    for k in karten_rows:
        je_release[k["release_id"]].append(k)

    def beste_erweiterung(schluessel_menge: set[str], ausser=None):
        zaehler = Counter()
        for exp, index in je_exp.items():
            if exp == ausser:
                continue
            treffer = len(schluessel_menge & index.keys())
            if treffer:
                zaehler[exp] = treffer
        exp, treffer = (zaehler.most_common(1) or [(None, 0)])[0]
        if exp is not None and treffer >= max(2, len(schluessel_menge) * 0.3):
            return exp
        return None

    updates = []
    eindeutig = mehrdeutig = 0
    for rid, liste in je_release.items():
        schluessel = {k["id"]: _schluessel_unser(spiel, dict(k)) for k in liste}
        variante = {k["id"]: bool(k["variante"]) or bool(_VARIANTE.search(k["name"] or "")) for k in liste}
        # Schlüssel, zu denen die Liste auch eine normale Version enthält
        mit_normaler = {schluessel[k["id"]] for k in liste if not variante[k["id"]]}
        ergebnis: dict[int, tuple] = {}
        # Dieselbe Produktnummer bei mehreren Karten (TCGdex ordnet Illustrations-Seltenheiten oft der
        # normalen Karte zu): dann gelten alle Versionen dieser Karte in der Erweiterung als Kandidaten
        haeufigkeit = Counter(k["cm_quelle"] for k in liste if k["cm_quelle"] in bekannt)
        # 1. Hauptzuordnung über die Cardmarket-Erweiterung mit den meisten Treffern
        haupt = beste_erweiterung({x for x in schluessel.values() if x})
        index = je_exp.get(haupt, {})
        for k in liste:
            if k["cm_quelle"] and k["cm_quelle"] in bekannt:  # Nummer direkt aus der Quelle (Scryfall, TCGdex)
                exp, key = info[k["cm_quelle"]]
                versionen = je_exp.get(exp, {}).get(key or "", [])
                if haeufigkeit[k["cm_quelle"]] > 1 and len(versionen) > 1:
                    ergebnis[k["id"]] = (None, versionen)
                else:
                    ergebnis[k["id"]] = (k["cm_quelle"], None)
                continue
            kandidaten = index.get(schluessel[k["id"]] or "", [])
            if len(kandidaten) == 1 and (not variante[k["id"]] or schluessel[k["id"]] not in mit_normaler):
                ergebnis[k["id"]] = (kandidaten[0], None)
            elif len(kandidaten) > 1:
                ergebnis[k["id"]] = (None, kandidaten)
        # 2. Besondere Versionen führt Cardmarket teils in einer eigenen Erweiterung (z. B. „Extras“)
        rest = [k for k in liste if k["id"] not in ergebnis and schluessel[k["id"]]]
        if rest:
            zweit = beste_erweiterung({schluessel[k["id"]] for k in rest}, ausser=haupt)
            index2 = je_exp.get(zweit, {})
            for k in rest:
                kandidaten = index2.get(schluessel[k["id"]], [])
                if len(kandidaten) == 1:
                    ergebnis[k["id"]] = (kandidaten[0], None)
                elif len(kandidaten) > 1:
                    ergebnis[k["id"]] = (None, kandidaten)
        # 3. Mehrere Versionen: nach Seltenheit und Preis einander zuordnen
        gruppen: dict[tuple, list] = defaultdict(list)
        for k in liste:
            cm, mehrere = ergebnis.get(k["id"], (None, None))
            if mehrere:
                gruppen[tuple(sorted(mehrere))].append(k)
        geschaetzt = {}
        for versionen, mitglieder in gruppen.items():
            for kid, (cm, mehrere, schaetzung) in _nach_rang(mitglieder, list(versionen), preis).items():
                ergebnis[kid] = (cm, mehrere)
                geschaetzt[kid] = schaetzung
        for k in liste:
            cm, mehrere = ergebnis.get(k["id"], (None, None))
            if cm:
                eindeutig += 1
            elif mehrere:
                mehrdeutig += 1
            updates.append((cm, ",".join(map(str, sorted(mehrere))) if mehrere else None,
                            1 if geschaetzt.get(k["id"]) else 0, k["id"]))
    with db.verbindung() as con:
        con.executemany("UPDATE karten SET cm_id = ?, cm_kandidaten = ?, cm_geschaetzt = ? WHERE id = ?", updates)
    return {"eindeutig": eindeutig, "mehrdeutig": mehrdeutig, "produkte": produkte_zuordnen(spiel)}


_SYNONYME = {"blisters": "blister", "boosters": "booster", "packs": "pack", "single": "1", "one": "1", "three": "3",
             "decks": "deck", "tins": "tin", "boxes": "box", "display": "box"}
_FUELL = {"pack", "the", "of", "and", "edition", "english", "en", "version", "retail", "lgs", "cards", "card"}


def _woerter(text: str) -> frozenset:
    w = re.findall(r"[a-z0-9]+", (text or "").lower().replace("&", " and "))
    return frozenset(_SYNONYME.get(x, x) for x in w if _SYNONYME.get(x, x) not in _FUELL)


def produkte_zuordnen(spiel: str) -> int:
    """Versiegelte Produkte (TCGplayer) den Cardmarket-Produkten zuordnen – nur bei eindeutigem Treffer."""
    with db.verbindung() as con:
        cm = con.execute("SELECT id, name, expansion FROM cm_produkte WHERE spiel = ? AND art = 'sealed'", (spiel,)).fetchall()
        zeilen = con.execute("SELECT p.id, p.release_id, p.name, r.name AS set_name FROM setprodukte p "
                             "JOIN releases r ON r.id = p.release_id WHERE p.spiel = ?", (spiel,)).fetchall()
    if not cm or not zeilen:
        return 0
    je_set: dict[int, list] = defaultdict(list)
    for z in zeilen:
        je_set[z["release_id"]].append(z)
    cm_woerter = [(c["id"], c["expansion"], _woerter(c["name"])) for c in cm]
    updates, n = [], 0
    for rid, liste in je_set.items():
        set_w = _woerter(liste[0]["set_name"])
        if not set_w:
            continue
        # Kandidaten: Cardmarket-Produkte, die den Set-Namen enthalten; davon die häufigste Erweiterung
        passend = [c for c in cm_woerter if set_w <= c[2]]
        haeufig = Counter(c[1] for c in passend).most_common(1)
        if haeufig:
            passend = [c for c in passend if c[1] == haeufig[0][0]]
        for z in liste:
            # TCGplayer „Half Booster Box“ heißt bei Cardmarket „Booster Box (18 Boosters)“
            name = re.sub(r"half booster box", "booster box 18 boosters", z["name"], flags=re.I)
            w = _woerter(re.sub(r"[\[\]()]", " ", name))
            treffer = [c[0] for c in passend if c[2] == w]
            if not treffer:  # Reihenfolge und Füllwörter weichen ab, z. B. „3 Pack Blisters [Zapdos]“
                aehnlich = [(len(w & c[2]) / len(w | c[2]), c[0]) for c in passend if w | c[2]]
                aehnlich = sorted((a for a in aehnlich if a[0] >= 0.85), reverse=True)
                if aehnlich and (len(aehnlich) == 1 or aehnlich[0][0] > aehnlich[1][0]):
                    treffer = [aehnlich[0][1]]
            cm_id = treffer[0] if len(treffer) == 1 else None
            n += bool(cm_id)
            updates.append((cm_id, z["id"]))
    with db.verbindung() as con:
        con.executemany("UPDATE setprodukte SET cm_id = ? WHERE id = ?", updates)
    return n


def aktualisieren(nur: str | None = None) -> str:
    if not _lock.acquire(blocking=False):
        return "Läuft bereits."
    status["laeuft"] = True
    try:
        teile = []
        stand = _stand()
        for spiel in sorted(karten.freigeschaltet()):
            if (nur and nur != spiel) or not unterstuetzt(spiel):
                continue
            try:
                n = _dateien_laden(spiel)
                z = zuordnen(spiel)
                stand[spiel] = {"zeit": datetime.now(timezone.utc).isoformat(), "produkte": n, **z}
                teile.append(f"{spiel}: {z['eindeutig']} Karten zugeordnet")
            except Exception as e:  # noqa: BLE001
                log.warning("Cardmarket-Preise für %s nicht abrufbar: %s", spiel, e)
                teile.append(f"{spiel}: Fehler – {e}")
            time.sleep(1)
        db.meta_set("cm_stand", json.dumps(stand))
        msg = " | ".join(teile) or "Kein Spiel mit Kartenlisten freigeschaltet."
        status["meldung"] = msg
        log.info("Cardmarket-Preise: %s", msg)
        return msg
    finally:
        status["laeuft"] = False
        _lock.release()


def _stand() -> dict:
    try:
        return json.loads(db.meta_get("cm_stand") or "{}")
    except ValueError:
        return {}


def info() -> dict:
    return {"aktiv": aktiv(), "laeuft": status["laeuft"], "meldung": status["meldung"], "stand": _stand()}


# --- Abfragen ---------------------------------------------------------------------------

def fuer_release(spiel: str, karten_rows: list[dict]) -> list[dict | None]:
    """Preisangaben je Karte (in derselben Reihenfolge) oder None."""
    if not aktiv() or not unterstuetzt(spiel):
        return [None] * len(karten_rows)
    ids = set()
    for k in karten_rows:
        if k.get("cm_id"):
            ids.add(k["cm_id"])
        for x in (k.get("cm_kandidaten") or "").split(","):
            if x:
                ids.add(int(x))
    preise, namen = {}, {}
    if ids:
        with db.verbindung() as con:
            liste = sorted(ids)
            for start in range(0, len(liste), 900):
                teil = liste[start:start + 900]
                platz = ",".join("?" * len(teil))
                for r in con.execute(f"SELECT * FROM cm_preise WHERE id IN ({platz})", teil):
                    preise[r["id"]] = dict(r)
                for r in con.execute(f"SELECT id, name, expansion FROM cm_produkte WHERE id IN ({platz})", teil):
                    namen[r["id"]] = (r["name"], r["expansion"])
    ergebnis = []
    versiegelt = bool(karten_rows) and "nummer" not in karten_rows[0]
    for k in karten_rows:
        such = _ohne_klammern(k.get("name") or "")
        kandidaten = [int(x) for x in (k.get("cm_kandidaten") or "").split(",") if x]
        if kandidaten and kandidaten[0] in namen:
            # Suche mit dem Cardmarket-Namen (bei Pokémon englisch) in der passenden Erweiterung
            cm_name, erweiterung = namen[kandidaten[0]]
            cm_name = re.sub(r"\s*\[[^\]]*\]", "", cm_name).strip()
        else:
            cm_name, erweiterung = such, None
        if k.get("cm_id"):
            p = preise.get(k["cm_id"]) or {}
            e = {"url": cm_url(spiel, k["cm_id"])}
            if k.get("cm_geschaetzt"):
                e["geschaetzt"] = True
            for feld in ("trend", "avg30", "low", "trend_foil"):
                if p.get(feld):
                    e[feld] = round(p[feld], 2)
            ergebnis.append(e)
        elif k.get("cm_kandidaten"):
            werte = [preise[int(x)]["trend"] for x in k["cm_kandidaten"].split(",")
                     if int(x) in preise and preise[int(x)].get("trend")]
            e = {"url": cm_url(spiel, suche=cm_name, erweiterung=erweiterung), "versionen": len(kandidaten)}
            if werte:
                e["von"], e["bis"] = round(min(werte), 2), round(max(werte), 2)
            ergebnis.append(e)
        elif such and not versiegelt and spiel != "pokemon":
            # Ohne Zuordnung: Suche nach dem Namen (nicht bei Pokémon, dort sind unsere Namen deutsch)
            ergebnis.append({"url": cm_url(spiel, suche=such)})
        else:
            ergebnis.append(None)
    return ergebnis


def stand_fuer(spiel: str) -> str | None:
    return (_stand().get(spiel) or {}).get("zeit")


# --- Hintergrund ---------------------------------------------------------------------------

def starte_hintergrund():
    def schleife():
        time.sleep(180)
        while True:
            try:
                if aktiv():
                    stand = _stand()
                    faellig = [s for s in karten.freigeschaltet() if unterstuetzt(s) and (
                        not (stand.get(s) or {}).get("zeit") or datetime.now(timezone.utc)
                        - datetime.fromisoformat(stand[s]["zeit"]) >= timedelta(hours=INTERVALL_STUNDEN))]
                    if faellig:
                        aktualisieren()
            except Exception:  # noqa: BLE001
                log.exception("Cardmarket-Preise fehlgeschlagen")
            time.sleep(3600)

    threading.Thread(target=schleife, daemon=True, name="cardmarket").start()
