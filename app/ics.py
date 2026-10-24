"""Erzeugt iCalendar-Feeds (RFC 5545) ohne externe Abhängigkeiten."""
from datetime import date, timedelta

from . import db

MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
          "September", "Oktober", "November", "Dezember"]


def _esc(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _falten(zeile: str) -> str:
    """Zeilen auf max. 75 Oktette falten, ohne UTF-8-Zeichen zu zerschneiden."""
    roh = zeile.encode("utf-8")
    if len(roh) <= 75:
        return zeile
    teile, aktuell, laenge, limit = [], "", 0, 75
    for z in zeile:
        b = len(z.encode("utf-8"))
        if laenge + b > limit:
            teile.append(aktuell)
            aktuell, laenge, limit = z, b, 74  # Folgezeilen beginnen mit Leerzeichen
        else:
            aktuell += z
            laenge += b
    teile.append(aktuell)
    return "\r\n ".join(teile)


def zeitraum_text(r: dict) -> str:
    d = date.fromisoformat(r["datum"])
    if r["genauigkeit"] == "monat":
        return f"ca. {MONATE[d.month - 1]} {d.year}"
    if r["genauigkeit"] == "quartal":
        return f"ca. Q{(d.month - 1) // 3 + 1} {d.year}"
    return d.strftime("%d.%m.%Y")


def _stamp(ts: str) -> str:
    return ts.replace("-", "").replace(":", "")


def _event(uid, start: str, summary, beschreibung, url, r, host) -> list[str]:
    d = date.fromisoformat(start)
    zeilen = [
        "BEGIN:VEVENT",
        f"UID:{uid}@{host}",
        f"DTSTAMP:{_stamp(r['geaendert'])}",
        f"LAST-MODIFIED:{_stamp(r['geaendert'])}",
        f"SEQUENCE:{r['sequenz']}",
        f"DTSTART;VALUE=DATE:{d.strftime('%Y%m%d')}",
        f"DTEND;VALUE=DATE:{(d + timedelta(days=1)).strftime('%Y%m%d')}",
        f"SUMMARY:{_esc(summary)}",
        f"DESCRIPTION:{_esc(beschreibung)}",
        f"CATEGORIES:{_esc(r['spiel_name'])}",
        "TRANSP:TRANSPARENT",
    ]
    if r["status"] == "abgesagt":
        zeilen.append("STATUS:CANCELLED")
    elif r["status"] in ("geruecht", "angekuendigt") or r["genauigkeit"] != "tag":
        zeilen.append("STATUS:TENTATIVE")
    else:
        zeilen.append("STATUS:CONFIRMED")
    if url:
        zeilen.append(f"URL:{url}")
    zeilen.append("END:VEVENT")
    return zeilen


def feed(rows: list[dict], titel: str, host: str, mit_prerelease: bool, basis_url: str = "") -> str:
    zeilen = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//tcg-kalender//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_esc(titel)}",
        "X-WR-TIMEZONE:Europe/Berlin",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
    ]
    for r in rows:
        status_txt = db.STATUS_FILTER[r["status_effektiv"]]
        name = r["name"] + (f" ({r['code']})" if r.get("code") else "")
        praefix = ""
        if r["status"] == "geruecht":
            praefix = "Gerücht: "
        elif r["status"] == "abgesagt":
            praefix = "Abgesagt: "
        elif r["status"] == "verschoben":
            praefix = "Verschoben: "
        if r["genauigkeit"] != "tag":
            praefix += f"[{zeitraum_text(r)}] "
        summary = f"[{r['spiel_name']}] {praefix}{name}"

        teile = [
            f"Spiel: {r['spiel_name']}",
            f"Typ: {db.TYPEN.get(r['typ'], r['typ'])}",
            f"Status: {status_txt}",
            f"Termin: {zeitraum_text(r)}",
        ]
        if r.get("prerelease"):
            teile.append(f"Prerelease: {date.fromisoformat(r['prerelease']).strftime('%d.%m.%Y')}")
        if r.get("vorheriges_datum") and r["status"] == "verschoben":
            teile.append(f"Vorher geplant: {date.fromisoformat(r['vorheriges_datum']).strftime('%d.%m.%Y')}")
        if r.get("notiz"):
            teile.append("")
            teile.append(r["notiz"])
        if r.get("quelle"):
            teile.append("")
            teile.append(f"Quelle: {r['quelle']}")
        if basis_url:
            teile.append(f"Details: {basis_url}/?r={r['id']}")
        beschreibung = "\n".join(teile)

        zeilen += _event(f"tcg-{r['id']}", r["datum"], summary, beschreibung, r.get("quelle"), r, host)
        if mit_prerelease and r.get("prerelease"):
            zeilen += _event(f"tcg-pre-{r['id']}", r["prerelease"],
                             f"[{r['spiel_name']}] Prerelease: {name}", beschreibung,
                             r.get("quelle"), r, host)
    zeilen.append("END:VCALENDAR")
    return "\r\n".join(_falten(z) for z in zeilen) + "\r\n"
