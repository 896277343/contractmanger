document.addEventListener("DOMContentLoaded", async () => {
  const input = document.querySelector('input[name="country"]');
  const list = document.getElementById("country-suggestions");
  const preview = document.getElementById("country-continent-preview");
  if (!input || !list || !preview) return;
  const continents = {AF: "非洲", AN: "南极洲", AS: "亚洲", EU: "欧洲", NA: "北美洲", OC: "大洋洲", SA: "南美洲"};
  let rows;
  try {
    const response = await fetch(input.dataset.countrySource, {credentials: "same-origin"});
    if (!response.ok) return;
    rows = Object.entries(await response.json()).map(([code, row]) => ({code, ...row}));
  } catch (_) { return; }

  list.style.cssText = "display:none;position:absolute;z-index:1000;max-height:240px;overflow:auto;background:var(--body-bg,#fff);border:1px solid #aaa;min-width:280px;box-shadow:0 4px 12px #0002";
  function normalized(value) { return value.toLowerCase().replace(/[\s'’-]/g, ""); }
  function match(row, term) {
    return [row.code, row.name, row.zh, row.pinyin, row.initials].some(value => normalized(value).includes(term));
  }
  function exact(value) {
    const term = normalized(value);
    return rows.find(row => [row.code, row.name, row.zh, row.pinyin].some(candidate => normalized(candidate) === term));
  }
  function showContinent(row) {
    preview.textContent = row ? `所属大洲：${continents[row.continent] || ""}` : "";
    const saved = document.querySelector(".field-continent .readonly");
    if (saved && row) saved.textContent = continents[row.continent] || "";
  }
  function select(row) {
    input.value = row.name;
    list.style.display = "none";
    showContinent(row);
    input.dispatchEvent(new Event("change", {bubbles: true}));
  }
  function render() {
    const term = normalized(input.value.trim());
    list.replaceChildren();
    showContinent(exact(input.value));
    if (!term) { list.style.display = "none"; return; }
    const matches = rows.filter(row => match(row, term)).slice(0, 10);
    for (const row of matches) {
      const option = document.createElement("button");
      option.type = "button";
      option.setAttribute("role", "option");
      option.textContent = `${row.name} · ${row.zh} · ${row.code} · ${row.pinyin}`;
      option.style.cssText = "display:block;width:100%;text-align:left;padding:6px 8px;border:0;background:transparent;cursor:pointer;color:inherit";
      option.addEventListener("mousedown", event => { event.preventDefault(); select(row); });
      list.appendChild(option);
    }
    list.style.display = matches.length ? "block" : "none";
  }
  input.addEventListener("input", render);
  input.addEventListener("focus", render);
  input.addEventListener("keydown", event => {
    if (event.key === "Escape") list.style.display = "none";
    if (event.key === "Enter" && list.style.display !== "none" && list.firstElementChild) {
      event.preventDefault();
      list.firstElementChild.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, cancelable: true}));
    }
  });
  input.addEventListener("blur", () => {
    const row = exact(input.value);
    if (row) select(row);
    list.style.display = "none";
  });
  showContinent(exact(input.value));
});
