from game.ai_personality import (
    DISRUPTOR,
    EXPANSION,
    STANDARD,
    TRADER,
    get_ai_personality_profile,
)
from game.development_cards import DevelopmentCardType
from game.forecast_events import (
    SHEEP_DROUGHT_EVENT_ID,
    WHEAT_HARVEST_EVENT_ID,
)
from game.hex_tile import get_token_pip_count
from game.resources import BUILD_COSTS, ResourceType


AI_ACTION_DELAY_MS = 1250
AI_SPEED_OPTIONS = (
    ("ゆっくり", 2000),
    ("標準", AI_ACTION_DELAY_MS),
    ("高速", 500),
    ("一時停止", None),
)


class SimpleAI:
    """A small, deterministic heuristic player that only chooses legal actions."""

    def step(self, game):
        if game.winner is not None or game.has_active_dice_animation():
            return False

        if game.phase == "initial":
            return self._step_initial(game)
        if game.phase != "main":
            return False

        if game.special_phase == "discard":
            if game.discard_player is None or not game.discard_player.is_ai:
                return False
            self._set_status(
                game,
                game.discard_player,
                "捨てる資源を選択",
                "都市・開拓地に必要な組み合わせを優先して残します",
            )
            game.discard_resource(self._choose_discard(game.discard_player))
            return True

        player = game.get_current_player()
        if player is None or not player.is_ai:
            return False
        profile = self._profile(player)

        if game.special_phase is not None:
            return self._step_special(game, player)

        if not game.dice_rolled:
            self._set_status(game, player, "ダイスを振る", "出目を確認して建設計画を更新します")
            game.handle_roll_dice()
            return True

        if not game.development_card_used_this_turn and self._play_development_card(game, player):
            return True

        buildable_node_getters = {
            "city": game.get_buildable_city_nodes,
            "settlement": game.get_buildable_settlement_nodes,
        }
        for build_type in profile.build_order:
            candidates = buildable_node_getters[build_type](player)
            if not candidates:
                continue
            node = max(
                candidates,
                key=lambda candidate: self._node_score(game, candidate, player),
            )
            if build_type == "city":
                self._set_status(
                    game,
                    player,
                    "都市を建設",
                    "生産力が最も高くなる開拓地を都市化します",
                )
                game.build_city((node.x, node.y))
            else:
                self._set_status(
                    game,
                    player,
                    "開拓地を建設",
                    "出目の強さ・資源の種類・港を比較しました",
                )
                game.build_settlement((node.x, node.y))
            return True

        priority_goals = profile.goal_order[:2]

        if not game.ai_domestic_trade_attempted:
            domestic_trade = self._choose_domestic_trade(
                game,
                player,
                goals=priority_goals,
            )
            if domestic_trade is not None:
                game.ai_domestic_trade_attempted = True
                partner, give, receive = domestic_trade
                self._set_status(
                    game,
                    player,
                    "国内交易を提案",
                    "公開されている生産力と直近獲得から交渉相手を選びます",
                )
                game.propose_domestic_trade(partner, give, receive)
                return True

        priority_trade = self._choose_bank_trade(
            game,
            player,
            goals=priority_goals,
        )
        if priority_trade is not None:
            give_resource, receive_resource = priority_trade
            self._set_status(game, player, "銀行交易", "上位の建設目標に必要な資源へ交換します")
            game.start_bank_trade()
            game.select_bank_trade_resource(give_resource)
            game.select_bank_trade_resource(receive_resource)
            return True

        road_edges = game.get_buildable_road_edges(player)
        best_road = None
        best_road_score = float("-inf")
        if road_edges:
            best_road = max(road_edges, key=lambda candidate: self._edge_score(game, candidate, player))
            best_road_score = self._edge_score(game, best_road, player)

        can_buy_development = bool(
            game.development_deck and player.can_afford(BUILD_COSTS["development"])
        )
        if can_buy_development and profile.development_before_road:
            self._set_status(
                game,
                player,
                "発展カードを購入",
                "騎士と特殊効果で相手の生産を抑えます",
            )
            game.buy_development_card()
            return True
        should_expand_road = best_road is not None and (
            best_road_score >= profile.road_score_threshold
            or game.get_player_longest_road_length(player) < profile.minimum_road_length
            or not can_buy_development
        )
        if should_expand_road:
            self._set_status(game, player, "街道を建設", "将来の開拓地候補と最長交易路を伸ばします")
            edge = best_road
            game.build_road(self._edge_midpoint(edge))
            return True

        if can_buy_development:
            self._set_status(game, player, "発展カードを購入", "盤面を伸ばせないため特殊効果を確保します")
            game.buy_development_card()
            return True

        if not game.ai_domestic_trade_attempted:
            game.ai_domestic_trade_attempted = True
            domestic_trade = self._choose_domestic_trade(game, player)
            if domestic_trade is not None:
                partner, give, receive = domestic_trade
                self._set_status(
                    game,
                    player,
                    "国内交易を提案",
                    "公開されている生産力と直近獲得から交渉相手を選びます",
                )
                game.propose_domestic_trade(partner, give, receive)
                return True

        trade = self._choose_bank_trade(game, player)
        if trade is not None:
            give_resource, receive_resource = trade
            self._set_status(game, player, "銀行交易", "余剰資源を次の建設に必要な資源へ交換します")
            game.start_bank_trade()
            game.select_bank_trade_resource(give_resource)
            game.select_bank_trade_resource(receive_resource)
            return True

        self._set_status(game, player, "手番を終了", "実行できる建設・購入・交易がありません")
        game.record_event(
            f"{player.name}が手番を終了",
            "建設・購入・交易できる行動がないため終了",
            actor=player,
        )
        game.finish_current_turn()
        return True

    def _step_initial(self, game):
        if game.initial_dice_phase:
            if not game.initial_dice_contenders:
                return False
            player = game.initial_dice_contenders[game.initial_player_index]
            if not player.is_ai:
                return False
            self._set_status(game, player, "初期ダイス", "配置順を決めます")
            game.handle_initial_key_roll()
            return True

        if not game.initial_placement_order:
            return False
        player = game.initial_placement_order[game.initial_player_index]
        if not player.is_ai:
            return False

        if not game.waiting_for_road:
            candidates = game.get_initial_settlement_candidates()
            if not candidates:
                return False
            node = max(candidates, key=lambda candidate: self._node_score(game, candidate, player))
            self._set_status(game, player, "初期開拓地を選択", "生産力・資源の多様性・港を評価します")
            game.handle_initial_placement((node.x, node.y))
            return True

        candidates = game.get_initial_road_candidates(player)
        if not candidates:
            return False
        edge = max(candidates, key=lambda candidate: self._edge_score(game, candidate, player))
        self._set_status(game, player, "初期街道を選択", "次の開拓地へ伸ばしやすい方向を選びます")
        destination = edge[1] if edge[0] is game.last_settlement_node else edge[0]
        game.handle_initial_placement((destination.x, destination.y))
        return True

    def _step_special(self, game, player):
        if game.special_phase == "move_robber":
            profile_key = self._profile(player).key
            tile = max(
                game.robber_tile_candidates,
                key=lambda candidate: self._robber_score(game, candidate, player),
            )
            detail = {
                STANDARD: "自分の生産を避け、得点上位を妨害します",
                EXPANSION: "自分の開拓網を守り、生産力の高い相手を抑えます",
                TRADER: "自分の生産を避け、公開手札が多い相手を狙います",
                DISRUPTOR: "得点上位の高確率タイルを優先して封鎖します",
            }[profile_key]
            self._set_status(game, player, "盗賊を移動", detail)
            game.relocate_robber(tile)
            return True

        if game.special_phase == "steal":
            profile_key = self._profile(player).key
            victim = max(
                game.robber_target_players,
                key=lambda candidate: self._steal_target_score(
                    game,
                    candidate,
                    player,
                ),
            )
            detail = {
                STANDARD: "公開得点が高い相手を優先します",
                EXPANSION: "公開得点と手札枚数を合わせて比較します",
                TRADER: "公開手札枚数が多い相手を優先します",
                DISRUPTOR: "公開得点と騎士数が高い相手を重点的に狙います",
            }[profile_key]
            self._set_status(game, player, "略奪相手を選択", detail)
            game.steal_random_resource(victim)
            game.complete_robber_phase()
            return True

        if game.special_phase == "year_of_plenty":
            resource_type = self._choose_year_of_plenty_resource(game, player)
            if resource_type is None:
                game.complete_resource_selection_if_bank_empty()
                return True
            self._set_status(game, player, "収穫の資源を選択", "優先建設に足りない資源を獲得します")
            game.handle_resource_selection(resource_type)
            return True

        if game.special_phase == "monopoly":
            resource_type = self._choose_monopoly_resource(game, player)
            self._set_status(game, player, "独占する資源を選択", "盤面の公開生産量から期待値を比較します")
            game.handle_resource_selection(resource_type)
            return True

        if game.special_phase == "road_building":
            edges = game.get_buildable_road_edges(player, require_affordability=False)
            if not edges:
                game.complete_road_building_phase()
                return True
            edge = max(edges, key=lambda candidate: self._edge_score(game, candidate, player))
            self._set_status(game, player, "無料の街道を建設", "開拓余地が広がる方向へ伸ばします")
            game.handle_free_road_build_click(self._edge_midpoint(edge))
            return True

        if game.special_phase in ("bank_trade_give", "bank_trade_receive"):
            game.cancel_selection()
            return True

        return False

    @staticmethod
    def _set_status(game, player, title, detail=""):
        game.set_ai_status(player, title, detail, log=True)

    def _play_development_card(self, game, player):
        profile = self._profile(player)
        monopoly_resource = self._choose_monopoly_resource(game, player)
        monopoly_public_score = self._monopoly_public_score(game, player, monopoly_resource)
        if (
            monopoly_public_score >= profile.monopoly_threshold
            and player.has_playable_development_card(DevelopmentCardType.MONOPOLY)
        ):
            self._set_status(game, player, "独占カードを使用", "公開生産量が多い資源を狙います")
            game.use_monopoly_card()
            return True

        if player.has_playable_development_card(DevelopmentCardType.YEAR_OF_PLENTY):
            for cost_name in profile.goal_order:
                missing = self._missing_cards(player, BUILD_COSTS[cost_name])
                if 0 < sum(missing.values()) <= 2 and all(
                    game.bank.available(resource_type) >= amount
                    for resource_type, amount in missing.items()
                ):
                    self._set_status(game, player, "収穫カードを使用", "上位の建設目標を完成させます")
                    game.use_year_of_plenty_card()
                    return True

        legal_road_count = len(
            game.get_buildable_road_edges(player, require_affordability=False)
        )
        if (
            player.has_playable_development_card(DevelopmentCardType.ROAD_BUILDING)
            and game.has_legal_road_placement(player)
            and legal_road_count
            >= min(profile.road_building_min_options, player.roads_remaining)
        ):
            self._set_status(game, player, "街道建設カードを使用", "2本の街道で開拓範囲を広げます")
            game.use_road_building_card()
            return True

        if (
            player.has_playable_development_card(DevelopmentCardType.KNIGHT)
            and self._should_play_knight(game, player)
        ):
            self._set_status(game, player, "騎士カードを使用", "最大騎士力または生産妨害を狙います")
            game.use_knight_card()
            return True
        return False

    def _should_play_knight(self, game, player):
        next_count = player.played_knights + 1
        owner = game.largest_army_owner
        can_claim_largest_army = next_count >= 3 and (
            owner is None
            or (owner is not player and next_count > owner.played_knights)
        )
        robber_blocks_self = bool(
            game.board.robber_tile
            and any(
                node.building is not None and node.building.owner is player
                for node in game.board.robber_tile.corners
            )
        )
        opponents = [candidate for candidate in game.players if candidate is not player]
        leader_threat = max(
            (game.get_player_public_victory_points(candidate) for candidate in opponents),
            default=0,
        ) >= self._profile(player).knight_leader_threshold
        return can_claim_largest_army or robber_blocks_self or leader_threat

    def _choose_bank_trade(
        self,
        game,
        player,
        goals=None,
    ):
        if goals is None:
            goals = self._profile(player).goal_order
        rates = game.get_trade_rates(player)
        for cost_name in goals:
            cost = BUILD_COSTS[cost_name]
            missing = self._missing_cards(player, cost)
            if sum(missing.values()) > 2:
                continue
            for receive_resource in missing:
                if game.bank.available(receive_resource) <= 0:
                    continue
                candidates = []
                for give_resource, rate in rates.items():
                    if give_resource == receive_resource:
                        continue
                    reserve = cost.get(give_resource, 0)
                    surplus = player.resources[give_resource] - reserve
                    if surplus >= rate:
                        candidates.append((surplus - rate, give_resource))
                if candidates:
                    return max(candidates, key=lambda candidate: candidate[0])[1], receive_resource
        return None

    def _choose_domestic_trade(
        self,
        game,
        player,
        goals=None,
    ):
        profile = self._profile(player)
        if goals is None:
            goals = profile.goal_order
        for cost_name in goals:
            cost = BUILD_COSTS[cost_name]
            missing = self._missing_cards(player, cost)
            if not missing:
                continue
            if sum(missing.values()) > profile.domestic_trade_max_missing:
                continue
            for receive_resource in missing:
                partners = sorted(
                    (
                        candidate
                        for candidate in game.players
                        if candidate is not player
                        and candidate.total_resource_count() > 0
                    ),
                    key=lambda candidate: self._public_trade_partner_score(
                        game,
                        candidate,
                        receive_resource,
                        requester=player,
                    ),
                    reverse=True,
                )
                for partner in partners:
                    give_candidates = []
                    for give_resource, amount in player.resources.items():
                        if give_resource == receive_resource:
                            continue
                        reserve = max(
                            0,
                            cost.get(give_resource, 0)
                            - profile.trade_reserve_relaxation,
                        )
                        surplus = amount - reserve
                        if surplus <= 0:
                            continue
                        give_candidates.append((surplus, amount, give_resource))
                    if give_candidates:
                        give_resource = max(give_candidates, key=lambda candidate: candidate[:2])[2]
                        return partner, {give_resource: 1}, {receive_resource: 1}
        return None

    def _public_trade_partner_score(
        self,
        game,
        partner,
        resource_type,
        *,
        requester=None,
    ):
        """Estimate supply from public facts, never from the partner's hand types."""
        production = self._player_production_scores(game, partner).get(
            resource_type,
            0,
        )
        recent_distribution = getattr(game, "last_resource_distribution", {})
        recent_bundle = recent_distribution.get(partner.name, {})
        recent_gain = recent_bundle.get(resource_type, 0)
        likelihood = production + recent_gain * 20
        leader_penalty = 0
        if requester is not None and self._profile(requester).key == DISRUPTOR:
            leader_penalty = game.get_player_public_victory_points(partner) * 25
        return (
            likelihood - leader_penalty,
            int(partner.is_ai),
            partner.total_resource_count(),
        )

    def evaluate_domestic_trade(self, player, *, incoming, outgoing):
        profile = self._profile(player)
        if not incoming or not outgoing:
            return "reject"
        if any(player.resources[resource_type] < amount for resource_type, amount in outgoing.items()):
            return "reject"
        before_resources = dict(player.resources)
        after_resources = dict(player.resources)
        for resource_type, amount in outgoing.items():
            after_resources[resource_type] -= amount
        for resource_type, amount in incoming.items():
            after_resources[resource_type] += amount

        protected_goals = ("city", "settlement")
        protected_before = {
            goal: self._resource_distance(before_resources, BUILD_COSTS[goal])
            for goal in protected_goals
        }
        protected_after = {
            goal: self._resource_distance(after_resources, BUILD_COSTS[goal])
            for goal in protected_goals
        }
        if any(
            protected_before[goal] == 0 and protected_after[goal] > 0
            for goal in protected_goals
        ):
            return "reject"

        priority_goals = profile.goal_order[:2]
        before_distances = {
            goal: self._resource_distance(before_resources, BUILD_COSTS[goal])
            for goal in priority_goals
        }
        after_distances = {
            goal: self._resource_distance(after_resources, BUILD_COSTS[goal])
            for goal in priority_goals
        }
        incoming_value = sum(
            self._trade_resource_value(player, resource_type) * amount
            for resource_type, amount in incoming.items()
        )
        outgoing_value = sum(
            self._trade_resource_value(player, resource_type) * amount
            for resource_type, amount in outgoing.items()
        )
        if outgoing_value <= 0:
            return "reject"
        improves_priority_goal = any(
            after_distances[goal] < before_distances[goal]
            for goal in priority_goals
        )
        completes_priority_goal = any(
            before_distances[goal] > 0 and after_distances[goal] == 0
            for goal in priority_goals
        )
        if (
            completes_priority_goal
            and incoming_value >= outgoing_value * profile.trade_complete_ratio
        ):
            return "accept"
        if (
            improves_priority_goal
            and incoming_value >= outgoing_value * profile.trade_improve_ratio
        ):
            return "accept"
        if incoming_value >= outgoing_value * profile.trade_fair_ratio:
            return "accept"
        if (
            improves_priority_goal
            or incoming_value >= outgoing_value * profile.trade_counter_ratio
        ):
            return "counter"
        return "reject"

    @staticmethod
    def _resource_distance(resources, cost):
        return sum(
            max(0, required - resources.get(resource_type, 0))
            for resource_type, required in cost.items()
        )

    def build_domestic_trade_counter(self, active_player, responding_player, give, receive):
        counter_give = {resource_type: give.get(resource_type, 0) for resource_type in active_player.resources}
        counter_receive = {resource_type: receive.get(resource_type, 0) for resource_type in active_player.resources}

        candidates = [
            (self._trade_resource_value(responding_player, resource_type), resource_type)
            for resource_type, amount in counter_give.items()
            if amount > 0 and counter_receive.get(resource_type, 0) == 0
        ]
        if candidates:
            resource_type = max(candidates, key=lambda candidate: candidate[0])[1]
            counter_give[resource_type] += 1
            return counter_give, counter_receive

        if sum(counter_receive.values()) > 1:
            removable = [
                resource_type
                for resource_type, amount in counter_receive.items()
                if amount > 0
            ]
            if removable:
                resource_type = min(
                    removable,
                    key=lambda candidate: self._trade_resource_value(responding_player, candidate),
                )
                counter_receive[resource_type] -= 1
                if sum(counter_receive.values()) > 0:
                    return counter_give, counter_receive
        return None

    def _trade_resource_value(self, player, resource_type):
        value = 10
        if player.resources[resource_type] == 0:
            value += 3
        profile_key = self._profile(player).key
        goal_weights = {
            STANDARD: ((6, "city"), (5, "settlement"), (3, "development"), (2, "road")),
            EXPANSION: ((7, "settlement"), (5, "road"), (4, "city"), (1, "development")),
            TRADER: ((6, "city"), (5, "settlement"), (3, "road"), (2, "development")),
            DISRUPTOR: ((7, "development"), (5, "city"), (3, "settlement"), (1, "road")),
        }[profile_key]
        for weight, cost_name in goal_weights:
            cost = BUILD_COSTS[cost_name]
            if cost.get(resource_type, 0) > player.resources[resource_type]:
                value += weight
        value -= min(4, max(0, player.resources[resource_type] - 2))
        return value

    def _choose_monopoly_resource(self, game, player):
        return max(
            player.resources,
            key=lambda resource_type: (
                self._monopoly_public_score(game, player, resource_type),
                self._trade_resource_value(player, resource_type),
            ),
        )

    def _monopoly_public_score(self, game, player, resource_type):
        score = 0
        for tile in game.board.tiles:
            if not game.is_frontier_tile_revealed(tile):
                continue
            if tile.resource_type != resource_type:
                continue
            pip_value = get_token_pip_count(tile.number)
            for node in tile.corners:
                if node.building is None or node.building.owner is player:
                    continue
                score += pip_value * node.building.resource_multiplier
        return score

    def _choose_year_of_plenty_resource(self, game, player):
        available = [
            resource_type
            for resource_type in player.resources
            if game.bank.available(resource_type) > 0
        ]
        if not available:
            return None

        for cost_name in self._profile(player).goal_order:
            missing = self._missing_cards(player, BUILD_COSTS[cost_name])
            for resource_type in missing:
                if resource_type in available:
                    return resource_type
        return min(available, key=lambda resource_type: player.resources[resource_type])

    def _choose_discard(self, player):
        preferred_goal = next(
            (
                goal
                for goal in self._profile(player).goal_order
                if goal != "city" or player.cities_remaining > 0
            ),
            "settlement",
        )
        keep_cost = BUILD_COSTS[preferred_goal]
        return max(
            player.resources,
            key=lambda resource_type: (
                player.resources[resource_type] - keep_cost.get(resource_type, 0),
                player.resources[resource_type],
            ),
        )

    def _player_production_scores(self, game, player):
        scores = {resource_type: 0 for resource_type in player.resources}
        for node in game.board.nodes:
            if node.building is None or node.building.owner is not player:
                continue
            for tile in game.get_public_node_tiles(node):
                if tile.resource_type == ResourceType.DESERT:
                    continue
                scores[tile.resource_type] += (
                    get_token_pip_count(tile.number)
                    * node.building.resource_multiplier
                )
        return scores

    def _resource_need_weights(self, game, player):
        public_points = game.get_player_public_victory_points(player)
        profile_key = self._profile(player).key
        if public_points < 5:
            weights = {
                STANDARD: {
                    ResourceType.WOOD: 1.25,
                    ResourceType.BRICK: 1.25,
                    ResourceType.SHEEP: 1.05,
                    ResourceType.WHEAT: 1.10,
                    ResourceType.ORE: 0.85,
                },
                EXPANSION: {
                    ResourceType.WOOD: 1.65,
                    ResourceType.BRICK: 1.65,
                    ResourceType.SHEEP: 1.10,
                    ResourceType.WHEAT: 1.05,
                    ResourceType.ORE: 0.65,
                },
                TRADER: {
                    ResourceType.WOOD: 1.20,
                    ResourceType.BRICK: 1.05,
                    ResourceType.SHEEP: 1.25,
                    ResourceType.WHEAT: 1.20,
                    ResourceType.ORE: 0.90,
                },
                DISRUPTOR: {
                    ResourceType.WOOD: 0.80,
                    ResourceType.BRICK: 0.75,
                    ResourceType.SHEEP: 1.35,
                    ResourceType.WHEAT: 1.45,
                    ResourceType.ORE: 1.35,
                },
            }[profile_key]
        else:
            weights = {
                STANDARD: {
                    ResourceType.WOOD: 0.85,
                    ResourceType.BRICK: 0.80,
                    ResourceType.SHEEP: 0.95,
                    ResourceType.WHEAT: 1.35,
                    ResourceType.ORE: 1.45,
                },
                EXPANSION: {
                    ResourceType.WOOD: 1.35,
                    ResourceType.BRICK: 1.30,
                    ResourceType.SHEEP: 1.05,
                    ResourceType.WHEAT: 1.10,
                    ResourceType.ORE: 1.00,
                },
                TRADER: {
                    ResourceType.WOOD: 1.00,
                    ResourceType.BRICK: 0.90,
                    ResourceType.SHEEP: 1.10,
                    ResourceType.WHEAT: 1.30,
                    ResourceType.ORE: 1.30,
                },
                DISRUPTOR: {
                    ResourceType.WOOD: 0.65,
                    ResourceType.BRICK: 0.60,
                    ResourceType.SHEEP: 1.35,
                    ResourceType.WHEAT: 1.45,
                    ResourceType.ORE: 1.55,
                },
            }[profile_key]
        active_event = getattr(
            game,
            "is_forecast_event_active",
            lambda _event: False,
        )
        next_event = getattr(game, "get_next_forecast_event_id", lambda: None)()
        if active_event(SHEEP_DROUGHT_EVENT_ID):
            weights[ResourceType.SHEEP] *= 0.35
        elif next_event == SHEEP_DROUGHT_EVENT_ID:
            weights[ResourceType.SHEEP] *= 0.80
        if (
            active_event(WHEAT_HARVEST_EVENT_ID)
            or next_event == WHEAT_HARVEST_EVENT_ID
        ):
            weights[ResourceType.WHEAT] *= 1.20
        production = self._player_production_scores(game, player)
        strongest = max(production.values(), default=0)
        for resource_type in weights:
            if strongest <= 0:
                weights[resource_type] += 0.35
            else:
                scarcity = (strongest - production[resource_type]) / strongest
                weights[resource_type] += scarcity * 0.70
        return weights

    def _node_score(self, game, node, player):
        profile = self._profile(player)
        need_weights = self._resource_need_weights(game, player)
        pip_score = sum(
            get_token_pip_count(tile.number) * need_weights[tile.resource_type]
            for tile in game.get_public_node_tiles(node)
            if tile.resource_type != ResourceType.DESERT
        )
        resources = {
            tile.resource_type
            for tile in game.get_public_node_tiles(node)
            if tile.resource_type != ResourceType.DESERT
        }
        production = self._player_production_scores(game, player)
        harbor_bonus = 0
        for harbor in game.get_public_node_harbors(node):
            if harbor.resource_type is None:
                harbor_bonus = max(harbor_bonus, profile.generic_harbor_bonus)
            else:
                synergy = min(6, production.get(harbor.resource_type, 0) * 0.4)
                harbor_bonus = max(
                    harbor_bonus,
                    profile.specific_harbor_bonus + synergy,
                )
        return (
            pip_score * profile.pip_weight
            + len(resources) * profile.diversity_weight
            + harbor_bonus
        )

    def _edge_score(self, game, edge, player):
        profile = self._profile(player)
        open_nodes = [node for node in edge if node.building is None]
        if not open_nodes:
            return -50
        score = max(self._node_score(game, node, player) for node in open_nodes)
        for node in open_nodes:
            if game.is_spacing_rule_satisfied(node):
                score += profile.edge_spacing_bonus
            lookahead = [
                adjacent
                for adjacent in game.get_adjacent_nodes(node)
                if adjacent.building is None
            ]
            if lookahead:
                score += max(
                    self._node_score(game, adjacent, player)
                    for adjacent in lookahead
                ) * profile.edge_lookahead_weight
            if profile.opponent_contact_bonus:
                opponent_contacts = sum(
                    adjacent.building is not None
                    and adjacent.building.owner is not player
                    for adjacent in game.get_adjacent_nodes(node)
                )
                score += opponent_contacts * profile.opponent_contact_bonus
        if game.get_player_longest_road_length(player) >= 4:
            score += profile.longest_road_bonus
        discovery_count = getattr(
            game,
            "get_frontier_edge_discovery_count",
            lambda _edge: 0,
        )(edge)
        score += discovery_count * 10
        return score

    def _robber_score(self, game, tile, player):
        profile = self._profile(player)
        score = 0
        pip_value = max(1, get_token_pip_count(tile.number))
        opponent_points = {
            candidate: game.get_player_public_victory_points(candidate)
            for candidate in game.players
            if candidate is not player
        }
        leading_score = max(opponent_points.values(), default=0)
        for node in tile.corners:
            if node.building is None:
                continue
            value = node.building.resource_multiplier * pip_value
            if node.building.owner is player:
                score -= value * profile.robber_self_penalty
            else:
                public_points = opponent_points.get(node.building.owner, 0)
                leader_bonus = (
                    profile.robber_leader_bonus
                    if public_points == leading_score and leading_score > 0
                    else 0
                )
                score += (
                    value * profile.robber_production_weight
                    + public_points * profile.robber_point_weight
                    + leader_bonus
                    + node.building.owner.total_resource_count()
                    * profile.robber_hand_weight
                )
        return score

    def _steal_target_score(self, game, candidate, player):
        public_points = game.get_player_public_victory_points(candidate)
        public_hand_size = candidate.total_resource_count()
        profile_key = self._profile(player).key
        if profile_key == TRADER:
            return public_hand_size, public_points
        if profile_key == DISRUPTOR:
            return (
                public_points * 10
                + min(public_hand_size, 4)
                + candidate.played_knights * 3,
                public_points,
                public_hand_size,
            )
        if profile_key == EXPANSION:
            return public_points * 2 + public_hand_size, public_points, public_hand_size
        return public_points, public_hand_size

    def _missing_cards(self, player, cost):
        return {
            resource_type: amount - player.resources.get(resource_type, 0)
            for resource_type, amount in cost.items()
            if player.resources.get(resource_type, 0) < amount
        }

    def _edge_midpoint(self, edge):
        node1, node2 = edge
        return ((node1.x + node2.x) / 2, (node1.y + node2.y) / 2)

    @staticmethod
    def _profile(player):
        return get_ai_personality_profile(
            getattr(player, "ai_personality", STANDARD)
        )
