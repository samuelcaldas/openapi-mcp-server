# Deployment Guide for OpenAPI MCP Server

[← Back to main README](README.md)

This document provides guidance on deploying the OpenAPI MCP Server in various environments. The current release exposes the `stdio` transport only; SSE and Streamable HTTP are reserved for future work aligned with the official MCP Python SDK.

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
# Run with Petstore API example
docker run -p 8000:8000 \
  -e API_NAME=petstore \
  -e API_BASE_URL=https://petstore3.swagger.io/api/v3 \
  -e API_SPEC_URL=https://petstore3.swagger.io/api/v3/openapi.json \
  openapi-mcp-server:latest

# Run with custom API configuration
docker run -p 8000:8000 \
  -e API_NAME=myapi \
  -e API_BASE_URL=https://api.example.com \
  -e API_SPEC_URL=https://api.example.com/openapi.json \
  -e SERVER_TRANSPORT=stdio \
  -e ENABLE_PROMETHEUS=false \
  -e ENABLE_OPERATION_PROMPTS=true \
  openapi-mcp-server:latest
```

### Environment Variables for Docker

You can customize the container behavior using environment variables:

```bash
# Server configuration
-e SERVER_NAME="My API Server" \
-e SERVER_DEBUG=true \
-e SERVER_MESSAGE_TIMEOUT=60 \
-e SERVER_HOST="0.0.0.0" \
-e SERVER_PORT=8000 \
-e SERVER_TRANSPORT="stdio" \
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
4. Check that the port mapping is correct (-p 8000:8000)
5. Verify network connectivity for external API access

## Transport status

This fork currently supports `stdio` only. SSE and Streamable HTTP are not implemented yet; deployment guidance for those transports will be added only when the corresponding MCP SDK integration is available.

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
