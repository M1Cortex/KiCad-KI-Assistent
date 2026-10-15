"""Generate the plugin toolbar icons (run manually; icons are committed)."""

from __future__ import annotations

import os

from PIL import Image, ImageDraw

BASE = os.path.dirname(os.path.abspath(__file__))


def draw_icon(size: int, light: bool) -> Image.Image:
    S = size * 4  # supersample for anti-aliasing
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    fg = (40, 40, 45, 255) if light else (235, 238, 245, 255)
    accent = (200, 30, 45, 255)
    green = (30, 150, 90, 255)

    m = S * 0.12
    lw = max(2, int(S * 0.055))

    d.rounded_rectangle(
        [m, m, S - m, S - m], radius=S * 0.12, outline=fg, width=lw
    )

    y1, y2 = S * 0.34, S * 0.62
    x_a, x_b, x_mid = S * 0.26, S * 0.74, S * 0.50

    d.line([(x_a, y1), (x_b, y1)], fill=fg, width=lw)
    d.line([(x_a, y2), (x_b, y2)], fill=fg, width=lw)
    d.line([(x_mid, y1), (x_mid, y2)], fill=fg, width=lw)

    pr = lw * 1.25
    for px, py in [
        (x_a, y1), (x_b, y1), (x_a, y2), (x_b, y2), (x_mid, y1), (x_mid, y2)
    ]:
        d.ellipse([px - pr, py - pr, px + pr, py + pr], fill=fg)

    ring_r = S * 0.155
    cx, cy = S * 0.66, S * 0.63
    d.ellipse([cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r],
              outline=accent, width=lw)
    hx0, hy0 = cx + ring_r * 0.72, cy + ring_r * 0.72
    hx1, hy1 = S * 0.88, S * 0.85
    d.line([(hx0, hy0), (hx1, hy1)], fill=accent, width=lw)
    d.ellipse([hx1 - lw, hy1 - lw, hx1 + lw, hy1 + lw], fill=accent)

    c_w = max(2, int(S * 0.045))
    d.line([(cx - ring_r * 0.48, cy), (cx - ring_r * 0.12, cy + ring_r * 0.36)],
           fill=green, width=c_w)
    d.line([(cx - ring_r * 0.12, cy + ring_r * 0.36),
            (cx + ring_r * 0.50, cy - ring_r * 0.38)], fill=green, width=c_w)

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    os.makedirs(os.path.join(BASE, "icons"), exist_ok=True)
    for light in (True, False):
        theme = "light" if light else "dark"
        for size in (24, 48):
            out = os.path.join(BASE, "icons", f"review_{theme}_{size}.png")
            draw_icon(size, light).save(out)
            print("wrote", out)


if __name__ == "__main__":
    main()