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
import httpx2
import json
import os
import pytest
import shutil
import socket
import subprocess
import time
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _find_betha_env() -> Path:
    direct = REPOSITORY_ROOT / '.env.betha'
    if direct.exists():
        return direct
    worktree_parent = REPOSITORY_ROOT.parent.parent / '.env.betha'
    if worktree_parent.exists():
        return worktree_parent
    return direct


BETHA_ENV = _find_betha_env()
CATALOG_PATH = REPOSITORY_ROOT / 'tests' / 'fixtures' / 'betha' / 'catalog.json'
IMAGE_TAG = 'openapi-mcp-server:betha-smoke-test'
DOCKER_CONTEXT = 'docker-dev'


# ---------------------------------------------------------------------------
# Skip conditions
# ---------------------------------------------------------------------------


def _docker_available():
    return shutil.which('docker') is not None


def _betha_env_present():
    return BETHA_ENV.exists() or bool(os.environ.get('BETHA_API_KEY'))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reserve_port():
    """Return a currently unused local TCP port."""
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def _wait_for_tcp(host, port, timeout=60):
    """Block until TCP port accepts connections or timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(0.5)
    raise TimeoutError(f'Port {host}:{port} did not open within {timeout}s')


def _wait_for_http(url, timeout=60):
    """Block until HTTP endpoint responds or timeout expires."""
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(url, method='GET')
            with urllib.request.urlopen(req, timeout=1):
                return
        except urllib.error.HTTPError:
            return
        except Exception:
            time.sleep(1)
    raise TimeoutError(f'Endpoint {url} did not respond within {timeout}s')


def _load_betha_credentials():
    if BETHA_ENV.exists():
        load_dotenv(BETHA_ENV)
    return {
        'api_key': os.environ.get('BETHA_API_KEY') or os.environ.get('AUTH_TOKEN', ''),
        'api_url': os.environ.get('BETHA_API_URL', 'https://pessoal.betha.cloud/service-layer'),
        'spec_path': os.environ.get(
            'BETHA_SPEC_PATH', 'tests/fixtures/betha/pessoal-service-layer.json'
        ),
    }


def _load_available_docker_specs():
    """Return distinct available specs from the Betha fixture catalog."""
    if not CATALOG_PATH.exists():
        return []
    with open(CATALOG_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    entries = data if isinstance(data, list) else data.get('entries', [])
    seen = set()
    specs = []
    for entry in entries:
        if entry.get('status') == 'available' and entry.get('fixture_path'):
            fix_path = entry['fixture_path']
            if fix_path not in seen:
                seen.add(fix_path)
                specs.append(
                    {
                        'product': entry['product'],
                        'fixture_path': fix_path,
                        'api_base_url': entry.get('api_base_url') or 'https://example.com',
                    }
                )
    return specs


def _docker_server_args(
    docker_image: str,
    host_port: int,
    api_name: str,
    api_base_url: str,
    spec_container_path: str,
    transport: str,
    max_tools: int = 20,
) -> list[str]:
    """Return docker run arguments. Secrets are passed via ambient env, never in argv."""
    return [
        'docker',
        '--context',
        DOCKER_CONTEXT,
        'run',
        '-d',
        '-p',
        f'127.0.0.1:{host_port}:8000',
        '-e',
        f'API_NAME={api_name}',
        '-e',
        f'API_BASE_URL={api_base_url}',
        '-e',
        f'API_SPEC_PATH={spec_container_path}',
        '-e',
        'AUTH_TYPE=bearer',
        '-e',
        'AUTH_TOKEN',
        '-e',
        f'SERVER_TRANSPORT={transport}',
        '-e',
        'SERVER_HOST=0.0.0.0',
        '-e',
        'SERVER_PORT=8000',
        '-e',
        'ALLOW_REMOTE_BIND=true',
        '-e',
        'TZ=America/Sao_Paulo',
        '-e',
        f'MAX_TOOLS={max_tools}',
        docker_image,
    ]


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


def test_docker_server_args_excludes_auth_token():
    """Verify that _docker_server_args does not contain --auth-token or raw credentials in argv."""
    args = _docker_server_args(
        docker_image='image:test',
        host_port=8000,
        api_name='Test',
        api_base_url='https://example.com',
        spec_container_path='/app/spec.json',
        transport='http',
    )
    assert '--auth-token' not in args
    assert not any(a.startswith('AUTH_TOKEN=') for a in args)
    assert not any('token' in a.lower() and '=' in a and not a.startswith('API_') for a in args)
    for i, a in enumerate(args):
        if a == 'AUTH_TOKEN':
            assert i > 0 and args[i - 1] == '-e'


@pytest.mark.docker
@pytest.mark.asyncio
async def test_docker_image_excludes_env_files(docker_image):
    """The built distroless image must not contain .env or .env.* files."""
    result = subprocess.run(
        [
            'docker',
            '--context',
            DOCKER_CONTEXT,
            'run',
            '--rm',
            '--entrypoint',
            '/app/.venv/bin/python3',
            docker_image,
            '-c',
            'import os, sys; found = [f for f in os.listdir("/app") if f.startswith(".env")]; sys.exit(1 if found else 0)',
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f'Found .env files inside container image: {result.stderr or result.stdout}'
    )


@pytest.mark.docker
@pytest.mark.asyncio
@pytest.mark.parametrize('transport', ['http', 'sse'])
@pytest.mark.parametrize(
    'spec_info',
    _load_available_docker_specs(),
    ids=lambda s: f'{s["product"]}-{Path(s["fixture_path"]).stem}',
)
async def test_docker_betha_transport_matrix(docker_image, spec_info, transport):
    """Container running Streamable-HTTP or SSE transport exposes Betha MCP tools."""
    creds = _load_betha_credentials()
    if not creds['api_key']:
        pytest.skip('BETHA_API_KEY not set')

    host_port = _reserve_port()
    spec_container_path = f'/app/{spec_info["fixture_path"]}'

    container_id = None
    env = os.environ.copy()
    env['AUTH_TOKEN'] = creds['api_key']

    args = _docker_server_args(
        docker_image=docker_image,
        host_port=host_port,
        api_name=spec_info['product'],
        api_base_url=spec_info['api_base_url'],
        spec_container_path=spec_container_path,
        transport=transport,
        max_tools=20,
    )
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        if result.returncode != 0:
            pytest.fail(f'docker run failed: {result.stderr}')

        container_id = result.stdout.strip()

        if transport == 'http':
            _wait_for_http(f'http://127.0.0.1:{host_port}/mcp', timeout=60)
            async with httpx2.AsyncClient(timeout=15) as client:
                async with streamable_http_client(
                    f'http://127.0.0.1:{host_port}/mcp',
                    http_client=client,
                    max_sse_event_size=None,
                ) as (read, write):
                    async with ClientSession(read, write, read_timeout_seconds=10) as session:
                        await asyncio.wait_for(session.initialize(), timeout=15)
                        tool_result = await asyncio.wait_for(session.list_tools(), timeout=10)
                        tool_names = {tool.name for tool in tool_result.tools}
                        assert len(tool_names) > 0, (
                            f'Expected non-empty tools list for {spec_info["product"]}'
                        )
                        if 'pessoal' in spec_info['fixture_path']:
                            assert any(
                                'Estado' in name or 'estado' in name.lower() for name in tool_names
                            ), f'Expected Pessoal tools, got: {list(tool_names)[:5]}'
        else:
            _wait_for_http(f'http://127.0.0.1:{host_port}/sse', timeout=60)
            async with sse_client(
                f'http://127.0.0.1:{host_port}/sse', timeout=30, sse_read_timeout=45
            ) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=15) as session:
                    await asyncio.wait_for(session.initialize(), timeout=20)
                    tool_result = await asyncio.wait_for(session.list_tools(), timeout=15)
                    tool_names = {tool.name for tool in tool_result.tools}
                    assert len(tool_names) > 0, (
                        f'Expected non-empty tools list for {spec_info["product"]}'
                    )
                    if 'pessoal' in spec_info['fixture_path']:
                        assert any(
                            'Estado' in name or 'estado' in name.lower() for name in tool_names
                        ), f'Expected Pessoal tools, got: {list(tool_names)[:5]}'
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
