"""Tests for the document loader (app/common/docs_loader.py).

Every test builds its own small docs folder in pytest's tmp_path. No secrets, no network.
"""
import ast
import hashlib
from io import BytesIO
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.common import docs_loader
from app.common.docs_loader import (
    DocSection, DocsLoaderError, clean_text, discover_files, ingest, load_docs,
    load_docs_with_report, normalise_doc_ref, validate_sections,
)

DOCSECTION_FIELDS = ["id", "doc_id", "title", "heading", "heading_path", "text", "version", "status",
                     "updated", "superseded_by", "format", "source_path"]
MD_FRONT = "---\ntitle: {title}\nversion: \"1.0\"\nstatus: current\nupdated: 2026-01-01\n---\n"


def write(folder: Path, rel: str, content, binary: bool = False) -> Path:
    path = folder / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if binary:
        path.write_bytes(content)
    else:
        path.write_bytes(content.encode("utf-8"))
    return path


def md(title: str, body: str) -> str:
    return MD_FRONT.format(title=title) + body


def blank_pdf_bytes() -> bytes:
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def tree_fingerprint(folder: Path) -> dict[str, tuple[str, int]]:
    return {p.relative_to(folder).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in sorted(folder.rglob("*")) if p.is_file()}


# 1. Recursive discovery
def test_recursive_discovery(tmp_path):
    write(tmp_path, "a.md", md("A", "# A\n\ntext"))
    write(tmp_path, "sub/b.md", md("B", "# B\n\ntext"))
    write(tmp_path, "sub/deeper/c.html", "<title>C</title><h1>C</h1><p>text</p>")
    files = discover_files(tmp_path)
    assert [f.doc_id for f in files] == ["a", "sub/b", "sub/deeper/c"]
    assert [f.rel_path for f in files] == ["a.md", "sub/b.md", "sub/deeper/c.html"]
    assert {s.doc_id for s in load_docs(tmp_path)} == {"a", "sub/b", "sub/deeper/c"}


# 2 + 26. Supported extension filtering; unsupported files reported, never loaded
def test_unsupported_files_are_reported_not_loaded(tmp_path):
    write(tmp_path, "good.md", md("Good", "# Good\n\ntext"))
    write(tmp_path, "notes.txt", "plain text")
    write(tmp_path, "img/logo.png", b"\x89PNG\r\n", binary=True)
    write(tmp_path, "UPPER.MD", md("Upper", "# Upper\n\ntext"))
    sections, report = load_docs_with_report(tmp_path)
    assert report.files_discovered == 4 and report.supported_files == 2
    assert report.unsupported_files == ["img/logo.png", "notes.txt"]
    assert {s.doc_id for s in sections} == {"good", "UPPER"}


# 3. Deterministic processing order
def test_processing_order_is_sorted_and_repeatable(tmp_path):
    for name in ("zeta.md", "alpha.md", "mid/beta.md"):
        write(tmp_path, name, md(name, f"# {name}\n\nbody of {name}"))
    first = [s.id for s in load_docs(tmp_path)]
    second = [s.id for s in load_docs(tmp_path)]
    assert first == second == ["doc:alpha#1", "doc:mid/beta#1", "doc:zeta#1"]


# 4. All 18 documents load
# 5. All expected document ids exist
# 6. Document ids match retrieval_gold.jsonl
# Data card reference values (measured, not forced: H2 sections + intro text, see module docstring)
# 7. Markdown metadata
# 8. HTML metadata and clean HTML text
def test_html_parsing_details(tmp_path):
    html = ("<html><head><title>T &amp; C</title><meta name='status' content='current'></head><body>"
            "<header>site header</header><h1>T &amp; C</h1><p>one</p><p>two</p>"
            "<h2>Codes &lt;4xx&gt;</h2><p>A&nbsp;B</p><pre>  keep   spacing</pre><h3>Sub</h3><p>deep</p>"
            "<script>alert(1)</script><aside>ad</aside></body></html>")
    write(tmp_path, "page.html", html)
    sections = load_docs(tmp_path)
    assert sections[0].title == "T & C" and sections[0].text == "one\ntwo"  # words not joined
    assert sections[1].heading == "Codes <4xx>"
    assert "A B" in sections[1].text and "keep   spacing" in sections[1].text
    assert "### Sub\ndeep" in sections[1].text  # nested heading stays in its parent section
    joined = " ".join(s.text for s in sections)
    assert "site header" not in joined and "alert" not in joined and " ad" not in joined


# 9. PDF text extraction
def test_pdf_without_text_is_reported_not_fabricated(tmp_path):
    write(tmp_path, "scan.pdf", blank_pdf_bytes(), binary=True)
    sections, report = load_docs_with_report(tmp_path)
    assert sections == []
    assert report.documents_skipped == [{"path": "scan.pdf", "reason": "PDF has no extractable text (scanned image or empty)"}]
    assert report.pdf_extraction[0]["status"] == "failed" and report.pdf_extraction[0]["empty_pages"] == [1]


# 10. Missing metadata
def test_missing_metadata_is_none_and_reported(tmp_path):
    write(tmp_path, "bare.md", "# Bare doc\n\nSome text.\n\n## Part\n\nMore.")
    sections, report = load_docs_with_report(tmp_path)
    s = sections[0]
    assert s.title == "Bare doc"  # taken from the document's own top heading (reported)
    assert (s.version, s.status, s.updated, s.superseded_by) == (None, None, None, None)
    assert report.missing_metadata == [{"doc_id": "bare", "fields": ["title", "version", "status", "updated"]}]
    assert {"path": "bare.md", "message": "title taken from the document's top heading"} in report.warnings


def test_document_without_any_title(tmp_path):
    write(tmp_path, "notitle.md", "Just a paragraph.")
    sections, report = load_docs_with_report(tmp_path)
    assert sections[0].title is None and sections[0].heading == "notitle"  # falls back to the doc id
    assert report.missing_metadata[0]["fields"][0] == "title"


# 11. Invalid metadata
def test_invalid_metadata_is_reported_and_not_replaced(tmp_path):
    write(tmp_path, "bad.md", "---\ntitle: Bad\nversion: 2.3\nstatus: retired\nupdated: 01/09/2026\n"
                              "superseded_by: nowhere.md\n---\n# Bad\n\ntext")
    sections, report = load_docs_with_report(tmp_path)
    s = sections[0]
    assert (s.title, s.version, s.status, s.updated) == ("Bad", None, None, None)
    assert s.superseded_by == "nowhere"  # kept as stated, flagged as unknown
    fields = {(i["field"], i["reason"]) for i in report.invalid_metadata}
    assert ("version", "must be non-empty text (got float)") in fields
    assert ("status", "must be one of current, deprecated") in fields
    assert ("updated", "must be a date in YYYY-MM-DD form") in fields
    assert ("superseded_by", "points to an unknown document") in fields


def test_broken_front_matter_does_not_stop_loading(tmp_path):
    write(tmp_path, "broken.md", "---\ntitle: [unclosed\n---\n# Broken\n\nstill readable")
    sections, report = load_docs_with_report(tmp_path)
    assert sections[0].text == "still readable"
    assert {"path": "broken.md", "message": "front matter is not valid YAML; metadata ignored"} in report.warnings


# 12. Deprecated documents are kept
# 13. superseded_by normalisation
def test_superseded_by_normalisation():
    known = {"rate-limits", "legacy/old", "legacy/new", "guide"}
    assert normalise_doc_ref("rate-limits.md", "legacy/old", known) == "rate-limits"
    assert normalise_doc_ref("./rate-limits.md", "legacy/old", known) == "rate-limits"
    assert normalise_doc_ref("/rate-limits", "legacy/old", known) == "rate-limits"
    assert normalise_doc_ref("guide.PDF", "x", known) == "guide"
    assert normalise_doc_ref("new.md", "legacy/old", known) == "legacy/new"  # sibling in the same folder
    assert normalise_doc_ref("legacy\\new.html", "x", known) == "legacy/new"
    assert normalise_doc_ref("missing.md", "legacy/old", known) == "missing"


# 14 + 18. Heading-based chunking and hierarchy
def test_headings_inside_code_blocks_do_not_split(tmp_path):
    body = "# Doc\n\n## Real\n\n```bash\n## not a heading\ncurl  -X POST /v1/orders\n```\n\nafter"
    write(tmp_path, "code.md", md("Doc", body))
    sections = load_docs(tmp_path)
    assert len(sections) == 1 and sections[0].heading == "Real"
    assert "```bash\n## not a heading\ncurl  -X POST /v1/orders\n```" in sections[0].text


# 15 + 16 + 17. Numbering from 1, stable ids, unique ids
def test_id_collisions_are_refused():
    s = DocSection(id="doc:a#1", doc_id="a", title="A", heading="A", heading_path=["A"], text="x",
                   version=None, status=None, updated=None, superseded_by=None, format="md", source_path="a.md")
    with pytest.raises(DocsLoaderError):
        validate_sections([s, s])
    with pytest.raises(DocsLoaderError):
        validate_sections([s.model_copy(update={"id": "doc:a#2"})])


def test_duplicate_document_ids_are_reported(tmp_path):
    write(tmp_path, "guide.md", md("Guide md", "# Guide\n\nmarkdown"))
    write(tmp_path, "guide.html", "<title>Guide html</title><h1>Guide</h1><p>html</p>")
    sections, report = load_docs_with_report(tmp_path)
    assert [s.format for s in sections] == ["html"]  # first in sorted order wins, nothing overwritten
    assert report.documents_skipped[0]["path"] == "guide.md"
    assert "duplicate document id 'guide'" in report.documents_skipped[0]["reason"]


# 19 + 20 + 21. Duplicates are reported, never removed
def test_exact_section_duplicates_are_reported_not_removed(tmp_path):
    write(tmp_path, "a.md", md("A", "# A\n\n## Retry\n\nRetry 429 with   backoff.\n\n## Other\n\nunique a"))
    write(tmp_path, "b.md", md("B", "# B\n\n## Retries\n\nretry 429 WITH backoff.\n"))
    sections, report = load_docs_with_report(tmp_path)
    assert report.duplicate_sections == [{"sha256": report.duplicate_sections[0]["sha256"],
                                          "section_ids": ["doc:a#1", "doc:b#1"]}]
    assert len(sections) == 3  # both copies kept


# 22. Empty documents
def test_empty_documents(tmp_path):
    write(tmp_path, "zero.md", "")
    write(tmp_path, "spaces.md", "   \n\n  ")
    write(tmp_path, "frontonly.md", md("Only metadata", ""))
    write(tmp_path, "ok.md", md("Ok", "# Ok\n\ncontent"))
    sections, report = load_docs_with_report(tmp_path)
    assert [s.doc_id for s in sections] == ["ok"]
    assert {d["path"]: d["reason"] for d in report.documents_skipped} == {
        "spaces.md": "file is empty", "zero.md": "file is empty"}
    assert report.empty_documents == ["frontonly", "spaces.md", "zero.md"]
    assert report.documents_loaded == 2  # frontonly loaded (metadata) but has no sections


# 23. Empty sections
def test_empty_sections_are_reported_and_not_emitted(tmp_path):
    write(tmp_path, "doc.md", md("Doc", "# Doc\n\n## Empty one\n\n\n## Full\n\nreal text\n\n## Empty two\n"))
    sections, report = load_docs_with_report(tmp_path)
    assert [(s.id, s.heading) for s in sections] == [("doc:doc#1", "Full")]
    assert report.empty_sections == [{"doc_id": "doc", "heading": "Empty one"},
                                     {"doc_id": "doc", "heading": "Empty two"}]


# 24. Corrupted PDF
def test_corrupted_pdf_is_reported(tmp_path):
    write(tmp_path, "broken.pdf", b"%PDF-1.4\nthis is not really a pdf\n%%EOF", binary=True)
    sections, report = load_docs_with_report(tmp_path)
    assert sections == []
    assert report.documents_skipped[0]["path"] == "broken.pdf"
    assert report.documents_skipped[0]["reason"].startswith(("corrupted or unreadable PDF", "PDF has no"))
    assert report.pdf_extraction[0]["status"] == "failed"


# 25. Invalid encoding
def test_invalid_encoding_is_reported(tmp_path):
    write(tmp_path, "latin.md", "---\ntitle: Caf\xe9\n---\n# Caf\xe9\n".encode("latin-1"), binary=True)
    write(tmp_path, "bom.md", "﻿".encode("utf-8") + md("Bom", "# Bom\n\nok").encode("utf-8"), binary=True)
    sections, report = load_docs_with_report(tmp_path)
    assert [s.doc_id for s in sections] == ["bom"]  # a UTF-8 BOM is fine
    assert report.documents_skipped == [{"path": "latin.md", "reason": "invalid UTF-8 encoding at byte 14"}]


# 27. Source-path traceability
# 28. Whitespace normalisation
def test_clean_text_whitespace():
    raw = "  \r\nLine  one  has   gaps  \r\n\r\n\r\n\r\n  - indented  item\rzero​width﻿\n\n"
    assert clean_text(raw) == "Line one has gaps\n\n  - indented item\nzerowidth"
    assert clean_text("é") == "é"  # Unicode NFC


# 29. Technical content preservation
# 30. Schema compatibility
# 31. Report accuracy
def test_report_counts_are_exact(tmp_path):
    write(tmp_path, "one.md", md("One", "# One\n\nintro\n\n## A\n\na\n\n## B\n\nb"))
    write(tmp_path, "dir/two.html", "<title>Two</title><meta name='version' content='2'><h1>Two</h1><h2>X</h2><p>x</p>")
    write(tmp_path, "bad.pdf", b"not a pdf", binary=True)
    write(tmp_path, "readme.txt", "ignored")
    sections, report = load_docs_with_report(tmp_path)
    assert (report.files_discovered, report.supported_files, report.documents_loaded) == (4, 3, 2)
    assert report.sections_total == len(sections) == 4
    assert report.documents_by_format == {"md": 1, "html": 1, "pdf": 0}
    assert [d["path"] for d in report.documents_skipped] == ["bad.pdf"]
    assert [e["path"] for e in report.errors] == ["bad.pdf"]
    assert report.unsupported_files == ["readme.txt"]
    assert report.missing_metadata == [{"doc_id": "dir/two", "fields": ["status", "updated"]}]
    assert report.duration_ms >= 0
    first = report.model_dump(exclude={"duration_ms"})
    assert load_docs_with_report(tmp_path)[1].model_dump(exclude={"duration_ms"}) == first


# 32. No source-file modification
# 33. One bad document does not stop the batch
def test_recovers_from_invalid_documents(tmp_path):
    write(tmp_path, "a-good.md", md("Good", "# Good\n\nfine"))
    write(tmp_path, "b-broken.pdf", b"\x00\x01garbage", binary=True)
    write(tmp_path, "c-latin.md", b"caf\xe9", binary=True)
    write(tmp_path, "d-empty.html", "")
    write(tmp_path, "e-good.html", "<title>E</title><h1>E</h1><p>fine too</p>")
    sections, report = load_docs_with_report(tmp_path)
    assert [s.doc_id for s in sections] == ["a-good", "e-good"]
    assert [d["path"] for d in report.documents_skipped] == ["b-broken.pdf", "c-latin.md", "d-empty.html"]
    assert all(d["reason"] for d in report.documents_skipped)


# 34. No unnecessary dependencies
def test_only_approved_imports():
    tree = ast.parse(Path(docs_loader.__file__).read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    third_party = roots - {"__future__", "hashlib", "logging", "os", "re", "time", "unicodedata",
                           "collections", "dataclasses", "datetime", "html", "io", "pathlib", "typing", "app"}
    assert third_party == {"yaml", "pydantic", "pypdf"}


# Configuration errors
def test_invalid_folder_raises(tmp_path):
    with pytest.raises(DocsLoaderError):
        load_docs(tmp_path / "missing")
    file_path = write(tmp_path, "file.md", "x")
    with pytest.raises(DocsLoaderError):
        load_docs(file_path)
    with pytest.raises(DocsLoaderError):
        load_docs("")


def test_empty_folder_gives_empty_result(tmp_path):
    sections, report = load_docs_with_report(tmp_path)
    assert sections == [] and report.files_discovered == 0 and report.documents_loaded == 0


def test_invalid_file_names_are_reported(tmp_path):
    write(tmp_path, "has space.md", md("S", "# S\n\ntext"))
    sections, report = load_docs_with_report(tmp_path)
    assert sections == [] and "invalid document id" in report.documents_skipped[0]["reason"]


def test_oversized_file_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(docs_loader, "MAX_FILE_BYTES", 50)
    write(tmp_path, "big.md", md("Big", "# Big\n\n" + "x" * 100))
    sections, report = load_docs_with_report(tmp_path)
    assert sections == [] and report.documents_skipped[0]["reason"] == "file is larger than 50 bytes"


# Synthetic replacements for the mock's real-data tests
@pytest.fixture
def sample(tmp_path):
    write(tmp_path, "setup.md", md("Setup", "# Setup\n\nIntro: each key may send 100 requests per minute.\n\n"
                                            "## Install\n\nRun `pip install`.\n\n### Required fields\n\n- `name`\n\n"
                                            "## Configure\n\nSet ERR_TIMEOUT to 15000 ms."))
    write(tmp_path, "legacy/setup-v1.md", "---\ntitle: Old setup\nversion: \"0.9\"\nstatus: deprecated\n"
                                          "superseded_by: setup.md\n---\n# Old setup\n\n## Limits\n\n60 requests per minute.")
    write(tmp_path, "status.html", "<html><head><title>Status</title><meta name='version' content='2.1'>"
                                   "<meta name='status' content='current'><meta name='updated' content='2026-09-01'>"
                                   "</head><body><nav>Home | Docs</nav><h1>Status</h1><p>Intro text.</p>"
                                   "<h2>Latency</h2><p>p95 under 1500 ms.</p><script>widget()</script></body></html>")
    return tmp_path


def test_sample_folder_loads_all_formats(sample):
    sections, report = load_docs_with_report(sample)
    assert report.documents_loaded == 3 and report.errors == []
    assert report.documents_by_format == {"md": 2, "html": 1, "pdf": 0}
    assert [s.id for s in sections] == ["doc:legacy/setup-v1#1", "doc:setup#1", "doc:setup#2", "doc:setup#3",
                                        "doc:status#1", "doc:status#2"]


def test_markdown_metadata_and_nested_headings(sample):
    by_id = {s.id: s for s in load_docs(sample)}
    intro = by_id["doc:setup#1"]
    assert (intro.title, intro.version, intro.status, intro.updated, intro.format) == (
        "Setup", "1.0", "current", "2026-01-01", "md")
    assert intro.heading_path == ["Setup"] and "100 requests per minute" in intro.text
    assert "title:" not in intro.text
    install = by_id["doc:setup#2"]
    assert install.heading_path == ["Setup", "Install"] and "### Required fields" in install.text
    assert "`name`" in install.text and "ERR_TIMEOUT" in by_id["doc:setup#3"].text


def test_html_metadata_and_clean_text(sample):
    status = [s for s in load_docs(sample) if s.doc_id == "status"]
    assert [s.heading for s in status] == ["Status", "Latency"]
    assert (status[0].version, status[0].status, status[0].updated, status[0].format) == (
        "2.1", "current", "2026-09-01", "html")
    joined = " ".join(s.text for s in status)
    assert "Home | Docs" not in joined and "widget" not in joined and "p95 under 1500 ms." in joined


def test_deprecated_document_is_kept_and_reported(sample):
    sections, report = load_docs_with_report(sample)
    legacy = [s for s in sections if s.doc_id == "legacy/setup-v1"]
    assert legacy and all(s.status == "deprecated" and s.superseded_by == "setup" for s in legacy)
    assert report.deprecated_documents == ["legacy/setup-v1"]


def test_ids_are_unique_stable_and_source_files_untouched(sample):
    before = tree_fingerprint(sample)
    first = load_docs(sample)
    again = load_docs(sample)
    assert [s.model_dump() for s in first] == [s.model_dump() for s in again]
    assert len({s.id for s in first}) == len(first)
    assert tree_fingerprint(sample) == before


def test_exact_file_duplicates_are_reported_not_removed(sample, tmp_path):
    folder = tmp_path / "dups"
    folder.mkdir()
    content = (sample / "setup.md").read_bytes()
    (folder / "setup.md").write_bytes(content)
    (folder / "copy-of-setup.md").write_bytes(content)
    sections, report = load_docs_with_report(folder)
    assert report.documents_loaded == 2 and len(report.duplicate_files) == 1
    assert {s.doc_id for s in sections} == {"setup", "copy-of-setup"}


def test_docsection_schema_is_exact(sample):
    sample_section = load_docs(sample)[0]
    assert list(DocSection.model_fields) == DOCSECTION_FIELDS
    assert DocSection.model_validate_json(sample_section.model_dump_json()) == sample_section
    with pytest.raises(ValidationError):
        DocSection(**sample_section.model_dump(), extra_field=1)
    with pytest.raises(ValidationError):
        DocSection(**{**sample_section.model_dump(), "format": "docx"})
    with pytest.raises(ValidationError):
        sample_section.text = "changed"  # frozen: consumers cannot mutate shared sections

