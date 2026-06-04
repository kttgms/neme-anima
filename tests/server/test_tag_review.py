"""Unit tests for the LLM tag-review building blocks.

Covers the deterministic pieces that don't need a live model: the danbooru
search/canonicalize index and the reconciliation that turns a model's raw,
possibly-sloppy verdict into a trustworthy applyable diff.
"""

from __future__ import annotations

from pathlib import Path

from neme_anima.llm import _extract_json_object
from neme_anima.server.api.frames import _parse_tag_line, _reconcile_review
from neme_anima.tag_vocabulary import DanbooruIndex, load_index

# A tiny stand-in for the real 200k-row CSV. Note `yellow_hair` is only an
# *alias* of the canonical `blonde_hair`, mirroring the real vocabulary.
SAMPLE_CSV = (
    'blonde_hair,0,1981860,"gold_hair,yellow_hair,blond"\n'
    "angel_wings,0,52425,\n"
    "long_hair,0,5624146,\n"
    'halo,0,414828,""\n'
    "tongue_out,0,300000,\n"
)


def _index(tmp_path: Path) -> DanbooruIndex:
    csv = tmp_path / "danbooru-tags.csv"
    csv.write_text(SAMPLE_CSV, encoding="utf-8")
    return DanbooruIndex.from_csv(csv)


def test_parse_tag_line_dedupes_and_trims():
    assert _parse_tag_line(" 1girl,  solo , 1girl ,, smile ") == ["1girl", "solo", "smile"]


def test_extract_json_object():
    # plain object
    assert _extract_json_object('{"keep": ["a"]}') == {"keep": ["a"]}
    # fenced + surrounding prose
    got = _extract_json_object('Here is my review:\n```json\n{"add": []}\n```\nDone.')
    assert got == {"add": []}
    # a leading <think> block is stripped before parsing
    assert _extract_json_object('<think>let me see {not json}</think>\n{"remove": []}') == {
        "remove": []
    }
    # nothing parseable
    assert _extract_json_object("no json here at all") is None
    assert _extract_json_object("") is None


def test_index_returns_space_form_names(tmp_path: Path):
    idx = _index(tmp_path)
    hits = idx.search("angel")
    assert hits[0]["tag"] == "angel wings"  # underscores -> spaces
    assert hits[0]["category"] == "general"


def test_index_search_ranked_by_post_count(tmp_path: Path):
    idx = _index(tmp_path)
    # both "long hair" and ... only one matches "hair"? blonde_hair + long_hair
    hits = idx.search("hair")
    names = [h["tag"] for h in hits]
    assert names[0] == "long hair"  # 5.6M > blonde's 1.9M
    assert "blonde hair" in names


def test_canonicalize_resolves_alias_and_underscores(tmp_path: Path):
    idx = _index(tmp_path)
    # alias -> canonical, space form
    assert idx.canonicalize("yellow_hair") == ("blonde hair", True)
    assert idx.canonicalize("yellow hair") == ("blonde hair", True)
    # already-canonical, different case/spacing
    assert idx.canonicalize("Blonde Hair") == ("blonde hair", True)
    # not a real tag
    assert idx.canonicalize("definitely not a tag") == ("definitely not a tag", False)


def test_load_index_caches_until_file_changes(tmp_path: Path):
    csv = tmp_path / "danbooru-tags.csv"
    csv.write_text(SAMPLE_CSV, encoding="utf-8")
    a = load_index(csv)
    b = load_index(csv)
    assert a is b  # same mtime/size -> cached instance


def test_reconcile_drops_hallucinated_and_canonicalizes(tmp_path: Path):
    idx = _index(tmp_path)
    existing = ["1girl", "blonde hair", "angel wings"]
    raw = {
        "keep": ["1girl"],  # model's keep is ignored; we derive it
        "remove": [
            {"tag": "angel wings", "reason": "not visible"},
            {"tag": "not present", "reason": "bogus"},  # not in existing -> ignored
        ],
        "add": [
            {"tag": "yellow_hair", "reason": "hair color"},   # alias of existing -> dropped
            {"tag": "halo", "reason": "ring above head"},      # real, new -> kept
            {"tag": "xyzzy", "reason": "made up"},             # not real -> dropped
        ],
    }
    out = _reconcile_review(raw, existing, idx)

    # keep is derived: existing minus what we actually removed.
    assert out["keep"] == ["1girl", "blonde hair"]
    assert [r["tag"] for r in out["remove"]] == ["angel wings"]
    assert [a["tag"] for a in out["add"]] == ["halo"]
    assert out["proposed_final"] == ["1girl", "blonde hair", "halo"]
    # transparency notes explain every drop/normalization
    joined = " | ".join(out["notes"])
    assert "not present" in joined          # ignored removal
    assert "already present" in joined      # yellow_hair -> blonde hair already there
    assert "not a danbooru tag" in joined   # xyzzy dropped


def test_reconcile_canonicalizes_a_genuinely_new_alias(tmp_path: Path):
    idx = _index(tmp_path)
    existing = ["1girl"]
    raw = {"keep": [], "remove": [], "add": [{"tag": "yellow_hair", "reason": "blonde"}]}
    out = _reconcile_review(raw, existing, idx)
    # yellow_hair isn't present, so it survives — but canonicalized to blonde hair.
    assert [a["tag"] for a in out["add"]] == ["blonde hair"]
    assert any("normalized to 'blonde hair'" in n for n in out["notes"])
