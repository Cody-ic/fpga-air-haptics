"""Render manufacturing layers only; optional PyGerber 2.4.3 and Pillow."""
import zipfile
from pathlib import Path

from PIL import Image, ImageOps
from pygerber.common.rgba import RGBA
from pygerber.gerberx3.api.v2 import (
    ColorScheme, FileTypeEnum, GerberFile, ParsedProject, PixelFormatEnum,
)

ROOT = Path(__file__).resolve().parent


def color(hex_color):
    return ColorScheme.COPPER_ALPHA.model_copy(update={
        "solid_color": RGBA.from_hex(hex_color),
        "solid_region_color": RGBA.from_hex(hex_color),
    })


def main():
    output = ROOT / "previews"
    output.mkdir(exist_ok=True)
    with zipfile.ZipFile(ROOT / "Haptics_8x8_R1_release_Gerber.zip") as archive:
        for side, extensions in (("top", (".GTL", ".GTS", ".GTO", ".GKO")),
                                 ("bottom", (".GBL", ".GBS", ".GBO", ".GKO"))):
            layers = []
            for extension, kind in zip(extensions, (FileTypeEnum.COPPER, FileTypeEnum.MASK,
                                                    FileTypeEnum.SILK, FileTypeEnum.EDGE)):
                name = next(n for n in archive.namelist() if n.endswith(extension))
                layers.append(GerberFile.from_str(archive.read(name).decode("utf-8"), kind).parse())
            target = output / f"gerber_{side}.png"
            ParsedProject(layers).render_raster(target, dpmm=12,
                color_map={FileTypeEnum.COPPER: color("#24594a"),
                           FileTypeEnum.MASK: color("#c7ad69"),
                           FileTypeEnum.SILK: color("#f3f0dc"),
                           FileTypeEnum.EDGE: color("#789b95")},
                pixel_format=PixelFormatEnum.RGBA)
            foreground = Image.open(target).convert("RGBA")
            if side == "bottom":
                foreground = ImageOps.mirror(foreground)
            image = Image.new("RGBA", foreground.size, "#101820")
            image.alpha_composite(foreground)
            image.convert("RGB").save(target)
            print(target.name, flush=True)


if __name__ == "__main__":
    main()
