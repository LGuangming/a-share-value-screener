# Production image publication

The formal package names are:

- `ghcr.io/lguangming/a-share-value-screener-web`
- `ghcr.io/lguangming/a-share-value-screener-worker`

The historical `*-test-pr28` packages, archived workflow receipts and test initializer pins remain test-only. They are not renamed, deleted or promoted. Production deployment requires separate operator approval and immutable digest references.

Before the first release, the owner must initialize both formal packages as private, associate them with this repository and grant its Actions token write access. Confirm the VPS read-only registry credential can pull them. The active publisher deliberately stops before any push if either package metadata cannot be read or is not private/associated. No automatic package bootstrap or visibility change is performed.

After separate authorization, a main push with `[publish-production]` in the head commit message runs the normal CI and Docker gates, transfers the exact accepted Web and Minimal Worker archives, verifies checksums, architecture, UID and revision, and publishes both with a run-specific tag. It verifies registry digest pulls against the accepted image IDs and checks package privacy again. Only the complete pair yields a production-release-receipt artifact with both digest references. A partial publication is not a deployable release; do not use individual tags from a failed run. No latest tag, VPS operation, data migration or deployment is performed.

Copy both approved immutable references from the successful receipt into the VPS production configuration during a separately authorized 1Panel deployment. Preserve old image references for rollback. A CI or registry success does not substitute for isolated VPS testing or authenticated readiness checks.

## CI routing

Ordinary development-branch pushes do not trigger the main CI workflow. PRs to main and main pushes classify changed paths without top-level path skips; the stable `CI result` job verifies all selected jobs. Plain Markdown docs run scope/contract tests only; CSS runs Web typecheck/build without PostgreSQL; ordinary application changes run relevant integration tests; shared backend/Web contracts run both integrations. Docker, dependency, schema, deployment, CI, test/fixture and unrecognized changes conservatively run the full candidate gate. Missing diffs and manual CI dispatch also select all tests. A production release marker on a main push always selects every acceptance test and the unchanged publication job. Superseded PR runs are canceled; main runs are isolated by run ID. Existing required-check settings may need to be changed by the repository administrator to use `CI result` before enabling routing.
