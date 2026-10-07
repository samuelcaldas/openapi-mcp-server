# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

FROM ghcr.io/astral-sh/uv:0.12.23 AS uv-bin

FROM debian:bookworm-slim AS builder

COPY --from=uv-bin /uv /bin/uv

# Install Python 3.11 build dependencies matching Debian 12 distroless runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-venv \
    python3-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --force --system app && \
    useradd app -g app -d /app

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON=/usr/bin/python3 \
    UV_FROZEN=true

COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev --no-editable

COPY . /app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable && \
    chown -R app:app /app

# Final distroless stage — no package manager, no shell, minimal attack surface
FROM gcr.io/distroless/python3-debian12

# Copy user/group databases so USER app resolves by name
COPY --from=builder /etc/passwd /etc/passwd
COPY --from=builder /etc/group /etc/group
COPY --from=builder --chown=app:app /app /app

# Get healthcheck script
COPY ./docker-healthcheck.sh /usr/local/bin/docker-healthcheck.sh

# Place executables in the environment at the front of the path
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    TZ=America/Sao_Paulo

USER app

EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=10s --start-period=10s --retries=3 CMD ["/app/.venv/bin/python3", "/usr/local/bin/docker-healthcheck.sh"]
ENTRYPOINT ["/app/.venv/bin/awslabs.openapi-mcp-server"]
