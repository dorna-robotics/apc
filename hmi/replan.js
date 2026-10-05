// hmi/replan.js — apc's view of the Replan choice (launch.yaml `replan:`).
//
// Three steps of apc's own, like bna's — the platform keeps the frame, the
// stepper, Back / Next, Cancel, Remove & replan and the request:
//   Choose            the bench as the run-setup screen draws it
//                     (setup.js benchHtml / benchState, from the run's own
//                     parameters), each IN position badged with how many
//                     of its discs are still in the run. The choice is BY
//                     POSITION: a click on a stack crosses it out and every
//                     disc on offer in it leaves — the ones still stacked
//                     and the one of them that may be on the cup or the
//                     anode; again keeps it. Nothing is chosen one disc at
//                     a time. Finished discs are not part of a position's
//                     choice (removing one changes only its record).
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

import { CSS, benchHtml, benchState, discPlace, HOLDERS, posName } from "./setup.js";

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
  <div><h6>Take a stack out when</h6><ul>
    <li>the wrong discs are in it</li>
    <li>a disc is dropped, chipped or stuck</li>
    <li>the lab pulled the lot</li></ul></div>
  <div><h6>How</h6><ul>
    <li>Click a stack position — it is crossed out, every disc in it leaves</li>
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
  return p ? `in ${posName(LABEL[p.key], p.slot)}` : "off the bench";
}

function discName(i) {
  return `Disc ${Number(i) + 1}`;
}

// The positions: "in_1:3" → its live discs on offer (the choice), its
// finished ones (a count, never chosen), and what the operator calls it.
function positions() {
  const by = new Map();
  for (const [i, row] of _offer) {
    const p = discPlace(_st, i);
    if (!p) continue;
    const key = `${p.key}:${p.index}`;
    const e = by.get(key) || { key, holder: p.key, index: p.index, name: posName(LABEL[p.key], p.slot), live: [], done: [] };
    (row.done ? e.done : e.live).push(i);
    by.set(key, e);
  }
  for (const e of by.values()) { e.live.sort((a, b) => a - b); e.done.sort((a, b) => a - b); }
  return [...by.values()].sort((a, b) => a.key.localeCompare(b.key));
}
const chosen = () => positions().filter(e => _st.crossedPos.has(e.key) && e.live.length);
const chosenDiscs = () => chosen().flatMap(e => e.live);

// The live discs per IN position, for the bench's pills.
function offerCounts() {
  const counts = {};
  for (const h of HOLDERS) if (h.in) counts[h.key] = Array(_st.in[h.key].length).fill(0);
  for (const e of positions()) counts[e.holder][e.index] = e.live.length;
  return counts;
}

// A disc that is not in its stack any more: on the cup, the anode, at the
// camera — named, because the operator has to go and get it.
const inFlight = e => e.live.filter(i => ((_offer.get(i) || {}).holds || []).length);

function chooseHtml() {
  const legend = `<div class="legend"><div><i class="full"></i> Full stack</div><div><i class="empty"></i> Empty</div>
        <div><i class="badge"></i> Discs in the run</div>` +
    (_st.crossedPos.size ? `<div><i class="skip"></i> Leaves the run</div>` : "") + `</div>`;
  return WHY + `<div class="card"><h4>The bench — click a stack to take it out</h4><div class="inner">
      <div class="scroll"><div class="rack disc">${benchHtml(_st, { counts: offerCounts(), crossed: _st.crossedPos })}</div></div>
      ${legend}
    </div></div>`;
}

// Clear the bench — no checklist: how to get each disc out, with the
// button that does it (a component's own declared operator action,
// api.invoke). The one confirmation is the tick on Confirm.
function btn(label, comp, meth) {
  return `<button type="button" class="act" data-comp="${esc(comp)}" data-meth="${esc(meth)}">${esc(label)}</button>`;
}

function clearHtml() {
  const take = chosen().map(e => {
    const fl = inFlight(e).map(i => `${discName(i)} ${whereIs(i)}`);
    const stacked = e.live.length - fl.length;
    return `<li><b>${esc(e.name)}</b> — ${stacked ? `the whole stack, ${stacked} disc${stacked === 1 ? "" : "s"}` : "the stack is empty"}` +
      (fl.length ? `; ${esc(fl.join(", "))}` : "") + `</li>`;
  }).join("");
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
      <p>Lift each chosen stack off its position whole; a disc of it on the cup or the anode is named.</p>
      <ul class="take">${take}</ul>
    </section>
    <p class="note">The robot does not move until you press Resume.</p>
  </div></div>`;
}

// Confirm — apc's own: what leaves, the thing the operator guarantees,
// and why. The platform sends it (Remove & replan).
function confirmHtml() {
  const ch = chosen();
  const chips = ch.map(e => `<span class="cchip"><b>${esc(e.name)}</b> ${e.live.length} disc${e.live.length === 1 ? "" : "s"}</span>`).join("");
  const n = chosenDiscs().length;
  return `<div class="conf">
    <h6>Leaves the run — ${ch.length} stack${ch.length === 1 ? "" : "s"}, ${n} disc${n === 1 ? "" : "s"}</h6>
    <div class="cchips">${chips}</div>
    <div class="checks">
      <label class="check${_conf.bench ? " on" : ""}">
        <input type="checkbox" data-conf="bench" required${_conf.bench ? " checked" : ""}>
        <span>I have taken these off the bench <span class="muted">— the stacks, and any disc of them on the cup or the anode</span></span>
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
.hmi.apc .legend i.skip { background:var(--c-full); opacity:.45; position:relative; overflow:hidden; }
.hmi.apc .legend i.skip::after { content:""; position:absolute; inset:0;
  background:linear-gradient(to top right, transparent 44%, var(--muted,#888) 44%, var(--muted,#888) 56%, transparent 56%); }
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
    _st.crossedPos = new Set();      // "in_1:3" — the stack positions chosen
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
      // a stack position — on the bench (a well with discs in the run) or
      // its chip below — toggles as one: every disc in it
      const well = e.target.closest(".well.pos[data-h]");
      const key = well ? `${well.dataset.h}:${well.dataset.i}` : null;
      if (!key || !positions().some(p => p.key === key && p.live.length)) return;
      _st.crossedPos.has(key) ? _st.crossedPos.delete(key) : _st.crossedPos.add(key);
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
    if (!_st || !chosenDiscs().length) return false;
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

  // Every disc on offer in the chosen stacks — the offer's own items.
  value() {
    return chosenDiscs().map(i => _offer.get(i).item);
  },
};
