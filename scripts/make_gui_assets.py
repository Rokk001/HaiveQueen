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
}
# The three icons of the System panel are drawn here (the mockup shows a folded flag and a padlock): a processor chip, a memory module and a drive.
GOLD = (255, 250, 20)   # the stroke colour of the other icons
ICON_SIZE = (32, 28)    # the size of the other icons
SCALE = 8               # drawn at 8 times the size, then scaled down for smooth lines
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


# Draw the icon of the processor chip.
def draw_cpu(d, k):
    """Chip: square body with a smaller square die and pins on each side (coordinates in icon pixels, `k` scales them)."""
    w = int(2 * k)
    d.rounded_rectangle([8 * k, 6 * k, 24 * k, 22 * k], radius=2 * k, outline=GOLD, width=w)
    d.rectangle([12 * k, 10 * k, 20 * k, 18 * k], outline=GOLD, width=w)
    for x in (11, 16, 21):
        d.line([x * k, 2 * k, x * k, 6 * k], fill=GOLD, width=w)
        d.line([x * k, 22 * k, x * k, 26 * k], fill=GOLD, width=w)
    for y in (10, 14, 18):
        d.line([4 * k, y * k, 8 * k, y * k], fill=GOLD, width=w)
        d.line([24 * k, y * k, 28 * k, y * k], fill=GOLD, width=w)


# Draw the icon of the memory module.
def draw_ram(d, k):
    """Memory module: a long board with three memory chips on it and a row of contacts at the lower edge."""
    w = int(2 * k)
    d.rounded_rectangle([2 * k, 6 * k, 30 * k, 21 * k], radius=1.5 * k, outline=GOLD, width=w)
    for x in (5, 12, 19):
        d.rectangle([x * k, 9 * k, (x + 5) * k, 15 * k], outline=GOLD, width=int(1.5 * k))
    d.rectangle([26 * k, 9 * k, 28 * k, 15 * k], fill=GOLD)
    for x in range(5, 29, 4):
        d.rectangle([x * k, 21 * k, (x + 2) * k, 25 * k], fill=GOLD)


# Draw the icon of the drive.
def draw_disk(d, k):
    """Drive: a flat housing with a platter circle in the upper part and a bar with an indicator light below."""
    w = int(2 * k)
    d.rounded_rectangle([3 * k, 3 * k, 29 * k, 25 * k], radius=3 * k, outline=GOLD, width=w)
    d.ellipse([10 * k, 6 * k, 22 * k, 16 * k], outline=GOLD, width=w)
    d.ellipse([14.5 * k, 9.5 * k, 17.5 * k, 12.5 * k], fill=GOLD)
    d.line([7 * k, 20.5 * k, 17 * k, 20.5 * k], fill=GOLD, width=w)
    d.ellipse([23 * k, 19 * k, 26 * k, 22 * k], fill=GOLD)


# Write the three System icons.
def write_system_icons():
    """Draw icon_cpu, icon_ram and icon_disk at 8 times the size on the dashboard background and write them at the size of the other icons."""
    from PIL import ImageDraw
    for name, draw in (("icon_cpu", draw_cpu), ("icon_ram", draw_ram), ("icon_disk", draw_disk)):
        big = Image.new("RGB", (ICON_SIZE[0] * SCALE, ICON_SIZE[1] * SCALE), BG)
        draw(ImageDraw.Draw(big), SCALE)
        big.resize(ICON_SIZE, Image.LANCZOS).save(OUT / (name + ".png"))


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
    write_system_icons()


if __name__ == "__main__":
    main()
