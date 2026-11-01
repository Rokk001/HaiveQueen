"""Cut the graphics of the graphical dashboard out of the mockup docs/HaiveQueen_Dashboard.png into assets/gui/ (development tool: needs Pillow).

The window itself uses only tkinter; it loads the PNG files written here. Run: python scripts/make_gui_assets.py
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
MOCKUP = ROOT / "docs" / "HaiveQueen_Dashboard.png"
OUT = ROOT / "assets" / "gui"
BG = (0, 6, 8)   # background colour of the mockup
# name: (left, top, right, bottom) in mockup pixels (the mockup includes a 40 px window title bar)
CROPS = {
    "t_apiaries": (22, 215, 82, 285), "t_hives": (228, 215, 290, 285), "t_queens": (432, 215, 495, 285),
    "t_bees": (625, 215, 692, 285), "t_io": (832, 212, 882, 290),
    "panel_icon": (24, 314, 56, 346), "row_icon": (58, 380, 82, 404), "row_icon_dim": (58, 410, 82, 434),
    "foot_bee": (14, 893, 58, 936), "foot_hex": (1328, 893, 1366, 931),
    "icon_logs": (1056, 214, 1086, 242), "icon_system": (524, 746, 556, 774), "icon_queue": (858, 746, 890, 774),
    "icon_cpu": (524, 783, 556, 811), "icon_ram": (524, 812, 556, 840), "icon_disk": (524, 841, 556, 869),
}
# The header (logo, wordmark, honeycomb, slogan) is one picture; the version line is painted over and drawn as text by the window.
HEADER = (0, 41, 1672, 204)
VERSION_BOX = (336, 170, 700, 200)


ICON_SOURCE = ROOT / "docs" / "icon.png"   # the app icon: the golden Queen bee in a hexagon


# Write the window and shortcut icon.
def write_app_icon():
    """Write assets/gui/app.ico (16 to 256 px: window, taskbar, desktop shortcut) and app_icon.png (256 px, for systems without .ico) from docs/icon.png."""
    if not ICON_SOURCE.is_file():
        return
    icon = Image.open(ICON_SOURCE).convert("RGBA")
    icon.save(OUT / "app.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    icon.resize((256, 256), Image.LANCZOS).save(OUT / "app_icon.png")


# Cut every picture.
def main():
    """Write the header and all icons as PNG files, and the app icon."""
    OUT.mkdir(parents=True, exist_ok=True)
    write_app_icon()
    im = Image.open(MOCKUP).convert("RGB")
    head = im.crop(HEADER)
    x0, y0, x1, y1 = VERSION_BOX
    head.paste(BG, (x0, y0 - HEADER[1], x1, y1 - HEADER[1]))
    head.save(OUT / "header.png")
    for name, box in CROPS.items():
        im.crop(box).save(OUT / (name + ".png"))


if __name__ == "__main__":
    main()
