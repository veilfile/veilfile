// Come in (spec 4.2; AGENTS.md, "How shell.js works": Behaviours).
const REDUCE = window.matchMedia("(prefers-reduced-motion: reduce)");

// The --fade token (".7s") in ms; unreadable: 700.
function fadeMs() {
  const match = /^\s*([\d.]+)(m?s)\s*$/.exec(getComputedStyle(document.documentElement).getPropertyValue("--fade"));
  const ms = match ? parseFloat(match[1]) * (match[2] === "s" ? 1000 : 1) : NaN;
  return Number.isFinite(ms) ? ms : 700;
}

// The same rule as base.css, and as instant() in shell.js.
function instant() {
  const motion = document.documentElement.getAttribute("data-motion");
  return motion === "off" || (motion === null && REDUCE.matches);
}

document.addEventListener("click", async (event) => {
  const enter = event.target.closest("[data-come-in]");
  if (!enter || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const threshold = enter.closest("[data-threshold]");
  const install = threshold && threshold.querySelector("[data-install]");
  if (!install) return;
  event.preventDefault();
  if (threshold.dataset.entering) return; // a second click while fading
  threshold.dataset.entering = "true";
  const leaving = [...threshold.querySelectorAll("[data-threshold-line]"), enter];
  if (!instant()) {
    leaving.forEach((part) => part.classList.add("is-faded"));
    await new Promise((resolve) => setTimeout(resolve, fadeMs()));
  }
  leaving.forEach((part) => { part.hidden = true; });
  install.classList.add("is-faded");
  install.hidden = false;
  install.getBoundingClientRect(); // lay it out while invisible, then let it fade in
  install.classList.remove("is-faded");
  const copy = install.querySelector('[role="tabpanel"]:not([hidden]) [data-copy]');
  if (copy) copy.focus({ preventScroll: true });
});
