# Out Holders — where each disc lands

Three OUT holders, seven positions each (A1 … A7), a stack of up to
`stack_size` discs (200, `hmi/default.j2`) per position. A position is
filled from the bottom up, one disc lifting the next drop by 0.254 mm,
and when it is full the lane moves to its next position. The order never
changes within a run, so a holder reads like a log: the lower a disc in
a stack, the earlier it came through.

## Pass 1, Pass 2 — the good discs

A disc passed by both cameras whose measurement is inside the window:
capacitance between the run's `c_min_nf` and `c_max_nf` (34–41 nF
by default) and dissipation factor D at most `d_max` (0.02) — the setup
screen's Measurement step (`Sort`, `actions.py`).

A pass disc goes to the **same position it was picked from**, in the
pass holder of the same number: **In 1 A3 → Pass 1 A3**, **In 2 A5 →
Pass 2 A5**. So each pass holder reads like its IN holder, and you can
see at a glance which pass stack came from which IN stack. A position
holds at most a stack's worth on both sides, so a pass position never
fills up mid-run.

## Fail 1 — the bad discs, by what went wrong

The fail holder is split into three lanes. Which lane a disc goes to
says what failed it, so the holder can be read without the sheet. The
setup screen and the pendant name them under the Fail row: **Capacitor**
(A1–A3), **Visual** (A4–A6), **Stuck** (A7).

### A1 – A3 · capacitor fail

Both cameras passed the disc, the meter read it, the reading is outside
the window. The value is in the run's records sheet (`c_f`). Fills
A1 → A3 in order.

### A4 – A6 · visual fail

A camera said the disc is bad, or saw no disc at all:

- **Bottom (station) camera, before the anode.** No circle the disc's
  size; a chip (its edge's deepest dent past the run's `ring_chip`); no
  inner circle of the band; or off-centre (beyond the run's
  `ring_offset`); or the face anomaly check failed it (the run's `classifier`
  sensitivity). The disc goes
  straight to A4 – A6 without a measurement. If the camera saw *no* disc, the hand
  still goes through the drop (the suction is never released mid-way),
  but nothing is counted: the position's fill count and the fail tally
  do not move, so an empty hand never lifts the next real drop.
- **Top (robot) camera, on the anode.** No circle the disc's size; no
  inner circle of its band; off-centre (beyond the run's `ring_offset`);
  or the face anomaly check failed it. No chip check from the top — the chips
  show from the bottom. No clamp, no reading: the disc is
  picked back off the anode and dropped at A4 – A6, counted. "No disc"
  here counts too, since the bottom camera had seen it.

Fills A4, A5, then A6.

### A7 · stuck on the anode

After the pick off the anode the robot camera still saw a disc on the
anode — an extra disc came up with the pick, or one stayed behind.
Whatever the hand holds at that moment (the disc, a second one that came
up with it, or nothing) is dropped at A7 and counted as one fail. The
anode is then blown clear before the next disc is placed, up to
`ClearAnode.MAX_BLOWS` tries, after which the run pauses for you to
clear it by hand and Resume.

## What never reaches an out holder

Two "no disc" verdicts in a row from the same IN position, by either
camera, mean that stack has run out (`EMPTY_RUN_ENDS_COLUMN`). The
discs the operator marked there but that were never picked are **void**:
they are not in any holder, and their rows in the records sheet read
`status = void`. The run moves to the next IN position.

## When a lane is full

A lane with no room left stops the run; the log says which lane
("the fail_measure lane is FULL — …"). Empty that lane's positions in
the holder, then start the next batch: the fill counts begin at zero
with every run, they are not carried over.

## Reading the sheet

One row per disc in `records/<run>/records.csv`:

| column | reads | meaning |
|---|---|---|
| `result` | `pass` / `fail` | which kind of holder |
| `out` | `Pass 1 A3`, `Fail 1 A7`, `Fail 1 A4 (empty hand)` | holder and position; `(empty hand)` = nothing was dropped or counted |
| `reason` | `C = 4.7e-07`, `bottom camera: chip`, `bottom camera: off-centre`, `top camera: off-centre`, `bottom camera: face anomaly`, `top camera: no disc`, `anode still occupied after the pick` | why it went there |
| `ring_dent_px`, `ring_dent_at`, `ring_offset_px`, `ring_box_ratio` | numbers | the station camera's circles, every disc it measured (`top_ring_offset_px`, `top_ring_box_ratio` the robot camera's) — choose the run's sensitivities from these |
| `c_f` | farads | the capacitance, when the disc was measured |
| `d` | — | the dissipation factor D, when the disc was measured |
| `visual_card` | `fails/disc_007_bottom.jpg` | a camera fail's card, kept beside the sheet |

The pendant shows the same: a count in every OUT position, the pass /
fail tally, and the last reading.

Every disc a camera failed (a chip, off-centre, no edge, no inner edge,
the classifier; not "no disc") keeps its card in `records/<run>/fails/`:
the disc's square, 800 px, nothing drawn (about 50 KB from the bottom
camera, 90 KB from the top), and a `.json` beside it with the card's
numbers and the thresholds in force, in that picture's pixels — when the
run's **Fail pictures** setting (`save_fail_pictures`, the setup screen's
Inspection step) is On. It is Off by default: nothing is saved and the
sheet has no `visual_card`; `reason` still says why each disc failed.

---

## Quick reference

| Holder | Positions | Holds | Decided by |
|---|---|---|---|
| Pass 1, Pass 2 | the IN position it came from (In 2 A3 → Pass 2 A3) | capacitance inside the window | the meter (`Sort`) |
| Fail 1 | A1 → A3 · Capacitor | reading outside the window | the meter (`Sort`) |
| Fail 1 | A4 → A6 · Visual | a camera said fail, or saw no disc | station camera (`Reject`), robot camera (`Sort`) |
| Fail 1 | A7 · Stuck | a disc was still on the anode after the pick | robot camera (`CheckAnode`) |

Set in `actions.py`: the fail lanes (`LANES`), the pass mirror (`_lane`), the blow tries (`ClearAnode.MAX_BLOWS`), the empties
that end a position (`EMPTY_RUN_ENDS_COLUMN`). The stack size and the
measurement window (`c_min_nf`, `c_max_nf`, `d_max`) are in `hmi/default.j2`.
