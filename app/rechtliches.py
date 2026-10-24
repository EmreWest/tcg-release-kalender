"""Impressum und Datenschutzerklärung: Vorlagen und eine einfache, sichere Textformatierung."""
import html
import re

VORLAGE_IMPRESSUM = """## Angaben gemäß § 5 DDG
[Name des Geschäfts und Rechtsform, z. B. Muster Sammelkarten GbR]
[Bei einer GbR: Vor- und Nachnamen aller Gesellschafter]
[Straße und Hausnummer]
[PLZ und Ort]

## Kontakt
Telefon: [Telefonnummer]
E-Mail: [E-Mail-Adresse]

## Umsatzsteuer-ID
Umsatzsteuer-Identifikationsnummer gemäß § 27a UStG: [USt-IdNr., Zeile löschen, falls keine vorhanden]

## Verbraucherstreitbeilegung
Wir sind nicht bereit oder verpflichtet, an Streitbeilegungsverfahren vor einer Verbraucherschlichtungsstelle teilzunehmen.

## Hinweis zu Marken
Alle genannten Spiele, Sets und Produkte sind Marken ihrer jeweiligen Inhaber. Diese Seite steht in keiner Verbindung zu den Herstellern. Alle Termine ohne Gewähr.
"""

VORLAGE_DATENSCHUTZ = """## Verantwortlicher
[Name des Geschäfts bzw. der verantwortlichen Person]
[Anschrift]
E-Mail: [E-Mail-Adresse]

## Überblick
Diese Seite zeigt Erscheinungstermine von Sammelkartenspielen. Sie verwendet keine Cookies, keine Analyse- oder Tracking-Werkzeuge und lädt keine Inhalte von fremden Servern (keine externen Schriften, Skripte oder Karten). Es gibt keine Benutzerkonten, Formulare oder Newsletter.

## Server-Protokolle
Beim Aufruf der Seite oder des Kalender-Abos verarbeitet der Server technisch notwendige Daten: IP-Adresse, Datum und Uhrzeit, aufgerufene Adresse, übertragene Datenmenge sowie Browser- bzw. Kalender-App-Kennung. Diese Daten werden ausschließlich zum Betrieb und zur Absicherung der Seite verwendet und nach [Anzahl] Tagen gelöscht.
Rechtsgrundlage ist Art. 6 Abs. 1 lit. f DSGVO; unser berechtigtes Interesse liegt im sicheren und stabilen Betrieb der Seite.
Die Seite wird [selbst betrieben auf eigener Hardware / betrieben bei: Name und Anschrift des Hosting-Anbieters].

## Speicherung im Browser
Die Seite speichert einige Werte im lokalen Speicher deines Browsers (localStorage). Diese Werte bleiben auf deinem Gerät und werden nicht an uns übertragen.

Notwendig (ohne Einwilligung, § 25 Abs. 2 Nr. 2 TDDDG):
- tcg-design: dein gewähltes Design (hell, dunkel, wie System)
- tcg-einwilligung: deine Entscheidung zu diesem Speicher-Hinweis
- tcg-homescreen-hinweis: dass der Hinweis zum Home-Bildschirm bereits angezeigt wurde

Nur mit deiner Einwilligung (§ 25 Abs. 1 TDDDG):
- tcg-filter: deine gewählten Filter und die zuletzt genutzte Ansicht
- tcg-letzter-besuch: Zeitpunkt deines letzten Besuchs, um neue und geänderte Termine zu markieren

Du kannst deine Entscheidung jederzeit über „Speicher-Einstellungen“ unten auf der Seite ändern. Beim Widerruf werden die Komfort-Daten sofort gelöscht. Alle Daten lassen sich außerdem über die Browser-Einstellungen löschen.

## Kalender-Abo
Abonnierst du den Kalender, ruft deine Kalender-App die Termine in regelmäßigen Abständen von unserem Server ab; dabei fallen die oben beschriebenen Server-Protokolle an. Nutzt du die Schaltfläche „Google Kalender“, wirst du zu Google weitergeleitet, und Google ruft den Kalender von seinen Servern ab. Dafür gilt die Datenschutzerklärung von Google: https://policies.google.com/privacy

## Links zu anderen Seiten
Bei vielen Terminen ist eine Quelle verlinkt. Erst wenn du einen solchen Link anklickst, wird die fremde Seite aufgerufen; dort gelten die Datenschutzbestimmungen des jeweiligen Anbieters.

## Deine Rechte
Du hast das Recht auf Auskunft (Art. 15 DSGVO), Berichtigung (Art. 16), Löschung (Art. 17), Einschränkung der Verarbeitung (Art. 18), Datenübertragbarkeit (Art. 20) und Widerspruch (Art. 21) sowie das Recht, eine Einwilligung jederzeit mit Wirkung für die Zukunft zu widerrufen (Art. 7 Abs. 3). Wende dich dazu an die oben genannte Adresse.
Außerdem kannst du dich bei einer Datenschutz-Aufsichtsbehörde beschweren, für Unternehmen in Bayern zum Beispiel beim Bayerischen Landesamt für Datenschutzaufsicht (BayLDA), https://www.lda.bayern.de

Stand: [Monat und Jahr]
"""

_URL = re.compile(r"(https?://[^\s<]+[^\s<.,;:!?)\]])")
_MAIL = re.compile(r"\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")


def _inline(text: str) -> str:
    t = html.escape(text, quote=True)
    t = _URL.sub(lambda m: f'<a href="{m.group(1)}" rel="noopener" target="_blank">{m.group(1)}</a>', t)
    t = _MAIL.sub(lambda m: f'<a href="mailto:{m.group(1)}">{m.group(1)}</a>', t)
    return t


def formatieren(text: str) -> str:
    """Wandelt einfachen Text in HTML um: '## ' = Überschrift, '- ' = Liste, Leerzeile = Absatz.
    Alles andere wird maskiert, eigenes HTML ist nicht möglich."""
    bloecke = re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n").strip())
    teile = []
    for block in bloecke:
        zeilen = [z.rstrip() for z in block.split("\n") if z.strip()]
        if not zeilen:
            continue
        absatz, liste = [], []

        def absatz_schliessen():
            if absatz:
                teile.append("<p>" + "<br>".join(absatz) + "</p>")
                absatz.clear()

        def liste_schliessen():
            if liste:
                teile.append("<ul>" + "".join(f"<li>{x}</li>" for x in liste) + "</ul>")
                liste.clear()

        for z in zeilen:
            s = z.strip()
            if s.startswith("### "):
                absatz_schliessen(); liste_schliessen()
                teile.append(f"<h3>{_inline(s[4:])}</h3>")
            elif s.startswith("## ") or s.startswith("# "):
                absatz_schliessen(); liste_schliessen()
                teile.append(f"<h2>{_inline(s.split(' ', 1)[1])}</h2>")
            elif s.startswith("- "):
                absatz_schliessen()
                liste.append(_inline(s[2:]))
            else:
                liste_schliessen()
                absatz.append(_inline(s))
        absatz_schliessen(); liste_schliessen()
    return "\n".join(teile)
