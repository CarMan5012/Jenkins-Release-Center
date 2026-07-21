# Jenkins Backup View Tabs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Jenkins 备份中保存视图快照，并在汇总页面使用横向标签页按视图过滤 Job。

**Architecture:** `details.json` 升级到版本 2，顶层新增只保存视图名称与 Job 名称的 `views`，现有 `jobs` 详情不重复。前端一次加载完整详情，在内存中选择视图并过滤表格；版本 1 自动生成“全部任务”，未被任何视图引用的 Job 自动进入“未分类”。

**Tech Stack:** Python、FastAPI、requests、pytest、Vue 3、TypeScript、Naive UI、Node assert

---

### Task 1: 备份视图快照

**Files:**
- Modify: `backend/app/services/jenkins_backup_service.py:456-491,599-701`
- Test: `backend/tests/test_jenkins_backup.py`

- [ ] **Step 1: 写视图快照失败测试**

在 `backend/tests/test_jenkins_backup.py` 导入 `get_view_snapshots`，新增测试：

```python
def test_get_view_snapshots_maps_jobs_and_falls_back():
    session = MagicMock()
    views_response = MagicMock()
    views_response.json.return_value = {
        "views": [
            {"name": "All", "url": "http://jenkins/view/All/"},
            {"name": "生产环境", "url": "http://jenkins/view/prod/"},
        ]
    }
    views_response.raise_for_status.return_value = None
    session.get.return_value = views_response

    with patch(
        "app.services.jenkins_backup_service.get_all_jobs_recursive",
        side_effect=[
            [{"name": "api", "url": "http://jenkins/job/api/", "class": "job"}],
            [{"name": "api", "url": "http://jenkins/job/api/", "class": "job"}],
        ],
    ):
        snapshots = get_view_snapshots(session, "http://jenkins/", {"api", "web"})

    assert snapshots == [
        {"name": "All", "job_names": ["api"]},
        {"name": "生产环境", "job_names": ["api"]},
    ]

    session.get.side_effect = requests.RequestException("unavailable")
    assert get_view_snapshots(session, "http://jenkins/", {"api", "web"}) == [
        {"name": "全部任务", "job_names": ["api", "web"]}
    ]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest backend/tests/test_jenkins_backup.py::test_get_view_snapshots_maps_jobs_and_falls_back -v`

Expected: FAIL，提示 `get_view_snapshots` 尚未定义。

- [ ] **Step 3: 实现最小视图采集函数**

在 `get_all_jobs_recursive` 后新增：

```python
def get_view_snapshots(session, base_url, backed_job_names):
    backed_job_names = set(backed_job_names)
    fallback = [{"name": "全部任务", "job_names": sorted(backed_job_names)}]
    try:
        response = session.get(
            urljoin(base_url, "api/json?tree=views[name,url]"),
            timeout=15,
        )
        response.raise_for_status()
        snapshots = []
        for view in response.json().get("views", []):
            if not view.get("name") or not view.get("url"):
                continue
            names = [
                job["name"]
                for job in get_all_jobs_recursive(session, base_url, view["url"])
                if job["name"] in backed_job_names
            ]
            snapshots.append({"name": view["name"], "job_names": names})
        return snapshots or fallback
    except (requests.RequestException, ValueError, TypeError, KeyError):
        return fallback
```

生成 `details.json` 前调用：

```python
view_snapshots = get_view_snapshots(session, base_url, all_parsed_info)
```

写入结构改为：

```python
{"version": 2, "views": view_snapshots, "jobs": job_details}
```

- [ ] **Step 4: 更新集成断言并运行测试**

将原 `details["version"] == 1` 攏为：

```python
assert details["version"] == 2
assert details["views"] == [
    {"name": "全部任务", "job_names": ["h5-shop-build"]}
]
```

Run: `pytest backend/tests/test_jenkins_backup.py -v`

Expected: 全部通过。

### Task 2: 详情接口和前端数据兼容

**Files:**
- Modify: `backend/app/api/jenkins.py:25-39`
- Modify: `frontend/src/utils/jenkins-backup.ts`
- Test: `backend/tests/test_jenkins.py`
- Test: `frontend/scripts/jenkins-backup-ui.test.mjs`

- [ ] **Step 1: 写版本 1、版本 2 归一化失败测试**

在前端测试中断言版本 1 自动回退、版本 2 保留视图并添加未分类：

```js
const legacy = normalizeBackupDetails({
  available: true,
  version: 1,
  jobs: [{ name: 'api' }],
});
assert.deepEqual(Array.from(legacy.views[0].job_names), ['api']);
assert.equal(legacy.views[0].name, '全部任务');

const grouped = normalizeBackupDetails({
  available: true,
  version: 2,
  views: [{ name: '生产环境', job_names: ['api'] }],
  jobs: [{ name: 'api' }, { name: 'web' }],
});
assert.equal(grouped.views[1].name, '未分类');
assert.deepEqual(Array.from(grouped.views[1].job_names), ['web']);
```

- [ ] **Step 2: 运行前后端测试确认失败**

Run: `npm.cmd run test:ui`

Expected: FAIL，`views` 不存在。

Run: `pytest backend/tests/test_jenkins.py -v`

Expected: FAIL，详情响应未返回 `views`。

- [ ] **Step 3: 扩展详情接口和 TypeScript 类型**

`read_backup_details` 的两个返回结构都新增 `views`：

```python
return {"available": False, "version": 0, "views": [], "jobs": []}
```

```python
return {
    "available": True,
    "version": details.get("version", 1),
    "views": details.get("views", []),
    "jobs": details["jobs"],
}
```

前端新增：

```ts
export interface BackupView {
  name: string;
  job_names: string[];
}

export interface BackupDetails {
  available: boolean;
  version: number;
  views: BackupView[];
  jobs: BackupJobDetail[];
}
```

在 `normalizeBackupDetails` 先归一化 `jobs`，再处理视图：缺少视图时生成“全部任务”；收集所有 `job_names`，把未引用 Job 追加为“未分类”；只保留实际存在于 `jobs` 的名称。

- [ ] **Step 4: 运行兼容测试**

Run: `pytest backend/tests/test_jenkins.py -v`

Expected: 全部通过。

Run: `npm.cmd run test:ui`

Expected: 全部通过。

### Task 3: 横向视图标签和部署

**Files:**
- Modify: `frontend/src/views/jenkins/BackupDrawer.vue`

- [ ] **Step 1: 增加当前视图和过滤结果**

导入 `computed`，增加：

```ts
const activeViewName = ref('');
const visibleJobs = computed(() => {
  const view = details.value.views.find((item) => item.name === activeViewName.value);
  if (!view) return details.value.jobs;
  const names = new Set(view.job_names);
  return details.value.jobs.filter((job) => names.has(job.name));
});
```

`clearSummary` 清空 `activeViewName`。加载详情后默认选择 `All`（不区分大小写），否则选择第一个视图：

```ts
activeViewName.value = details.value.views.find(
  (view) => view.name.toLowerCase() === 'all',
)?.name || details.value.views[0]?.name || '';
```

- [ ] **Step 2: 添加横向标签并替换表格数据源**

在表格滚动容器上方加入原生按钮列表：

```vue
<nav v-if="details.views.length" class="backup-view-tabs" aria-label="Jenkins 视图">
  <button
    v-for="view in details.views"
    :key="view.name"
    type="button"
    :class="{ active: activeViewName === view.name }"
    @click="activeViewName = view.name"
  >
    {{ view.name }} <span>{{ view.job_names.length }}</span>
  </button>
</nav>
```

把 `v-for="job in details.jobs"` 改为 `v-for="job in visibleJobs"`，空状态判断同步改为 `visibleJobs.length`。

- [ ] **Step 3: 添加紧凑且可横向滚动的样式**

```css
.backup-view-tabs {
  display: flex;
  gap: 6px;
  overflow-x: auto;
  padding: 0 0 8px;
}

.backup-view-tabs button {
  flex: none;
  padding: 5px 9px;
  border: 1px solid #dfe4ea;
  border-radius: 7px;
  background: #fff;
  color: #667085;
  cursor: pointer;
}

.backup-view-tabs button.active {
  border-color: #4c8bf5;
  background: #eef5ff;
  color: #175cd3;
}
```

- [ ] **Step 4: 完整验证**

Run: `pytest backend/tests/test_jenkins_backup.py backend/tests/test_jenkins.py -v`

Expected: 全部通过。

Run: `npm.cmd run test:ui`

Expected: 全部通过。

Run: `npm.cmd run build`

Expected: TypeScript 和 Vite 构建退出码为 0。

- [ ] **Step 5: 更新运行环境**

Run: `docker compose build backend web`

Expected: 两个镜像构建成功。

Run: `docker compose up -d --no-deps backend web`

Expected: `release-scheduler-backend` 和 `release-scheduler-web` 为 `Up`。

说明：当前工作目录未被 Git 识别，跳过计划中的提交步骤，不执行任何仓库初始化。
