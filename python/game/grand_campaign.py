"""Privacy boundary for future ``grand_campaign_v1`` event plans.

Frontier boards deliberately keep undiscovered harbors out of every public
projection.  A normal forecast-event catalog cannot safely choose a blockade
from the authoritative board's complete harbor list because the chosen stable
ID would reveal that an undiscovered harbor exists.  This module instead
creates an immutable announcement plan from the stable IDs that were already
public *at announcement time*.

The authority-only seed is accepted while creating or verifying a plan, but
is never retained by :class:`HarborBlockadePlan` and never appears in its JSON
document.  The eligible-ID snapshot and chosen target are public.  Revealing
another harbor later therefore cannot silently reselect the announced target.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import hmac
import json
import re
from typing import Any

GRAND_CAMPAIGN_CATALOG_ID = "grand_campaign_v1"
GRAND_CAMPAIGN_PLAN_FORMAT = "catan-grand-campaign-plan"
GRAND_CAMPAIGN_PLAN_VERSION = 1
HARBOR_BLOCKADE_EVENT_ID = "harbor_blockade_v1"

HARBOR_BLOCKADE_TARGET = "target"
HARBOR_BLOCKADE_SKIP = "skip"
NO_REVEALED_HARBORS_REASON = "no_revealed_harbors"

# This matches the public board-manifest boundary.  Keep both the list length
# and numeric stable-ID range bounded before hashing attacker-controlled data.
MAX_REVEALED_HARBORS = 64
MAX_HARBOR_INDEX = MAX_REVEALED_HARBORS - 1
MAX_RESOLUTION_NUMBER = 9_007_199_254_740_991

_DOCUMENT_KEYS = frozenset(
    {
        "format",
        "version",
        "catalog",
        "event_id",
        "resolution_number",
        "eligible_harbor_ids",
        "outcome",
    }
)
_TARGET_OUTCOME_KEYS = frozenset({"kind", "harbor_id"})
_SKIP_OUTCOME_KEYS = frozenset({"kind", "reason"})
_HARBOR_ID_PATTERN = re.compile(r"harbor-(0|[1-9][0-9]?)\Z")
_SEED_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


class GrandCampaignError(ValueError):
    """Raised when a campaign announcement plan or its inputs are invalid."""


@dataclass(frozen=True)
class HarborBlockadePlan:
    """Immutable, viewer-safe snapshot for one frontier harbor blockade.

    ``eligible_harbor_ids`` is the exact public set captured when the event was
    announced.  The plan does not accept a later revealed-harbor list when its
    target is read, so discovery during the forecast window cannot change the
    outcome.
    """

    resolution_number: int
    eligible_harbor_ids: tuple[str, ...] | list[str]
    target_harbor_id: str | None

    def __post_init__(self) -> None:
        resolution_number = _bounded_integer(
            self.resolution_number,
            label="resolution_number",
            minimum=0,
            maximum=MAX_RESOLUTION_NUMBER,
        )
        eligible = _canonical_revealed_harbor_ids(
            self.eligible_harbor_ids,
            require_canonical_order=True,
        )
        target = self.target_harbor_id
        if eligible:
            if target is None:
                raise GrandCampaignError(
                    "公開済みの交換所がある計画には封鎖対象が必要です。"
                )
            target = _stable_harbor_id(target)
            if target not in eligible:
                raise GrandCampaignError(
                    "港湾封鎖の対象は予告時点の公開交換所から選んでください。"
                )
        elif target is not None:
            raise GrandCampaignError(
                "公開交換所がない計画に封鎖対象は指定できません。"
            )
        object.__setattr__(self, "resolution_number", resolution_number)
        object.__setattr__(self, "eligible_harbor_ids", eligible)
        object.__setattr__(self, "target_harbor_id", target)

    @classmethod
    def create(
        cls,
        revealed_harbor_ids: Sequence[str],
        *,
        secret_seed: str,
        resolution_number: int,
    ) -> HarborBlockadePlan:
        """Freeze and deterministically select from already-public IDs only."""

        eligible = _canonical_revealed_harbor_ids(revealed_harbor_ids)
        _secret_seed(secret_seed)
        resolution_number = _bounded_integer(
            resolution_number,
            label="resolution_number",
            minimum=0,
            maximum=MAX_RESOLUTION_NUMBER,
        )
        target = _select_target(
            eligible,
            secret_seed=secret_seed,
            resolution_number=resolution_number,
        )
        return cls(
            resolution_number=resolution_number,
            eligible_harbor_ids=eligible,
            target_harbor_id=target,
        )

    @classmethod
    def from_public_document(
        cls,
        document: Mapping[str, Any],
    ) -> HarborBlockadePlan:
        """Parse a strict public plan without requiring authority secrets."""

        _expect_exact_keys(document, _DOCUMENT_KEYS, "grand campaign plan")
        if document["format"] != GRAND_CAMPAIGN_PLAN_FORMAT:
            raise GrandCampaignError("grand campaign plan formatが不正です。")
        if (
            type(document["version"]) is not int
            or document["version"] != GRAND_CAMPAIGN_PLAN_VERSION
        ):
            raise GrandCampaignError("grand campaign plan versionが不正です。")
        if document["catalog"] != GRAND_CAMPAIGN_CATALOG_ID:
            raise GrandCampaignError("grand campaign catalogが不正です。")
        if document["event_id"] != HARBOR_BLOCKADE_EVENT_ID:
            raise GrandCampaignError("grand campaign event IDが不正です。")

        eligible = _canonical_revealed_harbor_ids(
            document["eligible_harbor_ids"],
            require_json_array=True,
            require_canonical_order=True,
        )
        outcome = document["outcome"]
        if not isinstance(outcome, Mapping):
            raise GrandCampaignError("港湾封鎖outcomeが不正です。")
        kind = outcome.get("kind")
        if kind == HARBOR_BLOCKADE_TARGET:
            _expect_exact_keys(outcome, _TARGET_OUTCOME_KEYS, "target outcome")
            target = outcome["harbor_id"]
        elif kind == HARBOR_BLOCKADE_SKIP:
            _expect_exact_keys(outcome, _SKIP_OUTCOME_KEYS, "skip outcome")
            if outcome["reason"] != NO_REVEALED_HARBORS_REASON:
                raise GrandCampaignError("港湾封鎖skip reasonが不正です。")
            target = None
        else:
            raise GrandCampaignError("港湾封鎖outcome kindが不正です。")
        return cls(
            resolution_number=document["resolution_number"],
            eligible_harbor_ids=eligible,
            target_harbor_id=target,
        )

    @property
    def skipped(self) -> bool:
        """Whether resolution must explicitly skip because no harbor was public."""

        return self.target_harbor_id is None

    def to_public_document(self) -> dict[str, Any]:
        """Return a fresh JSON-safe document that never contains the seed."""

        if self.target_harbor_id is None:
            outcome = {
                "kind": HARBOR_BLOCKADE_SKIP,
                "reason": NO_REVEALED_HARBORS_REASON,
            }
        else:
            outcome = {
                "kind": HARBOR_BLOCKADE_TARGET,
                "harbor_id": self.target_harbor_id,
            }
        return {
            "format": GRAND_CAMPAIGN_PLAN_FORMAT,
            "version": GRAND_CAMPAIGN_PLAN_VERSION,
            "catalog": GRAND_CAMPAIGN_CATALOG_ID,
            "event_id": HARBOR_BLOCKADE_EVENT_ID,
            "resolution_number": self.resolution_number,
            "eligible_harbor_ids": list(self.eligible_harbor_ids),
            "outcome": outcome,
        }

    def verify_authority_seed(self, secret_seed: str) -> None:
        """Reject a plan not selected by ``secret_seed`` and its frozen pool."""

        expected = _select_target(
            self.eligible_harbor_ids,
            secret_seed=secret_seed,
            resolution_number=self.resolution_number,
        )
        if not hmac.compare_digest(expected or "", self.target_harbor_id or ""):
            raise GrandCampaignError(
                "港湾封鎖計画がauthority seedによる選択と一致しません。"
            )

    def forecast_parameters(self) -> dict[str, str] | None:
        """Adapt a targeted plan to forecast parameters; ``None`` means skip."""

        if self.target_harbor_id is None:
            return None
        return {"harbor_id": self.target_harbor_id}


def create_harbor_blockade_plan(
    revealed_harbor_ids: Sequence[str],
    *,
    secret_seed: str,
    resolution_number: int,
) -> HarborBlockadePlan:
    """Document-oriented constructor used by a future campaign scheduler."""

    return HarborBlockadePlan.create(
        revealed_harbor_ids,
        secret_seed=secret_seed,
        resolution_number=resolution_number,
    )


def validate_harbor_blockade_public_document(
    document: Mapping[str, Any],
) -> None:
    """Validate the strict public shape without access to the secret seed."""

    HarborBlockadePlan.from_public_document(document)


def verify_harbor_blockade_public_document(
    document: Mapping[str, Any],
    *,
    secret_seed: str,
) -> None:
    """Validate structure and prove the deterministic authority selection."""

    plan = HarborBlockadePlan.from_public_document(document)
    plan.verify_authority_seed(secret_seed)


def _select_target(
    eligible_harbor_ids: tuple[str, ...],
    *,
    secret_seed: str,
    resolution_number: int,
) -> str | None:
    _secret_seed(secret_seed)
    resolution_number = _bounded_integer(
        resolution_number,
        label="resolution_number",
        minimum=0,
        maximum=MAX_RESOLUTION_NUMBER,
    )
    if not eligible_harbor_ids:
        return None
    payload = json.dumps(
        {
            "catalog": GRAND_CAMPAIGN_CATALOG_ID,
            "event_id": HARBOR_BLOCKADE_EVENT_ID,
            "resolution_number": resolution_number,
            "eligible_harbor_ids": list(eligible_harbor_ids),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    digest = hmac.new(
        bytes.fromhex(secret_seed),
        b"catan:grand-campaign:harbor-blockade\0" + payload,
        hashlib.sha256,
    ).digest()
    selected_index = int.from_bytes(digest, "big") % len(eligible_harbor_ids)
    return eligible_harbor_ids[selected_index]


def _canonical_revealed_harbor_ids(
    values: Any,
    *,
    require_json_array: bool = False,
    require_canonical_order: bool = False,
) -> tuple[str, ...]:
    if require_json_array:
        valid_container = type(values) is list
    else:
        valid_container = isinstance(values, (list, tuple))
    if not valid_container:
        raise GrandCampaignError("revealed_harbor_idsは配列で指定してください。")
    if len(values) > MAX_REVEALED_HARBORS:
        raise GrandCampaignError(
            f"公開交換所は{MAX_REVEALED_HARBORS}件以下で指定してください。"
        )
    parsed = tuple(_stable_harbor_id(value) for value in values)
    if len(set(parsed)) != len(parsed):
        raise GrandCampaignError("revealed_harbor_idsに重複があります。")
    canonical = tuple(sorted(parsed, key=_harbor_index))
    if require_canonical_order and parsed != canonical:
        raise GrandCampaignError(
            "revealed_harbor_idsはstable ID順で指定してください。"
        )
    return canonical


def _stable_harbor_id(value: Any) -> str:
    if type(value) is not str or _HARBOR_ID_PATTERN.fullmatch(value) is None:
        raise GrandCampaignError("stable harbor IDが不正です。")
    if _harbor_index(value) > MAX_HARBOR_INDEX:
        raise GrandCampaignError("stable harbor IDが上限を超えています。")
    return value


def _harbor_index(value: str) -> int:
    return int(value.removeprefix("harbor-"))


def _secret_seed(value: Any) -> str:
    if type(value) is not str or _SEED_PATTERN.fullmatch(value) is None:
        raise GrandCampaignError("grand campaign secret seedが不正です。")
    return value


def _bounded_integer(
    value: Any,
    *,
    label: str,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise GrandCampaignError(f"{label}が不正です。")
    return value


def _expect_exact_keys(
    document: Any,
    expected: frozenset[str],
    label: str,
) -> None:
    if not isinstance(document, Mapping) or set(document) != expected:
        raise GrandCampaignError(f"{label}の項目が不正です。")


__all__ = (
    "GRAND_CAMPAIGN_CATALOG_ID",
    "GRAND_CAMPAIGN_PLAN_FORMAT",
    "GRAND_CAMPAIGN_PLAN_VERSION",
    "HARBOR_BLOCKADE_SKIP",
    "HARBOR_BLOCKADE_TARGET",
    "HARBOR_BLOCKADE_EVENT_ID",
    "MAX_HARBOR_INDEX",
    "MAX_REVEALED_HARBORS",
    "MAX_RESOLUTION_NUMBER",
    "NO_REVEALED_HARBORS_REASON",
    "GrandCampaignError",
    "HarborBlockadePlan",
    "create_harbor_blockade_plan",
    "validate_harbor_blockade_public_document",
    "verify_harbor_blockade_public_document",
)
