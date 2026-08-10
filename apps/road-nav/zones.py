"""Zone attribution: per-chunk area ids -> root zones, and per-zone road statistics."""
from __future__ import annotations

import json
import os

import numpy as np

import gridmap
import wowdata as w

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out')
THRESH = 64
"""Alpha >= this counts as painted road (verified value from the research render)."""


def root_zone(areas: dict[int, dict], aid: int) -> int:
    """Walk AreaTable parents up to the top-level zone."""
    seen = set()
    while aid and aid in areas and areas[aid]['parent'] and aid not in seen:
        seen.add(aid)
        aid = areas[aid]['parent']
    return aid


def load_tile(map_id: int, col: int, row: int):
    """Return (road mask or None, 16x16 chunk area ids).

    Area ids come from the server `.map` AREA block, which is authoritative --
    the MCNK header field agrees with it on 99.4% of chunks but carries garbage
    (float bit patterns) on a handful of Northrend tiles.
    """
    p = os.path.join(OUT, str(map_id), f"{col}_{row}.npz")
    if not os.path.exists(p):
        return None, None
    z = np.load(p)
    mask = z['mask'] if 'mask' in z.files else None
    areas = z['areas']
    mp = os.path.join(w.MAPS_DIR, f"{map_id:03d}{row:02d}{col:02d}.map")
    if os.path.exists(mp):
        g = gridmap.GridMap(mp)
        areas = (g.area_map if g.area_map is not None
                 else np.full((16, 16), g.grid_area, np.uint16))
    return mask, areas


def scan(map_id: int, areas: dict[int, dict]) -> dict:
    """Per-root-zone road statistics for one map."""
    meta = json.load(open(os.path.join(OUT, str(map_id), 'tiles.json')))
    zone_stats: dict[int, dict] = {}
    for t in meta['tiles']:
        if t.get('missing'):
            continue
        col, row = t['col'], t['row']
        mask, chunk_areas = load_tile(map_id, col, row)
        if chunk_areas is None:
            continue
        for iy in range(16):
            for ix in range(16):
                aid = int(chunk_areas[iy, ix])
                if not aid:
                    continue
                zid = root_zone(areas, aid)
                st = zone_stats.setdefault(zid, {
                    'zone_id': zid,
                    'name': areas.get(zid, {}).get('name', f'#{zid}'),
                    'chunks': 0, 'chunks_road': 0, 'road_px': 0,
                    'tiles': set(), 'tiles_road': set(),
                    'road_tex': set(), 'mtex': set(),
                })
                st['chunks'] += 1
                st['tiles'].add((col, row))
                st['mtex'].update(t['mtex'])
                if mask is not None:
                    sub = mask[iy * 64:(iy + 1) * 64, ix * 64:(ix + 1) * 64]
                    px = int((sub >= THRESH).sum())
                    if px:
                        st['chunks_road'] += 1
                        st['road_px'] += px
                        st['tiles_road'].add((col, row))
                        st['road_tex'].update(t['road_tex'])
    for st in zone_stats.values():
        st['tiles'] = sorted(st['tiles'])
        st['tiles_road'] = sorted(st['tiles_road'])
        st['road_tex'] = sorted(st['road_tex'])
        st['mtex'] = sorted(st['mtex'])
    return zone_stats


def all_zone_stats() -> dict[int, dict]:
    areas = w.area_table()
    out = {}
    for mid in w.MAPS:
        for zid, st in scan(mid, areas).items():
            st['map_id'] = mid
            out[(mid, zid)] = st
    return out


if __name__ == '__main__':
    stats = all_zone_stats()
    rows = sorted(stats.values(), key=lambda s: (-s['road_px'], s['name']))
    for s in rows:
        print(f"{s['map_id']:4d} {s['name'][:34]:34s} chunks={s['chunks']:6d} "
              f"road_chunks={s['chunks_road']:5d} px={s['road_px']:9d} "
              f"tex={len(s['road_tex'])}")
