---
license: cc-by-nc-sa-4.0
pretty_name: EgoPressure
tags:
  - hand-pose
  - pressure-estimation
  - egocentric-vision
  - hand-object-interaction
  - MANO
size_categories:
  - 1M<n<10M
---

<h1 align="center">EgoPressure: A Dataset for Hand Pressure and Pose Estimation in Egocentric Vision</h1>

<div align="center">

**CVPR 2025 (Highlight)**

[Yiming Zhao<sup>1*</sup>](https://yiming-zhao.github.io), [Taein Kwon<sup>1*</sup>](https://taeinkwon.com/), [Paul Streli<sup>1*</sup>](https://www.paulstreli.com), [Marc Pollefeys<sup>1,2</sup>](https://people.inf.ethz.ch/marc.pollefeys/), [Christian Holz<sup>1</sup>](https://www.christianholz.net/)<br/>

<sup>1</sup> ETH Zürich
<sup>2</sup> Microsoft<br/>
<sup>*</sup> Equal contribution<br/>

</div>

A dataset for **hand pressure and pose estimation in egocentric vision**.
Participants perform touch gestures on a
pressure-sensing touchpad (Sensel Morph) while being recorded by a
head-mounted egocentric camera and seven static Azure Kinects — providing
synchronized RGB-D from 8 views, fine-grained touch pressure for every
contact, and MANO hand mesh annotations.

- 📄 Paper: https://arxiv.org/abs/2409.02224
- 🛠️ Toolkit (download / load / visualize): https://github.com/eth-siplab/EgoPressure
- 🎬 Preview videos: https://drive.google.com/drive/folders/1JUIUvIR2jAV-ghYGtVLgBEN1JdCzkvnE

<p align="center">
<img src="assets/teaser.gif" width="800">
</p>

## Composition

| | |
|---|---|
| Participants | 21 (pseudonymized `p_001` … `p_021`) |
| Sequences | **1,344** — 32 gestures × 2 hands per participant |
| Gestures | palm/finger/index presses (high, low, no-contact), pinches, pinch-zoom, grasps, pull/push, drawing, typing + a calibration routine |
| Views | 8 synchronized RGB-D cameras (1 egocentric 1920×1080 + 7 static 2560×1440) at 30 Hz |
| Frames | ≈630K per camera stream (≈5M camera-frames overall) |
| Modalities | RGB, depth, hand masks, raw touch-pressure grid, UV pressure maps, MANO hand meshes (pose, shape, per-vertex refinement), per-frame egocentric camera pose |
| Total size | **1.96 TB** — selectively downloadable per participant / sequence / camera / modality |

## Quick start (toolkit)

The official toolkit handles selective download, loading every modality,
geometry, and visualization:

```bash
pip install "egopressure[all] @ git+https://github.com/eth-siplab/EgoPressure"

egopressure list
egopressure download --participants p_001 --cameras d --modalities rgb,depth,pressure,pose
egopressure video p_001 p_001_press_palm_low_x5_right --out seq.mp4
```

```python
from egopressure import EgoPressure

ep = EgoPressure.from_hub(participants=["p_001"], cameras=["d"],
                          modalities=["rgb", "depth", "pressure", "pose"])
seq = ep.sequence("p_001", "p_001_press_palm_low_x5_right")
frame = seq.load_frame(60, depth=True)      # rgb, depth, force, annotation
frame.show(camera="d", overlays=["mesh", "skeleton", "pressure"])
```

The toolkit's [data reference](https://github.com/eth-siplab/EgoPressure/blob/master/docs/DATA.md)
documents every field, unit, and convention.

## Download without the toolkit

Everything is sharded by participant / sequence / camera / modality, so plain
`huggingface_hub` works too:

```python
from huggingface_hub import snapshot_download

snapshot_download("eth-siplab/EgoPressure", repo_type="dataset",
                  local_dir="egopressure_data",
                  allow_patterns=["configs/p_001/*",
                                  "data/p_001/*/cam-d.color.parquet",
                                  "data/p_001/*/pressure.parquet",
                                  "data/p_001/*/annotation.parquet"])
```

### Layout

```
configs/<participant>/<sequence>.json         # camera calibration + metadata
configs/<participant>/<sequence>_k4a/         # per-camera factory calibrations
data/<participant>/<sequence>/
    cam-d.color.parquet   cam-1.color.parquet ... cam-7.color.parquet
    cam-d.depth.parquet   cam-1.depth.parquet ... cam-7.depth.parquet
    cam-d.mask.parquet    cam-1.mask.parquet  ... cam-7.mask.parquet
    pressure.parquet                                 # Sensel grid + UV pressure
    annotation.parquet                               # MANO pose/mesh + ego pose
```

Each Parquet shard holds one row per frame (`frame` column).

### Modalities

| Shard | Content |
|---|---|
| `cam-*.color` | RGB frames as original JPEG bytes (ego `d`: 1920×1080; static `1..7`: 2560×1440; undistorted) |
| `cam-*.depth` | 512×512 uint16 depth in millimetres (PNG bytes), all 8 cameras; depth-sensor frame, covering the camera's color view (+32 px guard band) — `0` = no measurement |
| `cam-*.mask` | hand segmentation masks (PNG bytes) |
| `pressure` | raw Sensel Morph grid (105×185 float32, flattened) + normalised 224×224 UV pressure map with `[min, max]` range |
| `annotation` | MANO `vertices (778×3)`, `joint_position (21×3)`, `betas (10)`, `full_pose (48)`, `transl`, `normals`, `displacement`, per-static-camera `visible_vertices (7×778)`, per-frame egocentric camera pose (`ego_R` 3×3, `ego_T` 3) |

### Geometry essentials

- World origin on the touchpad surface (the calibrated 240 × 137.5 mm pad
  rectangle, x–y plane at z = 0); MANO vertices and the ego pose are in **metres**, static-camera
  `ModelViewMatrix` extrinsics in **millimetres**.
- Images are undistorted → plain pinhole projection with the config intrinsics.
- Depth maps are in each camera's **depth-sensor frame** (not registered to
  color) and cover the region co-visible with that camera's color image;
  `configs/<p>/<seq>_k4a/` holds the factory calibrations — the toolkit's
  `register_depth_to_color()` produces pixel-aligned RGB-D.
- Raw sensor counts convert to Newtons via `counts / 1736` (cell pitch 1.25 mm).

## License & citation

Released under **CC BY-NC-SA 4.0** (non-commercial, academic). MANO-derived
annotations are additionally subject to the
[MANO license](https://mano.is.tue.mpg.de/license.html).

```bibtex
@InProceedings{Zhao_2025_CVPR,
  author    = {Zhao, Yiming and Kwon, Taein and Streli, Paul and Pollefeys, Marc and Holz, Christian},
  title     = {EgoPressure: A Dataset for Hand Pressure and Pose Estimation in Egocentric Vision},
  booktitle = {Proceedings of the IEEE/CVF Conference on Computer Vision and Pattern Recognition (CVPR)},
  month     = {June},
  year      = {2025},
  pages     = {27727--27738}
}
```
