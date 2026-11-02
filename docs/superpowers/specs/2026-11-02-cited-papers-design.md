# Cited papers downloader design

Build the Codex skill plus Python helper selected by the user. The user explicitly requested implementation, a private GitHub repository, and testing. Both the main PDF and destination directory are required manual inputs; no personal directory is a default or a committed example.

## Approaches

AI-only instructions require repeated file bookkeeping. A standalone downloader cannot reliably settle ambiguous publication identities. The selected hybrid lets Codex verify references and official publication sources while Python extracts numbered references, validates records, names folders, downloads files, and maintains resumable state.

## Behavior

- Process only the supplied main paper's direct references. Preserve original numbering and raw citation; fail clearly on incomplete/ambiguous extraction rather than inventing references.
- Require `--paper` and `--target` for initialization. A missing target or an example placeholder must not create output. A skill must ask for the destination when absent.
- Codex resolves each reference using identifiers, title, authors and official publication evidence, then passes JSON records to the helper.
- Use `{number}_{short-title}_{venue}_{publication-year}`. Use `arXiv` for verified preprints, `Unknown` for uncertain venue, and record original citation year separately. Sanitize path components and preserve manual files.
- For known accepted/published works download only the verified official final PDF (`publishedVersion`). An accepted author manuscript is a distinct `acceptedVersion`; do not silently substitute it. Preprints (`submittedVersion`) are allowed for records explicitly classified as preprint, never because a DOI lookup failed.
- Uncertain, unavailable, nonpaper and failed references remain in the manifest and CSV. Partial completion is explicit.
- Download to temporary files, validate a readable PDF and title/author identity, record checksum and source URLs, then finalize without overwriting existing files. Reruns verify saved checksums and skip completed downloads.
- Bind a manifest to the source PDF checksum so a different main paper cannot reuse citation numbers accidentally. Validate an entire resolution batch before updating it.
- Keep PDFs, absolute personal paths, secrets and generated manifests out of the GitHub repository. Tests use synthetic fixtures and temporary directories. A real LatentRAG test stays outside the repository.

## Components and scope

Self-contained skill at `skills/download-cited-papers/` with scripts and a JSON example reference. Python 3.9+, `pypdf`, stdlib networking/CLI, unittest tests. The helper has `init`, `resolve`, `fetch`, and `report` commands; web search/publication decisions belong to Codex. No OpenAI API key or paid service is required by the helper.

## Validation

Offline unit/integration tests cover numbering, policy, path handling, identity rejection, malformed downloads, resume and manual-file preservation. Independently exercise skill behavior with a missing destination and ambiguous publication status. Extract all 90 references from the local LatentRAG PDF and smoke-test real official PDFs in an explicitly supplied temporary destination. Publish code only to a private repository and verify visibility.
