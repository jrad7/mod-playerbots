"""Extract road-texture alpha masks from WoW 3.3.5 client ADTs.

Hardened over the research-phase prototype:
  * 4-bit (2048B), big-alpha 8-bit (4096B) and RLE-compressed MCAL layers
  * MCNK flag 0x8000 "do not fix alpha map" (63x63 vs 64x64 semantics)
  * per-chunk area ids harvested in the same pass (MCNK header 0x34)
  * every MTEX name reported, not just the matching ones, so the coverage audit
    can spot near-misses

Two gotchas that cost real time in research and must not be re-broken:
  * MCNR lies about its size (declares 435, occupies 448) -- never linear-scan
    MCNK sub-chunks, always use the offsets in the 128-byte MCNK header.
  * MPQ patch-chain priority matters; many Azeroth tiles live in patch*.MPQ.
"""
from __future__ import annotations

import struct

import numpy as np

from wowdata import chunks

ROAD_WORDS = ('road', 'cobble', 'path', 'street', 'brick')
"""Verified filter from the 215-texture survey."""

NOT_ROAD: dict[int | None, tuple[str, ...]] = {
    # Icecrown Citadel decorative floor; paints blobs, never a linear route.
    None: ('icec_floorskull_brick',),
    # Reused as Northrend's seabed/shoreline fill -- 13.4M px, the entire
    # coastal ring in overview_571.png, and not a road anywhere on the map.
    571: ('bonewastesroad',),
    # Ahn'Qiraj gate platform and Silithus structures: 814k px in 10 tiles,
    # all blobs (out/audit/t_aqbrick.png).
    1: ('burningsteppsbrick01',),
}
"""Audit-driven denylist, keyed by map id (None = all maps). See coverage-report.md."""

EXTRA_ROAD: dict[int, tuple[str, ...]] = {
    # Eversong Woods paints its roads with a "Dirt" name (out/audit/c_eversong.png
    # shows the full winding network); same for the Desolace / Thousand Needles track.
    530: ('eversongdirt02',),
    1: ('desolacecracks', 'desolacedirtfootprints'),
}
"""Zone-specific road textures the generic filter misses, added on audit evidence."""

MCNK_DO_NOT_FIX_ALPHA = 0x8000
MCLY_USE_ALPHA = 0x100
MCLY_COMPRESSED = 0x200

WDT_BIG_ALPHA = 0x4 | 0x80


def is_road_texture(name: str, map_id: int | None = None) -> bool:
    n = name.lower()
    for key in (None, map_id):
        if key in NOT_ROAD and any(b in n for b in NOT_ROAD[key]):
            return False
    if map_id in EXTRA_ROAD and any(e in n for e in EXTRA_ROAD[map_id]):
        return True
    return any(w in n for w in ROAD_WORDS)


def _decode_rle(buf: bytes) -> np.ndarray:
    """Decompress an MCLY-0x200 alpha layer to 4096 uint8."""
    out = np.zeros(4096, dtype=np.uint8)
    o = i = 0
    n = len(buf)
    while o < 4096 and i < n:
        ctl = buf[i]
        i += 1
        count = ctl & 0x7F
        if count == 0:
            break
        if ctl & 0x80:                       # fill
            if i >= n:
                break
            take = min(count, 4096 - o)
            out[o:o + take] = buf[i]
            i += 1
            o += take
        else:                                # copy
            take = min(count, 4096 - o, n - i)
            out[o:o + take] = np.frombuffer(buf, np.uint8, take, i)
            i += count
            o += take
    return out


def _decode_alpha(mcal: bytes, off: int, layer_flags: int, big_alpha: bool,
                  do_not_fix: bool) -> np.ndarray | None:
    """Return a 64x64 uint8 alpha grid for one layer, or None if unreadable."""
    if layer_flags & MCLY_COMPRESSED:
        a = _decode_rle(mcal[off:]).reshape(64, 64)
    elif big_alpha:
        raw = mcal[off:off + 4096]
        if len(raw) < 4096:
            return None
        a = np.frombuffer(raw, np.uint8, 4096).reshape(64, 64)
    else:
        raw = mcal[off:off + 2048]
        if len(raw) < 2048:
            return None
        b = np.frombuffer(raw, np.uint8, 2048)
        a = np.empty(4096, dtype=np.uint8)
        a[0::2] = (b & 0x0F) * 17
        a[1::2] = (b >> 4) * 17
        a = a.reshape(64, 64)

    if not do_not_fix:
        # client treats the map as 63x63 and duplicates the final row/column
        a = a.copy()
        a[:, 63] = a[:, 62]
        a[63, :] = a[62, :]
    return a


def parse_adt(adt: bytes, big_alpha: bool, road_words=None, map_id: int | None = None):
    """Parse one ADT blob.

    Returns (mask, meta) where mask is a 1024x1024 uint8 array of road paint
    strength and meta carries the texture/area bookkeeping the audit needs.
    """
    mtex: list[str] = []
    mcnks: list[tuple[int, int]] = []
    for tag, pos, size in chunks(adt):
        if tag == b'MTEX':
            mtex = [n.decode('utf-8', 'replace')
                    for n in adt[pos + 8:pos + 8 + size].split(b'\0') if n]
        elif tag == b'MCNK':
            mcnks.append((pos, size))

    if road_words is None:
        road_ids = {i for i, n in enumerate(mtex) if is_road_texture(n, map_id)}
    else:
        road_ids = {i for i, n in enumerate(mtex)
                    if any(w in n.lower() for w in road_words)}

    mask = np.zeros((1024, 1024), dtype=np.uint8)
    areas = np.zeros((16, 16), dtype=np.uint32)
    compressed_seen = False
    painted_chunks = 0

    for cpos, _size in mcnks:
        hdr = adt[cpos + 8:cpos + 8 + 128]
        flags, ix, iy, nlay = struct.unpack_from('<4I', hdr, 0)
        ofsLayer, _ofsRefs, ofsAlpha, sizeAlpha = struct.unpack_from('<4I', hdr, 0x1C)
        areaid = struct.unpack_from('<I', hdr, 0x34)[0]
        areas[iy, ix] = areaid
        if not road_ids or not nlay or not ofsAlpha:
            continue
        # sizeAlpha counts the sub-chunk header too
        mcal = adt[cpos + ofsAlpha + 8: cpos + ofsAlpha + sizeAlpha]
        do_not_fix = bool(flags & MCNK_DO_NOT_FIX_ALPHA)
        hit = False
        for li in range(nlay):
            tid, lfl, off, _eff = struct.unpack_from(
                '<4I', adt, cpos + ofsLayer + 8 + li * 16)
            if tid not in road_ids or not (lfl & MCLY_USE_ALPHA):
                continue
            if lfl & MCLY_COMPRESSED:
                compressed_seen = True
            a = _decode_alpha(mcal, off, lfl, big_alpha, do_not_fix)
            if a is None:
                continue
            by, bx = iy * 64, ix * 64
            np.maximum(mask[by:by + 64, bx:bx + 64], a,
                       out=mask[by:by + 64, bx:bx + 64])
            hit = True
        if hit:
            painted_chunks += 1

    meta = {
        'mtex': mtex,
        'road_tex': [mtex[i] for i in sorted(road_ids)],
        'areas': areas,
        'painted_chunks': painted_chunks,
        'compressed': compressed_seen,
        'big_alpha': big_alpha,
        'road_px': int((mask >= 64).sum()),
    }
    return mask, meta
