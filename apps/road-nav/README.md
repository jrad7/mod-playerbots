# Road-navigation extraction pipeline

Offline Python that builds the road-following travel-node graph the router
prices. Nothing here touches the server or requires a build; it reads the 3.3.5a
client MPQs and the server's `.map`/DBC data and writes SQL.

**The generated SQL is not committed** — the graph is ~175,000 rows and rebuilds
in about eight minutes, so run the pipeline to produce it. Until you do,
`AiPlayerbot.TravelNodeRoadIdBase` matches nothing and the road cost model is
inert; the bots fall back to the legacy POI seed.

```
pip install mpyq numpy scipy scikit-image pillow
```

## Paths and credentials

| Env var | Default |
|---|---|
| `WOW_DATA` | `/mnt/h/WOW-LOCAL/wow-3.3.5a/Data` — client `Data` dir (MPQs) |
| `AC_DBC` | `/mnt/h/WOW-LOCAL/azerothcore/env/dist/bin/dbc` — extracted DBCs |
| `AC_MAPS` | `/mnt/h/WOW-LOCAL/azerothcore/env/dist/bin/maps` — extracted `.map` files |

The defaults are one developer's layout; override all three. `validate.py`,
`emit_sql.py` and `astar.py` shell out to `mysql` at `127.0.0.1` as
`acore`/`acore` (the AzerothCore default) against `acore_world` and
`acore_playerbots`.

Everything is written under `out/`, which is git-ignored.

## Run order

```bash
python3 extract.py            # ~20 s   -> out/<map>/*.npz + tiles.json
python3 render.py             # ~50 s   -> out/audit/overview_<map>.png
python3 report.py             # ~90 s   -> out/audit/coverage-report.md + zones/
python3 graph.py              # ~4 min  -> out/graph/<map>.json
python3 graphrender.py        # QA      -> out/audit/graph_<map>.png
python3 validate.py           # ~2 min  -> out/audit/validation-report.md
python3 emit_sql.py           # ~2 s    -> out/sql/*.sql
python3 astar.py              # routing check (needs the scratch tables, below)
```

Maps covered: 0 (Eastern Kingdoms), 1 (Kalimdor), 530 (Outland), 571 (Northrend).

## Modules

| File | Role |
|---|---|
| `wowdata.py` | MPQ priority chain (with a corrected sector reader — mpyq mis-detects stored sectors and throws on three Northrend ADTs), IFF chunk walking, WDT tile enumeration, DBC reader, the validated tile↔world transform |
| `adtroads.py` | ADT road-mask extractor: 4-bit / big-alpha / RLE MCAL, the "do not fix alpha" flag, per-chunk area ids, and the audit-driven texture allow/deny lists |
| `gridmap.py` | Server `.map` reader (heights, areas, liquid), mirroring `GridTerrainData.cpp` index-for-index |
| `extract.py` | Parallel per-tile extraction driver |
| `zones.py` | Chunk area id → root zone, per-zone road statistics |
| `render.py` | Mask stitching, hillshade, composite renders |
| `nearmiss.py`, `linearity.py` | Forensics: which texture paints a given zone, and is it a ribbon or a blob |
| `report.py` | Per-zone renders and `coverage-report.md`, including the manual verdict table |
| `graph.py` | skeletonise → prune → collapse → vectorise → stitch components → Z-snap |
| `graphrender.py` | Vector graph over the mask render |
| `validate.py` | Patrol ground truth, landmark spot checks, taxi proximity, town-to-town reachability |
| `emit_sql.py` | POI anchoring, on/off ramps, `playerbots_travelnode*` SQL, gap connectors, prune list |
| `astar.py` | Offline reimplementation of the in-game cost model; answers "does the router take the road?" |

## Graph conventions

These are contracts with `TravelNode.cpp`, not incidental choices:

- **Road identity is the id range, not a column.** Road nodes occupy ids
  `>= AiPlayerbot.TravelNodeRoadIdBase` (100000); the legacy seed occupies
  0..3780. A link counts as road-following iff its *destination* id is in the
  road range — asymmetric on purpose, so on-ramps are cheap and off-ramps pay
  the off-road premium.
- Every node is emitted `linked=1` and every link `calculated=1`, so
  `TravelNodeMap::LoadNodeStore()` never raises `hasToGen`.
- `saveNodeStore()` refuses to run while road nodes are loaded — it renumbers
  every id to its array index and would silently collapse the reserved range.

## Generated SQL

`emit_sql.py` writes five files to `out/sql/`. Apply the first four in this
order — nodes before the links and paths that reference them:

| File | Contents |
|---|---|
| `road_travelnode.sql` | road junctions and anchors, ids `100000+` |
| `road_travelnode_link.sql` | road links, plus the on-ramps from legacy POIs |
| `road_travelnode_path.sql` | per-link polylines |
| `road_connectors.sql` | creates `playerbots_travelnode_connector` and fills it with the gap joins |
| `road_prune_legacy_links.sql` | **do not apply** — see below |

```bash
cd out/sql
for f in road_travelnode road_travelnode_link road_travelnode_path road_connectors; do
  mysql -h127.0.0.1 -uacore -pacore acore_playerbots < $f.sql
done
```

Each `INSERT` file opens with a `DELETE` over the reserved id range, so
re-applying after a re-emission is safe and does not touch the legacy seed. The
link and path deletes match on *either* endpoint, because on-ramps start at a
legacy POI below the base id — matching only `node_id` would leave those rows
behind as duplicate-key collisions.

Current emission: 11,634 nodes, 28,036 links (850 of them on-ramps), 134,240 path
points, 618 gap connectors, next free id 111634. Restart worldserver after
loading; the node store is read once at startup.

`road_prune_legacy_links.sql` deletes the 1,276 legacy walk links over 200 yd
whose endpoints both have a road ramp. It was **reviewed and rejected**:
`astar.py --prune` changed the outcome for exactly one route, because the legacy
nodes that win are too far from any road to get a ramp in the first place (the
median legacy node sits 157–368 yd off-road, and a 60 yd ramp radius reaches only
~25% of them). `--aggressive` — drop *every* legacy walk link over 200 yd — does
hit the road share target but strands 6 of 10 Eastern Kingdoms routes as
unroutable on foot. The off-road cost multiplier
(`AiPlayerbot.TravelNodeOffRoadCostMultiplier`) was taken instead. Regenerate the
file here if you want to revisit that.

## Scratch tables for `astar.py`

`astar.py` routes over `acore_playerbots.roadtest_travelnode*`, a copy of the
seed plus the emitted road rows, so nothing live is modified:

```bash
mysql -h127.0.0.1 -uacore -pacore acore_playerbots -e "
DROP TABLE IF EXISTS roadtest_travelnode, roadtest_travelnode_link, roadtest_travelnode_path;
CREATE TABLE roadtest_travelnode LIKE playerbots_travelnode;
CREATE TABLE roadtest_travelnode_link LIKE playerbots_travelnode_link;
CREATE TABLE roadtest_travelnode_path LIKE playerbots_travelnode_path;
INSERT INTO roadtest_travelnode SELECT * FROM playerbots_travelnode;
INSERT INTO roadtest_travelnode_link SELECT * FROM playerbots_travelnode_link;
INSERT INTO roadtest_travelnode_path SELECT * FROM playerbots_travelnode_path;"

for f in road_travelnode road_travelnode_link road_travelnode_path; do
  sed "s/INSERT INTO \`playerbots_travelnode/INSERT INTO \`roadtest_travelnode/" out/sql/$f.sql \
    | mysql -h127.0.0.1 -uacore -pacore acore_playerbots
done
```

```bash
python3 astar.py                # stock cost model
python3 astar.py --prune        # with the (rejected) prune list applied
python3 astar.py --aggressive   # drop every legacy walk link > 200 yd
python3 astar.py --sweep        # off-road cost multiplier sweep
```

Override the schema prefix with `ROAD_SCHEMA`.

## Outputs

- `out/<map>/*.npz` — per-tile road masks (1024², uint8) + chunk area ids
- `out/audit/overview_<map>.png`, `out/audit/zones/*.png` — mask renders
- `out/audit/coverage-report.md` — per-zone verdicts and gap-zone decisions
- `out/graph/<map>.json` — the canonical vector road graph (nodes + edge
  polylines, world coords with Z)
- `out/audit/graph_<map>.png` — graph QA renders
- `out/audit/validation-report.md` — graph metrics
- `out/sql/*.sql` — the SQL deliverable
- `out/sql/provisional_connectors.json` — gap connectors, same set as the
  connector table; verify in game with `.playerbots travel verifyconnectors`
- `out/audit/astar-results.md` — offline routing tables

## In-game verification

The branch adds these GM/console commands; `roadcheck` exercises the whole chain
in one call:

```
.playerbots travel roadcheck [mapId]           # benchmark routes: road share, yards, A* time
.playerbots travel roadroute <dest> [origin]   # leg-by-leg breakdown
.playerbots travel verifyconnectors <mapId|all> [apply]
.playerbots travel roadstats [reset]           # soak telemetry
.playerbots travel roaddemo <count> [mapId]    # live bots walking a circuit of towns
.playerbots travel roaddemo status | stop
.playerbots debug zone showpath=road           # draw the road graph in world
```

Requires `AiPlayerbot.EnableTravelNodes = 1`, plus
`AiPlayerbot.TravelNodeRoadIdBase`, `TravelNodeOffRoadCostMultiplier` and
`TravelNodeMaxAStarNodes` (all defaulted in `playerbots.conf.dist`).
