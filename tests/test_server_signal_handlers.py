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
"""Tests for the server module's signal handlers."""

import io
import json
import os
import select as _select
import signal
import subprocess
import sys
import textwrap
import threading
import time
from awslabs.openapi_mcp_server.server import setup_signal_handlers
from awslabs.openapi_mcp_server.stdio import _InterruptibleStdin
from unittest.mock import MagicMock, call, patch


@patch('awslabs.openapi_mcp_server.server.signal')
@patch('awslabs.openapi_mcp_server.server.logger')
@patch('awslabs.openapi_mcp_server.server.metrics')
def test_setup_signal_handlers_registration(mock_metrics, mock_logger, mock_signal):
    """Test that signal handlers are properly registered."""
    mock_original_handler = MagicMock()
    mock_signal.getsignal.return_value = mock_original_handler

    setup_signal_handlers()

    mock_signal.signal.assert_has_calls(
        [
            call(mock_signal.SIGTERM, mock_signal.signal.call_args[0][1]),
            call(mock_signal.SIGINT, mock_signal.signal.call_args[0][1]),
        ]
    )


@patch('awslabs.openapi_mcp_server.server.signal')
@patch('awslabs.openapi_mcp_server.server.logger')
@patch('awslabs.openapi_mcp_server.server.metrics')
@patch('awslabs.openapi_mcp_server.server.sys.exit')
def test_signal_handler_sigterm(mock_exit, mock_metrics, mock_logger, mock_signal):
    """Stdio SIGTERM requests EOF via stop_event without raising SystemExit."""
    mock_metrics.get_summary.return_value = {'api_calls': 10, 'errors': 2}
    mock_signal.getsignal.return_value = MagicMock()
    stop_event = threading.Event()
    setup_signal_handlers(stop_event=stop_event)
    signal_handler = mock_signal.signal.call_args[0][1]

    signal_handler(mock_signal.SIGTERM, None)

    mock_metrics.get_summary.assert_called_once()
    mock_logger.info.assert_any_call("Final metrics: {'api_calls': 10, 'errors': 2}")
    assert stop_event.is_set()
    mock_exit.assert_not_called()


@patch('awslabs.openapi_mcp_server.server.signal')
@patch('awslabs.openapi_mcp_server.server.logger')
@patch('awslabs.openapi_mcp_server.server.metrics')
@patch('awslabs.openapi_mcp_server.server.sys.exit')
def test_signal_handler_sigint(mock_exit, mock_metrics, mock_logger, mock_signal):
    """Test the signal handler with SIGINT when no stop_event is set."""
    mock_metrics.get_summary.return_value = {'api_calls': 10, 'errors': 2}
    mock_original_handler = MagicMock()
    mock_signal.getsignal.return_value = mock_original_handler
    mock_signal.SIG_DFL = signal.SIG_DFL
    mock_signal.SIG_IGN = signal.SIG_IGN

    setup_signal_handlers()
    signal_handler = mock_signal.signal.call_args[0][1]

    signal_handler(mock_signal.SIGINT, None)

    mock_metrics.get_summary.assert_called_once()
    mock_logger.info.assert_any_call("Final metrics: {'api_calls': 10, 'errors': 2}")
    mock_logger.info.assert_any_call('Process Interrupted, Shutting down gracefully...')
    mock_exit.assert_called_once_with(0)
    mock_original_handler.assert_not_called()


@patch('awslabs.openapi_mcp_server.server.signal')
@patch('awslabs.openapi_mcp_server.server.logger')
@patch('awslabs.openapi_mcp_server.server.metrics')
@patch('awslabs.openapi_mcp_server.server.sys.exit')
def test_signal_handler_sigint_default_handler(mock_exit, mock_metrics, mock_logger, mock_signal):
    """Test the signal handler with SIGINT when the original handler is the default."""
    mock_metrics.get_summary.return_value = {'api_calls': 10, 'errors': 2}
    mock_signal.SIG_DFL = signal.SIG_DFL
    mock_signal.SIG_IGN = signal.SIG_IGN
    mock_signal.getsignal.return_value = mock_signal.SIG_DFL

    setup_signal_handlers()
    signal_handler = mock_signal.signal.call_args[0][1]

    signal_handler(mock_signal.SIGINT, None)

    mock_metrics.get_summary.assert_called_once()
    mock_logger.info.assert_any_call("Final metrics: {'api_calls': 10, 'errors': 2}")
    mock_logger.info.assert_any_call('Process Interrupted, Shutting down gracefully...')
    mock_exit.assert_called_once_with(0)


@patch('awslabs.openapi_mcp_server.server.signal')
@patch('awslabs.openapi_mcp_server.server.logger')
@patch('awslabs.openapi_mcp_server.server.metrics')
@patch('awslabs.openapi_mcp_server.server.sys.exit')
def test_signal_handler_sigint_ignore_handler(mock_exit, mock_metrics, mock_logger, mock_signal):
    """Test the signal handler with SIGINT when the original handler is ignore."""
    mock_metrics.get_summary.return_value = {'api_calls': 10, 'errors': 2}
    mock_signal.SIG_DFL = signal.SIG_DFL
    mock_signal.SIG_IGN = signal.SIG_IGN
    mock_signal.getsignal.return_value = mock_signal.SIG_IGN

    setup_signal_handlers()
    signal_handler = mock_signal.signal.call_args[0][1]

    signal_handler(mock_signal.SIGINT, None)

    mock_metrics.get_summary.assert_called_once()
    mock_logger.info.assert_any_call("Final metrics: {'api_calls': 10, 'errors': 2}")
    mock_logger.info.assert_any_call('Process Interrupted, Shutting down gracefully...')
    mock_exit.assert_called_once_with(0)


@patch('awslabs.openapi_mcp_server.server.signal')
@patch('awslabs.openapi_mcp_server.server.logger')
@patch('awslabs.openapi_mcp_server.server.metrics')
@patch('awslabs.openapi_mcp_server.server.sys.exit')
def test_stdio_signal_requests_event_without_exiting(
    mock_exit, mock_metrics, mock_logger, mock_signal
):
    """Stdio signal sets the reader stop event without raising SystemExit."""
    stop_event = threading.Event()
    setup_signal_handlers(stop_event=stop_event)
    signal_handler = mock_signal.signal.call_args[0][1]

    signal_handler(signal.SIGTERM, None)

    assert stop_event.is_set()
    mock_exit.assert_not_called()


# ── Unit tests for _InterruptibleStdin ───────────────────────────────────────


class TestInterruptibleStdin:
    """_InterruptibleStdin adapter behaviour."""

    def _pipes(self):
        return os.pipe()

    def test_readable(self):
        """Adapter reports itself readable."""
        r, w = self._pipes()
        event = threading.Event()
        try:
            assert _InterruptibleStdin(r, event).readable()
        finally:
            for fd in (r, w):
                try:
                    os.close(fd)
                except OSError:
                    pass

    def test_fileno_raises_unsupported_so_sdk_skips_dup(self):
        """fileno() must raise UnsupportedOperation so MCP SDK uses proxy buffer."""
        import pytest

        r, w = self._pipes()
        event = threading.Event()
        try:
            with pytest.raises(io.UnsupportedOperation):
                _InterruptibleStdin(r, event).fileno()
        finally:
            for fd in (r, w):
                try:
                    os.close(fd)
                except OSError:
                    pass

    def test_readinto_returns_data_from_data_fd(self):
        """Readinto returns data written to the read end of the pipe."""
        r, w = self._pipes()
        event = threading.Event()
        try:
            os.write(w, b'ping')
            buf = bytearray(16)
            assert _InterruptibleStdin(r, event).readinto(buf) == 4
            assert buf[:4] == b'ping'
        finally:
            for fd in (r, w):
                try:
                    os.close(fd)
                except OSError:
                    pass

    def test_readinto_returns_zero_when_stop_event_already_set(self):
        """Readinto returns 0 (EOF) immediately when stop event is already set."""
        r, w = self._pipes()
        event = threading.Event()
        event.set()
        try:
            buf = bytearray(16)
            assert _InterruptibleStdin(r, event).readinto(buf) == 0
        finally:
            for fd in (r, w):
                try:
                    os.close(fd)
                except OSError:
                    pass

    def test_stop_event_unblocks_blocked_readinto(self):
        """stop_event.set() unblocks a readinto waiting on an empty pipe within 1s."""
        r, w = os.pipe()
        event = threading.Event()
        results = []

        def reader():
            buf = bytearray(16)
            results.append(_InterruptibleStdin(r, event).readinto(buf))

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        time.sleep(0.05)
        event.set()
        t.join(timeout=1.0)
        assert not t.is_alive(), 'stop_event did not unblock readinto'
        assert results == [0]
        for fd in (r, w):
            try:
                os.close(fd)
            except OSError:
                pass


def test_real_subprocess_sigterm_exits_gracefully_after_initialize():
    """Actual subprocess exits 0 within 5s after SIGTERM while idle in stdio loop."""
    server_script = textwrap.dedent(r"""
import io, os, sys, threading
from awslabs.openapi_mcp_server.stdio import interruptible_stdin
from awslabs.openapi_mcp_server.server import setup_signal_handlers
from fastmcp import FastMCP

stop_event = threading.Event()
setup_signal_handlers(stop_event=stop_event)
with interruptible_stdin(stop_event):
    FastMCP('SignalTest').run(transport='stdio', show_banner=False)
""")

    proc = subprocess.Popen(
        [sys.executable, '-c', server_script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    init_msg = (
        json.dumps(
            {
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'initialize',
                'params': {
                    'protocolVersion': '2025-03-26',
                    'capabilities': {},
                    'clientInfo': {'name': 'test', 'version': '1'},
                },
            }
        ).encode()
        + b'\n'
    )
    proc.stdin.write(init_msg)
    proc.stdin.flush()

    ready = _select.select([proc.stdout], [], [], 5.0)[0]
    assert ready, 'server did not respond to initialize within 5s'
    line = proc.stdout.readline()
    resp = json.loads(line)
    assert resp.get('id') == 1, f'unexpected initialize response: {resp}'

    proc.send_signal(signal.SIGTERM)

    try:
        exit_code = proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise AssertionError(
            'server did not exit within 5s after SIGTERM (Docker stop window exceeded)'
        )

    assert exit_code == 0, f'expected clean exit code 0, got {exit_code}'
