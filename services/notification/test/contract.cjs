const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const Ajv2020 = require('ajv/dist/2020.js').default;
const addFormats = require('ajv-formats').default;

const openapi = JSON.parse(readFileSync(
  resolve(__dirname, '../../../api_contracts/openapi/notification.openapi.json'), 'utf8',
));
const ajv = new Ajv2020({ strict: false });
addFormats(ajv);
ajv.addSchema(openapi, 'notification');

function assertSchema(name, value) {
  const validate = ajv.getSchema(`notification#/components/schemas/${name}`);
  assert.ok(validate(value), `${name}: ${ajv.errorsText(validate.errors)}`);
}

async function assertResponse(response, status, schema, requestId) {
  assert.equal(response.status, status);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  if (requestId) assert.equal(response.headers.get('x-request-id'), requestId);
  const body = await response.json();
  assertSchema(schema, body);
  return body;
}

module.exports = { assertResponse, assertSchema };
