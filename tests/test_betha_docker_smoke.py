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
"""Docker smoke tests for the distroless image against the Betha Pessoal API.

Requires:
- .env.betha present with BETHA_API_KEY
- docker CLI available on PATH
- docker-dev remote context configured (used for build/run)

Tag: ``docker`` — exclude from CI with ``-m 'not docker'``.
"""

import asyncio
import os
import shutil
import socket
import subprocess
import sys
import time
import pytest

from pathlib import Path
from dotenv import load_dotenv

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BETHA_ENV = REPOSITORY_ROOT / '.env.betha'
IMAGE_TAG = 'openapi-mcp-server:betha-smoke-test'
DOCKER_CONTEXT = 'docker-dev'


# ---------------------------------------------------------------------------
# Skip conditions
# ---------------------------------------------------------------------------


def _docker_available():
    return shutil.which('docker') is not None


def _betha_env_present():
    return BETHA_ENV.exists()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reserve_port():
    """Return a currently unused local TCP port."""
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def _wait_for_tcp(host, port, timeout=30):
    """Block until TCP port accepts connections or timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(0.5)
    raise TimeoutError(f'Port {host}:{port} did not open within {timeout}s')


def _load_betha_credentials():
    load_dotenv(BETHA_ENV)
    return {
        'api_key': os.environ.get('BETHA_API_KEY', ''),
        'api_url': os.environ.get('BETHA_API_URL', 'https://pessoal.betha.cloud/service-layer'),
        'spec_path': os.environ.get(
            'BETHA_SPEC_PATH', 'tests/fixtures/betha/pessoal-service-layer.json'
        ),
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope='module')
def docker_image():
    """Build the distroless image once for all docker tests in this module."""
    if not _docker_available():
        pytest.skip('docker CLI not found — skipping Docker smoke tests')
    if not _betha_env_present():
        pytest.skip('.env.betha not found — skipping Docker smoke tests')

    result = subprocess.run(
        [
            'docker',
            '--context',
            DOCKER_CONTEXT,
            'build',
            '-t',
            IMAGE_TAG,
            str(REPOSITORY_ROOT),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        pytest.fail(
            f'Docker build failed (exit {result.returncode}):\n'
            f'stdout: {result.stdout}\nstderr: {result.stderr}'
        )
    yield IMAGE_TAG

    # Cleanup: remove the test image
    subprocess.run(
        ['docker', '--context', DOCKER_CONTEXT, 'rmi', '-f', IMAGE_TAG],
        capture_output=True,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.docker
@pytest.mark.asyncio
async def test_docker_distroless_has_no_shell(docker_image):
    """The distroless image must not provide /bin/sh or /bin/bash."""
    result = subprocess.run(
        [
            'docker',
            '--context',
            DOCKER_CONTEXT,
            'run',
            '--rm',
            '--entrypoint',
            '/bin/sh',
            docker_image,
            '-c',
            'echo hi',
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0, (
        'Expected non-zero exit code (no shell in distroless image), '
        f'but got 0. stdout: {result.stdout}'
    )


@pytest.mark.docker
@pytest.mark.asyncio
async def test_docker_entrypoint_shows_help(docker_image):
    """The ENTRYPOINT must resolve and respond to --help."""
    result = subprocess.run(
        [
            'docker',
            '--context',
            DOCKER_CONTEXT,
            'run',
            '--rm',
            '-e',
            'API_NAME=test',
            '-e',
            'API_BASE_URL=https://example.com',
            docker_image,
            '--help',
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0 or 'usage' in output.lower() or 'api-name' in output.lower(), (
        f'Expected --help output, got exit {result.returncode}:\n{output}'
    )


@pytest.mark.docker
@pytest.mark.asyncio
async def test_docker_betha_http_transport_exposes_pessoal_tools(docker_image):
    """Container running Streamable-HTTP transport exposes Betha Pessoal MCP tools."""
    creds = _load_betha_credentials()
    if not creds['api_key']:
        pytest.skip('BETHA_API_KEY not set')

    host_port = _reserve_port()
    spec_container_path = '/app/tests/fixtures/betha/pessoal-service-layer.json'

    container_id = None
    try:
        result = subprocess.run(
            [
                'docker',
                '--context',
                DOCKER_CONTEXT,
                'run',
                '-d',
                '-p',
                f'127.0.0.1:{host_port}:8000',
                '-e',
                'API_NAME=Pessoal',
                '-e',
                f'API_BASE_URL={creds["api_url"]}',
                '-e',
                f'API_SPEC_PATH={spec_container_path}',
                '-e',
                'AUTH_TYPE=bearer',
                '-e',
                f'AUTH_TOKEN={creds["api_key"]}',
                '-e',
                'SERVER_TRANSPORT=http',
                '-e',
                'SERVER_HOST=0.0.0.0',
                '-e',
                'SERVER_PORT=8000',
                '-e',
                'ALLOW_REMOTE_BIND=true',
                '-e',
                'TZ=America/Sao_Paulo',
                docker_image,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            pytest.fail(f'docker run failed: {result.stderr}')

        container_id = result.stdout.strip()

        # Wait for the container to bind the port
        _wait_for_tcp('127.0.0.1', host_port, timeout=30)

        # Connect via MCP SDK and assert Pessoal tools
        async with httpx2.AsyncClient(timeout=15) as client:
            async with streamable_http_client(
                f'http://127.0.0.1:{host_port}/mcp', http_client=client
            ) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=10) as session:
                    await asyncio.wait_for(session.initialize(), timeout=15)
                    tool_result = await asyncio.wait_for(session.list_tools(), timeout=10)
                    tool_names = {tool.name for tool in tool_result.tools}
                    assert any(
                        'Estado' in name or 'estado' in name.lower() for name in tool_names
                    ), f'Expected Pessoal tools, got sample: {list(tool_names)[:5]}'
    finally:
        if container_id:
            subprocess.run(
                ['docker', '--context', DOCKER_CONTEXT, 'stop', container_id],
                capture_output=True,
                timeout=15,
            )
            subprocess.run(
                ['docker', '--context', DOCKER_CONTEXT, 'rm', container_id],
                capture_output=True,
                timeout=10,
            )
