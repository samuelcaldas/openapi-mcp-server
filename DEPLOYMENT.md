# Deployment Guide for OpenAPI MCP Server

[← Back to main README](README.md)

This document covers local `stdio` use and network deployment with Streamable HTTP or legacy SSE. `stdio` remains the default; Streamable HTTP serves `/mcp`, while SSE serves `/sse`. Network transports have no built-in inbound MCP authentication.

## Building and Deploying with Docker

The project includes a Dockerfile that sets up the environment for running the OpenAPI MCP Server.

### Building the Docker Image

Navigate to the project directory and build the Docker image:

```bash
# Navigate to the project directory
cd /path/to/openapi-mcp-server

# Build the Docker image
docker build -t openapi-mcp-server:latest .
```

### Running the Container Locally

Once the image is built, you can run it locally:

```bash
# Local MCP clients use stdio; no network port is needed.
docker run -i \
  -e API_NAME=petstore \
  -e API_BASE_URL=https://petstore3.swagger.io/api/v3 \
  -e API_SPEC_URL=https://petstore3.swagger.io/api/v3/openapi.json \
  -e TZ=America/Sao_Paulo \
  openapi-mcp-server:latest

# Streamable HTTP behind an operator-managed, authenticated gateway.
docker run -p 127.0.0.1:8000:8000 \
  -e API_NAME=myapi \
  -e API_BASE_URL=https://api.example.com \
  -e API_SPEC_URL=https://api.example.com/openapi.json \
  -e SERVER_TRANSPORT=http \
  -e SERVER_HOST=0.0.0.0 \
  -e SERVER_PORT=8000 \
  -e ALLOW_REMOTE_BIND=true \
  -e ALLOWED_ORIGINS=https://mcp.example.com \
  -e TZ=America/Sao_Paulo \
  openapi-mcp-server:latest
```

`ALLOW_REMOTE_BIND=true` only permits a non-loopback listener; it does not add inbound authentication. The Docker examples bind the published port to host loopback for a same-host authenticated gateway. If the gateway runs on another host, use a separate trusted container network or firewall that allows only the gateway to reach the server; do not publish the port to untrusted networks. Configure Nginx Proxy Manager (NPM) manually; do not expose an unauthenticated listener directly to the network.

### Environment Variables for Docker

You can customize the container behavior using environment variables:

```bash
# Server configuration
-e SERVER_NAME="My API Server" \
-e SERVER_DEBUG=true \
-e SERVER_MESSAGE_TIMEOUT=60 \
-e SERVER_HOST="127.0.0.1" \
-e SERVER_PORT=8000 \
-e SERVER_TRANSPORT="stdio" \
-e SERVER_HTTP_PATH="/mcp" \
-e ALLOW_REMOTE_BIND="false" \
-e ALLOWED_ORIGINS="" \
-e LOG_LEVEL="INFO" \

# API configuration
-e API_NAME="myapi" \
-e API_BASE_URL="https://api.example.com" \
-e API_SPEC_URL="https://api.example.com/openapi.json" \

# Authentication configuration
-e AUTH_TYPE="api_key" \
-e AUTH_API_KEY="YOUR_API_KEY" \
-e AUTH_API_KEY_NAME="X-API-Key" \
-e AUTH_API_KEY_IN="header" \

# Prometheus configuration
-e ENABLE_PROMETHEUS=false \
-e PROMETHEUS_PORT=9090 \
-e ENABLE_OPERATION_PROMPTS=true
```

### Graceful Shutdown

The OpenAPI MCP Server implements a robust graceful shutdown mechanism to ensure clean termination when the server is stopped or interrupted. This is particularly important for production deployments where abrupt termination could lead to connection errors, data loss, or incomplete operations.

#### How Graceful Shutdown Works

When the server receives a termination signal (SIGINT from Ctrl+C or SIGTERM from container orchestrators):

1. The server logs that it's shutting down gracefully
2. Final metrics are logged to provide visibility into the server's state at shutdown
3. For SIGINT (Ctrl+C), the server chains to the original handler after logging
4. Uvicorn's built-in graceful shutdown process handles the actual shutdown
5. Active connections are allowed to complete before the server exits

#### Configuration Options

The server's graceful shutdown can be configured through uvicorn options:

```bash
# Set a custom timeout for graceful shutdown (in seconds)
-e UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN=5.0

# Enable/disable graceful shutdown (default: true)
-e UVICORN_GRACEFUL_SHUTDOWN=true
```

#### Best Practices for Container Environments

When running in container environments like Docker, Kubernetes, or ECS:

1. **Set appropriate termination grace periods** - Allow enough time for connections to complete
2. **Use SIGTERM for orchestrated shutdowns** - Container orchestrators typically send SIGTERM
3. **Configure health checks** - Ensure they fail appropriately during shutdown
4. **Monitor shutdown metrics** - Track shutdown times and any errors during shutdown

### Deploying to AWS

#### Push to Amazon ECR

```bash
# Authenticate with ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin YOUR_AWS_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com

# Create ECR repository (if it doesn't exist)
aws ecr create-repository --repository-name openapi-mcp-server

# Tag and push the image
docker tag openapi-mcp-server:latest YOUR_AWS_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/openapi-mcp-server:latest
docker push YOUR_AWS_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/openapi-mcp-server:latest
```

#### Health Checks and Monitoring

The Docker container includes a health check script. You can monitor the container health with:

```bash
# Check container health
docker inspect --format='{{.State.Health.Status}}' container_id

# View container logs
docker logs container_id
```

#### Troubleshooting

If you encounter issues:

1. Check container logs: `docker logs container_id`
2. Verify environment variables are set correctly
3. Ensure the API specification URL is accessible from within the container
4. For a same-host gateway, check the loopback-only port mapping (`-p 127.0.0.1:8000:8000`); for a remote gateway, verify the trusted network/firewall allows only that gateway.
5. Verify network connectivity for external API access

## Network transports

`stdio` is the default for local MCP clients. Select a network transport explicitly with `--transport http` (alias for `streamable-http`) or `--transport sse`. Streamable HTTP uses `/mcp` by default; legacy SSE uses `/sse`. SSE is retained for compatibility and should only be used when required by an existing client.

| CLI option | Environment variable | Default | Purpose |
|---|---|---|---|
| `--transport` | `SERVER_TRANSPORT` | `stdio` | `stdio`, `http`, `streamable-http`, or `sse` |
| `--host` | `SERVER_HOST` | `127.0.0.1` | Listener address |
| `--port` | `SERVER_PORT` | `8000` | Listener port |
| `--http-path` | `SERVER_HTTP_PATH` | `/mcp` | Streamable HTTP endpoint path |
| `--allow-remote-bind` | `ALLOW_REMOTE_BIND=true` | disabled | Required for any non-loopback bind |
| `--allowed-origins` | `ALLOWED_ORIGINS` | loopback origins only | Comma-separated exact `http(s)` origins trusted for browser/proxy requests |

CLI options override their corresponding environment variables. `--http-path` must be an absolute path, not a URL or a path containing a query or fragment. SSE keeps its `/sse` endpoint. Missing `Origin` is allowed for non-browser MCP clients. Host and Origin checks remain enabled for all network transports; remote binding does not disable them. These checks are not CORS and do not authenticate callers. Configure explicit trusted origins when a reverse proxy requires them, and preserve the intended host/origin at the proxy boundary. NPM configuration is manual.

The server has no inbound MCP authentication. The existing API auth options (`AUTH_TYPE`, `AUTH_TOKEN`, API key, Basic, or Cognito) authenticate outbound requests from this server to the OpenAPI API; they do not protect inbound MCP connections. Remote binding requires an operator-managed secure gateway that authenticates and authorizes callers. Do not expose a remote listener without one.

Examples:

```bash
# Streamable HTTP on loopback, reachable only from this host
awslabs.openapi-mcp-server --transport http --api-name petstore \
  --api-url https://petstore3.swagger.io/api/v3 \
  --spec-url https://petstore3.swagger.io/api/v3/openapi.json

# Remote listener; publish only behind a secure gateway
awslabs.openapi-mcp-server --transport streamable-http --host 0.0.0.0 \
  --port 8000 --allow-remote-bind --allowed-origins https://mcp.example.com \
  --api-name petstore --api-url https://petstore3.swagger.io/api/v3 \
  --spec-url https://petstore3.swagger.io/api/v3/openapi.json
```

```bash
# Container example; host-loopback only for a same-host secure gateway
# For a remote gateway, use a trusted container network/firewall allowing only that gateway.
# Configure Nginx Proxy Manager manually.
docker run -p 127.0.0.1:8000:8000 \
  -e API_NAME=petstore \
  -e API_BASE_URL=https://petstore3.swagger.io/api/v3 \
  -e API_SPEC_URL=https://petstore3.swagger.io/api/v3/openapi.json \
  -e SERVER_TRANSPORT=http -e SERVER_HOST=0.0.0.0 -e SERVER_PORT=8000 \
  -e ALLOW_REMOTE_BIND=true -e ALLOWED_ORIGINS=https://mcp.example.com \
  -e TZ=America/Sao_Paulo \
  openapi-mcp-server:latest
```

For remote deployments, set an application timezone environment variable supported by the container when needed; the examples use `TZ=America/Sao_Paulo`.

## Observability with AWS Services

### AWS X-Ray for Distributed Tracing

AWS X-Ray provides end-to-end tracing capabilities that help you analyze and debug distributed applications:

1. **Enable X-Ray in your application**:
   ```python
   # Install the X-Ray SDK
   pip install aws-xray-sdk

   # Add X-Ray middleware to your application
   from aws_xray_sdk.core import xray_recorder
   from aws_xray_sdk.ext.flask.middleware import XRayMiddleware

   xray_recorder.configure(service='openapi-mcp-server')
   XRayMiddleware(app, xray_recorder)
   ```

2. **Configure X-Ray daemon** in your container:
   ```yaml
   # Add X-Ray daemon as a sidecar container
   - name: xray-daemon
     image: amazon/aws-xray-daemon
     ports:
       - containerPort: 2000
         protocol: UDP
   ```

3. **Set up IAM permissions** for X-Ray:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": [
           "xray:PutTraceSegments",
           "xray:PutTelemetryRecords"
         ],
         "Resource": "*"
       }
     ]
   }
   ```

### Amazon Managed Service for Prometheus

For comprehensive metrics collection and monitoring, integrate with Amazon Managed Service for Prometheus:

1. **Configure Prometheus in your application**:
   ```python
   # Set environment variables
   ENABLE_PROMETHEUS=true
   PROMETHEUS_PORT=9090
   ```

2. **Set up remote write to Amazon Managed Service for Prometheus**:
   ```yaml
   # prometheus.yml
   global:
     scrape_interval: 15s

   remote_write:
     - url: https://aps-workspaces.us-east-1.amazonaws.com/workspaces/ws-12345678-1234-1234-1234-123456789012/api/v1/remote_write
       sigv4:
         region: us-east-1
       queue_config:
         max_samples_per_send: 1000
         max_shards: 200

   scrape_configs:
     - job_name: 'openapi-mcp-server'
       static_configs:
         - targets: ['localhost:9090']
   ```

3. **Create an IAM role** with permissions for Amazon Managed Service for Prometheus:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": [
           "aps:RemoteWrite",
           "aps:GetSeries",
           "aps:GetLabels",
           "aps:GetMetricMetadata"
         ],
         "Resource": "*"
       }
     ]
   }
   ```

4. **Visualize metrics** using Amazon Managed Grafana:
   - Create a Grafana workspace in the AWS console
   - Add Amazon Managed Service for Prometheus as a data source
   - Import or create dashboards for monitoring your OpenAPI MCP Server

This comprehensive observability setup provides both tracing and metrics monitoring for your OpenAPI MCP Server deployment.

## AWS Service Integration Documentation References

### Amazon Managed Service for Prometheus

For integrating with Amazon Managed Service for Prometheus, refer to:

- [Amazon Managed Service for Prometheus User Guide](https://docs.aws.amazon.com/prometheus/latest/userguide/what-is-Amazon-Managed-Service-Prometheus.html)
- [Getting started with Amazon Managed Service for Prometheus](https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-getting-started.html)
- [Remote write to Amazon Managed Service for Prometheus](https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-remote-write.html)
- [IAM policies for Amazon Managed Service for Prometheus](https://docs.aws.amazon.com/prometheus/latest/userguide/security_iam_service-with-iam.html)

### Amazon CloudWatch

For CloudWatch integration, refer to:

- [Amazon CloudWatch User Guide](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/WhatIsCloudWatch.html)
- [Using Container Insights](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/ContainerInsights.html)
- [Creating CloudWatch alarms](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/AlarmThatSendsEmail.html)
- [CloudWatch Logs for containers](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_GettingStarted.html)

### Amazon Managed Grafana

For visualization with Amazon Managed Grafana:

- [Amazon Managed Grafana User Guide](https://docs.aws.amazon.com/grafana/latest/userguide/what-is-Amazon-Managed-Service-Grafana.html)
- [Adding data sources to Amazon Managed Grafana](https://docs.aws.amazon.com/grafana/latest/userguide/AMG-data-sources.html)
- [Working with dashboards in Amazon Managed Grafana](https://docs.aws.amazon.com/grafana/latest/userguide/AMG-dashboards.html)

### AWS Application Load Balancer

For general load balancer configuration:

- [What is an Application Load Balancer?](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/introduction.html)
- [Creating an Application Load Balancer](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/create-application-load-balancer.html)
- [Target group settings for long-running connections](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-target-groups.html#target-group-attributes)

### Amazon ECS

For deploying to Amazon ECS, refer to the official AWS documentation:
- [Amazon ECS Developer Guide](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/Welcome.html)
- [Creating an Amazon ECS service](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/create-service.html)

### Amazon EKS

For deploying to Amazon EKS, refer to:
- [Amazon EKS User Guide](https://docs.aws.amazon.com/eks/latest/userguide/what-is-eks.html)
- [Getting started with Amazon EKS](https://docs.aws.amazon.com/eks/latest/userguide/getting-started.html)
- [Deploying applications to Amazon EKS](https://docs.aws.amazon.com/eks/latest/userguide/deploy-apps.html)
