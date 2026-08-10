"""QA render: the vector road graph drawn over the Phase 1 mask/hillshade."""
from __future__ import annotations

import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

import wowdata as w
from render import compose
from zones import OUT


def render(map_id: int, ds: int = 16, path: str | None = None) -> str:
    g = json.load(open(os.path.join(OUT, 'graph', f"{map_id}.json")))
    meta = json.load(open(os.path.join(OUT, str(map_id), 'tiles.json')))
    tiles = [(t['col'], t['row']) for t in meta['tiles'] if not t.get('missing')]
    c0 = min(c for c, _ in tiles)
    r0 = min(r for _, r in tiles)
    img = compose(map_id, tiles, ds).convert('RGB')
    step = 1024 // ds
    dr = ImageDraw.Draw(img)

    def px(x, y):
        row_f = 32 - x / w.TILE
        col_f = 32 - y / w.TILE
        return ((col_f - c0) * step, (row_f - r0) * step)

    for e in g['edges']:
        pts = [px(p[0], p[1]) for p in e['waypoints']]
        col = (255, 90, 90) if e.get('provisional') else (80, 170, 255)
        if len(pts) > 1:
            dr.line(pts, fill=col, width=1)
    for n in g['nodes']:
        x, y = px(n['x'], n['y'])
        r = 2 if n['kind'] == 'plaza' else 1
        c = (255, 210, 60) if n['kind'] == 'plaza' else (150, 255, 150)
        dr.ellipse([x - r, y - r, x + r, y + r], fill=c)

    path = path or os.path.join(OUT, 'audit', f"graph_{map_id}.png")
    img.save(path)
    return path


if __name__ == '__main__':
    for mid in ([int(a) for a in sys.argv[1:]] or list(w.MAPS)):
        print(render(mid))
