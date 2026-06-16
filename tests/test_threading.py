"""Free-threading posture: all draft and resolution state is instance/context-local."""

import threading

import nshconfig as C
from tests.scenario import TrainConfig


def test_concurrent_drafts_are_isolated():
    n = 8
    barrier = threading.Barrier(n)
    results: dict[int, tuple[int, int]] = {}
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            barrier.wait()
            cfg = TrainConfig.config_draft()
            cfg.model.dim = 100 + i
            cfg.model.encoder.ln.dim = C.interp(lambda c, i=i: c.root().model.dim + i)
            for _ in range(50):  # interleave writes across threads
                cfg.batch = i
            f = C.finalize(cfg)
            results[i] = (f.model.encoder.ln.dim, f.model.head.dim)
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    for i in range(n):
        assert results[i] == (100 + 2 * i, 100 + i)


def test_concurrent_finalize_of_shared_classes():
    # Same CLASSES, distinct trees, all finalizing at once: the ContextVar stack and
    # the handler-cache-free draft writes must never bleed across threads.
    n = 8
    barrier = threading.Barrier(n)
    out: dict[int, int] = {}
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            barrier.wait()
            for _ in range(25):
                cfg = TrainConfig.config_draft()
                cfg.model.dim = i * 1000
                out[i] = C.finalize(cfg).model.head.dim
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert all(out[i] == i * 1000 for i in range(n))
