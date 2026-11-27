import json
import sqlite3
from pathlib import Path

import pymupdf
import pytest

from vrag import merkle
from vrag.cli import main
from vrag.config import ROOT
from vrag.ingest import MAX_CHARS, MIN_CHARS, extract_pages, ingest_folder, split_passages
from vrag.store import get_passage, load_corpus, save_corpus
from vrag.text import ZERO32, content_hash, normalize

SAMPLES = ROOT / "data" / "samples"


# --- splitting ---------------------------------------------------------------

def test_split_merges_short_paragraphs():
    text = "Short one.\n\nShort two.\n\nShort three."
    assert split_passages(text) == ["Short one. Short two. Short three."]


def test_split_keeps_paragraphs_in_range_separate():
    a, b = "a" * 500, "b" * 500
    assert split_passages(f"{a}\n\n{b}") == [a, b]


def test_split_long_paragraph_on_sentences():
    sentence = "This is a sentence of moderate length that ends here."
    text = " ".join([sentence] * 60)  # about 3,200 characters
    parts = split_passages(text)
    assert len(parts) >= 3
    assert all(len(p) <= MAX_CHARS for p in parts)
    assert all(p.endswith(".") for p in parts)
    assert " ".join(parts) == text


def test_split_one_huge_sentence_without_punctuation():
    text = " ".join(["word"] * 1000)
    parts = split_passages(text)
    assert all(len(p) <= MAX_CHARS for p in parts)
    assert " ".join(parts) == text


def test_split_ignores_blank_text_and_normalizes():
    assert split_passages(" \n\n \t ") == []
    assert split_passages("a  b\n c") == ["a b c"]


def test_sample_passages_are_in_target_range():
    for f in SAMPLES.glob("public-*.txt"):
        for p in split_passages(extract_pages(f)[0]):
            assert len(p) <= MAX_CHARS
    # at least one passage reaches the minimum
    assert any(len(p) >= MIN_CHARS for f in SAMPLES.glob("public-*.txt") for p in split_passages(extract_pages(f)[0]))


# --- extraction --------------------------------------------------------------

def make_pdf(path: Path, pages: list[str]) -> None:
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()


def test_extract_pdf_pages(tmp_path):
    pdf = tmp_path / "doc.pdf"
    make_pdf(pdf, ["First page text", "Second page text"])
    pages = extract_pages(pdf)
    assert len(pages) == 2
    assert "First page" in pages[0] and "Second page" in pages[1]


def test_extract_txt_is_single_page_and_strips_bom(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"\xef\xbb\xbfhello")
    assert extract_pages(f) == ["hello"]


# --- ingest ------------------------------------------------------------------

def test_ingest_ids_order_and_hashes(tmp_path):
    (tmp_path / "b.txt").write_text("Beta text.", encoding="utf-8")
    (tmp_path / "a.txt").write_text("Alpha text.", encoding="utf-8")
    corpus = ingest_folder(tmp_path, "demo", "Demo", "v1")
    assert [(p.doc_id, p.page, p.index) for p in corpus.passages] == [("a", 1, 0), ("b", 1, 0)]
    assert [p.leaf_index for p in corpus.passages] == [0, 1]
    p = corpus.passages[0]
    assert p.passage_id == "demo:a:p1:0"
    assert p.text == normalize("Alpha text.")
    assert p.content_hash == content_hash("Alpha text.")
    assert corpus.levels[0][0] == merkle.leaf_hash("a", 1, 0, p.content_hash)


def test_ingest_pdf_pages_are_one_based(tmp_path):
    make_pdf(tmp_path / "report.pdf", ["Page one content.", "Page two content."])
    corpus = ingest_folder(tmp_path, "r", "R", "v1")
    assert [(p.doc_id, p.page, p.index) for p in corpus.passages] == [("report", 1, 0), ("report", 2, 0)]


def test_ingest_skips_bad_and_empty_files(tmp_path):
    (tmp_path / "good.txt").write_text("Real content.", encoding="utf-8")
    (tmp_path / "empty.txt").write_text("  \n ", encoding="utf-8")
    (tmp_path / "broken.pdf").write_bytes(b"not a pdf")
    (tmp_path / "notes.docx").write_bytes(b"ignored")
    corpus = ingest_folder(tmp_path, "s", "S", "v1")
    assert [p.doc_id for p in corpus.passages] == ["good"]
    assert {name for name, _ in corpus.skipped} == {"empty.txt", "broken.pdf"}


def test_ingest_with_nothing_usable_is_an_error(tmp_path):
    (tmp_path / "empty.txt").write_text("", encoding="utf-8")
    with pytest.raises(ValueError):
        ingest_folder(tmp_path, "s", "S", "v1")


def test_ingest_rejects_duplicate_stems_and_bad_slug(tmp_path):
    (tmp_path / "x.txt").write_text("one", encoding="utf-8")
    make_pdf(tmp_path / "x.pdf", ["two"])
    with pytest.raises(ValueError):
        ingest_folder(tmp_path, "s", "S", "v1")
    with pytest.raises(ValueError):
        ingest_folder(tmp_path, "../evil", "S", "v1")


def test_ingesting_samples_twice_gives_same_root(tmp_path):
    first = ingest_folder(SAMPLES, "samples", "Samples", "v1")
    second = ingest_folder(SAMPLES, "samples", "Samples", "v1")
    assert first.root == second.root
    assert [p.passage_id for p in first.passages] == [p.passage_id for p in second.passages]
    assert [p.text for p in first.passages] == [p.text for p in second.passages]


def test_cli_ingest_twice_gives_same_root(tmp_path, capsys):
    argv = ["ingest", str(SAMPLES), "--slug", "samples", "--name", "Samples", "--version", "v1",
            "--out-dir", str(tmp_path)]
    assert main(argv) == 0
    root1 = load_corpus("samples", tmp_path).root
    assert main(argv) == 0
    assert load_corpus("samples", tmp_path).root == root1
    assert f"0x{root1.hex()}" in capsys.readouterr().out


# --- store -------------------------------------------------------------------

def test_public_corpus_round_trip(tmp_path):
    corpus = ingest_folder(SAMPLES, "samples", "Samples", "v1")
    save_corpus(corpus, tmp_path)
    loaded = load_corpus("samples", tmp_path)
    assert loaded.root == corpus.root
    assert loaded.levels == corpus.levels
    assert [p.text for p in loaded.passages] == [p.text for p in corpus.passages]
    assert not (tmp_path / "private.db").exists()


def test_private_corpus_keeps_text_and_salts_out_of_public_file(tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    secret = "Fictional secret budget figure 987,654 units."
    (folder / "memo.txt").write_text(secret, encoding="utf-8")
    out = tmp_path / "out"
    corpus = ingest_folder(folder, "memo", "Memo", "v1", private=True)
    path = save_corpus(corpus, out)

    raw = path.read_text(encoding="utf-8")
    assert "987,654" not in raw
    assert "salt" not in raw.lower()
    assert "text" not in json.loads(raw)["passages"][0]
    salt = corpus.passages[0].salt
    assert salt != ZERO32 and salt.hex() not in raw

    row = sqlite3.connect(out / "private.db").execute("SELECT text, salt FROM passages").fetchone()
    assert row[0] == normalize(secret) and bytes(row[1]) == salt


def test_get_passage_hides_private_text_from_unauthorized(tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "memo.txt").write_text("Fictional private text.", encoding="utf-8")
    out = tmp_path / "out"
    corpus = ingest_folder(folder, "memo", "Memo", "v1", private=True)
    save_corpus(corpus, out)

    hidden = get_passage("memo", "memo", 1, 0, authorized=False, out_dir=out)
    assert hidden is not None and hidden.text is None and hidden.salt is None
    assert hidden.content_hash == corpus.passages[0].content_hash

    shown = get_passage("memo", "memo", 1, 0, authorized=True, out_dir=out)
    assert shown.text == "Fictional private text." and shown.salt == corpus.passages[0].salt
    # the stored salt and text reproduce the stored hash
    assert content_hash(shown.text, shown.salt) == shown.content_hash

    assert get_passage("memo", "memo", 9, 9, authorized=True, out_dir=out) is None


def test_get_passage_public_ignores_authorization(tmp_path):
    corpus = ingest_folder(SAMPLES, "samples", "Samples", "v1")
    save_corpus(corpus, tmp_path)
    p0 = corpus.passages[0]
    got = get_passage("samples", p0.doc_id, p0.page, p0.index, authorized=False, out_dir=tmp_path)
    assert got.text == p0.text


def test_reingesting_private_corpus_replaces_rows(tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "memo.txt").write_text("Fictional private text.", encoding="utf-8")
    out = tmp_path / "out"
    for _ in range(2):
        save_corpus(ingest_folder(folder, "memo", "Memo", "v1", private=True), out)
    count = sqlite3.connect(out / "private.db").execute("SELECT COUNT(*) FROM passages").fetchone()[0]
    assert count == 1


def test_load_missing_corpus(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_corpus("nope", tmp_path)
