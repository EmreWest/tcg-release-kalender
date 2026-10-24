"use strict";
// Speicher-Hinweis (Einwilligung nach § 25 TDDDG) und einmaliger Hinweis zum Home-Bildschirm auf Mobilgeräten.
// Alle Werte liegen nur im localStorage des Browsers und werden nie an den Server übertragen.
(function () {
  const SCHLUESSEL = "tcg-einwilligung";
  const VERSION = 1;
  const KOMFORT_SCHLUESSEL = ["tcg-filter", "tcg-letzter-besuch"];
  const HS_SCHLUESSEL = "tcg-homescreen-hinweis";
  const HS_DAUER_MS = 15000;
  const skript = document.currentScript;
  const datenschutzUrl = (skript && skript.dataset.datenschutz) || "";
  const beobachter = [];

  function lesen(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function schreiben(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  function loeschen(k) { try { localStorage.removeItem(k); } catch (e) {} }

  function entscheidung() {
    try {
      const e = JSON.parse(lesen(SCHLUESSEL) || "null");
      return e && e.v === VERSION ? e : null;
    } catch (e) { return null; }
  }

  const api = {
    entschieden: () => !!entscheidung(),
    komfort: () => !!(entscheidung() || {}).komfort,
    beiAenderung: (fn) => beobachter.push(fn),
    oeffnen: () => bannerZeigen(true),
  };
  window.TCGSpeicher = api;

  function festlegen(komfort) {
    schreiben(SCHLUESSEL, JSON.stringify({ v: VERSION, komfort: !!komfort, zeit: new Date().toISOString() }));
    if (!komfort) KOMFORT_SCHLUESSEL.forEach(loeschen);
    beobachter.forEach((fn) => { try { fn(!!komfort); } catch (e) {} });
  }

  // --- Speicher-Hinweis -----------------------------------------------------------
  let banner = null;
  function bannerZeigen(manuell) {
    if (banner) { banner.querySelector("button").focus(); return; }
    const aktuell = entscheidung();
    banner = document.createElement("section");
    banner.className = "hinweis-box speicher-hinweis";
    banner.setAttribute("role", "region");
    banner.setAttribute("aria-label", "Speicher-Einstellungen");
    banner.innerHTML = `
      <h2>Einstellungen im Browser speichern?</h2>
      <p>Diese Seite nutzt keine Cookies und kein Tracking. Notwendig gespeichert werden nur technische Einstellungen wie dein gewähltes Design und diese Entscheidung – lokal in deinem Browser.</p>
      <p>Mit „Alle erlauben“ merkt sich die Seite außerdem deine Filter und markiert, was seit deinem letzten Besuch neu ist. Auch das bleibt auf deinem Gerät.${
        datenschutzUrl ? ` <a href="${datenschutzUrl}">Mehr in der Datenschutzerklärung</a>` : ""}</p>
      ${manuell && aktuell ? `<p class="stand">Aktuell: ${aktuell.komfort ? "alle erlaubt" : "nur notwendige"}</p>` : ""}
      <div class="hinweis-knoepfe">
        <button type="button" class="knopf" data-wahl="0">Nur notwendige</button>
        <button type="button" class="knopf" data-wahl="1">Alle erlauben</button>
      </div>`;
    banner.querySelectorAll("[data-wahl]").forEach((b) => b.addEventListener("click", () => {
      festlegen(b.dataset.wahl === "1");
      bannerSchliessen();
    }));
    document.body.appendChild(banner);
    if (manuell) banner.querySelector("button").focus();
  }
  function bannerSchliessen() {
    if (!banner) return;
    banner.remove();
    banner = null;
    homescreenPlanen(1200);
  }

  // --- Home-Bildschirm-Hinweis ---------------------------------------------------------
  let installEreignis = null;
  window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); installEreignis = e; });

  function istMobil() {
    const ua = navigator.userAgent || "";
    const ios = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
    return ios || /Android|Mobile/i.test(ua) || (matchMedia("(pointer: coarse)").matches && matchMedia("(max-width: 900px)").matches);
  }
  function istApp() {
    return matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
  }

  const ICON_TEILEN = '<svg class="hs-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-label="Teilen-Symbol"><path d="M12 3v12M8 7l4-4 4 4"/><path d="M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-7"/></svg>';
  const ICON_MENUE = '<svg class="hs-icon" viewBox="0 0 24 24" fill="currentColor" aria-label="Menü-Symbol"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>';

  function anleitung() {
    const ua = navigator.userAgent || "";
    const ios = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
    if (ios) {
      return `Tippe auf ${ICON_TEILEN} <b>Teilen</b> und dann auf <b>„Zum Home-Bildschirm“</b>. Falls der Eintrag fehlt, scroll im Menü etwas nach unten.`;
    }
    if (/SamsungBrowser/.test(ua)) {
      return `Tippe unten rechts auf das Menü und dann auf <b>„Seite hinzufügen zu“</b> → <b>„Startbildschirm“</b>.`;
    }
    if (/Firefox/.test(ua)) {
      return `Tippe auf ${ICON_MENUE} und dann auf <b>„Installieren“</b> bzw. <b>„Zum Startbildschirm hinzufügen“</b>.`;
    }
    return `Tippe oben rechts auf ${ICON_MENUE} und dann auf <b>„Zum Startbildschirm hinzufügen“</b> bzw. <b>„App installieren“</b>.`;
  }

  let hsGeplant = false;
  function homescreenPlanen(verzoegerung) {
    if (hsGeplant || lesen(HS_SCHLUESSEL) || !istMobil() || istApp() || banner) return;
    hsGeplant = true;
    setTimeout(homescreenZeigen, verzoegerung);
  }

  function homescreenZeigen() {
    if (banner || lesen(HS_SCHLUESSEL) || document.querySelector("dialog[open]")) {
      hsGeplant = false;
      if (!lesen(HS_SCHLUESSEL)) setTimeout(() => homescreenPlanen(0), 4000);
      return;
    }
    schreiben(HS_SCHLUESSEL, new Date().toISOString());  // nur einmal pro Gerät bzw. Browser
    const box = document.createElement("section");
    box.className = "hinweis-box homescreen-hinweis";
    box.setAttribute("role", "status");
    box.innerHTML = `
      <button type="button" class="hs-schliessen" aria-label="Hinweis schließen">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg></button>
      <h2>Kalender auf den Home-Bildschirm</h2>
      <p>${installEreignis ? "Dann startet er wie eine App, ohne Browserleiste." : anleitung()}</p>
      ${installEreignis ? `<div class="hinweis-knoepfe"><button type="button" class="knopf primaer" data-installieren>Zum Home-Bildschirm hinzufügen</button></div>` : ""}
      <div class="hs-zeit" aria-hidden="true"><i></i></div>`;
    document.body.appendChild(box);

    let rest = HS_DAUER_MS, start = Date.now(), timer = null;
    const balken = box.querySelector(".hs-zeit i");
    function weiter() {
      start = Date.now();
      timer = setTimeout(schliessen, rest);
      balken.style.transition = `transform ${rest}ms linear`;
      requestAnimationFrame(() => { balken.style.transform = "scaleX(0)"; });
    }
    function pause() {
      if (!timer) return;
      clearTimeout(timer); timer = null;
      rest = Math.max(1500, rest - (Date.now() - start));
      const breite = balken.getBoundingClientRect().width / box.querySelector(".hs-zeit").getBoundingClientRect().width;
      balken.style.transition = "none";
      balken.style.transform = `scaleX(${breite})`;
    }
    function schliessen() {
      clearTimeout(timer);
      box.classList.add("weg");
      setTimeout(() => box.remove(), 250);
    }
    box.querySelector(".hs-schliessen").addEventListener("click", schliessen);
    // Beim Lesen bzw. Antippen nicht verschwinden lassen
    box.addEventListener("pointerdown", pause);
    box.addEventListener("pointerup", () => { if (!timer) weiter(); });
    box.addEventListener("focusin", pause);
    const inst = box.querySelector("[data-installieren]");
    if (inst) inst.addEventListener("click", async () => {
      try { installEreignis.prompt(); await installEreignis.userChoice; } catch (e) {}
      installEreignis = null;
      schliessen();
    });
    requestAnimationFrame(weiter);
  }

  // --- Start --------------------------------------------------------------------------
  function start() {
    document.addEventListener("click", (e) => {
      if (e.target.closest("[data-speicher-einstellungen]")) bannerZeigen(true);
    });
    if (!entscheidung()) bannerZeigen(false);
    else homescreenPlanen(2500);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
