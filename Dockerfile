# The health record in a container, for `docker compose up`. See docker/README.md.
#
# Not the primary install path (CLAUDE.md): the desktop app and a pip install
# are. This runs exactly the same server, from the same hash-pinned lock the
# release is frozen from, so what runs here is what the test workflow installed.

FROM python:3.14-slim

# libgomp1: the downloaded llama-server binary links against OpenMP.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# The container runs as the person who owns the record folder, so every file it
# writes into the bind mount is theirs and their sync client can touch it.
ARG UID=1000
ARG GID=1000
RUN groupadd --gid "${GID}" health \
    && useradd --uid "${UID}" --gid "${GID}" --home-dir /home/health --create-home health \
    && mkdir -p /data/vault \
    && chown -R health:health /data

WORKDIR /opt/health-agent

# Dependencies first and on their own, so changing the code does not reinstall
# them. --require-hashes: the lock is the whole list, and nothing unpinned
# arrives beside it.
COPY packaging/requirements-app.txt packaging/requirements-app.txt
RUN pip install --no-cache-dir --require-hashes -r packaging/requirements-app.txt

# Run from source rather than `pip install .`, which would fetch an unpinned
# build backend. The built interface is committed, so no node is needed.
COPY agent agent
COPY docker/entrypoint.py docker/entrypoint.py
RUN printf '#!/bin/sh\nexec python -m agent.cli "$@"\n' > /usr/local/bin/health-agent \
    && chmod 0755 /usr/local/bin/health-agent

# No USER: the entrypoint starts as root only to hand a folder Docker itself
# created back to the record's owner, then drops to that user before anything
# else runs. See docker/entrypoint.py.
ENV HEALTH_UID=${UID} \
    HEALTH_GID=${GID} \
    HOME=/home/health \
    HEALTH_VAULT=/data/vault \
    PYTHONPATH=/opt/health-agent \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

ENTRYPOINT ["python", "/opt/health-agent/docker/entrypoint.py"]
