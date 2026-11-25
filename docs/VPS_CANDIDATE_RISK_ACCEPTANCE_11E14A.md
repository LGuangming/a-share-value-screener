# 11E-14A — scoped OpenSSL risk acceptance and candidate CI

Decision approved by project operator on 2026-11-25: end the dedicated
11E-11 remediation effort, not the security scanning. Temporary acceptance
through 2026-12-25 UTC (exclusive cutoff 2026-12-26 00:00 UTC). The duration
is an implementation choice for review, not evidence that the vulnerability
is fixed. No renewal by default.

| Scope | Exact match |
|---|---|
| Candidate Web | ashare-web:ci, candidate matrix only |
| Candidate Worker | ashare-worker:minimal-eval, default Minimal only |
| OS | Debian 13, os-pkgs / debian |
| Installed package | libssl3t64 3.5.9-1~deb13u1 |
| Findings | CVE-2026-107494 and CVE-2026-124709, HIGH only |
| Fixed version | Must be absent/empty |
| Expiry | 2026-12-26T00:00:00Z |

Evidence: prior candidate run 45599074111, job 139153010857, both
Trivy generations; Debian libssl3t64 package metadata and OpenSSL runtime
identity. These are QUIC denial-of-service findings in the previous scan.
Risk is not eliminated by the host reverse proxy, loopback, internal
network or non-root execution. Retain existing network, credential and
resource controls; do not enable QUIC/HTTP3 as part of this phase.

No global .trivyignore, no ignoring unfixed vulnerabilities, no scanner
failure tolerance. The candidate vulnerability scan returns raw JSON with
exit code zero for findings only; scanner execution errors remain failures.
A required subsequent Python gate binds raw evidence to exact local image
ID and candidate tag, checks the Debian OS result, and blocks every other
HIGH/CRITICAL finding, including the same CVE in another component,
different version, CRITICAL severity, or newly available fix. Missing or
invalid reports and missing decision receipts fail closed. Original raw
JSON, decision JSON, SBOMs, secret scans and runtime identity evidence are
retained as artifacts. Secret gates remain unmodified. Distroless and Slim
comparison jobs remain strict, non-release candidates and cannot satisfy
the independent release candidate gate. Tests cover expiry, exact match,
scope, fixed version, critical escalation and malformed evidence.

CI trigger: add feature/vps-image-security to the push branch filter and
cover deploy/docs paths as well as existing Docker/code/test paths. The
previous SHA 5202373 had two successful Tests and Build runs but no Docker
run; exact reason PR event did not generate Docker CI is not established.
The original 11E-14A revision required PostgreSQL 17 and 18.7. Following
the operator's single-version decision, only the full PostgreSQL 18.7
candidate and independent acceptance gate must pass on the reviewed SHA. Additional container fixture assertions
verify the 56 M12 table permissions with the actual non-owner worker.
Comparison job failures remain visible; do not misrepresent whole workflow
as green if they fail. No live VPS tests in this phase.

This acceptance is for candidate CI only. PR #27 remains unchanged;
future publication must independently enforce the same or stricter rule,
re-evaluate expiry and scan the exact reviewed immutable pair. No merge,
registry push, deployment, data changes or release authorization.

## Explicit stacked-branch trigger fallback

The first 11E-14A commit (457c0da) added the PR #28 branch to the direct
Docker push filter. At inspection only Tests and Build push/PR runs were
observed, so direct-trigger repair was not yet demonstrated. The follow-up
adds workflow_call to the same Docker workflow and a push-only reusable
caller in Tests and Build on refs/heads/feature/vps-image-security.
This uses the same committed revision and full matrix, not a copied or
reduced smoke test. No secrets inherited; contents read only. The direct
push filter returns to its original branch to avoid duplicate push builds;
normal standalone pull_request triggers are retained. A separate PR run
may still occur and must be reported distinctly. On the push caller run,
comparison failures can make the combined Tests and Build red even when
the independent candidate acceptance succeeds; inspect individual jobs.
No automatic rerun of old SHAs and no dispatch of PR #27.

## PostgreSQL 18.7-only acceptance (operator-approved 2026-11-25)

The live VPS database was already migrated to PostgreSQL 18.7 in M12.
PG17 is retired from current CI and release acceptance, not a supported
fallback target. The four PostgreSQL matrices (Python pipeline, Web build,
Distroless comparison and Minimal candidate) each contain exactly one entry:
postgres:18.7-alpine. The disposable Compose fixture uses that exact tag
without a PG17 default or database-image override. Python 3.11/3.13 tests,
base resolution, strict Slim comparison, all existing candidate security
and functional checks and the independent candidate gate are unchanged.
Expected combined push caller job count: nine rather than thirteen.
Previous dual-version results remain historical evidence only. No source
schema, production Compose, live database, archive, PR #27 or main changes.
No change to exception expiry, scope, raw evidence, secret or vulnerability
thresholds. New SHA must independently pass before publication consideration.
