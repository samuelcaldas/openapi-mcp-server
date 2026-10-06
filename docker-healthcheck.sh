#!/usr/bin/env python3
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
"""Container healthcheck for stdio and HTTP transports."""

import glob
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class RejectRedirects(HTTPRedirectHandler):
    """Keep readiness checks on the configured local endpoint."""

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        """Reject redirects instead of probing a different endpoint."""
        return None


OPENER = build_opener(RejectRedirects)


SERVER = 'awslabs.openapi-mcp-server'
ACCEPTED_HTTP_STATUSES = {200, 405, 406}


def process_is_running():
    """Return whether the server entrypoint exists in /proc."""
    for commandline_path in glob.glob('/proc/[0-9]*/cmdline'):
        try:
            with open(commandline_path, 'rb') as commandline_file:
                arguments = commandline_file.read().split(b'\0')
            if any(
                argument.endswith(b'/app/.venv/bin/' + SERVER.encode()) for argument in arguments
            ):
                return True
        except OSError:
            continue
    return False


def option_value(arguments, name, default):
    """Get a CLI option value, accepting both supported spellings."""
    for index, argument in enumerate(arguments):
        if argument.startswith(f'{name}='):
            return argument.partition('=')[2]
        if argument == name and index + 1 < len(arguments):
            return arguments[index + 1]
    return default


def server_arguments():
    """Return server arguments from the container entrypoint command line."""
    if len(sys.argv) > 1:
        return sys.argv[1:]
    try:
        with open('/proc/1/cmdline', 'rb') as commandline_file:
            commandline = commandline_file.read().split(b'\0')
        return [argument.decode() for argument in commandline[1:] if argument]
    except (OSError, UnicodeDecodeError):
        return []


def check_server(arguments, environment):
    """Check HTTP transport readiness or stdio process liveness."""
    transport = option_value(arguments, '--transport', environment.get('SERVER_TRANSPORT', 'stdio'))
    if transport == 'http':
        transport = 'streamable-http'
    if transport == 'stdio':
        return process_is_running()
    if transport not in ('streamable-http', 'sse'):
        return False

    try:
        port = int(option_value(arguments, '--port', environment.get('SERVER_PORT', '8000')))
        if not 1 <= port <= 65535:
            return False
    except ValueError:
        return False

    path = (
        '/sse'
        if transport == 'sse'
        else option_value(arguments, '--http-path', environment.get('SERVER_HTTP_PATH', '/mcp'))
    )
    if not path.startswith('/') or path.startswith('//') or '?' in path or '#' in path:
        return False

    request = Request(f'http://127.0.0.1:{port}{path}', method='HEAD')
    try:
        with OPENER.open(request, timeout=3) as response:
            return response.status in ACCEPTED_HTTP_STATUSES
    except HTTPError as error:
        return error.code in ACCEPTED_HTTP_STATUSES
    except (OSError, URLError, ValueError):
        return False


def main():
    """Exit with Docker's healthcheck status."""
    if check_server(server_arguments(), os.environ):
        print(f'{SERVER} is healthy')
        return 0
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
