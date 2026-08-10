"""Phase 2.6 -- validate the extracted road graph.

Three checks from the plan:
  * patrol ground truth -- long creature patrol routes ride the roads; measure
    lateral deviation of our centreline from them
  * known-coordinate spot checks -- hand-picked landmarks must have a waypoint
    within 5 yd
  * connectivity -- every taxi town should sit near the graph, and the largest
    connected component should hold the trunk network
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from collections import defaultdict, deque

import numpy as np
from scipy.spatial import cKDTree

import wowdata as w
from zones import OUT

MYSQL = ['mysql', '-h127.0.0.1', '-uacore', '-pacore', '-N', '-B', 'acore_world', '-e']

# Landmark coordinates come from acore_world.game_tele (the GM .tele table), which
# is authoritative -- an earlier hand-typed list produced nonsense distances.
# These are town-centre teleport spots, so a few yards of offset from the road
# itself is expected; the bar here is "the road reaches the town", not sub-metre.
TELE_LANDMARKS = ['Goldshire', 'SentinelHill', 'Darkshire', 'Lakeshire', 'Thelsamar',
                  'Southshore', 'RefugePointe', 'Ironforge', 'Undercity',
                  'Astranaar', 'Auberdine', 'RazorHill', 'NijelsPoint',
                  'Shattrath', 'Telaar', 'FalconWatch',
                  'ValianceKeep', 'AmberLedge']
LANDMARK_OK_YD = 40.0

_LEGACY_LANDMARKS = [
    (0, 'Goldshire crossroads', -9464.0, 62.0),
    (0, 'Sentinel Hill', -10643.0, 1052.0),
    (0, 'Darkshire', -10510.0, -1258.0),
    (0, 'Lakeshire bridge', -9265.0, -2160.0),
    (0, 'Thelsamar', -5410.0, -3345.0),
    (0, 'Menethil Harbor road', -3735.0, -830.0),
    (0, 'Southshore', -757.0, -520.0),
    (0, 'Refuge Pointe', -1247.0, -2560.0),
    (0, 'Chillwind Camp', 928.0, -1436.0),
    (0, 'Light\'s Hope Chapel', 2276.0, -5335.0),
    (1, 'Crossroads', -443.0, -2647.0),
    (1, 'Razor Hill', 314.0, -4728.0),
    (1, 'Bloodhoof Village', -2360.0, -347.0),
    (1, 'Astranaar', 2755.0, -378.0),
    (1, 'Auberdine', 6376.0, 522.0),
    (1, 'Camp Taurajo', -2358.0, -1961.0),
    (1, 'Nijel\'s Point', 1859.0, 1372.0),
    (1, 'Feathermoon ferry road', -4400.0, 3150.0),
    (530, 'Shattrath south road', -1770.0, 5250.0),
    (530, 'Telaar', -2740.0, 6560.0),
    (530, 'Falcon Watch', -1150.0, 3980.0),
    (530, 'Temple of Telhamat', 550.0, 3900.0),
    (530, 'Silvermoon road', 9300.0, -7300.0),
    (571, 'Valiance Keep road', 2270.0, 5200.0),
    (571, 'Wintergarde Keep', 3700.0, 2400.0),
    (571, 'Amber Ledge', 3700.0, 5400.0),
]


def sql(q: str) -> list[list[str]]:
    out = subprocess.run(MYSQL + [q], capture_output=True, text=True)
    if out.returncode:
        raise RuntimeError(out.stderr)
    return [line.split('\t') for line in out.stdout.strip().split('\n') if line]


def load_graph(map_id: int) -> dict:
    return json.load(open(os.path.join(OUT, 'graph', f"{map_id}.json")))


def waypoint_tree(g: dict) -> tuple[cKDTree, np.ndarray]:
    pts = []
    for e in g['edges']:
        pts.extend([[p[0], p[1]] for p in e['waypoints']])
    arr = np.array(pts)
    return cKDTree(arr), arr


def patrol_check(map_id: int, tree: cKDTree, min_points: int = 120,
                 max_paths: int = 40) -> dict:
    """Lateral deviation of the extracted centreline from long creature patrols.

    Only patrols whose median distance is under 30 yd are counted as "matched"
    -- the rest are indoor/air/short-range routes that never touch a road.
    """
    rows = sql(f"""
        SELECT w.id, COUNT(*) n FROM waypoint_data w
          JOIN creature_addon a ON a.path_id = w.id
          JOIN creature c ON c.guid = a.guid
         WHERE c.map = {map_id}
         GROUP BY w.id HAVING n >= {min_points}
         ORDER BY n DESC LIMIT {max_paths}""")
    matched, all_med = [], []
    for pid, _n in rows:
        pts = sql(f"SELECT position_x, position_y FROM waypoint_data "
                  f"WHERE id = {pid} ORDER BY point")
        arr = np.array([[float(a), float(b)] for a, b in pts])
        d, _ = tree.query(arr)
        med = float(np.median(d))
        all_med.append((int(pid), med, float(d.mean()), float(d.max()), len(arr)))
        if med < 30.0:
            matched.append((int(pid), med, float(d.mean()), float(d.max()), len(arr)))
    return {'paths_considered': len(rows), 'matched': matched, 'all': all_med}


def landmark_check(map_id: int, tree: cKDTree) -> list[tuple[str, float]]:
    names = "','".join(TELE_LANDMARKS)
    rows = sql(f"SELECT name, map, position_x, position_y FROM game_tele "
               f"WHERE name IN ('{names}') AND map = {map_id} ORDER BY name")
    out = []
    for name, _m, x, y in rows:
        d, _ = tree.query([float(x), float(y)])
        out.append((name, float(d)))
    return out


JUNK_TAXI = ('Transport', 'Quest -', 'Quest-', 'Development', 'CC ', 'Test',
             'Programmer', 'Flavor', 'Filming', ' - ET - ', 'Teleport')


def taxi_check(map_id: int, tree: cKDTree, radius: float = 50.0) -> dict:
    """Real towns only: TaxiNodes.dbc also carries transport, quest-cinematic and
    dev-land entries, many parked at the origin."""
    near, far = [], []
    for t in w.taxi_nodes():
        if t['map'] != map_id or not t['name']:
            continue
        if any(j in t['name'] for j in JUNK_TAXI):
            continue
        if abs(t['x']) < 1.0 and abs(t['y']) < 1.0:
            continue
        d, _ = tree.query([t['x'], t['y']])
        (near if d <= radius else far).append((t['name'], float(d)))
    return {'near': near, 'far': sorted(far, key=lambda kv: kv[1])}


def town_reachability(map_id: int, g: dict, snap: float = 200.0) -> dict:
    """The metric that actually decides usefulness: can a bot road-walk town to town?

    Each real taxi town is snapped to the nearest road waypoint; two towns are
    road-connected when their waypoints land in the same graph component.
    """
    inc = defaultdict(list)
    for i, e in enumerate(g['edges']):
        inc[e['a']].append(i)
        inc[e['b']].append(i)
    comp: dict[int, int] = {}
    c = 0
    for s in range(len(g['nodes'])):
        if s in comp:
            continue
        q = deque([s])
        while q:
            n = q.popleft()
            if n in comp:
                continue
            comp[n] = c
            for i in inc[n]:
                q.append(g['edges'][i]['a'])
                q.append(g['edges'][i]['b'])
        c += 1
    wp, own = [], []
    for e in g['edges']:
        for p in e['waypoints']:
            wp.append([p[0], p[1]])
            own.append(e['a'])
    if not wp:
        return {'towns': 0, 'pairs': 0, 'all_pairs': 0, 'groups': {}}
    tree = cKDTree(np.array(wp))
    groups: dict[int, list[str]] = defaultdict(list)
    total = 0
    for t in w.taxi_nodes():
        if t['map'] != map_id or not t['name']:
            continue
        if any(j in t['name'] for j in JUNK_TAXI):
            continue
        if abs(t['x']) < 1.0 and abs(t['y']) < 1.0:
            continue
        d, i = tree.query([t['x'], t['y']])
        if d > snap:
            continue
        total += 1
        groups[comp[own[i]]].append(t['name'])
    pairs = sum(len(v) * (len(v) - 1) // 2 for v in groups.values())
    allp = total * (total - 1) // 2
    return {'towns': total, 'pairs': pairs, 'all_pairs': allp, 'groups': dict(groups)}


def components(g: dict):
    inc = defaultdict(list)
    for i, e in enumerate(g['edges']):
        inc[e['a']].append(i)
        inc[e['b']].append(i)
    seen = set()
    comps = []
    for s in range(len(g['nodes'])):
        if s in seen:
            continue
        q = deque([s])
        comp = set()
        length = 0.0
        eids = set()
        while q:
            n = q.popleft()
            if n in comp:
                continue
            comp.add(n)
            for i in inc[n]:
                if i in eids:
                    continue
                eids.add(i)
                length += g['edges'][i]['length']
                q.append(g['edges'][i]['a'])
                q.append(g['edges'][i]['b'])
        seen |= comp
        comps.append((len(comp), length, comp))
    comps.sort(key=lambda c: -c[1])
    return comps


def report(map_ids: list[int]) -> str:
    lines = ['# Phase 2 — Road Graph Validation Report', '']
    for mid in map_ids:
        g = load_graph(mid)
        tree, arr = waypoint_tree(g)
        st = g['stats']
        comps = components(g)
        big = comps[0] if comps else (0, 0.0, set())
        lines += [f"## Map {mid} — {w.MAPS[mid]}", '',
                  f"- nodes **{len(g['nodes'])}**, edges **{len(g['edges'])}**, "
                  f"waypoints **{st['waypoints']}**, centreline **{st['length_yd']/1000:.1f}k yd**",
                  f"- provisional connectors: **{st['connectors']}**; "
                  f"Z repaired {st['z_repaired']}, dropped {st['z_dropped']}; "
                  f"waypoints under deep water: **{st['deep_water_waypoints']}**",
                  f"- components: **{len(comps)}**; largest holds "
                  f"{big[0]} nodes / {big[1]/1000:.1f}k yd "
                  f"({100*big[1]/max(st['length_yd'],1):.0f}% of the network)", '']

        tr = town_reachability(mid, g)
        lines += [f"### Town-to-town road reachability", '',
                  f"{tr['towns']} real taxi towns snap onto the graph (<=200 yd); "
                  f"**{tr['pairs']}/{tr['all_pairs']} town pairs "
                  f"({100*tr['pairs']/max(tr['all_pairs'],1):.0f}%) are connected by road alone.** "
                  f"The remainder need the legacy POI links Phase 3 keeps alongside the road graph.", '']
        big_groups = sorted(tr['groups'].values(), key=len, reverse=True)[:3]
        for gp in big_groups:
            lines.append(f"- [{len(gp)}] {', '.join(sorted(gp))}")
        lines.append('')

        lm = landmark_check(mid, tree)
        if lm:
            ok = sum(1 for _, d in lm if d < LANDMARK_OK_YD)
            lines += [f"### Landmarks ({ok}/{len(lm)} within {LANDMARK_OK_YD:.0f} yd "
                      f"of an extracted road)", '',
                      '| Landmark | nearest waypoint (yd) |', '|---|---:|']
            lines += [f"| {n} | {d:.1f} |" for n, d in lm]
            lines.append('')

        pc = patrol_check(mid, tree)
        m = pc['matched']
        if pc['all']:
            lines += [f"### Patrol ground truth ({len(m)}/{pc['paths_considered']} "
                      f"long patrols ride the extracted roads)", '']
            if m:
                med = np.mean([x[1] for x in m])
                mean = np.mean([x[2] for x in m])
                lines += [f"Across matched patrols: mean deviation **{mean:.1f} yd**, "
                          f"median-of-medians **{med:.1f} yd**.", '',
                          '| path id | points | median (yd) | mean (yd) | max (yd) |',
                          '|---:|---:|---:|---:|---:|']
                for pid, md, mn, mx, n in sorted(m, key=lambda r: r[1])[:12]:
                    lines.append(f"| {pid} | {n} | {md:.1f} | {mn:.1f} | {mx:.1f} |")
            lines.append('')

        tc = taxi_check(mid, tree)
        tot = len(tc['near']) + len(tc['far'])
        lines += [f"### Taxi-town proximity ({len(tc['near'])}/{tot} within 50 yd of a road waypoint)", '']
        if tc['far']:
            lines.append('Exceptions (boat/flight-only or gap zones): ' +
                         ', '.join(f"{n} ({d:.0f} yd)" for n, d in tc['far'][:25]) +
                         ('…' if len(tc['far']) > 25 else ''))
        lines.append('')
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    ids = [int(a) for a in sys.argv[1:]] or list(w.MAPS)
    txt = report(ids)
    p = os.path.join(OUT, 'audit', 'validation-report.md')
    with open(p, 'w') as fh:
        fh.write(txt)
    print(txt)
    print('->', p)
