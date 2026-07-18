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
    DEFAULT_FORECAST_OPTIONS,
    FORECAST_EVENTS_KIND,
    ForecastEventError,
    canonical_forecast_options,
)
from game.frontier import (
    DEFAULT_FRONTIER_OPTIONS,
    FRONTIER_KIND,
    FrontierError,
    canonical_frontier_options,
)


VARIANT_CONFIG_VERSION = 1
STANDARD_VARIANT_KIND = "standard"
SUPPORTED_VARIANT_KINDS = frozenset(
    {STANDARD_VARIANT_KIND, FORECAST_EVENTS_KIND, FRONTIER_KIND}
)
_DOCUMENT_KEYS = frozenset({"version", "kind", "options"})


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
        else:
            try:
                canonical_options = canonical_frontier_options(self.options)
            except FrontierError as exc:
                raise ValueError("frontier options が不正です。") from exc

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
    "VARIANT_CONFIG_VERSION",
    "VariantConfig",
    "FORECAST_EVENTS_KIND",
    "FRONTIER_KIND",
)
