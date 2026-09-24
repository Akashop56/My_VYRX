"""Streaming, extensible parsers for local knowledge sources."""
from __future__ import annotations

import csv
import codecs
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Protocol


class ParserError(RuntimeError):
    """A source could not be parsed without exposing raw details to callers."""


class UnsupportedFormatError(ParserError):
    """No parser is registered for a source suffix."""


@dataclass(frozen=True)
class ParsedUnit:
    """A bounded parser output unit with source-relative location."""

    text: str
    char_start: int
    char_end: int
    line_start: int
    line_end: int
    kind: str = "text"


class Parser(Protocol):
    name: str

    def parse(self, path: Path, *, read_size: int = 65536) -> Iterator[ParsedUnit]:
        ...


def _bounded_units(
    text: str,
    *,
    char_start: int,
    line_start: int,
    kind: str,
    limit: int,
) -> Iterator[ParsedUnit]:
    """Split one parser record without retaining an unbounded unit."""
    limit = max(1024, int(limit))
    offset = char_start
    current_line = line_start
    for index in range(0, len(text), limit):
        piece = text[index:index + limit]
        end = offset + len(piece)
        next_line = current_line + piece.count("\\n")
        yield ParsedUnit(piece, offset, end, current_line, next_line, kind)
        offset = end
        current_line = next_line


def _iter_utf8_text(path: Path, *, read_size: int = 65536) -> Iterator[ParsedUnit]:
    """Stream strict UTF-8 text through a bounded incremental decoder."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    char_offset = 0
    line_number = 1
    pending = ""
    with path.open("rb") as handle:
        while True:
            raw = handle.read(max(1024, int(read_size)))
            if not raw:
                break
            pending += decoder.decode(raw, final=False)
            # Keep parser units bounded while preserving line boundaries where
            # practical. A very long line is split by the same fixed bound.
            while len(pending) >= max(1024, int(read_size)):
                cut = pending.rfind("\n", 0, max(1024, int(read_size)) + 1)
                if cut <= 0:
                    cut = max(1024, int(read_size))
                text = pending[:cut]
                pending = pending[cut:]
                start = char_offset
                char_offset += len(text)
                start_line = line_number
                line_number += text.count("\n")
                yield ParsedUnit(text, start, char_offset, start_line, line_number, "text")
    pending += decoder.decode(b"", final=True)
    if pending:
        start = char_offset
        char_offset += len(pending)
        start_line = line_number
        line_number += pending.count("\n")
        yield ParsedUnit(pending, start, char_offset, start_line, line_number, "text")


class TextParser:
    name = "utf8-text"

    def parse(self, path: Path, *, read_size: int = 65536) -> Iterator[ParsedUnit]:
        yield from _iter_utf8_text(path, read_size=read_size)


class JsonParser(TextParser):
    name = "json-stream"

    def parse(self, path: Path, *, read_size: int = 65536) -> Iterator[ParsedUnit]:
        # ijson validates and walks JSON tokens incrementally. The actual text
        # stream is then exposed unchanged so chunk content remains faithful to
        # the source. This is two bounded passes, not a whole-file json.load().
        try:
            import ijson  # type: ignore
        except ImportError as exc:  # pragma: no cover - packaging guard
            raise ParserError("JSON streaming parser dependency is unavailable") from exc
        try:
            with path.open("rb") as handle:
                for _ in ijson.parse(handle):
                    pass
        except Exception as exc:
            raise ParserError(f"invalid JSON: {type(exc).__name__}") from exc
        yield from _iter_utf8_text(path, read_size=read_size)


class CsvParser:
    name = "csv-stream"

    def parse(self, path: Path, *, read_size: int = 65536) -> Iterator[ParsedUnit]:
        # csv.reader consumes one record at a time. A conservative field limit
        # prevents a malformed single field from becoming an unbounded buffer.
        previous_limit = csv.field_size_limit()
        csv.field_size_limit(max(previous_limit, 8 * 1024 * 1024))
        char_offset = 0
        line_start = 1
        try:
            with path.open("r", encoding="utf-8", errors="strict", newline="") as handle:
                reader = csv.reader(handle)
                for row in reader:
                    text = ",".join(row)
                    start = char_offset
                    char_offset += len(text) + 1
                    end_line = max(line_start, reader.line_num)
                    yield from _bounded_units(
                        text,
                        char_start=start,
                        line_start=line_start,
                        kind="csv-record",
                        limit=read_size,
                    )
                    line_start = end_line + 1
        except UnicodeError as exc:
            raise ParserError(f"invalid UTF-8: {type(exc).__name__}") from exc
        except (csv.Error, OSError) as exc:
            raise ParserError(f"invalid CSV: {type(exc).__name__}") from exc
        finally:
            csv.field_size_limit(previous_limit)


class PdfParser:
    name = "pdf-pages"

    def parse(self, path: Path, *, read_size: int = 65536) -> Iterator[ParsedUnit]:
        reader_cls = None
        try:
            from pypdf import PdfReader  # type: ignore
            reader_cls = PdfReader
        except ImportError:
            try:
                from PyPDF2 import PdfReader  # type: ignore
                reader_cls = PdfReader
            except ImportError as exc:  # pragma: no cover - packaging guard
                raise ParserError("PDF parser dependency is unavailable") from exc

        try:
            reader = reader_cls(str(path))
            char_offset = 0
            for page_number, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                if not text:
                    continue
                start = char_offset
                char_offset += len(text)
                yield from _bounded_units(
                    text,
                    char_start=start,
                    line_start=page_number,
                    kind="pdf-page",
                    limit=read_size,
                )
        except Exception as exc:
            raise ParserError(f"invalid PDF: {type(exc).__name__}") from exc


@dataclass
class ParserRegistry:
    """Suffix-to-parser registry that can be extended without engine changes."""

    _parsers: dict[str, Parser] = field(default_factory=dict)

    def parser_for(self, path: Path) -> Parser | None:
        return self._parsers.get(path.suffix.casefold())

    def register(self, suffix: str, parser: Parser) -> None:
        normalized = suffix.casefold()
        if not normalized.startswith("."):
            normalized = "." + normalized
        self._parsers[normalized] = parser

    def supported_suffixes(self) -> tuple[str, ...]:
        return tuple(sorted(self._parsers))


def default_parser_registry() -> ParserRegistry:
    text = TextParser()
    registry = ParserRegistry({
        ".txt": text,
        ".md": text,
        ".py": text,
        ".json": JsonParser(),
        ".csv": CsvParser(),
        ".pdf": PdfParser(),
    })
    return registry
