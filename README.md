# apc

Disc capacitance sort. Each disc is picked off an IN stack, inspected
at the vertical camera station, placed on the anode, inspected by the
robot camera, clamped by the cathode, its capacitance read on the
BK 879B, then dropped into a PASS or FAIL holder by the window in
`Sort` (`C_MIN..C_MAX`, `actions.py`). A FLAT BT project: one disc
through the bench at a time, `ROUTE` at the bottom of `actions.py`.

Each inspection is two models on one camera: the detector finds the
disc, the classifier judges the crop around its box (`CLS_ROI_OFFSET`
px of margin). A classifier fail at either camera sends the disc to
the fail column with no measurement. The station camera seeing NO disc
means the IN stack ran out before the operator's mark: that column is
finished, its remaining discs are voided, and the run moves to the
next column.

## Layout

```
apc/
├── main.py             # canonical entry point — byte-identical to examples/*/main.py
├── launch.yaml         # project_name, port, scene, recipes, actions, checks, hmi pointers
├── actions.py          # predicates, setup(), Start → per-disc chain → Park, ROUTE
├── checks.py           # vision / sensor checks (empty)
├── recipes.j2          # holders, stations, meter — recipe aliases + solved ref_joints
├── hmi/
│   ├── default.j2      # the kwargs: in_1 / in_2, one list of 7 per IN holder
│   ├── setup.js        # run-setup screen — click each IN position full / empty
│   └── pendant.js      # during-run screen — holders, tally, last reading
├── scene/
│   ├── core_500.j2     # chassis
│   ├── layout.j2       # holders, anode/cathode, cameras, meter
│   └── calibration.j2  # layout.j2 with the probe rod mounted (calibrate.ipynb)
├── components/         # anode, cathode (@register)
├── CAD/                # their .glb
├── model/              # vision models the detections load (below)
├── dev/camera/         # camera bring-up notebook
└── core/               # this bench: calibration, caches, motion book (git-ignored)
```

`results/` (one folder per run, `records.csv`), `data/` (operator
uploads) and `rec/` (replay recordings) are the platform's defaults for
this folder and are git-ignored.

## `model/` — vision models

Trained pickles for the vision server, checked in with the project like
the CAD: they are bench assets, not operator uploads (`data/`).

| file | type | for |
|---|---|---|
| `disc.pkl` | `od` — disc detector, 416 px, int8 | finding the disc in a station frame |
| `disc_pass_fail_cropped.pkl` | `cls` — pass / fail on the cropped disc, 448 px, int8 | the visual verdict |

`recipes.j2` registers four detections — `bottom_od` / `bottom_cls` on
the station camera, `top_od` / `top_cls` on the robot camera — one
Inspector recipe each. A detection loads its model by `path`; the path
is a file on THIS machine (the client ships the bytes to the vision
unit at register time, nothing is staged there, vision-guide §5) and
nothing resolves it against the project folder, so it is absolute.
Training: `~/Downloads/vision/training_notebooks/`.

## Run

```bash
cd ~/Downloads/projects/apc && sudo python3 main.py           # UI at :5010
cd ~/Downloads/workspace/workspace && sudo python3 -m workspace.bt.replay ~/Downloads/projects/apc --batch 1 --kw in_1=1,0,0,0,0,0,0
```

A full IN position is `MAX_PER_SLOT` discs (255), so the replay above
plans one stack. The audit row per disc (`rt.record`) is seeded by
`Create`, filled by `Measure` and `Sort`, and closed with `status` at
`Park`; the platform caps records at 10 000 items per run.

## BK 879B initialization

Manual setup for the LCR meter before remote use:

1. Hold **power** to turn on the meter.
2. Hold the middle-right **UTIL** button until the utility menu appears.
3. Menu opens on the beep option — press the **down arrow** to turn beep off.
4. Press **UTIL** to cycle through the menu until you reach **AoFF** (auto-off).
5. Press the **down arrow** until it reads **OFF**.
6. Press the bottom-left **L/C/R/Z** button to return to the main menu.
7. Verify there is a **C** in the top-left corner. If not, press **L/C/R/Z**
   until it cycles to **C**.
8. Press the top-middle **USB** button — **RMT** should flash in the
   bottom-right of the screen. The meter is now in remote mode.
