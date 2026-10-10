import copy
import hashlib
import json

import pytest

from pcr_maa.acceptance import evaluate_samples, load_registry


def samples(candidate=59):
    return [{"revision": 38 if index % 2 == 0 else 39,
             "config_sha256": "a" * 64, "workload_sha256": "b" * 64,
             "manifest_sha256": ("c" if index % 2 == 0 else "d") * 64,
             "report_sha256": "e" * 64,
             "review": dict.fromkeys(("live_device", "comparable_workload", "no_duplicate_consumption", "no_wrong_settlement"), True),
             "report": {"run_id": str(index), "game_day": f"2026-10-{10 + index}",
                        "outcome": "complete", "supported_chain_complete": True,
                        "started_at": 100, "finished_at": 200 if index % 2 == 0 else 100 + candidate,
                        "steps": [{"name": "hard", "status": "complete"}]}}
            for index in range(6)]


def test_six_alternating_days_pass_only_measured_target():
    assert evaluate_samples(samples())["status"] == "passed"
    assert evaluate_samples(samples(61))["status"] == "failed"
    assert evaluate_samples(samples()[:2])["status"] == "pending"


@pytest.mark.parametrize("change,error", [
    (lambda s: s[1]["review"].update(live_device=False), "live_correctness"),
    (lambda s: s[1].update(config_sha256="f" * 64), "incomparable"),
    (lambda s: s[1].update(manifest_sha256="f" * 64), "candidate_changed"),
    (lambda s: s[1]["report"].update(game_day=s[0]["report"]["game_day"]), "duplicate_game_day"),
    (lambda s: s[1]["report"].update(outcome="partial"), "incomplete"),
    (lambda s: s[1]["report"].update(finished_at=float("nan")), "invalid_run_timing"),
])
def test_bad_evidence_cannot_pass(change, error):
    value = samples()
    change(value)
    with pytest.raises(ValueError, match=error):
        evaluate_samples(value)


def test_excluded_failure_remains_visible_and_does_not_count():
    value = samples()
    failed = copy.deepcopy(value[0])
    failed["excluded_reason"] = "different daily workload"
    failed["report"]["outcome"] = "failed"
    result = evaluate_samples([failed] + value)
    assert result["status"] == "passed"
    assert len(result["excluded"]) == 1


def test_candidate_longest_cannot_exceed_baseline_longest():
    value = samples()
    value[1]["report"]["finished_at"] = 201
    assert evaluate_samples(value)["status"] == "failed"


def test_registry_binds_original_report_bytes(tmp_path):
    report = samples()[0]
    payload = json.dumps(report.pop("report")).encode()
    path = tmp_path / "report.json"
    path.write_bytes(payload)
    report.update(report_path="report.json", report_sha256=hashlib.sha256(payload).hexdigest())
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"samples": [report]}), encoding="utf-8")
    assert load_registry(registry)["status"] == "pending"
    path.write_bytes(payload + b" ")
    with pytest.raises(ValueError, match="sample_report_changed"):
        load_registry(registry)
