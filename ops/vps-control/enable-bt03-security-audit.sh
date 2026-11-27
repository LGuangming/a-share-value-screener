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
test -f "$dest/bridge.py"
test -x /opt/ashare-research-runtime/bin/python
test ! -L "$dest/security_audit.py"
test ! -L "$dest/profiles/bt03-security-audit"
units=(ashare-vps-control ashare-research-worker)
active=()
for unit in "${units[@]}"; do
  if systemctl is-active --quiet "$unit.timer"; then active+=("$unit.timer"); fi
done
backup="$(mktemp -d /root/ashare-bt03-audit-backup.XXXXXX)"
chmod 0700 "$backup"
changed=0
finish() {
  rc=$?
  trap - EXIT
  set +e
  if (( rc != 0 && changed )); then
    echo "Restoring previous configuration and runner"
    cp -a "$backup/bridge.py" "$dest/bridge.py" || rc=1
    if test -f "$backup/security_audit.py"; then
      cp -a "$backup/security_audit.py" "$dest/security_audit.py" || rc=1
    else
      rm -f -- "$dest/security_audit.py" || rc=1
    fi
    cp -a "$backup/config.json" "$cfg/config.json" || rc=1
    cp -a "$backup/research-worker.json" "$cfg/research-worker.json" || rc=1
    if test -f "$backup/bt03-security-audit"; then
      cp -a "$backup/bt03-security-audit" "$dest/profiles/bt03-security-audit" || rc=1
    else
      rm -f -- "$dest/profiles/bt03-security-audit" || rc=1
    fi
  fi
  for timer in "${active[@]}"; do
    systemctl start "$timer" || rc=1
    systemctl is-active --quiet "$timer" || rc=1
  done
  systemctl list-timers 'ashare-*' --all
  echo "Backup: $backup"
  if (( rc == 0 )); then echo "BT03 SECURITY AUDIT READY"; fi
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
cp -a "$dest/bridge.py" "$cfg/config.json" "$cfg/research-worker.json" "$backup/"
if test -f "$dest/security_audit.py"; then cp -a "$dest/security_audit.py" "$backup/"; fi
if test -e "$dest/profiles/bt03-security-audit"; then
  test -f "$dest/profiles/bt03-security-audit"
  cp -a "$dest/profiles/bt03-security-audit" "$backup/"
fi
changed=1
install -o root -g root -m 0644 "$root/bridge.py" "$root/security_audit.py" "$dest/"
printf '#!/bin/sh\nexec /opt/ashare-research-runtime/bin/python -I /opt/ashare-control/security_audit.py\n' > "$backup/new-wrapper"
install -o root -g root -m 0755 "$backup/new-wrapper" "$dest/profiles/bt03-security-audit"
runuser -u ashare-research -- /usr/bin/python3 -I -c '
import sys,hashlib
sys.path.insert(0,"/opt/ashare-control")
import maintenance_runner as m,bridge
d=m.load()
assert "existing_market" in d and all(b["stage"]=="security" for b in d["batches"])
p=m.BASE/d["dataset"]/"maintenance-state.json"
s=bridge.bounded_json(p)
assert s["plan_sha256"]==hashlib.sha256(bridge.dump(d).encode()).hexdigest()
assert s["completed"]==len(d["batches"])==72
assert d["python"]=="/opt/ashare-research-runtime/bin/python"
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
worker["profiles"]["bt03-security-audit"]={"script":"/opt/ashare-control/profiles/bt03-security-audit","timeout_seconds":1500}
bridge["async_profiles"]["bt03-security-audit"]="research"
bridge.setdefault("reports",{})["bt03-security-quality"]={"kind":"security_audit","file":"/var/lib/ashare-control-spool/research/results/bt03-security-audit.json"}
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
assert "bt03-security-audit" in worker.config(Path("/etc/ashare-control/research-worker.json"),"research")["profiles"]
'
runuser -u ashare-control -- /usr/bin/python3 -I -c '
import sys
sys.path.insert(0,"/opt/ashare-control")
import bridge
assert bridge.config("/etc/ashare-control/config.json")["async_profiles"]["bt03-security-audit"]=="research"
'
runuser -u ashare-research -- /opt/ashare-research-runtime/bin/python -I -c '
import sys,duckdb
sys.path.insert(0,"/opt/ashare-control")
import security_audit
print("Audit runtime:",duckdb.__version__)
'
echo "Security audit profile authorized; no audit started by installer"
