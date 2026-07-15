"""Concurrent validation must isolate every interpolation context."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import nshconfig as C


class ThreadLeaf(C.Config):
    derived: int


class ThreadConfig(C.Config):
    source: int
    leaf: ThreadLeaf


def test_interpolation_context_is_isolated_between_threads():
    worker_count = 8
    rounds = 16
    resolver_barrier = Barrier(worker_count)

    def worker(
        index: int,
    ) -> list[int]:
        results: list[int] = []
        for round_index in range(rounds):
            work = ThreadConfig.config_draft()
            source_value = index * 1_000 + round_index

            def resolve(context: C.Context) -> int:
                resolver_barrier.wait(timeout=20)
                source = context.root(ThreadConfig).source
                resolver_barrier.wait(timeout=20)
                return source * 10 + index

            work.source = source_value
            work.leaf.derived = C.interp(resolve)
            final = work.config_finalize()
            results.append(final.leaf.derived)

        # ThreadPoolExecutor reuses the same worker context for every round. This
        # finalization proves interpolation stack tokens were reset in the
        # originating thread, not merely isolated from other threads.
        cleanup = ThreadConfig.config_draft()
        cleanup.source = index
        cleanup.leaf.derived = C.interp(
            lambda context: context.parent(ThreadConfig).source
        )
        cleanup_final = cleanup.config_finalize()
        assert cleanup_final.leaf.derived == index
        return results

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        results = list(pool.map(worker, range(worker_count)))

    for index, worker_results in enumerate(results):
        for round_index, derived in enumerate(worker_results):
            source_value = index * 1_000 + round_index
            assert derived == source_value * 10 + index
