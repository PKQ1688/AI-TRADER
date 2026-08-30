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


def _moving_block_sample(
    values: list[float],
    block_size: int,
    rng: random.Random,
) -> list[float]:
    sample: list[float] = []
    size = len(values)
    while len(sample) < size:
        start = rng.randrange(0, size)
        sample.extend(
            values[(start + offset) % size] for offset in range(block_size)
        )
    return sample[:size]


def _block_sign_flip_mean(
    values: list[float],
    block_size: int,
    rng: random.Random,
) -> float:
    signed: list[float] = []
    for start in range(0, len(values), block_size):
        sign = 1.0 if rng.randrange(0, 2) else -1.0
        signed.extend(sign * item for item in values[start : start + block_size])
    return mean(signed)


def evaluate_paired_returns(
    observed: list[float],
    baseline: list[float],
    benchmark: str = "year_matched_random_3bar",
    bootstrap_rounds: int = 2000,
    random_seed: int = 7,
) -> SignificanceReport:
    if len(observed) != len(baseline):
        raise ValueError("observed and baseline returns must have equal length")
    if not observed:
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

    observed_mean = mean(observed)
    benchmark_mean = mean(baseline)
    paired_diffs = [left - right for left, right in zip(observed, baseline)]
    observed_diff = mean(paired_diffs)

    rng = random.Random(random_seed)
    n = len(paired_diffs)
    block_size = max(1, round(n**0.5))
    bootstrap_diffs: list[float] = []
    null_diffs: list[float] = []
    for _ in range(bootstrap_rounds):
        bootstrap_diffs.append(
            mean(_moving_block_sample(paired_diffs, block_size, rng))
        )
        null_diffs.append(
            _block_sign_flip_mean(paired_diffs, block_size, rng)
        )

    diffs_sorted = sorted(bootstrap_diffs)
    ci_low = _percentile(diffs_sorted, 0.025)
    ci_high = _percentile(diffs_sorted, 0.975)
    # One-sided H1: the Chan signal return exceeds its matched random return.
    # The +1 correction prevents a Monte Carlo estimate of exactly zero.
    p_value = (
        1 + sum(1 for value in null_diffs if value >= observed_diff)
    ) / (len(null_diffs) + 1)

    return SignificanceReport(
        benchmark=benchmark,
        sample_size=n,
        observed_mean=observed_mean,
        benchmark_mean=benchmark_mean,
        mean_diff=observed_diff,
        p_value=p_value,
        ci_low=ci_low,
        ci_high=ci_high,
        block_size=block_size,
    )


def evaluate_significance(
    trades: list[Trade],
    benchmark: str = "year_matched_random_3bar",
    bootstrap_rounds: int = 2000,
    random_seed: int = 7,
) -> SignificanceReport:
    return evaluate_paired_returns(
        observed=[item.forward_3bar_return for item in trades],
        baseline=[item.benchmark_return for item in trades],
        benchmark=benchmark,
        bootstrap_rounds=bootstrap_rounds,
        random_seed=random_seed,
    )
