"""End-to-end tests for the CLI MCP transports and inbound request guards."""

import asyncio
import httpx2
import json
import os
import pytest
import socket
import sys
import threading
import time
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from pathlib import Path
from urllib.parse import parse_qs, urlparse


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SERVER_ENVIRONMENT_OVERRIDES = (
    'SERVER_TRANSPORT',
    'SERVER_HOST',
    'SERVER_PORT',
    'SERVER_HTTP_PATH',
    'ALLOW_REMOTE_BIND',
    'ALLOWED_ORIGINS',
)
INITIALIZE_REQUEST = {
    'jsonrpc': '2.0',
    'id': 1,
    'method': 'initialize',
    'params': {
        'protocolVersion': '2025-03-26',
        'capabilities': {},
        'clientInfo': {'name': 'transport-integration-test', 'version': '1.0'},
    },
}


class _SearchAPIHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Return a deterministic search response for the test API."""
        if urlparse(self.path).path != '/search':
            self.send_error(404)
            return

        query = parse_qs(urlparse(self.path).query).get('query', [''])[0]
        body = json.dumps({'query': query, 'items': [f'result:{query}']}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        """Keep local integration-test output quiet."""


@pytest.fixture
def search_api():
    """Serve a local search endpoint, shutting it down even after test failures."""
    server = ThreadingHTTPServer(('127.0.0.1', 0), _SearchAPIHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def openapi_spec(tmp_path):
    """Write the small OpenAPI document used by real CLI subprocesses."""
    spec = {
        'openapi': '3.0.0',
        'info': {'title': 'Local Search API', 'version': '1.0.0'},
        'paths': {
            '/search': {
                'get': {
                    'operationId': 'searchItems',
                    'summary': 'Search items',
                    'parameters': [
                        {
                            'name': 'query',
                            'in': 'query',
                            'required': True,
                            'schema': {'type': 'string'},
                        }
                    ],
                    'responses': {
                        '200': {
                            'description': 'Search results',
                            'content': {
                                'application/json': {
                                    'schema': {
                                        'type': 'object',
                                        'required': ['query', 'items'],
                                        'properties': {
                                            'query': {'type': 'string'},
                                            'items': {
                                                'type': 'array',
                                                'items': {'type': 'string'},
                                            },
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    }
    path = tmp_path / 'openapi.json'
    path.write_text(json.dumps(spec), encoding='utf-8')
    return path


def _base_arguments(spec_path, api_port):
    """Return required CLI arguments for a local API and spec."""
    return [
        '--spec-path',
        str(spec_path),
        '--api-url',
        f'http://127.0.0.1:{api_port}',
        '--allow-insecure-http',
        '--allow-private-networks',
    ]


def _reserve_port():
    """Return a currently unused local TCP port."""
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


async def _wait_for_listener(process, host, port):
    """Wait briefly for the CLI subprocess to bind its transport socket."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.returncode is not None:
            raise RuntimeError(f'MCP server exited during startup: {process.returncode}')
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=0.3)
            writer.close()
            await writer.wait_closed()
            return
        except (OSError, asyncio.TimeoutError):
            await asyncio.sleep(0.05)
    raise TimeoutError(f'MCP server did not listen on {host}:{port}')


async def _stop_process(process):
    """Stop a subprocess and drain its output pipes."""
    if process.returncode is None:
        process.terminate()
        try:
            return await asyncio.wait_for(process.communicate(), timeout=3)
        except asyncio.TimeoutError:
            process.kill()
    return await process.communicate()


def _server_environment():
    """Copy the environment without server settings that alter CLI defaults."""
    environment = os.environ.copy()
    for key in SERVER_ENVIRONMENT_OVERRIDES:
        environment.pop(key, None)
    return environment


@asynccontextmanager
async def _running_server(spec_path, api_port, transport, *, host='127.0.0.1', allow_remote=False):
    """Run one actual CLI network transport and always stop its process."""
    port = _reserve_port()
    arguments = [
        sys.executable,
        '-m',
        'awslabs.openapi_mcp_server.server',
        *_base_arguments(spec_path, api_port),
        '--transport',
        transport,
        '--host',
        host,
        '--port',
        str(port),
    ]
    if allow_remote:
        arguments.append('--allow-remote-bind')
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
            output = (stdout + stderr).decode(errors='replace')
            raise RuntimeError(f'{error}; server output: {output}') from error
        yield port
    finally:
        await _stop_process(process)


async def _assert_session_contract(read_stream, write_stream):
    """Exercise tools and prompts over a real initialized MCP session."""
    async with ClientSession(read_stream, write_stream, read_timeout_seconds=3) as session:
        await asyncio.wait_for(session.initialize(), timeout=5)
        tool_result = await asyncio.wait_for(session.list_tools(), timeout=5)
        assert 'searchItems' in {tool.name for tool in tool_result.tools}

        call_result = await asyncio.wait_for(
            session.call_tool('searchItems', {'query': 'books'}), timeout=5
        )
        assert not call_result.is_error
        assert call_result.structured_content == {'query': 'books', 'items': ['result:books']}

        prompt_result = await asyncio.wait_for(session.list_prompts(), timeout=5)
        assert 'searchItems' in {prompt.name for prompt in prompt_result.prompts}
        prompt = await asyncio.wait_for(
            session.get_prompt('searchItems', {'query': 'books'}), timeout=5
        )
        assert any('Search items' in message.content.text for message in prompt.messages)


async def _exercise_network_transport(transport, port):
    """Connect through the official MCP SDK's HTTP or SSE client."""
    if transport == 'http':
        async with httpx2.AsyncClient(timeout=3) as client:
            async with streamable_http_client(
                f'http://127.0.0.1:{port}/mcp', http_client=client
            ) as streams:
                await _assert_session_contract(streams[0], streams[1])
        return

    async with sse_client(f'http://127.0.0.1:{port}/sse', timeout=3, sse_read_timeout=5) as streams:
        await _assert_session_contract(streams[0], streams[1])


@pytest.mark.parametrize('transport', ['http', 'sse'])
@pytest.mark.asyncio
async def test_network_transports_expose_search_tool_and_prompts(
    transport, search_api, openapi_spec
):
    """HTTP and SSE clients can call the generated tool and prompt."""
    async with _running_server(openapi_spec, search_api, transport) as port:
        await asyncio.wait_for(_exercise_network_transport(transport, port), timeout=20)


@pytest.mark.asyncio
async def test_stdio_cli_exposes_search_tool_and_prompts(search_api, openapi_spec):
    """The actual CLI stdio transport exposes the same generated components."""
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            '-m',
            'awslabs.openapi_mcp_server.server',
            *_base_arguments(openapi_spec, search_api),
        ],
        env=_server_environment(),
        cwd=REPOSITORY_ROOT,
    )
    async with stdio_client(parameters) as streams:
        await asyncio.wait_for(_assert_session_contract(streams[0], streams[1]), timeout=20)


def _guard_target(transport, port):
    """Return the route and method used to probe an inbound server guard."""
    if transport == 'http':
        return f'http://127.0.0.1:{port}/mcp', 'POST'
    return f'http://127.0.0.1:{port}/sse', 'GET'


@pytest.mark.parametrize('transport', ['http', 'sse'])
@pytest.mark.asyncio
async def test_network_transports_reject_foreign_origin(transport, search_api, openapi_spec):
    """A browser request from an unapproved origin receives HTTP 403."""
    async with _running_server(openapi_spec, search_api, transport) as port:
        target, method = _guard_target(transport, port)
        async with httpx2.AsyncClient(timeout=2) as client:
            response = await client.request(
                method,
                target,
                headers={'Origin': 'https://attacker.invalid'},
                json=INITIALIZE_REQUEST if method == 'POST' else None,
            )
        assert response.status_code == 403


@pytest.mark.parametrize('transport', ['http', 'sse'])
@pytest.mark.asyncio
async def test_network_transports_reject_hostile_host(transport, search_api, openapi_spec):
    """A request with an untrusted Host receives HTTP 421."""
    async with _running_server(openapi_spec, search_api, transport) as port:
        target, method = _guard_target(transport, port)
        async with httpx2.AsyncClient(timeout=2) as client:
            response = await client.request(
                method,
                target,
                headers={'Host': 'attacker.invalid'},
                json=INITIALIZE_REQUEST if method == 'POST' else None,
            )
        assert response.status_code == 421


@pytest.mark.asyncio
async def test_remote_bind_fails_without_explicit_opt_in(search_api, openapi_spec):
    """The CLI rejects wildcard binding unless remote access is explicitly enabled."""
    port = _reserve_port()
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        '-m',
        'awslabs.openapi_mcp_server.server',
        *_base_arguments(openapi_spec, search_api),
        '--transport',
        'http',
        '--host',
        '0.0.0.0',
        '--port',
        str(port),
        cwd=REPOSITORY_ROOT,
        env=_server_environment(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()

    output = (stdout + stderr).decode(errors='replace').lower()
    assert process.returncode != 0
    assert 'remote' in output or 'allow-remote-bind' in output


@pytest.mark.parametrize('transport', ['http', 'sse'])
@pytest.mark.asyncio
async def test_remote_bind_opt_in_does_not_allow_foreign_origin(
    transport, search_api, openapi_spec
):
    """Remote binding opt-in never disables inbound request guards."""
    async with _running_server(
        openapi_spec, search_api, transport, host='0.0.0.0', allow_remote=True
    ) as port:
        target, method = _guard_target(transport, port)
        async with httpx2.AsyncClient(timeout=2) as client:
            response = await client.request(
                method,
                target,
                headers={'Origin': 'https://attacker.invalid'},
                json=INITIALIZE_REQUEST if method == 'POST' else None,
            )
            assert response.status_code == 403
            response = await client.request(
                method,
                target,
                headers={'Host': 'attacker.invalid'},
                json=INITIALIZE_REQUEST if method == 'POST' else None,
            )
        assert response.status_code == 421
