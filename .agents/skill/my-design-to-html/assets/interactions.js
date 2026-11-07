(() => {
  "use strict";
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const { search: searchIndex, codes } = JSON.parse($("#document-data").textContent);
  const toast = $(".toast");
  let toastTimer;
  function announce(message) {
    toast.textContent = message;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      toast.textContent = "";
    }, 2400);
  }
  async function copy(text) {
    try {
      if (!navigator.clipboard) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(text);
    } catch {
      const field = document.createElement("textarea");
      field.value = text;
      field.style.cssText = "position:fixed;left:-10000px;top:0";
      document.body.append(field);
      field.select();
      const succeeded = document.execCommand("copy");
      field.remove();
      if (!succeeded) {
        announce("请选中代码后复制");
        return;
      }
    }
    announce("代码已复制");
  }
  $$("[data-copy]").forEach((button) =>
    button.addEventListener("click", () => {
      void copy(codes[Number(button.dataset.copy)]);
    }),
  );
  $$("[data-expand]").forEach((button) =>
    button.addEventListener("click", () => {
      const expanded = button.closest(".code-block").classList.toggle("expanded");
      button.textContent = expanded ? "收起代码" : "展开代码";
      button.setAttribute("aria-expanded", String(expanded));
    }),
  );
  function reveal(id) {
    const node = document.getElementById(id);
    if (!node) return;
    let parent = node.parentElement;
    while (parent) {
      if (parent.tagName === "DETAILS") parent.open = true;
      parent = parent.parentElement;
    }
    const code = node.closest(".code-block");
    if (code) {
      code.classList.add("expanded");
      const expand = $("[data-expand]", code);
      if (expand) {
        expand.textContent = "收起代码";
        expand.setAttribute("aria-expanded", "true");
      }
    }
    node.scrollIntoView({ block: "start" });
    if (!node.hasAttribute("tabindex")) node.setAttribute("tabindex", "-1");
    node.focus({ preventScroll: true });
  }
  document.addEventListener("click", (event) => {
    const link = event.target.closest('a[href^="#"]');
    if (!link) return;
    const id = decodeURIComponent(link.hash.slice(1));
    if (!document.getElementById(id)) return;
    event.preventDefault();
    $$(".mobile-nav[open]").forEach((details) => {
      details.open = false;
    });
    history.pushState(null, "", "#" + id);
    reveal(id);
  });
  window.addEventListener("hashchange", () => reveal(decodeURIComponent(location.hash.slice(1))));
  if (location.hash)
    requestAnimationFrame(() => reveal(decodeURIComponent(location.hash.slice(1))));

  const searchDialog = $("#search-dialog");
  const searchInput = $("#search-input");
  const results = $("#search-results");
  function search() {
    const query = searchInput.value.trim().toLowerCase();
    const words = query.split(/\s+/).filter(Boolean);
    const matches = searchIndex
      .filter((item) =>
        words.every((word) => (item.title + " " + item.text).toLowerCase().includes(word)),
      )
      .sort(
        (a, b) =>
          Number(b.title.toLowerCase().includes(query)) -
          Number(a.title.toLowerCase().includes(query)),
      );
    results.replaceChildren();
    for (const item of matches.slice(0, 25)) {
      const a = document.createElement("a");
      a.className = "search-result";
      a.href = "#" + item.id;
      const title = document.createElement("strong");
      title.textContent = item.title;
      const description = document.createElement("p");
      const index = query ? item.text.toLowerCase().indexOf(query) : 0;
      const start = Math.max(0, index - 24);
      description.textContent = item.text.slice(start, start + 110);
      a.append(title, description);
      a.addEventListener("click", () => searchDialog.close());
      results.append(a);
    }
    $("#search-meta").textContent = query
      ? "找到 " + matches.length + " 项" + (matches.length > 25 ? "，显示前 25 项" : "")
      : "输入章节、字段或关键词 · Tab 选择结果 · Enter 跳转";
  }
  $$("[data-search]").forEach((button) =>
    button.addEventListener("click", () => {
      searchDialog.showModal();
      search();
      searchInput.focus();
    }),
  );
  searchInput.addEventListener("input", search);
  searchInput.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      $(".search-result", results)?.focus();
    }
    if (event.key === "Enter") $(".search-result", results)?.click();
  });
  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      if (!searchDialog.open) searchDialog.showModal();
      search();
      searchInput.focus();
    }
  });
  $$("[data-close]").forEach((button) =>
    button.addEventListener("click", () => button.closest("dialog").close()),
  );
  $$("dialog").forEach((dialog) =>
    dialog.addEventListener("click", (event) => {
      if (event.target !== dialog) return;
      const bounds = dialog.getBoundingClientRect();
      if (
        event.clientX < bounds.left ||
        event.clientX > bounds.right ||
        event.clientY < bounds.top ||
        event.clientY > bounds.bottom
      )
        dialog.close();
    }),
  );

  const diagramDialog = $("#diagram-dialog");
  const zoomContent = $("#zoom-content");
  const zoomViewport = $("#zoom-viewport");
  let diagramSource,
    diagramName,
    svgWidth = 1,
    svgHeight = 1,
    scale = 1;
  function setScale(next) {
    scale = Math.max(0.07, Math.min(3, next));
    const svg = $("svg", zoomContent);
    if (!svg) return;
    svg.style.width = svgWidth * scale + "px";
    svg.style.height = svgHeight * scale + "px";
    zoomContent.style.width = svgWidth * scale + "px";
    $("#zoom-level").textContent = Math.round(scale * 100) + "%";
  }
  function fit() {
    setScale(
      Math.min(
        (zoomViewport.clientWidth - 64) / svgWidth,
        (zoomViewport.clientHeight - 64) / svgHeight,
      ),
    );
  }
  $$("[data-diagram]").forEach((button) =>
    button.addEventListener("click", () => {
      const figure = button.closest(".diagram");
      const original = $(".diagram-stage > svg", figure);
      diagramSource = original.outerHTML;
      diagramName = figure.dataset.title;
      $("#zoom-title").textContent = diagramName;
      const svg = original.cloneNode(true);
      // Namespace the copy so SVG marker references do not collide with the source.
      const nodes = [svg, ...$$("*", svg)];
      const ids = new Map(
        nodes.filter((node) => node.id).map((node) => [node.id, "zoom-" + node.id]),
      );
      for (const node of nodes) {
        for (const attribute of [...node.attributes]) {
          let value = attribute.value.replace(/url\(['"]?#([^)'"]+)['"]?\)/g, (match, id) =>
            ids.has(id) ? `url(#${ids.get(id)})` : match,
          );
          if (attribute.name === "id") value = ids.get(value);
          else if (/^(?:xlink:)?href$/.test(attribute.name) && ids.has(value.slice(1)))
            value = "#" + ids.get(value.slice(1));
          else if (/^aria-(?:labelledby|describedby)$/.test(attribute.name))
            value = value
              .split(/\s+/)
              .map((id) => ids.get(id) ?? id)
              .join(" ");
          node.setAttribute(attribute.name, value);
        }
        if (node.localName === "style")
          node.textContent = node.textContent.replace(/#([A-Za-z_][\w:-]*)/g, (match, id) =>
            ids.has(id) ? "#" + ids.get(id) : match,
          );
      }
      zoomContent.replaceChildren(svg);
      const viewBox = original.viewBox.baseVal;
      svgWidth = viewBox.width;
      svgHeight = viewBox.height;
      diagramDialog.showModal();
      fit();
      zoomViewport.scrollTop = 0;
      zoomViewport.scrollLeft = 0;
    }),
  );
  $$("[data-zoom]").forEach((button) =>
    button.addEventListener("click", () => {
      const mode = button.dataset.zoom;
      if (mode === "fit") fit();
      else setScale(mode === "actual" ? 1 : scale * (mode === "in" ? 1.3 : 1 / 1.3));
    }),
  );
  $("#download-svg").addEventListener("click", () => {
    if (!diagramSource) return;
    const url = URL.createObjectURL(
      new Blob([diagramSource], { type: "image/svg+xml;charset=utf-8" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = "design-" + diagramName.replace(/[^\w\u4e00-\u9fff-]+/g, "-") + ".svg";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });

  let scheduled = false;
  function updateReading() {
    scheduled = false;
    const total = document.documentElement.scrollHeight - innerHeight;
    $(".reading-progress").style.transform =
      "scaleX(" + (total > 0 ? Math.min(1, scrollY / total) : 0) + ")";
  }
  addEventListener(
    "scroll",
    () => {
      if (!scheduled) {
        scheduled = true;
        requestAnimationFrame(updateReading);
      }
    },
    { passive: true },
  );
  addEventListener("resize", updateReading);
  updateReading();
})();
