# Code signing policy

OwnDesk currently has no trusted code signing certificate. SignPath Foundation has not approved the project, and no signing request is enabled. Unsigned candidates are for verification, not accepted public Windows releases.

## Responsibilities

The current sole maintainer is [rauf60190-dotcom](https://github.com/rauf60190-dotcom). The maintainer authors changes, reviews external contributions and would manually approve each signing request. No other approver or independent reviewer is currently appointed. Multi-factor authentication for GitHub and SignPath must be confirmed before enabling signing; it is not claimed as verified here.

## Build and approval rules

- Build from the public repository at an identified commit with a clean tracked working tree.
- Preserve dependency lock files, upstream notices and pinned download hashes. Third-party binaries keep their own publisher identity; do not re-sign them as OwnDesk.
- Run frontend, Windows, Ubuntu and PostgreSQL checks before accepting a release.
- Record the source commit and SHA-256 of the payload, final executable and dependency input files. A CI artifact is a candidate until installation, privacy and removal acceptance is complete.
- Signing requires a separate explicit manual approval. Pull requests cannot trigger signing or receive production credentials.
- After signing, verify publisher, certificate chain, timestamp and exact final bytes. Recheck the downloaded installer on Windows with normal protection enabled before publication.

The current verification workflow uses a read-only GitHub token and does not sign, deploy or create public releases. It builds an unsigned EXE candidate and verifies its embedded payload on a disposable runner. This does not establish SmartScreen reputation or prove acceptance by every Windows security policy.

## Network activity and privacy

OwnDesk installation downloads dependencies from their configured upstream sources. Public access through OwnDesk and Cloudflare is a separate user-selected connection; Telegram integration is configured by an administrator. Google login and registration email apply to the OwnDesk portal. The organization server stores support data at the installation selected by its operator.

See the [service privacy policy](https://owndesk.work/privacy), [terms](https://owndesk.work/terms), installation documentation and [Windows service removal procedure](WINDOWS-UNINSTALL.md). The candidate bootstrap displays `infra/INSTALLATION-NOTICE.txt` before preparation. The notice explains system changes, downloads and the separate portal pairing step. Native installation requires an unchecked acknowledgement; cancellation exits before preparation. Review of the full installer flow remains a prerequisite for a signed release; this page alone does not establish compliance.

Only after actual approval and activation will the project add a statement identifying its signing provider and certificate publisher.
