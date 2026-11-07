# Verification and example provenance

## Packaging checks — 7 November 2026

The clean repository was checked on Windows:

| Check | Result |
|---|---|
| Coordinator unit tests | 64 discovered; 63 passed, 1 skipped |
| Skip | Symlink creation unavailable on this Windows setup; Linux validation required |
| Exported workshop application | 18 tests passed |
| Complete Pact-only config | Synthetic authorized start routed with empty legacy role maps |
| Documentation links | Local Markdown targets checked |

Commands are in the README. Offline coordinator tests use mocked Switch transport and real temporary Git repositories; they are not proof of a new live deployment. Historical pilot checks recorded all 64 coordinator tests passing on Ubuntu. This packaging session does not redeploy AWS or repeat the multi-person live test.

## Historical live pilot evidence

The deployed Switch/Discord pilot completed version 1 and then version 2 of the workshop task, with two distinct stakeholder confirmations and delivery-lead acceptance. The readable final evidence response reported:

- 18 application tests passed; exit status 0.
- Independent review approved the candidate.
- 9 of 10 requirements had builder-supplied test references; one scope/nonpublication requirement needed inspection rather than a fabricated test mapping.
- Human acceptance recorded for this candidate.
- No push, PR or deployment performed by the workflow.

Fresh authorized status requests from the lead and another stakeholder no longer produced the contradictory permission refusal after the narrow Switch rule was corrected. The compact evidence response was also exercised in Discord. Room visibility was separately confirmed by the operator.

The public repo intentionally excludes private room transcripts, identities and full runtime databases. The demo video should show the visible human decisions and verified transitions; do not portray this summary as independently audited telemetry.

## Included output

`examples/workshop-registration/` is the exported accepted version-2 library from source commit:

```text
1a3618e7a7c68fd40b77c00451917e783d759cd6
```

This commit belongs to the original private generated workspace; it is not an ancestor of this new submission repository. No `.git`, private ledger or ownership marker is included with the exported example. File hashes are listed in `example-sha256.json` for checking the Git-normalized LF text export. Packaging normalizes line endings, without changing the example's behavior.

Behavior includes two confirmed places, FIFO waiting, UTC-aware time, cancellation and staff promotion. More than 120 minutes before start, cancellation promotes automatically. At exactly 120 minutes or closer, it reserves the vacancy for explicit staff promotion. At or after start, mutations are rejected. See the example README for detailed semantics and limitations.

## Not claimed

No large-scale reliability study, production security audit, unrestricted multi-tenant execution, continuous uptime guarantee, automatic GitHub publication, or fresh cloud installation test was performed as part of this packaging work. GitHub Actions results, when available, are a separate verification record.
