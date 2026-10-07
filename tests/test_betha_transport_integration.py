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
"""Live integration tests for SSE and Streamable-HTTP transports against the Betha Pessoal API.

These tests require a populated .env.betha file at the repository root.  They
are tagged ``live`` so they can be excluded from CI with ``-m 'not live'`` and
skipped automatically when the credentials file is absent.
"""

import asyncio
import os
import pytest
import sys
from pathlib import Path

import httpx2
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

import socket
import time

# Re-use subprocess helpers from the existing integration test module.
from test_transport_integration import (
    _reserve_port,
    _server_environment,
    _stop_process,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BETHA_ENV = REPOSITORY_ROOT / '.env.betha'

# Betha spec takes ~15s to parse (prance fails on invalid OAuth2, falls back to
# basic parser).  Use a much longer deadline than the default 10s one.
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
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope='session')
def betha_credentials():
    """Load Betha API credentials; skip all callers when .env.betha is absent."""
    if not BETHA_ENV.exists():
        pytest.skip('.env.betha not found — skipping live Betha tests')
    load_dotenv(BETHA_ENV)
    api_key = os.environ.get('BETHA_API_KEY', '')
    if not api_key:
        pytest.skip('BETHA_API_KEY not set in .env.betha')
    return {
        'api_key': api_key,
        'api_url': os.environ.get('BETHA_API_URL', 'https://pessoal.betha.cloud/service-layer'),
        'spec_path': REPOSITORY_ROOT
        / os.environ.get('BETHA_SPEC_PATH', 'tests/fixtures/betha/pessoal-service-layer.json'),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _betha_server_args(credentials):
    """Return CLI arguments to start the server against the Betha Pessoal API."""
    return [
        '--spec-path',
        str(credentials['spec_path']),
        '--api-url',
        credentials['api_url'],
        '--api-name',
        'Pessoal',
        '--auth-type',
        'bearer',
        '--auth-token',
        credentials['api_key'],
    ]


async def _assert_betha_tools_listed(read_stream, write_stream):
    """Initialize an MCP session and assert Pessoal tools are present."""
    async with ClientSession(read_stream, write_stream, read_timeout_seconds=30) as session:
        await asyncio.wait_for(session.initialize(), timeout=45)
        result = await asyncio.wait_for(session.list_tools(), timeout=30)
        tool_names = {tool.name for tool in result.tools}
        # The Pessoal spec exposes hundreds of GET+query tools; spot-check a stable one.
        assert any('Estado' in name or 'estado' in name.lower() for name in tool_names), (
            f'Expected an "Estado" tool in tool list, got sample: {list(tool_names)[:10]}'
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
# Network transport: tool listing
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.asyncio
@pytest.mark.parametrize('transport', ['http', 'sse'])
async def test_betha_network_transport_exposes_pessoal_tools(transport, betha_credentials):
    """Streamable-HTTP and SSE transports expose Betha Pessoal tools over MCP.

    SSE list_tools is expected to fail for the full Betha spec (429 tools exceed
    the 1 MiB SSE frame limit in mcp.shared._httpx_utils.sse_within_origin).
    The test is still useful for confirming server startup and SSE connectivity.
    """
    port = _reserve_port()
    arguments = [
        sys.executable,
        '-m',
        'awslabs.openapi_mcp_server.server',
        *_betha_server_args(betha_credentials),
        '--transport',
        transport,
        '--host',
        '127.0.0.1',
        '--port',
        str(port),
    ]
    process = await asyncio.create_subprocess_exec(
        *arguments,
        cwd=REPOSITORY_ROOT,
        env=_server_environment(),
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

        if transport == 'http':
            async with httpx2.AsyncClient(timeout=30) as client:
                async with streamable_http_client(
                    f'http://127.0.0.1:{port}/mcp',
                    http_client=client,
                    max_sse_event_size=None,
                ) as (read, write):
                    await asyncio.wait_for(
                        _assert_betha_tools_listed(read, write), timeout=90
                    )
        else:
            # SSE list_tools exceeds the 1 MiB SSE frame limit in the MCP SDK
            # (mcp.shared._httpx_utils.sse_within_origin has a hardcoded 1 MiB cap
            # with no public override).  Just confirm the server accepts an SSE
            # connection and responds to initialize — that is sufficient to validate
            # SSE transport startup with the Betha spec.
            async with sse_client(
                f'http://127.0.0.1:{port}/sse', timeout=30, sse_read_timeout=45
            ) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=30) as session:
                    await asyncio.wait_for(session.initialize(), timeout=45)
    finally:
        await _stop_process(process)


# ---------------------------------------------------------------------------
# Network transport: real API tool call
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.asyncio
async def test_betha_http_transport_calls_real_api(betha_credentials):
    """Streamable-HTTP transport can call getAllEstado against the live Betha API."""
    port = _reserve_port()
    arguments = [
        sys.executable,
        '-m',
        'awslabs.openapi_mcp_server.server',
        *_betha_server_args(betha_credentials),
        '--transport',
        'http',
        '--host',
        '127.0.0.1',
        '--port',
        str(port),
    ]
    process = await asyncio.create_subprocess_exec(
        *arguments,
        cwd=REPOSITORY_ROOT,
        env=_server_environment(),
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
                await asyncio.wait_for(
                    _assert_betha_tool_call(read, write), timeout=120
                )
    finally:
        await _stop_process(process)


# ---------------------------------------------------------------------------
# Stdio transport: real API tool call
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.asyncio
async def test_betha_stdio_transport_calls_real_api(betha_credentials):
    """Stdio transport can call getAllEstado against the live Betha API."""
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            '-m',
            'awslabs.openapi_mcp_server.server',
            *_betha_server_args(betha_credentials),
        ],
        env=_server_environment(),
        cwd=str(REPOSITORY_ROOT),
    )
    async with stdio_client(parameters) as (read, write):
        await asyncio.wait_for(_assert_betha_tool_call(read, write), timeout=120)
