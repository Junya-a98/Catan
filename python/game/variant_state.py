"""Versioned runtime-state boundary for optional match variants.

Full saves retain both public and authority-private state.  Network and
spectator projections must use :meth:`VariantState.to_public_document`, which
omits the ``private`` key entirely rather than exposing an empty placeholder.
Only the no-op ``standard`` state is supported in this stage.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from game.variant import SUPPORTED_VARIANT_KINDS, VariantConfig


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
    """Immutable full runtime state owned by the authoritative game."""

    format: str = VARIANT_STATE_FORMAT
    version: int = VARIANT_STATE_VERSION
    kind: str = "standard"
    config_fingerprint: str = field(
        default_factory=lambda: VariantConfig.standard().fingerprint()
    )
    public: Mapping[str, Any] = field(default_factory=dict, hash=False)
    private: Mapping[str, Any] = field(default_factory=dict, hash=False)

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
        if self.public:
            raise VariantStateError("standard variant の public state は空にしてください。")
        if self.private:
            raise VariantStateError("standard variant の private state は空にしてください。")

        # Do not retain mutable caller-owned documents in a running match.
        object.__setattr__(self, "public", MappingProxyType({}))
        object.__setattr__(self, "private", MappingProxyType({}))

    @classmethod
    def initial(cls, config: VariantConfig) -> VariantState:
        """Create the empty runtime state bound to a validated config."""

        if not isinstance(config, VariantConfig):
            raise VariantStateError("variant設定が不正です。")
        return cls(
            kind=config.kind,
            config_fingerprint=config.fingerprint(),
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
        """Parse a strict full-save document, or initialize a legacy save."""

        validated_config = _optional_config(config)
        if document is None:
            state = cls.initial(validated_config or VariantConfig.standard())
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
        """Parse the strict public projection where ``private`` is forbidden."""

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

    def to_document(self) -> dict[str, Any]:
        """Return a fresh full-save document including private state."""

        return {
            "format": self.format,
            "version": self.version,
            "kind": self.kind,
            "config_fingerprint": self.config_fingerprint,
            "public": {},
            "private": {},
        }

    def to_public_document(self) -> dict[str, Any]:
        """Return a fresh viewer-safe document with no ``private`` key."""

        return {
            "format": self.format,
            "version": self.version,
            "kind": self.kind,
            "config_fingerprint": self.config_fingerprint,
            "public": {},
        }

    def __copy__(self) -> VariantState:
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> VariantState:
        memo[id(self)] = self
        return self

    def __reduce__(self):
        return (type(self).from_document, (self.to_document(),))


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
