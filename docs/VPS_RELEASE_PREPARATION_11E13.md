# Phase 11E-13 — VPS release and deployment preparation (blocked)

**11E-14A update:** scoped risk acceptance supersedes the historical two-CVE
OpenSSL block only as defined in [11E-14A](VPS_CANDIDATE_RISK_ACCEPTANCE_11E14A.md).
No other release gate or authorization changes.

**Historical preparation baseline:** M12 is now complete. For current production
integration and capacity review use [11E-14 post-M12 integration](VPS_POST_M12_INTEGRATION_11E14.md).
The isolated test instructions below remain for fixtures only; never use the
migrated ashare DB or archive for them. The old VPS size is not current evidence.
No separate data migration preparation is required. Release remains blocked.

Status: preparation only. Technical baseline PR #28 commit
`0fc7602aeb67a6e1f03b3d67b767fa3efb22cc65`, Actions runs
45597222204 and 45597222301. Tests and Build 6/6 passed; default Minimal
candidate functional tests passed for PostgreSQL 17 and 18.7-alpine.
Release candidate acceptance failed. OpenSSL CVE-2026-107494 and
CVE-2026-124709 remain HIGH findings in each candidate image.
No exception, no release, no registry push, no deployment, no actual VPS
initialization, no production database connection or data transfer.
This document is a checklist/runbook, not a deploy script or authorization.
Keep PR #27 and all other PRs unchanged during this phase.

## 1. Release gate and approval sequence

1. Apply the current 11E-14A scoped and expiring OpenSSL risk acceptance,
   independently verify Node's embedded OpenSSL and Debian libssl3t64,
   and require every other security gate to pass. Lock official source/base/
   package/wheel identities; do not change metadata to hide actual contents.
2. Review the final immutable application SHA (not this historical baseline).
   Require PostgreSQL 18.7 Tests and Build business jobs and the default
   Minimal candidate to pass, base/wheel verification success and independent release-candidate
   acceptance success on the same reviewed source. Preserve old/new Trivy
   scans (including unfixed HIGH/CRITICAL and secret gates), upstream component
   inventory, SBOM, actual runtime versions, logs and base/wheel locks.
   Historical Distroless Worker/Slim comparison artifacts are non-release
   evidence. Their jobs are retired from routine CI; never substitute these
   optional targets for candidates. Formal Web zstd and shell-free checks remain.
   Do not infer release approval from functional test success.
3. Obtain explicit authorization to update/review PR #27. It is currently
   pinned to PR #26 d5f1ed82ea4360cfc0619aeb6e6fa13752177aac, with PR26
   package names and a shell-based identity test. Do not dispatch it as-is.
   Adapt BOTH review and publish jobs to the final reviewed source, exact
   pinned build inputs and default Minimal target, shell-free runtime checks
   (Node process.getuid/getgid; Python os.getuid/getgid), provenance,
   strict scans and candidate gate. Review Vercel Git integration before
   any separate main merge. No main merge or dispatch in phase 11E-13.
4. Later authorized publication uses separate private isolated TEST packages
   (names to be approved; never existing production or old PR26 tags).
   Both images must be built and scanned before any push; record source SHA,
   workflow/run/attempt, candidate gate evidence, platform linux/amd64,
   package, immutable registry digest, image config ID, OCI revision label,
   base/wheel/source locks, SBOM hashes and scan database timestamps.
   Verify the authenticated pull by digest, tag-to-digest binding, image ID
   equality with the scanned image, and anonymous denial plus package UI
   visibility. A partial publication is a blocked pair, not a usable release.
   No credentials in artifacts, stdout, compose output or chat.
5. Obtain a separate VPS test deployment approval only after the pair is
   independently verified, the read-only host preflight passes, and the
   isolated database and verified fixture archive are available. Production
   cutover, schedules, maintenance apply, DNS, SSH/firewall changes, and
   production data migration each remain outside this approval.

## 2. Read-only host preflight (operator, when separately authorized)

These commands do not create directories, pull images, start containers,
query PostgreSQL, modify firewall/SSH, or change Docker state. Run locally
in the administrator terminal. Do not post unredacted credentials, network
details or environment contents. Missing tools/paths are a blocker, not
permission to install/create them. Do not use docker inspect without a
restrictive format, docker compose config without filtering, or cat .env.

```bash
uname -m
docker version --format 'Server={{.Server.Version}}'
docker compose version --short
docker info --format 'Root={{.DockerRootDir}} Driver={{.Driver}} Cgroup={{.CgroupVersion}}'
nproc
free -m
swapon --show
df -h / /opt
df -i / /opt
docker system df
docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
docker network ls --format 'table {{.Name}}\t{{.Driver}}\t{{.Scope}}'
docker volume ls --format '{{.Name}}'
ss -lnt
getent passwd 10001 || true
getent passwd 10002 || true
getent group 10010 || true
for p in /opt/ashare/test /opt/ashare/test/app /opt/ashare/test/config \
 /opt/ashare/test/config/.env /opt/ashare/test/config/web.env \
 /opt/ashare/test/config/worker.env /opt/ashare/test/archive \
 /opt/ashare/test/cache/nextjs /opt/ashare/test/tmp /opt/ashare/test/tmp/duckdb; do
  if test -e "$p"; then
    stat -c '%F %a %u:%g %n' -- "$p"
    realpath -e -- "$p"
  else
    printf 'MISSING %s\n' "$p"
  fi
done
```

Review amd64, Compose >=2.24.4, actual Docker storage filesystem and free
inodes, project identity/port conflicts, UID/GID conflicts, and canonical
paths (no symlinked directory or env file). Confirm root-owned private
config files 0600, archive 10002:10010 mode 2750, web cache 10001:10010
and scratch 10002:10010 mode 0750. Inspect parent permissions locally.
Inspect the approved external database network with a restricted format:
`docker network inspect --format '{{.Name}} internal={{.Internal}} {{range .Containers}}{{.Name}} {{end}}' NETWORK`.
Require the actual isolated test PostgreSQL service/alias postgres, database
ashare_test, distinct restricted reader/writer identities and no production
database endpoint/container attached to the test network. The existing
guard/CI is not evidence of actual VPS isolation. Do not connect to the
database in this phase. Inspect approved loopback port occupancy; 3000 is
the default, never public-bind 3000 or 5432. Keep host-network OpenResty
and the existing HTTPS/password gate. Never modify a running project's
network, container, volume, data or config to make these checks pass.

## 3. Capacity assessment and acceptance record

Previous proposed VPS: 2 vCPU, 2.5 GB RAM, 30 GB SSD (not measured here).
Compose ceilings are Web 768 MiB / 1.5 CPU, Worker 1024 MiB / 2 CPU;
combined 1792 MiB / 3.5 CPU, excluding PostgreSQL, OpenResty, Docker,
OS and any proxy service. Worker is profile-gated and one-shot, not a
permanent service. Node heap 512 MiB and DuckDB managed-memory 512 MB
are not total RSS limits. Do not assume a 2.5 GB VPS has adequate RAM.
Measure existing usage and later isolated peak RSS/cgroup events,
CPU, swap activity, database latency, disk growth and scratch high-water.
Use docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}} {{.BlockIO}}'
for existing running containers only. Do not start stress tests now.
Proposed initial engineering margins (not measured or approved limits):
leave at least 512 MiB available after measured concurrent peak and
at least 20% free on each relevant filesystem after incoming images,
previous rollback pair, fixture archive, scratch and backup estimates.
Insufficient or unknown capacity blocks launch. Do not prune images,
delete archives, change resource limits, or increase swap automatically.
Plan sequential worker runs and no concurrent backfill/maintenance;
measure again before enabling any scheduler. Record observed values,
calculation, operator decision and date rather than declaring a pass.

## 4. Immutable image and resolved Compose checks (future, after approved pull)

Run from /opt/ashare/test/app, with exact reviewed Compose files and
operator-approved configuration. The image pair must have been verified
and pulled by digest in a separately authorized step. There are no valid
release digests yet. Enter the approved registry references, SHA and
scanned image config IDs from the signed-off release record (not from a
mutable tag or from the locally installed image itself). Commands below
are inspection only; they intentionally fail on missing images, tags,
wrong identity or revision. Do not create placeholder digests.

```bash
set -euo pipefail
read -r -p 'Approved Web package@sha256 digest: ' WEB_PIN
read -r -p 'Approved Worker package@sha256 digest: ' WORKER_PIN
read -r -p 'Approved full source SHA: ' REVIEWED_SHA
read -r -p 'Approved Web image config ID (sha256): ' WEB_ID
read -r -p 'Approved Worker image config ID (sha256): ' WORKER_ID
[[ "$REVIEWED_SHA" =~ ^[0-9a-f]{40}$ ]]
[[ "$WEB_PIN" =~ ^ghcr\.io/lguangming/a-share-value-screener-web-test-[a-z0-9-]+@sha256:[0-9a-f]{64}$ ]]
[[ "$WORKER_PIN" =~ ^ghcr\.io/lguangming/a-share-value-screener-worker-test-[a-z0-9-]+@sha256:[0-9a-f]{64}$ ]]
[[ "$WEB_PIN" != *-test-pr26@* && "$WORKER_PIN" != *-test-pr26@* ]]
for k in web worker; do
  if [[ "$k" == web ]]; then
    pin="$WEB_PIN"; expected="$WEB_ID"; uidgid=10001:10010
  else
    pin="$WORKER_PIN"; expected="$WORKER_ID"; uidgid=10002:10010
  fi
  [[ "$expected" =~ ^sha256:[0-9a-f]{64}$ ]]
  test "$(docker image inspect "$pin" --format '{{.Id}}')" = "$expected"
  test "$(docker image inspect "$pin" --format '{{.Os}}/{{.Architecture}}')" = linux/amd64
  test "$(docker image inspect "$pin" --format '{{.Config.User}}')" = "$uidgid"
  test "$(docker image inspect "$pin" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')" = "$REVIEWED_SHA"
  docker image inspect "$pin" --format '{{range .RepoDigests}}{{println .}}{{end}}' | grep -Fxq "$pin"
  docker image inspect "$pin" --format 'Verified ID={{.Id}} Size={{.Size}} User={{.Config.User}}'
done
cd /opt/ashare/test/app
dc=(docker compose --env-file ../config/.env -f compose.yaml -f compose.loopback.yaml -f compose.vps-test.yaml --profile worker)
"${dc[@]}" config --quiet
"${dc[@]}" config --format json | python3 -I -c '
import json, sys
c=json.load(sys.stdin)
w=c["services"]["web"]; k=c["services"]["worker"]
def check(ok, label):
    if not ok: raise SystemExit("FAIL: "+label)
    print("PASS: "+label)
check(c["name"]=="ashare-test", "project")
check(set(c["services"])=={"web","worker"}, "test application services")
check(w["image"]==sys.argv[1] and k["image"]==sys.argv[2], "approved image pair")
check(w["entrypoint"]==["/nodejs/bin/node","/app/test-web-launch.cjs"], "web guard")
check(k["entrypoint"]==["/usr/local/bin/python3","-m","value_screener.test_worker_launch"], "worker guard")
check(k["command"]==["--help"] and "worker" in k["profiles"], "worker default and profile")
check(c["networks"]["worker_egress"]["internal"] is True, "internal worker egress")
check(k["environment"]["ARCHIVE_LOCAL_ALLOW_MAINTENANCE_APPLY"]=="false", "maintenance apply disabled")
check(w["user"]=="10001:10010" and k["user"]=="10002:10010", "container identities")
for svc in (w,k):
    check(set(svc["cap_drop"])=={"ALL"} and "no-new-privileges:true" in svc["security_opt"], "container security")
    check(not svc.get("privileged",False), "not privileged")
    check(not svc.get("network_mode"), "no host networking")
    check(all(v["type"]=="bind" and v["source"].startswith("/opt/ashare/test/") and not v.get("bind",{}).get("create_host_path",True) for v in svc["volumes"]), "isolated pre-existing binds")
def mounts(svc):
    return {v["target"]:(v["source"],v.get("read_only",False)) for v in svc["volumes"]}
check(mounts(w)=={"/data/archive":("/opt/ashare/test/archive",True),"/app/.next/cache":("/opt/ashare/test/cache/nextjs",False)}, "web mount contract")
check(mounts(k)=={"/data/archive":("/opt/ashare/test/archive",False),"/data/tmp":("/opt/ashare/test/tmp",False)}, "worker mount contract")
check(len(w["ports"])==1 and w["ports"][0]["host_ip"]=="127.0.0.1" and int(w["ports"][0]["target"])==3000 and not k.get("ports"), "loopback only")
check(set(w["networks"])=={"database","web_loopback"} and set(k["networks"])=={"database","worker_egress"}, "network membership")
check(c["networks"]["database"]["external"] is True, "external DB network")
' "$WEB_PIN" "$WORKER_PIN"
```

Do not use set -x, redirect raw resolved JSON to disk, or paste Compose
configuration in chat: it can contain passwords. The check prints only
labels, not values. A Compose output/schema discrepancy must be reviewed,
not bypassed. Compare external network name and loopback published port
against the approved host record separately. Verify image entrypoint/command
against reviewed Dockerfile and use the existing CI shell-free identity
checks in the later authorized offline container validation. The static
checks are not a database or actual runtime readiness test.

## 5. Deployment and rollback rehearsal plan (NOT executable authorization)

Before first launch: record the exact previous verified image pair,
reviewed Compose SHA and env-file backup identity privately, current
project/containers, mounts, health status and database/archive backup
restore evidence. No previous verified pair means first deployment rollback
is stop-only. Never use a mutable tag as a rollback target. Ensure the
test database/fixture archive are isolated and separately approved.
Only then may a later authorized operator use base + loopback + VPS-test
overlay, start Web only, and wait for health. Confirm actual bind address,
non-root identity, memory/CPU and read-only web archive. Liveness /login
is not readiness: use deploy/check-readiness.cjs with approved HTTPS URL,
private password, known fixture stock/query/year; verify login, Secure
Cookie, SQL, bars/factors, archive and snapshot freshness. Never set
ASHARE_TEST_ALLOW_STALE for actual VPS acceptance. Worker fixture run
requires separate permission and cannot enable provider egress or
maintenance apply. No production cutover or schedule.

Rollback triggers: failed auth/readiness, wrong digest/revision/identity,
unexpected egress or public port, SQL/archive inconsistency, repeated
health failures, OOM or resource threshold breach. Stop the isolated
ashare-test project only using the same reviewed overlay/config; no -v,
no docker system prune, no network/volume removal, no production project
actions. If a previous verified pair exists, restore only its reviewed
configuration, verify both digests again and repeat readiness. Otherwise
keep the test project stopped. App rollback does not reverse database or
archive writes; recovery from verified backup is a separate authorized
operation. Preserve logs and data, do not publish or switch traffic.

## 6. Acceptance checklist

| Item | Phase 11E-13 status |
| --- | --- |
| Default Minimal and VPS-test overlay technical baseline | CI verified in 11E-12 |
| OpenSSL and strict release candidate gate | BLOCKED |
| PR27 shell-free workflow and final source/target adaptation | Planned, not modified |
| Registry pair / image config IDs / approved source | Not available |
| Read-only host preflight / capacity measurement | Prepared, not executed |
| Resolved Compose and digest inspection | Prepared, not executed |
| Actual VPS test / HTTPS readiness / rollback drill | Not executed |
| Production data, schedule, publication and deployment | Not authorized |

This runbook adds no workflow trigger or release permission. Its command
examples have not been executed on the VPS; actual preflight evidence and
security clearance remain mandatory. Do not use this document as a green
release certificate.

Current acceptance supersedes the historical dual-version baseline: Python
3.13 and PostgreSQL 18.7 only, with CPython 3.13.18 Minimal runtime and all
11E-14A scoped security gates. No separate Python 3.11 acceptance requirement.
