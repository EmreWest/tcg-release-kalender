// Anmeldung der Verwaltung auf den öffentlichen Seiten: „Anmelden“ unten, nach der Anmeldung
// „Verwaltung“ oben und „Abmelden“ unten. Preise liefert der Server ohnehin nur mit gültiger Sitzung.
(function () {
  const ZAHNRAD = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>';

  function anmeldenLink() {
    const a = document.querySelector("[data-login]");
    if (a) a.href = "/login?weiter=" + encodeURIComponent(location.pathname + location.search);
  }

  function angemeldetZeigen() {
    document.documentElement.classList.add("angemeldet");
    const rechts = document.querySelector(".kopf-rechts");
    if (rechts && !rechts.querySelector("[data-verwaltung]")) {
      const a = document.createElement("a");
      a.className = "icon-knopf kartenlisten-knopf";
      a.href = "/admin";
      a.title = "Verwaltung";
      a.dataset.verwaltung = "";
      a.innerHTML = ZAHNRAD + "<span>Verwaltung</span>";
      rechts.insertBefore(a, rechts.querySelector("#design-knopf"));
    }
    const login = document.querySelector("[data-login]");
    if (login) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "link-knopf fuss-login";
      b.textContent = "Abmelden";
      b.onclick = async () => {
        b.disabled = true;
        try {
          await fetch("/api/logout", { method: "POST", headers: { "X-Admin-Anfrage": "1" } });
        } catch (e) { /* Seite trotzdem neu laden */ }
        location.reload();
      };
      login.replaceWith(b);
    }
  }

  anmeldenLink();
  fetch("/api/sitzung", { cache: "no-store" })
    .then((r) => (r.ok ? r.json() : {}))
    .then((d) => { if (d.angemeldet) angemeldetZeigen(); })
    .catch(() => {});
})();
