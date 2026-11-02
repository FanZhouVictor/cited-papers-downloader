# Cited papers implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. The user's instruction authorizes implementation and private publication; continue without another approval round.

**Goal:** Deliver a private, tested repository containing a reusable Codex skill and resumable Python citation downloader.

**Architecture:** Codex handles source verification; Python handles extraction, durable manifests and downloads. The skill is self-contained and takes manual source/destination inputs.

**Tech Stack:** Python 3.9+, pypdf, urllib, argparse, unittest.

**Spec:** `docs/superpowers/specs/2026-11-02-cited-papers-design.md`

## Global constraints

Required explicit target; no hardcoded personal paths; official final PDFs for accepted/published papers; no silent preprint fallback; no overwrite of manual files; downloaded PDFs never committed.

## Review focus

Placeholder/missing targets must not create folders. A missing DOI is not proof of preprint status. Citation numbers belong to one source PDF. Resume must detect tampering and preserve manual files. HTML or a different paper must never become a successful download.

### Task 1: Numbered reference extraction

Files: `skills/download-cited-papers/scripts/reference_extract.py`, `tests/test_reference_extract.py`.

Produces `extract_references(text: str) -> list[dict]` and `extract_pdf(path: Path) -> list[dict]`; each reference has `citation_number` (int), `raw_citation` (str). Missing/duplicate numbers and unsupported bibliography raise ValueError. Use pdftotext when present, pypdf otherwise.

- [x] Write tests for wrapped references, page furniture, appendix boundary, missing/duplicate numbers, unsupported author-year bibliography; observe failure.
- [x] Implement extractor and run its tests.
- [x] Check the provided local PDF extracts 90 contiguous references without committing its text.

### Task 2: Manifests, version policy and downloader

Files: `skills/download-cited-papers/scripts/cited_papers.py`, `tests/test_cited_papers.py`, `requirements.txt`, `.gitignore`.

Consumes extraction interface. Commands: `init --paper PATH --target PATH`, `resolve --target PATH --records JSON`, `fetch --target PATH [--only N ...]`, `report --target PATH`. Manifest records preserve raw citations and resolution metadata. Every command requires an explicit target.

- [x] Write failing tests for input safety, atomic batch validation, policy decisions, source binding, naming, PDF validation, checksum resume and file preservation.
- [x] Implement and run the complete offline suite.
- [x] Exercise CLI missing target, initialization, resolution, fetch and report in temporary paths.

### Task 3: Skill and delivery

Files: `skills/download-cited-papers/SKILL.md`, `agents/openai.yaml`, `references/resolution-records.json`, root README, CI workflow.

- [x] Run a baseline skill scenario before writing instructions; capture gaps.
- [x] Write concise workflow instructions and install/usage documentation with `<MAIN_PDF>` / `<TARGET_FOLDER>` placeholders.
- [x] Validate skill metadata and run independent forward test.
- [x] Run real extraction and official-download smoke tests outside repository.
- [ ] Review, repair material findings, run final suite, commit and push to a private repository; verify privacy and remote commit.

## Execution record

Separate new repository staged in a temporary directory; existing literature repo and manual folders remain untouched. GitHub account confirmed using authenticated CLI. Personal-path examples and test output will not be committed.
