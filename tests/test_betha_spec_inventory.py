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
"""Offline inventory and validation tests for Betha OpenAPI specifications."""

import hashlib
import json
import pytest
from awslabs.openapi_mcp_server.api.config import Config
from awslabs.openapi_mcp_server.server import create_mcp_server_async
from awslabs.openapi_mcp_server.utils.openapi import load_openapi_spec
from awslabs.openapi_mcp_server.utils.openapi_validator import validate_openapi_spec
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = REPOSITORY_ROOT / 'tests' / 'fixtures' / 'betha'
CATALOG_PATH = FIXTURES_DIR / 'catalog.json'

BETHA_23_PRODUCTS = {
    'Almoxarifado',
    'Compras',
    'Configuracoes',
    'Contabil',
    'Contratos',
    'ControleInterno',
    'ESocial',
    'Folha',
    'Frotas',
    'GestaoFiscalCloud',
    'LivroEletronico',
    'Obras',
    'Patrimonio',
    'Planejamento',
    'PortalDoGestor',
    'Procuradoria',
    'ProtocoloFly',
    'RH',
    'Saude',
    'StudioAplicacoes',
    'Tesouraria',
    'Transparencia',
    'Tributos',
}

VALID_STATUSES = {
    'available',
    'placeholder',
    'unavailable',
    'access_limited',
    'duplicate',
}


def _load_catalog():
    if not CATALOG_PATH.exists():
        pytest.fail(f'Catalog file {CATALOG_PATH} does not exist')
    with open(CATALOG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def test_catalog_file_exists_and_is_valid_json():
    """Verify that catalog.json exists and parses as valid JSON."""
    assert CATALOG_PATH.exists(), f'Expected {CATALOG_PATH} to exist'
    data = _load_catalog()
    assert isinstance(data, list) or (isinstance(data, dict) and 'entries' in data), (
        'Catalog must be a list or dict with "entries"'
    )


def test_catalog_covers_all_23_betha_products():
    """All 23 product modules from the Betha source tree must be explicitly cataloged."""
    data = _load_catalog()
    entries = data if isinstance(data, list) else data['entries']
    cataloged_products = {entry['product'] for entry in entries}

    missing = BETHA_23_PRODUCTS - cataloged_products
    assert not missing, f'Missing products in Betha catalog: {sorted(missing)}'


def test_catalog_entries_have_required_schema():
    """Each entry must declare product, status, and metadata fields."""
    data = _load_catalog()
    entries = data if isinstance(data, list) else data['entries']

    required_keys = {
        'product',
        'status',
        'source_page',
        'artifact_url',
        'fixture_path',
        'sha256',
        'retrieval_date',
        'api_base_url',
    }

    for entry in entries:
        missing_keys = required_keys - set(entry.keys())
        assert not missing_keys, (
            f'Entry for {entry.get("product", "unknown")} missing keys: {missing_keys}'
        )
        assert entry['status'] in VALID_STATUSES, (
            f"Invalid status '{entry['status']}' for {entry['product']}. Must be one of {VALID_STATUSES}"
        )


def test_unresolved_entries_do_not_masquerade_as_available():
    """Entries marked placeholder, unavailable, or access_limited must not claim verified local fixtures."""
    data = _load_catalog()
    entries = data if isinstance(data, list) else data['entries']

    for entry in entries:
        if entry['status'] in ('placeholder', 'unavailable', 'access_limited'):
            assert entry.get('fixture_path') is None or entry['status'] == 'access_limited', (
                f'Entry {entry["product"]} with status {entry["status"]} must not have a verified fixture_path'
            )


def test_contabil_test_is_not_counted_as_separate_available_product():
    """contabil-test.json is a schema duplicate of contabil-service-layer and must not be marked as a distinct available product."""
    data = _load_catalog()
    entries = data if isinstance(data, list) else data['entries']

    for entry in entries:
        if entry.get('fixture_path') == 'tests/fixtures/betha/contabil-test.json':
            assert entry['status'] != 'available', (
                'contabil-test.json must not be counted as an independent available contract'
            )


def _get_available_entries():
    if not CATALOG_PATH.exists():
        return []
    with open(CATALOG_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    entries = data if isinstance(data, list) else data['entries']
    return [e for e in entries if e.get('status') == 'available']


@pytest.mark.parametrize(
    'entry',
    _get_available_entries(),
    ids=lambda e: f'{e["product"]}-{Path(e["fixture_path"]).stem}',
)
def test_available_fixtures_exist_and_match_hash(entry):
    """Every available catalog fixture must exist on disk and match its recorded SHA-256."""
    fixture_path = REPOSITORY_ROOT / entry['fixture_path']
    assert fixture_path.exists(), f'Fixture file not found: {entry["fixture_path"]}'

    content = fixture_path.read_bytes()
    actual_hash = hashlib.sha256(content).hexdigest()
    assert actual_hash == entry['sha256'], (
        f'SHA-256 mismatch for {entry["fixture_path"]}: expected {entry["sha256"]}, got {actual_hash}'
    )


@pytest.mark.parametrize(
    'entry',
    _get_available_entries(),
    ids=lambda e: f'{e["product"]}-{Path(e["fixture_path"]).stem}',
)
def test_available_fixtures_load_and_validate_offline(entry):
    """Every available catalog fixture must load through load_openapi_spec and validate."""
    fixture_path = REPOSITORY_ROOT / entry['fixture_path']
    spec = load_openapi_spec(path=str(fixture_path))

    assert isinstance(spec, dict), f'Failed to load {entry["fixture_path"]} as dict'
    assert 'paths' in spec, f"Spec {entry['fixture_path']} missing 'paths'"
    assert validate_openapi_spec(spec), f'Spec {entry["fixture_path"]} failed OpenAPI validation'

    total_ops = sum(
        1
        for path_item in spec.get('paths', {}).values()
        if isinstance(path_item, dict)
        for method, op in path_item.items()
        if method.lower() in ('get', 'post', 'put', 'delete', 'patch', 'options', 'head')
        and isinstance(op, dict)
    )
    assert total_ops > 0, f'Spec {entry["fixture_path"]} has 0 operations'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'entry',
    _get_available_entries(),
    ids=lambda e: f'{e["product"]}-{Path(e["fixture_path"]).stem}',
)
async def test_available_fixtures_generate_mcp_tools(entry):
    """Every available catalog fixture must initialize an MCP server and expose non-empty tools."""
    fixture_path = REPOSITORY_ROOT / entry['fixture_path']
    config = Config(
        api_name=entry['product'],
        api_base_url=entry.get('api_base_url') or 'https://example.com',
        api_spec_path=str(fixture_path),
        auth_type='none',
        max_tools=20,
    )
    server = await create_mcp_server_async(config)
    tools = await server.list_tools()
    assert len(tools) > 0, f'Server for {entry["product"]} generated 0 MCP tools'
