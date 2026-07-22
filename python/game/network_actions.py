"""Authoritative semantic command routing for LAN matches.

The transport owns session authentication and passes its trusted ``seat_index``
separately.  Commands never contain a player identity, keyboard key, or screen
coordinate, so a client cannot impersonate another seat or depend on a
particular Pygame layout.
"""

from __future__ import annotations

import json
from typing import Any

from game.bank import RESOURCE_TYPES
from game.development_cards import DevelopmentCardType
from game.network_protocol import build_board_reference_index
from game.persistence import serialize_game
from game.resources import BUILD_COSTS, ResourceType


SUPPORTED_GAME_COMMANDS = frozenset(
    {
        "roll_dice",
        "end_turn",
        "cancel",
        "build",
        "initial_place",
        "move_robber",
        "steal",
        "select_resource",
        "buy_development",
        "start_bank_trade",
        "start_domestic_trade",
        "market_create",
        "market_fill",
        "market_cancel",
        "auction_create",
        "auction_bid",
        "auction_cancel_bid",
        "auction_accept",
        "auction_cancel",
        "credit_borrow",
        "credit_repay",
        "trade_partner",
        "trade_broadcast",
        "trade_edit_side",
        "trade_receive_operator",
        "trade_adjust",
        "trade_submit",
        "trade_reveal",
        "trade_accept",
        "trade_counter",
        "trade_reject",
        "use_development",
        "finish_road_building",
    }
)

# Persistent multiplayer systems may need a very small number of commands
# from an authenticated participant who is not the current turn actor.  Keep
# this an explicit wire-command allowlist: adding a command to
# ``SUPPORTED_GAME_COMMANDS`` must never make it usable out of turn by
# accident.  The auction dispatcher still owns the domain-level legality
# checks (lot/revision/escrow); this policy only answers whether the trusted
# session seat may reach that dispatcher at all.
OFF_TURN_GAME_COMMANDS = frozenset(
    {
        "auction_bid",
        "auction_cancel_bid",
    }
)

# A standard board has 72 edges and 54 nodes, so even a deliberately generous
# all-target action list remains well below this wire-level safety bound.  The
# cap also keeps a malformed/custom authority object from producing an
# unbounded response.
MAX_GAME_COMMAND_OPTIONS = 512

_GAME_COMMAND_OPTION_ORDER = (
    "roll_dice",
    "initial_place",
    "move_robber",
    "steal",
    "select_resource",
    "build",
    "buy_development",
    "start_bank_trade",
    "start_domestic_trade",
    "market_create",
    "market_fill",
    "market_cancel",
    "auction_create",
    "auction_bid",
    "auction_cancel_bid",
    "auction_accept",
    "auction_cancel",
    "credit_borrow",
    "credit_repay",
    "trade_partner",
    "trade_broadcast",
    "trade_edit_side",
    "trade_receive_operator",
    "trade_adjust",
    "trade_submit",
    "trade_reveal",
    "trade_accept",
    "trade_counter",
    "trade_reject",
    "use_development",
    "finish_road_building",
    "cancel",
    "end_turn",
)
_GAME_COMMAND_OPTION_RANK = {
    command: rank
    for rank, command in enumerate(_GAME_COMMAND_OPTION_ORDER)
}
_RESOURCE_OPTION_RANK = {
    resource.name: rank for rank, resource in enumerate(RESOURCE_TYPES)
}
_PIECE_OPTION_RANK = {"road": 0, "settlement": 1, "city": 2}
_DEVELOPMENT_OPTION_RANK = {
    card: rank for rank, card in enumerate((
        "knight",
        "road_building",
        "year_of_plenty",
        "monopoly",
    ))
}

_EMPTY_ARGS_COMMANDS = frozenset(
    {
        "roll_dice",
        "end_turn",
        "cancel",
        "buy_development",
        "start_bank_trade",
        "start_domestic_trade",
        "trade_broadcast",
        "trade_submit",
        "trade_reveal",
        "trade_counter",
        "trade_reject",
        "finish_road_building",
    }
)
_DEVELOPMENT_COMMANDS = {
    "knight": "use_knight_card",
    "road_building": "use_road_building_card",
    "year_of_plenty": "use_year_of_plenty_card",
    "monopoly": "use_monopoly_card",
}


class NetworkActionError(ValueError):
    """A command rejection with a stable machine-readable error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def resolve_active_actor_index(game: Any) -> int | None:
    """Return the seat currently allowed to supply input.

    This deliberately resolves from authoritative object references instead of
    accepting an actor supplied by a client.
    """

    players = list(getattr(game, "players", ()))
    if not players or getattr(game, "phase", None) == "finished":
        return None

    special_phase = getattr(game, "special_phase", None)
    actor = None

    if special_phase == "player_handoff":
        actor = getattr(game, "handoff_player", None)
    elif isinstance(special_phase, str) and special_phase.startswith(
        "domestic_trade_"
    ):
        get_actor = getattr(game, "get_domestic_trade_actor", None)
        actor = get_actor() if callable(get_actor) else None
    elif special_phase == "discard":
        actor = getattr(game, "discard_player", None)
    elif getattr(game, "phase", None) == "initial":
        if getattr(game, "initial_dice_phase", False):
            contenders = list(getattr(game, "initial_dice_contenders", ()))
            index = getattr(game, "initial_player_index", -1)
            if _is_index(index, contenders):
                actor = contenders[index]
        else:
            placement_order = list(
                getattr(game, "initial_placement_order", ())
            )
            index = getattr(game, "initial_player_index", -1)
            if _is_index(index, placement_order):
                actor = placement_order[index]
    else:
        get_current_player = getattr(game, "get_current_player", None)
        if callable(get_current_player):
            actor = get_current_player()

    try:
        return players.index(actor)
    except ValueError:
        return None


def build_game_command_options(
    game: Any,
    seat_index: int | None,
) -> list[dict[str, Any]]:
    """Describe every semantic command currently legal for one trusted seat.

    The function is intentionally read-only: it uses the same authoritative
    rule queries as :func:`apply_game_command`, but never probes legality by
    applying a command.  Returned values contain only JSON primitives and
    stable board identifiers, making the result suitable for both Pygame and
    future browser clients.

    A spectator, invalid/AI seat, or finished match receives an empty list.
    Non-active human seats receive only explicitly allowlisted persistent
    auction commands. Descriptors are deterministic, unique by ``command``
    plus ``args``, and capped by :data:`MAX_GAME_COMMAND_OPTIONS`.
    """

    players = list(getattr(game, "players", ()))
    if (
        seat_index is None
        or type(seat_index) is not int
        or not _is_index(seat_index, players)
        or getattr(players[seat_index], "is_ai", False)
        or getattr(game, "phase", None) == "finished"
        or getattr(game, "winner", None) is not None
    ):
        return []

    actor_index = resolve_active_actor_index(game)
    if actor_index != seat_index:
        return _build_off_turn_auction_options(game, players[seat_index])

    phase = getattr(game, "phase", None)
    if phase == "initial":
        return _build_initial_command_options(game)
    if phase != "main":
        return []

    special_phase = getattr(game, "special_phase", None)
    if special_phase is not None:
        return _build_special_phase_command_options(
            game,
            players[seat_index],
            special_phase,
        )
    return _build_main_command_options(game, players[seat_index])


def _build_off_turn_auction_options(
    game: Any,
    player: Any,
) -> list[dict[str, Any]]:
    if not is_off_turn_game_command_allowed(game, "auction_bid"):
        return []
    return _finalise_command_options(_trade_auction_command_options(game, player))


def _build_initial_command_options(game: Any) -> list[dict[str, Any]]:
    if getattr(game, "initial_dice_phase", False):
        if getattr(game, "has_active_dice_animation", lambda: False)():
            return []
        return _finalise_command_options([_command_option("roll_dice")])

    order = list(getattr(game, "initial_placement_order", ()))
    index = getattr(game, "initial_player_index", -1)
    if not _is_index(index, order):
        return []
    player = order[index]
    if getattr(game, "waiting_for_road", False):
        candidates = game.get_initial_road_candidates(player)
        options = _board_target_options(
            game,
            "edge",
            candidates,
            "initial_place",
        )
    else:
        candidates = game.get_initial_settlement_candidates()
        options = _board_target_options(
            game,
            "node",
            candidates,
            "initial_place",
        )
    return _finalise_command_options(options)


def _build_main_command_options(
    game: Any,
    player: Any,
) -> list[dict[str, Any]]:
    options = _development_command_options(game, player)
    if not getattr(game, "dice_rolled", False):
        if not getattr(game, "has_active_dice_animation", lambda: False)():
            options.append(_command_option("roll_dice"))
        return _finalise_command_options(options)

    options.extend(
        _board_target_options(
            game,
            "edge",
            game.get_buildable_road_edges(player),
            "build",
            piece="road",
        )
    )
    options.extend(
        _board_target_options(
            game,
            "node",
            game.get_buildable_settlement_nodes(player),
            "build",
            piece="settlement",
        )
    )
    options.extend(
        _board_target_options(
            game,
            "node",
            game.get_buildable_city_nodes(player),
            "build",
            piece="city",
        )
    )
    if getattr(game, "development_deck", None) and player.can_afford(
        BUILD_COSTS["development"]
    ):
        options.append(_command_option("buy_development"))
    if game.has_bank_trade_option(player):
        options.append(_command_option("start_bank_trade"))
    if game.has_domestic_trade_option(player):
        options.append(_command_option("start_domestic_trade"))
    if getattr(game, "can_create_trade_market_order", lambda _player: False)(
        player
    ):
        options.append(_command_option("market_create"))
    get_market_orders = getattr(game, "get_trade_market_orders", lambda: ())
    player_index = list(getattr(game, "players", ())).index(player)
    for order in get_market_orders():
        if order.seller_index == player_index:
            options.append(
                _command_option(
                    "market_cancel",
                    order_id=order.order_id,
                    revision=order.revision,
                )
            )
        elif getattr(game, "can_fill_trade_market_order", lambda *_args: False)(
            player,
            order,
        ):
            options.append(
                _command_option(
                    "market_fill",
                    order_id=order.order_id,
                    revision=order.revision,
                )
            )
    options.extend(_trade_auction_command_options(game, player))
    options.extend(_resource_credit_command_options(game, player))
    if getattr(game, "action_mode", None) is not None:
        options.append(_command_option("cancel"))
    options.append(_command_option("end_turn"))
    return _finalise_command_options(options)


def _resource_credit_command_options(game: Any, player: Any) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for resource in RESOURCE_TYPES:
        if getattr(game, "can_borrow_resource_credit", lambda *_args: False)(
            player,
            resource,
        ):
            options.append(
                _command_option("credit_borrow", resource=resource.name)
            )
    loan = getattr(game, "get_resource_credit_loan", lambda _player: None)(player)
    if loan is not None and getattr(
        game,
        "can_repay_resource_credit",
        lambda *_args: False,
    )(player):
        options.append(
            _command_option(
                "credit_repay",
                loan_id=loan.loan_id,
                revision=loan.revision,
            )
        )
    return options


def _trade_auction_command_options(game: Any, player: Any) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    if getattr(game, "can_create_trade_auction", lambda _player: False)(player):
        options.append(_command_option("auction_create"))
    players = list(getattr(game, "players", ()))
    try:
        player_index = players.index(player)
    except ValueError:
        return options
    for auction in getattr(game, "get_trade_auctions", lambda: ())():
        reference = {
            "auction_id": auction.auction_id,
            "revision": auction.revision,
        }
        if auction.seller_index == player_index:
            if getattr(game, "can_accept_trade_auction", lambda *_args: False)(
                player,
                auction,
            ):
                options.extend(
                    _command_option(
                        "auction_accept",
                        **reference,
                        bidder_index=bid.bidder_index,
                    )
                    for bid in auction.bids
                )
            if getattr(game, "can_cancel_trade_auction", lambda *_args: False)(
                player,
                auction,
            ):
                options.append(_command_option("auction_cancel", **reference))
            continue
        if getattr(game, "can_bid_trade_auction", lambda *_args: False)(
            player,
            auction,
        ):
            options.append(_command_option("auction_bid", **reference))
        if getattr(
            game,
            "can_cancel_trade_auction_bid",
            lambda *_args: False,
        )(player, auction):
            options.append(_command_option("auction_cancel_bid", **reference))
    return options


def _development_command_options(
    game: Any,
    player: Any,
) -> list[dict[str, Any]]:
    options = []
    card_types = {
        "knight": DevelopmentCardType.KNIGHT,
        "road_building": DevelopmentCardType.ROAD_BUILDING,
        "year_of_plenty": DevelopmentCardType.YEAR_OF_PLENTY,
        "monopoly": DevelopmentCardType.MONOPOLY,
    }
    for card_name, card_type in card_types.items():
        can_use, _message = game.can_use_development_card(player, card_type)
        if not can_use:
            continue
        if card_type is DevelopmentCardType.ROAD_BUILDING and (
            getattr(player, "roads_remaining", 0) <= 0
            or not game.has_legal_road_placement(player)
        ):
            continue
        if (
            card_type is DevelopmentCardType.YEAR_OF_PLENTY
            and game.bank.total_cards() <= 0
        ):
            continue
        options.append(
            _command_option("use_development", card=card_name)
        )
    return options


def _build_special_phase_command_options(
    game: Any,
    player: Any,
    special_phase: str,
) -> list[dict[str, Any]]:
    if special_phase == "discard":
        discard_player = getattr(game, "discard_player", None)
        options = [
            _command_option("select_resource", resource=resource.name)
            for resource in RESOURCE_TYPES
            if discard_player is not None
            and discard_player.resources.get(resource, 0) > 0
        ]
    elif special_phase == "year_of_plenty":
        options = [
            _command_option("select_resource", resource=resource.name)
            for resource in RESOURCE_TYPES
            if game.bank.available(resource) > 0
        ]
    elif special_phase == "monopoly":
        options = [
            _command_option("select_resource", resource=resource.name)
            for resource in RESOURCE_TYPES
        ]
    elif special_phase == "move_robber":
        options = _board_target_options(
            game,
            "tile",
            getattr(game, "robber_tile_candidates", ()),
            "move_robber",
        )
    elif special_phase == "steal":
        target_ids = {id(target) for target in getattr(game, "robber_target_players", ())}
        options = [
            _command_option("steal", seat_index=index)
            for index, candidate in enumerate(getattr(game, "players", ()))
            if id(candidate) in target_ids
        ]
    elif special_phase == "bank_trade_give":
        options = _bank_trade_give_options(game, player)
    elif special_phase == "bank_trade_receive":
        options = _bank_trade_receive_options(game, player)
    elif special_phase == "road_building":
        options = _road_building_options(game, player)
    elif isinstance(special_phase, str) and special_phase.startswith(
        "domestic_trade_"
    ):
        options = _domestic_trade_options(game, special_phase)
    else:
        options = []
    return _finalise_command_options(options)


def _bank_trade_give_options(game: Any, player: Any) -> list[dict[str, Any]]:
    rates = game.get_trade_rates(player)
    options = [
        _command_option("select_resource", resource=resource.name)
        for resource in RESOURCE_TYPES
        if resource in rates
        and player.available_resource_count(resource) >= rates[resource]
        and any(
            receive is not resource and game.bank.available(receive) > 0
            for receive in RESOURCE_TYPES
        )
    ]
    options.append(_command_option("cancel"))
    return options


def _bank_trade_receive_options(
    game: Any,
    player: Any,
) -> list[dict[str, Any]]:
    give_resource = getattr(game, "bank_trade_give_resource", None)
    rates = game.get_trade_rates(player)
    can_still_pay = (
        give_resource in rates
        and player.available_resource_count(give_resource) >= rates[give_resource]
    )
    options = [
        _command_option("select_resource", resource=resource.name)
        for resource in RESOURCE_TYPES
        if can_still_pay
        and resource is not give_resource
        and game.bank.available(resource) > 0
    ]
    options.append(_command_option("cancel"))
    return options


def _road_building_options(game: Any, player: Any) -> list[dict[str, Any]]:
    candidates = game.get_buildable_road_edges(
        player,
        require_affordability=False,
    )
    options = []
    if getattr(game, "free_roads_remaining", 0) > 0:
        options.extend(
            _board_target_options(
                game,
                "edge",
                candidates,
                "build",
                piece="road",
            )
        )
    if (
        getattr(game, "free_roads_remaining", 0) <= 0
        or not candidates
    ):
        options.append(_command_option("finish_road_building"))
    return options


def _domestic_trade_options(
    game: Any,
    special_phase: str,
) -> list[dict[str, Any]]:
    options = []
    if special_phase == "domestic_trade_partner":
        active_player = game.get_current_player()
        options.extend(
            _command_option("trade_partner", seat_index=index)
            for index, candidate in enumerate(getattr(game, "players", ()))
            if candidate is not active_player
            and candidate.available_resource_total() > 0
        )
        if game.get_domestic_trade_eligible_partners(active_player):
            options.append(_command_option("trade_broadcast"))
    elif special_phase == "domestic_trade_edit":
        edit_side = getattr(game, "domestic_trade_edit_side", None)
        options.extend(
            _command_option("trade_edit_side", side=side)
            for side in ("give", "receive")
            if side != edit_side
        )
        receive_operator = getattr(
            game,
            "domestic_trade_receive_operator",
            "and",
        )
        options.extend(
            _command_option("trade_receive_operator", operator=operator)
            for operator in ("and", "or")
            if operator != receive_operator
        )
        give = getattr(game, "domestic_trade_give", {})
        receive = getattr(game, "domestic_trade_receive", {})
        for side, bundle, other_bundle in (
            ("give", give, receive),
            ("receive", receive, give),
        ):
            for resource in RESOURCE_TYPES:
                current = bundle.get(resource, 0)
                if current > 0:
                    options.append(
                        _command_option(
                            "trade_adjust",
                            side=side,
                            resource=resource.name,
                            delta=-1,
                        )
                    )
                limit = game.get_domestic_trade_quantity_limit(side, resource)
                if current < limit and other_bundle.get(resource, 0) <= 0:
                    options.append(
                        _command_option(
                            "trade_adjust",
                            side=side,
                            resource=resource.name,
                            delta=1,
                        )
                    )
        valid, _message = game.validate_domestic_trade_terms()
        has_destination = (
            getattr(game, "domestic_trade_partner", None) is not None
            or (
                getattr(game, "domestic_trade_is_broadcast", False)
                and getattr(game, "domestic_trade_broadcast_index", -1) < 0
            )
        )
        if valid and has_destination:
            options.append(_command_option("trade_submit"))
    elif special_phase in (
        "domestic_trade_handoff",
        "domestic_trade_counter_handoff",
    ):
        options.append(_command_option("trade_reveal"))
    elif special_phase == "domestic_trade_response":
        options.extend(_domestic_trade_accept_options(game))
        options.extend(
            (
                _command_option("trade_counter"),
                _command_option("trade_reject"),
            )
        )
    elif special_phase == "domestic_trade_counter_response":
        options.extend(_domestic_trade_accept_options(game))
        options.append(_command_option("trade_reject"))

    # Every domestic-trade sub-phase is cancellable by the active editor or
    # responder, including the reveal gates retained for compatibility.
    options.append(_command_option("cancel"))
    return options


def _domestic_trade_accept_options(game: Any) -> list[dict[str, Any]]:
    if getattr(game, "domestic_trade_receive_operator", "and") == "and":
        return (
            [_command_option("trade_accept")]
            if game.can_execute_domestic_trade()
            else []
        )
    return [
        _command_option("trade_accept", resource=resource.name)
        for resource, _bundle in game.get_domestic_trade_receive_branches()
        if resource is not None and game.can_execute_domestic_trade(resource)
    ]


def _board_target_options(
    game: Any,
    kind: str,
    candidates: Any,
    command: str,
    **args: Any,
) -> list[dict[str, Any]]:
    references = build_board_reference_index(game)[kind]
    if kind == "edge":
        candidate_keys = {_edge_key(edge) for edge in candidates}
        return [
            _command_option(command, **args, target=target_id)
            for target_id, edge in references.items()
            if _edge_key(edge) in candidate_keys
        ]

    candidate_ids = {id(candidate) for candidate in candidates}
    return [
        _command_option(command, **args, target=target_id)
        for target_id, target in references.items()
        if id(target) in candidate_ids
    ]


def _command_option(command: str, **args: Any) -> dict[str, Any]:
    return {"command": command, "args": args}


def _finalise_command_options(
    options: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    unique = {
        (option["command"], _canonical_args(option["args"])): option
        for option in options
    }
    return sorted(unique.values(), key=_command_option_sort_key)[
        :MAX_GAME_COMMAND_OPTIONS
    ]


def _canonical_args(args: dict[str, Any]) -> str:
    return json.dumps(
        args,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _command_option_sort_key(option: dict[str, Any]) -> tuple[Any, ...]:
    command = option["command"]
    args = option["args"]
    return (
        _GAME_COMMAND_OPTION_RANK.get(command, len(_GAME_COMMAND_OPTION_RANK)),
        _PIECE_OPTION_RANK.get(args.get("piece"), -1),
        _stable_board_id_key(args.get("target")),
        args.get("seat_index", -1),
        {"give": 0, "receive": 1}.get(args.get("side"), -1),
        _RESOURCE_OPTION_RANK.get(args.get("resource"), -1),
        args.get("delta", 0),
        _DEVELOPMENT_OPTION_RANK.get(args.get("card"), -1),
        _canonical_args(args),
    )


def _stable_board_id_key(value: Any) -> tuple[str, int]:
    if type(value) is not str:
        return ("", -1)
    prefix, separator, suffix = value.rpartition("-")
    if separator and suffix.isdigit():
        return (prefix, int(suffix))
    return (value, -1)


def advance_network_handoffs(game: Any) -> bool:
    """Skip pass-and-play privacy gates that are unnecessary across clients.

    Each LAN client already receives a viewer-filtered state.  Leaving the
    local, shared-screen reveal gates active would deadlock remote turns and
    trades.  The authority may call this after local host actions as well as
    after :func:`apply_game_command`.
    """

    changed = False
    for _ in range(4):
        special_phase = getattr(game, "special_phase", None)
        if special_phase == "player_handoff":
            reveal = getattr(game, "reveal_player_handoff", None)
        elif special_phase in (
            "domestic_trade_handoff",
            "domestic_trade_counter_handoff",
        ):
            reveal = getattr(game, "reveal_domestic_trade_response", None)
        else:
            break
        if not callable(reveal) or not reveal():
            break
        changed = True
    return changed


def apply_game_command(
    game: Any,
    seat_index: int | None,
    command: str,
    args: dict[str, Any] | None = None,
) -> bool:
    """Validate and apply one semantic command to the authoritative game.

    ``seat_index`` must come from the authenticated transport session.  A
    successful call always changes authoritative match state and returns
    ``True``; every rejection raises :class:`NetworkActionError`.
    """

    players = list(getattr(game, "players", ()))
    _validate_session_seat(players, seat_index)
    if type(command) is not str or command not in SUPPORTED_GAME_COMMANDS:
        raise NetworkActionError("unsupported_command", "未対応のゲーム操作です。")
    command_args = _normalise_args(args)

    if getattr(players[seat_index], "is_ai", False):
        raise NetworkActionError(
            "seat_not_controllable", "AIの席をネットワークから操作できません。"
        )

    actor_index = resolve_active_actor_index(game)
    if actor_index is None:
        raise NetworkActionError(
            "no_active_actor", "現在はプレイヤー操作を受け付けていません。"
        )
    if seat_index != actor_index and not is_off_turn_game_command_allowed(
        game,
        command,
    ):
        raise NetworkActionError(
            "not_active_player", "現在この操作を行える席ではありません。"
        )

    before = _state_fingerprint(game)
    _dispatch(game, command, command_args, seat_index=seat_index)
    after = _state_fingerprint(game)
    if before == after:
        raise NetworkActionError(
            "no_state_change", "操作は現在の状態では実行できませんでした。"
        )

    advance_network_handoffs(game)
    return True


def is_off_turn_game_command_allowed(game: Any, command: Any) -> bool:
    """Return whether ``command`` may cross the non-active-seat boundary.

    This is intentionally narrower than domain legality.  A permitted command
    must still be authenticated by the controller, dispatched with that
    trusted seat, pass its auction and escrow validation, and mutate state.
    Transient mandatory/UI phases are excluded so an asynchronous cancellation
    cannot change the hand used by discard or domestic-trade resolution.
    """

    return bool(
        type(command) is str
        and command in OFF_TURN_GAME_COMMANDS
        and getattr(game, "phase", None) == "main"
        and getattr(game, "winner", None) is None
        and getattr(game, "special_phase", None) is None
        and getattr(game, "action_mode", None) is None
        and not getattr(game, "has_active_dice_animation", lambda: False)()
    )


def _dispatch(
    game: Any,
    command: str,
    args: dict[str, Any],
    *,
    seat_index: int,
) -> None:
    if command in _EMPTY_ARGS_COMMANDS:
        _expect_fields(args)

    if command == "roll_dice":
        _roll_dice(game)
    elif command == "end_turn":
        _end_turn(game)
    elif command == "cancel":
        _cancel(game)
    elif command == "build":
        _build(game, args)
    elif command == "initial_place":
        _initial_place(game, args)
    elif command == "move_robber":
        _move_robber(game, args)
    elif command == "steal":
        _steal(game, args)
    elif command == "select_resource":
        _select_resource(game, args)
    elif command == "buy_development":
        _buy_development(game)
    elif command == "start_bank_trade":
        _start_bank_trade(game)
    elif command == "start_domestic_trade":
        _start_domestic_trade(game)
    elif command == "market_create":
        _market_create(game, args)
    elif command == "market_fill":
        _market_fill(game, args)
    elif command == "market_cancel":
        _market_cancel(game, args)
    elif command == "auction_create":
        _auction_create(game, game.players[seat_index], args)
    elif command == "auction_bid":
        _auction_bid(game, game.players[seat_index], args)
    elif command == "auction_cancel_bid":
        _auction_cancel_bid(game, game.players[seat_index], args)
    elif command == "auction_accept":
        _auction_accept(game, game.players[seat_index], args)
    elif command == "auction_cancel":
        _auction_cancel(game, game.players[seat_index], args)
    elif command == "credit_borrow":
        _credit_borrow(game, game.players[seat_index], args)
    elif command == "credit_repay":
        _credit_repay(game, game.players[seat_index], args)
    elif command == "trade_partner":
        _trade_partner(game, args)
    elif command == "trade_broadcast":
        _require_phase(game, "domestic_trade_partner")
        game.select_domestic_trade_broadcast()
    elif command == "trade_edit_side":
        _trade_edit_side(game, args)
    elif command == "trade_receive_operator":
        _trade_receive_operator(game, args)
    elif command == "trade_adjust":
        _trade_adjust(game, args)
    elif command == "trade_submit":
        _trade_submit(game)
    elif command == "trade_reveal":
        _trade_reveal(game)
    elif command == "trade_accept":
        _trade_accept(game, args)
    elif command == "trade_counter":
        _require_phase(game, "domestic_trade_response")
        game.begin_domestic_trade_counter()
    elif command == "trade_reject":
        if getattr(game, "special_phase", None) not in (
            "domestic_trade_response",
            "domestic_trade_counter_response",
        ):
            _not_allowed("現在は交易を拒否できません。")
        game.reject_domestic_trade()
    elif command == "use_development":
        _use_development(game, args)
    elif command == "finish_road_building":
        _finish_road_building(game)


def _roll_dice(game: Any) -> None:
    if getattr(game, "has_active_dice_animation", lambda: False)():
        _not_allowed("ダイス演出の完了を待ってください。")
    if getattr(game, "phase", None) == "initial":
        if not getattr(game, "initial_dice_phase", False):
            _not_allowed("初期ダイスは終了しています。")
        game.handle_initial_key_roll()
        return
    if (
        getattr(game, "phase", None) != "main"
        or getattr(game, "winner", None) is not None
        or getattr(game, "special_phase", None) is not None
        or getattr(game, "dice_rolled", False)
    ):
        _not_allowed("現在はダイスを振れません。")
    game.handle_roll_dice()


def _end_turn(game: Any) -> None:
    if (
        getattr(game, "phase", None) != "main"
        or getattr(game, "winner", None) is not None
        or getattr(game, "special_phase", None) is not None
        or not getattr(game, "dice_rolled", False)
    ):
        _not_allowed("現在は手番を終了できません。")
    game.finish_current_turn()


def _cancel(game: Any) -> None:
    special_phase = getattr(game, "special_phase", None)
    is_domestic = getattr(game, "is_domestic_trade_phase", lambda: False)()
    if not (
        is_domestic
        or special_phase in ("bank_trade_give", "bank_trade_receive")
        or getattr(game, "action_mode", None) is not None
    ):
        _not_allowed("キャンセルする操作がありません。")
    game.cancel_selection()


def _build(game: Any, args: dict[str, Any]) -> None:
    _expect_fields(args, "piece", "target")
    piece = args["piece"]
    if piece not in ("road", "settlement", "city"):
        raise NetworkActionError("invalid_args", "建設する駒の種類が不正です。")
    special_phase = getattr(game, "special_phase", None)
    if (
        getattr(game, "phase", None) != "main"
        or getattr(game, "winner", None) is not None
    ):
        _not_allowed("現在は建設できません。")

    player = game.get_current_player()
    if special_phase == "road_building":
        if piece != "road":
            _not_allowed("街道建設カードでは街道だけを配置できます。")
        edge = _resolve_target(game, args["target"], "edge")
        candidates = game.get_buildable_road_edges(
            player, require_affordability=False
        )
        if not _edge_in(edge, candidates):
            raise NetworkActionError("invalid_target", "その辺には街道を置けません。")
        game.handle_free_road_build_click(_edge_midpoint(edge))
        return
    if not getattr(game, "dice_rolled", False):
        _not_allowed("現在は建設できません。")
    if special_phase is not None:
        _not_allowed("進行中の特殊処理を先に完了してください。")

    if piece == "road":
        edge = _resolve_target(game, args["target"], "edge")
        if not _edge_in(edge, game.get_buildable_road_edges(player)):
            raise NetworkActionError("invalid_target", "その辺には街道を建設できません。")
        game.build_road(_edge_midpoint(edge))
    elif piece == "settlement":
        node = _resolve_target(game, args["target"], "node")
        if node not in game.get_buildable_settlement_nodes(player):
            raise NetworkActionError(
                "invalid_target", "その交差点には開拓地を建設できません。"
            )
        game.build_settlement((node.x, node.y))
    else:
        node = _resolve_target(game, args["target"], "node")
        if node not in game.get_buildable_city_nodes(player):
            raise NetworkActionError(
                "invalid_target", "その交差点は都市へ発展できません。"
            )
        game.build_city((node.x, node.y))


def _initial_place(game: Any, args: dict[str, Any]) -> None:
    _expect_fields(args, "target")
    if (
        getattr(game, "phase", None) != "initial"
        or getattr(game, "initial_dice_phase", False)
    ):
        _not_allowed("現在は初期配置を行えません。")
    player = game.initial_placement_order[game.initial_player_index]
    if not getattr(game, "waiting_for_road", False):
        node = _resolve_target(game, args["target"], "node")
        if node not in game.get_initial_settlement_candidates():
            raise NetworkActionError(
                "invalid_target", "その交差点には初期開拓地を配置できません。"
            )
        game.handle_initial_placement((node.x, node.y))
        return

    edge = _resolve_target(game, args["target"], "edge")
    candidates = game.get_initial_road_candidates(player)
    if not _edge_in(edge, candidates):
        raise NetworkActionError(
            "invalid_target", "その辺には初期街道を配置できません。"
        )
    last_node = game.last_settlement_node
    destination = edge[1] if edge[0] is last_node else edge[0]
    game.handle_initial_placement((destination.x, destination.y))


def _move_robber(game: Any, args: dict[str, Any]) -> None:
    _expect_fields(args, "target")
    _require_phase(game, "move_robber")
    tile = _resolve_target(game, args["target"], "tile")
    if tile not in getattr(game, "robber_tile_candidates", ()):
        raise NetworkActionError("invalid_target", "盗賊をその地形へ移動できません。")
    game.relocate_robber(tile)


def _steal(game: Any, args: dict[str, Any]) -> None:
    _expect_fields(args, "seat_index")
    _require_phase(game, "steal")
    victim_index = _validated_target_seat(game, args["seat_index"])
    victim = game.players[victim_index]
    if victim not in getattr(game, "robber_target_players", ()):
        raise NetworkActionError("invalid_target", "その席は略奪対象ではありません。")
    game.steal_random_resource(victim)
    game.complete_robber_phase()


def _select_resource(game: Any, args: dict[str, Any]) -> None:
    _expect_fields(args, "resource")
    resource = _resource_from_name(args["resource"])
    special_phase = getattr(game, "special_phase", None)
    if special_phase == "discard":
        discard_player = getattr(game, "discard_player", None)
        if discard_player is None or discard_player.resources[resource] <= 0:
            raise NetworkActionError("invalid_target", "その資源は捨てられません。")
        game.discard_resource(resource)
    elif special_phase == "year_of_plenty":
        if game.bank.available(resource) <= 0:
            raise NetworkActionError("invalid_target", "銀行にその資源がありません。")
        game.handle_resource_selection(resource)
    elif special_phase == "monopoly":
        game.handle_resource_selection(resource)
    elif special_phase in ("bank_trade_give", "bank_trade_receive"):
        player = game.get_current_player()
        rates = game.get_trade_rates(player)
        if special_phase == "bank_trade_give":
            if player.available_resource_count(resource) < rates[resource]:
                raise NetworkActionError(
                    "invalid_target", "その資源は必要枚数を支払えません。"
                )
        elif (
            resource == getattr(game, "bank_trade_give_resource", None)
            or game.bank.available(resource) <= 0
        ):
            raise NetworkActionError(
                "invalid_target", "その資源は受け取り対象にできません。"
            )
        game.select_bank_trade_resource(resource)
    else:
        _not_allowed("現在は資源を選択できません。")


def _buy_development(game: Any) -> None:
    if (
        getattr(game, "phase", None) != "main"
        or getattr(game, "winner", None) is not None
        or getattr(game, "special_phase", None) is not None
        or not getattr(game, "dice_rolled", False)
    ):
        _not_allowed("現在は発展カードを購入できません。")
    game.buy_development_card()


def _start_bank_trade(game: Any) -> None:
    player = game.get_current_player()
    if (
        getattr(game, "phase", None) != "main"
        or getattr(game, "winner", None) is not None
        or getattr(game, "special_phase", None) is not None
        or not getattr(game, "dice_rolled", False)
        or not game.has_bank_trade_option(player)
    ):
        _not_allowed("現在は銀行交易を開始できません。")
    game.start_bank_trade()


def _start_domestic_trade(game: Any) -> None:
    player = game.get_current_player()
    if (
        getattr(game, "phase", None) != "main"
        or getattr(game, "winner", None) is not None
        or getattr(game, "special_phase", None) is not None
        or not getattr(game, "dice_rolled", False)
        or not game.has_domestic_trade_option(player)
    ):
        _not_allowed("現在は国内交易を開始できません。")
    game.start_domestic_trade()


def _market_create(game: Any, args: dict[str, Any]) -> None:
    _expect_fields(args, "offer", "wanted")
    player = game.get_current_player()
    if not getattr(game, "can_create_trade_market_order", lambda _player: False)(
        player
    ):
        _not_allowed("現在は常設市場へ出品できません。")
    offer = _market_resource_bundle(args["offer"], label="出品資源")
    wanted = _market_resource_bundle(args["wanted"], label="希望資源")
    if not game.create_trade_market_order(player, offer, wanted):
        _not_allowed("常設市場へ出品できませんでした。")


def _market_fill(game: Any, args: dict[str, Any]) -> None:
    _expect_fields(args, "order_id", "revision")
    order_id, revision = _market_order_reference(args)
    player = game.get_current_player()
    if not game.fill_trade_market_order(player, order_id, revision):
        _not_allowed("常設市場の注文を購入できませんでした。")


def _market_cancel(game: Any, args: dict[str, Any]) -> None:
    _expect_fields(args, "order_id", "revision")
    order_id, revision = _market_order_reference(args)
    player = game.get_current_player()
    if not game.cancel_trade_market_order(player, order_id, revision):
        _not_allowed("常設市場の注文を取り消せませんでした。")


def _auction_create(game: Any, player: Any, args: dict[str, Any]) -> None:
    _expect_fields(args, "offer", "minimum_bid_cards")
    minimum = args["minimum_bid_cards"]
    if type(minimum) is not int or not 1 <= minimum <= 19:
        raise NetworkActionError(
            "invalid_args",
            "最低入札枚数は1〜19で指定してください。",
        )
    offer = _market_resource_bundle(args["offer"], label="競売の出品資源")
    if not game.create_trade_auction(player, offer, minimum):
        _not_allowed("公開競売を開始できませんでした。")


def _auction_bid(game: Any, player: Any, args: dict[str, Any]) -> None:
    _expect_fields(args, "auction_id", "revision", "offer")
    auction_id, revision = _auction_reference(args)
    offer = _market_resource_bundle(args["offer"], label="入札資源")
    if not game.bid_trade_auction(player, auction_id, revision, offer):
        _not_allowed("公開競売へ入札できませんでした。")


def _auction_cancel_bid(game: Any, player: Any, args: dict[str, Any]) -> None:
    _expect_fields(args, "auction_id", "revision")
    auction_id, revision = _auction_reference(args)
    if not game.cancel_trade_auction_bid(player, auction_id, revision):
        _not_allowed("公開競売の入札を取り消せませんでした。")


def _auction_accept(game: Any, player: Any, args: dict[str, Any]) -> None:
    _expect_fields(args, "auction_id", "revision", "bidder_index")
    auction_id, revision = _auction_reference(args)
    bidder_index = _validated_target_seat(game, args["bidder_index"])
    if not game.accept_trade_auction(
        player,
        auction_id,
        revision,
        bidder_index,
    ):
        _not_allowed("公開競売の落札者を決定できませんでした。")


def _auction_cancel(game: Any, player: Any, args: dict[str, Any]) -> None:
    _expect_fields(args, "auction_id", "revision")
    auction_id, revision = _auction_reference(args)
    if not game.cancel_trade_auction(player, auction_id, revision):
        _not_allowed("公開競売を取り消せませんでした。")


def _credit_borrow(game: Any, player: Any, args: dict[str, Any]) -> None:
    _expect_fields(args, "resource")
    resource = _resource_from_name(args["resource"])
    if not getattr(game, "can_borrow_resource_credit", lambda *_args: False)(
        player,
        resource,
    ):
        _not_allowed("現在はその資源を借りられません。")
    if not game.borrow_resource_credit(player, resource):
        _not_allowed("資源を借りられませんでした。")


def _credit_repay(game: Any, player: Any, args: dict[str, Any]) -> None:
    _expect_fields(args, "loan_id", "revision", "payment")
    loan_id, revision = _credit_reference(args)
    payment = _credit_payment_bundle(args["payment"])
    if not game.repay_resource_credit(player, loan_id, revision, payment):
        _not_allowed("ローンを返済できませんでした。")


def _trade_partner(game: Any, args: dict[str, Any]) -> None:
    _expect_fields(args, "seat_index")
    _require_phase(game, "domestic_trade_partner")
    target_index = _validated_target_seat(game, args["seat_index"])
    if target_index == resolve_active_actor_index(game):
        raise NetworkActionError("invalid_target", "自分自身とは交易できません。")
    if game.players[target_index].available_resource_total() <= 0:
        raise NetworkActionError("invalid_target", "その席には交換可能な資源がありません。")
    game.select_domestic_trade_partner(target_index)


def _trade_edit_side(game: Any, args: dict[str, Any]) -> None:
    _expect_fields(args, "side")
    _require_phase(game, "domestic_trade_edit")
    side = args["side"]
    if side not in ("give", "receive"):
        raise NetworkActionError("invalid_args", "交易編集方向が不正です。")
    if side == getattr(game, "domestic_trade_edit_side", None):
        raise NetworkActionError("no_state_change", "既にその編集方向です。")
    game.set_domestic_trade_edit_side(side)


def _trade_receive_operator(game: Any, args: dict[str, Any]) -> None:
    _expect_fields(args, "operator")
    _require_phase(game, "domestic_trade_edit")
    operator = args["operator"]
    if operator not in ("and", "or"):
        raise NetworkActionError("invalid_args", "OR条件の指定が不正です。")
    if operator == getattr(game, "domestic_trade_receive_operator", "and"):
        raise NetworkActionError("no_state_change", "既にその条件です。")
    if not game.set_domestic_trade_receive_operator(operator):
        _not_allowed("受け取り条件を変更できません。")


def _trade_adjust(game: Any, args: dict[str, Any]) -> None:
    _expect_fields(args, "side", "resource", "delta")
    _require_phase(game, "domestic_trade_edit")
    side = args["side"]
    if side not in ("give", "receive"):
        raise NetworkActionError("invalid_args", "交易編集方向が不正です。")
    delta = args["delta"]
    if type(delta) is not int or delta not in (-1, 1):
        raise NetworkActionError("invalid_args", "交易枚数の変更量が不正です。")
    resource = _resource_from_name(args["resource"])
    if not game.adjust_domestic_trade_resource(side, resource, delta):
        raise NetworkActionError("no_state_change", "交易条件を変更できませんでした。")


def _trade_submit(game: Any) -> None:
    _require_phase(game, "domestic_trade_edit")
    valid, message = game.validate_domestic_trade_terms()
    if not valid:
        raise NetworkActionError("action_not_allowed", message)
    if not game.submit_domestic_trade_offer():
        _not_allowed("交易条件を送信できません。")


def _trade_reveal(game: Any) -> None:
    if getattr(game, "special_phase", None) not in (
        "domestic_trade_handoff",
        "domestic_trade_counter_handoff",
    ):
        _not_allowed("現在は交易回答画面へ進めません。")
    if not game.reveal_domestic_trade_response():
        _not_allowed("交易回答画面へ進めませんでした。")


def _trade_accept(game: Any, args: dict[str, Any]) -> None:
    if getattr(game, "special_phase", None) not in (
        "domestic_trade_response",
        "domestic_trade_counter_response",
    ):
        _not_allowed("現在は交易を承諾できません。")
    operator = getattr(game, "domestic_trade_receive_operator", "and")
    if operator == "and":
        _expect_fields(args)
        selected_resource = None
    else:
        _expect_fields(args, "resource")
        selected_resource = _resource_from_name(args["resource"])
    if not game.can_execute_domestic_trade(selected_resource):
        _not_allowed("現在の手札では交易条件を満たせません。")
    game.accept_domestic_trade(selected_resource)


def _use_development(game: Any, args: dict[str, Any]) -> None:
    _expect_fields(args, "card")
    card_name = args["card"]
    method_name = _DEVELOPMENT_COMMANDS.get(card_name)
    if method_name is None:
        raise NetworkActionError("invalid_args", "発展カードの種類が不正です。")
    if (
        getattr(game, "phase", None) != "main"
        or getattr(game, "winner", None) is not None
        or getattr(game, "special_phase", None) is not None
    ):
        _not_allowed("現在は発展カードを使用できません。")
    player = game.get_current_player()
    card_type = {
        "knight": DevelopmentCardType.KNIGHT,
        "road_building": DevelopmentCardType.ROAD_BUILDING,
        "year_of_plenty": DevelopmentCardType.YEAR_OF_PLENTY,
        "monopoly": DevelopmentCardType.MONOPOLY,
    }[card_name]
    can_use, message = game.can_use_development_card(player, card_type)
    if not can_use:
        raise NetworkActionError("action_not_allowed", message)
    getattr(game, method_name)()


def _finish_road_building(game: Any) -> None:
    _require_phase(game, "road_building")
    player = game.get_current_player()
    if (
        getattr(game, "free_roads_remaining", 0) > 0
        and game.has_legal_road_placement(player)
    ):
        _not_allowed("配置可能な無料街道が残っています。")
    if not game.complete_road_building_phase():
        _not_allowed("街道建設カードの処理を終了できません。")


def _resolve_target(game: Any, value: Any, expected_kind: str) -> Any:
    if type(value) is not str:
        raise NetworkActionError("invalid_target", "盤面targetの形式が不正です。")
    targets = build_board_reference_index(game)[expected_kind]
    try:
        return targets[value]
    except KeyError as exc:
        raise NetworkActionError(
            "invalid_target", "盤面targetの形式または範囲が不正です。"
        ) from exc


def _resource_from_name(value: Any) -> ResourceType:
    if type(value) is not str:
        raise NetworkActionError("invalid_args", "資源の形式が不正です。")
    try:
        resource = ResourceType[value]
    except KeyError as exc:
        raise NetworkActionError("invalid_args", "資源の種類が不正です。") from exc
    if resource is ResourceType.DESERT:
        raise NetworkActionError("invalid_args", "砂漠は資源として選べません。")
    return resource


def _market_resource_bundle(value: Any, *, label: str) -> dict[ResourceType, int]:
    if type(value) is not dict or not value:
        raise NetworkActionError("invalid_args", f"{label}が不正です。")
    bundle = {}
    for name, amount in value.items():
        resource = _resource_from_name(name)
        if type(amount) is not int or not 1 <= amount <= 19:
            raise NetworkActionError(
                "invalid_args",
                f"{label}の枚数は1〜19で指定してください。",
            )
        bundle[resource] = amount
    return bundle


def _credit_payment_bundle(value: Any) -> dict[ResourceType, int]:
    bundle = _market_resource_bundle(value, label="返済資源")
    if sum(bundle.values()) > 3:
        raise NetworkActionError(
            "invalid_args",
            "1回の返済は合計3枚以下で指定してください。",
        )
    return bundle


def _market_order_reference(args: dict[str, Any]) -> tuple[str, int]:
    order_id = args["order_id"]
    revision = args["revision"]
    if type(order_id) is not str or not order_id:
        raise NetworkActionError("invalid_args", "市場order_idが不正です。")
    if type(revision) is not int or revision < 1:
        raise NetworkActionError("invalid_args", "市場revisionが不正です。")
    return order_id, revision


def _auction_reference(args: dict[str, Any]) -> tuple[str, int]:
    auction_id = args["auction_id"]
    revision = args["revision"]
    if type(auction_id) is not str or not auction_id:
        raise NetworkActionError("invalid_args", "auction_idが不正です。")
    if type(revision) is not int or revision < 1:
        raise NetworkActionError("invalid_args", "競売revisionが不正です。")
    return auction_id, revision


def _credit_reference(args: dict[str, Any]) -> tuple[str, int]:
    loan_id = args["loan_id"]
    revision = args["revision"]
    if type(loan_id) is not str or not loan_id:
        raise NetworkActionError("invalid_args", "loan_idが不正です。")
    if type(revision) is not int or revision < 1:
        raise NetworkActionError("invalid_args", "ローンrevisionが不正です。")
    return loan_id, revision


def _validated_target_seat(game: Any, value: Any) -> int:
    players = list(getattr(game, "players", ()))
    if type(value) is not int or not _is_index(value, players):
        raise NetworkActionError("invalid_target", "対象の席番号が不正です。")
    return value


def _validate_session_seat(players: list[Any], seat_index: int | None) -> None:
    if seat_index is None:
        raise NetworkActionError(
            "spectator_forbidden", "観戦者はゲームを操作できません。"
        )
    if type(seat_index) is not int or not _is_index(seat_index, players):
        raise NetworkActionError("invalid_session", "接続sessionの席番号が不正です。")


def _normalise_args(args: dict[str, Any] | None) -> dict[str, Any]:
    if args is None:
        return {}
    if type(args) is not dict or any(type(key) is not str for key in args):
        raise NetworkActionError("invalid_args", "操作argsはJSON objectで指定してください。")
    return args


def _expect_fields(args: dict[str, Any], *required: str) -> None:
    expected = set(required)
    actual = set(args)
    if actual != expected:
        raise NetworkActionError(
            "invalid_args", "操作argsの必須fieldまたは余分なfieldが不正です。"
        )


def _require_phase(game: Any, expected: str) -> None:
    if getattr(game, "special_phase", None) != expected:
        _not_allowed("現在のゲームフェーズではその操作を行えません。")


def _not_allowed(message: str) -> None:
    raise NetworkActionError("action_not_allowed", message)


def _is_index(index: Any, values: list[Any]) -> bool:
    return type(index) is int and 0 <= index < len(values)


def _edge_key(edge: tuple[Any, Any]) -> frozenset[int]:
    return frozenset((id(edge[0]), id(edge[1])))


def _edge_in(edge: tuple[Any, Any], candidates: Any) -> bool:
    key = _edge_key(edge)
    return any(_edge_key(candidate) == key for candidate in candidates)


def _edge_midpoint(edge: tuple[Any, Any]) -> tuple[float, float]:
    return (
        (edge[0].x + edge[1].x) / 2,
        (edge[0].y + edge[1].y) / 2,
    )


def _state_fingerprint(game: Any) -> tuple[dict[str, Any], tuple[Any, ...]]:
    state = serialize_game(game)
    # Logs and panel preferences are presentation data; a rejected command that
    # merely emits feedback must not be reported as a successful game action.
    state.pop("history", None)
    state.pop("ui", None)
    runtime = (
        getattr(game, "pending_dice_context", None),
        getattr(game, "pending_dice_roll", None),
        getattr(game, "pending_dice_player_name", None),
        getattr(getattr(game, "dice_overlay", None), "state", None),
    )
    return state, runtime
