"""Versioned configuration boundary for optional match variants.

The no-op ``standard`` kind and the separately selectable ``forecast_events``
kind share one strict document.  Keeping this configuration independent from
Pygame and the game engine lets lobby, save, replay, and network code agree on
identity without making variant fields part of the small ``HouseRules`` model.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
from types import MappingProxyType
from typing import Any

from game.forecast_events import (
    CAMPAIGN_FORECAST_CATALOG_ID,
    DEFAULT_FORECAST_OPTIONS,
    FORECAST_CATALOG_ID,
    FORECAST_EVENTS_KIND,
    ForecastEventError,
    canonical_forecast_options,
)
from game.frontier import (
    DEFAULT_FRONTIER_OPTIONS,
    EXPANDED_FRONTIER_OPTIONS,
    FRONTIER_KIND,
    STANDARD_FRONTIER_CATALOG,
    FrontierError,
    canonical_frontier_options,
    frontier_catalog_from_options,
)
from game.trade_market import MAX_ORDER_TTL, MIN_ORDER_TTL
from game.trade_auction import MAX_AUCTION_TTL, MIN_AUCTION_TTL


VARIANT_CONFIG_VERSION = 1
STANDARD_VARIANT_KIND = "standard"
TRADE2_VARIANT_KIND = "trade2"
CREDIT_VARIANT_KIND = "credit"
COMPOSITE_VARIANT_KIND = "composite"
CREDIT_CATALOG = "bank_loan_v1"
COMPOSITE_EVENTS_ECONOMY_CATALOG = "events_economy_v1"
COMPOSITE_GRAND_CAMPAIGN_CATALOG = "grand_campaign_v1"
TRADE2_CATALOG = "standing_market_v1"
TRADE2_AUCTION_CATALOG = "market_auction_v1"
DEFAULT_TRADE2_OPTIONS = {
    "catalog": TRADE2_CATALOG,
    "order_ttl_turns": 4,
}
DEFAULT_TRADE2_AUCTION_OPTIONS = {
    "catalog": TRADE2_AUCTION_CATALOG,
    "order_ttl_turns": 4,
    "auction_ttl_turns": 4,
}
DEFAULT_CREDIT_OPTIONS = {"catalog": CREDIT_CATALOG}
DEFAULT_COMPOSITE_OPTIONS = {"catalog": COMPOSITE_EVENTS_ECONOMY_CATALOG}
DEFAULT_GRAND_CAMPAIGN_OPTIONS = {"catalog": COMPOSITE_GRAND_CAMPAIGN_CATALOG}
SUPPORTED_VARIANT_KINDS = frozenset(
    {
        STANDARD_VARIANT_KIND,
        FORECAST_EVENTS_KIND,
        FRONTIER_KIND,
        TRADE2_VARIANT_KIND,
        CREDIT_VARIANT_KIND,
        COMPOSITE_VARIANT_KIND,
    }
)
_DOCUMENT_KEYS = frozenset({"version", "kind", "options"})
_TRADE2_OPTION_KEYS = frozenset(DEFAULT_TRADE2_OPTIONS)
_TRADE2_AUCTION_OPTION_KEYS = frozenset(DEFAULT_TRADE2_AUCTION_OPTIONS)
_CREDIT_OPTION_KEYS = frozenset(DEFAULT_CREDIT_OPTIONS)
_COMPOSITE_OPTION_KEYS = frozenset(DEFAULT_COMPOSITE_OPTIONS)


@dataclass(frozen=True)
class VariantConfig:
    """Immutable, strictly validated configuration for one match variant.

    ``standard`` requires an empty mapping. ``forecast_events`` uses a small,
    exact options schema so its schedule is reproducible across saves and
    clients without changing the outer transport boundary.
    """

    version: int = VARIANT_CONFIG_VERSION
    kind: str = STANDARD_VARIANT_KIND
    options: Mapping[str, Any] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != VARIANT_CONFIG_VERSION:
            raise ValueError(
                f"variant version は {VARIANT_CONFIG_VERSION} で指定してください。"
            )
        if not isinstance(self.kind, str) or self.kind not in SUPPORTED_VARIANT_KINDS:
            raise ValueError(f"未対応のvariant kindです: {self.kind}")
        if not isinstance(self.options, Mapping):
            raise ValueError("variant options はオブジェクトで指定してください。")
        if self.kind == STANDARD_VARIANT_KIND:
            if self.options:
                raise ValueError("standard variant の options は空にしてください。")
            canonical_options = {}
        elif self.kind == FORECAST_EVENTS_KIND:
            try:
                canonical_options = canonical_forecast_options(self.options)
            except ForecastEventError as exc:
                raise ValueError("forecast_events options が不正です。") from exc
        elif self.kind == FRONTIER_KIND:
            try:
                canonical_options = canonical_frontier_options(self.options)
            except FrontierError as exc:
                raise ValueError("frontier options が不正です。") from exc
        elif self.kind == TRADE2_VARIANT_KIND:
            canonical_options = _canonical_trade2_options(self.options)
        elif self.kind == CREDIT_VARIANT_KIND:
            canonical_options = _canonical_credit_options(self.options)
        elif self.kind == COMPOSITE_VARIANT_KIND:
            canonical_options = _canonical_composite_options(self.options)
        else:  # pragma: no cover - supported-kind guard rejects this first.
            raise ValueError(f"未対応のvariant kindです: {self.kind}")

        # Never retain a caller-owned mutable mapping in a room or running
        # match.
        object.__setattr__(
            self,
            "options",
            MappingProxyType(canonical_options),
        )

    @classmethod
    def standard(cls) -> VariantConfig:
        """Return the no-op configuration used by current official rules."""

        return cls()

    @classmethod
    def forecast_events(
        cls,
        *,
        catalog: str | None = None,
        forecast_lead_turns: int | None = None,
        event_interval_turns: int | None = None,
    ) -> VariantConfig:
        """Return the supported forecast-events configuration."""

        options = dict(DEFAULT_FORECAST_OPTIONS)
        if catalog is not None:
            options["catalog"] = catalog
        if forecast_lead_turns is not None:
            options["forecast_lead_turns"] = forecast_lead_turns
        if event_interval_turns is not None:
            options["event_interval_turns"] = event_interval_turns
        return cls(kind=FORECAST_EVENTS_KIND, options=options)

    @classmethod
    def frontier(cls) -> VariantConfig:
        """Return the standard-board fog-of-exploration configuration."""

        return cls(kind=FRONTIER_KIND, options=dict(DEFAULT_FRONTIER_OPTIONS))

    @classmethod
    def frontier_expanded(cls) -> VariantConfig:
        """Return the opt-in thirty-seven-tile frontier configuration."""

        return cls(kind=FRONTIER_KIND, options=dict(EXPANDED_FRONTIER_OPTIONS))

    @classmethod
    def trade2(
        cls,
        *,
        order_ttl_turns: int | None = None,
    ) -> VariantConfig:
        """Return the standing-market v1 configuration."""

        options = dict(DEFAULT_TRADE2_OPTIONS)
        if order_ttl_turns is not None:
            options["order_ttl_turns"] = order_ttl_turns
        return cls(kind=TRADE2_VARIANT_KIND, options=options)

    @classmethod
    def trade2_auction(
        cls,
        *,
        order_ttl_turns: int | None = None,
        auction_ttl_turns: int | None = None,
    ) -> VariantConfig:
        """Return Trade 2.0 with the standing market and public auctions."""

        options = dict(DEFAULT_TRADE2_AUCTION_OPTIONS)
        if order_ttl_turns is not None:
            options["order_ttl_turns"] = order_ttl_turns
        if auction_ttl_turns is not None:
            options["auction_ttl_turns"] = auction_ttl_turns
        return cls(kind=TRADE2_VARIANT_KIND, options=options)

    @classmethod
    def credit(cls) -> VariantConfig:
        """Return the isolated bank resource-credit v1 configuration."""

        return cls(kind=CREDIT_VARIANT_KIND, options=dict(DEFAULT_CREDIT_OPTIONS))

    @classmethod
    def composite_events_economy(cls) -> VariantConfig:
        """Return the fixed, standard-board events-and-economy bundle.

        V1 intentionally exposes a catalog selection rather than a caller
        supplied component array.  That keeps save, replay, authority and AI
        behavior on one audited combination while the composition boundary is
        being introduced.
        """

        return cls(
            kind=COMPOSITE_VARIANT_KIND,
            options=dict(DEFAULT_COMPOSITE_OPTIONS),
        )

    @classmethod
    def composite_grand_campaign(cls) -> VariantConfig:
        """Return the fixed 37-tile exploration-and-economy campaign."""

        return cls(
            kind=COMPOSITE_VARIANT_KIND,
            options=dict(DEFAULT_GRAND_CAMPAIGN_OPTIONS),
        )

    def component_config(self, kind: str) -> VariantConfig | None:
        """Return the fixed child config for ``kind``, if this mode has it.

        A direct single variant is treated as its own component.  Callers can
        therefore migrate from ``config.kind == ...`` checks without needing
        a special branch for composite matches.
        """

        if not isinstance(kind, str):
            return None
        if self.kind != COMPOSITE_VARIANT_KIND:
            return self if self.kind == kind else None
        catalog = self.options["catalog"]
        if catalog not in (
            COMPOSITE_EVENTS_ECONOMY_CATALOG,
            COMPOSITE_GRAND_CAMPAIGN_CATALOG,
        ):
            return None  # pragma: no cover - constructor rejects this catalog.
        if kind == FORECAST_EVENTS_KIND:
            return type(self).forecast_events(
                catalog=(
                    CAMPAIGN_FORECAST_CATALOG_ID
                    if catalog == COMPOSITE_GRAND_CAMPAIGN_CATALOG
                    else FORECAST_CATALOG_ID
                ),
                forecast_lead_turns=2,
                event_interval_turns=6,
            )
        if (
            kind == FRONTIER_KIND
            and catalog == COMPOSITE_GRAND_CAMPAIGN_CATALOG
        ):
            return type(self).frontier_expanded()
        if kind == TRADE2_VARIANT_KIND:
            return type(self).trade2_auction(
                order_ttl_turns=4,
                auction_ttl_turns=4,
            )
        if kind == CREDIT_VARIANT_KIND:
            return type(self).credit()
        return None

    def has_component(self, kind: str) -> bool:
        """Return whether this mode directly supplies ``kind`` behavior."""

        return self.component_config(kind) is not None

    def board_topology_id(self) -> str:
        """Return the topology required by this fixed variant configuration."""

        frontier = self.component_config(FRONTIER_KIND)
        if frontier is None:
            return STANDARD_FRONTIER_CATALOG
        return frontier_catalog_from_options(frontier.options)

    def uses_hidden_board(self) -> bool:
        """Return whether terrain information is authority-private at start."""

        return self.has_component(FRONTIER_KIND)

    @classmethod
    def from_document(
        cls,
        document: Mapping[str, Any] | None,
    ) -> VariantConfig:
        """Parse the strict save/replay/LAN document.

        ``None`` represents a document written before variant settings existed
        and restores ``standard``.  A present document must contain exactly the
        versioned outer fields; partial or forward-version data is rejected.
        """

        if document is None:
            return cls.standard()
        if not isinstance(document, Mapping):
            raise ValueError("variant設定はオブジェクトで指定してください。")

        keys = set(document)
        if keys != _DOCUMENT_KEYS:
            unknown = sorted(str(key) for key in keys - _DOCUMENT_KEYS)
            missing = sorted(_DOCUMENT_KEYS - keys)
            detail = []
            if unknown:
                detail.append(f"未知: {', '.join(unknown)}")
            if missing:
                detail.append(f"不足: {', '.join(missing)}")
            suffix = f"（{' / '.join(detail)}）" if detail else ""
            raise ValueError(f"variant設定の項目が不正です。{suffix}")

        return cls(
            version=document["version"],
            kind=document["kind"],
            options=document["options"],
        )

    def to_document(self) -> dict[str, Any]:
        """Return a fresh canonical JSON-safe document."""

        return {
            "version": self.version,
            "kind": self.kind,
            "options": dict(self.options),
        }

    def __copy__(self) -> VariantConfig:
        """Reuse this immutable value when a lobby creates a rollback copy."""

        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> VariantConfig:
        """Avoid copying ``MappingProxyType`` while preserving immutability."""

        memo[id(self)] = self
        return self

    def __reduce__(self):
        """Rebuild from the public document when crossing a process boundary."""

        return (type(self).from_document, (self.to_document(),))

    def canonical_json(self) -> str:
        """Return the deterministic JSON used for identity calculation."""

        return json.dumps(
            self.to_document(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    def fingerprint(self) -> str:
        """Return a stable SHA-256 identity for saves, replays, and rooms."""

        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


__all__ = (
    "STANDARD_VARIANT_KIND",
    "SUPPORTED_VARIANT_KINDS",
    "CREDIT_VARIANT_KIND",
    "CREDIT_CATALOG",
    "DEFAULT_CREDIT_OPTIONS",
    "COMPOSITE_VARIANT_KIND",
    "COMPOSITE_EVENTS_ECONOMY_CATALOG",
    "COMPOSITE_GRAND_CAMPAIGN_CATALOG",
    "DEFAULT_COMPOSITE_OPTIONS",
    "DEFAULT_GRAND_CAMPAIGN_OPTIONS",
    "TRADE2_CATALOG",
    "TRADE2_AUCTION_CATALOG",
    "TRADE2_VARIANT_KIND",
    "DEFAULT_TRADE2_OPTIONS",
    "DEFAULT_TRADE2_AUCTION_OPTIONS",
    "VARIANT_CONFIG_VERSION",
    "VariantConfig",
    "FORECAST_EVENTS_KIND",
    "FRONTIER_KIND",
    "variant_board_topology",
    "variant_uses_hidden_board",
)


def _canonical_trade2_options(options: Mapping[str, Any]) -> dict[str, Any]:
    catalog = options.get("catalog")
    if catalog == TRADE2_CATALOG:
        if set(options) != _TRADE2_OPTION_KEYS:
            raise ValueError("trade2 options の項目が不正です。")
    elif catalog == TRADE2_AUCTION_CATALOG:
        if set(options) != _TRADE2_AUCTION_OPTION_KEYS:
            raise ValueError("trade2 auction options の項目が不正です。")
    else:
        raise ValueError("未対応のtrade2 catalogです。")
    ttl = options.get("order_ttl_turns")
    if type(ttl) is not int or not MIN_ORDER_TTL <= ttl <= MAX_ORDER_TTL:
        raise ValueError(
            f"order_ttl_turns は {MIN_ORDER_TTL}〜{MAX_ORDER_TTL} で指定してください。"
        )
    canonical = {"catalog": catalog, "order_ttl_turns": ttl}
    if catalog == TRADE2_AUCTION_CATALOG:
        auction_ttl = options.get("auction_ttl_turns")
        if (
            type(auction_ttl) is not int
            or not MIN_AUCTION_TTL <= auction_ttl <= MAX_AUCTION_TTL
        ):
            raise ValueError(
                "auction_ttl_turns は "
                f"{MIN_AUCTION_TTL}〜{MAX_AUCTION_TTL} で指定してください。"
            )
        canonical["auction_ttl_turns"] = auction_ttl
    return canonical


def _canonical_credit_options(options: Mapping[str, Any]) -> dict[str, Any]:
    if set(options) != _CREDIT_OPTION_KEYS:
        raise ValueError("credit options の項目が不正です。")
    if options.get("catalog") != CREDIT_CATALOG:
        raise ValueError("未対応のcredit catalogです。")
    return {"catalog": CREDIT_CATALOG}


def _canonical_composite_options(options: Mapping[str, Any]) -> dict[str, Any]:
    if set(options) != _COMPOSITE_OPTION_KEYS:
        raise ValueError("composite options の項目が不正です。")
    catalog = options.get("catalog")
    if catalog not in (
        COMPOSITE_EVENTS_ECONOMY_CATALOG,
        COMPOSITE_GRAND_CAMPAIGN_CATALOG,
    ):
        raise ValueError("未対応のcomposite catalogです。")
    return {"catalog": catalog}


def variant_board_topology(config: VariantConfig) -> str:
    """Compatibility helper delegating topology selection to ``config``."""

    if not isinstance(config, VariantConfig):
        raise ValueError("variant設定が不正です。")
    return config.board_topology_id()


def variant_uses_hidden_board(config: VariantConfig) -> bool:
    """Compatibility helper delegating hidden-board selection to ``config``."""

    if not isinstance(config, VariantConfig):
        raise ValueError("variant設定が不正です。")
    return config.uses_hidden_board()
