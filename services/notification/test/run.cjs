const { globSync } = require('node:fs');
const { resolve } = require('node:path');
const { run } = require('node:test');
const { spec } = require('node:test/reporters');

const suite = process.argv[2];
const files = globSync(`test/${suite}/*.test.cjs`).map((file) => resolve(file));
if (files.length === 0) {
  console.error(`No test files found for ${suite}`);
  process.exit(1);
}

let passed = 0;
const result = run({ files });
result.on('test:pass', (test) => {
  // Node reports a synthetic pass for a file that registers no tests.
  if (test.name !== test.file && test.details.type === 'test' && !test.skip && !test.todo) {
    passed++;
  }
});
result.on('test:fail', () => { process.exitCode = 1; });
result.on('end', () => {
  if (passed === 0) {
    console.error(`No tests passed in the ${suite} suite.`);
    process.exitCode = 1;
  }
});
result.compose(spec).pipe(process.stdout);
