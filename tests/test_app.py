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


PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82")


def test_logo_hochladen_und_entfernen(client):
    url = "/api/admin/spiele/pokemon/logo"
    hdr = {**H, "Content-Type": "image/png"}
    # falscher Typ und falscher Inhalt werden abgelehnt
    assert client.put(url, content=b"abc", auth=ADMIN, headers={**H, "Content-Type": "text/plain"}).status_code == 400
    assert client.put(url, content=b"kein png", auth=ADMIN, headers=hdr).status_code == 400
    boese = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert client.put(url, content=boese, auth=ADMIN, headers={**H, "Content-Type": "image/svg+xml"}).status_code == 400
    assert client.put(url, content=PNG, auth=ADMIN, headers={"Content-Type": "image/png"}).status_code == 403

    antwort = client.put(url, content=PNG, auth=ADMIN, headers=hdr)
    assert antwort.status_code == 200
    logo_url = antwort.json()["logo"]
    meta = {s["id"]: s for s in client.get("/api/meta").json()["spiele"]}
    assert meta["pokemon"]["logo"] == logo_url
    bild = client.get(logo_url)
    assert bild.status_code == 200 and bild.content == PNG
    assert bild.headers["content-type"] == "image/png"
    assert "default-src 'none'" in bild.headers["content-security-policy"]
    assert client.get("/logos/../test.db").status_code == 404

    assert client.delete(url, auth=ADMIN, headers=H).status_code == 200
    assert client.get(logo_url).status_code == 404
    assert client.get("/api/meta").json()["spiele"][0]["logo"] is None


def test_darstellung_titel_logo_favicon(client):
    seite = client.get("/").text
    assert "<title>TCG-Release-Kalender</title>" in seite and "{{" not in seite
    assert client.get("/favicon").headers["content-type"].startswith("image/svg")

    antwort = client.put("/api/admin/einstellungen", json={"titel": "Mein <Laden>", "untertitel": "", "titel_zeigen": False},
                         auth=ADMIN, headers=H)
    assert antwort.status_code == 200
    assert client.put("/api/admin/einstellungen", json={"titel": " "}, auth=ADMIN, headers=H).status_code == 400

    url = client.put("/api/admin/darstellung/logo", content=PNG, auth=ADMIN,
                     headers={**H, "Content-Type": "image/png"}).json()["url"]
    seite = client.get("/").text
    assert "<title>Mein &lt;Laden&gt;</title>" in seite
    assert f'src="{url}"' in seite and "nur-screenreader" in seite
    fav = client.get("/favicon")
    assert fav.headers["content-type"] == "image/png" and fav.content == PNG
    assert "Mein <Laden>" in client.get("/kalender.ics").text.replace("\\,", ",")
    assert client.put("/api/admin/darstellung/unbekannt", content=PNG, auth=ADMIN,
                      headers={**H, "Content-Type": "image/png"}).status_code == 404
    assert client.delete("/api/admin/darstellung/logo", auth=ADMIN, headers=H).status_code == 200
    assert client.get(url).status_code == 404


def test_auto_uebernahme_offizielle_quelle(client):
    from app import einstellungen
    client.put("/api/admin/einstellungen", json={"auto_uebernahme": "offiziell",
               "offizielle_domains": "https://www.pokemon.com/\nexample.org"}, auth=ADMIN, headers=H)
    assert einstellungen.lesen()["offizielle_domains"] == ["pokemon.com", "example.org"]
    liste = [
        {"aktion": "neu", "daten": {"spiel": "pokemon", "name": "Offiziell", "datum": "2027-03-26", "status": "bestaetigt"},
         "quelle": "https://press.pokemon.com/de/x"},
        {"aktion": "neu", "daten": {"spiel": "pokemon", "name": "Fanseite", "datum": "2027-03-26", "status": "bestaetigt"},
         "quelle": "https://fanseite.de/x"},
        {"aktion": "neu", "daten": {"spiel": "pokemon", "name": "Nur Monat", "datum": "2027-05-01", "genauigkeit": "monat",
                                    "status": "angekuendigt"}, "quelle": "https://www.pokemon.com/x"},
    ]
    n, fehler = checker.vorschlaege_einspielen(liste, "claude:test")
    assert n == 3 and not fehler
    namen = {r["name"] for r in client.get("/api/releases?spiele=pokemon").json()}
    assert "Offiziell" in namen and "Fanseite" not in namen and "Nur Monat" not in namen
    offen = {v["daten"]["name"] for v in client.get("/api/admin/vorschlaege", auth=ADMIN).json()}
    assert offen == {"Fanseite", "Nur Monat"}
    # Import von Hand wird nie automatisch übernommen
    checker.vorschlaege_einspielen([{"aktion": "neu", "daten": {"spiel": "pokemon", "name": "Import", "datum": "2027-03-26",
                                     "status": "bestaetigt"}, "quelle": "https://www.pokemon.com/y"}], "import")
    assert "Import" not in {r["name"] for r in client.get("/api/releases?spiele=pokemon").json()}


def test_datenquellen_abgleich(client, monkeypatch):
    from app import quellen
    stand = {"datum": "2026-12-03"}

    def falsche_quelle(ab):
        return [
            {"extern_id": "ygoprodeck:GLVI", "spiel": "yugioh", "name": "Glorious Victors", "code": "GLVI",
             "typ": "hauptset", "datum": stand["datum"], "quelle": "https://ygoprodeck.com/", "notiz": "x"},
            {"extern_id": "ygoprodeck:NEU1", "spiel": "yugioh", "name": "Ganz Neues Set", "code": "NEU1",
             "typ": "spezialset", "datum": "2027-02-11", "quelle": "https://ygoprodeck.com/", "notiz": "x"},
        ]
    monkeypatch.setattr(quellen, "QUELLEN", {"ygoprodeck": falsche_quelle})
    monkeypatch.setattr(quellen, "AKTIV", {"ygoprodeck"})

    assert "1 neu, 1 aktualisiert" in quellen.abgleichen()
    rows = {r["name"]: r for r in client.get("/api/releases?spiele=yugioh&rueckblick=3650").json()}
    gv = rows["Glorious Victors"]
    # Erstes Angleichen ist eine Korrektur, keine Verschiebung
    assert gv["datum"] == "2026-12-03" and gv["status"] == "bestaetigt" and gv["code"] == "GLVI"
    assert rows["Ganz Neues Set"]["status"] == "bestaetigt"

    # Manuelle Korrektur bleibt erhalten, solange sich die Quelle nicht ändert
    client.put(f"/api/admin/releases/{gv['id']}", json={"datum": "2026-12-04", "status": "bestaetigt"}, auth=ADMIN, headers=H)
    assert "0 neu, 0 aktualisiert" in quellen.abgleichen()
    assert client.get(f"/api/releases/{gv['id']}").json()["datum"] == "2026-12-04"

    # Ändert sich der Termin in der Quelle, wird er übernommen und gilt als verschoben
    stand["datum"] = "2026-12-17"
    quellen.abgleichen()
    nach = client.get(f"/api/releases/{gv['id']}").json()
    assert nach["datum"] == "2026-12-17" and nach["status"] == "verschoben"

    # Gelöschte Termine werden nicht wieder angelegt
    neu_id = rows["Ganz Neues Set"]["id"]
    client.delete(f"/api/admin/releases/{neu_id}", auth=ADMIN, headers=H)
    quellen.abgleichen()
    assert "Ganz Neues Set" not in {r["name"] for r in client.get("/api/releases?spiele=yugioh").json()}


def test_ygo_typ_erkennung():
    from app import quellen
    assert quellen._ygo_typ("Structure Deck: Blue-Eyes", 45) == "deck"
    assert quellen._ygo_typ("Beyond the Brave", 100) == "hauptset"
    assert quellen._ygo_typ("Magnificent Maestros", 24) == "spezialset"
    assert quellen._ygo_typ("2026 Mega-Tin", 60) == "sonstiges"
