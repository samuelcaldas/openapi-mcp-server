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

import signal
import threading

from awslabs.openapi_mcp_server.server import setup_signal_handlers
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
    """stdio SIGTERM requests EOF via stop_event without raising SystemExit."""
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
    """stdio signal sets the reader stop event without raising SystemExit."""
    stop_event = threading.Event()
    setup_signal_handlers(stop_event=stop_event)
    signal_handler = mock_signal.signal.call_args[0][1]

    signal_handler(signal.SIGTERM, None)

    assert stop_event.is_set()
    mock_exit.assert_not_called()
