"""Mechanical datums, independent of SOLIDWORKS and without hardware control."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
P = json.loads((HERE / "parameters.json").read_text(encoding="utf-8"))
BOARDS = json.loads((HERE / "pcb_geometry.json").read_text(encoding="utf-8"))["boards"]
OUT = ROOT / "dist" / P["revision"]
IDENTITY = [1., 0., 0., 0., 1., 0., 0., 0., 1.]
# SOLIDWORKS transform stores local basis vectors as rows.
FLIP_X = [1., 0., 0., 0., -1., 0., 0., 0., -1.]
LEFT_BODY = [0., 1., 0., 0., 0., 1., 1., 0., 0.]
RIGHT_BODY = [0., -1., 0., 0., 0., 1., -1., 0., 0.]
TURN_Z = [-1., 0., 0., 0., -1., 0., 0., 0., 1.]


def datums():
    rx_back = P["receiver_floor_mm"] + P["receiver_underboard_clearance_mm"]
    rx_front = rx_back + P["pcb_thickness_mm"]
    rx_face = rx_front + P["receiver_front_to_face_mm"]
    tx_face = rx_face + P["acoustic_gap_mm"]
    tx_front = tx_face + P["tx_front_to_face_mm"]
    carrier_top = tx_front - P["tx_front_boss_height_mm"]
    return dict(receiver_back_z_mm=rx_back, receiver_front_z_mm=rx_front,
                receiver_face_z_mm=rx_face, tx_face_z_mm=tx_face,
                tx_front_z_mm=tx_front, tx_back_z_mm=tx_front+P["pcb_thickness_mm"],
                carrier_top_z_mm=carrier_top,
                carrier_bottom_z_mm=carrier_top-P["carrier_thickness_mm"],
                acoustic_gap_mm=P["acoustic_gap_mm"])


def tx_mounts(profile):
    cx, cy = P[profile]["array_center_top_left_mm"]
    return [(round(h["xy_mm"][0]-cx, 4), round(h["xy_mm"][1]-cy, 4))
            for h in BOARDS[profile]["mounting_holes"]]


def item(name, xyz, rotation=IDENTITY, color="#587c8c"):
    return dict(name=name, translation_mm=list(xyz), rotation=rotation, color=color)


def receiver_origin():
    x, y = P["receiver"]["sensor_center_top_left_mm"]
    return [-x, y, 0.]


def placements(profile):
    """Two independent assemblies placed on the same table; no common base."""
    d = datums()
    stem = "02_tx_carrier_4x4" if profile == "tx4" else "03_tx_carrier_8x8"
    entries = [item("01_side_trestle", [P["body_placement_x_mm"][0], 0, 0], LEFT_BODY),
               item("01_side_trestle", [P["body_placement_x_mm"][1], 0, 0], RIGHT_BODY),
               item(stem, [0, 0, d["carrier_bottom_z_mm"]], color="#527a91"),
               item("REF_"+profile+"_electronics", [0, 0, d["tx_front_z_mm"]], FLIP_X, "#287e63")]
    for x, y in tx_mounts(profile):
        entries += [item("04_m3_rear_sleeve_6x5", [x, y, d["tx_back_z_mm"]], color="#e0ba77")]
        # M3x25 from underside. Rear washer/nut are lifted 5 mm off the PCB.
        entries += [item("REF_M3x25_screw", [x, y, d["carrier_bottom_z_mm"]-.5]),
                    item("REF_M3_washer", [x, y, d["carrier_bottom_z_mm"]-.5]),
                    item("REF_M3_washer", [x, y, d["tx_back_z_mm"]+5]),
                    item("REF_M3_nylock", [x, y, d["tx_back_z_mm"]+5.5])]
    for x, y in P["body_carrier_holes_mm"]:
        top = d["carrier_top_z_mm"]
        bottom = d["carrier_bottom_z_mm"]-P["body_ledge_vertical_mm"]
        entries += [item("REF_M4x30_screw", [x, y, top+.8], FLIP_X),
                    item("REF_M4_washer", [x, y, top]),
                    item("REF_M4_washer", [x, y, bottom-.8]),
                    item("REF_M4_nylock", [x, y, bottom-.8-5])]
    ox, oy, _ = receiver_origin()
    entries += [item("05_receiver_flat_tray", [ox, oy, 0], color="#c98b45"),
                item("REF_receiver_electronics", [ox, oy, d["receiver_front_z_mm"]], color="#2c966f")]
    clip_bottom = d["receiver_front_z_mm"]
    clip_top = clip_bottom+P["receiver_clip_thickness_mm"]
    nut_top = P["receiver_nut_slot_z_mm"][1]
    for x, y, direction in P["receiver_anchors_local_mm"]:
        entries += [item("06_receiver_edge_clip", [ox+x, oy+y, clip_bottom],
                         IDENTITY if direction == 1 else TURN_Z, "#e7b875"),
                    item("REF_M3x16_screw", [ox+x, oy+y, clip_top+.5], FLIP_X),
                    item("REF_M3_washer", [ox+x, oy+y, clip_top]),
                    item("REF_M3_plain_nut", [ox+x, oy+y, nut_top-2.4])]
    return entries
