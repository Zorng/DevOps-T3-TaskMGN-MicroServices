# Contract Validation Results

Date: 2026-11-14  
Contract version: 1.0.0  
Result: PASS

Validated with openapi-spec-validator 0.9.0 and jsonschema 4.26.0 using [the included validator](tools/validate_contracts.py).

| Check | Result |
| --- | --- |
| OpenAPI 3.1.1 documents | 4 valid |
| Documented operations | 41: 33 public, 2 private authorization, 6 per-service health |
| Schema fixture checks across documents | 43 passed |
| Embedded request/response example checks | 51 passed |
| Event examples and variant coverage | 5 passed |
| Negative schema cases | 14 correctly rejected |
| Local references and Markdown links | Resolved |
| Operation IDs and public route uniqueness | Passed |
| Path parameters and private dual-credential requirement | Passed |
| ETag example/version consistency and mention fixture offsets | Passed |

Negative cases include unknown task fields, excessive length, multiple assignees, empty patch, null title, invalid calendar date, too many mention ranges, malformed email, wrong event producer/version/type, and unexpected event properties.

This verifies contract structure and sample payloads only. It does not verify live Google authentication, database transactions, service authorization, networking, broker durability, outbox recovery, or end-to-end deployment. Those must be tested against implementations using [the acceptance scenarios](acceptance-scenarios.md).

All five owner reviews remain pending. The package is ready for contract review and mock/client preparation, not a claim of unanimous team approval or production readiness.
