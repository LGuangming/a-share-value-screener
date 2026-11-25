#!/usr/bin/env bash
# Read-only local administrator inventory. No pulls, SQL, starts or writes.
set -euo pipefail
[[ "$(id -u)" == 0 ]] || { echo 'Run as local root'; exit 64; }
export LC_ALL=C
unset DOCKER_HOST DOCKER_CONTEXT
image='postgres@sha256:0115267ab5a509022df9319c77be365cef335adb71556c75f60f5360ee247e71'
block=0
review=0
d(){ /usr/bin/docker --host unix:///var/run/docker.sock "$@"; }
fail(){ printf 'BLOCK: %s\n' "$*"; block=$((block+1)); }
note(){ printf 'REVIEW: %s\n' "$*"; review=$((review+1)); }
pass(){ printf 'PASS: %s\n' "$*"; }
echo '===== SYSTEM ====='
date -u
uname -m
nproc
free -m
[[ "$(uname -m)" == x86_64 ]] && pass 'tested CPU architecture' || fail 'unverified platform'
[[ "$(stat -fc %T /sys/fs/cgroup)" == cgroup2fs ]] && pass 'cgroup v2' || fail 'cgroup v2 required'
if [[ -r /sys/fs/cgroup/cgroup.controllers ]] && grep -qw memory /sys/fs/cgroup/cgroup.controllers; then pass 'memory controller'; else fail 'memory controller unavailable'; fi
mem=$(awk '/^MemAvailable:/{print $2}' /proc/meminfo)
[[ "$mem" =~ ^[0-9]+$ ]] || exit 1
(( mem >= 1572864 )) && pass 'available memory >= 1536 MiB' || fail 'available memory below 1536 MiB'
echo '===== PATHS ====='
for p in / /opt /opt/ashare; do
  if [[ ! -d "$p" || -L "$p" ]]; then fail "$p absent or unsafe"; continue; fi
  stat -c '%n type=%F uid=%u gid=%g mode=%a' "$p"
  mode=$(stat -c %a "$p")
  if [[ "$(stat -c %u "$p")" == 0 ]] && (( (8#$mode & 022) == 0 )); then pass "$p ownership"; else fail "$p writable by non-root or non-root owned"; fi
done
if [[ -e /opt/ashare/migration || -L /opt/ashare/migration ]]; then
  stat -c '%n type=%F uid=%u gid=%g mode=%a' /opt/ashare/migration
  fail 'migration root already exists'
else pass 'migration root absent'; fi
echo '===== DOCKER / DISK ====='
d version --format 'server={{.Server.Version}}'
d compose version
d info --format 'cgroup={{.CgroupVersion}} driver={{.Driver}}'
docker_root=$(d info --format '{{.DockerRootDir}}')
[[ -n "$docker_root" && -d "$docker_root" ]] || exit 1
echo "Docker root: $docker_root"
for p in /opt/ashare "$docker_root"; do
  [[ -d "$p" ]] || continue
  df -hP "$p"
  df -iP "$p"
  bytes=$(df -B1 --output=avail "$p" | tail -1 | tr -d ' ')
  [[ "$bytes" =~ ^[0-9]+$ ]] || exit 1
  (( bytes >= 30*1024*1024*1024 )) && pass "$p free >= 30 GiB" || fail "$p free below conservative 30 GiB gate"
done
if [[ -d /opt/ashare && "$(stat -c %d /opt/ashare)" != "$(stat -c %d "$docker_root")" ]]; then note 'separate filesystems: calculate budget for each'; fi
echo '===== PORT / RESOURCE NAMES ====='
listeners=$(ss -H -ltn 'sport = :55432')
if [[ -n "$listeners" ]]; then printf '%s\n' "$listeners"; fail 'TCP 55432 occupied'; else pass 'TCP 55432 no current listeners'; fi
# Include stopped containers' configured published ports, not only current listeners.
ids=$(d ps -aq)
if [[ -n "$ids" ]]; then
  while IFS= read -r cid; do
    ports=$(d inspect --type container --format '{{.Name}} {{json .HostConfig.PortBindings}}' "$cid")
    if [[ "$ports" == *'"HostPort":"55432"'* ]]; then echo "$ports"; fail '55432 configured in an existing container'; fi
  done <<< "$ids"
fi
containers=$(d ps -a --format '{{.Names}}')
volumes=$(d volume ls --format '{{.Name}}')
networks=$(d network ls --format '{{.Name}}')
projects=$(d ps -a --filter label=com.docker.compose.project=ashare-migration --format '{{.Names}}')
if grep -Fxq ashare-migration-postgres <<< "$containers"; then fail 'container name exists'; else pass 'container name available'; fi
if grep -Fxq ashare-migration-pgdata <<< "$volumes"; then fail 'volume name exists'; else pass 'volume name available'; fi
if grep -Fxq ashare-migration-private <<< "$networks"; then fail 'network name exists'; else pass 'network name available'; fi
[[ -z "$projects" ]] && pass 'migration project has no containers' || fail 'migration project already exists'
echo '===== LOCAL IMAGE ====='
if info=$(d image inspect "$image" --format '{{.Id}}|{{.Os}}|{{.Architecture}}|{{json .RepoDigests}}' 2>/dev/null); then
  echo "$info"
  # Match only exact repository digests, not a tag or image ID alone.
  if printf '%s\n' "$info" | /usr/bin/python3 -c 'import json,sys,re; i,o,a,r=sys.stdin.read().strip().split("|",3); d=sys.argv[1].split("@")[1]; ok={x+"@"+d for x in ("postgres","library/postgres","docker.io/library/postgres","index.docker.io/library/postgres")};sys.exit(0 if re.fullmatch(r"sha256:[0-9a-f]{64}",i) and o=="linux" and a=="amd64" and ok.intersection(json.loads(r) or []) else 1)' "$image"; then pass 'approved local digest and platform'; else fail 'local image digest or platform mismatch'; fi
else
  note 'approved digest not cached; no pull performed'
  d image inspect postgres:18.7-alpine --format 'TAG_ONLY={{.Id}} OS={{.Os}} ARCH={{.Architecture}} DIGESTS={{json .RepoDigests}}' 2>/dev/null || true
fi
echo '===== ACCOUNTS ====='
for u in ashare-ops ashare-migrate; do
  if id "$u" >/dev/null 2>&1; then
    id "$u"
    getent passwd "$u" | cut -d: -f1,3,4,7
    if runuser -u "$u" -- test -w /var/run/docker.sock; then fail "$u can write Docker socket"; else pass "$u cannot write Docker socket"; fi
    if id -nG "$u" | tr ' ' '\n' | grep -Eq '^(docker|sudo|wheel|lxd|incus-admin)$'; then fail "$u has privileged group"; fi
    if [[ "$u" == ashare-migrate ]]; then
      shell=$(getent passwd "$u" | cut -d: -f7)
      if [[ "$(id -u "$u")" -ge 100 && "$(id -g "$u")" -ne 0 && "$shell" =~ ^(/usr/sbin/nologin|/sbin/nologin|/bin/false)$ ]]; then note 'existing migration user: confirm account provenance and sudo policy'; else fail 'migration account not restricted'; fi
    fi
  else
    if [[ "$u" == ashare-migrate ]]; then pass 'migration account absent'; else fail 'existing ops account absent'; fi
  fi
done
echo '===== NETWORK BASELINE ====='
d network ls --format '{{.Name}} {{.Driver}}'
if grep -Fxq ashare-db <<< "$networks"; then
  d network inspect ashare-db --format 'production network driver={{.Driver}} internal={{.Internal}}'
else note 'expected production network not found'; fi
d ps -a --filter name='^/ashare-postgres$' --format '{{.Names}} {{.Status}} {{.Ports}}'
note 'review local sudo/forced-command policy, Docker version and routes; future network reachability/egress tests require separate approval'
printf 'PRECHECK COMPLETE blocks=%s reviews=%s (not initialization approval)\n' "$block" "$review"
(( block == 0 )) || exit 2
