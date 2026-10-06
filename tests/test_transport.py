"""Transport parsing, configuration validation, dispatch, and inbound guards."""

import pytest
from awslabs.openapi_mcp_server.api.config import Config, load_config
from awslabs.openapi_mcp_server.transport import (
    ExplicitOriginMiddleware,
    TransportKind,
    run_transport,
    validate_transport_config,
)
from fastmcp import FastMCP
from starlette.middleware import Middleware
from types import SimpleNamespace
from unittest.mock import MagicMock


def test_transport_http_alias_uses_streamable_http():
    """Map the legacy HTTP alias to streamable HTTP."""
    assert TransportKind.parse('http') is TransportKind.STREAMABLE_HTTP
    assert TransportKind.parse('streamable-http') is TransportKind.STREAMABLE_HTTP


def test_transport_rejects_unknown_kind():
    """Fail fast for unsupported transports."""
    with pytest.raises(ValueError, match='Unknown transport'):
        TransportKind.parse('websocket')


@pytest.mark.parametrize('port', [0, -1, 65536, True, False, '8000'])
def test_transport_rejects_invalid_port(port):
    """Reject invalid ports, including booleans and zero."""
    with pytest.raises(ValueError, match='port'):
        validate_transport_config(Config(port=port))


@pytest.mark.parametrize(
    'origin',
    [
        '*',
        'null',
        'https://host/path',
        'https://user@host',
        'ftp://host',
        'http://.',
        'https://example..test',
        'https://-invalid.test',
    ],
)
def test_transport_rejects_invalid_allowed_origins(origin):
    """Reject wildcard, malformed, or non-HTTP origin entries."""
    with pytest.raises(ValueError, match='origin'):
        validate_transport_config(Config(allowed_origins=[origin]))


@pytest.mark.parametrize('host', ['a..b', '-bad.host', '.', '[localhost]', '[::1'])
def test_transport_rejects_malformed_bind_hosts(host):
    """Reject invalid DNS labels and IPv6 brackets before spec I/O."""
    with pytest.raises(ValueError, match='host'):
        validate_transport_config(Config(transport='http', host=host, allow_remote_bind=True))


def test_network_dispatch_normalizes_bracketed_ipv6():
    """Pass a socket address, not an HTTP authority, to Uvicorn."""
    server = MagicMock()
    run_transport(server, Config(transport='http', host='[::1]'))
    assert server.run.call_args.kwargs['host'] == '::1'


@pytest.mark.asyncio
async def test_async_factory_rejects_transport_before_spec_io(monkeypatch):
    """Validate direct factory calls before loading any external specification."""
    from awslabs.openapi_mcp_server import server as server_module

    def unexpected_spec_io(*arguments, **options):
        pytest.fail('Specification loading preceded transport validation')

    monkeypatch.setattr(server_module, 'load_openapi_spec', unexpected_spec_io)
    with pytest.raises(ValueError, match='Unknown transport'):
        await server_module.create_mcp_server_async(
            Config(transport='invalid', api_spec_url='https://example.test/openapi.json')
        )


def test_transport_rejects_invalid_http_path():
    """Require an absolute endpoint path, not a URL."""
    with pytest.raises(ValueError, match='path'):
        validate_transport_config(Config(transport='http', http_path='https://host/mcp'))


def test_sse_ignores_unused_http_path():
    """Keep the fixed SSE endpoint independent of Streamable HTTP settings."""
    assert (
        validate_transport_config(Config(transport='sse', http_path='unused')) is TransportKind.SSE
    )


def test_network_dispatch_allows_compressed_ipv6_bind_host():
    """Allow a client's canonical IPv6 Host for an expanded listener address."""
    from fastmcp.server.http import _host_matches

    server = MagicMock()
    config = Config(transport='http', host='2001:0db8::1', allow_remote_bind=True)
    run_transport(server, config)

    assert _host_matches('[2001:db8::1]:8000', server.run.call_args.kwargs['allowed_hosts'])


def test_remote_host_requires_explicit_opt_in():
    """Require an explicit flag before binding to all interfaces."""
    with pytest.raises(ValueError, match='allow-remote-bind'):
        validate_transport_config(Config(transport='http', host='0.0.0.0'))


def test_remote_host_with_opt_in_validates_as_http():
    """Allow remote binding after explicit opt-in."""
    config = Config(transport='http', host='0.0.0.0', allow_remote_bind=True)
    assert validate_transport_config(config) is TransportKind.STREAMABLE_HTTP


def test_transport_config_uses_cli_zero_port_instead_of_environment(monkeypatch):
    """Reject a supplied zero port instead of falling back to environment."""
    monkeypatch.setenv('SERVER_PORT', '8123')
    args = SimpleNamespace(port=0)

    with pytest.raises(ValueError, match='port'):
        load_config(args)


def test_transport_config_cli_overrides_environment(monkeypatch):
    """Keep CLI transport settings authoritative over environment values."""
    monkeypatch.setenv('SERVER_TRANSPORT', 'sse')
    monkeypatch.setenv('SERVER_HOST', 'localhost')
    monkeypatch.setenv('SERVER_PORT', '8123')
    monkeypatch.setenv('SERVER_HTTP_PATH', '/events')
    monkeypatch.setenv('ALLOWED_ORIGINS', 'https://env.example')
    monkeypatch.setenv('ALLOW_REMOTE_BIND', 'true')
    args = SimpleNamespace(
        transport='http',
        host='127.0.0.1',
        port=9234,
        http_path='/api/mcp',
        allowed_origins='https://cli.example',
        allow_remote_bind=False,
    )

    config = load_config(args)

    assert config.transport == 'streamable-http'
    assert config.host == '127.0.0.1'
    assert config.port == 9234
    assert config.http_path == '/api/mcp'
    assert config.allowed_origins == ['https://cli.example']
    assert config.allow_remote_bind is False


def test_run_transport_uses_stdio_default():
    """Retain stdio as the default transport."""
    server = MagicMock()
    config = Config()

    run_transport(server, config)

    server.run.assert_called_once_with(transport='stdio')


def test_stdio_does_not_inherit_fastmcp_network_transport(monkeypatch):
    """Keep upstream settings from bypassing the wrapper's network guards."""
    import fastmcp

    monkeypatch.setattr(fastmcp.settings, 'transport', 'http')
    server = FastMCP('stdio-settings-test')
    selected = []

    async def record_run(transport=None, **options):
        selected.append(transport or fastmcp.settings.transport)

    monkeypatch.setattr(server, 'run_async', record_run)
    run_transport(server, Config())

    assert selected == ['stdio']


@pytest.mark.parametrize(
    ('transport', 'path', 'expected'),
    [('http', '/custom', 'streamable-http'), ('sse', '/sse', 'sse')],
)
def test_run_transport_uses_public_network_dispatch(transport, path, expected):
    """Dispatch both network transports through FastMCP's public run API."""
    server = MagicMock()
    config = Config(transport=transport, http_path=path)

    run_transport(server, config)

    options = server.run.call_args.kwargs
    assert options['transport'] == expected
    assert options['host'] == '127.0.0.1'
    assert options['port'] == 8000
    assert options['path'] == path
    assert options['host_origin_protection'] is True
    assert options['middleware'][0].cls is ExplicitOriginMiddleware


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('host', 'origin', 'expected_status'),
    [
        ('127.0.0.1:8000', 'http://localhost:3100', 200),
        ('127.0.0.1:8000', 'https://evil.example', 403),
        ('example.test:8000', 'https://example.test', 403),
        ('127.0.0.1:8000', None, 200),
    ],
)
async def test_explicit_origin_middleware_allows_loopback_only_by_default(
    host, origin, expected_status
):
    """Allow missing origins and loopback browser origins only."""

    async def endpoint(scope, receive, send):
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'ok'})

    middleware = ExplicitOriginMiddleware(endpoint, allowed_origins=[], loopback_only=True)
    headers = [(b'host', host.encode())]
    if origin is not None:
        headers.append((b'origin', origin.encode()))
    scope = {'type': 'http', 'headers': headers, 'scheme': 'http'}
    messages = []

    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}

    async def send(message):
        messages.append(message)

    await middleware(scope, receive, send)

    assert messages[0]['status'] == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('headers', 'expected_status'),
    [
        ([(b'host', b'[::1]:8000'), (b'origin', b'http://[::1]:3000')], 200),
        ([(b'host', b'localhost:8000'), (b'origin', b'http://LOCALHOST:3000')], 200),
        ([(b'host', b'[::1]:8000'), (b'origin', b'http://evil.example')], 403),
        (
            [
                (b'host', b'127.0.0.1'),
                (b'origin', b'http://localhost'),
                (b'origin', b'http://localhost'),
            ],
            421,
        ),
        ([(b'host', b'127.0.0.1'), (b'origin', b'http://host/path')], 403),
        ([(b'host', b'127.0.0.1'), (b'host', b'localhost')], 421),
        ([(b'host', b'bad host')], 421),
    ],
)
async def test_origin_middleware_rejects_bad_or_duplicate_headers(headers, expected_status):
    """Reject malformed and duplicate security headers."""

    async def endpoint(scope, receive, send):
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'ok'})

    middleware = ExplicitOriginMiddleware(endpoint, allowed_origins=[], loopback_only=True)
    scope = {'type': 'http', 'headers': headers, 'scheme': 'http'}
    messages = []

    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}

    async def send(message):
        messages.append(message)

    await middleware(scope, receive, send)

    assert messages[0]['status'] == expected_status


@pytest.mark.asyncio
async def test_explicit_origins_exclude_same_origin_fallback():
    """Keep the explicit origin list exclusive on remote listeners."""

    async def endpoint(scope, receive, send):
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'ok'})

    middleware = ExplicitOriginMiddleware(
        endpoint, allowed_origins=['https://allowed.example'], loopback_only=False
    )
    scope = {
        'type': 'http',
        'headers': [(b'host', b'remote.example'), (b'origin', b'http://remote.example')],
        'scheme': 'http',
    }
    messages = []

    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}

    async def send(message):
        messages.append(message)

    await middleware(scope, receive, send)

    assert messages[0]['status'] == 403


@pytest.mark.asyncio
async def test_fastmcp_host_guard_rejects_untrusted_host_and_origin():
    """Protect the actual FastMCP app against foreign host and origin headers."""
    server = FastMCP('transport-guard-test')
    app = server.http_app(
        path='/mcp',
        host_origin_protection=True,
        allowed_hosts=['127.0.0.1'],
        allowed_origins=[],
        middleware=[
            Middleware(
                ExplicitOriginMiddleware,
                allowed_origins=[],
                loopback_only=True,
                allowed_hosts=['127.0.0.1'],
            )
        ],
    )

    async def request(host, origin):
        sent = []
        headers = [(b'host', host.encode())]
        if origin is not None:
            headers.append((b'origin', origin.encode()))
        scope = {
            'type': 'http',
            'asgi': {'version': '3.0'},
            'http_version': '1.1',
            'method': 'GET',
            'scheme': 'http',
            'path': '/mcp',
            'raw_path': b'/mcp',
            'query_string': b'',
            'headers': headers,
            'server': ('127.0.0.1', 8000),
            'client': ('127.0.0.1', 12345),
            'app': app,
        }
        sent_request = False

        async def receive():
            nonlocal sent_request
            if not sent_request:
                sent_request = True
                return {'type': 'http.request', 'body': b'', 'more_body': False}
            return {'type': 'http.disconnect'}

        async def send(message):
            sent.append(message)

        await app(scope, receive, send)
        return sent[0]['status']

    assert await request('attacker.example', None) == 421
    assert await request('127.0.0.1:8000', 'https://attacker.example') == 403


def test_sse_uses_conventional_endpoint_and_warns():
    """Keep the conventional SSE endpoint and surface its deprecation."""
    server = MagicMock()
    with pytest.warns(DeprecationWarning):
        run_transport(server, Config(transport='sse'))
    assert server.run.call_args.kwargs['path'] == '/sse'


def test_validate_transport_does_not_resolve_remote_hostnames():
    """Validate bind host syntax without DNS resolution."""
    config = Config(transport='http', host='api.example.test', allow_remote_bind=True)
    assert validate_transport_config(config) is TransportKind.STREAMABLE_HTTP
