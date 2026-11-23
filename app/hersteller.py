"""Offizielle Herstellerseiten je Termin.

Angezeigt wird, in dieser Reihenfolge: die in der Verwaltung eingetragene Seite, eine automatisch
gefundene Produktseite des Herstellers, die Quelle des Termins, wenn sie auf einer Herstellerseite
liegt, sonst die Produktübersicht des Herstellers. Für die automatische Suche lädt der Container
einmal am Tag die Produktübersichten und ordnet deren Links nur bei eindeutigem Namen zu."""
import html
import logging
import re
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from urllib.parse import unquote, urljoin, urlparse

from . import db

log = logging.getLogger("tcg-kalender.hersteller")

INTERVALL_STUNDEN = 24
# Bei Änderungen an der Suche erhöhen: Dann läuft der Abgleich nach dem Update sofort statt erst nach 24 Stunden
SUCHE_VERSION = "3"
ZEITRAUM_TAGE = 180  # nur für kommende und kürzlich erschienene Sets suchen
_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

# spiel: (Herstellerseite, Produktübersicht, Domains des Herstellers, durchsuchte Seiten, Pfadmuster für Produktseiten)
HERSTELLER = {
    "pokemon": ("pokemon.com", "https://www.pokemon.com/de/pokemon-sammelkartenspiel", ("pokemon.com",),
                ["https://www.pokemon.com/de/pokemon-sammelkartenspiel"], r"^/de/pokemon-sammelkartenspiel/[a-z0-9-]+/?$"),
    "magic": ("magic.wizards.com", "https://magic.wizards.com/de/products", ("wizards.com",),
              ["https://magic.wizards.com/de/products"], r"^/de/products/"),
    "onepiece": ("onepiece-cardgame.com", "https://en.onepiece-cardgame.com/products/", ("onepiece-cardgame.com",),
                 ["https://en.onepiece-cardgame.com/products/", "https://en.onepiece-cardgame.com/products/?subcategory=boosters",
                  "https://en.onepiece-cardgame.com/products/?subcategory=decks"], r"/products/"),
    "lorcana": ("disneylorcana.com", "https://www.disneylorcana.com/de/", ("disneylorcana.com", "ravensburger.de"), [], None),
    "yugioh": ("yugioh-card.com", "https://www.yugioh-card.com/eu/de/products/", ("yugioh-card.com",),
               ["https://www.yugioh-card.com/eu/de/products/"], r"/product/"),
    "riftbound": ("playriftbound.com", "https://playriftbound.com/de-de/news/", ("playriftbound.com", "leagueoflegends.com"), [], None),
    "swu": ("starwarsunlimited.com", "https://starwarsunlimited.com/de/product-guide", ("starwarsunlimited.com",),
            ["https://starwarsunlimited.com/de/product-guide"], r"/products?/"),
    "gundam": ("gundam-gcg.com", "https://www.gundam-gcg.com/en/products/", ("gundam-gcg.com",),
               ["https://www.gundam-gcg.com/en/products/", "https://www.gundam-gcg.com/en/products/list.php"], r"/products/"),
    "digimon": ("digimoncard.com", "https://world.digimoncard.com/products/", ("digimoncard.com",),
                ["https://world.digimoncard.com/products/"], r"/products/"),
    "dbfw": ("dbs-cardgame.com", "https://www.dbs-cardgame.com/fw/en/products/", ("dbs-cardgame.com",),
             ["https://www.dbs-cardgame.com/fw/en/products/"], r"/products/"),
    "fab": ("fabtcg.com", "https://fabtcg.com/products/", ("fabtcg.com",), ["https://fabtcg.com/products/"], r"/products/"),
    "unionarena": ("unionarena-tcg.com", "https://www.unionarena-tcg.com/na/products/", ("unionarena-tcg.com",),
                   ["https://www.unionarena-tcg.com/na/products/"], r"/products/"),
    "weiss": ("ws-tcg.com", "https://en.ws-tcg.com/products/", ("ws-tcg.com",), ["https://en.ws-tcg.com/products/"], r"/products/"),
    "vanguard": ("cf-vanguard.com", "https://en.cf-vanguard.com/products/", ("cf-vanguard.com",),
                 ["https://en.cf-vanguard.com/products/", "https://en.cf-vanguard.com/products/cat/booster/",
                  "https://en.cf-vanguard.com/products/cat/trial/"], r"/products/"),
    "shadowverse": ("shadowverse-evolve.com", "https://en.shadowverse-evolve.com/products/", ("shadowverse-evolve.com",),
                    ["https://en.shadowverse-evolve.com/products/"], r"/products/"),
    "hololive": ("hololive-official-cardgame.com", "https://en.hololive-official-cardgame.com/products/",
                 ("hololive-official-cardgame.com",), ["https://en.hololive-official-cardgame.com/products/"], r"/products/"),
    "fftcg": ("fftcg.square-enix-games.com", "https://fftcg.square-enix-games.com/en", ("square-enix-games.com",), [], None),
    "sorcery": ("sorcerytcg.com", "https://sorcerytcg.com/", ("sorcerytcg.com",), [], None),
    "grandarchive": ("gatcg.com", "https://www.gatcg.com/", ("gatcg.com",), [], None),
    "cyberpunk": ("cyberpunktcg.com", "https://cyberpunktcg.com/", ("cyberpunktcg.com",), [], None),
}

# Hersteller, deren Produktseiten den Set-Code in der Adresse tragen (OP-18 -> op18.html). Solche Seiten werden
# nur übernommen, wenn der Seitentitel den Code enthält – so fallen auch erschienene Sets nicht heraus, die
# nicht mehr in der Übersicht stehen.
# (Adresse, Prüfung): Bei "code" muss der Seitentitel den Set-Code enthalten, bei "name" alle Wörter des Namens.
# {code} ist der Set-Code ohne Sonderzeichen, {slug} der englische Name in Kleinbuchstaben mit Bindestrichen.
CODE_VORLAGEN = {
    "onepiece": ("https://en.onepiece-cardgame.com/products/{code}.html", "code"),
    "gundam": ("https://www.gundam-gcg.com/en/products/{code}.html", "code"),
    "shadowverse": ("https://en.shadowverse-evolve.com/products/{code}/", "name"),
    "yugioh": ("https://www.yugioh-card.com/eu/de/product/{slug}/", "name"),
}

# Ableger eines Sets (Commander-Decks, weitere Wellen, Tins, Kollektionen …) bekommen die Seite des Hauptsets,
# wenn sich ihr Name nur um solche Wörter unterscheidet
_ABLEGER = {"commander", "welle", "wave", "premium", "collection", "kollektion", "tin", "tins", "binder", "starter",
            "deck", "decks", "build", "box", "bundle", "display", "ex", "mini", "special", "edition"}

_FUELL = {"the", "of", "and", "a", "der", "die", "das", "und", "von", "set", "tcg", "de", "en", "eu", "na", "fw", "html",
          "php", "www", "com", "products", "product", "produkte", "pokemon", "sammelkartenspiel", "magic", "news"}
_ZUSATZ = re.compile(r"\((?:welle|wave)[^)]*\)", re.I)

_lock = threading.Lock()
status = {"laeuft": False, "meldung": None}


def _woerter(text: str) -> set[str]:
    t = unquote(text or "").lower().replace("&amp;", "&")
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"), ("é", "e")):
        t = t.replace(a, b)
    return {w for w in re.findall(r"[a-z0-9]+", t) if w not in _FUELL}


def _auf_domain(url: str | None, domains) -> bool:
    try:
        p = urlparse(url or "")
    except ValueError:
        return False
    host = (p.hostname or "").lower()
    return p.scheme == "https" and any(host == d or host.endswith("." + d) for d in domains)


def _laden(url: str, versuche: int = 3) -> tuple[str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"})
    letzter = None
    for versuch in range(versuche):  # manche Seiten lehnen einzelne Abrufe ab und lassen den nächsten durch
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.geturl(), r.read(4_000_000).decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            letzter = e
            if versuch + 1 < versuche:
                time.sleep(3 + versuch * 3)
    raise letzter


def _code(code: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (code or "").lower())


def _slug(name: str | None) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", (name or "").lower().replace("'", "").replace("’", "")))


def per_code(spiel: str, r: dict) -> str | None:
    """Produktseite über Set-Code oder Namen finden; nur mit passendem Seitentitel."""
    if spiel not in CODE_VORLAGEN:
        return None
    vorlage, pruefung = CODE_VORLAGEN[spiel]
    code, slug = _code(r.get("code")), _slug(r.get("name"))
    if "{code}" in vorlage and not re.fullmatch(r"[a-z]{1,4}\d{1,3}", code or ""):
        return None
    if "{slug}" in vorlage and not (2 <= len(slug) <= 80):
        return None
    url = vorlage.format(code=code, slug=slug)
    try:
        ziel, text = _laden(url, versuche=1)
    except Exception:  # noqa: BLE001  – 404: gibt es (noch) nicht
        return None
    titel = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
    if urlparse(ziel).path != urlparse(url).path or not titel:
        return None
    titel = html.unescape(titel.group(1))
    if pruefung == "code" and code not in _code(titel):
        return None
    if pruefung == "name" and not (_woerter(r.get("name")) and _woerter(r.get("name")) <= _woerter(titel)):
        return None
    return url


def ableger(releases: list[dict], links: dict[int, str]) -> dict[int, str]:
    """Termine ohne Seite, deren Name aus dem Namen eines Sets mit Seite plus Ableger-Wörtern besteht."""
    basis = [(_woerter(r["name"]), links[r["id"]]) for r in releases if r["id"] in links]
    ergebnis = {}
    for r in releases:
        if r["id"] in links or r.get("offiziell"):
            continue
        nw = _woerter(r["name"])
        passend = [(len(bw), url) for bw, url in basis
                   if len(bw) >= 1 and bw < nw and all(w in _ABLEGER or w.isdigit() for w in nw - bw)]
        if passend:
            groesste = max(p[0] for p in passend)
            urls = {u for n, u in passend if n == groesste}
            if len(urls) == 1:
                ergebnis[r["id"]] = urls.pop()
    return ergebnis


def kandidaten(seite: str, basis: str, domains, muster: str) -> dict[str, dict]:
    """Links auf Produktseiten des Herstellers mit den Wörtern aus Pfad und Linktext."""
    ergebnis: dict[str, dict] = {}
    start = urlparse(basis).path.rstrip("/")
    for href, inner in re.findall(r'<a\b[^>]*?href="([^"#]+)"[^>]*>(.*?)</a>', seite, re.S | re.I):
        url = urljoin(basis, html.unescape(href))
        p = urlparse(url)
        if not _auf_domain(url, domains) or p.path.rstrip("/") in ("", start) or not re.search(muster, p.path):
            continue
        url = url.split("?")[0]
        k = ergebnis.setdefault(url, {"pfad": _woerter(p.path), "texte": []})
        text = _woerter(html.unescape(re.sub(r"<[^>]+>", " ", inner)))
        if text:
            k["texte"].append(text)
    return ergebnis


def zuordnen(releases: list[dict], kand: dict[str, dict]) -> dict[int, str]:
    """Nur eindeutige Treffer: Alle Wörter des Set-Namens stehen in der Adresse oder im Linktext (dort höchstens
    40 weitere Wörter, bei einwortigen Namen 4), und jeder Link gehört nur zum am besten passenden Termin."""
    beste: dict[int, tuple[int, str]] = {}
    for r in releases:
        treffer = None
        for name in filter(None, (r.get("name_de"), r.get("name"))):
            nw = _woerter(_ZUSATZ.sub("", name))
            if not nw:
                continue
            for url, k in kand.items():
                werte = [len(k["pfad"] - nw)] if nw <= k["pfad"] else []
                werte += [len(t - nw) for t in k["texte"] if nw <= t and len(t - nw) <= (40 if len(nw) >= 2 else 4)]
                if werte:
                    wert = min(werte)
                    if treffer is None or wert < treffer[0]:
                        treffer = (wert, url, False)
                    elif wert == treffer[0] and url != treffer[1]:
                        treffer = (wert, treffer[1], True)  # gleich gut zu zwei Seiten: offen lassen
        if treffer and not treffer[2]:
            beste[r["id"]] = (treffer[0], treffer[1])
    je_url: dict[str, int] = {}
    for wert, url in beste.values():
        je_url[url] = min(wert, je_url.get(url, wert))
    ergebnis = {}
    for rid, (wert, url) in beste.items():
        if je_url[url] == wert and sum(1 for w, u in beste.values() if u == url and w == wert) == 1:
            ergebnis[rid] = url
    return ergebnis


def abgleichen() -> str:
    if not _lock.acquire(blocking=False):
        return "Läuft bereits."
    status["laeuft"] = True
    try:
        von = (date.today() - timedelta(days=ZEITRAUM_TAGE)).isoformat()
        gefunden = fehler = 0
        for spiel, (_, _, domains, seiten, muster) in HERSTELLER.items():
            rels = [r for r in db.releases(spiele_filter=[spiel], von=von)]
            if not rels or not (seiten or spiel in CODE_VORLAGEN):
                continue
            kand: dict[str, dict] = {}
            for s in seiten:
                try:
                    basis, text = _laden(s)
                    for url, k in kandidaten(text, basis, domains, muster).items():
                        ziel = kand.setdefault(url, {"pfad": k["pfad"], "texte": []})
                        ziel["texte"] += k["texte"]
                except Exception as e:  # noqa: BLE001
                    fehler += 1
                    log.info("Herstellerseite %s nicht abrufbar: %s", s, e)
                time.sleep(2)
            neu = zuordnen(rels, kand)
            if spiel in CODE_VORLAGEN:
                bekannt = set(gefundene())
                for r in rels:
                    if r["id"] not in neu and r["id"] not in bekannt and not r.get("offiziell") and r.get("code"):
                        url = per_code(spiel, r)
                        if url:
                            neu[r["id"]] = url
                        time.sleep(1)
            neu.update(ableger(rels, {**gefundene_spiel(spiel), **neu}))
            jetzt = db.jetzt()
            with db.verbindung() as con:
                # Gefundene Links bleiben, auch wenn das Produkt später von der Übersicht verschwindet
                con.executemany("INSERT INTO hersteller_links (release_id, url, gefunden) VALUES (?,?,?) "
                                "ON CONFLICT(release_id) DO UPDATE SET url = excluded.url, gefunden = excluded.gefunden",
                                [(rid, url, jetzt) for rid, url in neu.items()])
            gefunden += len(neu)
        with db.verbindung() as con:
            con.execute("DELETE FROM hersteller_links WHERE release_id NOT IN (SELECT id FROM releases)")
            gesamt = con.execute("SELECT COUNT(*) FROM hersteller_links").fetchone()[0]
        msg = f"{gesamt} Produktseiten zugeordnet ({gefunden} in diesem Lauf)" + (f", {fehler} Seiten nicht abrufbar" if fehler else "")
        db.meta_set("hersteller_stand", datetime.now(timezone.utc).isoformat())
        db.meta_set("hersteller_version", SUCHE_VERSION)
        status["meldung"] = msg
        log.info("Herstellerseiten: %s", msg)
        return msg
    finally:
        status["laeuft"] = False
        _lock.release()


def gefundene_spiel(spiel: str) -> dict[int, str]:
    with db.verbindung() as con:
        return {r["release_id"]: r["url"] for r in con.execute(
            "SELECT h.release_id, h.url FROM hersteller_links h JOIN releases r ON r.id = h.release_id WHERE r.spiel = ?", (spiel,))}


def gefundene() -> dict[int, str]:
    with db.verbindung() as con:
        return {r["release_id"]: r["url"] for r in con.execute("SELECT release_id, url FROM hersteller_links")}


def link(r: dict, gefunden: dict[int, str] | None = None) -> dict | None:
    """Offizielle Seite eines Termins für die öffentliche Ansicht."""
    h = HERSTELLER.get(r.get("spiel"))
    if r.get("offiziell"):
        return {"url": r["offiziell"], "exakt": True}
    if h is None:
        return None
    name, uebersicht, domains, _, _ = h
    url = (gefunden if gefunden is not None else gefundene()).get(r.get("id"))
    if url:
        return {"url": url, "exakt": True}
    if _auf_domain(r.get("quelle"), domains):
        return {"url": r["quelle"], "exakt": True}
    return {"url": uebersicht, "exakt": False, "hersteller": name}


def starte_hintergrund():
    def schleife():
        time.sleep(150)
        while True:
            try:
                letzte = db.meta_get("hersteller_stand")
                if not letzte or db.meta_get("hersteller_version") != SUCHE_VERSION \
                        or datetime.now(timezone.utc) - datetime.fromisoformat(letzte) >= timedelta(hours=INTERVALL_STUNDEN):
                    abgleichen()
            except Exception:  # noqa: BLE001
                log.exception("Abgleich der Herstellerseiten fehlgeschlagen")
            time.sleep(3600)
    threading.Thread(target=schleife, daemon=True, name="hersteller").start()
