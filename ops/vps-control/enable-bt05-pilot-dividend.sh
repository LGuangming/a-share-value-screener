#!/bin/bash
# Explicit local approval of pilot reference, then enable fixed dividend capture.
# --prepare only checks evidence; --approve SHA256 explicitly approves that file.
set -Eeuo pipefail
test "$(id -u)" = 0
root="$(cd -- "$(dirname -- "$0")" && pwd -P)"
cfg=/etc/ashare-control
dest=/opt/ashare-control
source=/opt/ashare-bt05-source
runtime=/opt/ashare-research-runtime/bin/python
base=/var/lib/ashare-collection-work
reviewed="$cfg/pilot-reference"
test "$(git -C "$source" rev-parse HEAD)" = f74623aac69d46de06e915607d2ce0750c4f0275
test -z "$(git -C "$source" status --porcelain --untracked-files=no)"
test -f "$cfg/tushare-token"
test -f "$cfg/collection.json"
test -f "$dest/profiles/collect-dividend"
test ! -L "$dest/profiles/collect-dividend"
test ! -L "$reviewed"
prepare() {
  runuser -u ashare-collection -- "$runtime" -I "$root/pilot_reference_review.py" \
    --backend "$source/backend" --archive "$base/reference" \
    --candidate "$base/reference-candidate.json" --output "$base/pilot-candidate.json"
}
if test "$#" = 1 && test "$1" = --prepare; then
  prepare
  exit
fi
test "$#" = 2 && test "$1" = --approve
[[ "$2" =~ ^[0-9a-f]{64}$ ]]
approved="$2"
units=(ashare-vps-control ashare-collection-worker)
active=()
for unit in "${units[@]}"; do
  if systemctl is-active --quiet "$unit.timer"; then active+=("$unit.timer"); fi
done
backup="$(mktemp -d /root/ashare-bt05-dividend-backup.XXXXXX)"
chmod 0700 "$backup"
changed=0
finish() {
  rc=$?
  trap - EXIT
  set +e
  if (( rc != 0 && changed )); then
    for name in config.json collection.json collection-worker.json; do cp -a "$backup/$name" "$cfg/$name" || rc=1; done
    for name in bridge.py collection_runner.py; do cp -a "$backup/$name" "$dest/$name" || rc=1; done
    cp -a "$backup/collect-dividend" "$dest/profiles/collect-dividend" || rc=1
  fi
  for timer in "${active[@]}"; do
    systemctl start "$timer" || rc=1
    systemctl is-active --quiet "$timer" || rc=1
  done
  systemctl list-timers 'ashare-*' --all
  echo "Backup: $backup"
  if (( rc == 0 )); then echo "BT05 PILOT DIVIDEND READY"; fi
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
    *) echo "Active job; retry after completion" >&2; exit 1 ;;
  esac
done
python3 - <<'PY'
import json
from pathlib import Path
for p in Path("/var/lib/ashare-control").glob("issue-*.json"):
    if json.loads(p.read_text()).get("delivery") in ("queued","running"):
        raise SystemExit("Outstanding job; finish before upgrade")
PY
prepare
# Read as collection account, not root-followed paths; hash exact copied bytes.
runuser -u ashare-collection -- cat "$base/pilot-candidate.json" > "$backup/pilot-candidate.json"
test "$(sha256sum "$backup/pilot-candidate.json" | cut -d' ' -f1)" = "$approved"
mkdir "$backup/export"
PYTHONPATH="$source/backend:$root" PYTHONDONTWRITEBYTECODE=1 "$runtime" -P - "$backup" "$approved" <<'PY'
import json,sys
from pathlib import Path
from value_screener import bt05_reference_capture as ref
import pilot_reference_review as pilot
base=Path(sys.argv[1]);h=sys.argv[2]
candidate=base/"pilot-candidate.json"
assert ref.sha256(candidate)==h
doc=json.loads(candidate.read_text())
pilot.check(doc)
review=base/"review.json"
# Explicit invocation by root is the local operator approval; not certification.
ref.write_new(review,{"status":"OPERATOR_REVIEWED_REFERENCE_NOT_CERTIFIED",
    "candidate_sha256":h,"reviewer":"local VPS administrator (explicit hash approval)",
    "independent_calendar_source":"Scheduled sessions, pilot only: "+"; ".join(pilot.CALENDAR_SOURCES),
    "index_source_check":"Four independent close samples only: "+"; ".join(sorted({s for _,_,_,s in pilot.SAMPLES}))})
ref.review(candidate,review,base/"export")
PY
# Immutable reviewed files. Never overwrite an existing different review.
if test -e "$reviewed"; then
  test -d "$reviewed"
  for f in "$backup"/export/*.json; do cmp "$f" "$reviewed/$(basename "$f")"; done
else
  install -d -o root -g ashare-control-collection -m 0750 "$reviewed"
  for f in "$backup"/export/*.json; do install -o root -g ashare-control-collection -m 0640 "$f" "$reviewed/"; done
fi
cp -a "$cfg/config.json" "$cfg/collection.json" "$cfg/collection-worker.json" "$dest/bridge.py" "$dest/collection_runner.py" "$dest/profiles/collect-dividend" "$backup/"
changed=1
install -o root -g root -m 0644 "$root/bridge.py" "$root/collection_runner.py" "$dest/"
printf '#!/bin/sh\nexec /opt/ashare-research-runtime/bin/python -I /opt/ashare-control/collection_runner.py dividend\n' > "$backup/new-wrapper"
install -o root -g root -m 0755 "$backup/new-wrapper" "$dest/profiles/collect-dividend"
python3 - <<'PY'
import json,os,tempfile
from pathlib import Path
cfg=Path("/etc/ashare-control")
b=cfg/"config.json";w=cfg/"collection-worker.json";c=cfg/"collection.json"
bridge=json.loads(b.read_text());worker=json.loads(w.read_text());plan=json.loads(c.read_text())
assert bridge["repository_id"]==worker["repository_id"]
assert bridge["async_profiles"]["collect-reference"]=="collection"
assert plan["start"]=="2020-01-01" and plan["end"]=="2025-12-31"
calendar="/etc/ashare-control/pilot-reference/calendar.json"
assert plan["calendar"] in (None,calendar)
plan.update(calendar=calendar,dividend_start="2024-07-01",dividend_end="2025-01-31",
            dividend_budget=50,daily_budget=100)
worker["profiles"]["collect-dividend"]={"script":"/opt/ashare-control/profiles/collect-dividend","timeout_seconds":1800}
bridge["async_profiles"]["collect-dividend"]="collection"
bridge["reports"]["bt05-dividend-progress"]={"kind":"dividend_capture","file":"/var/lib/ashare-control-spool/collection/results/bt05-dividend.json"}
def save(p,d):
    st=p.stat()
    with tempfile.NamedTemporaryFile(mode="w",dir=p.parent,delete=False) as f:
        json.dump(d,f,indent=2);f.write("\n");f.flush()
        os.fchown(f.fileno(),st.st_uid,st.st_gid);os.fchmod(f.fileno(),0o640);os.fsync(f.fileno());tmp=f.name
    os.replace(tmp,p)
save(c,plan);save(w,worker);save(b,bridge)
PY
runuser -u ashare-collection -- "$runtime" -I -c '
import sys
sys.path.insert(0,"/opt/ashare-control")
import collection_runner,worker
from pathlib import Path
d=collection_runner.load()
sys.path.insert(0,d["backend"])
from value_screener import bt05_dividend_capture as div
from datetime import date
sessions=div.read_calendar(Path(d["calendar"]),date.fromisoformat(d["dividend_start"]),date.fromisoformat(d["dividend_end"]))
assert sessions
assert "collect-dividend" in worker.config(Path("/etc/ashare-control/collection-worker.json"),"collection")["profiles"]
print("Pilot dividend sessions:",len(sessions))
'
runuser -u ashare-control -- /usr/bin/python3 -I -c '
import sys
sys.path.insert(0,"/opt/ashare-control")
import bridge
d=bridge.config("/etc/ashare-control/config.json")
assert d["async_profiles"]["collect-dividend"]=="collection"
assert d["reports"]["bt05-dividend-progress"]["kind"]=="dividend_capture"
'
echo "Pilot reference approved; no API request started by installer."
