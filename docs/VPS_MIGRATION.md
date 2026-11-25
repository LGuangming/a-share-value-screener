# VPS migration — phase 05: database reader compatibility

Current deployment integration after completed M12: see
[11E-14 post-M12 integration](VPS_POST_M12_INTEGRATION_11E14.md). Historical
migration phases below are retained for design context, not instructions to
repeat migration. Existing ashare and archive are durable; test guards remain
fixture-only. No release or deployment authorization.

This phase changes the website reader only. No production migration, schema
changes, deploy, data archive changes or writer configuration changes.

## Runtime configuration

| Environment | DATABASE_DRIVER | DATABASE_READ_URL |
| --- | --- | --- |
| Existing Vercel | unset or neon | existing restricted Neon URL |
| Self-hosted VPS | postgres | standard TCP PostgreSQL URL for a restricted reader |

The reader never falls back to DATABASE_URL. Keep credentials server-side.
Restart the Next.js process when changing the mode or URL. Use TLS with
certificate verification for remote PostgreSQL; for a private Docker network
or loopback, no public port is needed. Do not expose TCP 5432 to the Internet.
Do not grant the website INSERT, UPDATE, DELETE, DDL or administrator rights.
The TCP adapter uses a small pool (4 connections per process), read-only
transactions by default, 5-second connection and 15-second statement limits.
A database role with SELECT-only grants remains the primary permission boundary.

All existing tagged SQL is unchanged; interpolation uses positional parameters.
DATE values are returned as calendar strings so timezone changes do not shift
trade/report dates. JSONB, numeric, timestamp and bigint use pg's normal decoding
and existing application normalization.

## Verification

Run npm install, npm run test:db, npm run typecheck, npm run build and the
existing test suite. The CI integration step provisions a disposable PostgreSQL
17 service and a restricted test reader; the source production major version is
not yet known. It uses synthetic fixtures only. The integration test is skipped
when TEST_POSTGRES_URL is unset and refuses non-loopback/non-fixture targets.
Never point it at a production database. A real Neon comparison and VPS smoke
test remain required after source access is restored.

No change to archive storage, worker scheduling, Docker deployment or DNS is
included in phase 05. Do not merge or deploy before explicit review.

## Phase 06 — read-only archive compatibility

The website defaults to ARCHIVE_STORAGE_DRIVER=neon and preserves the private
branch-scoped HTTPS S3 endpoint and restricted reader credentials. Set
ARCHIVE_STORAGE_DRIVER=local and ARCHIVE_LOCAL_ROOT=/data/archive to read the
same object keys from the VPS. Bind mount /opt/ashare/archive:/data/archive:ro.
Retain a-share-backtest/01_raw_data and every manifest, content-addressed
snapshot, original Parquet and JSONL.GZ object; do not rewrite manifests.
The configured root must exist, be absolute and canonical, and not be a
symlink or the filesystem root. The archive writer must be trusted, with
immutable parent directories and atomic publication; the web mount is read-only.
Unknown driver values fail closed. There is no fallback to Neon.
Missing files return null, malformed/corrupt/oversized files fail validation.
Prefix listings support the two existing list patterns; maxKeys overflow is
reported as truncated and rejected by the existing business readers (no silent
partial listing). No general-purpose pagination API is exposed in this phase.
Cache keys distinguish storage mode and root/bucket identity; existing TTLs
are retained. No additional service or public static file route is introduced.

Tests: npm run test:archive. For real Parquet tests install Python duckdb and
set ARCHIVE_TEST_FIXTURES=1 (CI does so). Synthetic fixtures test annual and
incremental archives, recent 120 sessions, factors, financial and ST manifests,
corruption, and parity with a mocked S3 transport. No actual Neon requests.
Python archive writers, inventory/export and production data verification are
outside this phase. The actual VPS mount and full production archive must be
verified before cutover. No merge or deployment without approval.

## Phase 07 — Python local pipeline

Python already uses psycopg TCP; continue to use a restricted ingestion
writer DATABASE_URL, not the website reader. Install
backend/requirements-worker.txt; historical BaoStock jobs may require the
existing optional BaoStock dependency. Set ARCHIVE_STORAGE_DRIVER=local,
ARCHIVE_LOCAL_ROOT=/data/archive. Default cloud driver and existing Actions
remain unchanged. Worker mount is writable; website mount is read-only.
Use a shared group for the reader and writer (files 0640, directories 0750).
Use a dedicated canonical filesystem root owned by trusted operators.

All existing active archive commands use the StorageConfig/s3_client factory.
Local metadata is stored under .archive_meta/objects, keyed by canonical object
key and content SHA-256, outside the public data namespace. A file without a
matching metadata record fails closed; do not synthesize trusted metadata by
hashing unverified downloads. During the later transfer phase use verified
source checksums/length/content type, preserve original modification time and
source ETag in the migration inventory, and ingest the exact bytes through the
local adapter. Local ETag identifies a file version, not the source ETag.
Staging files are on the same filesystem and fsynced before atomic rename.
Metadata is written first, so old data remains readable if publication fails.
Old metadata entries may remain after interruption; no automatic sidecar
garbage collection. This is not a transactional filesystem or a security
boundary against privileged writers; real crash recovery and disk limits need
VPS validation. Immutable binary objects cannot be implicitly replaced.

Run scheduled/manual VPS tasks through the same single-host lock:
```
PYTHONPATH=backend python -m value_screener.local_pipeline market
PYTHONPATH=backend python -m value_screener.local_pipeline financial --budget 40
PYTHONPATH=backend python -m value_screener.local_pipeline maintenance
PYTHONPATH=backend python -m value_screener.local_pipeline backtest --year 2025
```
Local run UUID recorded in job metrics, never a fabricated GitHub run ID.
A failed upstream command stops downstream steps; skipped/unchanged upstream
jobs do not publish a new NCAV result. No cron/scheduler is installed. All
mutating manual archive commands must be serialized with this pipeline by
operators; do not run raw modules concurrently or across multiple hosts.
DuckDB uses 2 threads and 512MB managed-memory limit in local mode; optional
DUCKDB_TEMP_DIRECTORY is an absolute scratch path. This is not an RSS limit.
The engineering backtest restores verified files into temporary disk before
running; it is not an investment performance result or PIT certification.

Maintenance defaults to audit only. Local apply requires explicit environment
opt-in and pipeline invocation; preserve all existing retention, backup, date,
archive and website gates. The local website health origin is restricted to
the internal web service or loopback port 3000, no external redirect, optional
private session cookie. Missing/expired auth blocks cleanup; do not enable
apply until actual authenticated local health checks are verified. Existing
retired consolidation and legacy deletion entry points reject local mode.
The writer adapter only permits healthcheck deletion or explicitly enabled
obsolete monthly-reader deletion; original daily data cannot be deleted by it.
No production cleanup, provider calls or schedule changes in this PR.

CI uses temporary directories, isolated PostgreSQL 17 and synthetic provider
data. It verifies market ingestion, real Parquet daily/month publication,
archive-first financial import and NCAV calculation, repeatability, metadata
and atomic failure tests. Production archive migration, actual data coverage,
multi-strategy performance, throughput and real VPS authenticated maintenance
remain separate acceptance steps.

## Phase 08 — application Docker images and Compose

Two Linux amd64 images are built separately: Node 22 Debian slim Next.js
standalone web (UID 10001) and Python 3.11 Debian slim worker (UID 10002).
Both use shared GID 10010. Existing Vercel builds do not enable standalone;
the Docker build sets NEXT_STANDALONE=1 only during image build. npm ci uses
the committed package-lock.json; Python dependencies retain declared ranges.
No deployment credentials are built into either image. Build context uses
an allowlist; data, env files, backups and Git metadata are excluded.

The application compose file expects pre-existing 1Panel-managed database
and proxy networks; does not install PostgreSQL or OpenResty. The database
host alias in the environment examples is illustrative. The actual network
names and host alias must be confirmed on the VPS in later phases. Worker
also attaches to a dedicated bridge for Tushare network egress. By default
no host port is published. For host-based OpenResty only, the optional
compose.loopback.yaml binds web to 127.0.0.1:3000; never expose 3000 or 5432
on the public interface. Retain the password gate and HTTPS. Rate limiting
and public reverse proxy verification belong to the later OpenResty phase.

Proposed host layout (not created in phase 08):
```
/opt/ashare/app/compose.yaml
/opt/ashare/config/.env
/opt/ashare/config/web.env
/opt/ashare/config/worker.env
/opt/ashare/archive
/opt/ashare/cache/nextjs
/opt/ashare/tmp/duckdb
/opt/ashare/backup
```
Keep private env files mode 0600, not in the repository or image. Use distinct
reader and writer database roles, and do not pass the signing secret to the
worker. Pre-create bind directories; Compose refuses missing sources. Set
archive owner 10002:10010, mode 2750; imported directories must be traversable
by the shared group and files readable (e.g. 0640). The web bind is read-only.
Set scratch owner 10002:10010 and web cache owner 10001:10010. Do not recursively
chown any existing production data until explicitly authorized. Confirm no
UID/GID conflict on the actual server. The archive mount must be the same
filesystem for all local writer tasks and staging. Cache and scratch are
disposable; archive/database are durable, outside the containers.

Web is capped at 768 MiB / 1.5 CPU, Node heap at 512 MiB; worker at
1024 MiB / 2 CPU, DuckDB managed memory 512 MB. These are initial limits,
not measured capacity. No Docker socket or privileged access is mounted.
Both run non-root, drop all capabilities, and have rotated JSON logs
(10 MiB x 3 per container). Web restarts unless stopped; worker is
profile-gated, one-shot, no restart. Schedule only after migration approval.

The web image healthcheck is a liveness probe of /login (200 or
unconfigured 503 indicates the server is responding). It is not readiness
or a claim that the password is configured; authenticated /api/health,
database read and archive coverage must be checked separately before cutover.
The existing password protection is unchanged; no public health bypass.

After later authorization and verified network/permissions, intended commands
from /opt/ashare/app are:
```
docker compose --env-file ../config/.env config --quiet
docker compose --env-file ../config/.env up -d web
docker compose --env-file ../config/.env run --rm worker market
docker compose --env-file ../config/.env run --rm worker financial --budget 40
docker compose --env-file ../config/.env run --rm worker maintenance
```
Do not run these against production yet. No registry publication workflow,
credentials, deployment, scheduler, database setup or automatic cleanup
is included. Pin reviewed immutable SHA tags or image digests once published.
Retain previous image versions for app rollback; schema/data rollback is separate.

Docker CI builds local images only on an isolated runner. The test overlay
starts disposable PostgreSQL 17 with separate fixture writer/reader roles.
A real worker writes synthetic Parquet and SQL through the local storage
adapter. Real standalone web must serve authenticated queries and static
assets, reject unauthenticated access, fail writes to the read-only archive
and database, and preserve SQL/archive state across web recreation. Test
network and volumes are removed after each run. No actual providers or
production credentials are used. Real image size and resource use are
reported/verified by CI and later VPS load tests; no performance promise.

## Phase 11B — Host + Loopback and isolated VPS test deployment

This section supersedes the earlier external-proxy-network instructions.
The base compose has no proxy-network dependency. Select exactly one overlay:
- `compose.loopback.yaml`: 127.0.0.1:3000 (default), separate project bridge
  for published-port connectivity, plus the external database network.
- `compose.bridge.yaml`: optional external PROXY_NETWORK, no published port.
  Never combine the two.

For the approved 1Panel Host-network OpenResty test use base + loopback +
`compose.vps-test.yaml`, Docker Compose >= 2.24.4 (supports !override).
Create dedicated /opt/ashare/test/{app,config,archive,cache/nextjs,tmp/duckdb}
with archive owner 10002:10010 mode 2750, cache owner 10001:10010 mode 0750,
tmp owner 10002:10010 mode 0750, root-only config (0600 files).
Keep credentials out of git. Copy test.env.example to config/.env,
web.test.env.example to config/web.env and worker.test.env.example to
config/worker.env. Use only ashare_test on postgres:5432 and dedicated
screener_reader/screener_writer. Percent-encode URI password characters.
No cloud or provider secrets, no production mount paths. Worker test egress
is internal; test maintenance apply is disabled. Guards run before normal
entrypoints, reject other URLs and verify database identity. This is an
operator guard, not a sandbox against a root administrator overriding
entrypoints, mounts or networks. Do not run production tasks.

After separate deployment approval, from /opt/ashare/test/app:

    docker compose --env-file ../config/.env -f compose.yaml -f compose.loopback.yaml -f compose.vps-test.yaml --profile worker config --quiet
    docker compose --env-file ../config/.env -f compose.yaml -f compose.loopback.yaml -f compose.vps-test.yaml up -d --wait web

Inspect the port binding (127.0.0.1 only) and test actual HTTPS via OpenResty.
The image /login health check is liveness only. Run deploy/check-readiness.cjs
with ASHARE_READINESS_URL (HTTPS), ASHARE_READINESS_PASSWORD (read privately
from the terminal), ASHARE_READINESS_STOCK, ASHARE_READINESS_QUERY and
ASHARE_READINESS_YEAR for known test data. It verifies auth, Secure Cookie,
SQL search, bars/factors and authenticated snapshot freshness. Empty test
DB/archive cannot pass full readiness. CI alone may allow a stale synthetic
snapshot on 127.0.0.1 using ASHARE_TEST_ALLOW_STALE=1.
CI matrices retain PostgreSQL 17 and add postgres:18.7-alpine for Node,
Python and Docker. CI uses disposable networks, fixture data and local
images only. It exercises the same VPS test overlay, including guard
rejection of production DB URLs, then container recreation. Actual 1Panel
TLS forwarding, public port reachability, resource pressure and production
parity still require later approval and verification.

Rollback: stop only this test Compose project (no -v, no network or data
deletion), retain isolated test directories and revert to the previous
reviewed image digest if applicable. Never operate on ashare-postgres or
the existing production Vercel/Neon environment.

## Phase 11E-12 — selected Minimal Worker runtime (not release approval)

The default Dockerfile.worker target is now runner, inheriting the reviewed
minimal-evaluation image. The production Compose inherits its normal
/usr/local/bin/python3 -m value_screener.local_pipeline entrypoint. VPS-test
Compose uses the same interpreter with value_screener.test_worker_launch.
All existing guard, UID/GID, mount, internal egress, memory/CPU and loopback
settings remain unchanged. The CI-only Distroless overlay is never used on
VPS. The old Minimal CI overlay remains for compatibility, but default CI
exercises the actual VPS-test overlay directly and verifies the resolved
launch contract. Use only the separately approved immutable image digest;
existing sample tags are placeholders, not published images.

The release candidate matrix builds the default target without --target,
verifies its entrypoint/command/identity, and retains full dual-PostgreSQL
functional, native, source, SBOM, secret and two-version strict vulnerability
checks. A separately named candidate acceptance job requires both matrices
and base verification to succeed. Distroless and Slim remain named comparison
jobs with unchanged strict scans; they may still make overall workflow red.
No vulnerability waiver, continue-on-error, publication or deployment.
OpenSSL remediation is deferred, not passed; current four candidate HIGH
findings remain release blockers. Do not change PR #27 publishing workflow
or any other PR as part of this phase. Publication and actual VPS setup
require separate approval.
