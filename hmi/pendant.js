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
//   last_d       its dissipation factor D
//   last_c_min / last_c_max / last_d_max  the window it was judged against
//   last_c_out   "below" | "above" | ""   where C fell out of it (Sort.c_out)
//   last_d_out   "above" | ""             where D fell out of it (Sort.d_out)
//   last_result  "pass" | "fail"
//   inspection   the LAST inspection, either camera (actions.py _inspect):
//                {cam, disc, verdict, why, det {conf, box}, outer / inner
//                [cx, cy, r], chip, dent, dent_at, dent_max, offset,
//                off_max, cls {circle, state, p (fail probability), max}, img, w, h} — chip
//                false: a camera without a chip check (the robot camera),
//                its Chip tile not checked; img an
//                rt.asset path (api.url makes it loadable), w × h the
//                frame it is in; absent in sim. Drawn as the inspection
//                card under the columns: four tiles over the one frame.
//   notice       {level, title, text} | absent    the operator is needed
//                (the anode blocked after MAX_BLOWS): a MODAL over the
//                whole screen, the bench dimmed behind it. Its Dismiss
//                button is the ONLY way to close it — a click on the
//                backdrop does nothing — and a dismissed notice stays
//                away until a different one arrives or the protocol
//                publishes without one. level: warning | error | info.
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
// THREE FULL-HEIGHT COLUMNS when the pane is wide enough, ONE when it is
// not (a container query on the pane's width — the screen adapts to the
// window, not the window to the screen), as bna's pendant: the bench;
// where the run is (the step in operator words, and the run as a ring);
// the last reading. Under them the inspection card, exactly their width. The counts are in the wells. The frame's ACTIVE
// ROUTINE panel is switched off (``hero: false``): it would show the
// same step a second time, in the log's words instead of these.
//
// This screen is READ-ONLY on purpose: stopping and pausing already live
// on the platform's own control rail.
//
// THE INSPECTION CARD — the last verdict, drawn on its own frame: (1) the
// detector's box, (2) the disc's two circles, (3) a close-up of the
// edge's deepest dent, ringed red when it is a chip, (4) the face the
// classifier sees. One
// picture (an rt.asset, held in memory by the runtime), every mark and
// label drawn here over it — sharp at any size, nothing burned into
// pixels. The card is built once per inspection and its element reused
// across the screen's redraws, so the picture never reloads on a tick.

const SLOTS_N = 7;
const SLOTS   = Array.from({ length: SLOTS_N }, (_, i) => `A${i + 1}`);

// Same table as setup.js, same order — ``lanes`` too (actions.py LANES).
const HOLDERS = [
  { key: "bad_1",  label: "Fail",   role: "bad",  in: false, lanes: [["Capacitor", 3], ["Visual", 3], ["Stuck", 1]] },
  { key: "good_2", label: "Pass 2", role: "good", in: false },
  { key: "good_1", label: "Pass 1", role: "good", in: false },
  { key: "in_2",   label: "In 2",   role: "in",   in: true  },
  { key: "in_1",   label: "In 1",   role: "in",   in: true  },
];

const CELL = 56, GAP = 10, GUTTER = 52;   // the setup screen's sizes

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
/* the notice card (when there is one) over the columns, as wide as they
   are; the stack centres in the pane on both axes */
.stack { display:flex; flex-direction:column; gap:var(--space-5); width:fit-content;
  max-width:100%; margin:auto; }
/* THREE FULL-HEIGHT COLUMNS — bench | run | last measurement — as bna's
   pendant: every card stands the row's height, its title top-left at the
   same line, its content from the top down. */
.cols { display:grid; grid-template-columns:auto minmax(240px, 300px) minmax(240px, 300px);
  gap:var(--space-5); align-items:stretch; justify-content:center; width:100%; }
.col { display:flex; flex-direction:column; gap:var(--space-5); min-width:0; }
.col > .card { flex:1 1 auto; }
/* narrow pane: one column, the bench first */
@container (max-width: 1000px) {
  .cols { grid-template-columns:minmax(0, 1fr); }
}

/* the inspection card: under the columns, EXACTLY as wide as they are —
   width 0 so its four pictures never widen the stack (which sizes to
   its content), min-width 100% so it then fills it */
.insp { width:0; min-width:100%; align-items:stretch; gap:var(--space-4); }
.insp .ihead { align-self:stretch; display:flex; align-items:center; gap:var(--space-3); flex-wrap:wrap; }
.insp .ihead h3 { margin:0; font-size:var(--text-xs); letter-spacing:.13em; text-transform:uppercase;
  font-weight:700; opacity:.7; text-align:left; }
.insp .tiles { align-self:stretch; }
.insp .who { font-size:var(--text-md); color:var(--muted); }
.insp .grow { flex:1; }
.insp .why { font-size:var(--text-md); font-weight:600; color:var(--red); }
.insp .vpill { font-size:var(--text-sm); font-weight:700; letter-spacing:.06em; padding:4px 14px;
  border-radius:999px; color:#fff; background:var(--muted); }
.insp .vpill.pass { background:var(--green); }
.insp .vpill.fail, .insp .vpill.empty { background:var(--red); }
.insp .tiles { display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:var(--space-4); }
@container (max-width: 860px) { .insp .tiles { grid-template-columns:repeat(2, minmax(0, 1fr)); } }
.insp .frame { position:relative; aspect-ratio:1; border-radius:var(--radius-md); overflow:hidden; background:#1c1c1e; }
.insp .frame svg { width:100%; height:100%; display:block; }
.insp .tag { position:absolute; left:10px; bottom:10px; max-width:calc(100% - 20px); white-space:nowrap;
  overflow:hidden; text-overflow:ellipsis; font-size:12.5px; font-weight:600; line-height:1; padding:6px 10px;
  border-radius:999px; background:rgba(0,0,0,.62); color:#fff; backdrop-filter:blur(6px); }
.insp .tag b { font-family:ui-monospace,Menlo,Consolas,monospace; font-weight:700; }
/* a tile whose check never ran: no picture — a quiet hatched panel, the
   tile's own outline, where the disc stopped */
.insp .frame.ph { background:var(--surface2);
  background-image:repeating-linear-gradient(135deg, transparent 0 11px, rgba(127,127,127,.07) 11px 22px);
  box-shadow:inset 0 0 0 1.5px var(--border); }
.insp .ph-in { position:absolute; inset:0; display:flex; flex-direction:column; align-items:center;
  justify-content:center; gap:6px; color:var(--muted); text-align:center; }
.insp .ph-in svg { width:56px; height:56px; fill:none; stroke:currentColor; stroke-width:1.6; opacity:.55;
  margin-bottom:4px; }
.insp .ph-in b { font-size:var(--text-md); font-weight:600; color:var(--text); opacity:.6; }
.insp .ph-in span { font-size:var(--text-sm); }
.insp .cap { display:flex; align-items:center; gap:var(--space-2); margin-top:var(--space-2);
  font-size:var(--text-md); font-weight:600; }
.insp .cap .n { width:20px; height:20px; border-radius:50%; background:var(--surface2); flex:none;
  display:flex; align-items:center; justify-content:center; font-size:11px; font-weight:700; color:var(--muted); }
.insp .cap .st { margin-left:auto; font-size:var(--text-xs); font-weight:700; letter-spacing:.05em; text-transform:uppercase; }
.insp .st.ok { color:var(--green); } .insp .st.bad { color:var(--red); } .insp .st.off { color:var(--muted); }

/* the notice: the operator is needed — a modal over the whole screen.
   The host is the pane; the backdrop fills it and dims the bench, the
   dialog sits in the middle, in the frame's own warning voice. The
   control rail under the pane stays reachable: the Resume it names
   is there. */
:host { position:relative; }
.modal-bg { position:absolute; inset:0; z-index:10; display:flex; align-items:center;
  justify-content:center; padding:var(--space-6); background:rgba(0,0,0,.5); }
.modal { width:min(560px, 100%); background:var(--surface); border-radius:var(--radius-lg);
  box-shadow:0 12px 48px rgba(0,0,0,.4); padding:var(--space-6) 32px 28px;
  display:flex; flex-direction:column; align-items:center; gap:var(--space-3);
  text-align:center; border-top:5px solid var(--amber);
  animation:notice-in var(--motion-med) var(--ease) both; }
@keyframes notice-in { from { transform:scale(.96); opacity:0; } to { transform:none; opacity:1; } }
@media (prefers-reduced-motion: reduce) { .modal { animation:none; } }
.modal .ico { width:64px; height:64px; border-radius:50%; display:flex; align-items:center;
  justify-content:center; background:rgba(255,159,10,.15); color:var(--amber); margin-bottom:var(--space-2); }
.modal .ico svg { width:34px; height:34px; }
.modal .nl { font-size:var(--text-sm); letter-spacing:.14em; text-transform:uppercase;
  font-weight:700; color:var(--amber); }
.modal h2 { margin:0; font-size:26px; font-weight:700; line-height:1.2; }
.modal p { margin:var(--space-2) 0 0; font-size:var(--text-lg); line-height:1.45; opacity:.9; }
.modal .hint { margin-top:var(--space-4); font-size:var(--text-md); opacity:.7; }
.modal .hint b { font-weight:700; opacity:1; }
.modal .ack { margin-top:var(--space-5); padding:10px 26px; border-radius:var(--radius-sm);
  border:1px solid var(--border); background:transparent; color:var(--text); font:inherit;
  font-size:var(--text-md); font-weight:600; letter-spacing:.02em; cursor:pointer; }
.modal .ack:hover { background:rgba(127,127,127,.12); }
.modal .ack:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.modal[data-level="error"] { border-top-color:var(--red); }
.modal[data-level="error"] .ico { background:rgba(255,69,58,.15); color:var(--red); }
.modal[data-level="error"] .nl { color:var(--red); }
.modal[data-level="info"] { border-top-color:var(--accent); }
.modal[data-level="info"] .ico { background:rgba(10,132,255,.15); color:var(--accent); }
.modal[data-level="info"] .nl { color:var(--accent); }

/* cards — the title top-left in grey caps (bna's), the content centred
   under it */
.card { background:var(--surface); border:1px solid var(--border);
  border-radius:var(--radius-lg); padding:var(--space-5) var(--space-6) var(--space-6);
  display:flex; flex-direction:column; align-items:center; gap:var(--space-5); }
.card > h3 { align-self:stretch; margin:0; font-size:var(--text-xs); letter-spacing:.13em;
  text-transform:uppercase; font-weight:700; opacity:.7; text-align:left; }

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

/* the bench: one grid, A1 on the left, space between OUT and IN */
.scroll { overflow-x:auto; padding:4px; }   /* room for a lane's box, 3 px past the wells */
.rack { display:grid; grid-template-columns:${GUTTER}px repeat(${SLOTS_N}, ${CELL}px);
  gap:${GAP}px; justify-content:center; align-items:center; justify-items:center; }
.rlab { justify-self:start; font-size:var(--text-sm); font-weight:700; }
.gap { grid-column:1 / -1; height:12px; }
/* a holder's lanes: each lane's wells in one rounded box, its name over
   it (setup.js draws them the same) */
.lane { align-self:end; margin-bottom:2px; text-align:center;
  font-size:10px; font-weight:600; color:var(--muted); }
.lanebox { justify-self:stretch; display:flex; gap:${GAP}px; margin:-3px;
  padding:2px; border:1px solid var(--border); border-radius:${CELL / 2 + 3}px;
  background:color-mix(in srgb, var(--text) 3%, transparent); }

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

/* the last reading — C and D, the hero numbers of this screen, read across the room */
.reading { display:flex; flex-direction:column; align-items:center; gap:var(--space-3); }
.reading .val { font-size:44px; font-weight:700; font-variant-numeric:tabular-nums;
  font-family:ui-monospace,Menlo,Consolas,monospace; line-height:1; }
.reading .val small { font-size:var(--text-lg); font-weight:400; opacity:.6; }
/* under each value its pass window; a value out of it is red — why the
   disc failed, at a glance */
.reading .val.out { color:var(--red); }
.reading .lim { font-size:var(--text-md); font-variant-numeric:tabular-nums; opacity:.7;
  margin-top:calc(-1 * var(--space-2)); }
.verdict { font-size:var(--text-md); letter-spacing:.1em; text-transform:uppercase;
  font-weight:700; padding:4px 12px; border-radius:var(--radius-sm); }
.verdict.pass { background:${C_GOOD}; color:${C_ON}; }
.verdict.fail { background:${C_BAD}; color:${C_ON}; }
.note { font-size:var(--text-lg); opacity:.75; }
`;

const esc = s => String(s == null ? "" : s)
  .replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
// A number, or null for anything that is not one — null and "" included
// (Number(null) is 0: a cleared value would draw as a zero reading).
const num = v => { if (v == null || v === "") return null; const n = Number(v); return Number.isFinite(n) ? n : null; };

// C in nF, the unit of the run's measurement window on the setup screen;
// a unit other than a bare farad is shown as sent.
function fmtC(c, unit) {
  if (String(unit || "") !== "F") return { v: String(Number(c.toPrecision(4))), u: String(unit || "") };
  return { v: (c * 1e9).toFixed(2), u: "nF" };
}

let _root = null;
let _api = null;
let _last = {};          // the values last drawn — Dismiss redraws them
let _insp = { key: null, el: null };   // the inspection card, built once per inspection
let _ack = null;         // the notice the operator dismissed (its key), or null

// A notice's identity: the same words are the same notice. Dismissed
// once, it stays away until a DIFFERENT one arrives; gone from the
// values (the protocol moved on), the slate is clean again.
const noticeKey = n => JSON.stringify([n.level, n.title, n.text]);

function benchHtml(v) {
  const ins   = (v.in_stacks  && typeof v.in_stacks  === "object") ? v.in_stacks  : {};
  const outs  = (v.out_stacks && typeof v.out_stacks === "object") ? v.out_stacks : {};
  const left  = (v.in_left    && typeof v.in_left    === "object") ? v.in_left    : {};
  const cnts  = (v.out_counts && typeof v.out_counts === "object") ? v.out_counts : {};
  let h = "";
  let prevIn = null;
  for (const hd of HOLDERS) {
    if (prevIn !== null && prevIn !== hd.in) h += `<div class="gap"></div>`;
    prevIn = hd.in;
    // the holder's lanes, over it, as setup.js draws them
    if (hd.lanes) h += `<div></div>` + hd.lanes.map(([label, n]) =>
      `<div class="lane" style="grid-column:span ${n}">${esc(label)}</div>`).join("");
    h += `<div class="rlab">${esc(hd.label)}</div>`;
    const row  = Array.isArray((hd.in ? ins : outs)[hd.key]) ? (hd.in ? ins : outs)[hd.key] : [];
    const nums = Array.isArray((hd.in ? left : cnts)[hd.key]) ? (hd.in ? left : cnts)[hd.key] : [];
    const ws = [];
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
      ws.push(`<div class="w ${hd.role}" data-state="${attr}" title="${esc(title)}">` +
              `<b>${SLOTS[i]}</b>` + (line ? `<u>${esc(line)}</u>` : "") + `</div>`);
    }
    // a lane's wells boxed together, as setup.js laneRow
    let at = 0;
    h += !hd.lanes ? ws.join("") : hd.lanes.map(([, n]) => {
      const box = `<div class="lanebox" style="grid-column:span ${n}">${ws.slice(at, at + n).join("")}</div>`;
      at += n;
      return box;
    }).join("");
  }
  return h;
}

// The pass window under each value, in the value's own unit.
function cLimHtml(v) {
  const lo = num(v.last_c_min), hi = num(v.last_c_max);
  if (lo == null || hi == null) return "";
  const f = x => fmtC(x, v.last_c_unit);
  return `<span class="lim">${esc(f(lo).v)} – ${esc(f(hi).v)} ${esc(f(hi).u)}</span>`;
}
function dLimHtml(v) {
  const hi = num(v.last_d_max);
  if (hi == null) return "";
  return `<span class="lim">≤ ${hi.toFixed(3)}</span>`;
}

function readingHtml(v) {
  const c = num(v.last_c);
  if (c == null) return `<div class="note">No reading yet.</div>`;
  const disc = num(v.last_disc);
  const res = v.last_result === "pass" ? "pass" : v.last_result === "fail" ? "fail" : null;
  return `<div class="reading">
    <span class="note">${disc == null ? "Last disc" : `Disc ${disc}`}</span>
    <span class="val${v.last_c_out ? " out" : ""}"><small>C </small>${esc(fmtC(c, v.last_c_unit).v)}<small> ${esc(fmtC(c, v.last_c_unit).u)}</small></span>
    ${cLimHtml(v)}
    <span class="val${v.last_d_out ? " out" : ""}"><small>D </small>${num(v.last_d) == null ? "—" : num(v.last_d).toFixed(3)}</span>
    ${dLimHtml(v)}
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

const LEVELS = { warning: "Operator needed", error: "Run stopped", info: "Notice" };

function noticeHtml(n) {
  if (!n || typeof n !== "object") { _ack = null; return ""; }
  if (noticeKey(n) === _ack) return "";
  const level = LEVELS[n.level] ? n.level : "warning";
  return `<div class="modal-bg"><div class="modal" data-level="${level}" role="alertdialog" aria-live="assertive">
    <div class="ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>
      <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
    </svg></div>
    <span class="nl">${esc(LEVELS[level])}</span>
    <h2>${esc(n.title || "")}</h2>
    ${n.text ? `<p>${esc(n.text)}</p>` : ""}
    <div class="hint">Then press <b>Resume</b> on the bar below.</div>
    <button type="button" class="ack">Dismiss</button>
  </div></div>`;
}

// Draw, and wire the one control this screen has: Dismiss. The backdrop
// has no handler on purpose — a stray tap on the bench must not take the
// notice away.
// ── the inspection card ───────────────────────────────────────────────
const IC = { outer: "#ff9f0a", inner: "#0a84ff", whole: "#30d158", miss: "#ff453a", box: "#0a84ff" };

// One inspection as the card's HTML: the frame (or a grey stand-in in sim,
// sized to the geometry), and per tile a viewBox over it — the whole disc
// in context for the detector, the disc close up for the other three.
function inspectionHtml(c) {
  const det = c.det, o = c.outer;
  const W = num(c.w) || (det ? Math.round(det.box[2] + det.box[0]) : 1000);
  const H = num(c.h) || (det ? Math.round(det.box[3] + det.box[1]) : 1000);
  const src = c.img && _api && typeof _api.url === "function" ? _api.url(c.img) : c.img;
  const pic = src ? `<image href="${esc(src)}" width="${W}" height="${H}"/>`
                  : `<rect width="${W}" height="${H}" fill="#3a3a3c"/>`;
  // the disc: its edge circle, else the detector's box as a circle
  const disc = o || (det ? [(det.box[0] + det.box[2]) / 2, (det.box[1] + det.box[3]) / 2,
                            Math.max(det.box[2] - det.box[0], det.box[3] - det.box[1]) / 2] : null);
  const square = (k) => {
    if (!disc) return `0 0 ${W} ${H}`;
    const h = Math.min(disc[2] * k, W / 2, H / 2);
    const x = Math.min(Math.max(disc[0] - h, 0), W - 2 * h), y = Math.min(Math.max(disc[1] - h, 0), H - 2 * h);
    return `${x.toFixed(1)} ${y.toFixed(1)} ${(2 * h).toFixed(1)} ${(2 * h).toFixed(1)}`;
  };
  const near = square(1.12), far = square(2.4);
  const sw = disc ? disc[2] * 2.24 / 260 : W / 260;          // ~1 screen px, in frame px
  const cross = (x, y, col) => `<path d="M${x - 9 * sw},${y} h${18 * sw} M${x},${y - 9 * sw} v${18 * sw}" stroke="${col}" stroke-width="${2.2 * sw}"/>`;
  const svg = (vb, body) => `<svg viewBox="${vb}" preserveAspectRatio="xMidYMid slice">${body}</svg>`;
  const t1 = svg(far, pic + (det ? `<rect x="${det.box[0]}" y="${det.box[1]}" width="${det.box[2] - det.box[0]}"
      height="${det.box[3] - det.box[1]}" fill="none" stroke="${IC.box}" stroke-width="${3 * sw * 2.1}" rx="${4 * sw}"/>` : ""));
  const i = c.inner;
  const t2 = svg(near, pic + (o ? `<circle cx="${o[0]}" cy="${o[1]}" r="${o[2]}" fill="none" stroke="${IC.outer}" stroke-width="${2.6 * sw}"/>`
      + cross(o[0], o[1], IC.outer) : "")
    + (i ? `<circle cx="${i[0]}" cy="${i[1]}" r="${i[2]}" fill="none" stroke="${IC.inner}" stroke-width="${2.6 * sw}"/>`
      + cross(i[0], i[1], IC.inner) : ""));
  // the deepest dent, close up: a notch a few px deep is invisible on the
  // whole disc. The fitted edge dashed, the dent ringed — red past the max.
  const dent = num(c.dent), dentMax = num(c.dent_max), da = num(c.dent_at);
  const chip = dent != null && dentMax != null && dent > dentMax;
  let t3 = svg(near, pic);
  if (o && da != null) {
    const x = o[0] + o[2] * Math.cos(da * Math.PI / 180), y = o[1] + o[2] * Math.sin(da * Math.PI / 180);
    const h = Math.min(o[2] * 0.2, W / 2, H / 2), zs = sw * 0.2 / 1.12;      // ~1 screen px at this zoom
    const vx = Math.min(Math.max(x - h, 0), W - 2 * h), vy = Math.min(Math.max(y - h, 0), H - 2 * h);
    t3 = svg(`${vx.toFixed(1)} ${vy.toFixed(1)} ${(2 * h).toFixed(1)} ${(2 * h).toFixed(1)}`, pic
      + `<circle cx="${o[0]}" cy="${o[1]}" r="${o[2]}" fill="none" stroke="${IC.outer}" stroke-width="${1.6 * zs}"
          stroke-dasharray="${6 * zs} ${5 * zs}"/>`
      + `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${(h * 0.32).toFixed(1)}" fill="none"
          stroke="${chip ? IC.miss : IC.whole}" stroke-width="${3 * zs}"/>`);
  }
  // the classifier's input — exactly the crop it is handed: the face
  // circle, grey outside (the vision ROI's own fill)
  const f = c.cls && c.cls.circle;
  const clip = `cls-${esc(c.disc)}-${esc(c.cam).replace(/\W/g, "")}`;
  const t4 = svg(near, `<rect width="${W}" height="${H}" fill="#808080"/>` + (f ? `<defs><clipPath id="${clip}">
      <circle cx="${f[0]}" cy="${f[1]}" r="${f[2]}"/></clipPath></defs><g clip-path="url(#${clip})">${pic}</g>
      <circle cx="${f[0]}" cy="${f[1]}" r="${f[2]}" fill="none" stroke="rgba(255,255,255,.85)"
        stroke-width="${1.6 * sw}" stroke-dasharray="${6 * sw} ${5 * sw}"/>` : ""));
  const off = num(c.offset), offMax = num(c.off_max);
  const cs = c.cls ? c.cls.state : null;
  // Where the disc stopped (actions.py ``failed_at``): the checks run
  // detector → disc edge → chip → concentric → classifier, the first that
  // fails decides, and every tile after the one it failed at never ran.
  // The concentric check is tile 2's, but it runs after the chip — so a
  // chip stops the classifier, and leaves tile 2 with its circles but no
  // verdict on the centres.
  const at = c.failed_at || null;
  // checks the run ignores (actions.py ``ignored``): chip / concentric /
  // classifier; ``circles`` false — no check used them, none were looked for
  const ign = new Set(Array.isArray(c.ignored) ? c.ignored : []);
  const ran = { 2: at !== "detector", 3: !["detector", "circles"].includes(at),
                4: at == null || at === "classifier" };
  const tile = (n, title, body, tag, st) => `<div class="tile"><div class="frame">${body}
      ${tag ? `<div class="tag">${tag}</div>` : ""}</div>
    <div class="cap"><span class="n">${n}</span>${title}<span class="st ${st[0]}">${st[1]}</span></div></div>`;
  // A tile whose check never ran: no picture — a quiet hatched panel, the
  // tile's own outline, and why: where the disc stopped, that the run
  // ignores the check, or (c.chip false) that this camera has none.
  const stoppedAt = { detector: "Detector", circles: "Circles", chip: "Chip", concentric: "Circles",
                      classifier: "Face anomaly" }[at] || "";
  const idle = (n, title, head = "Not run", why = `Stopped at ${esc(stoppedAt)}`) => `<div class="tile">
      <div class="frame ph"><div class="ph-in">${PH_ICON[n]}<b>${head}</b><span>${why}</span></div></div>
    <div class="cap"><span class="n">${n}</span>${title}<span class="st off">${head.toLowerCase()}</span></div></div>`;
  return `<div class="card insp">
    <div class="ihead"><h3>Last inspection</h3>
      <span class="who">${esc(cap(c.cam))} · disc ${esc(c.disc)}</span><span class="grow"></span>
      ${c.why ? `<span class="why">${esc(String(c.why).replace(/^[^:]*:\s*/, ""))}</span>` : ""}
      <span class="vpill ${esc(c.verdict)}">${c.verdict === "empty" ? "NO DISC" : esc(String(c.verdict || "").toUpperCase())}</span></div>
    <div class="tiles">
      ${tile(1, "Detector", t1, det ? `disc <b>${det.conf.toFixed(2)}</b>` : "no disc",
             det ? ["ok", "found"] : ["bad", "none"])}
      ${c.circles === false ? idle(2, "Circles", "Ignored", "No check uses them")
             : !ran[2] ? idle(2, "Circles") : tile(2, "Circles", t2,
             !o ? "no circle the disc's size"
             : at === "concentric" && off == null ? "no inner circle"
             : ign.has("concentric") ? `edge <b>${Math.round(o[2] * 2)}</b> px · centres ignored`
             : off != null ? `centres <b>${off.toFixed(1)}</b> px apart · fails above ${offMax} px`
             : at === "chip" ? `edge <b>${Math.round(o[2] * 2)}</b> px · centres not checked`
             : "no inner circle",
             !o ? ["bad", "none"]
             : at === "concentric" && off == null ? ["bad", "no inner"]
             : ign.has("concentric") ? ["off", "ignored"]
             : off != null ? (off <= offMax ? ["ok", "concentric"] : ["bad", "off-centre"])
             : at === "chip" ? ["off", "not checked"] : ["bad", "no inner"])}
      ${c.chip === false ? idle(3, "Chip", "Not checked", "No chip check on this camera")
             : ign.has("chip") ? idle(3, "Chip", "Ignored", "The chip check is ignored this run")
             : !ran[3] ? idle(3, "Chip") : tile(3, "Chip", t3, `notch <b>${dent.toFixed(1)}</b> px · fails above ${dentMax} px`,
             chip ? ["bad", "chip"] : ["ok", "whole"])}
      ${!ran[4] ? idle(4, "Face anomaly")
             : cs === "off" && !f ? idle(4, "Face anomaly", "Ignored", "The face anomaly check is ignored this run")
             : tile(4, "Face anomaly", t4,
             cs === "off" ? "face anomaly check ignored" : `fail <b>${Math.round(num(c.cls.p) * 100)}%</b> · fails above ${Math.round(num(c.cls.max) * 100)}%`,
             cs === "off" ? ["off", "ignored"] : cs === "pass" ? ["ok", "pass"] : ["bad", "fail"])}
    </div></div>`;
}

const cap = s => { const t = String(s || ""); return t.charAt(0).toUpperCase() + t.slice(1); };

// A not-run tile's outline — what that tile would have drawn: the two
// circles, the edge, the face the classifier sees.
const PH_ICON = {
  2: `<svg viewBox="0 0 48 48"><circle cx="24" cy="24" r="17"/><circle cx="24" cy="24" r="12.5"/></svg>`,
  3: `<svg viewBox="0 0 48 48"><circle cx="24" cy="24" r="17" stroke-dasharray="7.5 3.2"/></svg>`,
  4: `<svg viewBox="0 0 48 48"><circle cx="24" cy="24" r="15" stroke-dasharray="3 3"/><circle cx="24" cy="24" r="2.5"/></svg>`,
};

// The card's element for these values — rebuilt only when the inspection
// changed, so its picture does not reload on every redraw.
function inspectionEl(c) {
  if (!c || typeof c !== "object") return null;
  const key = JSON.stringify(c);
  if (_insp.key !== key) {
    const t = document.createElement("template");
    t.innerHTML = inspectionHtml(c).trim();
    _insp = { key, el: t.content.firstElementChild };
  }
  return _insp.el;
}

function render(v) {
  _last = v || {};
  _root.innerHTML = html(_last);
  const slot = _root.querySelector("#insp-slot");
  const card = inspectionEl(_last.inspection);
  if (slot && card) slot.replaceWith(card);
  else if (slot) slot.remove();
  const ack = _root.querySelector(".modal .ack");
  if (ack) ack.addEventListener("click", () => {
    const n = _last.notice;
    if (n && typeof n === "object") _ack = noticeKey(n);
    render(_last);
  });
}

function html(v) {
  const p = num(v.progress);
  const pct = p == null ? null : Math.max(0, Math.min(100, Math.round(p)));
  return `
  ${noticeHtml(v.notice)}
  <div class="wrap">
   <div class="stack">
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
      <div class="col">
        <div class="card state">
          <h3>Run</h3>
          <h2>${esc(v.headline || "Waiting to start")}</h2>
          ${ringHtml(pct)}
        </div>
      </div>
      <div class="col">
        <div class="card">
          <h3>Last measurement</h3>
          ${readingHtml(v)}
        </div>
      </div>
    </div>
    <div id="insp-slot"></div>
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
    _api = api || null;
    render(api && api.values ? api.values : {});
  },

  update(values) {
    if (!_root) return;
    render(values || {});
  },
};
