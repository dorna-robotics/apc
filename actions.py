"""apc protocol — Start → [per-disc pipeline] ×(inventory total) → Park.

IN inventory comes from hmi/default.j2 through the setup screen: two lists
of 7 (``in_1``, ``in_2``), index i = anchor A<i+1> of that holder, 0 for an
empty position and any positive value (the screen writes the stack size)
for a FULL stack. The operator only ever says full or empty; the count is
``stack_size`` in hmi/default.j2 — the one place it is written — read
here into MAX_PER_SLOT by setup().
Discs are consumed TOP-of-stack first, A1→A7, in_1 until empty, then
in_2. Each disc appears in the scene the moment it's about to be picked
(create-on-demand, one at a time via feed_free) at its stack position
(z = depth × Z_STEP) — the racks hold at most one transient disc while
the counts still come from the configured inventory.

Each disc goes through a SPLIT chain of small BT actions, threaded by
facts (the BT moves action→action as each eff is asserted). Per disc i:

   1. Create        spawn the disc at its inventory position (top of the
                    remaining stack at its in-holder anchor).
   2. Pick          suction-pick it off the IN stack and lift straight
                    out (Pick.PRM).
   3. Present       carry it to the vertical inspection station.
   4. InspectBottom station camera: DETECT the disc (vision/disc_od.yaml), then
                    CLASSIFY the same view cropped to its box + CLS_ROI_OFFSET
                    px (vision/disc_cls.yaml). Two outcomes:
                      pass  → on to the anode;
                      fail  → Reject: straight to the fail column. A disc
                              the detector does not see is a fail too —
                              the suction is never released mid-way, the
                              hand goes to the fail column as if it held
                              one.
                    TWO empty picks IN A ROW from one IN position — seen
                    by either camera — mean that stack has run out: its
                    remaining discs are VOID (done, never created) and
                    the run moves to the next position. One empty is a
                    fail, not a verdict on the stack (a detector can
                    miss a real disc). EMPTY_RUN_ENDS_COLUMN.
   5. Reject        (fail only) drop the held disc into the fail column.
   6. PlaceAnode    place the disc on the anode's "place" anchor, stand
                    where the robot camera sees it.
   7. InspectTop    robot camera: the same detect-then-classify. pass → the
                    measurement; fail → skip it, PickAnode takes the disc
                    straight to the fail column. No disc seen on the anode
                    is a fail too, never a pause.
   8. CathodeDown   drive the rotating cylinder down so the cathode contacts
                    the disc (clamped anode ↔ cathode).
   9. Measure       read the multimeter capacitance → record it for the disc.
  10. CathodeUp     retract the cylinder (cathode up).
  11. PickAnode     suction-pick the disc back off the anode.
  12. CheckAnode    robot camera on the anode again: the detector alone.
                    clear → the hand has the disc; occupied → a disc is
                    still on the anode, so whatever the hand holds is
                    a STUCK fail and the anode must be cleared before the
                    next placement.
  13. Sort          drop it into an OUT holder by its LANE (LANES): a
                    camera fail → the fail holder's A6–A7; stuck → A5; a
                    reading outside C_MIN..C_MAX → A1–A4; a pass → the
                    pass holders, out_good_1 then _2. Ordered fill.

And ONE run-level action, ClearAnode, planned wherever the anode is not
known clear — right after Start, and after any CheckAnode that saw a
disc left behind: image the anode; nothing there → ``anode_clear``;
something there → go to the anode's ``blow`` anchor, blow, and return
with no fact changed, so the planner selects it again — one action run
per attempt, no loop. After MAX_BLOWS attempts in a row it pauses for
the operator to clear the anode and Resume. Pick and PlaceAnode need
``anode_clear`` (PlaceAnode takes it away): no disc is taken while the
anode holds one — the blow-off needs the hand empty, so a disc picked
first would leave the run with nowhere to go.

Then Park once every disc is DONE — sorted, rejected or void. ``done`` is
the closure fact every way out asserts. ``ROUTE`` at the bottom of this
file is that order, and being listed there is what makes an action part
of the run (bt-framework-guide §13). A FLAT project: discs are strictly
serial through the bench (feed / hand / anode each hold one) and a sorted
disc is terminal, so there is no line the batch regroups at and no
``route: phases.py`` — the ROUTE lives here.

AUDIT. One ``rt.record`` row per disc (project-guide §3), keyed
``disc <n>``: seeded by Create with where it came from, ``visual_bottom``
/ ``visual_top`` by the two inspections, ``c_f`` by Measure on a valid
reading, ``result`` / ``out`` / ``reason`` by the drop, ``status`` derived
from the facts at Park. The run's ``records/<start>/records.csv``
(launch.yaml ``records:``) is the client's sheet.

The DROP is ORDERED by a fill counter (ctx.meta["filled"]) along a LANE —
an ordered list of (holder, slot) positions, one lane per verdict (LANES):
  * pass          out_good_1 A1→A7, then out_good_2 A1→A7
  * fail_measure  out_bad_1 A1→A4 — the reading outside the window
  * fail_stuck    out_bad_1 A5    — the anode still showed a disc after the pick
  * fail_visual   out_bad_1 A6→A7 — a camera said fail, or saw no disc
  * within a position: z starts at 0 and steps by Z_STEP per disc, up to
    MAX_PER_SLOT discs, then the lane's next position.
Every sorted disc is DELETED right after place() — sorted discs are
terminal, so nothing accumulates in the scene. Start additionally sweeps
any disc_* component left over from a previous run that was killed or
stopped mid-cycle, so the out racks can never show stale discs.

BT philosophy: actions are small; pre/eff carry the per-disc state machine
forward. Suction pick/place follow the runtime example (tool_tcp_z_offset
on pick, gravity_offset on place).

NOTE: no tool swapping — the suction gripper is mounted on the robot
(no rack), so NO action sets ``tool`` (leave it unset everywhere).

PENDANT. Every action publishes the operator-facing picture through
rt.op() (see _publish): a one-line headline in the operator's words, the
five holders as per-position STATES and COUNTS (discs left in each IN
position, discs in each OUT position), the pass/fail tally and the last
reading. hmi/pendant.js binds to exactly these keys; change them
together.
"""

from __future__ import annotations

import os

from workspace.bt import Action, predicate


# ── Per-disc facts (the action chain) ─────────────────────────────────
started      = predicate("started")
created      = predicate("created")      # disc spawned at an in holder
picked       = predicate("picked")       # disc in the gripper (off the in stack)
presented    = predicate("presented")    # disc presented at the station
inspected    = predicate("inspected")    # station camera: disc seen and PASSED
bottom_failed = predicate("bottom_failed")  # station camera: disc seen and FAILED
on_anode     = predicate("on_anode")     # disc placed on the anode
anode_inspected = predicate("anode_inspected")  # robot camera: PASSED on the anode
top_failed   = predicate("top_failed")   # robot camera: FAILED on the anode
cathode_down = predicate("cathode_down") # cylinder driven down (cathode contact)
measured     = predicate("measured")     # capacitance read for this disc
cathode_up   = predicate("cathode_up")   # cylinder retracted
off_anode    = predicate("off_anode")    # disc re-gripped off the anode
anode_checked = predicate("anode_checked")  # the anode was imaged after this disc's pick
anode_stuck  = predicate("anode_stuck")  # …and a disc was still on it: the hand's disc is a STUCK fail
sorted_      = predicate("sorted")       # disc dropped into an out holder
void         = predicate("void")         # never existed: its IN position ran out before its turn
done         = predicate("done")         # THE closure fact — sorted, good, bad or void
parked       = predicate("parked")

# ── Single-occupancy resources (capacity-1, no args) ──────────────────
# Three shared slots, each holding ONE disc at a time. Without these the
# planner runs actions in parallel across discs — creating several discs
# up front (they pile on the feed), or two discs on the anode, or picking
# while the cathode is down. Each fact is consumed (-fact) when its slot
# fills and restored (+fact) when it empties, forcing strictly
# one-disc-at-a-time:
#   feed_free  — one disc at a time between its Create and the cameras'
#                verdict on it. Restored by the inspections, NOT by Pick:
#                the next Create waits until the cameras have settled
#                what the pick took, so a position found empty can void
#                its remaining discs before any of them is spawned.
#   hand_empty — the gripper holds one disc.
#   anode_free — the anode/cathode station processes one disc.
# See project-guide §8 "Single-occupancy resources".
#
# capacity=True: shared mutual-exclusion facts, not causal ones —
# see dsl.py's "Capacity facts" section. Without the flag the
# scheduler ties precedence to whichever item's action the plan's
# own linearization set the fact last, serializing items that
# could otherwise be batched by tool.
feed_free   = predicate("feed_free", capacity=True)     # in-feed has no un-picked disc
hand_empty  = predicate("hand_empty", capacity=True)    # gripper holds no disc
anode_free  = predicate("anode_free", capacity=True)    # anode/cathode station is idle
# The anode has been SEEN empty (ClearAnode / CheckAnode) — PlaceAnode
# needs it and takes it away. Not a capacity fact: it is knowledge, set
# by a camera and consumed by a placement.
anode_clear = predicate("anode_clear")


# ── Exposed, tweakable parameters ─────────────────────────────────────
SLOTS       = [f"A{c}" for c in range(1, 7 + 1)]  # A1 .. A7, in order
Z_STEP      = 0.254                            # per-disc stack lift (mm), in + out
# Discs per position — a full IN stack, and an OUT position before the
# next one. NOT written here: ``stack_size`` in hmi/default.j2 is the one
# place, read by setup() (kwarg; the schema's own default when a caller
# passes none — a notebook, a bare setup()).
MAX_PER_SLOT = 0
_SCHEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hmi", "default.j2")


def _schema_default(key):
    """A kwarg's default straight from hmi/default.j2 — what the platform
    passes when nothing overrides it; here for callers that skip the
    platform (a notebook calling setup() bare)."""
    import yaml
    from jinja2 import Template
    with open(_SCHEMA) as f:
        return yaml.safe_load(Template(f.read()).render())[key]

# Visual inspection: the detector runs on the WHOLE frame (no ROI — it
# finds the disc itself); the classifier then sees the detector's box
# grown by this many px (roi.offset on the box corners), cropped — the
# model was trained on cropped discs (vision/disc_pass_fail_cropped.pkl).
CLS_ROI_OFFSET     = 100
# Classifier SENSITIVITY — the ``classifier`` kwarg (hmi/default.j2, set on
# the setup screen). The model gives a probability that the disc is a
# pass; a disc passes only if that probability clears the threshold.
# high catches the most (a pass needs 75 %), medium is the model's own
# call (50 %), low lets more through (25 %). ignore skips the classifier
# altogether — the detector still has to see a disc — and the sort is by
# the reading alone. setup() writes CLS_MODE from the kwarg.
CLS_MODES          = {"high": 0.75, "medium": 0.50, "low": 0.25, "ignore": None}
CLS_MODE           = "medium"
# How many "no disc" verdicts IN A ROW from one IN position — by either
# camera — end that position: its remaining discs are voided and the run
# moves on. One is a fail, not a verdict on the stack.
EMPTY_RUN_ENDS_COLUMN = 2

# Suction motion offsets (mirror the runtime example).
PICK_TCP_Z   = -5                             # suction drives deeper to grab
PLACE_GRAV   = -5                              # suction presses on release

_STEPS = 13                                    # per-disc steps for progress (nominal)


# ── Where a disc lands — the LANES ───────────────────────────────────
# A lane is an ORDERED list of (holder alias, slot) positions, filled in
# that order, MAX_PER_SLOT discs each (z stepping by Z_STEP), one lane per
# verdict. The fail holder is split: A1–A4 take the discs whose READING
# was outside the window, A5 the discs the hand held while the anode
# still showed one (STUCK — what the hand holds is not trusted), A6–A7
# the discs a CAMERA failed (or saw no disc), so the kinds never mix in
# one stack. Which lane a disc takes is decided in Sort / Reject; _drop
# only walks it.
PASS_HOLDERS = ["disc_out_good_1", "disc_out_good_2"]
FAIL_HOLDER  = "disc_out_bad_1"
LANES = {
    "pass":         [(h, s) for h in PASS_HOLDERS for s in SLOTS],
    "fail_measure": [(FAIL_HOLDER, s) for s in SLOTS[0:4]],     # A1..A4
    "fail_stuck":   [(FAIL_HOLDER, s) for s in SLOTS[4:5]],     # A5
    "fail_visual":  [(FAIL_HOLDER, s) for s in SLOTS[5:7]],     # A6, A7
}

# Where the robot camera looks at the anode from — the stand PlaceAnode
# ends on and ClearAnode / CheckAnode go to: an offset in the place
# anchor's frame that keeps the lens off the disc.
ANODE_VIEW = dict(anchor="place", offset=[10, 50, 70, 0, 0, 0])

# Where the next disc goes is tracked by a per-POSITION fill count in
# ctx.meta["filled"] = {(holder, slot): n_dropped}; z = n × Z_STEP. This
# is runtime state (lives in execute, never in planner facts), so it's
# BT-legal. It does NOT survive a restart mid-batch (the count resets);
# fine here because a batch is run start-to-finish and sorted discs are
# terminal — every one is DELETED right after place().

def _next_drop(filled, lane):
    """Next (holder, slot, z, count) along ``lane`` — the first position
    with room, its stack height. None when the whole lane is full."""
    for holder, slot in LANES[lane]:
        count = filled.get((holder, slot), 0)
        if count < MAX_PER_SLOT:
            return holder, slot, round(count * Z_STEP, 3), count
    return None


# ── Generic helpers ───────────────────────────────────────────────────

def _disc(disc: int) -> str:
    return f"disc_{disc}"


def _pass_prob(res) -> float:
    """The classifier's probability that the disc is a pass, from its ONE
    entry — the top class and its probability (vision/disc_cls.yaml,
    top_k 1). Two classes, so a top ``fail`` at p means pass at 1 - p.
    0 when the result is empty."""
    if not res:
        return 0.0
    top = res[0]
    try:
        p = float(top.get("conf", 0.0))
    except (TypeError, ValueError):
        return 0.0
    return p if top.get("cls") == "pass" else 1.0 - p


def _column_mates(disc: int) -> list:
    """The discs still to come from the same IN position as ``disc`` —
    what an ended position voids. Contiguous by construction (INVENTORY is
    built position by position, top of the stack first)."""
    col = INVENTORY[disc][:2]
    return [d for d in range(disc + 1, len(INVENTORY)) if INVENTORY[d][:2] == col]


def _empty_run(action, disc, seen: bool) -> bool:
    """Count a camera's verdict against the disc's IN position: a disc seen
    resets the position's run of empties, a "no disc" extends it. True
    when the run has reached EMPTY_RUN_ENDS_COLUMN — the position is
    finished. Runtime state (ctx.meta), like the fill counter."""
    col = INVENTORY[disc][:2]
    runs = action.ctx.meta.setdefault("empty_run", {})
    if seen:
        runs[col] = 0
        return False
    runs[col] = runs.get(col, 0) + 1
    return runs[col] >= EMPTY_RUN_ENDS_COLUMN


def _end_column(action, disc) -> None:
    """The run-time side of a position ending: say so, drop the scene
    disc its Create may already have spawned there, mark the position
    emptied for the pendant. The FACTS (done + void on the mates) are the
    action's ``column_done`` branch."""
    rt, ws = action.ctx.runtime, action.ctx.workspace
    in_h, slot, _z = INVENTORY[disc]
    mates = _column_mates(disc)
    rt.step(f"in_{in_h}[{slot}]: {EMPTY_RUN_ENDS_COLUMN} empty picks in a row — the stack has run out, "
            f"{len(mates)} more disc{'s' if len(mates) != 1 else ''} there voided")
    for d in mates:
        if _disc(d) in ws.components:
            ws.remove_component(_disc(d))
    action.ctx.meta.setdefault("void", set()).add((in_h, slot))
    action.ctx.meta["empty_run"][(in_h, slot)] = 0
    _publish(action, f"{_pos(in_h, slot)} is empty — moving on")


def _verdict(res) -> str:
    """The classifier's word under the run's sensitivity: ``pass`` only
    when its pass probability clears CLS_MODES[CLS_MODE]. An empty result
    is a fail — a disc the model cannot vouch for never reaches the anode."""
    threshold = CLS_MODES[CLS_MODE]
    if threshold is None:
        return "pass"            # ignore: the classifier has no say
    return "pass" if _pass_prob(res) >= threshold else "fail"


def _anode_seen(action, sim_return=[]):
    """Stand the robot camera over the anode (ANODE_VIEW) and run the
    DETECTOR alone. Returns the detector's hits (``[]`` = nothing on the
    anode), or ``None`` when the read failed (the declarative-retry
    contract: the caller returns False, the operator recovers).
    ``sim_return`` is what the look sees in sim: empty — the anode is
    clear — unless the caller says otherwise (ClearAnode's first look)."""
    rcp = action.ctx.recipes
    rcp["anode"].stand(ANODE_VIEW["anchor"], offset=ANODE_VIEW["offset"])
    return rcp["inspector_robot"].detect(roi={"corners": [], "crop": False}, sim_return=sim_return)


def _inspect(action, od_alias, cls_alias):
    """Detect, then classify — one camera, two models.

    The detector (``od_alias``) runs on the WHOLE fresh frame — an
    explicit empty ROI, so nothing set on the detection earlier narrows
    it; its best box, grown by CLS_ROI_OFFSET px, is the classifier's
    (``cls_alias``) region on a frame taken right after, the disc at rest
    (the server caches one frame per detection). Returns ``"empty"`` when
    the detector finds no disc, ``"pass"`` / ``"fail"`` from the
    classifier, ``None`` when a read failed (the declarative-retry
    contract: the caller returns False and the operator recovers).
    sim_return shapes are the server's real result lists.
    """
    rcp = action.ctx.recipes
    found = rcp[od_alias].detect(
        roi={"corners": [], "crop": False},
        sim_return=[{"cls": "disc", "conf": 0.99, "center": [1000, 600],
                     "corners": [[900, 500], [1100, 500], [1100, 700], [900, 700]]}])
    if found is None:
        return None
    if not found:
        return "empty"
    best = max(found, key=lambda r: r.get("conf", 0))
    box = best["corners"]
    # The two boxes, in the run log: what the detector found (full-frame
    # px) and what the classifier was handed. If the crop on the vision
    # unit does not match these numbers, the fault is downstream of here.
    xs = [c[0] for c in box]; ys = [c[1] for c in box]
    threshold = CLS_MODES[CLS_MODE]
    action.ctx.runtime.step(
        f"{od_alias}: {len(found)} found, best {best.get('conf', 0):.2f} at "
        f"x {min(xs):.0f}..{max(xs):.0f} y {min(ys):.0f}..{max(ys):.0f} "
        f"({max(xs) - min(xs):.0f}x{max(ys) - min(ys):.0f} px)"
        + (f" -> {cls_alias} roi +{CLS_ROI_OFFSET} px" if threshold is not None
           else " -> classifier ignored, pass"))
    if threshold is None:
        return "pass"            # ignore: the detector saw a disc, the reading decides
    res = rcp[cls_alias].detect(
        roi={"corners": box, "offset": CLS_ROI_OFFSET, "crop": True},
        sim_return=[{"cls": "pass", "conf": 0.99}])
    if res is None:
        return None
    v = _verdict(res)
    action.ctx.runtime.step(
        f"{cls_alias}: pass {_pass_prob(res):.2f} vs {threshold:.2f} ({CLS_MODE}) -> {v}")
    return v


# ── IN inventory ──────────────────────────────────────────────────────
# Filled by setup() from the in_1 / in_2 lists (each 7, index i = anchor
# A<i+1>; any positive entry = a full stack of MAX_PER_SLOT). INVENTORY[disc] =
# (holder, slot, z): stacks are consumed TOP-first (depth n-1 → 0, z =
# depth × Z_STEP), slots A1→A7, in_1 until empty, then in_2. Module-level
# so the per-disc actions (Create / Pick) can read their position; rebuilt
# on every setup() call, so a replan stays consistent with the same kwargs.
# LOADED[(holder, slot)] = discs that position started with — what the
# pendant's in-stack states are computed against.
INVENTORY: list = []   # disc index → (in_holder, slot, z)
LOADED: dict = {}      # (in_holder, slot) → discs loaded there


def _progress_pct(action):
    """Discs SETTLED over discs in the batch, the disc in flight counted
    by how far along its chain it is. A disc is settled when it is done
    (sorted or rejected — a rejected disc takes 5 steps, not 12, and
    still counts as one whole disc) or removed by the operator. 100 only
    when every disc is settled; Park says 100 itself."""
    discs = list(action._ctx_all_objects().get("disc", []))
    if not discs:
        return 0
    facts = (getattr(action.ctx, "state", None) or {}).get("facts") or set()
    acc = 0.0
    for d in discs:
        if (done.name, d) in facts or action._ctx_removed(d):
            acc += 1.0
        else:
            reached = sum(1 for p in _CHAIN if (p.name, d) in facts)
            acc += min(reached, _STEPS - 1) / _STEPS
    return int(acc / len(discs) * 100)


# The per-disc chain in order — what progress and the audit status read.
_CHAIN = (created, picked, presented, inspected, bottom_failed, on_anode,
          anode_inspected, top_failed, cathode_down, measured, cathode_up,
          off_anode, anode_checked, sorted_, void)


def _status_of(facts, disc):
    """The audit status, DERIVED from the facts — never typed by hand:
    the last fact of the chain this disc reached."""
    reached = [p.name for p in _CHAIN if (p.name, disc) in facts]
    return reached[-1] if reached else "not started"


# ── Pendant — what rt.op publishes ───────────────────────────────────
# States, never numbers: the operator marked each position full or empty
# and never saw a count, so the pendant shows the same vocabulary. Both
# helpers are pure so they can be checked without a runtime.
OUT_KEYS = (("disc_out_good_1", "good_1"), ("disc_out_good_2", "good_2"),
            ("disc_out_bad_1", "bad_1"))


def _in_states(loaded, picked, active=None, void=()):
    """Per-position state of the two IN holders.
      empty   nothing was loaded there
      full    loaded, discs remain
      active  the disc being picked right now comes from here
      done    loaded, and every disc has been taken — or the cameras found
              the stack empty early (``void``: the position is finished)
    ``loaded`` / ``picked`` map (holder, slot) → n; ``active`` is one
    (holder, slot) or None; ``void`` a set of (holder, slot)."""
    out = {}
    for h in (1, 2):
        row = []
        for slot in SLOTS:
            n, p = loaded.get((h, slot), 0), picked.get((h, slot), 0)
            if active == (h, slot):
                row.append("active")
            elif n <= 0:
                row.append("empty")
            elif p >= n or (h, slot) in void:
                row.append("done")
            else:
                row.append("full")
        out[f"in_{h}"] = row
    return out


def _in_left(loaded, picked):
    """Per-position discs STILL IN each IN holder — loaded minus taken.
    The number the pendant writes in the well; 0 where nothing was loaded."""
    return {f"in_{h}": [max(0, loaded.get((h, slot), 0) - picked.get((h, slot), 0))
                        for slot in SLOTS] for h in (1, 2)}


def _out_counts(filled):
    """Per-position discs IN each OUT holder — the drop's own fill counts,
    {(holder, slot): n}, laid out as the pendant draws them."""
    return {key: [min(MAX_PER_SLOT, filled.get((alias, s), 0)) for s in SLOTS]
            for alias, key in OUT_KEYS}


def _out_states(filled):
    """Per-position state of the three OUT holders: empty | filling | full."""
    return {key: ["empty" if n == 0 else "full" if n >= MAX_PER_SLOT else "filling"
                  for n in row] for key, row in _out_counts(filled).items()}


def _tag(disc):
    # No total: the operator never typed a count and is not shown one.
    return f"Disc {disc + 1}"


def _pos(holder, slot):
    return f"In {holder} {slot}"


def _publish(action, headline=None, active=None, **extra):
    """Push the operator-facing picture to the pendant. Replace semantics
    per key (Runtime.op); observability never blocks the workflow.
    ``notice=dict(level, title, text)`` puts a card on the pendant that
    needs the operator (level: warning | error | info); it is cleared
    by the next publish without one."""
    meta = action.ctx.meta
    vals = dict(
        in_stacks=_in_states(LOADED, meta.get("picked_from", {}), active,
                             meta.get("void", set())),
        in_left=_in_left(LOADED, meta.get("picked_from", {})),
        out_stacks=_out_states(meta.get("filled", {})),
        out_counts=_out_counts(meta.get("filled", {})),
        total_n=len(INVENTORY),
        pass_n=meta.get("pass_n", 0),
        fail_n=meta.get("fail_n", 0),
        done_n=meta.get("pass_n", 0) + meta.get("fail_n", 0),
        progress=_progress_pct(action),       # the pendant's bar, same figure as the step bar
    )
    if headline is not None:
        vals["headline"] = headline
    # The notice card: sent on EVERY publish — None removes the key
    # (Runtime.op), so a card stays up exactly until the next picture.
    vals["notice"] = extra.pop("notice", None)
    vals.update(extra)
    action.ctx.runtime.op(**vals)


# ── setup ─────────────────────────────────────────────────────────────

def setup(**kwargs):
    # The bench's numbers first — the kwarg when the platform passes it,
    # the schema's own default when a caller skips the platform (a bare
    # setup() from a notebook). hmi/default.j2 is the one place.
    def _num(key, cast):
        v = kwargs.get(key)
        return cast(v if v not in (None, "") else _schema_default(key))

    global MAX_PER_SLOT
    MAX_PER_SLOT = _num("stack_size", int)             # every count below is in units of it

    def _counts(key, default):
        """Parse an inventory spec into exactly len(SLOTS) disc counts —
        lenient by design, since a headless caller may deliver the list
        as a string like "1,1,1,1,1,1,1" or "[1, 0]":
          * list/tuple of numbers → used as-is
          * string → brackets/spaces stripped, split on commas
          * scalar → treated as [scalar]
        A shorter list fills the leading anchors (rest 0); a longer one is
        truncated to A1..A7. Each entry is a FLAG: anything above zero is
        a full stack of MAX_PER_SLOT discs (the screen sends 1 / 0), zero
        is an empty position."""
        raw = kwargs.get(key, default)
        if isinstance(raw, str):
            raw = [p for p in raw.strip().strip("[]").replace(" ", "").split(",") if p]
        elif isinstance(raw, (int, float)):
            raw = [raw]
        counts = []
        for n in list(raw)[:len(SLOTS)]:
            try:
                v = int(float(n))
            except (TypeError, ValueError):
                v = 0
            counts.append(MAX_PER_SLOT if v > 0 else 0)
        counts += [0] * (len(SLOTS) - len(counts))
        return counts

    in_1 = _counts("in_1", [1] * len(SLOTS))
    in_2 = _counts("in_2", [0] * len(SLOTS))

    # Classifier sensitivity — one of CLS_MODES; anything else (a typo
    # from a headless caller) is the model's own call, said so in the log.
    global CLS_MODE
    mode = str(kwargs.get("classifier", "medium") or "medium").strip().lower()
    CLS_MODE = mode if mode in CLS_MODES else "medium"

    INVENTORY.clear()
    LOADED.clear()
    for holder, counts in ((1, in_1), (2, in_2)):
        for s, n in enumerate(counts):
            LOADED[(holder, SLOTS[s])] = n
            for depth in range(n - 1, -1, -1):        # top of the stack first
                INVENTORY.append((holder, SLOTS[s], round(depth * Z_STEP, 3)))

    discs = list(range(len(INVENTORY)))

    def item_done(state, disc):
        return (done.name, disc) in state

    def goal(state):
        # What lies beyond the discs — the platform adds "every disc
        # done or removed" (workspace bt/remove.py).
        return (started.name,) in state and (parked.name,) in state


    def item_components(workspace, disc):
        # A disc's 3D model exists only between its Create and its Sort
        # (created on demand, deleted once placed) — so it is the model
        # when the disc is on the bench, nothing before or after. What an
        # operator Replan clears when the disc is removed (workspace
        # bt-framework-guide §8.6).
        name = _disc(disc)
        return [name] if name in workspace.components else []

    return {
        "initial_facts":   frozenset(),
        "goal":            goal,
        "item_done":       item_done,
        "objects":         {"disc": discs},
        "item_components": item_components,
    }


# ── Lifecycle ─────────────────────────────────────────────────────────

class Start(Action):
    params   = []
    duration = 5
    resource = "robot"
    START_JOINTS = [0, 45, -90, 0, -45, 0, 100]

    def pre(self):
        return ~started()

    def eff(self):
        # Seed the single-occupancy resources: feed, hand, anode all free.
        return {"started": (+started(), +feed_free(), +hand_empty(), +anode_free())}

    def execute(self):
        rt  = self.ctx.runtime
        rcp = self.ctx.recipes
        ws  = self.ctx.workspace
        core = ws.components["core"]
        # Fresh tallies for the pendant — a batch runs start-to-finish, and
        # None clears a reading left from the previous run (rt.op removes
        # the key).
        for k in ("picked_from", "filled", "disc_c", "verdict"):
            self.ctx.meta[k] = {}
        self.ctx.meta["blow_tries"] = 0
        self.ctx.meta["empty_run"] = {}
        self.ctx.meta["void"] = set()
        self.ctx.meta["pass_n"] = 0
        self.ctx.meta["fail_n"] = 0
        _publish(self, "Starting — homing", last_disc=None, last_c=None,
                 last_c_unit=None, last_result=None)
        thr = CLS_MODES[CLS_MODE]
        rt.step("classifier: ignored — the detector must see a disc, the reading sorts"
                if thr is None else f"classifier: {CLS_MODE} — a pass needs ≥ {thr:.0%}")
        rt.motor(1)
        # Home the rail before any move that assumes a homed axis:
        # set_axis_with_stop configures the axis + PID and homes against
        # the hard stop — already-homed axes (and sim) short-circuit to
        # True, so calling it every Start is cheap. A homing failure is
        # FATAL: return the reserved "killed" outcome — the runtime is
        # killed on the spot, nothing else runs, no motion ever happens
        # on the unhomed rail. The operator must Reset / re-Launch.
        if core.has_rail:
            rt.step("homing rail")
            if not rcp["robot"].set_axis_with_stop(core.rail_cfg):
                rt.step("homing failed")
                return "killed"
        # The cathode UP before anything is placed on the anode: a run
        # killed between CathodeDown and CathodeUp, or an operator's
        # Enable, leaves the cylinder down — and anode_free() is seeded
        # above regardless. Every run starts with the cathode up by
        # construction (within a run the facts guarantee it: PlaceAnode
        # needs anode_free, which only PickAnode after CathodeUp gives).
        rt.step("cathode up")
        ws.components["rotating_cylinder_mkb1630_1"].disable()
        # Move to a known ready pose (Recipe.park is a base move-to-joint
        # on the generic component-less "robot" recipe).
        rcp["robot"].park(joint=self.START_JOINTS)
        return "started"


class ClearAnode(Action):
    """Run-level: make sure the anode is EMPTY before anything is placed
    on it — at run start, and after a CheckAnode that saw a disc left
    behind. Image it; nothing there → ``clear``. A disc there → go to
    the anode's ``blow`` anchor, blow it off, and return ``blown`` with
    NO fact changed: the planner selects this action again, one run per
    attempt, no loop. After MAX_BLOWS attempts in a row the run pauses
    for the operator to clear the anode; Resume runs it again from zero.

    ``hand_empty`` in the pre: never while a disc is in the gripper — the
    blow-off is the suction's own release, it would drop that disc."""
    params   = []
    duration = 8
    resource = "robot"
    # The blow, three legs like a place. The ``blow`` anchor IS the blow
    # pose (5 mm above place's height, 15 mm along -x, tilted -20° about
    # x — the component's anchors). A PLANNED, collision-checked travel
    # to ``hover``, 20 mm STRAIGHT UP from the anchor, clear of the
    # anode's box; then one jmove onto the anchor with no plan and no
    # collision check — the way a place's final descent is — the air,
    # and the same jmove back out, so the next planned travel starts
    # outside the box.
    #
    # ``hover`` is an offset in the anchor's frame, and the anchor is
    # tilted 20°, so straight up is (0, -sin 20°, cos 20°) × 20 — NOT
    # [0, 0, 20], which is 20 mm back along the nozzle's axis. Straight
    # up, the hover and the blow point solve in ONE wrist branch and the
    # unplanned jmove between them moves no joint more than 8° (sim);
    # the first version hovered along the nozzle axis and the jmove
    # flipped j3/j4/j5 by 84/61/275° over the anode. The branch is a
    # property of the geometry, not of this offset: move the anchor,
    # re-measure the jmove's deltas before trusting it (the anchor's
    # comment names the one 5 mm that flips it). Change the tilt →
    # change this.
    BLOW      = dict(anchor="blow",
                     hover=[0, -6.84, 18.79, 0, 0, 0],
                     at=[0, 0, 0, 0, 0, 0],
                     seconds=5.0)
    LEG_PRM   = dict(has_motion_plan=[False, "jmove"])   # the unplanned legs in and out
    MAX_BLOWS = 5                     # blows in a row before the operator is asked

    def pre(self):
        return started() & hand_empty() & ~anode_clear()

    def eff(self):
        return {"clear": (+anode_clear(),),
                "blown": None}        # no fact: the planner asks this action again

    def execute(self):
        rt, rcp, ws = self.ctx.runtime, self.ctx.recipes, self.ctx.workspace
        rt.step("anode: looking for a disc")
        # SIM: the first look of a run sees a disc, so a sim run shows
        # the blow once (the same hit shape as the real detector's);
        # after a blow the anode is clear. Real runs ignore sim_return.
        found = _anode_seen(self, sim_return=(
            [{"cls": "disc", "conf": 0.99, "center": [1100, 1200],
              "corners": [[700, 750], [1550, 750], [1550, 1620], [700, 1620]]}]
            if self.ctx.meta.get("blow_tries", 0) == 0 else []))
        if found is None:
            rt.step("anode: camera read failed — recover the camera, then Resume")
            return False
        if not found:
            self.ctx.meta["blow_tries"] = 0
            rt.step("anode: clear")
            _publish(self, "Anode clear")          # also takes down the blocked card after a Resume
            return "clear"
        tries = self.ctx.meta.get("blow_tries", 0)
        if tries >= self.MAX_BLOWS:
            self.ctx.meta["blow_tries"] = 0
            rt.step(f"anode: still holds a disc after {tries} blows — clear the anode by hand, "
                    f"then Resume", level="warning")
            _publish(self, "Anode blocked — clear it, then Resume",
                     notice=dict(level="warning", title="Anode blocked",
                                 text=f"A disc is still on the anode after {tries} blows. "
                                      f"Clear the anode by hand, then press Resume."))
            rt.pause()
            rt.checkpoint()           # blocks until Resume; then this action runs again
            return False
        self.ctx.meta["blow_tries"] = tries + 1
        rt.step(f"anode: a disc is there — blow {tries + 1} of {self.MAX_BLOWS}")
        _publish(self, f"Clearing the anode — blow {tries + 1} of {self.MAX_BLOWS}")
        anchor = self.BLOW["anchor"]
        rcp["anode"].stand(anchor, offset=self.BLOW["hover"])                     # planned, outside the box
        rcp["anode"].stand(anchor, offset=self.BLOW["at"], **self.LEG_PRM)        # jmove onto the anchor, no plan
        ws.components["gripper_suction_1"].blow(self.BLOW["seconds"])
        rcp["anode"].stand(anchor, offset=self.BLOW["hover"], **self.LEG_PRM)     # jmove back out
        return "blown"


class Create(Action):
    """Spawn the disc at its configured inventory position — the top of
    the remaining stack at its in-holder anchor (z = depth × Z_STEP)."""
    params   = ["disc"]
    duration = 2
    resource = "robot"

    def pre(self, disc):
        # feed_free gates one un-picked disc at a time (no batch of Creates).
        # ~done skips a disc its position's empty picks already voided.
        return started() & feed_free() & ~created(disc) & ~done(disc)

    def eff(self, disc):
        return {"created": (+created(disc), -feed_free())}   # feed now occupied

    def execute(self, disc):
        rt, ws = self.ctx.runtime, self.ctx.workspace
        name = _disc(disc)
        # Idempotent retry — clear a leftover from a failed prior attempt.
        if name in ws.components:
            ws.remove_component(name)
        in_h, slot, z = INVENTORY[disc]   # configured stack position
        rt.step(f"disc {disc + 1}: create at in_{in_h}[{slot}] z={z}")
        rt.step(_progress_pct(self), level="progress")
        _publish(self, f"{_tag(disc)} — next from {_pos(in_h, slot)}", active=(in_h, slot))
        # Seed the audit row here, not at Start: discs exist on demand,
        # and a row per disc that never entered the bench is noise.
        rt.record(_tag(disc), source=f"in_{in_h} {slot}")
        ws.add_component(name, {
            "type": "disc_22mm",
            "attach": {
                "parent_name":   f"stack_holder_disc_in_{in_h}",
                "parent_solid":  "body",
                "parent_anchor": slot,
                "child_solid":   "body",
                "child_anchor":  "center",
                "offset":        [0, 0, z, 0, 0, 0],
            },
        })
        return "created"


class Pick(Action):
    """Suction-pick the disc off the IN stack and lift straight out —
    one recipe call, its parameters in PRM."""
    # soft_approach=True: stop at the gap above the stack, straight final
    # descent — matches the Sort side (smove travel blends otherwise).
    PRM       = dict(tool_tcp_z_offset=PICK_TCP_Z, soft_approach=True)
    params   = ["disc"]
    duration = 10
    resource = "robot"

    def pre(self, disc):
        # hand_empty gates one-disc-at-a-time in the gripper. anode_clear:
        # no disc is taken while the anode holds one — the blow-off is the
        # suction's own release, so ClearAnode needs the hand empty, and a
        # disc picked before the anode is clear leaves the run with nowhere
        # to go. The route planner takes an item's step before a run-level
        # one once the item has started (Create ran); this fact is what
        # puts ClearAnode first.
        return created(disc) & hand_empty() & anode_clear() & ~picked(disc)

    def eff(self, disc):
        # Disc leaves the feed into the hand: feed frees, hand fills.
        # Disc into the hand: hand fills. The feed stays busy until the
        # cameras have seen what the pick took (the inspections free it).
        return {"picked": (+picked(disc), -hand_empty())}

    def execute(self, disc):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        in_h, slot, _z = INVENTORY[disc]   # same position the disc was created at
        rt.step(f"disc {disc + 1}: pick from in_{in_h}[{slot}]")
        rt.step(_progress_pct(self), level="progress")
        _publish(self, f"{_tag(disc)} — picking from {_pos(in_h, slot)}", active=(in_h, slot))
        # The Rack recipe's component is the ADAPTER; pick resolves the
        # stack holder sitting on it itself.
        rcp[f"disc_in_{in_h}"].pick(slot, **self.PRM)
        rt.count("disc.picked", n=1)
        # One more taken from that position: the pendant's in-stack state
        # (full → done) is computed from this, never from the plan.
        taken = self.ctx.meta.setdefault("picked_from", {})
        taken[(in_h, slot)] = taken.get((in_h, slot), 0) + 1
        _publish(self, f"{_tag(disc)} — picked from {_pos(in_h, slot)}")
        return "picked"


class Present(Action):
    """Present the held disc at the horizontal inspection station (motion only)."""
    params   = ["disc"]
    duration = 6
    resource = "robot"

    # approach=True: the camera travel is a PLANNED fold, so the pick's
    # held exit lift fuses into it — one stop (the pick gap), then a
    # continuous ride to the camera. approach=False made this a direct
    # unplanned hop, which can never consume a held tail: the pick exit
    # ran classic (gap stop + padding stop) on every set. No soft
    # approach — nothing delicate about arriving at a camera pose.
    PRM      = dict(approach=True, soft_approach=False)

    def pre(self, disc):
        return picked(disc) & ~presented(disc)

    def eff(self, disc):
        return {"presented": (+presented(disc),)}

    def execute(self, disc):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step(f"disc {disc + 1}: present")
        rt.step(_progress_pct(self), level="progress")
        _publish(self, f"{_tag(disc)} — to the camera")
        rcp["inspector"].present(**self.PRM)
        return "presented"


class InspectBottom(Action):
    """Station camera: DETECT the held disc, then CLASSIFY it on the box
    the detector found. A sensing action with two outcomes
    (bt-framework-guide §7): ``pass`` (default) and ``fail``.

    A disc the detector does not see is a FAIL, not a pause and not an
    empty hand: the suction is never released mid-way, Reject carries
    whatever the gripper holds to the fail column exactly as it would a
    classified fail. The reason is kept for the audit row and the drop.

    A failed READ (camera down) returns False — the success facts are
    asserted only on a valid reading; the planner re-selects this action
    after the operator recovers the camera and resumes (the scale
    pattern, project-guide §8). A dead camera raises
    CameraUnavailableError and pauses like any critical device.
    """
    params   = ["disc"]
    duration = 6
    resource = "robot"

    def pre(self, disc):
        return presented(disc) & ~inspected(disc) & ~bottom_failed(disc)

    def eff(self, disc):
        # The feed opens on a FAIL here (the disc never reaches the top
        # camera) and on the top camera's verdict otherwise.
        # column_done: this disc is a fail like any other "no disc", AND the
        # position's remaining discs are void — done, never created.
        mates = _column_mates(disc)
        return {"pass":        (+inspected(disc),),
                "fail":        (+bottom_failed(disc), +feed_free()),
                "column_done": (+bottom_failed(disc), +feed_free(),
                                *(f for d in mates for f in (+void(d), +done(d))))}

    def execute(self, disc):
        rt = self.ctx.runtime
        rt.step(f"disc {disc + 1}: inspect bottom")
        rt.step(_progress_pct(self), level="progress")
        _publish(self, f"{_tag(disc)} — inspecting")
        v = _inspect(self, "inspector", "inspector_cls")
        if v is None:
            rt.count("inspect.bottom", read_failed=1)
            rt.step(f"disc {disc + 1}: inspection read failed — recover the camera, then Resume")
            return False
        rt.count("inspect.bottom", **{v: 1})        # v is "pass" / "fail" / "empty"
        rt.record(_tag(disc), visual_bottom="none" if v == "empty" else v)
        ended = _empty_run(self, disc, seen=(v != "empty"))
        if v != "pass":
            why = "bottom camera: no disc" if v == "empty" else "bottom camera: fail"
            self.ctx.meta.setdefault("verdict", {})[disc] = why
            rt.step(f"disc {disc + 1}: {why} → fail column")
            _publish(self, f"{_tag(disc)} — failed inspection", last_disc=disc + 1,
                     last_c=None, last_c_unit=None, last_result="fail")
            if ended:
                _end_column(self, disc)
                return "column_done"
            return "fail"
        return "pass"


class Reject(Action):
    """Bottom-camera fail: the held disc goes straight to the fail column."""
    params   = ["disc"]
    duration = 10
    resource = "robot"

    def pre(self, disc):
        return bottom_failed(disc) & ~done(disc)

    def eff(self, disc):
        return {"rejected": (+sorted_(disc), +done(disc), +hand_empty())}

    def execute(self, disc):
        why = self.ctx.meta.get("verdict", {}).get(disc, "bottom camera")
        held = why != "bottom camera: no disc"
        return "rejected" if _drop(self, disc, "fail_visual", why, held=held) else False


class PlaceAnode(Action):
    """Place the disc on the anode's "place" anchor with a SHORT exit
    (EXIT_CLEARANCE mm above the disc — the recipe's exit-leg number
    form), then stand at ANODE_VIEW so the robot camera has an
    unoccluded view of the disc for InspectTop."""
    EXIT_CLEARANCE = 10                  # mm above the placed disc
    PRM      = dict(gravity_offset=PLACE_GRAV, soft_approach=False)
    # The stand to the viewing pose stays a deliberate unplanned straight
    # lmove — the exit=EXIT_CLEARANCE start may still be inside the
    # anode's inflated box; this leg is the recipe-owned exit corridor.
    STAND_PRM = dict(has_motion_plan=[False, "lmove"])
    params   = ["disc"]
    duration = 10
    resource = "robot"

    def pre(self, disc):
        # anode_free gates one-disc-at-a-time on the shared anode/cathode;
        # anode_clear is the camera's word that nothing is on it.
        return inspected(disc) & anode_free() & anode_clear() & ~on_anode(disc)

    def eff(self, disc):
        # Disc leaves the hand onto the anode: hand frees, anode occupied,
        # and no longer known clear.
        return {"on_anode": (+on_anode(disc), +hand_empty(), -anode_free(), -anode_clear())}

    def execute(self, disc):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step(f"disc {disc + 1}: place on anode")
        rt.step(_progress_pct(self), level="progress")
        _publish(self, f"{_tag(disc)} — onto the anode")
        # exit=<number> pulls off just EXIT_CLEARANCE mm above the disc
        # (the approach keeps the recipe's full padding).
        rcp["anode"].place("place", exit=self.EXIT_CLEARANCE, **self.PRM)
        rcp["anode"].stand(ANODE_VIEW["anchor"], offset=ANODE_VIEW["offset"], **self.STAND_PRM)
        return "on_anode"


class InspectTop(Action):
    """Robot camera: the same detect-then-classify on the seated disc,
    before the measurement. ``pass`` (default) → measure; ``fail`` → the
    measurement is skipped and PickAnode takes it to the fail column.
    No disc seen on the anode is a fail too: PickAnode re-grips whatever
    is there and Sort drops it in the fail column, no pause.
    ``hand_empty`` in the pre keeps the arm at the anode hover: the
    planner cannot slot the next pick in between, so the camera is still
    over the anode when this runs."""
    params   = ["disc"]
    duration = 6
    resource = "robot"

    def pre(self, disc):
        return on_anode(disc) & hand_empty() & ~anode_inspected(disc) & ~top_failed(disc)

    def eff(self, disc):
        # The cameras are done with this disc: the feed opens for the next
        # Create. column_done as at the bottom camera.
        mates = _column_mates(disc)
        return {"pass":        (+anode_inspected(disc), +feed_free()),
                "fail":        (+top_failed(disc), +feed_free()),
                "column_done": (+top_failed(disc), +feed_free(),
                                *(f for d in mates for f in (+void(d), +done(d))))}

    def execute(self, disc):
        rt = self.ctx.runtime
        rt.step(f"disc {disc + 1}: inspect top")
        rt.step(_progress_pct(self), level="progress")
        _publish(self, f"{_tag(disc)} — inspecting on the anode")
        v = _inspect(self, "inspector_robot", "inspector_robot_cls")
        if v is None:
            rt.count("inspect.top", read_failed=1)
            rt.step(f"disc {disc + 1}: anode inspection failed — recover the camera, then Resume")
            return False
        rt.count("inspect.top", **{v: 1})           # v is "pass" / "fail" / "empty"
        rt.record(_tag(disc), visual_top="none" if v == "empty" else v)
        ended = _empty_run(self, disc, seen=(v != "empty"))
        if v != "pass":
            why = "top camera: no disc" if v == "empty" else "top camera: fail"
            self.ctx.meta.setdefault("verdict", {})[disc] = why
            rt.step(f"disc {disc + 1}: {why} → no measurement, fail column")
            _publish(self, f"{_tag(disc)} — failed inspection on the anode", last_disc=disc + 1,
                     last_c=None, last_c_unit=None, last_result="fail")
            if ended:
                _end_column(self, disc)
                return "column_done"
            return "fail"
        return "pass"


class CathodeDown(Action):
    """Drive the rotating cylinder down so the cathode contacts the disc."""
    params   = ["disc"]
    duration = 4
    resource = "robot"

    def pre(self, disc):
        return on_anode(disc) & anode_inspected(disc) & ~cathode_down(disc)

    def eff(self, disc):
        return {"cathode_down": (+cathode_down(disc),)}

    def execute(self, disc):
        rt, ws = self.ctx.runtime, self.ctx.workspace
        rt.step(f"disc {disc + 1}: cathode down")
        rt.step(_progress_pct(self), level="progress")
        _publish(self, f"{_tag(disc)} — clamping")
        ws.components["rotating_cylinder_mkb1630_1"].enable()
        return "cathode_down"


class Measure(Action):
    """Read the disc's capacitance (clamped anode ↔ cathode)."""
    params   = ["disc"]
    duration = 3
    resource = "robot"

    def pre(self, disc):
        return cathode_down(disc) & ~measured(disc)

    def eff(self, disc):
        return {"measured": (+measured(disc),)}

    def execute(self, disc):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step(_progress_pct(self), level="progress")
        _publish(self, f"{_tag(disc)} — measuring")
        m = rcp["meter"].read_capacitance()
        if m is None:
            # No implicit retry. A missing reading is an operator problem
            # (meter unplugged, or knocked out of RMT mode), so pause and
            # hand the run back to them. The disc stays clamped and the
            # robot does not move: ``checkpoint`` blocks the workflow
            # thread until Resume.
            #
            # We do NOT re-read here — returning False applies no effects,
            # so ``~measured(disc)`` still holds and the planner re-drives
            # Measure from observed state. Resume → this action runs again
            # → still unavailable → pauses again. One read per execute().
            rt.count("measure", unavailable=1)
            rt.step(f"disc {disc + 1}: meter unavailable — reconnect the meter "
                    f"(check RMT is on), then Resume")
            rt.pause()
            rt.checkpoint()          # blocks until the operator resumes
            return False
        # Stash the measured value on the ctx so Sort can read it without
        # a planning fact (it's per-disc runtime data, not plan state).
        self.ctx.meta.setdefault("disc_c", {})[disc] = m.primary
        rt.step(f"disc {disc + 1}: C = {m.primary:g} {m.primary_unit}")
        rt.count("measure", n=1)
        rt.record(_tag(disc), c_f=m.primary, c_unit=str(m.primary_unit))
        # The value itself goes to the pendant's reading card (SI-scaled
        # there); the headline stays a step, not a number.
        _publish(self, f"{_tag(disc)} — measured",
                 last_disc=disc + 1, last_c=m.primary, last_c_unit=str(m.primary_unit),
                 last_result="pass" if Sort.C_MIN <= m.primary <= Sort.C_MAX else "fail")
        return "measured"


class CathodeUp(Action):
    """Retract the cylinder (cathode up) so the disc can be lifted."""
    params   = ["disc"]
    duration = 4
    resource = "robot"

    def pre(self, disc):
        return measured(disc) & ~cathode_up(disc)

    def eff(self, disc):
        return {"cathode_up": (+cathode_up(disc),)}

    def execute(self, disc):
        rt, ws = self.ctx.runtime, self.ctx.workspace
        rt.step(f"disc {disc + 1}: cathode up")
        rt.step(_progress_pct(self), level="progress")
        _publish(self, f"{_tag(disc)} — unclamping")
        ws.components["rotating_cylinder_mkb1630_1"].disable()
        return "cathode_up"


class PickAnode(Action):
    """Suction-pick the disc back off the anode."""
    # fuse=True overrides the Scale class's fuse: false FOR THIS PICK
    # ONLY (the anode place keeps the no-hover-over-the-station rule):
    # the exit lift deposits and fuses into the sort travel. The sort
    # targets advance per disc, so batch 1 records them (classic stops
    # + one mismatch each); from the next batch the sequence repeats
    # and the seam merges. Novel positions pay one classic pass each.
    PRM      = dict(tool_tcp_z_offset=PICK_TCP_Z, soft_approach=True, approach=True,
                    fuse=True)
    params   = ["disc"]
    duration = 10
    resource = "robot"

    def pre(self, disc):
        # After the measurement, or straight after a top-camera fail (no
        # clamp, no read). hand_empty required to re-grip.
        return (cathode_up(disc) | top_failed(disc)) & hand_empty() & ~off_anode(disc)

    def eff(self, disc):
        # Disc back into the hand off the anode: hand fills, anode frees.
        return {"off_anode": (+off_anode(disc), -hand_empty(), +anode_free())}

    def execute(self, disc):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step(f"disc {disc + 1}: pick off anode")
        rt.step(_progress_pct(self), level="progress")
        _publish(self, f"{_tag(disc)} — off the anode")
        rcp["anode"].pick("place", **self.PRM)
        return "off_anode"


def _drop(action, disc, lane, why, held=True) -> bool:
    """Drop the held disc into the next position of ``lane`` (LANES —
    pass / fail_measure / fail_visual), then DELETE it — sorted discs are
    terminal and never linger in the scene. Shared by Sort and Reject.
    False when the lane is full (the action fails, the run pauses).

    ``held=False`` — the bottom camera saw NOTHING in the gripper (the
    stack ran out before the operator's mark, or the suction missed). The
    motion still runs as asked, but nothing lands: the holder's fill
    count and the pass/fail tallies do not move. Counting air would lift
    the bad column's next target 0.254 mm per phantom — a stack marked
    full that held 127 discs would put the next real drop 32 mm in the
    air. Under-counting a disc the detector merely missed costs one
    0.254 mm press on a compliant place — the safe direction."""
    rt, rcp, ws = action.ctx.runtime, action.ctx.recipes, action.ctx.workspace
    good = lane == "pass"
    filled = action.ctx.meta.setdefault("filled", {})   # (holder, slot) → n dropped
    nxt = _next_drop(filled, lane)
    if nxt is None:
        rt.step(f"disc {disc + 1}: the {lane} lane is FULL — "
                + ", ".join(f"{h}[{s}]" for h, s in LANES[lane]))
        return False
    holder, slot, z, count = nxt
    rt.step(f"disc {disc + 1}: {'GOOD' if good else 'BAD'} ({why}) → {holder}[{slot}] z={z}")
    rt.step(_progress_pct(action), level="progress")

    # Place the held disc into the ordered slot, then DELETE it. Nothing
    # accumulates (no meshes/pickables piling up over ~3500 discs); the
    # fill counter, not the scene, tracks where the next disc goes.
    rcp[holder].place(slot, offset=[0, 0, z, 0, 0, 0], **Sort.DROP_PRM)
    if _disc(disc) in ws.components:
        ws.remove_component(_disc(disc))
    name_out = dict(OUT_KEYS)[holder].replace("good_", "Pass ").replace("bad_", "Fail ")
    if not held:
        rt.step(f"disc {disc + 1}: nothing was in the gripper — {holder}[{slot}] count stays {count}")
        _publish(action, f"{_tag(disc)} — nothing in the gripper, column not counted")
        rt.record(_tag(disc), result="fail", out=f"{name_out} {slot} (empty hand)", reason=why)
        return True

    filled[(holder, slot)] = count + 1
    # one more disc through the bench: the total, and which column it went to
    rt.count("disc.sorted", n=1, **{"good" if good else "bad": 1})
    key = "pass_n" if good else "fail_n"
    action.ctx.meta[key] = action.ctx.meta.get(key, 0) + 1
    _publish(action, f"{_tag(disc)} — {'passed' if good else 'failed'} → {name_out} {slot}")
    rt.record(_tag(disc), result="pass" if good else "fail", out=f"{name_out} {slot}", reason=why)
    return True


class CheckAnode(Action):
    """Right after the pick off the anode: the robot camera looks at the
    anode again, detector only. ``clear`` (default) — the anode is empty,
    the hand has the disc, and the anode is known clear for the next
    placement. ``occupied`` — a disc is still on the anode: whatever the
    hand holds is a STUCK fail (Sort's fail_stuck lane), and the anode is
    not clear, so ClearAnode runs before the next PlaceAnode. Either way
    the run assumes a disc in the hand and goes on to Sort."""
    params   = ["disc"]
    duration = 6
    resource = "robot"

    def pre(self, disc):
        return off_anode(disc) & ~anode_checked(disc)

    def eff(self, disc):
        return {"clear":    (+anode_checked(disc), +anode_clear()),
                "occupied": (+anode_checked(disc), +anode_stuck(disc))}

    def execute(self, disc):
        rt = self.ctx.runtime
        rt.step(f"disc {disc + 1}: check the anode after the pick")
        rt.step(_progress_pct(self), level="progress")
        found = _anode_seen(self)
        if found is None:
            rt.step(f"disc {disc + 1}: anode check read failed — recover the camera, then Resume")
            return False
        if found:
            rt.step(f"disc {disc + 1}: a disc is STILL on the anode — this one is a stuck fail")
            rt.record(_tag(disc), anode_after_pick="occupied")
            _publish(self, f"{_tag(disc)} — anode still occupied", last_disc=disc + 1,
                     last_c=None, last_c_unit=None, last_result="fail")
            return "occupied"
        rt.record(_tag(disc), anode_after_pick="clear")
        return "clear"


class Sort(Action):
    """Drop the disc off the anode into an OUT holder: a top-camera fail
    is bad outright; otherwise the measured capacitance decides."""
    # Good/bad capacitance window (Farads). Defaulted WIDE so everything
    # currently lands in "good" — set the real spec later. Where a disc
    # lands by verdict is LANES (module level, shared with Reject).
    C_MIN = 0.0
    C_MAX = 1.0e9
    # The place into ANY out holder (good and bad — Sort and Reject both
    # drop through _drop):
    #   soft_approach=True  stop at the gap above the slot and take the
    #       final descent as its own straight leg — under smove travel the
    #       blended approach curved close enough to brush the rack (bench,
    #       replay-recorded).
    #   soft_exit=True      the mirror on the way out: the pull-off to the
    #       gap is its own straight leg ending at a stop, then the lift —
    #       never a fused curve inside the stack.
    DROP_PRM = dict(gravity_offset=PLACE_GRAV, soft_approach=True, soft_exit=True)
    params   = ["disc"]
    duration = 10
    resource = "robot"

    def pre(self, disc):
        return off_anode(disc) & anode_checked(disc) & ~done(disc)

    def eff(self, disc):
        # Disc dropped into the out holder: hand frees, disc done.
        return {"sorted": (+sorted_(disc), +done(disc), +hand_empty())}

    def execute(self, disc):
        rt = self.ctx.runtime
        facts = (getattr(self.ctx, "state", None) or {}).get("facts") or set()
        verdict = self.ctx.meta.get("verdict", {}).get(disc)
        if (anode_stuck.name, disc) in facts:
            lane, why = "fail_stuck", "anode still occupied after the pick"
        elif verdict is not None:
            lane, why = "fail_visual", verdict
        else:
            c = self.ctx.meta.get("disc_c", {}).get(disc)
            if c is None:
                # Unreachable by design: Measure blocks until it has a real
                # reading, so every measured disc has one. If this ever
                # fires it's a logic bug (fact set without execute running)
                # — fail loudly rather than silently binning a good disc.
                rt.step(f"disc {disc + 1}: no capacitance recorded — cannot sort", level="error")
                return False
            inside = self.C_MIN <= c <= self.C_MAX
            lane, why = ("pass" if inside else "fail_measure"), f"C = {c:g}"
        return "sorted" if _drop(self, disc, lane, why) else False


class Park(Action):
    """Final park — after every disc is done (sorted, good or bad)."""
    params      = []
    duration    = 5
    resource    = "robot"
    PARK_JOINTS = [0, 90, 0, 0, 0, 0, 100]
    RUN_END     = "completed"      # rt.count("run") field: how this run ended

    def pre(self):
        # Every disc STILL IN THE RUN — a removed one is never done.
        expr = ~parked() & started()
        for d in self._ctx_items():
            expr = expr & done(d)
        return expr

    def eff(self):
        return {"parked": (+parked(),)}

    def execute(self):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        # Status from the facts as they stand — "sorted" for a finished
        # disc, else the last fact it reached (an OperatorPark mid-run).
        facts = (getattr(self.ctx, "state", None) or {}).get("facts") or set()
        n_removed = 0
        for d in self._ctx_all_objects().get("disc", []):
            if (created.name, d) not in facts:
                continue                      # never entered the bench: no row
            remove = self._ctx_removed(d)
            n_removed += int(bool(remove))
            rt.record(_tag(d), status=(f"removed: {remove['by']} -> {remove['outcome']} ({remove['phase']})"
                                       if remove else _status_of(facts, d)))
        rt.count("disc.removed", n=n_removed)
        rt.count("run", n=1, **{self.RUN_END: 1})
        # Move to the park pose. Recipe.park is a base move-to-joint
        # (collision-aware + a checkpoint so Pause/Resume stays live).
        rcp["robot"].park(joint=self.PARK_JOINTS)
        rt.step(100, level="progress")
        meta = self.ctx.meta
        _publish(self, "Parked")     # the tally line carries the counts
        return "parked"


class OperatorPark(Park):
    """Operator-initiated park — fires on the Park button, outside the plan."""
    trigger = "park"
    RUN_END = "operator_park"


# The route — the order an item meets the actions (workspace.bt.protocol).
# Being listed here is what makes an action part of the run.
ROUTE = [Start, ClearAnode, Create, Pick, Present, InspectBottom, Reject, PlaceAnode, InspectTop,
         CathodeDown, Measure, CathodeUp, PickAnode, CheckAnode, Sort, Park]
