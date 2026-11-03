# Manual PDF import implementation plan

> **For agentic workers:** Execute the user's authorized addition without another approval round.

**Goal:** Request manual PDFs when automatic download fails, then validate and organize local files.

**Architecture:** Existing collection manifests retain resolution metadata. A local import command validates the explicit file version and copies PDFs without network access. Codex requests local paths and checks source/version evidence.

**Tech Stack:** Existing Python 3.9+ helper, pypdf, unittest.

**Spec:** `docs/superpowers/specs/2026-11-02-manual-import-design.md`

## Global constraints

Required target and citation input; preserve original local files; no network on import; official-final policy applies; preserve conflicting files; no personal paths in repository.

## Review focus

An unavailable record has no PDF URL. Local copies can have arbitrary filenames. Same import should be idempotent. Unknown publication status cannot become verified through a filename. A failed import must not change successful collection state.

### Task 1: Import and manual-action report

Files: `scripts/cited_papers.py`, `tests/test_cited_papers.py`, relative to skill/tests roots.

- [x] Write and run failing behavior tests for imports, policy, conflicts, resume and manual report.
- [x] Implement `import_pdf(target, citation, file, version)` and `import-pdf` CLI.
- [x] Run all tests and exercise CLI against an unavailable paper in a temporary collection.

### Task 2: Skill and publication

- [x] Update SKILL.md/README with the manual-download request, path handoff and import command.
- [x] Run an independent skill forward test and code review; repair material findings.
- [x] Validate, commit, push, synchronize installed checkout and verify hosted checks.

## Validation record

All 56 offline tests and skill metadata validation pass. Independent review's two retry findings were reproduced with failing tests and fixed. The existing temporary LatentRAG collection accepted the official local Search-R1 paper, preserved its original, recorded manual provenance and checksum, and retained success on a subsequent fetch. See `docs/TESTING.md` for details.
