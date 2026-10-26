"""SQLite-Datenhaltung für den TCG-Release-Kalender."""
import json
import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone

log = logging.getLogger("tcg-kalender.db")

DB_PFAD = os.environ.get("DB_PFAD", "/data/kalender.db")
SEED_PFAD = os.path.join(os.path.dirname(__file__), "seed.json")

STATUS = {
    "geruecht": "Gerücht",
    "angekuendigt": "Angekündigt",
    "bestaetigt": "Bestätigt",
    "verschoben": "Verschoben",
    "abgesagt": "Abgesagt",
}
# "erschienen" wird nicht gespeichert, sondern aus Datum + Status berechnet
STATUS_FILTER = {**STATUS, "erschienen": "Erschienen"}

TYPEN = {
    "hauptset": "Hauptset",
    "spezialset": "Spezial-/Sonderset",
    "deck": "Starter / Deck",
    "sonstiges": "Sonstiges",
}

GENAUIGKEIT = {"tag": "Tag", "monat": "Monat", "quartal": "Quartal"}

FELDER = ["spiel", "name", "code", "typ", "datum", "genauigkeit", "prerelease",
          "status", "notiz", "quelle"]

# Felder, deren Änderung im Änderungsprotokoll und in Benachrichtigungen erscheint
RELEVANTE_FELDER = ["name", "code", "typ", "datum", "genauigkeit", "prerelease", "status"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS spiele (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    farbe TEXT NOT NULL DEFAULT '#6b7280',
    reihenfolge INTEGER NOT NULL DEFAULT 100,
    logo TEXT
);
CREATE TABLE IF NOT EXISTS releases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    spiel TEXT NOT NULL REFERENCES spiele(id),
    name TEXT NOT NULL,
    code TEXT,
    typ TEXT NOT NULL DEFAULT 'hauptset',
    datum TEXT NOT NULL,
    genauigkeit TEXT NOT NULL DEFAULT 'tag',
    prerelease TEXT,
    status TEXT NOT NULL DEFAULT 'angekuendigt',
    notiz TEXT,
    quelle TEXT,
    vorheriges_datum TEXT,
    sequenz INTEGER NOT NULL DEFAULT 0,
    erstellt TEXT NOT NULL,
    geaendert TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vorschlaege (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    aktion TEXT NOT NULL,
    release_id INTEGER,
    daten TEXT NOT NULL,
    begruendung TEXT,
    quelle TEXT,
    herkunft TEXT,
    status TEXT NOT NULL DEFAULT 'offen',
    erstellt TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS aenderungen (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zeit TEXT NOT NULL,
    art TEXT NOT NULL,
    release_id INTEGER,
    spiel TEXT NOT NULL,
    name TEXT NOT NULL,
    datum TEXT,
    details TEXT
);
CREATE INDEX IF NOT EXISTS idx_aenderungen_zeit ON aenderungen(zeit);
CREATE TABLE IF NOT EXISTS quellen_stand (
    extern_id TEXT PRIMARY KEY,
    release_id INTEGER,
    datum TEXT,
    name TEXT,
    gesehen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    schluessel TEXT PRIMARY KEY,
    wert TEXT
);
"""

# Callbacks, die bei jeder protokollierten Änderung aufgerufen werden (z. B. Webhooks)
BEOBACHTER: list = []


def jetzt() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def verbindung():
    os.makedirs(os.path.dirname(DB_PFAD) or ".", exist_ok=True)
    con = sqlite3.connect(DB_PFAD)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init():
    with verbindung() as con:
        con.executescript(SCHEMA)
        # Migration: Logo-Spalte für ältere Datenbanken nachrüsten
        spalten = {r["name"] for r in con.execute("PRAGMA table_info(spiele)")}
        if "logo" not in spalten:
            con.execute("ALTER TABLE spiele ADD COLUMN logo TEXT")
        leer = con.execute("SELECT COUNT(*) FROM spiele").fetchone()[0] == 0
    if not os.path.exists(SEED_PFAD):
        return
    with open(SEED_PFAD, encoding="utf-8") as f:
        seed = json.load(f)
    seed_version = int(seed.get("version", 1))
    if leer:
        _seed_einspielen(seed["spiele"], seed["releases"])
        meta_set("seed_version", str(seed_version))
        log.info("Startdaten eingespielt: %d Spiele, %d Termine", len(seed["spiele"]), len(seed["releases"]))
        return
    # Bestehende Installation: Spiele, die mit einer neueren Version dazugekommen sind, einmalig ergänzen.
    # Was danach in der Verwaltung gelöscht wird, kommt nicht wieder.
    bisher = int(meta_get("seed_version") or 1)
    if bisher >= seed_version:
        return
    vorhanden = {s["id"] for s in spiele()}
    neue = [s for s in seed["spiele"] if int(s.get("seit", 1)) > bisher and s["id"] not in vorhanden]
    ids = {s["id"] for s in neue}
    termine = [r for r in seed["releases"] if r["spiel"] in ids]
    if neue:
        _seed_einspielen(neue, termine, protokoll=True)
        log.info("Neue Spiele ergänzt: %s (%d Termine)", ", ".join(s["name"] for s in neue), len(termine))
    meta_set("seed_version", str(seed_version))


def _seed_einspielen(spiele_liste: list[dict], termine: list[dict], protokoll: bool = False):
    with verbindung() as con:
        start = con.execute("SELECT COALESCE(MAX(reihenfolge), -1) + 1 FROM spiele").fetchone()[0]
        for i, s in enumerate(spiele_liste):
            con.execute(
                "INSERT INTO spiele (id, name, farbe, reihenfolge) VALUES (?,?,?,?)",
                (s["id"], s["name"], s.get("farbe", "#6b7280"), start + i),
            )
    heute = date.today().isoformat()
    for r in termine:
        # Ins Änderungsprotokoll nur, was noch bevorsteht
        anlegen(r, protokoll=protokoll and r["datum"] >= heute)


def ist_erschienen(r: dict) -> bool:
    if r["status"] in ("geruecht", "abgesagt"):
        return False
    if r["genauigkeit"] != "tag":
        return False
    return r["datum"] <= date.today().isoformat()


def effektiver_status(r: dict) -> str:
    return "erschienen" if ist_erschienen(r) else r["status"]


def _bereinigen(daten: dict) -> dict:
    d = {k: daten.get(k) for k in FELDER if k in daten}
    for k in ("code", "prerelease", "notiz", "quelle"):
        if k in d and (d[k] is None or str(d[k]).strip() == ""):
            d[k] = None
    for k in ("name", "code", "notiz", "quelle"):
        if isinstance(d.get(k), str):
            d[k] = d[k].strip()
    if "status" in d and d["status"] not in STATUS:
        raise ValueError(f"Unbekannter Status: {d['status']}")
    if "typ" in d and d["typ"] not in TYPEN:
        raise ValueError(f"Unbekannter Typ: {d['typ']}")
    if "genauigkeit" in d and d["genauigkeit"] not in GENAUIGKEIT:
        raise ValueError(f"Unbekannte Genauigkeit: {d['genauigkeit']}")
    for k in ("datum", "prerelease"):
        if d.get(k):
            date.fromisoformat(d[k])
    if d.get("quelle") and not d["quelle"].startswith(("http://", "https://")):
        raise ValueError("Quelle muss mit http:// oder https:// beginnen")
    return d


# --- Spiele ------------------------------------------------------------------

def spiele() -> list[dict]:
    with verbindung() as con:
        return [dict(r) for r in con.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM releases r WHERE r.spiel = s.id) AS anzahl "
            "FROM spiele s ORDER BY reihenfolge, name")]


def _farbe_pruefen(farbe: str) -> str:
    farbe = (farbe or "").strip()
    if len(farbe) != 7 or not farbe.startswith("#"):
        raise ValueError("Farbe im Format #rrggbb angeben")
    int(farbe[1:], 16)
    return farbe.lower()


def spiel_anlegen(id_: str, name: str, farbe: str):
    id_ = (id_ or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]{1,40}", id_):
        raise ValueError("ID darf nur Buchstaben, Ziffern, - und _ enthalten")
    if not (name or "").strip():
        raise ValueError("Name fehlt")
    with verbindung() as con:
        if con.execute("SELECT 1 FROM spiele WHERE id = ?", (id_,)).fetchone():
            raise ValueError("Diese ID gibt es schon")
        con.execute(
            "INSERT INTO spiele (id, name, farbe, reihenfolge) VALUES (?,?,?,"
            "(SELECT COALESCE(MAX(reihenfolge),0)+1 FROM spiele))",
            (id_, name.strip(), _farbe_pruefen(farbe)),
        )


def spiel_aendern(id_: str, daten: dict):
    felder = {}
    if "name" in daten:
        if not str(daten["name"]).strip():
            raise ValueError("Name fehlt")
        felder["name"] = str(daten["name"]).strip()
    if "farbe" in daten:
        felder["farbe"] = _farbe_pruefen(daten["farbe"])
    if "reihenfolge" in daten:
        felder["reihenfolge"] = int(daten["reihenfolge"])
    if not felder:
        return
    with verbindung() as con:
        cur = con.execute(f"UPDATE spiele SET {', '.join(f'{k} = ?' for k in felder)} WHERE id = ?",
                          list(felder.values()) + [id_])
        if cur.rowcount == 0:
            raise KeyError(id_)


def spiel_loeschen(id_: str) -> str | None:
    """Löscht ein Spiel ohne Termine und gibt den Dateinamen seines Logos zurück."""
    with verbindung() as con:
        if con.execute("SELECT COUNT(*) FROM releases WHERE spiel = ?", (id_,)).fetchone()[0]:
            raise ValueError("Spiel hat noch Termine – erst diese löschen")
        row = con.execute("SELECT logo FROM spiele WHERE id = ?", (id_,)).fetchone()
        con.execute("DELETE FROM spiele WHERE id = ?", (id_,))
    return row["logo"] if row else None


def spiel_logo_setzen(id_: str, datei: str | None) -> str | None:
    """Setzt (oder entfernt mit None) das Logo eines Spiels und gibt das bisherige zurück."""
    with verbindung() as con:
        row = con.execute("SELECT logo FROM spiele WHERE id = ?", (id_,)).fetchone()
        if not row:
            raise KeyError(id_)
        con.execute("UPDATE spiele SET logo = ? WHERE id = ?", (datei, id_))
    return row["logo"]


# --- Termine -----------------------------------------------------------------

_RELEASE_SQL = ("SELECT r.*, s.name AS spiel_name, s.farbe AS spiel_farbe, s.logo AS spiel_logo "
                "FROM releases r JOIN spiele s ON s.id = r.spiel")


def releases(spiele_filter=None, status_filter=None, typen_filter=None,
             von: str | None = None, mit_ungenauen=True) -> list[dict]:
    sql = _RELEASE_SQL + " WHERE 1=1"
    args: list = []
    if spiele_filter:
        sql += f" AND r.spiel IN ({','.join('?' * len(spiele_filter))})"
        args += spiele_filter
    if typen_filter:
        sql += f" AND r.typ IN ({','.join('?' * len(typen_filter))})"
        args += typen_filter
    if von:
        # Quartals-/Monatsangaben, deren Zeitraum noch läuft, nicht abschneiden
        sql += " AND (r.datum >= ? OR (r.genauigkeit != 'tag' AND r.datum >= date(?, '-3 months')))"
        args += [von, von]
    if not mit_ungenauen:
        sql += " AND r.genauigkeit = 'tag'"
    sql += " ORDER BY r.datum, r.genauigkeit = 'tag' DESC, s.reihenfolge, r.name"
    with verbindung() as con:
        rows = [dict(r) for r in con.execute(sql, args)]
    for r in rows:
        r["status_effektiv"] = effektiver_status(r)
    if status_filter:
        rows = [r for r in rows if r["status_effektiv"] in status_filter]
    return rows


def release(rid: int, mit_spiel=False) -> dict | None:
    with verbindung() as con:
        if mit_spiel:
            r = con.execute(_RELEASE_SQL + " WHERE r.id = ?", (rid,)).fetchone()
        else:
            r = con.execute("SELECT * FROM releases WHERE id = ?", (rid,)).fetchone()
    if not r:
        return None
    r = dict(r)
    if mit_spiel:
        r["status_effektiv"] = effektiver_status(r)
    return r


def _protokoll(art: str, r: dict, details: dict | None = None):
    with verbindung() as con:
        cur = con.execute(
            "INSERT INTO aenderungen (zeit, art, release_id, spiel, name, datum, details) VALUES (?,?,?,?,?,?,?)",
            (jetzt(), art, r.get("id"), r["spiel"], r["name"], r.get("datum"),
             json.dumps(details, ensure_ascii=False) if details else None),
        )
        eintrag_id = cur.lastrowid
    eintrag = aenderung(eintrag_id)
    for b in BEOBACHTER:
        try:
            b(eintrag)
        except Exception:  # noqa: BLE001
            log.exception("Beobachter fehlgeschlagen")


def anlegen(daten: dict, protokoll=True) -> int:
    d = _bereinigen(daten)
    for pflicht in ("spiel", "name", "datum"):
        if not d.get(pflicht):
            raise ValueError(f"Feld fehlt: {pflicht}")
    d.setdefault("typ", "hauptset")
    d.setdefault("genauigkeit", "tag")
    d.setdefault("status", "angekuendigt")
    ts = jetzt()
    spalten = list(d.keys()) + ["erstellt", "geaendert"]
    with verbindung() as con:
        if not con.execute("SELECT 1 FROM spiele WHERE id = ?", (d["spiel"],)).fetchone():
            raise ValueError(f"Unbekanntes Spiel: {d['spiel']}")
        cur = con.execute(
            f"INSERT INTO releases ({','.join(spalten)}) VALUES ({','.join('?' * len(spalten))})",
            list(d.values()) + [ts, ts],
        )
        rid = cur.lastrowid
    if protokoll:
        _protokoll("neu", {**d, "id": rid})
    return rid


def aendern(rid: int, daten: dict, korrektur: bool = False):
    """Ändert einen Termin. Mit korrektur=True gilt eine Datumsänderung nicht als Verschiebung."""
    alt = release(rid)
    if not alt:
        raise KeyError(rid)
    d = _bereinigen(daten)
    d = {k: v for k, v in d.items() if v != alt.get(k)}
    if not d:
        return
    if not korrektur and "datum" in d and alt["datum"] and alt["genauigkeit"] == "tag" \
            and d.get("genauigkeit", "tag") == "tag":
        d["vorheriges_datum"] = alt["datum"]
        # Ein bestätigter Termin, der sich ändert, gilt als verschoben – außer der Status wird explizit gesetzt
        if "status" not in d and alt["status"] == "bestaetigt":
            d["status"] = "verschoben"
    relevante = {k: [alt.get(k), d[k]] for k in RELEVANTE_FELDER if k in d}
    d["sequenz"] = alt["sequenz"] + 1
    d["geaendert"] = jetzt()
    with verbindung() as con:
        con.execute(
            f"UPDATE releases SET {', '.join(f'{k} = ?' for k in d)} WHERE id = ?",
            list(d.values()) + [rid],
        )
    if relevante:
        neu = {**alt, **d}
        _protokoll("verschoben" if "datum" in relevante and not korrektur else "geaendert", neu, relevante)


def loeschen(rid: int):
    alt = release(rid)
    if not alt:
        return
    with verbindung() as con:
        con.execute("DELETE FROM releases WHERE id = ?", (rid,))
    _protokoll("geloescht", alt)


# --- Änderungsprotokoll ------------------------------------------------------------

def _aenderung_dict(row) -> dict:
    a = dict(row)
    a["details"] = json.loads(a["details"]) if a["details"] else None
    return a


def aenderung(aid: int) -> dict:
    with verbindung() as con:
        row = con.execute(
            "SELECT a.*, s.name AS spiel_name, s.farbe AS spiel_farbe, s.logo AS spiel_logo FROM aenderungen a "
            "LEFT JOIN spiele s ON s.id = a.spiel WHERE a.id = ?", (aid,)).fetchone()
    return _aenderung_dict(row)


def aenderungen(limit=50, spiele_filter=None) -> list[dict]:
    sql = ("SELECT a.*, s.name AS spiel_name, s.farbe AS spiel_farbe, s.logo AS spiel_logo FROM aenderungen a "
           "LEFT JOIN spiele s ON s.id = a.spiel")
    args: list = []
    if spiele_filter:
        sql += f" WHERE a.spiel IN ({','.join('?' * len(spiele_filter))})"
        args += spiele_filter
    sql += " ORDER BY a.zeit DESC, a.id DESC LIMIT ?"
    args.append(limit)
    with verbindung() as con:
        return [_aenderung_dict(r) for r in con.execute(sql, args)]


# --- Vorschläge -------------------------------------------------------------

def vorschlag_anlegen(aktion: str, daten: dict, release_id=None, begruendung=None,
                      quelle=None, herkunft="import") -> int:
    if aktion not in ("neu", "aenderung"):
        raise ValueError("aktion muss 'neu' oder 'aenderung' sein")
    with verbindung() as con:
        # Doppelte offene Vorschläge vermeiden
        for v in con.execute("SELECT daten, release_id, aktion FROM vorschlaege WHERE status = 'offen'"):
            if v["aktion"] == aktion and v["release_id"] == release_id and json.loads(v["daten"]) == daten:
                return 0
        cur = con.execute(
            "INSERT INTO vorschlaege (aktion, release_id, daten, begruendung, quelle, herkunft, erstellt) "
            "VALUES (?,?,?,?,?,?,?)",
            (aktion, release_id, json.dumps(daten, ensure_ascii=False), begruendung, quelle, herkunft, jetzt()),
        )
        return cur.lastrowid


def vorschlaege(status="offen") -> list[dict]:
    with verbindung() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM vorschlaege WHERE status = ? ORDER BY erstellt DESC, id DESC", (status,))]
    for r in rows:
        r["daten"] = json.loads(r["daten"])
        r["release"] = release(r["release_id"]) if r["release_id"] else None
    return rows


def vorschlag_entscheiden(vid: int, uebernehmen: bool, daten_override: dict | None = None):
    with verbindung() as con:
        v = con.execute("SELECT * FROM vorschlaege WHERE id = ?", (vid,)).fetchone()
    if not v or v["status"] != "offen":
        raise KeyError(vid)
    if uebernehmen:
        daten = daten_override or json.loads(v["daten"])
        if not daten.get("quelle") and v["quelle"]:
            daten["quelle"] = v["quelle"]
        if v["aktion"] == "neu":
            anlegen(daten)
        else:
            if not release(v["release_id"]):
                raise ValueError("Der Termin zu diesem Vorschlag existiert nicht mehr")
            aendern(v["release_id"], daten)
    with verbindung() as con:
        con.execute("UPDATE vorschlaege SET status = ? WHERE id = ?",
                    ("uebernommen" if uebernehmen else "abgelehnt", vid))


def meta_get(k: str) -> str | None:
    with verbindung() as con:
        r = con.execute("SELECT wert FROM meta WHERE schluessel = ?", (k,)).fetchone()
        return r["wert"] if r else None


def meta_set(k: str, v: str):
    with verbindung() as con:
        con.execute("INSERT INTO meta (schluessel, wert) VALUES (?,?) "
                    "ON CONFLICT(schluessel) DO UPDATE SET wert = excluded.wert", (k, v))
