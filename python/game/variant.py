"""Versioned configuration boundary for optional match variants.

Only the no-op ``standard`` kind is supported in the first stage.  Keeping the
configuration independent from Pygame and the game engine lets lobby, save,
replay, and network code share one strict canonical document without making
future variant fields part of the small ``HouseRules`` model.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
from types import MappingProxyType
from typing import Any


VARIANT_CONFIG_VERSION = 1
STANDARD_VARIANT_KIND = "standard"
SUPPORTED_VARIANT_KINDS = frozenset({STANDARD_VARIANT_KIND})
_DOCUMENT_KEYS = frozenset({"version", "kind", "options"})


@dataclass(frozen=True)
class VariantConfig:
    """Immutable, strictly validated configuration for one match variant.

    The options mapping is intentionally empty while only ``standard`` exists.
    It is still present in the versioned document so future kinds can add their
    own strict option schema without changing the outer transport boundary.
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
        if self.options:
            raise ValueError("standard variant の options は空にしてください。")

        # Never retain a caller-owned mutable mapping in a room or running
        # match.  Future kinds can replace this with their own immutable model.
        object.__setattr__(self, "options", MappingProxyType({}))

    @classmethod
    def standard(cls) -> VariantConfig:
        """Return the no-op configuration used by current official rules."""

        return cls()

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
            "options": {},
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
)
