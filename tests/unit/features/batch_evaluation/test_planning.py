from __future__ import annotations

import pytest

from cowork_agent.features.batch_evaluation.planning import DataSharder, resolve_worker_count


@pytest.mark.parametrize(
    ("requested", "healthy", "ready", "effective", "warns"),
    [(1, 3, 10, 1, False), (2, 3, 10, 2, False), (4, 3, 10, 3, True), (4, 5, 2, 2, False)],
)
def test_worker_resolution(
    requested: int, healthy: int, ready: int, effective: int, warns: bool
) -> None:
    result = resolve_worker_count(requested, healthy, ready)

    assert result.effective_workers == effective
    assert (result.warning is not None) is warns


def test_worker_resolution_warns_only_when_credentials_limit_requested_workers() -> None:
    result = resolve_worker_count(requested=4, healthy_credentials=3, ready_work=10)

    assert result.warning is not None
    assert result.warning.code == "WORKER_COUNT_REDUCED"
    assert result.warning.details == {
        "requested_workers": 4,
        "effective_workers": 3,
        "healthy_credentials": 3,
    }


@pytest.mark.parametrize("requested", [0, -1])
def test_worker_resolution_rejects_non_positive_requested_workers(requested: int) -> None:
    with pytest.raises(ValueError, match="requested_workers"):
        resolve_worker_count(requested, healthy_credentials=1, ready_work=1)


def test_round_robin_shards_preserve_original_ordinals() -> None:
    shards = DataSharder().partition(tuple("abcdefg"), 3)

    assert [[item.ordinal for item in shard] for shard in shards] == [[0, 3, 6], [1, 4], [2, 5]]


def test_sharder_omits_empty_shards() -> None:
    shards = DataSharder().partition(tuple("ab"), 3)

    assert [[item.value for item in shard] for shard in shards] == [["a"], ["b"]]


@pytest.mark.parametrize("shard_count", [0, -1])
def test_sharder_rejects_non_positive_shard_counts(shard_count: int) -> None:
    with pytest.raises(ValueError, match="shard_count"):
        DataSharder().partition(("a",), shard_count)
