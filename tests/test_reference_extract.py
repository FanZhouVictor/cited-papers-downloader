"""Numbering and reading-order behavior for synthetic bibliographies."""
import importlib
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "download-cited-papers" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def make_pdf(text):
    """Make a tiny text PDF without relying on any PDF-writing package."""
    commands = ["BT /F1 12 Tf 50 750 Td"]
    for index, line in enumerate(text.splitlines()):
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        if index:
            commands.append("0 -16 Td")
        commands.append("(" + escaped + ") Tj")
    commands.append("ET")
    stream = "\n".join(commands).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(str(number).encode("ascii") + b" 0 obj\n" + obj + b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(("%010d 00000 n \n" % offset).encode("ascii"))
    pdf.extend(("trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % xref).encode("ascii"))
    return bytes(pdf)


class ReferenceExtractionTests(unittest.TestCase):
    def setUp(self):
        self.extractor = importlib.import_module("reference_extract")

    def test_wrapped_references_preserve_numbering_and_citation_text(self):
        text = """An introduction cites [7].
References
 [1] Ada Example. Retrieval with
     latent representations. In ExampleConf, 2024.

 [2] Bert Example. Another paper. Journal, 2025.
"""
        self.assertEqual(self.extractor.extract_references(text), [
            {"citation_number": 1, "raw_citation": "Ada Example. Retrieval with latent representations. In ExampleConf, 2024."},
            {"citation_number": 2, "raw_citation": "Bert Example. Another paper. Journal, 2025."},
        ])

    def test_page_numbers_and_repeated_heading_do_not_pollute_citations(self):
        text = "References\n[1] Ada Example. A long\n\n  12\n\fReferences\n     paper. 2024.\n[2] Bert Example. A paper. 2025.\n 13\n"
        self.assertEqual(self.extractor.extract_references(text), [
            {"citation_number": 1, "raw_citation": "Ada Example. A long paper. 2024."},
            {"citation_number": 2, "raw_citation": "Bert Example. A paper. 2025."},
        ])

    def test_appendix_stops_the_bibliography(self):
        for heading in ("Appendix A: More results", "Appendices", "A     Additional experiments"):
            with self.subTest(heading=heading):
                text = "References\n[1] Ada Example. Main paper. 2024.\n\f" + heading + "\nAppendix cites [2].\n[2] This is not a reference.\n"
                self.assertEqual(self.extractor.extract_references(text), [
                    {"citation_number": 1, "raw_citation": "Ada Example. Main paper. 2024."},
                ])

    def test_two_columns_are_read_down_the_left_before_the_right(self):
        text = "References\n" + "\n".join([
            "[1] Ada Example. First title.".ljust(52) + "[3] Chen Example. Third title.",
            "    ExampleConf, 2024.".ljust(52) + "    Example Journal, 2022.",
            "[2] Bert Example. Second title.".ljust(52) + "[4] Dev Example. Fourth title.",
            "    Example Journal, 2023.".ljust(52) + "    ExampleConf, 2021.",
        ])
        self.assertEqual(self.extractor.extract_references(text), [
            {"citation_number": 1, "raw_citation": "Ada Example. First title. ExampleConf, 2024."},
            {"citation_number": 2, "raw_citation": "Bert Example. Second title. Example Journal, 2023."},
            {"citation_number": 3, "raw_citation": "Chen Example. Third title. Example Journal, 2022."},
            {"citation_number": 4, "raw_citation": "Dev Example. Fourth title. ExampleConf, 2021."},
        ])

    def test_wrapped_author_initial_is_not_an_appendix_heading(self):
        text = "References\n[1] Long Author List,\n    A. Smith, and Bert Example. A paper. 2024."
        self.assertEqual(self.extractor.extract_references(text), [
            {"citation_number": 1, "raw_citation": "Long Author List, A. Smith, and Bert Example. A paper. 2024."},
        ])

    def test_large_numbering_gap_has_a_bounded_error_message(self):
        with self.assertRaises(ValueError) as context:
            self.extractor.extract_references("References\n[1] Ada. First.\n[1000] Bert. Unknown.")
        self.assertLess(len(str(context.exception)), 200)
        self.assertIn("2", str(context.exception))

    def test_missing_internal_label_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "[Mm]issing.*2"):
            self.extractor.extract_references("References\n[1] Ada. First. 2024.\n[3] Chen. Third. 2025.")

    def test_numbering_must_start_at_one(self):
        with self.assertRaisesRegex(ValueError, "[Mm]issing.*1"):
            self.extractor.extract_references("References\n[2] Bert. Second. 2025.")

    def test_duplicate_labels_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "[Dd]uplicate.*1"):
            self.extractor.extract_references("References\n[1] Ada. First. 2024.\n[1] Bert. Also first. 2025.")

    def test_reordered_labels_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "order|sequence"):
            self.extractor.extract_references("References\n[1] Ada. First.\n[3] Chen. Third.\n[2] Bert. Second.")

    def test_author_year_bibliography_is_explicitly_unsupported(self):
        with self.assertRaisesRegex(ValueError, "numbered|number|unsupported"):
            self.extractor.extract_references("References\nExample, A. (2024). A paper.\nExample, B. (2025). Another paper.")

    def test_unlabeled_text_before_first_entry_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "label|number|unlabeled"):
            self.extractor.extract_references("References\nAda Example. A paper whose label is lost.\n[1] Bert. A paper. 2025.")

    def test_empty_numbered_reference_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "[Ee]mpty|[Mm]issing.*citation"):
            self.extractor.extract_references("References\n[1]\n[2] Bert. A paper. 2025.")

    @unittest.skipUnless(shutil.which("pdftotext"), "pdftotext is not installed")
    def test_extract_pdf_reads_a_real_synthetic_pdf(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "synthetic.pdf"
            path.write_bytes(make_pdf("References\n[1] Ada Example. Synthetic paper. 2024.\nAppendix A\nThis is supplementary text."))
            self.assertEqual(self.extractor.extract_pdf(path), [
                {"citation_number": 1, "raw_citation": "Ada Example. Synthetic paper. 2024."},
            ])

    def test_pypdf_fallback_extracts_pages_when_pdftotext_is_absent(self):
        class Page:
            def __init__(self, text):
                self.text = text

            def extract_text(self):
                return self.text

        reader = types.SimpleNamespace(pages=[Page("References\n[1] Ada. First paper. 2024."), Page("[2] Bert. Second paper. 2025.")])
        pypdf = types.ModuleType("pypdf")
        pypdf.PdfReader = lambda path: reader
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "synthetic.pdf"
            path.write_bytes(make_pdf("References\n[1] Ada. First paper. 2024."))
            with mock.patch.object(self.extractor.shutil, "which", return_value=None), mock.patch.dict(sys.modules, {"pypdf": pypdf}):
                self.assertEqual(self.extractor.extract_pdf(path), [
                    {"citation_number": 1, "raw_citation": "Ada. First paper. 2024."},
                    {"citation_number": 2, "raw_citation": "Bert. Second paper. 2025."},
                ])


if __name__ == "__main__":
    unittest.main()
