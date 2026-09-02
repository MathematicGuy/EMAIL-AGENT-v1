from __future__ import annotations

import pytest

from cowork_agent.features.batch_evaluation.planning import DataSharder, resolve_worker_count


def test_worker_resolution_logic_and_warnings() -> None:
    # Under requested
    res1 = resolve_worker_count(requested=2, healthy_credentials=3, ready_work=10)
    assert res1.effective_workers == 2 and res1.warning is None

    # Limited by credentials -> warns
    res2 = resolve_worker_count(requested=4, healthy_credentials=3, ready_work=10)
    assert res2.effective_workers == 3
    assert res2.warning is not None and res2.warning.code == "WORKER_COUNT_REDUCED"

    # Limited by ready work -> does not warn
    res3 = resolve_worker_count(requested=4, healthy_credentials=5, ready_work=2)
    assert res3.effective_workers == 2 and res3.warning is None

    # Non-positive requested workers rejected
    with pytest.raises(ValueError, match="requested_workers"):
        resolve_worker_count(0, healthy_credentials=1, ready_work=1)


def test_data_sharder_partitioning_and_ordinals() -> None:
    shards = DataSharder().partition(tuple("abcdefg"), 3)
    assert [[item.ordinal for item in shard] for shard in shards] == [[0, 3, 6], [1, 4], [2, 5]]

    empty_omitted = DataSharder().partition(tuple("ab"), 3)
    assert [[item.value for item in shard] for shard in empty_omitted] == [["a"], ["b"]]

    with pytest.raises(ValueError, match="shard_count"):
        DataSharder().partition(("a",), 0)
