"""Independent mesh and datum checks for the fixed overhead TX / movable RX.

This module never controls SOLIDWORKS or hardware.  Run it only after the new
P5 meshes and CAD metadata have been generated.  All distances are millimetres.
The checks describe nominal geometry, not physical fit or acoustic validation.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import struct
import sys

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import layout

P = layout.P
BOARDS = layout.BOARDS
OUT = layout.OUT
EPS = 2e-4
MINIMUM_PRINTABLE_NAMES = {
    "01_side_trestle", "02_tx_carrier_4x4", "03_tx_carrier_8x8",
    "04_m3_rear_sleeve_6x5", "05_receiver_flat_tray", "06_receiver_edge_clip",
    "07_fastener_fit_coupon",
}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def triangles(path):
    """Read binary STL directly, without a CAD application or mesh repair."""
    raw = Path(path).read_bytes()
    require(len(raw) >= 84, f"Truncated STL: {path}")
    count = struct.unpack_from("<I", raw, 80)[0]
    require(len(raw) == 84 + 50 * count, f"Unexpected STL encoding: {path}")
    dtype = np.dtype([("normal", "<f4", 3), ("vertices", "<f4", (3, 3)),
                      ("attribute", "<u2")])
    return np.frombuffer(raw, offset=84, count=count, dtype=dtype)["vertices"].astype(float)


def mesh_info(path, metadata, printable, strict_topology=True):
    mesh = triangles(path)
    require(len(mesh) > 0 and np.isfinite(mesh).all(), f"Empty/nonfinite STL: {path}")
    components = None
    if strict_topology:
        vertices, indices = np.unique(np.round(mesh.reshape(-1, 3), 4), axis=0,
                                      return_inverse=True)
        faces = indices.reshape(-1, 3)
        edges = defaultdict(list)
        for index, face in enumerate(faces):
            for first, second in zip(face, np.roll(face, -1)):
                edges[tuple(sorted((first, second)))].append((index, first < second))
        require(all(len(v) == 2 and v[0][1] != v[1][1] for v in edges.values()),
                f"Open, nonmanifold or inconsistently oriented STL: {path}")
        neighbors = defaultdict(list)
        for pair in edges.values():
            a, b = pair[0][0], pair[1][0]
            neighbors[a].append(b)
            neighbors[b].append(a)
        unseen = set(range(len(faces)))
        components = 0
        while unseen:
            pending = [unseen.pop()]
            components += 1
            while pending:
                for neighbor in neighbors[pending.pop()]:
                    if neighbor in unseen:
                        unseen.remove(neighbor)
                        pending.append(neighbor)
    cross = np.cross(mesh[:, 1] - mesh[:, 0], mesh[:, 2] - mesh[:, 0])
    require((np.linalg.norm(cross, axis=1) > 1e-8).all(), f"Degenerate STL: {path}")
    volume = float(np.einsum("ij,ij->i", mesh[:, 0],
                             np.cross(mesh[:, 1], mesh[:, 2])).sum() / 6)
    require(volume > 0, f"Nonpositive STL volume: {path}")
    expected_volume = float(metadata["volume_mm3"])
    relative_error = abs(volume - expected_volume) / expected_volume
    require(relative_error < .001, f"CAD/STL volume mismatch: {path}, {relative_error}")
    if strict_topology:
        require(components == metadata["solid_count"], f"CAD/STL body count mismatch: {path}")
    lo, hi = mesh.min(axis=(0, 1)), mesh.max(axis=(0, 1))
    size = hi - lo
    if printable:
        require(components == 1, f"Printable part has detached solids: {path}")
        require(abs(lo[2]) <= EPS, f"Print orientation does not rest at Z=0: {path}")
        printable_limit = np.array(P["assumed_print_bed_mm"], float) - 2 * P["print_margin_mm"]
        require((size <= printable_limit + EPS).all(),
                f"Part exceeds print bed including margin: {path}: {size.tolist()}")
    return dict(file=Path(path).name, triangles=len(mesh),
                strict_topology_checked=strict_topology,
                watertight=True if strict_topology else None,
                oriented=True if strict_topology else None,
                connected_solids=components, cad_body_count=metadata["solid_count"],
                reference_envelope_only=not strict_topology,
                bounds_mm=[lo.tolist(), hi.tolist()],
                size_mm=size.tolist(), volume_mm3=volume,
                cad_volume_relative_error=relative_error, printable=bool(printable))


def transform(entry, points):
    points = np.asarray(points, float)
    rotation = np.asarray(entry["rotation"], float).reshape(3, 3)
    require(np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-9),
            f"Non-rigid component transform: {entry['name']}")
    require(abs(np.linalg.det(rotation) - 1) < 1e-9,
            f"Mirrored/scaled component transform: {entry['name']}")
    return points @ rotation + np.asarray(entry["translation_mm"])


def clipped_points_in_z(mesh, low, high):
    """Clip triangles to a height slab before taking a conservative XY box.

    A top ledge must not shrink the free travel area near the table simply
    because the entire stand's bounding box also contains that ledge.
    """
    points = []
    for triangle in mesh:
        if triangle[:, 2].max() < low or triangle[:, 2].min() > high:
            continue
        polygon = [v for v in triangle]
        for height, above in ((low, True), (high, False)):
            clipped = []
            if not polygon:
                break
            previous = polygon[-1]
            previous_inside = previous[2] >= height if above else previous[2] <= height
            for current in polygon:
                current_inside = current[2] >= height if above else current[2] <= height
                if current_inside != previous_inside:
                    fraction = (height - previous[2]) / (current[2] - previous[2])
                    clipped.append(previous + fraction * (current - previous))
                if current_inside:
                    clipped.append(current)
                previous, previous_inside = current, current_inside
            polygon = clipped
        points.extend(polygon)
    return np.asarray(points).reshape(-1, 3)


def vertical_hits(mesh, x, y):
    """Unoriented Z intersections, rejecting faces parallel to the test ray."""
    a, b, c = mesh[:, 0], mesh[:, 1], mesh[:, 2]
    det = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    good = abs(det) > 1e-10
    a, b, c, det = a[good], b[good], c[good], det[good]
    u = ((x - a[:, 0]) * (c[:, 1] - a[:, 1]) - (y - a[:, 1]) * (c[:, 0] - a[:, 0])) / det
    v = ((b[:, 0] - a[:, 0]) * (y - a[:, 1]) - (b[:, 1] - a[:, 1]) * (x - a[:, 0])) / det
    inside = (u >= -1e-7) & (v >= -1e-7) & (u + v <= 1 + 1e-7)
    z = a[:, 2] + u * (b[:, 2] - a[:, 2]) + v * (c[:, 2] - a[:, 2])
    return np.unique(np.round(z[inside], 5))


def solid_intervals(mesh, x, y):
    hits = vertical_hits(mesh, x, y)
    require(len(hits) % 2 == 0,
            f"Ambiguous vertical probe on a boundary at ({x:.4f}, {y:.4f}): {hits}")
    return list(zip(hits[::2], hits[1::2]))


def material_in_height(mesh, x, y, low, high):
    return any(min(b, high) - max(a, low) > EPS for a, b in solid_intervals(mesh, x, y))


def sample_rectangle(rect, nx=5, ny=5, inset=.03):
    x0, y0, x1, y1 = rect
    require(x1 > x0 and y1 > y0, f"Invalid rectangle: {rect}")
    for x in np.linspace(x0 + inset, x1 - inset, nx):
        for y in np.linspace(y0 + inset, y1 - inset, ny):
            yield float(x), float(y)


def local_receiver_rectangle(top_left_rectangle):
    """Receiver STL uses X right, Y up; its top-left outline origin is (0,0)."""
    x0, y0, x1, y1 = top_left_rectangle
    return [x0, -y1, x1, -y0]


def component(name, entries):
    selected = [e for e in entries if e["name"] == name]
    require(len(selected) == 1, f"Expected one {name}, got {len(selected)}")
    return selected[0]


def source_emitters(profile):
    selected = [p for p in BOARDS[profile]["parts"]
                if p["manufacturer_part"].upper() == "MA40S4S"]
    expected = P[profile]["rows"] * P[profile]["cols"]
    require(len(selected) == expected, f"Wrong emitter count for {profile}: {len(selected)}")
    cx, cy = P[profile]["array_center_top_left_mm"]
    positions = [(v["center_xy_mm"][0] - cx, v["center_xy_mm"][1] - cy)
                 for v in selected]
    pitch = P[profile]["pitch_mm"]
    expected_positions = sorted((round((col - (P[profile]["cols"] - 1) / 2) * pitch, 3),
                                 round((row - (P[profile]["rows"] - 1) / 2) * pitch, 3))
                                for row in range(P[profile]["rows"])
                                for col in range(P[profile]["cols"]))
    require(sorted((round(x, 3), round(y, 3)) for x, y in positions) == expected_positions,
            f"Emitter positions disagree with the project geometry for {profile}")
    return selected, positions


def check_carrier(profile, meshes):
    name = "02_tx_carrier_4x4" if profile == "tx4" else "03_tx_carrier_8x8"
    mesh = meshes[name]
    entries = layout.placements(profile)
    board_entry = component("REF_" + profile + "_electronics", entries)
    carrier_entry = component(name, entries)
    world_carrier = transform(carrier_entry, mesh)
    # Parent PCB data uses Y down.  A native electronics model uses Y up and
    # the FLIP_X placement then inverts it: the final holes must retain Y down.
    mounts = layout.tx_mounts(profile)
    cx, cy = P[profile]["array_center_top_left_mm"]
    mount_records = []
    for source_hole, (x, y) in zip(BOARDS[profile]["mounting_holes"], mounts):
        board_local = [source_hole["xy_mm"][0] - cx,
                       -(source_hole["xy_mm"][1] - cy), 0]
        actual = transform(board_entry, board_local)
        require(np.allclose(actual[:2], [x, y], atol=EPS),
                f"Downward PCB / carrier mounting-hole handedness error: {profile}")
        require(not len(vertical_hits(mesh, x, y)),
                f"Carrier blocks the PCB fixing-hole axis: {profile}, {(x, y)}")
        # Positive control on the boss annulus rather than a second empty hole.
        annulus_radius = (P["m3_clearance_diameter_mm"] / 2 + P["tx_boss_od_mm"] / 2) / 2
        boss_hits = vertical_hits(mesh, x + annulus_radius, y)
        require(len(boss_hits) >= 2 and abs(boss_hits.max() -
                (P["carrier_thickness_mm"] + P["tx_front_boss_height_mm"])) < EPS,
                f"Missing/incorrect front PCB support boss: {profile}, {(x, y)}")
        mount_records.append(dict(source_xy_mm=source_hole["xy_mm"], carrier_xy_mm=[x, y],
                                  pcb_world_xy_mm=actual[:2].tolist(), through_axis_clear=True))
    _, positions = source_emitters(profile)
    radius = P["sensor_case_diameter_mm"] / 2
    sample_count = 0
    for x, y in positions:
        probes = [(x, y)] + [(x + r * math.cos(a), y + r * math.sin(a))
                              for r in (radius / 2, radius)
                              for a in np.linspace(0, 2 * math.pi, 16, endpoint=False)]
        for px, py in probes:
            require(not len(vertical_hits(world_carrier, px, py)),
                    f"Carrier obstructs an emitter projection: {profile}, {(px, py)}")
            for e in entries:
                if e["name"] == "01_side_trestle":
                    require(not len(vertical_hits(transform(e, meshes[e["name"]]), px, py)),
                            f"Trestle obstructs emitter projection: {profile}")
            sample_count += 1
    for x, y in P["body_carrier_holes_mm"]:
        require(not len(vertical_hits(mesh, x, y)), f"M4 carrier hole blocked: {profile}, {(x,y)}")
        require(len(vertical_hits(mesh, x + 3.2, y)) >= 2,
                f"M4 carrier hole lacks surrounding material: {profile}, {(x,y)}")
        for angle in np.linspace(0,2*math.pi,16,endpoint=False):
            px, py = x+4.45*math.cos(angle), y+4.45*math.sin(angle)
            require(material_in_height(mesh,px,py,.1,P["carrier_thickness_mm"]-.1),
                    f"M4 OD9 top washer would overhang the carrier: {profile}, {(x,y)}")
    normal = transform(board_entry, [0, 0, 1]) - transform(board_entry, [0, 0, 0])
    require(np.allclose(normal, [0, 0, -1]), f"TX is not facing down: {profile}")
    d = layout.datums()
    tx_face = transform(board_entry, [0, 0, P["tx_front_to_face_mm"]])
    require(abs(tx_face[2] - d["tx_face_z_mm"]) < EPS, f"TX face datum mismatch: {profile}")
    return dict(profile=profile, source_project_sha256=BOARDS[profile]["source_sha256"],
                mounting_holes=mount_records, downward_normal_verified=True,
                emitter_count=len(positions), projection_probes=sample_count,
                emitter_projections_unobstructed=True, tx_face_z_mm=float(tx_face[2]))


def check_receiver(meshes):
    mesh = meshes["05_receiver_flat_tray"]
    d = layout.datums()
    contacts = P["receiver_contacts_top_left_mm"]
    records = []
    for rect in contacts:
        probes = list(sample_rectangle(local_receiver_rectangle(rect), 3, 3, .12))
        for x, y in probes:
            hits = vertical_hits(mesh, x, y)
            require(len(hits) >= 2 and abs(hits.max() - d["receiver_back_z_mm"]) < EPS,
                    f"Missing or incorrect receiver edge-support height at {(x,y)}: {hits}")
        records.append(dict(contact_top_left_mm=rect, probes=len(probes),
                            support_z_mm=d["receiver_back_z_mm"]))
    # Check the centre and a dense grid under the board.  Exclude the deliberate
    # bare-edge contact strips and round corners; the floor itself is allowed.
    width, height = BOARDS["receiver"]["board_size_mm"]
    probe_count = 0
    for x in np.linspace(3, width - 3, 19):
        for ydown in np.linspace(3, height - 3, 11):
            require(not material_in_height(mesh, float(x), -float(ydown),
                    P["receiver_floor_mm"] + EPS, d["receiver_back_z_mm"] - EPS),
                    f"Receiver bottom-component cavity is obstructed at {(x,-ydown)}")
            probe_count += 1
    require(abs(d["receiver_back_z_mm"] - P["receiver_floor_mm"] -
                P["receiver_underboard_clearance_mm"]) < EPS,
            "Receiver underboard cavity is not the specified 12 mm")
    # Side access uses each connector footprint and a path from it to the
    # outside of the tray.  Keep floor-level material; reject any raised wall
    # across the plug/switch access path.  These are nominal clearance probes,
    # not evidence that an unmeasured cable bend radius fits.
    parts = {v["reference"]: v for v in BOARDS["receiver"]["parts"]}
    access = []
    for ref, side in (("U4", "right"), ("CN1", "left"), ("SW1", "bottom")):
        part = parts[ref]
        extents = part["xy_footprint_extents_by_library_layer_mm"]
        rect = extents.get("48", extents.get("3"))
        require(rect is not None, f"No PCB-derived keepout for receiver {ref}")
        x0, y0, x1, y1 = rect
        lo, hi = mesh.min(axis=(0,1)), mesh.max(axis=(0,1))
        if side == "right":
            path = [x0 - .25, -y1 - .5, hi[0] + 1, -y0 + .5]
        elif side == "left":
            path = [lo[0] - 1, -y1 - .5, x1 + .25, -y0 + .5]
        else:
            path = [x0 - .5, lo[1] - 1, x1 + .5, -y0 + .25]
        checked = 0
        for x, y in sample_rectangle(path, 11, 7, .08):
            require(not material_in_height(mesh, x, y, P["receiver_floor_mm"] + EPS,
                                          d["receiver_front_z_mm"] + 12),
                    f"Raised receiver-tray material blocks {ref} {side} access at {(x,y)}")
            checked += 1
        access.append(dict(reference=ref, side=side, source_footprint_rectangle_mm=rect,
                           clearance_probe_rectangle_local_mm=path, probes=checked))
    # Align the sensor from its real top-left PCB coordinate, not board centre.
    entries = layout.placements("tx4")
    rx_entry = component("REF_receiver_electronics", entries)
    sx, sy = P["receiver"]["sensor_center_top_left_mm"]
    rx_face = transform(rx_entry, [sx, -sy, P["receiver_front_to_face_mm"]])
    require(np.allclose(rx_face, [0, 0, d["receiver_face_z_mm"]], atol=EPS),
            f"Receiver sensor is not at the declared face datum: {rx_face}")
    normal = transform(rx_entry, [0, 0, 1]) - transform(rx_entry, [0, 0, 0])
    require(np.allclose(normal, [0, 0, 1]), "Receiver sensor does not face upward")
    clip_entries = [e for e in entries if e["name"] == "06_receiver_edge_clip"]
    require(len(clip_entries) == len(contacts), "Missing receiver edge clip")
    ox, oy, _ = layout.receiver_origin()
    clip_contacts = []
    for rect in contacts:
        x0, y0, x1, y1 = rect
        cx, cy = (x0+x1)/2 + ox, -(y0+y1)/2 + oy
        matching = []
        for entry in clip_entries:
            world_clip = transform(entry, meshes["06_receiver_edge_clip"])
            hits = vertical_hits(world_clip, cx, cy)
            if len(hits) and abs(hits.min()-d["receiver_front_z_mm"]) < EPS:
                matching.append(entry)
        require(len(matching) == 1,
                f"Receiver contact does not have exactly one correctly oriented clip: {rect}")
        clip_contacts.append(dict(contact_top_left_mm=rect,
                                  clip_origin_world_mm=matching[0]["translation_mm"],
                                  lower_contact_z_mm=d["receiver_back_z_mm"],
                                  upper_contact_z_mm=d["receiver_front_z_mm"]))
    for x, y, _ in P["receiver_anchors_local_mm"]:
        require(not material_in_height(mesh, x, y, P["receiver_floor_mm"]+EPS,
                                       d["receiver_front_z_mm"]),
                f"Receiver screw axis is obstructed above the recessed floor: {(x,y)}")
    return dict(edge_contacts=records, bottom_cavity_probes=probe_count,
                underboard_clearance_mm=P["receiver_underboard_clearance_mm"],
                side_access=access, sensor_world_front_center_mm=rx_face.tolist(),
                upward_normal_verified=True, upper_and_lower_edge_capture=clip_contacts,
                uses_receiver_pcb_holes=False)


def check_fastener_stacks():
    """Nominal clamp stack and engagement; no unmeasured torque/load claims."""
    d = layout.datums()
    # Standard washers/nuts match the explicit reference builders and list.
    m4_stack = .8 + P["carrier_thickness_mm"] + P["body_ledge_vertical_mm"] + .8 + 5
    tx_m3_stack = (.5 + P["carrier_thickness_mm"] + P["tx_front_boss_height_mm"] +
                   P["pcb_thickness_mm"] + P["tx_rear_sleeve_mm"][1] + .5 + 4)
    m4_protrusion = 30 - m4_stack
    tx_protrusion = 25 - tx_m3_stack
    require(m4_protrusion >= 1.4 - EPS, "M4x30 does not extend two pitches past the nut")
    require(tx_protrusion >= 1 - EPS, "TX M3x25 does not extend two pitches past the nut")
    rx_head_seat = d["receiver_front_z_mm"] + P["receiver_clip_thickness_mm"] + .5
    rx_tip = rx_head_seat - 16
    rx_nut_top = P["receiver_nut_slot_z_mm"][1]
    rx_nut_bottom = rx_nut_top - 2.4
    rx_protrusion = rx_nut_bottom - rx_tip
    require(rx_protrusion >= 1 - EPS, "RX M3x16 does not extend two pitches past the plain nut")
    require(rx_tip > P["receiver_floor_mm"] + EPS,
            "Receiver screw bottoms against the recessed floor before clamping")
    require(P["receiver_nut_slot_z_mm"][1] - P["receiver_nut_slot_z_mm"][0] >= 2.4,
            "Plain M3 nut cannot fit the receiver pocket height")
    return dict(carrier_M4x30=dict(stack_including_nut_mm=m4_stack,
                                 nominal_thread_protrusion_mm=m4_protrusion),
                tx_M3x25=dict(stack_including_nut_mm=tx_m3_stack,
                             nominal_thread_protrusion_mm=tx_protrusion),
                receiver_M3x16=dict(head_seat_z_mm=rx_head_seat, tip_z_mm=rx_tip,
                                   ordinary_DIN934_nut_z_mm=[rx_nut_bottom,rx_nut_top],
                                   nominal_thread_protrusion_mm=rx_protrusion,
                                   tip_to_recessed_floor_mm=rx_tip-P["receiver_floor_mm"]),
                actual_torque_or_strength_validated=False)


def check_coupon(mesh):
    records = []
    for x, diameter in zip((-22.5,-7.5,7.5,22.5),(3.4,3.6,4.5,4.7)):
        require(not len(vertical_hits(mesh,x,0)), f"Fit-coupon hole is filled: {diameter}")
        require(not len(vertical_hits(mesh,x+diameter/2-.10,0)),
                f"Fit-coupon hole is too small: {diameter}")
        require(np.allclose(vertical_hits(mesh,x+diameter/2+.10,0),[0,5],atol=EPS),
                f"Fit-coupon positive diameter control failed: {diameter}")
        records.append(dict(center_local_mm=[x,0],nominal_diameter_mm=diameter,
                            inner_and_outer_probes_passed=True))
    return records


def check_table_fasteners_and_sweep(profile, meshes, reference_meshes):
    entries = layout.placements(profile)
    d = layout.datums()
    body_records = []
    fixed_meshes = []
    for e in entries:
        if e["name"] == "01_side_trestle":
            mesh = transform(e, meshes[e["name"]])
            lo, hi = mesh.min(axis=(0,1)), mesh.max(axis=(0,1))
            require(abs(lo[2]) < EPS, f"Trestle foot not resting at table Z=0: {lo}")
            # The two ends of the long foot must both reach the same plane.
            table_vertices = mesh.reshape(-1,3)[abs(mesh.reshape(-1,3)[:,2]) < EPS]
            require(len(table_vertices) > 0 and np.ptp(table_vertices[:,1]) >=
                    2 * P["body_half_foot_length_mm"] - EPS,
                    "Trestle does not have a full-length planar table contact")
            body_records.append(dict(translation_mm=e["translation_mm"],
                                      bounds_mm=[lo.tolist(),hi.tolist()], table_z_mm=float(lo[2])))
            # The M4 axes must pass through both the carrier and the ledge,
            # with real supporting material adjacent to each clearance hole.
            for u in (-55,55):
                axis = transform(e,[u,d["carrier_bottom_z_mm"],24])
                require(any(np.allclose(axis[:2],[x,y],atol=EPS)
                            for x,y in P["body_carrier_holes_mm"]),
                        f"Trestle and carrier M4 axes are misaligned: {axis}")
                require(not len(vertical_hits(mesh,*axis[:2])),
                        f"Trestle M4 clearance hole is blocked: {axis}")
                control = vertical_hits(mesh,axis[0]+3.2,axis[1])
                require(len(control)>=2 and abs(control.max()-d["carrier_bottom_z_mm"])<EPS,
                        f"No ledge material next to the M4 hole: {axis}")
                for angle in np.linspace(0,2*math.pi,16,endpoint=False):
                    px, py = axis[0]+4.45*math.cos(angle), axis[1]+4.45*math.sin(angle)
                    require(material_in_height(mesh,px,py,
                            d["carrier_bottom_z_mm"]-P["body_ledge_vertical_mm"]+.1,
                            d["carrier_bottom_z_mm"]-.1),
                            f"M4 OD9 lower washer would overhang the side ledge: {axis}")
            fixed_meshes.append((e["name"], mesh))
        elif e["name"].startswith(("02_tx_carrier", "03_tx_carrier")):
            fixed_meshes.append((e["name"], transform(e, meshes[e["name"]])))
    screw_records = []
    for e in entries:
        if e["name"].startswith(("REF_M3", "REF_M4")):
            require(e["name"] in reference_meshes,
                    f"Missing nominal fastener STL {e['name']} required for table clearance check")
            mesh = transform(e, reference_meshes[e["name"]])
            low = float(mesh[:,:,2].min())
            require(low >= -EPS, f"Fastener extends below table: {e['name']}, Z={low}")
            screw_records.append(dict(name=e["name"], minimum_z_mm=low))
    require(screw_records, "No fasteners were checked")
    # Conservative swept AABB of the *entire receiver assembly*: tray, clips,
    # nominal electronics and receiver fasteners.  A disjoint AABB proves no
    # collision for all XY translations in the continuous square, rather than
    # checking a few individual sample positions.
    rx_entries = [e for e in entries if e["name"] in
                  ("05_receiver_flat_tray", "06_receiver_edge_clip", "REF_receiver_electronics")]
    for e in entries:
        if e["name"] in ("REF_M3x16_screw", "REF_M3_plain_nut") or (
                e["name"] == "REF_M3_washer" and
                abs(e["translation_mm"][2] - (d["receiver_front_z_mm"] +
                    P["receiver_clip_thickness_mm"])) < EPS):
            rx_entries.append(e)
    rx_meshes = []
    for e in rx_entries:
        source = meshes if e["name"] in meshes else reference_meshes
        require(e["name"] in source, f"Missing receiver nominal mesh: {e['name']}")
        rx_meshes.append(transform(e, source[e["name"]]).reshape(-1,3))
    all_points = np.concatenate(rx_meshes)
    low, high = all_points.min(axis=0), all_points.max(axis=0)
    span = P["tx8"]["pitch_mm"] * (P["tx8"]["rows"] - 1) / 2
    swept_low, swept_high = low - [span,span,0], high + [span,span,0]
    clearances = []
    for name, fixed_mesh in fixed_meshes:
        f_low, f_high = fixed_mesh.min(axis=(0,1)), fixed_mesh.max(axis=(0,1))
        gaps = np.maximum(f_low - swept_high, swept_low - f_high)
        # If there is already a vertical gap the full AABB is sufficient.
        # Otherwise use the fixed geometry only at receiver-occupied heights.
        slab_clipped = False
        if gaps[2] <= EPS:
            slab_points = clipped_points_in_z(fixed_mesh, swept_low[2], swept_high[2])
            require(len(slab_points) > 0, f"Could not inspect shared-height slab for {name}")
            f_low, f_high = slab_points.min(axis=0), slab_points.max(axis=0)
            gaps = np.maximum(f_low - swept_high, swept_low - f_high)
            slab_clipped = True
        require((gaps > EPS).any(),
                f"Conservative receiver +/-{span} mm swept envelope overlaps {name}: "
                f"receiver {swept_low}/{swept_high}, fixed {f_low}/{f_high}")
        clearances.append(dict(name=name, clipped_to_receiver_height_slab=slab_clipped,
                               fixed_obstacle_bounds_mm=[f_low.tolist(),f_high.tolist()],
                               axis_separation_mm=gaps.tolist(),
                               guaranteed_separation_mm=float(gaps.max())))
    gap = d["tx_face_z_mm"] - d["receiver_face_z_mm"]
    require(abs(gap - P["acoustic_gap_mm"]) < EPS, "Acoustic face spacing is incorrect")
    return dict(profile=profile, trestle_table_contacts=body_records,
                nominal_fasteners=screw_records, acoustic_face_gap_mm=gap,
                receiver_swept_translation_each_axis_mm=[-span,span],
                receiver_assembly_bounds_mm=[low.tolist(),high.tolist()],
                receiver_continuous_swept_bounds_mm=[swept_low.tolist(),swept_high.tolist()],
                conservative_sweep_separations=clearances,
                separate_base_and_stand_verified=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUT / "validation.json")
    args = parser.parse_args()
    require(OUT.exists(), f"P5 CAD generation has not produced {OUT}")
    printables, references, records = {}, {}, []
    for folder, target, expected_printable in (("stl", printables, True),
                                               ("reference", references, False)):
        for path in sorted((OUT / folder).glob("*.stl")):
            metadata_path = OUT / (path.stem + ".json")
            require(metadata_path.exists(), f"Missing CAD volume metadata: {metadata_path}")
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            require(bool(metadata["printable"]) == expected_printable,
                    f"Reference/printable STL in wrong folder: {path}")
            # Electronics are a collection of overlapping nominal bodies and
            # cable envelopes, not a union intended for manufacture.  Keep
            # their finite/bounds/volume checks; do not merge or repair them to
            # force the printable-parts topology contract onto a reference.
            strict = not path.stem.endswith("_electronics")
            records.append(mesh_info(path, metadata, expected_printable, strict))
            target[path.stem] = triangles(path)
    require(MINIMUM_PRINTABLE_NAMES <= printables.keys(), "Missing P5 printable components")
    require(len(printables) == 7, f"Expected 7 printable part types, got {len(printables)}")
    carrier_checks = [check_carrier(p, printables) for p in ("tx4", "tx8")]
    receiver = check_receiver(printables)
    fastener_stacks = check_fastener_stacks()
    fit_coupon = check_coupon(printables["07_fastener_fit_coupon"])
    assemblies = [check_table_fasteners_and_sweep(p, printables, references)
                  for p in ("tx4", "tx8")]
    report = dict(passed=True, revision=P["revision"], printable_part_types=len(printables),
                  assumed_print_bed_mm=P["assumed_print_bed_mm"],
                  margin_each_side_mm=P["print_margin_mm"],
                  maximum_printable_dimension_mm=max(max(r["size_mm"]) for r in records if r["printable"]),
                  meshes=records, carriers=carrier_checks, receiver=receiver, assemblies=assemblies,
                  fastener_stacks=fastener_stacks, fit_coupon_holes=fit_coupon,
                  source_geometry_notes=json.loads((HERE / "pcb_geometry.json").read_text(encoding="utf-8"))["notes"],
                  scope="Independent printable meshes and nominal fasteners have strict topology and CAD-volume checks. Electronics are overlapping, non-manufacturing reference envelopes checked for finite coordinates, positive volume, CAD-volume consistency and bounds; they remain included in the swept envelope. Source PCB coordinates, component transforms, table and conservative continuous receiver travel are checked. Native sketch constraints and BREP interference are checked separately.",
                  actual_weld_heights_measured=False, actual_wiring_measured=False,
                  printed_or_load_tested=False, acoustic_response_measured=False,
                  remaining_limits=["actual component and solder heights", "actual plugs and cable bends",
                                    "printing and assembly tolerance", "loading and long-term creep",
                                    "acoustic field and tactile response"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(passed=True, output=str(args.output), printable_part_types=len(printables),
                         maximum_printable_dimension_mm=report["maximum_printable_dimension_mm"],
                         acoustic_face_gap_mm=P["acoustic_gap_mm"]), ensure_ascii=False))


if __name__ == "__main__":
    main()
