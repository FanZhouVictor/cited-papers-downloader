---
name: download-cited-papers
description: Use when a user wants to collect the papers cited by a main paper PDF into a local folder while retaining citation numbers and publication-version evidence.
---

# Download cited papers

Collect the main PDF's direct references. Codex verifies bibliography entries and publication sources; the bundled Python helper manages numbered records, downloads, and resume checks. It does not search the web.

## Required inputs

Obtain the real main PDF path and target folder from the user before initialization. `<MAIN_PDF>` and `<TARGET_FOLDER>` below are documentation placeholders, not inputs. If the target is missing, ask for it; never infer one from the workspace, source location, or a temporary directory. When the user already supplied a real target, use it without asking again.

Run commands from this skill's directory with its Python environment. Keep generated resolution JSON, manifests, reports, and PDFs in the user-supplied target, outside the code repository.

The commands below assume the installed skill has `.venv/bin/python` as described in the repository README. In a development checkout, use the repository-root `.venv/bin/python` with the script's absolute path instead.

## Workflow

1. Initialize:

   ```bash
   .venv/bin/python scripts/cited_papers.py init --paper '<MAIN_PDF>' --target '<TARGET_FOLDER>'
   ```

   Inspect the **whole source bibliography** against the extracted records. Preserve each original citation number and raw citation; include nonpapers and unresolved entries. Download only direct references. Missing/duplicate numbers, incomplete extraction, scanned PDFs, or unsupported author-year bibliographies require a manual AI extraction follow-up; stop the automatic path rather than use a partial or invented list.

2. Search and verify every reference using title, authors, identifiers, venue, and year. Open primary sources such as official proceedings, publisher pages, and repository records. Verify identity, publication status, and the offered PDF's version separately. A failed DOI search does not establish preprint status. A citation's old arXiv label does not override evidence of later acceptance or publication.

3. Write a JSON array to a file in the target. Use the fields and types shown in [resolution-records.json](references/resolution-records.json); its citation number is illustrative and must match the actual source. Each record requires `citation_number`, `title`, `short_title`, `authors`, `venue`, `year`, `publication_status`, `version`, `pdf_url`, `source_url`, and `evidence_url`; `notes` is optional. Ready records need verified title/authors/venue/year and URLs supporting identity, status, and version. Use publication year for folder names; retain the original citation year in the raw citation.

4. Apply the version rule, then resolve and fetch:

   | Verified status | Allowed PDF | If unavailable or uncertain |
   | --- | --- | --- |
   | `published` or `accepted` | Official final `publishedVersion` only | `pdf_url: null`; record why in `notes` |
   | `preprint` | Verified `submittedVersion` | Require affirmative preprint evidence |
   | `unknown` | No download | Keep for review |
   | `nonpaper` | No download | Keep accounted for |

   **For a known accepted or published paper, never substitute an accepted author manuscript (`acceptedVersion`) or preprint (`submittedVersion`).** An official 403, paywall, broken link, time pressure, or desire for a complete collection does not relax this rule. Try another verified official final source; if none is available, leave the PDF unavailable. Never relabel a manuscript as `publishedVersion` or reclassify the paper as `preprint` to make it downloadable.

   ```bash
   .venv/bin/python scripts/cited_papers.py resolve --target '<TARGET_FOLDER>' --records '<RESOLUTION_JSON>'
   .venv/bin/python scripts/cited_papers.py fetch --target '<TARGET_FOLDER>'
   .venv/bin/python scripts/cited_papers.py report --target '<TARGET_FOLDER>'
   ```

   Use `fetch --target '<TARGET_FOLDER>' --only 19 35` for selected retries. Missing official PDFs become `official_pdf_unavailable`; uncertain status becomes `needs_review`.

5. Report downloaded, unavailable, unresolved/review, nonpaper, and failed references with evidence and reasons. State partial completion clearly. The helper checks readable PDF content and title/author identity, then records checksums and provenance. Existing manual files stay intact; a filename alone proves no identity or version. Resume uses saved checksums, and a manifest is bound to the main PDF's checksum. Never switch source PDFs under an existing manifest.
