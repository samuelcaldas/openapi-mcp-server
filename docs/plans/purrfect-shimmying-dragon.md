# Plan: HTTP Method Filtering + Operation Limiting for Large OpenAPI Specs

## Context

Large OpenAPI specs (e.g., the Betha Pessoal spec with 429 GET+query tools) cause real problems:
- **Timeouts**: `list_tools` response exceeds SSE frame limits; LLM sessions time out loading the tool list.
- **Token cost**: Every tool description inflates the MCP context sent to the model.
- **Security surface**: Mutation endpoints (POST, PUT, DELETE, PATCH) should be suppressible for read-only consumers.

Two features are needed, both behind env var feature flags with zero effect when unset:

1. **HTTP method filtering** — env vars to disable specific HTTP methods from appearing as MCP tools.
   Examples: `DISABLE_HTTP_METHODS=POST,PUT,DELETE,PATCH` for a read-only MCP connection.

2. **Operation limiting** — cap the number of tools/resources exposed; expose only the first N operations.
   Example: `MAX_TOOLS=50` to avoid SSE frame limit and reduce context size.

---

## Architecture Understanding

### Tool registration flow (current)

```
load_config()         → Config dataclass (api/config.py)
_build_route_maps()   → list[RouteMap] (server.py:72)
                          – maps GET ops to TOOL / RESOURCE / RESOURCE_TEMPLATE
FastMCP.from_openapi(route_maps=custom_mappings, ...)
  → OpenAPIProvider.__init__
     → (route_maps + DEFAULT_ROUTE_MAPPINGS)  # custom first = first match wins
     → _determine_route_type(route, route_maps)  # first match returns
     → MCPType.TOOL → _create_openapi_tool()
     → MCPType.EXCLUDE → skip entirely
server.enable(tags=..., only=True)  # post-creation allowlist
server.disable(tags=...)            # post-creation denylist
```

### Key insight: `MCPType.EXCLUDE` via `RouteMap`

`_determine_route_type` iterates route_maps in order and returns the **first match**. `custom_mappings` are prepended before `DEFAULT_ROUTE_MAPPINGS`. So a `RouteMap(methods=['POST'], pattern='.*', mcp_type=MCPType.EXCLUDE)` inserted at the front of `custom_mappings` will match all POST routes and exclude them before the default TOOL fallback fires.

Imports already in server.py: `RouteMap`, `MCPType` — no new imports needed.

### FastMCP `disable()` — alternative for post-creation filtering

`server.disable(names={'tool1', 'tool2'})` or `server.disable(components={'tool'})` works post-creation. This could be used for MAX_TOOLS limiting after registration. However, pre-filtering in `_build_route_maps` is cheaper (never allocates tool objects for excluded methods) and avoids coupling to internal FastMCP component naming.

---

**Scope (user-confirmed):** `DISABLE_HTTP_METHODS` only affects non-GET methods. GET operations become resources/resource-templates (not tools); disabling GET would break all read access and is out of scope. The EXCLUDE RouteMap is added only for non-GET methods. If the user passes `GET` in `DISABLE_HTTP_METHODS`, log a warning and silently ignore it.

## Feature 1: HTTP Method Filtering

### Config changes — `api/config.py`

Add to `Config` dataclass:
```python
# HTTP method filtering (feature flag; no-op when unset)
disable_http_methods: str = ''  # comma-separated: POST,PUT,DELETE,PATCH,OPTIONS,HEAD
```

Add to `load_config` env_vars dict:
```python
'DISABLE_HTTP_METHODS': (lambda v: setattr(config, 'disable_http_methods', v)),
```

Add to CLI args in `server.py` `main()`:
```python
parser.add_argument(
    '--disable-http-methods',
    help='Comma-separated HTTP methods to suppress as MCP tools (e.g. POST,PUT,DELETE,PATCH)',
)
```

Add to arg overlay in `load_config`:
```python
if hasattr(args, 'disable_http_methods') and args.disable_http_methods:
    config.disable_http_methods = args.disable_http_methods
```

### Server changes — `server.py`

Modify `_build_route_maps(spec, disabled_methods=None)` to prepend EXCLUDE rules:

```python
def _build_route_maps(spec: Dict[str, Any], disabled_methods: set[str] | None = None) -> list:
    """Map GET routes to resources/templates/tools; prepend EXCLUDE maps for disabled methods."""
    mappings = []

    # Feature flag: prepend EXCLUDE RouteMap for each disabled HTTP method.
    # Since custom_mappings are checked before DEFAULT_ROUTE_MAPPINGS (first match wins),
    # these exclusions fire before any tool registration.
    if disabled_methods:
        for method in disabled_methods:
            mappings.append(
                RouteMap(
                    methods=[method.upper()],
                    pattern=r'.*',
                    mcp_type=MCPType.EXCLUDE,
                )
            )

    # Existing GET → TOOL/RESOURCE/RESOURCE_TEMPLATE logic (unchanged)
    for path, path_item in spec.get('paths', {}).items():
        ...  # unchanged
    return mappings
```

Call site (line 289 area):
```python
disabled_methods = (
    {m.strip().upper() for m in config.disable_http_methods.split(',') if m.strip()}
    if config.disable_http_methods
    else None
)
if disabled_methods:
    logger.info(f'Excluding HTTP methods from MCP tools: {sorted(disabled_methods)}')

custom_mappings = _build_route_maps(openapi_spec, disabled_methods=disabled_methods)
```

Apply same filter to additional specs (line 484 area):
```python
extra_provider = OpenAPIProvider(
    openapi_spec=extra_spec,
    client=...,
    route_maps=_build_route_maps(extra_spec, disabled_methods=disabled_methods) + ...,
    ...
)
```

---

## Feature 2: Operation Limiting (MAX_TOOLS)

### Problem

MCP has no native pagination for `list_tools`. Limiting is done by simply not registering tools beyond a cap. Post-creation `server.disable(names=...)` is viable but requires knowing all tool names. A pre-registration approach via `route_map_fn` is cleaner.

### Config changes — `api/config.py`

```python
# Operation count limiting (feature flag; 0 = no limit)
max_tools: int = 0  # max number of tool-type components to expose; 0 = unlimited
```

Env var:
```python
'MAX_TOOLS': (lambda v: setattr(config, 'max_tools', int(v))),
```

CLI arg:
```python
parser.add_argument(
    '--max-tools',
    type=int,
    default=0,
    help='Maximum number of MCP tools to expose (0 = unlimited); use to limit large specs',
)
```

### Server changes — `server.py`

After `server.enable(tags=...)` / `server.disable(tags=...)`, apply the cap:

```python
# Feature flag: limit total tool count (e.g. to avoid SSE frame size limits)
if config.max_tools and config.max_tools > 0:
    all_tools = list(server._get_provider().list_tools_sync())  # or via FastMCP API
    tools_to_disable = {t.name for t in all_tools[config.max_tools:]}
    if tools_to_disable:
        logger.warning(
            f'MAX_TOOLS={config.max_tools}: hiding {len(tools_to_disable)} tools '
            f'({len(all_tools)} total). Consider using INCLUDE_TAGS to select a subset.'
        )
        server.disable(names=tools_to_disable)
```

**Note on FastMCP API for listing tools synchronously:** FastMCP's `list_tools` is async. Use the internal provider list or call `asyncio.get_event_loop().run_until_complete(server.list_tools())` inside the already-async `create_mcp_server_async`. The simplest approach:

```python
if config.max_tools > 0:
    result = await server.list_tools()          # works inside async context
    if len(result) > config.max_tools:
        excess_names = {t.name for t in result[config.max_tools:]}
        logger.warning(f'MAX_TOOLS={config.max_tools}: capping {len(result)} tools; hiding {len(excess_names)}')
        server.disable(names=excess_names)
```

This runs after all `enable`/`disable` tag filters are applied, so the cap respects `INCLUDE_TAGS`/`EXCLUDE_TAGS`.

**Ordering decision (user-confirmed):** GET-first. GET ops (read, safe) survive before non-GET (mutation). Sort: GET tools first (alphabetical within group), then non-GET (alphabetical). Disable the tail beyond `max_tools`.

---

## Files to Modify

| File | Changes |
|---|---|
| `awslabs/openapi_mcp_server/api/config.py` | Add `disable_http_methods: str = ''` and `max_tools: int = 0` fields; add env var entries `DISABLE_HTTP_METHODS`, `MAX_TOOLS`; add CLI arg overlay |
| `awslabs/openapi_mcp_server/server.py` | Modify `_build_route_maps(spec, disabled_methods=None)` to prepend EXCLUDE RouteMaps; parse `disabled_methods` from config and pass to both primary and additional-spec calls; apply `max_tools` cap post-tag-filter |

No new files. No new dependencies. `RouteMap` and `MCPType` already imported in `server.py`.

---

## Env Vars Summary

| Env Var | CLI Flag | Type | Default | Effect |
|---|---|---|---|---|
| `DISABLE_HTTP_METHODS` | `--disable-http-methods` | `string` | `''` (no-op) | Comma-separated HTTP methods to exclude. E.g. `POST,PUT,DELETE,PATCH` for read-only mode. |
| `MAX_TOOLS` | `--max-tools` | `int` | `0` (no-op) | Cap on registered tools. `0` = unlimited. Excess tools are hidden via `server.disable(names=...)`. |

Both flags are **no-ops when unset** (default values cause no code path change).

---

## Verification

```bash
# 1. Method filtering — POST/PUT/DELETE/PATCH absent from tools
DISABLE_HTTP_METHODS=POST,PUT,DELETE,PATCH \
  python -m awslabs.openapi_mcp_server.server \
  --spec-path tests/fixtures/betha/pessoal-service-layer.json \
  --api-url https://pessoal.betha.cloud/service-layer &
# Connect via MCP SDK and assert no tool names start with insert/update/delete

# 2. Read-only shorthand (same result)
python -m awslabs.openapi_mcp_server.server --disable-http-methods POST,PUT,DELETE,PATCH ...

# 3. MAX_TOOLS cap
MAX_TOOLS=10 python -m awslabs.openapi_mcp_server.server ... &
# list_tools returns exactly 10 tools

# 4. Existing suite still clean
pytest -m "not live and not docker" -q   # must: 605 passed

# 5. New unit tests in tests/test_new_features.py (following existing pattern):
# - test_disable_http_methods_excludes_post_tools
# - test_disable_http_methods_no_op_when_unset
# - test_max_tools_caps_tool_count
# - test_max_tools_no_op_when_zero
# - test_config_disable_http_methods_from_env
# - test_config_max_tools_from_env
```

---

## Constraints

- Both features are **feature flags**: unset = behavior unchanged.
- Method names are validated against `_HTTP_METHODS` set; invalid values are logged and skipped.
- `MAX_TOOLS` cap is applied **after** `INCLUDE_TAGS`/`EXCLUDE_TAGS` — ordering: tags filter first, then count cap.
- Log a `WARNING` when MAX_TOOLS truncates, with a hint to use `INCLUDE_TAGS` for more intentional selection.
- No new dependencies. No new files.
