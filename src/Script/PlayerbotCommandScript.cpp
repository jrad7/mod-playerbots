/*
 * This program is free software; you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the
 * Free Software Foundation; either version 2 of the License, or (at your
 * option) any later version.
 *
 * This program is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
 * FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
 * more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program. If not, see <http://www.gnu.org/licenses/>.
 */

#include <algorithm>
#include <cstdlib>
#include <string>
#include <vector>

#include "BattleGroundTactics.h"
#include "Chat.h"
#include "GuildTaskMgr.h"
#include "ObjectMgr.h"
#include "PerfMonitor.h"
#include "PlayerbotAIConfig.h"
#include "PlayerbotMgr.h"
#include "RandomPlayerbotMgr.h"
#include "ScriptMgr.h"
#include "TravelNode.h"

using namespace Acore::ChatCommands;

class playerbots_commandscript : public CommandScript
{
public:
    playerbots_commandscript() : CommandScript("playerbots_commandscript") {}

    ChatCommandTable GetCommands() const override
    {
        static ChatCommandTable playerbotsDebugCommandTable = {
            {"bg", HandleDebugBGCommand, SEC_GAMEMASTER, Console::Yes},
            {"zone", HandleDebugZoneCommand, SEC_GAMEMASTER, Console::No},
        };

        static ChatCommandTable playerbotsAccountCommandTable = {
            {"setKey", HandleSetSecurityKeyCommand, SEC_PLAYER, Console::No},
            {"link", HandleLinkAccountCommand, SEC_PLAYER, Console::No},
            {"linkedAccounts", HandleViewLinkedAccountsCommand, SEC_PLAYER, Console::No},
            {"unlink", HandleUnlinkAccountCommand, SEC_PLAYER, Console::No},
        };

        static ChatCommandTable playerbotsTravelCommandTable = {
            {"generatenode", HandleGenerateTravelNodesCommand, SEC_GAMEMASTER, Console::Yes},
            {"roadcheck", HandleRoadCheckCommand, SEC_GAMEMASTER, Console::Yes},
            {"roadroute", HandleRoadRouteCommand, SEC_GAMEMASTER, Console::Yes},
            {"verifyconnectors", HandleVerifyConnectorsCommand, SEC_GAMEMASTER, Console::Yes},
            {"roadstats", HandleRoadStatsCommand, SEC_GAMEMASTER, Console::Yes},
        };

        static ChatCommandTable playerbotsCommandTable = {
            {"bot", HandlePlayerbotCommand, SEC_PLAYER, Console::No},
            {"gtask", HandleGuildTaskCommand, SEC_GAMEMASTER, Console::Yes},
            {"pmon", HandlePerfMonCommand, SEC_GAMEMASTER, Console::Yes},
            {"rndbot", HandleRandomPlayerbotCommand, SEC_GAMEMASTER, Console::Yes},
            {"travel", playerbotsTravelCommandTable},
            {"debug", playerbotsDebugCommandTable},
            {"account", playerbotsAccountCommandTable},
        };

        static ChatCommandTable commandTable = {
            {"playerbots", playerbotsCommandTable},
        };

        return commandTable;
    }

    static bool HandlePlayerbotCommand(ChatHandler* handler, char const* args)
    {
        return PlayerbotMgr::HandlePlayerbotMgrCommand(handler, args);
    }

    static bool HandleRandomPlayerbotCommand(ChatHandler* handler, char const* args)
    {
        return RandomPlayerbotMgr::HandlePlayerbotConsoleCommand(handler, args);
    }

    static bool HandleGuildTaskCommand(ChatHandler* handler, char const* args)
    {
        return GuildTaskMgr::HandleConsoleCommand(handler, args);
    }

    static bool HandlePerfMonCommand(ChatHandler* /*handler*/, char const* args)
    {
        if (!strcmp(args, "reset"))
        {
            sPerfMonitor.Reset();
            return true;
        }

        if (!strcmp(args, "tick"))
        {
            sPerfMonitor.PrintStats(true, false);
            return true;
        }

        if (!strcmp(args, "stack"))
        {
            sPerfMonitor.PrintStats(false, true);
            return true;
        }

        if (!strcmp(args, "toggle"))
        {
            sPlayerbotAIConfig.perfMonEnabled = !sPlayerbotAIConfig.perfMonEnabled;
            if (sPlayerbotAIConfig.perfMonEnabled)
                LOG_INFO("playerbots", "Performance monitor enabled");
            else
                LOG_INFO("playerbots", "Performance monitor disabled");
            return true;
        }

        sPerfMonitor.PrintStats();
        return true;
    }

    static bool HandleGenerateTravelNodesCommand(ChatHandler* handler, char const* /*args*/)
    {
        handler->PSendSysMessage("Regenerating travel node paths...");
        LOG_INFO("playerbots", "Manual travel node regeneration started via console command.");
        sTravelNodeMap.generateAll();
        handler->PSendSysMessage("Travel node regeneration complete. Paths saved to database.");
        return true;
    }

    static bool HandleDebugBGCommand(ChatHandler* handler, char const* args)
    {
        return BGTactics::HandleConsoleCommand(handler, args);
    }

    // Town pairs used to measure whether the router walks the road. Endpoints
    // are game_tele names so no coordinates are duplicated here, and the set is
    // the same one the offline sweep in apps/road-nav/astar.py used — the point
    // is that the in-game numbers can be laid beside its results table.
    struct RoadCheckRoute
    {
        uint32 mapId;
        char const* from;
        char const* to;
    };

    static std::vector<RoadCheckRoute> const& RoadCheckRoutes()
    {
        static std::vector<RoadCheckRoute> const routes = {
            {0, "Goldshire", "SentinelHill"},
            {0, "Goldshire", "Darkshire"},
            {0, "Goldshire", "Lakeshire"},
            {0, "Darkshire", "SentinelHill"},
            {0, "Southshore", "ChillwindCamp"},
            {0, "Southshore", "RefugePointe"},
            {0, "Thelsamar", "MenethilHarbor"},
            {0, "Thelsamar", "Ironforge"},
            {0, "RefugePointe", "Hammerfall"},
            {0, "ChillwindCamp", "LightsHopeChapel"},
            {1, "TheCrossroads", "RazorHill"},
            {1, "TheCrossroads", "Ratchet"},
            {1, "TheCrossroads", "CampTaurajo"},
            {1, "CampTaurajo", "TheramoreIsle"},
            {1, "Astranaar", "Auberdine"},
            {1, "Astranaar", "SplintertreePost"},
            {1, "BloodhoofVillage", "ThunderBluff"},
            {1, "Auberdine", "Darnassus"},
            {1, "FeathermoonStronghold", "CampMojache"},
            {1, "NijelsPoint", "ShadowpreyVillage"},
            {530, "Shattrath", "Telaar"},
            {530, "Shattrath", "FalconWatch"},
            {530, "Shattrath", "Garadar"},
            {530, "Telaar", "Garadar"},
            {530, "FalconWatch", "Thrallmar"},
            {530, "Shattrath", "Sylvanaar"},
            {530, "Shattrath", "AllerianStronghold"},
            {530, "Zangarmarsh", "Shattrath"},
            {530, "Ghostlands", "SilvermoonCity"},
            {530, "BloodmystIsle", "TheExodar"},
            {571, "ValianceKeep", "AmberLedge"},
            {571, "ValianceKeep", "FizzcrankAirstrip"},
            {571, "AmberLedge", "WintergardeKeep"},
            {571, "WintergardeKeep", "StarsRest"},
            {571, "Dragonblight", "WintergardeKeep"},
            {571, "GrizzlyHills", "Dragonblight"},
            {571, "ZulDrak", "GrizzlyHills"},
            {571, "Dalaran", "CrystalsongForest"},
            {571, "SholazarBasin", "ValianceKeep"},
            {571, "WarsongHold", "Dragonblight"},
        };

        return routes;
    }

    static bool ResolveTele(char const* name, WorldPosition& out)
    {
        GameTele const* tele = sObjectMgr->GetGameTele(name, true);
        if (!tele)
            return false;

        out = WorldPosition(tele->mapId, tele->position_x, tele->position_y, tele->position_z, tele->orientation);
        return true;
    }

    static bool TravelNodesReady(ChatHandler* handler)
    {
        if (!sPlayerbotAIConfig.enableTravelNodes)
        {
            handler->PSendSysMessage("Travel nodes are off. Set AiPlayerbot.EnableTravelNodes = 1 and restart.");
            return false;
        }

        if (sTravelNodeMap.getNodes().empty())
        {
            handler->PSendSysMessage("No travel nodes are loaded.");
            return false;
        }

        return true;
    }

    // .playerbots travel roadcheck [mapId]
    //
    // Routes every benchmark pair over the live graph and reports how much of
    // each walk follows extracted road. This is the measurement that says
    // whether the road data is doing anything, without watching a bot travel.
    static bool HandleRoadCheckCommand(ChatHandler* handler, char const* args)
    {
        if (!TravelNodesReady(handler))
            return true;

        uint32 onlyMap = 0xFFFFFFFF;
        if (args && *args)
            onlyMap = static_cast<uint32>(atoi(args));

        if (!sTravelNodeMap.hasRoadData())
            handler->PSendSysMessage(
                "Warning: no nodes in the road id range ({}+) are loaded, so every route below is legacy-only.",
                sPlayerbotAIConfig.travelNodeRoadIdBase);

        handler->PSendSysMessage("off-road cost multiplier {:.2f}, {} road nodes loaded",
                                 sPlayerbotAIConfig.travelNodeOffRoadCostMultiplier,
                                 sTravelNodeMap.getRoadNodeCount());
        handler->PSendSysMessage("route | road share | walked | ridden | legs | build");

        uint32 checked = 0;
        uint32 unroutable = 0;
        uint32 overThreshold = 0;
        float shareSum = 0.f;
        uint32 microsSum = 0;
        uint32 microsWorst = 0;

        for (RoadCheckRoute const& route : RoadCheckRoutes())
        {
            if (onlyMap != 0xFFFFFFFF && route.mapId != onlyMap)
                continue;

            WorldPosition from;
            WorldPosition to;
            if (!ResolveTele(route.from, from) || !ResolveTele(route.to, to))
            {
                handler->PSendSysMessage("{} -> {} : no game_tele entry", route.from, route.to);
                continue;
            }

            ++checked;
            RoadRouteStats stats = sTravelNodeMap.MeasureRoute(from, to, nullptr);
            microsSum += stats.buildMicros;
            microsWorst = std::max(microsWorst, stats.buildMicros);

            if (!stats.routed)
            {
                ++unroutable;
                handler->PSendSysMessage("{} -> {} : NO ROUTE ({}, {} us)", route.from, route.to,
                                         stats.snapFailed ? "endpoints did not snap onto the graph" : "A* found none",
                                         stats.buildMicros);
                continue;
            }

            shareSum += stats.roadShare();
            if (stats.roadShare() > 20.f)
                ++overThreshold;

            handler->PSendSysMessage("{} -> {} : {:.0f}% | {:.0f} yd | {:.0f} yd over {} legs | {} nodes | {} us",
                                     route.from, route.to, stats.roadShare(), stats.walkYards, stats.rideYards,
                                     stats.rideLegs, stats.nodeCount, stats.buildMicros);
        }

        if (!checked)
        {
            handler->PSendSysMessage("No benchmark routes for that map.");
            return true;
        }

        uint32 const routed = checked - unroutable;
        handler->PSendSysMessage("{}/{} routes over 20% road, mean road share {:.0f}%, {} unroutable.", overThreshold,
                                 checked, routed ? shareSum / routed : 0.f, unroutable);
        handler->PSendSysMessage("A* build time: mean {} us, worst {} us over {} routes.", microsSum / checked,
                                 microsWorst, checked);
        return true;
    }

    // .playerbots travel roadroute <game_tele name>
    //
    // Leg-by-leg breakdown of one route, from the caller's position (or from a
    // second named tele when called on the console).
    static bool HandleRoadRouteCommand(ChatHandler* handler, char const* args)
    {
        if (!TravelNodesReady(handler))
            return true;

        if (!args || !*args)
        {
            handler->PSendSysMessage("usage: .playerbots travel roadroute <destination> [origin]");
            handler->PSendSysMessage("Names are game_tele entries; origin defaults to your position.");
            return true;
        }

        std::string argstr = args;
        std::string destName = argstr;
        std::string originName;
        if (size_t split = argstr.find(' '); split != std::string::npos)
        {
            destName = argstr.substr(0, split);
            originName = argstr.substr(split + 1);
        }

        WorldPosition to;
        if (!ResolveTele(destName.c_str(), to))
        {
            handler->PSendSysMessage("No game_tele named '{}'.", destName);
            return true;
        }

        WorldPosition from;
        Player* player = handler->GetSession() ? handler->GetSession()->GetPlayer() : nullptr;
        if (!originName.empty())
        {
            if (!ResolveTele(originName.c_str(), from))
            {
                handler->PSendSysMessage("No game_tele named '{}'.", originName);
                return true;
            }
        }
        else if (player)
            from = WorldPosition(player->GetMapId(), player->GetPositionX(), player->GetPositionY(),
                                 player->GetPositionZ(), player->GetOrientation());
        else
        {
            handler->PSendSysMessage("On the console an origin is required: roadroute <destination> <origin>");
            return true;
        }

        if (from.GetMapId() != to.GetMapId())
        {
            handler->PSendSysMessage("Origin and destination are on different maps ({} vs {}); the node graph only "
                                     "routes within a map.",
                                     from.GetMapId(), to.GetMapId());
            return true;
        }

        RoadRouteStats stats = sTravelNodeMap.MeasureRoute(from, to, nullptr);
        if (!stats.routed)
        {
            handler->PSendSysMessage("No route ({}).",
                                     stats.snapFailed ? "endpoints did not snap onto the graph" : "A* found none");
            return true;
        }

        handler->PSendSysMessage("{:.0f}% of {:.0f} walked yards on road; {:.0f} yd ridden over {} legs; {} nodes in "
                                 "{} us.",
                                 stats.roadShare(), stats.walkYards, stats.rideYards, stats.rideLegs, stats.nodeCount,
                                 stats.buildMicros);

        for (std::string const& line : sTravelNodeMap.DescribeRoute(from, to, nullptr))
            handler->PSendSysMessage("{}", line);

        return true;
    }

    // .playerbots travel roadstats [reset]
    //
    // Running totals since startup: how much of the walking bots planned follows
    // a road, and how often they gave up and teleported instead. Reset before a
    // soak, read after; run the same soak with EnableTravelNodes off for the
    // control teleport rate.
    static bool HandleRoadStatsCommand(ChatHandler* handler, char const* args)
    {
        if (args && *args && std::string(args).find("reset") != std::string::npos)
        {
            sTravelNodeMap.Telemetry().reset();
            handler->PSendSysMessage("Travel telemetry reset.");
            return true;
        }

        for (std::string const& line : sTravelNodeMap.TelemetryReport())
            handler->PSendSysMessage("{}", line);

        return true;
    }

    // .playerbots travel verifyconnectors <mapId|all> [apply]
    //
    // The road graph closes breaks in the paint (bridges, fords, tunnels) with
    // straight connectors that the offline pipeline could not prove walkable.
    // This walks each of them with the real PathGenerator.
    static bool HandleVerifyConnectorsCommand(ChatHandler* handler, char const* args)
    {
        if (!TravelNodesReady(handler))
            return true;

        if (!args || !*args)
        {
            handler->PSendSysMessage("usage: .playerbots travel verifyconnectors <mapId|all> [apply]");
            handler->PSendSysMessage("Checking every map at once loads terrain for every grid a connector touches; "
                                     "one map at a time is kinder.");
            return true;
        }

        std::string argstr = args;
        bool apply = false;
        if (size_t split = argstr.find(' '); split != std::string::npos)
        {
            apply = argstr.substr(split + 1).find("apply") != std::string::npos;
            argstr = argstr.substr(0, split);
        }

        uint32 const mapId = argstr == "all" ? 0xFFFFFFFF : static_cast<uint32>(atoi(argstr.c_str()));

        handler->PSendSysMessage("Verifying connectors on {}{}...", argstr == "all" ? "every map" : "map " + argstr,
                                 apply ? ", deleting failures" : " (report only)");

        for (std::string const& line : sTravelNodeMap.VerifyConnectors(mapId, apply))
            handler->PSendSysMessage("{}", line);

        return true;
    }

    // Visual constants for showpath markers. Two waypoint-family
    // creatures give nodes vs path waypoints distinct visuals; both
    // render at their creature_template default scale (no override).
    //   nodes (anchors) → 15897, prominent waypoint variant
    //   path waypoints  → 15631, standard BG-showpath waypoint
    //
    // SHOWPATH_PATH_DISPLAY_ID = 0 uses the path-creature's default
    // model. To experiment with a model override, set this to a known-
    // good creature display ID for your DB (spell-visual IDs are not
    // universally registered as creature displays — using one risks
    // summoning invisible markers).
    static constexpr uint32 SHOWPATH_NODE_CREATURE = 15897;
    static constexpr uint32 SHOWPATH_PATH_CREATURE = 15631;
    static constexpr uint32 SHOWPATH_PATH_DISPLAY_ID = 0;       // 0 = default model
    static constexpr uint32 SHOWPATH_DESPAWN_MS = 60000;

    static bool HandleDebugZoneCommand(ChatHandler* handler, char const* args)
    {
        Player* player = handler->GetSession() ? handler->GetSession()->GetPlayer() : nullptr;
        if (!player)
        {
            handler->PSendSysMessage("Command requires an in-game player.");
            return false;
        }

        if (!args || !*args)
        {
            handler->PSendSysMessage("usage: .playerbots debug zone showpath=all|node|path");
            return false;
        }

        char* cmd = strtok(const_cast<char*>(args), " ");
        // showpath=all  → nodes + cached path waypoints (full picture)
        // showpath=node → only node anchors
        // showpath=path → only cached path waypoints (no anchors)
        // showpath=road → only the extracted road graph, nodes and waypoints
        bool showNodes = false;
        bool showLinks = false;
        bool roadOnly = false;
        if (cmd && strcmp(cmd, "showpath=all") == 0)
        {
            showNodes = true;
            showLinks = true;
        }
        else if (cmd && strcmp(cmd, "showpath=node") == 0)
        {
            showNodes = true;
            showLinks = false;
        }
        else if (cmd && strcmp(cmd, "showpath=path") == 0)
        {
            showNodes = false;
            showLinks = true;
        }
        else if (cmd && strcmp(cmd, "showpath=road") == 0)
        {
            showNodes = true;
            showLinks = true;
            roadOnly = true;
        }
        else
        {
            handler->PSendSysMessage("usage: .playerbots debug zone showpath=all|node|path|road");
            return false;
        }

        uint32 zoneId = player->GetZoneId();
        std::vector<TravelNode*> const& zoneNodes = sTravelNodeMap.GetNodesInZone(zoneId);
        if (zoneNodes.empty())
        {
            handler->PSendSysMessage("No travel nodes registered in zone {} (is the travel node system loaded?)", zoneId);
            return true;
        }

        WorldPosition playerPos(player->GetMapId(), player->GetPositionX(), player->GetPositionY(),
                                player->GetPositionZ(), player->GetOrientation());

        // Markers are a scarce budget, so spend them on what is under the
        // player's nose. Drawing in zone-index order spends the whole budget on
        // whichever nodes happen to load first — with road data loaded that is
        // the legacy POI seed, whose links are cross-country beelines averaging
        // ~93 waypoints each, so six of them exhaust the budget and the road
        // graph never gets drawn at all.
        constexpr uint32 MAX_PATH_MARKERS = 1000;
        constexpr float SHOWPATH_RADIUS = 500.0f;

        std::vector<TravelNode*> nodes;
        nodes.reserve(zoneNodes.size());
        uint32 roadInZone = 0;
        for (TravelNode* node : zoneNodes)
        {
            if (!node || node->GetMapId() != player->GetMapId())
                continue;
            if (node->isRoadNode())
                ++roadInZone;
            if (roadOnly && !node->isRoadNode())
                continue;
            nodes.push_back(node);
        }

        std::sort(nodes.begin(), nodes.end(), [&playerPos](TravelNode* a, TravelNode* b)
                  { return a->fDist(playerPos) < b->fDist(playerPos); });

        handler->PSendSysMessage("Zone {}: {} travel nodes, {} of them road.", zoneId, zoneNodes.size(), roadInZone);
        if (roadOnly && nodes.empty())
        {
            handler->PSendSysMessage("No road nodes in this zone — is the road SQL loaded and "
                                     "AiPlayerbot.TravelNodeRoadIdBase correct?");
            return true;
        }

        // node markers — full-scale anchor at each travel-node position.
        uint32 nodesPlaced = 0;
        if (showNodes)
        {
            for (TravelNode* node : nodes)
            {
                WorldPosition* pos = node->getPosition();
                if (!pos)
                    continue;
                Creature* wp = player->SummonCreature(SHOWPATH_NODE_CREATURE,
                                                      pos->GetPositionX(), pos->GetPositionY(),
                                                      pos->GetPositionZ(), 0,
                                                      TEMPSUMMON_TIMED_DESPAWN, SHOWPATH_DESPAWN_MS);
                if (wp)
                {
                    wp->SetOwnerGUID(player->GetGUID());
                    ++nodesPlaced;
                }
            }
        }

        if (!showLinks)
        {
            handler->PSendSysMessage("Showing {} travel nodes in zone {} (60s)", nodesPlaced, zoneId);
            return true;
        }

        // path-waypoint markers — a breadcrumb trail along each stored polyline,
        // nearest links first and only within SHOWPATH_RADIUS of the player.
        // Walk-type links from any node in the list are drawn; requiring the
        // destination to be in-zone too would leave sparse zones (Teldrassil)
        // with nothing, since their only links leave the zone.
        uint32 pathPlaced = 0;
        uint32 linksDrawn = 0;
        uint32 roadLinksDrawn = 0;
        bool capped = false;
        for (TravelNode* node : nodes)
        {
            if (capped)
                break;

            auto* links = node->getLinks();
            if (!links)
                continue;

            for (auto const& kv : *links)
            {
                TravelNode* dst = kv.first;
                TravelNodePath* path = kv.second;
                if (!dst || !path)
                    continue;
                if (path->getPathType() != TravelNodePathType::walk)
                    continue;

                bool const roadLink = node->isRoadNode() && dst->isRoadNode();
                if (roadOnly && !roadLink)
                    continue;

                uint32 placedHere = 0;
                for (WorldPosition const& wpPos : path->GetPath())
                {
                    if (wpPos.GetMapId() != player->GetMapId())
                        continue;
                    if (playerPos.distance(wpPos) > SHOWPATH_RADIUS)
                        continue;
                    if (pathPlaced >= MAX_PATH_MARKERS)
                    {
                        capped = true;
                        break;
                    }

                    Creature* mk = player->SummonCreature(SHOWPATH_PATH_CREATURE,
                                                          wpPos.GetPositionX(),
                                                          wpPos.GetPositionY(), wpPos.GetPositionZ(),
                                                          0, TEMPSUMMON_TIMED_DESPAWN,
                                                          SHOWPATH_DESPAWN_MS);
                    if (mk)
                    {
                        mk->SetOwnerGUID(player->GetGUID());
                        if (SHOWPATH_PATH_DISPLAY_ID)
                            mk->SetDisplayId(SHOWPATH_PATH_DISPLAY_ID);
                        ++pathPlaced;
                        ++placedHere;
                    }
                }

                if (placedHere)
                {
                    ++linksDrawn;
                    if (roadLink)
                        ++roadLinksDrawn;
                }

                if (capped)
                    break;
            }
        }

        handler->PSendSysMessage("Showing {} nodes + {} waypoints across {} walk links ({} of them road) within {:.0f} "
                                 "yd{} (60s)",
                                 nodesPlaced, pathPlaced, linksDrawn, roadLinksDrawn, SHOWPATH_RADIUS,
                                 capped ? ", capped at 1000 waypoint markers" : "");
        return true;
    }

    static bool HandleSetSecurityKeyCommand(ChatHandler* handler, char const* args)
    {
        if (!args || !*args)
        {
            handler->PSendSysMessage("Usage: .playerbots account setKey <securityKey>");
            return false;
        }

        Player* player = handler->GetSession()->GetPlayer();
        std::string key = args;

        PlayerbotMgr* mgr = PlayerbotsMgr::instance().GetPlayerbotMgr(player);
        if (mgr)
        {
            mgr->HandleSetSecurityKeyCommand(player, key);
            return true;
        }
        else
        {
            handler->PSendSysMessage("PlayerbotMgr instance not found.");
            return false;
        }
    }

    static bool HandleLinkAccountCommand(ChatHandler* handler, char const* args)
    {
        if (!args || !*args)
            return false;

        char* accountName = strtok((char*)args, " ");
        char* key = strtok(nullptr, " ");

        if (!accountName || !key)
        {
            handler->PSendSysMessage("Usage: .playerbots account link <accountName> <securityKey>");
            return false;
        }

        Player* player = handler->GetSession()->GetPlayer();

        PlayerbotMgr* mgr = PlayerbotsMgr::instance().GetPlayerbotMgr(player);
        if (mgr)
        {
            mgr->HandleLinkAccountCommand(player, accountName, key);
            return true;
        }
        else
        {
            handler->PSendSysMessage("PlayerbotMgr instance not found.");
            return false;
        }
    }

    static bool HandleViewLinkedAccountsCommand(ChatHandler* handler, char const* /*args*/)
    {
        Player* player = handler->GetSession()->GetPlayer();

        PlayerbotMgr* mgr = PlayerbotsMgr::instance().GetPlayerbotMgr(player);
        if (mgr)
        {
            mgr->HandleViewLinkedAccountsCommand(player);
            return true;
        }
        else
        {
            handler->PSendSysMessage("PlayerbotMgr instance not found.");
            return false;
        }
    }

    static bool HandleUnlinkAccountCommand(ChatHandler* handler, char const* args)
    {
        if (!args || !*args)
            return false;

        char* accountName = strtok((char*)args, " ");
        if (!accountName)
        {
            handler->PSendSysMessage("Usage: .playerbots account unlink <accountName>");
            return false;
        }

        Player* player = handler->GetSession()->GetPlayer();

        PlayerbotMgr* mgr = PlayerbotsMgr::instance().GetPlayerbotMgr(player);
        if (mgr)
        {
            mgr->HandleUnlinkAccountCommand(player, accountName);
            return true;
        }
        else
        {
            handler->PSendSysMessage("PlayerbotMgr instance not found.");
            return false;
        }
    }
};

void AddPlayerbotsCommandscripts() { new playerbots_commandscript(); }
