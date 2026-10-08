# OpenAPI MCP Server Tests

[← Back to main README](../README.md)

This directory contains tests for the OpenAPI MCP Server project.

## Test Structure

The tests are organized by module:

- `tests/api/`: Tests for API-related modules
  - `test_config.py`: Tests for API configuration handling
  - `test_discovery.py`: Tests for the API discovery module

- `tests/prompts/`: Tests for prompt-related modules
  - `test_instructions.py`: Tests for dynamic instruction generation
  - `test_operation_instructions.py`: Tests for operation-specific prompts
  - `test_enhanced_instructions.py`: Tests for enhanced API documentation

- `tests/utils/`: Tests for utility modules
  - `test_cache_provider.py`: Tests for the cache provider module
  - `test_http_client.py`: Tests for the HTTP client utilities
  - `test_metrics_provider.py`: Tests for the metrics provider module
  - `test_metrics_provider_prometheus.py`: Tests for the Prometheus metrics provider (skipped if prometheus_client not installed)
  - `test_metrics_provider_decorators.py`: Tests for the metrics provider decorators
  - `test_openapi_validator.py`: Tests for the OpenAPI validation utilities

- `tests/test_init.py`: Tests for module initialization
- `tests/test_main.py`: Tests for the main entry point
- `tests/test_server.py`: Tests for the server creation and configuration

## Running Tests

To run the tests, use pytest:

```bash
# Install test dependencies
pip install "awslabs.openapi-mcp-server[test]"

# Run all tests
pytest

# Run tests with coverage
pytest --cov=awslabs

# Run specific test file
pytest tests/utils/test_cache_provider.py

# Run tests with verbose output
pytest -v
```

## Test Coverage

The tests aim to cover:

1. **Unit Tests**: Testing individual components in isolation
   - Configuration handling
   - OpenAPI spec loading and validation
   - Caching mechanisms
   - Metrics collection
   - HTTP client functionality
   - API discovery tools
   - Prompt generation utilities

2. **Integration Tests**: Testing components working together
   - Server creation and configuration
   - API mounting and tool registration
   - Authentication handling
   - Dynamic prompt generation
   - Operation-specific prompts

## Environment Variables for Testing

Some tests can be influenced by environment variables:

- `ENABLE_CACHETOOLS=true`: Test with cachetools integration
- `ENABLE_PROMETHEUS=true`: Test with Prometheus metrics (requires prometheus_client package)
- `ENABLE_TENACITY=true`: Test with tenacity retry logic
- `ENABLE_OPENAPI_CORE=true`: Test with openapi-core validation
- `ENABLE_OPERATION_PROMPTS=true`: Test with operation-specific prompts

## Mock Strategy

The tests use mocking to isolate components:

- External HTTP requests are mocked using `httpx2` mocks
- File operations are mocked using `mock_open`
- Environment variables are temporarily set and restored
- Async functions are tested using `pytest.mark.asyncio` and `AsyncMock`
- MCP server functionality is mocked using `MagicMock`

## Adding New Tests

When adding new tests:

1. Follow the existing module structure
2. Use appropriate mocking to avoid external dependencies
3. Test both success and failure paths
4. Include tests for edge cases
5. Ensure tests are isolated and don't depend on external state
6. For prompt tests, verify both content generation and registration

## Betha OpenAPI Inventory & Transport Tests

The repository includes a comprehensive provenance catalog, offline validation, and transport tests for Betha Sistemas API specifications.

### Fixture Catalog & Inventory (`tests/fixtures/betha/catalog.json`)

The catalog tracks all 23 official Betha product modules, explicitly classifying every module without inferring missing specs:
- **Available contracts**:
  - `Contabil` (Service Layer, OpenAPI 3.0.1, SHA-256: `8f4cd523fe3d06ead31662be992d481f1f6366b8d36fe02dca489ab1ce6a10da`)
  - `Contabil` (Integration Services, OpenAPI 3.0.1, SHA-256: `76a5ae0db4816dd95540b589f5f8c522d22862f9d33cec46475867bf68606aca`)
  - `Folha` / `RH` (Service Layer, OpenAPI 3.1.0, byte-identical contract, SHA-256: `1c290730d8df06444a2ca7d7f80ebd16a323370bc82224bc6ae3f8debc7c87b3`)
  - `Planejamento` (Service Layer, OpenAPI 3.0.1, SHA-256: `f1a990acfa948148db522956d6dcaa3548e6f3092b6e1e399316ff4918237586`)
- **Duplicate**: `Contabil` test fixture (`contabil-test.json`) identical except version string, not counted as distinct coverage.
- **Access Limited**: `Tributos` (OpenAPI 3.0.1 candidate from test environment) and `StudioAplicacoes` (requires dynamic authenticated session).
- **Placeholder**: Modules with documentation pages that are placeholders or missing spec links (`Almoxarifado`, `Compras`, `Contratos`, `ESocial`, `GestaoFiscalCloud`, `LivroEletronico`, `PortalDoGestor`, `Suite`, `Tesouraria`, `Transparencia`).
- **Unavailable**: Modules with no technical migration or service documentation found in the index (`Configuracoes`, `ControleInterno`, `Frotas`, `Obras`, `Patrimonio`, `Procuradoria`, `ProtocoloFly`, `Saude`).

### Test Suites

1. **Offline Spec Inventory (`tests/test_betha_spec_inventory.py`)**:
   - Runs fully offline without network or credentials.
   - Asserts catalog schema validity, product coverage across all 23 modules, SHA-256 fixture checksum integrity, schema loading/validation, and non-empty FastMCP tool creation.
   ```bash
   pytest tests/test_betha_spec_inventory.py
   ```

2. **Host Transport Integration (`tests/test_betha_transport_integration.py`)**:
   - Parameterized across available specs for both Streamable HTTP (`/mcp`) and SSE (`/sse`) transports.
   - Enforces strict secret hygiene: credentials are passed strictly via `AUTH_TOKEN` in the subprocess environment, never as command-line arguments (`--auth-token`).
   - Limits tool generation to bounded counts (`max_tools=20`) to prevent SSE frame overruns.
   - Separates MCP tool listing/discovery from generated API operation execution.
   ```bash
   # Run offline hygiene test
   pytest tests/test_betha_transport_integration.py -k test_betha_server_args_excludes_auth_token

   # Run live transport matrix (requires .env.betha or BETHA_API_KEY)
   pytest tests/test_betha_transport_integration.py -m live -k test_betha_transport_matrix
   ```

3. **Docker Smoke Tests (`tests/test_betha_docker_smoke.py`)**:
   - Verifies distroless container deployment on `docker-dev` remote context with `TZ=America/Sao_Paulo`.
   - Asserts shell absence (`/bin/sh` fails) and entrypoint responsiveness.
   - Verifies secret exclusion: asserts built image contains no `.env` or `.env.*` files.
   - Uses environment forwarding (`-e AUTH_TOKEN`) so secret values never appear in process argument vectors.
   - Exercises available catalog contracts across both Streamable HTTP and SSE transports.
   ```bash
   # Run Docker smoke suite (requires docker-dev context and credentials)
   pytest tests/test_betha_docker_smoke.py -m docker
   ```

