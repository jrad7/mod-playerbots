"""Mask stitching and audit renders (Phase 1.4)."""
from __future__ import annotations

import os

import numpy as np
from PIL import Image

import wowdata as w
from gridmap import GridMap
from zones import OUT, load_tile

TEX = 1024
"""Alpha texels per tile side."""


def stitch(map_id: int, tiles: list[tuple[int, int]], ds: int = 8,
           road_words=None) -> tuple[np.ndarray, tuple[int, int]]:
    """Stitch tile masks into one array at TEX/ds pixels per tile.

    Returns (image, (col0, row0)). Image rows run north->south, columns west->east.
    """
    if not tiles:
        return np.zeros((1, 1), np.uint8), (0, 0)
    cols = [c for c, _ in tiles]
    rows = [r for _, r in tiles]
    c0, c1, r0, r1 = min(cols), max(cols), min(rows), max(rows)
    step = TEX // ds
    img = np.zeros(((r1 - r0 + 1) * step, (c1 - c0 + 1) * step), np.uint8)
    for col, row in tiles:
        mask, _ = load_tile(map_id, col, row)
        if mask is None:
            continue
        sub = mask.reshape(step, ds, step, ds).max(axis=(1, 3))
        y = (row - r0) * step
        x = (col - c0) * step
        img[y:y + step, x:x + step] = sub
    return img, (c0, r0)


def hillshade(map_id: int, tiles: list[tuple[int, int]], ds: int = 8) -> np.ndarray:
    """Dim terrain background from the server height grid, same framing as stitch()."""
    cols = [c for c, _ in tiles]
    rows = [r for _, r in tiles]
    c0, c1, r0, r1 = min(cols), max(cols), min(rows), max(rows)
    step = TEX // ds
    h = np.full(((r1 - r0 + 1) * step, (c1 - c0 + 1) * step), np.nan, np.float32)
    for col, row in tiles:
        p = os.path.join(w.MAPS_DIR, f"{map_id:03d}{row:02d}{col:02d}.map")
        if not os.path.exists(p):
            continue
        g = GridMap(p)
        v9 = g.height_grid()[:128, :128]
        z = np.array(Image.fromarray(v9).resize((step, step), Image.BILINEAR))
        h[(row - r0) * step:(row - r0 + 1) * step,
          (col - c0) * step:(col - c0 + 1) * step] = z
    valid = np.isfinite(h)
    if not valid.any():
        return np.zeros(h.shape, np.uint8)
    lo, hi = np.percentile(h[valid], [2, 98])
    gy, gx = np.gradient(np.nan_to_num(h, nan=float(lo)))
    shade = np.clip(0.5 + (gx + gy) * 0.06, 0, 1)
    base = np.clip((np.nan_to_num(h, nan=float(lo)) - lo) / max(hi - lo, 1e-3), 0, 1)
    out = (18 + 42 * base * shade * 2).astype(np.uint8)
    out[~valid] = 8
    return out


def compose(map_id: int, tiles: list[tuple[int, int]], ds: int = 8,
            highlight: np.ndarray | None = None) -> Image.Image:
    """Roads (warm white) over dim hillshade; optional per-pixel zone highlight tint."""
    roads, _ = stitch(map_id, tiles, ds)
    bg = hillshade(map_id, tiles, ds)
    rgb = np.stack([bg, bg, (bg * 1.15).clip(0, 255).astype(np.uint8)], axis=-1)
    if highlight is not None:
        rgb[..., 1] = np.where(highlight, np.clip(rgb[..., 1] + 14, 0, 255), rgb[..., 1])
    r = roads.astype(np.float32) / 255.0
    rgb[..., 0] = np.clip(rgb[..., 0] + r * 235, 0, 255)
    rgb[..., 1] = np.clip(rgb[..., 1] + r * 205, 0, 255)
    rgb[..., 2] = np.clip(rgb[..., 2] + r * 120, 0, 255)
    return Image.fromarray(rgb.astype(np.uint8))


def overview(map_id: int, ds: int = 16, path: str | None = None) -> str:
    import json
    meta = json.load(open(os.path.join(OUT, str(map_id), 'tiles.json')))
    tiles = [(t['col'], t['row']) for t in meta['tiles'] if not t.get('missing')]
    img = compose(map_id, tiles, ds)
    path = path or os.path.join(OUT, 'audit', f"overview_{map_id}.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path)
    return path


if __name__ == '__main__':
    import sys
    for mid in ([int(a) for a in sys.argv[1:]] or list(w.MAPS)):
        print(overview(mid))
