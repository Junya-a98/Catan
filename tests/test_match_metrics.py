import json
import math

import pytest

from game.match_metrics import (
    MATCH_METRICS_FORMAT,
    MATCH_METRICS_VERSION,
    MatchMetrics,
    MatchMetricsError,
    MatchMetricsTracker,
    restore_match_metrics,
    serialize_match_metrics,
)


def test_empty_tracker_is_json_safe_and_uses_neutral_zero_luck():
    metrics = MatchMetricsTracker()
    metrics.register_player("seat-1", "Player1")

    player = metrics.player("seat-1")

    assert player.luck_index == 100
    assert json.loads(json.dumps(metrics.serialize(), allow_nan=False)) == {
        "format": MATCH_METRICS_FORMAT,
        "version": MATCH_METRICS_VERSION,
        "players": [
            {
                "player_id": "seat-1",
                "display_name": "Player1",
                "roads_built": 0,
                "settlements_built": 0,
                "cities_built": 0,
                "domestic_trades": 0,
                "bank_trades": 0,
                "actual_production_units": 0,
                "expected_production_units": 0.0,
                "luck_index": 100.0,
            }
        ],
        "point_checkpoints": [],
        "important_events": [],
    }


def test_records_build_trade_and_production_totals_per_player():
    metrics = MatchMetrics()
    metrics.register_player("alice", "Alice")
    metrics.register_player("bob", "Bob")

    metrics.record_build("alice", "road", count=2)
    metrics.record_build("alice", "settlement")
    metrics.record_build("alice", "city")
    metrics.record_domestic_trade("alice", "bob", count=2)
    metrics.record_bank_trade("alice", count=3)
    metrics.record_production("alice", actual_units=5, expected_units=4)
    metrics.record_production("alice", actual_units=2, expected_units=1.5)

    alice = metrics.player("alice")
    bob = metrics.player("bob")

    assert (alice.roads_built, alice.settlements_built, alice.cities_built) == (
        2,
        1,
        1,
    )
    assert alice.domestic_trades == 2
    assert bob.domestic_trades == 2
    assert alice.bank_trades == 3
    assert alice.actual_production_units == 7
    assert alice.expected_production_units == 5.5
    assert alice.luck_index == pytest.approx(127.272727)
    assert bob.luck_index == 100


def test_zero_expected_production_never_creates_infinity_or_nan():
    metrics = MatchMetrics()
    metrics.record_production("seat-1", actual_units=3, expected_units=0)

    assert metrics.player("seat-1").luck_index == 100
    json.dumps(metrics.to_dict(), allow_nan=False)


def test_point_checkpoints_and_important_events_link_to_replay_frames():
    metrics = MatchMetrics()

    first = metrics.record_point_checkpoint(
        "initial-placement-complete",
        {"seat-1": 2, "seat-2": 2},
        detail="初期配置完了",
        replay_frame_index=3,
    )
    second = metrics.record_point_checkpoint(
        "turn-ended",
        {"seat-1": 4, "seat-2": 2},
    )
    highlight = metrics.record_important_event(
        "最長交易路",
        "Player1が最長交易路を獲得",
        replay_frame_index=18,
    )
    unknown_frame = metrics.record_important_event("交易成立", "木材と羊を交換")

    assert first.sequence == 0
    assert first.replay_frame_index == 3
    assert second.sequence == 1
    assert second.replay_frame_index is None
    assert highlight.sequence == 0
    assert highlight.replay_frame_index == 18
    assert unknown_frame.sequence == 1
    assert unknown_frame.replay_frame_index is None
    assert [player.player_id for player in metrics.players] == ["seat-1", "seat-2"]


def test_round_trip_preserves_metrics_order_and_all_events():
    metrics = MatchMetrics()
    metrics.register_player("p1", "Player1")
    metrics.record_build("p1", "road")
    metrics.record_domestic_trade("p1")
    metrics.record_bank_trade("p1")
    metrics.record_production("p1", actual_units=2, expected_units=2.5)
    metrics.record_point_checkpoint(
        "turn-ended", {"p1": 3}, replay_frame_index=4
    )
    metrics.record_important_event("都市建設", "Player1", replay_frame_index=4)

    document = serialize_match_metrics(metrics)
    restored = restore_match_metrics(json.loads(json.dumps(document)))

    assert isinstance(restored, MatchMetrics)
    assert restored.to_dict() == document
    returned = restored.to_dict()
    returned["players"][0]["roads_built"] = 999
    returned["point_checkpoints"][0]["points"]["p1"] = 999
    assert restored.player("p1").roads_built == 1
    assert restored.point_checkpoints[0].points["p1"] == 3


def test_restore_defaults_absent_historic_sections_and_player_fields():
    assert MatchMetrics.restore({}).to_dict() == {
        "format": MATCH_METRICS_FORMAT,
        "version": MATCH_METRICS_VERSION,
        "players": [],
        "point_checkpoints": [],
        "important_events": [],
    }

    restored = MatchMetrics.from_dict({"players": [{"player_id": "legacy"}]})
    player = restored.player("legacy")

    assert player.display_name == "legacy"
    assert player.roads_built == 0
    assert player.domestic_trades == 0
    assert player.actual_production_units == 0
    assert player.expected_production_units == 0
    assert player.luck_index == 100


@pytest.mark.parametrize(
    "document, message",
    [
        (None, "must be an object"),
        ({"format": "other"}, "unsupported match metrics format"),
        ({"version": True}, "version must be an integer"),
        ({"version": 2}, "unsupported match metrics version"),
        ({"players": None}, "players must be an array"),
        ({"players": [None]}, "player metric must be an object"),
        ({"players": [{"player_id": ""}]}, "player_id must not be empty"),
        (
            {"players": [{"player_id": "p"}, {"player_id": "p"}]},
            "duplicate player_id",
        ),
        (
            {"players": [{"player_id": "p", "roads_built": -1}]},
            "roads_built must be between",
        ),
        (
            {
                "players": [
                    {"player_id": "p", "expected_production_units": math.nan}
                ]
            },
            "must be finite",
        ),
        (
            {
                "point_checkpoints": [
                    {
                        "semantic_event": "turn-ended",
                        "points": {"p": 2},
                        "replay_frame_index": -1,
                    }
                ]
            },
            "replay_frame_index",
        ),
        (
            {
                "point_checkpoints": [
                    {
                        "sequence": 1,
                        "semantic_event": "turn-ended",
                        "points": {},
                    }
                ]
            },
            "sequence must be contiguous",
        ),
        (
            {"important_events": [{"title": "", "detail": ""}]},
            "title must not be empty",
        ),
        (
            {
                "important_events": [
                    {"title": "bad\u001b", "detail": "terminal escape"}
                ]
            },
            "control characters",
        ),
    ],
)
def test_restore_rejects_malformed_or_unsafe_documents(document, message):
    with pytest.raises(MatchMetricsError, match=message):
        MatchMetrics.from_dict(document)


def test_runtime_validation_does_not_partially_record_invalid_trade_partner():
    metrics = MatchMetrics()
    metrics.register_player("p1")

    with pytest.raises(MatchMetricsError):
        metrics.record_domestic_trade("p1", "")

    assert metrics.player("p1").domestic_trades == 0


def test_counter_overflow_does_not_partially_mutate_multi_field_operations():
    metrics = MatchMetrics.from_dict(
        {
            "players": [
                {
                    "player_id": "full",
                    "domestic_trades": 10_000_000,
                    "expected_production_units": 10_000_000,
                },
                {"player_id": "other"},
            ]
        }
    )

    with pytest.raises(MatchMetricsError):
        metrics.record_domestic_trade("other", "full")
    with pytest.raises(MatchMetricsError):
        metrics.record_production("full", actual_units=1, expected_units=1)

    assert metrics.player("other").domestic_trades == 0
    assert metrics.player("full").domestic_trades == 10_000_000
    assert metrics.player("full").actual_production_units == 0


@pytest.mark.parametrize("building", ("ROAD", "town", ""))
def test_unknown_building_types_are_rejected(building):
    metrics = MatchMetrics()

    with pytest.raises(MatchMetricsError, match="building must be one of"):
        metrics.record_build("p1", building)


def test_players_property_is_a_snapshot_not_mutable_tracker_state():
    metrics = MatchMetrics()
    metrics.record_build("p1", "road")

    exposed = metrics.players[0]
    exposed.roads_built = 999

    assert metrics.player("p1").roads_built == 1
