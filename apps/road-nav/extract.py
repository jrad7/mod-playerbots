"""Phase 1.3 -- full-world road-mask extraction driver.

Writes one compressed .npz per present ADT tile under out/<map_id>/ plus a
per-map tiles.json with the metadata the coverage audit consumes.

    python3 extract.py            # all four maps
    python3 extract.py 0 530      # selected maps
"""
from __future__ import annotations

import json
import os
import sys
import time
from multiprocessing import Pool

import numpy as np

import adtroads as ar
import wowdata as w

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out')

_client: w.Client | None = None


def _init():
    global _client
    _client = w.Client()


def _tile(job):
    mid, mapname, col, row, big = job
    blob = _client.read(f"World\\Maps\\{mapname}\\{mapname}_{col}_{row}.adt")
    if blob is None:
        return {'col': col, 'row': row, 'missing': True}
    mask, meta = ar.parse_adt(blob, big, map_id=mid)
    d = os.path.join(OUT, str(mid))
    if meta['road_px']:
        np.savez_compressed(os.path.join(d, f"{col}_{row}.npz"),
                            mask=mask, areas=meta['areas'])
    else:
        # still cache area ids -- zone attribution needs them for empty tiles too
        np.savez_compressed(os.path.join(d, f"{col}_{row}.npz"),
                            areas=meta['areas'])
    return {
        'col': col, 'row': row,
        'road_px': meta['road_px'],
        'painted_chunks': meta['painted_chunks'],
        'compressed': meta['compressed'],
        'road_tex': meta['road_tex'],
        'mtex': meta['mtex'],
        'areas': sorted({int(a) for a in meta['areas'].ravel() if a}),
    }


def run(map_ids: list[int], workers: int = 12):
    client = w.Client()
    for mid in map_ids:
        mapname = w.MAPS[mid]
        flags, tiles = w.read_wdt(client, mapname)
        big = bool(flags & ar.WDT_BIG_ALPHA)
        d = os.path.join(OUT, str(mid))
        os.makedirs(d, exist_ok=True)
        jobs = [(mid, mapname, c, r, big) for c, r in sorted(tiles)]
        t0 = time.time()
        results = []
        with Pool(workers, initializer=_init) as pool:
            for i, res in enumerate(pool.imap_unordered(_tile, jobs, chunksize=4)):
                results.append(res)
                if (i + 1) % 200 == 0:
                    print(f"  {mapname}: {i+1}/{len(jobs)} "
                          f"({time.time()-t0:.0f}s)", flush=True)
        painted = [r for r in results if r.get('road_px')]
        print(f"{mid} {mapname}: {len(jobs)} tiles, {len(painted)} with road paint, "
              f"mphd=0x{flags:x} bigAlpha={big}, {time.time()-t0:.0f}s")
        with open(os.path.join(d, 'tiles.json'), 'w') as fh:
            json.dump({'map_id': mid, 'name': mapname, 'mphd_flags': flags,
                       'big_alpha': big, 'tiles': results}, fh)


if __name__ == '__main__':
    ids = [int(a) for a in sys.argv[1:]] or list(w.MAPS)
    run(ids)
