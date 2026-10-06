"""Tests for interruptible POSIX stdio shutdown."""

import json
import os
import pytest
import select
import signal
import subprocess
import sys


@pytest.mark.skipif(os.name != 'posix', reason='interruptible stdio uses POSIX select')
@pytest.mark.parametrize('shutdown_signal', [signal.SIGTERM, signal.SIGINT])
def test_signal_after_mcp_initialize_exits_cleanly(shutdown_signal):
    """SIGTERM after MCP initialization closes stdio and exits without forced kill."""
    script = """
import threading
from fastmcp import FastMCP
from awslabs.openapi_mcp_server.server import setup_signal_handlers
from awslabs.openapi_mcp_server.stdio import interruptible_stdin
stop_event = threading.Event()
setup_signal_handlers(stop_event=stop_event)
with interruptible_stdin(stop_event):
    FastMCP("signal-test").run(transport="stdio", show_banner=False)
"""
    process = subprocess.Popen(
        [sys.executable, '-c', script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        initialize = {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'initialize',
            'params': {
                'protocolVersion': '2025-06-18',
                'capabilities': {},
                'clientInfo': {'name': 'stdio-shutdown-test', 'version': '1'},
            },
        }
        initialize_line = json.dumps(initialize) + '\n'
        split = len(initialize_line) // 2
        process.stdin.write(initialize_line[:split])
        process.stdin.flush()
        ready, _, _ = select.select([process.stdout], [], [], 0.1)
        assert not ready, 'server responded before receiving a complete JSON-RPC line'
        process.stdin.write(initialize_line[split:])
        process.stdin.flush()
        ready, _, _ = select.select([process.stdout], [], [], 5)
        assert ready, f'server exited before initialize: {process.poll()}'
        response = json.loads(process.stdout.readline())
        assert response['id'] == 1

        process.send_signal(shutdown_signal)
        assert process.wait(timeout=2) == 0
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        pytest.fail('stdio server did not exit after signal')
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()
