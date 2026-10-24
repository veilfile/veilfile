// Copy (spec 4.2; AGENTS.md, "How shell.js works": Behaviours).
const SHOWN_MS = 1800;
const ANNOUNCE_DELAY_MS = 60; // between emptying the status and setting it

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) return;
  const source = document.getElementById(button.dataset.copy);
  if (!source) return;
  const command = source.querySelector("[data-copy-text]") || source;
  const scope = button.closest("[data-tabs], [data-code-block]") || document;
  const status = scope.querySelector("[data-copy-status]");
  let copied = false;
  try {
    await navigator.clipboard.writeText(command.textContent.trim());
    copied = true;
  } catch (error) {
    const range = document.createRange();
    range.selectNodeContents(command);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  }
  button.textContent = copied ? "Copied" : "Selected";
  if (status) {
    // Emptied first, or a second Copy within 1.8 s might not be read out.
    status.textContent = "";
    clearTimeout(status.announce);
    status.announce = setTimeout(() => {
      status.textContent = copied ? "Copied" : "Command selected; copy it with your keyboard";
    }, ANNOUNCE_DELAY_MS);
  }
  const then = button.closest('[role="tabpanel"]')?.querySelector("[data-copy-then]");
  if (then) then.classList.add("is-shown");
  clearTimeout(button.copyReset);
  button.copyReset = setTimeout(() => {
    button.textContent = "Copy";
    if (status) status.textContent = "";
  }, SHOWN_MS);
});
