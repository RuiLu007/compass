(() => {
  const table = [...document.querySelectorAll("table")].find((item) =>
    item.tHead?.textContent.includes("一级分类 / category_id"),
  );
  if (!table) throw new Error("Missing classification catalog table");
  const rows = [...table.tBodies[0].rows];
  const panel = document.createElement("div");
  panel.className = "scene-filters";
  panel.innerHTML = `<label>业务域<select aria-label="筛选业务域"><option value="">全部业务域</option></select></label>
    <label>复杂度<select aria-label="筛选复杂度"><option value="">全部复杂度</option></select></label>
    <label class="scene-search">搜索场景<input type="search" aria-label="搜索场景" placeholder="场景编号、任务或特征描述"></label>
    <button type="button">重置</button><span role="status" aria-live="polite"></span>`;
  table.parentElement.before(panel);
  const [category, difficulty] = panel.querySelectorAll("select");
  const search = panel.querySelector("input");
  const count = panel.querySelector('[role="status"]');
  for (const [select, index] of [[category, 0], [difficulty, 3]]) {
    for (const value of new Set(rows.map((row) => row.cells[index].textContent.trim()))) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = index === 0 ? value.split(" / ")[0] : value;
      select.append(option);
    }
  }
  const empty = document.createElement("p");
  empty.className = "scene-empty";
  empty.textContent = "没有匹配的场景，请调整筛选条件。";
  table.parentElement.after(empty);
  function update() {
    const query = search.value.trim().toLowerCase();
    let visible = 0;
    for (const row of rows) {
      row.hidden = Boolean(
        (category.value && row.cells[0].textContent.trim() !== category.value) ||
        (difficulty.value && row.cells[3].textContent.trim() !== difficulty.value) ||
        (query && !row.textContent.toLowerCase().includes(query)),
      );
      if (!row.hidden) visible++;
    }
    count.textContent = `${visible} / ${rows.length} 个场景`;
    empty.hidden = visible > 0;
  }
  panel.addEventListener("input", update);
  panel.addEventListener("change", update);
  panel.querySelector("button").addEventListener("click", () => {
    category.value = difficulty.value = search.value = "";
    update();
  });
  update();
})();
