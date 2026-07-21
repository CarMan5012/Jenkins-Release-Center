import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const sourcePath = new URL('../src/utils/jenkins-backup.ts', import.meta.url);
const source = fs.existsSync(sourcePath) ? fs.readFileSync(sourcePath, 'utf8') : '';
assert.ok(source, 'jenkins backup helper exists');

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
  normalizeBackupDetails,
  environmentCopyText,
  credentialCopyText,
  scriptDisplayName,
} = module.exports;

const normalized = normalizeBackupDetails({
  available: true,
  version: 1,
  jobs: [{ name: 'api-job' }],
});

assert.equal(normalized.jobs[0].name, 'api-job');
assert.deepEqual(Array.from(normalized.jobs[0].scripts), []);
assert.deepEqual(Array.from(normalized.jobs[0].environment.variables), []);
assert.deepEqual(Array.from(normalized.jobs[0].credentials), []);
assert.deepEqual(
  JSON.parse(JSON.stringify(normalized.views)),
  [{ name: '全部任务', job_names: ['api-job'] }],
);

const malformedSource = {
  available: true,
  version: 2,
  jobs: [
    null,
    'bad',
    { name: 'safe', environment: { tools: 7 }, maven_config: 'bad' },
  ],
};
const malformedSnapshot = JSON.stringify(malformedSource);
const malformed = normalizeBackupDetails(malformedSource);

assert.deepEqual(
  JSON.parse(JSON.stringify(malformed.jobs.map((job) => job.name))),
  ['safe'],
);
assert.deepEqual(JSON.parse(JSON.stringify(malformed.jobs[0].environment.tools)), {});
assert.equal(malformed.jobs[0].maven_config, undefined);
assert.equal(JSON.stringify(malformedSource), malformedSnapshot);

const malformedNestedSource = {
  available: true,
  version: 2,
  jobs: [{
    name: 'nested',
    triggers: [null, 'bad', { type: 'timer' }],
    parameters: [
      null,
      'bad',
      { name: 42, type: null, default_value: 7, description: {} },
    ],
    environment: {
      variables: [null, 'bad', { name: 42, value: false, source: 7 }],
    },
    credentials: [{
      id: 5,
      type: null,
      bindings: { username: 'JENKINS_USER', password: 9 },
      values: { username: 'robot', password: false },
      value_unavailable: 'yes',
    }],
    scripts: [null, 'bad', { filename: 12, type: null, phase: false, content: {} }],
  }],
};
const malformedNestedSnapshot = JSON.stringify(malformedNestedSource);
const malformedNested = normalizeBackupDetails(malformedNestedSource);
const nestedJob = malformedNested.jobs[0];

assert.equal(nestedJob.triggers.length, 1);
assert.ok(nestedJob.triggers.every((trigger) => trigger && typeof trigger === 'object'));
assert.deepEqual(JSON.parse(JSON.stringify(nestedJob.parameters)), [
  { name: '', type: '' },
]);
assert.deepEqual(JSON.parse(JSON.stringify(nestedJob.environment.variables)), [
  { name: '', value: '' },
]);
assert.deepEqual(JSON.parse(JSON.stringify(nestedJob.credentials)), [{
  id: '',
  type: '',
  bindings: { username: 'JENKINS_USER' },
  values: { username: 'robot' },
  value_unavailable: false,
}]);
assert.deepEqual(JSON.parse(JSON.stringify(nestedJob.scripts)), [{
  filename: '',
  type: '',
  phase: '',
  content: '',
}]);
assert.equal(JSON.stringify(malformedNestedSource), malformedNestedSnapshot);

const invalidViewsSource = {
  available: true,
  version: 2,
  jobs: [{ name: 'api-job' }],
  views: [
    { name: '', job_names: 'api-job' },
    { name: 42, job_names: ['api-job'] },
    { job_names: null },
  ],
};
const invalidViewsSnapshot = JSON.stringify(invalidViewsSource);
const invalidViews = normalizeBackupDetails(invalidViewsSource);

assert.deepEqual(
  JSON.parse(JSON.stringify(invalidViews.views)),
  [{ name: '全部任务', job_names: ['api-job'] }],
);
assert.equal(JSON.stringify(invalidViewsSource), invalidViewsSnapshot);

const grouped = normalizeBackupDetails({
  available: true,
  version: 2,
  jobs: [{ name: 'api-job' }, { name: 'web-job' }, { name: 'ops-job' }],
  views: [
    {
      name: 'Backend',
      job_names: ['api-job', 'unknown-job', 'api-job', 42],
    },
    { name: '', job_names: ['web-job'] },
    { job_names: ['web-job'] },
    null,
  ],
});

assert.deepEqual(
  JSON.parse(JSON.stringify(grouped.views)),
  [
    { name: 'Backend', job_names: ['api-job'] },
    { name: '未分类', job_names: ['web-job', 'ops-job'] },
  ],
);

const empty = normalizeBackupDetails({
  available: true,
  version: 2,
  jobs: [],
});
assert.deepEqual(Array.from(empty.views), []);

assert.equal(
  environmentCopyText({ name: 'APP_ENV', value: 'test', source: 'job' }),
  'APP_ENV=test',
);

assert.equal(
  credentialCopyText({
    id: 'Harbor',
    type: 'username_password',
    bindings: { username: 'HARBOR_USER', password: 'HARBOR_PASSWORD' },
    values: { username: 'robot', password: 'secret' },
  }),
  'HARBOR_USER=robot\nHARBOR_PASSWORD=secret',
);

assert.equal(
  scriptDisplayName({ type: 'shell', filename: 'build_step_1.sh' }, 0),
  '构建脚本一',
);
assert.equal(
  scriptDisplayName({ type: 'pipeline', filename: 'Jenkinsfile' }, 0),
  '流水线脚本',
);
assert.equal(
  scriptDisplayName({ type: 'maven', filename: 'maven_step_1.sh' }, 0),
  'Maven 构建',
);