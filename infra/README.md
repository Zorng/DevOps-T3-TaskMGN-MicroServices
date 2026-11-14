# Infrastructure

Status: deployment and local orchestration are not implemented. Backend hosting target: Oracle Cloud Always Free. Frontend hosting target: Netlify.

This directory holds shared deployment configuration, local orchestration, broker configuration, and centralized logging/metrics configuration. Application source and Dockerfiles remain in their respective component directories.

Keep the deployment stack neutral until the broker, database engines, and component run commands are selected. At that point, add a local Docker Compose configuration with a documented command and relative build contexts pointing to the components. Do not substitute a fake runnable stack for missing services.

Required deployment behavior:

- Separate service-owned databases and persistent storage, even when sharing one VM.
- Private service/database/broker networking and a public HTTPS gateway.
- Durable event delivery and the queue/retry/dead-letter behavior in the [event contract](../api_contracts/events/README.md).
- Centralized logs and metrics with the documented request/event correlation.
- Environment-based secrets, backups/restore steps, health checks, and independently deployable service images.
- ARM64-compatible images if Oracle ARM VMs are used.

VM count, region, provisioning method, broker product, and monitoring tools remain undecided. Store only reproducible source configuration here; keep credentials, Terraform state, local database volumes, and generated runtime data out of Git. Document the actual startup, deployment, recovery, and shutdown commands alongside the configuration when added.
