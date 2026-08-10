"""Phase 3 -- anchor the road graph to POIs and emit playerbots_travelnode* SQL.

Design decisions taken from phase-3-graph-integration.md:
  1. Augment, never replace: road rows live in a reserved id range (>= 100000)
     alongside the 3,781-node legacy seed.
  2. On/off ramps: every legacy POI node within 60 yd of a road gets a short
     bidirectional walk link onto the nearest road node.
  3. Roads win by pruning redundant long legacy walk links (emitted separately
     as reviewable DELETEs), not by faking cost.
  4. Cities terminate at gate nodes; interiors stay on legacy links.

Regeneration safety (verified against origin/TravelSystem-pr, see
integration-report.md): every emitted node carries linked=1 and every emitted
link calculated=1, so LoadNodeStore() never sets hasToGen and generateWalkPaths()
skips our nodes outright.
"""
from __future__ import annotations

import json
import math
import os
import struct
import subprocess
import sys
from collections import defaultdict

import numpy as np
from scipy.spatial import cKDTree

import wowdata as w
from zones import OUT

ID_BASE = 100_000
"""Reserved id range for road rows. Legacy seed occupies 0..3780."""

ANCHOR_SNAP_YD = 60.0
"""Decision 2 (naming): POIs farther than this keep only their legacy links."""

RAMP_SNAP_YD = 120.0
"""On/off-ramp radius. The plan defaulted to 60 yd; measured, that reaches only
~25% of legacy nodes (median legacy node is 157-368 yd from a road depending on
the map), and every un-ramped node keeps a legacy beeline that outcompetes the
road. 120 yd reaches ~35% while still being a leg a bot can plausibly walk
off-road; beyond that the "ramp" is really cross-country travel."""

LINK_WALK = 1
CREATURE_RADIUS = 50.0     # matches TravelNodePath::calculateCost (agro radius + 5)
PRUNE_MIN_YD = 200.0       # decision 3a: legacy walk links longer than this

NPCFLAG_FLIGHTMASTER = 0x2000
NPCFLAG_SPIRITHEALER = 0x4000
NPCFLAG_INNKEEPER = 0x10000

SQL_DIR = os.path.join(OUT, 'sql')

WORLD = ['mysql', '-h127.0.0.1', '-uacore', '-pacore', '-N', '-B', 'acore_world', '-e']
BOTS = ['mysql', '-h127.0.0.1', '-uacore', '-pacore', '-N', '-B', 'acore_playerbots', '-e']


def q(cmd, sql_text):
    out = subprocess.run(cmd + [sql_text], capture_output=True, text=True)
    if out.returncode:
        raise RuntimeError(out.stderr)
    return [line.split('\t') for line in out.stdout.strip().split('\n') if line]


# ------------------------------------------------------------------ factions

class Factions:
    """Enough of FactionTemplate.dbc to reproduce Unit::GetFactionReactionTo."""

    def __init__(self):
        dbc = w.DBC(os.path.join(w.DBC_DIR, 'FactionTemplate.dbc'))
        self.t = {}
        for r in dbc.rows():
            self.t[r[0]] = {
                'faction': r[1], 'group': r[3], 'friendGroup': r[4],
                'enemyGroup': r[5], 'enemies': r[6:10], 'friends': r[10:14],
            }

    def friendly(self, a_id: int, b_id: int) -> bool:
        a, b = self.t.get(a_id), self.t.get(b_id)
        if not a or not b:
            return False
        if b['faction'] and b['faction'] in a['enemies']:
            return False
        if b['faction'] and b['faction'] in a['friends']:
            return True
        if b['group'] & a['enemyGroup']:
            return False
        if b['group'] & a['friendGroup']:
            return True
        if a['group'] & b['enemyGroup']:
            return False
        return bool(a['group'] & b['friendGroup'])


def creature_index(map_id: int, fac: Factions):
    """KD-tree of spawns plus the three max-level buckets the cost model uses."""
    rows = q(WORLD, f"""
        SELECT c.position_x, c.position_y, t.maxlevel, t.faction
          FROM creature c JOIN creature_template t ON t.entry = c.id
         WHERE c.map = {map_id}""")
    xs, lv, bucket = [], [], []
    for x, y, maxlevel, faction in rows:
        f = int(faction)
        af = fac.friendly(f, 1)
        hf = fac.friendly(f, 2)
        if not af and not hf:
            b = 0
        elif af and not hf:
            b = 1
        elif hf and not af:
            b = 2
        else:
            continue                      # friendly to both: no annoyance
        xs.append([float(x), float(y)])
        lv.append(int(maxlevel))
        bucket.append(b)
    if not xs:
        return None, np.zeros(0), np.zeros(0)
    return cKDTree(np.array(xs)), np.array(lv), np.array(bucket)


def max_creature(tree, levels, buckets, waypoints) -> tuple[int, int, int]:
    if tree is None:
        return 0, 0, 0
    pts = np.array([[p[0], p[1]] for p in waypoints])
    hits = tree.query_ball_point(pts, CREATURE_RADIUS)
    seen = set()
    for h in hits:
        seen.update(h)
    out = [0, 0, 0]
    for i in seen:
        b = int(buckets[i])
        out[b] = max(out[b], min(int(levels[i]), 127))
    return tuple(out)


# ------------------------------------------------------------------- anchors

def poi_anchors(map_id: int) -> list[tuple[str, float, float]]:
    """Named destinations worth naming a road node after (3.1)."""
    out = []
    for t in w.taxi_nodes():
        if t['map'] == map_id and t['name'] and abs(t['x']) + abs(t['y']) > 1:
            out.append((f"{t['name']} road", t['x'], t['y']))
    rows = q(WORLD, f"""
        SELECT t.name, c.position_x, c.position_y, t.npcflag
          FROM creature c JOIN creature_template t ON t.entry = c.id
         WHERE c.map = {map_id}
           AND (t.npcflag & {NPCFLAG_INNKEEPER} OR t.npcflag & {NPCFLAG_FLIGHTMASTER}
                OR t.npcflag & {NPCFLAG_SPIRITHEALER})""")
    for name, x, y, flag in rows:
        kind = ('innkeeper' if int(flag) & NPCFLAG_INNKEEPER else
                'flightmaster' if int(flag) & NPCFLAG_FLIGHTMASTER else 'spirit healer')
        out.append((f"{name} ({kind}) road", float(x), float(y)))
    # instance entrances: source coordinates live in AreaTrigger.dbc
    tele = {int(r[0]) for r in q(WORLD, "SELECT ID FROM areatrigger_teleport")}
    names = {int(r[0]): r[1] for r in q(WORLD, "SELECT ID, Name FROM areatrigger_teleport")}
    dbc = w.DBC(os.path.join(w.DBC_DIR, 'AreaTrigger.dbc'))
    for r in dbc.rows():
        if r[0] in tele and r[1] == map_id:
            x = dbc.floats(r, 2)
            y = dbc.floats(r, 3)
            out.append((f"{names.get(r[0], 'entrance')} entrance road", x, y))
    return out


def legacy_nodes(map_id: int) -> list[dict]:
    rows = q(BOTS, f"SELECT id, name, x, y, z FROM playerbots_travelnode WHERE map_id = {map_id}")
    return [{'id': int(i), 'name': n, 'x': float(x), 'y': float(y), 'z': float(z)}
            for i, n, x, y, z in rows]


def legacy_walk_links(map_id: int) -> list[tuple[int, int, float]]:
    rows = q(BOTS, f"""
        SELECT l.node_id, l.to_node_id, l.distance
          FROM playerbots_travelnode_link l
          JOIN playerbots_travelnode a ON a.id = l.node_id
          JOIN playerbots_travelnode b ON b.id = l.to_node_id
         WHERE l.type = {LINK_WALK} AND a.map_id = {map_id} AND b.map_id = {map_id}""")
    return [(int(a), int(b), float(d)) for a, b, d in rows]


# ---------------------------------------------------------------- transform

def _split_edge(nodes, edges, ei, wi, name, kind):
    e = edges[ei]
    if wi <= 0:
        nodes[e['a']].setdefault('name', name)
        return e['a']
    if wi >= len(e['waypoints']) - 1:
        nodes[e['b']].setdefault('name', name)
        return e['b']
    p = e['waypoints'][wi]
    nid = len(nodes)
    nodes.append({'id': nid, 'x': p[0], 'y': p[1], 'z': p[2], 'kind': kind, 'name': name})
    head = e['waypoints'][:wi + 1]
    tail = e['waypoints'][wi:]
    prov = e.get('provisional', False)
    edges[ei] = {'a': e['a'], 'b': nid, 'length': _plen(head),
                 'provisional': prov, 'waypoints': head}
    edges.append({'a': nid, 'b': e['b'], 'length': _plen(tail),
                  'provisional': prov, 'waypoints': tail})
    return nid


def _plen(pts):
    return sum(math.dist(pts[i][:2], pts[i + 1][:2]) for i in range(len(pts) - 1))


def anchor(map_id: int, g: dict, rounds: int = 4) -> dict:
    """3.1: name road nodes after nearby POIs, splitting edges where needed.

    Splitting invalidates an edge's waypoint indices, so each round skips edges
    already touched and the next round re-indexes; several rounds are needed
    because towns cluster many POIs onto the same stretch of road.
    """
    nodes, edges = g['nodes'], g['edges']
    pending = poi_anchors(map_id)
    named = 0
    for _ in range(rounds):
        if not pending:
            break
        pts, tags = [], []
        for ei, e in enumerate(edges):
            for wi, p in enumerate(e['waypoints']):
                pts.append([p[0], p[1]])
                tags.append((ei, wi))
        if not pts:
            break
        tree = cKDTree(np.array(pts))
        dirty: set[int] = set()
        deferred = []
        for name, x, y in pending:
            idxs = tree.query_ball_point([x, y], ANCHOR_SNAP_YD)
            if not idxs:
                continue
            best = None
            blocked = False
            for k in idxs:
                ei, wi = tags[k]
                if ei in dirty:
                    blocked = True
                    continue
                d = math.dist([x, y], pts[k])
                if best is None or d < best[0]:
                    best = (d, ei, wi)
            if best is None:
                if blocked:
                    deferred.append((name, x, y))
                continue
            _d, ei, wi = best
            before = len(edges)
            _split_edge(nodes, edges, ei, wi, name[:1000], 'anchor')
            if len(edges) != before:
                dirty.add(ei)
            named += 1
        pending = deferred
    return {'named': named, 'unsnapped_deferred': len(pending)}


def ramps(map_id: int, g: dict) -> list[dict]:
    """Decision 2: link legacy POI nodes onto the nearest road node."""
    nodes, edges = g['nodes'], g['edges']
    legacy = legacy_nodes(map_id)
    if not legacy or not nodes:
        return []
    npos = np.array([[n['x'], n['y']] for n in nodes])
    tree = cKDTree(npos)
    out = []
    for ln in legacy:
        d, i = tree.query([ln['x'], ln['y']])
        if d > RAMP_SNAP_YD:
            continue
        rn = nodes[int(i)]
        out.append({'legacy': ln['id'], 'road': int(i), 'distance': float(d),
                    'waypoints': [[ln['x'], ln['y'], ln['z']],
                                  [rn['x'], rn['y'], rn['z']]]})
    return out


def road_components(nodes, edges) -> dict[int, int]:
    """node index -> connected component id over the road graph."""
    from collections import deque
    inc = defaultdict(list)
    for e in edges:
        inc[e['a']].append(e)
        inc[e['b']].append(e)
    comp: dict[int, int] = {}
    c = 0
    for s0 in range(len(nodes)):
        if s0 in comp:
            continue
        dq = deque([s0])
        while dq:
            n = dq.popleft()
            if n in comp:
                continue
            comp[n] = c
            for e in inc[n]:
                dq.append(e['a'])
                dq.append(e['b'])
        c += 1
    return comp


def esc(s: str) -> str:
    return s.replace('\\', '').replace("'", '').replace('\n', ' ')[:1000]


def emit(map_ids: list[int], prune_enabled: bool = True) -> dict:
    os.makedirs(SQL_DIR, exist_ok=True)
    fac = Factions()
    node_sql, link_sql, path_sql, prune_sql = [], [], [], []
    stats = {}
    next_id = ID_BASE

    provisional_list = []

    for mid in map_ids:
        g = json.load(open(os.path.join(OUT, 'graph', f"{mid}.json")))
        a = anchor(mid, g)
        nodes, edges = g['nodes'], g['edges']
        rmp = ramps(mid, g)
        tree, levels, buckets = creature_index(mid, fac)

        base = next_id
        for i, n in enumerate(nodes):
            name = n.get('name') or f"road {n.get('kind', 'junction')} {mid}:{i}"
            node_sql.append(f"({base + i},'{esc(name)}',{mid},{n['x']:.3f},"
                            f"{n['y']:.3f},{n['z']:.3f},1)")
        next_id = base + len(nodes)

        # playerbots_travelnode_link is keyed on (node_id, to_node_id), so parallel
        # edges between the same two junctions -- a road that loops back, or an
        # edge split on both sides -- have to collapse to the shortest one.
        best_edge: dict[tuple[int, int], dict] = {}
        dup = 0
        for e in edges:
            if e['a'] == e['b']:
                continue
            key = (min(e['a'], e['b']), max(e['a'], e['b']))
            prev = best_edge.get(key)
            if prev is None:
                best_edge[key] = e
            else:
                dup += 1
                if e['length'] < prev['length']:
                    best_edge[key] = e
        stats_dup = dup

        points = 0
        for e in best_edge.values():
            a_id, b_id = base + e['a'], base + e['b']
            wp = e['waypoints']
            m0, m1, m2 = max_creature(tree, levels, buckets, wp)
            dist = max(e['length'], 0.1)
            for src, dst, poly in ((a_id, b_id, wp), (b_id, a_id, wp[::-1])):
                link_sql.append(f"({src},{dst},{LINK_WALK},0,{dist:.3f},0,0,1,{m0},{m1},{m2})")
                for nr, p in enumerate(poly):
                    path_sql.append(f"({src},{dst},{nr},{mid},{p[0]:.3f},{p[1]:.3f},{p[2]:.3f})")
                    points += 1
            if e.get('provisional'):
                provisional_list.append({
                    'map': mid, 'a': a_id, 'b': b_id, 'length': round(dist, 1),
                    'reason': e.get('reason', 'gap'),
                    'from': [round(wp[0][0], 1), round(wp[0][1], 1), round(wp[0][2], 1)],
                    'to': [round(wp[-1][0], 1), round(wp[-1][1], 1), round(wp[-1][2], 1)]})

        for r in rmp:
            src, dst = r['legacy'], base + r['road']
            d = max(r['distance'], 0.1)
            m0, m1, m2 = max_creature(tree, levels, buckets, r['waypoints'])
            for s, t, poly in ((src, dst, r['waypoints']), (dst, src, r['waypoints'][::-1])):
                link_sql.append(f"({s},{t},{LINK_WALK},0,{d:.3f},0,0,1,{m0},{m1},{m2})")
                for nr, p in enumerate(poly):
                    path_sql.append(f"({s},{t},{nr},{mid},{p[0]:.3f},{p[1]:.3f},{p[2]:.3f})")
                    points += 1

        # 3.3 prune list. The plan's rule is "prune only when an offline road
        # route exists between the endpoints", so a shared ramp is not enough --
        # both endpoints must ramp into the *same* road component.
        comp = road_components(nodes, best_edge.values())
        ramped = {r['legacy']: comp.get(r['road'], -1) for r in rmp}
        pruned = 0
        for aa, bb, d in legacy_walk_links(mid):
            if (d > PRUNE_MIN_YD and aa in ramped and bb in ramped
                    and ramped[aa] == ramped[bb] >= 0):
                prune_sql.append(f"DELETE FROM `playerbots_travelnode_link` "
                                 f"WHERE node_id={aa} AND to_node_id={bb};")
                prune_sql.append(f"DELETE FROM `playerbots_travelnode_path` "
                                 f"WHERE node_id={aa} AND to_node_id={bb};")
                pruned += 1

        stats[mid] = {'nodes': len(nodes), 'edges': len(best_edge),
                      'parallel_edges_collapsed': stats_dup, 'ramps': len(rmp),
                      'named_anchors': a['named'], 'points': points,
                      'pruned_legacy_links': pruned, 'id_base': base}
        print(f"map {mid}: {len(nodes)} road nodes (ids {base}..{next_id-1}), "
              f"{len(best_edge)} edges ({stats_dup} parallel collapsed), {len(rmp)} on/off ramps, "
              f"{a['named']} named anchors, {points} path points, "
              f"{pruned} legacy links proposed for pruning")

    header = ("-- Road-following travel nodes, generated by\n"
              "-- apps/road-nav/emit_sql.py\n"
              f"-- Reserved id range: {ID_BASE}+ (legacy seed occupies 0..3780).\n"
              "-- Every node is linked=1 and every link calculated=1 so that\n"
              "-- TravelNodeMap::LoadNodeStore() never raises hasToGen.\n")

    def write(name, cols, table, rows, guard, chunk=500):
        p = os.path.join(SQL_DIR, name)
        with open(p, 'w') as fh:
            fh.write(header)
            # Idempotent: clear whatever a previous emission left in the reserved
            # range so the file can be re-applied, and so it is safe to ship as a
            # dated update on a database that already carries road rows.
            fh.write(f"DELETE FROM `{table}` WHERE {guard};\n")
            for i in range(0, len(rows), chunk):
                fh.write(f"INSERT INTO `{table}` ({cols}) VALUES\n")
                fh.write(',\n'.join(rows[i:i + chunk]))
                fh.write(';\n')
        return p, len(rows)

    # A link or path row belongs to the road graph if either endpoint is in the
    # reserved range: on-ramps start at a legacy POI below ID_BASE.
    edge_guard = f"`node_id` >= {ID_BASE} OR `to_node_id` >= {ID_BASE}"
    p1, n1 = write('road_travelnode.sql', '`id`,`name`,`map_id`,`x`,`y`,`z`,`linked`',
                   'playerbots_travelnode', node_sql, f"`id` >= {ID_BASE}")
    p2, n2 = write('road_travelnode_link.sql',
                   '`node_id`,`to_node_id`,`type`,`object`,`distance`,`swim_distance`,'
                   '`extra_cost`,`calculated`,`max_creature_0`,`max_creature_1`,`max_creature_2`',
                   'playerbots_travelnode_link', link_sql, edge_guard)
    p3, n3 = write('road_travelnode_path.sql',
                   '`node_id`,`to_node_id`,`nr`,`map_id`,`x`,`y`,`z`',
                   'playerbots_travelnode_path', path_sql, edge_guard)
    p4 = os.path.join(SQL_DIR, 'road_prune_legacy_links.sql')
    with open(p4, 'w') as fh:
        fh.write(header)
        fh.write("-- Decision 3a: legacy walk links over 200 yd whose endpoints both\n"
                 "-- have a road on/off ramp. Review before applying.\n")
        fh.write('\n'.join(prune_sql) + ('\n' if prune_sql else ''))
    p5 = os.path.join(SQL_DIR, 'provisional_connectors.json')
    with open(p5, 'w') as fh:
        json.dump(provisional_list, fh, indent=1)

    # Same list as a table the server can read, so
    # '.playerbots travel verifyconnectors' can walk each gap join with the real
    # PathGenerator and mark or delete it. Nothing in the runtime requires this
    # table; it is verification bookkeeping.
    p6 = os.path.join(SQL_DIR, 'road_connectors.sql')
    with open(p6, 'w') as fh:
        fh.write(header)
        fh.write("-- Gap connectors: road links that close a break in the paint\n"
                 "-- (bridge, ford, tunnel, or a plain unpainted stretch) and so\n"
                 "-- were never proven walkable offline. reason is the offline\n"
                 "-- classification; verified is filled in by the server:\n"
                 "--   0 unchecked, 1 navmesh-walkable, 2 not walkable.\n")
        fh.write("DROP TABLE IF EXISTS `playerbots_travelnode_connector`;\n"
                 "CREATE TABLE `playerbots_travelnode_connector` (\n"
                 "  `node_id` int unsigned NOT NULL,\n"
                 "  `to_node_id` int unsigned NOT NULL,\n"
                 "  `map_id` int unsigned NOT NULL,\n"
                 "  `length` float NOT NULL DEFAULT 0,\n"
                 "  `reason` varchar(16) NOT NULL DEFAULT 'gap',\n"
                 "  `verified` tinyint unsigned NOT NULL DEFAULT 0,\n"
                 "  PRIMARY KEY (`node_id`,`to_node_id`)\n"
                 ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n")
        rows = [f"({c['a']},{c['b']},{c['map']},{c['length']},'{c['reason']}',0)"
                for c in provisional_list]
        for i in range(0, len(rows), 500):
            fh.write("INSERT INTO `playerbots_travelnode_connector` "
                     "(`node_id`,`to_node_id`,`map_id`,`length`,`reason`,`verified`) VALUES\n")
            fh.write(',\n'.join(rows[i:i + 500]))
            fh.write(';\n')

    print(f"\n{n1} node rows -> {p1}")
    print(f"{n2} link rows -> {p2}")
    print(f"{n3} path rows -> {p3}")
    print(f"{len(prune_sql)//2} prune statements -> {p4}")
    print(f"{len(provisional_list)} provisional connectors -> {p5}")
    print(f"{len(provisional_list)} connector rows -> {p6}")
    return {'stats': stats, 'rows': {'node': n1, 'link': n2, 'path': n3},
            'prune': len(prune_sql) // 2, 'provisional': len(provisional_list),
            'next_id': next_id}


if __name__ == '__main__':
    ids = [int(a) for a in sys.argv[1:]] or list(w.MAPS)
    res = emit(ids)
    with open(os.path.join(SQL_DIR, 'emit_stats.json'), 'w') as fh:
        json.dump(res, fh, indent=1)
