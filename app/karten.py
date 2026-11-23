"""Kartenlisten mit Bildern je Set – optional, je Spiel in der Verwaltung freizuschalten.

- Ohne Freischaltung ist nichts davon sichtbar und es wird nichts abgerufen.
- Listen und Bilder liegen im Datenverzeichnis (Datenbank und /data/karten) und überleben
  Neustarts und Updates des Containers.
- Ein Bild wird genau einmal geladen, auf WebP verkleinert und danach nur noch lokal ausgeliefert.
  Besucher haben so keinen Kontakt zu fremden Servern.
- Listen werden nach dem ersten Abruf nur noch für neue und kommende Sets aktualisiert – dort
  kommen während der Spoiler-Phase laufend Karten dazu.
"""
import hashlib
import io
import json
import logging
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlparse

from PIL import Image

from . import db, einstellungen, quellen

log = logging.getLogger("tcg-kalender.karten")

INTERVALL_STUNDEN = float(os.environ.get("KARTEN_INTERVALL_STUNDEN") or "24")
BILDBREITE = 400
# Listen von Sets, die höchstens so lange erschienen sind (oder noch kommen), werden bei jedem Lauf aktualisiert
AKTUELL_TAGE = 60
MAX_BILD_BYTES = 12 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 40_000_000

# Bilder werden nur von diesen Servern geladen
ERLAUBTE_HOSTS = {
    "cards.scryfall.io", "images.ygoprodeck.com", "cdn.swu-db.com", "assets.tcgdex.net",
    "tcgplayer-cdn.tcgplayer.com", "legendstory-production-s3-public.s3.amazonaws.com",
    "storage.googleapis.com", "d2wlb52bya4y8z.cloudfront.net",
    # Set-Symbole und -Logos
    "svgs.scryfall.io", "dhhim4ltzu1pj.cloudfront.net",
}
# Mindestabstand je Server in Sekunden; YGOPRODeck bittet ausdrücklich um Zurückhaltung
HOST_PAUSE = {"images.ygoprodeck.com": 0.25}
STANDARD_PAUSE = 0.08
PARALLEL = 4

QUELLE_JE_SPIEL = {"magic": "scryfall", "yugioh": "ygoprodeck", "swu": "swudb", "fab": "fabcube", "pokemon": "tcgdex",
                   **{sp: "tcgcsv" for sp in quellen.TCGCSV_KATEGORIEN}}
QUELLEN_NAMEN = {
    "scryfall": "Scryfall", "ygoprodeck": "YGOPRODeck", "swudb": "SWU-DB", "fabcube": "The FaB Cube",
    "tcgdex": "TCGdex (deutsche Karten)", "tcgcsv": "TCGplayer-Katalog (über TCGCSV)",
}
QUELLEN_LINKS = {
    "scryfall": "https://scryfall.com/", "ygoprodeck": "https://ygoprodeck.com/", "swudb": "https://www.swu-db.com/",
    "fabcube": "https://github.com/the-fab-cube/flesh-and-blood-cards", "tcgdex": "https://tcgdex.dev/",
    "tcgcsv": "https://tcgcsv.com/",
}

status = {"laeuft": False, "spiel": None, "schritt": None, "fertig": 0, "gesamt": 0, "meldung": None}
_warteschlange: list[tuple[str, bool]] = []
_wl_lock = threading.Lock()
_abbruch = threading.Event()
_arbeiter: threading.Thread | None = None


# --- Einstellungen ------------------------------------------------------------------

def unterstuetzt(spiel: str) -> bool:
    return spiel in QUELLE_JE_SPIEL


def freigeschaltet() -> set[str]:
    vorhanden = {s["id"] for s in db.spiele()}
    return {s for s in (einstellungen.lesen().get("karten_spiele") or []) if s in vorhanden and unterstuetzt(s)}


def ist_aktiv(spiel: str) -> bool:
    return spiel in freigeschaltet()


def freischalten(spiel: str, an: bool):
    if not unterstuetzt(spiel):
        raise ValueError("Für dieses Spiel gibt es keine Datenquelle für Kartenlisten")
    aktuell = set(einstellungen.lesen().get("karten_spiele") or [])
    aktuell = aktuell | {spiel} if an else aktuell - {spiel}
    einstellungen.aendern({"karten_spiele": sorted(aktuell)})


def stand() -> dict:
    try:
        return json.loads(db.meta_get("karten_stand") or "{}")
    except ValueError:
        return {}


def _stand_setzen(spiel: str, werte: dict | None):
    s = stand()
    if werte is None:
        s.pop(spiel, None)
    else:
        s[spiel] = werte
    db.meta_set("karten_stand", json.dumps(s))


# --- Dateien ------------------------------------------------------------------------

def verzeichnis() -> str:
    return os.path.join(os.path.dirname(db.DB_PFAD) or ".", "karten")


_DATEINAME = re.compile(r"^[0-9a-f]{24}\.webp$")


def _datei(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:24] + ".webp"


def bild_pfad(spiel: str, datei: str) -> str | None:
    if not _DATEINAME.match(datei or "") or not re.fullmatch(r"[a-z0-9_-]+", spiel or ""):
        return None
    pfad = os.path.join(verzeichnis(), spiel, datei[:2], datei)
    return pfad if os.path.isfile(pfad) else None


# --- Zuordnung der Sets ----------------------------------------------------------------

def sets_verknuepfen(spiel: str) -> int:
    """Ordnet Termine ohne Verknüpfung über die Set-Liste der Quelle zu (legt nichts an, ändert nichts)."""
    q = QUELLE_JE_SPIEL[spiel]
    sets = [s for s in quellen.QUELLEN[q]["funktion"](quellen.ANFANG, True) if s["spiel"] == spiel]
    with quellen._lock:  # nicht gleichzeitig mit einem Abgleich in quellen_stand schreiben
        return quellen.verknuepfen(sets)


def verknuepfte_sets(spiel: str) -> list[dict]:
    praefix = QUELLE_JE_SPIEL[spiel] + ":"
    with db.verbindung() as con:
        rows = con.execute(
            "SELECT r.id AS release_id, r.datum, r.name AS release_name, MIN(q.extern_id) AS extern_id "
            "FROM releases r JOIN quellen_stand q ON q.release_id = r.id "
            "WHERE r.spiel = ? AND substr(q.extern_id, 1, ?) = ? GROUP BY r.id ORDER BY r.datum DESC",
            (spiel, len(praefix), praefix)).fetchall()
        namen = {r["extern_id"]: r["name"] for r in con.execute(
            "SELECT extern_id, name FROM quellen_stand WHERE substr(extern_id, 1, ?) = ?", (len(praefix), praefix))}
    return [{**dict(r), "quell_name": namen.get(r["extern_id"])} for r in rows]


# --- Kartenlisten je Quelle -----------------------------------------------------------

def _laden(url: str):
    return quellen._laden(url)


def _text(wert, laenge=200) -> str | None:
    if wert is None:
        return None
    t = re.sub(r"\s+", " ", str(wert)).strip()
    return t[:laenge] or None


SCRYFALL_SELTENHEIT = {"common": "Common", "uncommon": "Uncommon", "rare": "Rare", "mythic": "Mythic Rare",
                       "special": "Special", "bonus": "Bonus"}


def _scryfall(set_id: str, set_name: str | None, kontext: dict) -> list[dict]:
    s = _laden(f"https://api.scryfall.com/sets/{quote(set_id)}")
    url = s.get("search_uri")
    karten = []
    while url:
        time.sleep(0.1)  # Scryfall bittet um 50–100 ms Abstand
        try:
            seite = _laden(url)
        except urllib.error.HTTPError as e:
            if e.code == 404:  # Set ohne Karten (z. B. noch nicht gespoilert)
                break
            raise
        for c in seite.get("data") or []:
            vorne = (c.get("card_faces") or [{}])[0]
            bilder = c.get("image_uris") or vorne.get("image_uris") or {}
            karten.append({
                "nummer": c.get("collector_number"), "name": c.get("name"),
                "seltenheit": SCRYFALL_SELTENHEIT.get(c.get("rarity"), (c.get("rarity") or "").title() or None),
                "art": c.get("type_line") or vorne.get("type_line"), "bild_url": bilder.get("normal"),
                "cm_id": c.get("cardmarket_id"),
            })
        url = seite.get("next_page") if seite.get("has_more") else None
    return karten


def _ygo_index() -> dict[str, list[dict]]:
    """Alle Karten einmal je Lauf laden (YGOPRODeck bittet darum, die API sparsam zu nutzen)."""
    idx: dict[str, list[dict]] = {}
    for c in _laden("https://db.ygoprodeck.com/api/v7/cardinfo.php").get("data") or []:
        bild = (c.get("card_images") or [{}])[0].get("image_url")
        for s in c.get("card_sets") or []:
            idx.setdefault(s.get("set_name") or "", []).append({
                "nummer": s.get("set_code"), "name": c.get("name"), "seltenheit": s.get("set_rarity"),
                "art": c.get("humanReadableCardType") or c.get("type"), "bild_url": bild,
            })
    return idx


def _ygoprodeck(set_code: str, set_name: str | None, kontext: dict) -> list[dict]:
    if "ygo" not in kontext:
        kontext["ygo"] = _ygo_index()
    gesehen, karten = set(), []
    for k in kontext["ygo"].get(set_name or "", []):
        schluessel = (k["nummer"], k["seltenheit"])
        if schluessel not in gesehen:
            gesehen.add(schluessel)
            karten.append(k)
    return karten


def _swudb(set_id: str, set_name: str | None, kontext: dict) -> list[dict]:
    karten = []
    for c in _laden(f"https://api.swu-db.com/cards/{quote(set_id)}").get("data") or []:
        variante = c.get("VariantType") or "Normal"
        if "Foil" in variante or "Serialized" in variante:  # gleiches Bild wie die normale Variante
            continue
        name = c.get("Name") or ""
        if c.get("Subtitle"):
            name += f" – {c['Subtitle']}"
        karten.append({"nummer": c.get("Number"), "name": name, "seltenheit": c.get("Rarity"), "art": c.get("Type"),
                       "variante": None if variante == "Normal" else variante, "bild_url": c.get("FrontArt")})
    return karten


FAB_SELTENHEIT = {"C": "Common", "R": "Rare", "S": "Super Rare", "M": "Majestic", "L": "Legendary", "F": "Fabled",
                  "T": "Token", "V": "Marvel", "P": "Promo", "B": "Basic"}
FAB_PITCH = {"1": "Rot", "2": "Gelb", "3": "Blau"}
_FAB_FOLIE = {"S": 0, "R": 1, "C": 2, "G": 3}
_FAB_EDITION = {"N": 0, "U": 1, "F": 2, "A": 3}


def _fab_index() -> dict[str, dict[str, tuple]]:
    idx: dict[str, dict[str, tuple]] = {}
    url = "https://raw.githubusercontent.com/the-fab-cube/flesh-and-blood-cards/develop/json/english/card.json"
    for c in _laden(url):
        for p in c.get("printings") or []:
            je_set = idx.setdefault(p.get("set_id") or "", {})
            rang = (_FAB_FOLIE.get(p.get("foiling"), 9), _FAB_EDITION.get(p.get("edition"), 9))
            alt = je_set.get(p.get("id"))
            if alt is None or rang < alt[0]:
                je_set[p.get("id")] = (rang, c, p)
    return idx


def _fabcube(set_id: str, set_name: str | None, kontext: dict) -> list[dict]:
    if "fab" not in kontext:
        kontext["fab"] = _fab_index()
    karten = []
    for nummer, (_rang, c, p) in kontext["fab"].get(set_id, {}).items():
        name = c.get("name") or ""
        if FAB_PITCH.get(str(c.get("pitch") or "")):
            name += f" ({FAB_PITCH[str(c['pitch'])]})"
        karten.append({"nummer": nummer, "name": name, "seltenheit": FAB_SELTENHEIT.get(p.get("rarity"), p.get("rarity")),
                       "art": c.get("type_text"), "bild_url": p.get("image_url")})
    return karten


def _tcgdex(set_id: str, set_name: str | None, kontext: dict) -> list[dict]:
    s = _laden(f"https://api.tcgdex.net/v2/de/sets/{quote(set_id)}")
    alt = kontext.get("bisher") or {}
    # Für ältere Sets hat TCGdex oft keine deutschen Scans – dann das englische Kartenbild nehmen
    ersatz, englisch = {}, {}
    if any(not c.get("image") for c in s.get("cards") or []):
        try:
            en = _laden(f"https://api.tcgdex.net/v2/en/sets/{quote(set_id)}").get("cards") or []
            ersatz = {c.get("localId"): c.get("image") for c in en if c.get("image")}
            englisch = {c.get("localId"): c.get("name") for c in en}
        except Exception:  # noqa: BLE001
            ersatz = {}
    karten = []
    for c in s.get("cards") or []:
        bild = c.get("image") or ersatz.get(c.get("localId"))
        k = {"nummer": c.get("localId"), "name": c.get("name"), "name_en": englisch.get(c.get("localId")),
             "bild_url": f"{bild}/high.webp" if bild else None}
        bekannt = alt.get(k["nummer"])
        if bekannt and bekannt.get("seltenheit") and bekannt.get("cm_quelle"):
            k["seltenheit"], k["art"], k["cm_id"] = bekannt["seltenheit"], bekannt.get("art"), bekannt["cm_quelle"]
            k["tcg_id"] = bekannt.get("tcg_id")
        else:
            # Seltenheit und Cardmarket-Nummer stehen nur in den Einzeldaten der Karte –
            # einmal laden, danach wiederverwenden
            try:
                d = _laden(f"https://api.tcgdex.net/v2/de/cards/{quote(c['id'])}")
                k["seltenheit"], k["art"] = d.get("rarity"), d.get("category")
                preise = d.get("pricing") or {}
                k["cm_id"] = (preise.get("cardmarket") or {}).get("idProduct")
                k["tcg_id"] = next((v.get("productId") for v in (preise.get("tcgplayer") or {}).values()
                                    if isinstance(v, dict) and v.get("productId")), None)
            except Exception:  # noqa: BLE001
                if bekannt:
                    k["seltenheit"], k["art"] = bekannt.get("seltenheit"), bekannt.get("art")
            time.sleep(0.05)
        karten.append(k)
    return karten


def _tcgcsv_karten(produkte: list[dict]) -> list[dict]:
    karten = []
    for p in produkte:
        ext = {e.get("name"): e.get("value") for e in p.get("extendedData") or []}
        if not ext.get("Number"):  # Displays, Booster und anderes versiegeltes Produkt
            continue
        bild = p.get("imageUrl") or None
        karten.append({
            "tcg_id": p.get("productId"),
            "nummer": ext.get("Number"), "name": p.get("name"), "seltenheit": ext.get("Rarity"),
            "art": ext.get("CardType") or ext.get("Card Type") or ext.get("Type") or ext.get("Category"),
            "bild_url": bild.replace("_200w.", "_400w.") if bild else None,
        })
    return karten


def _tcgcsv(gruppe: str, set_name: str | None, kontext: dict) -> list[dict]:
    kategorie = quellen.TCGCSV_KATEGORIEN[kontext["spiel"]]
    time.sleep(0.2)
    alle = _laden(f"https://tcgcsv.com/tcgplayer/{kategorie}/{quote(gruppe)}/products").get("results") or []
    kontext["tcg_alle"] = alle  # versiegelte Produkte und Preise kommen aus derselben Liste
    return _tcgcsv_karten(alle)


LISTEN = {"scryfall": _scryfall, "ygoprodeck": _ygoprodeck, "swudb": _swudb, "fabcube": _fabcube,
          "tcgdex": _tcgdex, "tcgcsv": _tcgcsv}


def _sortierschluessel(nummer: str | None):
    teile = re.findall(r"\d+|\D+", nummer or "")
    return [(0, int(t), "") if t.isdigit() else (1, 0, t.lower()) for t in teile] or [(2, 0, "")]


def _liste_speichern(rid: int, spiel: str, extern_id: str, karten: list[dict]):
    karten = [k for k in karten if _text(k.get("name"))]
    karten.sort(key=lambda k: _sortierschluessel(k.get("nummer")))
    zeilen, urls = [], set()
    for pos, k in enumerate(karten):
        url = k.get("bild_url")
        if url and not (url.startswith("https://") and urlparse(url).hostname in ERLAUBTE_HOSTS):
            url = None
        if url:
            urls.add(url)
        cm, tcg = k.get("cm_id"), k.get("tcg_id")
        zeilen.append((rid, spiel, pos, _text(k.get("nummer"), 40), _text(k.get("name")), _text(k.get("seltenheit"), 60),
                       _text(k.get("art"), 120), _text(k.get("variante"), 60), url,
                       int(cm) if str(cm or "").isdigit() else None, int(tcg) if str(tcg or "").isdigit() else None,
                       k.get("tcg_preis")))
    with db.verbindung() as con:
        con.execute("DELETE FROM karten WHERE release_id = ?", (rid,))
        con.executemany("INSERT INTO karten (release_id, spiel, pos, nummer, name, seltenheit, art, variante, bild_url, "
                        "cm_quelle, tcg_id, tcg_preis) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", zeilen)
        con.executemany("INSERT OR IGNORE INTO kartenbilder (url, spiel) VALUES (?, ?)", [(u, spiel) for u in urls])
        con.execute("INSERT INTO kartenlisten (release_id, spiel, extern_id, anzahl, abgerufen, fehler) VALUES (?,?,?,?,?,NULL) "
                    "ON CONFLICT(release_id) DO UPDATE SET spiel = excluded.spiel, extern_id = excluded.extern_id, "
                    "anzahl = excluded.anzahl, abgerufen = excluded.abgerufen, fehler = NULL",
                    (rid, spiel, extern_id, len(zeilen), db.jetzt()))


def _fehler_vermerken(rid: int, spiel: str, extern_id: str, text: str):
    with db.verbindung() as con:
        con.execute("INSERT INTO kartenlisten (release_id, spiel, extern_id, anzahl, abgerufen, fehler) VALUES (?,?,?,0,?,?) "
                    "ON CONFLICT(release_id) DO UPDATE SET fehler = excluded.fehler",
                    (rid, spiel, extern_id, db.jetzt(), text[:300]))


# --- Bilder ---------------------------------------------------------------------------

_host_locks: dict[str, threading.Lock] = {}
_host_zeit: dict[str, float] = {}
_host_lock = threading.Lock()


def _warten(host: str):
    with _host_lock:
        lock = _host_locks.setdefault(host, threading.Lock())
    with lock:
        pause = HOST_PAUSE.get(host, STANDARD_PAUSE)
        rest = _host_zeit.get(host, 0) + pause - time.monotonic()
        if rest > 0:
            time.sleep(rest)
        _host_zeit[host] = time.monotonic()


def _herunterladen(url: str) -> bytes:
    host = urlparse(url).hostname
    if not url.startswith("https://") or host not in ERLAUBTE_HOSTS:
        raise ValueError(f"Server nicht erlaubt: {host}")
    _warten(host)
    req = urllib.request.Request(url, headers={"User-Agent": quellen.USER_AGENT, "Accept": "image/*"})
    with urllib.request.urlopen(req, timeout=40) as r:
        inhalt = r.read(MAX_BILD_BYTES + 1)
    if len(inhalt) > MAX_BILD_BYTES:
        raise ValueError("Bild zu groß")
    return inhalt


def _umwandeln(inhalt: bytes) -> bytes:
    with Image.open(io.BytesIO(inhalt)) as bild:
        if bild.format not in ("JPEG", "PNG", "WEBP", "GIF"):
            raise ValueError(f"Unbekanntes Bildformat: {bild.format}")
        bild.load()
        bild = bild.convert("RGBA" if bild.mode in ("RGBA", "LA", "P") else "RGB")
        if bild.width > BILDBREITE:
            bild.thumbnail((BILDBREITE, BILDBREITE * 4), Image.LANCZOS)
        aus = io.BytesIO()
        bild.save(aus, "WEBP", quality=76, method=4)
        return aus.getvalue()


def _bild_holen(url: str, spiel: str) -> tuple[str, int]:
    daten = _umwandeln(_herunterladen(url))
    datei = _datei(url)
    ordner = os.path.join(verzeichnis(), spiel, datei[:2])
    os.makedirs(ordner, exist_ok=True)
    ziel = os.path.join(ordner, datei)
    tmp = f"{ziel}.{threading.get_ident()}.tmp"
    with open(tmp, "wb") as f:
        f.write(daten)
    os.replace(tmp, ziel)
    return datei, len(daten)


def _bilder_laden(spiel: str):
    with db.verbindung() as con:
        offen = [r["url"] for r in con.execute(
            "SELECT url FROM kartenbilder WHERE spiel = ? AND datei IS NULL AND fehler < 3 "
            "AND url IN (SELECT bild_url FROM karten WHERE spiel = ? UNION SELECT bild_url FROM setprodukte WHERE spiel = ?)",
            (spiel, spiel, spiel))]
    status.update(schritt="Bilder", fertig=0, gesamt=len(offen))

    def einzeln(url):
        try:
            datei, groesse = _bild_holen(url, spiel)
            return url, datei, groesse, None
        except urllib.error.HTTPError as e:
            return url, None, 0, 3 if e.code in (403, 404, 410) else 1
        except Exception as e:  # noqa: BLE001
            log.debug("Bild %s: %s", url, e)
            return url, None, 0, 1

    with ThreadPoolExecutor(PARALLEL) as pool:
        for start in range(0, len(offen), 100):
            if _abbruch.is_set():
                break
            ergebnisse = list(pool.map(einzeln, offen[start:start + 100]))
            with db.verbindung() as con:
                for url, datei, groesse, fehler in ergebnisse:
                    if datei:
                        con.execute("UPDATE kartenbilder SET datei = ?, bytes = ?, versucht = ? WHERE url = ?",
                                    (datei, groesse, db.jetzt(), url))
                    else:
                        con.execute("UPDATE kartenbilder SET fehler = MIN(3, fehler + ?), versucht = ? WHERE url = ?",
                                    (fehler, db.jetzt(), url))
            status["fertig"] += len(ergebnisse)


def _aufraeumen(spiel: str):
    """Bilder entfernen, die keine Karte mehr braucht (z. B. nach neuer Version eines Scans)."""
    with db.verbindung() as con:
        weg = con.execute("SELECT url, datei FROM kartenbilder WHERE spiel = ? AND url NOT IN "
                          "(SELECT bild_url FROM karten WHERE spiel = ? AND bild_url IS NOT NULL "
                          "UNION SELECT bild_url FROM setprodukte WHERE spiel = ? AND bild_url IS NOT NULL)",
                          (spiel, spiel, spiel)).fetchall()
        for r in weg:
            if r["datei"]:
                try:
                    os.remove(os.path.join(verzeichnis(), spiel, r["datei"][:2], r["datei"]))
                except OSError:
                    pass
        con.executemany("DELETE FROM kartenbilder WHERE url = ?", [(r["url"],) for r in weg])


# --- Ablauf ----------------------------------------------------------------------------

def _abrufen(spiel: str, alle: bool = False) -> str:
    try:
        return _abrufen_intern(spiel, alle)
    finally:
        status.update(spiel=None, schritt=None)


def _abrufen_intern(spiel: str, alle: bool) -> str:
    q = QUELLE_JE_SPIEL[spiel]
    status.update(laeuft=True, spiel=spiel, schritt="Sets zuordnen", fertig=0, gesamt=0, meldung=None)
    bisheriger = stand().get(spiel)
    if not bisheriger:
        # Erster Abruf: vermerken, damit er nach einem Neustart des Containers automatisch weiterläuft
        _stand_setzen(spiel, {"zeit": None, "meldung": "Erster Abruf läuft", "abgeschlossen": False})
    elif bisheriger.get("pausiert"):
        _stand_setzen(spiel, {**bisheriger, "pausiert": False})
    try:
        sets_verknuepfen(spiel)
    except Exception:  # noqa: BLE001
        log.exception("Kartenlisten %s: Zuordnung der Sets fehlgeschlagen", spiel)
    sets = verknuepfte_sets(spiel)
    with db.verbindung() as con:
        bisher = {r["release_id"]: dict(r) for r in con.execute("SELECT * FROM kartenlisten WHERE spiel = ?", (spiel,))}
    grenze = (date.today() - timedelta(days=AKTUELL_TAGE)).isoformat()
    jahr = (date.today() - timedelta(days=365)).isoformat()

    def faellig(s):
        b = bisher.get(s["release_id"])
        if alle or not b or b["extern_id"] != s["extern_id"] or s["datum"] >= grenze:
            return True
        return (b["anzahl"] == 0 or b["fehler"]) and s["datum"] >= jahr

    offen = [s for s in sets if faellig(s)]
    status.update(schritt="Kartenlisten", fertig=0, gesamt=len(offen))
    kontext = {"spiel": spiel}
    fehler = 0
    for s in offen:
        if _abbruch.is_set():
            break
        kennung = s["extern_id"].split(":", 1)[1]
        try:
            if q == "tcgdex":
                with db.verbindung() as con:
                    kontext["bisher"] = {r["nummer"]: dict(r) for r in con.execute(
                        "SELECT nummer, seltenheit, art, cm_quelle, tcg_id FROM karten WHERE release_id = ?", (s["release_id"],))}
            karten = LISTEN[q](kennung, s["quell_name"], kontext)
            from . import produkte
            tcg = None
            try:
                # TCGplayer: versiegelte Produkte, Ersatzbilder und Marktpreise für die Reihenfolge der Versionen
                tcg = produkte.tcg_daten(spiel, db.release(s["release_id"]) or {}, s["extern_id"], kontext)
                produkte.karten_ergaenzen(karten, tcg)
            except Exception as e:  # noqa: BLE001
                log.warning("TCGplayer-Daten zu %s nicht abrufbar: %s", s["release_name"], e)
            kontext.pop("tcg_alle", None)
            _liste_speichern(s["release_id"], spiel, s["extern_id"], karten)
            if tcg is not None:
                produkte.speichern(s["release_id"], spiel, tcg["sealed"])
        except Exception as e:  # noqa: BLE001
            fehler += 1
            log.warning("Kartenliste %s (%s) fehlgeschlagen: %s", s["release_name"], s["extern_id"], e)
            _fehler_vermerken(s["release_id"], spiel, s["extern_id"], str(e))
        status["fertig"] += 1
    kontext.clear()
    # Cardmarket-Zuordnung für die neuen Listen (die Preisdaten selbst kommen aus dem täglichen Abruf)
    try:
        from . import preise
        preise.zuordnen(spiel)
    except Exception:  # noqa: BLE001
        log.exception("Cardmarket-Zuordnung %s fehlgeschlagen", spiel)
    if not _abbruch.is_set():
        _bilder_laden(spiel)
    _aufraeumen(spiel)
    z = zahlen(spiel)
    if _abbruch.is_set():
        meldung = "Abgebrochen – beim nächsten Abruf geht es an derselben Stelle weiter."
        # Nach einem Abbruch nicht automatisch weitermachen, erst wieder nach einem Klick in der Verwaltung
        _stand_setzen(spiel, {**(stand().get(spiel) or {}), "pausiert": True})
    else:
        def n(x):
            return f"{x:,}".replace(",", ".")
        meldung = f"{n(z['sets_mit_liste'])} Sets mit {n(z['karten'])} Karten, {n(z['bilder_lokal'])} Bilder lokal"
        if fehler:
            meldung += f", {fehler} Listen fehlgeschlagen"
        _stand_setzen(spiel, {"zeit": datetime.now(timezone.utc).isoformat(), "meldung": meldung, "abgeschlossen": True})
    log.info("Kartenlisten %s: %s", spiel, meldung)
    status["meldung"] = meldung
    return meldung


def _arbeiten():
    global _arbeiter
    while True:
        with _wl_lock:
            if not _warteschlange:
                status.update(laeuft=False, spiel=None, schritt=None)
                _arbeiter = None
                return
            spiel, alle = _warteschlange.pop(0)
        _abbruch.clear()
        try:
            if ist_aktiv(spiel):
                _abrufen(spiel, alle)
        except Exception as e:  # noqa: BLE001
            log.exception("Kartenlisten %s fehlgeschlagen", spiel)
            status["meldung"] = f"Fehler: {e}"
        if _abbruch.is_set():
            with _wl_lock:
                _warteschlange.clear()


def anstossen(spiel: str, alle: bool = False) -> bool:
    global _arbeiter
    with _wl_lock:
        if status["spiel"] == spiel or any(s == spiel for s, _ in _warteschlange):
            return False
        _warteschlange.append((spiel, alle))
        if _arbeiter is None:
            _arbeiter = threading.Thread(target=_arbeiten, daemon=True, name="karten")
            status["laeuft"] = True
            _arbeiter.start()
    return True


def abbrechen():
    _abbruch.set()


def loeschen(spiel: str):
    """Entfernt alle Kartenlisten und Bilder eines Spiels."""
    if status["spiel"] == spiel:
        raise ValueError("Für dieses Spiel läuft gerade ein Abruf")
    with _wl_lock:
        _warteschlange[:] = [x for x in _warteschlange if x[0] != spiel]
    with db.verbindung() as con:
        for tabelle in ("karten", "kartenlisten", "kartenbilder", "setprodukte"):
            con.execute(f"DELETE FROM {tabelle} WHERE spiel = ?", (spiel,))
    shutil.rmtree(os.path.join(verzeichnis(), spiel), ignore_errors=True)
    _stand_setzen(spiel, None)


# --- Abfragen ---------------------------------------------------------------------------

def zahlen(spiel: str) -> dict:
    with db.verbindung() as con:
        listen = con.execute("SELECT COUNT(*) AS n, COALESCE(SUM(anzahl), 0) AS k FROM kartenlisten "
                             "WHERE spiel = ? AND anzahl > 0", (spiel,)).fetchone()
        preise = con.execute("SELECT COUNT(cm_id) AS e, COUNT(cm_kandidaten) AS m FROM karten WHERE spiel = ?",
                             (spiel,)).fetchone()
        bilder = con.execute("SELECT COUNT(*) AS n, COUNT(datei) AS lokal, COALESCE(SUM(bytes), 0) AS b, "
                             "SUM(CASE WHEN datei IS NULL AND fehler >= 3 THEN 1 ELSE 0 END) AS fehlt "
                             "FROM kartenbilder WHERE spiel = ?", (spiel,)).fetchone()
    return {"sets_mit_liste": listen["n"], "karten": listen["k"], "bilder_gesamt": bilder["n"],
            "bilder_lokal": bilder["lokal"], "bilder_fehlen": bilder["fehlt"] or 0, "bytes": bilder["b"],
            "preise_eindeutig": preise["e"], "preise_mehrdeutig": preise["m"]}


def uebersicht() -> dict:
    aktiv = freigeschaltet()
    st = stand()
    spiele = []
    for s in db.spiele():
        eintrag = {"id": s["id"], "name": s["name"], "aktiv": s["id"] in aktiv, "unterstuetzt": unterstuetzt(s["id"])}
        if eintrag["unterstuetzt"]:
            q = QUELLE_JE_SPIEL[s["id"]]
            eintrag |= {"quelle": QUELLEN_NAMEN[q], **zahlen(s["id"]), "stand": st.get(s["id"]),
                        "sets_verknuepft": len(verknuepfte_sets(s["id"]))}
        spiele.append(eintrag)
    pfad = os.path.dirname(db.DB_PFAD) or "."
    try:
        frei = shutil.disk_usage(pfad).free
    except OSError:
        frei = None
    with _wl_lock:
        wartend = [s for s, _ in _warteschlange]
    return {"spiele": spiele, "status": dict(status), "warteschlange": wartend, "frei_bytes": frei,
            "intervall_stunden": INTERVALL_STUNDEN}


_WELLE = re.compile(r"\s*[\(\[]?\s*[-–:]?\s*\b(welle|wave|teil|part)\s*\d+\s*[\)\]]?", re.I)


def _grundname(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _WELLE.sub("", name or "").lower())


def _wellen(aktiv: set[str], eigene: dict[int, int]) -> dict[int, int]:
    """Termine ohne eigene Liste, die eine weitere Welle eines Sets sind, z. B.
    „30th Celebration Premium (Welle 4)“ oder „30th Celebration ex Tins & Binder Collection (Welle 5)“
    -> Liste von „30th Celebration“. Zugeordnet wird das Set mit Liste, dessen Name am längsten mit dem
    Namen der Welle übereinstimmt. Ergebnis: Termin -> Termin mit Liste."""
    if not aktiv or not eigene:
        return {}
    with db.verbindung() as con:
        rows = con.execute(f"SELECT id, spiel, name, datum FROM releases WHERE spiel IN ({','.join('?' * len(aktiv))})",
                           sorted(aktiv)).fetchall()
    mit_liste: dict[str, list] = {}
    for r in rows:
        if eigene.get(r["id"]):
            mit_liste.setdefault(r["spiel"], []).append((_grundname(r["name"]), r))
    zuordnung = {}
    for r in rows:
        if eigene.get(r["id"]) or not _WELLE.search(r["name"] or ""):
            continue
        grund = _grundname(r["name"])
        passend = [(len(g), -abs((date.fromisoformat(r["datum"]) - date.fromisoformat(x["datum"])).days), x["id"])
                   for g, x in mit_liste.get(r["spiel"], []) if len(g) >= 6 and grund.startswith(g)
                   and x["datum"] <= r["datum"]]
        if passend:
            zuordnung[r["id"]] = max(passend)[2]
    return zuordnung


def anzahlen(eigene_nur: bool = False) -> dict[int, int]:
    """Anzahl Karten je Termin – nur für freigeschaltete Spiele. Weitere Wellen eines Sets
    bekommen die Liste des Sets mit (außer bei eigene_nur)."""
    aktiv = freigeschaltet()
    if not aktiv:
        return {}
    with db.verbindung() as con:
        rows = con.execute(f"SELECT release_id, anzahl FROM kartenlisten WHERE anzahl > 0 AND spiel IN "
                           f"({','.join('?' * len(aktiv))})", sorted(aktiv)).fetchall()
    eigene = {r["release_id"]: r["anzahl"] for r in rows}
    if eigene_nur or not eigene:
        return eigene
    return eigene | {rid: eigene[ziel] for rid, ziel in _wellen(aktiv, eigene).items()}


def _produkte_ausgabe(spiel: str, produkte: list, mit_preisen: bool) -> list[dict]:
    from . import preise
    preisliste = preise.fuer_release(spiel, produkte) if mit_preisen else [None] * len(produkte)
    return [{"name": p["name_de"] or p["name"], "name_en": p["name"] if p["name_de"] else None,
             "bild": f"/karten/bild/{spiel}/{p['datei']}" if p["datei"] else None,
             **({"cardmarket": pp} if pp else {})} for p, pp in zip(produkte, preisliste)]


def produkte_liste(rid: int, mit_preisen: bool = False) -> list[dict] | None:
    """Nur die Produkte eines Sets (für die Detailansicht im Kalender)."""
    r = db.release(rid, mit_spiel=True)
    if not r or not ist_aktiv(r["spiel"]):
        return None
    from . import produkte as produkte_modul
    produkte = produkte_modul.fuer_release(rid)
    if not produkte:
        ziel = _wellen({r["spiel"]}, anzahlen(eigene_nur=True)).get(rid)
        produkte = produkte_modul.fuer_release(ziel) if ziel else []
    return _produkte_ausgabe(r["spiel"], produkte, mit_preisen)


def liste(rid: int, mit_preisen: bool = False) -> dict | None:
    """Kartenliste eines Sets; Cardmarket-Preise und -Links nur mit mit_preisen (angemeldete Verwaltung)."""
    r = db.release(rid, mit_spiel=True)
    if not r or not ist_aktiv(r["spiel"]):
        return None
    with db.verbindung() as con:
        kl = con.execute("SELECT * FROM kartenlisten WHERE release_id = ? AND anzahl > 0", (rid,)).fetchone()
    quelle_rid = rid
    if not kl:
        # Weitere Welle eines Sets: Liste des Sets verwenden
        ziel = _wellen({r["spiel"]}, anzahlen(eigene_nur=True)).get(rid)
        if ziel:
            quelle_rid = ziel
            with db.verbindung() as con:
                kl = con.execute("SELECT * FROM kartenlisten WHERE release_id = ? AND anzahl > 0", (ziel,)).fetchone()
    from . import produkte as produkte_modul
    produkte = produkte_modul.fuer_release(rid) or (produkte_modul.fuer_release(quelle_rid) if quelle_rid != rid else [])
    if not kl and not produkte:
        return None
    with db.verbindung() as con:
        karten = [dict(k) for k in con.execute(
            "SELECT k.nummer, k.name, k.seltenheit, k.art, k.variante, k.cm_id, k.cm_kandidaten, k.cm_geschaetzt, b.datei "
            "FROM karten k "
            "LEFT JOIN kartenbilder b ON b.url = k.bild_url WHERE k.release_id = ? ORDER BY k.pos", (quelle_rid,))]
    q = QUELLE_JE_SPIEL[r["spiel"]]
    from . import preise, setbilder
    preisliste = preise.fuer_release(r["spiel"], karten) if mit_preisen else [None] * len(karten)
    return {
        "release": {**{k: r[k] for k in ("id", "spiel", "spiel_name", "spiel_farbe", "name", "name_de", "code", "typ", "datum",
                                         "genauigkeit", "status_effektiv")}, **setbilder.zuordnung().get(rid, {})},
        "quelle": {"name": QUELLEN_NAMEN[q], "url": QUELLEN_LINKS[q]},
        "abgerufen": kl["abgerufen"] if kl else None,
        "produkte": _produkte_ausgabe(r["spiel"], produkte, mit_preisen),
        "preise_stand": preise.stand_fuer(r["spiel"]) if mit_preisen and preise.aktiv() else None,
        "karten": [{"nummer": k["nummer"], "name": k["name"], "seltenheit": k["seltenheit"], "art": k["art"],
                    "variante": k["variante"],
                    "bild": f"/karten/bild/{r['spiel']}/{k['datei']}" if k["datei"] else None,
                    **({"cardmarket": p} if p else {})} for k, p in zip(karten, preisliste)],
    }


# --- Hintergrund ---------------------------------------------------------------------------

def starte_hintergrund():
    """Hält einmal abgerufene Spiele aktuell: neue Sets, Spoiler und fehlende Bilder."""
    def schleife():
        time.sleep(120)
        while True:
            try:
                if INTERVALL_STUNDEN > 0:
                    st = stand()
                    for spiel in sorted(freigeschaltet()):
                        s = st.get(spiel)
                        if not s or s.get("pausiert"):
                            continue  # noch nie in der Verwaltung abgerufen oder dort abgebrochen
                        if not s.get("abgeschlossen") or not s.get("zeit") or datetime.now(timezone.utc) - \
                                datetime.fromisoformat(s["zeit"]) >= timedelta(hours=INTERVALL_STUNDEN):
                            anstossen(spiel)
            except Exception:  # noqa: BLE001
                log.exception("Geplanter Kartenabruf fehlgeschlagen")
            time.sleep(1800)

    threading.Thread(target=schleife, daemon=True, name="karten-plan").start()
