"""Versioned runtime-state boundary for optional match variants.

Full saves retain both public and authority-private state.  Network and
spectator projections omit the ``private`` key entirely.  The standard mode
remains an empty no-op document; forecast-events keeps its future draw pile in
the private half and exposes only announced/active events.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from game.forecast_events import (
    FORECAST_EVENTS_KIND,
    ForecastEventError,
    ForecastTurnUpdate,
    active_event_ids,
    advance_forecast_documents,
    consume_active_effect,
    create_initial_forecast_documents,
    forecast_event_id,
    validate_forecast_documents,
    validate_forecast_public,
    validate_forecast_schedule,
)
from game.frontier import (
    FRONTIER_KIND,
    FrontierError,
    axial_key,
    create_initial_frontier_documents,
    reveal_frontier_tiles,
    validate_frontier_documents,
    validate_frontier_public,
)
from game.trade_market import (
    TRADE_MARKET_FORMAT,
    TRADE_MARKET_VERSION,
    MarketMutationPlan,
    MarketMutationResult,
    MarketOrder,
    TradeMarket,
    TradeMarketError,
)
from game.trade_auction import (
    TRADE_AUCTION_FORMAT,
    TRADE_AUCTION_VERSION,
    AuctionHouse,
    AuctionLot,
    AuctionMutationPlan,
    AuctionMutationResult,
    TradeAuctionError,
)
from game.resource_credit import (
    CREDIT_ADVANCE,
    LOAN_ACTIVE,
    LOAN_DELINQUENT,
    MAX_PLAYER_COUNT,
    MIN_PLAYER_COUNT,
    RESOURCE_CREDIT_FORMAT,
    RESOURCE_CREDIT_VERSION,
    CreditBook,
    CreditMutationPlan,
    CreditMutationResult,
    ResourceCreditError,
    ResourceLoan,
)
from game.variant import (
    COMPOSITE_EVENTS_ECONOMY_CATALOG,
    COMPOSITE_GRAND_CAMPAIGN_CATALOG,
    COMPOSITE_VARIANT_KIND,
    CREDIT_CATALOG,
    CREDIT_VARIANT_KIND,
    STANDARD_VARIANT_KIND,
    SUPPORTED_VARIANT_KINDS,
    TRADE2_AUCTION_CATALOG,
    TRADE2_CATALOG,
    TRADE2_VARIANT_KIND,
    VariantConfig,
)


VARIANT_STATE_FORMAT = "catan-variant-state"
VARIANT_STATE_VERSION = 1
_FULL_DOCUMENT_KEYS = frozenset(
    {
        "format",
        "version",
        "kind",
        "config_fingerprint",
        "public",
        "private",
    }
)
_PUBLIC_DOCUMENT_KEYS = _FULL_DOCUMENT_KEYS - {"private"}
_TRADE2_PUBLIC_KEYS = frozenset({"catalog", "completed_turns", "orders"})
_TRADE2_AUCTION_PUBLIC_KEYS = _TRADE2_PUBLIC_KEYS | {"auctions"}
_TRADE2_PRIVATE_KEYS = frozenset({"next_sequence"})
_TRADE2_AUCTION_PRIVATE_KEYS = _TRADE2_PRIVATE_KEYS | {"next_auction_sequence"}
_CREDIT_PUBLIC_KEYS = frozenset({"catalog", "completed_turns", "loans"})
_CREDIT_PRIVATE_KEYS = frozenset({"next_sequence"})
_COMPOSITE_PUBLIC_KEYS = frozenset(
    {"catalog", "completed_turns", "components"}
)
_COMPOSITE_PRIVATE_KEYS = frozenset({"components"})
_COMPOSITE_TIMED_COMPONENT_KINDS = (
    FORECAST_EVENTS_KIND,
    TRADE2_VARIANT_KIND,
    CREDIT_VARIANT_KIND,
)
_COMPOSITE_COMPONENT_ORDER = (
    FORECAST_EVENTS_KIND,
    FRONTIER_KIND,
    TRADE2_VARIANT_KIND,
    CREDIT_VARIANT_KIND,
)
_MAX_COMPLETED_TURNS = 2_147_483_647


class VariantStateError(ValueError):
    """Raised when runtime variant state is malformed or mismatched."""


@dataclass(frozen=True)
class CompositeTurnUpdate:
    """All child results produced at one composite turn boundary.

    The immutable result lets the game validate and commit external resource
    ledgers before publishing any of the child clocks.  No intermediate state
    with only one advanced component is ever observable.
    """

    forecast: ForecastTurnUpdate
    expired_market_orders: tuple[MarketOrder, ...]
    auction: AuctionMutationResult | None
    credit: CreditMutationResult


@dataclass(frozen=True)
class VariantState:
    """Immutable runtime state owned by the authoritative game.

    ``_projection_only`` is used only by untrusted network validation.  Such an
    object can emit another public document but can never become a full save.
    """

    format: str = VARIANT_STATE_FORMAT
    version: int = VARIANT_STATE_VERSION
    kind: str = STANDARD_VARIANT_KIND
    config_fingerprint: str = field(
        default_factory=lambda: VariantConfig.standard().fingerprint()
    )
    public: Mapping[str, Any] = field(default_factory=dict, hash=False)
    private: Mapping[str, Any] = field(default_factory=dict, hash=False)
    _projection_only: bool = field(
        default=False,
        repr=False,
        compare=False,
        hash=False,
    )

    def __post_init__(self) -> None:
        if self.format != VARIANT_STATE_FORMAT:
            raise VariantStateError("variant state format が不正です。")
        if type(self.version) is not int or self.version != VARIANT_STATE_VERSION:
            raise VariantStateError(
                f"variant state version は {VARIANT_STATE_VERSION} で指定してください。"
            )
        if not isinstance(self.kind, str) or self.kind not in SUPPORTED_VARIANT_KINDS:
            raise VariantStateError(f"未対応のvariant state kindです: {self.kind}")
        if not _valid_fingerprint(self.config_fingerprint):
            raise VariantStateError("variant設定fingerprintが不正です。")
        if not isinstance(self.public, Mapping):
            raise VariantStateError("variant state public はオブジェクトで指定してください。")
        if not isinstance(self.private, Mapping):
            raise VariantStateError("variant state private はオブジェクトで指定してください。")
        if type(self._projection_only) is not bool:
            raise VariantStateError("variant state projection flagが不正です。")

        try:
            if self.kind == STANDARD_VARIANT_KIND:
                if self.public:
                    raise VariantStateError(
                        "standard variant の public state は空にしてください。"
                    )
                if self.private:
                    raise VariantStateError(
                        "standard variant の private state は空にしてください。"
                    )
            elif self.kind == FORECAST_EVENTS_KIND:
                validate_forecast_public(self.public)
                if self._projection_only:
                    if self.private:
                        raise VariantStateError(
                            "公開variant stateにprivate情報を含められません。"
                        )
                else:
                    validate_forecast_documents(self.public, self.private)
            elif self.kind == FRONTIER_KIND:
                validate_frontier_public(self.public)
                if self._projection_only:
                    if self.private:
                        raise VariantStateError(
                            "公開variant stateにprivate情報を含められません。"
                        )
                else:
                    validate_frontier_documents(self.public, self.private)
            elif self.kind == TRADE2_VARIANT_KIND:
                _validate_trade2_public(self.public)
                if self._projection_only:
                    if self.private:
                        raise VariantStateError(
                            "公開variant stateにprivate情報を含められません。"
                        )
                else:
                    _trade_market_from_documents(self.public, self.private)
                    if self.public.get("catalog") == TRADE2_AUCTION_CATALOG:
                        _trade_auction_from_documents(self.public, self.private)
            elif self.kind == CREDIT_VARIANT_KIND:
                _validate_credit_public(self.public)
                if self._projection_only:
                    if self.private:
                        raise VariantStateError(
                            "公開variant stateにprivate情報を含められません。"
                        )
                else:
                    _credit_book_from_documents(self.public, self.private)
            elif self.kind == COMPOSITE_VARIANT_KIND:
                _validate_composite_documents(
                    self.public,
                    self.private,
                    projection_only=self._projection_only,
                    config=_composite_config_from_public(self.public),
                )
            else:  # pragma: no cover - supported-kind guard rejects this first.
                raise VariantStateError(
                    f"未対応のvariant state kindです: {self.kind}"
                )
        except ForecastEventError as exc:
            raise VariantStateError("forecast variant stateが不正です。") from exc
        except FrontierError as exc:
            raise VariantStateError("frontier variant stateが不正です。") from exc
        except (TradeMarketError, TradeAuctionError) as exc:
            raise VariantStateError("trade2 variant stateが不正です。") from exc
        except ResourceCreditError as exc:
            raise VariantStateError("credit variant stateが不正です。") from exc

        # Never retain a caller-owned mutable document in a room or match.
        object.__setattr__(self, "public", _freeze_json(self.public))
        object.__setattr__(self, "private", _freeze_json(self.private))

    @classmethod
    def initial(
        cls,
        config: VariantConfig,
        *,
        deck_seed: str | None = None,
        frontier_robber_axial: tuple[int, int] | None = None,
        forecast_harbor_ids: tuple[str, ...] | list[str] | None = None,
    ) -> VariantState:
        """Create a new runtime state bound to a validated config."""

        if not isinstance(config, VariantConfig):
            raise VariantStateError("variant設定が不正です。")
        if config.kind == STANDARD_VARIANT_KIND:
            public, private = {}, {}
        elif config.kind == FORECAST_EVENTS_KIND:
            try:
                public, private = create_initial_forecast_documents(
                    config.options,
                    deck_seed=deck_seed,
                    revealed_harbor_ids=forecast_harbor_ids,
                )
            except ForecastEventError as exc:
                raise VariantStateError("forecast variant stateを作成できません。") from exc
        elif config.kind == FRONTIER_KIND:
            if frontier_robber_axial is None:
                raise VariantStateError("frontierには初期盗賊タイルが必要です。")
            try:
                public, private = create_initial_frontier_documents(
                    config.options,
                    robber_axial=frontier_robber_axial,
                )
            except FrontierError as exc:
                raise VariantStateError("frontier variant stateを作成できません。") from exc
        elif config.kind == TRADE2_VARIANT_KIND:
            public = {
                "catalog": config.options["catalog"],
                "completed_turns": 0,
                "orders": [],
            }
            private = {"next_sequence": 0}
            if config.options["catalog"] == TRADE2_AUCTION_CATALOG:
                public["auctions"] = []
                private["next_auction_sequence"] = 0
        elif config.kind == CREDIT_VARIANT_KIND:
            public = {
                "catalog": config.options["catalog"],
                "completed_turns": 0,
                "loans": [],
            }
            private = {"next_sequence": 0}
        elif config.kind == COMPOSITE_VARIANT_KIND:
            component_public: dict[str, Any] = {}
            component_private: dict[str, Any] = {}
            for kind in _composite_component_kinds(config):
                child_config = config.component_config(kind)
                if child_config is None:  # pragma: no cover - fixed catalog invariant.
                    raise VariantStateError("composite component設定が不正です。")
                child_state = cls.initial(
                    child_config,
                    deck_seed=(deck_seed if kind == FORECAST_EVENTS_KIND else None),
                    frontier_robber_axial=(
                        frontier_robber_axial if kind == FRONTIER_KIND else None
                    ),
                    forecast_harbor_ids=(
                        forecast_harbor_ids
                        if kind == FORECAST_EVENTS_KIND
                        else None
                    ),
                )
                component_public[kind] = _thaw_json(child_state.public)
                component_private[kind] = _thaw_json(child_state.private)
            public = {
                "catalog": config.options["catalog"],
                "completed_turns": 0,
                "components": component_public,
            }
            private = {"components": component_private}
        else:  # pragma: no cover - VariantConfig rejects this first.
            raise VariantStateError(f"未対応のvariant state kindです: {config.kind}")
        return cls(
            kind=config.kind,
            config_fingerprint=config.fingerprint(),
            public=public,
            private=private,
        )

    @classmethod
    def standard(cls) -> VariantState:
        """Return the empty state used by current official rules."""

        return cls.initial(VariantConfig.standard())

    @classmethod
    def from_document(
        cls,
        document: Mapping[str, Any] | None,
        *,
        config: VariantConfig | None = None,
    ) -> VariantState:
        """Parse a strict full-save document, or a legacy standard save."""

        validated_config = _optional_config(config)
        if document is None:
            if (
                validated_config is not None
                and validated_config.kind != STANDARD_VARIANT_KIND
            ):
                raise VariantStateError(
                    "standard以外のvariantにはruntime stateが必要です。"
                )
            state = cls.standard()
        else:
            _validate_document_keys(document, _FULL_DOCUMENT_KEYS, "variant state")
            state = cls(
                format=document["format"],
                version=document["version"],
                kind=document["kind"],
                config_fingerprint=document["config_fingerprint"],
                public=document["public"],
                private=document["private"],
            )
        if validated_config is not None:
            state.validate_config(validated_config)
        return state

    @classmethod
    def from_public_document(
        cls,
        document: Mapping[str, Any],
        *,
        config: VariantConfig | None = None,
    ) -> VariantState:
        """Parse a strict public projection where ``private`` is forbidden."""

        validated_config = _optional_config(config)
        _validate_document_keys(
            document,
            _PUBLIC_DOCUMENT_KEYS,
            "public variant state",
        )
        state = cls(
            format=document["format"],
            version=document["version"],
            kind=document["kind"],
            config_fingerprint=document["config_fingerprint"],
            public=document["public"],
            private={},
            _projection_only=True,
        )
        if validated_config is not None:
            state.validate_config(validated_config)
        return state

    def validate_config(self, config: VariantConfig) -> None:
        """Require this state to belong to exactly the supplied config."""

        if not isinstance(config, VariantConfig):
            raise VariantStateError("variant設定が不正です。")
        if self.kind != config.kind:
            raise VariantStateError("variant state kindが設定と一致しません。")
        if self.config_fingerprint != config.fingerprint():
            raise VariantStateError(
                "variant state fingerprintが設定と一致しません。"
            )
        if self.kind == FORECAST_EVENTS_KIND:
            try:
                if not self._projection_only:
                    validate_forecast_documents(
                        self.public,
                        self.private,
                        options=config.options,
                    )
                validate_forecast_schedule(self.public, config.options)
            except ForecastEventError as exc:
                raise VariantStateError(
                    "forecast event周期が設定と一致しません。"
                ) from exc
        elif self.kind == FRONTIER_KIND:
            try:
                validate_frontier_public(self.public, options=config.options)
                if not self._projection_only:
                    validate_frontier_documents(
                        self.public,
                        self.private,
                        options=config.options,
                    )
            except FrontierError as exc:
                raise VariantStateError(
                    "frontier盤面catalogが設定と一致しません。"
                ) from exc
        elif self.kind == TRADE2_VARIANT_KIND:
            if self.public.get("catalog") != config.options.get("catalog"):
                raise VariantStateError("trade2 catalogが設定と一致しません。")
            expected_ttl = config.options["order_ttl_turns"]
            if any(
                order.expires_turn - order.created_turn != expected_ttl
                for order in _trade2_public_orders(self.public)
            ):
                raise VariantStateError("trade2注文期限が設定と一致しません。")
            if config.options["catalog"] == TRADE2_AUCTION_CATALOG:
                expected_auction_ttl = config.options["auction_ttl_turns"]
                if any(
                    auction.expires_turn - auction.created_turn
                    != expected_auction_ttl
                    for auction in _trade2_public_auctions(self.public)
                ):
                    raise VariantStateError(
                        "trade2競売期限が設定と一致しません。"
                    )
        elif self.kind == CREDIT_VARIANT_KIND:
            if self.public.get("catalog") != config.options.get("catalog"):
                raise VariantStateError("credit catalogが設定と一致しません。")
        elif self.kind == COMPOSITE_VARIANT_KIND:
            _validate_composite_documents(
                self.public,
                self.private,
                projection_only=self._projection_only,
                config=config,
            )

    def component_state(self, kind: str) -> VariantState | None:
        """Return one child state while keeping direct variants source-compatible."""

        if not isinstance(kind, str):
            return None
        if self.kind != COMPOSITE_VARIANT_KIND:
            return self if self.kind == kind else None
        composite_config = _composite_config_from_public(self.public)
        child_config = composite_config.component_config(kind)
        if child_config is None:
            return None
        component_public = self.public["components"][kind]
        component_private = (
            {}
            if self._projection_only
            else self.private["components"][kind]
        )
        return type(self)(
            kind=kind,
            config_fingerprint=child_config.fingerprint(),
            public=_thaw_json(component_public),
            private=_thaw_json(component_private),
            _projection_only=self._projection_only,
        )

    def with_component_state(
        self,
        config: VariantConfig,
        kind: str,
        component: VariantState,
    ) -> VariantState:
        """Replace exactly one full child state in an authority document.

        The outer clock and every non-target child are retained unchanged.
        Clock-changing turn advancement will later update all three children
        in one operation; this adapter is for same-boundary child mutations.
        """

        self.ensure_full()
        self.validate_config(config)
        if not isinstance(component, VariantState):
            raise VariantStateError("component stateが不正です。")
        component.ensure_full()

        if self.kind != COMPOSITE_VARIANT_KIND:
            if kind != self.kind:
                raise VariantStateError("このvariantには指定componentがありません。")
            component.validate_config(config)
            return component

        child_config = config.component_config(kind)
        if child_config is None or kind not in _composite_component_kinds(config):
            raise VariantStateError("このcompositeには指定componentがありません。")
        component.validate_config(child_config)
        child_clock = component.public.get("completed_turns")
        if (
            kind in _COMPOSITE_TIMED_COMPONENT_KINDS
            and child_clock != self.public["completed_turns"]
        ):
            raise VariantStateError("componentの完了手番がcompositeと一致しません。")

        public = _thaw_json(self.public)
        private = _thaw_json(self.private)
        public["components"][kind] = _thaw_json(component.public)
        private["components"][kind] = _thaw_json(component.private)
        return self._with_documents(public, private)

    def replace_component_state(
        self,
        config: VariantConfig,
        kind: str,
        component: VariantState,
    ) -> VariantState:
        """Alias spelling for :meth:`with_component_state`."""

        return self.with_component_state(config, kind, component)

    def ensure_full(self) -> None:
        """Reject a viewer-only projection at an authority/save boundary."""

        if self._projection_only:
            raise VariantStateError("公開variant stateは完全保存に利用できません。")

    def advance_forecast_turn(
        self,
        config: VariantConfig,
        *,
        player_count: int,
        revealed_harbor_ids: tuple[str, ...] | list[str] | None = None,
    ) -> tuple[VariantState, ForecastTurnUpdate]:
        """Advance one completed turn in forecast-events mode."""

        self.ensure_full()
        self.validate_config(config)
        if self.kind != FORECAST_EVENTS_KIND:
            raise VariantStateError("forecast_events以外では手番を進められません。")
        try:
            public, private, update = advance_forecast_documents(
                _thaw_json(self.public),
                _thaw_json(self.private),
                config.options,
                player_count=player_count,
                revealed_harbor_ids=revealed_harbor_ids,
            )
        except ForecastEventError as exc:
            raise VariantStateError("forecast eventの手番更新に失敗しました。") from exc
        return self._with_documents(public, private), update

    def advance_composite_turn(
        self,
        config: VariantConfig,
        *,
        player_count: int,
        revealed_harbor_ids: tuple[str, ...] | list[str] | None = None,
    ) -> tuple[VariantState, CompositeTurnUpdate]:
        """Advance every fixed child from clock ``N`` to ``N + 1`` atomically."""

        self.ensure_full()
        self.validate_config(config)
        if self.kind != COMPOSITE_VARIANT_KIND:
            raise VariantStateError(
                "composite以外では複合手番を進められません。"
            )
        completed_turns = self.public["completed_turns"] + 1
        if completed_turns > _MAX_COMPLETED_TURNS:
            raise VariantStateError("compositeの完了手番数が上限を超えました。")

        forecast_config = config.component_config(FORECAST_EVENTS_KIND)
        trade_config = config.component_config(TRADE2_VARIANT_KIND)
        credit_config = config.component_config(CREDIT_VARIANT_KIND)
        if forecast_config is None or trade_config is None or credit_config is None:
            raise VariantStateError("composite component設定が不足しています。")

        forecast_state = self.component_state(FORECAST_EVENTS_KIND)
        trade_state = self.component_state(TRADE2_VARIANT_KIND)
        credit_state = self.component_state(CREDIT_VARIANT_KIND)
        if forecast_state is None or trade_state is None or credit_state is None:
            raise VariantStateError("composite component stateが不足しています。")

        next_forecast, forecast_update = forecast_state.advance_forecast_turn(
            forecast_config,
            player_count=player_count,
            revealed_harbor_ids=revealed_harbor_ids,
        )
        next_trade, expired_orders, auction_update = (
            trade_state.advance_trade2_turn(trade_config)
        )
        next_credit, credit_update = credit_state.advance_credit_turn(
            credit_config
        )
        children = {
            FORECAST_EVENTS_KIND: next_forecast,
            TRADE2_VARIANT_KIND: next_trade,
            CREDIT_VARIANT_KIND: next_credit,
        }
        if any(
            child.public.get("completed_turns") != completed_turns
            for child in children.values()
        ):
            raise VariantStateError("composite componentの更新時刻が一致しません。")

        public = _thaw_json(self.public)
        private = _thaw_json(self.private)
        public["completed_turns"] = completed_turns
        for kind, child in children.items():
            public["components"][kind] = _thaw_json(child.public)
            private["components"][kind] = _thaw_json(child.private)
        next_state = self._with_documents(public, private)
        return next_state, CompositeTurnUpdate(
            forecast=forecast_update,
            expired_market_orders=expired_orders,
            auction=auction_update,
            credit=credit_update,
        )

    def trade_market(self) -> TradeMarket:
        """Return the complete immutable standing-market authority state."""

        self.ensure_full()
        if self.kind != TRADE2_VARIANT_KIND:
            raise VariantStateError("trade2以外には常設市場がありません。")
        try:
            return _trade_market_from_documents(self.public, self.private)
        except TradeMarketError as exc:  # pragma: no cover - constructor validates.
            raise VariantStateError("常設市場stateが不正です。") from exc

    def credit_book(self) -> CreditBook:
        """Return the complete immutable resource-credit authority state."""

        self.ensure_full()
        if self.kind != CREDIT_VARIANT_KIND:
            raise VariantStateError("credit以外には信用台帳がありません。")
        try:
            return _credit_book_from_documents(self.public, self.private)
        except ResourceCreditError as exc:  # pragma: no cover - constructor validates.
            raise VariantStateError("信用台帳stateが不正です。") from exc

    def validate_credit_player_count(self, player_count: int) -> None:
        """Cross-check every public liability against the match seat count."""

        if self.kind != CREDIT_VARIANT_KIND:
            raise VariantStateError("credit以外には信用台帳がありません。")
        if (
            type(player_count) is not int
            or not MIN_PLAYER_COUNT <= player_count <= MAX_PLAYER_COUNT
        ):
            raise VariantStateError("credit参加人数は2〜4人で指定してください。")
        for loan in _credit_public_loans(self.public):
            if loan.borrower_index >= player_count:
                raise VariantStateError("信用台帳の借り手が参加席に存在しません。")
            if loan.due_turn - loan.opened_turn != player_count:
                raise VariantStateError("信用台帳の返済期限が参加人数と一致しません。")

    def apply_credit_plan(
        self,
        config: VariantConfig,
        plan: CreditMutationPlan,
    ) -> tuple[VariantState, CreditMutationResult]:
        """Apply one borrow/repayment plan without committing bank resources."""

        self.ensure_full()
        self.validate_config(config)
        if self.kind != CREDIT_VARIANT_KIND:
            raise VariantStateError("credit以外では信用操作を実行できません。")
        if not isinstance(plan, CreditMutationPlan):
            raise VariantStateError("信用操作planが不正です。")
        if plan.operation == CREDIT_ADVANCE:
            raise VariantStateError(
                "信用期限の更新にはadvance_credit_turnを使用してください。"
            )
        if plan.current_turn != self.public["completed_turns"]:
            raise VariantStateError("信用操作の完了手番がstateと一致しません。")
        try:
            result = self.credit_book().apply(plan)
        except ResourceCreditError as exc:
            raise VariantStateError("信用台帳の更新に失敗しました。") from exc
        return self._with_credit_book(result.book), result

    def advance_credit_turn(
        self,
        config: VariantConfig,
    ) -> tuple[VariantState, CreditMutationResult]:
        """Advance one completed turn and transition newly overdue loans."""

        self.ensure_full()
        self.validate_config(config)
        if self.kind != CREDIT_VARIANT_KIND:
            raise VariantStateError("credit以外では信用期限を進められません。")
        current_turn = self.public["completed_turns"]
        completed_turns = current_turn + 1
        if completed_turns > _MAX_COMPLETED_TURNS:
            raise VariantStateError("creditの完了手番数が上限を超えました。")
        try:
            book = self.credit_book()
            # A loan due at the current boundary remains repayable throughout
            # that borrower's turn.  Transition it only as that turn closes,
            # then publish the incremented completed-turn clock.
            result = book.apply(book.plan_advance(current_turn=current_turn))
        except ResourceCreditError as exc:
            raise VariantStateError("信用期限の更新に失敗しました。") from exc
        return (
            self._with_credit_book(
                result.book,
                completed_turns=completed_turns,
            ),
            result,
        )

    def apply_trade_market_plan(
        self,
        config: VariantConfig,
        plan: MarketMutationPlan,
    ) -> tuple[VariantState, MarketMutationResult]:
        """Apply one optimistic market plan without committing player resources."""

        self.ensure_full()
        self.validate_config(config)
        if self.kind != TRADE2_VARIANT_KIND:
            raise VariantStateError("trade2以外では市場操作を実行できません。")
        try:
            result = self.trade_market().apply(plan)
        except TradeMarketError as exc:
            raise VariantStateError("常設市場の更新に失敗しました。") from exc
        return self._with_trade_market(result.market), result

    def advance_trade_market_turn(
        self,
        config: VariantConfig,
    ) -> tuple[VariantState, tuple[MarketOrder, ...]]:
        """Advance one completed turn and remove every newly expired order."""

        self.ensure_full()
        self.validate_config(config)
        if self.kind != TRADE2_VARIANT_KIND:
            raise VariantStateError("trade2以外では市場期限を進められません。")
        if config.options.get("catalog") == TRADE2_AUCTION_CATALOG:
            raise VariantStateError(
                "公開競売を含むtrade2はadvance_trade2_turnを使用してください。"
            )
        completed_turns = self.public["completed_turns"] + 1
        if completed_turns > _MAX_COMPLETED_TURNS:
            raise VariantStateError("trade2の完了手番数が上限を超えました。")
        try:
            market = self.trade_market()
            result = market.apply(
                market.plan_expire(current_turn=completed_turns)
            )
        except TradeMarketError as exc:
            raise VariantStateError("常設市場の期限更新に失敗しました。") from exc
        return (
            self._with_trade_market(
                result.market,
                completed_turns=completed_turns,
            ),
            result.removed_orders,
        )

    def advance_trade2_turn(
        self,
        config: VariantConfig,
    ) -> tuple[
        VariantState,
        tuple[MarketOrder, ...],
        AuctionMutationResult | None,
    ]:
        """Advance market and auction expiry at one atomic turn boundary."""

        self.ensure_full()
        self.validate_config(config)
        if self.kind != TRADE2_VARIANT_KIND:
            raise VariantStateError("trade2以外では交易期限を進められません。")
        if config.options.get("catalog") != TRADE2_AUCTION_CATALOG:
            next_state, expired_orders = self.advance_trade_market_turn(config)
            return next_state, expired_orders, None

        completed_turns = self.public["completed_turns"] + 1
        if completed_turns > _MAX_COMPLETED_TURNS:
            raise VariantStateError("trade2の完了手番数が上限を超えました。")
        try:
            market = self.trade_market()
            market_result = market.apply(
                market.plan_expire(current_turn=completed_turns)
            )
            house = self.trade_auction()
            auction_result = house.apply(
                house.plan_expire(current_turn=completed_turns)
            )
        except (TradeMarketError, TradeAuctionError) as exc:
            raise VariantStateError("trade2の期限更新に失敗しました。") from exc

        market_document = market_result.market.to_document()
        auction_document = auction_result.house.to_document()
        return (
            self._with_documents(
                {
                    "catalog": self.public["catalog"],
                    "completed_turns": completed_turns,
                    "orders": market_document["open_orders"],
                    "auctions": auction_document["open_auctions"],
                },
                {
                    "next_sequence": market_document["next_sequence"],
                    "next_auction_sequence": auction_document["next_sequence"],
                },
            ),
            market_result.removed_orders,
            auction_result,
        )

    def trade_auction(self) -> AuctionHouse:
        """Return the complete immutable public-auction authority state."""

        self.ensure_full()
        if (
            self.kind != TRADE2_VARIANT_KIND
            or self.public.get("catalog") != TRADE2_AUCTION_CATALOG
        ):
            raise VariantStateError("このtrade2設定には公開競売がありません。")
        try:
            return _trade_auction_from_documents(self.public, self.private)
        except TradeAuctionError as exc:  # pragma: no cover - constructor validates.
            raise VariantStateError("公開競売stateが不正です。") from exc

    def apply_trade_auction_plan(
        self,
        config: VariantConfig,
        plan: AuctionMutationPlan,
    ) -> tuple[VariantState, AuctionMutationResult]:
        """Apply one optimistic auction plan without committing resources."""

        self.ensure_full()
        self.validate_config(config)
        if config.options.get("catalog") != TRADE2_AUCTION_CATALOG:
            raise VariantStateError("このtrade2設定では公開競売を操作できません。")
        try:
            result = self.trade_auction().apply(plan)
        except TradeAuctionError as exc:
            raise VariantStateError("公開競売の更新に失敗しました。") from exc
        return self._with_trade_auction(result.house), result

    def consume_forecast_effect(self, event_id: str) -> tuple[VariantState, bool]:
        """Consume a triggered public effect without changing the secret deck."""

        self.ensure_full()
        if self.kind != FORECAST_EVENTS_KIND:
            return self, False
        try:
            public, consumed = consume_active_effect(self.public, event_id)
        except ForecastEventError as exc:
            raise VariantStateError("forecast event効果を消費できません。") from exc
        if not consumed:
            return self, False
        return self._with_documents(public, _thaw_json(self.private)), True

    def active_forecast_event_ids(self) -> tuple[str, ...]:
        if self.kind != FORECAST_EVENTS_KIND:
            return ()
        try:
            return active_event_ids(self.public)
        except ForecastEventError as exc:  # pragma: no cover - constructor validates.
            raise VariantStateError("forecast active stateが不正です。") from exc

    def next_forecast_event_id(self) -> str | None:
        if self.kind != FORECAST_EVENTS_KIND:
            return None
        try:
            return forecast_event_id(self.public)
        except ForecastEventError as exc:  # pragma: no cover - constructor validates.
            raise VariantStateError("forecast stateが不正です。") from exc

    def next_forecast_parameters(self) -> Mapping[str, Any]:
        if self.kind != FORECAST_EVENTS_KIND:
            return MappingProxyType({})
        parameters = self.public["forecast"].get("parameters", {})
        return parameters

    def active_forecast_effect(self, event_id: str) -> Mapping[str, Any] | None:
        if self.kind != FORECAST_EVENTS_KIND:
            return None
        return next(
            (
                effect
                for effect in self.public["active_effects"]
                if effect["event_id"] == event_id
            ),
            None,
        )

    def is_frontier_tile_revealed(self, axial: tuple[int, int]) -> bool:
        if self.kind != FRONTIER_KIND:
            return True
        try:
            return axial_key(axial) in self.public["revealed_tiles"]
        except FrontierError as exc:
            raise VariantStateError("frontier tile座標が不正です。") from exc

    def reveal_frontier_tiles(
        self,
        axials: tuple[tuple[int, int], ...] | list[tuple[int, int]],
    ) -> tuple[VariantState, tuple[tuple[int, int], ...]]:
        """Return a full state with authority-approved tiles made public."""

        self.ensure_full()
        if self.kind != FRONTIER_KIND:
            return self, ()
        try:
            public, private, revealed = reveal_frontier_tiles(
                self.public,
                self.private,
                axials,
            )
        except FrontierError as exc:
            raise VariantStateError("frontier tileを公開できません。") from exc
        if not revealed:
            return self, ()
        return self._with_documents(public, private), revealed

    def to_document(self) -> dict[str, Any]:
        """Return a fresh full-save document including private state."""

        self.ensure_full()
        return {
            "format": self.format,
            "version": self.version,
            "kind": self.kind,
            "config_fingerprint": self.config_fingerprint,
            "public": _thaw_json(self.public),
            "private": _thaw_json(self.private),
        }

    def to_public_document(self) -> dict[str, Any]:
        """Return a fresh viewer-safe document with no ``private`` key."""

        return {
            "format": self.format,
            "version": self.version,
            "kind": self.kind,
            "config_fingerprint": self.config_fingerprint,
            "public": _thaw_json(self.public),
        }

    def _with_documents(
        self,
        public: Mapping[str, Any],
        private: Mapping[str, Any],
    ) -> VariantState:
        return type(self)(
            format=self.format,
            version=self.version,
            kind=self.kind,
            config_fingerprint=self.config_fingerprint,
            public=public,
            private=private,
        )

    def _with_trade_market(
        self,
        market: TradeMarket,
        *,
        completed_turns: int | None = None,
    ) -> VariantState:
        if not isinstance(market, TradeMarket):
            raise VariantStateError("常設市場stateが不正です。")
        if completed_turns is None:
            completed_turns = self.public["completed_turns"]
        market_document = market.to_document()
        return self._with_documents(
            {
                "catalog": self.public["catalog"],
                "completed_turns": completed_turns,
                "orders": market_document["open_orders"],
                **(
                    {"auctions": _thaw_json(self.public["auctions"])}
                    if self.public["catalog"] == TRADE2_AUCTION_CATALOG
                    else {}
                ),
            },
            {
                "next_sequence": market_document["next_sequence"],
                **(
                    {
                        "next_auction_sequence": self.private[
                            "next_auction_sequence"
                        ]
                    }
                    if self.public["catalog"] == TRADE2_AUCTION_CATALOG
                    else {}
                ),
            },
        )

    def _with_credit_book(
        self,
        book: CreditBook,
        *,
        completed_turns: int | None = None,
    ) -> VariantState:
        if not isinstance(book, CreditBook):
            raise VariantStateError("信用台帳stateが不正です。")
        if completed_turns is None:
            completed_turns = self.public["completed_turns"]
        document = book.to_document()
        return self._with_documents(
            {
                "catalog": self.public["catalog"],
                "completed_turns": completed_turns,
                "loans": document["open_loans"],
            },
            {"next_sequence": document["next_sequence"]},
        )

    def _with_trade_auction(self, house: AuctionHouse) -> VariantState:
        if not isinstance(house, AuctionHouse):
            raise VariantStateError("公開競売stateが不正です。")
        house_document = house.to_document()
        return self._with_documents(
            {
                "catalog": self.public["catalog"],
                "completed_turns": self.public["completed_turns"],
                "orders": _thaw_json(self.public["orders"]),
                "auctions": house_document["open_auctions"],
            },
            {
                "next_sequence": self.private["next_sequence"],
                "next_auction_sequence": house_document["next_sequence"],
            },
        )

    def __copy__(self) -> VariantState:
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> VariantState:
        memo[id(self)] = self
        return self

    def __reduce__(self):
        factory = (
            type(self).from_public_document
            if self._projection_only
            else type(self).from_document
        )
        document = (
            self.to_public_document()
            if self._projection_only
            else self.to_document()
        )
        return (factory, (document,))


def _optional_config(config: VariantConfig | None) -> VariantConfig | None:
    if config is not None and not isinstance(config, VariantConfig):
        raise VariantStateError("variant設定が不正です。")
    return config


def _composite_config_from_public(public: Mapping[str, Any]) -> VariantConfig:
    """Recover one fixed composite config without accepting component options."""

    if not isinstance(public, Mapping):
        raise VariantStateError("composite public stateが不正です。")
    catalog = public.get("catalog")
    if catalog == COMPOSITE_EVENTS_ECONOMY_CATALOG:
        return VariantConfig.composite_events_economy()
    if catalog == COMPOSITE_GRAND_CAMPAIGN_CATALOG:
        return VariantConfig.composite_grand_campaign()
    raise VariantStateError("未対応のcomposite state catalogです。")


def _composite_component_kinds(config: VariantConfig) -> tuple[str, ...]:
    if not isinstance(config, VariantConfig) or config.kind != COMPOSITE_VARIANT_KIND:
        raise VariantStateError("composite設定が不正です。")
    kinds = tuple(
        kind
        for kind in _COMPOSITE_COMPONENT_ORDER
        if config.component_config(kind) is not None
    )
    if not set(_COMPOSITE_TIMED_COMPONENT_KINDS).issubset(kinds):
        raise VariantStateError("composite timed component設定が不足しています。")
    return kinds


def _validate_composite_documents(
    public: Mapping[str, Any],
    private: Mapping[str, Any],
    *,
    projection_only: bool,
    config: VariantConfig,
) -> None:
    if not isinstance(config, VariantConfig) or config.kind != COMPOSITE_VARIANT_KIND:
        raise VariantStateError("composite設定が不正です。")
    if config.options.get("catalog") not in (
        COMPOSITE_EVENTS_ECONOMY_CATALOG,
        COMPOSITE_GRAND_CAMPAIGN_CATALOG,
    ):
        raise VariantStateError("未対応のcomposite catalogです。")
    if not isinstance(public, Mapping) or set(public) != _COMPOSITE_PUBLIC_KEYS:
        raise VariantStateError("composite public stateの項目が不正です。")
    if public.get("catalog") != config.options.get("catalog"):
        raise VariantStateError("未対応のcomposite state catalogです。")
    completed_turns = public.get("completed_turns")
    if (
        type(completed_turns) is not int
        or not 0 <= completed_turns <= _MAX_COMPLETED_TURNS
    ):
        raise VariantStateError("composite completed_turnsが不正です。")

    public_components = public.get("components")
    component_kinds = _composite_component_kinds(config)
    component_keys = frozenset(component_kinds)
    if (
        not isinstance(public_components, Mapping)
        or set(public_components) != component_keys
    ):
        raise VariantStateError("composite public componentsが不正です。")

    if projection_only:
        if private:
            raise VariantStateError("公開variant stateにprivate情報を含められません。")
        private_components: Mapping[str, Any] = {}
    else:
        if not isinstance(private, Mapping) or set(private) != _COMPOSITE_PRIVATE_KEYS:
            raise VariantStateError("composite private stateの項目が不正です。")
        private_components = private.get("components")
        if (
            not isinstance(private_components, Mapping)
            or set(private_components) != component_keys
        ):
            raise VariantStateError("composite private componentsが不正です。")

    for kind in component_kinds:
        child_config = config.component_config(kind)
        if child_config is None:  # pragma: no cover - fixed catalog invariant.
            raise VariantStateError("composite component設定が不正です。")
        try:
            child = VariantState(
                kind=kind,
                config_fingerprint=child_config.fingerprint(),
                public=public_components[kind],
                private=(
                    {}
                    if projection_only
                    else private_components[kind]
                ),
                _projection_only=projection_only,
            )
            child.validate_config(child_config)
        except (KeyError, TypeError, VariantStateError) as exc:
            raise VariantStateError(
                f"composite {kind} component stateが不正です。"
            ) from exc
        if (
            kind in _COMPOSITE_TIMED_COMPONENT_KINDS
            and child.public.get("completed_turns") != completed_turns
        ):
            raise VariantStateError(
                f"composite {kind} componentの完了手番が一致しません。"
            )


def _validate_credit_public(public: Mapping[str, Any]) -> None:
    if not isinstance(public, Mapping):
        raise VariantStateError("credit public stateはオブジェクトで指定してください。")
    if set(public) != _CREDIT_PUBLIC_KEYS:
        raise VariantStateError("credit public stateの項目が不正です。")
    if public.get("catalog") != CREDIT_CATALOG:
        raise VariantStateError("未対応のcredit catalogです。")
    completed_turns = public.get("completed_turns")
    if (
        type(completed_turns) is not int
        or not 0 <= completed_turns <= _MAX_COMPLETED_TURNS
    ):
        raise VariantStateError("credit completed_turnsが不正です。")
    loans = public.get("loans")
    if not isinstance(loans, (list, tuple)):
        raise VariantStateError("credit loansは配列で指定してください。")
    parsed = _credit_public_loans(public)
    for loan in parsed:
        if loan.opened_turn > completed_turns:
            raise VariantStateError("creditローンの開始手番が未来です。")
        if loan.status == LOAN_ACTIVE and completed_turns > loan.due_turn:
            raise VariantStateError("期限超過したactiveローンをstateに残せません。")
        if loan.status == LOAN_DELINQUENT and completed_turns <= loan.due_turn:
            raise VariantStateError("期限前のローンを延滞状態にできません。")
    try:
        CreditBook(
            next_sequence=_minimum_credit_next_sequence(parsed),
            open_loans=parsed,
        )
    except ResourceCreditError as exc:
        raise VariantStateError("credit公開ローンが不正です。") from exc


def _credit_public_loans(public: Mapping[str, Any]) -> tuple[ResourceLoan, ...]:
    try:
        return tuple(
            ResourceLoan.from_document(_thaw_json(loan))
            for loan in public["loans"]
        )
    except (KeyError, TypeError, ResourceCreditError) as exc:
        raise VariantStateError("credit公開ローンが不正です。") from exc


def _minimum_credit_next_sequence(loans: tuple[ResourceLoan, ...]) -> int:
    if not loans:
        return 0
    return max(int(loan.loan_id.removeprefix("loan-")) for loan in loans) + 1


def _credit_book_from_documents(
    public: Mapping[str, Any],
    private: Mapping[str, Any],
) -> CreditBook:
    _validate_credit_public(public)
    if not isinstance(private, Mapping) or set(private) != _CREDIT_PRIVATE_KEYS:
        raise VariantStateError("credit private stateの項目が不正です。")
    document = {
        "format": RESOURCE_CREDIT_FORMAT,
        "version": RESOURCE_CREDIT_VERSION,
        "next_sequence": private["next_sequence"],
        "open_loans": _thaw_json(public["loans"]),
    }
    return CreditBook.from_document(document)


def _validate_trade2_public(public: Mapping[str, Any]) -> None:
    if not isinstance(public, Mapping):
        raise VariantStateError("trade2 public stateはオブジェクトで指定してください。")
    catalog = public.get("catalog")
    expected_keys = (
        _TRADE2_AUCTION_PUBLIC_KEYS
        if catalog == TRADE2_AUCTION_CATALOG
        else _TRADE2_PUBLIC_KEYS
    )
    if set(public) != expected_keys:
        raise VariantStateError("trade2 public stateの項目が不正です。")
    if catalog not in (TRADE2_CATALOG, TRADE2_AUCTION_CATALOG):
        raise VariantStateError("未対応のtrade2 catalogです。")
    completed_turns = public.get("completed_turns")
    if (
        type(completed_turns) is not int
        or not 0 <= completed_turns <= _MAX_COMPLETED_TURNS
    ):
        raise VariantStateError("trade2 completed_turnsが不正です。")
    orders = public.get("orders")
    if not isinstance(orders, (list, tuple)):
        raise VariantStateError("trade2 ordersは配列で指定してください。")
    parsed = _trade2_public_orders(public)
    if any(order.is_expired(completed_turns) for order in parsed):
        raise VariantStateError("期限切れのtrade2注文を公開stateに残せません。")
    next_sequence = _minimum_trade2_next_sequence(parsed)
    try:
        TradeMarket(next_sequence=next_sequence, open_orders=parsed)
    except TradeMarketError as exc:
        raise VariantStateError("trade2公開注文が不正です。") from exc
    if catalog == TRADE2_AUCTION_CATALOG:
        auctions = _trade2_public_auctions(public)
        if any(auction.is_expired(completed_turns) for auction in auctions):
            raise VariantStateError("期限切れの公開競売をstateに残せません。")
        try:
            AuctionHouse(
                next_sequence=_minimum_trade2_next_auction_sequence(auctions),
                open_auctions=auctions,
            )
        except TradeAuctionError as exc:
            raise VariantStateError("trade2公開競売が不正です。") from exc


def _trade2_public_orders(public: Mapping[str, Any]) -> tuple[MarketOrder, ...]:
    try:
        return tuple(MarketOrder.from_document(order) for order in public["orders"])
    except (KeyError, TypeError, TradeMarketError) as exc:
        raise VariantStateError("trade2公開注文が不正です。") from exc


def _minimum_trade2_next_sequence(orders: tuple[MarketOrder, ...]) -> int:
    if not orders:
        return 0
    return max(int(order.order_id.removeprefix("market-")) for order in orders) + 1


def _trade2_public_auctions(public: Mapping[str, Any]) -> tuple[AuctionLot, ...]:
    auctions = public.get("auctions")
    if not isinstance(auctions, (list, tuple)):
        raise VariantStateError("trade2 auctionsは配列で指定してください。")
    try:
        return tuple(
            AuctionLot.from_document(_thaw_json(auction))
            for auction in auctions
        )
    except (TypeError, TradeAuctionError) as exc:
        raise VariantStateError("trade2公開競売が不正です。") from exc


def _minimum_trade2_next_auction_sequence(
    auctions: tuple[AuctionLot, ...],
) -> int:
    if not auctions:
        return 0
    return (
        max(
            int(auction.auction_id.removeprefix("auction-"))
            for auction in auctions
        )
        + 1
    )


def _trade_market_from_documents(
    public: Mapping[str, Any],
    private: Mapping[str, Any],
) -> TradeMarket:
    _validate_trade2_public(public)
    expected_private = (
        _TRADE2_AUCTION_PRIVATE_KEYS
        if public.get("catalog") == TRADE2_AUCTION_CATALOG
        else _TRADE2_PRIVATE_KEYS
    )
    if not isinstance(private, Mapping) or set(private) != expected_private:
        raise VariantStateError("trade2 private stateの項目が不正です。")
    document = {
        "format": TRADE_MARKET_FORMAT,
        "version": TRADE_MARKET_VERSION,
        "next_sequence": private["next_sequence"],
        "open_orders": _thaw_json(public["orders"]),
    }
    return TradeMarket.from_document(document)


def _trade_auction_from_documents(
    public: Mapping[str, Any],
    private: Mapping[str, Any],
) -> AuctionHouse:
    _validate_trade2_public(public)
    if public.get("catalog") != TRADE2_AUCTION_CATALOG:
        raise VariantStateError("このtrade2 stateには公開競売がありません。")
    if not isinstance(private, Mapping) or set(private) != _TRADE2_AUCTION_PRIVATE_KEYS:
        raise VariantStateError("trade2 auction private stateの項目が不正です。")
    document = {
        "format": TRADE_AUCTION_FORMAT,
        "version": TRADE_AUCTION_VERSION,
        "next_sequence": private["next_auction_sequence"],
        "open_auctions": _thaw_json(public["auctions"]),
    }
    return AuctionHouse.from_document(document)


def _validate_document_keys(
    document: Mapping[str, Any],
    expected: frozenset[str],
    label: str,
) -> None:
    if not isinstance(document, Mapping):
        raise VariantStateError(f"{label}はオブジェクトで指定してください。")
    keys = set(document)
    if keys == expected:
        return
    unknown = sorted(str(key) for key in keys - expected)
    missing = sorted(expected - keys)
    detail = []
    if unknown:
        detail.append(f"未知: {', '.join(unknown)}")
    if missing:
        detail.append(f"不足: {', '.join(missing)}")
    suffix = f"（{' / '.join(detail)}）" if detail else ""
    raise VariantStateError(f"{label}の項目が不正です。{suffix}")


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise VariantStateError("variant stateのkeyが不正です。")
        return MappingProxyType(
            {key: _freeze_json(child) for key, child in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(child) for child in value)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise VariantStateError("variant stateにJSONで表せない値があります。")


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(child) for child in value]
    return value


def _valid_fingerprint(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and value == value.lower()
        and all(character in "0123456789abcdef" for character in value)
    )


__all__ = (
    "VARIANT_STATE_FORMAT",
    "VARIANT_STATE_VERSION",
    "VariantState",
    "VariantStateError",
)
