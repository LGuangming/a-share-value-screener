#!/bin/bash
# Explicit local administrator setup. Run after upgrade-v2.sh, with polling paused.
set -euo pipefail
test "$(id -u)" = 0
root="$(cd -- "$(dirname -- "$0")" && pwd -P)"
command -v setfacl >/dev/null || { echo "Install ACL utilities before setup" >&2; exit 1; }
systemctl stop ashare-vps-control.timer
for lane in research collection; do
  if test -f "/etc/systemd/system/ashare-$lane-worker.timer"; then
    systemctl stop "ashare-$lane-worker.timer"
  fi
done
for unit in ashare-vps-control ashare-research-worker ashare-collection-worker; do
  if systemctl is-active --quiet "$unit.service" || test "$(systemctl show -p ActiveState --value "$unit.service")" = activating; then
    echo "Active task: $unit; finish it before installation" >&2; exit 1
  fi
done
id ashare-control >/dev/null
install -d -o root -g root -m 0755 /var/lib/ashare-control-spool
for lane in research collection; do
  group="ashare-control-$lane"
  user="ashare-$lane"
  getent group "$group" >/dev/null || groupadd --system "$group"
  id "$user" >/dev/null 2>&1 || useradd --system --gid "$group" --shell /usr/sbin/nologin "$user"
  test "$(id -gn "$user")" = "$group"
  install -d -o root -g root -m 0755 "/var/lib/ashare-control-spool/$lane"
  install -d -o ashare-control -g "$group" -m 2750 "/var/lib/ashare-control-spool/$lane/requests"
  install -d -o "$user" -g "$group" -m 2750 "/var/lib/ashare-control-spool/$lane/results"
  # Grant traversal only, never read access to the bridge credential.
  setfacl -m "u:$user:--x" /etc/ashare-control
  if ! test -e "/etc/ashare-control/$lane-worker.json"; then
    install -o root -g "$group" -m 0640 "$root/async-worker.example.json" "/etc/ashare-control/$lane-worker.json"
  fi
  install -m 0644 "$root/ashare-$lane-worker.service" "$root/ashare-$lane-worker.timer" /etc/systemd/system/
done
install -o root -g root -m 0644 "$root/worker.py" "$root/collection_runner.py" "$root/maintenance_runner.py" "$root/runtime_slots.py" /opt/ashare-control/
for stage in reference dividend; do
  name="collect-$stage"
  if ! test -e "/opt/ashare-control/profiles/$name" && ! test -L "/opt/ashare-control/profiles/$name"; then
    tmp="$(mktemp)"
    printf '#!/bin/sh\nexec /usr/bin/python3 -I /opt/ashare-control/collection_runner.py %s\n' "$stage" > "$tmp"
    install -o root -g root -m 0755 "$tmp" "/opt/ashare-control/profiles/$name"
    rm -f "$tmp"
  fi
done
# Install fixed research-only wrappers. No profile is enabled here.
for name in bt03-maintain runtime-activate runtime-rollback; do
  if test -e "/opt/ashare-control/profiles/$name" || test -L "/opt/ashare-control/profiles/$name"; then
    continue
  fi
  tmp="$(mktemp)"
  case "$name" in
    bt03-maintain) printf '#!/bin/sh\nexec /usr/bin/python3 -I /opt/ashare-control/maintenance_runner.py\n' > "$tmp" ;;
    runtime-activate) printf '#!/bin/sh\nexec /usr/bin/python3 -I /opt/ashare-control/runtime_slots.py candidate\n' > "$tmp" ;;
    runtime-rollback) printf '#!/bin/sh\nexec /usr/bin/python3 -I /opt/ashare-control/runtime_slots.py baseline\n' > "$tmp" ;;
  esac
  install -o root -g root -m 0755 "$tmp" "/opt/ashare-control/profiles/$name"
  rm -f "$tmp"
done
install -d -o root -g root -m 0755 /etc/systemd/system/ashare-vps-control.service.d
cat > /etc/systemd/system/ashare-vps-control.service.d/async-spool.conf <<'EOF'
[Service]
SupplementaryGroups=ashare-control-research ashare-control-collection
ReadWritePaths=/var/lib/ashare-control-spool/research/requests /var/lib/ashare-control-spool/collection/requests
EOF
systemctl daemon-reload
echo "Worker units installed, not enabled. Configure pinned repo ID, locally approved profiles, evidence paths and credentials before startup."
