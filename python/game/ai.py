from game.development_cards import DevelopmentCardType
from game.hex_tile import get_token_pip_count
from game.resources import BUILD_COSTS, ResourceType


AI_ACTION_DELAY_MS = 720


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
            game.discard_resource(self._choose_discard(game.discard_player))
            return True

        player = game.get_current_player()
        if player is None or not player.is_ai:
            return False

        if game.special_phase is not None:
            return self._step_special(game, player)

        if not game.dice_rolled:
            game.handle_roll_dice()
            return True

        if not game.development_card_used_this_turn and self._play_development_card(game, player):
            return True

        city_nodes = game.get_buildable_city_nodes(player)
        if city_nodes:
            node = max(city_nodes, key=lambda candidate: self._node_score(candidate, player))
            game.build_city((node.x, node.y))
            return True

        settlement_nodes = game.get_buildable_settlement_nodes(player)
        if settlement_nodes:
            node = max(settlement_nodes, key=lambda candidate: self._node_score(candidate, player))
            game.build_settlement((node.x, node.y))
            return True

        road_edges = game.get_buildable_road_edges(player)
        if road_edges:
            edge = max(road_edges, key=lambda candidate: self._edge_score(candidate, player))
            game.build_road(self._edge_midpoint(edge))
            return True

        if game.development_deck and player.can_afford(BUILD_COSTS["development"]):
            game.buy_development_card()
            return True

        if not game.ai_domestic_trade_attempted:
            game.ai_domestic_trade_attempted = True
            domestic_trade = self._choose_domestic_trade(game, player)
            if domestic_trade is not None:
                partner, give, receive = domestic_trade
                game.propose_domestic_trade(partner, give, receive)
                return True

        trade = self._choose_bank_trade(game, player)
        if trade is not None:
            give_resource, receive_resource = trade
            game.start_bank_trade()
            game.select_bank_trade_resource(give_resource)
            game.select_bank_trade_resource(receive_resource)
            return True

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
            node = max(candidates, key=lambda candidate: self._node_score(candidate, player))
            game.handle_initial_placement((node.x, node.y))
            return True

        candidates = game.get_initial_road_candidates(player)
        if not candidates:
            return False
        edge = max(candidates, key=lambda candidate: self._edge_score(candidate, player))
        destination = edge[1] if edge[0] is game.last_settlement_node else edge[0]
        game.handle_initial_placement((destination.x, destination.y))
        return True

    def _step_special(self, game, player):
        if game.special_phase == "move_robber":
            tile = max(game.robber_tile_candidates, key=lambda candidate: self._robber_score(candidate, player))
            game.relocate_robber(tile)
            return True

        if game.special_phase == "steal":
            victim = max(game.robber_target_players, key=lambda candidate: candidate.total_resource_count())
            game.steal_random_resource(victim)
            game.complete_robber_phase()
            return True

        if game.special_phase == "year_of_plenty":
            resource_type = self._choose_year_of_plenty_resource(game, player)
            if resource_type is None:
                game.complete_resource_selection_if_bank_empty()
                return True
            game.handle_resource_selection(resource_type)
            return True

        if game.special_phase == "monopoly":
            resource_type = self._choose_monopoly_resource(game, player)
            game.handle_resource_selection(resource_type)
            return True

        if game.special_phase == "road_building":
            edges = game.get_buildable_road_edges(player, require_affordability=False)
            if not edges:
                game.complete_road_building_phase()
                return True
            edge = max(edges, key=lambda candidate: self._edge_score(candidate, player))
            game.handle_free_road_build_click(self._edge_midpoint(edge))
            return True

        if game.special_phase in ("bank_trade_give", "bank_trade_receive"):
            game.cancel_selection()
            return True

        return False

    def _play_development_card(self, game, player):
        monopoly_resource = self._choose_monopoly_resource(game, player)
        monopoly_public_score = self._monopoly_public_score(game, player, monopoly_resource)
        if (
            monopoly_public_score >= 8
            and player.has_playable_development_card(DevelopmentCardType.MONOPOLY)
        ):
            game.use_monopoly_card()
            return True

        if player.has_playable_development_card(DevelopmentCardType.YEAR_OF_PLENTY):
            for cost_name in ("city", "settlement", "development", "road"):
                missing = self._missing_cards(player, BUILD_COSTS[cost_name])
                if 0 < sum(missing.values()) <= 2 and all(
                    game.bank.available(resource_type) >= amount
                    for resource_type, amount in missing.items()
                ):
                    game.use_year_of_plenty_card()
                    return True

        if (
            player.has_playable_development_card(DevelopmentCardType.ROAD_BUILDING)
            and game.has_legal_road_placement(player)
        ):
            game.use_road_building_card()
            return True

        if player.has_playable_development_card(DevelopmentCardType.KNIGHT):
            game.use_knight_card()
            return True
        return False

    def _choose_bank_trade(self, game, player):
        rates = game.get_trade_rates(player)
        for cost_name in ("city", "settlement", "development", "road"):
            cost = BUILD_COSTS[cost_name]
            missing = self._missing_cards(player, cost)
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

    def _choose_domestic_trade(self, game, player):
        for cost_name in ("city", "settlement", "development", "road"):
            cost = BUILD_COSTS[cost_name]
            missing = self._missing_cards(player, cost)
            if not missing:
                continue
            for receive_resource in missing:
                partners = sorted(
                    (candidate for candidate in game.players if candidate is not player),
                    key=lambda candidate: (not candidate.is_ai, -candidate.total_resource_count()),
                )
                for partner in partners:
                    if partner is player or partner.total_resource_count() <= 0:
                        continue
                    give_candidates = []
                    for give_resource, amount in player.resources.items():
                        if give_resource == receive_resource:
                            continue
                        reserve = cost.get(give_resource, 0)
                        surplus = amount - reserve
                        if surplus <= 0:
                            continue
                        give_candidates.append((surplus, amount, give_resource))
                    if give_candidates:
                        give_resource = max(give_candidates, key=lambda candidate: candidate[:2])[2]
                        return partner, {give_resource: 1}, {receive_resource: 1}
        return None

    def evaluate_domestic_trade(self, player, *, incoming, outgoing):
        if not incoming or not outgoing:
            return "reject"
        if any(player.resources[resource_type] < amount for resource_type, amount in outgoing.items()):
            return "reject"
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
        if incoming_value >= outgoing_value:
            return "accept"
        if incoming_value >= outgoing_value * 0.60:
            return "counter"
        return "reject"

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
        for weight, cost_name in ((6, "city"), (5, "settlement"), (3, "development"), (2, "road")):
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

        for cost_name in ("city", "settlement", "development", "road"):
            missing = self._missing_cards(player, BUILD_COSTS[cost_name])
            for resource_type in missing:
                if resource_type in available:
                    return resource_type
        return min(available, key=lambda resource_type: player.resources[resource_type])

    def _choose_discard(self, player):
        keep_cost = BUILD_COSTS["city"] if player.cities_remaining > 0 else BUILD_COSTS["settlement"]
        return max(
            player.resources,
            key=lambda resource_type: (
                player.resources[resource_type] - keep_cost.get(resource_type, 0),
                player.resources[resource_type],
            ),
        )

    def _node_score(self, node, player):
        pips = sum(
            get_token_pip_count(tile.number)
            for tile in node.tiles
            if tile.resource_type != ResourceType.DESERT
        )
        resources = {
            tile.resource_type
            for tile in node.tiles
            if tile.resource_type != ResourceType.DESERT
        }
        harbor_bonus = 3 if node.harbors else 0
        return pips * 4 + len(resources) * 5 + harbor_bonus

    def _edge_score(self, edge, player):
        score = 0
        for node in edge:
            if node.building is None:
                score = max(score, self._node_score(node, player))
        return score

    def _robber_score(self, tile, player):
        score = 0
        pip_value = max(1, get_token_pip_count(tile.number))
        for node in tile.corners:
            if node.building is None:
                continue
            value = node.building.resource_multiplier * pip_value
            if node.building.owner is player:
                score -= value * 5
            else:
                score += value * 4 + node.building.owner.total_resource_count()
        return score

    def _missing_cards(self, player, cost):
        return {
            resource_type: amount - player.resources.get(resource_type, 0)
            for resource_type, amount in cost.items()
            if player.resources.get(resource_type, 0) < amount
        }

    def _edge_midpoint(self, edge):
        node1, node2 = edge
        return ((node1.x + node2.x) / 2, (node1.y + node2.y) / 2)
