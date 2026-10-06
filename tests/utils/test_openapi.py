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
"""Tests for the OpenAPI utilities using direct module patching."""

# Import modules we need to mock
import httpx2
import json
import pytest
from unittest.mock import MagicMock, mock_open, patch


# Create a no-op cache decorator function for testing
def no_cache_decorator(ttl_seconds=None):
    """No-op cache decorator for testing."""

    def decorator(func):
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        return wrapper

    return decorator


# We need to ensure validate_openapi_spec always returns True by default
def always_valid_spec(*args, **kwargs):
    """Return True for testing validation."""
    return True


# Patch both cache decorator and validation before importing
with (
    patch('awslabs.openapi_mcp_server.utils.cache_provider.cached', no_cache_decorator),
    patch(
        'awslabs.openapi_mcp_server.utils.openapi_validator.validate_openapi_spec',
        always_valid_spec,
    ),
):
    # Now import the function we want to test
    from awslabs.openapi_mcp_server.utils.openapi import load_openapi_spec


class TestOpenAPIUtils:
    """Tests for OpenAPI utilities."""

    def test_no_args(self):
        """Test that the function raises ValueError when no arguments are provided."""
        with pytest.raises(ValueError, match='Either url/validated_url or path must be provided'):
            load_openapi_spec()

    @patch('awslabs.openapi_mcp_server.utils.openapi.time.sleep')
    @patch('awslabs.openapi_mcp_server.utils.openapi._pinned_fetch')
    @patch(
        'awslabs.openapi_mcp_server.utils.url_validator.resolve_hostname',
        return_value=['93.184.216.34'],
    )
    def test_url_http_error(self, mock_resolve, mock_fetch, mock_sleep):
        """Test HTTP error handling on the DNS-pinned fetch path."""
        # The pinned fetch raises HTTPError; validation resolves to a public IP.
        mock_fetch.side_effect = httpx2.HTTPError('HTTP Error')

        # Test the exception is propagated correctly
        with pytest.raises(httpx2.HTTPError, match='HTTP Error'):
            load_openapi_spec(url='https://example.com/api.json')

    @patch('awslabs.openapi_mcp_server.utils.openapi.time.sleep')
    @patch('awslabs.openapi_mcp_server.utils.openapi._pinned_fetch')
    @patch(
        'awslabs.openapi_mcp_server.utils.url_validator.resolve_hostname',
        return_value=['93.184.216.34'],
    )
    def test_url_timeout(self, mock_resolve, mock_fetch, mock_sleep):
        """Test timeout exception handling on the DNS-pinned fetch path."""
        # Setup the mock to raise TimeoutException
        mock_fetch.side_effect = httpx2.TimeoutException('Timeout Error')

        # Test the exception is propagated correctly
        with pytest.raises(httpx2.TimeoutException, match='Timeout Error'):
            load_openapi_spec(url='https://example.com/api.json')

    @patch('awslabs.openapi_mcp_server.utils.openapi._pinned_fetch')
    @patch(
        'awslabs.openapi_mcp_server.utils.url_validator.resolve_hostname',
        return_value=['93.184.216.34'],
    )
    def test_url_invalid_spec(self, mock_resolve, mock_fetch):
        """Test invalid OpenAPI spec validation."""
        # The fetch returns a well-formed but non-OpenAPI body.
        mock_fetch.return_value = b'{"invalid": "spec"}'

        # Mock the validate_openapi_spec function directly
        with patch(
            'awslabs.openapi_mcp_server.utils.openapi.validate_openapi_spec', return_value=False
        ):
            # Test ValueError is raised for invalid spec
            with pytest.raises(ValueError, match='Invalid OpenAPI specification'):
                load_openapi_spec(url='https://example.com/api.json')

    @patch('pathlib.Path.exists', return_value=False)
    def test_path_not_found(self, mock_exists):
        """Test file not found error."""
        with pytest.raises(FileNotFoundError):
            load_openapi_spec(path='/nonexistent/file.json')

    @patch('pathlib.Path.exists', return_value=True)
    @patch('builtins.open', new_callable=mock_open, read_data='{"invalid": json')
    @patch('json.loads', side_effect=json.JSONDecodeError('Invalid JSON', '', 0))
    @patch.dict('sys.modules', {'yaml': None})
    def test_path_yaml_import_error(self, mock_json, mock_file, mock_exists):
        """Test YAML import error."""
        with pytest.raises(ImportError, match="Required dependency 'pyyaml' not installed"):
            load_openapi_spec(path='/path/to/file.yaml')

    @patch('pathlib.Path.exists', return_value=True)
    @patch('builtins.open', new_callable=mock_open, read_data='invalid: yaml')
    @patch('json.loads', side_effect=json.JSONDecodeError('Invalid JSON', '', 0))
    def test_path_yaml_invalid_spec(self, mock_json, mock_file, mock_exists):
        """Test invalid YAML error."""
        # Mock yaml module
        mock_yaml = MagicMock()
        mock_yaml.safe_load.side_effect = Exception('Invalid YAML')

        # Patch the yaml module
        with patch.dict('sys.modules', {'yaml': mock_yaml}):
            with pytest.raises(ValueError):
                load_openapi_spec(path='/path/to/file.yaml')

    @patch('pathlib.Path.exists', return_value=True)
    @patch('builtins.open', new_callable=mock_open, read_data='{"openapi": "3.0.0"}')
    @patch('json.loads', return_value={'openapi': '3.0.0'})
    def test_path_invalid_validation(self, mock_json, mock_file, mock_exists):
        """Test invalid OpenAPI spec from file."""
        # Override the validation function to return False. Patch the name in the
        # module where it is used (openapi.py binds it at import time), not where
        # it is defined, or the mock never takes effect.
        with patch(
            'awslabs.openapi_mcp_server.utils.openapi.validate_openapi_spec',
            return_value=False,
        ):
            with pytest.raises(ValueError, match='Invalid OpenAPI specification'):
                load_openapi_spec(path='/path/to/file.json')


class TestURLFallbackToFile:
    """Tests for URL-fetch-fails-fall-back-to-local-file behaviour."""

    # Minimal valid-ish spec JSON the file branch will read and pass validation.
    _SPEC_JSON = b'{"openapi": "3.0.0", "info": {"title": "T", "version": "1"}, "paths": {}}'

    @patch('awslabs.openapi_mcp_server.utils.openapi.time.sleep')
    @patch('awslabs.openapi_mcp_server.utils.openapi._pinned_fetch')
    @patch('awslabs.openapi_mcp_server.utils.openapi._validate_url_sync')
    @patch('pathlib.Path.exists', return_value=True)
    def test_load_openapi_spec_falls_back_to_file_on_http_error(
        self, mock_exists, mock_validate_url, mock_fetch, mock_sleep
    ):
        """URL HTTPError with path provided triggers warning and file fallback."""
        mock_validate_url.return_value = MagicMock()
        mock_fetch.side_effect = httpx2.HTTPError('network error')

        with (
            patch(
                'builtins.open',
                mock_open(read_data=self._SPEC_JSON),
            ),
            patch('awslabs.openapi_mcp_server.utils.openapi.logger') as mock_logger,
        ):
            result = load_openapi_spec(
                url='https://example.test/spec.json',
                path='/some/local/spec.json',
            )

        assert result is not None
        warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
        assert any('falling back' in msg for msg in warning_calls)

    @patch('awslabs.openapi_mcp_server.utils.openapi.time.sleep')
    @patch('awslabs.openapi_mcp_server.utils.openapi._pinned_fetch')
    @patch('awslabs.openapi_mcp_server.utils.openapi._validate_url_sync')
    @patch('pathlib.Path.exists', return_value=True)
    def test_load_openapi_spec_falls_back_to_file_on_timeout(
        self, mock_exists, mock_validate_url, mock_fetch, mock_sleep
    ):
        """URL TimeoutException with path provided triggers warning and file fallback."""
        mock_validate_url.return_value = MagicMock()
        mock_fetch.side_effect = httpx2.TimeoutException('timed out')

        with (
            patch(
                'builtins.open',
                mock_open(read_data=self._SPEC_JSON),
            ),
            patch('awslabs.openapi_mcp_server.utils.openapi.logger') as mock_logger,
        ):
            result = load_openapi_spec(
                url='https://example.test/spec.json',
                path='/some/local/spec.json',
            )

        assert result is not None
        warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
        assert any('falling back' in msg for msg in warning_calls)

    @patch('awslabs.openapi_mcp_server.utils.openapi._validate_url_sync')
    def test_load_openapi_spec_does_not_fall_back_on_ssrf_error(self, mock_validate_url):
        """SSRFError always propagates — file path must not suppress it."""
        from fastmcp.server.auth.ssrf import SSRFError

        mock_validate_url.side_effect = SSRFError('blocked')

        with pytest.raises(SSRFError, match='blocked'):
            load_openapi_spec(
                url='https://internal.example/spec.json',
                path='/some/local/spec.json',
            )

    @patch('awslabs.openapi_mcp_server.utils.openapi.time.sleep')
    @patch('awslabs.openapi_mcp_server.utils.openapi._pinned_fetch')
    @patch('awslabs.openapi_mcp_server.utils.openapi._validate_url_sync')
    def test_load_openapi_spec_raises_when_url_fails_and_no_path_given(
        self, mock_validate_url, mock_fetch, mock_sleep
    ):
        """URL HTTPError without path provided raises — no fallback available."""
        mock_validate_url.return_value = MagicMock()
        mock_fetch.side_effect = httpx2.HTTPError('network error')

        with pytest.raises(httpx2.HTTPError):
            load_openapi_spec(url='https://example.test/spec.json')
