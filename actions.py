"""apc protocol — Start → [per-disc pipeline] ×(inventory total) → Park.

IN inventory comes from hmi/default.j2 through the setup screen: two lists
of 7 (``in_1``, ``in_2``), index i = anchor A<i+1> of that holder, 0 for an
empty position and any positive value (the screen writes MAX_PER_SLOT) for
a FULL stack of MAX_PER_SLOT discs. The operator only ever says full or
empty; the count is this file's.
Discs are consumed TOP-of-stack first, A1→A7, in_1 until empty, then
in_2. Each disc appears in the scene the moment it's about to be picked
(create-on-demand, one at a time via feed_free) at its stack position
(z = depth × Z_STEP) — the racks hold at most one transient disc while
the counts still come from the configured inventory.

Each disc goes through a SPLIT chain of small BT actions, threaded by
facts (the BT moves action→action as each eff is asserted). Per disc i:

   1. Create        spawn the disc at its inventory position (top of the
                    remaining stack at its in-holder anchor).
   2. Pick          suction-pick it off the IN stack.
   3. Present       carry it to the vertical inspection station.
   4. InspectBottom station camera: DETECT the disc (model/disc.pkl), then
                    CLASSIFY the same view cropped to its box + CLS_ROI_OFFSET
                    px (model/disc_pass_fail_cropped.pkl). Three outcomes:
                      pass  → on to the anode;
                      fail  → Reject: straight to the fail column;
                      empty → no disc in the gripper: the stack was shorter
                              than the operator's mark. The column is
                              finished — this disc and every later disc of
                              that column are VOID, and the run moves to
                              the next column.
   5. Reject        (fail only) drop the held disc into the fail column.
   6. PlaceAnode    place the disc on the anode's "place" anchor, stand
                    where the robot camera sees it.
   7. InspectTop    robot camera: the same detect-then-classify. pass → the
                    measurement; fail → skip it, PickAnode takes the disc
                    straight to the fail column. No disc seen on the anode
                    is a read failure (operator, Resume), never a verdict.
   8. CathodeDown   drive the rotating cylinder down so the cathode contacts
                    the disc (clamped anode ↔ cathode).
   9. Measure       read the multimeter capacitance → record it for the disc.
  10. CathodeUp     retract the cylinder (cathode up).
  11. PickAnode     suction-pick the disc back off the anode.
  12. Sort          drop it into an OUT holder: a top-camera fail → bad;
                    else C_MIN ≤ C ≤ C_MAX → good (fill out_good_1, then
                    _2), otherwise bad (out_bad_1). Ordered fill (below).

Then Park once every disc is DONE — sorted, rejected or void. ``done`` is
the closure fact every way out asserts; ``sorted`` / ``void`` say which. ``ROUTE`` at the bottom of this
file is that order, and being listed there is what makes an action part
of the run (bt-framework-guide §13). A FLAT project: discs are strictly
serial through the bench (feed / hand / anode each hold one) and a sorted
disc is terminal, so there is no line the batch regroups at and no
``route: phases.py`` — the ROUTE lives here.

AUDIT. One ``rt.record`` row per disc (project-guide §3), keyed
``disc <n>``: seeded by Create with where it came from, ``visual_bottom``
/ ``visual_top`` by the two inspections, ``c_f`` by Measure on a valid
reading, ``result`` / ``out`` / ``reason`` by the drop, ``status`` derived
from the facts at Park. The run's ``results/<start>/records.csv``
is the client's sheet.

The DROP is ORDERED by a fill counter (ctx.meta["filled"]):
  * good fills out_good_1 completely, then out_good_2; bad fills out_bad_1.
  * within a holder: slots A1 → A7 in order.
  * within a slot: z starts at 0 and steps by Z_STEP per disc, up to
    MAX_PER_SLOT discs.
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
five holders as per-position STATES (never counts — the operator never
typed one), the pass/fail tally and the last reading. hmi/pendant.js
binds to exactly these keys; change them together.
"""

from __future__ import annotations

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
sorted_      = predicate("sorted")       # disc dropped into an out holder
void         = predicate("void")         # never existed: its column ran out
done         = predicate("done")         # THE closure fact — sorted or void
parked       = predicate("parked")

# ── Single-occupancy resources (capacity-1, no args) ──────────────────
# Three shared slots, each holding ONE disc at a time. Without these the
# planner runs actions in parallel across discs — creating several discs
# up front (they pile on the feed), or two discs on the anode, or picking
# while the cathode is down. Each fact is consumed (-fact) when its slot
# fills and restored (+fact) when it empties, forcing strictly
# one-disc-at-a-time:
#   feed_free  — one disc at a time between Create and the station
#                camera's verdict. Restored by InspectBottom, NOT by Pick:
#                the next Create waits until the camera has settled what
#                the pick actually took, so a column found empty can void
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


# ── Exposed, tweakable parameters ─────────────────────────────────────
SLOTS       = [f"A{c}" for c in range(1, 7 + 1)]  # A1 .. A7, in order
Z_STEP      = 0.254                            # per-disc stack lift (mm), in + out
MAX_PER_SLOT = 255                             # discs per slot before next slot

# Visual inspection: the detector runs on the WHOLE frame (no ROI — it
# finds the disc itself); the classifier then sees the detector's box
# grown by this many px (roi.offset on the box corners), cropped — the
# model was trained on cropped discs (model/disc_pass_fail_cropped.pkl).
CLS_ROI_OFFSET     = 100

# Suction motion offsets (mirror the runtime example).
PICK_TCP_Z   = -5                             # suction drives deeper to grab
PLACE_GRAV   = -5                              # suction presses on release

_STEPS = 12                                    # per-disc steps for progress (nominal)


# ── Ordered-drop position — a simple counter ──────────────────────────
# Where the next disc goes is tracked by a per-holder fill COUNT in
# ctx.meta["filled"] = {holder_alias: n_dropped}. From the count we derive
# (slot, z) deterministically: slot = SLOTS[count // MAX_PER_SLOT], z =
# (count % MAX_PER_SLOT) * Z_STEP; roll to the next holder when the current
# is full. This is runtime state (lives in execute, never in planner
# facts), so it's BT-legal. It does NOT survive a restart mid-batch (the
# count resets); fine here because a batch is run start-to-finish and
# sorted discs are terminal — every one is DELETED right after place().

def _next_drop(filled, holders):
    """Next (holder_alias, slot, z, count) from the per-holder fill counts.
    Fills slot A1→A7, stacking z by Z_STEP up to MAX_PER_SLOT, holder by
    holder. Returns None when every holder is full."""
    cap = len(SLOTS) * MAX_PER_SLOT
    for holder in holders:
        count = filled.get(holder, 0)
        if count < cap:
            slot = SLOTS[count // MAX_PER_SLOT]
            z = round((count % MAX_PER_SLOT) * Z_STEP, 3)
            return holder, slot, z, count
    return None


# ── Generic helpers ───────────────────────────────────────────────────

def _disc(disc: int) -> str:
    return f"disc_{disc}"


def _column_mates(disc: int) -> list:
    """The discs still to come from the same IN position as ``disc`` —
    what an empty pick voids. Contiguous by construction (INVENTORY is
    built column by column, top of the stack first)."""
    col = INVENTORY[disc][:2]
    return [d for d in range(disc + 1, len(INVENTORY)) if INVENTORY[d][:2] == col]


def _verdict(res) -> str:
    """The classifier's word. ``pass`` only when its top class says so;
    anything else — ``fail``, or no class above conf (an empty list) — is
    a fail. A disc the model cannot vouch for never reaches the anode."""
    return "pass" if res and res[0].get("cls") == "pass" else "fail"


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
    action.ctx.runtime.step(
        f"{od_alias}: {len(found)} found, best {best.get('conf', 0):.2f} at "
        f"x {min(xs):.0f}..{max(xs):.0f} y {min(ys):.0f}..{max(ys):.0f} "
        f"({max(xs) - min(xs):.0f}x{max(ys) - min(ys):.0f} px) -> {cls_alias} roi +{CLS_ROI_OFFSET} px")
    res = rcp[cls_alias].detect(
        roi={"corners": box, "offset": CLS_ROI_OFFSET, "crop": True},
        sim_return=[{"cls": "pass", "conf": 0.99}])
    if res is None:
        return None
    if res:
        action.ctx.runtime.step(f"{cls_alias}: {res[0].get('cls')} {res[0].get('conf', 0):.2f}")
    return _verdict(res)


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
    discs = action._ctx_all_objects().get("disc", [])
    total = (len(discs) or 1) * _STEPS
    ctx_state = getattr(action.ctx, "state", None) or {}
    facts = ctx_state.get("facts") or set()
    done = 0
    for d in discs:
        for p in _CHAIN:
            if (p.name, d) in facts:
                done += 1
    return int((done + 1) / total * 100)


# The per-disc chain in order — what progress and the audit status read.
_CHAIN = (created, picked, presented, inspected, bottom_failed, on_anode,
          anode_inspected, top_failed, cathode_down, measured, cathode_up,
          off_anode, sorted_, void)


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
      done    loaded, and every disc has been taken — or the camera found
              the stack empty early (``void``: the column is finished)
    ``loaded`` / ``picked`` map (holder, slot) → n; ``active`` is one
    (holder, slot) or None; ``void`` is a set of (holder, slot)."""
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


def _out_states(filled):
    """Per-position state of the three OUT holders, from the same fill
    counter Sort uses (_next_drop): empty | filling | full."""
    out = {}
    for alias, key in OUT_KEYS:
        c = filled.get(alias, 0)
        row = []
        for i in range(len(SLOTS)):
            n = max(0, min(MAX_PER_SLOT, c - i * MAX_PER_SLOT))
            row.append("empty" if n == 0 else "full" if n >= MAX_PER_SLOT else "filling")
        out[key] = row
    return out


def _tag(disc):
    # No total: the operator never typed a count and is not shown one.
    return f"Disc {disc + 1}"


def _pos(holder, slot):
    return f"In {holder} {slot}"


def _publish(action, headline=None, active=None, **extra):
    """Push the operator-facing picture to the pendant. Replace semantics
    per key (Runtime.op); observability never blocks the workflow."""
    meta = action.ctx.meta
    vals = dict(
        in_stacks=_in_states(LOADED, meta.get("picked_from", {}), active,
                             meta.get("void", set())),
        out_stacks=_out_states(meta.get("filled", {})),
        total_n=len(INVENTORY),
        pass_n=meta.get("pass_n", 0),
        fail_n=meta.get("fail_n", 0),
        done_n=meta.get("pass_n", 0) + meta.get("fail_n", 0),
    )
    if headline is not None:
        vals["headline"] = headline
    vals.update(extra)
    action.ctx.runtime.op(**vals)


# ── setup ─────────────────────────────────────────────────────────────

def setup(**kwargs):
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


    return {
        "initial_facts": frozenset(),
        "goal":          goal,
        "item_done":     item_done,
        "objects":       {"disc": discs},
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
        self.ctx.meta["void"] = set()
        self.ctx.meta["pass_n"] = 0
        self.ctx.meta["fail_n"] = 0
        _publish(self, "Starting — homing", last_disc=None, last_c=None,
                 last_c_unit=None, last_result=None)
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
        # Move to a known ready pose (Recipe.park is a base move-to-joint
        # on the generic component-less "robot" recipe).
        rcp["robot"].park(joint=self.START_JOINTS)
        return "started"


class Create(Action):
    """Spawn the disc at its configured inventory position — the top of
    the remaining stack at its in-holder anchor (z = depth × Z_STEP)."""
    params   = ["disc"]
    duration = 2
    resource = "robot"

    def pre(self, disc):
        # feed_free gates one disc between Create and the camera's verdict;
        # ~done skips a disc its column's empty pick already voided.
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
    """Suction-pick the disc off the IN stack."""
    # soft_approach=True: stop at the gap above the stack, straight final
    # descent — matches the Sort side (smove travel blends otherwise).
    PRM      = dict(tool_tcp_z_offset=PICK_TCP_Z, soft_approach=True)
    params   = ["disc"]
    duration = 10
    resource = "robot"

    def pre(self, disc):
        # hand_empty gates one-disc-at-a-time in the gripper.
        return created(disc) & hand_empty() & ~picked(disc)

    def eff(self, disc):
        # Disc into the hand: hand fills. The feed stays busy until
        # InspectBottom has seen what the pick took (or that it took nothing).
        return {"picked": (+picked(disc), -hand_empty())}

    def execute(self, disc):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        in_h, slot, _z = INVENTORY[disc]   # same position the disc was created at
        rt.step(f"disc {disc + 1}: pick from in_{in_h}[{slot}]")
        rt.step(_progress_pct(self), level="progress")
        _publish(self, f"{_tag(disc)} — picking from {_pos(in_h, slot)}", active=(in_h, slot))
        rcp[f"disc_in_{in_h}"].pick(slot, **self.PRM)
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
    the detector found. A sensing action with three outcomes
    (bt-framework-guide §7): ``pass`` (default), ``fail``, ``empty``.

    A failed read (camera down) returns False — the success facts are
    asserted only on a valid reading; the planner re-selects this action
    after the operator recovers the camera and resumes (the scale
    pattern, project-guide §8). A dead camera raises
    CameraUnavailableError and pauses like any critical device.

    ``empty`` — the detector sees no disc in the gripper: the operator
    marked the stack full, but it ran out. The suction let go of air; the
    column is finished. This disc and every later disc of that column are
    VOID (done, never created), and the feed opens for the next column.
    """
    params   = ["disc"]
    duration = 6
    resource = "robot"

    def pre(self, disc):
        return presented(disc) & ~inspected(disc) & ~bottom_failed(disc) & ~done(disc)

    def eff(self, disc):
        mates = _column_mates(disc)
        return {
            "pass":  (+inspected(disc), +feed_free()),
            "fail":  (+bottom_failed(disc), +feed_free()),
            # Nothing in the hand: hand empty, feed open, this column void.
            "empty": (+void(disc), +done(disc), +hand_empty(), +feed_free(),
                      *(f for d in mates for f in (+void(d), +done(d)))),
        }

    def execute(self, disc):
        rt, ws = self.ctx.runtime, self.ctx.workspace
        rt.step(f"disc {disc + 1}: inspect bottom")
        rt.step(_progress_pct(self), level="progress")
        _publish(self, f"{_tag(disc)} — inspecting")
        v = _inspect(self, "inspector", "inspector_cls")
        if v is None:
            rt.step(f"disc {disc + 1}: inspection read failed — recover the camera, then Resume")
            return False
        if v == "empty":
            in_h, slot, _z = INVENTORY[disc]
            mates = _column_mates(disc)
            rt.step(f"disc {disc + 1}: no disc in the gripper — in_{in_h}[{slot}] is "
                    f"empty, {len(mates)} more discs of that column voided")
            # Let go of the air, and drop the phantom disc from the scene.
            ws.components["gripper_suction_1"].disable()
            if _disc(disc) in ws.components:
                ws.remove_component(_disc(disc))
            self.ctx.meta.setdefault("void", set()).add((in_h, slot))
            rt.record(_tag(disc), visual_bottom="none")
            _publish(self, f"{_pos(in_h, slot)} is empty — moving on")
            return "empty"
        rt.record(_tag(disc), visual_bottom=v)
        if v == "fail":
            rt.step(f"disc {disc + 1}: FAILED bottom inspection → fail column")
            _publish(self, f"{_tag(disc)} — failed inspection", last_disc=disc + 1,
                     last_c=None, last_c_unit=None, last_result="fail")
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
        return "rejected" if _drop(self, disc, good=False, why="bottom camera") else False


class PlaceAnode(Action):
    """Place the disc on the anode's "place" anchor with a SHORT exit
    (EXIT_CLEARANCE mm above the disc — the recipe's exit-leg number
    form), then stand at VIEW_OFFSET so the robot camera has an
    unoccluded view of the disc for InspectTop."""
    VIEW_OFFSET = [10, 50, 70, 0, 0, 0]  # anchor-frame [x, y, z, a, b, c]
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
        # anode_free gates one-disc-at-a-time on the shared anode/cathode.
        return inspected(disc) & anode_free() & ~on_anode(disc)

    def eff(self, disc):
        # Disc leaves the hand onto the anode: hand frees, anode occupied.
        return {"on_anode": (+on_anode(disc), +hand_empty(), -anode_free())}

    def execute(self, disc):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step(f"disc {disc + 1}: place on anode")
        rt.step(_progress_pct(self), level="progress")
        _publish(self, f"{_tag(disc)} — onto the anode")
        # exit=<number> pulls off just EXIT_CLEARANCE mm above the disc
        # (the approach keeps the recipe's full padding).
        rcp["anode"].place("place", exit=self.EXIT_CLEARANCE, **self.PRM)
        rcp["anode"].stand("place", offset=self.VIEW_OFFSET, **self.STAND_PRM)
        return "on_anode"


class InspectTop(Action):
    """Robot camera: the same detect-then-classify on the seated disc,
    before the measurement. ``pass`` (default) → measure; ``fail`` → the
    measurement is skipped and PickAnode takes it to the fail column.
    No disc seen on the anode is not a verdict — the disc was placed, so
    something is wrong on the bench: return False, operator, Resume.
    ``hand_empty`` in the pre keeps the arm at the anode hover: the
    planner cannot slot the next pick in between, so the camera is still
    over the anode when this runs."""
    params   = ["disc"]
    duration = 6
    resource = "robot"

    def pre(self, disc):
        return on_anode(disc) & hand_empty() & ~anode_inspected(disc) & ~top_failed(disc)

    def eff(self, disc):
        return {"pass": (+anode_inspected(disc),),
                "fail": (+top_failed(disc),)}

    def execute(self, disc):
        rt = self.ctx.runtime
        rt.step(f"disc {disc + 1}: inspect top")
        rt.step(_progress_pct(self), level="progress")
        _publish(self, f"{_tag(disc)} — inspecting on the anode")
        v = _inspect(self, "inspector_robot", "inspector_robot_cls")
        if v is None:
            rt.step(f"disc {disc + 1}: anode inspection failed — recover the camera, then Resume")
            return False
        if v == "empty":
            rt.step(f"disc {disc + 1}: no disc seen on the anode — check the anode, then Resume")
            return False
        rt.record(_tag(disc), visual_top=v)
        if v == "fail":
            self.ctx.meta.setdefault("verdict", {})[disc] = "top_fail"
            rt.step(f"disc {disc + 1}: FAILED top inspection → no measurement, fail column")
            _publish(self, f"{_tag(disc)} — failed inspection on the anode", last_disc=disc + 1,
                     last_c=None, last_c_unit=None, last_result="fail")
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
            rt.step(f"disc {disc + 1}: meter unavailable — reconnect the meter "
                    f"(check RMT is on), then Resume")
            rt.pause()
            rt.checkpoint()          # blocks until the operator resumes
            return False
        # Stash the measured value on the ctx so Sort can read it without
        # a planning fact (it's per-disc runtime data, not plan state).
        self.ctx.meta.setdefault("disc_c", {})[disc] = m.primary
        rt.step(f"disc {disc + 1}: C = {m.primary:g} {m.primary_unit}")
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


# soft_approach=True: stop at the gap above the OUT slot and take the
# final descent as its own straight leg — under smove travel the blended
# approach curved close enough to brush the rack (bench, replay-recorded).
DROP_PRM = dict(gravity_offset=PLACE_GRAV, soft_approach=True)


def _drop(action, disc, good, why) -> bool:
    """Drop the held disc into the next ordered slot of the good or bad
    holders (fill counter), then DELETE it — sorted discs are terminal and
    never linger in the scene. Shared by Sort and Reject. False when every
    holder of that kind is full (the action fails, the run pauses)."""
    rt, rcp, ws = action.ctx.runtime, action.ctx.recipes, action.ctx.workspace
    holders = Sort.GOOD_HOLDERS if good else Sort.BAD_HOLDERS
    filled = action.ctx.meta.setdefault("filled", {})   # holder → n dropped
    nxt = _next_drop(filled, holders)
    if nxt is None:
        rt.step(f"disc {disc + 1}: all {'good' if good else 'bad'} holders FULL")
        return False
    holder, slot, z, count = nxt
    rt.step(f"disc {disc + 1}: {'GOOD' if good else 'BAD'} ({why}) → {holder}[{slot}] z={z}")
    rt.step(_progress_pct(action), level="progress")

    # Place the held disc into the ordered slot, then DELETE it. Nothing
    # accumulates (no meshes/pickables piling up over ~3500 discs); the
    # fill counter, not the scene, tracks where the next disc goes.
    rcp[holder].place(slot, offset=[0, 0, z, 0, 0, 0], **DROP_PRM)
    if _disc(disc) in ws.components:
        ws.remove_component(_disc(disc))

    filled[holder] = count + 1
    key = "pass_n" if good else "fail_n"
    action.ctx.meta[key] = action.ctx.meta.get(key, 0) + 1
    name_out = dict(OUT_KEYS)[holder].replace("good_", "Pass ").replace("bad_", "Fail ")
    _publish(action, f"{_tag(disc)} — {'passed' if good else 'failed'} → {name_out} {slot}")
    rt.record(_tag(disc), result="pass" if good else "fail", out=f"{name_out} {slot}", reason=why)
    return True


class Sort(Action):
    """Drop the disc off the anode into an OUT holder: a top-camera fail
    is bad outright; otherwise the measured capacitance decides."""
    # Good/bad capacitance window (Farads). Defaulted WIDE so everything
    # currently lands in "good" — set the real spec later.
    C_MIN = 0.0
    C_MAX = 1.0e9
    # Ordered OUT-holder fill sequences (recipe aliases, in fill order).
    GOOD_HOLDERS = ["disc_out_good_1", "disc_out_good_2"]
    BAD_HOLDERS  = ["disc_out_bad_1"]
    params   = ["disc"]
    duration = 10
    resource = "robot"

    def pre(self, disc):
        return off_anode(disc) & ~done(disc)

    def eff(self, disc):
        # Disc dropped into the out holder: hand frees, disc done.
        return {"sorted": (+sorted_(disc), +done(disc), +hand_empty())}

    def execute(self, disc):
        rt = self.ctx.runtime
        if self.ctx.meta.get("verdict", {}).get(disc) == "top_fail":
            good, why = False, "top camera"
        else:
            c = self.ctx.meta.get("disc_c", {}).get(disc)
            if c is None:
                # Unreachable by design: Measure blocks until it has a real
                # reading, so every measured disc has one. If this ever
                # fires it's a logic bug (fact set without execute running)
                # — fail loudly rather than silently binning a good disc.
                rt.step(f"disc {disc + 1}: no capacitance recorded — cannot sort", level="error")
                return False
            good, why = self.C_MIN <= c <= self.C_MAX, f"C = {c:g}"
        return "sorted" if _drop(self, disc, good, why) else False


class Park(Action):
    """Final park — after every disc is done (sorted, rejected or void)."""
    params      = []
    duration    = 5
    resource    = "robot"
    PARK_JOINTS = [0, 90, 0, 0, 0, 0, 100]

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
        # Status from the facts as they stand — "sorted" / "void" for a
        # finished disc, else the last fact it reached (an OperatorPark
        # mid-run). A voided disc never entered the bench and has no row.
        facts = (getattr(self.ctx, "state", None) or {}).get("facts") or set()
        for d in self._ctx_all_objects().get("disc", []):
            if (created.name, d) not in facts:
                continue                      # never entered the bench: no row
            remove = self._ctx_removed(d)
            rt.record(_tag(d), status=(f"removed: {remove['by']} -> {remove['outcome']} ({remove['phase']})"
                                       if remove else _status_of(facts, d)))
        # Move to the park pose. Recipe.park is a base move-to-joint
        # (collision-aware + a checkpoint so Pause/Resume stays live).
        rcp["robot"].park(joint=self.PARK_JOINTS)
        meta = self.ctx.meta
        _publish(self, f"Parked — {meta.get('pass_n', 0)} passed, {meta.get('fail_n', 0)} failed")
        return "parked"


class OperatorPark(Park):
    """Operator-initiated park — fires on the Park button, outside the plan."""
    trigger = "park"


# The route — the order an item meets the actions (workspace.bt.protocol).
# Being listed here is what makes an action part of the run.
ROUTE = [Start, Create, Pick, Present, InspectBottom, Reject, PlaceAnode, InspectTop,
         CathodeDown, Measure, CathodeUp, PickAnode, Sort, Park]
