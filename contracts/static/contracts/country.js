document.addEventListener("DOMContentLoaded", () => {
  const input = document.querySelector('input[name="country"]');
  const list = document.getElementById("country-code-options");
  if (!input || !list) return;
  const label = document.createElement("span");
  label.className = "help";
  label.style.marginLeft = "0.6em";
  input.insertAdjacentElement("afterend", label);
  const names = new Map(Array.from(list.options, option => [option.value, option.label]));
  const showName = () => { label.textContent = names.get(input.value.trim().toUpperCase()) || ""; };
  input.addEventListener("input", showName);
  showName();
});
