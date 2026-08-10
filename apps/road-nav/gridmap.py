"""Reader for AzerothCore server `.map` grids (heights, areas, liquid).

Mirrors `src/server/game/Grids/GridTerrainData.cpp` exactly, including the
x/y index convention: x_int indexes the world-X axis, y_int the world-Y axis,
V9 is 129x129 row-major on x.

Used for Z-snapping road waypoints (Phase 2.5) and water sanity checks.
"""
from __future__ import annotations

import os
import struct

import numpy as np

from wowdata import MAPS_DIR, TILE

MAP_RESOLUTION = 128
INVALID_HEIGHT = -100000.0

MAP_AREA_NO_AREA = 0x0001
MAP_HEIGHT_NO_HEIGHT = 0x0001
MAP_HEIGHT_AS_INT16 = 0x0002
MAP_HEIGHT_AS_INT8 = 0x0004
MAP_HEIGHT_HAS_FLIGHT_BOUNDS = 0x0008
MAP_LIQUID_NO_TYPE = 0x0001
MAP_LIQUID_NO_HEIGHT = 0x0002


class GridMap:
    """One 533x533 yd map tile. Filename convention is `{map:03d}{row:02d}{col:02d}.map`."""

    def __init__(self, path: str):
        with open(path, 'rb') as fh:
            b = fh.read()
        (magic, _ver, _build, aOff, _aSize, hOff, _hSize,
         lOff, _lSize, holeOff, _holeSize) = struct.unpack_from('<4s4s9I', b, 0)
        assert magic == b'MAPS', path

        self.grid_area = 0
        self.area_map: np.ndarray | None = None
        if aOff:
            fourcc, flags, gridArea = struct.unpack_from('<4sHH', b, aOff)
            self.grid_area = gridArea
            if not (flags & MAP_AREA_NO_AREA):
                self.area_map = np.frombuffer(
                    b, np.uint16, 256, aOff + 8).reshape(16, 16)

        self.grid_height = INVALID_HEIGHT
        self.v9: np.ndarray | None = None
        self.v8: np.ndarray | None = None
        if hOff:
            _fourcc, flags, gh, gmax = struct.unpack_from('<4sIff', b, hOff)
            self.grid_height = gh
            p = hOff + 16
            if not (flags & MAP_HEIGHT_NO_HEIGHT):
                if flags & MAP_HEIGHT_AS_INT16:
                    mul = (gmax - gh) / 65535.0
                    v9 = np.frombuffer(b, np.uint16, 129 * 129, p).astype(np.float32)
                    v8 = np.frombuffer(b, np.uint16, 128 * 128, p + 2 * 129 * 129).astype(np.float32)
                    self.v9 = gh + v9 * mul
                    self.v8 = gh + v8 * mul
                elif flags & MAP_HEIGHT_AS_INT8:
                    mul = (gmax - gh) / 255.0
                    v9 = np.frombuffer(b, np.uint8, 129 * 129, p).astype(np.float32)
                    v8 = np.frombuffer(b, np.uint8, 128 * 128, p + 129 * 129).astype(np.float32)
                    self.v9 = gh + v9 * mul
                    self.v8 = gh + v8 * mul
                else:
                    self.v9 = np.frombuffer(b, np.float32, 129 * 129, p).copy()
                    self.v8 = np.frombuffer(b, np.float32, 128 * 128, p + 4 * 129 * 129).copy()
                self.v9 = self.v9.reshape(129, 129)
                self.v8 = self.v8.reshape(128, 128)

        self.liquid_level = INVALID_HEIGHT
        self.liquid_map: np.ndarray | None = None
        self.liquid_flags: np.ndarray | None = None
        self.liquid_global_flags = 0
        self.loff_x = self.loff_y = self.lwidth = self.lheight = 0
        if lOff:
            (_fourcc, flags, lflags, _ltype, offX, offY, wdt, hgt,
             level) = struct.unpack_from('<4sBBHBBBBf', b, lOff)
            self.liquid_global_flags = lflags
            self.loff_x, self.loff_y = offX, offY
            self.lwidth, self.lheight = wdt, hgt
            self.liquid_level = level
            p = lOff + 16
            if not (flags & MAP_LIQUID_NO_TYPE):
                p += 512                       # liquidEntry uint16[256]
                self.liquid_flags = np.frombuffer(b, np.uint8, 256, p).reshape(16, 16)
                p += 256
            if not (flags & MAP_LIQUID_NO_HEIGHT):
                self.liquid_map = np.frombuffer(
                    b, np.float32, wdt * hgt, p).reshape(hgt, wdt)

        self.holes = None
        if holeOff:
            self.holes = np.frombuffer(b, np.uint16, 256, holeOff).reshape(16, 16)

    # ---------------------------------------------------------------- queries

    def height(self, x: float, y: float) -> float:
        if self.v9 is None:
            return self.grid_height
        fx = MAP_RESOLUTION * (32 - x / TILE)
        fy = MAP_RESOLUTION * (32 - y / TILE)
        xi, yi = int(fx), int(fy)
        fx -= xi
        fy -= yi
        xi &= MAP_RESOLUTION - 1
        yi &= MAP_RESOLUTION - 1
        v9, v8 = self.v9, self.v8
        h5 = 2 * v8[xi, yi]
        if fx + fy < 1:
            if fx > fy:                                  # h1 h2 h5
                h1, h2 = v9[xi, yi], v9[xi + 1, yi]
                a, bq, c = h2 - h1, h5 - h1 - h2, h1
            else:                                        # h1 h3 h5
                h1, h3 = v9[xi, yi], v9[xi, yi + 1]
                a, bq, c = h5 - h1 - h3, h3 - h1, h1
        else:
            if fx > fy:                                  # h2 h4 h5
                h2, h4 = v9[xi + 1, yi], v9[xi + 1, yi + 1]
                a, bq, c = h2 + h4 - h5, h4 - h2, h5 - h2
            else:                                        # h3 h4 h5
                h3, h4 = v9[xi, yi + 1], v9[xi + 1, yi + 1]
                a, bq, c = h4 - h3, h3 + h4 - h5, h5 - h3
        return a * fx + bq * fy + c

    def height_grid(self) -> np.ndarray:
        """V9 corner heights as a 129x129 array indexed [x_idx, y_idx]."""
        if self.v9 is None:
            return np.full((129, 129), self.grid_height, np.float32)
        return self.v9

    def liquid_at(self, x: float, y: float) -> float:
        """Liquid surface height at (x, y), or INVALID_HEIGHT where there is none."""
        fx = MAP_RESOLUTION * (32 - x / TILE)
        fy = MAP_RESOLUTION * (32 - y / TILE)
        xi = int(fx) & (MAP_RESOLUTION - 1)
        yi = int(fy) & (MAP_RESOLUTION - 1)
        flags = (self.liquid_flags[xi >> 3, yi >> 3]
                 if self.liquid_flags is not None else self.liquid_global_flags)
        if not flags:
            return INVALID_HEIGHT
        if self.liquid_map is None:
            return self.liquid_level
        lx = xi - self.loff_y
        ly = yi - self.loff_x
        if lx < 0 or lx >= self.lheight or ly < 0 or ly >= self.lwidth:
            return INVALID_HEIGHT
        return float(self.liquid_map[lx, ly])


class GridSet:
    """Lazy cache of GridMap tiles for one map id."""

    def __init__(self, map_id: int, maps_dir: str = MAPS_DIR):
        self.map_id = map_id
        self.dir = maps_dir
        self._cache: dict[tuple[int, int], GridMap | None] = {}

    def tile(self, col: int, row: int) -> GridMap | None:
        key = (col, row)
        if key not in self._cache:
            path = os.path.join(self.dir, f"{self.map_id:03d}{row:02d}{col:02d}.map")
            self._cache[key] = GridMap(path) if os.path.exists(path) else None
        return self._cache[key]

    def height(self, x: float, y: float) -> float:
        row = int(32 - x / TILE)
        col = int(32 - y / TILE)
        g = self.tile(col, row)
        return g.height(x, y) if g else INVALID_HEIGHT

    def liquid(self, x: float, y: float) -> float:
        row = int(32 - x / TILE)
        col = int(32 - y / TILE)
        g = self.tile(col, row)
        return g.liquid_at(x, y) if g else INVALID_HEIGHT
