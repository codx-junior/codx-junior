#!/bin/bash
# =============================================================================
# codx-junior — GCE startup-script
#
# Attach as metadata key `startup-script` when creating the instance, and add a
# firewall rule allowing TCP 19981 (or use the default-network tag below).
#
#   gcloud compute instances create codx-junior \
#     --metadata-from-file startup-script=gcp-startup-script.sh --tags codx-junior ...
#   gcloud compute firewall-rules create codx-junior-web \
#     --allow tcp:19981 --target-tags codx-junior
#
# The script runs on every boot, which is fine: the installer is idempotent and
# only reconciles. Logs: /var/log/codx-junior-install.log
# =============================================================================
set -euo pipefail

CODX_JUNIOR_AI_PROVIDER="deepseek"
CODX_JUNIOR_AI_API_KEY="sk-REPLACE-ME"
CODX_JUNIOR_PROJECTS_PATH="/opt/codx-junior/projects"
CODX_JUNIOR_WEB_PORT="19981"

# Written only once, so a key rotated in the file afterwards is not overwritten
# by a reboot.
if [[ ! -f /etc/codx-junior/settings.env ]]; then
    mkdir -p /etc/codx-junior
    cat > /etc/codx-junior/settings.env <<EOF
CODX_JUNIOR_AI_PROVIDER=${CODX_JUNIOR_AI_PROVIDER}
CODX_JUNIOR_AI_API_KEY=${CODX_JUNIOR_AI_API_KEY}
CODX_JUNIOR_PROJECTS_PATH=${CODX_JUNIOR_PROJECTS_PATH}
CODX_JUNIOR_WEB_PORT=${CODX_JUNIOR_WEB_PORT}
EOF
    chmod 600 /etc/codx-junior/settings.env
fi

curl -fsSL \
    https://raw.githubusercontent.com/codx-junior/codx-junior/main/codx-junior-installer/cloud/bootstrap.sh \
    -o /tmp/codx-junior-bootstrap.sh
bash /tmp/codx-junior-bootstrap.sh --env-file /etc/codx-junior/settings.env
