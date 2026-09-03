// hmi/setup.js — apc run setup: mark which IN stacks are loaded, see the bench.
//
// Hosted in a shadow root inside the Parameters modal. The platform owns
// the modal chrome, the Set/Start buttons, and it validates whatever
// value() returns against hmi/default.j2; validate() only ADDS a message.
// Contract: {css, mount(root, api), value(), validate()} — HMI_GUIDE §5.
//
// Built on /orchestrator/hmi-kit/kit.js like tph: the kit carries the
// shared language (cards, buttons, messages, wells, the frozen state);
// only the bench drawing is project CSS.
//
// What it draws, top to bottom — the five disc holders on fixture plate 4,
// column 5, in the order the operator sees them with the IN holders
// nearest (scene/layout.j2):
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
// count of a full stack) for a full position, 0 for an empty one. The
// Pass / Fail rows carry no input; they are on this page so the operator
// sees the whole bench and confirms those holders are empty before Start
// (a bench check, never saved with the parameters).

import { kitCss, wellCss, esc } from "/orchestrator/hmi-kit/kit.js";

// ── bench geometry — must match actions.py ─────────────────────────────
const SLOTS_N = 7;                                          // SLOTS: A1..A7
const SLOTS   = Array.from({ length: SLOTS_N }, (_, i) => `A${i + 1}`);
const IN_KEYS = ["in_1", "in_2"];                           // the kwargs, consumed in this order
// What a FULL position is written as: MAX_PER_SLOT in actions.py, the
// discs in a full stack. Written to the run record, never shown here.
const FULL    = 255;

// The holders, TOP TO BOTTOM as drawn. `in` rows take clicks; the rest
// are placeholders the robot fills during the run.
const HOLDERS = [
  { key: "bad_1",  label: "Fail",   in: false },
  { key: "good_2", label: "Pass 2", in: false },
  { key: "good_1", label: "Pass 1", in: false },
  { key: "in_2",   label: "In 2",   in: true  },
  { key: "in_1",   label: "In 1",   in: true  },
];
const IN_HOLDERS  = HOLDERS.filter(h => h.in);
const OUT_HOLDERS = HOLDERS.filter(h => !h.in);
const OUT_NAMES   = OUT_HOLDERS.slice().reverse().map(h => h.label).join(", ");   // "Pass 1, Pass 2, Fail"
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
const CSS = kitCss + wellCss({
  full: "#b9c6d2",     // a loaded IN stack — bare metal
  good: "#a4d89c",     // a passed disc (pendant)
  bad:  "#e8a79c",     // a failed disc (pendant)
}) + `
/* everything in a card sits centred, the bench included */
.hmi.apc .card .inner { align-items:center; }
.hmi.apc .card > h4 { text-align:center; }
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
/* row labels */
.hmi .rack.disc .rlab { justify-self:start; font-size:11px; font-weight:700; }
/* the OUT / IN groups are two different things: a hairline between them
   (width:100% — the kit centres grid items, which would shrink it to 0) */
.hmi .rack.disc .sep { grid-column:1 / -1; width:100%; height:1px;
  background:var(--border); margin:3px 0; }
/* the bench check */
.hmi .check { display:flex; gap:8px; align-items:flex-start; cursor:pointer; font-size:12.5px; }
.hmi .check input { width:auto; margin-top:2px; }
.hmi .legend i.full { background:var(--c-full); }
.hmi .legend i.empty { background:var(--surface); border-color:var(--border);
  border-style:dashed; }
.hmi .legend i.out { background:var(--surface); border-color:var(--border);
  border-style:dashed; opacity:.5; }
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

// ── validation — always live ───────────────────────────────────────────
// ONE message box, always one line, so a click never changes the
// screen's height: the modal is centred vertically by the platform, and
// a taller screen would shift the bench under the operator's cursor.
function check(st) {
  const errs = [];
  const full = [];
  for (const h of IN_HOLDERS) {
    st.in[h.key].forEach((f, i) => { if (f) full.push({ h, i }); });
  }
  if (!full.length) errs.push("No discs loaded — click the In stacks that are full.");
  return { errs, full };
}

function msgHtml(V) {
  return V.errs.length
    ? `<div class="msg m-bad"><b>Blocked</b><div>${esc(V.errs[0])}</div></div>`
    : `<div class="msg m-good"><b>Ready</b><div></div></div>`;
}

// ── render ─────────────────────────────────────────────────────────────
// One grid for the whole bench. Axis across the top, A1 on the left; one
// row per holder in HOLDERS order; a hairline between the OUT and IN
// groups. Every IN position is a toggle carrying its holder key and
// index (data-h / data-i); OUT positions carry nothing.
function benchHtml(st) {
  let h = `<div class="ax"></div>` + SLOTS.map(s => `<div class="ax">${s}</div>`).join("");
  let prevIn = null;
  for (const hd of HOLDERS) {
    if (prevIn !== null && prevIn !== hd.in) h += `<div class="sep"></div>`;
    prevIn = hd.in;
    h += `<div class="rlab">${esc(hd.label)}</div>`;
    for (let i = 0; i < SLOTS_N; i++) {
      const s = SLOTS[i];
      if (!hd.in) {
        h += `<div class="well out" title="${esc(`${hd.label} · ${s} · empty — the robot fills it during the run`)}"><b>${s}</b><i>empty</i></div>`;
      } else if (st.in[hd.key][i]) {
        h += `<div class="well k-full toggle" data-h="${hd.key}" data-i="${i}" ` +
          `title="${esc(`${hd.label} · ${s} · full · click to mark empty`)}"><b>${s}</b><i>full</i></div>`;
      } else {
        h += `<div class="well empty toggle" data-h="${hd.key}" data-i="${i}" ` +
          `title="${esc(`${hd.label} · ${s} · empty · click to mark full`)}"><b>${s}</b><i>empty</i></div>`;
      }
    }
  }
  return h;
}

function bodyHtml(st) {
  const V = check(st);
  return `
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

    <div class="card">
      <h4>Bench check</h4>
      <div class="inner">
        <label class="check"><input type="checkbox" id="outok"${st.outOk ? " checked" : ""}>
          <span>${OUT_NAMES} are empty.</span></label>
      </div>
    </div>
  </div>`;
}

// Draw into a wrapper the screen owns, NEVER into the shadow root itself:
// the platform's <style> is a sibling of the wrapper in that root.
function render(wrap, st) {
  wrap.innerHTML = bodyHtml(st);
  const q = sel => wrap.querySelector(sel);
  const frozen = () => wrap.dataset.frozen === "1";

  q("#outok").onchange = e => {
    if (frozen()) { e.target.checked = st.outOk; return; }
    st.outOk = !!e.target.checked;
    render(wrap, st);
  };
  q("#allfull").onclick = () => {
    if (frozen()) return;
    for (const k of IN_KEYS) st.in[k] = Array(SLOTS_N).fill(1);
    render(wrap, st);
  };
  q("#allempty").onclick = () => {
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

// ── fit the modal to this screen ──────────────────────────────────────
// The Parameters modal's width is PLATFORM CSS (540px, or 1120px with
// .modal-wide) — neither is this screen's width, so the bench either
// wraps or floats in dead space. Same liberty bna and tph take: measure
// the bench and ask the modal to match. Every lookup is optional — if
// the platform renames .modal or the measurement is 0, this quietly does
// nothing.
//
// THE PLATFORM CACHES THIS SCREEN ACROSS OPENS (kwargs.js
// renderKwargsForm's render-signature cache): re-opening the modal with
// the same values does NOT call mount() again. So the fit cannot live in
// mount alone — one observer per modal, kept for the life of the page,
// re-fits on every show and restores the platform's width on every hide,
// so nothing leaks into another screen shown in the same modal.
const MODAL_CHROME_PX = 44;   // .modal-body padding (20+20) + borders
const CARD_PAD_PX     = 30;   // .card padding (14+14) + border
const SLACK_PX        = 36;   // the scroll padding + a margin, so nothing
                              // the cursor does can widen the content

function fitModalTo(root, wrap) {
  const holder = root && root.host;
  if (!holder || typeof holder.closest !== "function") return;
  const modal = holder.closest(".modal");
  if (!modal || !modal.style) return;
  // The latest screen in this modal — a remount (Reset All, new values)
  // replaces the wrapper, and the observer below must measure the live one.
  modal._apcFit = { root, wrap };
  // Measure the BENCH, not the wrapper: a squeezed wrapper reports the
  // squeezed width, and sizing to that would lock the squeeze in.
  const bench = typeof wrap.querySelector === "function" && wrap.querySelector(".rack.disc");
  const need = bench ? Math.ceil(bench.scrollWidth || 0) + SLACK_PX + CARD_PAD_PX + MODAL_CHROME_PX : 0;
  if (!(need > SLACK_PX + CARD_PAD_PX + MODAL_CHROME_PX)) return;   // no layout yet — leave it alone

  if (modal.dataset.apcSaved !== "1") {          // the platform's own width, once
    modal.dataset.apcSaved = "1";
    modal.dataset.apcPrevW = modal.style.width || "";
    modal.dataset.apcPrevM = modal.style.maxWidth || "";
  }
  modal.style.width = `min(${need}px, calc(100vw - 32px))`;
  modal.style.maxWidth = "none";

  const overlay = modal.closest(".modal-overlay");
  if (overlay && typeof MutationObserver === "function" && !modal._apcFitObs) {
    const obs = new MutationObserver(() => {
      if (overlay.classList.contains("show")) {
        const live = modal._apcFit;
        if (live) fitModalTo(live.root, live.wrap);
      } else {
        modal.style.width = modal.dataset.apcPrevW || "";
        modal.style.maxWidth = modal.dataset.apcPrevM || "";
      }
    });
    obs.observe(overlay, { attributes: true, attributeFilter: ["class"] });
    modal._apcFitObs = obs;
  }
}

// ── the platform's setup-screen contract ──────────────────────────────
// {css, mount(root, api), value(), validate()} — HMI_GUIDE §5.
let _st = null;

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

export default {
  css: CSS,

  mount(root, api) {
    // outOk is a BENCH confirmation — never restored, never saved: the
    // operator looks at the holders every time (HMI_GUIDE §4).
    _st = { in: {}, outOk: false };
    for (const k of IN_KEYS) _st.in[k] = flags(initial(api, k, []));

    // One wrapper, created once, re-rendered forever. The kit's rules
    // scope under .hmi; data-frozen on the wrapper is what its frozen
    // styling — and this screen's click guards — key off.
    root.querySelectorAll(":scope > .hmi").forEach(n => n.remove());
    const wrap = document.createElement("div");
    wrap.className = "hmi apc";
    wrap.dataset.frozen = api && api.frozen ? "1" : "0";
    root.appendChild(wrap);
    render(wrap, _st);
    // After layout, not during it: scrollWidth is 0 until the browser
    // has laid the bench out — and on a RE-open the module is already
    // cached, so mount() runs before the overlay is even shown (display:
    // none, width 0). One frame is not enough then; watch the bench and
    // fit the moment it has a size. Falls back to a frame when there is
    // no ResizeObserver.
    const bench = wrap.querySelector(".rack.disc");
    if (bench && typeof ResizeObserver === "function") {
      const ro = new ResizeObserver(() => {
        if (!(bench.scrollWidth > 0)) return;
        fitModalTo(root, wrap);
        ro.disconnect();
      });
      ro.observe(bench);
    } else if (typeof requestAnimationFrame === "function") {
      requestAnimationFrame(() => fitModalTo(root, wrap));
    }
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
    // an empty array is truthy in JS.
    if (!_st) return "";
    const errs = check(_st).errs;
    if (!errs.length)
      return _st.outOk ? "" : `Confirm ${OUT_NAMES} are empty before starting.`;
    return errs.length === 1 ? errs[0] : `${errs.length} problems, first: ${errs[0]}`;
  },
};
