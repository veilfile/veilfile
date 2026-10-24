// The cloth (spec 3.1 to 3.4, 8): a fragment shader drawn on one fixed canvas behind the page, calmed everywhere
// so that small text keeps its contrast wherever it sits, with an ink wash on whatever the pointer or the keyboard
// is on. shell.js imports this module only when Motion is on and WebGL works, and calls start() with a canvas and
// its context; everything after that is decided here. The shader is the prototype's
// (docs/superpowers/specs/assets/), with the spec's additions marked where they differ.

const ZOOM = 0.6;
const MAX_INKS = 4;
const PACE = { threshold: 0.75, entered: 0.65, chapter: 0.6 };   // spec 3.1, 4.2: the cloth slows after Come in
// The calm (spec 3.1, 9): the cloth is drawn at CALM of its variation, then a soft floor lifts its darkest pixels in
// Paper and a soft ceiling lowers its brightest in Ink, so --muted (tokens.css) keeps 4.5:1 over every pixel the cloth
// can draw, grain included. The offsets are in luma (the shader's own, on the colours as drawn) from the appearance's
// paper: Paper may go FLOOR below it, Ink CEILING above it. They were measured over 1,495 moments of the cloth at 1280x800
// and 375x667 against --muted #534e47 and #aea79c, with a grain of up to 0.025; the grain is no stronger now (GRAIN_PAPER and
// GRAIN_INK, below), and over 1,496 moments with it --muted holds 4.682:1 at worst in Paper and 4.838:1 in Ink. A lighter --muted
// or a stronger cloth or grain needs them measured again (pages.tests_veil works the contrast out from these numbers). KNEE is how
// softly the limit takes hold.
const CALM = 0.70;
const FLOOR = 0.1163;
const CEILING = 0.1089;
const KNEE = 0.0004;
// The film grain (spec 3.1) is added to the finished cloth, per canvas pixel. GRAIN_PAPER and GRAIN_INK are the farthest it can move a
// pixel's luma, in each appearance (the contrast above is measured with them: pages.tests_veil); Ink's is weaker. It drifts: every pixel
// goes round its own phase GRAIN_RATE times a second, whatever the cloth's pace.
const GRAIN_PAPER = 0.025;
const GRAIN_INK = 0.018;
const GRAIN_RATE = 1.5;   // cycles a second
// The resolution, in canvas px per CSS px, follows the screen (spec 3.1, 8): the first step is SHARE of the device pixel ratio (a canvas
// pixel is 2.5 screen pixels), kept between LOW and TOP; each step after is STEP of the one above, down to LAST, which also takes a step
// that would come within 15% above it.
const SHARE = 0.4;
const TOP = 1.2;
const LOW = { desktop: 0.8, phone: 0.65 };
const LAST = { desktop: 0.35, phone: 0.28 };
const STEP = 0.75;
const SLOW_MS = 24;     // a window whose median frame is longer than this lowers the resolution
const HELPED = 0.85;    // ...and a step that leaves the median over SLOW_MS and not under this share of what caused it did not help (a frame rate capped by the display, not a slow GPU): it is undone
const CAPPED = "veilfile-veil-capped";   // in sessionStorage: the step the visit's ladder ended at, so the tab's next pages start there and do not probe again
const KEPT = "veilfile-veil-step";       // ...and the finest step a walk down has kept so far (a slow GPU that each step helped): the tab's next pages start there, not at the first step, and still only go down
const STILL_MS = 40;    // where the resolution cannot go lower or has stopped helping, longer than this switches to the still page (30 Hz, where iOS Low Power Mode caps frames, keeps the cloth)
const WINDOW = 60;      // frames per measurement
const WARMUP = 30;      // frames ignored after a start, a resume or a resolution change
const PHONE = window.matchMedia("(max-width: 40rem)");
const SHEETED = window.matchMedia("(max-width: 63.99rem)");   // the width at which the open menu is a sheet over the page (base.css, menu.css); shell.js' PHONE. From 64rem up it is a column, which the cloth does not draw
const DARK = window.matchMedia("(prefers-color-scheme: dark)");
const INK_AGE = 4;      // s: a wash spreads as it ages (the prototype's 2% a second), but only this long, so a held wash does not grow over its neighbours
const INK_TARGETS = ".interactive, .chapter :is(p, dd) a:not(.interactive, .label), .reading :is(p, li, td, th, blockquote, dd) a:not(.interactive, .label)";

const VERT = "attribute vec2 a; void main(){ gl_Position = vec4(a, 0.0, 1.0); }";
const FRAG = `precision highp float;
uniform vec2 u_res; uniform float u_time; uniform float u_theme; uniform float u_cloth; uniform float u_soft;   // u_soft: how many times coarser the canvas is than the device's own first step (1 on a healthy device)
uniform float u_grain;   // where the film grain has got to, in cycles (0 to 1, wrapping)
uniform float u_sheet;   // the phone menu's sheet, 0 (none) to 1: paper over the cloth, from nothing at the left edge to all of it at half the width
uniform vec4 u_inkP[${MAX_INKS}];   // centre x, y (canvas px), radius x, y
uniform vec4 u_inkQ[${MAX_INKS}];   // growth 0..1, strength 0..1, age (s), seed

float hash(vec2 p){ p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
float noise(vec2 p){ vec2 i = floor(p), f = fract(p); vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x), mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y); }
float fbm(vec2 p){ float v = 0.0, a = 0.5; mat2 m = mat2(1.6, 1.2, -1.2, 1.6);
  for (int i = 0; i < 4; i++) { v += a * noise(p); p = m * p; a *= 0.5; } return v; }

// Three random numbers for a pixel (Hoskins' hash without sine, whole pixel coordinates), and a triangle wave through one cycle, 0 to 1 to 0.
vec3 hash32(vec2 p){ vec3 q = fract(vec3(p.xyx) * vec3(0.1031, 0.1030, 0.0973)); q += dot(q, q.yxz + 33.33); return fract((q.xxy + q.yzz) * q.zyx); }
float tri(float x){ return abs(fract(x) * 2.0 - 1.0); }

const vec3 L = vec3(-0.451, 0.551, 0.702);      // key light from the upper left
const vec3 H = vec3(-0.2445, 0.2986, 0.9225);   // halfway between the light and the eye
// The five colours of each appearance (spec 3.2), blended so a switch fades across.
vec3 PAPER, LIT, SHADE, SHEEN, WASH;
void appearance(float k){
  PAPER = mix(vec3(0.922, 0.902, 0.871), vec3(0.104, 0.098, 0.091), k);   // paper | warm slate
  LIT   = mix(vec3(0.992, 0.952, 0.880), vec3(0.300, 0.302, 0.312), k);   // vanilla | faint cool light
  SHADE = mix(vec3(0.735, 0.728, 0.712), vec3(0.052, 0.049, 0.046), k);   // cool grey | near black
  SHEEN = mix(vec3(0.080, 0.078, 0.074), vec3(0.125, 0.135, 0.150), k);   // the sheen matters more at night
  WASH  = mix(vec3(0.235, 0.210, 0.185), vec3(0.880, 0.850, 0.800), k);   // dark ink | pale wash
}

// A slow swell of air moving under the cloth; every layer rides on it.
float swell(vec2 p, float t){
  vec2 c = vec2(0.8 + 0.5 * sin(t * 0.071), 0.5 + 0.28 * sin(t * 0.053 + 1.0));
  return 0.09 * exp(-dot(p - c, p - c) / 0.2);
}

vec3 cloth(vec3 n){
  float diff = clamp(dot(n, L) / L.z, 0.0, 1.5);
  vec3 c = mix(SHADE, LIT, smoothstep(0.2, 1.15, diff));
  return c + SHEEN * pow(max(dot(n, H), 0.0), 28.0);   // soft sheen on the crests
}

// The sheet underneath everything: broad diagonal folds.
float baseH(vec2 p, float t){
  vec2 d = vec2(0.83, 0.56);
  float u = dot(p, d), v = dot(p, vec2(-d.y, d.x));
  float w = fbm(p * 0.9 + vec2(0.0, t * 0.02));
  return 0.10 * sin(u * 4.2 + 2.2 * w + t * 0.11) + 0.04 * sin(u * 7.5 - v * 1.6 + t * 0.17 + 1.3 * w) + swell(p, t);
}

// One turned-over band of cloth. x: distance inside its nearest edge, y: along it, z: across it.
vec4 band(vec2 p, float t, float i){
  vec2 a; float ang, W;
  if (i < 0.5) { a = vec2(0.50, 0.80); ang = -0.38; W = 0.20; }
  else if (i < 1.5) { a = vec2(1.20, 0.36); ang = 0.62; W = 0.24; }
  else { a = vec2(0.30, 0.18); ang = 0.22; W = 0.17; }
  vec2 dir = vec2(cos(ang), sin(ang)), nn = vec2(-dir.y, dir.x);
  float along = dot(p - a, dir);
  float s = dot(p - a, nn) - 0.08 * sin(along * 1.9 + t * 0.16 + i * 2.3) - 0.028 * sin(along * 4.1 - t * 0.23 + i);
  float hw = W * (0.55 + 0.45 * sin(along * 1.5 + t * 0.19 + i * 2.1)) + 0.02;   // the band twists and narrows
  return vec4(hw - abs(s), along, s, hw);
}
float bandH(vec4 r, vec2 p, float t, float i){ return 0.012 * sin(r.z * 34.0 + r.y * 1.2 + t * 0.3 + i) + swell(p, t); }

vec3 layBand(vec3 col, vec2 p, float t, float i){
  col *= 1.0 - 0.12 * smoothstep(-0.03, 0.05, band(p + L.xy * 0.04, t, i).x);   // its soft shadow on the cloth below
  float e = 0.003;
  vec4 r = band(p, t, i);
  if (r.x < -0.004) return col;
  vec4 rx = band(p + vec2(e, 0.0), t, i), ry = band(p + vec2(0.0, e), t, i);
  float h = bandH(r, p, t, i);
  vec3 nf = normalize(vec3(-(bandH(rx, p + vec2(e, 0.0), t, i) - h) / e, -(bandH(ry, p + vec2(0.0, e), t, i) - h) / e, 1.0));
  // where the cloth rolls over the edge it is seen edge-on: dense, and lit or shaded by which way it faces
  vec2 outward = -normalize(vec2(rx.x - r.x, ry.x - r.x) + 1e-6);
  float phi = asin(1.0 - clamp(r.x / min(0.085, r.w), 0.0, 1.0));
  vec3 n = normalize(vec3(outward * sin(phi) + nf.xy, cos(phi) * nf.z));
  float a = (1.0 - exp(-0.32 / max(n.z, 0.1))) * smoothstep(-0.0015, 0.0015, r.x);
  vec3 c = cloth(n) + (noise(vec2(r.y * 9.0, r.z * 150.0)) - 0.5) * 0.07;       // weave lines running with the band
  return mix(col, c, a);
}

// A drop of ink on paper: a pale granulating wash with a darker edge where the pigment dries.
// (Rewritten from the prototype without a reversed smoothstep or a pow of a negative number, which
// GLSL ES 1.00 leaves undefined.) It is drawn for a canvas at the device's own first step (0.8 to 1.2, or from 0.65 on a phone:
// ladderFor), where its edge, a few pixels wide, is smooth once the canvas is stretched to the screen. The canvas can be much
// coarser (down to 0.28 on a phone: a pixel is 3.6 CSS px), where an edge that narrow and a grain that fine are only stair-steps,
// so k, how many times coarser the canvas is than that first step (1 on a healthy device, where nothing changes: its wash is as
// crisp as it was drawn), widens the edge, the rim and the ragged outline by k and takes the granulation and the rim's weight
// down by it: the same few canvas pixels of soft edge at every resolution below the device's normal one.
float wash(vec4 P, vec4 Q){
  if (Q.y < 0.002) return 0.0;
  float k = u_soft;
  vec2 d = (gl_FragCoord.xy - P.xy) / (P.zw * (0.4 + 0.6 * Q.x) * (1.0 + 0.02 * Q.z));
  float r = length(d) + (fbm(d * 1.4 / k + Q.w + Q.z * 0.03) - 0.5) * 0.5
          + (noise(gl_FragCoord.xy * 0.2 + Q.w) - 0.5) * 0.07 / k;                // ragged, fibrous edge
  float body = (1.0 - smoothstep(0.94 - 0.06 * k, 0.94 + 0.06 * k, r)) * (1.0 + (0.6 * noise(gl_FragCoord.xy * 0.4 + Q.w * 9.0) - 0.3) / k);
  float x = (r - 0.93) / (0.055 * k);
  float rim = exp(-x * x);
  return (body * 0.10 + rim * 0.16 * inversesqrt(k)) * Q.y;
}

void main(){
  appearance(u_theme);
  vec2 uv = gl_FragCoord.xy / u_res;
  float aspect = u_res.x / u_res.y, t = u_time;
  // The composition was made for a 16:10 frame centred on (0.8, 0.5); every window keeps that centre.
  vec2 p = (vec2(uv.x * aspect, uv.y) - vec2(aspect * 0.5, 0.5)) * ${ZOOM.toFixed(2)} + vec2(0.8, 0.5);

  // base sheet over the paper
  float e = 0.004, h = baseH(p, t);
  vec3 n = normalize(vec3(-(baseH(p + vec2(e, 0.0), t) - h) / e, -(baseH(p + vec2(0.0, e), t) - h) / e, 1.0));
  vec3 col = PAPER * 0.965;
  vec2 bd = vec2(0.83, 0.56);
  vec3 c = cloth(n) + (noise(vec2(dot(p, vec2(-bd.y, bd.x)) * 9.0, dot(p, bd) * 150.0)) - 0.5) * 0.06;
  col = mix(col, c, 1.0 - exp(-0.5 / max(n.z, 0.1)));

  // three bands turned over on top, each casting a soft shadow on what lies beneath
  col = layBand(col, p, t, 0.0);
  col = layBand(col, p, t, 1.0);
  col = layBand(col, p, t, 2.0);

  // The calm, everywhere (spec 3.1): pulled toward the paper by CALM, then kept inside a soft limit on its luma, nothing
  // darker than FLOOR below the paper's in Paper, nothing lighter than CEILING above it in Ink. Both limits are offsets from
  // the appearance's own paper, and each is far out of reach (1.0) in the other appearance, so neither bites while one
  // fades into the other. The grain is added after.
  col = PAPER + (col - PAPER) * ${CALM.toFixed(2)};
  {
    const vec3 W3 = vec3(0.2126, 0.7152, 0.0722);
    float yP = dot(PAPER, W3), y = dot(col, W3);
    float lo = yP - mix(${FLOOR.toFixed(4)}, 1.0, u_theme), hi = yP + mix(1.0, ${CEILING.toFixed(4)}, u_theme);
    float a = y - lo; a = 0.5 * (a + sqrt(a * a + ${KNEE.toFixed(4)}));
    float b = hi - (lo + a); b = 0.5 * (b + sqrt(b * b + ${KNEE.toFixed(4)}));
    col += (hi - b) - y;
  }
  col = mix(PAPER, col, u_cloth);

  // The phone menu's sheet (spec 4.4): the paper laid over the cloth by a share that rises linearly from nothing at the left edge to all of it at
  // half the width, as the CSS surface it replaces was masked (linear-gradient(to right, transparent, solid 50%)), so the scene shows across the left
  // half and the links lie on the paper. The washes come after it, so they show on the sheet, and so does the film grain.
  col = mix(col, PAPER, u_sheet * clamp(uv.x * 2.0, 0.0, 1.0));

  for (int i = 0; i < ${MAX_INKS}; i++) col = mix(col, WASH, clamp(wash(u_inkP[i], u_inkQ[i]), 0.0, 0.6));

  // Film grain: each pixel has three phases of its own, fixed, and u_grain takes all of them round together. A triangle wave through a
  // phase is flat over the pixels, so the sum of three is bell-shaped (most pixels near the cloth, few far out), zero-mean and within
  // +-1, which the appearance's peak scales: a pixel drifts smoothly through its brightness, a shimmer, not a fresh value every frame.
  vec3 phase = hash32(floor(gl_FragCoord.xy)) + u_grain;
  col += (tri(phase.x) + tri(phase.y) + tri(phase.z) - 1.5) / 1.5 * mix(${GRAIN_PAPER.toFixed(4)}, ${GRAIN_INK.toFixed(4)}, u_theme);
  gl_FragColor = vec4(col, 1.0);
}`;

// Starts the cloth on canvas (with its WebGL context gl). off(reason) is shell.js's way to turn Motion
// off for this visit; ended() tells it the cloth has gone, so turning Motion on again starts afresh.
// sheet(time) is shell.js's driver of the phone menu's sheet (spec 4.4): it returns the sheet's one value, 0 to 1, for the frame at this time,
// stepping it first if the frame has not been stepped yet. The step is made once for a frame's time, whoever asks first, so it does not matter
// whether this callback or the driver's own comes first in a frame (reading --sheet from CSS would give the paper the last frame's value, a whole
// slow frame behind the links, whenever this one did). The paper is then drawn with what the links and the page's fade-out show, nothing timed here.
export function start({ canvas, gl, off, ended, sheet = () => 0 }) {
  const root = document.documentElement;
  const program = build(gl);
  const u = (name) => gl.getUniformLocation(program, name);
  const at = { res: u("u_res"), time: u("u_time"), theme: u("u_theme"), cloth: u("u_cloth"), soft: u("u_soft"), grain: u("u_grain"), sheet: u("u_sheet"), inkP: u("u_inkP"), inkQ: u("u_inkQ") };
  const inkP = new Float32Array(4 * MAX_INKS), inkQ = new Float32Array(4 * MAX_INKS);
  const ladder = ladderFor(PHONE.matches ? "phone" : "desktop");
  let step = 0, capped = false, before = 0;   // before: the median that caused the last step down, until the next window has judged it
  try {
    const index = (value) => Math.min(Math.max(0, Math.floor(Number(value)) || 0), ladder.length - 1);
    const walked = sessionStorage.getItem(KEPT), kept = sessionStorage.getItem(CAPPED);
    if (walked !== null) step = index(walked);   // a walk that a smaller canvas helped is not paid for again on every page: carry on from where it got to
    if (kept !== null) { step = Math.max(step, index(kept)); capped = true; }
  } catch (error) {
    // Storage is blocked: this visit probes again.
  }
  let scale = ladder[step];
  let now = performance.now(), last = null, raf = 0, t = 8, grain = 0, frames = 0, sample = [], stopped = false;
  let cloth = 0, clothTo = 1, theme = themeWanted(), pace = paceWanted(), leaving = false;
  let inks = [];
  const held = new Set();   // the elements that hold their wash

  canvas.className = "veil";
  canvas.setAttribute("aria-hidden", "true");
  document.body.prepend(canvas);
  size();

  function themeWanted() {
    const chosen = root.getAttribute("data-theme");
    return chosen === "dark" || (chosen !== "light" && DARK.matches) ? 1 : 0;
  }
  function paceWanted() {
    if (!document.querySelector("main [data-threshold]")) return PACE.chapter;
    return document.querySelector("main [data-install]:not([hidden])") ? PACE.entered : PACE.threshold;
  }
  // The canvas draws in its own CSS box (base.css: fixed, 100vw by 100%), not the window's. On a phone innerHeight moves
  // with the toolbar (pulling to refresh moves it) while the fixed canvas does not, so its pixels follow its own box,
  // checked every frame, and every element's box is mapped in the canvas's frame (inkUniforms), never the window's.
  function size() {
    const width = Math.max(1, Math.round(canvas.clientWidth * scale)), height = Math.max(1, Math.round(canvas.clientHeight * scale));
    if (canvas.width !== width) canvas.width = width;
    if (canvas.height !== height) canvas.height = height;
  }
  function shown(element) {
    if (element.closest("[hidden]") || !element.getClientRects().length) return false;
    return element.checkVisibility ? element.checkVisibility({ opacityProperty: true, visibilityProperty: true }) : true;
  }
  // Ink: a wash grows under what the pointer, a finger or the keyboard is on. It lives while they are on the element,
  // a click or not, and fades when they leave (spec 3.4); on a phone a tap is the pointer arriving and leaving at once,
  // so the wash blooms and fades. The open menu's sheet below 64rem is drawn in the canvas too (u_sheet, below), under the washes, so its
  // links are inked like any other control and the sheet's paper and grain lie under the wash.
  const SHEET = "[data-menu-toggle][aria-expanded='true'] + #shell-menu";   // the menu while it is open: the sheet stays displayed for the length of its close, but its wash is let go when MENU says shut
  function inkable(element) {
    return Boolean(element) && !leaving && !element.closest(".catalog__frame");   // no new wash while the cloth fades out
  }
  // An element's box in CSS pixels while it is shown and has a size; null when it is hidden, detached or empty.
  function inkBox(element) {
    if (!element.isConnected || !shown(element)) return null;
    const b = element.getBoundingClientRect();
    return b.width && b.height ? { left: b.left, top: b.top, width: b.width, height: b.height } : null;
  }
  // Four washes at most. When all four are there the one that is fading goes first, then the oldest nothing holds; if every
  // one is held (the current chapter, the selected tab) there is no room, and the element keeps its flat fill. True when the
  // element has a live wash. The mark of a wash that goes stays while its element has another that is live (the pointer came back).
  function addInk(element, keep = false) {
    if (inks.some((ink) => ink.element === element && !ink.dead)) return true;
    if (inks.length >= MAX_INKS) {
      let i = inks.findIndex((ink) => ink.dead);
      if (i < 0) i = inks.findIndex((ink) => !ink.keep);
      if (i < 0) return false;
      const [gone] = inks.splice(i, 1);
      if (!inks.some((ink) => ink.element === gone.element && !ink.dead)) gone.element.classList.remove("is-inked");
    }
    inks.push({ element, born: now, dead: null, keep, seed: Math.random() * 40, from: 1, since: now, box: inkBox(element) });
    element.classList.add("is-inked");
    kick();
    return true;
  }
  // The wash fades from now on: the pointer or focus has left the element, or it has gone. Nothing holds it any more.
  function endInk(element) {
    for (const ink of inks) if (ink.element === element && !ink.dead) { ink.dead = now; ink.keep = false; }
    element.classList.remove("is-inked");
  }
  // How much of its wash an ink shows: fading from the moment it was let go (1.8 s), or, when it is held
  // again, rising from the strength it had to full over 0.5 s.
  function strength(ink) {
    if (ink.dead !== null) return Math.max(0, 1 - (now - ink.dead) / 1800);
    return ink.from + (1 - ink.from) * Math.min(1, (now - ink.since) / 500);
  }
  function inkUniforms() {
    const frame = canvas.getBoundingClientRect();   // where the drawing is: x counts from its left, y up from its bottom, as gl_FragCoord does
    for (const ink of inks) {
      // The wash follows its element while it is shown. An element that is hidden (Come in once it has been
      // clicked), has no size or has left the page ends its wash, which fades where it was: never at a zero box.
      const box = inkBox(ink.element);
      if (box) ink.box = box;
      else if (!ink.dead) endInk(ink.element);
      if (!ink.dead && !inkable(ink.element)) endInk(ink.element);   // the sheet opened over it, or the window narrowed
    }
    inks = inks.filter((ink) => (!ink.dead || now - ink.dead < 1800) && ink.box);
    inkP.fill(0); inkQ.fill(0);
    inks.forEach((ink, i) => {
      const age = (now - ink.born) / 1000, b = ink.box;
      const grow = 1 - Math.pow(1 - Math.min(1, age / 1.4), 3);   // 1.4 s, cubic ease-out (spec 3.4)
      inkP.set([(b.left + b.width / 2 - frame.left) * scale, (frame.bottom - (b.top + b.height / 2)) * scale,
        (b.width * 0.48 + 6) * scale, (b.height * 0.8 + 4) * scale], i * 4);
      inkQ.set([grow, strength(ink), Math.min(age, INK_AGE), ink.seed], i * 4);
    });
  }
  // The current chapter in the open menu and the selected install tab hold their wash while they are shown
  // (spec 3.4), so with the cloth running the wash, not the flat fill, marks them.
  const HELD = `${SHEET} a[aria-current='page'], [role='tab'][aria-selected='true']`;
  function holdSelected() {
    const want = new Set([...document.querySelectorAll(HELD)].filter((element) => inkable(element) && shown(element)));
    for (const element of held) {
      if (want.has(element)) continue;
      held.delete(element);
      // No longer selected: the wash fades, unless the pointer is on it, when it is an ordinary hover wash.
      if (!element.matches(":hover")) endInk(element);
      else for (const ink of inks) if (ink.element === element) ink.keep = false;
    }
    for (const element of want) {
      if (held.has(element)) continue;
      // A hover wash (or one that is fading) carries on instead of starting again from nothing, from the
      // strength it has now. An element is held once it has its wash; one that finds no place is tried again every frame.
      const ink = latest(element);
      if (ink) resume(ink, true);
      else if (!addInk(element, true)) continue;
      held.add(element);
    }
  }
  const latest = (element) => inks.filter((ink) => ink.element === element).pop();
  function resume(ink, keep) {
    ink.from = strength(ink); ink.since = now; ink.dead = null; ink.keep = keep;
    ink.element.classList.add("is-inked");
  }

  const target = (event) => event.target instanceof Element ? event.target.closest(INK_TARGETS) : null;
  const listeners = {
    pointerover: (event) => {
      const element = target(event);
      if (!inkable(element) || held.has(element)) return;
      // The pointer comes back from the document element, which a change's picture was for a moment (The pointer in a change): the wash it let go carries on.
      const ink = event.relatedTarget === root && latest(element);
      if (ink && strength(ink) > 0) resume(ink, false); else addInk(element);
    },
    pointerout: (event) => {
      const element = target(event);
      if (element && !held.has(element) && !(event.relatedTarget instanceof Node && element.contains(event.relatedTarget))) endInk(element);
    },
    focusin: (event) => { const element = target(event); if (inkable(element) && !held.has(element) && element.matches(":focus-visible")) addInk(element); },
    focusout: (event) => { const element = target(event); if (element && !held.has(element)) endInk(element); },
  };
  for (const [name, listener] of Object.entries(listeners)) document.addEventListener(name, listener, true);
  // An in-place change makes the clicked link the current one: hold its wash at once, before the focus moves
  // from the link to the new heading, which would let the wash go. (The cross-fade itself takes no pointer from
  // the page, base.css, but the moment before it does: AGENTS.md, "The pointer in a change".)
  document.addEventListener("veilfile:swapped", holdSelected);

  // Motion, appearance and the system's colour scheme, as they change.
  const watch = new MutationObserver(() => {
    if (root.getAttribute("data-motion") !== "on") leave(); else if (leaving) { leaving = false; clothTo = 1; }
    kick();
  });
  watch.observe(root, { attributes: true, attributeFilter: ["data-motion", "data-theme"] });
  const onScheme = () => kick();
  DARK.addEventListener("change", onScheme);
  const onVisible = () => { if (!document.hidden) { last = null; sample = []; frames = 0; kick(); } };
  document.addEventListener("visibilitychange", onVisible);
  const onLost = (event) => { event.preventDefault(); stop("lost"); };
  canvas.addEventListener("webglcontextlost", onLost);

  // Motion went off: the cloth fades out over about a second, then everything stops (spec 3.3).
  function leave() {
    if (leaving) return;
    leaving = true; clothTo = 0;
    for (const ink of inks) ink.element.classList.remove("is-inked");
    inks = []; held.clear();
  }
  function stop(reason) {
    if (stopped) return;
    stopped = true;
    cancelAnimationFrame(raf);
    for (const [name, listener] of Object.entries(listeners)) document.removeEventListener(name, listener, true);
    document.removeEventListener("veilfile:swapped", holdSelected);
    watch.disconnect();
    DARK.removeEventListener("change", onScheme);
    document.removeEventListener("visibilitychange", onVisible);
    canvas.removeEventListener("webglcontextlost", onLost);
    for (const ink of inks) ink.element.classList.remove("is-inked");
    delete root.dataset.veil;
    canvas.remove();
    const lose = gl.getExtension("WEBGL_lose_context");
    if (lose && reason !== "lost") lose.loseContext();
    if (reason) off(reason);
    ended();
  }

  function remember(key, value) {
    try { sessionStorage.setItem(key, String(value)); } catch (error) { /* the next page probes again */ }
  }
  // Adaptive rendering (spec 8): lower the resolution while frames run long, unless a lower one does not shorten them and leaves them long (then
  // the display's frame rate is the limit, and the step is undone for the visit and the tab); where it cannot help, the still page. A step that
  // is kept is remembered for the tab (KEPT), so the next page starts there instead of walking the ladder again.
  function measure(ms) {
    if (document.hidden) return;
    frames += 1;
    if (frames <= WARMUP) return;
    sample.push(ms);
    if (sample.length < WINDOW) return;
    const median = sample.sort((a, b) => a - b)[WINDOW >> 1];
    sample = [];
    if (before && median > SLOW_MS && median > before * HELPED) {   // a step that reached healthy frames is kept, however little it shortened them
      step -= 1; scale = ladder[step]; frames = 0; capped = true; before = 0;
      remember(CAPPED, step);
      return;
    }
    if (before) remember(KEPT, step);   // the step down helped (or reached healthy frames): the tab's next pages start here
    before = 0;
    if (median <= SLOW_MS) return;
    if (!capped && step < ladder.length - 1) { before = median; step += 1; scale = ladder[step]; frames = 0; }   // the next frame sizes the canvas to it
    else if (median > STILL_MS) stop("slow");
  }

  function frame(time) {
    raf = 0;
    if (stopped || document.hidden) return;
    try {
      draw(time);
    } catch (error) {
      stop("error");   // a cloth that cannot draw gives way to the still page, not a broken one
    }
  }
  function draw(time) {
    const ms = last === null ? 0 : time - last;
    const dt = Math.min(0.1, ms / 1000);   // a long gap (a hidden tab) neither jumps the cloth nor reads as slow
    if (last !== null) measure(ms);
    last = time; now = time;
    if (stopped) return;
    const share = leaving || !SHEETED.matches ? 0 : sheet(time);   // the sheet's value for this frame, taken before anything reads where its links are; 0 once Motion is off (the CSS surface is back at once, base.css) and where the menu is a column
    cloth += (clothTo - cloth) * Math.min(1, dt * 1.8);              // about a second (spec 3.3)
    theme += (themeWanted() - theme) * Math.min(1, dt * 2.2);        // about 1.5 s (spec 3.2)
    pace += (paceWanted() - pace) * Math.min(1, dt * 0.8);
    t += dt * pace;
    grain = (grain + dt * GRAIN_RATE) % 1;                          // cycles, wrapped here so it stays exact however long the visit
    if (!leaving) holdSelected();
    inkUniforms();
    size();
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.uniform2f(at.res, canvas.width, canvas.height);
    gl.uniform1f(at.time, t);
    gl.uniform1f(at.theme, theme);
    gl.uniform1f(at.cloth, cloth);
    gl.uniform1f(at.soft, ladder[0] / scale);
    gl.uniform1f(at.grain, grain);
    gl.uniform1f(at.sheet, share);
    gl.uniform4fv(at.inkP, inkP);
    gl.uniform4fv(at.inkQ, inkQ);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    if (!root.dataset.veil) root.dataset.veil = "on";
    if (leaving && cloth < 0.01) { stop(null); return; }
    kick();
  }
  function kick() { if (!raf && !stopped) raf = requestAnimationFrame(frame); }

  // The first frames fade the cloth in from plain paper, so it never pops over the still page.
  kick();
}

// The steps a device can draw at, the first and finest first: 1.2, 0.9, 0.68, 0.51, 0.38, 0.28 on a 3x phone; 0.8, 0.6, 0.45, 0.35 on a desktop.
function ladderFor(kind) {
  const steps = [Math.min(TOP, Math.max(LOW[kind], SHARE * (window.devicePixelRatio || 1)))];
  while (steps[steps.length - 1] > LAST[kind]) {
    const next = steps[steps.length - 1] * STEP;
    steps.push(next < LAST[kind] * 1.15 ? LAST[kind] : next);
  }
  return steps;
}

// Compiles and links the shader; a failure throws, and shell.js turns Motion off for the visit.
function build(gl) {
  if (gl.getParameter(gl.MAX_FRAGMENT_UNIFORM_VECTORS) < 32) throw new Error("veil.js: too few fragment uniforms");
  const shader = (type, source) => {
    const s = gl.createShader(type);
    gl.shaderSource(s, source);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(`veil.js: ${gl.getShaderInfoLog(s)}`);
    return s;
  };
  const program = gl.createProgram();
  gl.attachShader(program, shader(gl.VERTEX_SHADER, VERT));
  gl.attachShader(program, shader(gl.FRAGMENT_SHADER, FRAG));
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(`veil.js: ${gl.getProgramInfoLog(program)}`);
  gl.useProgram(program);
  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  const a = gl.getAttribLocation(program, "a");
  gl.enableVertexAttribArray(a);
  gl.vertexAttribPointer(a, 2, gl.FLOAT, false, 0, 0);
  return program;
}
