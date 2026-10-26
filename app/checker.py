"""Halbautomatische Aktualisierung: Claude recherchiert per Websuche neue Sets und
Terminänderungen und legt sie als Vorschläge an. Übernommen wird erst nach
Bestätigung im Admin-Bereich."""
import json
import logging
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone

from . import db, einstellungen

log = logging.getLogger("tcg-kalender.checker")

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
MODELL = (os.environ.get("CLAUDE_MODEL") or "claude-sonnet-5-5")
WEBSEARCH_TOOL = (os.environ.get("CLAUDE_WEBSEARCH_TOOL") or "web_search_20250305")
MAX_SUCHEN = int((os.environ.get("CLAUDE_MAX_SUCHEN_PRO_SPIEL") or "6"))
INTERVALL_TAGE = float((os.environ.get("CHECK_INTERVALL_TAGE") or "7"))
CHECK_STUNDE = int((os.environ.get("CHECK_STUNDE") or "6"))

_lock = threading.Lock()
status = {"laeuft": False, "letzte_meldung": None}

PROMPT = """Du pflegst einen deutschen Release-Kalender für Sammelkartenspiele (TCG), gedacht für einen Händler in Deutschland.
Heute ist der {heute}. Spiel: {spiel_name} (ID: {spiel_id}).

Recherchiere per Websuche, welche Produkte für dieses Spiel angekündigt sind, die ab heute bis ca. 15 Monate in der Zukunft erscheinen, und ob sich bei bekannten Terminen etwas geändert hat.
Relevant ist der Erscheinungstermin für Deutschland/EU. Gibt es keine eigene EU-Angabe, nimm den Termin der englischen Version und vermerke das in "notiz". Bevorzuge offizielle Herstellerseiten und Distributoren; Fan-Seiten nur ergänzend.

Bereits im Kalender (JSON):
{bestand}

Gib am Ende GENAU EINEN ```json-Codeblock mit einer Liste von Vorschlägen aus. Leere Liste, wenn nichts Neues.
Jedes Element:
{{
  "aktion": "neu" | "aenderung",
  "release_id": <ID aus dem Bestand bei "aenderung", sonst null>,
  "daten": {{
    "spiel": "{spiel_id}",
    "name": "Set-Name (englisch bzw. offizieller Name)",
    "code": "Set-Code oder null",
    "typ": "hauptset" | "spezialset" | "deck" | "sonstiges",
    "datum": "YYYY-MM-DD (bei Monat/Quartal der erste Tag des Zeitraums)",
    "genauigkeit": "tag" | "monat" | "quartal",
    "prerelease": "YYYY-MM-DD oder null",
    "status": "geruecht" | "angekuendigt" | "bestaetigt" | "verschoben" | "abgesagt",
    "notiz": "kurze Notiz auf Deutsch oder null"
  }},
  "quelle": "URL der besten Quelle – wenn möglich die offizielle Seite des Herstellers",
  "begruendung": "ein Satz auf Deutsch, was neu ist bzw. was sich geändert hat"
}}
Regeln:
- Bei "aenderung" enthält "daten" nur die Felder, die sich ändern.
- "bestaetigt" nur bei offiziell genanntem Tagesdatum; "angekuendigt" bei offizieller Ankündigung ohne festes Datum oder nur aus Händlerlisten; "geruecht" bei Leaks/Markenanmeldungen.
- Keine Vorschläge für Dinge, die im Bestand schon korrekt stehen. Keine Einzelpromos, Sleeves oder Zubehör; Booster-Sets, Spezialsets, wichtige Decks/Boxen ja.
"""


def aktiv() -> bool:
    return bool(API_KEY)


def _bestand(spiel_id: str) -> str:
    von = (date.today() - timedelta(days=30)).isoformat()
    rows = db.releases(spiele_filter=[spiel_id], von=von)
    kurz = [{k: r[k] for k in ("id", "name", "code", "typ", "datum", "genauigkeit", "prerelease", "status")}
            for r in rows]
    return json.dumps(kurz, ensure_ascii=False, indent=1)


def _json_block(text: str):
    bloecke = re.findall(r"```json\s*(.*?)```", text, re.S)
    if not bloecke:
        raise ValueError("Kein JSON-Block in der Antwort")
    return json.loads(bloecke[-1])


def _anfrage(client, prompt: str) -> str:
    nachrichten = [{"role": "user", "content": prompt}]
    tools = [{"type": WEBSEARCH_TOOL, "name": "web_search", "max_uses": MAX_SUCHEN}]
    if WEBSEARCH_TOOL >= "web_search_20260209":
        tools[0]["allowed_callers"] = ["direct"]
    for _ in range(5):
        antwort = client.messages.create(model=MODELL, max_tokens=8000,
                                         messages=nachrichten, tools=tools)
        if antwort.stop_reason == "pause_turn":
            nachrichten.append({"role": "assistant", "content": antwort.content})
            continue
        return "".join(b.text for b in antwort.content if b.type == "text")
    raise RuntimeError("Antwort nach mehreren Fortsetzungen nicht abgeschlossen")


def vorschlaege_einspielen(liste: list, herkunft: str) -> tuple[int, list[str]]:
    """Validiert eine Vorschlagsliste (Format wie oben) und legt sie an."""
    angelegt, fehler = 0, []
    spiel_ids = {s["id"] for s in db.spiele()}
    for i, v in enumerate(liste):
        try:
            aktion = v["aktion"]
            daten = v["daten"]
            rid = v.get("release_id")
            if aktion == "neu":
                if daten.get("spiel") not in spiel_ids:
                    raise ValueError(f"Unbekanntes Spiel: {daten.get('spiel')}")
                db._bereinigen(daten)
                for pflicht in ("name", "datum"):
                    if not daten.get(pflicht):
                        raise ValueError(f"Feld fehlt: {pflicht}")
            else:
                if not rid or not db.release(int(rid)):
                    raise ValueError(f"release_id {rid} existiert nicht")
                daten.pop("spiel", None)
                db._bereinigen(daten)
            vid = db.vorschlag_anlegen(aktion, daten, int(rid) if rid else None,
                                       v.get("begruendung"), v.get("quelle"), herkunft)
            if vid:
                angelegt += 1
                if herkunft.startswith("claude") and einstellungen.automatisch_uebernehmen(aktion, daten, v.get("quelle")):
                    try:
                        db.vorschlag_entscheiden(vid, True)
                        status["automatisch"] = status.get("automatisch", 0) + 1
                    except Exception as e:  # noqa: BLE001
                        log.warning("Automatische Übernahme von Vorschlag %s fehlgeschlagen: %s", vid, e)
        except Exception as e:  # noqa: BLE001
            fehler.append(f"Eintrag {i + 1}: {e}")
    return angelegt, fehler


def pruefen(nur_spiel: str | None = None) -> str:
    if not aktiv():
        return "Kein ANTHROPIC_API_KEY gesetzt – automatische Prüfung ist deaktiviert."
    if not _lock.acquire(blocking=False):
        return "Prüfung läuft bereits."
    status["laeuft"] = True
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=API_KEY)
        gesamt, meldungen = 0, []
        status["automatisch"] = 0
        from . import quellen
        per_api = quellen.spiele_mit_quelle()
        for s in db.spiele():
            if nur_spiel and s["id"] != nur_spiel:
                continue
            if not nur_spiel and s["id"] in per_api:
                continue  # kommt bereits über eine Daten-API
            try:
                text = _anfrage(client, PROMPT.format(
                    heute=date.today().isoformat(), spiel_name=s["name"],
                    spiel_id=s["id"], bestand=_bestand(s["id"])))
                n, fehler = vorschlaege_einspielen(_json_block(text), f"claude:{MODELL}")
                gesamt += n
                if fehler:
                    meldungen.append(f"{s['name']}: {len(fehler)} ungültige Einträge verworfen")
            except Exception as e:  # noqa: BLE001
                log.exception("Prüfung für %s fehlgeschlagen", s["id"])
                meldungen.append(f"{s['name']}: Fehler – {e}")
        db.meta_set("letzte_pruefung", datetime.now(timezone.utc).isoformat())
        auto = status.get("automatisch", 0)
        msg = f"{gesamt} neue Vorschläge" + (f", davon {auto} automatisch übernommen." if auto else ".") \
            + (" " + " | ".join(meldungen) if meldungen else "")
        status["letzte_meldung"] = msg
        return msg
    finally:
        status["laeuft"] = False
        _lock.release()


def _faellig() -> bool:
    letzte = db.meta_get("letzte_pruefung")
    jetzt = datetime.now(timezone.utc)
    if datetime.now().hour < CHECK_STUNDE:
        return False
    if not letzte:
        return True
    return jetzt - datetime.fromisoformat(letzte) >= timedelta(days=INTERVALL_TAGE) - timedelta(hours=1)


def starte_hintergrund():
    if not aktiv() or INTERVALL_TAGE <= 0:
        log.info("Automatische Prüfung deaktiviert.")
        return

    def schleife():
        time.sleep(30)
        while True:
            try:
                if _faellig():
                    log.info("Starte geplante Prüfung …")
                    log.info(pruefen())
            except Exception:  # noqa: BLE001
                log.exception("Geplante Prüfung fehlgeschlagen")
            time.sleep(3600)

    threading.Thread(target=schleife, daemon=True, name="checker").start()
