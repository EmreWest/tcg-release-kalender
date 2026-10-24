# TCG-Release-Kalender

Ein selbst gehosteter Release-Kalender für Sammelkartenspiele (Pokémon, One Piece, Magic: The Gathering, Disney Lorcana, Yu-Gi-Oh!, Riftbound, Star Wars: Unlimited und weitere). Die Oberfläche ist deutsch, die Termine beziehen sich auf Deutschland bzw. die EU.

Besucher filtern nach Spielen, Status und Produkttyp und abonnieren genau diese Auswahl als Kalender in Apple-, Google-, Outlook- oder Thunderbird-Kalender. Neue Sets und Terminverschiebungen landen dann automatisch im eigenen Kalender.

## Funktionen

- Liste, Monats- und Wochenansicht. Jedes Spiel hat eine eigene Farbe, ein Klick auf einen Termin zeigt alle Details.
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
- Optional halbautomatische Pflege: Claude recherchiert regelmäßig per Websuche und legt Vorschläge an, die erst nach deiner Bestätigung übernommen werden.

## Schnellstart mit Docker

Ein fertiges Image für amd64 und arm64 (z. B. Raspberry Pi) liegt in der GitHub Container Registry. Es muss nichts selbst gebaut werden.

```bash
mkdir tcg-kalender && cd tcg-kalender
curl -O https://raw.githubusercontent.com/EmreWest/tcg-release-kalender/main/docker-compose.yml
curl -o .env https://raw.githubusercontent.com/EmreWest/tcg-release-kalender/main/.env.example
# .env öffnen und mindestens ADMIN_PASSWORT setzen
docker compose up -d
```

Danach:

- Kalender: `http://<server>:8085/`
- Verwaltung: `http://<server>:8085/admin` (Benutzer und Passwort aus der `.env`)

Die Daten liegen in `./data/kalender.db` (SQLite). Beim ersten Start wird eine Liste aktueller Termine eingespielt; danach wird sie nie wieder überschrieben.

Ohne Compose:

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
| `SEITENTITEL` | Überschrift der Seite und Name des Kalenders | `TCG-Release-Kalender` |
| `TZ` | Zeitzone des Containers | `Europe/Berlin` |
| `ANTHROPIC_API_KEY` | Aktiviert die automatische Recherche (siehe unten) | leer |
| `CLAUDE_MODEL` | Modell für die Recherche | `claude-sonnet-5-5` |
| `CLAUDE_WEBSEARCH_TOOL` | Version des Websuche-Tools | `web_search_20250305` |
| `CLAUDE_MAX_SUCHEN_PRO_SPIEL` | Websuchen je Spiel und Durchlauf | `6` |
| `CHECK_INTERVALL_TAGE` | Abstand der Recherche in Tagen, `0` schaltet sie ab | `7` |
| `CHECK_STUNDE` | Frühester Start der Recherche (Stunde, Ortszeit) | `6` |
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
| `spiele` | IDs der Spiele, kommagetrennt. Die Standard-IDs: `pokemon`, `onepiece`, `magic`, `lorcana`, `yugioh`, `riftbound`, `swu`, `gundam`, `digimon`, `dbfw` | alle |
| `status` | `geruecht`, `angekuendigt`, `bestaetigt`, `verschoben`, `abgesagt`, `erschienen` | alle |
| `typ` | `hauptset`, `spezialset`, `deck`, `sonstiges` | alle |
| `prerelease` | `1` = Prerelease-Termine als eigene Einträge | aus |
| `ungenau` | `0` = nur Termine mit festem Tag | an |
| `rueckblick` | wie viele Tage zurück der Kalender reicht | `90` |
| `name` | eigener Kalendername | automatisch |

Ändert sich ein Termin, behält er seine ID. Kalender-Apps aktualisieren den vorhandenen Eintrag, statt einen zweiten anzulegen.

## Termine pflegen

In der Verwaltung unter `/admin`:

- Vorschläge: Was die automatische Recherche oder ein Import gefunden hat. Übernehmen, vor dem Übernehmen bearbeiten oder ablehnen. Bei Änderungen wird alt gegen neu angezeigt.
- Termine: Alle Einträge anlegen, bearbeiten, löschen. Wird das Datum eines bestätigten Termins geändert, gilt er automatisch als „Verschoben“ und das alte Datum bleibt sichtbar – außer du setzt den Status selbst.
- Spiele: Name, Farbe und Reihenfolge ändern, neue Spiele hinzufügen. Spiele mit Terminen lassen sich nicht löschen.
- Import: Vorschläge als JSON einfügen (Format unten).
- System: Status der Recherche und der Benachrichtigungen, Datensicherung als JSON.

Bei Monats- oder Quartalsangaben ist das Datum der erste Tag des Zeitraums (z. B. `2027-04-01` mit Genauigkeit „Quartal“ für Q2 2027).

## Automatische Recherche mit Claude (optional)

Mit gesetztem `ANTHROPIC_API_KEY` (erhältlich unter https://console.anthropic.com) durchsucht der Container im eingestellten Abstand für jedes Spiel einzeln das Web nach neuen Sets und Terminänderungen. Die Ergebnisse erscheinen als Vorschläge in der Verwaltung; ohne deine Bestätigung ändert sich nichts. Unter „System“ lässt sich ein Durchlauf auch sofort starten.

Pro Durchlauf fallen API-Kosten für Tokens und Websuchen an. Die aktuellen Preise stehen unter https://docs.claude.com/en/docs/about-claude/pricing.

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
| `GET /healthz` | Zustandsprüfung |

Schreibende Verwaltungs-Endpunkte unter `/api/admin/` erwarten Benutzer und Passwort sowie den Header `X-Admin-Anfrage: 1` (Schutz gegen Anfragen fremder Webseiten).

## Datensicherung

Es reicht, den Ordner `data` zu sichern. Alternativ unter System → „Alle Daten als JSON herunterladen“.

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
| `app/checker.py` | Automatische Recherche mit Claude |
| `app/seed.json` | Startdaten für eine leere Datenbank |
| `app/static/` | Oberfläche (HTML, CSS, JavaScript ohne Build-Schritt) |

Die Seite lädt keine externen Schriften, Skripte oder Tracker.
