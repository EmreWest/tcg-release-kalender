"use strict";
// Kartenliste eines Sets: Raster mit Bildern, Suche, Filter nach Seltenheit, Großansicht.

const MON = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"];
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const farbe = (f) => (/^#[0-9a-f]{6}$/i.test(f || "") ? f : "#6b7280");

let DATEN = null, SICHTBAR = [], AKTUELL = -1;

function datumText(r) {
  const d = new Date(r.datum + "T12:00:00");
  if (r.genauigkeit === "monat") return `${MON[d.getMonth()]} ${d.getFullYear()}`;
  if (r.genauigkeit === "quartal") return `Q${Math.floor(d.getMonth() / 3) + 1} ${d.getFullYear()}`;
  return d.toLocaleDateString("de-DE", { day: "numeric", month: "long", year: "numeric" });
}

const euro = (x) => x.toLocaleString("de-DE", { style: "currency", currency: "EUR", minimumFractionDigits: 2 });

// Wert für Sortierung und Anzeige: Preistrend, sonst 30-Tage-Schnitt, sonst günstigstes Angebot
function preisWert(k) {
  const c = k.cardmarket;
  if (!c) return null;
  return c.trend ?? c.avg30 ?? c.low ?? c.bis ?? null;
}

function preisText(k) {
  const c = k.cardmarket;
  if (!c) return "";
  if (c.von !== undefined) return c.von === c.bis ? euro(c.von) : `${euro(c.von)} – ${euro(c.bis)}`;
  const w = c.trend ?? c.avg30 ?? c.low;
  return w ? euro(w) : "";
}

function normal(s) { return String(s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, ""); }

function kopfRendern() {
  const r = DATEN.release, k = DATEN.karten;
  const titel = r.name_de || r.name, englisch = r.name_de && r.name_de !== r.name ? r.name : "";
  document.title = `${titel} – Kartenliste`;
  $("#zurueck").href = `/?r=${r.id}`;
  const seltenheiten = [...new Set(k.map((x) => x.seltenheit).filter(Boolean))];
  const mitBild = k.filter((x) => x.bild).length;
  $("#karten-seite").innerHTML = `
    <section class="flaeche karten-kopf" style="--c:${farbe(r.spiel_farbe)}">
      <div class="detail-band"></div>
      <div class="karten-kopf-inhalt">
        ${r.logo ? `<img class="set-logo kopf-logo" src="${esc(r.logo)}" alt="">` : ""}
        <div class="detail-spiel"><i></i><span>${esc(r.spiel_name)}</span></div>
        <h2>${r.symbol ? `<img class="set-symbol titel${r.symbol_mono ? " mono" : ""}" src="${esc(r.symbol)}" alt="">` : ""}${esc(titel)}</h2>
        ${englisch ? `<div class="detail-en">Englisch: ${esc(englisch)}</div>` : ""}
        <div class="karten-meta">
          <span>${esc(datumText(r))}</span>
          ${r.code ? `<span class="code">${esc(r.code)}</span>` : ""}
          ${k.length ? `<span>${k.length.toLocaleString("de-DE")} ${k.length === 1 ? "Karte" : "Karten"}</span>` : ""}
          ${(DATEN.produkte || []).length ? `<span>${DATEN.produkte.length} Produkte</span>` : ""}
          ${mitBild < k.length ? `<span>${mitBild.toLocaleString("de-DE")} mit Bild</span>` : ""}
        </div>
      </div>
    </section>
    ${produkteHtml()}
    ${k.length ? `<h3 class="abschnitt-titel">Karten</h3>` : ""}
    <div class="karten-werkzeuge"${k.length ? "" : " hidden"}>
      <div class="suche">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
        <input type="search" id="k-suche" placeholder="Name, Nummer oder Kartentyp" aria-label="Karten durchsuchen">
      </div>
      ${DATEN.karten.some((k) => preisWert(k)) ? `<select id="k-sortierung" aria-label="Sortierung">
        <option value="">Nach Nummer</option>
        <option value="preis-ab">Preis absteigend</option>
        <option value="preis-auf">Preis aufsteigend</option></select>` : ""}
      ${seltenheiten.length > 1 ? `<select id="k-seltenheit" aria-label="Seltenheit">
        <option value="">Alle Seltenheiten</option>
        ${seltenheiten.map((s) => `<option>${esc(s)}</option>`).join("")}</select>` : ""}
      <span class="karten-zahl" id="k-zahl"></span>
    </div>
    <div class="karten-raster" id="k-raster"></div>
    <p class="hinweis karten-quelle">Kartendaten und -bilder: <a href="${esc(DATEN.quelle.url)}" target="_blank" rel="noopener">${esc(DATEN.quelle.name)}</a>,
      ${DATEN.abgerufen ? `Stand ${new Date(DATEN.abgerufen).toLocaleDateString("de-DE")}.` : ""} Produktbilder: TCGplayer-Katalog.
      ${DATEN.preise_stand ? `Preise: Preistrend laut <a href="https://www.cardmarket.com/de" target="_blank" rel="noopener">Cardmarket</a>, Stand ${new Date(DATEN.preise_stand).toLocaleDateString("de-DE")}; bei mehreren Versionen einer Karte die Spanne aller Versionen.` : ""}
      Alle Kartenbilder © der jeweiligen Rechteinhaber.
      Bei neuen Sets kommen während der Spoiler-Phase laufend Karten dazu.</p>`;
  $("#k-suche").addEventListener("input", rasterRendern);
  const sel = $("#k-seltenheit");
  if (sel) sel.addEventListener("change", rasterRendern);
  const sort = $("#k-sortierung");
  if (sort) sort.addEventListener("change", rasterRendern);
}

const EXTERN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>';

function produkteHtml() {
  const p = DATEN.produkte || [];
  if (!p.length) return "";
  return `<section class="produkte">
    <h3 class="abschnitt-titel">Produkte in diesem Set</h3>
    <div class="produkt-raster">${p.map((x) => {
      const c = x.cardmarket || {};
      const preis = c.trend ?? c.avg30 ?? c.low;
      const inhalt = `
        <span class="produkt-bild">${x.bild ? `<img src="${esc(x.bild)}" alt="" loading="lazy" decoding="async">` : ""}</span>
        <span class="produkt-text">
          <span class="produkt-name">${esc(x.name)}</span>
          ${x.name_en ? `<span class="produkt-en">${esc(x.name_en)}</span>` : ""}
          ${preis ? `<span class="produkt-preis">Preistrend ${euro(preis)}</span>` : ""}
          ${c.url ? `<span class="produkt-link">${preis ? "Bei Cardmarket ansehen" : "Bei Cardmarket suchen"} ${EXTERN}</span>` : ""}
        </span>`;
      return c.url
        ? `<a class="produkt" href="${esc(c.url)}" target="_blank" rel="noopener noreferrer">${inhalt}</a>`
        : `<div class="produkt">${inhalt}</div>`;
    }).join("")}</div>
  </section>`;
}

function rasterRendern() {
  if (!DATEN.karten.length) return;
  const q = normal($("#k-suche").value.trim());
  const sel = $("#k-seltenheit") ? $("#k-seltenheit").value : "";
  SICHTBAR = [];
  DATEN.karten.forEach((k, i) => {
    if (sel && k.seltenheit !== sel) return;
    if (q && !normal(`${k.name} ${k.nummer} ${k.art} ${k.variante}`).includes(q)) return;
    SICHTBAR.push(i);
  });
  const sortierung = $("#k-sortierung") ? $("#k-sortierung").value : "";
  if (sortierung) {
    const richtung = sortierung === "preis-ab" ? -1 : 1;
    SICHTBAR.sort((a, b) => {
      const pa = preisWert(DATEN.karten[a]), pb = preisWert(DATEN.karten[b]);
      if (pa === null && pb === null) return a - b;
      if (pa === null) return 1;
      if (pb === null) return -1;
      return (pa - pb) * richtung || a - b;
    });
  }
  $("#k-zahl").textContent = SICHTBAR.length === DATEN.karten.length ? "" : `${SICHTBAR.length.toLocaleString("de-DE")} Treffer`;
  $("#k-raster").innerHTML = SICHTBAR.length ? SICHTBAR.map((i, pos) => {
    const k = DATEN.karten[i];
    const bild = k.bild
      ? `<img src="${esc(k.bild)}" alt="" loading="lazy" decoding="async" width="400" height="558">`
      : `<span class="kartenbild-leer">${esc(k.name)}</span>`;
    return `<button type="button" class="kartenfeld" data-pos="${pos}" aria-label="${esc(k.name)}${k.nummer ? ", " + esc(k.nummer) : ""}">
      <span class="kartenbild">${bild}</span>
      <span class="kartenzeile"><span class="kartennr">${esc(k.nummer || "")}</span>${k.seltenheit ? `<span class="kartensel">${esc(k.seltenheit)}</span>` : ""}</span>
      <span class="kartenname">${esc(k.name)}${k.variante ? ` <span class="kartenvariante">${esc(k.variante)}</span>` : ""}</span>
      ${preisText(k) ? `<span class="kartenpreis${k.cardmarket.von !== undefined ? " spanne" : ""}">${preisText(k)}</span>` : ""}
    </button>`;
  }).join("") : `<div class="flaeche leer karten-leer">Keine Karte passt zur Suche.</div>`;
}

function grossansicht(pos) {
  if (pos < 0 || pos >= SICHTBAR.length) return;
  AKTUELL = pos;
  const k = DATEN.karten[SICHTBAR[pos]];
  const dlg = $("#karte-dlg");
  const zeilen = [["Nummer", k.nummer], ["Seltenheit", k.seltenheit], ["Kartentyp", k.art], ["Variante", k.variante]]
    .filter(([, v]) => v).map(([a, b]) => [a, esc(b)]);
  const c = k.cardmarket;
  if (c) {
    if (c.von !== undefined) {
      zeilen.push(["Preistrend", `${preisText(k)} <span class="hinweis">(${c.versionen} Versionen bei Cardmarket)</span>`]);
    } else {
      if (c.trend) zeilen.push(["Preistrend", euro(c.trend)]);
      if (c.avg30) zeilen.push(["30-Tage-Schnitt", euro(c.avg30)]);
      if (c.low) zeilen.push(["Ab", euro(c.low)]);
      if (c.trend_foil) zeilen.push(["Trend Foil/Holo", euro(c.trend_foil)]);
    }
  }
  dlg.innerHTML = `
    <div class="karte-dlg-inhalt">
      <button type="button" class="icon-knopf schliessen-x" data-schliessen aria-label="Schließen">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg></button>
      <div class="karte-gross">${k.bild ? `<img src="${esc(k.bild)}" alt="${esc(k.name)}">` : `<span class="kartenbild-leer">Noch kein Bild</span>`}</div>
      <div class="karte-info">
        <h2 id="karte-titel">${esc(k.name)}</h2>
        <table class="detail-tabelle">${zeilen.map(([a, b]) => `<tr><th>${a}</th><td>${b}</td></tr>`).join("")}</table>
        ${c && c.url ? `<a class="knopf primaer cm-knopf" href="${esc(c.url)}" target="_blank" rel="noopener noreferrer">
          ${c.von !== undefined || (!c.trend && !c.avg30 && !c.low) ? "Bei Cardmarket suchen" : "Bei Cardmarket ansehen"}
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg></a>` : ""}
        <div class="karte-nav">
          <button type="button" class="knopf" data-schritt="-1" ${pos === 0 ? "disabled" : ""} aria-label="Vorherige Karte">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg></button>
          <span class="hinweis">${pos + 1} / ${SICHTBAR.length}</span>
          <button type="button" class="knopf" data-schritt="1" ${pos === SICHTBAR.length - 1 ? "disabled" : ""} aria-label="Nächste Karte">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 18 6-6-6-6"/></svg></button>
        </div>
      </div>
    </div>`;
  dlg.querySelector("[data-schliessen]").onclick = () => dlg.close();
  dlg.querySelectorAll("[data-schritt]").forEach((b) => (b.onclick = () => grossansicht(AKTUELL + Number(b.dataset.schritt))));
  if (!dlg.open) dlg.showModal();
}

function ereignisse() {
  document.addEventListener("click", (e) => {
    const f = e.target.closest(".kartenfeld");
    if (f) grossansicht(Number(f.dataset.pos));
  });
  const dlg = $("#karte-dlg");
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
  document.addEventListener("keydown", (e) => {
    if (!dlg.open) return;
    if (e.key === "ArrowLeft") grossansicht(AKTUELL - 1);
    if (e.key === "ArrowRight") grossansicht(AKTUELL + 1);
  });
  // Wischen in der Großansicht
  let startX = null;
  dlg.addEventListener("touchstart", (e) => { startX = e.touches[0].clientX; }, { passive: true });
  dlg.addEventListener("touchend", (e) => {
    if (startX === null) return;
    const dx = e.changedTouches[0].clientX - startX;
    startX = null;
    if (Math.abs(dx) > 60) grossansicht(AKTUELL + (dx < 0 ? 1 : -1));
  });
}

(async function start() {
  const id = (location.pathname.match(/\/karten\/(\d+)/) || [])[1];
  try {
    const antwort = await fetch(`/api/karten/${id}`);
    if (!antwort.ok) throw new Error(String(antwort.status));
    DATEN = await antwort.json();
  } catch (e) {
    $("#karten-seite").innerHTML = `<div class="flaeche leer">Für dieses Set gibt es keine Kartenliste.<br>
      <a class="knopf" href="/">Zum Kalender</a></div>`;
    return;
  }
  kopfRendern();
  rasterRendern();
  ereignisse();
})();
