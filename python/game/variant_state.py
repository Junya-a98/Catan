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
from game.variant import (
    STANDARD_VARIANT_KIND,
    SUPPORTED_VARIANT_KINDS,
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


class VariantStateError(ValueError):
    """Raised when runtime variant state is malformed or mismatched."""


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
            else:
                validate_frontier_public(self.public)
                if self._projection_only:
                    if self.private:
                        raise VariantStateError(
                            "公開variant stateにprivate情報を含められません。"
                        )
                else:
                    validate_frontier_documents(self.public, self.private)
        except ForecastEventError as exc:
            raise VariantStateError("forecast variant stateが不正です。") from exc
        except FrontierError as exc:
            raise VariantStateError("frontier variant stateが不正です。") from exc

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
                validate_forecast_schedule(self.public, config.options)
            except ForecastEventError as exc:
                raise VariantStateError(
                    "forecast event周期が設定と一致しません。"
                ) from exc

    def ensure_full(self) -> None:
        """Reject a viewer-only projection at an authority/save boundary."""

        if self._projection_only:
            raise VariantStateError("公開variant stateは完全保存に利用できません。")

    def advance_forecast_turn(
        self,
        config: VariantConfig,
        *,
        player_count: int,
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
            )
        except ForecastEventError as exc:
            raise VariantStateError("forecast eventの手番更新に失敗しました。") from exc
        return self._with_documents(public, private), update

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
