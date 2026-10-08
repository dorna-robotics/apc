# Out Holders — where each disc lands

Three OUT holders, seven positions each (A1 … A7), a stack of up to
`stack_size` discs (200, `hmi/default.j2`) per position. A position is
filled from the bottom up, one disc lifting the next drop by 0.254 mm,
and when it is full the lane moves to its next position. The order never
changes within a run, so a holder reads like a log: the lower a disc in
a stack, the earlier it came through.

## Pass 1, Pass 2 — the good discs

A disc passed by both cameras whose capacitance reading is inside the
window (`Sort.C_MIN … C_MAX`, `actions.py`).

Fills **Pass 1 A1 → A7**, then **Pass 2 A1 → A7**. Fourteen positions
of 200 take a whole run (both IN holders full), so the pass lane never
fills up mid-run.

## Fail 1 — the bad discs, by what went wrong

The fail holder is split into three lanes. Which lane a disc goes to
says what failed it, so the holder can be read without the sheet.

### A1 – A4 · measurement fail

Both cameras passed the disc, the meter read it, the reading is outside
the window. The value is in the run's records sheet (`c_f`). Fills
A1 → A4 in order.

### A5 · stuck on the anode

After the pick off the anode the robot camera still saw a disc on the
anode. Whatever the hand holds at that moment (the disc, a second one
that came up with it, or nothing) is dropped at A5 and counted as one
fail. The anode is then blown clear before the next disc is placed, up
to `ClearAnode.MAX_BLOWS` tries, after which the run pauses for you to
clear it by hand and Resume.

### A6 – A7 · camera fail

A camera said the disc is bad, or saw no disc at all:

- **Bottom (station) camera, before the anode.** The disc goes straight
  to A6 / A7 without a measurement. If the camera saw *no* disc, the hand
  still goes through the drop (the suction is never released mid-way),
  but nothing is counted: the position's fill count and the fail tally
  do not move, so an empty hand never lifts the next real drop.
- **Top (robot) camera, on the anode.** No clamp, no reading: the disc is
  picked back off the anode and dropped at A6 / A7, counted. "No disc"
  here counts too, since the bottom camera had seen it.

Fills A6, then A7.

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
| `out` | `Pass 1 A3`, `Fail 1 A5`, `Fail 1 A6 (empty hand)` | holder and position; `(empty hand)` = nothing was dropped or counted |
| `reason` | `C = 4.7e-07`, `bottom camera: fail`, `top camera: no disc`, `anode still occupied after the pick` | why it went there |
| `c_f` | farads | the reading, when the disc was measured |

The pendant shows the same: a count in every OUT position, the pass /
fail tally, and the last reading.

---

## Quick reference

| Holder | Positions | Holds | Decided by |
|---|---|---|---|
| Pass 1, Pass 2 | A1 → A7, A1 → A7 | capacitance inside the window | the meter (`Sort`) |
| Fail 1 | A1 → A4 | reading outside the window | the meter (`Sort`) |
| Fail 1 | A5 | a disc was still on the anode after the pick | robot camera (`CheckAnode`) |
| Fail 1 | A6 → A7 | a camera said fail, or saw no disc | station camera (`Reject`), robot camera (`Sort`) |

Set in `actions.py`: the lanes (`LANES`), the window (`Sort.C_MIN`,
`Sort.C_MAX`), the blow tries (`ClearAnode.MAX_BLOWS`), the empties
that end a position (`EMPTY_RUN_ENDS_COLUMN`). The stack size is
`stack_size` in `hmi/default.j2`.
