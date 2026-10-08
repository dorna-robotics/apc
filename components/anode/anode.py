from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid
from dorna2.pose import rotate_abc

from workspace.components.factory import register

@register("anode")
class Anode:
    DEFAULTS = dict(
        anchors={"body": {
            "center": [0, 0, 0, 0, 0, 0],
            "place":  [0, 6.25, 106.24, 0, 0, 90],
            # blow — the air-blow pose: 5 mm above place's height, 15 mm
            # along -x; the orientation TAUGHT on the bench (2026-10-08):
            # the robot was jogged to the pose that blows right, joints
            # [-56.45, 54.25, -111.73, -45.20, -16.48, 146.40, rail 17.57],
            # and the tool's orientation there, expressed in this body's
            # frame and flipped onto the anchor (the tool's z is the
            # anchor's -z: the recipe stands the tool INTO an anchor), is
            # this abc. The jet is 23.6° off vertical toward the body's +x;
            # the wrist is rolled ~18° from the old rotate_abc tilt. The
            # 5 mm of height is load-bearing: at place height the blow point
            # solved in the other wrist branch from its hover and the
            # unplanned jmove onto it swung the wrist over the anode.
            "blow":   [-15, 6.25, 111.24, -21.452, -17.334, 107.249],
            "top":    [0, 6.25, 106.24, 0, 0, 0],
            "hole_0":  [ 75,  37.5, 0, 0, 0, 0],
            "hole_1":  [-75,  37.5, 0, 0, 0, 0],
            "hole_2":  [-75, -37.5, 0, 0, 0, 0],
            "hole_3":  [ 75, -37.5, 0, 0, 0, 0],
            "clb_0":    [50, 6.25, 106.24, 0, 0, 90],
            "clb_1":    [-50, 6.25, 106.24, 0, 0, 90],

        }},

        collision_box={"body": [
            {"pose": [0.0, 9.625, ((106.24/2)+2), 0.0, 0.0, 0.0], "scale": [168.5, 101.5+6.75, 108.24]},  # placeholder — set from CAD
        ]},
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        prm = deepcopy(self.DEFAULTS)
        merge(prm, cfg)
        merge(prm, kwargs)
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))

        self.name = name
        self.workspace = workspace
        self.type = prm["type"]

        self.assembly = {
            k: Solid(
                type=self.type,
                anchors=prm["anchors"][k],
                component=self.name,
                **({"collision_box": cb[k]} if (cb := prm.get("collision_box")) and k in cb else {}),
            )
            for k in prm["anchors"]
        }
