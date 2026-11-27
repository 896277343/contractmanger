document.addEventListener("DOMContentLoaded", () => {
  const signed = document.getElementById("id_signed_on");
  const ship = document.getElementById("id_agreed_ship_on");
  const business = document.getElementById("id_business_type");
  const customs = document.getElementById("id_customs_declared");
  let shipEdited = ship?.dataset.savedValue === "1";
  let customsEdited = customs?.dataset.savedValue === "1";

  function addThreeMonths(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
    if (!match) return "";
    const year = Number(match[1]);
    const month = Number(match[2]);
    const day = Number(match[3]);
    const shifted = new Date(Date.UTC(year, month - 1 + 3, 1));
    const lastDay = new Date(Date.UTC(shifted.getUTCFullYear(), shifted.getUTCMonth() + 1, 0)).getUTCDate();
    return `${shifted.getUTCFullYear()}-${String(shifted.getUTCMonth() + 1).padStart(2, "0")}-${String(Math.min(day, lastDay)).padStart(2, "0")}`;
  }

  if (signed && ship) {
    ship.addEventListener("input", () => { shipEdited = true; });
    signed.addEventListener("change", () => {
      if (!shipEdited) ship.value = addThreeMonths(signed.value);
    });
  }
  if (business && customs) {
    customs.addEventListener("change", () => { customsEdited = true; });
    business.addEventListener("change", () => {
      if (!customsEdited) customs.value = business.value === "外销" ? "true" : "false";
    });
  }
});
