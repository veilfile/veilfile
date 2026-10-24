// The shell's behavior: the corner menu, the display preferences, in-place chapter changes and the cloth's loader (spec 4.4, 4.5, 6.3, 6.4).
// How it works, and why: AGENTS.md, "How shell.js works" (named below by its headings).

const THEME_KEY = "veilfile-theme";
const MOTION_KEY = "veilfile-motion";
const SLOW_KEY = "veilfile-veil-off"; // in sessionStorage; shell.html's boot script reads it
const APPEARANCE_ORDER = ["dark", "system", "light"]; // Ink, Auto, Paper
const APPEARANCE_WORD = { dark: "Ink", system: "Auto", light: "Paper" };
const PHONE = window.matchMedia("(max-width: 63.99rem)"); // the open menu is a full sheet
const root = document.documentElement;
const REDUCE = window.matchMedia("(prefers-reduced-motion: reduce)");
const FONT_WAIT_MS = 400; // the longest a new chapter stays invisible for a font that is still loading
const SCROLL_SETTLE_MS = 200; // a scrolled page's position is remembered after it stops moving
const FETCH_TIMEOUT_MS = 8000; // no answer by then: an ordinary page load

function save(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (error) {
    // Storage is blocked: the choice lasts for this page only.
  }
}

let sheetStep;
let closeSheet;
let whenShut = () => Promise.resolve(); // set by setUpMenu: a promise for when the sheet is shut, which MENU pressed again during a close takes back (Choosing)
let reach = () => {}; // the menu's updateInert: asked again whenever `away` changes
let away = false; // <main> holds the old page, faded out by a change from the sheet: out of reach until the swap (Choosing)
function holdMain(on) {
  away = on;
  reach();
}

// Loads the faces any page of this layout may show, so that no change waits on one (Faces). A promise for when they have.
const loadFaces = () => Promise.all((document.body.dataset.faces || "").split("|").filter(Boolean).map((face) => document.fonts.load(face).catch(() => {})));

// One value stepped once per drawn frame, for the sheet and for <main>'s fade (Sheet driver). write(v, to) is told each value.
function driver(write, opening, closing, v = 0) {
  let to = v, last = 0, rest = () => {};
  const step = (time) => {
    if (v !== to && time !== last) {
      const [ms, k] = to ? opening : closing;
      const q = (1 - v) ** (1 / k) - (to ? 1 : -1) * Math.min(.25, (time - (last || time)) / ms);
      v = 1 - Math.min(1, Math.max(0, q)) ** k;
      last = time;
      write(v, to);
    }
    return v;
  };
  const frame = (time) => step(time) === to ? rest() : requestAnimationFrame(frame);
  return {
    step,
    go(goal, atOnce) { // a promise for when it is at rest, or has been turned round
      rest();
      to = goal;
      last = 0;
      if (atOnce) v = to;
      write(v, to);
      return new Promise((resolve) => {
        rest = resolve;
        if (v === to) resolve();
        else requestAnimationFrame(frame);
      });
    },
  };
}

// <main>'s fade when a change is chosen from the phone sheet (Choosing): out with the sheet, in once the new page is there.
const fade = driver((v, to) => {
  const main = document.querySelector("main");
  const driven = v < 1 || !to;
  main.style.transition = driven ? "none" : "";
  main.style.opacity = driven ? v : "";
}, [170, 5], [180, 1.7], 1);

function setUpMenu(nav) {
  const toggle = nav.querySelector("[data-menu-toggle]");
  const list = nav.querySelector("[data-menu-list]");
  if (!toggle || !list) return;
  const isShellMenu = Boolean(nav.closest("header"));
  const isOpen = () => toggle.getAttribute("aria-expanded") === "true";
  // Under the phone sheet everything is inert, so Tab stays in the menu.
  const covered = isShellMenu
    ? [...document.querySelectorAll(".skip-link, main, .shell-corner")].filter((part) => !part.contains(nav))
    : [];
  const updateInert = () => {
    const sheet = isOpen() && PHONE.matches;
    covered.forEach((part) => { part.inert = sheet || (away && part.localName === "main"); });
  };
  const shut = [];
  // The sheet's fade (spec 4.4): one value, --sheet, stepped per drawn frame (Sheet driver).
  const sheet = driver((v, to) => {
    [list, ...list.children, ...covered].forEach((reader) => reader.style.setProperty("--sheet", v)); // not inherited: see base.css
    list.classList.toggle("is-open", to || v);
    if (!(to || v)) shut.splice(0).forEach((resolve) => resolve());
  }, [240, 5], [180, 1.7]);
  if (isShellMenu) {
    sheetStep = sheet.step;
    closeSheet = () => setOpen(false, false);
    whenShut = () => (list.classList.contains("is-open") ? new Promise((resolve) => shut.push(resolve)) : Promise.resolve());
    reach = updateInert;
  }
  const setOpen = (open, returnFocus, atOnce) => {
    toggle.setAttribute("aria-expanded", String(open));
    const rest = sheet.go(+open, atOnce || instant() || !PHONE.matches || !isShellMenu);
    updateInert();
    if (open) loadFaces();
    if (!open && returnFocus) toggle.focus();
    return rest;
  };
  toggle.addEventListener("click", () => setOpen(!isOpen(), false));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && isOpen()) setOpen(false, true);
  });
  // A click outside the menu closes it, and a tap on the sheet's own surface (Backdrop tap).
  document.addEventListener("click", (event) => {
    if (isOpen() && !nav.contains(event.target)) setOpen(false, false);
  });
  list.addEventListener("click", (event) => {
    if (isOpen() && PHONE.matches && event.target === list) setOpen(false, false);
  });
  PHONE.addEventListener("change", updateInert);
  // A cross-fade is about to start: the sheet closes first (Leaving).
  document.addEventListener("veilfile:leaving", () => {
    if (isShellMenu && PHONE.matches) setOpen(false, false, true);
  });
  // The menu starts closed on every page and only MENU opens it (spec 4.4); a change in place leaves it closed.
  document.addEventListener("veilfile:swapped", () => {
    if (isShellMenu) setOpen(false, false);
  });
}

function setUpAppearance(button) {
  const show = () => {
    const word = APPEARANCE_WORD[root.getAttribute("data-theme")] || "Auto";
    button.textContent = word;
    button.setAttribute("aria-label", `Appearance: ${word}`);
  };
  button.addEventListener("click", () => {
    const current = APPEARANCE_ORDER.indexOf(root.getAttribute("data-theme"));
    const next = APPEARANCE_ORDER[(current + 1) % APPEARANCE_ORDER.length];
    root.setAttribute("data-theme", next);
    save(THEME_KEY, next);
    show();
  });
  show();
}

function setUpMotion(button) {
  // The visible word is FX, the name "FX: motion" (controls.html); aria-pressed carries the state.
  const state = button.querySelector("[data-motion-state]");
  const show = () => {
    const on = root.getAttribute("data-motion") !== "off";
    button.setAttribute("aria-pressed", String(on));
    if (state) state.textContent = on ? "· on" : "· off";
  };
  button.addEventListener("click", () => {
    const next = root.getAttribute("data-motion") === "off" ? "on" : "off";
    if (next === "on") { // asked for again: the cloth may try once more
      delete root.dataset.veilOff;
      try {
        sessionStorage.removeItem(SLOW_KEY);
      } catch (error) {
        // Storage is blocked: nothing was remembered.
      }
    }
    root.setAttribute("data-motion", next);
    save(MOTION_KEY, next);
  });
  // Follow data-motion however it changes, the cloth's automatic Off included.
  new MutationObserver(show).observe(root, { attributes: true, attributeFilter: ["data-motion"] });
  show();
}

// The cloth's loader (spec 3.3, 6.3, 8, 9): when veil.js is fetched, what a failure does (Cloth loader).
let veil = null; // set while a cloth is starting or running
function veilOff(reason) {
  root.dataset.veilOff = reason;
  root.setAttribute("data-motion", "off");
  if (reason === "slow") {
    try {
      sessionStorage.setItem(SLOW_KEY, "slow");
    } catch (error) {
      // Storage is blocked: the next page measures again.
    }
  }
}
function dropContext(gl) {
  const lose = gl.getExtension("WEBGL_lose_context");
  if (lose) lose.loseContext();
}
async function startVeil() {
  const src = document.body.dataset.veilSrc;
  if (veil || !src || root.getAttribute("data-motion") !== "on" || root.dataset.veilOff) return;
  if (window.matchMedia("(forced-colors: active)").matches) { // forced text colours could sit on a light cloth
    veilOff("forced-colors");
    return;
  }
  const canvas = document.createElement("canvas");
  const gl = canvas.getContext("webgl", { alpha: false, antialias: false, depth: false, failIfMajorPerformanceCaveat: true, powerPreference: "low-power" });
  if (!gl) {
    veilOff("no-webgl");
    return;
  }
  veil = true;
  try {
    const { start } = await import(src);
    if (root.getAttribute("data-motion") !== "on") { // switched off while the module was on its way
      dropContext(gl);
      veil = null;
      return;
    }
    start({ canvas, gl, off: veilOff, ended: () => { veil = null; }, sheet: sheetStep });
  } catch (error) {
    dropContext(gl);
    veil = null;
    veilOff("error");
  }
}
function setUpVeil() {
  if (document.readyState === "complete") startVeil();
  else window.addEventListener("load", startVeil, { once: true });
  new MutationObserver(startVeil).observe(root, { attributes: true, attributeFilter: ["data-motion"] });
}

// Narrow screens: the page fades out along the top (spec 4.1); --fade-y is the scroll offset (Top fade).
function setUpTopFade() {
  const place = () => {
    if (PHONE.matches) document.querySelector(".shell-main").style.setProperty("--fade-y", `${window.scrollY}px`);
  };
  let queued = false;
  const queue = () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; place(); });
  };
  window.addEventListener("scroll", queue, { passive: true });
  PHONE.addEventListener("change", queue);
  document.addEventListener("veilfile:swapped", place);
  place();
}

// In-place chapter changes (spec 4.5): the latest click wins, anything unexpected is an ordinary load (Changing in place).
let changes = 0;
let crossfading = null; // the view transition that cross-fades <main> now, the newest one
let shown = window.location.pathname + window.location.search; // the page now in <main>

// The same rule as base.css, and as instant() in come-in.js.
function instant() {
  const motion = root.getAttribute("data-motion");
  return motion === "off" || (motion === null && REDUCE.matches);
}

// The --change-fade token (".35s") in ms; unreadable: 350.
function fadeMs() {
  const match = /^\s*([\d.]+)(m?s)\s*$/.exec(getComputedStyle(root).getPropertyValue("--change-fade"));
  const ms = match ? parseFloat(match[1]) * (match[2] === "s" ? 1000 : 1) : NaN;
  return Number.isFinite(ms) ? ms : 350;
}

async function changeTo(url, push, scroll = 0, sheeted = false) {
  const change = ++changes;
  const layout = document.body.dataset.layout;
  const main = document.querySelector("main");
  const motion = !instant();
  sheeted = sheeted && motion;
  let loaded = false;
  const faces = loadFaces().then(() => { loaded = true; });
  // From the open phone sheet nothing cross-fades; view transitions cross-fade otherwise, and anything else fades out from the click and in at the swap.
  const crossfade = motion && !sheeted && Boolean(document.startViewTransition);
  let faded;
  if (sheeted) {
    // The sheet closes by its driver and the old content fades out with it, while the page is on its way (Choosing).
    if (crossfading) crossfading.skipTransition();
    holdMain(true);
    faded = Promise.all([closeSheet(), fade.go(0)]);
  } else {
    holdMain(false);
    fade.go(1, true); // a change overtaken from the sheet leaves <main> faded
    if (motion && !crossfade) {
      main.classList.add("is-leaving");
      // Gone when its fade has ended; the timer is for a page whose animations do not run.
      const ended = main.getAnimations().map((animation) => animation.finished.catch(() => {}));
      faded = Promise.race([Promise.all(ended), new Promise((resolve) => setTimeout(resolve, 2 * fadeMs()))]);
    }
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  let next;
  try {
    const response = await fetch(url, { headers: { Accept: "text/html" }, credentials: "same-origin", signal: controller.signal });
    const type = response.headers.get("Content-Type") || "";
    if (!response.ok || !type.includes("text/html") || new URL(response.url).pathname !== new URL(url).pathname) {
      throw new Error("not this page");
    }
    next = new DOMParser().parseFromString(await response.text(), "text/html");
    if (!layout || next.body.dataset.layout !== layout || !next.querySelector("main")) throw new Error("another layout");
  } catch (error) {
    // An overtaken change leaves the newer one's fade alone.
    if (change !== changes) return;
    main.classList.remove("is-leaving");
    holdMain(false);
    fade.go(1);
    window.location.assign(url);
    return;
  } finally {
    clearTimeout(timer);
  }
  await faded; // the old content has gone, and the page has come
  if (change !== changes) return;
  if (sheeted) { // MENU pressed again during the close opened the sheet again: the swap waits until it is shut (Choosing)
    await whenShut();
    if (change !== changes) return;
  }
  let late; // the longest a change waits for a face, from the first wait on, and made only when a face is still loading (Faces)
  const bounded = (promise) => Promise.race([promise, late = late || new Promise((resolve) => setTimeout(resolve, FONT_WAIT_MS))]);
  if (motion && !loaded) {
    await bounded(faces);
    if (change !== changes) return;
  }
  const swap = async () => {
    // Called a frame or more after the click: a newer click in the gap must find nothing done.
    if (change !== changes) return;
    try {
      const from = window.scrollY; // where the old content was seen
      main.replaceChildren(...next.querySelector("main").childNodes);
      document.title = next.title;
      document.head.querySelectorAll("[data-page-meta]").forEach((element) => element.remove());
      const meta = document.createDocumentFragment();
      next.head.querySelectorAll("[data-page-meta]").forEach((element) => meta.append(element));
      document.head.querySelector("title").after(meta);
      document.body.className = next.body.className;
      for (const link of document.querySelectorAll("#shell-menu a")) {
        const twin = next.querySelector(`#shell-menu a[href="${link.getAttribute("href")}"]`);
        const mark = twin && twin.getAttribute("aria-current");
        if (mark) link.setAttribute("aria-current", mark);
        else link.removeAttribute("aria-current");
      }
      if (push) history.pushState(null, "", url);
      shown = new URL(url).pathname + new URL(url).search;
      window.scrollTo(0, scroll); // also lays the new content out, which asks for its fonts
      // --slide: how far apart the old picture and the new content are, so nothing slides (base.css).
      if (crossfade) root.style.setProperty("--slide", `${window.scrollY - from}px`);
      // Hidden until a font it asked for is there, or fallback text flashes (bounded; nothing waits, and no timer is made, when no face is loading).
      if (motion && document.fonts.status === "loading") {
        await bounded(document.fonts.ready);
        if (change !== changes) return;
      }
      main.classList.remove("is-leaving");
      holdMain(false);
      fade.go(1);
      // The menu follows the page first, then focus moves: an inert heading cannot take it.
      document.dispatchEvent(new CustomEvent("veilfile:swapped"));
      const heading = main.querySelector("h1");
      if (heading) {
        heading.tabIndex = -1;
        heading.focus({ preventScroll: true });
      }
    } catch (error) {
      // Failed part-way: an ordinary load puts right whatever is half changed.
      main.classList.remove("is-leaving");
      holdMain(false);
      fade.go(1);
      window.location.assign(url);
    }
  };
  // Fetched before this, never inside it: a view transition pauses the page's drawing until swap is done.
  if (crossfade) {
    document.dispatchEvent(new CustomEvent("veilfile:leaving"));
    // <main> carries the name only while it cross-fades (View transition name).
    main.style.setProperty("view-transition-name", "chapter");
    const transition = crossfading = document.startViewTransition(swap);
    const release = () => {
      if (crossfading !== transition) return;
      crossfading = null;
      main.style.removeProperty("view-transition-name");
      root.style.removeProperty("--slide");
    };
    transition.finished.then(release, release);
  } else await swap();
}

// Where the page is scrolled, kept in its history entry (Scroll).
function rememberScroll() {
  if (history.scrollRestoration !== "manual" || window.location.pathname + window.location.search !== shown) return;
  try {
    history.replaceState({ ...history.state, scroll: window.scrollY }, "");
  } catch (error) {
    // Not remembered: Back shows the page from the top.
  }
}

// Put the remembered position back after a reload or a full load.
function restoreScroll() {
  const saved = history.state && history.state.scroll;
  if (history.scrollRestoration === "manual" && typeof saved === "number") window.scrollTo(0, saved);
}

function setUpChapterChanges() {
  let scrolling;
  window.addEventListener("scroll", () => {
    clearTimeout(scrolling);
    scrolling = setTimeout(rememberScroll, SCROLL_SETTLE_MS);
  }, { passive: true });
  window.addEventListener("beforeunload", rememberScroll);
  window.addEventListener("pagehide", rememberScroll);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") rememberScroll();
  });
  restoreScroll();
  document.addEventListener("click", (event) => {
    const link = event.target.closest("a[href]");
    if (!link || event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if ((link.target && link.target !== "_self") || link.hasAttribute("download") || link.hasAttribute("data-come-in")) return;
    if (!document.body.dataset.layout) return;
    const url = new URL(link.href, window.location.href);
    if (url.origin !== window.location.origin) return;
    if (url.hash) return; // a #fragment is the browser's to scroll to
    // Only a page of this layout changes in place.
    if (!(document.body.dataset.layoutPaths || "").split(" ").includes(url.pathname)) return;
    event.preventDefault();
    const push = url.href !== window.location.href;
    if (push && window.location.pathname + window.location.search === shown) {
      // Leaving this page: remember its scroll by hand (Scroll).
      try {
        history.scrollRestoration = "manual";
      } catch (error) {
        // Left to the browser.
      }
      rememberScroll();
    }
    const sheeted = PHONE.matches && Boolean(link.closest("#shell-menu.is-open"));
    changeTo(url.href, push, 0, sheeted);
  });
  window.addEventListener("popstate", () => {
    // A jump within the page fires popstate too; the browser has done it.
    if (window.location.pathname + window.location.search === shown) return;
    // Back and Forward: the entry as it was left.
    if (document.body.dataset.layout) changeTo(window.location.href, false, (history.state && history.state.scroll) || 0);
  });
}

try {
  document.querySelectorAll("[data-menu]").forEach(setUpMenu);
  document.querySelectorAll("[data-appearance-toggle]").forEach(setUpAppearance);
  document.querySelectorAll("[data-motion-toggle]").forEach(setUpMotion);
  setUpChapterChanges();
  setUpTopFade();
  setUpVeil();
  root.classList.add("shell-ready"); // the switches show the saved choices (controls.css)
} catch (error) {
  // Half set up is worse than none: the no-JavaScript layout, and report the error.
  root.classList.remove("js");
  throw error;
}
