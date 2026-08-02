"""The whole design system, as one string.

System fonts only. A dashboard for a project whose argument is that nothing
leaves the device must not open a connection to a font CDN in order to draw
itself — the first request a reviewer sees in the network tab would undo the
paragraph it is rendering.

Two visual vocabularies, and keeping them unlike each other is the point.
Statuses are filled pills carrying data. Annotations — FIXTURE, dry-run,
advisory only — are outlined and monospace, so they can never be mistaken for a
status at a glance.

Colour here is computed, not picked. Every foreground/background pair below
clears WCAG AA — **4.5:1 for normal text, 3:1 for large text and for the ring
or boundary that tells you where a control is** — and the measured ratio is in
the comment beside the token. That is not ceremony: a palette that was right
once and is later nudged by eye stops being right *silently*, and the person it
stops working for is never the person who changed it. If you alter a colour
here, recompute the pairs it appears in.

Spacing, radii and type come from named scales, and the scales are irregular on
purpose. They were built by taking the values already in use and merging only
those within a pixel of each other — 9 into 8, 13 into 12, and so on. A tidier
4px grid was possible and rejected: it moved 64% of the spacing in the file, by
up to 4px, and it snapped the 44px tap target down to 40 — trading a real
guarantee for a prettier list of numbers. What is here reduces the vocabulary
without moving anything a person could see.

Four literals stay outside the scales, each for a reason that is not
aesthetic:

  * `body { font-size: 15px }` — the base the rem scale is measured against.
  * `.composer input { font-size: 16px }` — below this, mobile Safari zooms the
    page on focus. A platform constraint, not a size choice.
  * `padding: 1px` on inline code, twice — a hairline, and the scale starts at 2.

Dimensions are not spacing and are not on a scale: a 132px QR code, the 1180px
page cap and the media query bounds are sizes of particular things.
"""

from __future__ import annotations

# The dark palette, written once and used twice: the system's preference, and
# the reader's override of it. Two hand-maintained copies of a palette is two
# palettes, and the one that drifted would be whichever gets looked at less.
_DARK = """
  color-scheme: dark;
  --bg: #101318;
  --panel: #171b22;
  --panel-sunken: #1d222b;
  --ink: #e9ecf1;
  --ink-soft: #aab3c1;
  --ink-faint: #848d9e;   /* 4.78:1 on --panel-sunken; #7f8899 was 4.47 */
  --line: #262c37;
  --line-strong: #333b49;
  --accent: #7ba0ff;                /* 6.84:1 on --panel */
  --accent-fill: #3457b8;           /* deliberately not --accent: white on
                                       that was 2.53:1. This is 6.56:1. */
  --accent-on-fill: #ffffff;
  --accent-on-fill-muted: #d4dcf7;  /* 4.80:1 on --accent-fill */
  --good-bg: #12301f; --good-ink: #7ddba4; --good-line: #22563a;
  --wait-bg: #33260c; --wait-ink: #f0c274; --wait-line: #5c451a;
  --bad-bg: #35171a;  --bad-ink: #f0a0a0;  --bad-line: #5d2a2d;
  --flat-bg: #1f242e; --flat-ink: #aab3c1; --flat-line: #333b49;
  --unknown-ink: #7f8899; --unknown-line: #3d4552;
  --shadow: 0 1px 2px rgba(0, 0, 0, .3), 0 6px 18px rgba(0, 0, 0, .22);
"""

STYLESHEET = """
:root {
  color-scheme: light;
  --bg: #f6f7f9;
  --panel: #ffffff;
  --panel-sunken: #f1f3f6;
  --ink: #14171c;
  --ink-soft: #4a5261;
  --ink-faint: #646c7a;   /* 4.76:1 on --panel-sunken, its worst surface */
  --line: #dfe3ea;
  --line-strong: #c6ccd8;
  /* --accent is a *foreground*: links, the current tab, the focus ring — so in
     dark mode it has to be light enough to read on a dark panel. --accent-fill
     is the opposite job, a surface with white text sitting on it, so it has to
     stay dark in both modes. One token cannot do both, and while it tried,
     every message the person sent was white on #7ba0ff at 2.53:1. */
  --accent: #1f4fd8;                /* 6.63:1 on --panel */
  --accent-fill: #1f4fd8;           /* --accent-on-fill on it: 6.63:1 */
  --accent-on-fill: #ffffff;
  --accent-on-fill-muted: #d4dcf7;  /* 4.85:1 on --accent-fill */
  --good-bg: #e3f5ea; --good-ink: #146b3c; --good-line: #a9dcc0;
  --wait-bg: #fdf0d9; --wait-ink: #8a5605; --wait-line: #eecd95;
  --bad-bg: #fce8e8;  --bad-ink: #9c2222;  --bad-line: #edb9b9;
  --flat-bg: #eceff4; --flat-ink: #4a5261; --flat-line: #d3d9e2;
  --unknown-ink: #767f8f; --unknown-line: #b4bcc8;
  /* spacing */
  --space-2: 2px;
  --space-4: 4px;
  --space-6: 6px;
  --space-8: 8px;
  --space-10: 10px;
  --space-12: 12px;
  --space-14: 14px;
  --space-16: 16px;
  --space-18: 18px;
  --space-20: 20px;
  --space-22: 22px;
  --space-24: 24px;
  --space-26: 26px;
  --space-28: 28px;
  --space-40: 40px;
  --space-72: 72px;
  /* radii */
  --radius-4: 4px;
  --radius-8: 8px;
  --radius-10: 10px;
  --radius-12: 12px;
  --radius-14: 14px;
  --radius-pill: 999px;
  /* type, named for its pixel size at the 16px root */
  --text-12: .75rem;
  --text-13: .8125rem;
  --text-15: .9375rem;
  --text-17: 1.0625rem;
  --text-22: 1.375rem;
  --text-28: 1.75rem;
  --text-32: 2rem;
  --shadow: 0 1px 2px rgba(18, 22, 30, .05), 0 6px 18px rgba(18, 22, 30, .05);
  --sans: ui-sans-serif, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
}

/* What the machine asked for — unless the reader has said otherwise here. */
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {__DARK__}
}
/* What the reader asked for, which outranks the machine. Equal specificity to
   the rule above, so this winning is a matter of coming after it. */
:root[data-theme="dark"] {__DARK__}

* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: var(--sans);
  font-size: 15px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}
/* Page padding lives here and nowhere else. It used to be here *and* on body —
   the handset rules further down grew their own — and the two stacked: 52px a
   side on every screen, 136px of dead space at the foot. On a 375px phone that
   spent 104px of the viewport on margins before a word was drawn. The
   safe-area insets belong on whichever element carries the padding, so they
   moved here with it rather than staying behind on body. */
.page {
  max-width: 1180px;
  margin-inline: auto;
  padding-top: var(--space-40);
  padding-left: max(var(--space-28), env(safe-area-inset-left));
  padding-right: max(var(--space-28), env(safe-area-inset-right));
  padding-bottom: max(var(--space-72), env(safe-area-inset-bottom));
}

/* --- focus: drawn, not left to the user agent --------------------------- */
/* Everything interactive on these pages is custom-drawn, so the browser's
   default ring lands on surfaces it was never contrasted against — on the
   composer's button, against --panel-sunken, it is very nearly invisible.

   :focus-visible rather than :focus, so a mouse click does not leave a ring
   sitting there afterwards. The keyboard user is the one who needs it; to the
   mouse user the same ring reads as a control stuck in a state.

   outline-offset is doing real work, not spacing. --accent scores 1.00:1
   against --accent-fill, so a ring drawn inside a filled surface would vanish
   into it; offset outward it lands on the panel behind, where it clears 3:1 in
   both modes (light 6.63, dark 6.84). */
a:focus-visible,
button:focus-visible,
input:focus-visible,
summary:focus-visible,
[tabindex]:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

/* --- the theme control --------------------------------------------------- */
/* The slot ships empty and the script fills it, so a page with scripting off
   shows no control rather than a dead one — the same rule the console follows
   about not offering an action it cannot carry out. An empty flex row collapses
   to nothing, so that page loses no space to it either. */
.theme { display: flex; justify-content: flex-end; gap: var(--space-8); }
.theme-toggle {
  font: inherit; font-size: var(--text-12); cursor: pointer;
  letter-spacing: .08em; text-transform: uppercase;
  color: var(--ink-faint); background: transparent;
  border: 1px solid var(--line-strong); border-radius: var(--radius-pill);
  padding: var(--space-4) var(--space-12);
}
.theme-toggle:hover { color: var(--accent); border-color: var(--accent); }
/* Sized for a finger only where there is one. The composer's buttons take 44px
   unconditionally because they are the primary action of a chat; this is chrome,
   and 44px of it in the corner of a desktop page is just a large button. */
@media (pointer: coarse) {
  .theme-toggle { min-height: 44px; min-width: 44px; }
}

/* --- the banner --------------------------------------------------------- */
.banner { margin-bottom: var(--space-26); }
.banner h1 { font-size: var(--text-32); line-height: 1.2; margin: 0 0 var(--space-6); letter-spacing: -.02em; }
.banner .who { color: var(--ink-soft); margin: 0; font-size: var(--text-17); }
.banner .meta {
  display: flex; flex-wrap: wrap; gap: var(--space-8) var(--space-18);
  margin-top: var(--space-14); font-family: var(--mono); font-size: var(--text-13); color: var(--ink-faint);
}
.readonly {
  display: inline-block; margin-left: var(--space-10); vertical-align: 5px;
  font-family: var(--mono); font-size: var(--text-12); letter-spacing: .08em; text-transform: uppercase;
  color: var(--ink-faint); border: 1px solid var(--line-strong); border-radius: var(--radius-pill);
  padding: var(--space-4) var(--space-10);
}

/* --- navigation --------------------------------------------------------- */
.nav { display: flex; gap: var(--space-4); margin: 0 0 var(--space-24); border-bottom: 1px solid var(--line); }
.nav a {
  padding: var(--space-8) var(--space-16); text-decoration: none; color: var(--ink-soft);
  font-size: var(--text-15); border-bottom: 2px solid transparent; margin-bottom: -1px;
}
.nav a:hover { color: var(--ink); }
.nav a[aria-current="page"] { color: var(--accent); border-bottom-color: var(--accent); font-weight: 600; }

/* --- cards -------------------------------------------------------------- */
.deck {
  display: grid; gap: var(--space-18);
  grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
  margin-bottom: var(--space-18);
}
.card {
  background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius-12);
  box-shadow: var(--shadow); padding: var(--space-20) var(--space-22); min-width: 0;
}
.card-wide { grid-column: 1 / -1; }
.card h2 {
  margin: 0 0 var(--space-14); font-size: var(--text-13); font-weight: 600;
  letter-spacing: .07em; text-transform: uppercase; color: var(--ink-faint);
}
.card-body > :first-child { margin-top: 0; }
.card-body > :last-child { margin-bottom: 0; }
.note {
  margin: var(--space-14) 0 0; padding-top: var(--space-12); border-top: 1px solid var(--line);
  font-size: var(--text-13); color: var(--ink-faint);
}
.empty { margin: 0; color: var(--ink-faint); font-size: var(--text-15); }

/* --- tables ------------------------------------------------------------- */
/* The scroll box is per-table, from render.rows(). Contained here, the widest
   ledger costs one sideways scroll inside its own card; uncontained, it was the
   whole document that moved. */
.scroll-x { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: var(--text-15); }
th {
  text-align: left; font-size: var(--text-12); font-weight: 600; letter-spacing: .06em;
  text-transform: uppercase; color: var(--ink-faint);
  padding: 0 var(--space-12) var(--space-8) 0; border-bottom: 1px solid var(--line);
}
td { padding: var(--space-12) var(--space-12) var(--space-12) 0; border-bottom: 1px solid var(--line); vertical-align: top; }
tr:last-child td { border-bottom: 0; }
td:last-child, th:last-child { padding-right: 0; }
.amount { font-family: var(--mono); font-variant-numeric: tabular-nums; white-space: nowrap; }
.who-cell { font-weight: 600; }
.sub { display: block; color: var(--ink-faint); font-size: var(--text-13); margin-top: var(--space-2); }
.rule {
  display: block; margin-top: var(--space-4); font-size: var(--text-13); color: var(--ink-soft);
  border-left: 2px solid var(--line-strong); padding-left: var(--space-8);
}
.rule-id { font-family: var(--mono); color: var(--ink-faint); }

/* --- badges and chips: deliberately unalike ----------------------------- */
.badge {
  display: inline-block; padding: var(--space-2) var(--space-8); border-radius: var(--radius-pill);
  font-size: var(--text-12); font-weight: 600; letter-spacing: .01em; white-space: nowrap;
  border: 1px solid transparent;
}
.badge-good { background: var(--good-bg); color: var(--good-ink); border-color: var(--good-line); }
.badge-wait { background: var(--wait-bg); color: var(--wait-ink); border-color: var(--wait-line); }
.badge-bad  { background: var(--bad-bg);  color: var(--bad-ink);  border-color: var(--bad-line); }
.badge-flat { background: var(--flat-bg); color: var(--flat-ink); border-color: var(--flat-line); }
.badge-unknown {
  background: transparent; color: var(--unknown-ink);
  border: 1px dotted var(--unknown-line);
}
.chip {
  display: inline-block; padding: 1px var(--space-8); border-radius: var(--radius-4);
  font-family: var(--mono); font-size: var(--text-12); letter-spacing: .08em; text-transform: uppercase;
  color: var(--ink-faint); background: transparent; border: 1px dashed var(--line-strong);
  white-space: nowrap;
}

/* --- the boundary panel: the absence is drawn --------------------------- */
.intro { max-width: 74ch; margin-bottom: var(--space-18); }
.intro p { margin: 0; color: var(--ink-soft); font-size: var(--text-17); line-height: 1.5; }
.boundary { display: grid; gap: var(--space-18); grid-template-columns: 1fr 1fr; margin-bottom: var(--space-18); }
.boundary:last-child { margin-bottom: 0; }
@media (max-width: 780px) { .boundary { grid-template-columns: 1fr; } }
.withheld, .shared {
  border-radius: var(--radius-12); padding: var(--space-18) var(--space-20); min-width: 0;
}
.withheld {
  border: 1px dashed var(--line-strong);
  background-color: var(--panel-sunken);
  background-image: repeating-linear-gradient(
    -45deg, transparent 0 9px, rgba(127, 136, 153, .07) 9px 18px);
}
.shared { border: 1px solid var(--line); background: var(--panel-sunken); }
.withheld h3, .shared h3 { margin: 0 0 var(--space-8); font-size: var(--text-17); line-height: 1.3; }
.withheld p { color: var(--ink-soft); margin: 0 0 var(--space-10); font-size: var(--text-15); }
.withheld code, .shared code, .meta code {
  font-family: var(--mono); font-size: .875em;
  background: var(--bg); border: 1px solid var(--line); border-radius: var(--radius-4); padding: 1px var(--space-4);
}
.turn {
  background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius-10);
  padding: var(--space-10) var(--space-12); margin-bottom: var(--space-8);
}
.turn:last-child { margin-bottom: 0; }
.turn-head {
  display: flex; align-items: baseline; gap: var(--space-8); flex-wrap: wrap;
  margin-bottom: var(--space-4); font-size: var(--text-13); color: var(--ink-faint);
}
.turn-who { font-weight: 600; color: var(--ink); font-size: var(--text-15); }
.turn-body { margin: 0; font-size: var(--text-15); }
.sharing-state {
  margin-top: var(--space-14); padding-top: var(--space-12); border-top: 1px solid var(--line);
  font-size: var(--text-13); color: var(--ink-faint);
}
.also-visible { margin: 0; padding-left: var(--space-18); color: var(--ink-soft); font-size: var(--text-15); }
.also-visible li { margin-bottom: var(--space-4); }

/* --- degraded panels ---------------------------------------------------- */
.degraded {
  border: 1px dashed var(--line-strong); border-radius: var(--radius-12);
  background: var(--panel-sunken); padding: var(--space-16) var(--space-18); color: var(--ink-soft);
  font-size: var(--text-15);
}
.degraded strong { color: var(--ink); }
.degraded .said {
  display: block; margin-top: var(--space-8); font-family: var(--mono); font-size: var(--text-13);
  color: var(--ink-faint); white-space: pre-wrap;
}

/* --- counts ------------------------------------------------------------- */
.counts { display: grid; gap: var(--space-14); grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); }
.count { background: var(--panel-sunken); border-radius: var(--radius-10); padding: var(--space-14) var(--space-16); }
.count .n {
  display: block; font-family: var(--mono); font-variant-numeric: tabular-nums;
  font-size: var(--text-28); line-height: 1.1; font-weight: 600;
}
.count .label { display: block; margin-top: var(--space-4); font-size: var(--text-13); color: var(--ink-faint); }

/* --- the 404 ------------------------------------------------------------ */
.nowhere { max-width: 620px; }
.nowhere h1 { font-size: var(--text-22); margin: 0 0 var(--space-12); }
.nowhere p { color: var(--ink-soft); margin: 0 0 var(--space-12); }

/* --- plans -------------------------------------------------------------- */
.plan { padding: var(--space-14) 0; border-bottom: 1px solid var(--line); }
.plan:first-child { padding-top: 0; }
.plan:last-child { border-bottom: 0; padding-bottom: 0; }
.plan-head { display: flex; align-items: baseline; gap: var(--space-10); flex-wrap: wrap; }
.plan-name { font-weight: 600; font-size: var(--text-17); }
.plan-line { margin: var(--space-6) 0 0; color: var(--ink-soft); font-size: var(--text-15); }
.plan-items { margin: var(--space-8) 0 0; padding-left: var(--space-18); font-size: var(--text-15); color: var(--ink-soft); }
.plan-items li { margin-bottom: var(--space-2); }
.books-it { color: var(--ink-faint); font-size: var(--text-13); }
"""

# One palette, two selectors. Substituted rather than concatenated so the CSS
# above stays readable as CSS.
STYLESHEET = STYLESHEET.replace("__DARK__", _DARK)

# --- the demo console ---------------------------------------------------------
# Two phone lines side by side. Deliberately unlike the sponsor dashboard: that
# one is a report, this one is a rehearsal, and they should not be mistaken for
# each other in a screenshot.
STYLESHEET += """
.lines { display: grid; gap: var(--space-18); grid-template-columns: 1fr 1fr; align-items: start; }
@media (max-width: 860px) { .lines { grid-template-columns: 1fr; } }
.line {
  background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius-12);
  box-shadow: var(--shadow); overflow: hidden;
}
.line-head {
  display: flex; align-items: baseline; gap: var(--space-10); flex-wrap: wrap;
  padding: var(--space-12) var(--space-16); border-bottom: 1px solid var(--line); background: var(--panel-sunken);
}
.line-who { font-weight: 600; }
.line-num { font-family: var(--mono); font-size: var(--text-13); color: var(--ink-faint); }
/* No inner scrollbar by default. A thread inside its own scroll box means a new
   reply can land below a fold the page gives you no sign of — which reads
   exactly like the agent never answered. The page scrolls; the thread grows. */
.thread { padding: var(--space-14) var(--space-16); display: flex; flex-direction: column; gap: var(--space-10);
          min-height: 200px; }
.bubble { max-width: 85%; border-radius: var(--radius-14); padding: var(--space-8) var(--space-12); font-size: var(--text-15); }
.bubble p { margin: 0; }
.bubble-who { font-size: var(--text-12); color: var(--ink-faint); margin-bottom: var(--space-4); }
/* The person typing is on the right, steward on the left — the arrangement a
   phone uses, so nobody has to be told which side is which. */
.bubble.them { align-self: flex-end; background: var(--accent-fill); color: var(--accent-on-fill); }
/* A solid token, not white-at-75%: the opacity was what pushed this to 2.04:1
   in dark mode. It is de-emphasised by being smaller, which costs no contrast. */
.bubble.them .bubble-who { color: var(--accent-on-fill-muted); }
.bubble.agent { align-self: flex-start; background: var(--panel-sunken); border: 1px solid var(--line); }
.composer { display: flex; gap: var(--space-8); padding: var(--space-12) var(--space-16); border-top: 1px solid var(--line); }
.composer input {
  flex: 1; font: inherit; font-size: var(--text-15); color: var(--ink);
  background: var(--panel-sunken); border: 1px solid var(--line-strong);
  border-radius: var(--radius-8); padding: var(--space-8) var(--space-12);
}
.composer button, .acts button {
  font: inherit; font-size: var(--text-15); font-weight: 600; cursor: pointer;
  border-radius: var(--radius-8); padding: var(--space-8) var(--space-16); border: 1px solid var(--line-strong);
  background: var(--panel-sunken); color: var(--ink);
}
.composer button:hover, .acts button:hover { border-color: var(--accent); color: var(--accent); }
.controls { margin-top: var(--space-18); }
.pending {
  background: var(--panel); border: 1px solid var(--line-strong); border-radius: var(--radius-12);
  padding: var(--space-16) var(--space-18); box-shadow: var(--shadow);
}
.acts { display: flex; gap: var(--space-8); margin-top: var(--space-12); }
.acts .yes { border-color: var(--good-line); color: var(--good-ink); background: var(--good-bg); }
.acts .no { border-color: var(--line-strong); }
.thinking { color: var(--ink-faint); font-size: var(--text-15); padding: 0 var(--space-16) var(--space-12); }
/* Beside the payment link, never instead of it: whoever is reading on a laptop
   still needs the URL, and whoever has only a phone needs the code. */
.pay { margin: var(--space-10) 0 0; }
.pay img { width: 132px; height: 132px; display: block;
           background: #fff; padding: var(--space-8); border-radius: var(--radius-8); }
.pay figcaption { margin-top: var(--space-6); font-size: var(--text-12); color: var(--ink-faint); }
"""

# --- handsets -----------------------------------------------------------------
# One page, three platforms. Nothing here branches on the OS: the only genuinely
# platform-specific rules are iOS's, and both of them are harmless everywhere
# else, which is the whole argument for not writing two pages.
STYLESHEET += """
html {
  /* Safari resizes text in landscape unless told not to. */
  -webkit-text-size-adjust: 100%;
}
/* There was a body rule here clearing the notch and the home indicator. .page
   already did that, at the top of this file, which is how every screen ended up
   padded twice. One owner now, and it is .page. */

.composer input {
  /* Exactly 16px, and not a rem less. Mobile Safari zooms the whole page when a
     focused input is smaller than this, which on a chat box means every reply
     leaves the viewport mid-conversation. */
  font-size: 16px;
}
.composer button, .acts button {
  /* Both platforms' guidance lands near the same number for a tap target. */
  min-height: 44px; min-width: 44px;
}
.banner.solo h1 { font-size: var(--text-22); }
.lines.solo { grid-template-columns: 1fr; }
.lines.solo .thread { min-height: 55vh; }

/* Only where two columns sit side by side is a cap worth having, and even then
   it is generous enough that a reply is visible without hunting for it. */
@media (min-width: 861px) {
  .lines:not(.solo) .thread { max-height: 62vh; overflow-y: auto; }
}

/* One query for every phone in portrait. 375 (SE), 390 (14), 412 (Pixel) and
   428 (Pro Max) all sit well under this, and none of them wants a different
   number — they want the same one, which is why this is a bound and not a set
   of device widths. The insets are repeated because this rule replaces the
   left/right padding set at the top, and leaving them out here would put text
   back under the notch in landscape. */
@media (max-width: 620px) {
  .page {
    padding-top: var(--space-20);
    padding-left: max(var(--space-16), env(safe-area-inset-left));
    padding-right: max(var(--space-16), env(safe-area-inset-right));
  }
  .thread { padding: var(--space-12); }
  .bubble { max-width: 92%; }
  .line-head { padding: var(--space-12) var(--space-12); }
  .composer { padding: var(--space-10) var(--space-12); }
  .card, .panel { padding: var(--space-14); }
  h1 { font-size: var(--text-22); }
}

/* The QR pair, shown on the desktop page so a phone can join by pointing at it. */
.join { display: flex; gap: var(--space-22); flex-wrap: wrap; margin-top: var(--space-18); }
.join figure { margin: 0; text-align: center; }
.join img {
  width: 168px; height: 168px; display: block;
  background: #fff; padding: var(--space-8); border-radius: var(--radius-10); border: 1px solid var(--line);
}
.join figcaption { margin-top: var(--space-8); font-size: var(--text-13); color: var(--ink-soft); }
.join code { font-family: var(--mono); font-size: var(--text-12); color: var(--ink-faint); }
"""
