#!/usr/bin/env python3
"""生成 PWA 图标（纯标准库 PNG 编码，无第三方依赖）。

设计: 深色渐变圆角方底 + 薄荷绿终端 Chevron ">" + 方块光标。
"""
from __future__ import annotations

import os
import struct
import zlib

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "server", "static", "icons")

BG_TOP = (16, 26, 38)      # #101A26
BG_BOTTOM = (12, 42, 36)   # #0C2A24
ACCENT = (61, 220, 151)    # #3DDC97


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def seg_dist(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
    t = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / L2))
    return ((px - (x1 + t * dx)) ** 2 + (py - (y1 + t * dy)) ** 2) ** 0.5


def rounded_rect_alpha(x, y, w, h, r):
    if x < 0 or y < 0 or x >= w or y >= h:
        return 0.0
    for cx, cy in ((r, r), (w - r, r), (r, h - r), (w - r, h - r)):
        inside_corner = (x < r or x >= w - r) and (y < r or y >= h - r)
        if inside_corner and ((x - cx) ** 2 + (y - cy) ** 2) > r * r:
            return 0.0
    return 1.0


def make_pixel(safe_scale=1.0, crop_circle=False):
    def pixel(px, py, w, h):
        # 背景
        a_bg = rounded_rect_alpha(px, py, w, h, w * 0.225)
        bg = lerp(BG_TOP, BG_BOTTOM, py / h)
        # 内容区（maskable 缩放到 80% 安全区）
        s = safe_scale
        cx, cy = w / 2, h / 2
        X = (px - cx) / s + cx
        Y = (py - cy) / s + cy
        d1 = seg_dist(X, Y, w * 0.32, h * 0.30, w * 0.50, h * 0.50)
        d2 = seg_dist(X, Y, w * 0.50, h * 0.50, w * 0.32, h * 0.70)
        chev = max(0.0, 1.0 - min(d1, d2) / (w * 0.055))
        # 光标方块
        cur = (w * 0.56 <= X <= w * 0.74) and (h * 0.60 <= Y <= h * 0.72)
        r, g, b = bg
        a = a_bg
        if chev > 0:
            k = min(1.0, chev)
            r, g, b = lerp((r, g, b), ACCENT, k)
            a = a_bg
        if cur:
            r, g, b = ACCENT
        if crop_circle:
            a = a_bg if ((px - cx) ** 2 + (py - cy) ** 2) <= (w / 2) ** 2 else 0.0
        return (r, g, b, int(a * 255))
    return pixel


def write_png(path, w, h, pixel_fn, ss=2):
    raw = bytearray()
    for y in range(h):
        raw.append(0)  # filter: none
        for x in range(w):
            acc = [0, 0, 0, 0]
            for sy in range(ss):
                for sx in range(ss):
                    r, g, b, a = pixel_fn(x + sx / ss, y + sy / ss, w, h)
                    acc[0] += r; acc[1] += g; acc[2] += b; acc[3] += a
            raw += bytes(c // (ss * ss) for c in acc)
    comp = zlib.compress(bytes(raw), 9)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", comp)
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)
    print(f"{path}: {len(png)} bytes")


def main():
    os.makedirs(OUT, exist_ok=True)
    write_png(os.path.join(OUT, "icon-192.png"), 192, 192, make_pixel())
    write_png(os.path.join(OUT, "icon-512.png"), 512, 512, make_pixel())
    # maskable: 内容缩到 80% 安全区
    write_png(os.path.join(OUT, "icon-maskable-512.png"), 512, 512,
              make_pixel(safe_scale=0.8))
    # apple-touch-icon: 不透明底
    write_png(os.path.join(OUT, "apple-touch-icon.png"), 180, 180, make_pixel())


if __name__ == "__main__":
    main()
