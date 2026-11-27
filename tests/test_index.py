import pytest

from vrag.cli import main
from vrag.config import ROOT
from vrag.index import PassageRef, _collection, embed, index_corpus, search
from vrag.ingest import ingest_folder
from vrag.store import record_publication, save_corpus

SAMPLES = ROOT / "data" / "samples"
MIN_SIMILARITY = 0.35  # default from the design


@pytest.fixture
def out_dir(tmp_path):
    """An out/ folder with the public and private sample corpora saved and 'published'."""
    public = ingest_folder(SAMPLES, "pub", "Public", "v1", pattern="public-*")
    private = ingest_folder(SAMPLES, "priv", "Private", "v1", private=True, pattern="private-*")
    for corpus, corpus_id in ((public, 0), (private, 1)):
        save_corpus(corpus, tmp_path)
        record_publication(corpus.slug, corpus_id, "0xabc", 31337, tmp_path)
    return tmp_path


def test_embed_returns_384_dimensional_vectors():
    vectors = embed(["hello", "world"])
    assert len(vectors) == 2 and all(len(v) == 384 for v in vectors)


def test_index_requires_a_published_corpus(tmp_path):
    save_corpus(ingest_folder(SAMPLES, "pub", "Public", "v1", pattern="public-*"), tmp_path)
    with pytest.raises(ValueError, match="not published"):
        index_corpus("pub", tmp_path)


def test_search_on_empty_index_returns_nothing(tmp_path):
    assert search("anything", 5, True, tmp_path) == []


def test_public_search_finds_the_right_passage(out_dir):
    assert index_corpus("pub", out_dir) == 6
    hits = search("What is a Merkle proof?", 3, False, out_dir)
    ref, score = hits[0]
    assert ref.doc_id == "public-merkle-trees" and score >= MIN_SIMILARITY
    assert ref.passage_id.startswith("pub:public-merkle-trees:p1:")
    assert [s for _, s in hits] == sorted((s for _, s in hits), reverse=True)


def test_irrelevant_question_scores_below_the_threshold(out_dir):
    index_corpus("pub", out_dir)
    assert all(score < MIN_SIMILARITY for _, score in search("What is the capital of France?", 5, False, out_dir))


def test_unauthorized_search_never_returns_private_passages(out_dir):
    index_corpus("pub", out_dir)
    index_corpus("priv", out_dir)
    for question in [
        "How much is allocated to the Riverside Footbridge?",
        "Who is the shortlisted contractor?",
        "contingency reserve approval",
        "What is a Merkle proof?",
    ]:
        hits = search(question, 10, False, out_dir)
        assert hits and not any(ref.private or ref.slug == "priv" for ref, _ in hits), question


def test_authorized_search_includes_private_passages(out_dir):
    index_corpus("pub", out_dir)
    index_corpus("priv", out_dir)
    ref, score = search("How much is allocated to the Riverside Footbridge?", 3, True, out_dir)[0]
    assert ref == PassageRef("priv", "private-synthetic-memo", 1, 1, True)
    assert score >= MIN_SIMILARITY


def test_reindexing_replaces_instead_of_duplicating(out_dir):
    index_corpus("pub", out_dir)
    index_corpus("pub", out_dir)
    assert _collection(out_dir).count() == 6


def test_chroma_stores_no_passage_text(out_dir):
    index_corpus("priv", out_dir)
    stored = _collection(out_dir).get(include=["documents", "metadatas"])
    assert all(d is None for d in (stored["documents"] or [None]))
    assert "Harbourlight" not in str(stored)


def test_k_larger_than_index_is_fine(out_dir):
    index_corpus("pub", out_dir)
    assert len(search("Merkle", 50, False, out_dir)) == 6


def test_index_cli(out_dir, capsys):
    assert main(["index", "--slug", "pub", "--out-dir", str(out_dir)]) == 0
    assert "Indexed 6 passages" in capsys.readouterr().out
    assert main(["index", "--slug", "missing", "--out-dir", str(out_dir)]) == 1
