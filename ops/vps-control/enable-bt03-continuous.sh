#!/bin/bash
# Local administrator only, run from exact reviewed CI-passed checkout.
set -Eeuo pipefail
test "$(id -u)" = 0
root="$(cd -- "$(dirname -- "$0")" && pwd -P)"
cfg=/etc/ashare-control
dest=/opt/ashare-control
test -f "$cfg/maintenance.json"
test -f "$cfg/research-worker.json"
test -f "$cfg/config.json"
test -f "$dest/maintenance_runner.py"
test ! -L "$dest/profiles/bt03-maintain-all"
units=(ashare-vps-control ashare-research-worker)
active=()
for unit in "${units[@]}"; do
  if systemctl is-active --quiet "$unit.timer"; then active+=("$unit.timer"); fi
done
backup="$(mktemp -d /root/ashare-bt03-bulk-backup.XXXXXX)"
chmod 0700 "$backup"
changed=0
finish() {
  rc=$?
  trap - EXIT
  set +e
  if (( rc != 0 && changed )); then
    echo "Restoring previous configuration and runner"
    cp -a "$backup/maintenance_runner.py" "$dest/maintenance_runner.py" || rc=1
    cp -a "$backup/config.json" "$cfg/config.json" || rc=1
    cp -a "$backup/research-worker.json" "$cfg/research-worker.json" || rc=1
    if test -f "$backup/bt03-maintain-all"; then
      cp -a "$backup/bt03-maintain-all" "$dest/profiles/bt03-maintain-all" || rc=1
    else
      rm -f -- "$dest/profiles/bt03-maintain-all" || rc=1
    fi
  fi
  for timer in "${active[@]}"; do
    systemctl start "$timer" || rc=1
    systemctl is-active --quiet "$timer" || rc=1
  done
  systemctl list-timers 'ashare-*' --all
  echo "Backup: $backup"
  if (( rc == 0 )); then echo "BT03 CONTINUOUS PROFILE READY"; fi
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
        raise SystemExit("Outstanding task; finish it before upgrade")
PY
cp -a "$dest/maintenance_runner.py" "$cfg/config.json" "$cfg/research-worker.json" "$backup/"
if test -e "$dest/profiles/bt03-maintain-all"; then
  test -f "$dest/profiles/bt03-maintain-all"
  cp -a "$dest/profiles/bt03-maintain-all" "$backup/"
fi
changed=1
install -o root -g root -m 0644 "$root/maintenance_runner.py" "$dest/maintenance_runner.py"
printf '#!/bin/sh\nexec /usr/bin/python3 -I /opt/ashare-control/maintenance_runner.py --continuous\n' > "$backup/new-wrapper"
install -o root -g root -m 0755 "$backup/new-wrapper" "$dest/profiles/bt03-maintain-all"
runuser -u ashare-research -- /usr/bin/python3 -I -c '
import sys,hashlib
sys.path.insert(0,"/opt/ashare-control")
import maintenance_runner as m,bridge
d=m.load()
assert "existing_market" in d and all(b["stage"]=="security" for b in d["batches"])
p=m.BASE/d["dataset"]/"maintenance-state.json"
s=bridge.bounded_json(p)
assert s["plan_sha256"]==hashlib.sha256(bridge.dump(d).encode()).hexdigest()
assert type(s["completed"]) is int and 0<s["completed"]<=len(d["batches"])
print("Verified checkpoint:",s["completed"],"/",len(d["batches"]))
'
python3 - <<'PY'
import json,os,tempfile
from pathlib import Path
cfg=Path("/etc/ashare-control")
b=cfg/"config.json";w=cfg/"research-worker.json"
bridge=json.loads(b.read_text());worker=json.loads(w.read_text())
assert worker["repository_id"]==bridge["repository_id"]
assert bridge["async_profiles"]["bt03-maintain"]=="research"
assert "bt03-maintain" in worker["profiles"]
worker["profiles"]["bt03-maintain-all"]={"script":"/opt/ashare-control/profiles/bt03-maintain-all","timeout_seconds":1500}
bridge["async_profiles"]["bt03-maintain-all"]="research"
def save(p,d):
    st=p.stat()
    with tempfile.NamedTemporaryFile(mode="w",dir=p.parent,delete=False) as f:
        json.dump(d,f,indent=2);f.write("\n");f.flush()
        os.fchown(f.fileno(),st.st_uid,st.st_gid)
        os.fchmod(f.fileno(),0o640);os.fsync(f.fileno());tmp=f.name
    os.replace(tmp,p)
save(w,worker);save(b,bridge)
PY
runuser -u ashare-research -- /usr/bin/python3 -I -c '
import sys
sys.path.insert(0,"/opt/ashare-control")
import worker
from pathlib import Path
assert "bt03-maintain-all" in worker.config(Path("/etc/ashare-control/research-worker.json"),"research")["profiles"]
'
runuser -u ashare-control -- /usr/bin/python3 -I -c '
import sys
sys.path.insert(0,"/opt/ashare-control")
import bridge
assert bridge.config("/etc/ashare-control/config.json")["async_profiles"]["bt03-maintain-all"]=="research"
'
echo "Continuous profile authorized; no maintenance job started by installer"
