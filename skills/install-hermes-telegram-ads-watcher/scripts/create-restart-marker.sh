#!/bin/bash
# Creates the restart notification marker so gateway sends
# "♻ Gateway restarted successfully. Your session continues."
# on every start. Use as ExecStartPre in systemd unit.
#
# Usage:
#   ~/.config/systemd/user/hermes-gateway-<profile>.service.d/10-restart-marker.conf:
#   [Service]
#   ExecStartPre=/home/hermes/.hermes/scripts/create-restart-marker.sh

cat > /home/hermes/.hermes/.restart_notify.json << 'MARKER'
{"platform": "telegram", "chat_id": "YOUR_TELEGRAM_CHAT_ID", "chat_type": "private", "thread_id": null, "message_id": null}
MARKER
