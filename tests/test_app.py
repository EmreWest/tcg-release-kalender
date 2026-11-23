import json
import os
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
    assert client.post("/api/admin/spiele", json={"id": "testspiel", "name": "Testspiel", "farbe": "#123456"},
                       auth=ADMIN, headers=H).status_code == 200
    assert client.post("/api/admin/spiele", json={"id": "testspiel", "name": "Doppelt", "farbe": "#123456"},
                       auth=ADMIN, headers=H).status_code == 400
    assert client.put("/api/admin/spiele/testspiel", json={"farbe": "falsch"}, auth=ADMIN, headers=H).status_code == 400
    assert client.put("/api/admin/spiele/testspiel", json={"farbe": "#abcdef"}, auth=ADMIN, headers=H).status_code == 200
    # Spiele mit Terminen lassen sich nicht löschen
    assert client.delete("/api/admin/spiele/pokemon", auth=ADMIN, headers=H).status_code == 400
    assert client.delete("/api/admin/spiele/testspiel", auth=ADMIN, headers=H).status_code == 200


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
    monkeypatch.setattr(quellen, "QUELLEN", {"ygoprodeck": {"funktion": falsche_quelle, "text": "YGOPRODeck (Yu-Gi-Oh!)",
                                                             "spiel": "yugioh"}})
    monkeypatch.setattr(quellen, "aktive", lambda: {"ygoprodeck"})

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


def test_rechtliches_und_manifest(client):
    assert client.get("/impressum").status_code == 404
    seite = client.get("/").text
    assert 'href="/impressum"' not in seite and "data-speicher-einstellungen" in seite

    text = "## Angaben\nMuster <b>GbR</b>\nE-Mail: info@example.org\n\n- Punkt https://example.org/x"
    assert client.put("/api/admin/einstellungen", json={"impressum": text, "datenschutz": "Text"},
                      auth=ADMIN, headers=H).status_code == 200
    imp = client.get("/impressum")
    assert imp.status_code == 200
    assert "<h2>Angaben</h2>" in imp.text and "&lt;b&gt;GbR&lt;/b&gt;" in imp.text
    assert 'href="mailto:info@example.org"' in imp.text and 'href="https://example.org/x"' in imp.text
    seite = client.get("/").text
    assert 'href="/impressum"' in seite and 'data-datenschutz="/datenschutz"' in seite

    m = client.get("/manifest.webmanifest")
    assert m.headers["content-type"].startswith("application/manifest+json")
    assert m.json()["display"] == "standalone" and m.json()["icons"]

    e = client.get("/api/admin/einstellungen", auth=ADMIN).json()
    assert "§ 5 DDG" in e["vorlage_impressum"] and "TDDDG" in e["vorlage_datenschutz"]


def test_formatieren_ohne_html():
    from app import rechtliches
    html_text = rechtliches.formatieren('<script>alert(1)</script>\n## <img src=x onerror=1>\njavascript:alert(1)')
    assert "<script>" not in html_text and "<img" not in html_text
    assert 'href="javascript' not in html_text


def test_neue_spiele_fuer_bestehende_installation(client):
    ids = {s["id"] for s in client.get("/api/meta").json()["spiele"]}
    assert {"fab", "unionarena", "hololive", "cyberpunk"} <= ids
    # Zustand einer Installation von Version 1 nachbauen
    with db.verbindung() as con:
        con.execute("DELETE FROM releases WHERE spiel IN ('fab','unionarena','weiss','vanguard','shadowverse',"
                    "'hololive','fftcg','sorcery','grandarchive','cyberpunk')")
        con.execute("DELETE FROM spiele WHERE id IN ('fab','unionarena','weiss','vanguard','shadowverse',"
                    "'hololive','fftcg','sorcery','grandarchive','cyberpunk')")
    db.meta_set("seed_version", "1")
    db.init()
    ids = {s["id"] for s in db.spiele()}
    assert "fab" in ids and "cyberpunk" in ids
    assert any(r["name"] == "Outbreak" for r in db.releases(spiele_filter=["fab"]))
    # Danach gelöschte Spiele kommen nicht wieder
    with db.verbindung() as con:
        con.execute("DELETE FROM releases WHERE spiel = 'weiss'")
        con.execute("DELETE FROM spiele WHERE id = 'weiss'")
    db.init()
    assert "weiss" not in {s["id"] for s in db.spiele()}


def test_einstellungen_speichern_nur_abweichungen(client):
    import json as _json
    from app import einstellungen
    client.put("/api/admin/einstellungen", json={"titel": "Eigener Titel"}, auth=ADMIN, headers=H)
    gespeichert = _json.loads(db.meta_get("einstellungen"))
    assert gespeichert == {"titel": "Eigener Titel"}
    # Eine früher eingefrorene alte Domainliste wird durch die aktuelle ersetzt
    db.meta_set("einstellungen", _json.dumps({"offizielle_domains": einstellungen._DOMAINS_V1}))
    assert "fabtcg.com" in einstellungen.lesen()["offizielle_domains"]


def test_quellen_umschalten(client):
    from app import quellen
    client.put("/api/admin/einstellungen", json={"quellen_aktiv": ["swudb", "unbekannt"]}, auth=ADMIN, headers=H)
    assert quellen.aktive() == {"swudb"}
    assert quellen.spiele_mit_quelle() == {"swu"}
    assert client.put("/api/admin/einstellungen", json={"quellen_aktiv": "swudb"}, auth=ADMIN, headers=H).status_code == 400


def test_quellen_parser(monkeypatch):
    from app import quellen
    assert quellen._us_datum("11/20/26") == "2026-11-20" and quellen._us_datum("2/30/26") is None
    monkeypatch.setattr(quellen, "_laden", lambda url: [
        {"fullName": "Icons 2027", "isBaseSet": True, "releaseDate": "11/20/26", "setId": "IC27"},
        {"fullName": "Homeworlds - OP Promo", "setId": "HMWP", "parentSetId": "HMW", "releaseDate": "11/20/26"},
        {"fullName": "Costco Exclusive", "setId": "CST", "releaseDate": "12/15/26"},
        {"fullName": "Twin Suns 2027", "isBaseSet": True, "releaseDate": "5/7/27", "setId": "TS27"},
    ])
    sets = {s["code"]: s for s in quellen.swudb("2026-10-01")}
    assert set(sets) == {"IC27", "TS27"} and sets["IC27"]["typ"] == "spezialset" and sets["TS27"]["typ"] == "deck"
    monkeypatch.setattr(quellen, "_laden", lambda url: [
        {"id": "OBK", "name": "Outbreak", "printings": [{"initial_release_date": "2027-02-12T00:00:00.000Z",
                                                         "product_page": "https://fabtcg.com/products/booster-set/obk/"}]},
        {"id": "AMO", "name": "Armory Deck - Dr. Mortimer", "printings": [{"initial_release_date": "2026-11-14T00:00:00.000Z"}]},
        {"id": "XXX", "name": "OP Event Tokens", "printings": [{"initial_release_date": "2026-12-01T00:00:00.000Z"}]},
        {"id": "WTR", "name": "Welcome to Rathe", "printings": [{"initial_release_date": "2019-10-11T00:00:00.000Z"}]},
    ])
    sets = {s["code"]: s for s in quellen.fabcube("2026-10-01")}
    assert set(sets) == {"OBK", "AMO"} and sets["AMO"]["typ"] == "deck" and sets["OBK"]["typ"] == "hauptset"


def test_erschienen_bei_monat_und_quartal():
    from datetime import date as _d, timedelta as _td
    vor_einem_jahr = (_d.today() - _td(days=400)).replace(day=1).isoformat()
    assert db.ist_erschienen({"status": "bestaetigt", "genauigkeit": "monat", "datum": vor_einem_jahr})
    diesen_monat = _d.today().replace(day=1).isoformat()
    assert not db.ist_erschienen({"status": "angekuendigt", "genauigkeit": "monat", "datum": diesen_monat})
    assert db.zeitraum_ende("2026-11-01", "quartal") == "2026-12-31"
    assert db.zeitraum_ende("2026-02-01", "monat") == "2026-02-28"


def _falsche_quelle(sets):
    return {"falsch": {"funktion": lambda ab, alle=False: [s for s in sets if s["datum"] >= ab],
                       "text": "Testquelle", "spiele": ["magic"]}}


def test_historie_import(client, monkeypatch):
    from app import quellen
    gesehen = []
    monkeypatch.setattr(db, "BEOBACHTER", [gesehen.append])
    vorhanden = next(r for r in client.get("/api/releases?spiele=magic").json() if r["name"] == "Star Trek")
    sets = [
        {"extern_id": "x:1", "spiel": "magic", "name": "Altes Set", "code": "ALT", "typ": "hauptset",
         "datum": "2001-05-04", "quelle": "https://example.org", "notiz": "n"},
        {"extern_id": "x:2", "spiel": "magic", "name": "Star Trek", "code": "TRK", "typ": "hauptset",
         "datum": "2026-11-20", "quelle": "https://example.org", "notiz": "n"},
        {"extern_id": "x:3", "spiel": "magic", "name": "Zukunft", "code": "ZUK", "typ": "hauptset",
         "datum": "2030-01-04", "quelle": "https://example.org", "notiz": "n"},
    ]
    monkeypatch.setattr(quellen, "QUELLEN", _falsche_quelle(sets))
    vorher = len(db.aenderungen(500))
    assert "2 Sets übernommen" in quellen.historie_importieren()
    # Bestehender Termin nur verknüpft, nicht verschoben
    assert client.get(f"/api/releases/{vorhanden['id']}").json()["datum"] == vorhanden["datum"]
    archiv = client.get("/api/archiv?spiele=magic").json()["releases"]
    assert any(r["name"] == "Altes Set" for r in archiv)
    assert not any(r["name"] == "Zukunft" for r in archiv)
    # Nur das kommende Set steht im Änderungsprotokoll, Benachrichtigungen gab es keine
    neu = db.aenderungen(500)[: len(db.aenderungen(500)) - vorher]
    assert [a["name"] for a in neu] == ["Zukunft"]
    assert gesehen == []
    # Einmalig: zweiter Lauf tut nichts, erzwungener Lauf legt nichts doppelt an
    assert "bereits vollständig" in quellen.historie_importieren()
    quellen.historie_importieren(erzwingen=True)
    assert sum(1 for r in db.releases(spiele_filter=["magic"], von="1900-01-01") if r["name"] == "Altes Set") == 1
    assert quellen.historie_stand()["falsch"]["neu"] == 2


def test_tcgcsv_parser(monkeypatch):
    from app import quellen
    monkeypatch.setattr(quellen.time, "sleep", lambda s: None)
    monkeypatch.setattr(quellen.db, "spiele", lambda: [{"id": "onepiece"}, {"id": "cyberpunk"}])
    gruppen = {
        68: [
            {"groupId": 1, "name": "Romance Dawn", "abbreviation": "OP01", "publishedOn": "2022-12-02T00:00:00"},
            {"groupId": 2, "name": "Romance Dawn Pre-Release Cards", "abbreviation": "OP01PR", "publishedOn": "2022-11-25T00:00:00"},
            {"groupId": 3, "name": "ST-31: Starter Deck 31 RED Monkey.D.Luffy", "abbreviation": "ST-31", "publishedOn": "2026-07-31T00:00:00"},
            {"groupId": 4, "name": "Extra Booster: One Piece Heroines Edition Vol. 2", "abbreviation": "EB05", "publishedOn": "2030-10-30T00:00:00"},
            {"groupId": 5, "name": "One Piece Promotion Cards", "abbreviation": "P", "publishedOn": "2022-09-01T00:00:00"},
            {"groupId": 6, "name": "Starter Deck 1: Straw Hat Crew", "abbreviation": "ST-01", "publishedOn": "2022-12-02T00:00:00"},
            {"groupId": 7, "name": "Starter Deck 1 Straw Hat Crew", "abbreviation": "ST01", "publishedOn": "2022-12-02T00:00:00"},
        ],
        92: [{"groupId": 8, "name": "The Heist - Retail Starter Deck", "abbreviation": "", "publishedOn": "2026-11-06T00:00:00"}],
    }
    monkeypatch.setattr(quellen, "_laden", lambda url: {"results": gruppen[int(url.split("/")[-2])]})
    sets = {s["extern_id"]: s for s in quellen.tcgcsv("1990-01-01")}
    assert set(sets) == {"tcgcsv:1", "tcgcsv:3", "tcgcsv:4", "tcgcsv:6", "tcgcsv:8"}
    assert sets["tcgcsv:3"]["name"] == "Starter Deck 31 RED Monkey.D.Luffy" and sets["tcgcsv:3"]["typ"] == "deck"
    assert sets["tcgcsv:1"]["typ"] == "hauptset" and sets["tcgcsv:1"]["status"] == "bestaetigt"
    assert sets["tcgcsv:4"]["typ"] == "spezialset" and sets["tcgcsv:4"]["status"] == "angekuendigt"
    assert sets["tcgcsv:8"]["name"] == "The Heist Starter Deck" and sets["tcgcsv:8"]["code"] is None


def test_archiv_endpunkt_und_seite(client):
    d = client.get("/api/archiv").json()
    assert d["anzahl"] == len(d["releases"]) > 0
    from datetime import date as _d
    assert all(r["datum"] < _d.today().isoformat() for r in d["releases"])
    daten = [r["datum"] for r in d["releases"]]
    assert daten == sorted(daten, reverse=True)
    assert 'data-ansicht="archiv"' in client.get("/").text


def test_aufgeteilte_startdaten_bereinigt(client):
    db.anlegen({"spiel": "dbfw", "name": "Starter Decks FS13 & FS14", "datum": "2027-01-22", "typ": "deck"})
    db.meta_set("seed_version", "2")
    db.init()
    namen = {r["name"] for r in db.releases(spiele_filter=["dbfw"], von="1900-01-01")}
    assert "Starter Decks FS13 & FS14" not in namen
    assert "Starter Deck 13: The Saiyan Raised on Earth" in namen


def test_statische_dateien_versioniert(client):
    from app import main
    html_text = client.get("/").text
    assert f"/static/app.js?v={main.STATIC_VERSION}" in html_text
    assert f"/static/style.css?v={main.STATIC_VERSION}" in html_text
    assert "/static/app.js\"" not in html_text
    assert "immutable" in client.get(f"/static/app.js?v={main.STATIC_VERSION}").headers["cache-control"]
    assert client.get("/static/app.js").headers["cache-control"] == "no-cache"
    assert client.get("/static/app.js?v=alt").headers["cache-control"] == "no-cache"


# --- Kartenlisten ---------------------------------------------------------------------

def _png(farbe=(200, 30, 30)) -> bytes:
    import io
    from PIL import Image
    aus = io.BytesIO()
    Image.new("RGB", (600, 838), farbe).save(aus, "PNG")
    return aus.getvalue()


def _kartenquelle(monkeypatch, release_id):
    from app import karten

    def verknuepfen(spiel):
        with db.verbindung() as con:
            con.execute("INSERT OR IGNORE INTO quellen_stand (extern_id, release_id, datum, name, gesehen) "
                        "VALUES ('scryfall:abc', ?, '2026-11-20', 'Testset', 'x')", (release_id,))
        return 1

    liste = [
        {"nummer": "10", "name": "Zehn", "seltenheit": "Rare", "art": "Creature", "bild_url": "https://cards.scryfall.io/n/10.jpg"},
        {"nummer": "2", "name": "Zwei", "seltenheit": "Common", "bild_url": "https://cards.scryfall.io/n/2.jpg"},
        {"nummer": "3", "name": "Fremd", "bild_url": "https://boese.example.com/x.jpg"},
    ]
    monkeypatch.setattr(karten, "sets_verknuepfen", verknuepfen)
    monkeypatch.setattr(karten, "LISTEN", {**karten.LISTEN, "scryfall": lambda kennung, name, kontext: liste})
    abrufe = []
    monkeypatch.setattr(karten, "_herunterladen", lambda url: abrufe.append(url) or _png())
    return abrufe


def test_kartenlisten_standardmaessig_aus(client):
    uebersicht = client.get("/api/admin/karten", auth=ADMIN).json()
    assert uebersicht["spiele"] and not any(s["aktiv"] for s in uebersicht["spiele"])
    rows = client.get("/api/releases?rueckblick=3650").json()
    assert not any("karten" in r for r in rows)
    assert client.get("/api/karten/1").status_code == 404
    assert client.get("/karten/1").status_code == 404
    # Abrufen geht erst nach dem Freischalten
    assert client.post("/api/admin/karten/magic/abrufen", auth=ADMIN, headers=H).status_code == 400


def test_kartenliste_abrufen_und_anzeigen(client, monkeypatch):
    from app import karten
    rid = next(r for r in client.get("/api/releases?spiele=magic").json() if r["name"] == "Star Trek")["id"]
    abrufe = _kartenquelle(monkeypatch, rid)
    assert client.put("/api/admin/karten/magic", json={"aktiv": True}, auth=ADMIN, headers=H).status_code == 200
    meldung = karten._abrufen("magic")
    assert "1 Sets mit 3 Karten" in meldung
    assert sorted(abrufe) == ["https://cards.scryfall.io/n/10.jpg", "https://cards.scryfall.io/n/2.jpg"]

    d = client.get(f"/api/karten/{rid}").json()
    assert [k["nummer"] for k in d["karten"]] == ["2", "3", "10"]  # natürlich sortiert
    assert d["karten"][1]["bild"] is None  # fremder Server wird nicht geladen
    bild = client.get(d["karten"][0]["bild"])
    assert bild.status_code == 200 and bild.headers["content-type"] == "image/webp"
    from PIL import Image
    import io
    assert Image.open(io.BytesIO(bild.content)).width == karten.BILDBREITE
    assert client.get(f"/api/releases/{rid}").json()["karten"] == 3
    assert client.get("/karten/%d" % rid).status_code == 200

    # Zweiter Lauf: vorhandene Bilder werden nicht erneut geladen
    abrufe.clear()
    karten._abrufen("magic")
    assert abrufe == []

    # Ausschalten blendet alles aus, die Daten bleiben erhalten
    client.put("/api/admin/karten/magic", json={"aktiv": False}, auth=ADMIN, headers=H)
    assert client.get(f"/api/karten/{rid}").status_code == 404
    assert client.get(d["karten"][0]["bild"]).status_code == 404
    assert "karten" not in client.get(f"/api/releases/{rid}").json()
    client.put("/api/admin/karten/magic", json={"aktiv": True}, auth=ADMIN, headers=H)
    assert len(client.get(f"/api/karten/{rid}").json()["karten"]) == 3

    # Löschen entfernt Listen und Bilder
    assert client.delete("/api/admin/karten/magic", auth=ADMIN, headers=H).status_code == 200
    assert client.get(f"/api/karten/{rid}").status_code == 404
    assert not os.path.exists(os.path.join(karten.verzeichnis(), "magic"))


def test_kartenquellen_parser(monkeypatch):
    from app import karten
    produkte = [
        {"name": "Booster Box", "imageUrl": "https://tcgplayer-cdn.tcgplayer.com/product/1_200w.jpg", "extendedData": []},
        {"name": "Franky", "imageUrl": "https://tcgplayer-cdn.tcgplayer.com/product/2_200w.jpg",
         "extendedData": [{"name": "Number", "value": "OP18-021"}, {"name": "Rarity", "value": "L"},
                          {"name": "CardType", "value": "Leader"}]},
    ]
    k = karten._tcgcsv_karten(produkte)
    assert k == [{"nummer": "OP18-021", "name": "Franky", "seltenheit": "L", "art": "Leader",
                  "bild_url": "https://tcgplayer-cdn.tcgplayer.com/product/2_400w.jpg"}]

    swu = {"data": [
        {"Number": "001", "Name": "Luke", "Subtitle": "Jedi", "Rarity": "Rare", "Type": "Leader", "VariantType": "Normal",
         "FrontArt": "https://cdn.swu-db.com/images/cards/X/001.png"},
        {"Number": "001", "Name": "Luke", "Rarity": "Rare", "VariantType": "Foil", "FrontArt": "https://cdn.swu-db.com/a.png"},
        {"Number": "270", "Name": "Luke", "Rarity": "Rare", "VariantType": "Hyperspace", "FrontArt": "https://cdn.swu-db.com/b.png"},
    ]}
    monkeypatch.setattr(karten, "_laden", lambda url: swu)
    k = karten._swudb("X", None, {})
    assert [(x["nummer"], x["name"], x["variante"]) for x in k] == [("001", "Luke – Jedi", None), ("270", "Luke", "Hyperspace")]

    karten_json = [{"name": "Snatch", "pitch": "1", "type_text": "Generic Action - Attack", "printings": [
        {"id": "WTR167", "set_id": "WTR", "foiling": "R", "edition": "A", "rarity": "C", "image_url": "https://x/rainbow.webp"},
        {"id": "WTR167", "set_id": "WTR", "foiling": "S", "edition": "U", "rarity": "C", "image_url": "https://x/standard.webp"},
    ]}]
    monkeypatch.setattr(karten, "_laden", lambda url: karten_json)
    k = karten._fabcube("WTR", None, {})
    assert k == [{"nummer": "WTR167", "name": "Snatch (Rot)", "seltenheit": "Common", "art": "Generic Action - Attack",
                  "bild_url": "https://x/standard.webp"}]

    ygo = {"data": [{"name": "Dark Magician", "type": "Normal Monster", "card_images": [{"image_url": "https://images.ygoprodeck.com/images/cards/1.jpg"}],
                     "card_sets": [{"set_name": "Legend of Blue Eyes", "set_code": "LOB-005", "set_rarity": "Ultra Rare"},
                                   {"set_name": "Legend of Blue Eyes", "set_code": "LOB-005", "set_rarity": "Ultra Rare"},
                                   {"set_name": "Anderes Set", "set_code": "AS-001", "set_rarity": "Common"}]}]}
    monkeypatch.setattr(karten, "_laden", lambda url: ygo)
    k = karten._ygoprodeck("LOB", "Legend of Blue Eyes", {})
    assert [(x["nummer"], x["seltenheit"]) for x in k] == [("LOB-005", "Ultra Rare")]

    with pytest.raises(ValueError):
        karten._herunterladen("https://boese.example.com/x.jpg")


def test_verknuepfen_legt_nichts_an(client):
    from app import quellen
    vorher = len(db.releases(von="1900-01-01"))
    sets = [{"extern_id": "x:1", "spiel": "magic", "name": "Star Trek", "code": "TRK", "typ": "hauptset", "datum": "2026-11-20"},
            {"extern_id": "x:2", "spiel": "magic", "name": "Gibt es nicht", "code": "GEN", "typ": "hauptset", "datum": "2001-01-01"}]
    assert quellen.verknuepfen(sets) == 1
    assert quellen.verknuepfen(sets) == 0
    assert len(db.releases(von="1900-01-01")) == vorher


def test_set_symbole_und_logos(client, monkeypatch):
    from app import karten, quellen, setbilder
    magic = next(r for r in client.get("/api/releases?spiele=magic").json() if r["name"] == "Star Trek")["id"]
    with db.verbindung() as con:
        con.execute("INSERT INTO quellen_stand (extern_id, release_id, datum, name, gesehen) VALUES ('scryfall:trk', ?, '2026-11-20', 'Star Trek', 'x')", (magic,))
    monkeypatch.setattr(setbilder, "_verknuepfen", lambda: None)
    monkeypatch.setattr(setbilder, "_scryfall", lambda: {"scryfall:trk": {"symbol": "https://svgs.scryfall.io/sets/trk.svg", "mono": True}})
    monkeypatch.setattr(setbilder, "_ygoprodeck", lambda: {})
    monkeypatch.setattr(setbilder, "_fabcube", lambda: {})
    monkeypatch.setattr(setbilder, "_tcgdex", lambda v, b: {})
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M0 0h10v10z"/></svg>'
    abrufe = []
    monkeypatch.setattr(karten, "_herunterladen", lambda url: abrufe.append(url) or svg)
    assert "1 Symbole" in setbilder.abgleichen()

    r = client.get(f"/api/releases/{magic}").json()
    assert r["symbol"].startswith("/setbild/") and r["symbol_mono"] is True
    antwort = client.get(r["symbol"])
    assert antwort.status_code == 200 and antwort.headers["content-type"].startswith("image/svg+xml")
    assert "default-src 'none'" in antwort.headers["content-security-policy"]
    # Zweiter Lauf lädt nichts erneut
    setbilder.abgleichen()
    assert len(abrufe) == 1

    # SVG mit Skript wird abgelehnt
    monkeypatch.setattr(karten, "_herunterladen", lambda url: b'<svg><script>alert(1)</script></svg>')
    with pytest.raises(ValueError):
        setbilder._speichern("https://svgs.scryfall.io/sets/boese.svg", "symbol")

    # Ausschalten blendet aus
    client.put("/api/admin/einstellungen", json={"set_bilder": False}, auth=ADMIN, headers=H)
    assert "symbol" not in client.get(f"/api/releases/{magic}").json()
    assert client.get(r["symbol"]).status_code == 404


def test_kartenlisten_uebersicht(client, monkeypatch):
    from app import karten
    assert client.get("/api/kartenlisten").json() == {"spiele": [], "sets": []}
    assert client.get("/karten").status_code == 404
    assert 'href="/karten"' not in client.get("/").text
    rid = next(r for r in client.get("/api/releases?spiele=magic").json() if r["name"] == "Star Trek")["id"]
    _kartenquelle(monkeypatch, rid)
    client.put("/api/admin/karten/magic", json={"aktiv": True}, auth=ADMIN, headers=H)
    karten._abrufen("magic")
    d = client.get("/api/kartenlisten").json()
    assert [s["id"] for s in d["spiele"]] == ["magic"]
    assert d["sets"][0]["id"] == rid and d["sets"][0]["karten"] == 3
    assert client.get("/karten").status_code == 200
    assert 'href="/karten"' in client.get("/").text
