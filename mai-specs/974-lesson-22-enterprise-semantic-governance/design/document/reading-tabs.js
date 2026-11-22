(() => {
  const groups = [...document.querySelectorAll("[data-tab-group]")];
  function activate(button) {
    const group = button.closest("[data-tab-group]");
    for (const tab of group.querySelectorAll(':scope > [role="tablist"] > [role="tab"]')) {
      const active = tab === button;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
      document.getElementById(tab.getAttribute("aria-controls")).hidden = !active;
    }
  }
  function reveal(hash) {
    if (!hash.startsWith("#")) return;
    let target;
    try {
      target = document.getElementById(decodeURIComponent(hash.slice(1)));
    } catch {
      return;
    }
    const panel = target?.closest('[role="tabpanel"]');
    if (panel?.parentElement.hasAttribute("data-tab-group"))
      activate(document.getElementById(panel.getAttribute("aria-labelledby")));
  }
  for (const group of groups) {
    const bar = group.querySelector(':scope > [role="tablist"]');
    const tabs = [...bar.querySelectorAll('[role="tab"]')];
    activate(tabs[0]);
    group.setAttribute("data-tabs-ready", "");
    bar.addEventListener("click", (event) => {
      const tab = event.target.closest('[role="tab"]');
      if (tab) activate(tab);
    });
    bar.addEventListener("keydown", (event) => {
      const index = tabs.indexOf(event.target);
      if (index < 0) return;
      const next = {
        Home: 0,
        End: tabs.length - 1,
        ArrowLeft: (index - 1 + tabs.length) % tabs.length,
        ArrowRight: (index + 1) % tabs.length,
      }[event.key];
      if (next === undefined) return;
      event.preventDefault();
      activate(tabs[next]);
      tabs[next].focus();
    });
  }
  // Reveal the panel before the common reader scrolls to the matching source or search hit.
  document.addEventListener(
    "click",
    (event) => {
      const link = event.target.closest('a[href^="#"]');
      if (link) reveal(link.hash);
    },
    true,
  );
  window.addEventListener("hashchange", () => reveal(location.hash));
  reveal(location.hash);
})();
