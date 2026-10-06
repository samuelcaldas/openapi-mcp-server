"""Regression tests for the container healthcheck."""

from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from threading import Thread
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / 'docker-healthcheck.sh'
LOADER = SourceFileLoader('docker_healthcheck', str(SCRIPT))
SPEC = spec_from_loader(LOADER.name, LOADER)
HEALTHCHECK = module_from_spec(SPEC)
LOADER.exec_module(HEALTHCHECK)


class HeadHandler(BaseHTTPRequestHandler):
    """Return a controlled status for HTTP readiness requests."""

    status = 200
    requests = []

    def do_HEAD(self):
        """Record and respond to the probe."""
        self.requests.append((self.command, self.path))
        self.send_response(self.status)
        if self.status == 302:
            self.send_header('Location', '/redirected')
        self.end_headers()

    def log_message(self, _format, *_args):
        """Suppress HTTP server output in tests."""
        pass


def run_server(status=200):
    """Start a local endpoint returning the requested status."""
    HeadHandler.status = status
    HeadHandler.requests = []
    server = HTTPServer(('127.0.0.1', 0), HeadHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_http_transport_accepts_expected_statuses_and_uses_head():
    """Accept 200, 405, 406 without opening an SSE stream."""
    for status in (200, 405, 406):
        server, thread = run_server(status)
        try:
            assert (
                HEALTHCHECK.check_server(
                    ['--transport=http', '--port', str(server.server_port)],
                    {'SERVER_TRANSPORT': 'stdio', 'SERVER_PORT': '8000'},
                )
                is True
            )
            assert HeadHandler.requests == [('HEAD', '/mcp')]
        finally:
            server.shutdown()
            thread.join()
            server.server_close()


def test_http_transport_rejects_404():
    """Treat a missing health endpoint as unhealthy."""
    server, thread = run_server(404)
    try:
        assert not HEALTHCHECK.check_server(
            ['--transport=streamable-http', '--port', str(server.server_port)], {}
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_http_transport_rejects_unreachable_endpoint():
    """Treat connection errors as unhealthy."""
    server, _thread = run_server()
    port = server.server_port
    server.server_close()
    assert not HEALTHCHECK.check_server(['--transport=sse', '--port', str(port)], {})


def test_http_transport_rejects_redirects():
    """Do not follow redirects away from the local endpoint."""
    server, thread = run_server(302)
    try:
        assert not HEALTHCHECK.check_server(
            ['--transport=http', '--port', str(server.server_port)], {}
        )
        assert HeadHandler.requests == [('HEAD', '/mcp')]
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_environment_transport_port_and_path_are_used():
    """Use transport, port, and route from the environment."""
    server, thread = run_server()
    try:
        assert HEALTHCHECK.check_server(
            [],
            {
                'SERVER_TRANSPORT': 'http',
                'SERVER_PORT': str(server.server_port),
                'SERVER_HTTP_PATH': '/health-mcp',
            },
        )
        assert HeadHandler.requests == [('HEAD', '/health-mcp')]
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_cli_transport_port_and_path_override_environment():
    """Prefer CLI values over environment values."""
    server, thread = run_server()
    try:
        assert HEALTHCHECK.check_server(
            ['--transport=http', '--port', str(server.server_port), '--http-path=/cli'],
            {'SERVER_TRANSPORT': 'sse', 'SERVER_PORT': '1', 'SERVER_HTTP_PATH': '/env'},
        )
        assert HeadHandler.requests == [('HEAD', '/cli')]
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_sse_uses_sse_route_and_stdio_uses_process_liveness():
    """Use /sse for SSE and process liveness for stdio."""
    server, thread = run_server()
    try:
        assert HEALTHCHECK.check_server(
            ['--transport=sse', '--port', str(server.server_port)],
            {'SERVER_HTTP_PATH': '/mcp'},
        )
        assert HeadHandler.requests == [('HEAD', '/sse')]
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    with patch.object(HEALTHCHECK, 'process_is_running', return_value=True) as is_running:
        assert HEALTHCHECK.check_server(['--transport=stdio'], {'SERVER_TRANSPORT': 'http'})
    is_running.assert_called_once_with()
