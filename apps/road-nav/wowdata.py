"""Client data access: MPQ priority chain, chunk walking, WDT tile enumeration, DBC reader.

Everything here is read-only against the 3.3.5a client install; nothing touches the server.
"""
from __future__ import annotations

import bz2
import os
import struct
import zlib
from typing import Iterator

from mpyq import MPQArchive

MPQ_FILE_IMPLODE = 0x00000100
MPQ_FILE_COMPRESS = 0x00000200
MPQ_FILE_ENCRYPTED = 0x00010000
MPQ_FILE_SINGLE_UNIT = 0x01000000
MPQ_FILE_SECTOR_CRC = 0x04000000
MPQ_FILE_EXISTS = 0x80000000

DATA = os.environ.get("WOW_DATA", "/mnt/h/WOW-LOCAL/wow-3.3.5a/Data")
DBC_DIR = os.environ.get(
    "AC_DBC", "/mnt/h/WOW-LOCAL/azerothcore/env/dist/bin/dbc")
MAPS_DIR = os.environ.get(
    "AC_MAPS", "/mnt/h/WOW-LOCAL/azerothcore/env/dist/bin/maps")

# later archives override earlier ones (verified: many Azeroth tiles come from patch*.MPQ)
CHAIN = ["common.MPQ", "common-2.MPQ", "expansion.MPQ", "lichking.MPQ",
         "patch.MPQ", "patch-2.MPQ", "patch-3.MPQ"]

# map id -> client directory name
MAPS = {0: "Azeroth", 1: "Kalimdor", 530: "Expansion01", 571: "Northrend"}

TILE = 533.33333
"""Yards per ADT tile side."""


class Client:
    """Priority-ordered view over the MPQ chain, limited to World\\Maps to keep the index small."""

    def __init__(self, prefix: bytes = b"world\\maps\\"):
        self.archives = []
        self.index: dict[bytes, MPQArchive] = {}
        for m in CHAIN:
            a = MPQArchive(os.path.join(DATA, m), listfile=True)
            self.archives.append(a)
            for f in (a.files or []):
                lf = f.lower()
                if lf.startswith(prefix):
                    self.index[lf] = a

    def read(self, path: str | bytes) -> bytes | None:
        if isinstance(path, str):
            path = path.encode()
        a = self.index.get(path.lower())
        if not a:
            return None
        return _read_file(a, path)


def _decompress(sector: bytes) -> bytes:
    """Decompress one MPQ sector by its leading compression mask."""
    mask = sector[0]
    data = sector[1:]
    if mask == 0:
        return data
    if mask & 0x02:
        return zlib.decompress(data, 15)
    if mask & 0x10:
        return bz2.decompress(data)
    raise RuntimeError(f"unsupported MPQ compression mask 0x{mask:02x}")


def _read_file(a: MPQArchive, filename: bytes) -> bytes | None:
    """Correct replacement for MPQArchive.read_file.

    mpyq decides a sector is compressed with `remaining > len(sector)`, which
    misfires on a full-length *stored* sector in the middle of a file (three
    Northrend ADTs in patch-2/patch-3 hit this and raise). The real rule
    compares against the sector's own expected uncompressed length.
    """
    he = a.get_hash_table_entry(filename)
    if he is None:
        return None
    be = a.block_table[he.block_table_index]
    if not (be.flags & MPQ_FILE_EXISTS) or be.archived_size == 0:
        return None
    if be.flags & MPQ_FILE_ENCRYPTED:
        raise NotImplementedError(f"{filename!r}: encrypted")
    if be.flags & MPQ_FILE_IMPLODE:
        raise NotImplementedError(f"{filename!r}: PKWARE implode")

    a.file.seek(be.offset + a.header['offset'])
    raw = a.file.read(be.archived_size)

    if be.flags & MPQ_FILE_SINGLE_UNIT:
        if (be.flags & MPQ_FILE_COMPRESS) and be.archived_size < be.size:
            return _decompress(raw)
        return raw[:be.size]

    sector_size = 512 << a.header['sector_size_shift']
    nsec = -(-be.size // sector_size)
    npos = nsec + 1 + (1 if be.flags & MPQ_FILE_SECTOR_CRC else 0)
    positions = struct.unpack_from(f'<{npos}I', raw, 0)
    out = bytearray()
    for i in range(nsec):
        sector = raw[positions[i]:positions[i + 1]]
        expect = min(sector_size, be.size - i * sector_size)
        if (be.flags & MPQ_FILE_COMPRESS) and len(sector) < expect:
            sector = _decompress(sector)
        out += sector
    return bytes(out)


def chunks(data: bytes, start: int = 0, end: int | None = None) -> Iterator[tuple[bytes, int, int]]:
    """Yield (tag, header_pos, size) for each IFF chunk. tag is byte-reversed to reading order."""
    pos = start
    end = len(data) if end is None else end
    while pos + 8 <= end:
        tag = data[pos:pos + 4][::-1]
        size = struct.unpack_from('<I', data, pos + 4)[0]
        yield tag, pos, size
        pos += 8 + size


def read_wdt(client: Client, mapname: str) -> tuple[int, set[tuple[int, int]]]:
    """Return (mphd_flags, {(col, row) present tiles}) from the WDT MAIN chunk.

    ADT files are named Map_{col}_{row}.adt; MAIN is a 64x64 grid indexed [row][col].
    """
    blob = client.read(f"World\\Maps\\{mapname}\\{mapname}.wdt")
    if blob is None:
        raise FileNotFoundError(f"no WDT for {mapname}")
    flags = 0
    tiles: set[tuple[int, int]] = set()
    for tag, pos, size in chunks(blob):
        if tag == b'MPHD':
            flags = struct.unpack_from('<I', blob, pos + 8)[0]
        elif tag == b'MAIN':
            base = pos + 8
            for row in range(64):
                for col in range(64):
                    off = base + (row * 64 + col) * 8
                    f = struct.unpack_from('<I', blob, off)[0]
                    if f & 0x1:
                        tiles.add((col, row))
    return flags, tiles


def tile_to_world(col: int, row: int, px: float, py: float) -> tuple[float, float]:
    """Texel (px east-positive, py south-positive) in tile (col,row) -> (worldX, worldY).

    Validated to 1.7 yd against Goldshire in the research phase.
    """
    return ((32 - (row + py / 1024.0)) * TILE,
            (32 - (col + px / 1024.0)) * TILE)


def world_to_tile(x: float, y: float) -> tuple[int, int, float, float]:
    """Inverse of tile_to_world -> (col, row, px, py)."""
    fr = 32 - x / TILE
    fc = 32 - y / TILE
    row = int(fr)
    col = int(fc)
    return col, row, (fc - col) * 1024.0, (fr - row) * 1024.0


# --------------------------------------------------------------------------- DBC

class DBC:
    """Minimal WDBC reader (3.3.5 client format, as shipped in the server's dbc dir)."""

    def __init__(self, path: str):
        with open(path, 'rb') as fh:
            blob = fh.read()
        magic, self.nrec, self.nfield, self.recsize, self.strsize = struct.unpack_from(
            '<4s4I', blob, 0)
        assert magic == b'WDBC', f"{path}: not a DBC"
        self.data = blob[20:20 + self.nrec * self.recsize]
        self.strings = blob[20 + self.nrec * self.recsize:]

    def rows(self) -> Iterator[tuple]:
        for i in range(self.nrec):
            yield struct.unpack_from(f'<{self.nfield}I', self.data, i * self.recsize)

    def string(self, offset: int) -> str:
        end = self.strings.find(b'\0', offset)
        return self.strings[offset:end].decode('utf-8', 'replace')

    def floats(self, rec: tuple, idx: int) -> float:
        return struct.unpack('<f', struct.pack('<I', rec[idx]))[0]


def area_table() -> dict[int, dict]:
    """AreaTable.dbc -> {area_id: {map, parent, name}} (3.3.5 layout)."""
    dbc = DBC(os.path.join(DBC_DIR, 'AreaTable.dbc'))
    out = {}
    for r in dbc.rows():
        out[r[0]] = {
            'map': r[1],
            'parent': r[2],
            'name': dbc.string(r[11]),   # enUS locale slot
        }
    return out


def taxi_nodes() -> list[dict]:
    """TaxiNodes.dbc -> [{id, map, x, y, z, name}] (3.3.5 layout)."""
    dbc = DBC(os.path.join(DBC_DIR, 'TaxiNodes.dbc'))
    out = []
    for r in dbc.rows():
        out.append({
            'id': r[0],
            'map': r[1],
            'x': dbc.floats(r, 2),
            'y': dbc.floats(r, 3),
            'z': dbc.floats(r, 4),
            'name': dbc.string(r[5]),
        })
    return out
