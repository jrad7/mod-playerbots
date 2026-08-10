"""Phase 1.4 near-miss analysis.

For a zone, report every terrain texture by how many pixels it paints *inside
that zone's chunks*, so a zone whose roads use an unnamed texture can be spotted
by looking for a mid-volume, thin, linear layer.
"""
from __future__ import annotations

import collections
import json
import struct
import sys

import numpy as np

import adtroads as ar
import wowdata as w
from zones import load_tile, root_zone


def chunk_texture_pixels(adt: bytes, big_alpha: bool):
    """{(chunk_iy, chunk_ix): {texname: painted_px}} for every layer with an alpha map."""
    mtex: list[str] = []
    mcnks = []
    for tag, pos, size in w.chunks(adt):
        if tag == b'MTEX':
            mtex = [n.decode('utf-8', 'replace')
                    for n in adt[pos + 8:pos + 8 + size].split(b'\0') if n]
        elif tag == b'MCNK':
            mcnks.append(pos)
    out: dict[tuple[int, int], dict[str, int]] = {}
    for cpos in mcnks:
        hdr = adt[cpos + 8:cpos + 8 + 128]
        flags, ix, iy, nlay = struct.unpack_from('<4I', hdr, 0)
        ofsLayer, _r, ofsAlpha, sizeAlpha = struct.unpack_from('<4I', hdr, 0x1C)
        if not nlay or not ofsAlpha:
            continue
        mcal = adt[cpos + ofsAlpha + 8: cpos + ofsAlpha + sizeAlpha]
        dnf = bool(flags & ar.MCNK_DO_NOT_FIX_ALPHA)
        d: dict[str, int] = {}
        for li in range(nlay):
            tid, lfl, off, _e = struct.unpack_from('<4I', adt, cpos + ofsLayer + 8 + li * 16)
            if not (lfl & ar.MCLY_USE_ALPHA) or tid >= len(mtex):
                continue
            a = ar._decode_alpha(mcal, off, lfl, big_alpha, dnf)
            if a is None:
                continue
            px = int((a >= 64).sum())
            if px:
                d[mtex[tid].split('\\')[-1]] = d.get(mtex[tid].split('\\')[-1], 0) + px
        if d:
            out[(iy, ix)] = d
    return out


def zone_textures(map_id: int, zone_name: str, top: int = 25):
    at = w.area_table()
    client = w.Client()
    name = w.MAPS[map_id]
    big = map_id == 571
    meta = json.load(open(f"out/{map_id}/tiles.json"))
    zids = {z for z, v in at.items() if v['name'] == zone_name}
    agg = collections.Counter()
    chunks_in_zone = 0
    for t in meta['tiles']:
        col, row = t['col'], t['row']
        _m, areas = load_tile(map_id, col, row)
        if areas is None:
            continue
        want = {(iy, ix) for iy in range(16) for ix in range(16)
                if root_zone(at, int(areas[iy, ix])) in zids
                or int(areas[iy, ix]) in zids}
        if not want:
            continue
        chunks_in_zone += len(want)
        blob = client.read(f"World\\Maps\\{name}\\{name}_{col}_{row}.adt")
        if blob is None:
            continue
        for key, d in chunk_texture_pixels(blob, big).items():
            if key in want:
                agg.update(d)
    print(f"== map {map_id} / {zone_name}: {chunks_in_zone} chunks")
    for tex, px in agg.most_common(top):
        flag = 'ROAD' if ar.is_road_texture(tex) else '    '
        print(f"   {flag} {px:9d}  {tex}")
    return agg


if __name__ == '__main__':
    zone_textures(int(sys.argv[1]), sys.argv[2])
