# syntax=docker/dockerfile:1
# =============================================================================
# codx-junior — DIND image
#
# Build context is the `codx-junior-installer` directory (NOT the repository
# root): the image needs the installer, the runtime compose files and these
# scripts, not the sources.
#
#   docker build -t codxjuniorai/codx-junior:dind \
#     -f codx-junior-installer/dind/Dockerfile codx-junior-installer
#
# The image runs a nested dockerd and starts the SAME runtime compose as a host
# install, so there is one definition of the stack for every deployment.
# =============================================================================
FROM docker:27-dind

RUN apk add --no-cache bash curl jq openssl git ca-certificates

COPY install.sh /opt/codx-junior/install.sh
COPY runtime /opt/codx-junior/runtime
COPY dind/entrypoint.sh /usr/local/bin/codx-junior-dind-entrypoint.sh
RUN chmod +x /opt/codx-junior/install.sh /usr/local/bin/codx-junior-dind-entrypoint.sh

# Data root seen by the host installer (mounted at /data). Kept as a volume in
# the outer compose so the nested image layers survive a container recreate.
ENV CODX_DATA_DIR=/data \
    CODX_JUNIOR_PROJECTS_PATH=/data/projects \
    CODX_CONTAINER=1

EXPOSE 80 443 19981

ENTRYPOINT ["/usr/local/bin/codx-junior-dind-entrypoint.sh"]
