"""Check actual meshes, render both stacks and package only two print files."""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

from PIL import Image, ImageDraw, ImageFont
import numpy as np
import trimesh

from build_lifts import HERE, ROOT, OUT, RELEASE, P, NAMES, dimensions, layout


def load(path):
    return trimesh.load_mesh(path, process=True)


def heights(mesh, x, y):
    triangles = mesh.triangles
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    det = (b[:,0]-a[:,0])*(c[:,1]-a[:,1])-(b[:,1]-a[:,1])*(c[:,0]-a[:,0])
    valid = abs(det) > 1e-9
    a, b, c, det = a[valid], b[valid], c[valid], det[valid]
    u = ((x-a[:,0])*(c[:,1]-a[:,1])-(y-a[:,1])*(c[:,0]-a[:,0]))/det
    v = ((b[:,0]-a[:,0])*(y-a[:,1])-(b[:,1]-a[:,1])*(x-a[:,0]))/det
    inside = (u >= -1e-8) & (v >= -1e-8) & (u+v <= 1+1e-8)
    z = a[:,2]+u*(b[:,2]-a[:,2])+v*(c[:,2]-a[:,2])
    return np.unique(np.round(z[inside], 5))


def check():
    d = dimensions()
    records = []
    meshes = [load(OUT/"stl"/(name+".stl")) for name in NAMES]
    for i, (name, mesh) in enumerate(zip(NAMES, meshes)):
        expected = (*d["film_outer_mm" if i else "receiver_outer_mm"], 22.)
        metadata = json.loads((OUT/(name+".json")).read_text(encoding="utf-8"))
        assert mesh.is_watertight and mesh.is_winding_consistent and mesh.body_count == 1, name
        assert np.allclose(mesh.extents, expected, atol=.001), (name, mesh.extents)
        assert abs(mesh.bounds[0,2]) < .001 and np.all(mesh.extents < P["print_bed_mm"])
        assert abs(mesh.volume-metadata["volume_mm3"])/metadata["volume_mm3"] < .0001
        records.append(dict(name=name, size_mm=mesh.extents.tolist(), watertight=True,
                            connected_solids=1, volume_mm3=float(mesh.volume)))
    # Original receiver tray rests on a flat Z=20 bearing surface; its base is 4 mm thick.
    for x in np.linspace(-30, 30, 7):
        for y in np.linspace(-20, 20, 5):
            assert np.allclose(heights(meshes[0], x, y), [0,20])
    # Each unchanged film foot is fully supported; central observation window stays open.
    for sx in (-1, 1):
        for sy in (-1, 1):
            for dx in (-4., 0., 4.):
                for dy in (-4., 0., 4.):
                    assert np.allclose(heights(meshes[1], sx*55+dx, sy*68+dy), [0,20])
    aperture_probes = 0
    for x in np.linspace(-45, 45, 11):
        for y in np.linspace(-58, 58, 13):
            assert len(heights(meshes[1], x, y)) == 0
            aperture_probes += 1
    manifest = json.loads((OUT/"manifest.json").read_text(encoding="utf-8"))
    assert manifest["receiver_face_z_mm"] == manifest["film_bottom_z_mm"] == 44.7
    assert manifest["acoustic_gap_with_tx_risers_mm"] == 80.
    # With the transmitter risers unchanged, film lift fits between their inner edges.
    right_clearance = 68-d["film_outer_mm"][0]/2
    assert right_clearance >= 3.5
    result = dict(passed=True, parts=records, seat_height_mm=20., locating_lip_height_mm=2.,
                  observation_aperture_probes=aperture_probes,
                  film_centered_right_riser_clearance_mm=right_clearance,
                  actual_print_fit_not_tested=True)
    (OUT/"mesh_validation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return meshes


def preview(meshes, assembled):
    image = Image.new("RGB", (2200, 1260), "white")
    draw = ImageDraw.Draw(image)
    font = "C:/Windows/Fonts/msyh.ttc"
    title = ImageFont.truetype(font, 42)
    heading = ImageFont.truetype(font, 30)
    caption = ImageFont.truetype(font, 25)
    def centered(x, y, text, face, color="#294657"):
        draw.text((x,y), text, font=face, fill=color, anchor="mm")
    centered(1100, 54, "20 mm 增高垫 · 装配关系" if assembled else "两个一体打印垫子 · 每种打印1个", title)
    for i, name in enumerate(NAMES):
        center = 550+1100*i
        label = ("接收板＋原托盘＋新增垫子" if i == 0 else "薄膜夹框及原支脚＋新增垫子") if assembled else (
                  "13 接收托盘增高垫" if i == 0 else "14 薄膜支架增高垫")
        centered(center, 130, label, heading)
        render = Image.open(OUT/"preview"/(name+("_assembled" if assembled else "_bare")+".png")).convert("RGB")
        image.paste(render, (1100*i, 180))
        x,y,z = meshes[i].extents
        label = ("接收板继续固定在原托盘上" if i == 0 else "原膜架四脚放在新增垫子的承托面上") if assembled else (
                  f"{x:.1f} × {y:.3f} × 22 mm；承托面高20 mm")
        centered(center, 1090, label, caption)
    centered(1100, 1185, "蓝色为新增件，棕色为原打印件；原模型网格渲染，线缆未绘出。" if assembled else
             "平底朝下打印，挡边朝上；承托面20 mm＋定位挡边2 mm＝文件总高22 mm。", caption)
    image.save(OUT/"preview"/("垫高装配示意.png" if assembled else "新增垫子预览.png"))


def package():
    RELEASE.mkdir(exist_ok=True)
    for folder, suffix in (("stl",".stl"),("solidworks",".SLDPRT"),("step",".step")):
        (RELEASE/folder).mkdir(exist_ok=True)
        for name in NAMES:
            shutil.copy2(OUT/folder/(name+suffix), RELEASE/folder/(name+suffix))
    for name in ("manifest.json","native_validation.json","mesh_validation.json"):
        shutil.copy2(OUT/name, RELEASE/name)
    for name in ("新增垫子预览.png","垫高装配示意.png"):
        shutil.copy2(OUT/"preview"/name, RELEASE/name)
    instructions = (HERE/"打印说明.txt").read_text(encoding="utf-8")
    (RELEASE/"打印说明.txt").write_text(instructions, encoding="utf-8-sig")
    target = RELEASE/"TouchSee_Receiver_Film_Lift20_Print_2pcs_20261025.zip"
    with zipfile.ZipFile(target,"w",zipfile.ZIP_DEFLATED) as archive:
        for name in NAMES:
            archive.write(RELEASE/"stl"/(name+".stl"), name+".stl")
        archive.writestr("00_打印说明.txt", instructions.replace("\r\n","\n").replace("\n","\r\n").encode("utf-8-sig"))
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None and len(archive.namelist()) == 3
    print(json.dumps(dict(file=str(target), sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                          stl_files=2, instruction_files=1, each_quantity=1), ensure_ascii=False))


if __name__ == "__main__":
    meshes = check()
    preview(meshes, False)
    preview(meshes, True)
    package()
