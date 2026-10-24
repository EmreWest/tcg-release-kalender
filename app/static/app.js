"use strict";
// Öffentliche Seite: Liste, Monat, Woche, Änderungen, Detailansicht, Filter und Abo-Link.

const WT_KURZ = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];
const WT_LANG = ["Sonntag", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"];
const MON = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"];
const STANDARD_STATUS = ["geruecht", "angekuendigt", "bestaetigt", "verschoben", "erschienen"];
const ANSICHTEN = ["liste", "monat", "woche", "aenderungen"];
const ART_TEXT = { neu: "Neu", verschoben: "Termin geändert", geaendert: "Aktualisiert", geloescht: "Entfernt" };

let META = null;
let DATEN = [];
let AENDERUNGEN = null;
let VORHER_BESUCH = null;

const zustand = {
  ansicht: "liste",
  spiele: new Set(), status: new Set(), typ: new Set(),
  prerelease: false, ungenau: true, vergangen: false, suche: "",
  monat: null,      // Date, 1. des Monats
  woche: null,      // Date, Montag
  tag: null,        // ISO-Datum des gewählten Tags in der Monatsansicht
};

// --- Hilfen -------------------------------------------------------------------
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const farbe = (f) => (/^#[0-9a-f]{6}$/i.test(f || "") ? f : "#888888");
function iso(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }
function parse(s) { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); }
function plusTage(d, n) { const x = new Date(d); x.setDate(x.getDate() + n); return x; }
function montag(d) { const x = new Date(d.getFullYear(), d.getMonth(), d.getDate()); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x; }
function heute() { const d = new Date(); return new Date(d.getFullYear(), d.getMonth(), d.getDate()); }
function tageBis(isoDatum) { return Math.round((parse(isoDatum) - heute()) / 86400000); }
function kw(d) {
  const t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  t.setUTCDate(t.getUTCDate() + 4 - (t.getUTCDay() || 7));
  const jahrStart = new Date(Date.UTC(t.getUTCFullYear(), 0, 1));
  return Math.ceil(((t - jahrStart) / 86400000 + 1) / 7);
}
function fmt(isoDatum, mitWt = false) {
  const d = parse(isoDatum);
  const s = d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" });
  return mitWt ? `${WT_KURZ[d.getDay()]}, ${s}` : s;
}
function zeitraum(r) {
  const d = parse(r.datum);
  if (r.genauigkeit === "monat") return `ca. ${MON[d.getMonth()]} ${d.getFullYear()}`;
  if (r.genauigkeit === "quartal") return `ca. Q${Math.floor(d.getMonth() / 3) + 1} ${d.getFullYear()}`;
  return null;
}
function countdown(isoDatum) {
  const n = tageBis(isoDatum);
  if (n === 0) return "heute";
  if (n === 1) return "morgen";
  if (n === -1) return "gestern";
  if (n > 1) return n < 60 ? `in ${n} Tagen` : `in ${Math.round(n / 7)} Wochen`;
  return `vor ${-n} Tagen`;
}
function toast(t) {
  const el = $("#toast"); el.textContent = t; el.classList.add("an");
  clearTimeout(toast.t); toast.t = setTimeout(() => el.classList.remove("an"), 1900);
}
async function kopieren(text, meldung) {
  try { await navigator.clipboard.writeText(text); toast(meldung); }
  catch (e) { window.prompt("Zum Kopieren markieren:", text); }
}
function speicherLesen(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
function speicherSchreiben(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }

function markierung(r) {
  if (!VORHER_BESUCH) return "";
  if (r.erstellt > VORHER_BESUCH) return `<span class="badge neu">Neu</span>`;
  if (r.sequenz > 0 && r.geaendert > VORHER_BESUCH) return `<span class="badge geaendert">Geändert</span>`;
  return "";
}

// --- Filter -------------------------------------------------------------------
function passt(r) {
  if (!zustand.spiele.has(r.spiel) || !zustand.status.has(r.status_effektiv) || !zustand.typ.has(r.typ)) return false;
  if (!zustand.ungenau && r.genauigkeit !== "tag") return false;
  if (zustand.suche) {
    const q = zustand.suche.toLowerCase();
    if (!`${r.name} ${r.code || ""} ${r.spiel_name}`.toLowerCase().includes(q)) return false;
  }
  return true;
}

// Termine als Kalendereinträge: Release-Tag und optional Prerelease-Tag
function eintraege() {
  const out = [];
  for (const r of DATEN) {
    if (!passt(r)) continue;
    if (r.genauigkeit === "tag") out.push({ r, tag: r.datum, pre: false });
    if (zustand.prerelease && r.prerelease) out.push({ r, tag: r.prerelease, pre: true });
  }
  return out;
}
function nachTag(liste) {
  const m = new Map();
  for (const e of liste) { if (!m.has(e.tag)) m.set(e.tag, []); m.get(e.tag).push(e); }
  return m;
}

// Logo eines Spiels als <img>, sofern in der Verwaltung eines hochgeladen wurde
function logoUrl(spielId) {
  const sp = META.spiele.find((x) => x.id === spielId);
  return sp && sp.logo && /^\/logos\/[a-z0-9_-]+-[0-9a-f]{12}\.(png|webp|jpg|svg)$/.test(sp.logo) ? sp.logo : null;
}
function logo(spielId, klasse) {
  const url = logoUrl(spielId);
  return url ? `<img class="logo ${klasse}" src="${url}" alt="" loading="lazy" decoding="async">` : "";
}

function chip(gruppe, wert, label, f, mitLogo) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "chip";
  b.setAttribute("aria-pressed", zustand[gruppe].has(wert));
  if (f) b.style.setProperty("--c", farbe(f));
  const bild = mitLogo ? logo(wert, "logo-chip") : "";
  b.innerHTML = (bild || (f ? `<span class="punkt"></span>` : "")) + esc(label);
  b.onclick = () => {
    zustand[gruppe].has(wert) ? zustand[gruppe].delete(wert) : zustand[gruppe].add(wert);
    b.setAttribute("aria-pressed", zustand[gruppe].has(wert));
    aktualisieren();
  };
  return b;
}
function filterBauen() {
  const fs = $("#f-spiele"); fs.innerHTML = "";
  META.spiele.forEach((s) => fs.append(chip("spiele", s.id, s.name, s.farbe, true)));
  const st = $("#f-status"); st.innerHTML = "";
  Object.entries(META.status).forEach(([k, v]) => st.append(chip("status", k, v)));
  const ty = $("#f-typ"); ty.innerHTML = "";
  Object.entries(META.typen).forEach(([k, v]) => ty.append(chip("typ", k, v)));
  $("#f-prerelease").checked = zustand.prerelease;
  $("#f-ungenau").checked = zustand.ungenau;
  $("#f-vergangen").checked = zustand.vergangen;
  $("#f-suche").value = zustand.suche;
}

// --- URL und gespeicherte Einstellungen ------------------------------------------
function alleIds(gruppe) {
  if (gruppe === "spiele") return META.spiele.map((s) => s.id);
  if (gruppe === "status") return Object.keys(META.status);
  return Object.keys(META.typen);
}
function filterParams(fuerAbo) {
  const p = new URLSearchParams();
  for (const g of ["spiele", "status", "typ"]) {
    const alle = alleIds(g);
    const sel = alle.filter((x) => zustand[g].has(x));
    if (sel.length !== alle.length) p.set(g, sel.join(","));
  }
  if (zustand.prerelease) p.set("prerelease", "1");
  if (!zustand.ungenau) p.set("ungenau", "0");
  if (!fuerAbo) {
    if (zustand.vergangen) p.set("vergangen", "1");
    if (zustand.suche) p.set("suche", zustand.suche);
    if (zustand.ansicht !== "liste") p.set("ansicht", zustand.ansicht);
    if (zustand.ansicht === "monat" && zustand.monat) p.set("monat", iso(zustand.monat).slice(0, 7));
    if (zustand.ansicht === "woche" && zustand.woche) p.set("woche", iso(zustand.woche));
  }
  return p;
}
const qs = (p) => { const s = p.toString().replace(/%2C/g, ","); return s ? "?" + s : ""; };

function urlSchreiben(detailId) {
  const p = filterParams(false);
  if (detailId) p.set("r", detailId);
  history.replaceState(null, "", location.pathname + qs(p));
}
function einstellungenSpeichern() {
  speicherSchreiben("tcg-filter", JSON.stringify({
    spiele: [...zustand.spiele], status: [...zustand.status], typ: [...zustand.typ],
    prerelease: zustand.prerelease, ungenau: zustand.ungenau, vergangen: zustand.vergangen,
    ansicht: zustand.ansicht, bekannteSpiele: alleIds("spiele"),
  }));
}
function einstellungenLaden() {
  const p = new URLSearchParams(location.search);
  const filterImLink = ["spiele", "status", "typ", "prerelease", "ungenau", "vergangen", "suche"].some((k) => p.has(k));
  let gesp = null;
  try { gesp = JSON.parse(speicherLesen("tcg-filter") || "null"); } catch (e) { gesp = null; }

  const setze = (g, werte) => { zustand[g] = new Set(werte.filter((x) => alleIds(g).includes(x))); };
  if (filterImLink) {
    setze("spiele", p.has("spiele") ? p.get("spiele").split(",") : alleIds("spiele"));
    setze("status", p.has("status") ? p.get("status").split(",") : alleIds("status"));
    setze("typ", p.has("typ") ? p.get("typ").split(",") : alleIds("typ"));
    zustand.prerelease = p.get("prerelease") === "1";
    zustand.ungenau = p.get("ungenau") !== "0";
    zustand.vergangen = p.get("vergangen") === "1";
    zustand.suche = p.get("suche") || "";
  } else if (gesp) {
    // Spiele, die nach dem letzten Besuch neu dazugekommen sind, standardmäßig anzeigen
    const neue = alleIds("spiele").filter((x) => !(gesp.bekannteSpiele || []).includes(x));
    setze("spiele", [...gesp.spiele, ...neue]);
    setze("status", gesp.status);
    setze("typ", gesp.typ);
    zustand.prerelease = !!gesp.prerelease;
    zustand.ungenau = gesp.ungenau !== false;
    zustand.vergangen = !!gesp.vergangen;
  } else {
    setze("spiele", alleIds("spiele"));
    setze("status", STANDARD_STATUS);
    setze("typ", alleIds("typ"));
  }
  const a = p.get("ansicht") || (gesp && gesp.ansicht) || "liste";
  zustand.ansicht = ANSICHTEN.includes(a) ? a : "liste";
  const h = heute();
  zustand.monat = new Date(h.getFullYear(), h.getMonth(), 1);
  if (/^\d{4}-\d{2}$/.test(p.get("monat") || "")) zustand.monat = parse(p.get("monat") + "-01");
  zustand.woche = montag(h);
  if (/^\d{4}-\d{2}-\d{2}$/.test(p.get("woche") || "")) zustand.woche = montag(parse(p.get("woche")));
}

// --- Abo -------------------------------------------------------------------------
function aboAktualisieren() {
  const url = `${location.origin}/kalender.ics${qs(filterParams(true))}`;
  $("#abo-url").value = url;
  const webcal = url.replace(/^https?:/, "webcal:");
  $("#abo-webcal").href = webcal;
  $("#abo-google").href = "https://calendar.google.com/calendar/r?cid=" + encodeURIComponent(webcal);
  const n = zustand.spiele.size, alle = META.spiele.length;
  $("#filter-anz").textContent = n === alle ? "alle Spiele" : `${n} von ${alle} Spielen`;
}

// --- Ansichten -----------------------------------------------------------------------
function pille(e, klasse = "") {
  const r = e.r;
  const unsicher = ["geruecht", "angekuendigt"].includes(r.status) ? " unsicher" : "";
  const abg = r.status === "abgesagt" ? " abgesagt" : "";
  const titel = `${r.spiel_name}: ${e.pre ? "Prerelease " : ""}${r.name}`;
  return `<button type="button" class="pille${e.pre ? " pre" : ""}${unsicher}${abg} ${klasse}" style="--c:${farbe(r.spiel_farbe)}"
    data-r="${r.id}" title="${esc(titel)}"><span>${e.pre ? "Prerelease: " : ""}${esc(r.name)}</span></button>`;
}

function karte(e) {
  const r = e.r, d = parse(e.tag);
  const ca = !e.pre && zeitraum(r);
  const datum = ca
    ? `<div class="datum-block"><div class="ca">${esc(ca.replace(/ \d{4}$/, ""))}</div></div>`
    : `<div class="datum-block"><div class="tag">${String(d.getDate()).padStart(2, "0")}</div><div class="wt">${WT_KURZ[d.getDay()]}</div></div>`;
  const meta = e.pre ? [] : [`<span>${esc(META.typen[r.typ])}</span>`];
  if (!e.pre && r.prerelease) meta.push(`<span>Prerelease ${fmt(r.prerelease)}</span>`);
  if (e.pre) meta.push(`<span>Release ${fmt(r.datum)}</span>`);
  if (r.vorheriges_datum && r.status === "verschoben") meta.push(`<span>vorher ${fmt(r.vorheriges_datum)}</span>`);
  const n = ca ? 99 : tageBis(e.tag);
  return `<button type="button" class="eintrag${r.status_effektiv === "erschienen" && !e.pre ? " vergangen" : ""}" style="--c:${farbe(r.spiel_farbe)}" data-r="${r.id}">
    ${datum}
    <div>
      <div class="titelzeile"><span class="spiel">${logo(r.spiel, "logo-titel")}${esc(r.spiel_name)}</span>
        <span class="badge ${r.status_effektiv}">${esc(META.status[r.status_effektiv])}</span>${markierung(r)}</div>
      <div class="setzeile"><span class="name">${e.pre ? "Prerelease: " : ""}${esc(r.name)}</span>${r.code ? `<span class="code">${esc(r.code)}</span>` : ""}</div>
      <div class="meta">${meta.join("")}</div>
      ${r.notiz && !e.pre ? `<div class="notiz">${esc(r.notiz)}</div>` : ""}
    </div>
    <div class="countdown${n >= 0 && n <= 7 ? " bald" : ""}">${ca ? "" : countdown(e.tag)}</div>
  </button>`;
}

function listeRendern() {
  const grenze = iso(plusTage(heute(), -14));
  const items = [];
  for (const r of DATEN) {
    if (!passt(r)) continue;
    const aktuell = r.genauigkeit !== "tag" || r.datum >= grenze;
    if (aktuell || zustand.vergangen) items.push({ r, tag: r.datum, pre: false });
    if (zustand.prerelease && r.prerelease && (r.prerelease >= grenze || zustand.vergangen)) items.push({ r, tag: r.prerelease, pre: true });
  }
  items.sort((a, b) => a.tag.localeCompare(b.tag) || (a.r.genauigkeit === "tag" ? -1 : 1));

  $("#leiste").innerHTML = `<h2>${zustand.vergangen ? "Alle Releases" : "Kommende Releases"}</h2>
    <span class="zaehler">${items.length} ${items.length === 1 ? "Termin" : "Termine"}</span>`;
  if (!items.length) { $("#inhalt").innerHTML = leer(); return; }

  let html = "", gruppe = "";
  for (const e of items) {
    const d = parse(e.tag);
    const g = !e.pre && e.r.genauigkeit === "quartal" ? `Q${Math.floor(d.getMonth() / 3) + 1} ${d.getFullYear()}` : `${MON[d.getMonth()]} ${d.getFullYear()}`;
    if (g !== gruppe) { html += `<div class="monat-kopf">${g}</div>`; gruppe = g; }
    html += karte(e);
  }
  $("#inhalt").innerHTML = html;
}

function leer() {
  return `<div class="flaeche leer">Für diese Filter gibt es keine Termine.<br>
    <button type="button" class="knopf" id="filter-zurueck">Filter zurücksetzen</button></div>`;
}

function monatRendern() {
  const m = zustand.monat;
  const jahr = m.getFullYear(), mon = m.getMonth();
  $("#leiste").innerHTML = `<h2>${MON[mon]} ${jahr}</h2>
    <div class="nav">
      <button type="button" class="icon-knopf" data-nav="-1" aria-label="Vorheriger Monat">${pfeil("l")}</button>
      <button type="button" class="icon-knopf" data-nav="0">Heute</button>
      <button type="button" class="icon-knopf" data-nav="1" aria-label="Nächster Monat">${pfeil("r")}</button>
    </div>`;
  const tage = nachTag(eintraege());
  const start = montag(m);
  const ende = plusTage(montag(new Date(jahr, mon + 1, 0)), 6);
  const h = iso(heute());
  if (zustand.tag && parse(zustand.tag).getMonth() !== mon) zustand.tag = null;
  if (!zustand.tag && h.slice(0, 7) === iso(m).slice(0, 7)) zustand.tag = h;

  let html = `<div class="monat-raster" role="grid" aria-label="${MON[mon]} ${jahr}">`;
  for (let i = 1; i <= 7; i++) html += `<div class="wt-kopf" role="columnheader">${WT_KURZ[i % 7]}</div>`;
  for (let d = new Date(start); d <= ende; d = plusTage(d, 1)) {
    const t = iso(d);
    const liste = tage.get(t) || [];
    const klassen = ["zelle"];
    if (d.getMonth() !== mon) klassen.push("fremd");
    if (d.getDay() === 0 || d.getDay() === 6) klassen.push("wochenende");
    if (t === h) klassen.push("heute");
    if (t === zustand.tag) klassen.push("gewaehlt");
    const max = 3;
    const label = `${d.getDate()}. ${MON[d.getMonth()]}${liste.length ? `, ${liste.length} ${liste.length === 1 ? "Termin" : "Termine"}` : ""}`;
    html += `<div class="${klassen.join(" ")}" role="gridcell" data-tag="${t}" tabindex="0" aria-label="${label}">
      <span class="nr">${d.getDate()}</span>
      ${liste.slice(0, max).map((e) => pille(e)).join("")}
      ${liste.length > max ? `<span class="mehr">+${liste.length - max} weitere</span>` : ""}
      <span class="punkte">${liste.map((e) => `<i class="${e.pre ? "pre" : ""}" style="--c:${farbe(e.r.spiel_farbe)}"></i>`).join("")}</span>
    </div>`;
  }
  html += `</div>`;

  if (zustand.tag) {
    const liste = tage.get(zustand.tag) || [];
    const d = parse(zustand.tag);
    html += `<section class="tag-panel"><h3>${WT_LANG[d.getDay()]}, ${d.getDate()}. ${MON[d.getMonth()]}</h3>
      ${liste.length ? liste.map((e) => karte(e)).join("")
        : `<p class="hinweis" style="margin:0">An diesem Tag erscheint nichts.</p>`}</section>`;
  }

  // Termine ohne festen Tag, die in diesen Monat fallen
  const q = Math.floor(mon / 3);
  const ungenau = DATEN.filter((r) => passt(r) && r.genauigkeit !== "tag").filter((r) => {
    const d = parse(r.datum);
    if (d.getFullYear() !== jahr) return false;
    return r.genauigkeit === "monat" ? d.getMonth() === mon : Math.floor(d.getMonth() / 3) === q;
  });
  if (ungenau.length) {
    html += `<section class="ungenau-panel"><h3>Ohne festen Tag</h3><div class="pillen">
      ${ungenau.map((r) => pille({ r, pre: false }).replace("</span></button>", ` <small>· ${esc(zeitraum(r))}</small></span></button>`)).join("")}</div></section>`;
  }
  $("#inhalt").innerHTML = html;
}

function wocheRendern() {
  const w = zustand.woche;
  const ende = plusTage(w, 6);
  const kurz = (d) => `${d.getDate()}.${d.getMonth() + 1}.`;
  $("#leiste").innerHTML = `<h2>KW ${kw(w)} <span class="zaehler">${kurz(w)} – ${kurz(ende)}${ende.getFullYear()}</span></h2>
    <div class="nav">
      <button type="button" class="icon-knopf" data-nav="-1" aria-label="Vorherige Woche">${pfeil("l")}</button>
      <button type="button" class="icon-knopf" data-nav="0">Heute</button>
      <button type="button" class="icon-knopf" data-nav="1" aria-label="Nächste Woche">${pfeil("r")}</button>
    </div>`;
  const tage = nachTag(eintraege());
  const h = iso(heute());
  let html = `<div class="woche">`;
  for (let i = 0; i < 7; i++) {
    const d = plusTage(w, i), t = iso(d);
    const liste = tage.get(t) || [];
    html += `<div class="woche-tag${t === h ? " heute" : ""}">
      <div class="kopf-zeile"><b>${d.getDate()}</b><span>${WT_KURZ[d.getDay()]}</span></div>
      <div class="inhalt">${liste.length ? liste.map((e) => pille(e).replace('"><span>',
        `"><b class="woche-spiel">${logo(e.r.spiel, "logo-mini")}${esc(e.r.spiel_name)}</b><span>`).replace("</span></button>",
        `</span>${e.pre ? "" : `<small>${esc(META.typen[e.r.typ])}</small>`}</button>`)).join("")
        : `<span class="frei">–</span>`}</div>
    </div>`;
  }
  html += `</div>`;
  $("#inhalt").innerHTML = html;
}

async function aenderungenRendern() {
  const feed = `/feed.xml${zustand.spiele.size !== META.spiele.length ? "?spiele=" + [...zustand.spiele].join(",") : ""}`;
  $("#leiste").innerHTML = `<h2>Letzte Änderungen</h2>
    <a class="icon-knopf" href="${feed}" title="Änderungen als Atom-Feed">Als Feed abonnieren</a>`;
  if (!AENDERUNGEN) {
    $("#inhalt").innerHTML = `<div class="flaeche leer">Wird geladen …</div>`;
    try { AENDERUNGEN = await (await fetch("/api/aenderungen?limit=80")).json(); }
    catch (e) { $("#inhalt").innerHTML = `<div class="flaeche leer">Änderungen konnten nicht geladen werden.</div>`; return; }
    if (zustand.ansicht !== "aenderungen") return;
  }
  const liste = AENDERUNGEN.filter((a) => zustand.spiele.has(a.spiel));
  if (!liste.length) {
    $("#inhalt").innerHTML = `<div class="flaeche leer">Noch keine Änderungen für diese Spiele. Neue Sets und verschobene Termine erscheinen hier.</div>`;
    return;
  }
  const bekannt = new Set(DATEN.map((r) => r.id));
  $("#inhalt").innerHTML = `<div class="flaeche">${liste.map((a) => {
    const zeit = new Date(a.zeit);
    const titel = `${ART_TEXT[a.art] || a.art}: ${esc(a.name)}`;
    const kopf = a.art !== "geloescht" && a.release_id
      ? `<button type="button" class="link" data-r="${a.release_id}" ${bekannt.has(a.release_id) ? "" : 'data-nachladen="1"'}>${titel}</button>`
      : `<strong>${titel}</strong>`;
    return `<div class="aenderung" style="--c:${farbe(a.spiel_farbe)}">
      <span class="farbe"></span>
      <div><div>${kopf}</div><div class="hinweis" style="margin:0">${logo(a.spiel, "logo-mini")}${esc(a.spiel_name || a.spiel)}</div>
        ${a.text ? `<div class="text">${esc(a.text)}</div>` : ""}</div>
      <span class="zeit">${zeit.toLocaleDateString("de-DE")}</span>
    </div>`;
  }).join("")}</div>`;
}

function pfeil(r) {
  return r === "l"
    ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>'
    : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 18 6-6-6-6"/></svg>';
}

function rendern() {
  document.querySelectorAll("[data-ansicht]").forEach((b) => b.setAttribute("aria-pressed", b.dataset.ansicht === zustand.ansicht));
  if (zustand.ansicht === "monat") monatRendern();
  else if (zustand.ansicht === "woche") wocheRendern();
  else if (zustand.ansicht === "aenderungen") aenderungenRendern();
  else listeRendern();
}

function aktualisieren() {
  aboAktualisieren();
  rendern();
  urlSchreiben(detailOffen());
  einstellungenSpeichern();
}

// --- Detailansicht -------------------------------------------------------------------
function detailOffen() { const d = $("#detail"); return d.open ? d.dataset.r : null; }

async function detailZeigen(id) {
  let r = DATEN.find((x) => String(x.id) === String(id));
  if (!r) {
    const antwort = await fetch(`/api/releases/${encodeURIComponent(id)}`);
    if (!antwort.ok) { toast("Dieser Termin existiert nicht mehr."); urlSchreiben(null); return; }
    r = await antwort.json();
  }
  const dlg = $("#detail");
  const ca = zeitraum(r);
  const n = tageBis(r.datum);
  const datumText = ca || `${WT_LANG[parse(r.datum).getDay()]}, ${parse(r.datum).getDate()}. ${MON[parse(r.datum).getMonth()]} ${parse(r.datum).getFullYear()}`;
  const zeilen = [
    ["Produkttyp", esc(META.typen[r.typ])],
    ["Status", `<span class="badge ${r.status_effektiv}">${esc(META.status[r.status_effektiv])}</span> ${markierung(r)}`],
  ];
  if (r.code) zeilen.push(["Set-Code", esc(r.code)]);
  if (r.prerelease) zeilen.push(["Prerelease", `${fmt(r.prerelease, true)} <span class="hinweis">(${countdown(r.prerelease)})</span>`]);
  if (r.vorheriges_datum && r.status === "verschoben") zeilen.push(["Vorher geplant", fmt(r.vorheriges_datum)]);
  if (ca) zeilen.push(["Genauigkeit", `Nur ${r.genauigkeit === "monat" ? "der Monat" : "das Quartal"} ist bekannt`]);
  if (r.quelle) {
    let host = r.quelle; try { host = new URL(r.quelle).hostname.replace(/^www\./, ""); } catch (e) {}
    zeilen.push(["Quelle", `<a href="${esc(r.quelle)}" target="_blank" rel="noopener">${esc(host)}</a>`]);
  }
  zeilen.push(["Zuletzt geändert", new Date(r.geaendert).toLocaleDateString("de-DE")]);

  dlg.style.setProperty("--c", farbe(r.spiel_farbe));
  dlg.dataset.r = r.id;
  dlg.innerHTML = `<div class="detail-band"></div>
    <div class="detail-inhalt" style="position:relative">
      <button type="button" class="icon-knopf schliessen-x" data-schliessen aria-label="Schließen">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg></button>
      <div class="detail-spiel">${logo(r.spiel, "logo-detail") || "<i></i>"}<span>${esc(r.spiel_name)}</span></div>
      <h2 id="detail-titel">${esc(r.name)}</h2>
      <div class="detail-datum"><b>${esc(datumText)}</b>${ca ? "" : `<span class="countdown${n >= 0 && n <= 7 ? " bald" : ""}">${countdown(r.datum)}</span>`}</div>
      <table class="detail-tabelle">${zeilen.map(([k, v]) => `<tr><th>${k}</th><td>${v}</td></tr>`).join("")}</table>
      ${r.notiz ? `<p class="detail-notiz">${esc(r.notiz)}</p>` : ""}
      <div class="detail-aktionen">
        <div class="links">
          <a class="knopf primaer" href="/release/${r.id}.ics" download>In meinen Kalender</a>
          <button type="button" class="knopf" data-teilen>Link kopieren</button>
        </div>
        <button type="button" class="knopf" data-schliessen>Schließen</button>
      </div>
    </div>`;
  dlg.querySelectorAll("[data-schliessen]").forEach((b) => (b.onclick = () => dlg.close()));
  dlg.querySelector("[data-teilen]").onclick = () => kopieren(`${location.origin}/?r=${r.id}`, "Link kopiert");
  if (!dlg.open) dlg.showModal();
  urlSchreiben(r.id);
}

// --- Ereignisse ---------------------------------------------------------------------
function ereignisseBinden() {
  document.querySelectorAll("[data-ansicht]").forEach((b) => (b.onclick = () => {
    zustand.ansicht = b.dataset.ansicht; aktualisieren();
  }));
  document.querySelectorAll("[data-alle]").forEach((b) => (b.onclick = () => {
    alleIds(b.dataset.alle).forEach((x) => zustand[b.dataset.alle].add(x)); filterBauen(); aktualisieren();
  }));
  document.querySelectorAll("[data-keine]").forEach((b) => (b.onclick = () => {
    zustand[b.dataset.keine].clear(); filterBauen(); aktualisieren();
  }));
  $("#f-prerelease").onchange = (e) => { zustand.prerelease = e.target.checked; aktualisieren(); };
  $("#f-ungenau").onchange = (e) => { zustand.ungenau = e.target.checked; aktualisieren(); };
  $("#f-vergangen").onchange = (e) => { zustand.vergangen = e.target.checked; aktualisieren(); };
  let suchTimer;
  $("#f-suche").oninput = (e) => { clearTimeout(suchTimer); suchTimer = setTimeout(() => { zustand.suche = e.target.value.trim(); aktualisieren(); }, 150); };
  $("#abo-kopieren").onclick = () => kopieren($("#abo-url").value, "Abo-Link kopiert");

  $("#detail").addEventListener("close", () => urlSchreiben(null));
  $("#detail").addEventListener("click", (e) => { if (e.target === $("#detail")) $("#detail").close(); });

  // Delegation für dynamische Inhalte
  document.addEventListener("click", (e) => {
    const r = e.target.closest("[data-r]");
    if (r && !r.closest("dialog")) { e.stopPropagation(); detailZeigen(r.dataset.r); return; }
    const nav = e.target.closest("[data-nav]");
    if (nav) { navigieren(Number(nav.dataset.nav)); return; }
    if (e.target.closest("#filter-zurueck")) {
      for (const g of ["spiele", "typ"]) zustand[g] = new Set(alleIds(g));
      zustand.status = new Set(STANDARD_STATUS); zustand.suche = ""; zustand.ungenau = true;
      filterBauen(); aktualisieren(); return;
    }
    const zelle = e.target.closest(".zelle");
    if (zelle) { zustand.tag = zelle.dataset.tag; monatRendern(); }
  });
  document.addEventListener("keydown", (e) => {
    const zelle = e.target.closest && e.target.closest(".zelle");
    if (zelle && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); zustand.tag = zelle.dataset.tag; monatRendern(); $(`.zelle[data-tag="${zustand.tag}"]`).focus(); }
    if ((zustand.ansicht === "monat" || zustand.ansicht === "woche") && !$("#detail").open
        && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) {
      if (e.key === "ArrowLeft" && e.altKey) navigieren(-1);
      if (e.key === "ArrowRight" && e.altKey) navigieren(1);
    }
  });
}

function navigieren(richtung) {
  if (zustand.ansicht === "monat") {
    const h = heute();
    zustand.monat = richtung === 0 ? new Date(h.getFullYear(), h.getMonth(), 1)
      : new Date(zustand.monat.getFullYear(), zustand.monat.getMonth() + richtung, 1);
    zustand.tag = richtung === 0 ? iso(h) : null;
  } else if (zustand.ansicht === "woche") {
    zustand.woche = richtung === 0 ? montag(heute()) : plusTage(zustand.woche, 7 * richtung);
  }
  aktualisieren();
}

// --- Start ---------------------------------------------------------------------------
(async function start() {
  try {
    [META, DATEN] = await Promise.all([
      fetch("/api/meta").then((r) => r.json()),
      fetch("/api/releases?rueckblick=400").then((r) => r.json()),
    ]);
  } catch (e) {
    $("#inhalt").innerHTML = `<div class="flaeche leer">Die Termine konnten nicht geladen werden. Bitte Seite neu laden.</div>`;
    return;
  }
  if (META.titel) { $("#seitentitel").textContent = META.titel; document.title = META.titel; }
  VORHER_BESUCH = speicherLesen("tcg-letzter-besuch");
  speicherSchreiben("tcg-letzter-besuch", new Date().toISOString().replace(/\.\d+Z$/, "Z"));
  if (window.matchMedia("(max-width: 900px)").matches) $("#filter-box").open = false;

  einstellungenLaden();
  filterBauen();
  ereignisseBinden();
  aktualisieren();
  const r = new URLSearchParams(location.search).get("r");
  if (r) detailZeigen(r);
})();
