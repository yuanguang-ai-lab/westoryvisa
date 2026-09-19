#!/bin/sh
set -eu

display_number="${DISPLAY:-:102}"
screen_geometry="${DOCFLOW_VIRTUAL_SCREEN:-1280x800x24}"

Xvfb "$display_number" \
  -screen 0 "$screen_geometry" \
  -nolisten tcp \
  -ac \
  > /app/runtime/xvfb.log 2>&1 &
xvfb_pid=$!

ready=0
attempt=0
while [ "$attempt" -lt 80 ]; do
  if xdpyinfo -display "$display_number" >/dev/null 2>&1; then
    ready=1
    break
  fi
  attempt=$((attempt + 1))
  sleep 0.1
done
if [ "$ready" -ne 1 ]; then
  echo "DocFlow pilot display failed to start" >&2
  exit 1
fi

export DISPLAY="$display_number"
dbus-launch --exit-with-session openbox-session \
  > /app/runtime/openbox.log 2>&1 &
openbox_pid=$!

x11vnc \
  -display "$display_number" \
  -rfbport 5900 \
  -localhost \
  -forever \
  -shared \
  -viewonly \
  -nopw \
  -noxdamage \
  > /app/runtime/x11vnc-view.log 2>&1 &
view_vnc_pid=$!

x11vnc \
  -display "$display_number" \
  -rfbport 5901 \
  -localhost \
  -forever \
  -shared \
  -nopw \
  -noxdamage \
  > /app/runtime/x11vnc-control.log 2>&1 &
control_vnc_pid=$!

websockify --heartbeat=15 --web=/usr/share/novnc \
  0.0.0.0:6080 127.0.0.1:5900 \
  > /app/runtime/novnc-view.log 2>&1 &
view_web_pid=$!

websockify --heartbeat=15 --web=/usr/share/novnc \
  0.0.0.0:6081 127.0.0.1:5901 \
  > /app/runtime/novnc-control.log 2>&1 &
control_web_pid=$!

cleanup() {
  kill "$control_web_pid" "$view_web_pid" "$control_vnc_pid" \
    "$view_vnc_pid" "$openbox_pid" "$xvfb_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

while :; do
  for service_pid in \
    "$xvfb_pid" "$openbox_pid" "$view_vnc_pid" "$control_vnc_pid" \
    "$view_web_pid" "$control_web_pid"; do
    if ! kill -0 "$service_pid" 2>/dev/null; then
      echo "DocFlow pilot desktop service exited" >&2
      exit 1
    fi
  done
  sleep 5
done
