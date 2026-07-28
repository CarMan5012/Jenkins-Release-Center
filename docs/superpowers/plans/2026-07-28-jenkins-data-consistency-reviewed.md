# Jenkins Data Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 精确关联发布任务与 Jenkins build，按实例隔离历史数据，停止破坏性同步，并保证并发校正只有一次副作用。

**Architecture:** 持久化 Jenkins queue ID，以 `(server_id, job_name, build_number)` 标识历史；复用现有 `get_build_numbers()` 做安全删除。状态转换留在 `jenkins_client.py`，最终化用 SQL 条件更新认领；只把发布计划列表/详情 GET 改为纯查询，日志 GET 仍可只读访问 Jenkins。

**Tech Stack:** FastAPI、SQLAlchemy 2、SQLite/MySQL、requests、pytest、Vue 3、TypeScript、Node assert。

---

## 修改范围

- `backend/app/models/release.py`
- `backend/app/schemas/release.py`
- `backend/app/services/init_db.py`
- `backend/app/services/jenkins_client.py`
- `backend/app/services/release_service.py`
- `backend/app/services/jenkins_sync_task.py`
- `backend/app/api/release.py`
- `backend/app/api/history.py`
- `backend/app/api/jenkins.py`
- `backend/tests/test_jenkins_consistency.py`（新建）
- `backend/tests/test_jenkins_sync_task.py`
- `backend/tests/test_release_cancellation.py`
- `frontend/scripts/release-ui.test.mjs`

保留工作区已有未提交改动；不得 reset、checkout 或覆盖整个文件。

### Task 1: 数据模型与幂等升级

**Files:** models、schemas、init_db、新一致性测试。

- [ ] 写失败测试：用 SQLite 创建旧版最小表，插入同实例重复记录和跨实例同号记录，连续运行两次 `ensure_jenkins_consistency_schema()`；断言新列存在、同实例只保留内部任务记录、跨实例保留两条、唯一索引存在。

```python
def test_consistency_schema_upgrade_is_idempotent():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE jenkins_server (id INTEGER PRIMARY KEY, name VARCHAR(100) UNIQUE)"
        ))
        connection.execute(text(
            "CREATE TABLE release_task (id INTEGER PRIMARY KEY, build_number INTEGER)"
        ))
        connection.execute(text(
            "CREATE TABLE release_history (id INTEGER PRIMARY KEY, task_id INTEGER, "
            "server_name VARCHAR(100), job_name VARCHAR(150), build_number INTEGER, "
            "status VARCHAR(30))"
        ))
        connection.execute(text(
            "INSERT INTO jenkins_server VALUES (1, 's1'), (2, 's2')"
        ))
        connection.execute(text(
            "INSERT INTO release_history VALUES "
            "(1, 10, 's1', 'deploy', 7, 'BUILDING'), "
            "(2, NULL, 's1', 'deploy', 7, 'SUCCESS'), "
            "(3, NULL, 's2', 'deploy', 7, 'SUCCESS')"
        ))
    ensure_jenkins_consistency_schema(engine)
    ensure_jenkins_consistency_schema(engine)
    assert "jenkins_queue_id" in {
        item["name"] for item in inspect(engine).get_columns("release_task")
    }
    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT server_id, task_id FROM release_history ORDER BY server_id"
        )).all()
    assert rows == [(1, 10), (2, None)]
    assert any(
        item["name"] == "uix_release_history_build_identity" and item["unique"]
        for item in inspect(engine).get_indexes("release_history")
    )
```

- [ ] 验证 RED：

```powershell
cd backend
$env:PYTHONPATH='.'
.\venv\Scripts\python.exe -m pytest -q tests/test_jenkins_consistency.py::test_consistency_schema_upgrade_is_idempotent
```

- [ ] 模型增加 `ReleaseTask.jenkins_queue_id`、`ReleaseHistory.server_id` 和唯一约束；响应 schema 暴露两个可空字段。

```python
jenkins_queue_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
server_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

__table_args__ = (
    UniqueConstraint(
        "server_id", "job_name", "build_number",
        name="uix_release_history_build_identity",
    ),
)
```

- [ ] 在 `init_db.py` 实现并在 `create_all` 后调用以下函数。删除重复项时优先保留 `task_id IS NOT NULL`，避免丢失内部任务关联。

```python
def ensure_jenkins_consistency_schema(engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if not {"release_task", "release_history", "jenkins_server"} <= tables:
        return
    task_columns = {c["name"] for c in inspector.get_columns("release_task")}
    history_columns = {c["name"] for c in inspector.get_columns("release_history")}
    with engine.begin() as connection:
        if "jenkins_queue_id" not in task_columns:
            connection.execute(text(
                "ALTER TABLE release_task ADD COLUMN jenkins_queue_id INTEGER NULL"
            ))
        if "server_id" not in history_columns:
            connection.execute(text(
                "ALTER TABLE release_history ADD COLUMN server_id INTEGER NULL"
            ))
    with engine.begin() as connection:
        if engine.dialect.name == "mysql":
            connection.execute(text(
                "UPDATE release_history rh JOIN jenkins_server js "
                "ON js.name = rh.server_name SET rh.server_id = js.id "
                "WHERE rh.server_id IS NULL"
            ))
            connection.execute(text(
                "DELETE older FROM release_history older "
                "JOIN release_history newer ON newer.server_id = older.server_id "
                "AND newer.job_name = older.job_name "
                "AND newer.build_number = older.build_number "
                "AND ((older.task_id IS NULL AND newer.task_id IS NOT NULL) "
                "OR ((older.task_id IS NULL) = (newer.task_id IS NULL) "
                "AND newer.id > older.id)) WHERE older.server_id IS NOT NULL"
            ))
        else:
            connection.execute(text(
                "UPDATE release_history SET server_id = (SELECT id FROM jenkins_server "
                "WHERE jenkins_server.name = release_history.server_name) "
                "WHERE server_id IS NULL"
            ))
            connection.execute(text(
                "DELETE FROM release_history WHERE server_id IS NOT NULL AND EXISTS ("
                "SELECT 1 FROM release_history newer "
                "WHERE newer.server_id = release_history.server_id "
                "AND newer.job_name = release_history.job_name "
                "AND newer.build_number = release_history.build_number "
                "AND ((release_history.task_id IS NULL AND newer.task_id IS NOT NULL) "
                "OR ((release_history.task_id IS NULL) = (newer.task_id IS NULL) "
                "AND newer.id > release_history.id)))"
            ))
    indexes = {i["name"] for i in inspect(engine).get_indexes("release_history")}
    if "uix_release_history_build_identity" not in indexes:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE UNIQUE INDEX uix_release_history_build_identity "
                "ON release_history (server_id, job_name, build_number)"
            ))
```

- [ ] 运行测试并提交：

```powershell
.\venv\Scripts\python.exe -m pytest -q tests/test_jenkins_consistency.py -k schema
git add backend/app/models/release.py backend/app/schemas/release.py backend/app/services/init_db.py backend/tests/test_jenkins_consistency.py
git commit -m "fix: add Jenkins build identity schema"
```

### Task 2: Jenkins queue 和统一映射

**Files:** `jenkins_client.py`、一致性测试。

- [ ] 写失败测试，覆盖同源 queue URL、跨源拒绝、404 queue item、`ABORTED` 和空 result。

```python
def test_queue_and_status_primitives():
    client = JenkinsClient("https://jenkins.example/", "user", "token")
    assert client.extract_queue_id("https://jenkins.example/queue/item/123/") == 123
    with pytest.raises(ValueError):
        client.extract_queue_id("https://other.example/queue/item/123/")
    assert normalize_jenkins_status(True, None) == "BUILDING"
    assert normalize_jenkins_status(False, "ABORTED") == "CANCELLED"
    assert normalize_jenkins_status(False, "FAILURE") == "FAILED"
    assert normalize_jenkins_status(False, None) == "UNKNOWN"
```

- [ ] 实现共享函数和客户端方法：

```python
def normalize_jenkins_status(building: bool, result: Optional[str]) -> str:
    if building:
        return "BUILDING"
    return {
        "SUCCESS": "SUCCESS", "UNSTABLE": "UNSTABLE",
        "ABORTED": "CANCELLED", "FAILURE": "FAILED",
        "NOT_BUILT": "FAILED",
    }.get(result, "UNKNOWN")

def jenkins_datetime(timestamp_ms: Any) -> Optional[datetime]:
    if not isinstance(timestamp_ms, (int, float)) or timestamp_ms <= 0:
        return None
    return datetime.fromtimestamp(timestamp_ms / 1000.0)

def extract_queue_id(self, queue_url: str) -> int:
    queue, base = urlparse(queue_url), urlparse(self.base_url)
    if (queue.scheme, queue.netloc) != (base.scheme, base.netloc):
        raise ValueError("Queue URL origin does not match configured Jenkins origin")
    match = re.search(r"/queue/item/(\d+)/?$", queue.path)
    if not match:
        raise ValueError("Jenkins queue URL does not contain a queue item ID")
    return int(match.group(1))

def get_queue_item(self, queue_id: int) -> Optional[Dict[str, Any]]:
    response = self.session.get(
        urljoin(self.base_url, f"queue/item/{queue_id}/api/json"), timeout=10
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()
```

- [ ] `get_recent_builds` tree 加 `queueId`，`get_build_status` 返回 `queue_id`。
- [ ] 验证并提交：

```powershell
.\venv\Scripts\python.exe -m pytest -q tests/test_jenkins_consistency.py -k primitives tests/test_jenkins.py
git add backend/app/services/jenkins_client.py backend/tests/test_jenkins_consistency.py
git commit -m "fix: expose exact Jenkins queue identity"
```

### Task 3: 精确恢复构建号并安全取消

**Files:** `release_service.py`、`api/release.py`、一致性与取消测试。

- [ ] 在测试文件提供一个完整 `release_task_session(status, build_number, queue_id)` helper：创建内存 engine、`Base.metadata.create_all`、User、Server、Plan、Task，Task 的 `started_at=datetime.now()`。
- [ ] 写三个失败测试：已绑定 #42 不变；queue 过期后只接受同 `queueId` 的 #42；无同 queueId 匹配时保持 QUEUED 且 build number 为空。

```python
client.get_recent_builds.return_value = [
    {"number": 43, "queueId": 1002, "building": True, "result": None},
    {"number": 42, "queueId": 1001, "building": True, "result": None},
]
assert db.get(ReleaseTask, task.id).build_number == 42
```

- [ ] 实现唯一解析入口，删除两个“最新 build”校正块：

```python
ACTIVE_TASK_STATUSES = ("QUEUED", "BUILDING", "RUNNING")

def resolve_task_build_number(db, task, client):
    if task.build_number:
        return task.build_number
    if not task.jenkins_queue_id:
        return None
    item = client.get_queue_item(task.jenkins_queue_id)
    number = ((item or {}).get("executable") or {}).get("number")
    if number is None:
        match = next((b for b in client.get_recent_builds(task.job_name, 20)
                      if b.get("queueId") == task.jenkins_queue_id), None)
        number = match.get("number") if match else None
    if number is None:
        task.error_message = f"Jenkins queue #{task.jenkins_queue_id} is unresolved"
        db.commit()
        return None
    result = db.execute(update(ReleaseTask).where(
        ReleaseTask.id == task.id,
        ReleaseTask.status.in_(ACTIVE_TASK_STATUSES),
        ReleaseTask.build_number.is_(None),
    ).values(build_number=int(number), error_message=None))
    db.commit()
    db.refresh(task)
    return task.build_number if result.rowcount in (0, 1) else None
```

- [ ] 在现有 trigger 重试循环成功返回 queue URL 后立即解析、保存 `jenkins_queue_id`，再轮询 executable；不得删掉现有重试与 Origin 校验。
- [ ] 取消接口删除 recent builds 扫描，只停止任务已记录的 build。所有 retry/reset 同时清空 queue ID、build number、build URL、console URL。
- [ ] 修改取消测试，断言 `stop_build("deploy", 42)` 一次且 `get_recent_builds` 从未调用。
- [ ] 验证并提交：

```powershell
.\venv\Scripts\python.exe -m pytest -q tests/test_jenkins_consistency.py -k reconcile tests/test_release_cancellation.py
git add backend/app/services/release_service.py backend/app/api/release.py backend/tests/test_jenkins_consistency.py backend/tests/test_release_cancellation.py
git commit -m "fix: bind release tasks to Jenkins queue IDs"
```

### Task 4: 原子最终化和计划 GET 纯查询

**Files:** `release_service.py`、`api/release.py`、一致性测试。

- [ ] 写失败测试：同一任务连续最终化两次，第一次 true、第二次 false；mock 通知、历史、流水线推进并断言各一次。另调用 list/get plan，断言不会调用 reconcile。
- [ ] 实现认领：

```python
def claim_task_final_state(db, task_id, status, duration, started_at, finished_at):
    result = db.execute(update(ReleaseTask).where(
        ReleaseTask.id == task_id,
        ReleaseTask.status.in_(ACTIVE_TASK_STATUSES),
    ).values(status=status, duration=duration, started_at=started_at,
             finished_at=finished_at,
             error_message=None if status == "SUCCESS" else f"Jenkins result: {status}"))
    db.commit()
    return result.rowcount == 1
```

- [ ] 实现 `finalize_task_from_jenkins`：使用共享 mapping；BUILDING/UNKNOWN 不最终化；Jenkins timestamp 转 started_at；finished_at 为 started_at + duration；只有 claim 成功者发送通知、写历史、推进流水线和聚合计划。执行轮询和 reconcile 均调用它，删除重复映射。
- [ ] `write_history` 按 task ID 或完整 Jenkins 身份查找历史，优先内部记录；若命中外部记录，补齐 task/plan/server 并设 `is_external=False`，避免唯一键冲突。

```python
histories = db.query(ReleaseHistory).filter(or_(
    ReleaseHistory.task_id == task.id,
    and_(ReleaseHistory.server_id == task.server_id,
         ReleaseHistory.job_name == task.job_name,
         ReleaseHistory.build_number == task.build_number),
)).order_by(ReleaseHistory.task_id.isnot(None).desc(),
            ReleaseHistory.id.desc()).all()
```

- [ ] 从计划 list/detail GET 删除 reconcile、commit 和二次查询，只保留 select 与 404。
- [ ] 验证并提交：

```powershell
.\venv\Scripts\python.exe -m pytest -q tests/test_jenkins_consistency.py -k "final or get_plan" tests/test_release_pipeline.py
git add backend/app/services/release_service.py backend/app/api/release.py backend/tests/test_jenkins_consistency.py
git commit -m "fix: serialize Jenkins task finalization"
```

### Task 5: 非破坏性外部历史同步

**Files:** `jenkins_sync_task.py`、history/jenkins API、同步与一致性测试。

- [ ] 测试文件提供 `external_history_session()`（内存 Base、active server、job）和 `run_external_sync()`（只 patch SessionLocal/JenkinsClient）。写四个失败测试：第 21 条仍在完整清单则保留；完整清单 None 时不删；两实例同名同号保留两条；ABORTED 为 CANCELLED。
- [ ] cleanup 分组和 existing query 全部加入 server ID；保留优先级为内部 task、非 BUILDING、较新 ID。

```python
.order_by(ReleaseHistory.task_id.isnot(None).desc(),
          (ReleaseHistory.status != "BUILDING").desc(),
          ReleaseHistory.id.desc())
```

- [ ] recent 20 只负责 upsert。删除逻辑必须改为：

```python
inventory = client.get_build_numbers(job.name)
if inventory is None:
    logger.warning(f"Skipping deletion for {server.name}/{job.name}: inventory unavailable")
else:
    stale = db.query(ReleaseHistory).filter(
        ReleaseHistory.server_id == server.id,
        ReleaseHistory.job_name == job.name,
        ReleaseHistory.is_external.is_(True),
        ReleaseHistory.build_number.notin_(inventory),
    ).all()
    for history in stale:
        db.delete(history)
    db.commit()
```

- [ ] 状态使用共享 mapping；时间使用共享时间转换；完成 build 调用 progressive log 并保存最多 200000 字符；raw response 保存 result、external_sync、queueId。
- [ ] insert 遇到 `IntegrityError` 时 rollback，按完整身份重新读取并更新；不得覆盖内部记录的 task/plan/is_external。
- [ ] history 列表 GET 删除自动 cleanup；history log 优先按 server ID 找服务器、仅旧数据回退 server name；Jenkins 分支历史查询加入 server ID。
- [ ] 验证现有失败日志测试转绿并提交：

```powershell
.\venv\Scripts\python.exe -m pytest -q tests/test_jenkins_consistency.py -k external tests/test_jenkins_sync_task.py tests/test_jenkins.py
git add backend/app/services/jenkins_sync_task.py backend/app/api/history.py backend/app/api/jenkins.py backend/tests/test_jenkins_consistency.py backend/tests/test_jenkins_sync_task.py
git commit -m "fix: make Jenkins history sync non-destructive"
```

### Task 6: 前端契约回归

**Files:** `frontend/scripts/release-ui.test.mjs`。

- [ ] 删除快速运行必须返回 `preflight_status` 的旧断言，改为响应 message 后跳转 external history。
- [ ] 解构 `getTaskDurationSeconds/getPlanDurationSeconds` 并断言完成任务优先使用 Jenkins duration，而非时间差。

```javascript
assert.match(jenkinsSource, /response\.data\.message/);
assert.match(jenkinsSource, /path:\s*'\/history'/);
assert.match(jenkinsSource, /tab:\s*'external'/);
assert.equal(getTaskDurationSeconds({
  status: 'SUCCESS', duration: 120,
  started_at: '2026-07-28T10:00:00', finished_at: '2026-07-28T10:10:00',
}), 120);
```

- [ ] 验证和提交：

```powershell
cd ..\frontend
& 'D:\Software\Dev\Nodejs\npm.cmd' run test:ui
& 'D:\Software\Dev\Nodejs\npm.cmd' run build
git add frontend/scripts/release-ui.test.mjs
git commit -m "test: align release UI checks with Jenkins data"
```

### Task 7: 全量验证与交付

- [ ] 后端：

```powershell
cd ..\backend
$env:PYTHONPATH='.'
.\venv\Scripts\python.exe -m pytest -q
```

Expected: 全部通过，取消测试不再访问真实 `jenkins.example` 或超时。

- [ ] 前端：

```powershell
cd ..\frontend
& 'D:\Software\Dev\Nodejs\npm.cmd' run test:ui
& 'D:\Software\Dev\Nodejs\npm.cmd' run build
```

Expected: 两条命令退出码 0。

- [ ] 差异检查：

```powershell
cd ..
git diff --check
git status --short
git log -7 --oneline
```

Expected: 无 whitespace 错误；用户原有未提交文件仍保留；本修复形成五个小提交。

- [ ] 部署前人工门禁：生产数据库已有备份；启动升级成功；测试 Job 的 queue/build/status/duration 与 Jenkins 一致；一个后台周期内无重复通知；Jenkins 仍存在的第 21 条历史未删除。

本计划不自动备份生产库、不部署、不触发真实 Jenkins build；这些操作需要部署环境的单独授权。
