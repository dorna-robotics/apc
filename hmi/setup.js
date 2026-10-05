// hmi/setup.js — apc run setup: mark which IN stacks are loaded, then the
// final checks. Two steps, like bna's setup — Bench, then Final checks.
//
// Hosted in a shadow root inside the Parameters modal. The platform owns
// the modal chrome — width, Upload / Open, Set / Start — untouched here so
// every project's modal reads the same; it validates whatever value()
// returns against hmi/default.j2; validate() only ADDS a message.
// Contract: {css, mount(root, api), value(), validate()} — HMI_GUIDE §5.
//
// Built on /orchestrator/hmi-kit/kit.js like tph: the kit carries the
// shared language (cards, buttons, messages, wells, the frozen state);
// only the bench drawing and the stepper are project CSS. hmi/replan.js
// imports the bench from here (benchHtml / benchState / discPlace), so the
// Replan choice is drawn on exactly these holders.
//
// Step 1 — Bench. The five disc holders on fixture plate 4, column 5, in
// the order the operator sees them with the IN holders nearest
// (scene/layout.j2):
//
//   Fail     stack_holder_disc_out_bad_1    B5   placeholder — the robot fills it
//   Pass 2   stack_holder_disc_out_good_2   D5   placeholder — the robot fills it
//   Pass 1   stack_holder_disc_out_good_1   F5   placeholder — the robot fills it
//   In 2     stack_holder_disc_in_2         H5   click: full / empty
//   In 1     stack_holder_disc_in_1         J5   click: full / empty
//
// Each holder is ONE row of seven stack positions, A1..A7, 26 mm apart
// along the plate's X (components/stack_holder/stack_holder_disc_in.py).
// Drawn with A1 on the LEFT, which is how this bench faces its operator.
// This is NOT the turned 4x7 of bna: a single-letter rack has nothing to
// turn, so the row is drawn as the row. The pendant draws the same
// orientation (HMI_GUIDE §4) — one person reads both for the same holder.
//
// A position is either FULL or EMPTY — the operator loads whole stacks and
// never types or sees a count. The kwargs are the in_1 / in_2 lists of
// seven that setup() in actions.py reads: FULL (= MAX_PER_SLOT, the disc
// count of a full stack) for a full position, 0 for an empty one.
//
// Step 2 — Final checks. The things the robot cannot verify and a run
// breaks on: the OUT holders empty (the drop counter starts at zero), the
// anode clear and the cathode up (the first PlaceAnode lands there), the
// meter on and in RMT mode (Measure pauses on anything else). Bench
// confirmations — ticked every time, never saved with the parameters.

import { kitCss, wellCss, esc } from "/orchestrator/hmi-kit/kit.js";

// ── bench geometry — must match actions.py ─────────────────────────────
export const SLOTS_N = 7;                                   // SLOTS: A1..A7
export const SLOTS   = Array.from({ length: SLOTS_N }, (_, i) => `A${i + 1}`);
export const IN_KEYS = ["in_1", "in_2"];                    // the kwargs, consumed in this order
// What a FULL position is written as: MAX_PER_SLOT in actions.py, the
// discs in a full stack. Written to the run record, never shown here.
export const FULL    = 255;

// The holders, TOP TO BOTTOM as drawn. `in` rows take clicks; the rest
// are placeholders the robot fills during the run.
export const HOLDERS = [
  { key: "bad_1",  label: "Fail",   in: false },
  { key: "good_2", label: "Pass 2", in: false },
  { key: "good_1", label: "Pass 1", in: false },
  { key: "in_2",   label: "In 2",   in: true  },
  { key: "in_1",   label: "In 1",   in: true  },
];
const IN_HOLDERS  = HOLDERS.filter(h => h.in);
const OUT_HOLDERS = HOLDERS.filter(h => !h.in);
const OUT_LABELS  = OUT_HOLDERS.slice().reverse().map(h => h.label);              // ["Pass 1", "Pass 2", "Fail"]
const OUT_NAMES   = OUT_LABELS.slice(0, -1).join(", ") + " and " + OUT_LABELS[OUT_LABELS.length - 1];   // "Pass 1, Pass 2 and Fail"
const N_IN_STACKS = IN_HOLDERS.length * SLOTS_N;            // 14 clickable positions

// ── drawn at the 40 mL size ────────────────────────────────────────────
// A disc stack gets the cell bna gives a 40 mL vial: a circle big enough
// for its address. Row labels sit in a gutter on the left.
const CELL = 56, GAP = 6, GUTTER = 52;

// ── styles ────────────────────────────────────────────────────────────
// kitCss carries the shared language; wellCss declares the IDENTITY
// colors — a loaded stack, a passed disc, a failed disc. Fixed hues that
// do not invert with the theme, so the pendant (which copies them) and
// this screen read the same. Only the empty position is themed.
export const CSS = kitCss + wellCss({
  full: "#b9c6d2",     // a loaded IN stack — bare metal
  good: "#a4d89c",     // a passed disc (pendant)
  bad:  "#e8a79c",     // a failed disc (pendant)
}) + `
/* the bench is a picture: centred under its title, which stays left like
   every field label of the generic form */
.hmi.apc .card .inner { align-items:center; }
/* room for a hovered circle (the kit scales it 1.06) to grow WITHOUT
   spilling out of the scroll wrapper — a spill adds a scrollbar and the
   whole screen jumps under the cursor */
.hmi.apc .scroll { padding:6px; }
/* the bench: five holder rows in one grid, A1 on the left */
.hmi .rack.disc { grid-template-columns:${GUTTER}px repeat(${SLOTS_N}, ${CELL}px);
  gap:${GAP}px; justify-content:center; }
.hmi .rack.disc .well { width:${CELL}px; height:${CELL}px; flex-direction:column;
  line-height:1.1; font-size:11px; }
.hmi .rack.disc .well b { font-size:12px; font-weight:700; }
.hmi .rack.disc .well i { font-style:normal; font-size:8px; opacity:.7; margin-top:1px; }
/* an EMPTY IN position: hollow and dashed, still a toggle */
.hmi .rack.disc .well.empty { border-style:dashed; opacity:.55; }
/* an OUT position on this page: a placeholder, nothing to click */
.hmi .rack.disc .well.out { border-style:dashed; opacity:.4; }
/* a position with discs ON OFFER in the Replan view: a count badge */
.hmi .rack.disc .well .n { position:absolute; top:-6px; right:-6px; min-width:20px; height:20px;
  padding:0 6px; border-radius:999px; display:inline-flex; align-items:center; justify-content:center;
  font-size:11px; font-weight:700; background:var(--accent); color:#fff; }
/* row labels */
.hmi .rack.disc .rlab { justify-self:start; font-size:11px; font-weight:700; }
/* the OUT / IN groups are two different things: a hairline between them
   (width:100% — the kit centres grid items, which would shrink it to 0) */
.hmi .rack.disc .sep { grid-column:1 / -1; width:100%; height:1px;
  background:var(--border); margin:3px 0; }
/* the legend reads inline, like bna's: one line of swatches under the bench */
.hmi.apc .legend { flex-direction:row; flex-wrap:wrap; gap:var(--space-4);
  font-size:var(--text-xs); opacity:.85; color:inherit; }
.hmi.apc .legend i { width:12px; height:12px; }
/* a blocking message: bna's strip — the label, then the lines */
.hmi .msg .lines { display:flex; flex-direction:column; gap:2px; line-height:1.5; min-width:0; }
.hmi .legend i.full { background:var(--c-full); }
.hmi .legend i.empty { background:var(--surface); border-color:var(--border);
  border-style:dashed; }
.hmi .legend i.out { background:var(--surface); border-color:var(--border);
  border-style:dashed; opacity:.5; }

/* ── two steps in one screen ───────────────────────────────────────────
   The stepper is a pill of arrow segments — where you are and what comes
   next, in two words — and the step's own Back / Next sit at the far
   right of the same row, well away from the modal's Set & Start.
   Current = solid accent; complete = tinted accent with a check;
   upcoming = neutral. A step not done yet is not an error, so nothing
   here is red. The same stepper as bna's setup. */
.hmi .stepbar { display:flex; gap:var(--space-3); align-items:center; flex-wrap:wrap;
  padding-bottom:var(--space-3); margin-bottom:var(--space-4);
  border-bottom:1px solid var(--border); }
.hmi .stepbar .grow { flex:1 1 auto; }
.hmi .stepper { display:flex; align-items:stretch; height:38px; border-radius:999px;
  overflow:hidden; background:var(--surface); flex:none; }
.hmi .stepper .seg { --tip:14px; position:relative; display:flex; align-items:center;
  gap:var(--space-2); padding:0 calc(var(--tip) + 14px) 0 calc(var(--tip) + 16px);
  margin-left:calc(-1 * var(--tip) + 3px); border-radius:0; height:100%;
  font-size:var(--text-md); font-weight:600; white-space:nowrap;
  background:var(--surface2); color:var(--muted);
  clip-path:polygon(0 0, calc(100% - var(--tip)) 0, 100% 50%,
                    calc(100% - var(--tip)) 100%, 0 100%, var(--tip) 50%);
  transition:background var(--motion-fast) var(--ease), color var(--motion-fast) var(--ease); }
.hmi .stepper .seg:first-child { margin-left:0; padding-left:20px;
  clip-path:polygon(0 0, calc(100% - var(--tip)) 0, 100% 50%,
                    calc(100% - var(--tip)) 100%, 0 100%); }
.hmi .stepper .seg:last-child { padding-right:22px;
  clip-path:polygon(0 0, 100% 0, 100% 100%, 0 100%, var(--tip) 50%); }
.hmi .stepper .seg:hover { color:var(--text); }
.hmi .stepper .seg svg { width:16px; height:16px; flex:none; }
.hmi .stepper .seg.done { background:color-mix(in srgb, var(--accent) 14%, var(--surface));
  color:var(--accent); }
.hmi .stepper .seg.cur { background:var(--accent); color:#fff; }
.hmi .stepnav { display:flex; gap:var(--space-2); }
.hmi .stepnav button { display:inline-flex; align-items:center; gap:6px; height:38px;
  padding:0 16px; border-radius:999px; font-weight:600; }
.hmi .stepnav button svg { width:16px; height:16px; }
.hmi .stepnav .next { background:var(--accent); color:#fff; padding:0 14px 0 20px; }
.hmi .stepnav .next:hover { background:var(--accent); filter:brightness(1.08); }
.hmi .stepnav .back { background:transparent; color:var(--text); padding:0 20px 0 14px; }
.hmi .stepnav .back:hover { background:var(--surface2); }
/* Moving between the steps changes nothing — the stepper stays live frozen. */
.hmi[data-frozen="1"] .stepper button, .hmi[data-frozen="1"] .stepnav button {
  pointer-events:auto; opacity:1; }

/* ── final checks — one row per check: the box, one sentence, nothing in
   red. An unticked check is caught by the browser's own required-field
   bubble, pinned to the box (validate() -> reportValidity()). */
.hmi .checks { display:flex; flex-direction:column; gap:var(--space-3); width:100%; }
.hmi .check { display:flex; align-items:center; gap:var(--space-3);
  padding:var(--space-4); border:1px solid var(--border); border-radius:var(--radius-md);
  font-size:var(--text-md); cursor:pointer;
  transition:border-color var(--motion-fast) var(--ease), background var(--motion-fast) var(--ease); }
.hmi .check:hover { border-color:var(--accent); }
.hmi .check input { width:20px; height:20px; margin:0; flex:none; cursor:pointer;
  accent-color:var(--accent); }
.hmi .check.on { border-color:color-mix(in srgb, var(--accent) 45%, var(--border));
  background:color-mix(in srgb, var(--accent) 5%, var(--surface)); }
.hmi .check .muted { color:var(--muted); }
.hmi[data-frozen="1"] .check { cursor:default; }
`;

// ── state ──────────────────────────────────────────────────────────────
// Seven flags per IN holder. Lenient on the way in, like _counts() in
// actions.py setup(): a list, a "1,1,1" string or a bare number all load;
// a short list fills the leading anchors; anything above zero is FULL.
function flags(raw) {
  let v = raw;
  if (typeof v === "string") {
    v = v.trim().replace(/^\[|\]$/g, "").replace(/\s/g, "").split(",").filter(Boolean);
  } else if (typeof v === "number") {
    v = [v];
  }
  if (!Array.isArray(v)) v = [];
  const out = v.slice(0, SLOTS_N).map(n => (parseFloat(n) > 0 ? 1 : 0));
  while (out.length < SLOTS_N) out.push(0);
  return out;
}

// `api.values` carries ONLY what was saved from a previous Set — the
// platform keeps the schema defaults in a separate baseValues the screen
// never sees, so a first-ever open arrives with values = {}. Always:
// saved value -> schema default -> fallback.
function initial(api, key, fallback) {
  const v = (api && api.values) || {};
  if (v[key] !== undefined) return v[key];
  const spec = ((api && api.schema) || {})[key];
  if (spec && typeof spec === "object" && !Array.isArray(spec) && spec.default !== undefined) return spec.default;
  if (spec !== undefined && !(spec && typeof spec === "object" && !Array.isArray(spec))) return spec;   // a BARE entry
  return fallback;
}

// The bench as the run's parameters describe it — the one state the
// setup screen edits and the Replan view reads (api.values = what Start
// sent). The checks are bench confirmations: never restored, never saved.
export function benchState(api) {
  const st = { in: {}, step: "bench", checks: {} };
  for (const k of IN_KEYS) st.in[k] = flags(initial(api, k, []));
  return st;
}

// Where a disc index lives — the same numbering as setup() in actions.py:
// holder 1 then 2, position A1..A7, a FULL position holding FULL discs,
// top of the stack first. {holder, slot, depth} or null for an index
// past the inventory.
export function discPlace(st, disc) {
  let i = Number(disc);
  for (const key of IN_KEYS) {                 // consumption order: in_1 then in_2 — NOT the drawing order
    const holder = Number(key.slice(-1));
    for (let s = 0; s < SLOTS_N; s++) {
      const n = st.in[key][s] ? FULL : 0;
      if (i < n) return { holder, key, slot: SLOTS[s], index: s, depth: n - 1 - i };
      i -= n;
    }
  }
  return null;
}

// ── validation — always live ───────────────────────────────────────────
export function check(st) {
  const errs = [];
  const full = [];
  for (const h of IN_HOLDERS) {
    st.in[h.key].forEach((f, i) => { if (f) full.push({ h, i }); });
  }
  if (!full.length) errs.push("No discs loaded — click the In stacks that are full.");
  return { errs, full };
}

// The final checks: key, sentence, what the robot would hit without it.
const CHECKS = [
  { key: "out",   text: `${OUT_NAMES} are empty.`,
    sub: "the drop counter starts at zero — a leftover disc would be stacked on" },
  { key: "anode", text: "The anode is clear.",
    sub: "the first disc is placed there as soon as it is inspected" },
  { key: "meter", text: "The BK 879B is on and in RMT mode.",
    sub: "Measure pauses the run on anything else" },
];
const checksOk = st => CHECKS.every(c => st.checks[c.key]);

// Only the things that need a human. A screen that congratulates the
// operator when nothing is wrong trains them to skim this strip, so
// there is no "Ready" — the bench drawing shows the run (bna's rule).
function msgHtml(V) {
  return V.errs.length
    ? `<div class="msg m-bad"><b>Blocking</b><div class="lines">` +
      V.errs.map(t => `<span>${esc(t)}</span>`).join("") + `</div></div>`
    : "";
}

// ── render ─────────────────────────────────────────────────────────────
// One grid for the whole bench. Axis across the top, A1 on the left; one
// row per holder in HOLDERS order; a hairline between the OUT and IN
// groups. Every IN position is a toggle carrying its holder key and
// index (data-h / data-i); OUT positions carry nothing.
//
// opts.counts — {in_1: [7 numbers], in_2: [...]}: the Replan view's
// discs on offer per position, drawn as a badge; with counts given the
// positions are not toggles (the view's clicks go to its disc chips).
export function benchHtml(st, opts = {}) {
  const counts = opts.counts || null;
  let h = `<div class="ax"></div>` + SLOTS.map(s => `<div class="ax">${s}</div>`).join("");
  let prevIn = null;
  for (const hd of HOLDERS) {
    if (prevIn !== null && prevIn !== hd.in) h += `<div class="sep"></div>`;
    prevIn = hd.in;
    h += `<div class="rlab">${esc(hd.label)}</div>`;
    for (let i = 0; i < SLOTS_N; i++) {
      const s = SLOTS[i];
      const badge = counts && counts[hd.key] && counts[hd.key][i]
        ? `<span class="n">${counts[hd.key][i]}</span>` : "";
      const tog = counts ? "" : " toggle";
      if (!hd.in) {
        h += `<div class="well out" title="${esc(`${hd.label} · ${s} · empty — the robot fills it during the run`)}"><b>${s}</b><i>empty</i></div>`;
      } else if (st.in[hd.key][i]) {
        h += `<div class="well k-full${tog}" data-h="${hd.key}" data-i="${i}" ` +
          `title="${esc(`${hd.label} · ${s} · full${counts ? "" : " · click to mark empty"}`)}"><b>${s}</b><i>full</i>${badge}</div>`;
      } else {
        h += `<div class="well empty${tog}" data-h="${hd.key}" data-i="${i}" ` +
          `title="${esc(`${hd.label} · ${s} · empty${counts ? "" : " · click to mark full"}`)}"><b>${s}</b><i>empty</i>${badge}</div>`;
      }
    }
  }
  return h;
}

function stepBar(st, V) {
  const icon = d => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"
    stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
  const TICK = icon('<polyline points="20 6 9 17 4 12"/>');
  const NEXT = icon('<polyline points="9 18 15 12 9 6"/>');
  const BACK = icon('<polyline points="15 18 9 12 15 6"/>');
  // A step is complete when nothing on it blocks Start: the bench has a
  // loaded stack; every final check is ticked.
  const STEPS = [
    { key: "bench", title: "Bench",        ok: !V.errs.length },
    { key: "check", title: "Final checks", ok: checksOk(st) },
  ];
  const k = STEPS.findIndex(x => x.key === st.step);
  const stepper = STEPS.map(x => {
    const cls = ["seg", x.key === st.step ? "cur" : "", x.ok ? "done" : ""].filter(Boolean).join(" ");
    return `<button type="button" class="${cls}" data-step="${x.key}"${x.key === st.step ? ' aria-current="step"' : ""}>` +
           `${x.ok ? TICK : ""}${x.title}</button>`;
  }).join("");
  const nav = [
    k > 0 ? `<button type="button" class="back" data-step="${STEPS[k - 1].key}">${BACK}Back</button>` : "",
    k < STEPS.length - 1 ? `<button type="button" class="next" data-step="${STEPS[k + 1].key}">Next${NEXT}</button>` : "",
  ].join("");
  return `
  <div class="stepbar">
    <nav class="stepper" aria-label="Run setup steps">${stepper}</nav>
    <span class="grow"></span>
    <div class="stepnav">${nav}</div>
  </div>`;
}

function checksHtml(st) {
  return CHECKS.map(c => `
  <label class="check${st.checks[c.key] ? " on" : ""}">
    <input type="checkbox" data-check="${c.key}" required${st.checks[c.key] ? " checked" : ""}>
    <span>${esc(c.text)} <span class="muted">— ${esc(c.sub)}</span></span>
  </label>`).join("");
}

function bodyHtml(st) {
  const V = check(st);
  if (st.step === "check") {
    return stepBar(st, V) + `
  <div class="stack">
    <div class="card">
      <h4>Final checks — before Start</h4>
      <div class="inner">
        <div class="checks">${checksHtml(st)}</div>
      </div>
    </div>
  </div>`;
  }
  return stepBar(st, V) + `
  <div class="stack">
    <div id="msgs">${msgHtml(V)}</div>

    <div class="card">
      <h4>Bench — click the In stacks that are loaded</h4>
      <div class="inner">
        <div class="scroll"><div class="rack disc">${benchHtml(st)}</div></div>
        <div class="row">
          <button id="allfull" type="button">All full</button>
          <button id="allempty" type="button">All empty</button>
          <span style="flex:1"></span>
          <span class="fig"><b>${V.full.length}</b> / ${N_IN_STACKS} full</span>
        </div>
        <div class="legend">
          <div><i class="full"></i> Full stack</div>
          <div><i class="empty"></i> Empty</div>
          <div><i class="out"></i> Pass / Fail</div>
        </div>
      </div>
    </div>
  </div>`;
}

// Draw into a wrapper the screen owns, NEVER into the shadow root itself:
// the platform's <style> is a sibling of the wrapper in that root.
function render(wrap, st) {
  // The modal keeps its height between the steps: Final checks is short,
  // and a modal that shrinks and re-centres on Next reads as a new
  // dialog. The bench's height, measured while it is on screen, is the
  // floor for the other step.
  if (wrap.querySelector(".rack.disc")) st.benchH = wrap.offsetHeight;
  wrap.innerHTML = bodyHtml(st);
  wrap.style.minHeight = st.step !== "bench" && st.benchH ? `${st.benchH}px` : "";
  const q = sel => wrap.querySelector(sel);
  const frozen = () => wrap.dataset.frozen === "1";

  wrap.querySelectorAll("[data-step]").forEach(b => {
    b.onclick = () => { st.step = b.dataset.step; render(wrap, st); };
  });
  wrap.querySelectorAll("[data-check]").forEach(box => {
    box.onchange = e => {
      const k = e.target.dataset.check;
      if (frozen()) { e.target.checked = !!st.checks[k]; return; }
      st.checks[k] = !!e.target.checked;
      e.target.closest(".check").classList.toggle("on", e.target.checked);
      // the stepper's tick follows the last box without a full redraw
      const seg = wrap.querySelector('.stepper [data-step="check"]');
      if (seg) seg.classList.toggle("done", checksOk(st));
    };
  });
  const allfull = q("#allfull");
  if (allfull) allfull.onclick = () => {
    if (frozen()) return;
    for (const k of IN_KEYS) st.in[k] = Array(SLOTS_N).fill(1);
    render(wrap, st);
  };
  const allempty = q("#allempty");
  if (allempty) allempty.onclick = () => {
    if (frozen()) return;
    for (const k of IN_KEYS) st.in[k] = Array(SLOTS_N).fill(0);
    render(wrap, st);
  };
  // One delegated listener on the bench, not one per well (the kit's
  // bindToggles keys on a single Set; this bench spans two holders, so
  // the same shape is written out with data-h / data-i).
  const rack = q(".rack.disc");
  if (rack) rack.onclick = e => {
    if (frozen()) return;
    const w = e.target && e.target.closest ? e.target.closest(".well.toggle") : null;
    if (!w) return;
    const k = w.dataset.h, i = parseInt(w.dataset.i, 10);
    if (!IN_KEYS.includes(k) || Number.isNaN(i)) return;
    st.in[k][i] = st.in[k][i] ? 0 : 1;
    render(wrap, st);
  };
}

// ── the platform's setup-screen contract ──────────────────────────────
// {css, mount(root, api), value(), validate()} — HMI_GUIDE §5.
let _st = null;
let _wrap = null;

export default {
  css: CSS,

  mount(root, api) {
    _st = benchState(api);

    // One wrapper, created once, re-rendered forever. The kit's rules
    // scope under .hmi; data-frozen on the wrapper is what its frozen
    // styling — and this screen's click guards — key off.
    root.querySelectorAll(":scope > .hmi").forEach(n => n.remove());
    const wrap = document.createElement("div");
    wrap.className = "hmi apc";
    wrap.dataset.frozen = api && api.frozen ? "1" : "0";
    root.appendChild(wrap);
    _wrap = wrap;
    render(wrap, _st);
  },

  value() {
    if (!_st) return {};
    // The kwargs setup() in actions.py reads — seven entries per IN
    // holder, index i = A(i+1): FULL (the stack's disc count) or 0.
    return {
      in_1: _st.in.in_1.map(f => (f ? FULL : 0)),
      in_2: _st.in.in_2.map(f => (f ? FULL : 0)),
    };
  },

  validate() {
    // Returns a MESSAGE, not a list — the platform does `if (msg)`, and
    // an empty array is truthy in JS. A problem opens the step it is on.
    if (!_st) return "";
    const errs = check(_st).errs;
    if (errs.length) {
      if (_st.step !== "bench" && _wrap) { _st.step = "bench"; render(_wrap, _st); }
      return errs.length === 1 ? errs[0] : `${errs.length} problems, first: ${errs[0]}`;
    }
    if (checksOk(_st)) return "";
    if (_st.step !== "check" && _wrap) { _st.step = "check"; render(_wrap, _st); }
    const first = CHECKS.find(c => !_st.checks[c.key]);
    const box = _wrap && _wrap.querySelector(`[data-check="${first.key}"]`);
    if (box && box.reportValidity) box.reportValidity();
    return `Confirm: ${first.text}`;
  },
};
