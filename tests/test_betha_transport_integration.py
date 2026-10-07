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
"""Live integration tests for SSE and Streamable-HTTP transports against Betha APIs.

These tests require a populated .env.betha file at the repository root.  They
are tagged ``live`` so they can be excluded from CI with ``-m 'not live'`` and
skipped automatically when the credentials file is absent.

Secrets are injected strictly through the subprocess environment (AUTH_TOKEN),
never as CLI arguments (--auth-token).
"""

import asyncio
import httpx2
import json
import os
import pytest
import socket
import sys
import time
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from pathlib import Path

# Re-use subprocess helpers from the existing integration test module.
from test_transport_integration import (
    _reserve_port,
    _server_environment,
    _stop_process,
)


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

# Betha specs take ~15s to parse on initial load (schema normalization / prance fallback).
_BETHA_STARTUP_TIMEOUT = 60


async def _wait_for_listener(process, host, port, timeout=_BETHA_STARTUP_TIMEOUT):
    """Poll until the server binds *host:port* or *timeout* seconds elapse."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.returncode is not None:
            raise RuntimeError(f'Server process exited early with code {process.returncode}')
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            await asyncio.sleep(0.5)
    raise TimeoutError(f'Server did not listen on {host}:{port} within {timeout}s')


# ---------------------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------------------


def _load_available_specs():
    """Return distinct available specs from the Betha fixture catalog."""
    if not CATALOG_PATH.exists():
        return []
    with open(CATALOG_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    entries = data if isinstance(data, list) else data['entries']

    seen_fixtures = set()
    specs = []
    for entry in entries:
        if entry.get('status') == 'available' and entry.get('fixture_path'):
            fix_path = entry['fixture_path']
            if fix_path not in seen_fixtures:
                seen_fixtures.add(fix_path)
                specs.append(
                    {
                        'api_name': entry['product'],
                        'spec_rel_path': fix_path,
                        'api_url': entry.get('api_base_url') or 'https://example.com',
                    }
                )
    return specs


@pytest.fixture(scope='session')
def betha_credentials():
    """Load Betha API credentials; skip all callers when .env.betha and BETHA_API_KEY are absent."""
    if BETHA_ENV.exists():
        load_dotenv(BETHA_ENV)
    api_key = os.environ.get('BETHA_API_KEY', '')
    if not api_key:
        pytest.skip('.env.betha not found and BETHA_API_KEY not set — skipping live Betha tests')
    return {
        'api_key': api_key,
        'api_url': os.environ.get('BETHA_API_URL', 'https://pessoal.betha.cloud/service-layer'),
        'spec_path': REPOSITORY_ROOT
        / os.environ.get('BETHA_SPEC_PATH', 'tests/fixtures/betha/pessoal-service-layer.json'),
    }


def _betha_server_args(spec_path, api_url, api_name, transport, port, max_tools=20):
    """Return CLI arguments to start the server. Secrets are strictly omitted."""
    return [
        sys.executable,
        '-m',
        'awslabs.openapi_mcp_server.server',
        '--spec-path',
        str(spec_path),
        '--api-url',
        api_url,
        '--api-name',
        api_name,
        '--auth-type',
        'bearer',
        '--transport',
        transport,
        '--host',
        '127.0.0.1',
        '--port',
        str(port),
        '--max-tools',
        str(max_tools),
    ]


async def _assert_tools_listed(read_stream, write_stream, expected_keyword=None):
    """Initialize an MCP session and assert tools are present."""
    async with ClientSession(read_stream, write_stream, read_timeout_seconds=30) as session:
        await asyncio.wait_for(session.initialize(), timeout=45)
        result = await asyncio.wait_for(session.list_tools(), timeout=30)
        tool_names = {tool.name for tool in result.tools}
        assert len(tool_names) > 0, 'Expected non-empty tool set from MCP server'
        if expected_keyword:
            assert any(expected_keyword.lower() in name.lower() for name in tool_names), (
                f'Expected tool matching "{expected_keyword}", got sample: {list(tool_names)[:10]}'
            )
        return tool_names


async def _assert_betha_tool_call(read_stream, write_stream):
    """Call getAllEstado — a stable read-only endpoint — and assert a non-error result."""
    async with ClientSession(read_stream, write_stream, read_timeout_seconds=30) as session:
        await asyncio.wait_for(session.initialize(), timeout=45)
        result = await asyncio.wait_for(
            session.call_tool('getAllEstado', {'offset': 0, 'limit': 5}),
            timeout=60,
        )
        assert not result.is_error, f'Tool call returned an error: {result}'


# ---------------------------------------------------------------------------
# Offline argument hygiene tests
# ---------------------------------------------------------------------------


def test_betha_server_args_excludes_auth_token():
    """Verify that _betha_server_args does not contain --auth-token or raw credentials."""
    args = _betha_server_args(
        spec_path=Path('/fake/spec.json'),
        api_url='https://example.com',
        api_name='TestAPI',
        transport='http',
        port=8000,
    )
    assert '--auth-token' not in args, 'Arguments must not contain --auth-token'
    assert not any('token' in a.lower() and not a.startswith('--') for a in args)


# ---------------------------------------------------------------------------
# Network transport: matrix across all available specs (HTTP and SSE)
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.asyncio
@pytest.mark.parametrize('transport', ['http', 'sse'])
@pytest.mark.parametrize(
    'spec_info',
    _load_available_specs(),
    ids=lambda s: f'{s["api_name"]}-{Path(s["spec_rel_path"]).stem}',
)
async def test_betha_transport_matrix(spec_info, transport, betha_credentials):
    """Exercise Streamable-HTTP and SSE transports for all available Betha contracts."""
    spec_path = REPOSITORY_ROOT / spec_info['spec_rel_path']
    if not spec_path.exists():
        pytest.skip(f'Spec file {spec_info["spec_rel_path"]} not found')

    port = _reserve_port()
    arguments = _betha_server_args(
        spec_path=spec_path,
        api_url=spec_info['api_url'],
        api_name=spec_info['api_name'],
        transport=transport,
        port=port,
        max_tools=20,
    )

    process = await asyncio.create_subprocess_exec(
        *arguments,
        cwd=REPOSITORY_ROOT,
        env=_server_environment(auth_token=betha_credentials['api_key']),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        try:
            await _wait_for_listener(process, '127.0.0.1', port)
        except (RuntimeError, TimeoutError) as error:
            process.kill()
            stdout, stderr = await process.communicate()
            raise RuntimeError(
                f'{error}; server output: {(stdout + stderr).decode(errors="replace")}'
            ) from error

        keyword = 'estado' if 'pessoal' in spec_info['spec_rel_path'] else None

        if transport == 'http':
            async with httpx2.AsyncClient(timeout=30) as client:
                async with streamable_http_client(
                    f'http://127.0.0.1:{port}/mcp',
                    http_client=client,
                    max_sse_event_size=None,
                ) as (read, write):
                    await asyncio.wait_for(
                        _assert_tools_listed(read, write, expected_keyword=keyword),
                        timeout=90,
                    )
        else:
            async with sse_client(
                f'http://127.0.0.1:{port}/sse', timeout=30, sse_read_timeout=45
            ) as (read, write):
                await asyncio.wait_for(
                    _assert_tools_listed(read, write, expected_keyword=keyword),
                    timeout=90,
                )
    finally:
        await _stop_process(process)


# ---------------------------------------------------------------------------
# Opt-in live API tool call probes (read-only)
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.asyncio
async def test_betha_http_transport_calls_real_api(betha_credentials):
    """Streamable-HTTP transport can call getAllEstado against the live Betha API."""
    port = _reserve_port()
    arguments = _betha_server_args(
        spec_path=betha_credentials['spec_path'],
        api_url=betha_credentials['api_url'],
        api_name='Pessoal',
        transport='http',
        port=port,
        max_tools=20,
    )
    process = await asyncio.create_subprocess_exec(
        *arguments,
        cwd=REPOSITORY_ROOT,
        env=_server_environment(auth_token=betha_credentials['api_key']),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        try:
            await _wait_for_listener(process, '127.0.0.1', port)
        except (RuntimeError, TimeoutError) as error:
            process.kill()
            stdout, stderr = await process.communicate()
            raise RuntimeError(
                f'{error}; server output: {(stdout + stderr).decode(errors="replace")}'
            ) from error

        async with httpx2.AsyncClient(timeout=30) as client:
            async with streamable_http_client(
                f'http://127.0.0.1:{port}/mcp',
                http_client=client,
                max_sse_event_size=None,
            ) as (read, write):
                await asyncio.wait_for(_assert_betha_tool_call(read, write), timeout=120)
    finally:
        await _stop_process(process)


@pytest.mark.live
@pytest.mark.asyncio
async def test_betha_stdio_transport_calls_real_api(betha_credentials):
    """Stdio transport can call getAllEstado against the live Betha API."""
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            '-m',
            'awslabs.openapi_mcp_server.server',
            '--spec-path',
            str(betha_credentials['spec_path']),
            '--api-url',
            betha_credentials['api_url'],
            '--api-name',
            'Pessoal',
            '--auth-type',
            'bearer',
            '--max-tools',
            '20',
        ],
        env=_server_environment(auth_token=betha_credentials['api_key']),
        cwd=str(REPOSITORY_ROOT),
    )
    async with stdio_client(parameters) as (read, write):
        await asyncio.wait_for(_assert_betha_tool_call(read, write), timeout=120)
