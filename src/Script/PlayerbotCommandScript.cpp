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
#include <map>
#include <string>
#include <vector>

#include "BattleGroundTactics.h"
#include "Chat.h"
#include "GuildTaskMgr.h"
#include "ObjectAccessor.h"
#include "ObjectMgr.h"
#include "PerfMonitor.h"
#include "PlayerbotAI.h"
#include "PlayerbotAIConfig.h"
#include "PlayerbotMgr.h"
#include "Playerbots.h"
#include "Random.h"
#include "RandomPlayerbotMgr.h"
#include "ScriptMgr.h"
#include "StringFormat.h"
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
            {"roaddemo", HandleRoadDemoCommand, SEC_GAMEMASTER, Console::Yes},
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
    // are game_tele names so no coordinates are duplicated here. The first forty
    // are the set the offline sweep in apps/road-nav/astar.py used — the point
    // is that the in-game numbers can be laid beside its results table — and
    // the rest are Horde and Kalimdor pairs added afterwards, because that
    // original set is so Alliance-heavy that a Horde bot had no Eastern
    // Kingdoms route at all to be given.
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
            // Horde and second-side pairs added for the demo; measured by
            // roadcheck alongside the originals, just not part of that table.
            {0, "Brill", "Undercity"},
            {0, "TheSepulcher", "Undercity"},
            {0, "TarrenMill", "Undercity"},
            {0, "TarrenMill", "Hammerfall"},
            {0, "GromgolBaseCamp", "BootyBay"},
            {1, "Orgrimmar", "RazorHill"},
            {1, "TheCrossroads", "Orgrimmar"},
            {1, "CampTaurajo", "ThunderBluff"},
            {1, "SplintertreePost", "TheCrossroads"},
            {1, "ShadowpreyVillage", "CampMojache"},
            {1, "TheramoreIsle", "Ratchet"},
            {1, "NijelsPoint", "Astranaar"},
            {530, "Thrallmar", "Shattrath"},
            {571, "ConquestHold", "Dragonblight"},
            {571, "ConquestHold", "ZulDrak"},
        };

        return routes;
    }

    // Which side owns a town. A bot dropped into the other side's town is shot
    // by its guards before it walks a yard, so the demo starts a bot only from
    // — and sends it only to — towns its faction can stand in. Anything not
    // listed is neutral ground both sides can use; the zone-name teles
    // (Dragonblight, GrizzlyHills, ZulDrak, SholazarBasin, Zangarmarsh,
    // CrystalsongForest) fall through here on purpose.
    enum class TownSide : uint8
    {
        Neutral,
        Alliance,
        Horde,
    };

    static TownSide SideOfTown(char const* name)
    {
        static std::map<std::string, TownSide> const sides = {
            {"Goldshire", TownSide::Alliance},
            {"SentinelHill", TownSide::Alliance},
            {"Darkshire", TownSide::Alliance},
            {"Lakeshire", TownSide::Alliance},
            {"Southshore", TownSide::Alliance},
            {"RefugePointe", TownSide::Alliance},
            {"Thelsamar", TownSide::Alliance},
            {"MenethilHarbor", TownSide::Alliance},
            {"Ironforge", TownSide::Alliance},
            // Argent Dawn ground, but the flight master and the guards are
            // Alliance, so a Horde runner starting here does not survive.
            {"ChillwindCamp", TownSide::Alliance},
            {"Astranaar", TownSide::Alliance},
            {"Auberdine", TownSide::Alliance},
            {"Darnassus", TownSide::Alliance},
            {"FeathermoonStronghold", TownSide::Alliance},
            {"NijelsPoint", TownSide::Alliance},
            {"TheramoreIsle", TownSide::Alliance},
            {"Telaar", TownSide::Alliance},
            {"Sylvanaar", TownSide::Alliance},
            {"AllerianStronghold", TownSide::Alliance},
            {"TheExodar", TownSide::Alliance},
            {"BloodmystIsle", TownSide::Alliance},
            {"ValianceKeep", TownSide::Alliance},
            {"AmberLedge", TownSide::Alliance},
            {"WintergardeKeep", TownSide::Alliance},
            {"StarsRest", TownSide::Alliance},
            {"FizzcrankAirstrip", TownSide::Alliance},

            {"Undercity", TownSide::Horde},
            {"Brill", TownSide::Horde},
            {"TheSepulcher", TownSide::Horde},
            {"TarrenMill", TownSide::Horde},
            {"Hammerfall", TownSide::Horde},
            {"GromgolBaseCamp", TownSide::Horde},
            {"Orgrimmar", TownSide::Horde},
            {"RazorHill", TownSide::Horde},
            {"TheCrossroads", TownSide::Horde},
            {"CampTaurajo", TownSide::Horde},
            {"BloodhoofVillage", TownSide::Horde},
            {"ThunderBluff", TownSide::Horde},
            {"SplintertreePost", TownSide::Horde},
            {"CampMojache", TownSide::Horde},
            {"ShadowpreyVillage", TownSide::Horde},
            {"FalconWatch", TownSide::Horde},
            {"Thrallmar", TownSide::Horde},
            {"Garadar", TownSide::Horde},
            {"Ghostlands", TownSide::Horde},
            {"SilvermoonCity", TownSide::Horde},
            {"WarsongHold", TownSide::Horde},
            {"ConquestHold", TownSide::Horde},
        };

        auto const it = sides.find(name);
        return it == sides.end() ? TownSide::Neutral : it->second;
    }

    // A route is walkable by a team when neither end belongs to the other side.
    // Cross-faction pairs (Refuge Pointe to Hammerfall, Camp Taurajo to
    // Theramore) are therefore offered to nobody: roadcheck still measures them,
    // the demo just will not stage a bot on one.
    static bool RouteSuitsTeam(RoadCheckRoute const& route, TeamId team)
    {
        TownSide const enemy = team == TEAM_HORDE ? TownSide::Alliance : TownSide::Horde;
        return SideOfTown(route.from) != enemy && SideOfTown(route.to) != enemy;
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

    // One bot walking a circuit of towns, remembered so the demo can be
    // followed and called off.
    struct RoadDemoRunner
    {
        ObjectGuid guid;
        std::string botName;
        std::string from;
        std::string to;
        WorldPosition destination;
        float startDistance = 0.f;
        TeamId team = TEAM_ALLIANCE;
        uint32 legs = 0;          // towns reached since the demo started
        uint32 legStartedAt = 0;
        uint32 reissues = 0;      // times it had to be put back on this leg
    };

    struct RoadDemoState
    {
        std::vector<RoadDemoRunner> roster;
        uint32 onlyMap = 0xFFFFFFFF;
        bool running = false;
        uint32 arrivals = 0;
    };

    static RoadDemoState& RoadDemo()
    {
        static RoadDemoState state;
        return state;
    }

    static constexpr float ROAD_DEMO_ARRIVAL_YARDS = 40.f;
    // Renewed on every service pass; only needs to outlast the gap between
    // passes, but a generous window means a stalled service tick is survivable.
    static constexpr uint32 ROAD_DEMO_ACTIVE_WINDOW_MS = 10 * MINUTE * IN_MILLISECONDS;
    static constexpr uint32 ROAD_DEMO_SERVICE_INTERVAL_MS = 3000;

    // Towns paired with this one in the route table, on the same map and
    // walkable by this team. This is what turns a table of benchmark pairs into
    // a circuit: arriving somewhere is just picking the next hop out of it.
    static std::vector<std::string> NeighbourTowns(std::string const& town, TeamId team, uint32 onlyMap)
    {
        std::vector<std::string> out;
        for (RoadCheckRoute const& route : RoadCheckRoutes())
        {
            if (onlyMap != 0xFFFFFFFF && route.mapId != onlyMap)
                continue;
            if (!RouteSuitsTeam(route, team))
                continue;

            if (town == route.from)
                out.emplace_back(route.to);
            else if (town == route.to)
                out.emplace_back(route.from);
        }
        return out;
    }

    // Aim the bot at a town from wherever it is standing. Nothing teleports:
    // a bot that jumps to the next start line demonstrates nothing, and the
    // whole point is the walk between the two.
    static bool StartLeg(RoadDemoRunner& runner, Player* bot, PlayerbotAI* botAI, std::string town)
    {
        WorldPosition dest;
        if (!ResolveTele(town.c_str(), dest))
            return false;

        runner.to = std::move(town);
        runner.destination = dest;
        runner.startDistance = bot->GetExactDist(dest);
        runner.legStartedAt = getMSTime();

        botAI->rpgInfo.ClearTravel();
        botAI->rpgInfo.ChangeToGoGrind(dest);
        return true;
    }

    // Nothing is mutated unless a next town is actually found, so a caller can
    // treat false as "this runner has nowhere left to go" and retire it.
    static bool StartNextLeg(RoadDemoRunner& runner, Player* bot, PlayerbotAI* botAI)
    {
        std::vector<std::string> options = NeighbourTowns(runner.to, runner.team, RoadDemo().onlyMap);
        if (options.empty())
            return false;

        // Prefer anywhere but straight back, so the circuit wanders the map
        // instead of shuttling one road forever. A dead-end town has to.
        std::vector<std::string> onward;
        for (std::string const& candidate : options)
            if (candidate != runner.from)
                onward.push_back(candidate);

        std::vector<std::string> const& pool = onward.empty() ? options : onward;
        std::string next = pool[urand(0, pool.size() - 1)];

        runner.from = runner.to;
        return StartLeg(runner, bot, botAI, std::move(next));
    }

    // Called on a timer for as long as the demo runs. Bots leave the demo on
    // their own constantly — they arrive, they die and get reset, the state
    // machine decides the trip is over — and none of that is worth a command,
    // so this quietly puts them back on the road.
    static void ServiceRoadDemo()
    {
        RoadDemoState& demo = RoadDemo();
        if (!demo.running)
            return;

        for (auto it = demo.roster.begin(); it != demo.roster.end();)
        {
            Player* bot = ObjectAccessor::FindPlayer(it->guid);
            PlayerbotAI* botAI = bot ? GET_PLAYERBOT_AI(bot) : nullptr;
            if (!botAI || !bot->IsInWorld())
            {
                it = demo.roster.erase(it);
                continue;
            }

            // All three expire on their own. Without the force-active window a
            // bot with nobody near it is denied DETAILED_MOVE_ACTIVITY and
            // stops pathing; without the teleport deferral the random manager
            // eventually yanks it off its road.
            botAI->ForceActiveFor(ROAD_DEMO_ACTIVE_WINDOW_MS);
            botAI->SuppressRpgQuestDetour(true);
            sRandomPlayerbotMgr.ScheduleTeleport(bot->GetGUID().GetCounter(), 2 * HOUR);

            if (!bot->IsAlive() || bot->IsInFlight() || bot->IsBeingTeleported())
            {
                ++it;
                continue;
            }

            // Cross-map means a portal or transport leg is in progress; leave
            // the travel plan to finish it rather than second-guessing it.
            if (bot->GetMapId() != it->destination.GetMapId())
            {
                ++it;
                continue;
            }

            if (bot->GetExactDist(it->destination) < ROAD_DEMO_ARRIVAL_YARDS)
            {
                ++it->legs;
                ++demo.arrivals;

                // A town with no onward pair would otherwise be re-detected as
                // an arrival every pass; retire the runner instead of spinning.
                if (!StartNextLeg(*it, bot, botAI))
                {
                    botAI->SuppressRpgQuestDetour(false);
                    botAI->ForceActiveFor(0);
                    it = demo.roster.erase(it);
                    continue;
                }

                ++it;
                continue;
            }

            // Anything but GO_GRIND means the bot fell out of the demo. Put it
            // back on the same leg from wherever it now stands.
            if (botAI->rpgInfo.GetStatus() != RPG_GO_GRIND)
            {
                ++it->reissues;
                StartLeg(*it, bot, botAI, it->to);
            }

            ++it;
        }
    }

    static void ReleaseRoadDemo()
    {
        for (RoadDemoRunner const& runner : RoadDemo().roster)
        {
            Player* bot = ObjectAccessor::FindPlayer(runner.guid);
            PlayerbotAI* botAI = bot ? GET_PLAYERBOT_AI(bot) : nullptr;
            if (!botAI)
                continue;
            botAI->rpgInfo.ClearTravel();
            botAI->rpgInfo.ChangeToIdle();
            botAI->SuppressRpgQuestDetour(false);
            botAI->ForceActiveFor(0);
        }
        RoadDemo().roster.clear();
        RoadDemo().running = false;
        RoadDemo().arrivals = 0;
    }

    // .playerbots travel roaddemo <count> [mapId] | status | stop
    //
    // Puts live random bots on a continuous circuit of towns and leaves them
    // there. A bot is teleported once, to its first town, and from then on it
    // walks: reaching a town picks the next town paired with it in the route
    // table and it sets off again, indefinitely, until stopped.
    //
    // Each leg is an ordinary RPG GO_GRIND destination driving MoveFarTo every
    // tick — the same path any bot takes to a far objective, so what you watch
    // is the real behaviour and not a demo mode.
    static bool HandleRoadDemoCommand(ChatHandler* handler, char const* args)
    {
        if (!TravelNodesReady(handler))
            return true;

        std::string argstr = args ? args : "";

        if (argstr.find("stop") == 0)
        {
            size_t const released = RoadDemo().roster.size();
            ReleaseRoadDemo();
            handler->PSendSysMessage("Released {} demo bots back to normal RPG behaviour.", released);
            return true;
        }

        if (argstr.find("status") == 0)
        {
            if (RoadDemo().roster.empty())
            {
                handler->PSendSysMessage("No demo running. Start one with: .playerbots travel roaddemo <count> [mapId]");
                return true;
            }

            uint32 walking = 0;
            uint32 frozen = 0;
            uint32 dead = 0;
            uint32 planRoadYards = 0;
            uint32 planWalkYards = 0;
            float progressSum = 0.f;
            for (RoadDemoRunner const& runner : RoadDemo().roster)
            {
                Player* bot = ObjectAccessor::FindPlayer(runner.guid);
                PlayerbotAI* botAI = bot ? GET_PLAYERBOT_AI(bot) : nullptr;
                if (!botAI)
                    continue;

                float const remaining = bot->GetExactDist(runner.destination);
                float const progress =
                    runner.startDistance > 0.f
                        ? 100.f * std::max(0.f, runner.startDistance - remaining) / runner.startDistance
                        : 0.f;
                progressSum += progress;

                TravelPlan const& plan = botAI->rpgInfo.travelPlan;
                bool const onPlan = plan.IsActive();
                if (onPlan)
                    ++walking;
                if (!bot->IsAlive())
                    ++dead;

                planWalkYards += plan.plannedWalkYards;
                planRoadYards += plan.plannedRoadYards;

                // A bot denied detailed movement cannot path at all, so report
                // it rather than leaving "it just stands there" to be guessed at.
                bool const canMove = botAI->AllowActivity(DETAILED_MOVE_ACTIVITY);
                if (!canMove)
                    ++frozen;

                // The share of THIS bot's plan that follows road is the number
                // the demo exists to show. A low one is the cost model choosing
                // the beeline, not the bot going astray.
                std::string road = "no plan";
                if (onPlan && plan.plannedWalkYards)
                    road = Acore::StringFormat("{:.0f}% road", 100.f * plan.plannedRoadYards / plan.plannedWalkYards);
                else if (onPlan)
                    road = "direct walk";

                // Minutes on the current leg and how often it had to be put
                // back on it: a runner walking the same road for twenty
                // minutes, or restarted a dozen times, is the interesting one.
                uint32 const legMinutes =
                    runner.legStartedAt ? GetMSTimeDiffToNow(runner.legStartedAt) / (MINUTE * IN_MILLISECONDS) : 0;

                handler->PSendSysMessage(
                    "{} leg {} {} -> {} : {:.0f}% done, {:.0f} yd left, {}, {}m{}{}{} @ {:.0f},{:.0f}",
                    runner.botName, runner.legs + 1, runner.from, runner.to, progress, remaining, road, legMinutes,
                    runner.reissues ? Acore::StringFormat(", restarted {}x", runner.reissues) : std::string(),
                    bot->IsAlive() ? "" : ", DEAD", canMove ? "" : ", NOT ALLOWED TO MOVE", bot->GetPositionX(),
                    bot->GetPositionY());
            }

            size_t const total = RoadDemo().roster.size();
            handler->PSendSysMessage("{} runners, {} town arrivals so far: {} on a travel plan, {} dead. Mean "
                                     "progress on the current leg {:.0f}%.",
                                     total, RoadDemo().arrivals, walking, dead, total ? progressSum / total : 0.f);
            if (planWalkYards)
                handler->PSendSysMessage("Legs in flight plan {} yd of walking, {} yd of it on road ({:.0f}%).",
                                         planWalkYards, planRoadYards, 100.f * planRoadYards / planWalkYards);
            if (frozen)
                handler->PSendSysMessage("{} runners are denied detailed movement — the service tick is not renewing "
                                         "their force-active window.",
                                         frozen);
            handler->PSendSysMessage("Road share and teleports so far: .playerbots travel roadstats");
            return true;
        }

        // One bot per requested runner reads as an empty road. Three abreast on
        // each leg is what makes the routing visible as traffic, which is the
        // whole point of watching it rather than reading roadcheck's table.
        static constexpr uint32 RUNNERS_PER_SLOT = 3;

        uint32 const requested = static_cast<uint32>(atoi(argstr.c_str()));
        if (!requested)
        {
            handler->PSendSysMessage("usage: .playerbots travel roaddemo <count> [mapId]");
            handler->PSendSysMessage("       sends {} bots per <count>, faction-matched to their routes.",
                                     RUNNERS_PER_SLOT);
            handler->PSendSysMessage("       .playerbots travel roaddemo status | stop");
            return true;
        }

        uint32 const wanted = requested * RUNNERS_PER_SLOT;

        uint32 onlyMap = 0xFFFFFFFF;
        if (size_t split = argstr.find(' '); split != std::string::npos)
            onlyMap = static_cast<uint32>(atoi(argstr.c_str() + split + 1));

        std::vector<RoadCheckRoute> routes;
        for (RoadCheckRoute const& route : RoadCheckRoutes())
            if (onlyMap == 0xFFFFFFFF || route.mapId == onlyMap)
                routes.push_back(route);

        if (routes.empty())
        {
            handler->PSendSysMessage("No benchmark routes for map {}.", onlyMap);
            return true;
        }

        std::vector<RoadCheckRoute> allianceRoutes;
        std::vector<RoadCheckRoute> hordeRoutes;
        for (RoadCheckRoute const& route : routes)
        {
            if (RouteSuitsTeam(route, TEAM_ALLIANCE))
                allianceRoutes.push_back(route);
            if (RouteSuitsTeam(route, TEAM_HORDE))
                hordeRoutes.push_back(route);
        }

        if (allianceRoutes.empty() && hordeRoutes.empty())
        {
            handler->PSendSysMessage("Every route on map {} is cross-faction; nothing safe to stage.", onlyMap);
            return true;
        }

        // Only bots nobody is playing with: a bot grouped with a real player is
        // following them, and one in combat or in flight cannot be redirected.
        std::vector<Player*> candidates;
        for (auto const& entry : sRandomPlayerbotMgr.GetAllBots())
        {
            Player* bot = entry.second;
            if (!bot || !bot->IsInWorld() || !bot->IsAlive())
                continue;
            if (bot->IsInCombat() || bot->IsInFlight() || bot->IsBeingTeleported())
                continue;
            if (bot->GetGroup())
                continue;
            PlayerbotAI* botAI = GET_PLAYERBOT_AI(bot);
            if (!botAI)
                continue;

            // Written against GetMaster() rather than IsRealPlayer() /
            // HasRealPlayerMaster() on purpose: upstream #2592 keeps both names
            // but inverts what IsRealPlayer means, so the named form would go on
            // compiling and silently start hijacking self-bots.
            Player* master = botAI->GetMaster();
            if (master == bot)  // self-bot — somebody is playing this character
                continue;
            if (master && !GET_PLAYERBOT_AI(master))  // serving a human
                continue;

            candidates.push_back(bot);
        }

        if (candidates.empty())
        {
            handler->PSendSysMessage("No free random bots to send. Raise AiPlayerbot.MinRandomBots or wait for the "
                                     "current ones to leave combat.");
            return true;
        }

        if (candidates.size() < wanted)
            handler->PSendSysMessage("Only {} free bots available, asked for {}.", candidates.size(), wanted);

        // Clear any previous demo first so the roster does not accumulate.
        ReleaseRoadDemo();
        RoadDemo().onlyMap = onlyMap;
        RoadDemo().running = true;

        uint32 sent = 0;
        uint32 allianceCursor = 0;
        uint32 hordeCursor = 0;
        uint32 unroutable = 0;
        std::map<std::string, uint32> perOrigin;
        for (Player* bot : candidates)
        {
            if (sent >= wanted)
                break;

            bool const horde = bot->GetTeamId() == TEAM_HORDE;
            std::vector<RoadCheckRoute> const& pool = horde ? hordeRoutes : allianceRoutes;
            if (pool.empty())
            {
                ++unroutable;
                continue;
            }

            uint32& cursor = horde ? hordeCursor : allianceCursor;
            RoadCheckRoute const& route = pool[cursor % pool.size()];
            WorldPosition origin;
            WorldPosition destination;
            if (!ResolveTele(route.from, origin) || !ResolveTele(route.to, destination))
            {
                ++cursor;
                continue;
            }
            ++cursor;

            PlayerbotAI* botAI = GET_PLAYERBOT_AI(bot);

            // Scatter the start so a dozen bots do not stack in one pixel of
            // road, and stagger them along it so they read as traffic.
            float const spread = frand(-12.0f, 12.0f);
            WorldPosition start(origin.GetMapId(), origin.GetPositionX() + spread,
                                origin.GetPositionY() + frand(-12.0f, 12.0f), origin.GetPositionZ(),
                                origin.GetOrientation());

            // The only teleport in the whole demo: onto the first start line.
            // Everything after this is walked.
            //
            // resetAI = false: Reset(true) would wipe the rpg state we are about
            // to set, and the bot is idle anyway.
            botAI->TeleportTo(start, false);

            RoadDemoRunner runner;
            runner.guid = bot->GetGUID();
            runner.botName = bot->GetName();
            runner.from = route.from;
            runner.team = bot->GetTeamId();

            // The service tick renews the force-active window and the deferred
            // random-manager teleport from here on; this is just the first one,
            // so the bot is not denied pathfinding before the tick comes round.
            botAI->ForceActiveFor(ROAD_DEMO_ACTIVE_WINDOW_MS);
            botAI->SuppressRpgQuestDetour(true);
            sRandomPlayerbotMgr.ScheduleTeleport(bot->GetGUID().GetCounter(), 2 * HOUR);

            if (!StartLeg(runner, bot, botAI, route.to))
                continue;

            // StartLeg measures from where the bot is, and the teleport above
            // has not landed yet, so the first leg's yardstick comes from the
            // town it is being dropped into rather than the one it left.
            runner.startDistance = origin.distance(destination);

            RoadDemo().roster.push_back(runner);

            ++perOrigin[route.from];
            ++sent;
        }

        handler->PSendSysMessage("Sent {} bots ({} x {}) onto a circuit of {} Alliance and {} Horde routes.", sent,
                                 requested, RUNNERS_PER_SLOT, allianceRoutes.size(), hordeRoutes.size());
        handler->PSendSysMessage("They walk town to town from here — reaching one picks the next. Nothing else "
                                 "teleports.");
        if (unroutable)
            handler->PSendSysMessage("Skipped {} bots: no route on this map their faction can walk.", unroutable);
        for (auto const& [town, count] : perOrigin)
        {
            WorldPosition pos;
            if (!ResolveTele(town.c_str(), pos))
                continue;
            handler->PSendSysMessage("  {} bots leaving {} - .go xyz {:.1f} {:.1f} {:.1f} {}", count, town,
                                     pos.GetPositionX(), pos.GetPositionY(), pos.GetPositionZ(), pos.GetMapId());
        }
        handler->PSendSysMessage("Watch a road, then: .playerbots travel roaddemo status | stop");
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

// The road demo is a standing arrangement, not a one-shot command: runners
// arrive, die, get reset by the random manager, and have to be put back on the
// road. Nothing about that is worth typing a command for, so it is serviced
// here. Inert — one integer compare — whenever no demo is running.
class playerbots_roaddemo_worldscript : public WorldScript
{
public:
    playerbots_roaddemo_worldscript() : WorldScript("playerbots_roaddemo_worldscript", {WORLDHOOK_ON_UPDATE}) {}

    void OnUpdate(uint32 diff) override
    {
        _sinceService += diff;
        if (_sinceService < playerbots_commandscript::ROAD_DEMO_SERVICE_INTERVAL_MS)
            return;

        _sinceService = 0;
        playerbots_commandscript::ServiceRoadDemo();
    }

private:
    uint32 _sinceService = 0;
};

void AddPlayerbotsCommandscripts()
{
    new playerbots_commandscript();
    new playerbots_roaddemo_worldscript();
}
