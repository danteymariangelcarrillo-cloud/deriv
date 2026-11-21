"""Tests del token bucket (T005, INV-M06)."""

from __future__ import annotations

import asyncio
import time
from dataclasses import FrozenInstanceError

import pytest

from quantlab.data.rate_limiter import RateLimitState, TokenBucket


def test_rejects_invalid_construction() -> None:
    with pytest.raises(ValueError):
        TokenBucket(capacity=0, refill_per_second=2.5)
    with pytest.raises(ValueError):
        TokenBucket(capacity=30, refill_per_second=0.0)


def test_rejects_invalid_token_requests() -> None:
    bucket = TokenBucket(capacity=2, refill_per_second=1.0)

    with pytest.raises(ValueError):
        bucket.try_acquire(0)
    with pytest.raises(ValueError):
        bucket.try_acquire(3)


async def test_acquire_rejects_invalid_token_requests() -> None:
    bucket = TokenBucket(capacity=2, refill_per_second=1.0)

    with pytest.raises(ValueError):
        await bucket.acquire(0)
    with pytest.raises(ValueError):
        await bucket.acquire(3)


def test_burst_is_available_then_next_call_is_rejected() -> None:
    bucket = TokenBucket(capacity=3, refill_per_second=1.0)

    assert [bucket.try_acquire() for _ in range(3)] == [True, True, True]
    assert bucket.try_acquire() is False
    assert bucket.state.calls_last_minute == 3


def test_state_reports_contract_fields() -> None:
    bucket = TokenBucket(capacity=30, refill_per_second=2.5)

    state = bucket.state

    assert isinstance(state, RateLimitState)
    assert state.capacity == 30
    assert state.refill_per_second == 2.5
    assert state.budget_per_minute == 150
    assert state.tokens == pytest.approx(30.0, abs=0.01)
    assert state.calls_last_minute == 0
    assert state.waited_ms_last_request == 0


def test_available_tokens_refills_over_time() -> None:
    bucket = TokenBucket(capacity=10, refill_per_second=50.0)
    bucket.try_acquire(5)

    time.sleep(0.05)

    assert bucket.available_tokens > 5.0


def test_state_snapshot_is_immutable() -> None:
    state = TokenBucket(capacity=2, refill_per_second=1.0).state

    with pytest.raises(FrozenInstanceError):
        state.tokens = 99.0  # type: ignore[misc]


async def test_acquire_waits_for_refill_and_reports_wait() -> None:
    bucket = TokenBucket(capacity=1, refill_per_second=10.0)
    await bucket.acquire()

    started = time.monotonic()
    await bucket.acquire()
    elapsed = time.monotonic() - started

    assert elapsed >= 0.05  # ~0.1 s esperados
    assert bucket.state.waited_ms_last_request >= 50


async def test_default_budget_covers_30_burst_then_limits() -> None:
    bucket = TokenBucket(capacity=30, refill_per_second=2.5)

    for _ in range(30):
        assert bucket.try_acquire() is True
    assert bucket.try_acquire() is False

    started = time.monotonic()
    await bucket.acquire()
    elapsed = time.monotonic() - started

    assert elapsed >= 0.3  # un token a 2.5/s tarda 0.4 s
    assert bucket.state.waited_ms_last_request >= 300
    assert bucket.state.calls_last_minute == 31


async def test_concurrent_acquires_never_exceed_capacity() -> None:
    bucket = TokenBucket(capacity=2, refill_per_second=20.0)

    started = time.monotonic()
    await asyncio.gather(*(bucket.acquire() for _ in range(4)))
    elapsed = time.monotonic() - started

    # 2 tokens iniciales + 2 más a 20/s => ~0.1 s mínimos.
    assert elapsed >= 0.05
    assert bucket.state.calls_last_minute == 4
