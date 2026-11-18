# Native download notices — review draft

Collected from exact pinned vendor downloads and versioned upstream licenses. The original texts are preserved without changing their license.
This is not a complete SBOM or proof of licensing compatibility for every embedded library.

| Component | Version | Notice files | Pinned download SHA-256 |
|---|---|---:|---|
| postgresql | 17.11-3 | 291 | 4b8db0930c38f6ef845db919551dedda3b6b845aeb0927b3d79a6e8e9e4537cf |
| caddy | 2.10.2 | 1 | 9fd1ef9be5d9b05852b66ccc25f96f23d8651bcab20779861a745bdffa273722 |
| cloudflared | 2026.10.7 | 1 | 2493dbc168923bb3e59e5e7716f71deced92992eea9acd6e9d74f84ac7751343 |
| winsw | 2.12.0 | 1 | 05b82d46ad331cc16bdc00de5c6332c1ef818df8ceefcd49c726553209b3a0da |
| python | 3.14.10 | 44 | cc28026a650970cc1570bb127312f1f9634eb67abfe157ae6ca1acbbc627d091 |

See inventory.json for each notice's exact source path/URL and SHA-256. PostgreSQL/EDB notices include bundled tools and libraries present in the vendor archive; their inclusion here does not assert every one is executed by OwnDesk.

Pending: source/provenance inventory for embedded native dependencies and distribution obligations. The license of OwnDesk-written code is recorded separately in the root LICENSE/LICENSING.md; upstream notices keep their own terms.
