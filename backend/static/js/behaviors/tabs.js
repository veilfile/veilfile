// Tabs (spec 9): a click or the arrow, Home and End keys choose a tab. Delegated, so swapped-in tabs work.
function choose(tab) {
  for (const other of tab.closest('[role="tablist"]').querySelectorAll('[role="tab"]')) {
    const selected = other === tab;
    other.setAttribute("aria-selected", String(selected));
    other.tabIndex = selected ? 0 : -1;
    const panel = document.getElementById(other.getAttribute("aria-controls"));
    if (panel) panel.hidden = !selected;
  }
}

document.addEventListener("click", (event) => {
  const tab = event.target.closest('[data-tabs] [role="tab"]');
  if (tab) choose(tab);
});

document.addEventListener("keydown", (event) => {
  const tab = event.target.closest('[data-tabs] [role="tab"]');
  if (!tab) return;
  if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return; // the browser's shortcuts (Back, Forward)
  const tabs = [...tab.closest('[role="tablist"]').querySelectorAll('[role="tab"]')];
  const index = tabs.indexOf(tab);
  const next = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: tabs.length - 1 }[event.key];
  if (next === undefined) return;
  event.preventDefault();
  const target = tabs[(next + tabs.length) % tabs.length];
  choose(target);
  target.focus();
});
