"""Generate math constants and a static text overlay, never pre-rendered field frames."""
import argparse
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def generate(destination, font_path="C:/Windows/Fonts/msyh.ttc", *, preview_only=True, startup_calibration=False):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "sine.hex").write_text("".join(
        f"{round(2047 * math.sin(2 * math.pi * i / 1024)) & 4095:03x}\n"
        for i in range(1024)), encoding="ascii")
    mask = Image.new("1", (1024, 600))
    draw = ImageDraw.Draw(mask)
    def label(x, y, text, size=19):
        draw.text((x, y), text, fill=1, font=ImageFont.truetype(font_path, size))
    label(32, 12, "触见  /  理论声场", 25)
    label(596, 24, "启动校准 + 8×8控制" if startup_calibration else
          "仅预览 · 实际发射关闭" if preview_only else "8×8 阵列控制 / 理论声场", 24)
    label(590, 70, "二维幅值分布", 26)
    label(622, 120, "输出 / 校准状态" if startup_calibration else "播放状态", 20)
    label(590, 160, "8 × 8 阵列  /  间距 10.3 mm")
    label(590, 200, "截面高度", 20); label(860, 200, "mm", 20)
    label(590, 272, "单帧计算耗时", 20); label(860, 272, "ms", 20)
    label(590, 324, "颜色：归一化声压幅值", 21)
    label(590, 360, "理想点声源 · 单位激励 · 无反射", 18)
    label(590, 440, "0", 18); label(835, 440, "1", 18)
    label(590, 480, "先完成CAL2校准，再进入HAP3" if startup_calibration else "整幅图形下发后，由板内执行轨迹", 18)
    label(590, 512, "同一标尺比较各帧；非实测声压", 18)
    label(590, 548, "蓝：校准等待  绿：输出  黄：暂停" if startup_calibration else "绿：播放   黄：暂停   灰：停止", 17)
    label(36, 572, "−94.5", 16); label(265,572,"X / mm",16);label(495,572,"+94.5",16)
    label(36, 58, "+Y", 16)
    pixels = list(mask.getdata())
    words = [sum(int(pixels[i+j]) << j for j in range(16)) for i in range(0,len(pixels),16)]
    (destination / "labels.hex").write_text("".join(f"{v:04x}\n" for v in words),encoding="ascii")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--font", default="C:/Windows/Fonts/msyh.ttc")
    parser.add_argument("--combined", action="store_true", help="Label a combined transmitter/display build")
    args = parser.parse_args()
    generate(args.destination, args.font, preview_only=not args.combined)
