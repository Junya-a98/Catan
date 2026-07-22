"""Logical reservations over a player's existing total-resource mapping.

``player.resources`` remains the authoritative total hand.  This ledger keeps
only reservation metadata and derives ``available = owned - reserved``.  As a
result, reserved cards still count for discarding on seven, robbery, monopoly,
and the base game's nineteen-card conservation invariant.

The module is deliberately independent from Pygame, networking, persistence,
and the game loop.  Its versioned document serializes reservations only;
total ownership continues to use the existing player-resource save boundary.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
import json
import re
from typing import Any

from game.bank import BANK_RESOURCE_COUNT, RESOURCE_TYPES
from game.resources import ResourceType


RESOURCE_LEDGER_FORMAT = "catan-resource-ledger"
RESOURCE_LEDGER_VERSION = 1
MAX_RESOURCE_COUNT = BANK_RESOURCE_COUNT
MAX_RESERVATIONS = 64
MAX_RESERVATION_ID_LENGTH = 64

_DOCUMENT_KEYS = frozenset({"format", "version", "reservations"})
_RESERVATION_DOCUMENT_KEYS = frozenset({"id", "bundle"})
_RESOURCE_SET = frozenset(RESOURCE_TYPES)
_RESOURCE_NAMES = frozenset(resource.name for resource in RESOURCE_TYPES)
_RESERVATION_ID_PATTERN = re.compile(
    rf"[A-Za-z0-9][A-Za-z0-9._:-]{{0,{MAX_RESERVATION_ID_LENGTH - 1}}}\Z"
)


class ResourceLedgerError(ValueError):
    """Raised when a ledger argument, invariant, or document is malformed."""


@dataclass(frozen=True)
class RemovalResult:
    """Outcome of one forced removal from total ownership."""

    resource: ResourceType
    amount: int
    cancelled_reservation_ids: tuple[str, ...] = ()

    @property
    def canceled_reservation_ids(self) -> tuple[str, ...]:
        """US-spelling compatibility for transport/presentation callers."""

        return self.cancelled_reservation_ids


class ResourceLedger:
    """Manage logical reservations over a mutable five-resource total map.

    The supplied mapping is intentionally retained rather than copied.  Normal
    resource gains performed through existing game code therefore remain
    visible to the ledger, while ``consume``, ``spend_available``, and
    ``remove_owned`` update that same authoritative total mapping.
    """

    __slots__ = ("_resources", "_reservations")

    def __init__(self, resources: MutableMapping[ResourceType, int]) -> None:
        _validate_owned_resources(resources)
        self._resources = resources
        self._reservations: dict[str, dict[ResourceType, int]] = {}

    @property
    def has_reservations(self) -> bool:
        return bool(self._reservations)

    @property
    def owned(self) -> dict[ResourceType, int]:
        return self.owned_map()

    @property
    def reserved(self) -> dict[ResourceType, int]:
        return self.reserved_map()

    @property
    def available(self) -> dict[ResourceType, int]:
        return self.available_map()

    def owned_map(self) -> dict[ResourceType, int]:
        """Return a detached map of all five total-owned counts."""

        _validate_current_state(self._resources, self._reservations)
        return {resource: self._resources[resource] for resource in RESOURCE_TYPES}

    def reserved_map(self) -> dict[ResourceType, int]:
        """Return a detached aggregate map of all five reserved counts."""

        _validate_current_state(self._resources, self._reservations)
        return _reserved_totals(self._reservations)

    def available_map(self) -> dict[ResourceType, int]:
        """Return a detached map of voluntarily spendable counts."""

        owned = self.owned_map()
        reserved = _reserved_totals(self._reservations)
        return {
            resource: owned[resource] - reserved[resource]
            for resource in RESOURCE_TYPES
        }

    def owned_count(self, resource: ResourceType) -> int:
        resource = _validate_resource(resource)
        return self.owned_map()[resource]

    def reserved_count(self, resource: ResourceType) -> int:
        resource = _validate_resource(resource)
        return self.reserved_map()[resource]

    def available_count(self, resource: ResourceType) -> int:
        resource = _validate_resource(resource)
        return self.available_map()[resource]

    def reservations_map(self) -> dict[str, dict[ResourceType, int]]:
        """Return a detached ID-sorted copy of reservation bundles."""

        _validate_current_state(self._resources, self._reservations)
        return {
            reservation_id: dict(self._reservations[reservation_id])
            for reservation_id in sorted(self._reservations)
        }

    def reserve(
        self,
        reservation_id: str,
        bundle: Mapping[ResourceType, int],
    ) -> bool:
        """Reserve a complete bundle, returning ``False`` if it cannot fund."""

        validated_id = _validate_reservation_id(reservation_id)
        validated_bundle = _canonical_bundle(bundle, label="予約bundle")
        _validate_current_state(self._resources, self._reservations)
        if (
            validated_id in self._reservations
            or len(self._reservations) >= MAX_RESERVATIONS
        ):
            return False
        available = self.available_map()
        if any(
            available[resource] < amount
            for resource, amount in validated_bundle.items()
        ):
            return False

        self._reservations[validated_id] = validated_bundle
        return True

    def release(
        self,
        reservation_id: str,
    ) -> dict[ResourceType, int] | None:
        """Release a reservation without changing total ownership."""

        validated_id = _validate_reservation_id(reservation_id)
        _validate_current_state(self._resources, self._reservations)
        bundle = self._reservations.pop(validated_id, None)
        return None if bundle is None else dict(bundle)

    def replace(
        self,
        reservation_id: str,
        bundle: Mapping[ResourceType, int],
    ) -> bool:
        """Atomically replace one existing reservation's complete bundle.

        The old reservation remains in force while affordability is checked.
        This is important for revision-based offers such as auction bids: a
        failed update must neither release the previous escrow nor retain a
        partially updated bundle.
        """

        validated_id = _validate_reservation_id(reservation_id)
        validated_bundle = _canonical_bundle(bundle, label="差替bundle")
        _validate_current_state(self._resources, self._reservations)
        if validated_id not in self._reservations:
            return False

        candidate_reservations = {
            current_id: dict(current_bundle)
            for current_id, current_bundle in self._reservations.items()
        }
        candidate_reservations[validated_id] = validated_bundle
        try:
            _validate_current_state(self._resources, candidate_reservations)
        except ResourceLedgerError:
            return False

        self._reservations = candidate_reservations
        return True

    def consume(
        self,
        reservation_id: str,
    ) -> dict[ResourceType, int] | None:
        """Consume one entire reservation from total ownership atomically."""

        validated_id = _validate_reservation_id(reservation_id)
        _validate_current_state(self._resources, self._reservations)
        bundle = self._reservations.get(validated_id)
        if bundle is None:
            return None

        candidate_resources = dict(self._resources)
        for resource, amount in bundle.items():
            candidate_resources[resource] -= amount
        candidate_reservations = dict(self._reservations)
        del candidate_reservations[validated_id]
        _validate_current_state(candidate_resources, candidate_reservations)

        _replace_resource_counts(self._resources, candidate_resources)
        self._reservations = candidate_reservations
        return dict(bundle)

    def spend_available(self, bundle: Mapping[ResourceType, int]) -> bool:
        """Spend an unreserved bundle while preserving every reservation."""

        validated_bundle = _canonical_bundle(bundle, label="支払いbundle")
        _validate_current_state(self._resources, self._reservations)
        available = self.available_map()
        if any(
            available[resource] < amount
            for resource, amount in validated_bundle.items()
        ):
            return False

        candidate_resources = dict(self._resources)
        for resource, amount in validated_bundle.items():
            candidate_resources[resource] -= amount
        _validate_current_state(candidate_resources, self._reservations)
        _replace_resource_counts(self._resources, candidate_resources)
        return True

    def remove_owned(
        self,
        resource: ResourceType,
        amount: int,
    ) -> RemovalResult | None:
        """Apply a forced loss against total ownership.

        If unreserved cards are insufficient, reservations containing the
        resource are cancelled whole in lexicographically sorted ID order
        until the loss can be paid.  Insufficient total ownership returns
        ``None`` and changes neither totals nor reservations.
        """

        validated_resource = _validate_resource(resource)
        validated_amount = _validated_count(
            amount,
            label="強制喪失数",
            minimum=1,
        )
        _validate_current_state(self._resources, self._reservations)
        if self._resources[validated_resource] < validated_amount:
            return None

        candidate_reservations = {
            reservation_id: dict(bundle)
            for reservation_id, bundle in self._reservations.items()
        }
        available = self.available_count(validated_resource)
        cancelled: list[str] = []
        for reservation_id in sorted(candidate_reservations):
            if available >= validated_amount:
                break
            reservation = candidate_reservations[reservation_id]
            released = reservation.get(validated_resource, 0)
            if released <= 0:
                continue
            del candidate_reservations[reservation_id]
            cancelled.append(reservation_id)
            available += released

        if available < validated_amount:  # Defensive invariant guard.
            raise ResourceLedgerError("予約解放後も強制喪失を処理できません。")

        candidate_resources = dict(self._resources)
        candidate_resources[validated_resource] -= validated_amount
        _validate_current_state(candidate_resources, candidate_reservations)

        _replace_resource_counts(self._resources, candidate_resources)
        self._reservations = candidate_reservations
        return RemovalResult(
            resource=validated_resource,
            amount=validated_amount,
            cancelled_reservation_ids=tuple(cancelled),
        )

    def to_document(self) -> dict[str, Any]:
        """Return a fresh deterministic document containing reservations only."""

        _validate_current_state(self._resources, self._reservations)
        return {
            "format": RESOURCE_LEDGER_FORMAT,
            "version": RESOURCE_LEDGER_VERSION,
            "reservations": [
                {
                    "id": reservation_id,
                    "bundle": {
                        resource.name: bundle[resource]
                        for resource in RESOURCE_TYPES
                        if resource in bundle
                    },
                }
                for reservation_id, bundle in sorted(self._reservations.items())
            ],
        }

    @classmethod
    def from_document(
        cls,
        resources: MutableMapping[ResourceType, int],
        document: Mapping[str, Any],
    ) -> ResourceLedger:
        """Attach to totals and parse an exact, canonical v1 reservation state."""

        _validate_owned_resources(resources)
        _expect_exact_keys(document, _DOCUMENT_KEYS, "資源台帳")
        if document["format"] != RESOURCE_LEDGER_FORMAT:
            raise ResourceLedgerError("資源台帳formatが不正です。")
        version = document["version"]
        if type(version) is not int or version != RESOURCE_LEDGER_VERSION:
            raise ResourceLedgerError(
                f"資源台帳versionは {RESOURCE_LEDGER_VERSION} で指定してください。"
            )
        reservations = document["reservations"]
        if not isinstance(reservations, list):
            raise ResourceLedgerError("reservationsは配列で指定してください。")
        if len(reservations) > MAX_RESERVATIONS:
            raise ResourceLedgerError(
                f"予約数は {MAX_RESERVATIONS} 件以下にしてください。"
            )

        ledger = cls(resources)
        previous_id: str | None = None
        for item in reservations:
            _expect_exact_keys(item, _RESERVATION_DOCUMENT_KEYS, "予約")
            reservation_id = _validate_reservation_id(item["id"])
            if previous_id is not None and reservation_id <= previous_id:
                raise ResourceLedgerError(
                    "reservationsは重複のないID昇順で指定してください。"
                )
            bundle = _bundle_from_document(item["bundle"])
            if not ledger.reserve(reservation_id, bundle):
                raise ResourceLedgerError("予約済み資源が総所持数を超えています。")
            previous_id = reservation_id
        return ledger

    def canonical_json(self) -> str:
        return json.dumps(
            self.to_document(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )


def _validate_resource(resource: Any) -> ResourceType:
    if type(resource) is not ResourceType or resource not in _RESOURCE_SET:
        raise ResourceLedgerError("生産可能な5資源だけを指定してください。")
    return resource


def _validate_owned_resources(resources: Any) -> None:
    if not isinstance(resources, MutableMapping):
        raise ResourceLedgerError("resourcesは変更可能な資源mapにしてください。")
    if set(resources) != _RESOURCE_SET or any(
        type(resource) is not ResourceType for resource in resources
    ):
        raise ResourceLedgerError("resourcesは生産可能な5資源を正確に含めてください。")
    for resource in RESOURCE_TYPES:
        _validated_count(
            resources[resource],
            label=f"resources.{resource.name}",
            minimum=0,
        )


def _validate_current_state(
    resources: MutableMapping[ResourceType, int] | Mapping[ResourceType, int],
    reservations: Mapping[str, Mapping[ResourceType, int]],
) -> None:
    _validate_owned_resources(resources)
    if len(reservations) > MAX_RESERVATIONS:
        raise ResourceLedgerError("予約数が上限を超えています。")
    totals = _reserved_totals(reservations)
    if any(totals[resource] > resources[resource] for resource in RESOURCE_TYPES):
        raise ResourceLedgerError("予約済み資源が総所持数を超えています。")


def _canonical_bundle(
    bundle: Mapping[ResourceType, int],
    *,
    label: str,
) -> dict[ResourceType, int]:
    if not isinstance(bundle, Mapping) or not bundle:
        raise ResourceLedgerError(f"{label}は空でない資源mapで指定してください。")
    if len(bundle) > len(RESOURCE_TYPES) or any(
        type(resource) is not ResourceType or resource not in _RESOURCE_SET
        for resource in bundle
    ):
        raise ResourceLedgerError(f"{label}には生産可能な5資源だけを指定してください。")
    return {
        resource: _validated_count(
            bundle[resource],
            label=f"{label}.{resource.name}",
            minimum=1,
        )
        for resource in RESOURCE_TYPES
        if resource in bundle
    }


def _validated_count(value: Any, *, label: str, minimum: int) -> int:
    if type(value) is not int or not minimum <= value <= MAX_RESOURCE_COUNT:
        raise ResourceLedgerError(
            f"{label}は {minimum} 以上 {MAX_RESOURCE_COUNT} 以下の整数で指定してください。"
        )
    return value


def _validate_reservation_id(value: Any) -> str:
    if type(value) is not str or _RESERVATION_ID_PATTERN.fullmatch(value) is None:
        raise ResourceLedgerError(
            "予約IDは英数字で始まる64文字以下の安全な識別子にしてください。"
        )
    return value


def _reserved_totals(
    reservations: Mapping[str, Mapping[ResourceType, int]],
) -> dict[ResourceType, int]:
    totals = {resource: 0 for resource in RESOURCE_TYPES}
    for reservation_id, bundle in reservations.items():
        _validate_reservation_id(reservation_id)
        canonical = _canonical_bundle(bundle, label="予約bundle")
        for resource, amount in canonical.items():
            totals[resource] += amount
            if totals[resource] > MAX_RESOURCE_COUNT:
                raise ResourceLedgerError("予約済み資源数が上限を超えています。")
    return totals


def _bundle_from_document(value: Any) -> dict[ResourceType, int]:
    if not isinstance(value, Mapping) or not value:
        raise ResourceLedgerError("予約bundleは空でないobjectにしてください。")
    if len(value) > len(RESOURCE_TYPES) or any(
        type(name) is not str or name not in _RESOURCE_NAMES for name in value
    ):
        raise ResourceLedgerError(
            "予約bundleには生産可能な5資源だけを指定してください。"
        )
    return {
        resource: _validated_count(
            value[resource.name],
            label=f"予約bundle.{resource.name}",
            minimum=1,
        )
        for resource in RESOURCE_TYPES
        if resource.name in value
    }


def _expect_exact_keys(value: Any, expected: frozenset[str], label: str) -> None:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ResourceLedgerError(f"{label}の項目が不正です。")


def _replace_resource_counts(
    target: MutableMapping[ResourceType, int],
    source: Mapping[ResourceType, int],
) -> None:
    for resource in RESOURCE_TYPES:
        target[resource] = source[resource]


__all__ = (
    "MAX_RESERVATION_ID_LENGTH",
    "MAX_RESERVATIONS",
    "MAX_RESOURCE_COUNT",
    "RESOURCE_LEDGER_FORMAT",
    "RESOURCE_LEDGER_VERSION",
    "RemovalResult",
    "ResourceLedger",
    "ResourceLedgerError",
)
