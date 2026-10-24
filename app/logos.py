"""Vom Betreiber hochgeladene Spiel-Logos. Sie liegen nur im Datenverzeichnis, nicht im Image."""
import hashlib
import os
import re

from . import db

MAX_GROESSE = 1024 * 1024  # 1 MB

TYPEN = {
    "image/png": "png",
    "image/webp": "webp",
    "image/jpeg": "jpg",
    "image/svg+xml": "svg",
}
MEDIENTYP = {v: k for k, v in TYPEN.items()}

_DATEINAME = re.compile(r"^[a-z0-9_-]+-[0-9a-f]{12}\.(png|webp|jpg|svg)$")
_SVG_VERBOTEN = re.compile(rb"<script|javascript:|\bon[a-z]+\s*=|<foreignObject|<iframe|<embed|<object", re.I)


def verzeichnis() -> str:
    pfad = os.path.join(os.path.dirname(db.DB_PFAD) or ".", "logos")
    os.makedirs(pfad, exist_ok=True)
    return pfad


def _typ_pruefen(inhalt: bytes, medientyp: str) -> str:
    medientyp = (medientyp or "").split(";")[0].strip().lower()
    if medientyp not in TYPEN:
        raise ValueError("Erlaubt sind PNG, WebP, JPEG und SVG")
    if not inhalt:
        raise ValueError("Die Datei ist leer")
    if len(inhalt) > MAX_GROESSE:
        raise ValueError("Die Datei ist größer als 1 MB")
    endung = TYPEN[medientyp]
    if endung == "png" and not inhalt.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Die Datei ist kein gültiges PNG")
    if endung == "jpg" and not inhalt.startswith(b"\xff\xd8\xff"):
        raise ValueError("Die Datei ist kein gültiges JPEG")
    if endung == "webp" and not (inhalt[:4] == b"RIFF" and inhalt[8:12] == b"WEBP"):
        raise ValueError("Die Datei ist kein gültiges WebP")
    if endung == "svg":
        kopf = inhalt[:2048].lstrip()
        if b"<svg" not in inhalt[:4096] or not (kopf.startswith(b"<") or kopf.startswith(b"\xef\xbb\xbf<")):
            raise ValueError("Die Datei ist kein gültiges SVG")
        if _SVG_VERBOTEN.search(inhalt):
            raise ValueError("Das SVG enthält Skripte oder eingebettete Inhalte und wird nicht angenommen")
    return endung


def speichern(spiel_id: str, inhalt: bytes, medientyp: str) -> str:
    endung = _typ_pruefen(inhalt, medientyp)
    datei = f"{spiel_id}-{hashlib.sha256(inhalt).hexdigest()[:12]}.{endung}"
    ziel = os.path.join(verzeichnis(), datei)
    with open(ziel + ".tmp", "wb") as f:
        f.write(inhalt)
    os.replace(ziel + ".tmp", ziel)
    try:
        alt = db.spiel_logo_setzen(spiel_id, datei)
    except KeyError:
        os.remove(ziel)
        raise
    if alt and alt != datei:
        entfernen_datei(alt)
    return datei


def entfernen(spiel_id: str):
    alt = db.spiel_logo_setzen(spiel_id, None)
    if alt:
        entfernen_datei(alt)


def entfernen_datei(datei: str | None):
    if datei and _DATEINAME.match(datei):
        try:
            os.remove(os.path.join(verzeichnis(), datei))
        except FileNotFoundError:
            pass


def pfad(datei: str) -> str | None:
    """Gibt den Pfad zu einer Logo-Datei zurück oder None, wenn der Name ungültig ist oder fehlt."""
    if not _DATEINAME.match(datei or ""):
        return None
    p = os.path.join(verzeichnis(), datei)
    return p if os.path.isfile(p) else None
