"""Generate math constants and a static text overlay, never pre-rendered field frames."""
import argparse
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def generate(destination, font_path="C:/Windows/Fonts/msyh.ttc"):
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
    label(622, 20, "发射使能", 20); label(854, 20, "本地按键", 20)
    label(590, 66, "理论声场 · 当前设备配置", 23)
    label(622, 106, "播放状态", 19);label(822,106,"图像有效",19)
    label(622, 142, "单焦点轨迹", 19);label(822,142,"静态相位",19)
    label(590, 180, "截面高度", 20);label(916,180,"mm",18)
    label(590, 216, "驱动等级", 20);label(916,216,"%",18)
    label(590, 252, "轨迹频率", 20);label(916,252,"Hz",18)
    label(590, 288, "调制频率", 20);label(916,288,"Hz",18)
    label(590, 324, "载波频率", 20);label(916,324,"Hz",18)
    label(590, 360, "计算耗时", 20);label(916,360,"ms",18)
    label(590, 440, "0", 18);label(835,440,"1",18)
    label(590, 470, "颜色：归一化幅值 · 单位激励", 18)
    label(590, 498, "8 × 8 / 10.3 mm · 非实测声压", 18)
    label(590, 526, "绿：播放  黄：暂停  灰：停止", 17)
    label(590, 554, "只读显示 · 触摸控制尚未接入", 17)
    label(36, 572, "−94.5", 16); label(265,572,"X / mm",16);label(495,572,"+94.5",16)
    label(36, 58, "+Y", 16)
    pixels = list(mask.getdata())
    words = [sum(int(pixels[i+j]) << j for j in range(16)) for i in range(0,len(pixels),16)]
    (destination / "labels.hex").write_text("".join(f"{v:04x}\n" for v in words),encoding="ascii")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--font", default="C:/Windows/Fonts/msyh.ttc")
    args = parser.parse_args()
    generate(args.destination, args.font)
