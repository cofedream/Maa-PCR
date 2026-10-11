import copy
import hashlib
import json

import pytest

from pcr_maa.acceptance import evaluate_samples, load_registry


def samples(candidate=80, count=2):
    return [{"revision": 38 if index % 2 == 0 else 39,
             "config_sha256": "a" * 64, "workload_sha256": "b" * 64,
             "manifest_sha256": ("c" if index % 2 == 0 else "d") * 64,
             "report_sha256": "e" * 64,
             "review": dict.fromkeys(("live_device", "comparable_workload", "no_duplicate_consumption", "no_wrong_settlement"), True),
             "report": {"run_id": str(index), "game_day": f"2026-10-{10 + index}",
                        "outcome": "complete", "supported_chain_complete": True,
                        "started_at": 100, "finished_at": 200 if index % 2 == 0 else 100 + candidate,
                        "steps": [{"name": "hard", "status": "complete"}]}}
            for index in range(count)]


def test_one_comparable_pair_requires_faster_timing_and_explicit_review():
    result = evaluate_samples(samples(), meaningful_speedup_reviewed=True)
    assert result["status"] == "passed"
    assert result["required_game_days"] == 2
    assert result["candidate_to_baseline_ratio"] == .8
    assert evaluate_samples(samples())["status"] == "pending"
    assert evaluate_samples(samples(), meaningful_speedup_reviewed=1)["status"] == "pending"
    assert evaluate_samples(samples()[:1], meaningful_speedup_reviewed=True)["status"] == "pending"
    for candidate in (100, 101):
        assert evaluate_samples(samples(candidate), meaningful_speedup_reviewed=True)["status"] == "failed"


def test_additional_pairs_are_balanced_and_keep_complete_slower_samples():
    value = samples(count=6)
    value[1]["report"]["finished_at"] = 201
    result = evaluate_samples(value, meaningful_speedup_reviewed=True)
    assert result["status"] == "passed"
    assert result["candidate_median_seconds"] == 80
    assert result["candidate_longest_seconds"] == 101
    assert not result["excluded"]
    assert evaluate_samples(value[:-1], meaningful_speedup_reviewed=True)["status"] == "pending"


@pytest.mark.parametrize("change,error", [
    (lambda s: s[1]["review"].update(live_device=False), "live_correctness"),
    (lambda s: s[1].update(config_sha256="f" * 64), "incomparable"),
    (lambda s: s[1].update(manifest_sha256="f" * 64), "candidate_changed"),
    (lambda s: s[1]["report"].update(game_day=s[0]["report"]["game_day"]), "duplicate_game_day"),
    (lambda s: s[1]["report"].update(outcome="partial"), "incomplete"),
    (lambda s: s[1]["report"].update(finished_at=float("nan")), "invalid_run_timing"),
])
def test_bad_evidence_cannot_pass(change, error):
    value = samples(count=6)
    change(value)
    with pytest.raises(ValueError, match=error):
        evaluate_samples(value)


def test_excluded_failure_remains_visible_and_does_not_count():
    value = samples()
    failed = copy.deepcopy(value[0])
    failed["excluded_reason"] = "different daily workload"
    failed["report"]["outcome"] = "failed"
    result = evaluate_samples([failed] + value, meaningful_speedup_reviewed=True)
    assert result["status"] == "passed"
    assert len(result["excluded"]) == 1


def test_same_revision_samples_are_not_a_comparable_pair():
    value = samples()
    value[1].update(revision=38, manifest_sha256="c" * 64)
    assert evaluate_samples(value, meaningful_speedup_reviewed=True)["status"] == "pending"


def test_registry_binds_original_report_bytes(tmp_path):
    entries = samples()
    for index, entry in enumerate(entries):
        payload = json.dumps(entry.pop("report")).encode()
        path = tmp_path / f"report-{index}.json"
        path.write_bytes(payload)
        entry.update(report_path=path.name, report_sha256=hashlib.sha256(payload).hexdigest())
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"samples": entries}), encoding="utf-8")
    assert load_registry(registry)["status"] == "pending"
    registry.write_text(json.dumps({"samples": entries, "meaningful_speedup_reviewed": True}), encoding="utf-8")
    assert load_registry(registry)["status"] == "passed"
    path.write_bytes(payload + b" ")
    with pytest.raises(ValueError, match="sample_report_changed"):
        load_registry(registry)
