# API Gateway

Status: implementation not started; gateway software is not selected.

This component exposes the public API entry point and routes requests to the three business services. It owns ingress configuration, not business entities or databases. Broker events bypass the gateway.

Implement the [gateway routing contract](../api_contracts/gateway-routing.md) and [authentication boundary](../api_contracts/authentication.md): explicit routes, HTTPS/CORS, header stripping, correlation IDs, body limits, timeouts, and consistent errors. Keep private service endpoints, database ports, and broker administration inaccessible from public routes.

Keep gateway configuration, routing tests, container/build configuration, and placeholder environment examples here. Document actual run and verification commands after selecting the gateway. Oracle provisioning and shared deployment configuration belong in `infra/`.
