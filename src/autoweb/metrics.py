"""Deterministic aggregation of login outcomes into reliability and latency metrics."""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median
from typing import Iterable, Mapping, Sequence

from .ai.usage import UsageSnapshot
from .login.results import LoginResult


def percentile(samples: Iterable[float], fraction: float) -> float:
    """Nearest-rank percentile; deterministic for small sample counts."""
    if not 0 <= fraction <= 1 or not math.isfinite(fraction):
        raise ValueError("Percentile fraction must be between 0 and 1")
    ordered = sorted(samples)
    if not ordered:
        return 0.0
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[rank - 1]


@dataclass(frozen=True)
class DurationStats:
    count: int
    minimum: float
    median: float
    p95: float
    maximum: float
    mean: float

    @classmethod
    def from_samples(cls, samples: Iterable[float]) -> "DurationStats":
        values = [float(value) for value in samples]
        if not values:
            return cls(0, 0.0, 0.0, 0.0, 0.0, 0.0)
        return cls(len(values), min(values), median(values), percentile(values, 0.95),
                   max(values), mean(values))

    def as_dict(self) -> dict:
        return {
            "count": self.count,
            "min_seconds": round(self.minimum, 6),
            "median_seconds": round(self.median, 6),
            "p95_seconds": round(self.p95, 6),
            "max_seconds": round(self.maximum, 6),
            "mean_seconds": round(self.mean, 6),
        }


@dataclass(frozen=True)
class FlowSummary:
    flow: str
    runs: int
    succeeded: int
    failed: int
    budget_seconds: float
    budget_overruns: int
    duration: DurationStats
    steps: Mapping[str, DurationStats]
    failures: Mapping[str, int]

    @property
    def success_rate(self) -> float:
        return self.succeeded / self.runs if self.runs else 0.0

    def as_dict(self) -> dict:
        return {
            "flow": self.flow,
            "runs": self.runs,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "success_rate": round(self.success_rate, 6),
            "budget_seconds": self.budget_seconds,
            "budget_overruns": self.budget_overruns,
            "duration": self.duration.as_dict(),
            "steps": {key: stats.as_dict() for key, stats in self.steps.items()},
            "failures": dict(self.failures),
        }


def summarize_flow(flow: str, results: Sequence[LoginResult], *,
                   budget_seconds: float) -> FlowSummary:
    """Credit a run that stayed inside the configured login deadline."""
    if not math.isfinite(budget_seconds) or budget_seconds <= 0:
        raise ValueError("Budget must be a positive number of seconds")
    outcomes = list(results)
    succeeded = sum(result.status == "succeeded" for result in outcomes)
    failures = Counter(result.reason or "unknown" for result in outcomes
                       if result.status != "succeeded")
    steps: dict[str, list[float]] = {}
    for result in outcomes:
        for step in result.steps:
            steps.setdefault(f"{step.index}:{step.action}", []).append(step.result.elapsed_seconds)
    ordered_steps = {key: DurationStats.from_samples(steps[key]) for key in sorted(steps)}
    return FlowSummary(
        flow=flow,
        runs=len(outcomes),
        succeeded=succeeded,
        failed=len(outcomes) - succeeded,
        budget_seconds=float(budget_seconds),
        budget_overruns=sum(result.elapsed_seconds > budget_seconds for result in outcomes),
        duration=DurationStats.from_samples(result.elapsed_seconds for result in outcomes),
        steps=ordered_steps,
        failures=dict(failures),
    )


@dataclass(frozen=True)
class BenchmarkReport:
    generated_at: str
    flows: tuple[FlowSummary, ...]
    overall: DurationStats
    ai_usage: UsageSnapshot | None = None
    ai_cost_usd: float | None = None

    @property
    def total_runs(self) -> int:
        return sum(flow.runs for flow in self.flows)

    @property
    def total_succeeded(self) -> int:
        return sum(flow.succeeded for flow in self.flows)

    @property
    def success_rate(self) -> float:
        return self.total_succeeded / self.total_runs if self.total_runs else 0.0

    def as_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "total_runs": self.total_runs,
            "total_succeeded": self.total_succeeded,
            "success_rate": round(self.success_rate, 6),
            "overall": self.overall.as_dict(),
            "flows": [flow.as_dict() for flow in self.flows],
            "ai": (None if self.ai_usage is None else {
                "calls": self.ai_usage.calls,
                "prompt_tokens": self.ai_usage.prompt_tokens,
                "candidate_tokens": self.ai_usage.candidate_tokens,
                "total_tokens": self.ai_usage.total_tokens,
                "cost_usd": (None if self.ai_cost_usd is None else round(self.ai_cost_usd, 8)),
            }),
        }

    def to_markdown(self) -> str:
        lines = [
            "# AutoWeb login benchmark", "",
            f"Generated: {self.generated_at}",
            f"Runs: {self.total_runs} | Succeeded: {self.total_succeeded}",
            f"Success rate: {self.success_rate:.1%}",
            f"Latency p50: {self.overall.median:.3f}s | "
            f"p95: {self.overall.p95:.3f}s | max: {self.overall.maximum:.3f}s", "",
            "| Flow | Runs | Success | Budget (s) | p50 (s) | p95 (s) | Max (s) | Overruns |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for flow in self.flows:
            lines.append(
                f"| {flow.flow} | {flow.runs} | {flow.success_rate:.0%} | "
                f"{flow.budget_seconds:g} | {flow.duration.median:.3f} | "
                f"{flow.duration.p95:.3f} | {flow.duration.maximum:.3f} | "
                f"{flow.budget_overruns} |"
            )
        step_rows = [
            (flow.flow, key, stats) for flow in self.flows for key, stats in flow.steps.items()
        ]
        if step_rows:
            lines += ["", "| Flow | Step | Samples | p50 (s) | p95 (s) |",
                      "| --- | --- | --- | --- | --- |"]
            lines += [f"| {flow} | {step} | {stats.count} | {stats.median:.3f} | "
                      f"{stats.p95:.3f} |" for flow, step, stats in step_rows]
        if self.ai_usage is not None:
            usage = self.ai_usage
            cost = ("not priced" if self.ai_cost_usd is None
                    else f"${self.ai_cost_usd:.6f}")
            lines += ["", f"AI calls: {usage.calls} | prompt tokens: {usage.prompt_tokens} | "
                          f"candidate tokens: {usage.candidate_tokens} | cost: {cost}"]
        return "\n".join(lines) + "\n"


def build_report(runs_by_flow: Mapping[str, Sequence[LoginResult]],
                 budgets: Mapping[str, float], *, generated_at: str,
                 ai_usage: UsageSnapshot | None = None,
                 ai_cost_usd: float | None = None) -> BenchmarkReport:
    """Summarize each flow and pool every run for the headline latency figures."""
    missing = set(runs_by_flow) - set(budgets)
    if missing:
        raise ValueError(f"Missing login budget for: {', '.join(sorted(missing))}")
    flows = tuple(summarize_flow(name, results, budget_seconds=budgets[name])
                  for name, results in runs_by_flow.items())
    pooled = [result.elapsed_seconds for results in runs_by_flow.values() for result in results]
    return BenchmarkReport(
        generated_at=generated_at,
        flows=flows,
        overall=DurationStats.from_samples(pooled),
        ai_usage=ai_usage,
        ai_cost_usd=ai_cost_usd,
    )


def write_report(report: BenchmarkReport, destination: str | Path) -> tuple[Path, Path]:
    """Write JSON and Markdown siblings; return both paths."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    json_path = destination.with_suffix(".json")
    json_path.write_text(json.dumps(report.as_dict(), indent=2) + "\n", encoding="utf-8")
    markdown_path = destination.with_suffix(".md")
    markdown_path.write_text(report.to_markdown(), encoding="utf-8")
    return json_path, markdown_path
