# syntax=docker/dockerfile:1.7
# Multi-stage build: dependencies are resolved from uv.lock in a builder stage,
# the runtime stage holds only the virtual environment and the source, runs as
# an unprivileged user and has no package manager or compiler.

FROM python:3.13-slim-trixie AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
# git fetches IFC-DB at the commit uv.lock names, until it is on PyPI (ADR 0014).
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /app

# Optional extras to install. The default includes asyncpg for the server's own data in
# PostgreSQL (OPENCDE_STORE_URL, ADR 0029) and the IFC-DB module (IfcOpenShell), which
# OPENCDE_IFC_DB_ENABLED switches on at runtime; build with
# `--build-arg OPENCDE_EXTRAS="--extra postgres"` for a slimmer image without IfcOpenShell.
ARG OPENCDE_EXTRAS="--extra postgres --extra ifc-db"

# Dependencies first, so source changes do not invalidate this layer.
COPY pyproject.toml uv.lock .python-version ./
COPY client/pyproject.toml client/README.md ./client/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project --no-install-workspace ${OPENCDE_EXTRAS}

COPY opencde ./opencde
COPY client/opencde_client ./client/opencde_client
COPY README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev ${OPENCDE_EXTRAS}

# buildingSMART's ifc-gherkin-rules, in an environment of their own with IfcOpenShell 0.8.6 (ADR 0012), made from
# pinned downloads checked against their SHA-256. They add about 425 MB, so they are left out unless the image is built
# with `--build-arg OPENCDE_GHERKIN=1`, as the published image tagged `-gherkin` is. Only with the IFC-DB module.
ARG OPENCDE_GHERKIN=0
RUN --mount=type=cache,target=/root/.cache/uv \
    mkdir -p /opt/gherkin \
    && if [ "$OPENCDE_GHERKIN" = "1" ] && /app/.venv/bin/python -c "import ifcopenshell" 2>/dev/null; then \
         /app/.venv/bin/python -m ifc_db.gherkin_install /opt/gherkin \
         && /opt/gherkin/venv/bin/python -m compileall -q /opt/gherkin/run; \
       fi


# ifcmerge (Bruno Postle, GPL-3.0-or-later; ADR 0030, F97 c), the three-way merge of IFC files in STEP, at the commit
# its main had when it was pinned, checked against its SHA-256. It is run as a program of its own and never copied
# into the repository; the NOTICE says where it comes from.
ARG IFCMERGE_COMMIT=bd2e277f3bd353ba5b7bc450c91fc092f7d13950
ARG IFCMERGE_SHA256=5eb34fa35dd4033b1313496d1672376dcf1cedd8d716831daf2f95f5e5761c95
RUN mkdir -p /opt/ifcmerge \
    && /app/.venv/bin/python -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])" \
         "https://raw.githubusercontent.com/brunopostle/ifcmerge/${IFCMERGE_COMMIT}/ifcmerge" /opt/ifcmerge/ifcmerge \
    && echo "${IFCMERGE_SHA256}  /opt/ifcmerge/ifcmerge" | sha256sum -c - \
    && printf '%s\n' "ifcmerge by Bruno Postle, https://github.com/brunopostle/ifcmerge" \
         "commit ${IFCMERGE_COMMIT}" "GNU General Public License, version 3 or later (GPL-3.0-or-later):" \
         "see GPL-3 beside this file, or https://www.gnu.org/licenses/gpl-3.0.html" > /opt/ifcmerge/NOTICE


FROM python:3.13-slim-trixie AS runtime
# Apply the distribution's security updates and remove pip: the runtime needs no
# package manager, and pip's vendored libraries are what image scanners flag
# between rebuilds of the base image. git writes the projects' repositories,
# and Perl with DateTime runs ifcmerge (ADR 0030), each a program of its own.
RUN apt-get update && apt-get -y upgrade --no-install-recommends \
    && apt-get install -y --no-install-recommends git perl libdatetime-perl libdatetime-timezone-perl \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall -y pip \
    && groupadd --system --gid 10001 opencde \
    && useradd --system --uid 10001 --gid opencde --home-dir /app --shell /usr/sbin/nologin opencde \
    && mkdir -p /data /backups && chown opencde:opencde /data /backups
WORKDIR /app
COPY --from=builder --chown=opencde:opencde /app /app
# Owned by root, so that the server can run the rules but not change them. Empty when they were left out.
COPY --from=builder /opt/gherkin /opt/gherkin
COPY --from=builder /opt/ifcmerge /opt/ifcmerge
RUN cp /usr/share/common-licenses/GPL-3 /opt/ifcmerge/GPL-3
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OPENCDE_DATA_DIR=/data \
    OPENCDE_IFC_DB_GHERKIN_ENV=/opt/gherkin \
    OPENCDE_IFCMERGE_PATH=/opt/ifcmerge/ifcmerge
USER opencde
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"]
ENTRYPOINT ["opencde"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
