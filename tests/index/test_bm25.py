import numpy as np

from scis_help.index.bm25 import Bm25Index, tokenize


def test_tokenize_lowercases_and_drops_stopwords():
    assert tokenize("The VPN is DOWN") == ["vpn", "down"]


def test_tokenize_keeps_joined_forms_and_their_parts():
    tokens = tokenize("Map CCI-REPO via vpn.example.test")
    assert "cci-repo" in tokens and "cci" in tokens and "repo" in tokens
    assert "vpn.example.test" in tokens and "example" in tokens


def test_course_codes_match_with_or_without_a_space():
    assert "cs265" in tokenize("CS265")
    assert "cs265" in tokenize("CS 265")
    assert {"cs", "265"} <= set(tokenize("CS265"))


def test_search_masks_and_drops_zero_scores(tmp_path):
    index = Bm25Index.build(["vpn gateway help", "printer room", "exrepo shared drive"])
    assert [doc for doc, _ in index.search("exrepo", k=5)] == [2]
    assert index.search("exrepo", k=5, mask=np.array([1, 1, 0])) == []
    assert index.search("nothing matches", k=5) == []
    index.save(tmp_path / "bm25")
    loaded = Bm25Index.load(tmp_path / "bm25", size=3)
    assert [doc for doc, _ in loaded.search("printer", k=10)] == [1]
