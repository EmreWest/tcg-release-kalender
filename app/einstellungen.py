"""In der Verwaltung änderbare Einstellungen. Umgebungsvariablen liefern nur die Startwerte."""
import json
import os
from urllib.parse import urlparse

from . import db

STANDARD_DOMAINS = [
    "pokemon.com", "pokemon.de", "pokemoncenter.com",
    "onepiece-cardgame.com",
    "wizards.com", "magic.wizards.com",
    "disneylorcana.com", "ravensburger.de", "ravensburger.com",
    "yugioh-card.com", "konami.com",
    "playriftbound.com", "riotgames.com",
    "starwarsunlimited.com", "fantasyflightgames.com",
    "gundam-gcg.com",
    "digimoncard.com",
    "dbs-cardgame.com",
    "bandai.com", "bandai-tcg-plus.com",
]

AUTO_MODI = {
    "aus": "Aus – alle Vorschläge selbst prüfen",
    "offiziell": "Offizielle Quellen mit festem Datum automatisch übernehmen",
    "alle": "Alle Vorschläge automatisch übernehmen",
}


def standard() -> dict:
    modus = (os.environ.get("CLAUDE_AUTO_UEBERNAHME") or "aus").strip().lower()
    return {
        "titel": (os.environ.get("SEITENTITEL") or "TCG-Release-Kalender").strip(),
        "untertitel": (os.environ.get("SEITENUNTERTITEL")
                       or "Kommende Sets für Deutschland und die EU – filtern, anschauen, als Kalender abonnieren.").strip(),
        "titel_zeigen": True,
        "logo": None,
        "favicon": None,
        "auto_uebernahme": modus if modus in AUTO_MODI else "aus",
        "offizielle_domains": STANDARD_DOMAINS,
    }


def lesen() -> dict:
    wert = standard()
    try:
        gespeichert = json.loads(db.meta_get("einstellungen") or "{}")
    except ValueError:
        gespeichert = {}
    wert.update({k: v for k, v in gespeichert.items() if k in wert})
    return wert


def _domains(liste) -> list[str]:
    if isinstance(liste, str):
        liste = liste.replace(",", "\n").splitlines()
    sauber = []
    for d in liste or []:
        d = str(d).strip().lower()
        if not d:
            continue
        if "://" in d:
            d = urlparse(d).hostname or ""
        d = d.removeprefix("www.").strip("/. ")
        if d and all(c.isalnum() or c in "-." for c in d) and d not in sauber:
            sauber.append(d)
    return sauber


def aendern(daten: dict) -> dict:
    aktuell = lesen()
    neu = {}
    if "titel" in daten:
        titel = str(daten["titel"] or "").strip()
        if not titel:
            raise ValueError("Der Titel darf nicht leer sein")
        neu["titel"] = titel[:80]
    if "untertitel" in daten:
        neu["untertitel"] = str(daten["untertitel"] or "").strip()[:200]
    if "titel_zeigen" in daten:
        neu["titel_zeigen"] = bool(daten["titel_zeigen"])
    if "auto_uebernahme" in daten:
        if daten["auto_uebernahme"] not in AUTO_MODI:
            raise ValueError("Unbekannter Modus für die automatische Übernahme")
        neu["auto_uebernahme"] = daten["auto_uebernahme"]
    if "offizielle_domains" in daten:
        neu["offizielle_domains"] = _domains(daten["offizielle_domains"])
    return _speichern(aktuell | neu)


def _speichern(wert: dict) -> dict:
    db.meta_set("einstellungen", json.dumps(wert, ensure_ascii=False))
    return wert


def datei_setzen(art: str, datei: str | None) -> str | None:
    """Setzt Logo oder Favicon der Seite und gibt die bisherige Datei zurück."""
    aktuell = lesen()
    alt = aktuell.get(art)
    aktuell[art] = datei
    _speichern(aktuell)
    return alt


def ist_offiziell(url: str | None, domains: list[str]) -> bool:
    if not url:
        return False
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in domains)


def automatisch_uebernehmen(aktion: str, daten: dict, quelle: str | None) -> bool:
    e = lesen()
    if e["auto_uebernahme"] == "alle":
        return True
    if e["auto_uebernahme"] != "offiziell" or not ist_offiziell(quelle, e["offizielle_domains"]):
        return False
    if daten.get("genauigkeit", "tag") != "tag":
        return False
    if aktion == "neu":
        return daten.get("status") in ("bestaetigt", "verschoben", "abgesagt")
    if "status" in daten and daten["status"] in ("geruecht", "angekuendigt"):
        return False
    return True
