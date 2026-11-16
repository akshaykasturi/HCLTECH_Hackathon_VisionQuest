"""Document ingestion: md / html / pdf files in a folder -> list[DocSection] (one per "##" heading).

Default folder: backend/data/docs, or the DOCS_DIR environment variable.

Hand-over to doc search (doc_search.py): call load_docs(folder) and index the returned DocSection list.
Use load_docs_with_report(folder) to also get the IngestionReport (what was loaded, skipped, flagged).

Pipeline, one small function per stage:
    discover_files -> read_and_validate -> extract (markdown / html / pdf) -> clean_text
    -> metadata (extract + validate) -> chunk by heading -> duplicates (report only)
    -> schema / id validation -> IngestionReport

Rules
- Read-only: source files are opened for reading only. No network, no subprocesses.
- Nothing is invented: metadata the file does not state is None and is listed as missing.
- Nothing is hidden: every skipped file, empty section, bad metadata value and duplicate is in the
  report with its path and a reason. Duplicates and deprecated docs are REPORTED, never removed.
- Deterministic: files are processed in sorted path order; section ids are doc:<doc_id>#<n>, n from 1.

Chunking: one section per level-2 heading (Markdown "## ", HTML <h2>, PDF numbered heading "1. Title").
Text between the title and the first level-2 heading becomes the intro section (only if it has
text). Deeper headings (###, <h3>...) stay inside their parent section, so details such as
"Required fields" stays with its parent "Create an order" section. Sections without text are not
emitted (there is nothing to search) and are listed in report.empty_sections; numbering counts
emitted sections only.

Doc id = path relative to the docs folder, without extension, with "/" separators
(e.g. "guides/setup"). Use the same ids in any retrieval gold file.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Any, Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.common.paths import BACKEND_DIR, DATA_DIR

logger = logging.getLogger(__name__)

DEFAULT_DOCS_DIR = Path(os.getenv("DOCS_DIR") or DATA_DIR / "docs")
SUPPORTED_EXTENSIONS = {".md": "md", ".html": "html", ".pdf": "pdf"}
ALLOWED_STATUSES = ("current", "deprecated")
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 1000

DocFormat = Literal["md", "html", "pdf"]
DocStatus = Literal["current", "deprecated"]
PathLike = Union[str, Path]

_DOC_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")
_MD_H1_RE = re.compile(r"^#\s+(.+?)\s*#*\s*$")
_MD_H2_RE = re.compile(r"^##\s+(.*?)\s*#*\s*$")
_PDF_HEADING_RE = re.compile(r"^(\d{1,2})\.\s+([A-Z][^.:;!?]{0,70})$")
_PDF_VERSION_RE = re.compile(r"^Version\s+(\d+(?:\.\d+)*)\s*,\s*(\d{4}-\d{2}-\d{2})\b")
_INVISIBLE = dict.fromkeys(map(ord, "﻿​‌‍⁠"), None)


# ---------- output schema ----------

class DocSection(BaseModel):
    """One searchable section of one document (the hand-over object for doc search)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str                         # "doc:<doc_id>#<n>", n starts at 1
    doc_id: str                     # "guides/setup"
    title: Optional[str]            # document title (None if the file does not state one)
    heading: str                    # this section's heading (the doc heading for the intro)
    heading_path: list[str]         # [doc heading, section heading]; [doc heading] for the intro
    text: str                       # cleaned section text (never empty)
    version: Optional[str]
    status: Optional[DocStatus]     # "current" | "deprecated" | None (not stated)
    updated: Optional[str]          # ISO date "YYYY-MM-DD"
    superseded_by: Optional[str]    # doc_id of the replacement document
    format: DocFormat               # "md" | "html" | "pdf"
    source_path: str                # relative to backend/ when inside it, else absolute (forward slashes)


class DocsLoaderError(ValueError):
    """Invalid loader configuration (for example the docs folder does not exist)."""


@dataclass
class ApiDoc:
    """One loaded document before chunking (internal; not part of the hand-over contract)."""

    doc_id: str
    format: DocFormat
    source_path: str
    sha256: str
    title: Optional[str]
    version: Optional[str]
    status: Optional[str]
    updated: Optional[str]
    superseded_by: Optional[str]
    top_heading: Optional[str]                     # H1 / <h1> / PDF first line, if any
    blocks: list[tuple[Optional[str], str]]        # (level-2 heading or None for intro, raw text)


class IngestionReport(BaseModel):
    """What happened during one ingestion run. Deterministic apart from duration_ms."""

    source_folder: str
    files_discovered: int = 0
    supported_files: int = 0
    documents_loaded: int = 0
    documents_skipped: list[dict[str, str]] = Field(default_factory=list)       # {path, reason}
    documents_by_format: dict[str, int] = Field(default_factory=dict)
    sections_total: int = 0
    deprecated_documents: list[str] = Field(default_factory=list)
    empty_documents: list[str] = Field(default_factory=list)
    empty_sections: list[dict[str, str]] = Field(default_factory=list)          # {doc_id, heading}
    missing_metadata: list[dict[str, Any]] = Field(default_factory=list)        # {doc_id, fields}
    invalid_metadata: list[dict[str, str]] = Field(default_factory=list)        # {doc_id, field, reason}
    duplicate_files: list[dict[str, Any]] = Field(default_factory=list)         # {sha256, paths}
    duplicate_sections: list[dict[str, Any]] = Field(default_factory=list)      # {sha256, section_ids}
    unsupported_files: list[str] = Field(default_factory=list)
    warnings: list[dict[str, str]] = Field(default_factory=list)                # {path, message}
    errors: list[dict[str, str]] = Field(default_factory=list)                  # {path, message}
    pdf_extraction: list[dict[str, Any]] = Field(default_factory=list)
    duration_ms: float = 0.0


@dataclass
class IngestionResult:
    """Everything one run produced: sections (the contract), parsed docs and the report."""

    sections: list[DocSection]
    documents: list[ApiDoc]
    report: IngestionReport


class _SkipFile(Exception):
    """Internal: this file cannot be loaded; the message is the reason (no file content)."""


# ---------- stage A: discovery ----------

@dataclass(frozen=True)
class DiscoveredFile:
    path: Path
    rel_path: str           # relative to the docs folder, forward slashes
    doc_id: str
    format: DocFormat


def discover_files(folder: Path, report: Optional[IngestionReport] = None) -> list[DiscoveredFile]:
    """Find supported files under folder, recursively, in sorted order; report everything else.

    Directory symlinks are not followed; file symlinks pointing outside the folder are refused.
    """
    root = folder.resolve()
    found: list[DiscoveredFile] = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            total += 1
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            fmt = SUPPORTED_EXTENSIONS.get(path.suffix.lower())
            if fmt is None:
                if report is not None:
                    report.unsupported_files.append(rel)
                continue
            if path.is_symlink():
                target = path.resolve()
                if root != target and root not in target.parents:
                    if report is not None:
                        report.documents_skipped.append(
                            {"path": rel, "reason": "symlink points outside the docs folder"})
                    continue
            doc_id = rel[: -len(path.suffix)]
            found.append(DiscoveredFile(path, rel, doc_id, fmt))
    found.sort(key=lambda f: f.rel_path)
    if report is not None:
        report.files_discovered = total
        report.supported_files = len(found)
        report.unsupported_files.sort()
    return found


# ---------- stage B: validation and reading ----------

def read_and_validate(file: DiscoveredFile) -> bytes:
    """Return the file's bytes, or raise _SkipFile with a reason (bad id, unreadable, empty, too big)."""
    if not _DOC_ID_RE.match(file.doc_id):
        raise _SkipFile("file name gives an invalid document id (use letters, digits, '.', '_', '-')")
    try:
        size = file.path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise _SkipFile(f"file is larger than {MAX_FILE_BYTES} bytes")
        with open(file.path, "rb") as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
    except OSError as exc:
        raise _SkipFile(f"file cannot be read ({exc.__class__.__name__})") from None
    if len(data) > MAX_FILE_BYTES:
        raise _SkipFile(f"file is larger than {MAX_FILE_BYTES} bytes")
    if not data.strip():
        raise _SkipFile("file is empty")
    return data


def decode_text(data: bytes) -> str:
    """Decode UTF-8 (a BOM is allowed); anything else is an invalid encoding."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _SkipFile(f"invalid UTF-8 encoding at byte {exc.start}") from None


# ---------- stage D: cleaning ----------

def split_fenced(text: str) -> list[tuple[bool, str]]:
    """Split Markdown text into (is_code_block, chunk) parts so code is never re-formatted."""
    parts: list[tuple[bool, list[str]]] = [(False, [])]
    in_code = False
    for line in text.split("\n"):
        is_fence = bool(_FENCE_RE.match(line))
        if is_fence and not in_code:
            in_code = True
            parts.append((True, [line]))
            continue
        parts[-1][1].append(line)
        if is_fence and in_code:
            in_code = False
            parts.append((False, []))
    return [(code, "\n".join(lines)) for code, lines in parts if lines]


def clean_text(text: str) -> str:
    """Normalise text conservatively without changing its meaning.

    NFC Unicode, '\\n' line endings, no invisible characters, non-breaking spaces as spaces,
    no trailing spaces, single spaces between words (indentation kept), at most one blank line in
    a row. Code blocks only get line endings and trailing spaces normalised.
    """
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    text = text.translate(_INVISIBLE).replace(" ", " ")
    out: list[str] = []
    for is_code, chunk in split_fenced(text):
        for line in chunk.split("\n"):
            line = line.rstrip()
            if not is_code:
                stripped = line.lstrip(" ")
                indent = line[: len(line) - len(stripped)]
                line = indent + re.sub(r"[ \t]{2,}", " ", stripped)
            if not line and (not out or not out[-1]) and not is_code:
                continue
            out.append(line)
    return "\n".join(out).strip("\n")


def _normalise_for_compare(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip().casefold()


# ---------- stage C + E: extraction and metadata ----------

def _split_front_matter(text: str) -> tuple[Optional[str], str]:
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() in ("---", "..."):
                return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    return None, text


def extract_markdown(text: str) -> tuple[dict[str, Any], Optional[str], list[tuple[Optional[str], str]], list[str]]:
    """Front matter, top heading, (level-2 heading, text) blocks and warnings from Markdown."""
    warnings: list[str] = []
    meta: dict[str, Any] = {}
    raw_meta, body = _split_front_matter(text.replace("\r\n", "\n").replace("\r", "\n"))
    if raw_meta is not None:
        try:
            loaded = yaml.safe_load(raw_meta) if raw_meta.strip() else {}
        except yaml.YAMLError:
            loaded = None
            warnings.append("front matter is not valid YAML; metadata ignored")
        if isinstance(loaded, dict):
            meta = loaded
        elif loaded is not None:
            warnings.append("front matter is not a key/value mapping; metadata ignored")

    top_heading: Optional[str] = None
    blocks: list[tuple[Optional[str], list[str]]] = [(None, [])]
    for is_code, chunk in split_fenced(body):
        for line in chunk.split("\n"):
            if not is_code:
                h1 = _MD_H1_RE.match(line)
                if h1 and top_heading is None and not any(l.strip() for l in blocks[-1][1]) and len(blocks) == 1:
                    top_heading = h1.group(1).strip()
                    continue
                h2 = _MD_H2_RE.match(line) or (h1 if h1 else None)
                if h2:
                    blocks.append((h2.group(1).strip(), []))
                    continue
            blocks[-1][1].append(line)
    if not is_html_comment_free(body):
        warnings.append("HTML comments removed")
    return meta, top_heading, [(h, _strip_comments("\n".join(lines))) for h, lines in blocks], warnings


def is_html_comment_free(text: str) -> bool:
    """True when no HTML comment appears outside code blocks."""
    return all(code or "<!--" not in chunk for code, chunk in split_fenced(text))


def _strip_comments(text: str) -> str:
    return "\n".join(chunk if code else re.sub(r"<!--.*?-->", "", chunk, flags=re.S)
                     for code, chunk in split_fenced(text))


class _HtmlExtractor(HTMLParser):
    """Collects <title>, <meta name=...>, <h1>, and level-2 blocks; drops page furniture."""

    SKIP = {"script", "style", "nav", "header", "footer", "aside", "noscript", "template", "svg",
            "form", "button", "iframe", "object"}
    BLOCK = {"p", "div", "section", "article", "main", "ul", "ol", "li", "table", "tr", "pre",
             "blockquote", "dl", "dt", "dd", "h3", "h4", "h5", "h6", "hr", "figure", "figcaption"}
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
            "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title_parts: list[str] = []
        self.h1: Optional[str] = None
        self.blocks: list[tuple[Optional[str], list[str]]] = [(None, [])]
        self._skip_depth = 0
        self._in_title = False
        self._heading: Optional[str] = None     # "h1" or "h2" while inside one
        self._heading_parts: list[str] = []
        self._pre_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if self._skip_depth:
            if tag in self.SKIP:
                self._skip_depth += 1
            return
        if tag in self.SKIP:
            self._skip_depth = 1
            return
        if tag == "meta":
            values = dict(attrs)
            name = (values.get("name") or "").strip().lower()
            if name and values.get("content") is not None:
                self.meta[name] = values["content"] or ""
        elif tag == "title":
            self._in_title = True
        elif tag in ("h1", "h2"):
            self._heading, self._heading_parts = tag, []
        elif tag == "br":
            self._text("\n")
        elif tag == "li":
            self._text("\n- ")
        elif tag in ("td", "th"):
            self._text(" ")
        elif tag in self.BLOCK:
            if tag == "pre":
                self._pre_depth += 1
                if self._pre_depth == 1:
                    self._text("\n```\n")   # mark as code so cleaning keeps its spacing
                return
            self._text("\n")
            if tag in ("h3", "h4", "h5", "h6"):
                self._text("#" * int(tag[1]) + " ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            if tag in self.SKIP:
                self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
        elif tag in ("h1", "h2") and self._heading == tag:
            text = re.sub(r"\s+", " ", "".join(self._heading_parts)).strip()
            if tag == "h1" and self.h1 is None and len(self.blocks) == 1 and not "".join(self.blocks[0][1]).strip():
                self.h1 = text
            else:
                self.blocks.append((text, []))
            self._heading = None
        elif tag in self.BLOCK and tag not in self.VOID:
            if tag == "pre":
                self._pre_depth = max(0, self._pre_depth - 1)
                if self._pre_depth == 0:
                    self._text("\n```\n")
                return
            self._text("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        elif self._heading:
            self._heading_parts.append(data)
        else:
            self._text(data if self._pre_depth else re.sub(r"\s+", " ", data))

    def _text(self, value: str) -> None:
        self.blocks[-1][1].append(value)


def extract_html(text: str) -> tuple[dict[str, Any], Optional[str], list[tuple[Optional[str], str]], list[str]]:
    """Title/meta metadata, <h1>, and (<h2>, text) blocks from HTML, using html.parser only."""
    parser = _HtmlExtractor()
    parser.feed(text)
    parser.close()
    meta: dict[str, Any] = {k: v for k, v in parser.meta.items()
                            if k in ("version", "status", "updated", "superseded_by")}
    title = re.sub(r"\s+", " ", "".join(parser.title_parts)).strip()
    if title:
        meta["title"] = title
    blocks = []
    for heading, parts in parser.blocks:
        pieces = []
        for is_code, chunk in split_fenced("".join(parts)):
            if is_code:
                pieces.append(chunk.strip("\n"))
            else:
                pieces.extend(line.strip() for line in chunk.split("\n") if line.strip())
        blocks.append((heading, "\n".join(pieces)))
    return meta, parser.h1, blocks, []


def extract_pdf(data: bytes, report_entry: dict[str, Any]) -> tuple[dict[str, Any], Optional[str], list[tuple[Optional[str], str]], list[str]]:
    """Metadata, first line and numbered-heading blocks from a PDF, using pypdf.

    Every page is extracted; empty or failing pages are recorded in report_entry. Wrapped lines
    inside a section are joined with spaces. Raises _SkipFile if the PDF is unreadable or has no text.
    """
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError:
        raise _SkipFile("pypdf is not installed, so PDF files cannot be read") from None

    warnings: list[str] = []
    try:
        reader = PdfReader(BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise _SkipFile("PDF is encrypted")
        pages = reader.pages
        page_count = len(pages)
    except _SkipFile:
        raise
    except (PdfReadError, ValueError, TypeError, KeyError, OSError, AttributeError) as exc:
        raise _SkipFile(f"corrupted or unreadable PDF ({exc.__class__.__name__})") from None
    if page_count == 0:
        raise _SkipFile("PDF has no pages")
    if page_count > MAX_PDF_PAGES:
        raise _SkipFile(f"PDF has more than {MAX_PDF_PAGES} pages")

    report_entry["pages"] = page_count
    empty_pages: list[int] = []
    failed_pages: list[int] = []
    lines: list[str] = []
    for number, page in enumerate(pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:  # pypdf can raise many types on a broken page; keep the batch going
            failed_pages.append(number)
            warnings.append(f"page {number} could not be extracted ({exc.__class__.__name__})")
            continue
        page_lines = [line.strip() for line in clean_text(page_text).split("\n") if line.strip()]
        if not page_lines:
            empty_pages.append(number)
            warnings.append(f"page {number} has no extractable text")
        lines.extend(page_lines)
    report_entry.update({"pages_with_text": page_count - len(empty_pages) - len(failed_pages),
                         "empty_pages": empty_pages, "failed_pages": failed_pages})
    if not lines:
        raise _SkipFile("PDF has no extractable text (scanned image or empty)")

    meta: dict[str, Any] = {}
    try:
        info = reader.metadata or {}
        pdf_title = str(info.get("/Title") or "").strip()
    except Exception:
        pdf_title = ""
        warnings.append("PDF metadata could not be read")
    if pdf_title:
        meta["title"] = pdf_title

    top_heading = None
    if pdf_title and lines and _normalise_for_compare(lines[0]) == _normalise_for_compare(pdf_title):
        top_heading = lines.pop(0)
    for line in lines[:3]:
        version = _PDF_VERSION_RE.match(line)
        if version:
            meta["version"], meta["updated"] = version.group(1), version.group(2)
            break

    blocks: list[tuple[Optional[str], list[str]]] = [(None, [])]
    expected = 1
    for line in lines:
        heading = _PDF_HEADING_RE.match(line)
        if heading and int(heading.group(1)) == expected:   # numbered headings must run 1, 2, 3...
            blocks.append((f"{heading.group(1)}. {heading.group(2).strip()}", []))
            expected += 1
            continue
        blocks[-1][1].append(line)
    report_entry["headings_detected"] = expected - 1
    return meta, top_heading, [(h, " ".join(parts)) for h, parts in blocks], warnings


# ---------- stage F: metadata validation ----------

def _as_date_string(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and _DATE_RE.match(value.strip()):
        try:
            return date.fromisoformat(value.strip()).isoformat()
        except ValueError:
            return None
    return None


def normalise_doc_ref(value: str, from_doc_id: str, known_ids: set[str]) -> str:
    """Turn a reference like 'setup.md' or './x.html' into a doc_id.

    Tried in order: relative to the docs root, then relative to the referring doc's folder.
    If neither exists, the root-relative form is returned (the caller reports it as unknown).
    """
    ref = value.strip().replace("\\", "/")
    while ref.startswith("./"):
        ref = ref[2:]
    ref = ref.lstrip("/")
    stem, dot, ext = ref.rpartition(".")
    if dot and "/" not in ext and f".{ext.lower()}" in SUPPORTED_EXTENSIONS:
        ref = stem
    if ref in known_ids:
        return ref
    parent = from_doc_id.rpartition("/")[0]
    sibling = f"{parent}/{ref}" if parent else ref
    return sibling if sibling in known_ids else ref


def validate_metadata(doc_id: str, raw: dict[str, Any], known_ids: set[str],
                      report: IngestionReport) -> dict[str, Optional[str]]:
    """Check each metadata value; keep valid ones, set invalid ones to None and report why."""
    clean: dict[str, Optional[str]] = {}

    def invalid(name: str, reason: str) -> None:
        report.invalid_metadata.append({"doc_id": doc_id, "field": name, "reason": reason})
        clean[name] = None

    for name in ("title", "version"):
        value = raw.get(name)
        if value is None:
            clean[name] = None
        elif isinstance(value, str) and value.strip():
            clean[name] = value.strip()
        else:
            invalid(name, f"must be non-empty text (got {type(value).__name__})")

    status = raw.get("status")
    if status is None:
        clean["status"] = None
    elif isinstance(status, str) and status.strip().lower() in ALLOWED_STATUSES:
        clean["status"] = status.strip().lower()
    else:
        invalid("status", f"must be one of {', '.join(ALLOWED_STATUSES)}")

    updated = raw.get("updated")
    if updated is None:
        clean["updated"] = None
    else:
        parsed = _as_date_string(updated)
        if parsed is None:
            invalid("updated", "must be a date in YYYY-MM-DD form")
        else:
            clean["updated"] = parsed

    superseded = raw.get("superseded_by")
    if superseded is None:
        clean["superseded_by"] = None
    elif not isinstance(superseded, str) or not superseded.strip():
        invalid("superseded_by", "must be a document path or id")
    else:
        ref = normalise_doc_ref(superseded, doc_id, known_ids)
        clean["superseded_by"] = ref
        if ref == doc_id:
            report.invalid_metadata.append(
                {"doc_id": doc_id, "field": "superseded_by", "reason": "document supersedes itself"})
        elif ref not in known_ids:
            report.invalid_metadata.append(
                {"doc_id": doc_id, "field": "superseded_by", "reason": "points to an unknown document"})

    if clean.get("status") == "deprecated" and not clean.get("superseded_by"):
        report.warnings.append({"path": doc_id, "message": "deprecated document has no superseded_by"})

    missing = [name for name in ("title", "version", "status", "updated")
               if raw.get(name) is None]
    if missing:
        report.missing_metadata.append({"doc_id": doc_id, "fields": missing})
    return clean


# ---------- stage H: chunking ----------

def _source_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(BACKEND_DIR.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def chunk_document(doc: ApiDoc, report: IngestionReport) -> list[DocSection]:
    """Turn one document's blocks into DocSections numbered 1, 2, 3... (empty blocks are reported)."""
    base = doc.top_heading or doc.title or doc.doc_id
    sections: list[DocSection] = []
    for heading, raw_text in doc.blocks:
        text = clean_text(raw_text)
        if not text:
            if heading is not None:
                report.empty_sections.append({"doc_id": doc.doc_id, "heading": heading})
            continue
        n = len(sections) + 1
        sections.append(DocSection(
            id=f"doc:{doc.doc_id}#{n}", doc_id=doc.doc_id, title=doc.title,
            heading=heading if heading is not None else base,
            heading_path=[base, heading] if heading is not None else [base],
            text=text, version=doc.version, status=doc.status, updated=doc.updated,
            superseded_by=doc.superseded_by, format=doc.format, source_path=doc.source_path,
        ))
    return sections


# ---------- stage G: duplicates (report only) ----------

def find_duplicate_files(docs: list[ApiDoc]) -> list[dict[str, Any]]:
    """Groups of byte-identical files (same SHA-256). Nothing is removed."""
    groups: dict[str, list[str]] = defaultdict(list)
    for doc in docs:
        groups[doc.sha256].append(doc.source_path)
    return [{"sha256": digest, "paths": sorted(paths)}
            for digest, paths in sorted(groups.items()) if len(paths) > 1]


def find_duplicate_sections(sections: list[DocSection]) -> list[dict[str, Any]]:
    """Groups of sections whose normalised text is identical. Nothing is removed."""
    groups: dict[str, list[str]] = defaultdict(list)
    for section in sections:
        digest = hashlib.sha256(_normalise_for_compare(section.text).encode("utf-8")).hexdigest()
        groups[digest].append(section.id)
    return [{"sha256": digest, "section_ids": ids}
            for digest, ids in sorted(groups.items(), key=lambda kv: kv[1][0]) if len(ids) > 1]


# ---------- schema validation ----------

def validate_sections(sections: list[DocSection]) -> None:
    """Check id format, id uniqueness and numbering per document; raise DocsLoaderError if broken."""
    seen: set[str] = set()
    counters: dict[str, int] = defaultdict(int)
    for section in sections:
        if section.id in seen:
            raise DocsLoaderError(f"duplicate section id: {section.id}")
        seen.add(section.id)
        counters[section.doc_id] += 1
        if section.id != f"doc:{section.doc_id}#{counters[section.doc_id]}":
            raise DocsLoaderError(f"section id out of sequence: {section.id}")
        if not section.text.strip():
            raise DocsLoaderError(f"section has no text: {section.id}")


# ---------- orchestration ----------

def _resolve_folder(folder: Optional[PathLike]) -> Path:
    if folder is None:
        folder = DEFAULT_DOCS_DIR
    if not isinstance(folder, (str, Path)) or not str(folder).strip():
        raise DocsLoaderError("docs folder must be a non-empty path")
    path = Path(folder)
    if not path.exists():
        raise DocsLoaderError(f"docs folder does not exist: {path}")
    if not path.is_dir():
        raise DocsLoaderError(f"docs folder is not a directory: {path}")
    return path


def ingest(folder: Optional[PathLike] = None) -> IngestionResult:
    """Run the whole pipeline on folder (default: DEFAULT_DOCS_DIR)."""
    started = time.perf_counter()
    root = _resolve_folder(folder)
    report = IngestionReport(source_folder=_source_path(root))
    files = discover_files(root, report)
    known_ids = {f.doc_id for f in files}

    docs: list[ApiDoc] = []
    claimed: dict[str, str] = {}
    for file in files:
        if file.doc_id in claimed:
            report.documents_skipped.append(
                {"path": file.rel_path, "reason": f"duplicate document id '{file.doc_id}' "
                                                  f"(already used by {claimed[file.doc_id]})"})
            report.errors.append({"path": file.rel_path, "message": "duplicate document id"})
            continue
        claimed[file.doc_id] = file.rel_path
        pdf_entry: dict[str, Any] = {"doc_id": file.doc_id, "path": file.rel_path}
        try:
            data = read_and_validate(file)
            if file.format == "pdf":
                meta, top, blocks, warnings = extract_pdf(data, pdf_entry)
            else:
                text = decode_text(data)
                extractor = extract_markdown if file.format == "md" else extract_html
                meta, top, blocks, warnings = extractor(text)
        except _SkipFile as exc:
            reason = str(exc)
            report.documents_skipped.append({"path": file.rel_path, "reason": reason})
            report.errors.append({"path": file.rel_path, "message": reason})
            if reason == "file is empty":
                report.empty_documents.append(file.rel_path)
            if file.format == "pdf":
                report.pdf_extraction.append({**pdf_entry, "status": "failed", "reason": reason})
            logger.warning("skipped %s: %s", file.rel_path, reason)
            continue
        if file.format == "pdf":
            report.pdf_extraction.append({**pdf_entry, "status": "ok" if not warnings else "partial"})
        for message in warnings:
            report.warnings.append({"path": file.rel_path, "message": message})

        clean = validate_metadata(file.doc_id, meta, known_ids, report)
        if clean["title"] is None and top:
            # Still listed under missing_metadata (the file's metadata does not state it).
            clean["title"] = top
            report.warnings.append({"path": file.rel_path, "message": "title taken from the document's top heading"})
        docs.append(ApiDoc(
            doc_id=file.doc_id, format=file.format, source_path=_source_path(file.path),
            sha256=hashlib.sha256(data).hexdigest(), title=clean["title"], version=clean["version"],
            status=clean["status"], updated=clean["updated"], superseded_by=clean["superseded_by"],
            top_heading=top, blocks=blocks,
        ))

    sections: list[DocSection] = []
    for doc in docs:
        doc_sections = chunk_document(doc, report)
        if not doc_sections:
            report.empty_documents.append(doc.doc_id)
            report.warnings.append({"path": doc.doc_id, "message": "document has no text sections"})
        sections.extend(doc_sections)
    validate_sections(sections)

    report.documents_loaded = len(docs)
    report.documents_by_format = {fmt: sum(d.format == fmt for d in docs) for fmt in ("md", "html", "pdf")}
    report.sections_total = len(sections)
    report.deprecated_documents = sorted(d.doc_id for d in docs if d.status == "deprecated")
    report.duplicate_files = find_duplicate_files(docs)
    report.duplicate_sections = find_duplicate_sections(sections)
    report.documents_skipped.sort(key=lambda x: x["path"])
    report.errors.sort(key=lambda x: (x["path"], x["message"]))
    report.warnings.sort(key=lambda x: (x["path"], x["message"]))
    report.empty_documents.sort()
    report.invalid_metadata.sort(key=lambda x: (x["doc_id"], x["field"], x["reason"]))
    report.missing_metadata.sort(key=lambda x: x["doc_id"])
    report.duration_ms = round((time.perf_counter() - started) * 1000, 3)
    logger.info("docs ingestion: %d/%d documents, %d sections, %d skipped",
                report.documents_loaded, report.supported_files, report.sections_total,
                len(report.documents_skipped))
    return IngestionResult(sections=sections, documents=docs, report=report)


def load_docs(folder: Optional[PathLike] = None) -> list[DocSection]:
    """The hand-over: all DocSections from folder (default DEFAULT_DOCS_DIR), in order."""
    return ingest(folder).sections


def load_docs_with_report(folder: Optional[PathLike] = None) -> tuple[list[DocSection], IngestionReport]:
    """Same sections as load_docs, plus the IngestionReport."""
    result = ingest(folder)
    return result.sections, result.report
