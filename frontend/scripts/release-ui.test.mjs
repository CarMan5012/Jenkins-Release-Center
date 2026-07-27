import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const sourcePath = new URL('../src/utils/release-ui.ts', import.meta.url);
const source = fs.existsSync(sourcePath) ? fs.readFileSync(sourcePath, 'utf8') : '';
assert.ok(source, 'release-ui helper exists');

const releaseViewSource = fs.readFileSync(new URL('../src/views/release/index.vue', import.meta.url), 'utf8');
const detailSource = fs.readFileSync(new URL('../src/views/release/detail.vue', import.meta.url), 'utf8');
const dashboardSource = fs.readFileSync(new URL('../src/views/dashboard/index.vue', import.meta.url), 'utf8');
const jenkinsSource = fs.readFileSync(new URL('../src/views/jenkins/index.vue', import.meta.url), 'utf8');
const configSource = fs.readFileSync(new URL('../src/views/config/index.vue', import.meta.url), 'utf8');

assert.match(releaseViewSource, /const wizardForm = ref\(\{\s*name: '',\s*type: 'SCHEDULED'/);
assert.match(releaseViewSource, /function openCreateWizard\(\)[\s\S]*?wizardForm\.value = \{\s*name: '',\s*type: 'SCHEDULED'/);
assert.match(releaseViewSource, /:placeholder="!task\.job_id \? '请先选择 Job 任务' : '选择分支或手动输入分支\/Tag'/);
assert.match(releaseViewSource, /未获取到分支列表/);
assert.doesNotMatch(releaseViewSource, /const first = taskBranchOptions/);
assert.match(releaseViewSource, /servers = ref<Array<\{ id: number; name: string; is_active: number \}>>/);
assert.match(releaseViewSource, /if \(server && !server\.is_active\)[\s\S]*?message\.warning\('该 Jenkins 实例已被禁用，请先启用后再选择。'\)[\s\S]*?task\.server_id = null;[\s\S]*?return;/);
assert.match(releaseViewSource, /err\.response\?\.data\?\.detail \|\| '未获取到分支列表/);
assert.match(releaseViewSource, /@click="setQuickExecuteTime\(21, 30\)"[^>]*>21:30<\/n-button>/);
assert.match(releaseViewSource, /@click="setQuickExecuteTime\(22, 0\)"[^>]*>22:00<\/n-button>/);
assert.match(releaseViewSource, /function setQuickExecuteTime\(hour: number, minute: number\)/);
assert.match(configSource, /v-model:value="planRetention"/);
assert.match(configSource, /config_key: 'plan_retention_days'/);
assert.match(configSource, /const planRetention = ref<number \| null>\(30\)/);

assert.match(detailSource, /function retryTask\(task: ReleaseTask\)/);
for (const viewSource of [releaseViewSource, dashboardSource]) {
  assert.match(viewSource, /\/release\/plans\/\$\{(plan|response\.data)\.id\}\/trigger/);
}
assert.doesNotMatch(releaseViewSource, /getDependencyOptions/);

assert.match(releaseViewSource, /\/release\/plans\/\$\{plan\.id\}\/preflight/);
assert.match(detailSource, /\/release\/plans\/\$\{plan\.value\?\.id\}\/preflight/);
for (const viewSource of [releaseViewSource, detailSource, dashboardSource]) {
  assert.match(viewSource, /isPreflightBlocked\([\s\S]*?preflight_status\)/);
  assert.match(viewSource, /preflight_status/);
}
assert.match(releaseViewSource, /<PreflightResult/);
assert.match(detailSource, /<PreflightResult/);
assert.match(jenkinsSource, /response\.data\.preflight_status/);
assert.match(releaseViewSource, /:disabled="[^"]*busyKey === `preflight-\$\{plan\.id\}`/);
assert.match(releaseViewSource, /function triggerPlan\(plan: ReleasePlan\)[\s\S]*?busyKey\.value === `preflight-\$\{plan\.id\}`/);
assert.match(detailSource, /const preflightLoading = ref\(false\)/);
assert.match(detailSource, /:disabled="[^"]*preflightLoading/);
assert.match(detailSource, /function triggerPlan\(\)[\s\S]*?preflightLoading\.value/);
assert.match(jenkinsSource, /runModalVisible\.value = false;[\s\S]*?router\.push\(`\/release\/\$\{response\.data\.id\}`\)/);

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
  getPreflightMeta,
  isPreflightBlocked,
  formatDuration,
  getQuickExecuteTime,
  filterPlans,
  sortPlans,
} = module.exports;

assert.equal(getStatusMeta('SUCCESS').tone, 'success');
assert.equal(getStatusMeta('RUNNING').label, '运行中');
assert.equal(getStatusMeta('RUNNING', null).label, '排队中');
assert.equal(getStatusMeta('RUNNING', 12).label, '构建中');
assert.equal(getStatusMeta('QUEUED').label, '排队中');
assert.equal(getStatusMeta('BUILDING').label, '构建中');
assert.equal(getPreflightMeta('FAILED').label, '未通过');
assert.equal(getPreflightMeta('FAILED').tone, 'danger');
assert.deepEqual({ ...getPreflightMeta('WARNING') }, { label: '警告', tone: 'warning' });
assert.equal(getPreflightMeta('PASSED').label, '通过');
assert.equal(getPreflightMeta('PASSED').tone, 'success');
assert.equal(getPreflightMeta('UNCHECKED').label, '未检查');
assert.equal(getPreflightMeta('UNCHECKED').tone, 'neutral');
assert.equal(getPreflightMeta('UNKNOWN').label, '未检查');
assert.equal(getPreflightMeta('UNKNOWN').tone, 'neutral');
assert.equal(getPreflightMeta(null).label, '未检查');
assert.equal(getPreflightMeta(null).tone, 'neutral');
assert.equal(isPreflightBlocked(), true);
assert.equal(isPreflightBlocked('UNCHECKED'), true);
assert.equal(isPreflightBlocked('FAILED'), true);
assert.equal(isPreflightBlocked('WARNING'), false);
assert.equal(isPreflightBlocked('PASSED'), false);
assert.equal(isPreflightBlocked('UNKNOWN'), true);
assert.equal(formatDuration(65), '1m 5s');
assert.equal(formatDuration(null), '-');

const quickTimeNow = new Date(2026, 6, 27, 21, 45).getTime();
assert.equal(
  getQuickExecuteTime(null, 22, 0, quickTimeNow),
  new Date(2026, 6, 27, 22, 0).getTime(),
);
assert.equal(
  getQuickExecuteTime(null, 21, 30, quickTimeNow),
  new Date(2026, 6, 28, 21, 30).getTime(),
);
assert.equal(
  getQuickExecuteTime(new Date(2026, 6, 30, 8, 15).getTime(), 21, 30, quickTimeNow),
  new Date(2026, 6, 30, 21, 30).getTime(),
);

const plans = [
  { name: 'api-prod', status: 'FAILED', type: 'PIPELINE', created_at: '2026-07-09T02:00:00Z' },
  { name: 'web-dev', status: 'SUCCESS', type: 'IMMEDIATE', created_at: '2026-07-09T01:00:00Z' },
];

assert.equal(filterPlans(plans, 'api', 'ALL', 'ALL').length, 1);
assert.equal(filterPlans(plans, '', 'FAILED', 'PIPELINE')[0].name, 'api-prod');
assert.equal(sortPlans(plans, 'created_desc')[0].name, 'api-prod');
