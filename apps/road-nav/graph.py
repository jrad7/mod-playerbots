"""Phase 2 -- raster road masks to a clean, Z-aware vector road graph.

Stages: 2.1 skeletonize, 2.2 cleanup (spur prune / plaza collapse / fragment
drop), 2.3 vectorize (Douglas-Peucker + uniform resample), 2.4 gap joining,
2.5 Z-snap and water sanity.

Output: out/graph/<map>.json  {nodes[], edges[]} in world coordinates.

Resolution note: the plan expected windowed processing, but the bounding box of
*painted* tiles is far smaller than the continent (max 392 Mpx at 1.04 yd/px on
map 530), so each map is processed as one array.
"""
from __future__ import annotations

import json
import math
import os
import sys
from collections import defaultdict, deque

import numpy as np
from scipy import ndimage
from skimage.morphology import (binary_closing, disk, remove_small_holes,
                                remove_small_objects, skeletonize)

import wowdata as w
from gridmap import INVALID_HEIGHT, GridSet
from zones import OUT, THRESH, load_tile

DS = 2
"""Downsample factor from the 1024-texel tile mask; 2 -> 1.0417 yd per pixel."""
YPP = w.TILE / (1024 / DS)
"""Yards per pixel."""

CLOSE_RADIUS = 2          # bridges the dappled paint used in Northrend / Desolace
MIN_BLOB_PX = 300         # ~325 yd^2 of paint; below this it is speckle
MIN_HOLE_PX = 250
SPUR_YD = 15.0            # 2.2 spur pruning threshold
PLAZA_HALFWIDTH_YD = 9.0  # paint half-width above which a skeleton pixel is "plaza"
NODE_MERGE_YD = 12.0      # collapse radius for junction hairballs
MIN_COMPONENT_YD = 30.0   # drop isolated fragments shorter than this
SIMPLIFY_YD = 1.5         # Douglas-Peucker tolerance
RESAMPLE_YD = 15.0        # final waypoint spacing (plan: 10-20 yd)
GAP_YD = 90.0            # 2.4 component-stitch radius (swept: 40->22%, 90->36%, 130->36% of network in the largest component)
GAP_YD_CLEAR = 200.0     # ceiling for a connector over plainly walkable ground
Z_JUMP_YD = 10.0          # 2.5 per-waypoint Z discontinuity limit
WADE_DEPTH_YD = 1.6       # liquid deeper than this is not walkable


# --------------------------------------------------------------------- 2.1

def build_mask(map_id: int) -> tuple[np.ndarray, int, int]:
    """Stitched boolean road mask over the bounding box of painted tiles."""
    meta = json.load(open(os.path.join(OUT, str(map_id), 'tiles.json')))
    painted = [(t['col'], t['row']) for t in meta['tiles'] if t.get('road_px')]
    cols = [c for c, _ in painted]
    rows = [r for _, r in painted]
    c0, r0 = min(cols), min(rows)
    step = 1024 // DS
    h = (max(rows) - r0 + 1) * step
    wd = (max(cols) - c0 + 1) * step
    img = np.zeros((h, wd), bool)
    for col, row in painted:
        mask, _ = load_tile(map_id, col, row)
        if mask is None:
            continue
        sub = mask.reshape(step, DS, step, DS).max(axis=(1, 3)) >= THRESH
        img[(row - r0) * step:(row - r0 + 1) * step,
            (col - c0) * step:(col - c0 + 1) * step] = sub
    return img, c0, r0


def clean_mask(mask: np.ndarray) -> np.ndarray:
    mask = binary_closing(mask, disk(CLOSE_RADIUS))
    mask = remove_small_holes(mask, MIN_HOLE_PX)
    mask = remove_small_objects(mask, MIN_BLOB_PX)
    return mask


# --------------------------------------------------------------------- 2.2

_OFF = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def _degree(skel: np.ndarray) -> np.ndarray:
    k = np.ones((3, 3), np.uint8)
    k[1, 1] = 0
    return ndimage.convolve(skel.astype(np.uint8), k, mode='constant')


def trace_pixel_graph(skel: np.ndarray):
    """Skeleton -> (node_pixels, edges) with edges as pixel polylines.

    Junction/endpoint pixels are grouped into clusters (8-connected) so a fat
    junction becomes one graph node, not five.
    """
    deg = _degree(skel)
    nodepx = skel & (deg != 2)
    lab, n = ndimage.label(nodepx, structure=np.ones((3, 3)))
    # a pure loop has no node at all: seed one
    if n == 0 and skel.any():
        ys, xs = np.nonzero(skel)
        nodepx[ys[0], xs[0]] = True
        lab, n = ndimage.label(nodepx, structure=np.ones((3, 3)))

    centres = ndimage.center_of_mass(nodepx, lab, range(1, n + 1))
    nodes = [(float(cy), float(cx)) for cy, cx in centres]

    H, W = skel.shape
    edges: list[tuple[int, int, list[tuple[int, int]]]] = []
    seen: set[tuple[int, int, int, int]] = set()
    ys, xs = np.nonzero(nodepx)
    for y0, x0 in zip(ys.tolist(), xs.tolist()):
        a = lab[y0, x0] - 1
        for dy, dx in _OFF:
            y, x = y0 + dy, x0 + dx
            if not (0 <= y < H and 0 <= x < W) or not skel[y, x] or nodepx[y, x]:
                continue
            key = (y0, x0, y, x)
            if key in seen:
                continue
            path = [(y0, x0), (y, x)]
            py, px = y0, x0
            cy, cx = y, x
            while True:
                nxt = None
                for ddy, ddx in _OFF:
                    ny, nx = cy + ddy, cx + ddx
                    if not (0 <= ny < H and 0 <= nx < W) or not skel[ny, nx]:
                        continue
                    if (ny, nx) == (py, px):
                        continue
                    nxt = (ny, nx)
                    if nodepx[ny, nx]:
                        break
                if nxt is None:
                    break
                path.append(nxt)
                if nodepx[nxt]:
                    break
                py, px = cy, cx
                cy, cx = nxt
            end = path[-1]
            if not nodepx[end]:
                continue
            b = lab[end] - 1
            seen.add((end[0], end[1], path[-2][0], path[-2][1]))
            seen.add(key)
            edges.append((a, b, path))
    return nodes, edges


class Graph:
    """Node/edge graph in *pixel* space; converted to world coords at emit time."""

    def __init__(self, nodes, edges):
        self.nodes = [list(n) for n in nodes]          # [y, x]
        self.edges = [{'a': a, 'b': b, 'pts': pts} for a, b, pts in edges]
        self.alive = [True] * len(self.nodes)
        self.kind = ['junction'] * len(self.nodes)

    # -- helpers ---------------------------------------------------------
    def length(self, e) -> float:
        pts = e['pts']
        return sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
                   for i in range(len(pts) - 1)) * YPP

    def incident(self) -> dict[int, list[int]]:
        inc = defaultdict(list)
        for i, e in enumerate(self.edges):
            if e is None:
                continue
            inc[e['a']].append(i)
            if e['b'] != e['a']:
                inc[e['b']].append(i)
        return inc

    def compact(self):
        self.edges = [e for e in self.edges if e is not None]
        used = {e['a'] for e in self.edges} | {e['b'] for e in self.edges}
        remap = {}
        nodes, kind = [], []
        for i, nd in enumerate(self.nodes):
            if i in used:
                remap[i] = len(nodes)
                nodes.append(nd)
                kind.append(self.kind[i])
        for e in self.edges:
            e['a'] = remap[e['a']]
            e['b'] = remap[e['b']]
        self.nodes, self.kind = nodes, kind

    # -- 2.2 stages ------------------------------------------------------
    def prune_spurs(self, max_yd: float = SPUR_YD, rounds: int = 12):
        for _ in range(rounds):
            inc = self.incident()
            dropped = 0
            for i, e in enumerate(self.edges):
                if e is None:
                    continue
                if self.length(e) >= max_yd:
                    continue
                for end, other in ((e['a'], e['b']), (e['b'], e['a'])):
                    if len(inc[end]) == 1 and len(inc[other]) > 1:
                        self.edges[i] = None
                        dropped += 1
                        break
            if not dropped:
                break

    def collapse_plazas(self, dt: np.ndarray):
        """Merge junction hairballs and wide-paint areas into single nodes."""
        keep = list(range(len(self.nodes)))
        # union-find over nodes that are close together, or both inside wide paint
        parent = list(range(len(self.nodes)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i, j):
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[rj] = ri

        pts = np.array(self.nodes) if self.nodes else np.zeros((0, 2))
        if len(pts):
            from scipy.spatial import cKDTree
            tree = cKDTree(pts)
            rad = NODE_MERGE_YD / YPP
            for i, j in tree.query_pairs(rad):
                union(i, j)
            # wide-paint (plaza) nodes also absorb their neighbours
            wide = PLAZA_HALFWIDTH_YD / YPP
            for i, (y, x) in enumerate(pts):
                yi, xi = int(round(y)), int(round(x))
                if 0 <= yi < dt.shape[0] and 0 <= xi < dt.shape[1] and dt[yi, xi] >= wide:
                    self.kind[i] = 'plaza'

        groups = defaultdict(list)
        for i in keep:
            groups[find(i)].append(i)
        remap = {}
        newnodes, newkind = [], []
        for root, members in groups.items():
            ys = sum(self.nodes[m][0] for m in members) / len(members)
            xs = sum(self.nodes[m][1] for m in members) / len(members)
            idx = len(newnodes)
            newnodes.append([ys, xs])
            newkind.append('plaza' if any(self.kind[m] == 'plaza' for m in members)
                           or len(members) > 3 else 'junction')
            for m in members:
                remap[m] = idx
        for i, e in enumerate(self.edges):
            if e is None:
                continue
            e['a'], e['b'] = remap[e['a']], remap[e['b']]
            if e['a'] == e['b'] and self.length(e) < NODE_MERGE_YD * 2:
                self.edges[i] = None
        self.nodes, self.kind = newnodes, newkind

    def merge_degree2(self):
        """Splice out degree-2 nodes so an edge runs junction to junction."""
        changed = True
        while changed:
            changed = False
            inc = self.incident()
            for n, eids in inc.items():
                if len(eids) != 2:
                    continue
                i, j = eids
                ei, ej = self.edges[i], self.edges[j]
                if ei is None or ej is None or i == j:
                    continue
                if self.kind[n] == 'plaza':
                    continue
                pi = ei['pts'] if ei['b'] == n else ei['pts'][::-1]
                other_i = ei['a'] if ei['b'] == n else ei['b']
                pj = ej['pts'] if ej['a'] == n else ej['pts'][::-1]
                other_j = ej['b'] if ej['a'] == n else ej['a']
                if other_i == n or other_j == n:
                    continue
                self.edges[i] = {'a': other_i, 'b': other_j, 'pts': pi + pj[1:]}
                self.edges[j] = None
                changed = True

    def drop_small_components(self, min_yd: float = MIN_COMPONENT_YD):
        inc = self.incident()
        seen = set()
        for start in range(len(self.nodes)):
            if start in seen:
                continue
            comp, q = set(), deque([start])
            total = 0.0
            eids = set()
            while q:
                n = q.popleft()
                if n in comp:
                    continue
                comp.add(n)
                for i in inc[n]:
                    if self.edges[i] is None or i in eids:
                        continue
                    eids.add(i)
                    total += self.length(self.edges[i])
                    q.append(self.edges[i]['a'])
                    q.append(self.edges[i]['b'])
            seen |= comp
            if total < min_yd:
                for i in eids:
                    self.edges[i] = None


# --------------------------------------------------------------------- 2.3

def rdp(pts: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Iterative Douglas-Peucker (recursion would blow the stack on long roads)."""
    if len(pts) < 3:
        return list(pts)
    keep = np.zeros(len(pts), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        ay, ax = pts[i]
        by, bx = pts[j]
        dy, dx = by - ay, bx - ax
        norm = math.hypot(dy, dx)
        best, bi = -1.0, -1
        for k in range(i + 1, j):
            py, px = pts[k]
            if norm < 1e-9:
                d = math.hypot(py - ay, px - ax)
            else:
                d = abs(dx * (ay - py) - (ax - px) * dy) / norm
            if d > best:
                best, bi = d, k
        if best > tol:
            keep[bi] = True
            stack.append((i, bi))
            stack.append((bi, j))
    return [pts[k] for k in range(len(pts)) if keep[k]]


def resample(pts: list[tuple[float, float]], spacing: float) -> list[tuple[float, float]]:
    """Uniform arc-length resample; always keeps both endpoints."""
    if len(pts) < 2:
        return list(pts)
    out = [pts[0]]
    carry = 0.0
    for i in range(len(pts) - 1):
        y0, x0 = pts[i]
        y1, x1 = pts[i + 1]
        seg = math.hypot(y1 - y0, x1 - x0)
        if seg <= 1e-9:
            continue
        t = spacing - carry
        while t < seg:
            f = t / seg
            out.append((y0 + (y1 - y0) * f, x0 + (x1 - x0) * f))
            t += spacing
        carry = (carry + seg) % spacing
    if out[-1] != pts[-1]:
        out.append(pts[-1])
    return out


# --------------------------------------------------------------------- world

def to_world(y: float, x: float, c0: int, r0: int) -> tuple[float, float]:
    wx = (32 - (r0 + (y + 0.5) * DS / 1024.0)) * w.TILE
    wy = (32 - (c0 + (x + 0.5) * DS / 1024.0)) * w.TILE
    return wx, wy


# --------------------------------------------------------------------- 2.5

def snap_z(grid: GridSet, pts: list[list[float]]) -> tuple[list[list[float]], int, int]:
    """Attach Z from the server height grid; repair or drop discontinuities."""
    out = []
    repaired = dropped = 0
    for x, y in pts:
        out.append([x, y, float(grid.height(x, y))])
    # smooth single-sample spikes, drop persistent ones
    kept: list[list[float]] = []
    for i, p in enumerate(out):
        if p[2] <= INVALID_HEIGHT + 1:
            dropped += 1
            continue
        if kept and abs(p[2] - kept[-1][2]) > Z_JUMP_YD:
            nxt = out[i + 1] if i + 1 < len(out) else None
            if nxt and abs(nxt[2] - kept[-1][2]) <= Z_JUMP_YD * 1.5:
                repaired += 1
                continue
        kept.append(p)
    return kept, repaired, dropped


def water_flags(grid: GridSet, pts: list[list[float]]) -> int:
    """Count waypoints sitting under liquid deeper than wading depth."""
    n = 0
    for x, y, z in pts:
        lv = grid.liquid(x, y)
        if lv > INVALID_HEIGHT + 1 and lv - z > WADE_DEPTH_YD:
            n += 1
    return n


# --------------------------------------------------------------------- 2.4

def _corridor(grid: GridSet, ax, ay, bx, by, d):
    """Sample a straight connector; classify it rather than veto it.

    A bridge is a WMO: the terrain under it is the chasm or river it spans, so
    a height-continuity veto rejects precisely the crossings the graph needs
    (Thandol Span was the first casualty). Instead every connector is emitted
    `provisional` with a reason, and the polyline for a spanning connector
    interpolates Z between the two banks instead of following the chasm floor.
    Phase 4's PathGenerator pass is what actually adjudicates these.

    Returns (waypoints, reason) or None when the corridor leaves the map.
    """
    steps = max(2, int(d / 5))
    zs, xy = [], []
    reason = 'clear'
    for s in range(steps + 1):
        f = s / steps
        x, y = ax + (bx - ax) * f, ay + (by - ay) * f
        z = float(grid.height(x, y))
        if z <= INVALID_HEIGHT + 1:
            return None
        lv = grid.liquid(x, y)
        if lv > INVALID_HEIGHT + 1 and lv - z > WADE_DEPTH_YD:
            reason = 'water'
        xy.append((x, y))
        zs.append(z)
    for i in range(1, len(zs)):
        if abs(zs[i] - zs[i - 1]) > Z_JUMP_YD:
            reason = 'span' if reason == 'clear' else reason
            break
    if reason != 'clear':
        z0, z1 = zs[0], zs[-1]
        zs = [z0 + (z1 - z0) * (i / (len(zs) - 1)) for i in range(len(zs))]
    return [[x, y, z] for (x, y), z in zip(xy, zs)], reason


class _DSU:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, i):
        while self.p[i] != i:
            self.p[i] = self.p[self.p[i]]
            i = self.p[i]
        return i

    def union(self, i, j):
        ri, rj = self.find(i), self.find(j)
        if ri == rj:
            return False
        self.p[rj] = ri
        return True

    def grow(self, k=1):
        for _ in range(k):
            self.p.append(len(self.p))
        return len(self.p) - 1


def _split_at(nodes, edges, dsu, ei, wi):
    """Return the node id at waypoint wi of edge ei, splitting the edge if needed."""
    e = edges[ei]
    if wi <= 0:
        return e['a'], False
    if wi >= len(e['waypoints']) - 1:
        return e['b'], False
    p = e['waypoints'][wi]
    b = len(nodes)
    nodes.append({'id': b, 'x': p[0], 'y': p[1], 'z': p[2], 'kind': 'connector'})
    dsu.grow()
    head = e['waypoints'][:wi + 1]
    tail = e['waypoints'][wi:]
    edges[ei] = {'a': e['a'], 'b': b, 'length': _plen(head),
                 'provisional': e.get('provisional', False), 'waypoints': head}
    edges.append({'a': b, 'b': e['b'], 'length': _plen(tail),
                  'provisional': e.get('provisional', False), 'waypoints': tail})
    dsu.union(e['a'], b)
    dsu.union(b, e['b'])
    return b, True


def join_gaps(nodes: list[dict], edges: list[dict], map_id: int, grid: GridSet,
              radius: float = GAP_YD, rounds: int = 6):
    """Close bridge / ford / zone-border gaps by stitching components together.

    Measured shape of the problem on Eastern Kingdoms: the trunk splits into
    ~12 large pieces whose *closest approach* is 48-74 yd, and at that point
    neither side has a dead end -- one zone's road paint simply stops beside
    where the next zone's starts. So the rule is not "join collinear dead ends"
    but a Kruskal pass: repeatedly take the shortest terrain-walkable link
    between two still-separate components, splitting either polyline where the
    link lands.
    """
    from scipy.spatial import cKDTree
    made = 0
    for _ in range(rounds):
        dsu = _DSU(len(nodes))
        for e in edges:
            dsu.union(e['a'], e['b'])

        pts, tags = [], []
        for ei, e in enumerate(edges):
            for wi, p in enumerate(e['waypoints']):
                pts.append([p[0], p[1]])
                tags.append((ei, wi))
        if len(pts) < 2:
            return made
        arr = np.array(pts)
        tree = cKDTree(arr)

        # Candidate inter-component links, shortest first.
        # A k-nearest query is useless here: every waypoint's k nearest are its
        # own polyline neighbours 15 yd apart, so the cross-component candidate
        # never appears. Take all pairs inside the radius instead.
        comp_of = np.array([dsu.find(edges[ei]['a']) for ei, _ in tags])
        cand = []
        for i, j in tree.query_pairs(GAP_YD_CLEAR):
            if comp_of[i] == comp_of[j]:
                continue
            d = float(math.dist(arr[i], arr[j]))
            if d < 1.0:
                continue
            if d > GAP_YD_CLEAR:
                continue
            cand.append((d, i, j))
        cand.sort()

        dirty: set[int] = set()
        joined = 0
        for d, i, j in cand:
            ei, wi = tags[i]
            ej, wj = tags[j]
            if ei in dirty or ej in dirty or ei == ej:
                continue
            if dsu.find(edges[ei]['a']) == dsu.find(edges[ej]['a']):
                continue
            ax, ay = arr[i]
            bx, by = arr[j]
            res = _corridor(grid, float(ax), float(ay), float(bx), float(by), d)
            if res is None:
                continue
            poly, reason = res
            # Short links may span a bridge or ford (Phase 4 adjudicates those with
            # a real PathGenerator query). Long links are only allowed where the
            # ground between the two roads is plainly walkable -- otherwise a
            # 150 yd "connector" is just an invented cross-country shortcut.
            if d > radius and reason != 'clear':
                continue
            a, sa = _split_at(nodes, edges, dsu, ei, wi)
            if sa:
                dirty.add(ei)
            b, sb = _split_at(nodes, edges, dsu, ej, wj)
            if sb:
                dirty.add(ej)
            edges.append({'a': a, 'b': b, 'length': d, 'provisional': True,
                          'reason': reason, 'waypoints': poly})
            dsu.union(a, b)
            made += 1
            joined += 1
        if not joined:
            break
    return made


def _plen(pts) -> float:
    return sum(math.dist(pts[i][:2], pts[i + 1][:2]) for i in range(len(pts) - 1))


POST_MIN_COMPONENT_YD = 200.0
"""After stitching, a component shorter than this cannot serve any route."""


def prune_components(nodes: list[dict], edges: list[dict],
                     min_yd: float = POST_MIN_COMPONENT_YD):
    """Drop stranded fragments and renumber, so ids stay dense for the SQL emitter."""
    inc = defaultdict(list)
    for i, e in enumerate(edges):
        inc[e['a']].append(i)
        inc[e['b']].append(i)
    seen: set[int] = set()
    dead: set[int] = set()
    for s in range(len(nodes)):
        if s in seen:
            continue
        q = deque([s])
        comp, eids, total = set(), set(), 0.0
        while q:
            n = q.popleft()
            if n in comp:
                continue
            comp.add(n)
            for i in inc[n]:
                if i in eids:
                    continue
                eids.add(i)
                total += edges[i]['length']
                q.append(edges[i]['a'])
                q.append(edges[i]['b'])
        seen |= comp
        if total < min_yd:
            dead |= eids
    edges = [e for i, e in enumerate(edges) if i not in dead]
    used = {e['a'] for e in edges} | {e['b'] for e in edges}
    remap = {}
    kept = []
    for i, nd in enumerate(nodes):
        if i in used:
            remap[i] = len(kept)
            nd['id'] = len(kept)
            kept.append(nd)
    for e in edges:
        e['a'] = remap[e['a']]
        e['b'] = remap[e['b']]
    return kept, edges


# --------------------------------------------------------------------- driver

def build(map_id: int, verbose: bool = True, gap_radius: float = GAP_YD) -> dict:
    log = print if verbose else (lambda *a, **k: None)
    mask, c0, r0 = build_mask(map_id)
    log(f"map {map_id}: mask {mask.shape}, {int(mask.sum())} px painted")
    mask = clean_mask(mask)
    log(f"  cleaned: {int(mask.sum())} px")
    dt = ndimage.distance_transform_edt(mask)
    skel = skeletonize(mask)
    log(f"  skeleton: {int(skel.sum())} px")

    nodes_px, edges_px = trace_pixel_graph(skel)
    log(f"  raw graph: {len(nodes_px)} nodes, {len(edges_px)} edges")
    g = Graph(nodes_px, edges_px)
    g.prune_spurs()
    g.collapse_plazas(dt)
    g.merge_degree2()
    g.prune_spurs()
    g.drop_small_components()
    g.compact()
    log(f"  cleaned graph: {len(g.nodes)} nodes, {len(g.edges)} edges")

    grid = GridSet(map_id)
    nodes = []
    for i, (y, x) in enumerate(g.nodes):
        wx, wy = to_world(y, x, c0, r0)
        nodes.append({'id': i, 'x': wx, 'y': wy, 'z': float(grid.height(wx, wy)),
                      'kind': g.kind[i]})

    edges = []
    zrep = zdrop = 0
    for e in g.edges:
        pts = rdp([(p[0], p[1]) for p in e['pts']], SIMPLIFY_YD / YPP)
        pts = resample(pts, RESAMPLE_YD / YPP)
        world = [list(to_world(y, x, c0, r0)) for y, x in pts]
        wp, rep, drp = snap_z(grid, world)
        zrep += rep
        zdrop += drp
        if len(wp) < 2:
            continue
        # keep the polyline anchored on its nodes
        wp[0] = [nodes[e['a']]['x'], nodes[e['a']]['y'], nodes[e['a']]['z']]
        wp[-1] = [nodes[e['b']]['x'], nodes[e['b']]['y'], nodes[e['b']]['z']]
        ln = sum(math.dist(wp[i][:2], wp[i + 1][:2]) for i in range(len(wp) - 1))
        edges.append({'a': e['a'], 'b': e['b'], 'length': ln,
                      'provisional': False, 'waypoints': wp})
    log(f"  vectorized: {len(edges)} edges, z repaired {zrep}, dropped {zdrop}")

    # 2.5 water sanity: an edge that mostly sits under water is beach/seabed paint,
    # not a road (map 530's EversongDirt02 bleeds onto the Quel'Danas shoreline).
    before = len(edges)
    edges = [e for e in edges
             if water_flags(grid, e['waypoints']) <= 0.5 * len(e['waypoints'])]
    log(f"  water sanity: dropped {before - len(edges)} submerged edges")

    conn = join_gaps(nodes, edges, map_id, grid, radius=gap_radius)
    log(f"  connectors: {conn} provisional")

    n_before, e_before = len(nodes), len(edges)
    nodes, edges = prune_components(nodes, edges)
    log(f"  stranded fragments removed: {n_before - len(nodes)} nodes, "
        f"{e_before - len(edges)} edges")

    wet = sum(water_flags(grid, e['waypoints']) for e in edges if not e['provisional'])
    total_pts = sum(len(e['waypoints']) for e in edges)
    total_len = sum(e['length'] for e in edges)
    log(f"  total {total_len/1000:.1f}k yd of road, {total_pts} waypoints, "
        f"{wet} waypoints under deep water")

    return {'map_id': map_id, 'ds': DS, 'origin': [c0, r0],
            'nodes': nodes, 'edges': edges,
            'stats': {'length_yd': total_len, 'waypoints': total_pts,
                      'connectors': conn, 'deep_water_waypoints': wet,
                      'z_repaired': zrep, 'z_dropped': zdrop}}


def main(ids):
    os.makedirs(os.path.join(OUT, 'graph'), exist_ok=True)
    for mid in ids:
        g = build(mid)
        p = os.path.join(OUT, 'graph', f"{mid}.json")
        with open(p, 'w') as fh:
            json.dump(g, fh)
        print(f"  -> {p} ({os.path.getsize(p)/1e6:.1f} MB)")


if __name__ == '__main__':
    main([int(a) for a in sys.argv[1:]] or list(w.MAPS))
