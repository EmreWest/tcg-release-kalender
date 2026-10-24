// Hell/Dunkel/System – wird synchron im <head> geladen, damit beim Laden nichts aufblitzt.
(function () {
  const MODI = ["system", "hell", "dunkel"];
  const LABEL = { system: "Design: wie System", hell: "Design: hell", dunkel: "Design: dunkel" };
  const ICON = {
    system: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/></svg>',
    hell: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
    dunkel: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>',
  };
  function lesen() { try { return localStorage.getItem("tcg-design") || "system"; } catch (e) { return "system"; } }
  function setzen(modus) {
    if (modus === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", modus);
    try { localStorage.setItem("tcg-design", modus); } catch (e) {}
    const b = document.getElementById("design-knopf");
    if (b) { b.innerHTML = ICON[modus]; b.title = LABEL[modus]; b.setAttribute("aria-label", LABEL[modus] + " – umschalten"); }
  }
  setzen(lesen());
  document.addEventListener("DOMContentLoaded", function () {
    const b = document.getElementById("design-knopf");
    if (!b) return;
    setzen(lesen());
    b.addEventListener("click", function () {
      const naechster = MODI[(MODI.indexOf(lesen()) + 1) % MODI.length];
      setzen(naechster);
    });
  });
})();
