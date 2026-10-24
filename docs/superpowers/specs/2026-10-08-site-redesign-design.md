# Veilfile site redesign: design

- **Date:** 2026-10-08
- **Status:** draft for owner review
- **Scope:** the whole product UI. Public pages, Journal, Docs, legal pages, sign-in and account forms, device and OAuth approval, error pages, and the dashboard.
- **Reference prototype:** [`assets/2026-10-08-site-redesign-prototype.html`](assets/2026-10-08-site-redesign-prototype.html) is a working mockup of the shell. Open it in a browser. Its shader is the source of truth for how the cloth looks. It predates three decisions in this spec, the MCP install tab (§4.2), the calm (§3.1, which replaces its clearings) and the darker `--muted` (§3.2), so follow the spec where they differ.

## 1. Purpose

### 1.1 What the site is for

Agents are Veilfile's main users. They connect over MCP, the CLI or plain HTTP, after a human has set things up. The site exists for the humans around them:

- **Distribution:** getting people to install the CLI or connect the MCP server.
- **Plan sign-up.**
- **Marketing:** earning people's trust, intrigue and desire, plus search visibility (SEO) and visibility in AI answers (AIEO). The Journal (today's blog) exists mostly for SEO and AIEO. Muse writes it.

The dashboard is a place people check, sometimes often, but they don't do their work there. The work happens in their agents.

### 1.2 How it should feel

Calm, minimal, pensive, almost meditative. A visitor should feel invited in by a little mystery, and at the same time confident and fully informed. The site should offer knowledge and imply wisdom. The references are earendil.com and lefos.com (natural motion, grain, generous space, corner labels, a gnomic pixel mark), and pi.dev for the copyable install line. We borrow ideas, never identities. We use none of their fonts, marks or copy.

### 1.3 What was wrong with the current site

- It's too technical up front: the landing page opens with a curl transcript.
- It has no atmosphere.
- The voice is wrong.
- It feels assembled rather than designed. Everything is top-aligned with dead space below, where the references use space deliberately.

### 1.4 The maintainer is Muse

Muse is a cloud agent with subagents that will run the site day to day. It is persistent but works best from explicit recipes, so the design has to make the right output the easy output:

- The templates carry all of the design.
- Every convention is backed by a test whose failure says exactly what to fix.
- Every common job has a step-by-step recipe in AGENTS.md (§7).

## 2. Decisions at a glance

| Topic | Decision |
|---|---|
| Atmosphere | Layered voile seen up close ("Voile, nearer"), drawn live in WebGL with film grain. |
| Threshold line | "Some files are meant to be seen for a moment, / by the right eyes, and then let go." |
| Come in | The line fades away and the install line (MCP · npm · pip, MCP first) stands alone in the center. |
| Navigation | Chapters in any order, chosen from an Earendil-style corner menu. Each chapter is a real page with its own address. Chapters change in place when JavaScript is available. |
| Chapter pages | The cloth is calmed everywhere, so text reads over it wherever it sits, and it keeps moving behind the text. |
| Hover | Watercolor ink wash on everything clickable when Motion is on. A flat fill block when it's off. |
| Appearance | Paper and Ink. Follows the system setting by default. The switch cycles Ink → Auto → Paper. |
| Motion off and reduced motion | The cloth is removed, not paused. What's left is plain paper or slate with still grain. |
| Dashboard | The same shell. The cloth follows the Motion setting, like every other page. |
| Type | Cormorant Garamond italic for display, Newsreader for reading, IBM Plex Mono for labels and code. All self-hosted. |
| Mark | "Let go", a 4×4 pixel glyph. |
| Stack | Django templates throughout. React, Vite and Node are removed. Plain CSS and small JavaScript modules, with no build step. |

## 3. Visual system

### 3.1 The voile

The cloth is a fragment shader on one full-page canvas behind everything. See the prototype for the exact code.

- **The cloth:**
  - **Layers:** a base sheet with broad diagonal folds, plus three bands of cloth turned over on top of it. Each band casts a soft shadow on the layers below.
  - **Rolled edges:** where a band turns over, the cloth is seen edge-on, so it gets denser and catches or loses the light. Opacity follows how steeply the cloth turns.
  - **Light:** a key light from the upper left. Warm vanilla in the light, cool grey in the hollows, a soft sheen on the crests, fine weave lines running along each band.
- **Motion:**
  - A slow swell of air moves under the cloth: bands twist, narrow and widen.
  - It runs at 0.75 of the shader clock on the threshold and 0.6 on chapter pages.
  - The cloth is shown at zoom 0.6, which is the "nearer" framing.
- **Rendering:**
  - The shader renders at a resolution that follows the screen: 0.4 of the device pixel ratio in canvas pixels per CSS pixel (a canvas pixel is 2.5 screen pixels), never under 0.8 (0.65 at 40rem and narrower, phones) and never over 1.2. A 3x phone draws at 1.2, a 2x phone or Mac at 0.8, a 1x desktop at 0.8. The canvas is scaled up to the screen.
  - Film grain is added per pixel in the shader, and it drifts: each pixel has phases of its own, fixed, and goes round them about 1.5 times a second (whatever the cloth's pace), a shimmer and not static. The sum of three flat waves is bell-shaped, so most pixels sit near the cloth and few far out, and it averages out to nothing. It moves a pixel's luma by 0.025 at the most in Paper and 0.018 in Ink, weaker there as earendil.com's is (`GRAIN_PAPER` and `GRAIN_INK` in `veil.js`): a spread of 0.0084 and 0.0061, against the 0.0145 of the flat grain that was drawn afresh every frame.
- **The calm.** There are no clearings and no rectangles: nothing is cleared behind text, and nothing on a page asks the shader for anything. The cloth is calmed everywhere so that small text keeps 4.5:1 over every pixel it can draw:
  - The cloth's variation is scaled to 70% of what the prototype draws, toward the appearance's paper (`CALM` in `veil.js`).
  - A soft floor on the cloth's luma then lifts only the darkest pixels in Paper, and a soft ceiling lowers only the brightest in Ink (`FLOOR` and `CEILING`): Paper never goes below its paper by more than 0.1163, Ink never above its paper by more than 0.1089. Each limit is out of reach in the other appearance, so neither bites while one fades into the other.
  - The numbers come from the contrast floor for `--muted` (§3.2): measured over 1,495 moments of the cloth at 1280×800 and 375×667, whole canvas, with a grain of up to 0.025, `--muted` held 4.5:1 or better and `--ink` 8:1. The grain is no stronger now, and over 1,496 moments with it, whole canvas, `--muted` holds 4.682:1 at worst in Paper and 4.838:1 in Ink, and `--ink` body text 8.22:1 or better. A lighter `--muted`, or a stronger cloth or grain, needs them measured again.

  Without the calm, muted labels measured about 2.4:1 to 3.2:1 at the cloth's darkest folds in Paper and its brightest in Ink. Both are below the 4.5:1 minimum, and measurement is why the folds are softer than the prototype's. They still read as cloth, and they still move.

### 3.2 Paper and Ink

Every color is a design token in one file. The shader has a matching set of five colors per appearance and blends between them with a single 0 to 1 value, so a switch fades the whole scene in about 1.5 seconds.

| Token | Paper | Ink |
|---|---|---|
| ground (`--paper`) | `#ebe6de` | `#1b1917` |
| text (`--ink`) | `#2c2925` | `#e6e0d6` |
| labels (`--muted`) | `#534e47` | `#aea79c` |
| hairlines (`--hair`) | `rgba(44,41,37,.2)` | `rgba(230,224,214,.2)` |
| still-mode hover fill (`--fill`) | `rgba(44,41,37,.12)` | `rgba(230,224,214,.14)` |
| install box ground (`--box`) | `rgba(240,236,229,.35)` | `rgba(30,28,26,.35)` |

Shader colors, from the prototype's `appearance()` function:

| Role | Paper | Ink |
|---|---|---|
| ground | (0.922, 0.902, 0.871) | (0.104, 0.098, 0.091) |
| light | (0.992, 0.952, 0.880) | (0.300, 0.302, 0.312) |
| shade | (0.735, 0.728, 0.712) | (0.052, 0.049, 0.046) |
| sheen | (0.080, 0.078, 0.074) | (0.125, 0.135, 0.150) |
| ink wash | (0.235, 0.210, 0.185) | (0.880, 0.850, 0.800) |

The CSS ground must equal the shader ground exactly, so nothing seams where the canvas meets the page.

`--muted` is the prototype's label colour moved 20% toward `--ink` (in sRGB), so that small labels keep 4.5:1 over the calmed cloth (§3.1). `--muted` is the colour of every small piece of text that is not `--ink`.

The appearance switch sits bottom left, across the row from FX, the motion switch (§4.1). It shows its current state as a word (Ink, Auto or Paper) and cycles in that order. The default is Auto, which follows `prefers-color-scheme` and updates live when the system changes.

### 3.3 Motion

The switch is worded FX · on and FX · off (§4.1); Motion is the setting's name.

- **On (default):** the cloth moves, and hovers use the ink wash.
- **Off:**
  - The cloth fades out over about a second and leaves plain paper or slate with still grain.
  - Hovers become flat fills.
  - Rendering stops completely: no animation loop, no GPU work.
  - Turning Motion back on fades the cloth back in.
- **Reduced motion** (`prefers-reduced-motion: reduce`): with nothing saved, the page loads in the Off state and `veil.js` is not fetched. If the visitor turns Motion on, that choice is saved and wins over the system setting, for the cloth and for every fade.
- **No WebGL, or a device too slow to keep up:** the Off state. The automatic switch lasts only for that visit.
- **Still grain without the canvas:** a small tiling noise image as a CSS background, so the still page needs no JavaScript. It must match the shader's grain closely: one tile per appearance, zero-mean over its own ground, with the shader's spread (weaker in Ink, as the shader's is). It is drawn at the size of the screen's pixels, as the cloth's grain follows the screen (§3.1): one tile pixel is one device pixel, and the tile a whole 128 device pixels, at each pixel ratio screens really have: 1.25, 1.5 and 1.75 (Windows scaling), 2, 2.625 and 2.75 (Android), 3 and 4 (`--grain-size` is 128 divided by the ratio, chosen by resolution in `base.css`, each rule starting halfway to the next ratio, and used by `html`'s `background-size`; the phone menu's sheet, §4.4, draws the same tile at the same size). A tile pixel the size of a CSS pixel would show every speck two or three pixels across; a tile at the wrong fraction (85.33 CSS px at 1.25 is 106.67 device px) is resampled and does not repeat on the pixel grid.

### 3.4 Hover and focus

- **Ink wash (Motion on):**
  - **Shape:** a pale granulating wash with a darker, ragged edge where the pigment dries. It's drawn in the shader, so the grain and light pass through it. It was made for a canvas at the device's own first step (0.8 to 1.2 of the CSS resolution, or from 0.65 on a phone, §3.1), where its ragged edge is crisp and stays exactly as drawn; on a coarser one (the adaptive cloth, spec 8: any step below the first, as far as 0.28) the shader widens its edge and takes its grain down by how many times coarser the canvas is than that first step, so the edge stays a soft gradient over the same few canvas pixels instead of showing them as stair-steps.
  - **Size:** an ellipse centered on the element, with radii of about `0.48 × width + 6` and `0.8 × height + 4` CSS px.
  - **Timing:** it grows over 1.4 s with a cubic ease-out and fades over 1.8 s when the pointer leaves.
  - **Label color:** the label darkens to `--ink`.
  - **The current chapter** in the open menu, and the selected install tab, hold their wash. The menu starts closed (§4.4), so the chapter's wash begins when MENU opens it; when the menu shuts (Escape, a click elsewhere, or a link that changes the page in place) the wash is let go like any other and fades over 1.8 s.
  - **Inline links** (in a chapter's paragraphs and definitions, and in a reading page's paragraphs, list items, table cells and quotes) have a dotted underline that gives way to the wash.
  - **Where:** on every width, phones included. On a phone a tap is the pointer arriving and leaving at once, so the wash blooms under the finger and fades as it lifts; the selected install tab still holds its wash. The open menu sheet below 64rem is inked like the rest, because the cloth draws the sheet (§4.4): the current page's link holds its wash on it, and a hovered, focused or tapped link takes one; when the sheet closes the wash is let go and fades over 1.8 s on the cloth, behind where the sheet was (the sheet's links, its paper and the page's fade-out under it are one value advanced per drawn frame, §4.4). On a touchscreen (a coarse primary pointer, which cannot hover) the flat fill is also what a press shows, for as long as the finger is down; a hover fill never applies there, so it cannot stay behind where a wash has faded. The mark and MENU, which sit on the cloth with nothing over them, are inked like the rest.
- **Flat fill (Motion off, and wherever the wash is not drawn):** a `--fill` block behind the label, inset a little inside the hit area, appearing in 0.15 s when Motion is on (at once when Motion is off, when nothing moves). It is also what a control shows before the cloth has loaded and after it has failed. The current chapter keeps its block. Inline links lose the underline and get the block. A device that cannot hover (a phone keeps the hover of the last thing touched) shows the block for focus, the current chapter and the selected tab, and for a touch only while the finger is down (a press); a hover never applies there, so no block stays behind where a wash has faded.
- **Focus:** keyboard focus (`:focus-visible`) triggers the same wash or fill, plus a 1px outline in `--muted`.
- **Coverage:** every interactive element gets the effect: corner labels, MENU, menu items, Come in, the install tabs, Copy, FX, the appearance switch, inline links, form buttons and dashboard actions.

### 3.5 Type

| Role | Face | Use |
|---|---|---|
| Display | Cormorant Garamond, italic 300 and 400 | Threshold line, chapter titles, headings in reading pages. Lining numerals. `text-wrap: balance`. |
| Reading | Newsreader, 400 and 500 plus italic, optical sizing on | Body text everywhere. 18.5px with 1.62 line height on desktop, 17.5px on phones. About 65 characters per line. |
| Labels and code | IBM Plex Mono 400 | Corner labels and menu: uppercase, tracking .14em, chosen by eye between .2em and .12em, `clamp(13px, 0.35vw + 11.6px, 14px)`, that is 13px on phones and 14px from 686px wide (the owner chose the 13 and 14px sizes, 2026-10-16, after earendil.com's 15px corner and menu text; the tracking was picked by eye: a letter and its space are 10.4px at 14px and .14em, as they were at 13px and .2em, so the menu column keeps its width and the page its reach of 16 label widths; viewport units: container units would need a size container on the page, and that would make it the containing block for the fixed corners). Code: lowercase, 0.8em. Inline addresses and commands are never uppercase. |

- **Self-hosted.** All three are under the SIL Open Font License. No request ever goes to a third party to render a page.
- **Font files.**
  - Each face ships as WOFF2 files split by standard Unicode ranges: Latin, Latin Extended, and any other script the face supports.
  - Each file is declared with `unicode-range`, so a browser downloads a range only when a page uses one of its characters.
  - Do not subset to the characters currently on the site.
- **Loading.**
  - The Latin files for the display italic and the reading roman are preloaded, and so is each other face a page's first screen sets text in: the italic at the weight of headings (400) on the chapters that have headings and on the reading pages that do, not on About, the threshold or a post with no sections.
  - A change in place loads the faces its layout may show ahead of the swap (§4.5), so none waits on a font.
  - Fallback fonts are metric-matched with `size-adjust` and ascent and descent overrides, so text doesn't shift when the web fonts arrive.
- **Line breaks.** The threshold line breaks after "moment," on wide screens and is balanced by the browser on narrow ones.

### 3.6 The mark: "Let go"

It sits on a 4×4 grid of 140-unit cells, in a 560 viewBox. Rows from the top, with `#` for a filled cell:

```
####
###.
##.#
#.#.
```

```svg
<svg viewBox="0 0 560 560" xmlns="http://www.w3.org/2000/svg" shape-rendering="crispEdges">
  <path d="M0 0H560V140H420V280H280V420H140V560H0Z M420 280H560V420H420Z M280 420H420V560H280Z"/>
</svg>
```

- **Meaning:** a solid corner breaking into a checkerboard toward the lower right. In pixel art, a checkerboard is how something see-through is drawn, so the mark reads as a file going sheer and letting go. The corner can be read as a V on its side, and the carving hints at an F.
- **Colors:** `--ink` on Paper, `--ink` (bone) on Ink.
- **Favicon:** SVG with a `prefers-color-scheme` style inside it, plus 16px and 32px PNGs and a 180px apple-touch-icon. These replaced the favicons that were in `frontend/public/`; they are in `backend/static/img/`.
- **Lockup:** top left on every page, the mark alone, linking to the threshold, as on earendil.com (owner's decision, 2026-10-09). Its name, Veilfile, is in the link for screen readers and search engines but not shown. The mark is 24px on narrower screens (cells of 6px) and 28px from 64rem up (cells of 7px), so each cell is a whole number of pixels (owner's decision, 2026-10-16: friendlier corners; earendil.com's is 32px).
- The mark was screened for resemblance to swastikas and other loaded symbols. It has no hooked arms around a center. Any future variant must be screened the same way.

## 4. Pages and navigation

### 4.1 The shell

The same on every page:

- **Top left:** the lockup, linking to the threshold.
- **Top right:** MENU.
- **Bottom left:** the appearance switch.
- **Bottom right:** the motion switch, whose visible word is FX (owner's decision, 2026-10-16: so the bottom row reads PAPER and FX · ON, more even across the four corners). Its accessible name is "FX: motion", which holds the visible label (WCAG 2.5.3) and says what it does; `aria-pressed` carries on or off and the visible "· on" is hidden from the name, so a screen reader says "F X: motion, toggle button, pressed". Motion stays the setting's name everywhere else.

  The row is the same at every width (owner's decision, 2026-10-15). The tagline PRIVATE · EPHEMERAL that used to sit bottom left is gone: it crowded the row on a phone, where the switches had wrapped under it.
- **Behind everything:** the canvas, or the still grain.
- **Placement:** labels sit at 5.5% from the top or bottom and 5% from the side, and stay fixed while reading pages scroll.
- **Content never passes under a label** (owner's decision, 2026-10-09, after earendil.com). From 64rem up, the four labels stay fixed and the page runs in the space between them, kept clear of the longest label. Below 64rem the page takes the full width: the mark and MENU stay fixed, and along the top the page's content fades out over a short band as it scrolls up toward the edge, so it never reaches them (owner's decision, 2026-10-15: no bar is laid over the cloth; the cloth, or with Motion off the still grain, shows through everywhere). The bottom labels wait at the end of the page (at the bottom of the window when the page is short).

### 4.2 The threshold (`/`)

The line is set in the display face and centered both ways. COME IN sits below it.

Clicking Come in is the step across the threshold. The line fades out entirely, and the install line fades in alone, centered both ways. There is no replacement text: the box is self-explanatory, and Begin and the Docs explain it in full. The cloth slows slightly.

- **Nothing slides.** The layout change happens only while the content is invisible: the line and COME IN fade out over 0.7 s, then the install line fades in already in its final, centered position. Content never moves while visible. In an early mockup a shorter replacement line re-centered the group and visibly shifted it, which read as sloppy.
- **Going back:** the threshold returns to the line and COME IN on reload, or when the lockup is clicked.

The install line:

| Tab | Line | Follow-up after Copy |
|---|---|---|
| MCP (default) | `https://veilfile.com/mcp` | "Add it to your agent as a remote MCP server; sign in when it asks." |
| npm | `$ npm install -g veilfile` | "then `veilfile login`" |
| pip | `$ pip install veilfile` | "then `veilfile login`" |

- **The box:** hairline border, `--box` ground with a slight backdrop blur, mono text, and Copy at the right.
- **Copy:** turns into "Copied" for 1.8 s and is announced to screen readers. Copy uses `navigator.clipboard`, with a select-the-text fallback.
- **Follow-up:** after Copy, the tab's follow-up line fades in beneath the box, for every tab. Its space is reserved from the start, so nothing moves when it appears.
- **Keyboard:** focus moves to Copy when the line appears.
- **Without JavaScript:** Come in is a link to `/begin/`.
- **Agent-friendly addresses:** the MCP address is the same for every client. A client that supports MCP sign-in (OAuth) finds the server's sign-in through OAuth discovery and dynamic client registration (`docs/API.md`, "OAuth 2.0 authorization server"), asks the user to sign in (the account needs a verified email, as it does for an API key), so the user connects without pasting a key; a client that does not takes an API key, as Begin shows.

### 4.3 Chapters

Each chapter is a short page whose content block is centered in the space between the corners.

| Menu label | Address | Notes |
|---|---|---|
| About | `/about/` | Existing address. |
| How it works | `/how/` | New. A human summary. The technical version stays at `/docs/how-it-works/`. |
| Begin | `/begin/` | New. The setup chapter for humans (below). |
| Promises | `/promises/` | Renamed from `/trust/`, which gets a permanent (301) redirect. |
| Plans | `/plans/` | Existing address. Figures come from `backend/drop_core/plans.py`. Each plan ends with a link straight into sign-up with that plan chosen. |

**Begin** covers four things:

- **MCP first,** with exact snippets for the main clients: Claude Code, the Claude apps, Cursor and Codex. The snippets are the Docs' own (backend/docs/pages/agent.md), kept equal by a test; the Claude apps get one sentence until the owner confirms their steps in the voice pass.
- **Then the CLI:** `npm` or `pip`, `veilfile login`, `veilfile push <file>`.
- **Then curl**, for plain HTTP.
- **A line pointing agents at `/llms.txt`.**

Every snippet is a code block (`components/code_block.html`, owner's request, 2026-10-16): a focusable scroll box with a Copy button in a row of its own along its top, at the right, so the code keeps the whole width at every width and the button is never over it. Copy is the install line's (the same label, `copy.js`, the fill and the ink); it says Copied and tells screen readers in the block's own status, and is not shown without JavaScript. The Docs and the Journal render the same component for each fenced code block (§4.6). The snippets' text is in `backend/pages/snippets.py`, since a template cannot hold a multi-line value in an include.

**Draft chapter copy.** This comes from the prototype. It will be refined in implementation and reviewed by the owner before launch. Every claim is checked against the product:

- **About.** Title: "A quiet place for what your agents make." Body: "Agents make things all day: a screenshot from a failing test, a bundle of logs, a report written in the middle of the night. Most of it needs to be seen by one or two people, for a little while. Veilfile gives each file a private link, and lets it go when its time is up. No public repository, no bucket to lock down, no paste that lives forever."
- **How it works.** Title: "The life of a file". Four steps:
  - *Made:* your agent pushes a file through the command line, the API, or MCP.
  - *Veiled:* it gets back an unguessable link. The 256-bit token in the address is the only key; there is no public list of files, and nothing is indexed.
  - *Seen:* share the link with whoever should see it. Uploads are checked for anything shaped like a secret, and flagged.
  - *Let go:* when its days run out, the link stops working at once, and the file is deleted from storage soon after.
- **Promises.** Title: "What we promise".
  - *The link is the only way in.*
  - *Gone means deleted:* expired files are removed from storage by the sweep job, not hidden.
  - *We look only for the shapes of secrets:* uploads are scanned for anything shaped like a key or a token, a match is flagged by name, and file contents are never logged. (The scanner reads the bytes, so "not at your files" would overstate it.)
  - *Why not a gist?*
- **Plans.** Three plans with their uploads, longest link life, storage and key limits, all read from `plans.py`.

### 4.4 The menu

MENU unfolds a right-aligned column under itself, in three groups:

1. About · How it works · Begin · Promises · Plans
2. Docs · Journal · Sign in. "Sign in" becomes "Dashboard" when signed in.
3. Terms · Privacy · Report abuse · llms.txt (quieter).

Behavior:

- From 64rem up the items fade in with a short stagger.
- The menu starts closed on every page, at every width, and marks the current page when opened. It never stays open on its own (owner's decision, 2026-10-16, after earendil.com, whose menu is never pinned open). Below 64rem the open menu is a full-screen sheet; while the sheet is open, the page beneath it is inert and does not scroll.
- Escape closes it and returns focus to MENU. A click elsewhere closes it. A link in it that changes the page in place leaves it closed (§4.5), as the next page of earendil.com arrives with its menu closed.
- **The phone sheet** (below 64rem) opens like earendil.com's phone menu (owner's decision, 2026-10-16):
  - **Surface:** a full-screen sheet of paper, nothing at the left edge and solid from the middle on (a share that rises linearly across the left half). The cloth, and the page's own text, show across the left half; the right-aligned links lie on a solid sheet. MENU stays above it. The mark and the appearance switch show through the fading half. While the cloth runs, the cloth draws the sheet: the shader lays the appearance's paper over the cloth by that share (a `u_sheet` that is the sheet's one value, `--sheet`, below, for the frame it draws), with the film grain on it and the ink washes after it, so a wash shows on the sheet; `<main>`, which lies above the canvas, is masked out of the right half by what is left of the sheet (its alpha runs from 1 at the left edge to 1 minus the sheet's share from the middle on, read from the same `--sheet`), and the display control bottom right, which the solid surface used to cover, fades with it. Wherever the cloth is not drawing (Motion off, reduced motion's still page, no WebGL, a device too slow for the cloth, a lost context) the sheet is a CSS surface instead, as it always was: the page's ground (paper with the still grain, at the same size as the page's own, §3.3) masked the same way, and nothing is inked.
  - **Opening and closing:** it fades in over 0.24 s (opacity .01 to 1 and 6px down into place, `cubic-bezier(.22, 1, .36, 1)`) and out over 0.18 s (opacity 1 to .01 and 6px up, `ease-in`); the links make the move and the surface stays put, so a sheet scrolled when it closes keeps its surface behind the links for the whole fade.
    - **One value, advanced per drawn frame:** the links' opacity and move, the paper the cloth draws and the page's fade-out under the right half (`<main>`'s mask and the display control) are one value, `--sheet` (0 shut, 1 open), and `shell.js` advances it once for every frame that is drawn, by that frame's share of the fade (the time since the last frame over 240 ms opening, 180 ms closing) and never by more than a quarter of it. CSS and the shader only read it (it is written on each element that reads it, and does not inherit, since an inherited value written on the root every frame would make the browser recalculate the style of every element on the page, thousands of them on a long page): nothing about the sheet is a CSS transition or animation, and the cloth is handed the driver's step so that its paper is the value of its own frame, not the last frame's. Why: a fade that is a function of time (a CSS transition, a CSS animation, a curve timed from a clock) can finish between two drawn frames when one comes late, and the sheet blinks; a fade that moves by a step per drawn frame cannot be skipped, so a late frame only makes the steps bigger (at least two frames between 0 and 1 however far apart the frames are). CI's WebKit showed it: at a pixel ratio of 3 every frame took about 250 ms, and every CSS variant tried (will-change, a layer of its own, no mask, no grain, no scroll lock, no move) blinked from .01 to .99 in one frame, while a fade stepped from script by at most a quarter per frame showed .01, .58, .88, .98. An iPhone that draws the cloth has late frames too; the owner's (iOS 26.6, in Vivaldi) blinked.
    - **The curves** are 1 - q ** k, polynomials of the share of the fade still to go: k = 5 for the opening, within .0114 of `cubic-bezier(.22, 1, .36, 1)` at any time of the fade, and k = 1.7 for the closing, within .010 of `ease-in`. The share is recovered from the value at each step, so a turn-around (a close that interrupts the opening, an opening that interrupts the close) goes on from the value the sheet has, neither snapping nor jumping.
    - **The first frame has no time behind it:** opening the menu displays the sheet at opacity .01 (not 0: WebKit does not paint a layer at opacity 0, so its first full-screen paint would wait for the first frame in which it is above 0, which was 194 ms on CI's WebKit) with the links 6px up, and the fade goes on from the next frame, however long that frame takes to arrive.
    - **Displayed until the close has run:** the sheet stays displayed for the length of its close, taking no pointer, and goes (`display: none`) when the value reaches 0; no timer ends it.
    - **Immediate cases:** with Motion off, or reduced motion with nothing saved, the value jumps to its end at once, and so does a change that cross-fades (§4.5). The items do not fade in one by one: the panel fades as one.
  - **Closing:** besides MENU and Escape, a tap on the sheet's visible left half closes it, as a tap on earendil.com's backdrop does (the sheet's own surface, never a link). WebKit fires no click for a tap on an element it does not take for clickable, so the open sheet has the pointer cursor and `shell.js` listens for the click on the sheet itself.
  - **A change chosen from the sheet** closes it like any close, and nothing snaps (after a report from an iPhone, 2026-10-17: the old page blinked back and the sheet blinked away): at the tap the sheet starts its normal fade out (0.18 s, the driver's, the scroll lock released when it ends), the old content fades out with it, so it never reappears at full strength, and the page is fetched meanwhile; the content is swapped, and fades in, once the close has ended (§4.5). A wash under the tapped link is let go as on a wide screen and fades where the link was.
  - **A change that cross-fades** (a link in the page, Back or Forward) takes the sheet away at once, if it is open, since the cross-fade draws the old chapter above everything (§4.5): the value goes to 0 and the paper, `<main>`'s mask and the links go with it.
  - **Ink (§3.4):** the sheet is inked. The current page's link holds a wash while the sheet is open, and any link the pointer, a finger or the keyboard is on takes one. When the sheet closes (MENU is shut at once; the sheet stays displayed for the 0.18 s of its fade) the paper fades out with the links and the washes are let go, so a wash lingers on the cloth for a moment behind where the sheet was and fades over its usual 1.8 s. Where the cloth is not drawing, the flat fill answers instead.
- **Inside the dashboard** (the five sections of §4.8 and the pages that hang off them: the confirmation pages and the page that shows a new key) the menu changes (owner's decision, 2026-10-22):
  - group 1 lists the five sections in place of the chapters: Overview, Files, Keys, Connected apps and Billing. A section marks itself as the current page, and a page that hangs off one marks its section;
  - group 2 is Docs, Journal and Site (the threshold), then Sign out, a button in a form that POSTs to `/accounts/logout/` with the page's CSRF token, because a GET must never end a session;
  - group 3 is unchanged, and the menu is still one `<nav>`;
  - only a dashboard page draws the button and its token. The error pages, the throttled (429) pages and the `/a/<token>` miss page always draw the ordinary menu, even for a failure at a dashboard address, because they carry nothing that belongs to a person (§4.9);
  - everywhere else the menu is as listed above, with "Dashboard" in place of "Sign in" when signed in.

### 4.5 Changing chapters in place

- **Real pages first:** every chapter is a complete server-rendered page.
- **When `shell.js` is present,** clicking an internal link to a page that uses the same layout family:
  1. fetches the page;
  2. replaces `<main>` and the page metadata (title, description, canonical address and structured data);
  3. records the new address in browser history;
  4. fades the content (below);
  5. updates the menu's current mark, and leaves the menu closed (§4.4).

  The cloth continues uninterrupted.
- **How the content fades:**
  - **A link in the page, Back and Forward cross-fade** `<main>` with a view transition where the browser has them, wherever the page is scrolled, and fade it out and in where it has none. The pictures only fade; nothing slides.
  - **A link chosen from the open phone sheet fades by its own driver, with no view transition.** The sheet starts its normal close at the tap (§4.4), the old content fades out with it (0.18 s, the sheet's closing curve, the same value in every frame), and the page is fetched meanwhile. When the close has ended and the page has come, `<main>`'s content is swapped while it is invisible and then fades in (0.17 s on the sheet's opening curve: 0.35 s in all, the length of the cross-fade). Both fades are one value advanced per drawn frame by at most a quarter of the fade, as the sheet's is (§4.4), so a late frame cannot skip them. From the tap until the swap the old page, invisible for as long as the fetch and the font wait take, is inert, as it was under the open sheet: it can be neither tapped nor tabbed to, so a second tap, common when nothing seems to happen, cannot land on a link nobody can see and start a change that was not chosen; opening and shutting MENU in between does not give it back. MENU pressed again while the sheet is closing opens it again (the visitor changed their mind about the menu, not about the page): the swap then waits until the sheet is shut, with the old page still invisible and out of reach and the scroll lock on for as long as the sheet is open, so the swap never runs under an open sheet; a newer change ends the wait. Why not a view transition here: it needs the sheet gone at once, which put the old page back at full strength and then cross-faded (the blink); it pauses the page's drawing while its callback waits; the scroll lock's release moves iOS toolbars and `dvh`, and a resize cuts a running transition short; and it takes a picture of `<main>`, which for the tallest reading page (about 5,400 px at 375 wide, 16,200 device pixels at 3x) a phone may not take. A newer change wins; Motion off and reduced motion change at once.
  - **A tall page is no reason to leave the cross-fade** between pages in the page: measured at 375 x 3 in Chromium and WebKit, the transition between Terms and Privacy runs, nothing is clipped, and the picture is as sharp as a short page's (WebKit's is sharp; Chromium's is a little soft at every height, a short page's included). A phone's own texture limit was not measured (§13).
- **No change waits on a font:** the faces any page of the layout may show load when the menu opens and when a change begins, in parallel with the fetch, and the change waits for them (400 ms at most, and only if one is still loading) before it swaps the content or starts a transition, while the page is still drawing. A view transition's callback pauses the page's drawing while it waits, so it waits for nothing.
- **Any failure** falls back to an ordinary page load.
- **Back and forward** work.
- **Under reduced motion,** with Motion not saved on, the swap is instant.

### 4.6 Reading pages: Journal, Docs, legal

- **The column:**
  - One Newsreader column of about 65 characters, scrolling with the page.
  - With Motion on, the cloth moves behind the column, calmed as everywhere else (§3.1).
  - Code blocks and tables scroll sideways inside their own boxes; a long word or URL in running text wraps. The page itself never scrolls sideways.
  - Every fenced code block in the column (in a Journal post or a Docs page; the legal documents have none) is the code block of §4.3 (`components/code_block.html`), with its Copy button. The feed carries the plain code, and the quickstart's install tabs keep their own Copy buttons.
  - Bold text is Newsreader at weight 500, its heaviest face, with synthetic bold turned off, so `**bold**` is never drawn in a fallback face.
  - The Journal, Docs and legal pages are one layout family. They change in place among themselves (§4.5); a reading page to a chapter, or back, is an ordinary page load. The menu starts closed on a reading page, as on every page (§4.4).
- **The Journal:**
  - **Index (`/blog/`, labeled Journal):** each post shows its title in the display face, its date in mono, and its description.
  - **Post:** a meta line (JOURNAL · date · reading time), the title, the header image when the post names one (with its alt text), the body, then the older and newer posts. The reading time counts 200 words a minute, rounded up.
  - **Sources:** Markdown files in `backend/blog/posts/`, as today. Addresses under `/blog/` don't change.
  - **Feed:** `/blog/feed.xml`, titled Veilfile Journal, carries each post's full text beside its description. Every link in it is absolute, on the same origin as the canonical addresses.
- **Docs:**
  - Each page opens with a short list of its sections (its `##` headings; none when there are fewer than two) and ends with links to the previous and next pages, in the order of their `order`. The index lists the pages.
  - The sources stay in `backend/docs/pages/*.md`. The quickstart's install tabs and Copy buttons use the shell's own `tabs.js` and `copy.js`.
- **Legal:**
  - The same layout. A document's own heading is the page's heading, and a list of its sections follows it, as on Docs.
  - The document text is owner-only and does not change in this project: a test compares each page's text with its Markdown source.
- **Head:** every reading page has a unique title, a description, a canonical address and a link preview image (a Journal post's own `image` when its front matter names one). Structured data is in §6.6.

### 4.7 Forms

This covers sign in, sign up, password reset, password change, email verification, checkout confirmation, device code entry, approval and results, OAuth consent, and the dashboard's confirmation pages and the page that shows a new key (§4.8).

- **Layout:**
  - One narrow column (`layouts/form.html`, `--form-measure`). Mono labels above inputs (`components/field.html`); errors that belong to no field are `components/form_errors.html`.
  - Each input's 1px line is drawn in `--muted`, not `--hair`. `--hair` is 1.45:1 on Paper (1.72:1 on Ink), and an input has nothing but its line to mark its boundary, which WCAG 1.4.11 asks to meet 3:1.
  - One main action, with quieter secondary links. The action is a hairline box (its 1px `--hair` border stays: its text label identifies it) with a mono label inside, on `--box`, that takes the ink wash on hover and press like every control (owner's decision, 2026-10-18). The class `interactive` is what gives it that, so the action is `<button class="action interactive" type="submit">`. Two actions of equal weight, such as Approve and Deny, sit together.
  - The quieter pieces: secondary actions are `label interactive` links or buttons in `.form-links`, a status line that is not an error is `.notice` (`role="status"` where it is announced), and a checkbox or radio is a `.check` row with its box drawn in `--muted`.
  - A form page has no layout family (§4.5): it loads as an ordinary page, with no in-place changes and no `data-layout`. It sets its own `<title>` as `Name | Veilfile`, like every page of the site, and `<meta name="robots" content="noindex">` where it holds a token or a one-time state.
- **Errors** (owner's decision, 2026-10-18: no colour):
  - A plain sentence under the field that says what happened and what to do next, in full `--ink` (not `--muted`), with the field's line drawn solid `--ink` and a small mono label that reads "Check this" (uppercased by CSS).
  - The input says so to a screen reader too (`aria-invalid="true"`, tied to the sentence with `aria-describedby`), and a summary of errors that belong to no field is `role="alert"`.
- **Signed in:** a signed-in visit to sign in or sign up goes to the safe `next`, or to `/app/` (owner's decision, 2026-10-18).
- **What does not change:** every URL, form field and hidden field, status code and redirect; the query strings that ride along (`?next=`, `?plan=`, `?user_code=`), because the sign-in, sign-up, checkout, password-reset, verification and OAuth consent forms post to their own address with no `action`, the device code entry is a GET to `/device` (so a typed code reaches the address the CLI opens) and the device approval posts to `/device/approve/`; and the phrases the CLIs print or match.
- **Device page:** rewritten in the voice pass, so its copy stays as it is for now. The current copy is "Enter the code shown in your terminal. Only enter a code from a veilfile login you ran yourself." Only the check mark on the result page, which none of the three fonts has, is a word or drawn with CSS.
- **OAuth consent:** names the requesting app and says, in plain words, what the `mcp:use` scope lets it do. It has a heading, keeps its two forms (Deny and Authorize), its field names and its "unverified app" framing, and is never framed (`X-Frame-Options: DENY`). A request that django-oauth-toolkit reports as an error (no valid client) shows the error page, not a consent form.

### 4.8 The dashboard (`/app/`)

The dashboard uses the same shell and follows the Motion setting. It is for signed-in visitors: a signed-out request for any address under `/app/` (the staff catalog aside) goes to sign in and comes back, and every answer under `/app/`, the error pages included, carries `X-Robots-Tag: noindex, nofollow`. The pages say it in a `<meta>` too, and `robots.txt` still disallows `/app/`. Its sections:

| Section | Address | Contents |
|---|---|---|
| Overview | `/app/` | In this order: the line a return from Stripe leaves (`?billing=`), a prompt to verify the email, anything over a limit (keys, storage, how long links last, and uploads this month), the plan, the meters (uploads this month, storage, API keys, how long links last) and the account (the email and whether it is verified, Change email, Change password). |
| Files | `/app/files/` | Each file's name, size, when it was added and when it expires, secret flags (in words) and state (live, expired or held), 50 to a page, newest first, with Copy and Revoke while its link works. Paginated on the server (`?cursor=`). |
| Keys | `/app/keys/` | Each key's name, prefix, creation, last use and state (a revoked key stays listed), and a form to create one: the raw key is shown once, with Copy and a clear warning. Revoke. |
| Connected apps | `/app/apps/` | OAuth grants: the app's name, the host it returns to and when it was last authorized. Disconnect. |
| Billing | `/app/billing/` | The plan and the status of its subscription in the customer's words (Active, Payment overdue; never Stripe's codes), the status warnings, anything over a limit, Upgrade (to Stripe Checkout), Switch plan (behind a confirmation page), Manage billing (the Stripe portal, for a workspace that has a Stripe customer) and a switch that is waiting for payment. |

Every address, with the name the menu and the tests know it by. The five sections are the layout family `app`; every other page is an ordinary page load.

| Name | Address | Methods | Page |
|---|---|---|---|
| `app` | `/app/` | GET | Overview |
| `app-files` | `/app/files/` | GET | Files (`?cursor=`) |
| `app-file-revoke` | `/app/files/<uuid:artifact_id>/revoke/` | GET, POST | Confirm, then revoke |
| `app-keys` | `/app/keys/` | GET, POST | Keys; the POST creates a key and answers with it |
| `app-key-revoke` | `/app/keys/<int:key_id>/revoke/` | GET, POST | Confirm, then revoke |
| `app-apps` | `/app/apps/` | GET | Connected apps |
| `app-app-disconnect` | `/app/apps/<int:app_id>/disconnect/` | GET, POST | Confirm, then disconnect |
| `app-billing` | `/app/billing/` | GET | Billing |
| `app-billing-switch` | `/app/billing/switch/<slug:plan>/` | GET, POST | Confirm, then switch |
| `app-billing-checkout` | `/app/billing/checkout/` | POST | Hand-off to Stripe Checkout (303); a GET goes to Billing |
| `app-billing-portal` | `/app/billing/portal/` | POST | Hand-off to the Stripe portal (303); a GET goes to Billing |

Any other address under `/app/` is the shell's 404, and `/app` without its slash goes to `/app/`.

- **Rendering:** every section is a server-rendered Django view (`backend/dashboard/`) that calls the same plain functions as the JSON endpoints (`drop_core/account.py`, `drop_billing/account.py`): usage, keys, files, connected apps and billing have one set of rules, so a page and the API cannot disagree.
- **Moving around** (owner's decision, 2026-10-22): the five sections are one layout family, `app` (§4.5). A link between them, Back and Forward cross-fade `<main>` in place: the title changes, the menu marks the current section and focus goes to the `h1`. The pages of Files (`?cursor=`) change in place too. Everything that acts, every confirmation page and the page that shows a new key is an ordinary page load. A section fetched after the session ended is redirected to sign in, so that change falls back to an ordinary load, which reaches sign in. A section's head is its title ("Files | Veilfile") and the `noindex` tag, with none of the public head (no canonical address, Open Graph tags, structured data or feed links), and its faces are the title's italic 300 and Newsreader: the dashboard sets no text in italic 400.
- **State changes:** every one is a POST from a form in the page, with its CSRF token (Sign out is one: a button in the menu, §4.4). A GET never changes anything, never revokes and never creates a Stripe session, and a POST without its token is the "Form not sent" page.
- **Destructive actions:** revoking a key, a file or a connected app, and switching between paid plans, goes to a confirmation page (a GET that only shows a form and names what it will change), then a POST, then a redirect back with one line of confirmation, such as "Key revoked." The line is Django's messages framework: it shows as a status notice at the top of whichever dashboard page loads next, and nothing secret is ever put in one. There are no JavaScript dialogs. Upgrade, Keep and Manage billing take nothing away and ask nothing first.
- **Whose objects:** every object an address names (a key, a file, an app) is looked up within the signed-in person's own account. Another person's, and a missing one, give the shell's 404, never a 403 and never a change, so the two cannot be told apart. An object that is the person's own but can no longer be acted on, such as a key already revoked or a file whose link has stopped working, gets a sentence and a redirect back to its list.
- **The new key** (owner's decision, 2026-10-22): the raw key is written once, in the answer to the POST that makes it (200, `Cache-Control: no-store`). It is not carried across a redirect, because it must not be in the session, a message, a cookie, a URL, a log, a cache or the database (AGENTS.md, invariant 1). The Keys page issues a one-time token with its form and keeps it in the session, and the POST spends it: a reload, which sends the POST again, makes no second key and finds the token spent, and the page says that no key was made (409). The warning "Copy this key now. You won't see it again." takes focus, the key is in a code block with Copy, and Done goes back to Keys. A refusal shows Keys again with a sentence and no key: the email is not verified (403), the plan's key cap is reached (402) or the name is not a name (400).
- **Times and sizes** (owner's decision, 2026-10-22, for the times and their zone): the server writes every time in UTC inside `<time datetime data-local>` ("22 Oct 2026, 13:58 UTC"), and `behaviors/local-time.js` writes it again in the visitor's own zone and names the zone: the date, the time to the minute and the zone's short name (`Oct 22, 2026, 9:58 AM EDT`, or `7:28 PM GMT+5:30`), so a person reading a time knows which one it is. It does so on load and after each change in place. With no script the UTC text, which names its zone, stays. The uploads month on Overview is the UTC calendar month, and says so. A size is written as `999 B`, `1.5 KB`, `1.5 MB` or `25.0 GB` (1024 to the unit, one decimal), so a storage cap in gigabytes reads as one.
- **Billing:** plan names and prices come from `plans.py`, never from a template. Checkout and the portal are POSTs that send the browser (303) only to an `https://` address that Stripe's API returned (the portal sends the visitor back to Overview, as before). A GET makes no Stripe call, so every refusal arrives as one sentence on Billing after the click, never as a 500. Manage billing shows only for a workspace that has a Stripe customer.
- **What a customer reads when Billing refuses** (owner's decision, 2026-10-22): only Veilfile's own fixed sentences, never Stripe's text, an API error code or operator wording such as "this Veilfile instance" or "until Stripe is connected". Each refusal the page can explain has its own sentence (a payment that did not go through on a switch, a workspace that already has a subscription, a past-due one, no subscription to switch, more than one, a plan that is not one of the plans, no billing account). A 409 `already_subscribed` is answered in Veilfile's words whatever the API said, and the advice that depends on the subscription's status (past due, unpaid, incomplete, paused) is the status warning Billing prints itself. A failure the page cannot explain (a 502 from Stripe, a 503 `billing_not_configured`, a status or code it does not know, an address that is not `https://`) ends with a short reference: "Billing isn't available right now. Try again later. Reference: 4F9A2C" (six capitals or digits, new each time). Stripe's own message goes to the log, not the page: one line, `billing: dashboard refusal ref=4F9A2C where=checkout status=502 code=stripe_error message=...`, through the `drop_billing` logger (ERROR for those failures, WARNING for a refusal the page explains), carries the status, the code and Stripe's message under the same reference, so the owner finds what Stripe said by the reference a customer reads out. The JSON endpoints answer exactly as before, "Stripe: ..." included. A switch that Stripe answers with `payment_required` is remembered in the session for a day (the plan and an `https://` invoice address, nothing secret), so Billing offers "Complete payment" and "Keep {Plan}" until a later switch or the subscription settles it. No dashboard request sets a plan (AGENTS.md, invariant 3).
- **Account pages:** `/accounts/password_change/` and its done page are pages of the form layout (§4.7), linked from Overview. The email change stays at `/accounts/verify-email/`.
- **Phones:** the tables become stacked rows (§4.10).
- **Dashboard API endpoints:** the session-only JSON endpoints that the old React dashboard used stay documented and working, and every one answers exactly as before (AGENTS.md, invariant 4): the published CLIs call `/usage`, `/keys` and `DELETE /keys/<id>`. The dashboard no longer calls them; it calls the functions they share. Any removal is a separate, explicit decision of the owner.
- **Component catalog:** `/app/components/` is staff-only and renders every component in Paper and Ink, Motion on and off.

### 4.9 Error pages

- **Missing or expired file link (`/a/<token>`):**
  - One shell page for both cases, with the same 404 status as today. The response must not reveal whether a token ever existed: a missing link and an expired one (and a held one, or one whose file is gone) get the same status, body and headers, `Vary` and `Cache-Control` included, and the page echoes nothing of the request or the exception.
  - Copy: "Nothing here. If this link was shared with you, its moment may have passed."
  - Only the HTML page changes. A link that resolves still serves the file's bytes untouched, never wrapped in the shell.
- **Other errors:**
  - 404, 403 and 429 (throttled) use the shell, and so does the page for a failed CSRF check (`CSRF_FAILURE_VIEW`), which shows no reason. No error page shows the exception, the request path or anything that belongs to a person. The 404 for an unknown Docs or Journal slug still marks Docs or the Journal as the current section in the menu.
  - Every 429 is the throttled page, with the `Retry-After` the throttle sets: sign in, password reset, sign up and email verification (the last two answered with a bare sentence before). It is the same bytes for a real account and a missing one, so it carries no CSRF token and nothing per person.
- **500** is a self-contained template (`500.html`) with inline CSS and no context (no request, no static manifest, no URL reversing), so it still renders when something deeper is broken.

### 4.10 Phones

- **Corner labels:** the mark (24px) and MENU (13px) stay in the top corners; the switches, the appearance switch at the left and FX (the motion switch) at the right, wait at the end of the page (§4.1).
- **The menu:** a full-screen sheet that fades in over the cloth, solid behind the links and clear across the left half, inked like the rest of the page (§4.4). Its fade, the links', the paper's and the page's fade-out under it, is one value advanced per drawn frame, not a timed transition: on a phone drawing the cloth a frame can come late, and a timed fade would be over before it was drawn.
- **Threshold line:** scales down.
- **Install box:** tabs stay above it, and the command scrolls inside the box.
- **Reading pages:** fill the width with a 20px margin.
- **Forms:** inputs are 16px or larger, and a focused input scrolls to below the top fade (`scroll-padding`), clear of the mark and MENU. A form page has no layout family, so nothing about the phone sheet or its changes differs on it.
- **Dashboard tables:** become stacked rows (`table.rows`): each row is a block and each value is led by its column's name, and the first column, the name a person gave, wraps anywhere, so that a 255-character key name or a 512-character file name never scrolls the page sideways. They stack by the width of the dashboard's column (a container query at 40rem), not the window's: between the corners the column is about 394px wide in a 1024px window and 624px in a 1280px one, so a window query would leave a table of six columns far too wide. The table form shows from a window of about 1300px, so at 1280px the rows are stacked in the column's 624px, which the owner accepted.
- **The cloth:** renders at a resolution that follows the screen, from 0.65 up to 1.2 (§3.1).

## 5. Voice and copy

- **Audience:** the site speaks to the person setting Veilfile up. Their agents do the work ("what your agents make", "your agent pushes a file").
- **Style:** plain, calm and specific. Short sentences, no hype, no exclamation marks. American spelling, as the site uses today.
- **Honesty:**
  - Never imply a feature that doesn't exist. This is why "seen once" became "seen for a moment": links work for anyone who has them until they expire.
  - Product facts must match their sources: `docs/API.md`, `plans.py`, `backend/drop_core/sweep.py`, and the secret scanner's behavior.
- **Labels:** mono caps, short and literal.
- **Errors:** what happened and what to do, with no apology.

## 6. Architecture

### 6.1 What's removed

- The `frontend/` directory, removed in step 6 of §11: React, react-dom, react-router-dom, Vite, `Landing.jsx`, `Dashboard.jsx`, `Ambient.jsx`, `api.js` and `styles.css`, with its lock file.
- The Node build stage in the `Dockerfile` and its copy of `frontend/dist`, and the "Build the frontend" and `setup-node` steps in CI's `backend` job, removed in the same step, with `FRONTEND_DIR` and `FRONTEND_DIST` in the settings, the test runner's stand-in `index.html` (`config/test_templates/`) and the `.dockerignore` and `.gitignore` lines for `frontend/dist`. Node stays for `site-tests/` (axe-core) and the `npm/` CLI only.
- The old includes: `ambient.html`, the old `menu.html`, `cursor.html`, `page_transition.html`, and the per-template inline CSS. `cursor.html` and `page_transition.html` went with the reading pages (§11, step 4); the others went with the forms (step 5), once no template included them.
- `theme_boot.html` was replaced by the new boot script (§6.4) and removed with them.

### 6.2 Templates

```
backend/templates/
  shell.html              base: head, metadata blocks, boot script, corner labels, menu, <main>; the cloth's canvas is added first in <body> by veil.js
  layouts/
    chapter.html          threshold and chapters: centered content block
    reading.html          Journal, Docs, legal: long column
    form.html             auth, device, OAuth and the dashboard's confirmation pages: narrow form column (--form-measure); no registry entry, so no in-place changes
    app.html              the dashboard's five sections: one column from the top of the page, and the layout family `app`
  chapters/               threshold.html, about.html, how.html, begin.html, promises.html, plans.html
  dashboard/              overview.html, files.html, keys.html, apps.html, billing.html; the pages that hang off them: file_revoke.html, key_revoke.html, key_made.html, app_disconnect.html, billing_switch.html; over_limits.html, an include
  components/             one file per component, e.g. install_line.html, code_block.html, menu.html, plan.html, field.html, form_errors.html, meter.html, file_list.html
```

**Component rules:**

- Each component starts with a comment listing its inputs.
- It is included only with `{% include "components/<name>.html" with … only %}`, so it sees nothing it wasn't given.
- It has exactly one stylesheet, in `static/css/components/<name>.css`.
- It has an entry in the catalog and a rendering test.
- A `{% csrf_token %}` inside a component included with `only` renders nothing, so a form's token is written in the page's own template, or passed in as an input (the menu's Sign out form takes the page's token as `sign_out`).

### 6.3 Static files

```
backend/static/
  css/tokens.css          every color, size and space; Paper and Ink
  css/base.css            reset, type, shell, layouts
  css/components/*.css
  js/shell.js             menu, in-place chapter changes, preferences, appearance switch
  js/veil.js              WebGL cloth, the calm, ink; loaded only when Motion is on and WebGL works
  js/behaviors/*.js       small modules attached by data attribute, e.g. copy.js (data-copy), tabs.js
  fonts/                  WOFF2 files split by Unicode range
  img/grain-paper.png, grain-ink.png   tiling still-grain textures, one per appearance
  img/mark.svg, favicons
```

- All JavaScript is plain ES modules. Nothing is bundled or transpiled.
- Static storage switches to WhiteNoise's `CompressedManifestStaticFilesStorage`, which adds hashed filenames and compressed copies, so files can be cached for a long time. Today the site uses Django's default storage without hashing; only the old Vite build hashed its own files. `STATICFILES_DIRS` points at `backend/static/` instead of the removed `frontend/dist`. Tests keep using plain storage, so they don't need `collectstatic`.
- WhiteNoise writes gzip copies only: brotli is not installed, and adding it is a new dependency. Cloudflare compresses at the edge either way.
- The implementation plan decides whether the component stylesheets are served as separate files or joined by a small Python step at `collectstatic`. Either way, the source stays one file per component.

### 6.4 Preferences

- **Storage:** Motion and appearance are stored per browser in `localStorage`, with every read and write guarded. The keys and values are the ones the pages already used, so a visitor's saved choice carries over: `veilfile-theme` is `light`, `dark` or `system` (shown as Paper, Ink and Auto), and `veilfile-motion` is `on` or `off`. With no stored motion value, the default comes from `prefers-reduced-motion`.
- **Boot script:** a tiny inline script in `<head>` sets `data-theme` and `data-motion` on `<html>` before first paint, so the page never flashes the wrong appearance.
- **Fallback:** without storage, the page uses the system settings.

### 6.5 One registry of public pages

A new small Django app, `backend/pages/`, owns the public pages. It holds:

- `registry.py`, the list described below;
- the views for the threshold and chapters, which move here from `drop_core/views.py`;
- the sitemap view.

Do not name it `site`, because that would shadow Python's built-in `site` module. `registry.py` lists every public page:

- the address name, title and description;
- its menu group and layout;
- whether it belongs in the sitemap. The menu's groups and their order are MENU in the same file; each Page holds its layout, which in-place chapter changes compare.

The menu, `sitemap.xml`, page metadata and tests all read from it. Journal posts and Docs pages are added to the sitemap from their loaders, as today, and the `reading` layout family lists them too: `layout_paths("reading")` is the five registry pages and every published post and Docs page, and a post's or Docs page's head (title, description, canonical address, image and structured data) is built from its loader, so a new post or page needs no registry entry. `docs/llms.txt` stays hand-written, and a test checks that it mentions every chapter, the Journal and its feed, and the MCP address.

### 6.6 Search and AI visibility

- **Metadata on every public page:** a unique title, a 70 to 160 character description, a canonical address, Open Graph tags and an image (img/og.png: the mark on paper, 1200 by 630; a Journal post that names an `image` uses it).
- **Structured data:** `Article` for Journal posts (as `BlogPosting`) and for Docs pages (as `TechArticle`), both kinds of `Article`; `Product` and `Offer` for plans; `Organization` site-wide. It swaps with the page during in-place changes (§4.5), as the rest of the head does.
- **Nothing hidden from crawlers:** content never depends on JavaScript. The install commands appear in full on `/begin/`.
- **Unchanged:** the funnel tracking on the landing page, `robots.txt` and the noindex rules for `/app/*` (the header on every answer under it, and a `<meta>` on each of its pages: §4.8).

## 7. Muse's recipes and checks

### 7.1 Recipes

A new **Site recipes** section in `AGENTS.md`, beside the existing pricing recipe. Each recipe gives when to use it, the exact files, numbered steps, a copyable example, and the check command.

1. **Write a Journal post.**
   - Add `backend/blog/posts/<slug>.md` with this header:
     - `title`, up to 60 characters
     - `date`
     - `slug`, equal to the file name
     - `description`, 70 to 160 characters
     - optionally `image` with a required `image_alt`
   - The file name is the slug, with no date prefix.
   - The body is Markdown only: no raw HTML, and no first heading that repeats the title.
   - Run the check, then open a PR.
2. **Edit a chapter's words.** In `backend/templates/chapters/<name>.html`, change text only, inside the marked blocks.
3. **Edit Docs.** `backend/docs/pages/<name>.md`.
4. **Legal pages.** Owner-only. Stop and ask.
5. **Change or add a component.** Its template, its stylesheet, its catalog entry and its test.
6. **Change a color, size or spacing.** `static/css/tokens.css` only.
7. **Change prices.** The existing pricing recipe. Plans copy is generated from `plans.py`.

The "Run the tests" section loses its frontend build line, and the repo map is updated to match.

### 7.2 Checks

These run inside the normal `python manage.py test`. Every failure message names the file, the line, the problem and the fix. For example: `blog/posts/where-agents-put-screenshots.md: description is 212 characters; keep it between 70 and 160 so search results don't cut it off.`

- **Posts:**
  - the header is complete and within its limits;
  - the slug matches the file name;
  - the date is valid;
  - there's no raw HTML;
  - every image exists and has alt text;
  - the title isn't repeated as a heading.
- **Characters:** every character in posts, Docs, legal pages and templates is covered by the shipped font ranges. A failure names the character, the file and the line.
- **Links:** every internal link on every public page resolves. The test client renders each page and follows its internal links.
- **Pages:** every public page has a title, a description of the right length and a canonical address. The sitemap lists every page in the registry. `llms.txt` mentions every chapter.
- **Templates and CSS:**
  - no `style=` attributes;
  - no color values outside `tokens.css`;
  - components included with `only`;
  - every component has a catalog entry and a stylesheet;
  - no page writes its own `<html>` or `<style>`: only `shell.html` and `500.html` are whole documents, and only `500.html` has a `<style>` element;
  - every `{# #}` comment opens and closes on one line, because Django prints a comment that spans lines on the page as text;
  - no stylesheet takes the focus outline away;
  - every form page has the shell's skip link and `<main>`, one `<h1>` inside it, a title of the form `Name | Veilfile`, `interactive` on every button and `.action` link, and none of the old pages' chrome.
  These template and CSS rules apply to the new shell's files: `shell.html`, `components/`, every template that extends the shell or a layout (wherever it sits: `layouts/`, `chapters/`, `pages/`, `blog/`, `docs/` and `legal/` today) and `backend/static/css/`. Older templates are checked as they move onto the shell: a template is found by what it extends, not by a list of directories, so a page that moves onto the shell is under the rules at once, and a test fails for an `include` or `extends` that names a template that does not exist. Raw HTML in Docs is allowed only for the files listed in `RAW_HTML_ALLOWED` in `pages/content_checks.py`, each with its reason. Legal documents are checked for characters only.

## 8. Performance

- **Content first:** the HTML and the critical CSS render the text without JavaScript. `shell.js` is deferred, and `veil.js` loads after the page is ready.
- **Budgets:**
  - `shell.js` plus behaviors: 10 KB or less, gzipped;
  - `veil.js`: 15 KB or less, gzipped;
  - one font file per face on a typical page;
  - Largest Contentful Paint under 2.0 s on a mid-range phone on 4G;
  - layout shift about 0.
- **An adaptive cloth:**
  - the render scale follows the screen (§3.1): the first step is 0.4 of the device pixel ratio, between 0.8 (0.65 on phones) and 1.2, and each step down is three quarters of the one above, to a floor of 0.35 (0.28 on phones), which also takes any step that would come within 15% above it;
  - pause when the tab is hidden (the canvas is fixed to the window, so it is never off-screen);
  - lower the resolution if frames run long, but undo a step that does not shorten them (and leaves them long; a step that brings them under the limit stays, however little it gained): frames held at 30 a second by the display (iOS Low Power Mode) are not a slow GPU, and a lower resolution there is only blocky, so the cloth goes back to the step before, stops stepping down and the tab remembers it (`veilfile-veil-capped`);
  - a step that is kept is remembered for the tab too (`veilfile-veil-step`), so the next page starts there and only goes on down: a slow phone at 3x, which starts at 1.2, pays for its walk down once, not on every page;
  - switch to the still page for the visit if it still can't keep up.
- **Motion off** means no animation loop at all.

## 9. Accessibility

- **Contrast:** WCAG AA in both appearances. 4.5:1 for small text, guaranteed by the cloth's own range (§3.1: the calm and the floor), not left to chance over moving cloth: `--muted` holds 4.5:1 or better over every pixel the cloth can draw, and `--ink` text 7:1. Muted labels on flat ground measure about 6.6:1 in Paper and 7.4:1 in Ink. A new text colour is checked against the whole canvas, in both appearances, by the browser checks.
- **Keyboard:**
  - every control is reachable;
  - focus is visible (§3.4);
  - there's a skip link;
  - the menu starts closed, closes on Escape (returning focus) and on a click elsewhere, and while the phone sheet is open the page beneath it is inert;
  - the install tabs are a proper tablist.
- **Semantics:**
  - landmarks (`header`, `nav`, `main`, `footer`), with no `header` or `footer` element inside `main`; on reading pages the section list and the previous and next links are each a `nav` with its own label;
  - real links and buttons;
  - `aria-expanded` on MENU, `aria-pressed` on the motion switch (named "FX: motion", §4.1);
  - the appearance switch has a label that states its value;
  - the canvas is `aria-hidden`;
  - Copy announces "Copied".
- **Forms:**
  - one `<h1>` per page, inside `<main>`;
  - every input has a visible `<label for>`, and its line meets 3:1 against the page (WCAG 1.4.11);
  - an error sets `aria-invalid="true"` and is tied to its field with `aria-describedby`; a summary of errors that belong to no field is `role="alert"`;
  - `autocomplete` tokens are kept, and `autofocus` is used only where the page exists to take one input, and on the first field in error when a form is sent back with errors (rendered by the server, replacing the page's usual one, so one field has it); an error that belongs to no field leaves focus where the page had it and is announced by its `role="alert"`;
  - inputs are 16px or larger on phones, so iOS does not zoom in on them;
  - nothing scrolls sideways at 320px.
- **Dashboard:**
  - one `<h1>` per page, the section's name; each part of a page is a `<section>` with an `<h2>`;
  - every table has a `<caption>` or an `aria-label` and header cells with `scope="col"`; on phones the stacked rows keep each value's label, which the cell carries as `data-label`;
  - the one line after a redirect is a `role="status"` notice at the top of the page;
  - a meter is a `role="meter"` bar named by its label and described by its figures, with its minimum, maximum and value; the numbers are always in the text as well, and a bar over its limit says so in words;
  - Copy on a file's row says "Copied" in the one status line of the list, and the link it copies is never drawn: it sits in a visually hidden element, hidden from screen readers, in a page that is never cached;
  - the page that shows a new key puts focus on its warning, so the key is read after it;
  - a time is exact and names its zone: the UTC text is in the page, and the visitor's own zone, named, replaces it when the script runs.
- **Motion:** reduced motion means the still page. Cross-fades only, never slides.
- **Forced colours** (Windows High Contrast): the cloth does not run (the still page), because the system's text colours cannot be kept readable over it.

## 10. Testing and CI

- **Django tests,** in the existing `backend` job:
  - every page in the registry renders with its metadata, and its content is present without JavaScript;
  - the dashboard views: permissions, whose objects (another person's is the shell's 404), forms, POST and redirect, CSRF, the confirmation steps, the new key shown once and kept nowhere, and billing under each Stripe failure (with the billing mocks; nothing contacts Stripe);
  - `/trust/` redirects to `/promises/`;
  - missing and expired `/a/<token>` give identical responses (status, body and headers), and a found link serves the file's bytes untouched;
  - a throttled (429) page is identical for a real account and a missing one;
  - every form page keeps its fields, hidden fields, status codes and redirects (`?next=` and `?plan=` survive sign in and sign up, the device page and OAuth consent included) and shows each of its states: field errors, non-field errors, throttled, an invalid or expired code, an unverified email;
  - the error pages as production renders them (DEBUG off): 404, 403, the CSRF failure and 500, which renders with no request and no static manifest;
  - all of §7.2.
- **Browser-check jobs, `site`, `site-cloth`, `site-ink`, `site-reading`, `site-phone`, `site-forms`, `site-catalog` and `site-dashboard`:** the same browser run in parts, one job each, each with its own 20 minutes. `site` runs the page checks (`--part pages`: the threshold and the chapters); `site-cloth` runs the cloth's checks (`--part cloth`: WebGL in headless Chromium, a virtual clock and canvas read-backs); `site-ink` runs the ink washes' checks, the phone menu's sheet included (`--part ink`: the same machinery; the cloth's checks were over 16 minutes in one job, so they are split in two); `site-reading` runs the reading pages' checks (`--part reading`: the Journal, the Docs and the legal documents, whose long columns, section lists, code blocks, previous and next links and changes among themselves need checks of their own); `site-phone` runs the phone menu's checks (`--part phone`: the sheet's opening and closing, the changes chosen from it and the faces they wait for, in Chromium and in WebKit, the engine of every browser on an iPhone, which no other job installs); `site-forms` runs the forms' and the error pages' checks (`--part forms`: every form, device, OAuth and error page in each state it has, with the phone's top fade, autofill, Enter and `?next=` through sign in, in Chromium, after `site-tests/make_form_fixtures.py` makes its users, device codes, OAuth clients and the 404 and 500 as production draws them); `site-catalog` runs the component catalog's page checks (`--part catalog`: `/app/components/`, signed in as staff, in every width, appearance and Motion and in its edge cases, with the same checks `site` runs on a page; the catalog shows every component in each of its states, which took 530 s of `site`'s 20 minutes once the form components were in it). A later part is one more job of this list.
  - Each uses Playwright for Python and headless Chromium, and starts the app. CI runs them, like every other job, on a GitHub-hosted runner of its own, side by side (§13).
  - `site-dashboard` signs in as fixture users that `site-tests/make_dashboard_fixtures.py` makes (keys, files, an OAuth grant, and the longest names the models take) and checks: the sections as `site` checks a page; the changes in place among them (the title, the head, the menu's mark, focus on the `h1`, Back and Forward, and a section fetched after the session ended); Sign out from every section with and without script; a new key shown once, focused and copied, and gone after a reload and after Back, with no second key made; a revoke; the tables as stacked rows at 375px and 320px with a 255-character key name, a 512-character file name and sizes in gigabytes; and times in the visitor's zone, with the zone named, with script and in UTC without. CI has no Stripe, so every checkout, portal and switch there is refused with the customer's sentence and a reference, and Billing is seen that way and with stored states; the Stripe flows are Django tests. One short check runs in WebKit too, the engine of iPhones. It saves its screenshots as `site-dashboard-screenshots`.
  - `site`, `site-reading`, `site-forms` and `site-catalog` between them visit every public page and every form and error page in each state it has; `site-dashboard` visits every page of the dashboard, signed in with fixture users.
  - `site` runs at 375px and 1280px, in Paper and Ink, with Motion on and off; `site-reading` runs them in Paper and Ink with Motion on (Motion changes only how a change fades, which its change checks watch); `site-forms` runs each state at 1280px, 375px and 320px in Paper with Motion on, and the sign-in page with an error, the device approval page and the 404 again in Ink (the cloth is stubbed there; the other form and error pages are checked in Paper only, since the cloth's own Ink checks open the chapters); `site-catalog` runs the catalog as `site` runs a page.
  - `site`, `site-reading`, `site-forms` and `site-catalog` fail on:
    - sideways page scroll;
    - console errors;
    - fonts that fail to load;
    - accessibility violations found by axe-core;
    - a broken menu, Come in, Copy or chapter change;
    - (`site-forms`) a field without a label, an error not tied to its field, a button without the ink wash, text under 16px in a field on a phone, a focused field under the phone's top fade, a form that Enter does not submit, a 500 that asks for a file, and a `?next=` that is lost.
  - `site` saves screenshots of every page and state as a CI artifact (`site-screenshots`), so PRs can be reviewed visually; `site-reading` saves its own (`site-reading-screenshots`), `site-forms` its own (`site-forms-screenshots`), `site-catalog` its own (`site-catalog-screenshots`), and `site-cloth` and `site-ink` the few their checks take (`site-cloth-screenshots`, `site-ink-screenshots`).
- **Branch protection:** every browser-check job (`site`, `site-cloth`, `site-ink`, `site-reading`, `site-phone`, `site-forms`, `site-catalog` and `site-dashboard`) is a required check, beside `backend`, `npm-cli`, `python-cli` and `names`: a ruleset on `main` in the public repository requires them.

## 11. Delivery

Each step below is a reviewable PR that leaves the site working.

1. **Foundations.**
   - tokens, base CSS and the fonts;
   - `shell.html` and the boot script;
   - `shell.js` and the menu;
   - the mark and favicons;
   - the `pages` app and registry;
   - the content checks;
   - the `site` CI job, starting with whatever pages already use the new shell.
2. **Threshold and chapters,** with the still page only: no cloth yet.
3. **The cloth.** `veil.js`, the calm, the ink, the adaptive rendering, Paper and Ink in the shader.
4. **Reading pages:** Journal, Docs and legal, on one reading layout, with the `site-reading` job for their browser checks. The old includes only they used (`cursor.html`, `page_transition.html`) go with them; `ambient.html`, the old `menu.html` and `theme_boot.html` stayed while the forms used them and went with the forms (step 5).
5. **Forms, device, OAuth and error pages,** on one form layout, with the `site-forms` job for their browser checks. The old includes (`ambient.html`, the old `menu.html` and `theme_boot.html`) go with them, once no template includes them.
6. **The dashboard** as Django views on a layout of its own, with the `site-dashboard` job for its browser checks: five sections that change in place, and confirmation pages and a page that shows a new key that do not (§4.8). The same PR removes React, Vite and Node: `frontend/`, the Docker build's Node stage, CI's frontend build, `FRONTEND_DIST` and the test runner's stand-in `index.html`. Node stays for `site-tests/` and the `npm/` CLI only (§6.1).
7. **Voice pass and launch:** final copy with the owner, AGENTS.md recipes, and branch protection for the browser-check jobs (`site`, `site-cloth`, `site-ink`, `site-reading`, `site-phone`, `site-forms`, `site-catalog` and `site-dashboard`).

## 12. Out of scope

- API, MCP, OAuth and CLI behavior.
- Artifact serving, beyond the error page in §4.9.
- Pricing.
- The text of legal documents.
- The content of existing Journal posts and Docs pages, except where the voice pass or the new chapters require it, with owner review.

## 13. Risks and open questions

- **Cloth performance on low-end phones.** Mitigated by the adaptive rendering and the automatic still fallback in §8. To be measured on a real mid-range Android device during implementation.
- **Dashboard rewrite (done in step 6 of §11).** The React dashboard was 1,077 lines, and moving its sections to Django views was the largest single piece of work. It kept the behaviours that mattered: the show-once key (now the answer to the POST that makes it, with a one-time token) and the plan-switch confirmation text from `switchConfirmText` (now a confirmation page). What differs is in §4.8: the sections change in place, times are in the visitor's zone, a switch waiting for payment is remembered for a day, and the menu has a Sign out button. What the tests cannot see is how it looks and feels on a phone, which the browser part `site-dashboard` and the owner's own devices check.
- **Copy.** The chapter drafts and the device and error copy need the owner's review before launch.
- **CI time.** The `site` job multiplies pages by states, and the reading pages and the cloth's checks add to it, so they run as jobs of their own (`site-reading`, `site-cloth` and `site-ink`: the same run, in parts), and so do the phone menu's checks, which run in WebKit as well as Chromium (`site-phone`), and so do the forms' and error pages' checks, some 35 states of about 20 pages (`site-forms`), and so do the component catalog's checks, which grow with every component and state its page shows (`site-catalog`), and so do the dashboard's checks, which sign in as fixture users and walk every section and the pages that hang off them (`site-dashboard`). The jobs run side by side, each on its own GitHub-hosted runner, so a pull request takes about as long as its slowest job, and each job's own 20 minutes is a limit on that job alone (AGENTS.md, "Where CI runs"). If it gets slow, run the full matrix on PRs that touch templates, static files or content, and a smoke subset otherwise.
- **Fonts.** Confirm each face's supported Unicode ranges when building the font files. The character check (§7.2) catches gaps.
- **Tall pages and a phone's texture limit.** A view transition between pages in the page takes a picture of `<main>`, and the tallest reading page, Terms, is about 5,400 px at 375 wide, 16,200 device pixels at 3x, just under the 16,384 that a good GPU takes and above what many phones do. Measured on a Mac in Playwright's Chromium (software and Metal) and WebKit, at 375 x 3, between Terms and Privacy and on a page stretched to 14,000 px: the transition runs, nothing is clipped, and the picture is as sharp as a short page's (WebKit's is sharp; Chromium's is a little soft for every height). A phone's own limit was not measured. A change chosen from the phone sheet takes no picture at all (§4.5). If a reading page grows, or a phone shows a blurred or missing picture, look at one.
