#!/bin/bash
# Local administrator upgrade only. Invoke from a reviewed, pinned checkout.
# Does not start or enable services, change tokens, or grant data permissions.
set -euo pipefail
test "$(id -u)" = 0
root="$(cd -- "$(dirname -- "$0")" && pwd -P)"
test -f /etc/ashare-control/config.json
test -f /etc/ashare-control/github-token
test -d /var/lib/ashare-control
id ashare-control >/dev/null
# Never kill a running profile during upgrade. Pause polling first.
systemctl stop ashare-vps-control.timer
for lane in research collection; do
  if test -f "/etc/systemd/system/ashare-$lane-worker.timer"; then
    systemctl stop "ashare-$lane-worker.timer"
  fi
done
for lane in research collection; do
  if systemctl is-active --quiet "ashare-$lane-worker.service" || test "$(systemctl show -p ActiveState --value "ashare-$lane-worker.service")" = activating; then
    echo "Worker active: $lane; do not upgrade until finished" >&2; exit 1
  fi
done
if systemctl is-active --quiet ashare-vps-control.service || \
   test "$(systemctl show -p ActiveState --value ashare-vps-control.service)" = activating; then
  echo "A task is running. Timer paused; finish task before upgrading." >&2
  exit 1
fi
# No arbitrary source code copied from the task repository. Only this exact
# locally reviewed source checkout is installed, and existing state is retained.
backup="$(mktemp -d /root/ashare-control-v1-backup.XXXXXX)"
chmod 0700 "$backup"
cp -a /opt/ashare-control/bridge.py /etc/systemd/system/ashare-vps-control.service \
  /etc/systemd/system/ashare-vps-control.timer /etc/ashare-control/config.json "$backup/"
install -d -o root -g root -m 0755 /opt/ashare-control /opt/ashare-control/profiles
install -o root -g root -m 0644 "$root/bridge.py" "$root/research_runner.py" \
  "$root/docker_snapshot.py" "$root/runtime_slots.py" /opt/ashare-control/
install -o root -g root -m 0644 "$root/ashare-vps-control.service" \
  "$root/ashare-vps-control.timer" /etc/systemd/system/
# Only fixed stage wrappers; not registered in profiles until local approval.
for stage in preflight signals portfolio walk; do
  name="bt05-$stage"
  if test -e "/opt/ashare-control/profiles/$name" || test -L "/opt/ashare-control/profiles/$name"; then
    echo "Preserving existing local profile: $name"
    continue
  fi
  printf '#!/bin/sh\nexec /usr/bin/python3 -I /opt/ashare-control/research_runner.py %s\n' "$stage" \
    > "$backup/$name"
  install -o root -g root -m 0755 "$backup/$name" "/opt/ashare-control/profiles/$name"
done
systemctl daemon-reload
echo "V2 installed, existing token/config/state preserved. Polling paused."
echo "Backup stored at $backup"
echo "Review local configuration, then explicitly start service and timer."
