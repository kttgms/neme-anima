"""``_release_pipeline_models`` clears the imgutils GPU caches and flushes CUDA.

The pipeline's GPU footprint is the WD14 tagger session plus YOLO (person/face)
plus CCIP (identify + dedup). Each of those is lazy-loaded into a module-level
``ts_lru_cache`` inside ``imgutils`` and pinned for the process lifetime —
the ONLY way to release the underlying ``InferenceSession`` is to call
``cache_clear()`` on the cached factory, then run gc so the C++ destructor
fires. These tests stub the imgutils modules and assert we hit each one.
"""

from __future__ import annotations

import sys
import types

from neme_anima.pipeline import _IMGUTILS_LRU_CACHES, _release_pipeline_models


def _install_fake_modules(monkeypatch) -> dict[tuple[str, str], list[int]]:
    """Replace each imgutils submodule in sys.modules with a stand-in whose
    cached factories track invocation count. Returns a dict keyed by
    (module, function name) with a list that gets ``+1`` on each
    cache_clear call so tests can assert exact call counts."""
    counters: dict[tuple[str, str], list[int]] = {}
    for module_path, fn_names in _IMGUTILS_LRU_CACHES:
        fake = types.ModuleType(module_path)
        for fn_name in fn_names:
            counter: list[int] = []
            counters[(module_path, fn_name)] = counter

            class _Cached:
                def __init__(self, c: list[int]) -> None:
                    self._c = c

                def __call__(self, *a, **kw):  # pragma: no cover - not called
                    return None

                def cache_clear(self) -> None:
                    self._c.append(1)

            setattr(fake, fn_name, _Cached(counter))
        monkeypatch.setitem(sys.modules, module_path, fake)
    return counters


def test_release_clears_every_known_imgutils_cache(monkeypatch):
    counters = _install_fake_modules(monkeypatch)
    flushed: list[int] = []
    monkeypatch.setattr(
        "neme_anima.pipeline._flush_cuda_cache", lambda: flushed.append(1),
    )

    _release_pipeline_models()

    # Every cache listed in the registry must have been cleared exactly once.
    for key, calls in counters.items():
        assert calls == [1], f"{key} cache_clear not called exactly once: {calls}"
    assert flushed == [1], "CUDA flush must run after cache_clear + gc"


def test_release_tolerates_missing_module(monkeypatch):
    """If a future imgutils release renames or removes one of the
    private cached factories, the pipeline must still finish releasing
    the others. The other two subsystems are independent — losing one
    must not block the rest."""
    # Stand-in the wd14 module but DELETE its cached fn so getattr returns None.
    fake_wd14 = types.ModuleType("imgutils.tagging.wd14")
    monkeypatch.setitem(sys.modules, "imgutils.tagging.wd14", fake_wd14)
    # Leave YOLO + CCIP with working stubs.
    yolo_counter: list[int] = []
    fake_yolo = types.ModuleType("imgutils.generic.yolo")

    class _Y:
        def cache_clear(self):
            yolo_counter.append(1)

    fake_yolo._open_models_for_repo_id = _Y()
    monkeypatch.setitem(sys.modules, "imgutils.generic.yolo", fake_yolo)
    fake_ccip = types.ModuleType("imgutils.metrics.ccip")
    monkeypatch.setitem(sys.modules, "imgutils.metrics.ccip", fake_ccip)

    flushed: list[int] = []
    monkeypatch.setattr(
        "neme_anima.pipeline._flush_cuda_cache", lambda: flushed.append(1),
    )

    _release_pipeline_models()

    assert yolo_counter == [1], "the cache that was present must still be cleared"
    assert flushed == [1], "missing private API must not skip the CUDA flush"


def test_release_tolerates_cache_clear_raising(monkeypatch):
    """A broken third-party cache_clear (e.g. imgutils internals changed
    signature) must not take down the pipeline's finally block."""
    fake_wd14 = types.ModuleType("imgutils.tagging.wd14")

    class _Broken:
        def cache_clear(self):
            raise RuntimeError("imgutils internals moved")

    fake_wd14._get_wd14_model = _Broken()
    monkeypatch.setitem(sys.modules, "imgutils.tagging.wd14", fake_wd14)
    monkeypatch.setitem(sys.modules, "imgutils.generic.yolo", types.ModuleType("y"))
    monkeypatch.setitem(sys.modules, "imgutils.metrics.ccip", types.ModuleType("c"))

    flushed: list[int] = []
    monkeypatch.setattr(
        "neme_anima.pipeline._flush_cuda_cache", lambda: flushed.append(1),
    )

    _release_pipeline_models()  # must not raise

    assert flushed == [1], "must still flush even when a cache_clear raises"
