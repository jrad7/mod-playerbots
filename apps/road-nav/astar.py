"""Phase 3 exit criterion -- offline A* over the merged graph.

Reimplements TravelNodePath::getCost from origin/TravelSystem-pr closely enough
to answer the question the phase turns on: with road rows loaded alongside the
legacy seed, does the router actually choose the road?

    cost = (runDistance/speed + swimDistance/swimSpeed) * modifier
    modifier = 1 + 0.1*max(0, maxCreature[0]-level-10)
                 + 0.3*max(0, maxCreature[opposing]-level-10)

Runs against the scratch tables `roadtest_travelnode*` so the live playerbots
data is never touched.
"""
from __future__ import annotations

import heapq
import math
import os
import subprocess
import sys
from collections import defaultdict

SCHEMA = os.environ.get('ROAD_SCHEMA', 'roadtest')
ROAD_ID_BASE = 100_000
SPEED = 8.0
SWIM_SPEED = 4.0
LEVEL = 60
ALLIANCE = True

MYSQL = ['mysql', '-h127.0.0.1', '-uacore', '-pacore', '-N', '-B']


def q(db, sql_text):
    out = subprocess.run(MYSQL + [db, '-e', sql_text], capture_output=True, text=True)
    if out.returncode:
        raise RuntimeError(out.stderr)
    return [line.split('\t') for line in out.stdout.strip().split('\n') if line]


class Net:
    def __init__(self, map_id: int, with_roads: bool = True, pruned: set | None = None,
                 walk_only: bool = True):
        self.map_id = map_id
        lo, hi = (0, 10 ** 9) if with_roads else (0, ROAD_ID_BASE - 1)
        rows = q('acore_playerbots',
                 f"SELECT id, name, x, y, z FROM {SCHEMA}_travelnode "
                 f"WHERE map_id={map_id} AND id BETWEEN {lo} AND {hi}")
        self.pos = {}
        self.name = {}
        for i, n, x, y, z in rows:
            self.pos[int(i)] = (float(x), float(y), float(z))
            self.name[int(i)] = n
        links = q('acore_playerbots', f"""
            SELECT l.node_id, l.to_node_id, l.type, l.distance, l.swim_distance,
                   l.extra_cost, l.max_creature_0, l.max_creature_1, l.max_creature_2
              FROM {SCHEMA}_travelnode_link l
              JOIN {SCHEMA}_travelnode a ON a.id = l.node_id
              JOIN {SCHEMA}_travelnode b ON b.id = l.to_node_id
             WHERE a.map_id = {map_id} AND b.map_id = {map_id}
               AND l.node_id BETWEEN {lo} AND {hi} AND l.to_node_id BETWEEN {lo} AND {hi}""")
        self.adj = defaultdict(list)
        self.nlinks = 0
        for a, b, t, d, sd, ec, m0, m1, m2 in links:
            a, b, t = int(a), int(b), int(t)
            if pruned and (a, b) in pruned:
                continue
            # Flight/portal/transport links carry distance 0.1 and put the real
            # time in extra_cost, so with them enabled every long route is a
            # flight and the road question never comes up. Roads only matter for
            # the walking graph, which is what a bot without taxi access uses.
            if walk_only and t != 1:
                continue
            self.adj[a].append((b, int(t), float(d), float(sd), float(ec),
                                int(m0), int(m1), int(m2)))
            self.nlinks += 1

    offroad_multiplier = 1.0
    """Decision 3c probe: multiply non-road walk links by this. 1.0 = stock."""

    def cost(self, link) -> float:
        b, t, d, sd, ec, m0, m1, m2 = link
        opp = m2 if ALLIANCE else m1
        modifier = 1.0
        mob = (m0 - LEVEL) - 10
        fac = (opp - LEVEL) - 10
        if mob > 0:
            modifier += 0.1 * mob
        if fac > 0:
            modifier += 0.3 * fac
        if t != 1:
            return max(ec, 0.1) * modifier
        run = max(d - sd, 0.0)
        c = (run / SPEED + sd / SWIM_SPEED) * modifier
        if self.offroad_multiplier != 1.0 and b < ROAD_ID_BASE:
            c *= self.offroad_multiplier
        return c

    def nearest(self, x: float, y: float) -> int | None:
        best, bid = None, None
        for i, (px, py, _pz) in self.pos.items():
            dd = (px - x) ** 2 + (py - y) ** 2
            if best is None or dd < best:
                best, bid = dd, i
        return bid

    def route(self, start: int, goal: int):
        gx, gy, _ = self.pos[goal]

        def h(n):
            x, y, _ = self.pos[n]
            return math.hypot(x - gx, y - gy) / SPEED

        openq = [(h(start), 0.0, start)]
        came, g = {}, {start: 0.0}
        seen = set()
        while openq:
            _f, gc, n = heapq.heappop(openq)
            if n == goal:
                path = [n]
                while n in came:
                    n = came[n]
                    path.append(n)
                return list(reversed(path)), gc
            if n in seen:
                continue
            seen.add(n)
            for link in self.adj[n]:
                m = link[0]
                if m not in self.pos:
                    continue
                ng = gc + self.cost(link)
                if ng < g.get(m, float('inf')):
                    g[m] = ng
                    came[m] = n
                    heapq.heappush(openq, (ng + h(m), ng, m))
        return None, float('inf')

    def road_share(self, path) -> tuple[float, float]:
        """(yards travelled on road links, total yards)."""
        road = total = 0.0
        for a, b in zip(path, path[1:]):
            for link in self.adj[a]:
                if link[0] == b:
                    total += link[2]
                    if a >= ROAD_ID_BASE and b >= ROAD_ID_BASE:
                        road += link[2]
                    break
        return road, total


def teles(names):
    rows = q('acore_world',
             "SELECT name, map, position_x, position_y FROM game_tele WHERE name IN ('"
             + "','".join(names) + "')")
    return {r[0]: (int(r[1]), float(r[2]), float(r[3])) for r in rows}


ROUTES = {
    0: [('Goldshire', 'SentinelHill'), ('Goldshire', 'Darkshire'),
        ('Goldshire', 'Lakeshire'), ('Darkshire', 'SentinelHill'),
        ('Southshore', 'ChillwindCamp'), ('Southshore', 'RefugePointe'),
        ('Thelsamar', 'MenethilHarbor'), ('Thelsamar', 'Ironforge'),
        ('RefugePointe', 'Hammerfall'), ('ChillwindCamp', 'LightsHopeChapel')],
    1: [('TheCrossroads', 'RazorHill'), ('TheCrossroads', 'Ratchet'),
        ('TheCrossroads', 'CampTaurajo'), ('CampTaurajo', 'TheramoreIsle'),
        ('Astranaar', 'Auberdine'), ('Astranaar', 'SplintertreePost'),
        ('BloodhoofVillage', 'ThunderBluff'), ('Auberdine', 'Darnassus'),
        ('FeathermoonStronghold', 'CampMojache'), ('NijelsPoint', 'ShadowpreyVillage')],
    530: [('Shattrath', 'Telaar'), ('Shattrath', 'FalconWatch'),
          ('Shattrath', 'Garadar'), ('Telaar', 'Garadar'),
          ('FalconWatch', 'Thrallmar'), ('Shattrath', 'Sylvanaar'),
          ('Shattrath', 'AllerianStronghold'), ('Zangarmarsh', 'Shattrath'),
          ('Ghostlands', 'SilvermoonCity'), ('BloodmystIsle', 'TheExodar')],
    571: [('ValianceKeep', 'AmberLedge'), ('ValianceKeep', 'FizzcrankAirstrip'),
          ('AmberLedge', 'WintergardeKeep'), ('WintergardeKeep', 'StarsRest'),
          ('Dragonblight', 'WintergardeKeep'), ('GrizzlyHills', 'Dragonblight'),
          ('ZulDrak', 'GrizzlyHills'), ('Dalaran', 'CrystalsongForest'),
          ('SholazarBasin', 'ValianceKeep'), ('WarsongHold', 'Dragonblight')],
}


def all_long_legacy(map_id: int, min_yd: float = 200.0) -> set:
    """Every legacy walk link over min_yd -- the aggressive variant of decision 3a."""
    rows = q('acore_playerbots', f"""
        SELECT l.node_id, l.to_node_id FROM {SCHEMA}_travelnode_link l
          JOIN {SCHEMA}_travelnode a ON a.id = l.node_id
          JOIN {SCHEMA}_travelnode b ON b.id = l.to_node_id
         WHERE a.map_id = {map_id} AND b.map_id = {map_id}
           AND l.type = 1 AND l.node_id < {ROAD_ID_BASE}
           AND l.to_node_id < {ROAD_ID_BASE} AND l.distance > {min_yd}""")
    return {(int(a), int(b)) for a, b in rows}


def run(map_ids, prune=False, aggressive=False):
    pruned = set()
    if prune:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'out', 'sql', 'road_prune_legacy_links.sql')
        import re
        for m in re.finditer(r'node_id=(\d+) AND to_node_id=(\d+)', open(path).read()):
            pruned.add((int(m.group(1)), int(m.group(2))))

    lines = []
    for mid in map_ids:
        mpruned = set(pruned)
        if aggressive:
            mpruned |= all_long_legacy(mid)
        names = sorted({n for pair in ROUTES[mid] for n in pair})
        tl = teles(names)
        net = Net(mid, with_roads=True, pruned=mpruned)
        base = Net(mid, with_roads=False)
        # both nets are walk-only by default
        lines.append(f"### Map {mid}: {len(net.pos)} nodes / {net.nlinks} links "
                     f"(legacy only: {len(base.pos)} / {base.nlinks})")
        lines.append('')
        lines.append('| Route | road share | walked via road graph | walked, legacy only | detour |')
        lines.append('|---|---:|---:|---:|---:|')
        ok = 0
        for a, b in ROUTES[mid]:
            if a not in tl or b not in tl:
                lines.append(f"| {a} → {b} | _no game_tele entry_ | | | |")
                continue
            _m1, ax, ay = tl[a]
            _m2, bx, by = tl[b]
            s, g_ = net.nearest(ax, ay), net.nearest(bx, by)
            p, _c = net.route(s, g_)
            bs, bg = base.nearest(ax, ay), base.nearest(bx, by)
            bp, _bc = base.route(bs, bg)
            if not p:
                lines.append(f"| {a} → {b} | **unroutable on foot** | | | |")
                continue
            road, total = net.road_share(p)
            _r2, btotal = base.road_share(bp) if bp else (0, float('nan'))
            share = 100 * road / max(total, 1e-6)
            if share > 20:
                ok += 1
            det = (f"{100*(total-btotal)/btotal:+.0f}%" if bp and btotal > 0 else 'n/a')
            lines.append(f"| {a} → {b} | **{share:.0f}%** | {total:.0f} yd | "
                         f"{btotal:.0f} yd | {det} |")
        lines.append('')
        lines.append(f"{ok}/{len(ROUTES[mid])} routes spend more than 20% of their "
                     f"length on extracted road links.")
        lines.append('')
    return '\n'.join(lines)


def multiplier_sweep(map_ids, mults=(1.0, 1.1, 1.25, 1.5, 2.0, 3.0)):
    """Decision 3c probe: how much off-road penalty does it take to make roads win?"""
    lines = ['| Map | off-road multiplier | mean road share | unroutable | mean detour |',
             '|---|---:|---:|---:|---:|']
    for mid in map_ids:
        tl = teles(sorted({n for pr in ROUTES[mid] for n in pr}))
        base = Net(mid, with_roads=False)
        for m in mults:
            net = Net(mid, with_roads=True)
            net.offroad_multiplier = m
            shares, det, bad = [], [], 0
            for a, b in ROUTES[mid]:
                if a not in tl or b not in tl:
                    continue
                _x, ax, ay = tl[a]
                _y, bx, by = tl[b]
                p, _c = net.route(net.nearest(ax, ay), net.nearest(bx, by))
                bp, _ = base.route(base.nearest(ax, ay), base.nearest(bx, by))
                if not p:
                    bad += 1
                    continue
                r, t = net.road_share(p)
                _r2, bt = base.road_share(bp) if bp else (0, 0)
                shares.append(100 * r / max(t, 1e-6))
                if bt:
                    det.append(100 * (t - bt) / bt)
            lines.append(f"| {mid} | {m:.2f} | {sum(shares)/max(len(shares),1):.0f}% | "
                         f"{bad} | {sum(det)/max(len(det),1):+.0f}% |")
    return '\n'.join(lines)


if __name__ == '__main__':
    ids = [int(a) for a in sys.argv[1:] if a.isdigit()] or [0, 1, 530, 571]
    if '--sweep' in sys.argv:
        print(multiplier_sweep(ids))
    else:
        print(run(ids, prune='--prune' in sys.argv, aggressive='--aggressive' in sys.argv))
