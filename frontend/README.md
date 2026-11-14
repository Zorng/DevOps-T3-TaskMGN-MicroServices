# Frontend

Status: implementation not started; framework is not selected.

Implement the screens and journeys in the [product specification](../docs/product-specification.md), using the gateway's [REST contracts](../api_contracts/README.md). The frontend never calls private service APIs or connects to databases or the broker.

The contract specifies Google sign-in, in-memory application sessions, workspace/board navigation, task details, explicit comment mentions, invitations, and in-app notifications. Follow documented validation, pagination, concurrency, and unavailable-target behavior. Do not derive permissions solely from visible UI controls.

Keep source, tests, dependencies/lockfile, and framework configuration here. The deployment target is Netlify; its build command and output directory must be set after choosing the stack. Document required public configuration (gateway URL and Google web client ID), local development origin, and exact install/test/build/run commands. No backend credentials or private service keys belong in frontend configuration.
