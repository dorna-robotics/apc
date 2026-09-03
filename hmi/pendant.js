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
//   headline     "Disc 12 — measuring"
//   in_stacks    {in_1: [7 states], in_2: [...]}    empty|full|active|done
//   out_stacks   {good_1: [...], good_2: [...], bad_1: [...]}
//                                                   empty|filling|full
//   pass_n       passed so far             fail_n   failed so far
//   total_n / done_n are published too but deliberately NOT shown
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
// each a single row A1..A7 with A1 on the LEFT. States carry a GLYPH as
// well as a colour — colour never carries a state alone, and a pendant
// is read across a room.
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
:host { display:block; font-size:var(--text-md); }
* { box-sizing:border-box; }
.wrap { display:flex; flex-direction:column; gap:var(--space-5); padding:var(--space-4);
  width:fit-content; max-width:100%; margin:0 auto; }   /* centred in the pendant */

.head { display:flex; align-items:baseline; gap:var(--space-4); flex-wrap:wrap; }
.head h2 { margin:0; font-size:22px; font-weight:700; line-height:1.2; flex:1 1 14rem; }
.tally { font-size:26px; font-weight:700; font-variant-numeric:tabular-nums;
  font-family:ui-monospace,Menlo,Consolas,monospace; white-space:nowrap; }
.tally small { font-size:var(--text-sm); font-weight:400; opacity:.6; }

.card { border:1px solid var(--border); border-radius:var(--radius-lg);
  padding:var(--space-4); }
.card > h3 { margin:0 0 var(--space-3); font-size:var(--text-xs); letter-spacing:.13em;
  text-transform:uppercase; font-weight:700; opacity:.7; text-align:center; }
.head { justify-content:center; text-align:center; }
.legend, .reading { justify-content:center; }

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
   hidden, and the glyph is what carries state for an operator who
   cannot use the colour — it must never be the thing that is clipped. */
.w u { text-decoration:none; font-size:13px; font-weight:700; line-height:1; margin-top:2px; }

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

.legend { display:flex; gap:var(--space-4); flex-wrap:wrap; margin-top:var(--space-3);
  font-size:var(--text-xs); opacity:.8; }
.legend span { display:flex; align-items:center; gap:var(--space-2); }
.legend em { font-style:normal; font-weight:700; width:1em; text-align:center; }
.legend i { width:12px; height:12px; border-radius:50%; flex:none;
  border:1.5px solid ${C_STROKE}; }
.legend i.n { background:var(--surface); border-color:var(--border); border-style:dashed; }

/* the last reading */
.reading { display:flex; align-items:baseline; gap:var(--space-4); flex-wrap:wrap; }
.reading .val { font-size:26px; font-weight:700; font-variant-numeric:tabular-nums;
  font-family:ui-monospace,Menlo,Consolas,monospace; }
.reading .val small { font-size:var(--text-sm); font-weight:400; opacity:.6; }
.verdict { font-size:var(--text-xs); letter-spacing:.1em; text-transform:uppercase;
  font-weight:700; padding:2px 8px; border-radius:var(--radius-xs); }
.verdict.pass { background:${C_GOOD}; color:${C_ON}; }
.verdict.fail { background:${C_BAD}; color:${C_ON}; }
.note { font-size:var(--text-sm); opacity:.75; }
`;

const esc = s => String(s == null ? "" : s)
  .replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const num = v => { const n = Number(v); return Number.isFinite(n) ? n : null; };
const dash = v => (v == null || v === "") ? "—" : v;

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
  const ins  = (v.in_stacks  && typeof v.in_stacks  === "object") ? v.in_stacks  : {};
  const outs = (v.out_stacks && typeof v.out_stacks === "object") ? v.out_stacks : {};
  let h = `<div class="ax"></div>` + SLOTS.map(s => `<div class="ax">${s}</div>`).join("");
  let prevIn = null;
  for (const hd of HOLDERS) {
    if (prevIn !== null && prevIn !== hd.in) h += `<div class="sep"></div>`;
    prevIn = hd.in;
    h += `<div class="rlab">${esc(hd.label)}</div>`;
    const row = Array.isArray((hd.in ? ins : outs)[hd.key]) ? (hd.in ? ins : outs)[hd.key] : [];
    for (let i = 0; i < SLOTS_N; i++) {
      const raw = String(row[i] || "empty");
      const table = hd.in ? IN_STATES : OUT_STATES;
      const st = table[raw] ? raw : "empty";
      // "full" means two things on this bench: a loaded IN stack and a
      // filled OUT position. They are styled apart by name.
      const attr = (!hd.in && st === "full") ? "fullout" : st;
      const title = `${hd.label} · ${SLOTS[i]} · ${table[st].label}`;
      h += `<div class="w ${hd.role}" data-state="${attr}" title="${esc(title)}">` +
           `<b>${SLOTS[i]}</b>` + (table[st].glyph ? `<u>${table[st].glyph}</u>` : "") + `</div>`;
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

function html(v) {
  // Passed and failed only — never a total or a "done of": the operator
  // marked stacks full or empty and was never shown a disc count.
  const pass = dash(num(v.pass_n)), fail = dash(num(v.fail_n));
  return `
  <div class="wrap">
    <div class="head">
      <h2>${esc(v.headline || "Waiting to start")}</h2>
      <div class="tally">${pass}<small> passed</small> · ${fail}<small> failed</small></div>
    </div>

    <div class="card">
      <h3>Bench</h3>
      <div class="scroll"><div class="rack">${benchHtml(v)}</div></div>
      <div class="legend">
        <span><i style="background:${C_FULL}"></i> Loaded</span>
        <span><em>●</em> Picking</span>
        <span><em>✓</em> Emptied / full</span>
        <span><i style="background:${C_GOOD}"></i> Passed discs</span>
        <span><i style="background:${C_BAD}"></i> Failed discs</span>
        <span><i class="n"></i> Empty</span>
      </div>
    </div>

    <div class="card">
      <h3>Last measurement</h3>
      ${readingHtml(v)}
    </div>
  </div>`;
}

export default {
  css: CSS,

  mount(root, api) {
    // Own a wrapper; never touch root.innerHTML. The platform appends its
    // <style> (and any sibling pendant.css) to this same shadow root, and
    // clobbering the root would delete them on first paint.
    _root = document.createElement("div");
    root.appendChild(_root);
    _root.innerHTML = html(api && api.values ? api.values : {});
  },

  update(values) {
    if (!_root) return;
    _root.innerHTML = html(values || {});
  },
};
