# TCG-Release-Kalender

Ein selbst gehosteter Release-Kalender für Sammelkartenspiele (Pokémon, One Piece, Magic: The Gathering, Disney Lorcana, Yu-Gi-Oh!, Riftbound, Star Wars: Unlimited, Flesh and Blood, Union Arena und weitere – 20 Spiele ab Werk, beliebig erweiterbar). Die Oberfläche ist deutsch, die Termine beziehen sich auf Deutschland bzw. die EU.

Besucher filtern nach Spielen, Status und Produkttyp und abonnieren genau diese Auswahl als Kalender in Apple-, Google-, Outlook- oder Thunderbird-Kalender. Neue Sets und Terminverschiebungen landen dann automatisch im eigenen Kalender.

## Funktionen

- Liste, Monats- und Wochenansicht. Jedes Spiel hat eine eigene Farbe, ein Klick auf einen Termin zeigt alle Details.
- Eigene Logos je Spiel: In der Verwaltung hochladen, sie erscheinen in den Filtern, an den Terminen und in der Detailansicht.
- Kalender-Abo (`webcal://`) mit den gewählten Filtern; einzelne Termine lassen sich auch als `.ics`-Datei speichern.
- Helles und dunkles Design (folgt standardmäßig der Systemeinstellung, umschaltbar).
- Status je Termin: Gerücht, Angekündigt, Bestätigt, Verschoben, Abgesagt; „Erschienen“ ergibt sich automatisch aus dem Datum.
- Ungefähre Termine („ca. März“, „ca. Q2“) werden als solche gekennzeichnet und lassen sich ausblenden.
- Prerelease-Termine optional als eigene Einträge.
- Markierung „Neu“ und „Geändert“ für alles, was sich seit dem letzten Besuch getan hat.
- Änderungsprotokoll auf der Seite und als Atom-Feed (`/feed.xml`).
- Benachrichtigungen bei Änderungen per Discord-Webhook oder beliebigem JSON-Webhook.
- Teilbare Links: Filter, Ansicht und geöffneter Termin stehen in der Adresse.
- Verwaltung mit Passwortschutz: Termine und Spiele (Name, Farbe, Reihenfolge) pflegen, Vorschläge prüfen, JSON-Import und -Export.
- Automatischer Abgleich mit offenen Daten-APIs für Magic, Yu-Gi-Oh!, Star Wars: Unlimited, Flesh and Blood und Pokémon (deutsche Ausgaben) – ohne Kosten und ohne manuelles Zutun.
- Optional Recherche mit Claude für alle anderen Spiele: Neue Sets und Terminänderungen aus offiziellen Quellen werden automatisch übernommen, alles Unsichere landet als Vorschlag zur Prüfung.
- Eigener Titel, Untertitel, Logo und Favicon, einstellbar in der Verwaltung.
- Auf Mobilgeräten einmalig ein Hinweis mit Anleitung, wie man die Seite auf den Home-Bildschirm legt; sie startet dann wie eine App.
- Speicher-Hinweis nach § 25 TDDDG: keine Cookies, kein Tracking; Komfort-Funktionen speichern erst nach Einwilligung.
- Impressum und Datenschutzerklärung mit Vorlagen, pflegbar in der Verwaltung.

## Schnellstart mit Docker

Ein fertiges Image für amd64 und arm64 (z. B. Raspberry Pi) liegt in der GitHub Container Registry. Es muss nichts selbst gebaut werden.

1. Einen Ordner anlegen, z. B. `tcg-kalender`.
2. Darin die beiden Dateien unten anlegen: `docker-compose.yml` und `.env`.
3. In der `.env` mindestens `ADMIN_PASSWORT` setzen.
4. Starten:

```bash
docker compose up -d
```

Danach:

- Kalender: `http://<server>:8085/`
- Verwaltung: `http://<server>:8085/admin` (Benutzer und Passwort aus der `.env`)

### docker-compose.yml

```yaml
services:
  tcg-kalender:
    image: ghcr.io/emrewest/tcg-release-kalender:latest
    container_name: tcg-kalender
    restart: unless-stopped
    ports:
      - "8085:8000"
    volumes:
      - ./data:/data
    env_file: .env
```

Den Port links (`8085`) nach Bedarf anpassen.

### .env

```env
# Einstellungen für den TCG-Release-Kalender
ADMIN_USER=admin
ADMIN_PASSWORT=hier-ein-langes-passwort

# Öffentliche Adresse der Seite, z. B. https://kalender.example.org (leer = automatisch)
OEFFENTLICHE_URL=
# Domain für die IDs der Kalendereinträge – nach dem ersten Abo nicht mehr ändern
UID_DOMAIN=tcg-kalender.local

# Startwerte für Titel und Untertitel – später in der Verwaltung unter „Darstellung“ änderbar
SEITENTITEL=TCG-Release-Kalender
SEITENUNTERTITEL=

# Startwert für die automatisch abgeglichenen Daten-APIs: alle, leer (= aus) oder eine Liste aus
# scryfall, ygoprodeck, swudb, fabcube, tcgdex – danach in der Verwaltung unter „System“ umschaltbar
QUELLEN=alle
QUELLEN_INTERVALL_STUNDEN=12

# Optional: automatische Recherche mit Claude für alle anderen Spiele
ANTHROPIC_API_KEY=
CLAUDE_MODEL=claude-sonnet-5-5
CHECK_INTERVALL_TAGE=7
CHECK_STUNDE=6
CLAUDE_MAX_SUCHEN_PRO_SPIEL=6
# Startwert für die automatische Übernahme: aus, offiziell oder alle (in der Verwaltung änderbar)
CLAUDE_AUTO_UEBERNAHME=offiziell

# Optional: Benachrichtigungen bei Änderungen
DISCORD_WEBHOOK_URL=
WEBHOOK_URL=
WEBHOOK_SPIELE=
WEBHOOK_ARTEN=neu,verschoben,geaendert,geloescht
```

Leere Werte bedeuten „aus“. Was die einzelnen Einstellungen bewirken, steht unter [Konfiguration](#konfiguration).

Die Daten liegen in `./data/kalender.db` (SQLite). Beim ersten Start wird eine Liste aktueller Termine eingespielt; danach wird sie nie wieder überschrieben.

### Ohne Compose

```bash
docker run -d --name tcg-kalender -p 8085:8000 -v "$PWD/data:/data" \
  -e ADMIN_PASSWORT=geheim ghcr.io/emrewest/tcg-release-kalender:latest
```

### Aktualisieren

```bash
docker compose pull && docker compose up -d
```

Das Image wird bei jeder Änderung am Code und zusätzlich einmal pro Woche neu gebaut, damit Sicherheitsupdates des Basis-Images enthalten sind. Verfügbare Tags:

| Tag | Bedeutung |
|---|---|
| `latest` | aktueller Stand von `main` |
| `1.2.3`, `1.2` | feste Version (sobald Versionen getaggt werden) |
| `sha-abc1234` | ein bestimmter Commit |

## Konfiguration

Alle Einstellungen laufen über Umgebungsvariablen (in der `.env`).

| Variable | Bedeutung | Standard |
|---|---|---|
| `ADMIN_USER` | Benutzername für `/admin` | `admin` |
| `ADMIN_PASSWORT` | Passwort für `/admin`. Ohne Passwort ist die Verwaltung gesperrt. | leer |
| `OEFFENTLICHE_URL` | Adresse, unter der die Seite erreichbar ist, z. B. `https://kalender.example.org`. Wird für Links im Feed, in Kalendereinträgen und Benachrichtigungen verwendet. | wird aus der Anfrage ermittelt |
| `UID_DOMAIN` | Domain für die eindeutigen IDs der Kalendereinträge. Nach dem ersten Abo nicht mehr ändern, sonst entstehen doppelte Einträge. | `tcg-kalender.local` |
| `SEITENTITEL` | Startwert für Überschrift, Browser-Tab und Kalendername. Danach in der Verwaltung unter „Darstellung“ änderbar. | `TCG-Release-Kalender` |
| `SEITENUNTERTITEL` | Startwert für den Untertitel | kurzer Beschreibungstext |
| `QUELLEN` | Startwert für die aktiven Daten-APIs: `alle`, leer (= aus) oder kommagetrennt aus `scryfall`, `ygoprodeck`, `swudb`, `fabcube`, `tcgdex`. Danach in der Verwaltung unter „System“ umschaltbar. | `alle` |
| `QUELLEN_INTERVALL_STUNDEN` | Abstand des Abgleichs in Stunden | `12` |
| `TZ` | Zeitzone des Containers | `Europe/Berlin` |
| `ANTHROPIC_API_KEY` | Aktiviert die automatische Recherche (siehe unten) | leer |
| `CLAUDE_MODEL` | Modell für die Recherche | `claude-sonnet-5-5` |
| `CLAUDE_WEBSEARCH_TOOL` | Version des Websuche-Tools | `web_search_20250305` |
| `CLAUDE_MAX_SUCHEN_PRO_SPIEL` | Websuchen je Spiel und Durchlauf | `6` |
| `CHECK_INTERVALL_TAGE` | Abstand der Recherche in Tagen, `0` schaltet sie ab | `7` |
| `CHECK_STUNDE` | Frühester Start der Recherche (Stunde, Ortszeit) | `6` |
| `CLAUDE_AUTO_UEBERNAHME` | Startwert für die automatische Übernahme: `aus`, `offiziell` oder `alle`. Danach in der Verwaltung änderbar. | `aus` |
| `DISCORD_WEBHOOK_URL` | Änderungen an einen Discord-Kanal schicken | leer |
| `WEBHOOK_URL` | Änderungen als JSON an eine beliebige Adresse schicken (z. B. Home Assistant, n8n) | leer |
| `WEBHOOK_SPIELE` | Nur Änderungen dieser Spiele senden, z. B. `pokemon,onepiece` | alle |
| `WEBHOOK_ARTEN` | Welche Änderungen gesendet werden | `neu,verschoben,geaendert,geloescht` |

## Erreichbarkeit und HTTPS

Google Kalender holt Abos von Googles Servern ab. Dafür muss die Seite öffentlich per HTTPS erreichbar sein, zum Beispiel hinter einem Reverse Proxy (Caddy, Traefik, Nginx Proxy Manager) oder einem Cloudflare Tunnel. Apple-Kalender ruft Abos direkt vom Gerät ab; im Heimnetz oder über VPN reicht dort eine interne Adresse.

Beispiel für Caddy:

```
kalender.example.org {
    reverse_proxy tcg-kalender:8000
}
```

Die Verwaltung ist per Passwort geschützt (HTTP Basic Auth). Öffentlich nur über HTTPS betreiben. Wer mag, beschränkt `/admin` und `/api/admin/*` im Reverse Proxy zusätzlich auf das eigene Netz.

## Kalender abonnieren

Auf der Seite die gewünschten Filter setzen und auf „Abonnieren“ tippen. Der Link lässt sich auch von Hand bauen:

```
https://kalender.example.org/kalender.ics?spiele=pokemon,onepiece&status=bestaetigt,angekuendigt&prerelease=1
```

| Parameter | Werte | Standard |
|---|---|---|
| `spiele` | IDs der Spiele, kommagetrennt. Die Standard-IDs: `pokemon`, `onepiece`, `magic`, `lorcana`, `yugioh`, `riftbound`, `swu`, `gundam`, `digimon`, `dbfw`, `fab`, `unionarena`, `weiss`, `vanguard`, `shadowverse`, `hololive`, `fftcg`, `sorcery`, `grandarchive`, `cyberpunk` | alle |
| `status` | `geruecht`, `angekuendigt`, `bestaetigt`, `verschoben`, `abgesagt`, `erschienen` | alle |
| `typ` | `hauptset`, `spezialset`, `deck`, `sonstiges` | alle |
| `prerelease` | `1` = Prerelease-Termine als eigene Einträge | aus |
| `ungenau` | `0` = nur Termine mit festem Tag | an |
| `rueckblick` | wie viele Tage zurück der Kalender reicht | `90` |
| `name` | eigener Kalendername | automatisch |

Ändert sich ein Termin, behält er seine ID. Kalender-Apps aktualisieren den vorhandenen Eintrag, statt einen zweiten anzulegen.

## Darstellung anpassen

Unter Verwaltung → Darstellung:

- Titel: steht oben auf der Seite, im Browser-Tab, als Name des abonnierten Kalenders und im Feed.
- Untertitel: die Zeile unter dem Titel; leer lassen, um sie auszublenden.
- Logo: erscheint links neben dem Titel. Enthält dein Logo den Namen schon, kannst du den Titel daneben ausblenden; er bleibt dann für Screenreader und im Browser-Tab erhalten.
- Favicon: das Symbol im Browser-Tab und auf dem Homescreen. Ohne eigenes Favicon wird das Logo verwendet.

`SEITENTITEL` und `SEITENUNTERTITEL` in der `.env` sind nur Startwerte; was du in der Verwaltung speicherst, hat Vorrang.

## Impressum, Datenschutz und Speicher-Hinweis

Die Seite setzt keine Cookies und bindet nichts von fremden Servern ein. Einige Werte speichert sie im lokalen Speicher des Browsers (localStorage); sie verlassen das Gerät nie. Rechtlich gilt dafür trotzdem § 25 TDDDG, deshalb wird unterschieden:

| Schlüssel | Zweck | Einwilligung nötig |
|---|---|---|
| `tcg-design` | selbst gewähltes Design (nur gespeichert, wenn umgeschaltet wird) | nein |
| `tcg-einwilligung` | Entscheidung zum Speicher-Hinweis | nein |
| `tcg-homescreen-hinweis` | Home-Bildschirm-Hinweis wurde schon gezeigt | nein |
| `tcg-filter` | gemerkte Filter und Ansicht | ja |
| `tcg-letzter-besuch` | Markierung „Neu“ und „Geändert“ | ja |

Beim ersten Besuch erscheint unten ein kleiner Hinweis mit zwei gleichwertigen Schaltflächen, „Nur notwendige“ und „Alle erlauben“. Er überdeckt nichts und lässt sich jederzeit über „Speicher-Einstellungen“ im Fuß der Seite wieder öffnen; ein Widerruf löscht die Komfort-Daten sofort.

Auf Smartphones und Tablets erscheint danach einmalig ein Hinweis mit kurzer Anleitung (iPhone/iPad, Android Chrome, Samsung Internet, Firefox), wie man die Seite auf den Home-Bildschirm legt. Wo der Browser es anbietet, gibt es direkt eine Schaltfläche dafür. Der Hinweis lässt sich wegklicken und verschwindet nach 15 Sekunden von selbst; beim Antippen pausiert der Countdown. Auf Desktop-Rechnern und in der bereits installierten App erscheint er nicht.

Unter Verwaltung → Rechtliches pflegst du Impressum und Datenschutzerklärung. Für beide gibt es Vorlagen, die genau beschreiben, was diese Software speichert und verarbeitet; die Felder in eckigen Klammern füllst du aus. Sobald ein Text gespeichert ist, erscheint der Link im Fuß der Seite. Die Vorlagen ersetzen keine Rechtsberatung.

## Neue Spiele bei bestehenden Installationen

Kommen mit einem Update neue Spiele in die Startdaten, werden sie beim nächsten Start einmalig ergänzt, samt bekannter Termine. Spiele, die du danach löschst, kommen nicht wieder.

## Termine pflegen

In der Verwaltung unter `/admin`:

- Vorschläge: Was die automatische Recherche oder ein Import gefunden hat. Übernehmen, vor dem Übernehmen bearbeiten oder ablehnen. Bei Änderungen wird alt gegen neu angezeigt.
- Termine: Alle Einträge anlegen, bearbeiten, löschen. Wird das Datum eines bestätigten Termins geändert, gilt er automatisch als „Verschoben“ und das alte Datum bleibt sichtbar – außer du setzt den Status selbst.
- Spiele: Name, Farbe, Logo und Reihenfolge ändern, neue Spiele hinzufügen. Spiele mit Terminen lassen sich nicht löschen.

### Logos

Das Projekt enthält keine Logos. Wer welche zeigen möchte, lädt sie unter Verwaltung → Spiele hoch (PNG, WebP, JPEG oder SVG, höchstens 1 MB; SVGs mit Skripten werden abgelehnt). Sie werden in `data/logos/` gespeichert. Ohne Logo zeigt die Seite den Farbpunkt des Spiels. Logos von Sammelkartenspielen sind in der Regel markenrechtlich geschützt; ob du sie auf deiner Seite verwenden darfst, klärst du bitte selbst, etwa über die Richtlinien des Herstellers oder eine Händlervereinbarung.
- Import: Vorschläge als JSON einfügen (Format unten).
- System: Status der Recherche und der Benachrichtigungen, Datensicherung als JSON.

Bei Monats- oder Quartalsangaben ist das Datum der erste Tag des Zeitraums (z. B. `2027-04-01` mit Genauigkeit „Quartal“ für Q2 2027).

## Datenquellen (automatisch)

Für einige Spiele gibt es offene, frei nutzbare Daten-APIs. Der Container gleicht sie standardmäßig alle 12 Stunden ab; einzeln an- und abschalten lassen sie sich unter Verwaltung → System:

| Quelle | Spiel | Liefert |
|---|---|---|
| [Scryfall](https://scryfall.com/docs/api) | Magic: The Gathering | alle angekündigten Sets mit weltweitem Release-Datum, inkl. Commander-Decks |
| [YGOPRODeck](https://ygoprodeck.com/api-guide/) | Yu-Gi-Oh! | Sets mit TCG-Termin (Europa/Nordamerika), sobald die ersten Karten bekannt sind |
| [SWU-DB](https://www.swu-db.com/api) | Star Wars: Unlimited | Hauptsets, Icons-Sets und Twin-Suns-Decks mit Release-Datum, oft Monate im Voraus |
| [The FaB Cube](https://github.com/the-fab-cube/flesh-and-blood-cards) | Flesh and Blood | Booster-Sets, Armory-, Blitz- und Silver-Age-Decks, Mastery Packs, sobald sie im Datensatz stehen |
| [TCGdex](https://tcgdex.dev/) | Pokémon | deutsche Ausgaben mit deutschem Set-Namen und Erscheinungsdatum; neue Sets erscheinen dort erst kurz vor Release |

So arbeitet der Abgleich:

- Neue Sets werden direkt als „Bestätigt“ angelegt.
- Bestehende Termine werden über Name oder Set-Code zugeordnet und beim ersten Treffer an die Quelle angeglichen (gilt als Korrektur, nicht als Verschiebung).
- Ändert sich ein Termin später in der Quelle, wird er übernommen und als „Verschoben“ markiert.
- Deine manuellen Änderungen bleiben erhalten, solange sich der Wert in der Quelle nicht ändert.
- Löschst du einen Termin in der Verwaltung, wird er nicht wieder angelegt.

Unter Verwaltung → System siehst du das Ergebnis des letzten Abgleichs und kannst ihn sofort starten. Der Container braucht dafür ausgehenden Zugriff auf `api.scryfall.com`, `db.ygoprodeck.com`, `api.swu-db.com`, `raw.githubusercontent.com` und `api.tcgdex.net`.

Für One Piece, Lorcana, Digimon, Union Arena und die übrigen Spiele gibt es keine offenen Schnittstellen, die kommende Sets mit Datum kennen (geprüft wurden u. a. OPTCG-API, Lorcast, Lorcana-API, Grand Archive und Sorcery). Eigene Scraper für die Herstellerseiten sind bewusst nicht eingebaut: Sie brechen bei jedem Redesign, viele Seiten blocken automatisierte Abrufe, und manche Nutzungsbedingungen schließen sie aus. Für diese Spiele gibt es die Recherche mit Claude.

## Automatische Recherche mit Claude (optional)

Mit gesetztem `ANTHROPIC_API_KEY` (erhältlich unter https://console.anthropic.com) durchsucht der Container im eingestellten Abstand für jedes Spiel einzeln das Web nach neuen Sets und Terminänderungen. Die Ergebnisse erscheinen als Vorschläge in der Verwaltung; ohne deine Bestätigung ändert sich nichts. Unter „System“ lässt sich ein Durchlauf auch sofort starten.

Pro Durchlauf fallen API-Kosten für Tokens und Websuchen an. Die aktuellen Preise stehen unter https://docs.claude.com/en/docs/about-claude/pricing.

Spiele, die bereits über eine Datenquelle mit Vorlauf abgeglichen werden (Magic, Yu-Gi-Oh!, Star Wars: Unlimited, Flesh and Blood), überspringt die Recherche, um Kosten zu sparen. Einzeln lassen sie sich unter „System“ trotzdem recherchieren.

### Automatische Übernahme

Unter Verwaltung → System → „Gefundene Vorschläge“ legst du fest, was mit den Ergebnissen passiert:

| Einstellung | Verhalten |
|---|---|
| Aus | Alle Vorschläge warten auf deine Prüfung. |
| Offizielle Quellen | Automatisch übernommen wird, wenn die Quelle auf einer Domain aus der Liste offizieller Quellen liegt und der Termin ein festes Tagesdatum hat. Neue Sets zusätzlich nur mit Status „Bestätigt“. Alles andere bleibt zur Prüfung liegen. |
| Alle | Jeder Vorschlag wird sofort übernommen. |

Die Liste offizieller Quellen ist mit den Herstellerseiten vorbelegt und lässt sich dort bearbeiten (eine Domain pro Zeile, Subdomains zählen mit). Jede automatische Übernahme erscheint im Änderungsprotokoll und, falls eingerichtet, als Benachrichtigung. Vorschläge aus dem manuellen Import werden nie automatisch übernommen.

Ohne API-Key funktioniert alles andere wie gewohnt. Vorschläge lassen sich dann über den Import einspielen.

## Vorschlagsformat für den Import

```json
[
  {
    "aktion": "neu",
    "daten": {
      "spiel": "swu",
      "name": "Legacy of Skywalker",
      "code": null,
      "typ": "hauptset",
      "datum": "2027-03-12",
      "genauigkeit": "tag",
      "prerelease": null,
      "status": "angekuendigt",
      "notiz": "EU-Termin noch offen"
    },
    "quelle": "https://example.org/ankuendigung",
    "begruendung": "Neu angekündigt"
  },
  {
    "aktion": "aenderung",
    "release_id": 12,
    "daten": { "datum": "2027-02-19" },
    "quelle": "https://example.org/verschiebung",
    "begruendung": "Termin verschoben"
  }
]
```

- `genauigkeit`: `tag`, `monat` oder `quartal`
- `status`: `geruecht`, `angekuendigt`, `bestaetigt`, `verschoben`, `abgesagt`
- `typ`: `hauptset`, `spezialset`, `deck`, `sonstiges`
- Bei `aenderung` enthält `daten` nur die Felder, die sich ändern. Die `release_id` steht in der Datensicherung (System → „Alle Daten als JSON herunterladen“).

## Benachrichtigungen

Mit `DISCORD_WEBHOOK_URL` erscheint jede Änderung als Nachricht im Discord-Kanal, eingefärbt in der Farbe des Spiels. Mit `WEBHOOK_URL` wird bei jeder Änderung ein JSON-Objekt per POST geschickt:

```json
{
  "ereignis": "verschoben",
  "titel": "Termin geändert: Delta Reign",
  "text": "Datum: 06.11.2026 → 13.11.2026",
  "link": "https://kalender.example.org/?r=2",
  "aenderung": { "...": "Eintrag aus dem Änderungsprotokoll" },
  "termin": { "...": "aktueller Stand des Termins" }
}
```

Mögliche Ereignisse: `neu`, `verschoben`, `geaendert`, `geloescht`.

## Schnittstellen

Alle öffentlichen Endpunkte liefern JSON bzw. Kalender- oder Feed-Dateien und brauchen keine Anmeldung.

| Pfad | Inhalt |
|---|---|
| `GET /api/meta` | Spiele mit Farben, Status- und Typbezeichnungen |
| `GET /api/releases` | Termine; Parameter `spiele`, `status`, `typ`, `rueckblick` (Tage) |
| `GET /api/releases/{id}` | ein Termin |
| `GET /api/aenderungen` | Änderungsprotokoll; Parameter `spiele`, `limit` |
| `GET /kalender.ics` | Kalender-Abo (Parameter siehe oben) |
| `GET /release/{id}.ics` | ein einzelner Termin als Kalenderdatei |
| `GET /feed.xml` | Änderungen als Atom-Feed; Parameter `spiele` |
| `GET /logos/{datei}` | hochgeladene Logos (Adresse steht in `/api/meta`) |
| `GET /favicon` | aktuelles Favicon |
| `GET /manifest.webmanifest` | Web-App-Manifest für den Home-Bildschirm |
| `GET /impressum`, `GET /datenschutz` | Rechtstexte aus der Verwaltung |
| `GET /healthz` | Zustandsprüfung |

Schreibende Verwaltungs-Endpunkte unter `/api/admin/` erwarten Benutzer und Passwort sowie den Header `X-Admin-Anfrage: 1` (Schutz gegen Anfragen fremder Webseiten).

## Datensicherung

Es reicht, den Ordner `data` zu sichern (Datenbank und Logos). Alternativ unter System → „Alle Daten als JSON herunterladen“.

## Entwicklung und Betrieb ohne Docker

Voraussetzung: Python 3.12 oder neuer.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
DB_PFAD=./data/kalender.db ADMIN_PASSWORT=geheim uvicorn app.main:app --reload --port 8085
python -m pytest -q
```

Aufbau:

| Datei | Aufgabe |
|---|---|
| `app/main.py` | Webserver und Endpunkte (FastAPI) |
| `app/db.py` | Datenhaltung (SQLite), Status-Logik, Änderungsprotokoll |
| `app/ics.py` | Kalenderdateien nach RFC 5545 |
| `app/atom.py` | Atom-Feed |
| `app/notify.py` | Discord- und JSON-Webhooks |
| `app/logos.py` | Hochgeladene Logos prüfen und speichern |
| `app/quellen.py` | Abgleich mit Scryfall, YGOPRODeck, SWU-DB, The FaB Cube und TCGdex |
| `app/einstellungen.py` | In der Verwaltung änderbare Einstellungen |
| `app/rechtliches.py` | Vorlagen und Formatierung für Impressum und Datenschutz |
| `app/static/hinweise.js` | Speicher-Hinweis und Home-Bildschirm-Hinweis |
| `app/checker.py` | Automatische Recherche mit Claude |
| `app/seed.json` | Startdaten für eine leere Datenbank |
| `app/static/` | Oberfläche (HTML, CSS, JavaScript ohne Build-Schritt) |

Die Seite lädt keine externen Schriften, Skripte oder Tracker.
