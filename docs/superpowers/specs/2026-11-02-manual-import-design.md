# Manual PDF fallback

The user requested that automatic download failures lead to a request for a manual download and a local file path, followed by automatic organization.

Add `import-pdf --target <TARGET_FOLDER> --citation <N> --file <LOCAL_PDF> --version <VERIFIED_VERSION>`. Codex verifies the local paper and its version, resolves any missing metadata, and invokes the helper. The helper copies the supplied file, preserving the original, validates title/byline and readability, applies the existing publication policy, and uses the existing citation-number/title/venue/year folder convention. Accepted/published papers still require the official final version.

The manifest must record manual acquisition, original local source path, timestamp and checksum; unavailable/failed records become downloaded only after validation and successful organization. Unknown/nonpaper references and invalid citations must not be imported. Missing files, wrong papers, disallowed versions and existing conflicting files must leave the collection unchanged. Repeating the same import is idempotent. No network request is needed for an import.

Reports list failed/unavailable references needing manual action with citation number, title, source link, reason and required version. The skill asks for citation-number/local-path pairs after automatic attempts, continues independent references while waiting, and resumes from the existing target. Multiple missing papers are requested together. Keep user PDFs and paths out of the code repository.

Existing file-preservation, target-input and source-binding rules remain in effect. Tests cover unavailable-to-imported transitions, input/version/identity rejection, original-file preservation, conflicts and idempotency, CLI input and manual-action reporting. Independently test the updated skill, run the full suite, push the private repository, synchronize the installed checkout and verify CI.
