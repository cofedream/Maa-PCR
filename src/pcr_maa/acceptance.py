"""Evaluate reviewed daily runs; never launches a controller or changes ledgers."""
import hashlib
import math
import re
import statistics
from datetime import date
from pathlib import Path


def _digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _elapsed(report):
    values = [report.get(name) for name in ("started_at", "finished_at")]
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
        raise ValueError("invalid_run_timing")
    elapsed = values[1] - values[0]
    if elapsed <= 0:
        raise ValueError("invalid_run_timing")
    return elapsed


def evaluate_samples(samples):
    """Explicit live review is required; timing alone is not correctness evidence."""
    accepted, excluded = [], []
    for sample in samples:
        report = sample["report"]
        reason = sample.get("excluded_reason")
        if reason:
            excluded.append({"run_id": report.get("run_id"), "reason": reason})
            continue
        revision = sample.get("revision")
        if type(revision) is not int or revision not in (38, 39):
            raise ValueError("unexpected_revision")
        review = sample.get("review", {})
        if any(review.get(key) is not True for key in
               ("live_device", "comparable_workload", "no_duplicate_consumption", "no_wrong_settlement")):
            raise ValueError("live_correctness_review_required")
        if report.get("outcome") != "complete" or report.get("supported_chain_complete") is not True:
            raise ValueError("incomplete_run")
        if not all(_digest(sample.get(key)) for key in
                   ("config_sha256", "workload_sha256", "manifest_sha256", "report_sha256")):
            raise ValueError("sample_fingerprints_required")
        day = report.get("game_day")
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
            raise ValueError("invalid_game_day")
        plan = tuple(step["name"] for step in report["steps"])
        if not plan or any(step.get("status") not in
                           ("complete", "executed", "skipped_goal_met", "skipped_event_unavailable")
                           for step in report["steps"]):
            raise ValueError("incomplete_steps")
        accepted.append({"day": day, "revision": revision, "elapsed": _elapsed(report),
                         "plan": plan, "config": sample["config_sha256"],
                         "workload": sample["workload_sha256"], "manifest": sample["manifest_sha256"]})
    accepted.sort(key=lambda item: item["day"])
    if len({item["day"] for item in accepted}) != len(accepted):
        raise ValueError("duplicate_game_day")
    if accepted and len({(item["plan"], item["config"], item["workload"]) for item in accepted}) != 1:
        raise ValueError("incomparable_samples")
    for revision in (38, 39):
        if len({item["manifest"] for item in accepted if item["revision"] == revision}) > 1:
            raise ValueError("candidate_changed_requires_new_batch")
    values = {revision: [item["elapsed"] for item in accepted if item["revision"] == revision]
              for revision in (38, 39)}
    alternating = all(left["revision"] != right["revision"]
                      for left, right in zip(accepted, accepted[1:]))
    complete = len(accepted) == 6 and all(len(group) == 3 for group in values.values()) and alternating
    medians = {revision: statistics.median(group) if group else None for revision, group in values.items()}
    ratio = medians[39] / medians[38] if all(medians.values()) else None
    passed = complete and ratio <= .6 and max(values[39]) <= max(values[38])
    return {"status": "passed" if passed else "failed" if complete else "pending",
            "valid_game_days": len(accepted), "required_game_days": 6,
            "alternating": alternating, "baseline_median_seconds": medians[38],
            "candidate_median_seconds": medians[39], "candidate_to_baseline_ratio": ratio,
            "target_ratio": .6, "excluded": excluded,
            "candidate_manifest_sha256": next((item["manifest"] for item in accepted
                                                if item["revision"] == 39), None),
            "correctness_basis": "explicit live report review; not inferred from elapsed time"}


def load_registry(registry_path):
    import json
    registry_path = Path(registry_path).resolve()
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    samples = []
    for entry in registry["samples"]:
        report_path = Path(entry["report_path"])
        if not report_path.is_absolute():
            report_path = registry_path.parent / report_path
        payload = report_path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != entry.get("report_sha256"):
            raise ValueError("sample_report_changed")
        if "manifest_path" in entry:
            manifest_path = Path(entry["manifest_path"])
            if not manifest_path.is_absolute():
                manifest_path = registry_path.parent / manifest_path
            if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != entry["manifest_sha256"]:
                raise ValueError("sample_manifest_changed")
        samples.append({**entry, "report": json.loads(payload.decode("utf-8"))})
    return evaluate_samples(samples)
