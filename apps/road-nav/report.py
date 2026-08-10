"""Phase 1.4/1.5 -- per-zone audit renders and coverage-report.md.

VERDICTS below is the manual pass: every zone PNG was compared against the
in-game zone map and given a verdict plus, for anything short of OK, the
fallback decision Phase 2 will act on.
"""
from __future__ import annotations

import json
import os
import re

import numpy as np

import wowdata as w
from render import compose
from zones import all_zone_stats, load_tile, root_zone

AUDIT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out', 'audit')

# zone -> (verdict, note). Verdicts: OK | partial | missing | no-roads-in-zone
# "no-roads-in-zone" covers ocean areas, WMO city interiors and zones the client
# genuinely never painted a road into.
VERDICTS: dict[tuple[int, str], tuple[str, str]] = {
    # ---- map 0, Eastern Kingdoms
    (0, 'Stranglethorn Vale'): ('OK', ''),
    (0, 'Eastern Plaguelands'): ('OK', ''),
    (0, 'Dun Morogh'): ('OK', ''),
    (0, 'Swamp of Sorrows'): ('OK', ''),
    (0, 'Western Plaguelands'): ('OK', ''),
    (0, 'Tirisfal Glades'): ('OK', ''),
    (0, 'Elwynn Forest'): ('OK', 'reference zone; waypoint accuracy validated to 1.7 yd'),
    (0, 'Silverpine Forest'): ('OK', ''),
    (0, 'Duskwood'): ('OK', ''),
    (0, 'Wetlands'): ('OK', ''),
    (0, 'The Hinterlands'): ('OK', ''),
    (0, 'Burning Steppes'): ('OK', ''),
    (0, 'Westfall'): ('OK', ''),
    (0, 'Hillsbrad Foothills'): ('OK', ''),
    (0, 'Blasted Lands'): ('OK', ''),
    (0, 'Alterac Mountains'): ('OK', ''),
    (0, 'Arathi Highlands'): ('OK', ''),
    (0, 'Redridge Mountains'): ('OK', ''),
    (0, 'Loch Modan'): ('OK', ''),
    (0, 'Searing Gorge'): ('missing', 'only spill-over paint from Loch Modan/Ironforge tilesets. '
                                      'Fallback: hand-authored connector Thorium Point <-> Badlands/Loch Modan.'),
    (0, 'Badlands'): ('missing', 'Kargath road unpainted. Fallback: hand-authored connector '
                                 'Loch Modan gate <-> Kargath <-> Searing Gorge.'),
    (0, 'Deadwind Pass'): ('missing', 'Karazhan road unpainted; zone carries no road-named texture at all. '
                                      'Fallback: hand-authored connector Duskwood <-> Swamp of Sorrows.'),
    (0, 'Stormwind City'): ('no-roads-in-zone', 'WMO city; only the dock terrain is painted. Phase 3 anchors a gate node.'),
    (0, "Quel'thalas"): ('no-roads-in-zone', 'unreachable map-0 remnant strip.'),
    (0, 'The Great Sea'): ('no-roads-in-zone', 'ocean'),
    (0, 'The Forbidding Sea'): ('no-roads-in-zone', 'ocean'),
    (0, "Gillijim's Isle"): ('no-roads-in-zone', 'unused dev island'),
    # ---- map 1, Kalimdor
    (1, 'Desolace'): ('partial', 'roads recovered by adding DesolaceCracks/DesolaceDirtFootPrints, but the '
                                 'latter is also used as ground scatter -- the zone render is noisy and Phase 2 '
                                 'blob-pruning has to carry it.'),
    (1, 'Durotar'): ('OK', ''),
    (1, 'The Barrens'): ('OK', ''),
    (1, 'Ashenvale'): ('OK', ''),
    (1, 'Dustwallow Marsh'): ('OK', ''),
    (1, 'Feralas'): ('OK', ''),
    (1, 'Felwood'): ('OK', ''),
    (1, 'Hyjal'): ('OK', ''),
    (1, 'Darkshore'): ('OK', ''),
    (1, 'Mulgore'): ('OK', ''),
    (1, 'Thunder Bluff'): ('OK', 'mesa paths painted'),
    (1, 'Darnassus'): ('OK', ''),
    (1, 'Moonglade'): ('OK', ''),
    (1, 'Dire Maul'): ('OK', 'courtyard only; instance zone'),
    (1, 'Thousand Needles'): ('partial', 'canyon-floor track recovered via DesolaceDirtFootPrints; '
                                         'the Great Lift ramp is unpainted -- connector needed.'),
    (1, 'Azshara'): ('partial', 'only the west approach painted. Fallback: accept partial coverage.'),
    (1, 'Teldrassil'): ('partial', 'ring road painted thinly; island is self-contained, low routing value.'),
    (1, 'Winterspring'): ('partial', 'Everlook approaches only. Fallback: hand-authored connector to Felwood.'),
    (1, 'Silithus'): ('missing', 'no road-shaped texture layer in the zone. Fallback: hand-authored '
                                 'connector Un\'Goro border <-> Cenarion Hold <-> AQ gates.'),
    (1, 'Stonetalon Mountains'): ('missing', 'no road-shaped layer. Fallback: hand-authored connector '
                                             'Barrens <-> Sun Rock Retreat <-> Desolace/Ashenvale.'),
    (1, 'Tanaris'): ('missing', 'TanarisDirtFootprint exists but paints 11k px total. Fallback: '
                                'hand-authored connectors around the Gadgetzan hub.'),
    (1, 'Orgrimmar'): ('no-roads-in-zone', 'city terrain unpainted; Phase 3 anchors a gate node off the Durotar road.'),
    (1, "Un'Goro Crater"): ('no-roads-in-zone', 'the crater has no roads in game either -- correct result.'),
    (1, "Gates of Ahn'Qiraj"): ('no-roads-in-zone', 'brick platform, not a road (denylisted BurningSteppsBrick01).'),
    (1, 'GM Island'): ('no-roads-in-zone', ''),
    (1, 'The Great Sea'): ('no-roads-in-zone', 'ocean'),
    # ---- map 530, Outland + blood elf / draenei starts
    (530, 'Eversong Woods'): ('OK', 'recovered by adding EversongDirt02; that layer also base-fills whole '
                                    'chunks in places, which Phase 2 must strip as blobs.'),
    (530, "Isle of Quel'Danas"): ('OK', 'recovered by adding EversongDirt02; same base-fill caveat.'),
    (530, 'Ghostlands'): ('OK', ''),
    (530, 'Nagrand'): ('OK', ''),
    (530, 'Shadowmoon Valley'): ('OK', ''),
    (530, 'Netherstorm'): ('OK', ''),
    (530, 'Zangarmarsh'): ('OK', ''),
    (530, 'Bloodmyst Isle'): ('OK', ''),
    (530, 'Terokkar Forest'): ('OK', ''),
    (530, 'Azuremyst Isle'): ('OK', ''),
    (530, 'Hellfire Peninsula'): ('OK', ''),
    (530, 'Shattrath City'): ('OK', 'approach roads painted; interior is WMO'),
    (530, "Blade's Edge Mountains"): ('partial', 'plateau roads painted, canyon links are bridges (WMO) -- '
                                                 'Phase 2 gap joining must close them.'),
    (530, 'The Exodar'): ('no-roads-in-zone', 'WMO city; gate anchor'),
    (530, 'Silvermoon City'): ('no-roads-in-zone', 'WMO city; gate anchor'),
    (530, 'The North Sea'): ('no-roads-in-zone', 'ocean; carries EversongDirt02 beach bleed -- '
                                                 'Phase 2 drops it as unconnected fragments.'),
    (530, 'The Veiled Sea'): ('no-roads-in-zone', 'ocean'),
    (530, 'Twisting Nether'): ('no-roads-in-zone', 'void'),
    # ---- map 571, Northrend
    (571, 'Borean Tundra'): ('OK', ''),
    (571, 'Dragonblight'): ('OK', ''),
    (571, 'Wintergrasp'): ('OK', ''),
    (571, 'Crystalsong Forest'): ('OK', ''),
    (571, 'The Storm Peaks'): ('partial', 'SP_SnowPath* paints wide dappled areas rather than ribbons; '
                                          'usable trunk only after aggressive Phase 2 cleanup.'),
    (571, 'Sholazar Basin'): ('partial', 'titan road segments only'),
    (571, 'Grizzly Hills'): ('partial', 'log roads painted around settlements, not between them'),
    (571, "Zul'Drak"): ('partial', 'ZD_PathA covers 3 tiles; the main drakkari road is unpainted.'),
    (571, 'Howling Fjord'): ('missing', 'no confident road layer -- HF_SmoothRockA traces fjord cliff edges, '
                                        'not roads. Fallback: exclude zone from road routing.'),
    (571, 'Icecrown'): ('missing', 'IG_DirtySnow* arcs follow the crater rim, not the road. '
                                   'Fallback: exclude zone from road routing.'),
    (571, "Hrothgar's Landing"): ('missing', 'its only match was the denylisted BoneWastesRoad seabed fill. '
                                             'Fallback: exclude (isolated island, boat/flight access only).'),
    (571, 'The Frozen Sea'): ('no-roads-in-zone', 'ocean'),
    (571, 'The North Sea'): ('no-roads-in-zone', 'ocean'),
}

HAS_ROADS_INGAME = {k for k, (v, _) in VERDICTS.items() if v != 'no-roads-in-zone'}


def zone_render(map_id: int, stat: dict, ds: int = 8) -> str | None:
    """Render one zone: roads over hillshade, zone chunks tinted."""
    tiles = [tuple(t) for t in stat['tiles']]
    if not tiles or len(tiles) > 140:
        tiles = [tuple(t) for t in stat['tiles_road']] or tiles[:140]
    if not tiles:
        return None
    at = w.area_table()
    cols = [c for c, _ in tiles]
    rows = [r for _, r in tiles]
    c0, r0 = min(cols), min(rows)
    step = 1024 // ds
    hl = np.zeros(((max(rows) - r0 + 1) * step, (max(cols) - c0 + 1) * step), bool)
    per = step // 16
    for col, row in tiles:
        _m, areas = load_tile(map_id, col, row)
        if areas is None:
            continue
        for iy in range(16):
            for ix in range(16):
                if root_zone(at, int(areas[iy, ix])) == stat['zone_id']:
                    hl[(row - r0) * step + iy * per:(row - r0) * step + (iy + 1) * per,
                       (col - c0) * step + ix * per:(col - c0) * step + (ix + 1) * per] = True
    img = compose(map_id, tiles, ds, highlight=hl)
    safe = re.sub(r'[^A-Za-z0-9]+', '_', stat['name']).strip('_')
    path = os.path.join(AUDIT, 'zones', f"{map_id}_{safe}.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path)
    return path


def build(render_zones: bool = True):
    os.makedirs(AUDIT, exist_ok=True)
    stats = all_zone_stats()
    rows = [s for s in stats.values()
            if s['chunks'] >= 8 and not s['name'].startswith('#')]
    rows.sort(key=lambda s: (s['map_id'], -s['road_px']))

    if render_zones:
        for s in rows:
            key = (s['map_id'], s['name'])
            if key in VERDICTS and VERDICTS[key][0] != 'no-roads-in-zone':
                zone_render(s['map_id'], s)

    lines = []
    A = lines.append
    A('# Phase 1 — World Extraction & Coverage Report')
    A('')
    A('Generated by `pipeline/report.py`. Masks: `pipeline/out/<map>/<col>_<row>.npz`. '
      'Continent renders: `out/audit/overview_<map>.png`. Per-zone renders: `out/audit/zones/`.')
    A('')

    tot = {'OK': 0, 'partial': 0, 'missing': 0, 'no-roads-in-zone': 0, 'unclassified': 0}
    per_map: dict[int, dict[str, int]] = {}
    for s in rows:
        v = VERDICTS.get((s['map_id'], s['name']), ('unclassified', ''))[0]
        tot[v] += 1
        per_map.setdefault(s['map_id'], {}).setdefault(v, 0)
        per_map[s['map_id']][v] += 1

    A('## Summary')
    A('')
    A('| Map | Tiles | Tiles with road paint | Zones OK | partial | missing | no-roads-in-zone |')
    A('|---|---:|---:|---:|---:|---:|---:|')
    for mid in w.MAPS:
        meta = json.load(open(os.path.join('out', str(mid), 'tiles.json')))
        painted = sum(1 for t in meta['tiles'] if t.get('road_px'))
        p = per_map.get(mid, {})
        A(f"| {mid} {w.MAPS[mid]} | {len(meta['tiles'])} | {painted} | "
          f"{p.get('OK',0)} | {p.get('partial',0)} | {p.get('missing',0)} | {p.get('no-roads-in-zone',0)} |")
    road_zones = tot['OK'] + tot['partial'] + tot['missing']
    A('')
    A(f"**{tot['OK']}/{road_zones} road-bearing zones OK "
      f"({100*tot['OK']/max(road_zones,1):.0f}%), {tot['partial']} partial, {tot['missing']} missing.**")
    A('')

    A('## Per-zone detail')
    A('')
    A('| Map | Zone | Verdict | Chunks | Road chunks | Road px | Tiles | Tiles w/ paint | Road textures |')
    A('|---|---|---|---:|---:|---:|---:|---:|---|')
    for s in rows:
        v, note = VERDICTS.get((s['map_id'], s['name']), ('unclassified', ''))
        tex = ', '.join(sorted({t.split('\\')[-1].replace('.blp', '')
                                for t in s['road_tex']}))[:110]
        A(f"| {s['map_id']} | {s['name']} | {v} | {s['chunks']} | {s['chunks_road']} | "
          f"{s['road_px']} | {len(s['tiles'])} | {len(s['tiles_road'])} | {tex} |")
    A('')

    A('## Gap-zone decisions')
    A('')
    A('| Map | Zone | Verdict | Decision carried into Phase 2 |')
    A('|---|---|---|---|')
    for s in rows:
        v, note = VERDICTS.get((s['map_id'], s['name']), ('unclassified', ''))
        if v in ('partial', 'missing') and note:
            A(f"| {s['map_id']} | {s['name']} | {v} | {note} |")
    A('')

    with open(os.path.join(AUDIT, 'coverage-report.md'), 'w') as fh:
        fh.write('\n'.join(lines) + '\n')
    print(os.path.join(AUDIT, 'coverage-report.md'))
    print(tot)


if __name__ == '__main__':
    import sys
    build(render_zones='--no-render' not in sys.argv)
