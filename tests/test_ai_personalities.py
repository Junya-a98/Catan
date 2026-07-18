from types import SimpleNamespace

from game.ai import SimpleAI
from game.ai_personality import (
    AI_PERSONALITY_KEYS,
    AI_PERSONALITY_MODES,
    DISRUPTOR,
    EXPANSION,
    MIXED,
    STANDARD,
    TRADER,
    get_ai_personality_profile,
    normalize_ai_personality,
    normalize_ai_personality_mode,
)
from game.game import CatanGame
from game.forecast_events import (
    BANDIT_RAID_EVENT_ID,
    CONSTRUCTION_BOOM_EVENT_ID,
    MERCHANT_FESTIVAL_EVENT_ID,
)
from game.persistence import restore_game, serialize_game
from game.player import Player
from game.resources import ResourceType


def _player(name="CPU", personality=STANDARD):
    return Player(
        name,
        (120, 140, 160),
        is_ai=True,
        ai_personality=personality,
    )


def test_personality_public_api_normalizes_aliases_and_safe_defaults():
    assert AI_PERSONALITY_KEYS == (STANDARD, EXPANSION, TRADER, DISRUPTOR)
    assert AI_PERSONALITY_MODES == (STANDARD, MIXED, EXPANSION, TRADER, DISRUPTOR)
    assert normalize_ai_personality(" Builder ") == EXPANSION
    assert normalize_ai_personality("blocker") == DISRUPTOR
    assert normalize_ai_personality("unknown") == STANDARD
    assert normalize_ai_personality_mode("trader") == TRADER
    assert normalize_ai_personality_mode("unknown") == STANDARD
    assert get_ai_personality_profile(TRADER).label == "交渉重視"
    assert _player(personality="unknown").ai_personality == STANDARD


class _BuildChoiceGame:
    def __init__(self, player):
        self.player = player
        self.winner = None
        self.phase = "main"
        self.special_phase = None
        self.dice_rolled = True
        self.development_card_used_this_turn = True
        self.city_node = SimpleNamespace(x=10, y=20)
        self.settlement_node = SimpleNamespace(x=30, y=40)
        self.built = None

    @staticmethod
    def has_active_dice_animation():
        return False

    def get_current_player(self):
        return self.player

    def get_buildable_city_nodes(self, _player):
        return [self.city_node]

    def get_buildable_settlement_nodes(self, _player):
        return [self.settlement_node]

    def build_city(self, position):
        self.built = "city", position

    def build_settlement(self, position):
        self.built = "settlement", position

    @staticmethod
    def set_ai_status(*_args, **_kwargs):
        return None


def test_expansion_changes_legal_build_priority_without_a_separate_ai_instance(monkeypatch):
    ai = SimpleAI()
    monkeypatch.setattr(ai, "_node_score", lambda *_args: 1)

    standard = _player(personality=STANDARD)
    standard_game = _BuildChoiceGame(standard)
    assert ai.step(standard_game) is True
    assert standard_game.built[0] == "city"

    expansion = _player(personality=EXPANSION)
    expansion_game = _BuildChoiceGame(expansion)
    assert ai.step(expansion_game) is True
    assert expansion_game.built[0] == "settlement"


class _DevelopmentChoiceGame(_BuildChoiceGame):
    def __init__(self, player):
        super().__init__(player)
        self.ai_domestic_trade_attempted = True
        self.development_deck = [object()]
        self.road_edge = (
            SimpleNamespace(x=0, y=0),
            SimpleNamespace(x=2, y=0),
        )
        self.action = None

    def get_buildable_city_nodes(self, _player):
        return []

    def get_buildable_settlement_nodes(self, _player):
        return []

    def get_buildable_road_edges(self, _player):
        return [self.road_edge]

    @staticmethod
    def get_player_longest_road_length(_player):
        return 2

    def buy_development_card(self):
        self.action = "development"

    def build_road(self, _position):
        self.action = "road"


def test_disruptor_buys_development_before_an_available_road(monkeypatch):
    ai = SimpleAI()
    monkeypatch.setattr(ai, "_edge_score", lambda *_args: 60)
    monkeypatch.setattr(ai, "_choose_bank_trade", lambda *_args, **_kwargs: None)

    standard = _player(personality=STANDARD)
    for resource_type in (ResourceType.ORE, ResourceType.SHEEP, ResourceType.WHEAT):
        standard.add_resource(resource_type)
    standard_game = _DevelopmentChoiceGame(standard)
    assert ai.step(standard_game) is True
    assert standard_game.action == "road"

    disruptor = _player(personality=DISRUPTOR)
    for resource_type in (ResourceType.ORE, ResourceType.SHEEP, ResourceType.WHEAT):
        disruptor.add_resource(resource_type)
    disruptor_game = _DevelopmentChoiceGame(disruptor)
    assert ai.step(disruptor_game) is True
    assert disruptor_game.action == "development"


def test_construction_boom_makes_ai_use_the_shared_discounted_road_action(
    monkeypatch,
):
    ai = SimpleAI()
    monkeypatch.setattr(ai, "_edge_score", lambda *_args: 60)
    player = _player(personality=DISRUPTOR)
    game = _DevelopmentChoiceGame(player)
    game.is_forecast_event_active = (
        lambda event_id: event_id == CONSTRUCTION_BOOM_EVENT_ID
    )

    assert ai.step(game) is True
    assert game.action == "road"


def test_merchant_festival_lowers_ai_acceptance_threshold_for_legal_trade():
    ai = SimpleAI()
    responder = _player("応答者")
    responder.resources.update(
        {
            ResourceType.WOOD: 0,
            ResourceType.SHEEP: 1,
            ResourceType.WHEAT: 1,
            ResourceType.BRICK: 1,
            ResourceType.ORE: 2,
        }
    )
    incoming = {ResourceType.WOOD: 1}
    outgoing = {ResourceType.ORE: 2}
    festival_game = SimpleNamespace(
        is_forecast_event_active=(
            lambda event_id: event_id == MERCHANT_FESTIVAL_EVENT_ID
        )
    )

    assert ai.evaluate_domestic_trade(
        responder,
        incoming=incoming,
        outgoing=outgoing,
    ) == "counter"
    assert ai.evaluate_domestic_trade(
        responder,
        incoming=incoming,
        outgoing=outgoing,
        game=festival_game,
    ) == "accept"


def test_ai_avoids_announced_earthquake_edges_and_bandit_raid_numbers(
    monkeypatch,
):
    ai = SimpleAI()
    player = _player()
    open_edge = (
        SimpleNamespace(building=None),
        SimpleNamespace(building=None),
    )
    quake_edge = (
        SimpleNamespace(building=None),
        SimpleNamespace(building=None),
    )
    edge_game = SimpleNamespace(
        is_spacing_rule_satisfied=lambda _node: False,
        get_adjacent_nodes=lambda _node: [],
        get_player_longest_road_length=lambda _player: 0,
        get_frontier_edge_discovery_count=lambda _edge: 0,
        is_forecast_edge_announced=lambda edge: edge is quake_edge,
    )
    monkeypatch.setattr(ai, "_node_score", lambda *_args: 10)

    assert ai._edge_score(edge_game, open_edge, player) > ai._edge_score(
        edge_game,
        quake_edge,
        player,
    )

    safe_tile = SimpleNamespace(number=6, corners=[])
    raid_tile = SimpleNamespace(number=8, corners=[])
    raid_game = SimpleNamespace(
        players=[player],
        get_player_public_victory_points=lambda _player: 0,
        get_forecast_event_parameters=(
            lambda event_id: {"target_number": 8}
            if event_id == BANDIT_RAID_EVENT_ID
            else {}
        ),
    )
    assert ai._robber_score(raid_game, safe_tile, player) > ai._robber_score(
        raid_game,
        raid_tile,
        player,
    )


def test_ai_does_not_value_a_harbor_that_is_announced_for_blockade(monkeypatch):
    ai = SimpleAI()
    player = _player()
    node = SimpleNamespace()
    tile = SimpleNamespace(resource_type=ResourceType.WOOD, number=6)
    harbor = SimpleNamespace(resource_type=None)
    announced = {"value": False}
    game = SimpleNamespace(
        get_public_node_tiles=lambda _node: [tile],
        get_public_node_harbors=lambda _node: [harbor],
        is_forecast_harbor_announced=lambda _harbor: announced["value"],
    )
    monkeypatch.setattr(
        ai,
        "_resource_need_weights",
        lambda _game, _player: {resource_type: 1.0 for resource_type in ResourceType},
    )
    monkeypatch.setattr(
        ai,
        "_player_production_scores",
        lambda _game, _player: {resource_type: 0 for resource_type in ResourceType},
    )

    open_score = ai._node_score(game, node, player)
    announced["value"] = True
    blocked_score = ai._node_score(game, node, player)

    assert open_score > blocked_score


def test_trader_proposes_earlier_and_accepts_a_wider_but_legal_offer():
    ai = SimpleAI()
    proposer = _player("提案者")
    partner = _player("相手")
    proposer.resources.update(
        {
            ResourceType.WOOD: 2,
            ResourceType.SHEEP: 0,
            ResourceType.WHEAT: 0,
            ResourceType.BRICK: 0,
            ResourceType.ORE: 0,
        }
    )
    partner.add_resource(ResourceType.ORE)
    game = SimpleNamespace(
        players=[proposer, partner],
        board=SimpleNamespace(nodes=[]),
        last_resource_distribution={},
    )

    proposer.ai_personality = STANDARD
    assert ai._choose_domestic_trade(game, proposer, goals=("settlement",)) is None
    proposer.ai_personality = TRADER
    assert ai._choose_domestic_trade(game, proposer, goals=("settlement",)) is not None

    responder = _player("応答者")
    responder.resources.update(
        {
            ResourceType.WOOD: 0,
            ResourceType.SHEEP: 1,
            ResourceType.WHEAT: 1,
            ResourceType.BRICK: 1,
            ResourceType.ORE: 2,
        }
    )
    incoming = {ResourceType.WOOD: 1}
    outgoing = {ResourceType.ORE: 2}
    responder.ai_personality = STANDARD
    assert ai.evaluate_domestic_trade(responder, incoming=incoming, outgoing=outgoing) == "counter"
    responder.ai_personality = TRADER
    assert ai.evaluate_domestic_trade(responder, incoming=incoming, outgoing=outgoing) == "accept"
    responder.ai_personality = DISRUPTOR
    assert ai.evaluate_domestic_trade(responder, incoming=incoming, outgoing=outgoing) == "reject"


def test_disruptor_plays_knight_against_an_earlier_leader_threat():
    ai = SimpleAI()
    active = _player("active")
    opponent = _player("leader")
    game = SimpleNamespace(
        largest_army_owner=None,
        board=SimpleNamespace(robber_tile=None),
        players=[active, opponent],
        get_player_public_victory_points=lambda player: 4 if player is opponent else 2,
    )

    active.ai_personality = STANDARD
    assert ai._should_play_knight(game, active) is False
    active.ai_personality = DISRUPTOR
    assert ai._should_play_knight(game, active) is True


def test_personalities_choose_different_initial_nodes_and_robbery_targets():
    game = CatanGame(
        headless=True,
        board_seed=1,
        ai_player_count=1,
        ai_action_delay_ms=0,
    )
    player = game.players[-1]
    candidates = game.get_initial_settlement_candidates()
    choices = {}
    for personality in AI_PERSONALITY_KEYS:
        player.ai_personality = personality
        node = max(
            candidates,
            key=lambda candidate: game.ai._node_score(game, candidate, player),
        )
        choices[personality] = (round(node.x, 1), round(node.y, 1))

    assert choices[STANDARD] != choices[EXPANSION]
    assert choices[STANDARD] != choices[TRADER]

    leader = _player("leader")
    rich = _player("rich")
    leader.add_resource(ResourceType.WOOD)
    for _ in range(5):
        rich.add_resource(ResourceType.SHEEP)
    public_points = {leader: 3, rich: 2}
    robbery_game = SimpleNamespace(
        get_player_public_victory_points=lambda candidate: public_points[candidate]
    )
    player.ai_personality = TRADER
    trader_target = max(
        (leader, rich),
        key=lambda candidate: game.ai._steal_target_score(
            robbery_game, candidate, player
        ),
    )
    player.ai_personality = DISRUPTOR
    disruptor_target = max(
        (leader, rich),
        key=lambda candidate: game.ai._steal_target_score(
            robbery_game, candidate, player
        ),
    )
    assert trader_target is rich
    assert disruptor_target is leader


def test_expansion_road_lookahead_and_disruptor_robber_pressure_change_choices(
    monkeypatch,
):
    ai = SimpleAI()
    active = _player("active")
    future = SimpleNamespace(score=100, building=None)
    expansion_node = SimpleNamespace(score=10, building=None)
    direct_node = SimpleNamespace(score=40, building=None)
    game = SimpleNamespace(
        is_spacing_rule_satisfied=lambda node: node is expansion_node,
        get_adjacent_nodes=lambda node: [future] if node is expansion_node else [],
        get_player_longest_road_length=lambda _player: 0,
    )
    monkeypatch.setattr(ai, "_node_score", lambda _game, node, _player: node.score)
    expansion_edge = (expansion_node,)
    direct_edge = (direct_node,)

    active.ai_personality = STANDARD
    standard_choice = max(
        (expansion_edge, direct_edge),
        key=lambda edge: ai._edge_score(game, edge, active),
    )
    active.ai_personality = EXPANSION
    expansion_choice = max(
        (expansion_edge, direct_edge),
        key=lambda edge: ai._edge_score(game, edge, active),
    )
    assert standard_choice is direct_edge
    assert expansion_choice is expansion_edge

    leader = _player("leader")
    rich = _player("rich")
    leader.add_resource(ResourceType.WOOD)
    for _ in range(8):
        rich.add_resource(ResourceType.SHEEP)
    leader_tile = SimpleNamespace(
        number=3,
        corners=[
            SimpleNamespace(
                building=SimpleNamespace(owner=leader, resource_multiplier=2)
            )
        ],
    )
    rich_tile = SimpleNamespace(
        number=8,
        corners=[
            SimpleNamespace(
                building=SimpleNamespace(owner=rich, resource_multiplier=1)
            )
        ],
    )
    points = {active: 2, leader: 4, rich: 2}
    robber_game = SimpleNamespace(
        players=[active, leader, rich],
        get_player_public_victory_points=lambda player: points[player],
    )
    active.ai_personality = STANDARD
    assert max(
        (leader_tile, rich_tile),
        key=lambda tile: ai._robber_score(robber_game, tile, active),
    ) is rich_tile
    active.ai_personality = DISRUPTOR
    assert max(
        (leader_tile, rich_tile),
        key=lambda tile: ai._robber_score(robber_game, tile, active),
    ) is leader_tile


def test_personality_round_trip_and_legacy_save_defaults_are_compatible():
    game = CatanGame(
        headless=True,
        board_seed=101,
        ai_player_count=1,
        ai_action_delay_ms=0,
    )
    game.ai_personality_mode = DISRUPTOR
    game.players[-1].ai_personality = DISRUPTOR
    data = serialize_game(game)

    game.ai_personality_mode = MIXED
    game.players[-1].ai_personality = STANDARD
    restore_game(game, data, runtime_side_effects=False)
    assert game.ai_personality_mode == DISRUPTOR
    assert game.players[-1].ai_personality == DISRUPTOR

    legacy = serialize_game(game)
    legacy["ai"].pop("personality_mode")
    for player_data in legacy["players"]:
        player_data.pop("ai_personality")
    restore_game(game, legacy, runtime_side_effects=False)
    assert game.ai_personality_mode == STANDARD
    assert all(player.ai_personality == STANDARD for player in game.players)
