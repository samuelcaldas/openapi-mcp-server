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
"""Integration test for the MCPPromptManager."""

import pytest
from awslabs.openapi_mcp_server.prompts import MCPPromptManager
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_server():
    """Create a mock server with the necessary attributes."""
    server = MagicMock()

    # Mock add_prompt
    server.add_prompt = MagicMock()
    # add_prompt already mocked on server

    # Mock register_resource_handler
    server.register_resource_handler = MagicMock()

    server.route_classifications = {
        ('/pet/{petId}', 'GET'): 'resource_template',
        ('/pet/findByStatus', 'GET'): 'tool',
        ('/pet', 'GET'): 'resource',
        ('/pet', 'POST'): 'tool',
    }

    return server


@pytest.fixture
def mock_client():
    """Create a mock HTTP client."""
    client = AsyncMock()
    mock_response = AsyncMock()
    mock_response.text = '{"id": 1, "name": "doggie"}'
    mock_response.headers = {'Content-Type': 'application/json'}
    mock_response.raise_for_status = AsyncMock()
    client.get.return_value = mock_response
    return client


@pytest.fixture
def petstore_openapi_spec():
    """Create a simple PetStore OpenAPI spec for testing."""
    return {
        'openapi': '3.0.0',
        'info': {'title': 'Swagger Petstore', 'version': '1.0.0'},
        'paths': {
            '/pet/{petId}': {
                'get': {
                    'operationId': 'getPetById',
                    'summary': 'Find pet by ID',
                    'parameters': [
                        {
                            'name': 'petId',
                            'in': 'path',
                            'description': 'ID of pet to return',
                            'required': True,
                            'schema': {'type': 'integer', 'format': 'int64'},
                        }
                    ],
                    'responses': {
                        '200': {
                            'description': 'successful operation',
                            'content': {
                                'application/json': {'schema': {'$ref': '#/components/schemas/Pet'}}
                            },
                        }
                    },
                }
            },
            '/pet/findByStatus': {
                'get': {
                    'operationId': 'findPetsByStatus',
                    'summary': 'Finds Pets by status',
                    'parameters': [
                        {
                            'name': 'status',
                            'in': 'query',
                            'description': 'Status values that need to be considered for filter',
                            'required': True,
                            'schema': {
                                'type': 'array',
                                'items': {
                                    'type': 'string',
                                    'enum': ['available', 'pending', 'sold'],
                                },
                            },
                        }
                    ],
                    'responses': {
                        '200': {
                            'description': 'successful operation',
                            'content': {
                                'application/json': {
                                    'schema': {
                                        'type': 'array',
                                        'items': {'$ref': '#/components/schemas/Pet'},
                                    }
                                }
                            },
                        }
                    },
                }
            },
            '/pet': {
                'post': {
                    'operationId': 'addPet',
                    'summary': 'Add a new pet to the store',
                    'requestBody': {
                        'description': 'Pet object that needs to be added to the store',
                        'required': True,
                        'content': {
                            'application/json': {'schema': {'$ref': '#/components/schemas/Pet'}}
                        },
                    },
                    'responses': {
                        '200': {
                            'description': 'successful operation',
                            'content': {
                                'application/json': {'schema': {'$ref': '#/components/schemas/Pet'}}
                            },
                        }
                    },
                },
                'get': {
                    'operationId': 'listPets',
                    'summary': 'List all pets',
                    'responses': {
                        '200': {
                            'description': 'successful operation',
                            'content': {
                                'application/json': {
                                    'schema': {
                                        'type': 'array',
                                        'items': {'$ref': '#/components/schemas/Pet'},
                                    }
                                }
                            },
                        }
                    },
                },
            },
        },
        'components': {
            'schemas': {
                'Pet': {
                    'type': 'object',
                    'required': ['id', 'name'],
                    'properties': {
                        'id': {'type': 'integer', 'format': 'int64'},
                        'name': {'type': 'string'},
                        'status': {'type': 'string', 'enum': ['available', 'pending', 'sold']},
                    },
                }
            }
        },
    }


@pytest.mark.asyncio
async def test_generate_prompts_integration(mock_server, petstore_openapi_spec):
    """Test the full prompt generation process."""
    # Create the prompt manager
    prompt_manager = MCPPromptManager()

    # Generate prompts
    result = await prompt_manager.generate_prompts(
        mock_server,
        'petstore',
        petstore_openapi_spec,
        route_classifications=mock_server.route_classifications,
    )

    # Check that prompts were registered
    assert mock_server.add_prompt.call_count >= 3  # At least 3 operations

    # Check the result
    assert result['operation_prompts_generated'] is True

    # Check that workflow prompts were generated
    # We should have at least one workflow (list-get-update)
    assert result['workflow_prompts_generated'] is True


@pytest.mark.asyncio
async def test_public_component_callback_classifies_routes_for_prompts():
    """Capture FastMCP's post-mapping component classes for primary-spec prompts."""
    import httpx2
    from awslabs.openapi_mcp_server.server import (
        _build_route_maps,
        _create_component_callback,
    )
    from fastmcp import FastMCP

    spec = {
        'openapi': '3.0.0',
        'info': {'title': 'Classification API', 'version': '1.0.0'},
        'paths': {
            '/pets': {
                'get': {
                    'operationId': 'listPets',
                    'responses': {'200': {'description': 'OK'}},
                }
            },
            '/pets/{petId}': {
                'get': {
                    'operationId': 'getPet',
                    'parameters': [
                        {
                            'name': 'petId',
                            'in': 'path',
                            'required': True,
                            'schema': {'type': 'integer'},
                        }
                    ],
                    'responses': {'200': {'description': 'OK'}},
                }
            },
            '/pets/search': {
                'get': {
                    'operationId': 'searchPets',
                    'parameters': [{'$ref': '#/components/parameters/Status'}],
                    'responses': {'200': {'description': 'OK'}},
                }
            },
        },
        'components': {
            'parameters': {
                'Status': {
                    'name': 'status',
                    'in': 'query',
                    'required': True,
                    'schema': {'type': 'string'},
                }
            }
        },
    }
    route_classifications = {}
    route_resource_uris = {}
    client = httpx2.AsyncClient(base_url='https://api.example.com')

    try:
        server = FastMCP.from_openapi(
            spec,
            client=client,
            name='Classification API',
            route_maps=_build_route_maps(spec),
            mcp_component_fn=_create_component_callback(route_classifications, route_resource_uris),
        )
        await MCPPromptManager().generate_prompts(
            server,
            'primary',
            spec,
            route_classifications=route_classifications,
            resource_uris=route_resource_uris,
        )

        assert route_classifications == {
            ('/pets', 'GET'): 'resource',
            ('/pets/{petId}', 'GET'): 'resource_template',
            ('/pets/search', 'GET'): 'tool',
        }
        prompts = {prompt.name: prompt for prompt in await server.list_prompts()}
        resources = {resource.name: resource for resource in await server.list_resources()}
        templates = {template.name: template for template in await server.list_resource_templates()}
        list_messages = prompts['listPets'].fn()
        pet_messages = prompts['getPet'].fn(7)
        assert len(list_messages) == 2
        assert len(pet_messages) == 2
        assert len(prompts['searchPets'].fn('available')) == 1
        assert str(list_messages[1].content.resource.uri) == str(
            route_resource_uris[('/pets', 'GET')]
        )
        assert str(pet_messages[1].content.resource.uri) == str(
            route_resource_uris[('/pets/{petId}', 'GET')]
        )
        assert str(resources['listPets'].uri) == str(route_resource_uris[('/pets', 'GET')])
        assert str(templates['getPet'].uri_template) == str(
            route_resource_uris[('/pets/{petId}', 'GET')]
        )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_register_api_resource_handler_integration(mock_server, mock_client):
    """Test the resource handler registration process."""
    # Create the prompt manager
    prompt_manager = MCPPromptManager()

    # Register the resource handler
    prompt_manager.register_api_resource_handler(mock_server, 'petstore', mock_client)

    # Check that the resource handler was registered
    mock_server.register_resource_handler.assert_called_once()

    # Get the handler function
    handler_uri = mock_server.register_resource_handler.call_args[0][0]
    handler_func = mock_server.register_resource_handler.call_args[0][1]

    # Check the handler URI
    assert handler_uri == 'api://petstore/'

    # Test the handler function
    result = await handler_func('api://petstore/pet/123', {'petId': '123'})

    # Check that the client was called with the correct arguments
    mock_client.get.assert_called_once_with('/pet/123')

    # Check the result
    assert result['text'] == '{"id": 1, "name": "doggie"}'
    assert result['mimeType'] == 'application/json'


@pytest.mark.asyncio
async def test_full_integration(mock_server, mock_client, petstore_openapi_spec):
    """Test the full integration of the prompt manager."""
    # Create the prompt manager
    prompt_manager = MCPPromptManager()

    # Generate prompts
    result = await prompt_manager.generate_prompts(
        mock_server,
        'petstore',
        petstore_openapi_spec,
        route_classifications=mock_server.route_classifications,
    )

    # Register the resource handler
    prompt_manager.register_api_resource_handler(mock_server, 'petstore', mock_client)

    # Check that prompts were registered
    assert mock_server.add_prompt.call_count >= 3

    # Check that the resource handler was registered
    mock_server.register_resource_handler.assert_called_once()

    # Check the result
    assert result['operation_prompts_generated'] is True
    assert result['workflow_prompts_generated'] is True
