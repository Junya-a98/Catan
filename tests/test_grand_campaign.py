"""Privacy and determinism boundary for the future grand campaign mode."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import copy
import json

import pytest

from game.forecast_events import HARBOR_BLOCKADE_EVENT_ID
from game.grand_campaign import (
    GRAND_CAMPAIGN_CATALOG_ID,
    GRAND_CAMPAIGN_PLAN_FORMAT,
    GRAND_CAMPAIGN_PLAN_VERSION,
    HARBOR_BLOCKADE_SKIP,
    HARBOR_BLOCKADE_TARGET,
    MAX_HARBOR_INDEX,
    MAX_RESOLUTION_NUMBER,
    MAX_REVEALED_HARBORS,
    NO_REVEALED_HARBORS_REASON,
    GrandCampaignError,
    HarborBlockadePlan,
    create_harbor_blockade_plan,
    validate_harbor_blockade_public_document,
    verify_harbor_blockade_public_document,
)


SECRET_SEED = "a5" * 32
OTHER_SECRET_SEED = "5a" * 32


def _plan(
    revealed=("harbor-12", "harbor-2", "harbor-7"),
    *,
    seed=SECRET_SEED,
    resolution_number=17,
):
    return create_harbor_blockade_plan(
        revealed,
        secret_seed=seed,
        resolution_number=resolution_number,
    )


def test_fixed_catalog_public_document_is_strict_json_and_seed_free():
    plan = _plan()
    document = plan.to_public_document()
    encoded = json.dumps(document, sort_keys=True, allow_nan=False)

    assert document == {
        "format": GRAND_CAMPAIGN_PLAN_FORMAT,
        "version": GRAND_CAMPAIGN_PLAN_VERSION,
        "catalog": GRAND_CAMPAIGN_CATALOG_ID,
        "event_id": HARBOR_BLOCKADE_EVENT_ID,
        "resolution_number": 17,
        "eligible_harbor_ids": ["harbor-2", "harbor-7", "harbor-12"],
        "outcome": {
            "kind": HARBOR_BLOCKADE_TARGET,
            "harbor_id": plan.target_harbor_id,
        },
    }
    assert SECRET_SEED not in encoded
    assert "secret_seed" not in encoded
    validate_harbor_blockade_public_document(document)
    verify_harbor_blockade_public_document(document, secret_seed=SECRET_SEED)


def test_selection_is_deterministic_and_independent_of_public_input_order():
    first = _plan(("harbor-12", "harbor-2", "harbor-7"))
    second = _plan(("harbor-7", "harbor-12", "harbor-2"))
    third = _plan(["harbor-2", "harbor-7", "harbor-12"])

    assert first == second == third
    assert first.to_public_document() == second.to_public_document()
    assert first.target_harbor_id in first.eligible_harbor_ids


def test_selection_uses_both_secret_seed_and_resolution_number():
    revealed = tuple(f"harbor-{index}" for index in range(16))
    base = _plan(revealed, resolution_number=0)
    seed_targets = {
        _plan(revealed, seed=OTHER_SECRET_SEED, resolution_number=index).target_harbor_id
        for index in range(16)
    }
    resolution_targets = {
        _plan(revealed, resolution_number=index).target_harbor_id
        for index in range(16)
    }

    assert base.target_harbor_id in revealed
    assert len(seed_targets) > 1
    assert len(resolution_targets) > 1
    assert seed_targets != resolution_targets


def test_no_public_harbor_produces_an_explicit_skip_result():
    plan = _plan(())

    assert plan.skipped is True
    assert plan.target_harbor_id is None
    assert plan.forecast_parameters() is None
    assert plan.to_public_document()["outcome"] == {
        "kind": HARBOR_BLOCKADE_SKIP,
        "reason": NO_REVEALED_HARBORS_REASON,
    }
    verify_harbor_blockade_public_document(
        plan.to_public_document(),
        secret_seed=SECRET_SEED,
    )


def test_target_plan_adapts_to_forecast_parameters_without_private_data():
    plan = _plan(("harbor-3",))

    assert plan.skipped is False
    assert plan.target_harbor_id == "harbor-3"
    assert plan.forecast_parameters() == {"harbor_id": "harbor-3"}


def test_plan_never_returns_an_unrevealed_stable_id():
    revealed = {"harbor-1", "harbor-4", "harbor-9"}
    undiscovered_sentinels = {"harbor-31", "harbor-63"}
    plan = _plan(tuple(revealed))
    document_text = json.dumps(plan.to_public_document(), sort_keys=True)

    assert set(plan.eligible_harbor_ids) == revealed
    assert plan.target_harbor_id in revealed
    assert all(hidden not in document_text for hidden in undiscovered_sentinels)


def test_plan_is_immutable_when_more_harbors_are_revealed_during_forecast():
    announcement = _plan(("harbor-1", "harbor-4"))
    before = announcement.to_public_document()
    newly_revealed = ("harbor-8", "harbor-13")

    # The resolution reads the frozen plan.  It does not call the selector
    # again with the larger, later public set.
    assert announcement.target_harbor_id in {"harbor-1", "harbor-4"}
    assert not set(newly_revealed).intersection(announcement.eligible_harbor_ids)
    assert announcement.to_public_document() == before
    with pytest.raises(FrozenInstanceError):
        announcement.target_harbor_id = newly_revealed[0]


def test_public_document_round_trip_is_detached_and_immutable_by_value():
    original = _plan()
    document = original.to_public_document()
    parsed = HarborBlockadePlan.from_public_document(copy.deepcopy(document))

    document["eligible_harbor_ids"].append("harbor-20")
    document["outcome"]["harbor_id"] = "harbor-20"
    assert parsed == original
    assert parsed.to_public_document() != document


@pytest.mark.parametrize(
    "revealed",
    [
        ("harbor-1", "harbor-1"),
        ("harbor--1",),
        ("harbor-01",),
        ("Harbor-1",),
        ("harbor-64",),
        ("edge-1",),
        (1,),
        "harbor-1",
        {"harbor-1"},
    ],
)
def test_duplicate_noncanonical_out_of_range_and_non_array_ids_are_rejected(
    revealed,
):
    with pytest.raises(GrandCampaignError):
        _plan(revealed)


def test_revealed_harbor_count_is_bounded_before_id_processing():
    too_many = [f"harbor-{index}" for index in range(MAX_REVEALED_HARBORS + 1)]

    with pytest.raises(GrandCampaignError, match=str(MAX_REVEALED_HARBORS)):
        _plan(too_many)
    assert MAX_HARBOR_INDEX == MAX_REVEALED_HARBORS - 1


@pytest.mark.parametrize(
    ("seed", "resolution_number"),
    [
        ("a" * 63, 0),
        ("A" * 64, 0),
        ("g" * 64, 0),
        (b"a" * 64, 0),
        (SECRET_SEED, -1),
        (SECRET_SEED, True),
        (SECRET_SEED, MAX_RESOLUTION_NUMBER + 1),
    ],
)
def test_secret_seed_and_resolution_number_are_strictly_bounded(
    seed,
    resolution_number,
):
    with pytest.raises(GrandCampaignError):
        _plan(seed=seed, resolution_number=resolution_number)


def test_public_parser_rejects_unknown_fields_catalogs_and_noncanonical_pool():
    document = _plan().to_public_document()
    mutations = []

    extra = copy.deepcopy(document)
    extra["authority_seed"] = SECRET_SEED
    mutations.append(extra)
    wrong_catalog = copy.deepcopy(document)
    wrong_catalog["catalog"] = "grand_campaign_v2"
    mutations.append(wrong_catalog)
    wrong_event = copy.deepcopy(document)
    wrong_event["event_id"] = "future_event_v1"
    mutations.append(wrong_event)
    unordered = copy.deepcopy(document)
    unordered["eligible_harbor_ids"].reverse()
    mutations.append(unordered)
    non_json = copy.deepcopy(document)
    non_json["eligible_harbor_ids"] = tuple(non_json["eligible_harbor_ids"])
    mutations.append(non_json)

    for mutation in mutations:
        with pytest.raises(GrandCampaignError):
            HarborBlockadePlan.from_public_document(mutation)


def test_public_parser_rejects_inconsistent_target_and_skip_outcomes():
    targeted = _plan(("harbor-1", "harbor-2")).to_public_document()
    unknown_target = copy.deepcopy(targeted)
    unknown_target["outcome"]["harbor_id"] = "harbor-3"
    with pytest.raises(GrandCampaignError, match="予告時点"):
        HarborBlockadePlan.from_public_document(unknown_target)

    skip_with_pool = copy.deepcopy(targeted)
    skip_with_pool["outcome"] = {
        "kind": HARBOR_BLOCKADE_SKIP,
        "reason": NO_REVEALED_HARBORS_REASON,
    }
    with pytest.raises(GrandCampaignError, match="封鎖対象"):
        HarborBlockadePlan.from_public_document(skip_with_pool)

    empty = _plan(()).to_public_document()
    target_without_pool = copy.deepcopy(empty)
    target_without_pool["outcome"] = {
        "kind": HARBOR_BLOCKADE_TARGET,
        "harbor_id": "harbor-0",
    }
    with pytest.raises(GrandCampaignError, match="公開交換所がない"):
        HarborBlockadePlan.from_public_document(target_without_pool)


def test_authority_verification_detects_target_seed_and_resolution_tampering():
    plan = _plan(tuple(f"harbor-{index}" for index in range(16)))
    document = plan.to_public_document()

    with pytest.raises(GrandCampaignError, match="authority seed"):
        verify_harbor_blockade_public_document(
            document,
            secret_seed=OTHER_SECRET_SEED,
        )

    changed_resolution = copy.deepcopy(document)
    for candidate in range(1, 64):
        changed_resolution["resolution_number"] = plan.resolution_number + candidate
        try:
            verify_harbor_blockade_public_document(
                changed_resolution,
                secret_seed=SECRET_SEED,
            )
        except GrandCampaignError:
            break
    else:  # pragma: no cover - practically impossible for this fixed vector
        pytest.fail("resolution tampering did not change the selected target")


def test_document_cannot_smuggle_seed_through_outcome_or_top_level_fields():
    document = _plan().to_public_document()
    top_level = copy.deepcopy(document)
    top_level["secret_seed"] = SECRET_SEED
    outcome = copy.deepcopy(document)
    outcome["outcome"]["secret_seed"] = SECRET_SEED

    with pytest.raises(GrandCampaignError):
        validate_harbor_blockade_public_document(top_level)
    with pytest.raises(GrandCampaignError):
        validate_harbor_blockade_public_document(outcome)
