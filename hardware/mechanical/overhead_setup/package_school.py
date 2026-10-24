"""Expand checked P5 STLs into one-file-per-piece full and top-up school orders."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import zipfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SOURCE = ROOT / "P5_20261015"
DESTINATION = ROOT / "P5_school_spares_20261020"
# Both boards keep their own carrier and four sleeves; the side stand is shared.
# Last school order produced one piece of each of the seven source models.
ORDER = (
    # model, description, assembly/test quantity, spares, already available
    ("01_side_trestle", "共用侧架", 2, 0, 1),
    ("02_tx_carrier_4x4", "4×4 发射承载框", 1, 0, 1),
    ("03_tx_carrier_8x8", "8×8 发射承载框", 1, 0, 1),
    ("04_m3_rear_sleeve_6x5", "绝缘套筒", 8, 4, 1),
    ("05_receiver_flat_tray", "接收托盘", 1, 0, 1),
    ("06_receiver_edge_clip", "接收板短压片", 4, 2, 1),
    ("07_fastener_fit_coupon", "孔径试片", 1, 0, 1),
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    archive = SOURCE / "TouchSee_Overhead_STL_P5_20261015.zip"
    sums = dict(line.split("  ", 1)[::-1]
                for line in (SOURCE / "SHA256SUMS.txt").read_text().splitlines())
    assert digest(archive.read_bytes()) == sums[archive.name], "Source archive changed"
    expected = json.loads((SOURCE / "file_manifest.json").read_text(encoding="utf-8"))
    with zipfile.ZipFile(archive) as source:
        actual = {name for name in source.namelist() if name.lower().endswith(".stl")}
        assert actual == {f"stl/{part}.stl" for part, _, _, _, _ in ORDER}
        models = {name: source.read(name) for name in actual}
    for name, data in models.items():
        assert digest(data) == expected[name], name
    DESTINATION.mkdir(exist_ok=True)
    instructions = (HERE / "学校打印需求.md").read_text(encoding="utf-8")
    (DESTINATION / "学校打印需求.md").write_text(instructions, encoding="utf-8")
    summary = dict(date="2026-10-20", order_revision="P5-SCHOOL-R2", model_revision="P5-20261015",
                   source_archive=archive.name, source_sha256=digest(archive.read_bytes()),
                   geometry_changed=False, orders=[])
    outputs = []
    for topup, total, label in ((True, 17, "TOPUP"), (False, 24, "FULL")):
        items = []
        for part, chinese, required, spares, printed in ORDER:
            target = required + spares
            for copy in range(printed + 1 if topup else 1, target + 1):
                purpose = "spare" if copy > required else "fit_test" if part.startswith("07_") else "assembly"
                items.append(dict(filename=f"stl/{part}_{copy:02d}of{target:02d}.stl",
                                  model=f"stl/{part}.stl", name=chinese,
                                  purpose=purpose,
                                  print_quantity=1, sha256=digest(models[f"stl/{part}.stl"])))
        assert len(items) == total
        roles = Counter(item['purpose'] for item in items)
        assert roles['spare'] == 6
        title = "补充打印 17 件（含备件）" if topup else "全量打印 24 件（含试片及备件）"
        note = (f"P5 支架零件交付单：{title}\n订单版本 P5-SCHOOL-R2，2026-10-20。\n\n"
                f"本包有 {total} 个 STL 文件，每个文件只打印 1 件，总计 {total} 件。\n"
                "已含备用套筒 4 件、备用短压片 2 件，不再额外增加备件。\n"
                "同形文件不得去重；只执行本包，不与其他版本打印包叠加。\n"
                "单位 mm，比例 100%，不缩放、不镜像，保留模型打印朝向和孔槽。\n"
                "只打印 stl 文件夹，不打印螺钉、螺母、垫圈或电子件。\n\n")
        counts = Counter(item["name"] for item in items)
        note += "本包清单：\n" + "".join(f"{name}：{count} 件\n" for name, count in counts.items())
        if topup:
            note += "\n已备的原始 7 类模型各 1 件不重复生产；补齐后合计 24 件，含备件 6 件。\n"
        note += "\n详细条件见同包《学校打印需求.md》。\n"
        guide = instructions
        if not topup:
            print_rules = instructions.split("## 4. 打印要求\n", 1)[1].split("## 5. 交付与验收", 1)[0]
            guide = ("# P5 支架零件全量打印交付单\n\n" + note +
                     "\n## 全量数量\n\n| 零件 | 数量 |\n|---|---:|\n" +
                     "".join(f"| {name} | {count} |\n" for name, count in counts.items()) +
                     "\n两块发射板各有承载框和 4 个套筒，共用一套左右侧架。\n"
                     "其余 4 个套筒及 2 个短压片为备件；全部数量已计入本包。\n"
                     "\n## 文件逐项清单\n\n| 文件名 | 用途 | 数量 |\n|---|---|---:|\n" +
                     "".join(f"| `{Path(item['filename']).name}` | " +
                             {"assembly":"装配", "spare":"备用", "fit_test":"试配"}[item['purpose']] +
                             " | 1 |\n" for item in items) +
                     "\n## 打印要求\n" + print_rules +
                     "\n## 全量交付验收\n\n总计 24 件：装配件 17 件、试片 1 件、备件 6 件。"
                     "装配件和备件分袋标注，同形件不去重，金属标准件不打印。\n")
        else:
            listed = instructions.split("## 3. 本批文件逐项清单",1)[1].split("## 4.",1)[0]
            names = {line.split('`')[1] for line in listed.splitlines() if line.startswith('| `')}
            assert names == {Path(item['filename']).name for item in items}, "Guide file list differs from order"
        path = DESTINATION / f"TouchSee_P5_School_{label}_{total}pcs_spares_20261020.zip"
        receipt = dict(order=label, stl_files=total, pieces=total,
                       order_revision="P5-SCHOOL-R2", purposes=dict(roles),
                       per_file_quantity=1, geometry_changed=False, items=items)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as package:
            def put(name, data):
                info = zipfile.ZipInfo(name, date_time=(2026, 10, 20, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                package.writestr(info, data)
            for item in items:
                put(item["filename"], models[item["model"]])
            put("请先读_每个文件打印1件.txt", note.encode("utf-8-sig"))
            put("学校打印需求.md", guide.encode("utf-8"))
            put("piece_manifest.json", json.dumps(receipt, ensure_ascii=False, indent=2).encode("utf-8"))
        with zipfile.ZipFile(path) as package:
            assert package.testzip() is None
            assert len(package.namelist()) == len(set(package.namelist()))
            stls = {name for name in package.namelist() if name.endswith(".stl")}
            assert len(stls) == total
            assert stls == {item["filename"] for item in items}
            for item in items:
                assert digest(package.read(item["filename"])) == item["sha256"]
        outputs.append(path)
        summary["orders"].append(dict(filename=path.name, pieces=total,
                                       quantities=dict(counts), sha256=digest(path.read_bytes())))
    (DESTINATION / "validation.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (DESTINATION / "SHA256SUMS.txt").write_text(
        "".join(f"{digest(path.read_bytes())}  {path.name}\n" for path in outputs), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
