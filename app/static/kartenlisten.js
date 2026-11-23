"use strict";
// Übersicht aller Sets mit Kartenliste – unabhängig vom Kalender durchsuchbar.

const MON = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"];
const SCHRITT = 120;
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const farbe = (f) => (/^#[0-9a-f]{6}$/i.test(f || "") ? f : "#6b7280");
const normal = (s) => String(s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");

let DATEN = null, SPIELE = {}, gewaehlt = "", anzahl = SCHRITT;

function datumText(r) {
  const d = new Date(r.datum + "T12:00:00");
  if (r.genauigkeit === "monat") return `${MON[d.getMonth()]} ${d.getFullYear()}`;
  if (r.genauigkeit === "quartal") return `Q${Math.floor(d.getMonth() / 3) + 1} ${d.getFullYear()}`;
  return d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" });
}

function bild(r) {
  if (r.logo) return `<img class="set-logo" src="${esc(r.logo)}" alt="" loading="lazy" decoding="async">`;
  if (r.symbol) return `<img class="set-symbol gross${r.symbol_mono ? " mono" : ""}" src="${esc(r.symbol)}" alt="" loading="lazy">`;
  const sp = SPIELE[r.spiel];
  if (sp && sp.logo) return `<img class="spiel-logo-kachel" src="${esc(sp.logo)}" alt="" loading="lazy">`;
  return `<span class="kachel-code">${esc(r.code || "")}</span>`;
}

function grundgeruest() {
  const ausgeben = (id, name, f, logo) => `<button type="button" class="chip" data-spiel="${esc(id)}" aria-pressed="${gewaehlt === id}">
      ${logo ? `<img class="logo-chip" src="${esc(logo)}" alt="">` : (f ? `<span class="punkt" style="--c:${farbe(f)}"></span>` : "")}${esc(name)}</button>`;
  $("#uebersicht").innerHTML = `
    <div class="uebersicht-kopf">
      <h2>Kartenlisten</h2>
      <span class="hinweis" id="u-zahl"></span>
    </div>
    ${DATEN.spiele.length > 1 ? `<div class="chips u-spiele">${ausgeben("", "Alle Spiele")}${DATEN.spiele.map((s) => ausgeben(s.id, s.name, s.farbe, s.logo)).join("")}</div>` : ""}
    <div class="karten-werkzeuge">
      <div class="suche">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
        <input type="search" id="u-suche" placeholder="Set oder Code suchen" aria-label="Sets durchsuchen">
      </div>
    </div>
    <div class="set-kacheln" id="u-kacheln"></div>
    <div class="archiv-hinweis" id="u-mehr"></div>`;
  document.querySelectorAll("[data-spiel]").forEach((b) => (b.onclick = () => {
    gewaehlt = b.dataset.spiel; anzahl = SCHRITT;
    document.querySelectorAll("[data-spiel]").forEach((x) => x.setAttribute("aria-pressed", x === b));
    urlSchreiben(); rendern();
  }));
  $("#u-suche").addEventListener("input", () => { anzahl = SCHRITT; urlSchreiben(); rendern(); });
}

function urlSchreiben() {
  const p = new URLSearchParams();
  if (gewaehlt) p.set("spiel", gewaehlt);
  const q = $("#u-suche").value.trim();
  if (q) p.set("q", q);
  history.replaceState(null, "", location.pathname + (p.toString() ? "?" + p : ""));
}

function rendern() {
  const q = normal($("#u-suche").value.trim());
  const treffer = DATEN.sets.filter((r) => (!gewaehlt || r.spiel === gewaehlt) && (!q || normal(`${r.name} ${r.name_de || ""} ${r.code}`).includes(q)));
  $("#u-zahl").textContent = `${treffer.length.toLocaleString("de-DE")} Sets`;
  let html = "", jahr = "";
  for (const r of treffer.slice(0, anzahl)) {
    const j = r.datum.slice(0, 4);
    if (j !== jahr) { html += `<h3 class="archiv-jahr kachel-jahr">${j}</h3>`; jahr = j; }
    const sp = SPIELE[r.spiel] || {};
    html += `<a class="set-kachel" href="/karten/${r.id}" style="--c:${farbe(sp.farbe)}">
      <span class="kachel-bild">${bild(r)}</span>
      <span class="kachel-text">
        <span class="kachel-spiel">${esc(sp.name || r.spiel)}</span>
        <span class="kachel-name">${r.symbol && r.logo ? `<img class="set-symbol${r.symbol_mono ? " mono" : ""}" src="${esc(r.symbol)}" alt="">` : ""}${esc(r.name_de || r.name)}</span>
        ${r.name_de && r.name_de !== r.name ? `<span class="kachel-en">${esc(r.name)}</span>` : ""}
        <span class="kachel-meta">${esc(datumText(r))}${r.code ? ` · ${esc(r.code)}` : ""} · ${r.karten.toLocaleString("de-DE")} ${r.karten === 1 ? "Karte" : "Karten"}</span>
      </span>
    </a>`;
  }
  $("#u-kacheln").innerHTML = html || `<div class="flaeche leer karten-leer">Kein Set passt zur Suche.</div>`;
  const rest = treffer.length - Math.min(anzahl, treffer.length);
  $("#u-mehr").innerHTML = rest > 0 ? `<button type="button" class="knopf">Weitere ${Math.min(rest, SCHRITT)} von ${rest.toLocaleString("de-DE")} anzeigen</button>` : "";
  const mehr = $("#u-mehr button");
  if (mehr) mehr.onclick = () => { anzahl += SCHRITT; rendern(); };
}

(async function start() {
  try {
    const antwort = await fetch("/api/kartenlisten");
    DATEN = await antwort.json();
  } catch (e) { DATEN = { spiele: [], sets: [] }; }
  if (!DATEN.sets.length) {
    $("#uebersicht").innerHTML = `<div class="flaeche leer">Es gibt noch keine Kartenlisten.<br><a class="knopf" href="/">Zum Kalender</a></div>`;
    return;
  }
  SPIELE = Object.fromEntries(DATEN.spiele.map((s) => [s.id, s]));
  const p = new URLSearchParams(location.search);
  if (SPIELE[p.get("spiel")]) gewaehlt = p.get("spiel");
  grundgeruest();
  if (p.get("q")) $("#u-suche").value = p.get("q");
  rendern();
})();
