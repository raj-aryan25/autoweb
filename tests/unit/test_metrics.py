"""Aggregated reliability and latency metrics without a browser."""

import json

import pytest

from autoweb.ai.usage import UsageSnapshot
from autoweb.contracts import ActionResult
from autoweb.login import LoginResult, StepResult
from autoweb.metrics import (
    BenchmarkReport, DurationStats, build_report, percentile, summarize_flow, write_report,
)


def run(elapsed, status="succeeded", steps=(), reason=None):
    return LoginResult("site", status, elapsed, tuple(steps), reason=reason)


def step(index, action, elapsed):
    return StepResult(index, action, ActionResult(action, "succeeded", elapsed))


def test_percentile_is_nearest_rank_and_validates_fraction():
    samples = [4.0, 1.0, 3.0, 2.0]
    assert percentile(samples, 0) == 1.0
    assert percentile(samples, 0.5) == 2.0
    assert percentile(samples, 0.95) == 4.0
    assert percentile([], 0.95) == 0.0
    for fraction in (-0.1, 1.1, float("nan")):
        with pytest.raises(ValueError):
            percentile(samples, fraction)


def test_duration_stats_handles_no_samples():
    stats = DurationStats.from_samples([])
    assert stats.count == 0 and stats.p95 == 0.0
    assert stats.as_dict()["p95_seconds"] == 0.0


def test_flow_summary_reports_success_failures_and_budget_overruns():
    results = [
        run(1.0, steps=[step(1, "fill", 0.2), step(2, "submit", 0.5)]),
        run(2.0, steps=[step(1, "fill", 0.3)]),
        run(30.0, status="failed", steps=[step(1, "fill", 0.4)],
            reason="credentials_rejected"),
    ]
    summary = summarize_flow("password", results, budget_seconds=10)
    assert (summary.runs, summary.succeeded, summary.failed) == (3, 2, 1)
    assert summary.success_rate == pytest.approx(2 / 3)
    assert summary.budget_overruns == 1
    assert summary.failures == {"credentials_rejected": 1}
    assert summary.duration.count == 3 and summary.duration.maximum == 30.0
    assert summary.steps["1:fill"].count == 3
    assert summary.steps["2:submit"].count == 1


def test_flow_summary_rejects_a_nonpositive_budget():
    with pytest.raises(ValueError):
        summarize_flow("password", [run(1.0)], budget_seconds=0)


def test_report_pools_runs_and_serializes_json_and_markdown(tmp_path):
    report = build_report(
        {"password": [run(1.0), run(2.0)], "email_otp": [run(3.0)]},
        {"password": 10, "email_otp": 10},
        generated_at="2026-11-03T00:00:00+00:00",
        ai_usage=UsageSnapshot(calls=0),
        ai_cost_usd=0.0,
    )
    assert report.total_runs == 3 and report.total_succeeded == 3
    assert report.success_rate == 1.0
    assert report.overall.median == 2.0
    data = json.loads(json.dumps(report.as_dict()))
    assert [flow["flow"] for flow in data["flows"]] == ["password", "email_otp"]
    assert data["ai"]["calls"] == 0
    markdown = report.to_markdown()
    assert "Success rate: 100.0%" in markdown
    assert "| password | 2 | 100% |" in markdown
    json_path, markdown_path = write_report(report, tmp_path / "bench" / "latest")
    assert json.loads(json_path.read_text())["total_runs"] == 3
    assert markdown_path.read_text() == markdown


def test_report_requires_a_budget_for_every_flow():
    with pytest.raises(ValueError, match="email_otp"):
        build_report({"email_otp": [run(1.0)]}, {}, generated_at="now")


def test_report_without_runs_stays_serializable():
    report = build_report({}, {}, generated_at="now")
    assert report.success_rate == 0.0
    assert isinstance(report, BenchmarkReport)
    assert report.as_dict()["overall"]["count"] == 0
