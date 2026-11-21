(() => {
  const sections = JSON.parse(document.getElementById("chapter-originals").textContent);
  const original = document.getElementById("original-dialog");
  const heading = document.getElementById("original-heading");
  const copy = original.querySelector(".original-copy");
  let opener;
  for (const button of document.querySelectorAll("[data-original]")) {
    button.addEventListener("click", () => {
      const chapter = sections[Number(button.dataset.original)];
      heading.textContent = chapter.title + " · 主方案原文";
      copy.innerHTML = chapter.html;
      for (const table of copy.querySelectorAll("table")) {
        const region = document.createElement("div");
        region.className = "source-table";
        region.tabIndex = 0;
        region.setAttribute("role", "region");
        region.setAttribute("aria-label", "原文表格");
        table.before(region);
        region.append(table);
      }
      opener = button;
      original.showModal();
      original.scrollTop = 0;
      original.querySelector("button").focus();
    });
  }
  original.querySelector("button").addEventListener("click", () => original.close());
  original.addEventListener("close", () => {
    copy.replaceChildren();
    opener?.focus({ preventScroll: true });
  });
  original.addEventListener("click", (event) => {
    if (event.target !== original) return;
    const bounds = original.getBoundingClientRect();
    if (
      event.clientX < bounds.left ||
      event.clientX > bounds.right ||
      event.clientY < bounds.top ||
      event.clientY > bounds.bottom
    )
      original.close();
  });
  for (const group of document.querySelectorAll("[data-switcher]")) {
    const options = group.querySelectorAll('input[type="radio"]');
    const panels = group.querySelectorAll("[data-panel]");
    function select() {
      const current = Array.from(options).find((option) => option.checked);
      for (const panel of panels) panel.hidden = panel.dataset.panel !== current.value;
    }
    group.addEventListener("change", select);
    select();
  }
  const menu = document.querySelector(".chapter-nav");
  document.addEventListener("pointerdown", (event) => {
    if (menu.open && !menu.contains(event.target)) menu.open = false;
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && menu.open) {
      menu.open = false;
      menu.querySelector("summary").focus();
    }
  });
  const printState = new Map();
  window.addEventListener("beforeprint", () => {
    for (const item of document.querySelectorAll(".document details:not(.source-details)")) {
      printState.set(item, item.open);
      item.open = true;
    }
  });
  window.addEventListener("afterprint", () => {
    for (const [item, wasOpen] of printState) item.open = wasOpen;
    printState.clear();
  });
})();
