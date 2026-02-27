from __future__ import annotations

import random
from statistics import mean

from ai_trader.types import SignificanceReport, Trade


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    pos = q * (len(sorted_values) - 1)
    low = int(pos)
    high = min(low + 1, len(sorted_values) - 1)
    frac = pos - low
    return sorted_values[low] * (1 - frac) + sorted_values[high] * frac


def evaluate_significance(
    trades: list[Trade],
    benchmark: str = "time_matched_random",
    bootstrap_rounds: int = 2000,
    random_seed: int = 7,
) -> SignificanceReport:
    if not trades:
        return SignificanceReport(
            benchmark=benchmark,
            sample_size=0,
            observed_mean=0.0,
            benchmark_mean=0.0,
            mean_diff=0.0,
            p_value=1.0,
            ci_low=0.0,
            ci_high=0.0,
        )

    observed = [item.forward_3bar_return for item in trades]
    baseline = [item.benchmark_return for item in trades]

    observed_mean = mean(observed)
    benchmark_mean = mean(baseline)
    observed_diff = observed_mean - benchmark_mean

    rng = random.Random(random_seed)
    n = len(observed)
    diffs: list[float] = []
    for _ in range(bootstrap_rounds):
        sample_a = [observed[rng.randrange(0, n)] for _ in range(n)]
        sample_b = [baseline[rng.randrange(0, n)] for _ in range(n)]
        diffs.append(mean(sample_a) - mean(sample_b))

    diffs_sorted = sorted(diffs)
    ci_low = _percentile(diffs_sorted, 0.025)
    ci_high = _percentile(diffs_sorted, 0.975)
    p_value = sum(1 for value in diffs if value <= 0.0) / len(diffs)

    return SignificanceReport(
        benchmark=benchmark,
        sample_size=n,
        observed_mean=observed_mean,
        benchmark_mean=benchmark_mean,
        mean_diff=observed_diff,
        p_value=p_value,
        ci_low=ci_low,
        ci_high=ci_high,
    )
