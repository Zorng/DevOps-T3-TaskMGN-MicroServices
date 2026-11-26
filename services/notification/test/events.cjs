const { randomUUID } = require('node:crypto');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');

const contracts = resolve(__dirname, '../../../api_contracts');
const examples = JSON.parse(readFileSync(resolve(contracts, 'examples/events.json'), 'utf8'));

function example(type) {
  return structuredClone(examples.find((event) => event.type === type));
}

function freshEvent(type, overrides = {}) {
  const event = example(type);
  event.id = randomUUID();
  event.data.recipient_id = randomUUID();
  return Object.assign(event, overrides);
}

module.exports = { contracts, examples, example, freshEvent };
