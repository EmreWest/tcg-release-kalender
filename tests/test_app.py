import json
import xml.dom.minidom

import pytest
from fastapi.testclient import TestClient

from app import checker, db, main

ADMIN = ("admin", "test-passwort")
H = {"X-Admin-Anfrage": "1"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PFAD", str(tmp_path / "test.db"))
    monkeypatch.setattr(main, "ADMIN_PASSWORT", ADMIN[1])
    monkeypatch.setattr(db, "BEOBACHTER", [])
    with TestClient(main.app) as c:
        yield c


def test_startdaten_und_meta(client):
    meta = client.get("/api/meta").json()
    assert len(meta["spiele"]) >= 10
    assert "erschienen" in meta["status"]
    rows = client.get("/api/releases?rueckblick=3650").json()
    assert len(rows) > 20
    assert all("status_effektiv" in r for r in rows)


def test_filter(client):
    rows = client.get("/api/releases?spiele=magic&typ=hauptset&rueckblick=3650").json()
    assert rows and all(r["spiel"] == "magic" and r["typ"] == "hauptset" for r in rows)


def test_ics_feed(client):
    antwort = client.get("/kalender.ics?spiele=pokemon,magic&prerelease=1")
    assert antwort.status_code == 200
    assert antwort.headers["content-type"].startswith("text/calendar")
    text = antwort.text
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
    assert "Prerelease:" in text
    for zeile in text.split("\r\n"):
        assert len(zeile.encode()) <= 75


def test_ics_ohne_ungenaue(client):
    text = client.get("/kalender.ics?ungenau=0").text
    assert "[ca. " not in text


def test_einzelner_termin_ics(client):
    rid = client.get("/api/releases").json()[0]["id"]
    antwort = client.get(f"/release/{rid}.ics")
    assert antwort.status_code == 200
    assert antwort.text.count("BEGIN:VEVENT") >= 1
    assert client.get("/release/999999.ics").status_code == 404


def test_admin_braucht_login_und_header(client):
    assert client.get("/admin").status_code == 401
    assert client.get("/admin", auth=("admin", "falsch")).status_code == 401
    assert client.get("/admin", auth=ADMIN).status_code == 200
    # Schreibende Anfragen ohne Schutz-Header werden abgelehnt
    assert client.delete("/api/admin/releases/1", auth=ADMIN).status_code == 403


def test_verschieben_protokoll_und_feed(client):
    rows = client.get("/api/releases?spiele=pokemon").json()
    r = next(x for x in rows if x["status"] == "bestaetigt" and x["status_effektiv"] != "erschienen")
    neu = "2027-03-05"
    assert client.put(f"/api/admin/releases/{r['id']}", json={"datum": neu}, auth=ADMIN, headers=H).status_code == 200
    nach = client.get(f"/api/releases/{r['id']}").json()
    assert nach["status"] == "verschoben"
    assert nach["vorheriges_datum"] == r["datum"]
    assert nach["sequenz"] == 1

    aenderungen = client.get("/api/aenderungen").json()
    assert aenderungen[0]["art"] == "verschoben"
    assert aenderungen[0]["details"]["datum"] == [r["datum"], neu]

    feed = client.get("/feed.xml")
    assert feed.status_code == 200
    xml.dom.minidom.parseString(feed.content)
    assert "Termin geändert" in feed.text


def test_spiele_verwalten(client):
    assert client.post("/api/admin/spiele", json={"id": "fab", "name": "Flesh and Blood", "farbe": "#123456"},
                       auth=ADMIN, headers=H).status_code == 200
    assert client.post("/api/admin/spiele", json={"id": "fab", "name": "Doppelt", "farbe": "#123456"},
                       auth=ADMIN, headers=H).status_code == 400
    assert client.put("/api/admin/spiele/fab", json={"farbe": "falsch"}, auth=ADMIN, headers=H).status_code == 400
    assert client.put("/api/admin/spiele/fab", json={"farbe": "#abcdef"}, auth=ADMIN, headers=H).status_code == 200
    # Spiele mit Terminen lassen sich nicht löschen
    assert client.delete("/api/admin/spiele/pokemon", auth=ADMIN, headers=H).status_code == 400
    assert client.delete("/api/admin/spiele/fab", auth=ADMIN, headers=H).status_code == 200


def test_import_und_vorschlag(client):
    liste = [
        {"aktion": "neu", "daten": {"spiel": "swu", "name": "Testset", "datum": "2027-05-07", "status": "angekuendigt"},
         "quelle": "https://example.org", "begruendung": "Test"},
        {"aktion": "neu", "daten": {"spiel": "gibtsnicht", "name": "X", "datum": "2027-01-01"}},
    ]
    antwort = client.post("/api/admin/import", json=liste, auth=ADMIN, headers=H).json()
    assert antwort["angelegt"] == 1 and len(antwort["fehler"]) == 1
    vorschlaege = client.get("/api/admin/vorschlaege", auth=ADMIN).json()
    assert len(vorschlaege) == 1
    vid = vorschlaege[0]["id"]
    assert client.post(f"/api/admin/vorschlaege/{vid}/uebernehmen", auth=ADMIN, headers=H).status_code == 200
    namen = [r["name"] for r in client.get("/api/releases?spiele=swu").json()]
    assert "Testset" in namen


def test_checker_json_block():
    text = 'Ergebnis\n```json\n[{"aktion": "neu"}]\n```'
    assert checker._json_block(text) == [{"aktion": "neu"}]


def test_benachrichtigung_beobachter(client, monkeypatch):
    gesehen = []
    monkeypatch.setattr(db, "BEOBACHTER", [gesehen.append])
    rid = client.get("/api/releases").json()[0]["id"]
    client.put(f"/api/admin/releases/{rid}", json={"name": "Neuer Name"}, auth=ADMIN, headers=H)
    assert gesehen and gesehen[0]["art"] == "geaendert"
    assert json.dumps(gesehen[0]["details"])
