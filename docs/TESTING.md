# Validation record

Tested on 2026-11-02 with Python 3.9.6 and pypdf 6.21.4.

## Automated checks

`python -m unittest discover -s tests -v` passes 42 tests. Tests cover:

- Wrapped and two-column numbered bibliographies, page furniture, appendix boundaries, duplicate/missing labels and unsupported bibliography formats.
- Required real destination inputs and rejection of unresolved placeholders.
- Binding citation numbers to the main PDF checksum and atomic validation of resolution batches.
- Strict official-final-version policy, explicit preprint classification, uncertain status, unavailable PDFs and nonpaper records.
- HTTP success, redirects, rate-limit retry and HTTP 403 without source substitution.
- Readable PDF validation, ordered title/byline identity checks, author affiliation superscripts and rejection of papers that merely cite the expected title.
- Resume, altered-file detection, preservation of manual PDFs, symlink rejection and concurrent creation of manual metadata.

The skill creator's `quick_validate.py` passes. The skill metadata JSON/YAML and CLI help also validate. CI runs the offline suite on Python 3.9 and 3.12.

## Independent skill checks

A baseline experiment before authoring the skill showed an assistant choosing arXiv after an accepted paper's official PDF returned 403. The completed skill explicitly disallows that substitution. An independent forward test then exercised:

| Scenario | Observed result |
| --- | --- |
| User omits destination | Assistant asks for it before initialization |
| Old arXiv citation plus empty DOI search, current status unknown | `needs_review`, no download |
| Known accepted COLM paper, official PDF 403, arXiv available | `official_pdf_unavailable`, no preprint fallback |
| Literal `<TARGET_FOLDER>` supplied | CLI exits 2; no output created |

An independent code review found two defects: a first-page citation could pass the original identity check, and metadata created during a request could be overwritten. Both were reproduced with failing regression tests, corrected, and verified by the green 42-test suite.

## Live smoke test

The user's local LatentRAG main PDF was read without modification. Both the Poppler and pypdf extraction paths returned contiguous references 1–90. The test destination was explicitly supplied and kept outside the repository.

- Reference 35: downloaded the official NeurIPS 2025 *Search and Refine During Think* PDF, validated title/byline and 29 readable pages, recorded SHA-256, and successfully resumed without another download. The final stricter title/byline validator also accepts the saved official PDF.
- Reference 19: verified Search-R1's COLM 2025 acceptance; the official OpenReview PDF returned HTTP 403. Recorded `official_pdf_unavailable`; no arXiv substitute.
- Reference 90: retained the logit-lens web post as `nonpaper`.
- Remaining 87 references: intentionally left unresolved; this was a smoke test rather than a full collection run.

The repository includes code, synthetic tests and documentation only. Main PDFs, downloaded papers, resolution records and personal absolute paths were not committed.
