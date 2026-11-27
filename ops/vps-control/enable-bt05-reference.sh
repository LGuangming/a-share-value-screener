#!/bin/bash
# Root-only local setup; use a CI-passed control checkout.
set -Eeuo pipefail
test "$(id -u)" = 0
root="$(cd -- "$(dirname -- "$0")" && pwd -P)"
cfg=/etc/ashare-control
dest=/opt/ashare-control
source=/opt/ashare-bt05-source
runtime=/opt/ashare-research-runtime/bin/python
revision=f74623aac69d46de06e915607d2ce0750c4f0275
test -x "$runtime"
test -d "$source/.git"
test "$(git -C "$source" rev-parse HEAD)" = "$revision"
test -z "$(git -C "$source" status --porcelain --untracked-files=no)"
test -f "$cfg/collection-worker.json"
test -f "$dest/collection_runner.py"
test -f "$dest/profiles/collect-reference"
test ! -L "$cfg/tushare-token"
test ! -L "$cfg/collection.json"
# Do not request or print an existing secret; create only if absent.
if ! test -e "$cfg/tushare-token"; then
  test -t 0 || { echo "A local interactive terminal is required for credential setup" >&2; exit 1; }
  IFS= read -r -s -p 'Official Tushare token (local input only): ' token
  printf '\n'
  test -n "$token"
  (umask 077; set -o noclobber; printf '%s\n' "$token" > "$cfg/tushare-token")
  unset token
fi
test -s "$cfg/tushare-token"
test "$(stat -c '%u:%g:%a' "$cfg/tushare-token")" = "0:0:600"
units=(ashare-vps-control ashare-collection-worker)
active=()
for unit in "${units[@]}"; do
  if systemctl is-active --quiet "$unit.timer"; then active+=("$unit.timer"); fi
done
backup="$(mktemp -d /root/ashare-bt05-collection-backup.XXXXXX)"
chmod 0700 "$backup"
changed=0
finish() {
  rc=$?
  trap - EXIT
  set +e
  if (( rc != 0 && changed )); then
    cp -a "$backup/config.json" "$cfg/config.json" || rc=1
    cp -a "$backup/collection-worker.json" "$cfg/collection-worker.json" || rc=1
    cp -a "$backup/collection_runner.py" "$dest/collection_runner.py" || rc=1
    cp -a "$backup/collect-reference" "$dest/profiles/collect-reference" || rc=1
    cp -a "$backup/bridge.py" "$dest/bridge.py" || rc=1
    if test -f "$backup/collection.json"; then
      cp -a "$backup/collection.json" "$cfg/collection.json" || rc=1
    else
      rm -f -- "$cfg/collection.json" || rc=1
    fi
  fi
  for timer in "${active[@]}"; do
    systemctl start "$timer" || rc=1
    systemctl is-active --quiet "$timer" || rc=1
  done
  if (( rc == 0 )); then
    systemctl enable --now ashare-collection-worker.timer || rc=1
    systemctl is-active --quiet ashare-collection-worker.timer || rc=1
  fi
  systemctl list-timers 'ashare-*' --all
  echo "Backup: $backup"
  if (( rc == 0 )); then echo "BT05 REFERENCE COLLECTION READY"; fi
  exit "$rc"
}
trap finish EXIT
trap 'echo "Error on line $LINENO" >&2' ERR
for unit in "${units[@]}"; do systemctl stop "$unit.timer"; done
for unit in "${units[@]}"; do
  for i in $(seq 1 30); do
    case "$(systemctl show -p ActiveState --value "$unit.service")" in
      inactive|failed) break ;;
      *) sleep 1 ;;
    esac
  done
  case "$(systemctl show -p ActiveState --value "$unit.service")" in
    inactive|failed) ;;
    *) echo "Active job: $unit; retry after completion" >&2; exit 1 ;;
  esac
done
python3 - <<'PY'
import json
from pathlib import Path
for p in Path("/var/lib/ashare-control").glob("issue-*.json"):
    d=json.loads(p.read_text())
    if d.get("delivery") in ("queued","running"):
        raise SystemExit("Outstanding task; finish before upgrade")
PY
cp -a "$cfg/config.json" "$cfg/collection-worker.json" "$dest/bridge.py" \
  "$dest/collection_runner.py" "$dest/profiles/collect-reference" "$backup/"
if test -f "$cfg/collection.json"; then cp -a "$cfg/collection.json" "$backup/"; fi
changed=1
install -o root -g root -m 0644 "$root/bridge.py" "$root/collection_runner.py" "$dest/"
printf '#!/bin/sh\nexec /opt/ashare-research-runtime/bin/python -I /opt/ashare-control/collection_runner.py reference\n' > "$backup/new-wrapper"
install -o root -g root -m 0755 "$backup/new-wrapper" "$dest/profiles/collect-reference"
python3 - <<'PY'
import grp,json,os,tempfile
from pathlib import Path
cfg=Path("/etc/ashare-control")
b=cfg/"config.json";w=cfg/"collection-worker.json";c=cfg/"collection.json"
bridge=json.loads(b.read_text());worker=json.loads(w.read_text())
assert worker["repository_id"] in (0,bridge["repository_id"])
plan={"python":"/opt/ashare-research-runtime/bin/python",
      "backend":"/opt/ashare-bt05-source/backend","start":"2020-01-01","end":"2025-12-31",
      "calendar":None,"reference_budget":30,"dividend_budget":1,"daily_budget":31}
if c.exists() and json.loads(c.read_text())!=plan:
    raise SystemExit("Existing collection plan differs; refusing overwrite")
worker["repository_id"]=bridge["repository_id"]
worker["profiles"]["collect-reference"]={"script":"/opt/ashare-control/profiles/collect-reference","timeout_seconds":1800}
bridge.setdefault("async_profiles",{})["collect-reference"]="collection"
bridge.setdefault("reports",{})["bt05-reference-progress"]={"kind":"reference_capture","file":"/var/lib/ashare-control-spool/collection/results/bt05-reference.json"}
def save(p,d,gid=None):
    if gid is None:gid=p.stat().st_gid
    with tempfile.NamedTemporaryFile(mode="w",dir=p.parent,delete=False) as f:
        json.dump(d,f,indent=2);f.write("\n");f.flush()
        os.fchown(f.fileno(),0,gid);os.fchmod(f.fileno(),0o640);os.fsync(f.fileno());tmp=f.name
    os.replace(tmp,p)
save(c,plan,grp.getgrnam("ashare-control-collection").gr_gid)
save(w,worker);save(b,bridge)
PY
runuser -u ashare-collection -- "$runtime" -I -c '
import sys
sys.path.insert(0,"/opt/ashare-control")
import collection_runner,worker
from pathlib import Path
d=collection_runner.load()
sys.path.insert(0,d["backend"])
from value_screener import bt05_reference_capture
assert "collect-reference" in worker.config(Path("/etc/ashare-control/collection-worker.json"),"collection")["profiles"]
print("Collection runtime/configuration verified")
'
runuser -u ashare-control -- /usr/bin/python3 -I -c '
import sys
sys.path.insert(0,"/opt/ashare-control")
import bridge
d=bridge.config("/etc/ashare-control/config.json")
assert d["async_profiles"]["collect-reference"]=="collection"
assert d["reports"]["bt05-reference-progress"]["kind"]=="reference_capture"
'
echo "Only reference capture authorized. No provider request started by installer."
