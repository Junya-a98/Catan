import json

import pytest

from simulate import main


def test_simulate_cli_writes_dashboard_and_json(tmp_path, capsys):
    output_dir = tmp_path / "reports"

    assert (
        main(
            [
                "--games",
                "2",
                "--seed",
                "31",
                "--board-seed",
                "404",
                "--mode",
                "fully_random",
                "--target",
                "5",
                "--players",
                "2",
                "--personalities",
                "trader,disruptor",
                "--output-dir",
                str(output_dir),
                "--basename",
                "test-batch",
                "--quiet",
            ]
        )
        == 0
    )

    payload = json.loads((output_dir / "test-batch.json").read_text(encoding="utf-8"))
    html = (output_dir / "test-batch.html").read_text(encoding="utf-8")
    output = capsys.readouterr().out
    assert payload["summary"]["matches"] == 2
    assert payload["summary"]["completed"] == 2
    assert payload["metadata"]["board_seed"] == 404
    assert payload["metadata"]["personality_lineup"] == "trader / disruptor"
    assert [
        player["personality"] for player in payload["matches"][0]["players"]
    ] == ["trader", "disruptor"]
    assert [
        player["personality"] for player in payload["matches"][1]["players"]
    ] == ["disruptor", "trader"]
    assert "AI自己対戦レポート" in output
    assert "CATAN風 AI自己対戦ダッシュボード" in html


def test_simulate_cli_rejects_personality_count_mismatch(tmp_path):
    with pytest.raises(SystemExit) as error:
        main(
            [
                "--games",
                "1",
                "--players",
                "3",
                "--personalities",
                "standard,trader",
                "--output-dir",
                str(tmp_path),
                "--quiet",
            ]
        )

    assert error.value.code == 2
