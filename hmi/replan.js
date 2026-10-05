// hmi/replan.js — apc's view of the Replan choice (launch.yaml `replan:`).
//
// Three steps of apc's own, like bna's — the platform keeps the frame, the
// stepper, Back / Next, Cancel, Remove & replan and the request:
//   Choose            the bench as the run-setup screen draws it
//                     (setup.js benchHtml / benchState, from the run's own
//                     parameters), each IN position badged with how many
//                     of its discs are on offer, and the discs themselves
//                     as chips — Disc 12 · In 1 A3 · on the anode. A click
//                     crosses a disc out; again keeps it.
//   Clear the bench   how to get each chosen disc out — suction off
//                     (the core's Disable Tool: the gripper's output-off
//                     config), release the cathode if it is clamped,
//                     motors off, lift the arm clear by hand, take the
//                     disc off the cup or the anode — with the button for
//                     each thing the platform can do (the components' own
//                     declared operator actions, api.invoke).
//   Confirm           what leaves, the tick that they are off the bench,
//                     the reason.
//
// A VIEW only (bt-framework-guide §8.6): value() = the chosen items (the
// offer's own `item`s, the disc index); the platform keeps the request to
// the runtime. A disc is numbered as setup() in actions.py numbers it —
// setup.js discPlace() puts it back on its stack position.

import { CSS, benchHtml, benchState, discPlace, HOLDERS } from "./setup.js";

let _api = null;
let _wrap = null;
let _st = null;
let _step = "choose";
let _offer = new Map();          // disc index → the platform's offer row
let _conf = { bench: false, reason: "" };   // the Confirm step

const esc = s => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

const LABEL = Object.fromEntries(HOLDERS.map(h => [h.key, h.label]));
const CYL = "rotating_cylinder_mkb1630_1";     // the cathode's cylinder (CathodeDown/Up in actions.py)

// Why an operator is here and what a click does — short bullets.
const WHY = `<div class="why">
  <div><h6>Take a disc out when</h6><ul>
    <li>it is dropped, chipped or bent</li>
    <li>the wrong disc is in the stack</li>
    <li>it is stuck on the cup or the anode</li>
    <li>the lab pulled it</li></ul></div>
  <div><h6>How</h6><ul>
    <li>Click a disc below — it is crossed out</li>
    <li>Click again to keep it</li>
    <li>Next: clear the bench, then give the reason</li></ul></div>
</div>`;

// Where a disc is right now, in the operator's words: what it holds in
// the plan (the offer's `holds` — the hand, the anode, the feed) else
// its stack position.
function whereIs(i) {
  const row = _offer.get(i);
  const holds = (row && row.holds) || [];
  if (holds.some(h => /anode/i.test(h))) return "on the anode";
  if (holds.some(h => /hand/i.test(h))) return "in the suction cup";
  if (holds.some(h => /feed/i.test(h))) return "at the camera station";
  const p = discPlace(_st, i);
  return p ? `in ${LABEL[p.key]} ${p.slot}` : "off the bench";
}

function discName(i) {
  return `Disc ${Number(i) + 1}`;
}

// The discs on offer, per IN position, for the bench's badges.
function offerCounts() {
  const counts = {};
  for (const h of HOLDERS) if (h.in) counts[h.key] = Array(_st.in[h.key].length).fill(0);
  for (const [i, row] of _offer) {
    if (row.done) continue;
    const p = discPlace(_st, i);
    if (p) counts[p.key][p.index] += 1;
  }
  return counts;
}

function chip(i) {
  const row = _offer.get(i);
  const x = _st.crossed.has(i);
  return `<button type="button" class="chip${x ? " x" : ""}" data-i="${i}" aria-pressed="${x}">` +
    `<b>${esc(discName(i))}</b><span>${esc(whereIs(i))}</span>` +
    (row && row.with && row.with.length ? `<span class="with">+ ${esc(row.with.join(", "))}</span>` : "") +
    `</button>`;
}

function chooseHtml() {
  const live = [..._offer.keys()].filter(i => !_offer.get(i).done).sort((a, b) => a - b);
  const done = [..._offer.keys()].filter(i => _offer.get(i).done).sort((a, b) => a - b);
  const bench = `<div class="card"><h4>The bench</h4><div class="inner">
      <div class="scroll"><div class="rack disc">${benchHtml(_st, { counts: offerCounts() })}</div></div>
      <div class="legend"><div><i class="full"></i> Full stack</div><div><i class="empty"></i> Empty</div>
        <div><i class="badge"></i> Discs on offer</div></div>
    </div></div>`;
  const chips = live.length
    ? `<div class="chips">${live.map(chip).join("")}</div>`
    : `<p class="none">Nothing left in the run to remove.</p>`;
  const fin = done.length ? `
    <details class="done"><summary>Finished — ${done.length} disc${done.length === 1 ? "" : "s"}
      <span class="muted">removing one changes only its record</span></summary>
      <div class="chips">${done.map(chip).join("")}</div></details>` : "";
  return WHY + bench + `<div class="card"><h4>Discs in the run — click to take out</h4><div class="inner">
      ${chips}${fin}</div></div>`;
}

// Clear the bench — no checklist: how to get each disc out, with the
// button that does it (a component's own declared operator action,
// api.invoke). The one confirmation is the tick on Confirm.
function btn(label, comp, meth) {
  return `<button type="button" class="act" data-comp="${esc(comp)}" data-meth="${esc(meth)}">${esc(label)}</button>`;
}

function clearHtml() {
  const take = [..._st.crossed].sort((a, b) => a - b)
    .map(i => `<li><b>${esc(discName(i))}</b> — ${esc(whereIs(i))}</li>`).join("");
  return `<div class="clear"><div class="how">
    <section>
      <h6><span class="n">1</span>Release what holds it</h6>
      <p>A disc on the suction cup: turn the suction off — the same button as Disable Tool in
         Operator Controls — and take the disc. The robot turns the suction on again when it next picks.</p>
      <div class="acts">${btn("Suction off", "core", "tool_disable")}${btn("Suction on", "core", "tool_enable")}</div>
      <p>A disc clamped under the cathode: release the cathode first. Clamp it again only if a disc
         that stays is under it.</p>
      <div class="acts">${btn("Release cathode", CYL, "disable")}${btn("Clamp cathode", CYL, "enable")}</div>
    </section>
    <section>
      <h6><span class="n">2</span>Lift the arm clear</h6>
      <p>Turn the motors off and lift the arm by hand straight up, about 100 mm above where it
         works — clear of every holder, the camera station and the anode. Resume turns the motors
         back on; the button is there if you want them on sooner.</p>
      <div class="acts">${btn("Motors off", "core", "motor_disable")}${btn("Motors on", "core", "motor_enable")}</div>
      <p>If the robot is in alarm (red), fix the cause, then clear it — the same button as Operator Controls.</p>
      <div class="acts">${btn("Disable Alarm", "core", "alarm_disable")}</div>
    </section>
    <section>
      <h6><span class="n">3</span>Take these off the bench</h6>
      <p>A disc still in its stack: take the top disc of that position.</p>
      <ul class="take">${take}</ul>
    </section>
    <p class="note">The robot does not move until you press Resume.</p>
  </div></div>`;
}

// Confirm — apc's own: what leaves, the thing the operator guarantees,
// and why. The platform sends it (Remove & replan).
function confirmHtml() {
  const chips = [..._st.crossed].sort((a, b) => a - b)
    .map(i => `<span class="cchip"><b>${esc(discName(i))}</b> ${esc(whereIs(i))}</span>`).join("");
  const n = _st.crossed.size;
  return `<div class="conf">
    <h6>Leaves the run — ${n} disc${n === 1 ? "" : "s"}</h6>
    <div class="cchips">${chips}</div>
    <div class="checks">
      <label class="check${_conf.bench ? " on" : ""}">
        <input type="checkbox" data-conf="bench" required${_conf.bench ? " checked" : ""}>
        <span>I have taken these off the bench <span class="muted">— off the cup, the anode and the stack</span></span>
      </label>
    </div>
    <p class="resume-note">On Resume the motors turn on and the robot moves — keep clear of the arm.</p>
    <label class="reason"><span>Reason</span>
      <input type="text" id="reason" required autocomplete="off" value="${esc(_conf.reason)}"
             placeholder="Why they leave — e.g. dropped, chipped, stuck"></label>
  </div>`;
}

function render() {
  _wrap.innerHTML = _step === "clear" ? clearHtml()
    : _step === "confirm" ? confirmHtml()
    : chooseHtml();
}

export default {
  css: CSS + `
.hmi.apc .why { display:grid; grid-template-columns:1fr 1fr; gap:var(--space-5);
  margin:0 0 var(--space-4); padding:var(--space-4) var(--space-5);
  border:1px solid var(--border2); border-radius:var(--radius-lg); background:var(--surface); }
.hmi.apc .why h6 { margin:0 0 var(--space-2); font-size:var(--text-md); font-weight:700; color:var(--text); }
.hmi.apc .why ul { margin:0; padding-left:1.2em; display:flex; flex-direction:column; gap:4px;
  font-size:var(--text-md); line-height:1.4; color:var(--text); }
.hmi.apc .why li::marker { color:var(--muted); }
@media (max-width:720px) { .hmi.apc .why { grid-template-columns:1fr; } }
.hmi.apc .card + .card { margin-top:var(--space-4); }
.hmi.apc .legend i.badge { background:var(--accent); border-color:var(--accent); border-radius:999px; }
/* the discs on offer: one chip each — name, where it is, what leaves with it */
.hmi.apc .chips { display:flex; flex-wrap:wrap; gap:var(--space-2); width:100%; }
.hmi.apc .chip { display:inline-flex; flex-direction:column; align-items:flex-start; gap:2px;
  padding:8px 14px; border-radius:var(--radius-md); border:1px solid var(--border);
  background:var(--surface); color:var(--text); text-align:left; line-height:1.2; cursor:pointer;
  transition:border-color var(--motion-fast) var(--ease), background var(--motion-fast) var(--ease); }
.hmi.apc .chip:hover { border-color:var(--accent); }
.hmi.apc .chip b { font-size:var(--text-md); font-weight:700; }
.hmi.apc .chip span { font-size:var(--text-sm); color:var(--muted); }
.hmi.apc .chip .with { color:var(--text); }
.hmi.apc .chip.x { border-color:var(--red); background:color-mix(in srgb, var(--red) 8%, var(--surface)); }
.hmi.apc .chip.x b { text-decoration:line-through; color:var(--red); }
.hmi.apc .chip.x b::before { content:"✕ "; text-decoration:none; display:inline-block; }
.hmi.apc .none { margin:0; color:var(--muted); font-size:var(--text-md); }
.hmi.apc .done { width:100%; }
.hmi.apc .done summary { cursor:pointer; font-size:var(--text-md); font-weight:600; color:var(--text);
  padding:var(--space-2) 0; }
.hmi.apc .done summary .muted { font-weight:400; color:var(--muted); margin-left:var(--space-2); }
.hmi.apc .done .chips { margin-top:var(--space-2); }
.hmi.apc .clear { padding:var(--space-5); border:1px solid var(--border2); border-radius:var(--radius-lg);
  background:var(--surface); }
.hmi.apc .how { display:flex; flex-direction:column; gap:var(--space-5); }
.hmi.apc .how section { display:flex; flex-direction:column; gap:var(--space-2); }
.hmi.apc .how h6 { display:flex; align-items:center; gap:var(--space-2); margin:0;
  font-size:var(--text-md); font-weight:700; color:var(--text); }
.hmi.apc .how .n { width:24px; height:24px; border-radius:50%; display:inline-flex; align-items:center;
  justify-content:center; font-size:var(--text-xs); font-weight:700; background:var(--accent); color:#fff; }
.hmi.apc .how p { margin:0; font-size:var(--text-md); line-height:1.45; color:var(--text); }
.hmi.apc .how .acts { display:flex; gap:var(--space-2); flex-wrap:wrap; }
.hmi.apc .how .act { white-space:nowrap; font-weight:600; border:1px solid var(--border);
  background:var(--surface); border-radius:999px; padding:8px 16px; }
.hmi.apc .how .act:hover { border-color:var(--accent); color:var(--accent); background:var(--surface); }
.hmi.apc .how .take { margin:0; padding-left:1.2em; display:flex; flex-direction:column; gap:4px;
  font-size:var(--text-md); line-height:1.4; }
.hmi.apc .how .note { color:var(--muted); font-size:var(--text-sm); }
.hmi.apc .conf { display:flex; flex-direction:column; gap:var(--space-5); padding:var(--space-5);
  border:1px solid var(--border2); border-radius:var(--radius-lg); background:var(--surface); }
.hmi.apc .conf h6 { margin:0; font-size:var(--text-lg); font-weight:700; color:var(--text); }
.hmi.apc .conf .cchips { display:flex; flex-wrap:wrap; gap:var(--space-2); margin-top:calc(-1 * var(--space-3)); }
.hmi.apc .conf .cchip { padding:6px 14px; border-radius:999px; font-size:var(--text-md);
  background:var(--surface2); border:1px solid var(--border); color:var(--muted); }
.hmi.apc .conf .cchip b { color:var(--text); }
.hmi.apc .conf .resume-note { margin:calc(-1 * var(--space-2)) 0 0; font-size:var(--text-md); color:var(--muted); }
.hmi.apc .conf .reason { display:flex; flex-direction:column; gap:var(--space-2);
  font-size:var(--text-md); font-weight:600; color:var(--text); }
.hmi.apc .conf .reason input { width:100%; min-height:52px; padding:0 var(--space-4); box-sizing:border-box;
  font:inherit; font-weight:400; color:var(--text); background:var(--surface);
  border:1px solid var(--border); border-radius:var(--radius-md); }
.hmi.apc .conf .reason input:hover, .hmi.apc .conf .reason input:focus { border-color:var(--accent); outline:none; }
`,

  // Every step is apc's, Confirm included (ownsConfirm): the platform
  // keeps the frame, the stepper, Cancel / Remove & replan and the
  // request — it reads reason() and asks validate() before sending.
  steps: [{ key: "choose", title: "Choose" },
          { key: "clear", title: "Clear the bench" },
          { key: "confirm", title: "Confirm" }],
  ownsConfirm: true,

  mount(root, api) {
    _api = api;
    _offer = new Map((api.items || []).map(it => [Number(it.item), it]));
    _st = benchState(api);
    _st.crossed = new Set();
    _step = "choose";
    _conf = { bench: false, reason: "" };

    root.querySelectorAll(":scope > .hmi").forEach(n => n.remove());
    _wrap = document.createElement("div");
    _wrap.className = "hmi apc";
    root.appendChild(_wrap);
    render();

    _wrap.addEventListener("click", e => {
      const act = e.target.closest("button.act");
      if (act) {
        _api.invoke(act.dataset.comp, act.dataset.meth);
        return;
      }
      if (_step !== "choose") return;
      const el = e.target.closest(".chip[data-i]");
      if (!el) return;
      const i = Number(el.dataset.i);
      if (!_offer.has(i)) return;
      _st.crossed.has(i) ? _st.crossed.delete(i) : _st.crossed.add(i);
      _conf.bench = false;     // a different choice: confirm the bench again
      render();
      _api.changed();
    });
    // Confirm: the tick and the reason update in place — a re-render
    // while typing would drop the cursor.
    _wrap.addEventListener("change", e => {
      const k = e.target?.dataset?.conf;
      if (!k) return;
      _conf[k] = e.target.checked;
      e.target.closest(".check").classList.toggle("on", e.target.checked);
      _api.changed();
    });
    _wrap.addEventListener("input", e => {
      if (e.target?.id !== "reason") return;
      _conf.reason = e.target.value;
      _api.changed();
    });
  },

  show(step) {
    _step = step;
    render();
  },

  ready(step) {
    if (!_st || !_st.crossed.size) return false;
    if (step === "confirm") return _conf.bench && !!_conf.reason.trim();
    return true;
  },

  reason() {
    return _conf.reason.trim();
  },

  // Before the platform sends: what is missing, pointed at with the
  // browser's own required bubble ("" = ready).
  validate() {
    if (_step !== "confirm") return "Confirm first";
    for (const el of [..._wrap.querySelectorAll("[data-conf]"), _wrap.querySelector("#reason")]) {
      const empty = el.type === "checkbox" ? !el.checked : !el.value.trim();
      if (empty) {
        if (el.type !== "checkbox") el.value = "";
        el.reportValidity();
        return "incomplete";
      }
    }
    return "";
  },

  value() {
    return [..._st.crossed].map(i => _offer.get(i).item);
  },
};
