"""Extract explicit, contiguous numbered bibliographies from paper text.

This intentionally rejects unsupported or ambiguous extraction rather than
assigning citation numbers. Whitespace is normalized; citation wording and
punctuation are retained. PDF extraction prefers Poppler's layout output.
"""
from pathlib import Path
import re
import shutil
import subprocess
from typing import List, Dict


_HEADING = re.compile(r"^(?:\d+[.\s]+)?(?:references|bibliography|works cited|literature cited)\s*:?$", re.IGNORECASE)
_APPENDIX = re.compile(r"^(?:(?i:appendix\b|appendices\b)|[A-Z]\.?(?:\s{2,})\S)")
_LABEL = re.compile(r"^\s*\[(\d+)\](?:\s+(.*)|\s*)$")
_INLINE_LABEL = re.compile(r"\[\d+\](?=\s|$)")
_PAGE_NUMBER = re.compile(r"^(?:[-–]\s*)?(?:\d{1,3}|[ivxlcdm]+)(?:\s*[-–])?$", re.IGNORECASE)


def _reading_order(lines: List[str]) -> List[str]:
    """Unroll two columns when layout text exposes aligned reference labels."""
    label_columns = []
    for line in lines:
        for match in _INLINE_LABEL.finditer(line):
            before = line[:match.start()]
            if not before.strip() or re.search(r"\s{3,}$", before):
                label_columns.append(match.start())
    right_columns = [column for column in label_columns if column >= 24]
    if not right_columns or not any(column <= 8 for column in label_columns):
        return lines
    boundary = min(right_columns)
    # With layout-preserving text, the right-hand labels share the right
    # column's left edge. Continuations are indented relative to that edge.
    return [line[:boundary] for line in lines] + [line[boundary:] for line in lines]


def _bibliography_lines(text: str) -> List[str]:
    pages = []
    started = False
    finished = False
    for page in text.replace("\r\n", "\n").replace("\r", "\n").split("\f"):
        content = []
        for line in page.splitlines():
            stripped = line.strip()
            if _HEADING.fullmatch(stripped):
                started = True
                continue
            if not started:
                continue
            if _APPENDIX.match(stripped):
                finished = True
                break
            content.append(line.expandtabs(8))
        # A bare page number at the beginning/end of a page is furniture;
        # a year inside a citation is left intact.
        nonempty = [index for index, line in enumerate(content) if line.strip()]
        for index in set(nonempty[:1] + nonempty[-1:]):
            if _PAGE_NUMBER.fullmatch(content[index].strip()):
                content[index] = ""
        pages.extend(_reading_order(content))
        if finished:
            break
    if not started:
        raise ValueError("No References/Bibliography heading found; unsupported bibliography extraction")
    return pages


def extract_references(text: str) -> List[Dict]:
    """Return citation_number/raw_citation rows or reject ambiguous numbering.

    Supported bibliographies use a References/Bibliography heading and explicit
    ``[1]`` labels. Labels must start at 1 and occur exactly once, in order.
    This cannot infer a reference lost entirely from the end of extracted text.
    """
    references = []
    current_number = None
    current_parts = []
    seen = set()

    def finish_reference():
        if current_number is None:
            return
        citation = " ".join(current_parts).strip()
        if not citation:
            raise ValueError("Empty citation for reference [%d]" % current_number)
        references.append({"citation_number": current_number, "raw_citation": citation})

    for line in _bibliography_lines(text):
        stripped = line.strip()
        if not stripped:
            continue
        match = _LABEL.fullmatch(line)
        if match:
            finish_reference()
            current_number = int(match.group(1))
            if current_number in seen:
                raise ValueError("Duplicate reference label [%d]" % current_number)
            seen.add(current_number)
            current_parts = [match.group(2).strip()] if match.group(2) else []
        elif current_number is None:
            raise ValueError("Unsupported numbered bibliography: unlabeled text before first reference")
        else:
            current_parts.append(stripped)
    finish_reference()
    if not references:
        raise ValueError("Unsupported bibliography: no numbered [1] reference labels found")
    numbers = [reference["citation_number"] for reference in references]
    # Report the first gap without allocating a range up to an untrusted label.
    for expected, number in enumerate(sorted(seen), 1):
        if number != expected:
            raise ValueError("Missing or invalid reference label: expected %d, found %d" % (expected, number))
    if numbers != list(range(1, len(numbers) + 1)):
        raise ValueError("Reference labels are out of order; cannot determine a reliable reading sequence")
    return references


def extract_pdf(path: Path) -> List[Dict]:
    """Read a PDF without writing extracted text or changing its source file."""
    path = Path(path)
    if not path.is_file():
        raise ValueError("Source PDF does not exist or is not a file: %s" % path)
    executable = shutil.which("pdftotext")
    if executable:
        try:
            result = subprocess.run(
                [executable, "-layout", str(path.resolve()), "-"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                encoding="utf-8",
                errors="replace",
            )
        except (OSError, subprocess.CalledProcessError) as error:
            raise ValueError("Could not extract text from the source PDF with pdftotext: %s" % error) from error
        text = result.stdout
    else:
        try:
            from pypdf import PdfReader
        except ImportError as error:
            raise RuntimeError("PDF extraction requires pdftotext or the pypdf package; install pypdf") from error
        try:
            reader = PdfReader(str(path))
            text = "\f".join(page.extract_text() or "" for page in reader.pages)
        except Exception as error:
            raise ValueError("Could not read the source PDF with pypdf: %s" % error) from error
    return extract_references(text)
