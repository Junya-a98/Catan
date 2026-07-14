"""Deterministic, headless AI self-play using the production game rules.

The simulator deliberately drives :class:`game.game.CatanGame` instead of
reimplementing its rules.  Rendering, audio, dice animation, replay recording,
delays, and stdout logging are disabled by the small runtime adapter below.

Seat numbers exposed by this module are one-based because they are intended
for reports and dashboards.  ``turns`` and ``dice_counts`` only include normal
game turns; the initial order rolls are excluded.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import threading
from dataclasses import dataclass
from statistics import fmean
from typing import Callable, Iterable

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import game.game as game_module
from game.ai_personality import AI_PERSONALITY_KEYS
from game.bank import BANK_RESOURCE_COUNT, RESOURCE_TYPES
from game.building import BuildingType
from game.constants import MAX_VICTORY_POINT_TARGET, MIN_VICTORY_POINT_TARGET
from game.hex_tile import get_token_pip_count
from game.resources import ResourceType


DEFAULT_GAME_COUNT = 20
DEFAULT_MAX_TURNS = 1_000
DEFAULT_MAX_ACTION_STEPS = 50_000
SUPPORTED_PLAYER_COUNTS = (2, 3, 4)
SUPPORTED_BOARD_MODES = ("constrained", "fully_random")
SUPPORTED_AI_PERSONALITIES = AI_PERSONALITY_KEYS
ACTION_COUNT_KEYS = (
    "domestic_trade_offers",
    "domestic_trades_completed",
    "bank_trades",
    "robber_moves",
    "knights_used",
)

_RUN_LOCK = threading.RLock()


class _HeadlessCatanGame(game_module.CatanGame):
    """Production game with only presentation/timing side effects removed."""

    _LOG_LIMIT = 40

    def __init__(self, *args, **kwargs):
        self.self_play_turns = 0
        self.self_play_dice_counts = {total: 0 for total in range(2, 13)}
        self.self_play_action_counts = {}
        self.initial_placement_metrics = {}
        # ``headless=True`` is the explicit production integration point: it
        # creates neither a display nor an audio backend and skips replay/UI
        # setup.  This subclass only adds counters and instant-step behavior.
        super().__init__(*args, headless=True, **kwargs)

    def add_log(self, message):
        """Keep a small diagnostic tail without printing thousands of lines."""
        if not hasattr(self, "log_messages"):
            return
        self.log_messages.append(str(message))
        if len(self.log_messages) > self._LOG_LIMIT:
            del self.log_messages[: -self._LOG_LIMIT]
        self.log_scroll_offset = 0

    def notify(self, message, *, level="info", log=True, transient=True):
        del level, transient
        if log:
            self.add_log(message)

    def play_sound(self, _sound_name):
        return None

    def schedule_ai_action(self, delay_multiplier=1.0):
        del delay_multiplier
        self.ai_next_action_at = 0

    def has_active_dice_animation(self):
        return False

    def start_dice_animation(self, context, dice_values, player_name, title):
        del player_name, title
        dice_total = sum(dice_values)
        if context == "initial":
            self.resolve_initial_key_roll(dice_total)
            return
        if context == "main":
            self.self_play_turns += 1
            self.self_play_dice_counts[dice_total] += 1
            self.resolve_main_dice_roll(dice_total)

    def start_main_phase(self, previous_player=None):
        metrics = {}
        for player in self.players:
            nodes = [
                node
                for node in self.board.nodes
                if node.building is not None and node.building.owner is player
            ]
            producing_tiles = [
                tile
                for node in nodes
                for tile in node.tiles
                if tile.resource_type != ResourceType.DESERT
            ]
            metrics[player] = {
                "pips": sum(get_token_pip_count(tile.number) for tile in producing_tiles),
                "high_probability_access": any(
                    tile.number in (6, 8) for tile in producing_tiles
                ),
                "resource_diversity": len(
                    {tile.resource_type for tile in producing_tiles}
                ),
            }
        self.initial_placement_metrics = metrics
        super().start_main_phase(previous_player=previous_player)

    def _count_action(self, player, action):
        if player is None:
            return
        counts = self.self_play_action_counts.setdefault(
            player, {key: 0 for key in ACTION_COUNT_KEYS}
        )
        counts[action] += 1

    def submit_domestic_trade_offer(self):
        proposer = self.get_current_player()
        is_original_offer = (
            self.special_phase == "domestic_trade_edit"
            and not self.domestic_trade_is_counter
        )
        result = super().submit_domestic_trade_offer()
        if result and is_original_offer:
            self._count_action(proposer, "domestic_trade_offers")
        return result

    def execute_domestic_trade(self):
        proposer = self.get_current_player()
        result = super().execute_domestic_trade()
        if result:
            self._count_action(proposer, "domestic_trades_completed")
        return result

    def select_bank_trade_resource(self, resource_type):
        player = self.get_current_player()
        completing_trade = self.special_phase == "bank_trade_receive"
        resource_count_before = (
            player.total_resource_count() if player is not None else None
        )
        result = super().select_bank_trade_resource(resource_type)
        if (
            completing_trade
            and player is not None
            and self.special_phase is None
            and player.total_resource_count() != resource_count_before
        ):
            self._count_action(player, "bank_trades")
        return result

    def relocate_robber(self, tile):
        player = self.get_current_player()
        result = super().relocate_robber(tile)
        self._count_action(player, "robber_moves")
        return result

    def use_knight_card(self):
        player = self.get_current_player()
        played_before = player.played_knights if player is not None else None
        result = super().use_knight_card()
        if player is not None and player.played_knights != played_before:
            self._count_action(player, "knights_used")
        return result

    def reset_replay_recording(self):
        self.replay_recorder = None
        self.replay_pending_capture = None
        return True

    def flush_replay_capture(self, *, force_latest=False):
        del force_latest
        return False

    def save_completed_replay(self):
        return None

    def refresh_latest_replay_path(self):
        self.latest_replay_path = None
        return None


@dataclass(frozen=True)
class PlayerResult:
    """Final state for one one-based seat."""

    seat: int
    name: str
    personality: str
    action_counts: dict[str, int]
    victory_points: int
    public_victory_points: int
    resources: dict[str, int]
    resource_cards: int
    roads: int
    settlements: int
    cities: int
    played_knights: int
    longest_road: bool
    largest_army: bool
    initial_pips: int
    initial_high_probability_access: bool
    initial_resource_diversity: int
    won: bool

    def to_dict(self) -> dict:
        return {
            "seat": self.seat,
            "name": self.name,
            "personality": self.personality,
            "action_counts": dict(self.action_counts),
            "victory_points": self.victory_points,
            "public_victory_points": self.public_victory_points,
            "resources": dict(self.resources),
            "resource_cards": self.resource_cards,
            "roads": self.roads,
            "settlements": self.settlements,
            "cities": self.cities,
            "played_knights": self.played_knights,
            "longest_road": self.longest_road,
            "largest_army": self.largest_army,
            "initial_pips": self.initial_pips,
            "initial_high_probability_access": self.initial_high_probability_access,
            "initial_resource_diversity": self.initial_resource_diversity,
            "won": self.won,
        }


@dataclass(frozen=True)
class MatchResult:
    """Serializable outcome of one deterministic self-play match."""

    match_seed: int
    board_seed: int
    board_mode: str
    victory_target: int
    player_count: int
    completed: bool
    reason: str
    winner_seat: int | None
    winner_name: str | None
    starting_player_seat: int | None
    turn_order: tuple[int, ...]
    turns: int
    action_steps: int
    dice_counts: dict[int, int]
    players: tuple[PlayerResult, ...]
    validation_errors: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "match_seed": self.match_seed,
            "board_seed": self.board_seed,
            "board_mode": self.board_mode,
            "victory_target": self.victory_target,
            "player_count": self.player_count,
            "completed": self.completed,
            "reason": self.reason,
            "winner_seat": self.winner_seat,
            "winner_name": self.winner_name,
            "starting_player_seat": self.starting_player_seat,
            "turn_order": list(self.turn_order),
            "turns": self.turns,
            "action_steps": self.action_steps,
            "dice_counts": dict(self.dice_counts),
            "players": [player.to_dict() for player in self.players],
            "validation_errors": list(self.validation_errors),
        }


@dataclass(frozen=True)
class BatchResult:
    """Aggregate plus individual results for a self-play batch."""

    matches: tuple[MatchResult, ...]
    game_count: int
    completed_games: int
    board_seed: int | None
    board_mode: str
    victory_target: int
    player_count: int
    personality_lineup: tuple[str, ...]
    win_counts: dict[int, int]
    average_turns: float

    def to_dict(self) -> dict:
        return {
            "game_count": self.game_count,
            "completed_games": self.completed_games,
            "board_seed": self.board_seed,
            "board_mode": self.board_mode,
            "victory_target": self.victory_target,
            "player_count": self.player_count,
            "personality_lineup": list(self.personality_lineup),
            "win_counts": dict(self.win_counts),
            "average_turns": self.average_turns,
            "matches": [match.to_dict() for match in self.matches],
        }


def _validate_options(
    *,
    board_mode: str,
    player_count: int,
    victory_target: int,
    max_turns: int,
    max_action_steps: int,
) -> str:
    if board_mode == "balanced":
        board_mode = "constrained"
    if board_mode not in SUPPORTED_BOARD_MODES:
        raise ValueError(f"unsupported board mode: {board_mode}")
    if player_count not in SUPPORTED_PLAYER_COUNTS:
        raise ValueError("player_count must be 2, 3, or 4")
    if not MIN_VICTORY_POINT_TARGET <= victory_target <= MAX_VICTORY_POINT_TARGET:
        raise ValueError(
            "victory_target must be between "
            f"{MIN_VICTORY_POINT_TARGET} and {MAX_VICTORY_POINT_TARGET}"
        )
    if isinstance(max_turns, bool) or not isinstance(max_turns, int) or max_turns <= 0:
        raise ValueError("max_turns must be positive")
    if (
        isinstance(max_action_steps, bool)
        or not isinstance(max_action_steps, int)
        or max_action_steps <= 0
    ):
        raise ValueError("max_action_steps must be positive")
    return board_mode


def normalise_personalities(
    personalities: Iterable[str] | None,
    player_count: int,
) -> tuple[str, ...]:
    """Validate a seat-ordered personality lineup.

    An omitted lineup uses each available personality once in a four-player
    match.  Smaller matches use the same stable prefix.  Duplicate values are
    accepted intentionally so callers can run mirror matches.
    """
    if player_count not in SUPPORTED_PLAYER_COUNTS:
        raise ValueError("player_count must be 2, 3, or 4")
    if personalities is None:
        return SUPPORTED_AI_PERSONALITIES[:player_count]
    if isinstance(personalities, str):
        raise TypeError("personalities must be an iterable of names, not a string")
    lineup = tuple(personalities)
    if len(lineup) != player_count:
        raise ValueError("personalities must contain exactly one value per player")
    if any(not isinstance(personality, str) for personality in lineup):
        raise TypeError("all personalities must be strings")
    unsupported = [
        personality
        for personality in lineup
        if personality not in SUPPORTED_AI_PERSONALITIES
    ]
    if unsupported:
        supported = ", ".join(SUPPORTED_AI_PERSONALITIES)
        raise ValueError(
            f"unsupported AI personality: {unsupported[0]} (choose from {supported})"
        )
    return lineup


def parse_personality_lineup(value: str) -> tuple[str, ...]:
    """Parse the comma-separated CLI representation without hiding errors."""
    if not isinstance(value, str):
        raise TypeError("personality lineup must be a string")
    lineup = tuple(item.strip() for item in value.split(","))
    if not lineup or any(not item for item in lineup):
        raise ValueError("personalities must be a comma-separated list")
    return lineup


def _prepare_game(
    *,
    match_seed: int,
    board_seed: int,
    board_mode: str,
    player_count: int,
    victory_target: int,
    personalities: Iterable[str] | None = None,
) -> _HeadlessCatanGame:
    personality_lineup = normalise_personalities(personalities, player_count)
    random.seed(match_seed)
    game = _HeadlessCatanGame(
        board_mode=board_mode,
        board_seed=board_seed,
        ai_player_count=0,
        ai_action_delay_ms=0,
    )
    game.ai_player_count = max(0, player_count - 1)
    game.configure_players(
        player_count,
        reset_logs=False,
        schedule_ai=False,
        reset_replay=False,
    )

    # The interactive configuration intentionally reserves at least one human
    # seat.  A simulation makes every existing seat AI without changing any
    # placement, trade, build, or turn rules.
    for seat, (player, personality) in enumerate(
        zip(game.players, personality_lineup), start=1
    ):
        player.name = f"CPU{seat}"
        player.is_ai = True
        player.ai_personality = personality
    game.reset_match_metrics()
    game.ai_player_count = player_count
    game.public_gain_history = {player.name: [] for player in game.players}
    game.last_resource_distribution = {}
    game.turn_order = game.players.copy()
    game.reset_initial_setup_state()
    game.current_player_index = 0
    game.reset_turn_state()
    game.victory_point_target = victory_target
    game.clear_log()
    game.replay_recorder = None
    game.replay_pending_capture = None
    return game


def _player_results(game: _HeadlessCatanGame) -> tuple[PlayerResult, ...]:
    results = []
    for seat, player in enumerate(game.players, start=1):
        settlements = 0
        cities = 0
        for node in game.board.nodes:
            building = node.building
            if building is None or building.owner is not player:
                continue
            if building.building_type == BuildingType.CITY:
                cities += 1
            else:
                settlements += 1
        resources = {
            resource_type.name.lower(): player.resources.get(resource_type, 0)
            for resource_type in ResourceType
            if resource_type != ResourceType.DESERT
        }
        initial_metrics = game.initial_placement_metrics.get(player, {})
        results.append(
            PlayerResult(
                seat=seat,
                name=player.name,
                personality=getattr(player, "ai_personality", "standard"),
                action_counts=dict(
                    game.self_play_action_counts.get(
                        player, {key: 0 for key in ACTION_COUNT_KEYS}
                    )
                ),
                victory_points=game.get_player_victory_points(player),
                public_victory_points=game.get_player_public_victory_points(player),
                resources=resources,
                resource_cards=player.total_resource_count(),
                roads=sum(road.owner is player for road in game.board.roads),
                settlements=settlements,
                cities=cities,
                played_knights=player.played_knights,
                longest_road=game.longest_road_owner is player,
                largest_army=game.largest_army_owner is player,
                initial_pips=int(initial_metrics.get("pips", 0)),
                initial_high_probability_access=bool(
                    initial_metrics.get("high_probability_access", False)
                ),
                initial_resource_diversity=int(
                    initial_metrics.get("resource_diversity", 0)
                ),
                won=game.winner is player,
            )
        )
    return tuple(results)


def _build_match_result(
    game: _HeadlessCatanGame,
    *,
    match_seed: int,
    board_seed: int,
    board_mode: str,
    victory_target: int,
    action_steps: int,
    reason: str,
    validation_errors: tuple[str, ...],
) -> MatchResult:
    seat_by_player = {player: seat for seat, player in enumerate(game.players, start=1)}
    turn_order = ()
    if not game.initial_dice_phase:
        turn_order = tuple(
            seat_by_player[player]
            for player in game.turn_order
            if player in seat_by_player
        )
    winner_seat = seat_by_player.get(game.winner)
    return MatchResult(
        match_seed=match_seed,
        board_seed=board_seed,
        board_mode=board_mode,
        victory_target=victory_target,
        player_count=len(game.players),
        completed=game.winner is not None and not validation_errors,
        reason=reason,
        winner_seat=winner_seat,
        winner_name=game.winner.name if game.winner is not None else None,
        starting_player_seat=turn_order[0] if turn_order else None,
        turn_order=turn_order,
        turns=game.self_play_turns,
        action_steps=action_steps,
        dice_counts=dict(game.self_play_dice_counts),
        players=_player_results(game),
        validation_errors=validation_errors,
    )


def _validate_completed_state(game: _HeadlessCatanGame) -> tuple[str, ...]:
    errors = []
    players = set(game.players)

    for resource_type in RESOURCE_TYPES:
        total = game.bank.available(resource_type) + sum(
            player.resources[resource_type] for player in game.players
        )
        if total != BANK_RESOURCE_COUNT:
            errors.append(f"{resource_type.name}: resource total {total}")

    for player in game.players:
        player_roads = [road for road in game.board.roads if road.owner is player]
        settlements = [
            node
            for node in game.board.nodes
            if node.building is not None
            and node.building.owner is player
            and node.building.building_type == BuildingType.SETTLEMENT
        ]
        cities = [
            node
            for node in game.board.nodes
            if node.building is not None
            and node.building.owner is player
            and node.building.building_type == BuildingType.CITY
        ]
        if player.roads_remaining + len(player_roads) != 15:
            errors.append(f"{player.name}: road pieces")
        if player.settlements_remaining + len(settlements) != 5:
            errors.append(f"{player.name}: settlement pieces")
        if player.cities_remaining + len(cities) != 4:
            errors.append(f"{player.name}: city pieces")

    seen_edges = set()
    for road in game.board.roads:
        if road.owner not in players:
            errors.append("road owner is not a player")
        edge = frozenset((road.node1, road.node2))
        if edge in seen_edges:
            errors.append("duplicate road edge")
        seen_edges.add(edge)
    if any(
        node.building is not None and node.building.owner not in players
        for node in game.board.nodes
    ):
        errors.append("building owner is not a player")
    if sum(game.self_play_dice_counts.values()) != game.self_play_turns:
        errors.append("dice count does not match turns")
    if game.winner is not None:
        if game.phase != "finished":
            errors.append("winner exists outside finished phase")
        if game.get_player_victory_points(game.winner) < game.victory_point_target:
            errors.append("winner is below victory target")
    return tuple(errors)


def run_match(
    *,
    match_seed: int,
    board_seed: int | None = None,
    board_mode: str = "constrained",
    player_count: int = 4,
    victory_target: int = 10,
    max_turns: int = DEFAULT_MAX_TURNS,
    max_action_steps: int = DEFAULT_MAX_ACTION_STEPS,
    personalities: Iterable[str] | None = None,
) -> MatchResult:
    """Run one deterministic AI-only match with no presentation delays.

    ``match_seed`` controls dice, stealing, and the development deck.
    ``board_seed`` controls the board independently and defaults to the match
    seed.  Python's process-global random state is restored before returning.
    """
    if isinstance(match_seed, bool) or not isinstance(match_seed, int):
        raise TypeError("match_seed must be an int")
    if board_seed is None:
        board_seed = match_seed
    if isinstance(board_seed, bool) or not isinstance(board_seed, int):
        raise TypeError("board_seed must be an int")
    board_mode = _validate_options(
        board_mode=board_mode,
        player_count=player_count,
        victory_target=victory_target,
        max_turns=max_turns,
        max_action_steps=max_action_steps,
    )
    personality_lineup = normalise_personalities(personalities, player_count)

    # Game rules currently use the stdlib random module for dice, stealing,
    # and the development deck.  Serialize simulations and restore caller
    # state so reproducibility does not leak side effects into the app/tests.
    with _RUN_LOCK:
        caller_random_state = random.getstate()
        try:
            game = _prepare_game(
                match_seed=match_seed,
                board_seed=board_seed,
                board_mode=board_mode,
                player_count=player_count,
                victory_target=victory_target,
                personalities=personality_lineup,
            )
            action_steps = 0
            reason = "stalled"
            while game.winner is None:
                if game.self_play_turns >= max_turns:
                    reason = "turn_limit"
                    break
                if action_steps >= max_action_steps:
                    reason = "action_limit"
                    break
                action_steps += 1
                if not game.ai.step(game):
                    reason = "stalled"
                    break
            if game.winner is not None:
                reason = "victory"
            validation_errors = _validate_completed_state(game)
            if validation_errors:
                reason = "integrity_error"
            return _build_match_result(
                game,
                match_seed=match_seed,
                board_seed=board_seed,
                board_mode=board_mode,
                victory_target=victory_target,
                action_steps=action_steps,
                reason=reason,
                validation_errors=validation_errors,
            )
        finally:
            random.setstate(caller_random_state)


def run_batch(
    *,
    game_count: int = DEFAULT_GAME_COUNT,
    match_seed_start: int = 0,
    match_seeds: Iterable[int] | None = None,
    board_seed: int | None = None,
    board_mode: str = "constrained",
    player_count: int = 4,
    victory_target: int = 10,
    max_turns: int = DEFAULT_MAX_TURNS,
    max_action_steps: int = DEFAULT_MAX_ACTION_STEPS,
    personalities: Iterable[str] | None = None,
    progress: Callable[[int, int, MatchResult], None] | None = None,
) -> BatchResult:
    """Run a batch, optionally holding ``board_seed`` fixed across matches."""
    if match_seeds is None:
        if (
            isinstance(game_count, bool)
            or not isinstance(game_count, int)
            or game_count <= 0
        ):
            raise ValueError("game_count must be positive")
        if isinstance(match_seed_start, bool) or not isinstance(match_seed_start, int):
            raise TypeError("match_seed_start must be an int")
        seeds = tuple(range(match_seed_start, match_seed_start + game_count))
    else:
        seeds = tuple(match_seeds)
        if not seeds:
            raise ValueError("match_seeds must not be empty")
        if any(isinstance(seed, bool) or not isinstance(seed, int) for seed in seeds):
            raise TypeError("all match_seeds must be ints")
        game_count = len(seeds)

    personality_lineup = normalise_personalities(personalities, player_count)
    matches = []
    for match_offset, match_seed in enumerate(seeds):
        # Rotate the exact lineup across seats.  Over a multiple of the player
        # count every personality receives equal exposure to every seat, while
        # remaining deterministic for the same ordered seed list.
        rotation = match_offset % player_count
        match_personalities = (
            personality_lineup[rotation:] + personality_lineup[:rotation]
        )
        match = run_match(
            match_seed=match_seed,
            board_seed=board_seed,
            board_mode=board_mode,
            player_count=player_count,
            victory_target=victory_target,
            max_turns=max_turns,
            max_action_steps=max_action_steps,
            personalities=match_personalities,
        )
        matches.append(match)
        if progress is not None:
            progress(match_offset + 1, game_count, match)

    completed = [match for match in matches if match.completed]
    win_counts = {seat: 0 for seat in range(1, player_count + 1)}
    for match in completed:
        win_counts[match.winner_seat] += 1
    return BatchResult(
        matches=tuple(matches),
        game_count=len(matches),
        completed_games=len(completed),
        board_seed=board_seed,
        board_mode=matches[0].board_mode,
        victory_target=victory_target,
        player_count=player_count,
        personality_lineup=personality_lineup,
        win_counts=win_counts,
        average_turns=(
            fmean(match.turns for match in completed)
            if completed
            else 0.0
        ),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="カタン風ゲーム AI自己対戦シミュレーター")
    parser.add_argument("--games", type=int, default=DEFAULT_GAME_COUNT)
    parser.add_argument("--match-seed", type=int, default=0, help="連番seedの開始値")
    parser.add_argument("--board-seed", type=int, default=None, help="全試合で固定する盤面seed")
    parser.add_argument("--mode", choices=SUPPORTED_BOARD_MODES, default="constrained")
    parser.add_argument("--players", type=int, choices=SUPPORTED_PLAYER_COUNTS, default=4)
    parser.add_argument(
        "--personalities",
        type=parse_personality_lineup,
        default=None,
        help=(
            "席1から順にカンマ区切りで指定。"
            "standard,expansion,trader,disruptor（試合ごとに席をローテーション）"
        ),
    )
    parser.add_argument(
        "--target",
        type=int,
        choices=range(MIN_VICTORY_POINT_TARGET, MAX_VICTORY_POINT_TARGET + 1),
        default=10,
    )
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    parser.add_argument("--max-actions", type=int, default=DEFAULT_MAX_ACTION_STEPS)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        result = run_batch(
            game_count=args.games,
            match_seed_start=args.match_seed,
            board_seed=args.board_seed,
            board_mode=args.mode,
            player_count=args.players,
            victory_target=args.target,
            max_turns=args.max_turns,
            max_action_steps=args.max_actions,
            personalities=args.personalities,
        )
    except (TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            result.to_dict(),
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            sort_keys=args.pretty,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
