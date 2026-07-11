"""Concurrent validation must isolate every context-local lifecycle value."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import nshconfig as C


class ThreadLeaf(C.Config):
    derived: int


class ThreadConfig(C.Config):
    source: int
    leaf: ThreadLeaf


def test_interpolation_reads_and_source_labels_are_isolated_between_threads():
    worker_count = 8
    rounds = 16
    resolver_barrier = Barrier(worker_count)

    def worker(
        index: int,
    ) -> list[tuple[int, tuple[tuple[str, str], ...], set[str | None]]]:
        results = []
        for round_index in range(rounds):
            work = C.draft(ThreadConfig)
            label = f"worker-{index}-round-{round_index}"
            source_value = index * 1_000 + round_index

            def resolve(context: C.Context) -> int:
                resolver_barrier.wait(timeout=20)
                source = context.root(ThreadConfig).source
                resolver_barrier.wait(timeout=20)
                return source * 10 + index

            with C.source(label):
                work.source = source_value
                work.leaf.derived = C.interp(resolve)
                final = C.finalize(work)

            events = C.provenance(final)
            interpolation = events["leaf.derived"][-1]
            labels = {
                event.label for event in (*events["source"], *events["leaf.derived"])
            }
            results.append((final.leaf.derived, interpolation.reads, labels))

        # ThreadPoolExecutor reuses the same worker context for every round. This
        # finalization runs after all labelled scopes and proves their tokens were
        # reset in the originating thread, not merely isolated from other threads.
        cleanup = C.draft(ThreadConfig)
        cleanup.source = index
        cleanup.leaf.derived = C.interp(
            lambda context: context.parent(ThreadConfig).source
        )
        cleanup_final = C.finalize(cleanup)
        assert {
            event.label
            for events in C.provenance(cleanup_final).values()
            for event in events
        } == {None}
        return results

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        results = list(pool.map(worker, range(worker_count)))

    for index, worker_results in enumerate(results):
        for round_index, (derived, reads, labels) in enumerate(worker_results):
            source_value = index * 1_000 + round_index
            assert derived == source_value * 10 + index
            assert reads == (("source", str(source_value)),)
            assert labels == {f"worker-{index}-round-{round_index}"}
