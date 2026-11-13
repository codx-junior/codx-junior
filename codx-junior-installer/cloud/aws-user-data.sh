#!/bin/bash
# =============================================================================
# codx-junior — EC2 user-data script
#
# Paste as "User data" when launching the instance (Amazon Linux 2023 or
# Ubuntu 22.04+; the installer handles apt and dnf). The security group must
# allow TCP 19981 from your IP (or 80/443 if you put a TLS proxy in front).
#
#   aws ec2 run-instances --user-data file://aws-user-data.sh ...
#
# Logs: /var/log/codx-junior-install.log
# =============================================================================
set -euo pipefail

CODX_JUNIOR_AI_PROVIDER="deepseek"
CODX_JUNIOR_AI_API_KEY="sk-REPLACE-ME"
CODX_JUNIOR_PROJECTS_PATH="/opt/codx-junior/projects"
CODX_JUNIOR_WEB_PORT="19981"

mkdir -p /etc/codx-junior
cat > /etc/codx-junior/settings.env <<EOF
CODX_JUNIOR_AI_PROVIDER=${CODX_JUNIOR_AI_PROVIDER}
CODX_JUNIOR_AI_API_KEY=${CODX_JUNIOR_AI_API_KEY}
CODX_JUNIOR_PROJECTS_PATH=${CODX_JUNIOR_PROJECTS_PATH}
CODX_JUNIOR_WEB_PORT=${CODX_JUNIOR_WEB_PORT}
EOF
chmod 600 /etc/codx-junior/settings.env

curl -fsSL \
    https://raw.githubusercontent.com/codx-junior/codx-junior/main/codx-junior-installer/cloud/bootstrap.sh \
    -o /tmp/codx-junior-bootstrap.sh
bash /tmp/codx-junior-bootstrap.sh --env-file /etc/codx-junior/settings.env
