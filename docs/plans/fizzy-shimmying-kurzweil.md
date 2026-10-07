# Upgrade Python SOT to FastMCP 4 (MCP Python SDK v2) with stdio / SSE / Streamable HTTP

## Context

The Python SOT (`docs/sot/openapi-mcp-server-SOT`) is the behavioural reference for the TypeScript port. It is pinned to `fastmcp>=3.3.1,<4` (locked 3.4.3), which sits on `mcp` 1.29.0 (transitive; the only direct import is `mcp.types`).

Upstream state (verified against the python-sdk repo/releases, the MCP spec repo and gofastmcp docs):
- `mcp` **2.3.0** is current (Oct 2). v2 replaced `FastMCP` with `MCPServer`, needs `httpx2>=2.10.0`, caps request bodies at 4 MiB, limits HTTP sessions, and confines redirects to the endpoint origin.
- MCP spec rev **2026-07-28** defines only stdio and Streamable HTTP as standard transports; SSE is legacy but still supported by the SDK and by FastMCP ("backward compatibility").
- **FastMCP 4.0.11** (Oct 4) is "built directly on MCP Python SDK v2". It swaps `httpx` -> `httpx2` (exceptions, `client=` passed to `FastMCP.from_openapi` / `OpenAPIProvider` must be `httpx2.AsyncClient`) and moves imports (`Prompt`/`Message` from `fastmcp.prompts`; `OpenAPIProvider` from `fastmcp.server.providers.openapi`; openapi utils to `fastmcp.utilities.openapi`).

Decision (user): **keep fastmcp**, bump to the 4.x release built on `mcp` 2.x. The official SDK stays a transitive dependency, so no OpenAPI conversion is re-implemented.

Current gap: `main()` calls `mcp_server.run()` with no args, so the server is always stdio. `SERVER_TRANSPORT/HOST/PORT` are loaded into config (`api/config.py:51-57,127-132`) but unused; README/DEPLOYMENT.md contradict each other on this; no inbound Origin/DNS-rebinding protection exists. "Full" transport support therefore means building the dispatch, not just bumping versions.

## Open items to verify FIRST (not confirmed from docs)

1. fastmcp 4.x's exact `mcp` requirement (read `pip index`/lock after resolving) and that `uv lock` resolves with `httpx2`.
2. Where `RouteMap`, `MCPType`, `format_description_with_responses` live in 4.x (the 3->4 guide does not mention them).
3. Whether `fastmcp.server.auth.ssrf` (`SSRFError` + URL/IP helpers) still exists in 4.x; used by `utils/url_validator.py`, `utils/openapi.py`, `server.py:273`.
4. Whether `server._openapi_router._routes` (private, `prompts/generators/operation_prompts.py:177-195`) still exists, and a public replacement (e.g. list tools/resources and inspect provider routes).
5. Exact `run()`/`run_async()` params and the streamable-http transport name in 4.x (docs show `transport="http"`; web summaries show `"streamable-http"`), and how to add Origin/host validation or CORS middleware.

If (3) or (4) fail, stop and re-plan (that is the point where the "drop fastmcp" option re-enters).

## Approach

Work in a worktree per the global git lifecycle (`git fetch origin && git rebase origin/main`, `git worktree add .worktrees/fastmcp4-transports -b fastmcp4-transports`; merge `--no-ff` into `main`, no PR). Commit per logical step.

### Step 1 - Dependency bump (isolated commit)
- `pyproject.toml`: `fastmcp>=4,<5`; replace direct `httpx>=0.28.1,<1` with `httpx2>=2.10.0`; keep `uvicorn`. Add `httpx` back only if a remaining dependency needs it (check `prance`, `boto3` do not import it from our code).
- `uv lock`; confirm `mcp` resolves to 2.x. Update Dockerfile/`uv-requirements` only if needed (Dockerfile already uses Python 3.13).

### Step 2 - httpx -> httpx2 migration (23 test files touch httpx)
Mechanical rename in: `server.py` (80, 391), `auth/{base_auth,auth_provider,auth_protocol,basic_auth}.py` (`httpx.Auth`/`BasicAuth`, `get_httpx_auth`), `utils/{http_client,openapi,error_handler}.py` (`AsyncClient`, `Timeout`, exception mapping tables at `error_handler.py:117-120,245-259,332-339`). Keep the public method name `get_httpx_auth` to limit churn; update type hints. Verify retry/tenacity code still catches the right exception classes.

### Step 3 - Fastmcp 4 import/API fixes
Apply the import moves from the 3->4 guide in `server.py`, `prompts/generators/{operation,workflow}_prompts.py`, `utils/{url_validator,openapi}.py`. Replace the private `_openapi_router._routes` access with a public API if one exists (open item 4); otherwise isolate it behind one small adapter function with a test, so the next break has one fix point. Check `prompt_manager.py:104-151` duck-typed `register_resource_handler` still behaves.

### Step 4 - Transport dispatch (new)
- New small module `awslabs/openapi_mcp_server/transport.py`: `TransportKind` (enum: `stdio`, `streamable-http`, `sse`; accept `http` as alias), a validator (fail fast on unknown value / invalid port), and `run_transport(server, config)` using the fastmcp 4 `run`/`run_async` call for each kind.
- CLI: add `--transport {stdio,http,streamable-http,sse}`, `--host`, `--port`, optional `--http-path` (default `/mcp`); env `SERVER_TRANSPORT/HOST/PORT` remain the fallback via the existing config loader (`api/config.py`). CLI wins over env. Default stays `stdio` (no behaviour change for existing clients).
- `main()` (`server.py:765`) calls the dispatcher instead of bare `run()`. Keep existing signal handling (`server.py:580-615`) and re-check the SIGINT path behaviour noted by the exploration.
- SSE prints a deprecation warning (legacy per spec 2026-07-28 and fastmcp docs).

### Step 5 - Inbound HTTP safety defaults (decisions I'm making; shout if you disagree)
- Default bind `127.0.0.1`; binding a non-loopback host requires an explicit flag/env (`--allow-remote-bind`) and logs a warning that no inbound auth is configured.
- Origin/Host validation for HTTP transports (DNS-rebinding protection) via the mechanism confirmed in open item 5; allowlist from `--allowed-origins` / `ALLOWED_ORIGINS`, defaulting to loopback origins only.
- Out of scope: inbound MCP auth (existing auth providers are for outbound API calls only). Document this clearly.

### Step 6 - Tests
- Update tests that patch `awslabs.openapi_mcp_server.server.FastMCP` and the `httpx` mocks (23 files); `tests/test_main_extended.py:72-107` currently asserts `run()` with no args under SSE and must change to assert dispatch.
- New `tests/test_transport.py`: enum/validation/alias, CLI-vs-env precedence, non-loopback guard, per-kind dispatch (mocked server).
- New integration tests that really start the server on an ephemeral port: Streamable HTTP and SSE, using the SDK/fastmcp `Client` against a tiny local OpenAPI spec (initialize, list_tools, call a GET-with-query tool); plus a stdio subprocess test. Also assert a bad `Origin` is rejected.
- Existing flags: run with `ENABLE_PROMETHEUS`, `ENABLE_CACHETOOLS`, `ENABLE_TENACITY`, `ENABLE_OPERATION_PROMPTS` as in CLAUDE.md.

### Step 7 - Docs
Fix README (lines 9, 37, 257-273, 364-380), DEPLOYMENT.md (5, 29-57, 77-100, 150-152), MIGRATION.md (84-106): stdio default, how to select SSE/HTTP, security defaults, Docker `EXPOSE`/port publishing and health check for HTTP mode. Add CHANGELOG entry and bump version (minor). Note the SOT change in the repo-root `CLAUDE.md` only if the TS port's architecture notes are affected (e.g. HTTP transports now in scope).

## Critical files
`pyproject.toml`, `uv.lock`, `awslabs/openapi_mcp_server/server.py`, `api/config.py`, `utils/{http_client,openapi,error_handler,url_validator}.py`, `auth/*.py`, `prompts/generators/*.py`, `prompts/prompt_manager.py`, new `transport.py`, `Dockerfile`, `README.md`, `DEPLOYMENT.md`.

## Verification
1. `uv sync --all-extras` then `uv pip show mcp fastmcp httpx2` -> mcp 2.x, fastmcp 4.x.
2. `ruff check . && ruff format --check . && pyright`.
3. `pytest` (full, with the four feature env flags from CLAUDE.md), then `pytest --cov=awslabs --cov-report=term-missing`.
4. Manual end to end against petstore: 
   - stdio: `npx @modelcontextprotocol/inspector uv run awslabs.openapi-mcp-server --api-name petstore --api-url https://petstore3.swagger.io/api/v3 --spec-url https://petstore3.swagger.io/api/v3/openapi.json`
   - HTTP: same args plus `--transport http --port 8000`, connect Inspector to `http://127.0.0.1:8000/mcp`
   - SSE: `--transport sse`, connect to its SSE URL
   - confirm tools list, a GET-with-query tool call, and prompts in all three; confirm a request with a foreign `Origin` is rejected.
5. Docker build, run with `-p 8000:8000 -e SERVER_TRANSPORT=http -e SERVER_HOST=0.0.0.0 ...` and confirm the remote-bind guard/flag behaves as documented.
6. Test via the `docker-dev` context per global rules if running containers.
