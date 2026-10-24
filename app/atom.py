"""Atom-Feed des Änderungsprotokolls (für Feedreader, Discord-Bots, Automationen)."""
from xml.sax.saxutils import escape

from . import notify

ART_TEXT = notify.ART_TEXT


def feed(eintraege: list[dict], basis_url: str, titel: str, selbst_url: str) -> str:
    aktualisiert = eintraege[0]["zeit"] if eintraege else "2026-01-01T00:00:00Z"
    teile = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="de">',
        f"<title>{escape(titel)}</title>",
        f'<link href="{escape(basis_url)}/"/>',
        f'<link rel="self" href="{escape(selbst_url)}"/>',
        f"<id>{escape(basis_url)}/feed.xml</id>",
        f"<updated>{aktualisiert}</updated>",
    ]
    for e in eintraege:
        titelzeile = f"[{e.get('spiel_name') or e['spiel']}] {ART_TEXT.get(e['art'], e['art'])}: {e['name']}"
        link = f"{basis_url}/?r={e['release_id']}" if e.get("release_id") and e["art"] != "geloescht" else f"{basis_url}/"
        teile += [
            "<entry>",
            f"<title>{escape(titelzeile)}</title>",
            f'<link href="{escape(link)}"/>',
            f"<id>{escape(basis_url)}/aenderung/{e['id']}</id>",
            f"<updated>{e['zeit']}</updated>",
            f"<category term=\"{escape(e['spiel'])}\" label=\"{escape(e.get('spiel_name') or e['spiel'])}\"/>",
            f'<content type="text">{escape(notify.beschreibung(e))}</content>',
            "</entry>",
        ]
    teile.append("</feed>")
    return "\n".join(teile)
