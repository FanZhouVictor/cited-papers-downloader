#!/usr/bin/env python3
"""Resumable storage for a Codex-assisted, numbered citation collection."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from reference_extract import extract_pdf

SCHEMA_VERSION = 1
MAX_PDF_BYTES = 100 * 1024 * 1024
RESOLUTION_FIELDS = {
    "citation_number", "title", "short_title", "authors", "venue", "year",
    "publication_status", "version", "pdf_url", "source_url", "evidence_url", "notes",
}
PUBLICATION_STATES = {"published", "accepted", "preprint", "unknown", "nonpaper"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def target_path(value):
    raw = os.fspath(value).strip()
    if (not raw or any(token in raw for token in ("<", ">", "${", "TARGET_FOLDER", "MAIN_PDF"))):
        raise ValueError("Supply an actual destination folder, not an empty value or example placeholder.")
    target = Path(raw).expanduser().resolve()
    if target == Path(target.anchor):
        raise ValueError("The filesystem root cannot be a destination folder.")
    if target.exists() and not target.is_dir():
        raise ValueError("Destination is not a directory.")
    return target


@contextmanager
def project_lock(target):
    lock = target / ".cited-papers.lock"
    try:
        descriptor = os.open(str(lock), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ValueError("Collection is locked. Wait for the other command; remove a stale lock only after checking no command is running.")
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(str(os.getpid()))
        yield
    finally:
        lock.unlink(missing_ok=True)


def atomic_text(path, content, replace=True):
    if path.is_symlink():
        raise ValueError("Refusing to replace a symlink: " + path.name)
    descriptor, name = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if replace:
            os.replace(name, path)
        else:
            os.link(name, str(path))
    finally:
        Path(name).unlink(missing_ok=True)


def save_manifest(target, manifest):
    import io
    manifest["updated_at"] = now()
    # Guard both reserved paths before mutating either one.
    for name in ("references.json", "references.csv"):
        if (target / name).is_symlink():
            raise ValueError("Collection indexes must not be symlinks.")
    atomic_text(target / "references.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    output = io.StringIO(newline="")
    fields = ["citation_number", "status", "title", "folder", "venue", "year", "publication_status",
              "version", "pdf_url", "source_url", "evidence_url", "error", "raw_citation"]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in manifest["references"]:
        # Avoid interpreting downloaded metadata as formulas when opened in a spreadsheet.
        writer.writerow({key: ("'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@")) else value)
                         for key, value in row.items() if key in fields})
    atomic_text(target / "references.csv", output.getvalue())


def load_manifest(target):
    target = target_path(target)
    path = target / "references.json"
    if path.is_symlink():
        raise ValueError("Collection manifest must not be a symlink.")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError("No collection manifest. Run init with an explicit main PDF and target first.")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("Existing references.json is not a valid collection manifest; it was preserved.")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported collection manifest; existing files were preserved.")
    rows = manifest.get("references")
    fingerprint = manifest.get("source_sha256")
    if (not isinstance(rows, list) or not rows or not isinstance(fingerprint, str)
            or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)):
        raise ValueError("Invalid source or reference list in collection manifest.")
    numbers = [row.get("citation_number") for row in rows if isinstance(row, dict)]
    if len(numbers) != len(rows) or numbers != list(range(1, len(rows) + 1)):
        raise ValueError("Manifest reference numbers must be unique and contiguous from 1.")
    statuses = {"unresolved", "ready", "downloaded", "needs_review", "official_pdf_unavailable", "pdf_unavailable", "nonpaper", "failed"}
    for row in rows:
        if (type(row["citation_number"]) is not int or row.get("status") not in statuses
                or not isinstance(row.get("raw_citation"), str) or not row["raw_citation"].strip()):
            raise ValueError("Invalid reference data in collection manifest.")
    return manifest


def initialize(paper, target):
    target = target_path(target)
    paper = Path(paper).expanduser().resolve()
    if not paper.is_file():
        raise ValueError("Main PDF does not exist: " + str(paper))
    source_hash = sha256(paper)
    if (target / "references.json").exists() or (target / "references.json").is_symlink():
        existing = load_manifest(target)
        if existing["source_sha256"] != source_hash:
            raise ValueError("This target belongs to a different main PDF. Choose another folder.")
        return existing
    if (target / "references.csv").exists() or (target / "references.csv").is_symlink():
        raise ValueError("Existing references.csv has no matching manifest; it was preserved.")
    references = extract_pdf(paper)
    manifest = {"schema_version": SCHEMA_VERSION, "source_filename": paper.name,
                "source_sha256": source_hash, "created_at": now(),
                "references": [dict(row, status="unresolved") for row in references]}
    target.mkdir(parents=True, exist_ok=True)
    with project_lock(target):
        # Recheck after acquiring the lock so simultaneous init cannot replace state.
        if (target / "references.json").exists():
            raise ValueError("Another command initialized this target; rerun to resume.")
        save_manifest(target, manifest)
    return manifest


def safe_component(value, fallback="Paper", limit=80):
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9-]+", "-", text).strip("-")[:limit].rstrip("-")
    return text or fallback


def folder_name(row):
    number = row["citation_number"]
    if type(number) is not int or number < 1:
        raise ValueError("Citation number must be a positive integer.")
    return "{}_{}_{}_{}".format(number, safe_component(row["short_title"]),
                              safe_component(row["venue"], "Unknown", 40), row["year"])


def check_url(value, field):
    if not isinstance(value, str):
        raise ValueError(field + " must be an HTTP(S) URL.")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError(field + " must be an HTTP(S) URL without credentials.")
    return value


def validate_resolution(record, require_pdf_url=True):
    if not isinstance(record, dict) or set(record) - RESOLUTION_FIELDS:
        raise ValueError("Resolution records contain unsupported fields; citation provenance cannot be replaced.")
    row = dict(record)
    if type(row.get("citation_number")) is not int or row["citation_number"] < 1:
        raise ValueError("Each resolution needs a positive integer citation_number.")
    if not isinstance(row.get("title"), str) or not row["title"].strip():
        raise ValueError("Each resolution needs a full title.")
    state = row.get("publication_status")
    if state not in PUBLICATION_STATES:
        raise ValueError("Unknown publication_status.")
    if row.get("pdf_url"):
        check_url(row["pdf_url"], "pdf_url")
    for field in ("source_url", "evidence_url"):
        if row.get(field):
            check_url(row[field], field)
    if state in {"unknown", "nonpaper"}:
        row["status"] = "needs_review" if state == "unknown" else "nonpaper"
        return row
    for field in ("source_url", "evidence_url"):
        check_url(row.get(field), field)
    if require_pdf_url and not row.get("pdf_url"):
        row["status"] = "official_pdf_unavailable" if state in {"published", "accepted"} else "pdf_unavailable"
        return row
    if state in {"published", "accepted"} and row.get("version") != "publishedVersion":
        raise ValueError("Known accepted/published papers require an official final publishedVersion; no preprint or author-manuscript fallback.")
    if state == "preprint" and row.get("version") != "submittedVersion":
        raise ValueError("A preprint download must be labeled submittedVersion.")
    if not isinstance(row.get("authors"), list) or not row["authors"] or not all(isinstance(x, str) and x.strip() for x in row["authors"]):
        raise ValueError("Downloadable records need author names for PDF identity checks.")
    if type(row.get("year")) is not int or not 1500 <= row["year"] <= 2100:
        raise ValueError("Downloadable records need the verified publication year.")
    for field in ("short_title", "venue"):
        if not isinstance(row.get(field), str) or not row[field].strip():
            raise ValueError("Downloadable records need " + field + ".")
    if state == "preprint" and row["venue"] not in {"arXiv", "bioRxiv", "medRxiv", "Preprint", "SSRN", "ResearchSquare"}:
        raise ValueError("Preprints must use a preprint repository name or Preprint as venue.")
    row["folder"] = folder_name(row)
    row["status"] = "ready"
    return row


def resolve_records(target, records):
    target = target_path(target)
    if not isinstance(records, list) or not records:
        raise ValueError("Supply a nonempty JSON array of resolution records.")
    updates = [validate_resolution(row) for row in records]
    if len({r["citation_number"] for r in updates}) != len(updates):
        raise ValueError("Duplicate citation number in resolution batch.")
    with project_lock(target):
        manifest = load_manifest(target)
        rows = {row["citation_number"]: row for row in manifest["references"]}
        for update in updates:
            number = update["citation_number"]
            if number not in rows:
                raise ValueError("Citation number is absent from this main PDF: " + str(number))
            old = rows[number]
            if old.get("sha256"):
                differing = [key for key in RESOLUTION_FIELDS if key in update and update.get(key) != old.get(key)]
                if differing:
                    raise ValueError("Downloaded reference cannot be relabeled. Preserve it and use a fresh collection for a different version.")
        for update in updates:
            old = rows[update["citation_number"]]
            if not old.get("sha256"):
                preserved = {key: old[key] for key in ("citation_number", "raw_citation")}
                old.clear()
                old.update(preserved)
                old.update(update)
        save_manifest(target, manifest)
    return manifest


def transfer_pdf(url, destination):
    check_url(url, "pdf_url")
    for attempt in range(3):
        try:
            request = Request(url, headers={"User-Agent": "cited-papers-downloader/1.0 (personal research)", "Accept": "application/pdf"})
            with urlopen(request, timeout=30) as response, Path(destination).open("wb") as handle:
                final_url = check_url(response.geturl(), "redirect URL")
                total = 0
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_PDF_BYTES:
                        raise ValueError("PDF exceeds the 100 MiB download limit.")
                    handle.write(chunk)
                return final_url
        except HTTPError as error:
            if attempt == 2 or (error.code != 429 and error.code < 500):
                raise ValueError("PDF request returned HTTP " + str(error.code))
        except (URLError, TimeoutError, OSError) as error:
            if attempt == 2:
                raise ValueError("PDF request failed: " + str(error))
        time.sleep(attempt + 1)


def normalized_words(text):
    return re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower())


def validate_pdf(path, record):
    from pypdf import PdfReader
    with Path(path).open("rb") as handle:
        if not handle.read(8).startswith(b"%PDF-"):
            raise ValueError("Response is not a PDF (possibly a login or error page).")
    reader = PdfReader(str(path))
    if reader.is_encrypted or not reader.pages:
        raise ValueError("PDF must be readable and unencrypted.")
    text = reader.pages[0].extract_text() or ""
    header = re.split(r"(?im)^\s*(?:abstract\b|(?:1[.\s]+)?introduction\b)", text, maxsplit=1)[0][:2500]
    compact_header = "".join(normalized_words(header))
    compact_title = "".join(normalized_words(record["title"]))
    title_start = compact_header.find(compact_title) if compact_title else -1
    if title_start < 0:
        raise ValueError("Downloaded PDF first-page title does not match the resolved paper.")
    # Compare ordered full names in the byline, before abstract/body citations.
    # Whitespace removal tolerates wrapped names and flattened superscript digits.
    byline = compact_header[title_start + len(compact_title):]
    author_names = ["".join(normalized_words(author)) for author in record["authors"]]
    identity_check = "first_page_title_and_byline"
    if not any(author and author in byline for author in author_names):
        # Some journals print their author list at the end of the article.
        # Require both embedded fields to corroborate the already matched header title.
        metadata = reader.metadata
        metadata_title = "".join(normalized_words(metadata.title or "")) if metadata else ""
        metadata_author = "".join(normalized_words(metadata.author or "")) if metadata else ""
        if metadata_title != compact_title or not any(
                author and author == metadata_author for author in author_names):
            raise ValueError("Downloaded PDF authors do not match the resolved paper.")
        identity_check = "first_page_title_and_embedded_title_author"
    return {"page_count": len(reader.pages), "identity_check": identity_check}


def download(target, only=None):
    target = target_path(target)
    with project_lock(target):
        manifest = load_manifest(target)
        numbers = {row["citation_number"] for row in manifest["references"]}
        if only is not None and (not only or not set(only) <= numbers):
            raise ValueError("--only must contain citation numbers from this main PDF.")
        selected = set(only) if only is not None else numbers
        for row in manifest["references"]:
            if row["citation_number"] not in selected or row["status"] not in {"ready", "failed", "downloaded"}:
                continue
            temporary = None
            try:
                # Reapply policy and folder derivation, even if someone edited the manifest.
                manual_import = row.get("acquisition_method") == "manual_import"
                validated = validate_resolution({k: row[k] for k in RESOLUTION_FIELDS if k in row},
                                                require_pdf_url=not manual_import)
                if validated["status"] != "ready":
                    row["status"] = validated["status"]
                    continue
                folder = target / validated["folder"]
                row["folder"] = validated["folder"]
                if folder.is_symlink() or folder.resolve().parent != target:
                    raise ValueError("Paper folder must be directly inside the destination and not a symlink.")
                folder.mkdir(exist_ok=True)
                destination = folder / "paper.pdf"
                metadata = folder / "metadata.json"
                if destination.is_symlink() or metadata.is_symlink():
                    raise ValueError("Paper files and metadata must not be symlinks.")
                if destination.exists():
                    if not row.get("sha256"):
                        raise ValueError("Existing paper.pdf has no matching ownership record; preserved for manual review.")
                    if sha256(destination) != row["sha256"]:
                        raise ValueError("Existing PDF checksum changed; preserved for manual review.")
                    if metadata.exists():
                        try:
                            saved_metadata = json.loads(metadata.read_text(encoding="utf-8"))
                        except (ValueError, UnicodeError):
                            raise ValueError("Existing metadata.json is not a matching generated record; preserved for manual review.")
                        if (not isinstance(saved_metadata, dict) or saved_metadata.get("sha256") != row["sha256"]
                                or saved_metadata.get("citation_number") != row["citation_number"]):
                            raise ValueError("Existing metadata.json does not match this PDF; preserved for manual review.")
                    row["status"] = "downloaded"
                    row.pop("error", None)
                    if not metadata.exists():
                        atomic_text(metadata, json.dumps(row, indent=2, ensure_ascii=False) + "\n", replace=False)
                    continue
                if manual_import:
                    raise ValueError("Manually imported PDF is missing; provide its local path with import-pdf to restore it.")
                if metadata.exists():
                    raise ValueError("Existing metadata.json was preserved; inspect the incomplete paper folder before retrying.")
                descriptor, name = tempfile.mkstemp(prefix=".paper-", suffix=".part", dir=str(folder))
                os.close(descriptor)
                temporary = Path(name)
                final_url = transfer_pdf(row["pdf_url"], temporary)
                inspection = validate_pdf(temporary, row)
                result = dict(row, **inspection, sha256=sha256(temporary), downloaded_at=now(),
                              final_pdf_url=final_url, status="downloaded")
                result.pop("error", None)
                # Hard-link creation refuses to replace a file created during the request.
                os.link(str(temporary), str(destination))
                row.clear()
                row.update(result)
                atomic_text(metadata, json.dumps(result, indent=2, ensure_ascii=False) + "\n", replace=False)
            except Exception as error:
                row["status"] = "failed"
                row["error"] = str(error)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
                save_manifest(target, manifest)
    return manifest


def import_pdf(target, citation, file_path, version):
    """Copy a verified local PDF into its resolved citation folder, offline."""
    target = target_path(target)
    if type(citation) is not int or citation < 1:
        raise ValueError("Supply a positive integer citation number from the main PDF.")
    raw = os.fspath(file_path).strip()
    if not raw or any(token in raw for token in ("<", ">", "${")):
        raise ValueError("Supply the actual local PDF path, not a placeholder.")
    source = Path(raw).expanduser().resolve()
    if not source.is_file():
        raise ValueError("Local PDF does not exist or is not a regular file: " + str(source))
    if source.stat().st_size > MAX_PDF_BYTES:
        raise ValueError("Local PDF exceeds the 100 MiB size limit.")
    with project_lock(target):
        for name in ("references.json", "references.csv"):
            index = target / name
            if index.is_symlink() or (index.exists() and not index.is_file()):
                raise ValueError("Collection indexes must be regular files, not symlinks or directories.")
        original_manifest = (target / "references.json").read_bytes()
        manifest = load_manifest(target)
        row = next((r for r in manifest["references"] if r["citation_number"] == citation), None)
        if row is None:
            raise ValueError("Citation number is absent from this main PDF: " + str(citation))
        if row.get("publication_status") not in {"published", "accepted", "preprint"}:
            raise ValueError("Resolve this paper's identity and publication status before importing a PDF.")
        record = {key: row[key] for key in RESOLUTION_FIELDS if key in row}
        record["version"] = version
        verified = validate_resolution(record, require_pdf_url=False)
        try:
            validate_pdf(source, verified)
        except Exception as error:
            raise ValueError("Local PDF validation failed: " + str(error)) from error
        folder = target / verified["folder"]
        if folder.is_symlink() or folder.resolve().parent != target:
            raise ValueError("Paper folder must be directly inside the destination and not a symlink.")
        destination = folder / "paper.pdf"
        metadata = folder / "metadata.json"
        if destination.is_symlink() or metadata.is_symlink():
            raise ValueError("Paper files and metadata must not be symlinks.")
        if destination.exists():
            digest = sha256(source)
            if (not row.get("sha256") or row["sha256"] != digest or sha256(destination) != digest
                    or row.get("version") != version):
                raise ValueError("Existing paper.pdf conflicts with this import; it was preserved for manual review.")
            if metadata.exists():
                try:
                    saved = json.loads(metadata.read_text(encoding="utf-8"))
                except (ValueError, UnicodeError):
                    raise ValueError("Existing metadata.json was preserved for manual review.")
                if (not isinstance(saved, dict) or saved.get("sha256") != digest
                        or saved.get("citation_number") != citation):
                    raise ValueError("Existing metadata.json conflicts with this import; it was preserved.")
            elif row["status"] == "downloaded":
                atomic_text(metadata, json.dumps(row, indent=2, ensure_ascii=False) + "\n", replace=False)
            if row["status"] != "downloaded":
                raise ValueError("Existing incomplete paper folder was preserved; inspect it before importing again.")
            return manifest
        restoring = None
        if metadata.exists():
            try:
                saved = json.loads(metadata.read_text(encoding="utf-8"))
            except (ValueError, UnicodeError):
                raise ValueError("Existing metadata.json was preserved; inspect the paper folder before importing.")
            if (not isinstance(saved, dict) or not row.get("sha256") or sha256(source) != row["sha256"]
                    or saved.get("sha256") != row["sha256"] or saved.get("citation_number") != citation
                    or saved.get("acquisition_method") != "manual_import"
                    or saved.get("version") != version or saved.get("title") != verified["title"]):
                raise ValueError("Existing metadata.json was preserved; inspect the paper folder before importing.")
            restoring = saved
        folder.mkdir(exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".manual-", suffix=".part", dir=str(folder))
        os.close(descriptor)
        temporary = Path(name)
        created_metadata = False
        created_pdf = False
        content = None
        try:
            shutil.copyfile(str(source), str(temporary))
            if temporary.stat().st_size > MAX_PDF_BYTES:
                raise ValueError("Local PDF exceeds the 100 MiB size limit.")
            inspection = validate_pdf(temporary, verified)
            result = dict(restoring or row, **inspection)
            result.update(version=version, folder=verified["folder"], status="downloaded", sha256=sha256(temporary))
            if not restoring:
                result.update(acquisition_method="manual_import", manual_source_path=str(source), imported_at=now())
            result.pop("error", None)
            content = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
            if not restoring:
                atomic_text(metadata, content, replace=False)
                created_metadata = True
            os.link(str(temporary), str(destination))
            created_pdf = True
            row.clear()
            row.update(result)
            save_manifest(target, manifest)
        except Exception:
            # If the manifest did not commit, remove only files this import owns.
            # A committed manifest with a later CSV error retains a consistent PDF.
            if (target / "references.json").read_bytes() == original_manifest:
                if created_pdf and not destination.is_symlink() and destination.exists() and destination.samefile(temporary):
                    destination.unlink()
                if created_metadata and not metadata.is_symlink() and metadata.exists() and metadata.read_text(encoding="utf-8") == content:
                    metadata.unlink()
            raise
        finally:
            temporary.unlink(missing_ok=True)
    return manifest


def summarize(manifest):
    counts = dict(sorted(Counter(row["status"] for row in manifest["references"]).items()))
    complete = all(row["status"] in {"downloaded", "nonpaper"} for row in manifest["references"])
    manual = []
    for row in manifest["references"]:
        if (row["status"] in {"failed", "official_pdf_unavailable", "pdf_unavailable"}
                and row.get("publication_status") in {"published", "accepted", "preprint"}):
            manual.append({"citation_number": row["citation_number"], "title": row.get("title", row["raw_citation"]),
                           "source_url": row.get("source_url") or row.get("evidence_url") or row.get("pdf_url"),
                           "reason": row.get("error") or row.get("notes") or "Required PDF is unavailable for automatic download.",
                           "required_version": "submittedVersion" if row["publication_status"] == "preprint" else "publishedVersion"})
    return {"references": len(manifest["references"]), "complete": complete, "statuses": counts,
            "manual_downloads": manual}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("init", "resolve", "fetch", "report", "import-pdf"):
        child = sub.add_parser(command)
        child.add_argument("--target", required=True, help="Actual destination folder; no default.")
        if command == "init":
            child.add_argument("--paper", required=True, help="Main PDF whose reference numbers to preserve.")
        elif command == "resolve":
            child.add_argument("--records", required=True, help="JSON array of verified resolution records.")
        elif command == "fetch":
            child.add_argument("--only", type=int, nargs="+", help="Fetch only these reference numbers.")
        elif command == "import-pdf":
            child.add_argument("--citation", type=int, required=True, help="Citation number in the existing collection.")
            child.add_argument("--file", required=True, help="Local manually downloaded PDF; the original is preserved.")
            child.add_argument("--version", required=True, choices=["publishedVersion", "acceptedVersion", "submittedVersion"],
                               help="Verified version of the local PDF; existing publication rules still apply.")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            manifest = initialize(args.paper, args.target)
        elif args.command == "resolve":
            records = json.loads(Path(args.records).expanduser().read_text(encoding="utf-8"))
            manifest = resolve_records(args.target, records)
        elif args.command == "fetch":
            manifest = download(args.target, args.only)
        elif args.command == "import-pdf":
            manifest = import_pdf(args.target, args.citation, args.file, args.version)
        else:
            manifest = load_manifest(args.target)
        result = summarize(manifest)
        print(json.dumps(result, indent=2))
        if args.command == "fetch":
            selected = set(args.only) if args.only else None
            return int(any(row["status"] == "failed" for row in manifest["references"]
                           if selected is None or row["citation_number"] in selected))
        return 0
    except (ValueError, OSError, ImportError) as error:
        print("Error: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
