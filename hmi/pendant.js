// hmi/pendant.js — what the operator watches while an apc run goes.
//
// Hosted in a shadow root in the pendant's content area. The platform
// keeps the frame — navbar, control rail, state pill, alarms — and this
// draws the domain: the five disc holders, which position is live, the
// pass/fail tally and the last reading.
// Contract: {css, mount(root, api), update(values)} — hmi-guide §4b.
//
// Served by the RUNTIME server at /hmi/ (the setup screen comes from the
// orchestrator instead — different process, different port), which is
// why this file cannot import /orchestrator/hmi-kit/kit.js and carries
// its own copy of the handful of rules it needs (HMI_GUIDE §7).
// Everything here arrives from _publish() in actions.py (rt.op):
//
//   headline     "Disc 12 — measuring"           the step, in operator words
//   progress     0..100                           the run, as one bar
//   in_stacks    {in_1: [7 states], in_2: [...]}    empty|full|active|done
//   in_left      {in_1: [7 counts], in_2: [...]}    discs still in the position
//   out_stacks   {good_1: [...], good_2: [...], bad_1: [...]}
//                                                   empty|filling|full
//   out_counts   {good_1: [7 counts], ...}          discs in the position
//   pass_n / fail_n / total_n / done_n are published too but NOT drawn:
//   every count the operator needs is in a well
//   last_disc    number of the disc last measured
//   last_c       its capacitance           last_c_unit  the meter's unit
//   last_result  "pass" | "fail"
//
// A key the protocol has not published yet renders as "—", never as 0:
// a dash says "no data", a zero would claim the bench is untouched.
//
// DRAWN AS THE OPERATOR SEES IT — identical to hmi/setup.js, and it has
// to stay identical: the same person reads both screens for the same
// holders. Five rows, top to bottom Fail, Pass 2, Pass 1, In 2, In 1;
// each a single row A1..A7 with A1 on the LEFT. Every well carries its
// NUMBER — discs left in an IN position, discs landed in an OUT one —
// and the live position its ● glyph: colour never carries a state
// alone, and a pendant is read across a room.
//
// TWO COLUMNS when the pane is wide enough, ONE when it is not (a
// container query on the pane's width — the screen adapts to the window,
// not the window to the screen): the bench on the left; on the right,
// where the run is (the step in operator words, and the run as a ring)
// over the last reading. The counts are in the wells. The frame's ACTIVE
// ROUTINE panel is switched off (``hero: false``): it would show the
// same step a second time, in the log's words instead of these.
//
// This screen is READ-ONLY on purpose: stopping and pausing already live
// on the platform's own control rail.

const SLOTS_N = 7;
const SLOTS   = Array.from({ length: SLOTS_N }, (_, i) => `A${i + 1}`);

// Same table as setup.js, same order.
const HOLDERS = [
  { key: "bad_1",  label: "Fail",   role: "bad",  in: false },
  { key: "good_2", label: "Pass 2", role: "good", in: false },
  { key: "good_1", label: "Pass 1", role: "good", in: false },
  { key: "in_2",   label: "In 2",   role: "in",   in: true  },
  { key: "in_1",   label: "In 1",   role: "in",   in: true  },
];

const CELL = 56, GAP = 6, GUTTER = 52;   // the setup screen's sizes

const IN_STATES = {
  empty:  { glyph: "",  label: "Empty" },
  full:   { glyph: "",  label: "Loaded" },
  active: { glyph: "●", label: "Picking" },
  done:   { glyph: "✓", label: "Emptied" },
};
const OUT_STATES = {
  empty:   { glyph: "",  label: "Empty" },
  filling: { glyph: "·", label: "Filling" },
  full:    { glyph: "✓", label: "Full" },
};

// Identity colours — copied from setup.js's wellCss() call, verbatim.
// They do not invert with the theme; only the empty position does.
const C_FULL = "#b9c6d2", C_GOOD = "#a4d89c", C_BAD = "#e8a79c";
const C_ON = "#12191d", C_STROKE = "#2b3338";

const CSS = `
:host { font-size:var(--text-md); }
* { box-sizing:border-box; }
/* The pane is a blank the platform hands this screen (hmi-guide §4b):
   the wrap fills it and is the container the layout answers to; the
   columns centre in it on both axes (auto margins — they collapse to 0
   when the screen is taller than the pane, so it then starts at the top
   and scrolls instead of clipping). */
:host { display:block; height:100%; }
.root { height:100%; }          /* the mount wrapper — the chain .host > .root > .wrap must all have height for min-height:100% to mean the pane */
.wrap { width:100%; min-height:100%; padding:var(--space-4); container-type:inline-size;
  display:flex; }
.cols { display:grid; grid-template-columns:auto minmax(240px, 300px); gap:var(--space-5);
  align-items:start; justify-content:center; width:fit-content; max-width:100%;
  margin:auto; }
.col { display:flex; flex-direction:column; gap:var(--space-5); min-width:0; }
/* narrow pane: one column, the bench first */
@container (max-width: 860px) {
  .cols { grid-template-columns:minmax(0, 1fr); }
  .col.side { flex-direction:row; flex-wrap:wrap; }
  .col.side > .card { flex:1 1 240px; }
}

/* cards, everything centred in them */
.card { background:var(--surface); border:1px solid var(--border);
  border-radius:var(--radius-lg); padding:var(--space-6) var(--space-6) var(--space-5);
  display:flex; flex-direction:column; align-items:center; gap:var(--space-5); }
.card > h3 { margin:0; font-size:var(--text-xs); letter-spacing:.13em;
  text-transform:uppercase; font-weight:700; opacity:.7; text-align:center; }

.legend, .reading { justify-content:center; }

/* where the run is: the step in operator words, and the run as a ring.
   Every card reads the same way — a tracked-caps label (the frame's own
   voice: STEPS, ACTIVE ROUTINE), one line of words, one big figure. */
.state h2 { margin:0; font-size:var(--text-xl); font-weight:700; line-height:1.3; text-align:center; }
.ring { position:relative; width:148px; height:148px; }
.ring svg { width:100%; height:100%; transform:rotate(-90deg); }
.ring circle { fill:none; stroke-width:12; }
.ring .track { stroke:rgba(127,127,127,.2); }
.ring .fill { stroke:var(--accent); stroke-linecap:round;
  transition:stroke-dashoffset var(--motion-med) var(--ease); }
.ring .pct { position:absolute; inset:0; display:flex; align-items:center; justify-content:center;
  font-size:30px; font-weight:700; font-variant-numeric:tabular-nums;
  font-family:ui-monospace,Menlo,Consolas,monospace; }
.ring .pct small { font-size:var(--text-md); font-weight:400; opacity:.6; margin-left:1px; }
@media (prefers-reduced-motion: reduce) { .ring .fill { transition:none; } }

/* the bench: one grid, A1 on the left, a hairline between OUT and IN */
.scroll { overflow-x:auto; }
.rack { display:grid; grid-template-columns:${GUTTER}px repeat(${SLOTS_N}, ${CELL}px);
  gap:${GAP}px; justify-content:center; align-items:center; justify-items:center; }
.ax { font-size:9px; opacity:.45; text-align:center;
  font-family:ui-monospace,Menlo,Consolas,monospace; }
.rlab { justify-self:start; font-size:var(--text-sm); font-weight:700; }
.sep { grid-column:1 / -1; height:1px; background:var(--border); margin:3px 0;
  width:100%; }

.w { position:relative; width:${CELL}px; height:${CELL}px;
  border:2px solid var(--border); border-radius:50%; background:var(--surface);
  display:flex; flex-direction:column; align-items:center; justify-content:center;
  line-height:1.05; overflow:hidden;
  transition:background var(--motion-med) var(--ease),
             border-color var(--motion-med) var(--ease); }
.w b { font-size:var(--text-md); font-weight:700; }
/* IN FLOW, not corner-absolute: the well is a circle with overflow
   hidden, and the number + glyph are what carry state for an operator
   who cannot use the colour — they must never be the thing clipped. */
.w u { text-decoration:none; font-size:11px; font-weight:700; line-height:1; margin-top:2px;
  font-family:ui-monospace,Menlo,Consolas,monospace; font-variant-numeric:tabular-nums; }

/* IN positions */
.w[data-state="empty"]  { border-style:dashed; opacity:.3; }
.w[data-state="full"]   { background:${C_FULL}; border-color:${C_STROKE}; color:${C_ON}; }
.w[data-state="active"] { border-color:var(--accent); background:var(--accent);
                          color:#fff; opacity:1; }
.w[data-state="done"]   { border-color:var(--green); color:var(--green); opacity:.85; }
/* OUT positions — the identity colour of the disc they hold */
.w[data-state="filling"], .w[data-state="fullout"] { border-color:${C_STROKE}; color:${C_ON}; }
.w.good[data-state="filling"], .w.good[data-state="fullout"] { background:${C_GOOD}; }
.w.bad[data-state="filling"],  .w.bad[data-state="fullout"]  { background:${C_BAD}; }
.w[data-state="filling"] { opacity:.7; }
@media (prefers-reduced-motion: reduce) { .w { transition:none; } }

.legend { display:flex; gap:var(--space-4); flex-wrap:wrap; font-size:var(--text-xs);
  opacity:.8; }
/* legend + its note, one block under the bench. The note is its own
   line, NOT a legend item: in a flex row every item counts toward the
   card's max-content width as if on one line, and this sentence would
   widen the bench card by its own length. */
.foot { display:flex; flex-direction:column; align-items:center; gap:var(--space-2); }
.n2 { font-size:var(--text-xs); opacity:.8; text-align:center; }
.legend span { display:flex; align-items:center; gap:var(--space-2); }
.legend em { font-style:normal; font-weight:700; width:1em; text-align:center; }
.legend i { width:12px; height:12px; border-radius:50%; flex:none;
  border:1.5px solid ${C_STROKE}; }
.legend i.n { background:var(--surface); border-color:var(--border); border-style:dashed; }

/* the last reading — the hero number of this screen, read across the room */
.reading { display:flex; flex-direction:column; align-items:center; gap:var(--space-3); }
.reading .val { font-size:44px; font-weight:700; font-variant-numeric:tabular-nums;
  font-family:ui-monospace,Menlo,Consolas,monospace; line-height:1; }
.reading .val small { font-size:var(--text-lg); font-weight:400; opacity:.6; }
.verdict { font-size:var(--text-md); letter-spacing:.1em; text-transform:uppercase;
  font-weight:700; padding:4px 12px; border-radius:var(--radius-sm); }
.verdict.pass { background:${C_GOOD}; color:${C_ON}; }
.verdict.fail { background:${C_BAD}; color:${C_ON}; }
.note { font-size:var(--text-lg); opacity:.75; }
`;

const esc = s => String(s == null ? "" : s)
  .replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const num = v => { const n = Number(v); return Number.isFinite(n) ? n : null; };

// A reading in the unit an operator reads off the meter: a bare farad
// value is scaled to pF / nF / µF / mF; any other unit is shown as sent.
function fmtC(c, unit) {
  let u = String(unit || "");
  let x = c;
  if (u === "F" && x !== 0) {
    const a = Math.abs(x);
    if (a < 1e-9)      { x *= 1e12; u = "pF"; }
    else if (a < 1e-6) { x *= 1e9;  u = "nF"; }
    else if (a < 1e-3) { x *= 1e6;  u = "µF"; }
    else if (a < 1)    { x *= 1e3;  u = "mF"; }
  }
  return { v: Number(x.toPrecision(4)), u };
}

let _root = null;

function benchHtml(v) {
  const ins   = (v.in_stacks  && typeof v.in_stacks  === "object") ? v.in_stacks  : {};
  const outs  = (v.out_stacks && typeof v.out_stacks === "object") ? v.out_stacks : {};
  const left  = (v.in_left    && typeof v.in_left    === "object") ? v.in_left    : {};
  const cnts  = (v.out_counts && typeof v.out_counts === "object") ? v.out_counts : {};
  let h = `<div class="ax"></div>` + SLOTS.map(s => `<div class="ax">${s}</div>`).join("");
  let prevIn = null;
  for (const hd of HOLDERS) {
    if (prevIn !== null && prevIn !== hd.in) h += `<div class="sep"></div>`;
    prevIn = hd.in;
    h += `<div class="rlab">${esc(hd.label)}</div>`;
    const row  = Array.isArray((hd.in ? ins : outs)[hd.key]) ? (hd.in ? ins : outs)[hd.key] : [];
    const nums = Array.isArray((hd.in ? left : cnts)[hd.key]) ? (hd.in ? left : cnts)[hd.key] : [];
    for (let i = 0; i < SLOTS_N; i++) {
      const raw = String(row[i] || "empty");
      const table = hd.in ? IN_STATES : OUT_STATES;
      const st = table[raw] ? raw : "empty";
      // "full" means two things on this bench: a loaded IN stack and a
      // filled OUT position. They are styled apart by name.
      const attr = (!hd.in && st === "full") ? "fullout" : st;
      const n = num(nums[i]);
      // The number in the well: discs LEFT in an IN position, discs IN an
      // OUT one. The live IN position carries ● before its number; an
      // emptied one its ✓; an empty position shows nothing.
      let line = "";
      if (hd.in) {
        if (st === "active")      line = `●${n == null ? "" : n}`;
        else if (st === "done")   line = "✓";
        else if (st === "full")   line = n == null ? "" : String(n);
      } else if (n != null && n > 0) {
        line = String(n);
      }
      const what = hd.in ? (n == null ? "" : ` · ${n} left`) : (n == null ? "" : ` · ${n} disc${n === 1 ? "" : "s"}`);
      const title = `${hd.label} · ${SLOTS[i]} · ${table[st].label}${what}`;
      h += `<div class="w ${hd.role}" data-state="${attr}" title="${esc(title)}">` +
           `<b>${SLOTS[i]}</b>` + (line ? `<u>${esc(line)}</u>` : "") + `</div>`;
    }
  }
  return h;
}

function readingHtml(v) {
  const c = num(v.last_c);
  if (c == null) return `<div class="note">No reading yet.</div>`;
  const disc = num(v.last_disc);
  const res = v.last_result === "pass" ? "pass" : v.last_result === "fail" ? "fail" : null;
  return `<div class="reading">
    <span class="note">${disc == null ? "Last disc" : `Disc ${disc}`}</span>
    <span class="val">${esc(fmtC(c, v.last_c_unit).v)}<small> ${esc(fmtC(c, v.last_c_unit).u)}</small></span>
    ${res ? `<span class="verdict ${res}">${res === "pass" ? "Passed" : "Failed"}</span>` : ""}
  </div>`;
}

// The ring: r = 62 at a 12 px stroke inside a 148 px box; the dash
// offset is what is NOT yet done.
const RING_R = 62, RING_C = 2 * Math.PI * RING_R;

function ringHtml(pct) {
  const done = pct == null ? 0 : pct;
  return `<div class="ring" role="img" aria-label="${pct == null ? "progress unknown" : pct + "% of the run"}">
    <svg viewBox="0 0 148 148">
      <circle class="track" cx="74" cy="74" r="${RING_R}"></circle>
      <circle class="fill" cx="74" cy="74" r="${RING_R}"
        stroke-dasharray="${RING_C.toFixed(1)}" stroke-dashoffset="${(RING_C * (1 - done / 100)).toFixed(1)}"></circle>
    </svg>
    <div class="pct">${pct == null ? "—" : `${pct}<small>%</small>`}</div>
  </div>`;
}

function html(v) {
  const p = num(v.progress);
  const pct = p == null ? null : Math.max(0, Math.min(100, Math.round(p)));
  return `
  <div class="wrap">
    <div class="cols">
      <div class="col">
        <div class="card">
          <h3>Bench</h3>
          <div class="scroll"><div class="rack">${benchHtml(v)}</div></div>
          <div class="foot">
            <div class="legend">
              <span><i style="background:${C_FULL}"></i> Loaded</span>
              <span><em>●</em> Picking</span>
              <span><em>✓</em> Emptied</span>
              <span><i style="background:${C_GOOD}"></i> Passed discs</span>
              <span><i style="background:${C_BAD}"></i> Failed discs</span>
              <span><i class="n"></i> Empty</span>
            </div>
            <div class="n2">Numbers: discs left in an In position · discs in a Pass / Fail position</div>
          </div>
        </div>
      </div>
      <div class="col side">
        <div class="card state">
          <h3>Run</h3>
          <h2>${esc(v.headline || "Waiting to start")}</h2>
          ${ringHtml(pct)}
        </div>
        <div class="card">
          <h3>Last measurement</h3>
          ${readingHtml(v)}
        </div>
      </div>
    </div>
  </div>`;
}

export default {
  css: CSS,
  // The frame's ACTIVE ROUTINE panel would repeat the headline above.
  hero: false,

  mount(root, api) {
    // Own a wrapper; never touch root.innerHTML. The platform appends its
    // <style> (and any sibling pendant.css) to this same shadow root, and
    // clobbering the root would delete them on first paint.
    _root = document.createElement("div");
    _root.className = "root";
    root.appendChild(_root);
    _root.innerHTML = html(api && api.values ? api.values : {});
  },

  update(values) {
    if (!_root) return;
    _root.innerHTML = html(values || {});
  },
};
