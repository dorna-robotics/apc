# apc

Disc capacitance sort. Each disc is picked off an IN stack, inspected
at the vertical camera station, placed on the anode, inspected by the
robot camera, clamped by the cathode, its capacitance read on the
BK 879B, then dropped into a PASS or FAIL holder by the window in
`Sort` (`C_MIN..C_MAX`, `actions.py`). A FLAT BT project: one disc
through the bench at a time, `ROUTE` at the bottom of `actions.py`.

Each inspection is two models on one camera: the detector finds the
disc, the classifier judges the crop around its box (`CLS_ROI_OFFSET`
px of margin). How sure the classifier must be is the run's
`classifier` setting on the setup screen: high (a pass needs 75 %),
medium (50 %, the model's own call), low (25 %), or ignore (no
classifier at all — the detector still has to see a disc, the
capacitance reading alone sorts). A classifier fail at either camera sends the disc to
the fail column with no measurement. A disc the detector does not see
at either camera is a fail too: the suction is never released mid-way,
the hand goes to the fail column as if it held one.

## Layout

```
apc/
├── main.py             # canonical entry point — byte-identical to examples/*/main.py
├── launch.yaml         # project_name, port, scene, recipes, actions, checks, hmi pointers (default/setup/pendant/replan), core_dir, records/replays/uploads/counts, folders
├── actions.py          # predicates, setup(), Start → per-disc chain → Park, ROUTE
├── checks.py           # vision / sensor checks (empty)
├── recipes.j2          # holders, stations, meter — recipe aliases + solved ref_joints
├── hmi/
│   ├── default.j2      # the kwargs: in_1 / in_2, one list of 7 per IN holder
│   ├── setup.js        # run-setup screen, two steps — Bench (click each IN position full / empty), Final checks
│   ├── pendant.js      # during-run screen — holders, tally, last reading
│   └── replan.js       # the Replan choice on the bench — choose stack positions (every disc in them leaves), clear the bench, confirm
├── scene/
│   ├── core_500.j2     # chassis
│   ├── layout.j2       # holders, anode/cathode, cameras, meter
│   ├── calibration.j2  # layout.j2 with the probe rod mounted (calibrate.ipynb)
│   ├── bench.j2        # THIS unit: sim flags, rail offset, IPs, camera serials, meter port (git-ignored)
│   └── bench.example.j2  # the committed template for bench.j2 — copy, fill in
├── components/         # anode, cathode (@register)
├── CAD/                # their .glb
├── vision/             # the detections: a config per model, the model beside it (below)
├── dev/camera/         # camera bring-up notebook
├── core/               # this bench: calibration, caches, motion book (git-ignored)
├── records/            # one folder per run: records.jsonl, records.csv — the per-disc sheet (git-ignored)
├── replays/            # replay recordings (git-ignored)
├── uploads/            # operator input files — a file parameter's Open (git-ignored)
├── captures/           # the detections' pictures, one file per run per detection (git-ignored; saving off for now)
├── counts/             # rt.count's totals across every run — counts.json (git-ignored)
└── log/                # the project's console — workspace.log, written by the orchestrator (git-ignored)
```

The last six are data folders, never source: `records:`, `replays:`,
`uploads:` and `counts:` in `launch.yaml` say where the platform writes,
`captures/` is where the detections' `client_save_*` paths point,
`log/` is where the orchestrator keeps the project's console, and
`folders:` lists all six as file-browser tabs (Log read-only). The
orchestrator creates them at launch. Counted across runs (`rt.count`): `disc.picked`,
`inspect.bottom` / `inspect.top` (pass, fail, empty, read_failed),
`measure` (n, unavailable), `disc.sorted` (n = every disc processed to
the end, good, bad), `disc.removed`, `run` (completed, operator_park).

## `vision/` — the detections

Each trained model sits beside the config that names it, checked in with
the project like the CAD: they are bench assets, not operator uploads.
A config's paths resolve against `vision/` itself (vision-guide §5
"Config files"): the model beside it, the pictures in
`../captures/<config name>/` (`display.client_save_*`, written on this
machine, one file per run per detection — OFF for now, every save key
false; `label: 1` only draws on the live view); a key for a vlm config
goes beside it as `*.key`, git-ignored. `vision/` is not a file-browser
tab.

| config | model | type | for |
|---|---|---|---|
| `disc_od.yaml` | `disc.pkl` | `od` — disc detector, 416 px, int8 | finding the disc in a station frame |
| `disc_cls.yaml` | `disc_pass_fail_cropped.pkl` | `cls` — pass / fail on the cropped disc, 448 px, int8 | the visual verdict |

`recipes.j2` registers four detections — `bottom_od` / `bottom_cls` on
the station camera, `top_od` / `top_cls` on the robot camera — one
Inspector recipe each. Each detection is
`detection_preset: {config: vision/disc_od.yaml}` — a relative path is
relative to the file it is written in: `config:` to recipes.j2, the
paths inside the config to `vision/`; the client ships the model's bytes to the vision unit at
register time (vision-guide §5).
Training: `~/Downloads/vision/training_notebooks/`.

## Run

```bash
cd ~/Downloads/projects/apc && sudo python3 main.py           # UI at :5010
cd ~/Downloads/workspace/workspace && sudo python3 -m workspace.bt.replay ~/Downloads/projects/apc --batch 1 --kw in_1=1,0,0,0,0,0,0
```

A full IN position is `stack_size` discs — the one number in
`hmi/default.j2`, read by `setup()` and by the setup screen alike — so
the replay above plans one stack. The audit row per disc (`rt.record`) is seeded by
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
