"""Behavior tests: file loss, version substitution, and false success are failures."""
import hashlib
import io
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/download-cited-papers/scripts"
sys.path.insert(0, str(SCRIPTS))
import cited_papers as cp


def pdf_bytes(lines):
    """A real one-page text PDF, requiring no network or fixture PDFs."""
    def escape(s):
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = "BT /F1 12 Tf 50 750 Td " + " ".join(
        ("0 -18 Td " if i else "") + "(" + escape(line) + ") Tj"
        for i, line in enumerate(lines)
    ) + " ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream.encode())).encode() + b" >>\nstream\n" + stream.encode() + b"\nendstream",
    ]
    result = b"%PDF-1.4\n"
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result += str(i).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    start = len(result)
    result += b"xref\n0 6\n0000000000 65535 f \n"
    result += b"".join(("%010d 00000 n \n" % x).encode() for x in offsets[1:])
    result += b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(start).encode() + b"\n%%EOF\n"
    return result


def resolution(**changes):
    row = dict(citation_number=1, title="Reliable Evidence Retrieval", short_title="Evidence-R1",
               authors=["Ada Researcher"], venue="COLM", year=2025,
               publication_status="published", version="publishedVersion",
               pdf_url="https://example.org/paper.pdf", source_url="https://example.org/paper",
               evidence_url="https://example.org/proceedings", notes="Verified official proceedings PDF.")
    row.update(changes)
    return row


def author_at_end_pdf_bytes(metadata):
    """A journal PDF whose authors appear only on its second page."""
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter()
    for lines in (
        ["Reliable Evidence Retrieval",
         "A list of authors and affiliations appears at the end of the paper.",
         "Abstract", "Journal abstract and article text."],
        ["Authors and affiliations", "Ada Researcher"],
    ):
        writer.add_page(PdfReader(io.BytesIO(pdf_bytes(lines))).pages[0])
    writer.add_metadata(metadata)
    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paper = self.root / "main.pdf"
        self.paper.write_bytes(pdf_bytes(["A main paper", "References",
            "[1] Ada Researcher. Reliable Evidence Retrieval. COLM, 2025.",
            "[2] Bob Author. Another Useful Paper. arXiv, 2024."]))
        self.target = self.root / "output with spaces"

    def tearDown(self):
        self.temp.cleanup()

    def init(self):
        return cp.initialize(self.paper, self.target)

    def test_init_extracts_numbered_references_and_writes_csv(self):
        result = self.init()
        self.assertEqual([r["citation_number"] for r in result["references"]], [1, 2])
        self.assertEqual(result["source_sha256"], hashlib.sha256(self.paper.read_bytes()).hexdigest())
        self.assertTrue((self.target / "references.csv").is_file())

    def test_placeholder_target_does_not_create_output(self):
        for raw in ("", "<TARGET_FOLDER>", "${TARGET_FOLDER}", "/tmp/TARGET_FOLDER"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                cp.initialize(self.paper, raw)
        self.assertFalse(self.target.exists())

    def test_same_source_resume_preserves_resolution(self):
        self.init()
        cp.resolve_records(self.target, [resolution()])
        self.assertEqual(self.init()["references"][0]["status"], "ready")

    def test_different_main_paper_cannot_reuse_manifest(self):
        self.init()
        other = self.root / "other.pdf"
        other.write_bytes(pdf_bytes(["Different paper", "References", "[1] Other citation."]))
        with self.assertRaises(ValueError):
            cp.initialize(other, self.target)

    def test_known_published_paper_refuses_preprint_substitution(self):
        self.init()
        with self.assertRaises(ValueError):
            cp.resolve_records(self.target, [resolution(version="submittedVersion")])
        self.assertEqual(cp.load_manifest(self.target)["references"][0]["status"], "unresolved")

    def test_accepted_author_manuscript_is_not_official_final(self):
        self.init()
        with self.assertRaises(ValueError):
            cp.resolve_records(self.target, [resolution(publication_status="accepted", version="acceptedVersion")])

    def test_preprint_needs_explicit_classification_and_evidence(self):
        self.init()
        cp.resolve_records(self.target, [resolution(publication_status="preprint", version="submittedVersion", venue="arXiv")])
        self.assertEqual(cp.load_manifest(self.target)["references"][0]["status"], "ready")

    def test_unknown_and_missing_official_pdf_remain_accounted_for(self):
        self.init()
        cp.resolve_records(self.target, [resolution(publication_status="unknown"),
            resolution(citation_number=2, pdf_url=None)])
        self.assertEqual([r["status"] for r in cp.load_manifest(self.target)["references"]],
                         ["needs_review", "official_pdf_unavailable"])

    def test_invalid_batch_does_not_apply_partial_updates(self):
        self.init()
        before = (self.target / "references.json").read_bytes()
        with self.assertRaises(ValueError):
            cp.resolve_records(self.target, [resolution(), resolution(citation_number=99)])
        self.assertEqual((self.target / "references.json").read_bytes(), before)

    def test_number_and_provenance_cannot_be_overridden(self):
        self.init()
        with self.assertRaises(ValueError):
            cp.resolve_records(self.target, [resolution(raw_citation="An unrelated replacement")])

    def test_path_components_cannot_escape_destination(self):
        name = cp.folder_name(resolution(short_title="../../Escape/Title", venue="../COLM"))
        self.assertNotIn("/", name)
        self.assertNotIn("..", name)
        self.assertTrue(name.startswith("1_"))

    def test_download_validates_identity_and_resume_skips_network(self):
        self.init()
        cp.resolve_records(self.target, [resolution()])
        body = pdf_bytes(["Reliable Evidence Retrieval", "Ada Researcher", "Full paper content"])
        def transfer(url, destination):
            destination.write_bytes(body)
            return url
        with patch.object(cp, "transfer_pdf", side_effect=transfer):
            cp.download(self.target, only=[1])
        saved = self.target / "1_Evidence-R1_COLM_2025/paper.pdf"
        self.assertEqual(saved.read_bytes(), body)
        self.assertEqual(cp.load_manifest(self.target)["references"][0]["status"], "downloaded")
        self.assertEqual(cp.load_manifest(self.target)["references"][0]["identity_check"],
                         "first_page_title_and_byline")
        self.assertTrue(saved.with_name("metadata.json").is_file())
        with patch.object(cp, "transfer_pdf", side_effect=AssertionError("Resume used network")):
            cp.download(self.target, only=[1])

    def test_different_pdf_does_not_become_success(self):
        self.init()
        cp.resolve_records(self.target, [resolution()])
        def transfer(url, destination):
            destination.write_bytes(pdf_bytes(["Unrelated Mathematics", "Bob Author"]))
            return url
        with patch.object(cp, "transfer_pdf", side_effect=transfer):
            cp.download(self.target, only=[1])
        row = cp.load_manifest(self.target)["references"][0]
        self.assertEqual(row["status"], "failed")
        self.assertFalse((self.target / row["folder"] / "paper.pdf").exists())

    def test_mention_of_expected_paper_in_abstract_is_not_identity(self):
        self.init()
        cp.resolve_records(self.target, [resolution()])
        def transfer(url, destination):
            destination.write_bytes(pdf_bytes(["Completely Different Study", "Bob Author", "Abstract",
                "We compare with Reliable Evidence Retrieval by Ada Researcher."]))
            return url
        with patch.object(cp, "transfer_pdf", side_effect=transfer):
            cp.download(self.target, only=[1])
        self.assertEqual(cp.load_manifest(self.target)["references"][0]["status"], "failed")
        self.assertFalse(list(self.target.rglob("paper.pdf")))

    def test_author_at_end_journal_uses_matching_embedded_title_and_full_author(self):
        paper = self.root / "journal.pdf"
        paper.write_bytes(author_at_end_pdf_bytes({
            "/Title": "RELIABLE evidence: retrieval", "/Author": "ADA RESEARCHER"}))
        try:
            result = cp.validate_pdf(paper, resolution(authors=["Bob Author", "Ada Researcher"]))
        except ValueError as error:
            self.fail("Matching journal metadata was rejected: " + str(error))
        self.assertEqual(result, {
            "page_count": 2,
            "identity_check": "first_page_title_and_embedded_title_author",
        })

    def test_author_at_end_journal_rejects_missing_or_mismatched_metadata(self):
        paper = self.root / "journal.pdf"
        for metadata in (
            {},
            {"/Title": "Reliable Evidence Retrieval"},
            {"/Author": "Ada Researcher"},
            {"/Title": "Different Journal Article", "/Author": "Ada Researcher"},
            {"/Title": "Reliable Evidence Retrieval", "/Author": "Bob Author"},
            {"/Title": "Reliable Evidence Retrieval", "/Author": "A. Researcher"},
            {"/Title": "Reliable Evidence Retrieval Supplement", "/Author": "Ada Researcher"},
        ):
            with self.subTest(metadata=metadata):
                paper.write_bytes(author_at_end_pdf_bytes(metadata))
                with self.assertRaises(ValueError):
                    cp.validate_pdf(paper, resolution())

    def test_matching_metadata_does_not_replace_first_page_header_title(self):
        from pypdf import PdfReader, PdfWriter
        writer = PdfWriter()
        writer.add_page(PdfReader(io.BytesIO(pdf_bytes([
            "Completely Different Study", "Bob Author", "Abstract",
            "We compare with Reliable Evidence Retrieval by Ada Researcher.",
        ]))).pages[0])
        writer.add_metadata({"/Title": "Reliable Evidence Retrieval", "/Author": "Ada Researcher"})
        paper = self.root / "wrong-journal.pdf"
        with paper.open("wb") as handle:
            writer.write(handle)
        with self.assertRaises(ValueError):
            cp.validate_pdf(paper, resolution())

    def test_metadata_created_during_transfer_is_preserved(self):
        self.init()
        cp.resolve_records(self.target, [resolution()])
        metadata = self.target / "1_Evidence-R1_COLM_2025/metadata.json"
        def transfer(url, destination):
            metadata.write_text("Manual notes created during the request")
            destination.write_bytes(pdf_bytes(["Reliable Evidence Retrieval", "Ada Researcher"]))
            return url
        with patch.object(cp, "transfer_pdf", side_effect=transfer):
            cp.download(self.target, only=[1])
        self.assertEqual(metadata.read_text(), "Manual notes created during the request")
        self.assertEqual(cp.load_manifest(self.target)["references"][0]["status"], "failed")
        cp.download(self.target, only=[1])
        self.assertEqual(metadata.read_text(), "Manual notes created during the request")
        self.assertEqual(cp.load_manifest(self.target)["references"][0]["status"], "failed")

    def test_author_affiliation_numbers_do_not_reject_correct_pdf(self):
        self.init()
        cp.resolve_records(self.target, [resolution()])
        def transfer(url, destination):
            destination.write_bytes(pdf_bytes(["Reliable Evidence Retrieval", "Ada Researcher1*, Bob Author2"]))
            return url
        with patch.object(cp, "transfer_pdf", side_effect=transfer):
            cp.download(self.target, only=[1])
        self.assertEqual(cp.load_manifest(self.target)["references"][0]["status"], "downloaded")

    def test_html_download_is_rejected_without_pdf(self):
        self.init()
        cp.resolve_records(self.target, [resolution()])
        def transfer(url, destination):
            destination.write_bytes(b"<html>Log in</html>")
            return url
        with patch.object(cp, "transfer_pdf", side_effect=transfer):
            cp.download(self.target, only=[1])
        self.assertEqual(cp.load_manifest(self.target)["references"][0]["status"], "failed")
        self.assertFalse(list(self.target.rglob("paper.pdf")))

    def test_manual_pdf_is_preserved(self):
        self.init()
        cp.resolve_records(self.target, [resolution()])
        folder = self.target / "1_Evidence-R1_COLM_2025"
        folder.mkdir()
        original = b"manual file must be preserved"
        (folder / "paper.pdf").write_bytes(original)
        with patch.object(cp, "transfer_pdf", side_effect=AssertionError("Must not download")):
            cp.download(self.target, only=[1])
        self.assertEqual((folder / "paper.pdf").read_bytes(), original)
        self.assertEqual(cp.load_manifest(self.target)["references"][0]["status"], "failed")

    def test_symlinked_subfolder_is_rejected(self):
        self.init()
        cp.resolve_records(self.target, [resolution()])
        outside = self.root / "outside"
        outside.mkdir()
        (self.target / "1_Evidence-R1_COLM_2025").symlink_to(outside, target_is_directory=True)
        cp.download(self.target, only=[1])
        self.assertEqual(cp.load_manifest(self.target)["references"][0]["status"], "failed")
        self.assertEqual(list(outside.iterdir()), [])

    def test_reresolve_cannot_relabel_an_existing_download(self):
        self.init()
        cp.resolve_records(self.target, [resolution()])
        def transfer(url, destination):
            destination.write_bytes(pdf_bytes(["Reliable Evidence Retrieval", "Ada Researcher"]))
            return url
        with patch.object(cp, "transfer_pdf", side_effect=transfer):
            cp.download(self.target, only=[1])
        with self.assertRaises(ValueError):
            cp.resolve_records(self.target, [resolution(title="Different Paper")])

    def save_download(self):
        self.init()
        cp.resolve_records(self.target, [resolution()])
        def transfer(url, destination):
            destination.write_bytes(pdf_bytes(["Reliable Evidence Retrieval", "Ada Researcher"]))
            return url
        with patch.object(cp, "transfer_pdf", side_effect=transfer):
            cp.download(self.target, only=[1])
        return self.target / "1_Evidence-R1_COLM_2025/paper.pdf"

    def test_changed_download_is_preserved_and_not_reported_success(self):
        saved = self.save_download()
        saved.write_bytes(b"Manually replaced PDF")
        with patch.object(cp, "transfer_pdf", side_effect=AssertionError("Must preserve changed file")):
            cp.download(self.target, only=[1])
        self.assertEqual(saved.read_bytes(), b"Manually replaced PDF")
        self.assertEqual(cp.load_manifest(self.target)["references"][0]["status"], "failed")

    def test_resume_restores_missing_generated_metadata(self):
        saved = self.save_download()
        saved.with_name("metadata.json").unlink()
        with patch.object(cp, "transfer_pdf", side_effect=AssertionError("Must resume locally")):
            cp.download(self.target, only=[1])
        self.assertEqual(json.loads(saved.with_name("metadata.json").read_text())["title"], "Reliable Evidence Retrieval")

    def test_corrupt_manifest_has_clear_error(self):
        self.init()
        manifest = json.loads((self.target / "references.json").read_text())
        del manifest["references"][0]["status"]
        (self.target / "references.json").write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            cp.load_manifest(self.target)

    def test_unknown_selection_cannot_mutate_collection(self):
        self.init()
        before = (self.target / "references.json").read_bytes()
        with self.assertRaises(ValueError):
            cp.download(self.target, only=[999])
        self.assertEqual((self.target / "references.json").read_bytes(), before)

    def local_pdf(self, lines=None):
        source = self.root / "My Downloads" / "arbitrary filename.pdf"
        source.parent.mkdir(exist_ok=True)
        source.write_bytes(pdf_bytes(lines or ["Reliable Evidence Retrieval", "Ada Researcher", "Abstract", "Full paper"]))
        return source

    def test_import_organizes_unavailable_paper_without_network_or_moving_original(self):
        self.init()
        cp.resolve_records(self.target, [resolution(pdf_url=None)])
        source = self.local_pdf()
        original = source.read_bytes()
        with patch.object(cp, "transfer_pdf", side_effect=AssertionError("Local import used network")):
            cp.import_pdf(self.target, 1, source, "publishedVersion")
        row = cp.load_manifest(self.target)["references"][0]
        saved = self.target / "1_Evidence-R1_COLM_2025/paper.pdf"
        self.assertEqual(row["status"], "downloaded")
        self.assertEqual(row["acquisition_method"], "manual_import")
        self.assertEqual(row["manual_source_path"], str(source.resolve()))
        self.assertTrue(row["imported_at"])
        self.assertEqual(saved.read_bytes(), original)
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(json.loads(saved.with_name("metadata.json").read_text())["sha256"], row["sha256"])
        self.assertIn("downloaded", (self.target / "references.csv").read_text())

    def test_manual_import_same_file_is_idempotent(self):
        self.init()
        cp.resolve_records(self.target, [resolution(pdf_url=None)])
        source = self.local_pdf()
        cp.import_pdf(self.target, 1, source, "publishedVersion")
        before = (self.target / "references.json").read_bytes()
        cp.import_pdf(self.target, 1, source, "publishedVersion")
        self.assertEqual((self.target / "references.json").read_bytes(), before)
        self.assertEqual(len(list(self.target.rglob("paper.pdf"))), 1)

    def test_fetch_preserves_success_of_local_import_without_pdf_url(self):
        self.init()
        cp.resolve_records(self.target, [resolution(pdf_url=None)])
        cp.import_pdf(self.target, 1, self.local_pdf(), "publishedVersion")
        with patch.object(cp, "transfer_pdf", side_effect=AssertionError("Manual resume used network")):
            cp.download(self.target, only=[1])
        self.assertEqual(cp.load_manifest(self.target)["references"][0]["status"], "downloaded")
        self.assertEqual(cp.summarize(cp.load_manifest(self.target))["manual_downloads"], [])

    def test_missing_manual_pdf_can_be_restored_using_matching_generated_metadata(self):
        self.init()
        cp.resolve_records(self.target, [resolution(pdf_url=None)])
        source = self.local_pdf()
        cp.import_pdf(self.target, 1, source, "publishedVersion")
        saved = self.target / "1_Evidence-R1_COLM_2025/paper.pdf"
        metadata_before = saved.with_name("metadata.json").read_bytes()
        saved.unlink()
        cp.download(self.target, only=[1])
        self.assertEqual(cp.load_manifest(self.target)["references"][0]["status"], "failed")
        cp.import_pdf(self.target, 1, source, "publishedVersion")
        self.assertEqual(saved.read_bytes(), source.read_bytes())
        self.assertEqual(saved.with_name("metadata.json").read_bytes(), metadata_before)
        self.assertEqual(cp.load_manifest(self.target)["references"][0]["status"], "downloaded")

    def test_manual_import_preflights_index_conflicts_before_creating_files(self):
        self.init()
        cp.resolve_records(self.target, [resolution(pdf_url=None)])
        source = self.local_pdf()
        original = (self.target / "references.json").read_bytes()
        other = self.root / "User Notes.txt"
        other.write_text("Preserve these user notes")
        index = self.target / "references.csv"
        index.unlink()
        index.symlink_to(other)
        with self.assertRaises(ValueError):
            cp.import_pdf(self.target, 1, source, "publishedVersion")
        self.assertEqual(other.read_text(), "Preserve these user notes")
        self.assertEqual((self.target / "references.json").read_bytes(), original)
        self.assertFalse(list(self.target.rglob("paper.pdf")))
        self.assertFalse(list(self.target.rglob("metadata.json")))
        index.unlink()
        cp.import_pdf(self.target, 1, source, "publishedVersion")
        self.assertEqual(cp.load_manifest(self.target)["references"][0]["status"], "downloaded")

    def test_manual_import_save_failure_rolls_back_owned_files_and_allows_retry(self):
        self.init()
        cp.resolve_records(self.target, [resolution(pdf_url=None)])
        source = self.local_pdf()
        original = (self.target / "references.json").read_bytes()
        with patch.object(cp, "save_manifest", side_effect=OSError("Simulated write failure")), self.assertRaises(OSError):
            cp.import_pdf(self.target, 1, source, "publishedVersion")
        self.assertEqual((self.target / "references.json").read_bytes(), original)
        self.assertFalse(list(self.target.rglob("paper.pdf")))
        self.assertFalse(list(self.target.rglob("metadata.json")))
        self.assertTrue(source.is_file())
        cp.import_pdf(self.target, 1, source, "publishedVersion")
        self.assertEqual(cp.load_manifest(self.target)["references"][0]["status"], "downloaded")

    def test_manual_preprint_import_requires_preprint_classification(self):
        self.init()
        cp.resolve_records(self.target, [resolution(pdf_url=None, publication_status="preprint", version="submittedVersion", venue="arXiv")])
        cp.import_pdf(self.target, 1, self.local_pdf(), "submittedVersion")
        row = cp.load_manifest(self.target)["references"][0]
        self.assertEqual(row["status"], "downloaded")
        self.assertEqual(row["folder"], "1_Evidence-R1_arXiv_2025")

    def test_manual_import_rejects_nonfinal_versions_for_accepted_paper(self):
        self.init()
        cp.resolve_records(self.target, [resolution(pdf_url=None, publication_status="accepted")])
        source = self.local_pdf()
        before = (self.target / "references.json").read_bytes()
        for version in ("submittedVersion", "acceptedVersion", "unknown"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                cp.import_pdf(self.target, 1, source, version)
        self.assertEqual((self.target / "references.json").read_bytes(), before)
        self.assertFalse(list(self.target.rglob("paper.pdf")))

    def test_wrong_local_paper_does_not_change_manifest(self):
        self.init()
        cp.resolve_records(self.target, [resolution(pdf_url=None)])
        before = (self.target / "references.json").read_bytes()
        with self.assertRaises(ValueError):
            cp.import_pdf(self.target, 1, self.local_pdf(["Different Paper", "Other Author"]), "publishedVersion")
        self.assertEqual((self.target / "references.json").read_bytes(), before)
        self.assertFalse(list(self.target.rglob("paper.pdf")))

    def test_manual_import_requires_known_citation_and_resolved_metadata(self):
        self.init()
        source = self.local_pdf()
        before = (self.target / "references.json").read_bytes()
        for number in (1, 99, True):
            with self.subTest(number=number), self.assertRaises(ValueError):
                cp.import_pdf(self.target, number, source, "publishedVersion")
        self.assertEqual((self.target / "references.json").read_bytes(), before)

    def test_manual_import_missing_file_preserves_collection(self):
        self.init()
        cp.resolve_records(self.target, [resolution(pdf_url=None)])
        before = (self.target / "references.json").read_bytes()
        with self.assertRaises(ValueError):
            cp.import_pdf(self.target, 1, self.root / "missing.pdf", "publishedVersion")
        self.assertEqual((self.target / "references.json").read_bytes(), before)

    def test_manual_import_preserves_conflicting_pdf_and_metadata(self):
        self.init()
        cp.resolve_records(self.target, [resolution(pdf_url=None)])
        source = self.local_pdf()
        folder = self.target / "1_Evidence-R1_COLM_2025"
        folder.mkdir()
        before = (self.target / "references.json").read_bytes()
        for filename in ("paper.pdf", "metadata.json"):
            existing = folder / filename
            existing.write_bytes(b"User-created file")
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                cp.import_pdf(self.target, 1, source, "publishedVersion")
            self.assertEqual(existing.read_bytes(), b"User-created file")
            self.assertEqual((self.target / "references.json").read_bytes(), before)
            existing.unlink()

    def test_report_requests_manual_paths_for_failed_and_unavailable_papers(self):
        self.init()
        cp.resolve_records(self.target, [resolution(pdf_url=None), resolution(citation_number=2)])
        with patch.object(cp, "transfer_pdf", side_effect=ValueError("HTTP 403")):
            cp.download(self.target, only=[2])
        summary = cp.summarize(cp.load_manifest(self.target))
        self.assertEqual([r["citation_number"] for r in summary["manual_downloads"]], [1, 2])
        self.assertEqual(summary["manual_downloads"][0]["required_version"], "publishedVersion")
        self.assertEqual(summary["manual_downloads"][1]["reason"], "HTTP 403")
        self.assertEqual(summary["manual_downloads"][0]["source_url"], "https://example.org/paper")
        cp.import_pdf(self.target, 1, self.local_pdf(), "publishedVersion")
        self.assertEqual([r["citation_number"] for r in cp.summarize(cp.load_manifest(self.target))["manual_downloads"]], [2])

    def test_manual_import_cli_requires_explicit_version_then_imports(self):
        self.init()
        cp.resolve_records(self.target, [resolution(pdf_url=None)])
        command = [sys.executable, str(SCRIPTS / "cited_papers.py"), "import-pdf", "--target", str(self.target),
                   "--citation", "1", "--file", str(self.local_pdf())]
        missing_version = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(missing_version.returncode, 2)
        result = subprocess.run(command + ["--version", "publishedVersion"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["statuses"]["downloaded"], 1)


class TransferTests(unittest.TestCase):
    class Response(io.BytesIO):
        def geturl(self):
            return "https://example.org/final.pdf"

    def test_http_transfer_writes_bytes_and_records_redirect(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "test.part"
            with patch.object(cp, "urlopen", return_value=self.Response(b"%PDF-test")):
                result = cp.transfer_pdf("https://example.org/paper.pdf", destination)
            self.assertEqual(destination.read_bytes(), b"%PDF-test")
            self.assertEqual(result, "https://example.org/final.pdf")

    def test_rate_limit_is_retried_and_eventually_saved(self):
        error = HTTPError("https://example.org/paper.pdf", 429, "Rate limit", {}, None)
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "test.part"
            with patch.object(cp, "urlopen", side_effect=[error, self.Response(b"%PDF-success")]), patch.object(cp.time, "sleep"):
                cp.transfer_pdf("https://example.org/paper.pdf", destination)
            self.assertEqual(destination.read_bytes(), b"%PDF-success")

    def test_403_stops_and_does_not_substitute_source(self):
        error = HTTPError("https://example.org/paper.pdf", 403, "Forbidden", {}, None)
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "test.part"
            with patch.object(cp, "urlopen", side_effect=error), self.assertRaisesRegex(ValueError, "HTTP 403"):
                cp.transfer_pdf("https://example.org/paper.pdf", destination)
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
