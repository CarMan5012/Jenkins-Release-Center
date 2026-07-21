import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const sourcePath = new URL('../src/utils/release-ui.ts', import.meta.url);
const source = fs.existsSync(sourcePath) ? fs.readFileSync(sourcePath, 'utf8') : '';
assert.ok(source, 'release-ui helper exists');

const { outputText } = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
  },
});

const module = { exports: {} };
vm.runInNewContext(outputText, {
  module,
  exports: module.exports,
  require,
});

const {
  getStatusMeta,
  formatDuration,
  filterPlans,
  sortPlans,
} = module.exports;

assert.equal(getStatusMeta('SUCCESS').tone, 'success');
assert.equal(getStatusMeta('RUNNING').label, '运行中');
assert.equal(formatDuration(65), '1m 5s');
assert.equal(formatDuration(null), '-');

const plans = [
  { name: 'api-prod', status: 'FAILED', type: 'PIPELINE', created_at: '2026-07-09T02:00:00Z' },
  { name: 'web-dev', status: 'SUCCESS', type: 'IMMEDIATE', created_at: '2026-07-09T01:00:00Z' },
];

assert.equal(filterPlans(plans, 'api', 'ALL', 'ALL').length, 1);
assert.equal(filterPlans(plans, '', 'FAILED', 'PIPELINE')[0].name, 'api-prod');
assert.equal(sortPlans(plans, 'created_desc')[0].name, 'api-prod');
