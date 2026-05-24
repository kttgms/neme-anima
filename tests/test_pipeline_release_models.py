"""_release_pipeline_models drops the tagger and flushes the CUDA pool."""

from __future__ import annotations

from neme_anima.pipeline import _release_pipeline_models


def test_release_pipeline_models_closes_tagger_and_flushes(monkeypatch):
    closed = []
    flushed = []

    class FakeTagger:
        def close(self):
            closed.append(True)

    monkeypatch.setattr(
        "neme_anima.pipeline._flush_cuda_cache", lambda: flushed.append(True),
    )

    _release_pipeline_models(FakeTagger())  # type: ignore[arg-type]
    assert closed == [True]
    assert flushed == [True]


def test_release_pipeline_models_tolerates_none_tagger(monkeypatch):
    flushed = []
    monkeypatch.setattr(
        "neme_anima.pipeline._flush_cuda_cache", lambda: flushed.append(True),
    )
    _release_pipeline_models(None)
    assert flushed == [True]


def test_release_pipeline_models_tolerates_close_failure(monkeypatch):
    flushed = []

    class BrokenTagger:
        def close(self):
            raise RuntimeError("imgutils internals moved")

    monkeypatch.setattr(
        "neme_anima.pipeline._flush_cuda_cache", lambda: flushed.append(True),
    )
    _release_pipeline_models(BrokenTagger())  # type: ignore[arg-type]
    assert flushed == [True], "must still flush even when close() raises"
