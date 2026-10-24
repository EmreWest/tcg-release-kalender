"""Benachrichtigungen bei Änderungen: Discord-Webhook und/oder generischer JSON-Webhook."""
import json
import logging
import os
import threading
import urllib.request
from datetime import date

from . import db, ics

log = logging.getLogger("tcg-kalender.notify")

DISCORD_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").strip()
WEBHOOK_SPIELE = {s.strip() for s in os.environ.get("WEBHOOK_SPIELE", "").split(",") if s.strip()}
WEBHOOK_ARTEN = {s.strip() for s in (os.environ.get("WEBHOOK_ARTEN") or "neu,verschoben,geaendert,geloescht").split(",")
                 if s.strip()}
BASIS_URL = os.environ.get("OEFFENTLICHE_URL", "").rstrip("/")

ART_TEXT = {"neu": "Neu", "verschoben": "Termin geändert", "geaendert": "Aktualisiert", "geloescht": "Entfernt"}
FELD_TEXT = {"name": "Name", "code": "Code", "typ": "Typ", "datum": "Datum", "genauigkeit": "Genauigkeit",
             "prerelease": "Prerelease", "status": "Status"}


def aktiv() -> bool:
    return bool(DISCORD_URL or WEBHOOK_URL)


def wert_text(feld: str, wert) -> str:
    if wert in (None, ""):
        return "–"
    if feld in ("datum", "prerelease"):
        return date.fromisoformat(wert).strftime("%d.%m.%Y")
    if feld == "status":
        return db.STATUS.get(wert, wert)
    if feld == "typ":
        return db.TYPEN.get(wert, wert)
    if feld == "genauigkeit":
        return db.GENAUIGKEIT.get(wert, wert)
    return str(wert)


def beschreibung(eintrag: dict) -> str:
    """Kurzer deutscher Text zu einer Änderung (auch für den Atom-Feed)."""
    if eintrag["art"] == "neu":
        r = db.release(eintrag["release_id"]) if eintrag.get("release_id") else None
        if r:
            teile = [f"Termin: {ics.zeitraum_text(r)}",
                     f"Status: {wert_text('status', r['status'])}",
                     f"Typ: {wert_text('typ', r['typ'])}"]
            if r.get("prerelease"):
                teile.append(f"Prerelease: {wert_text('prerelease', r['prerelease'])}")
            return "\n".join(teile)
        return ""
    if eintrag["art"] == "geloescht":
        return "Der Termin wurde aus dem Kalender entfernt."
    zeilen = []
    for feld, (alt, neu) in (eintrag.get("details") or {}).items():
        zeilen.append(f"{FELD_TEXT.get(feld, feld)}: {wert_text(feld, alt)} → {wert_text(feld, neu)}")
    return "\n".join(zeilen)


def _post(url: str, daten: dict):
    req = urllib.request.Request(url, data=json.dumps(daten).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "tcg-kalender"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def _senden(eintrag: dict):
    titel = f"{ART_TEXT.get(eintrag['art'], eintrag['art'])}: {eintrag['name']}"
    text = beschreibung(eintrag)
    link = f"{BASIS_URL}/?r={eintrag['release_id']}" if BASIS_URL and eintrag["art"] != "geloescht" else None
    if DISCORD_URL:
        embed = {"title": titel[:256], "description": text[:4000],
                 "author": {"name": eintrag.get("spiel_name") or eintrag["spiel"]}}
        farbe = (eintrag.get("spiel_farbe") or "").lstrip("#")
        if len(farbe) == 6:
            embed["color"] = int(farbe, 16)
        if link:
            embed["url"] = link
        try:
            _post(DISCORD_URL, {"embeds": [embed]})
        except Exception:  # noqa: BLE001
            log.exception("Discord-Webhook fehlgeschlagen")
    if WEBHOOK_URL:
        try:
            _post(WEBHOOK_URL, {"ereignis": eintrag["art"], "titel": titel, "text": text, "link": link,
                                "aenderung": eintrag,
                                "termin": db.release(eintrag["release_id"], mit_spiel=True)
                                if eintrag.get("release_id") and eintrag["art"] != "geloescht" else None})
        except Exception:  # noqa: BLE001
            log.exception("Webhook fehlgeschlagen")


def beobachter(eintrag: dict):
    if not aktiv():
        return
    if eintrag["art"] not in WEBHOOK_ARTEN:
        return
    if WEBHOOK_SPIELE and eintrag["spiel"] not in WEBHOOK_SPIELE:
        return
    threading.Thread(target=_senden, args=(eintrag,), daemon=True).start()


def registrieren():
    if beobachter not in db.BEOBACHTER:
        db.BEOBACHTER.append(beobachter)
    if aktiv():
        log.info("Benachrichtigungen aktiv (%s)", ", ".join(
            n for n, u in (("Discord", DISCORD_URL), ("Webhook", WEBHOOK_URL)) if u))
