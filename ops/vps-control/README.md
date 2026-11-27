# VPS control bridge v1 — private GitHub queue

This is an alternative transport to custom MCP. It is NOT an MCP server,
SSH server, arbitrary remote terminal, deployment service or production
runner. Full custom MCP writes may depend on the ChatGPT plan/workspace.
The already connected GitHub app can create issues and read results.
The VPS polls GitHub outbound HTTPS only. No public listener.

The A-Share source repository is public: never use it as the task queue.
Create a separate **private** repository manually, e.g. ashare-vps-control.
Do not store VPS secrets, raw data, source logs, credentials or configs in it.
Only invite trusted operators. GitHub API credentials on the VPS must be
fine-grained, scoped to that private repository, with Issues read/write and
Metadata read; no Actions, Contents write, admin or production permissions.
Use expiration and rotation. Never paste tokens into chat or GitHub issues.

## Install after reviewing the exact branch/commit and CI

Use a separate checkout of the approved source revision, not the live
production directory. Commands below require root for installation only;
the service refuses to run as root. Linux/systemd and Python 3.10+ required.

    # Run from the approved source checkout root.
    python3 -m unittest discover -s ops/vps-control -p 'test_*.py' -v
    id ashare-control >/dev/null 2>&1 || useradd --system --user-group --shell /usr/sbin/nologin ashare-control
    install -d -o root -g root -m 0755 /opt/ashare-control /opt/ashare-control/profiles
    install -d -o root -g ashare-control -m 0750 /etc/ashare-control
    install -o root -g root -m 0644 ops/vps-control/bridge.py /opt/ashare-control/bridge.py
    install -o root -g ashare-control -m 0640 ops/vps-control/config.example.json /etc/ashare-control/config.json
    install -o root -g root -m 0644 ops/vps-control/ashare-vps-control.service /etc/systemd/system/
    install -o root -g root -m 0644 ops/vps-control/ashare-vps-control.timer /etc/systemd/system/

Edit /etc/ashare-control/config.json locally with the new private repository
full name and its numeric repository ID, plus the numeric user IDs allowed
to submit requests. The zero placeholders deliberately fail validation.
With a trusted authenticated GitHub CLI, obtain these locally:

    gh api repos/OWNER/ashare-vps-control --jq '{id,private,visibility}'
    gh api user --jq '{id,login}'

The linked GitHub app must be granted access to this new private repository
as well. Keep profiles empty for initial testing. Do not add the service user
to docker, sudo, root or production database groups.

Use a secure local editor to create /etc/ashare-control/github-token
containing only the fine-grained token, with root ownership and mode 0600.
Systemd LoadCredential supplies it to the bridge; it is not a subprocess
argument value or environment secret. Local profile scripts are trusted
code and run with the same service identity; do not let untrusted scripts
or users read service credentials/state. Restrict outgoing network as
appropriate for the chosen local profiles.

    chmod 0600 /etc/ashare-control/github-token
    systemctl daemon-reload
    systemctl start ashare-vps-control.service
    systemctl status ashare-vps-control.service --no-pager
    journalctl -u ashare-vps-control.service -n 30 --no-pager

One-shot service may show inactive after a successful poll. Only enable
the timer after configuration and status probe have been checked:

    systemctl enable --now ashare-vps-control.timer
    systemctl list-timers ashare-vps-control.timer

## Task protocol

Create a NEW issue in the designated private repo:

Title: VPS CONTROL v1

Body (exact raw JSON, no Markdown fence, no other fields):

    {"schema":"ashare-vps-control-v1","task":"status"}

The bridge checks repo privacy and pinned repo ID, pinned numeric author ID,
exact schema, open issue, creation age <= 15 minutes and unchanged request
on refetch. It polls recent open issues (first 100) and processes one request
per service activation. It ignores old, malformed and unauthorized issues.
It posts a VPS RESULT v1 JSON comment and closes the issue. Results contain
only bounded metrics/status, never logs, hostname, IP, env or file content.
A ChatGPT GitHub connector may need the user to explicitly authorize issue
creation. A later conversation can read the issue result; not synchronous SSH.

## Optional local approved execution profiles

Only after status-only acceptance, a local root administrator may place a
reviewed fixed script in /opt/ashare-control/profiles/ (root owned, 0755,
all parent directories root owned and non-group/world-writable). Example
config addition (not enabled by default):

    "profiles": {
      "bt05-preflight": {
        "script": "/opt/ashare-control/profiles/bt05-preflight",
        "timeout_seconds": 1800
      }
    }

The profile is a fixed local program with fixed paths/parameters. No remote
arguments, dynamic shell, remote script contents, git pull, arbitrary file
reads, Docker socket, sudo or root operations. Scripts are not supplied in
v1; they must be separately reviewed against actual VPS layout and accepted
backtest inputs. The service inherits no token/environment into its child,
but locally trusted profiles share the service UID and must never access
credentials/state. ProtectSystem=strict; if needed, configure a tightly
scoped systemd ReadWritePaths drop-in for a dedicated output directory,
and grant the service user only the necessary filesystem access.
No production write directory, no general docker group.
 
To request a locally enabled profile:

    {"schema":"ashare-vps-control-v1","task":"run_profile","profile":"bt05-preflight"}

This request is explicit authorization for that fixed profile only; no
arbitrary commands. The timeout is at most 7200 s, single in-flight task,
1 CPU/1 GiB service resource cap. Child stdout/stderr are not transmitted;
profile-owned private logs can be inspected locally. Only exit status is
reported. Never treat exit zero as independently verified backtest results.

## Delivery, audit and stop

Durable state /var/lib/ashare-control records issue IDs, request SHA256,
execution status and delivery state. Reporting failure retries delivery,
not task execution. A crash after starting a task is marked interrupted
unknown; do not automatically retry. Duplicate result comments can occur
if interrupted after posting but before storing delivery state. New issues
are new requests; do not submit duplicate execution issues casually.
State is security-critical; do not delete it to retry a job.

    systemctl disable --now ashare-vps-control.timer
    systemctl stop ashare-vps-control.service

Revoke the fine-grained token and remove the linked app's access if needed.
Keep the private issue/state audit history. This service has no production
deploy hook. No automatic installation, credential configuration, VPS access
or real task execution has been done by this PR.

## Future MCP

An authenticated remote MCP can eventually call the same narrowly scoped
task layer, subject to account support and OAuth/secure tunnel review.
Do not expose this bridge itself as a public HTTP command endpoint.
