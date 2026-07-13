import json

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
    assert "AI自己対戦レポート" in output
    assert "CATAN風 AI自己対戦ダッシュボード" in html
