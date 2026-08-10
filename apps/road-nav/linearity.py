"""Shape test for "is this texture a road?".

A road is a thin ribbon: its skeleton is long relative to its painted area.
A terrain base layer, a plaza or a seabed is a blob: short skeleton, huge area.
Score = skeleton_px / painted_px; a 10 yd (~20 texel) wide road scores ~0.05,
terrain fill scores an order of magnitude lower.
"""
from __future__ import annotations

import json
import sys

import numpy as np
from skimage.morphology import binary_closing, disk, remove_small_objects, skeletonize

import wowdata as w
from zones import load_tile, root_zone


def texture_mask(map_id: int, tex_word: str, tiles: list[tuple[int, int]],
                 client: w.Client, ds: int = 2) -> np.ndarray:
    import adtroads as ar
    name = w.MAPS[map_id]
    big = map_id == 571
    cols = [c for c, _ in tiles]
    rows = [r for _, r in tiles]
    c0, c1, r0, r1 = min(cols), max(cols), min(rows), max(rows)
    step = 1024 // ds
    img = np.zeros(((r1 - r0 + 1) * step, (c1 - c0 + 1) * step), np.uint8)
    for col, row in tiles:
        blob = client.read(f"World\\Maps\\{name}\\{name}_{col}_{row}.adt")
        if blob is None:
            continue
        m, meta = ar.parse_adt(blob, big, road_words=[tex_word])
        if not meta['road_px']:
            continue
        img[(row - r0) * step:(row - r0 + 1) * step,
            (col - c0) * step:(col - c0 + 1) * step] = m.reshape(
                step, ds, step, ds).max(axis=(1, 3))
    return img


def score(mask: np.ndarray, thresh: int = 64) -> dict:
    b = mask >= thresh
    if not b.any():
        return {'px': 0, 'skel': 0, 'ratio': 0.0}
    b = binary_closing(b, disk(2))
    b = remove_small_objects(b, 64)
    sk = skeletonize(b)
    px = int(b.sum())
    sl = int(sk.sum())
    return {'px': px, 'skel': sl, 'ratio': sl / px if px else 0.0}


def rank(map_id: int, zone_name: str, candidates: list[str], ds: int = 2):
    at = w.area_table()
    client = w.Client()
    meta = json.load(open(f"out/{map_id}/tiles.json"))
    zids = {z for z, v in at.items() if v['name'] == zone_name}
    tiles = []
    for t in meta['tiles']:
        _m, areas = load_tile(map_id, t['col'], t['row'])
        if areas is None:
            continue
        if any(root_zone(at, int(a)) in zids or int(a) in zids for a in areas.ravel()):
            tiles.append((t['col'], t['row']))
    print(f"== map {map_id} / {zone_name} over {len(tiles)} tiles")
    out = []
    for cand in candidates:
        m = texture_mask(map_id, cand.lower(), tiles, client, ds)
        s = score(m)
        out.append((cand, s, m))
        print(f"   {cand:34s} px={s['px']:9d} skel={s['skel']:7d} ratio={s['ratio']:.4f}")
    return out, tiles


if __name__ == '__main__':
    rank(int(sys.argv[1]), sys.argv[2], sys.argv[3].split(','))
