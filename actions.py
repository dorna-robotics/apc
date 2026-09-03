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

  1. Create       spawn the disc at its inventory position (top of the
                  remaining stack at its in-holder anchor).
  2. Pick         suction-pick it off the IN stack.
  3. Inspect      present to the inspection station + detect() (generic).
  4. PlaceAnode   place the disc on the anode's "place" anchor.
  5. CathodeDown  drive the rotating cylinder down so the cathode contacts
                  the disc (clamped anode ↔ cathode).
  6. Measure      read the multimeter capacitance → record it for the disc.
  7. CathodeUp    retract the cylinder (cathode up).
  8. PickAnode    suction-pick the disc back off the anode.
  9. Sort         drop it into an OUT holder by the measured C:
                  C_MIN ≤ C ≤ C_MAX → good (fill out_good_1, then _2);
                  otherwise → bad (out_bad_1). Ordered fill (see below).

Then Park once every disc is sorted.

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
inspected    = predicate("inspected")    # presented + detect() ran
on_anode     = predicate("on_anode")     # disc placed on the anode
cathode_down = predicate("cathode_down") # cylinder driven down (cathode contact)
measured     = predicate("measured")     # capacitance read for this disc
anode_inspected = predicate("anode_inspected")  # robot-camera check on the anode
cathode_up   = predicate("cathode_up")   # cylinder retracted
off_anode    = predicate("off_anode")    # disc re-gripped off the anode
sorted_      = predicate("sorted")       # disc dropped into an out holder
parked       = predicate("parked")

# ── Single-occupancy resources (capacity-1, no args) ──────────────────
# Three shared slots, each holding ONE disc at a time. Without these the
# planner runs actions in parallel across discs — creating several discs
# up front (they pile on the feed), or two discs on the anode, or picking
# while the cathode is down. Each fact is consumed (-fact) when its slot
# fills and restored (+fact) when it empties, forcing strictly
# one-disc-at-a-time:
#   feed_free  — only one un-picked disc may exist (create → pick → create
#                → pick…, never a batch of Creates ahead of the picks).
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

# Visual-inspection ROI: a 25x25x10 mm box over the disc. The ROI offset
# (px slack around the projected box) differs per station, so it lives on
# each Inspect class (InspectBottom.ROI_OFFSET / InspectTop.ROI_OFFSET).
# Horizontal station: box rides the gripper TCP (the disc is in hand).
# Robot camera at the anode: box sits on the anode place anchor.
INSPECT_BOX_WDH    = [25, 25, 10]
INSPECT_CROP       = True

# Suction motion offsets (mirror the runtime example).
PICK_TCP_Z   = -5                             # suction drives deeper to grab
PLACE_GRAV   = -5                              # suction presses on release

_STEPS = 11                                    # per-disc steps for progress


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
        for p in (created, picked, presented, inspected, on_anode,
                  anode_inspected, cathode_down, measured, cathode_up,
                  off_anode, sorted_):
            if (p.name, d) in facts:
                done += 1
    return int((done + 1) / total * 100)


# ── Pendant — what rt.op publishes ───────────────────────────────────
# States, never numbers: the operator marked each position full or empty
# and never saw a count, so the pendant shows the same vocabulary. Both
# helpers are pure so they can be checked without a runtime.
OUT_KEYS = (("disc_out_good_1", "good_1"), ("disc_out_good_2", "good_2"),
            ("disc_out_bad_1", "bad_1"))


def _in_states(loaded, picked, active=None):
    """Per-position state of the two IN holders.
      empty   nothing was loaded there
      full    loaded, discs remain
      active  the disc being picked right now comes from here
      done    loaded, and every disc has been taken
    ``loaded`` / ``picked`` map (holder, slot) → n; ``active`` is one
    (holder, slot) or None."""
    out = {}
    for h in (1, 2):
        row = []
        for slot in SLOTS:
            n, p = loaded.get((h, slot), 0), picked.get((h, slot), 0)
            if active == (h, slot):
                row.append("active")
            elif n <= 0:
                row.append("empty")
            elif p >= n:
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
        in_stacks=_in_states(LOADED, meta.get("picked_from", {}), active),
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
        return (sorted_.name, disc) in state

    def goal(state):
        return (
            (started.name,) in state
            and all(item_done(state, d) for d in discs)
            and (parked.name,) in state
        )

    goal_facts = frozenset(
        [(sorted_.name, d) for d in discs]
        + [(started.name,), (parked.name,)]
    )

    return {
        "initial_facts": frozenset(),
        "goal":          goal,
        "item_done":     item_done,
        "goal_facts":    goal_facts,
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
        for k in ("picked_from", "filled", "disc_c"):
            self.ctx.meta[k] = {}
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
        # feed_free gates one un-picked disc at a time (no batch of Creates).
        return started() & feed_free() & ~created(disc)

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
        # Disc leaves the feed into the hand: feed frees, hand fills.
        return {"picked": (+picked(disc), +feed_free(), -hand_empty())}

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
    """Camera read at the station — a DEVICE READ with declarative
    retry (the scale pattern, project-guide §8): the success fact is
    asserted only on a valid reading; a failed read returns False so
    the planner re-selects this action after the operator recovers the
    camera and resumes. A dead camera raises (CameraUnavailableError)
    and pauses like any critical device."""
    ROI_OFFSET = 40   # px slack around the projected box (station camera)
    params   = ["disc"]
    duration = 4
    resource = "robot"

    def pre(self, disc):
        return presented(disc) & ~inspected(disc)

    def eff(self, disc):
        return {"inspected": (+inspected(disc),)}

    def execute(self, disc):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step(f"disc {disc + 1}: inspect bottom")
        rt.step(_progress_pct(self), level="progress")
        _publish(self, f"{_tag(disc)} — inspecting")
        # ROI box rides the gripper TCP — the disc is in the hand; the
        # box is projected with this frame's camera_in_world.
        tool = self.ctx.core.current_tool()
        tcp = tool.assembly[next(iter(tool.assembly))].pose("tcp")
        res = rcp["inspector"].detect(
            roi={"box": [float(v) for v in tcp] + INSPECT_BOX_WDH,
                 "offset": self.ROI_OFFSET, "crop": INSPECT_CROP})
        if res is None:
            rt.step(f"disc {disc + 1}: inspection read failed — recover the camera, then Resume")
            return False
        return "inspected"


class PlaceAnode(Action):
    """Place the disc on the anode's "place" anchor with a SHORT exit
    (EXIT_CLEARANCE mm above the disc — the recipe's exit-leg number
    form), then stand at VIEW_OFFSET so the robot camera has an
    unoccluded view of the disc for InspectTop."""
    VIEW_OFFSET = [0, 75, 60, 0, 0, 0]   # anchor-frame [x, y, z, a, b, c]
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
    """Robot-camera read of the seated disc, before the measurement —
    same declarative-retry contract as Inspect. ``hand_empty`` in the
    pre keeps the arm parked at the anode hover: the planner cannot
    slot the next pick in between, so the camera is still over the
    anode when this runs."""
    ROI_OFFSET = 20   # px slack around the projected box (robot camera)
    params   = ["disc"]
    duration = 4
    resource = "robot"

    def pre(self, disc):
        return on_anode(disc) & hand_empty() & ~anode_inspected(disc)

    def eff(self, disc):
        return {"anode_inspected": (+anode_inspected(disc),)}

    def execute(self, disc):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step(f"disc {disc + 1}: inspect top")
        rt.step(_progress_pct(self), level="progress")
        _publish(self, f"{_tag(disc)} — inspecting on the anode")
        anode_body = self.ctx.workspace.components["anode_1"].assembly["body"]
        res = rcp["inspector_robot"].detect(
            roi={"box": [float(v) for v in anode_body.pose("place")] + INSPECT_BOX_WDH,
                 "offset": self.ROI_OFFSET, "crop": INSPECT_CROP})
        if res is None:
            rt.step(f"disc {disc + 1}: anode inspection failed — recover the camera, then Resume")
            return False
        return "anode_inspected"


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
        # The value itself goes to the pendant's reading card (SI-scaled
        # there); the headline stays a step, not a number.
        _publish(self, f"{_tag(disc)} — measured",
                 last_disc=disc + 1, last_c=m.primary, last_c_unit=str(m.primary_unit),
                 last_result="pass" if C_MIN <= m.primary <= C_MAX else "fail")
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
        # hand_empty required to re-grip; frees the anode for the next disc.
        return cathode_up(disc) & hand_empty() & ~off_anode(disc)

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


class Sort(Action):
    """Drop the disc into an OUT holder by its measured capacitance, into
    the next ordered slot (fill counter), then delete it — sorted discs
    are terminal and never linger in the scene."""
    # soft_approach=True: stop at the gap above the OUT slot and take the
    # final descent as its own straight leg — under smove travel the
    # blended approach curved close enough to brush the rack (bench,
    # replay-recorded).
    PRM      = dict(gravity_offset=PLACE_GRAV, soft_approach=True)
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
        return off_anode(disc) & ~sorted_(disc)

    def eff(self, disc):
        # Disc dropped into the out holder: hand frees.
        return {"sorted": (+sorted_(disc), +hand_empty())}

    def execute(self, disc):
        rt, rcp, ws = self.ctx.runtime, self.ctx.recipes, self.ctx.workspace

        c = self.ctx.meta.get("disc_c", {}).get(disc)
        if c is None:
            # Unreachable by design: Measure blocks until it has a real
            # reading, so every sorted disc has one. If this ever fires it's
            # a logic bug (fact set without execute running) — fail loudly
            # rather than silently binning a good disc as BAD.
            rt.step(f"disc {disc + 1}: no capacitance recorded — cannot sort", level="error")
            return False
        good = self.C_MIN <= c <= self.C_MAX
        holders = self.GOOD_HOLDERS if good else self.BAD_HOLDERS

        filled = self.ctx.meta.setdefault("filled", {})   # holder → n dropped
        nxt = _next_drop(filled, holders)
        if nxt is None:
            rt.step(f"disc {disc + 1}: all {'good' if good else 'bad'} holders FULL")
            return False
        holder, slot, z, count = nxt
        rt.step(f"disc {disc + 1}: {'GOOD' if good else 'BAD'} → {holder}[{slot}] z={z}")
        rt.step(_progress_pct(self), level="progress")

        # Place the held disc into the ordered slot, then DELETE it. Sorted
        # discs are terminal — we don't keep any in the scene, so nothing
        # accumulates (no meshes/pickables piling up over ~3500 discs). The
        # fill counter, not the scene, tracks where the next disc goes.
        # place() re-attaches the held disc_<i> into the slot; remove it.
        rcp[holder].place(slot, offset=[0, 0, z, 0, 0, 0], **self.PRM)
        name = _disc(disc)
        if name in ws.components:
            ws.remove_component(name)

        filled[holder] = count + 1
        self.ctx.meta["pass_n" if good else "fail_n"] = \
            self.ctx.meta.get("pass_n" if good else "fail_n", 0) + 1
        name_out = dict(OUT_KEYS)[holder].replace("good_", "Pass ").replace("bad_", "Fail ")
        _publish(self, f"{_tag(disc)} — {'passed' if good else 'failed'} → {name_out} {slot}")
        return "sorted"


class Park(Action):
    """Final park — after every disc is sorted."""
    params      = []
    duration    = 5
    resource    = "robot"
    PARK_JOINTS = [0, 90, 0, 0, 0, 0, 100]

    def pre(self):
        discs = self._ctx_all_objects().get("disc", [])
        expr = ~parked() & started()
        for d in discs:
            expr = expr & sorted_(d)
        return expr

    def eff(self):
        return {"parked": (+parked(),)}

    def execute(self):
        rcp = self.ctx.recipes
        # Move to the park pose. Recipe.park is a base move-to-joint
        # (collision-aware + a checkpoint so Pause/Resume stays live); apc has
        # no gripper/tool recipe, so we borrow "inspector".
        rcp["robot"].park(joint=self.PARK_JOINTS)
        meta = self.ctx.meta
        _publish(self, f"Parked — {meta.get('pass_n', 0)} passed, {meta.get('fail_n', 0)} failed")
        return "parked"


class OperatorPark(Park):
    """Operator-initiated park — fires on the Park button, outside the plan."""
    trigger = "park"
