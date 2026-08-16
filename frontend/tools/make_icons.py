"""Regenerate the PWA icons in ../public.  Run: python tools/make_icons.py

Draws an ascending bar-chart glyph — no font dependency, so it renders identically
everywhere. Tweak BG / FG and re-run if you want a different colour.
"""

import pathlib

from PIL import Image, ImageDraw

BG = (42, 120, 214)      # #2a78d6 — the app accent
FG = (255, 255, 255)
OUT = pathlib.Path(__file__).resolve().parent.parent / "public"


def rounded_rect(draw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def render(size: int, maskable: bool) -> Image.Image:
    # 4x supersample, then downscale — cheap antialiasing.
    scale = 4
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    if maskable:
        # Maskable icons get cropped to a circle by Android; fill the whole canvas
        # and keep the glyph inside the middle 80% safe zone.
        d.rectangle([0, 0, s, s], fill=BG)
        inset = s * 0.28
    else:
        rounded_rect(d, [0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=BG)
        inset = s * 0.24

    # Three ascending bars sitting on a common baseline.
    usable = s - 2 * inset
    bar_w = usable * 0.22
    gap = (usable - 3 * bar_w) / 2
    baseline = s - inset
    heights = [0.42, 0.68, 1.0]

    for i, h in enumerate(heights):
        x0 = inset + i * (bar_w + gap)
        y0 = baseline - usable * h
        rounded_rect(d, [x0, y0, x0 + bar_w, baseline], radius=int(bar_w * 0.3), fill=FG)

    return img.resize((size, size), Image.LANCZOS)


OUT.mkdir(parents=True, exist_ok=True)
for size in (192, 512):
    render(size, maskable=False).save(OUT / f"icon-{size}.png")
render(512, maskable=True).save(OUT / "icon-maskable-512.png")
render(32, maskable=False).save(OUT / "favicon.png")
print(f"wrote icons to {OUT}")
