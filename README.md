# Cited papers downloader

A reusable Codex skill and Python helper for collecting a main paper's direct references. The workflow has two stages: Codex checks the complete bibliography and verifies publication sources; Python validates resolution records, downloads PDFs, and maintains resumable state. The helper requires Python 3.9+ and `pypdf`; networking uses the standard library. It needs no OpenAI API key or paid API service.

Both the main PDF and destination are manual inputs. `<MAIN_PDF>`, `<TARGET_FOLDER>`, and `<RESOLUTION_JSON>` below are placeholders: replace them with real paths. If no destination is supplied, the skill asks for one before creating output.

## Install the skill

From this repository, copy the skill into your personal skills directory. If that skill directory already exists, review your existing copy before updating it.

```bash
mkdir -p ~/.codex/skills
cp -R skills/download-cited-papers ~/.codex/skills/
python3 -m venv ~/.codex/skills/download-cited-papers/.venv
~/.codex/skills/download-cited-papers/.venv/bin/python -m pip install -r ~/.codex/skills/download-cited-papers/requirements.txt
```

Restart Codex if needed to discover the skill. A natural-language request is:

> Use $download-cited-papers to collect papers cited by `<MAIN_PDF>` into `<TARGET_FOLDER>`. Preserve citation numbers and verify official publication versions.

## Use the helper

Run from the installed skill directory, or use the repository's skill directory with a Python environment containing its requirements:

```bash
cd ~/.codex/skills/download-cited-papers
.venv/bin/python scripts/cited_papers.py init --paper '<MAIN_PDF>' --target '<TARGET_FOLDER>'
```

Inspect the full source bibliography against the extracted list. Numbered references must be complete and unambiguous. The extractor uses `pdftotext` when available and falls back to `pypdf`. It does not support every PDF layout, scanned bibliography, or author-year style. Incomplete or unsupported extraction stops with an error and requires a manual AI extraction follow-up; it is not an automatic success.

Codex searches primary publication sources and writes a resolution JSON array inside the target folder. The [example record](skills/download-cited-papers/references/resolution-records.json) documents the fields; adapt it to the actual references rather than submitting it unchanged. Preserve the original citation numbers and raw citations. Every ready record needs identity metadata plus source and evidence URLs. The helper does not discover URLs itself.

```bash
.venv/bin/python scripts/cited_papers.py resolve --target '<TARGET_FOLDER>' --records '<RESOLUTION_JSON>'
.venv/bin/python scripts/cited_papers.py fetch --target '<TARGET_FOLDER>'
.venv/bin/python scripts/cited_papers.py report --target '<TARGET_FOLDER>'
```

To retry selected entries, use `fetch --target '<TARGET_FOLDER>' --only 19 35`. `resolve` validates the whole submitted batch before applying updates.

## Manual download fallback

If an automatic download fails or an official PDF is unavailable, Codex asks you to download the paper manually and provide its local path. The report's `manual_downloads` list includes each missing citation's number, title, source link, reason, and required version. Codex requests missing papers together, supplies their official landing links, and continues independent references while waiting. You can reply with citation/path pairs, such as `19: <LOCAL_PDF>`; you do not need to run the helper yourself.

Codex verifies each local paper and its publication version, resolves any missing metadata, then imports it into the existing target:

```bash
.venv/bin/python scripts/cited_papers.py import-pdf --target '<TARGET_FOLDER>' --citation 19 --file '<LOCAL_PDF>' --version publishedVersion
.venv/bin/python scripts/cited_papers.py report --target '<TARGET_FOLDER>'
```

Replace the citation and path with the actual inputs, and pass the verified version. Import reuses saved resolution metadata even when `pdf_url` is `null`; unknown or nonpaper records are ineligible. A local path alone does not prove the publication version. Published or accepted papers require the official final `publishedVersion`; `submittedVersion` is eligible only for a positively classified preprint.

Import makes no network requests. It validates readable PDF content and title/author identity, copies the PDF into the numbered folder, and records manual acquisition, original local path, timestamp, and checksum. The source file stays intact, repeated imports of the same file are idempotent, and existing conflicting files are preserved. A record becomes downloaded only after successful validation and organization.

## Version and file rules

For known published or accepted papers, only a verified official final `publishedVersion` is eligible. An accepted manuscript (`acceptedVersion`) or preprint (`submittedVersion`) cannot substitute when the official PDF is inaccessible. Use `pdf_url: null` and explain the missing official PDF; it becomes `official_pdf_unavailable`. A failed DOI query does not prove preprint status. Uncertain publication status becomes `needs_review`; affirmative evidence is required for a preprint download.

Paper folders follow `{number}_{short-title}_{venue}_{publication-year}`, with sanitized path components. Verified preprints use `arXiv`; uncertain venues use `Unknown`. The raw citation retains its original year. Each successful download includes `paper.pdf` and metadata; the target also contains `references.json` and `references.csv`, including unavailable, nonpaper, unresolved, and failed entries.

Downloads pass readable-PDF and title/author checks before finalization. Existing files, including manual PDFs, are preserved. Resume checks saved PDF checksums; the source PDF checksum binds citation numbers to that main paper. Use a separate target for a different source. These checks support source verification; Codex must still verify the publication version from authoritative evidence.

Keep input PDFs, downloaded PDFs, generated resolution files, reports, and personal paths outside the code repository. Tests generate synthetic PDFs in temporary directories; the repository includes no copyrighted fixture PDFs.

## Development checks

From the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python skills/download-cited-papers/scripts/cited_papers.py --help
```

The offline suite covers extraction, version policy, source binding, download and manual-import validation, resume, file preservation, and manual-action reporting. CI runs the suite on supported Python versions. Real publication lookups and downloads are separate smoke tests and stay outside the repository.
