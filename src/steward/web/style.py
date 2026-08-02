"""The whole design system, as one string.

System fonts only. A dashboard for a project whose argument is that nothing
leaves the device must not open a connection to a font CDN in order to draw
itself — the first request a reviewer sees in the network tab would undo the
paragraph it is rendering.

Two visual vocabularies, and keeping them unlike each other is the point.
Statuses are filled pills carrying data. Annotations — FIXTURE, dry-run,
advisory only — are outlined and monospace, so they can never be mistaken for a
status at a glance.
"""

from __future__ import annotations

STYLESHEET = """
:root {
  color-scheme: light dark;
  --bg: #f6f7f9;
  --panel: #ffffff;
  --panel-sunken: #f1f3f6;
  --ink: #14171c;
  --ink-soft: #4a5261;
  --ink-faint: #767f8f;
  --line: #dfe3ea;
  --line-strong: #c6ccd8;
  --accent: #1f4fd8;
  --good-bg: #e3f5ea; --good-ink: #146b3c; --good-line: #a9dcc0;
  --wait-bg: #fdf0d9; --wait-ink: #8a5605; --wait-line: #eecd95;
  --bad-bg: #fce8e8;  --bad-ink: #9c2222;  --bad-line: #edb9b9;
  --flat-bg: #eceff4; --flat-ink: #4a5261; --flat-line: #d3d9e2;
  --unknown-ink: #767f8f; --unknown-line: #b4bcc8;
  --radius: 12px;
  --shadow: 0 1px 2px rgba(18, 22, 30, .05), 0 6px 18px rgba(18, 22, 30, .05);
  --sans: ui-sans-serif, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #101318;
    --panel: #171b22;
    --panel-sunken: #1d222b;
    --ink: #e9ecf1;
    --ink-soft: #aab3c1;
    --ink-faint: #7f8899;
    --line: #262c37;
    --line-strong: #333b49;
    --accent: #7ba0ff;
    --good-bg: #12301f; --good-ink: #7ddba4; --good-line: #22563a;
    --wait-bg: #33260c; --wait-ink: #f0c274; --wait-line: #5c451a;
    --bad-bg: #35171a;  --bad-ink: #f0a0a0;  --bad-line: #5d2a2d;
    --flat-bg: #1f242e; --flat-ink: #aab3c1; --flat-line: #333b49;
    --unknown-ink: #7f8899; --unknown-line: #3d4552;
    --shadow: 0 1px 2px rgba(0, 0, 0, .3), 0 6px 18px rgba(0, 0, 0, .22);
  }
}

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
.page { max-width: 1180px; margin-inline: auto; padding: 40px 28px 72px; }

/* --- the banner --------------------------------------------------------- */
.banner { margin-bottom: 26px; }
.banner h1 { font-size: 2rem; line-height: 1.2; margin: 0 0 6px; letter-spacing: -.02em; }
.banner .who { color: var(--ink-soft); margin: 0; font-size: 1.0625rem; }
.banner .meta {
  display: flex; flex-wrap: wrap; gap: 8px 18px;
  margin-top: 14px; font-family: var(--mono); font-size: .8125rem; color: var(--ink-faint);
}
.readonly {
  display: inline-block; margin-left: 10px; vertical-align: 5px;
  font-family: var(--mono); font-size: .75rem; letter-spacing: .08em; text-transform: uppercase;
  color: var(--ink-faint); border: 1px solid var(--line-strong); border-radius: 999px;
  padding: 3px 10px;
}

/* --- navigation --------------------------------------------------------- */
.nav { display: flex; gap: 4px; margin: 0 0 24px; border-bottom: 1px solid var(--line); }
.nav a {
  padding: 9px 15px; text-decoration: none; color: var(--ink-soft);
  font-size: .9375rem; border-bottom: 2px solid transparent; margin-bottom: -1px;
}
.nav a:hover { color: var(--ink); }
.nav a[aria-current="page"] { color: var(--accent); border-bottom-color: var(--accent); font-weight: 600; }

/* --- cards -------------------------------------------------------------- */
.deck {
  display: grid; gap: 18px;
  grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
  margin-bottom: 18px;
}
.card {
  background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
  box-shadow: var(--shadow); padding: 20px 22px; min-width: 0;
}
.card-wide { grid-column: 1 / -1; }
.card h2 {
  margin: 0 0 14px; font-size: .8125rem; font-weight: 600;
  letter-spacing: .07em; text-transform: uppercase; color: var(--ink-faint);
}
.card-body > :first-child { margin-top: 0; }
.card-body > :last-child { margin-bottom: 0; }
.note {
  margin: 14px 0 0; padding-top: 12px; border-top: 1px solid var(--line);
  font-size: .8125rem; color: var(--ink-faint);
}
.empty { margin: 0; color: var(--ink-faint); font-size: .9375rem; }

/* --- tables ------------------------------------------------------------- */
table { width: 100%; border-collapse: collapse; font-size: .9375rem; }
th {
  text-align: left; font-size: .75rem; font-weight: 600; letter-spacing: .06em;
  text-transform: uppercase; color: var(--ink-faint);
  padding: 0 12px 8px 0; border-bottom: 1px solid var(--line);
}
td { padding: 11px 12px 11px 0; border-bottom: 1px solid var(--line); vertical-align: top; }
tr:last-child td { border-bottom: 0; }
td:last-child, th:last-child { padding-right: 0; }
.amount { font-family: var(--mono); font-variant-numeric: tabular-nums; white-space: nowrap; }
.who-cell { font-weight: 600; }
.sub { display: block; color: var(--ink-faint); font-size: .8125rem; margin-top: 2px; }
.rule {
  display: block; margin-top: 4px; font-size: .8125rem; color: var(--ink-soft);
  border-left: 2px solid var(--line-strong); padding-left: 9px;
}
.rule-id { font-family: var(--mono); color: var(--ink-faint); }

/* --- badges and chips: deliberately unalike ----------------------------- */
.badge {
  display: inline-block; padding: 2px 9px; border-radius: 999px;
  font-size: .75rem; font-weight: 600; letter-spacing: .01em; white-space: nowrap;
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
  display: inline-block; padding: 1px 8px; border-radius: 4px;
  font-family: var(--mono); font-size: .6875rem; letter-spacing: .08em; text-transform: uppercase;
  color: var(--ink-faint); background: transparent; border: 1px dashed var(--line-strong);
  white-space: nowrap;
}

/* --- the boundary panel: the absence is drawn --------------------------- */
.intro { max-width: 74ch; margin-bottom: 18px; }
.intro p { margin: 0; color: var(--ink-soft); font-size: 1.0625rem; line-height: 1.5; }
.boundary { display: grid; gap: 18px; grid-template-columns: 1fr 1fr; margin-bottom: 18px; }
.boundary:last-child { margin-bottom: 0; }
@media (max-width: 780px) { .boundary { grid-template-columns: 1fr; } }
.withheld, .shared {
  border-radius: var(--radius); padding: 18px 20px; min-width: 0;
}
.withheld {
  border: 1px dashed var(--line-strong);
  background-color: var(--panel-sunken);
  background-image: repeating-linear-gradient(
    -45deg, transparent 0 9px, rgba(127, 136, 153, .07) 9px 18px);
}
.shared { border: 1px solid var(--line); background: var(--panel-sunken); }
.withheld h3, .shared h3 { margin: 0 0 8px; font-size: 1.0625rem; line-height: 1.3; }
.withheld p { color: var(--ink-soft); margin: 0 0 10px; font-size: .9375rem; }
.withheld code, .shared code, .meta code {
  font-family: var(--mono); font-size: .875em;
  background: var(--bg); border: 1px solid var(--line); border-radius: 4px; padding: 1px 5px;
}
.turn {
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: 10px 13px; margin-bottom: 9px;
}
.turn:last-child { margin-bottom: 0; }
.turn-head {
  display: flex; align-items: baseline; gap: 9px; flex-wrap: wrap;
  margin-bottom: 4px; font-size: .8125rem; color: var(--ink-faint);
}
.turn-who { font-weight: 600; color: var(--ink); font-size: .875rem; }
.turn-body { margin: 0; font-size: .9375rem; }
.sharing-state {
  margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--line);
  font-size: .8125rem; color: var(--ink-faint);
}
.also-visible { margin: 0; padding-left: 18px; color: var(--ink-soft); font-size: .9375rem; }
.also-visible li { margin-bottom: 3px; }

/* --- degraded panels ---------------------------------------------------- */
.degraded {
  border: 1px dashed var(--line-strong); border-radius: var(--radius);
  background: var(--panel-sunken); padding: 16px 18px; color: var(--ink-soft);
  font-size: .9375rem;
}
.degraded strong { color: var(--ink); }
.degraded .said {
  display: block; margin-top: 8px; font-family: var(--mono); font-size: .8125rem;
  color: var(--ink-faint); white-space: pre-wrap;
}

/* --- counts ------------------------------------------------------------- */
.counts { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); }
.count { background: var(--panel-sunken); border-radius: 10px; padding: 14px 16px; }
.count .n {
  display: block; font-family: var(--mono); font-variant-numeric: tabular-nums;
  font-size: 1.75rem; line-height: 1.1; font-weight: 600;
}
.count .label { display: block; margin-top: 4px; font-size: .8125rem; color: var(--ink-faint); }

/* --- the 404 ------------------------------------------------------------ */
.nowhere { max-width: 620px; }
.nowhere h1 { font-size: 1.375rem; margin: 0 0 12px; }
.nowhere p { color: var(--ink-soft); margin: 0 0 12px; }

/* --- plans -------------------------------------------------------------- */
.plan { padding: 14px 0; border-bottom: 1px solid var(--line); }
.plan:first-child { padding-top: 0; }
.plan:last-child { border-bottom: 0; padding-bottom: 0; }
.plan-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.plan-name { font-weight: 600; font-size: 1.0625rem; }
.plan-line { margin: 6px 0 0; color: var(--ink-soft); font-size: .9375rem; }
.plan-items { margin: 9px 0 0; padding-left: 18px; font-size: .875rem; color: var(--ink-soft); }
.plan-items li { margin-bottom: 2px; }
.books-it { color: var(--ink-faint); font-size: .8125rem; }
"""

# --- the demo console ---------------------------------------------------------
# Two phone lines side by side. Deliberately unlike the sponsor dashboard: that
# one is a report, this one is a rehearsal, and they should not be mistaken for
# each other in a screenshot.
STYLESHEET += """
.lines { display: grid; gap: 18px; grid-template-columns: 1fr 1fr; align-items: start; }
@media (max-width: 860px) { .lines { grid-template-columns: 1fr; } }
.line {
  background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
  box-shadow: var(--shadow); overflow: hidden;
}
.line-head {
  display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;
  padding: 13px 16px; border-bottom: 1px solid var(--line); background: var(--panel-sunken);
}
.line-who { font-weight: 600; }
.line-num { font-family: var(--mono); font-size: .8125rem; color: var(--ink-faint); }
/* No inner scrollbar by default. A thread inside its own scroll box means a new
   reply can land below a fold the page gives you no sign of — which reads
   exactly like the agent never answered. The page scrolls; the thread grows. */
.thread { padding: 14px 16px; display: flex; flex-direction: column; gap: 10px;
          min-height: 200px; }
.bubble { max-width: 85%; border-radius: 14px; padding: 9px 13px; font-size: .9375rem; }
.bubble p { margin: 0; }
.bubble-who { font-size: .75rem; color: var(--ink-faint); margin-bottom: 3px; }
/* The person typing is on the right, steward on the left — the arrangement a
   phone uses, so nobody has to be told which side is which. */
.bubble.them { align-self: flex-end; background: var(--accent); color: #fff; }
.bubble.them .bubble-who { color: rgba(255,255,255,.75); }
.bubble.agent { align-self: flex-start; background: var(--panel-sunken); border: 1px solid var(--line); }
.composer { display: flex; gap: 8px; padding: 12px 16px; border-top: 1px solid var(--line); }
.composer input {
  flex: 1; font: inherit; font-size: .9375rem; color: var(--ink);
  background: var(--panel-sunken); border: 1px solid var(--line-strong);
  border-radius: 9px; padding: 9px 12px;
}
.composer button, .acts button {
  font: inherit; font-size: .875rem; font-weight: 600; cursor: pointer;
  border-radius: 9px; padding: 9px 15px; border: 1px solid var(--line-strong);
  background: var(--panel-sunken); color: var(--ink);
}
.composer button:hover, .acts button:hover { border-color: var(--accent); color: var(--accent); }
.controls { margin-top: 18px; }
.pending {
  background: var(--panel); border: 1px solid var(--line-strong); border-radius: var(--radius);
  padding: 16px 18px; box-shadow: var(--shadow);
}
.acts { display: flex; gap: 9px; margin-top: 12px; }
.acts .yes { border-color: var(--good-line); color: var(--good-ink); background: var(--good-bg); }
.acts .no { border-color: var(--line-strong); }
.thinking { color: var(--ink-faint); font-size: .875rem; padding: 0 16px 12px; }
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
body {
  /* Keeps content clear of the notch and the home indicator. Zero on anything
     that has neither, so Android and desktop are untouched. */
  padding-left: max(24px, env(safe-area-inset-left));
  padding-right: max(24px, env(safe-area-inset-right));
  padding-bottom: max(64px, env(safe-area-inset-bottom));
}
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
.banner.solo h1 { font-size: 1.375rem; }
.lines.solo { grid-template-columns: 1fr; }
.lines.solo .thread { min-height: 55vh; }

/* Only where two columns sit side by side is a cap worth having, and even then
   it is generous enough that a reply is visible without hunting for it. */
@media (min-width: 861px) {
  .lines:not(.solo) .thread { max-height: 62vh; overflow-y: auto; }
}

@media (max-width: 620px) {
  .page { padding-top: 20px; }
  .thread { padding: 12px; }
  .bubble { max-width: 92%; }
  .line-head { padding: 11px 12px; }
  .composer { padding: 10px 12px; }
  .card, .panel { padding: 14px; }
  h1 { font-size: 1.375rem; }
}

/* The QR pair, shown on the desktop page so a phone can join by pointing at it. */
.join { display: flex; gap: 22px; flex-wrap: wrap; margin-top: 18px; }
.join figure { margin: 0; text-align: center; }
.join img {
  width: 168px; height: 168px; display: block;
  background: #fff; padding: 9px; border-radius: 10px; border: 1px solid var(--line);
}
.join figcaption { margin-top: 8px; font-size: .8125rem; color: var(--ink-soft); }
.join code { font-family: var(--mono); font-size: .75rem; color: var(--ink-faint); }
"""
