# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

`awslabs.openapi-mcp-server` dynamically bridges OpenAPI 3.x specifications (JSON or YAML) into Model Context Protocol (MCP) tools and resources for LLMs over `stdio`. Upstream reference: `https://awslabs.github.io/mcp/servers/openapi-mcp-server` (hosted in `awslabs/mcp` monorepo under `src/openapi-mcp-server`).

## Development Commands

### Environment Setup
- Python version requirement: `>=3.10`
- Virtual environment: `python3 -m venv .venv && source .venv/bin/activate`
- Editable install with development tools: `pip install -e ".[dev,all]"` or `uv pip install -e ".[dev,all]"`
- Optional extras: `[yaml]`, `[prometheus]`, `[test]`, `[dev]`, `[all]`

### Testing
- Run entire test suite: `pytest`
- Run a single test file: `pytest tests/api/test_config.py`
- Run a single test function: `pytest tests/api/test_config.py -k test_config_load`
- Run a specific test directory: `pytest tests/auth/` or `pytest tests/utils/`
- Run tests with coverage: `pytest --cov=awslabs --cov-report=term-missing`
- Testing flags via environment variables:
  - `ENABLE_PROMETHEUS=true pytest tests/utils/test_metrics_provider_prometheus.py`
  - `ENABLE_CACHETOOLS=true pytest`
  - `ENABLE_TENACITY=true pytest`
  - `ENABLE_OPERATION_PROMPTS=true pytest`

### Linting & Formatting
- Run linter: `ruff check .`
- Auto-fix lint violations: `ruff check --fix .`
- Format code: `ruff format .`
- Type checking: `pyright`

### Running the Server
- Run CLI directly:
  `awslabs.openapi-mcp-server --api-name <name> --api-url <url> --spec-url <url>`
- Run using a local OpenAPI specification file:
  `awslabs.openapi-mcp-server --api-name <name> --api-url <url> --spec-path ./openapi.json`
- Run as a Python module:
  `python -m awslabs.openapi_mcp_server.server --api-name <name> --api-url <url> --spec-url <url>`
- Run via `uvx` in development:
  `uvx --refresh --from . awslabs.openapi-mcp-server --api-name petstore --api-url https://petstore3.swagger.io/api/v3 --spec-url https://petstore3.swagger.io/api/v3/openapi.json --log-level DEBUG`
- Test with MCP Inspector:
  `npx @modelcontextprotocol/inspector uvx --from . awslabs.openapi-mcp-server --api-name petstore --api-url https://petstore3.swagger.io/api/v3 --spec-url https://petstore3.swagger.io/api/v3/openapi.json`

### Docker
- Build image: `docker build -t openapi-mcp-server:latest .`
- Run container:
  `docker run -i -e API_NAME=petstore -e API_BASE_URL=https://petstore3.swagger.io/api/v3 -e API_SPEC_URL=https://petstore3.swagger.io/api/v3/openapi.json openapi-mcp-server:latest`

## Architecture & Code Structure

### Core Execution Flow
1. **Server Entrypoint (`awslabs/openapi_mcp_server/server.py`)**:
   - `main()` handles argument parsing and initializes the async event loop with `create_mcp_server_async(config)`.
   - `create_mcp_server_async()` validates runtime configuration, loads OpenAPI specs via `load_openapi_spec()`, validates schemas via `validate_openapi_spec()`, and instantiates `FastMCP`.
   - Attaches route mappings from `_build_route_maps()`, registers auth via `register_auth_provider()`, mounts prompts with `MCPPromptManager`, and exposes API operations via FastMCP's `OpenAPIProvider`.
   - Manages signal handling (SIGINT, SIGTERM) and uvicorn graceful shutdown.

2. **Intelligent Route Mapping (`server.py:_build_route_maps`)**:
   - FastMCP defaults to mapping HTTP GET endpoints as MCP resources.
   - `_build_route_maps()` parses the OpenAPI spec to find all GET operations specifying `in: query` parameters.
   - Maps these operations to `MCPType.TOOL` using regex route rules (`RouteMap`), enabling LLMs to invoke parameterized searches and filtered queries as executable tools.
   - Enriches generated tool descriptions with HTTP response codes and parameter examples (`format_description_with_responses`).

3. **Multi-Spec Composition (`server.py`, `api/config.py`)**:
   - Allows merging multiple OpenAPI specifications into a unified MCP server instance via `--additional-specs` / `ADDITIONAL_SPECS`.
   - Maintains isolated HTTP clients, individual authentication credentials, and distinct route mappings per specification.

4. **SSRF & Security Safeguards (`utils/url_validator.py`, `utils/http_client.py`)**:
   - **DNS Pinning**: Resolves hostnames upfront and forces socket connections exclusively to the pinned IP address, defending against TOCTOU DNS rebinding attacks.
   - **IP Range Enforcement**: Rejects loopback (`127.0.0.1`), RFC 1918 private subnets (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), and AWS/cloud link-local metadata endpoints (`169.254.169.254`) by default.
   - **Spec Resolution Controls**: 10 MiB maximum payload cap, rejects remote `$ref` targets, and limits local file access to canonicalized paths within allowed directories (`--allowed-spec-dirs`).

5. **Authentication Subsystem (`awslabs/openapi_mcp_server/auth/`)**:
   - Built on `AuthProtocol` typing interface and `BaseAuthProvider`.
   - Handlers:
     - `api_key_auth.py`: Inserts API keys into HTTP headers, query parameters, or cookies.
     - `bearer_auth.py`: Manages Bearer tokens with optional token refresh.
     - `basic_auth.py`: Standard HTTP Basic authentication.
     - `cognito_auth.py`: AWS Cognito user pool and OAuth2 client credentials flows via `boto3`.
   - `auth_factory.py` & `auth_cache.py`: Factory instantiation and thread-safe caching of auth providers.

6. **Dynamic Prompt Generation (`awslabs/openapi_mcp_server/prompts/`)**:
   - `prompt_manager.py`: Integrates with FastMCP's `list_prompts` and `get_prompt` hooks.
   - `generators/operation_prompts.py`: Generates concise prompt templates for specific API operations.
   - `generators/workflow_prompts.py`: Generates comprehensive API overview and workflow documentation prompts.
   - Token-efficient schema compaction achieves a 70–75% reduction in context window footprint.

7. **Resilience & Observability (`awslabs/openapi_mcp_server/utils/`)**:
   - `http_client.py`: Resilient HTTP requests via `httpx` with `tenacity` retry loops and backoff.
   - `cache_provider.py`: In-memory TTL caching via `cachetools`.
   - `metrics_provider.py`: Operation latency and request counts with optional Prometheus export.
