# Minimal DingTalk Plan Cards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace verbose release notifications with two compact DingTalk ActionCards whose environment comes from Jenkins View, whose status uses restrained colors, and whose single button opens Jenkins.

**Architecture:** Keep the existing plan-level notification entry points and transport. Add one small shared context helper for View names, Jenkins URL, and wall-clock duration, then reuse it in the existing start and summary functions. Do not add persistence, configuration, dependencies, per-Job messages, or new notification events.

**Tech Stack:** Python 3, SQLAlchemy models, DingTalk ActionCard JSON, pytest, `unittest.mock`.

---

## File map

- Modify `backend/app/services/notification.py`: derive card context and render the compact start/final cards.
- Modify `backend/tests/test_release_notify_dingtalk.py`: lock View selection, button URL, colors, status mapping, counts, duration, and plan-level message count.

### Task 1: Derive the Jenkins card context once

**Files:**
- Modify: `backend/app/services/notification.py:7-18,128-255`
- Test: `backend/tests/test_release_notify_dingtalk.py`

- [ ] **Step 1: Write failing context tests**

Add these imports and tests to `backend/tests/test_release_notify_dingtalk.py`:

```python
from datetime import datetime
from types import SimpleNamespace

from app.services.notification import _get_plan_card_context


def card_task(view=None, server_url="https://jenkins.example/", started_at=None, finished_at=None):
    return SimpleNamespace(
        job=SimpleNamespace(view=view) if view else None,
        server=SimpleNamespace(url=server_url),
        started_at=started_at,
        finished_at=finished_at,
    )


def test_plan_card_context_uses_unique_jenkins_views_and_wall_clock_duration():
    production = SimpleNamespace(name="生产环境", url="https://jenkins.example/view/prod/")
    gray = SimpleNamespace(name="灰度环境", url="https://jenkins.example/view/gray/")
    tasks = [
        card_task(production, started_at=datetime(2026, 7, 29, 10, 0), finished_at=datetime(2026, 7, 29, 10, 2)),
        card_task(production, started_at=datetime(2026, 7, 29, 10, 1), finished_at=datetime(2026, 7, 29, 10, 3)),
        card_task(gray, started_at=datetime(2026, 7, 29, 10, 2), finished_at=datetime(2026, 7, 29, 10, 5)),
    ]

    assert _get_plan_card_context(tasks) == (
        "生产环境 / 灰度环境",
        "https://jenkins.example/view/prod/",
        "5m 0s",
    )


def test_plan_card_context_falls_back_to_server_and_uncategorized():
    assert _get_plan_card_context([card_task()]) == (
        "未分类",
        "https://jenkins.example/",
        "-",
    )
```

- [ ] **Step 2: Run the tests and verify RED**

Run from `backend`:

```powershell
.\venv\Scripts\python.exe -m pytest -q tests/test_release_notify_dingtalk.py -k "plan_card_context"
```

Expected: collection fails because `_get_plan_card_context` does not exist.

- [ ] **Step 3: Add the minimal shared helper**

Add this above `send_plan_start_notification` in `backend/app/services/notification.py`:

```python
def _format_duration(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _get_plan_card_context(plan_tasks):
    view_names = []
    view_url = None
    server_url = None
    starts = []
    finishes = []

    for task in plan_tasks:
        server = getattr(task, "server", None)
        if not server_url and server:
            server_url = getattr(server, "url", None)

        job = getattr(task, "job", None)
        view = getattr(job, "view", None) if job else None
        if view:
            name = getattr(view, "name", None)
            if name and name not in view_names:
                view_names.append(name)
            if not view_url:
                view_url = getattr(view, "url", None)

        if getattr(task, "started_at", None):
            starts.append(task.started_at)
        if getattr(task, "finished_at", None):
            finishes.append(task.finished_at)

    environment = " / ".join(view_names) or "未分类"
    duration = "-"
    if starts and finishes:
        seconds = max(0, int((max(finishes) - min(starts)).total_seconds()))
        duration = _format_duration(seconds)

    return environment, view_url or server_url or "", duration
```

Do not introduce a class or configuration object; both callers need exactly these three values.

- [ ] **Step 4: Run the context tests and verify GREEN**

```powershell
.\venv\Scripts\python.exe -m pytest -q tests/test_release_notify_dingtalk.py -k "plan_card_context"
```

Expected: `2 passed`.

- [ ] **Step 5: Commit Task 1**

```powershell
git add backend/app/services/notification.py backend/tests/test_release_notify_dingtalk.py
git commit -m "feat: derive DingTalk card context from Jenkins"
```

### Task 2: Render the compact colored ActionCards

**Files:**
- Modify: `backend/app/services/notification.py:128-255`
- Test: `backend/tests/test_release_notify_dingtalk.py`

- [ ] **Step 1: Write failing card-content tests**

Add a small query stub and these tests to `backend/tests/test_release_notify_dingtalk.py`:

```python
def notification_db(notif_module, plan, tasks, config):
    db = MagicMock()

    def query(model):
        result = MagicMock()
        if model == notif_module.NotifyConfig:
            result.filter.return_value.all.return_value = [config]
        elif model == ReleasePlan:
            result.filter.return_value.first.return_value = plan
        elif model == ReleaseTask:
            result.filter.return_value.all.return_value = tasks
        return result

    db.query.side_effect = query
    return db


def test_plan_start_card_is_compact_colored_and_links_to_jenkins(monkeypatch):
    import app.services.notification as notif_module

    view = SimpleNamespace(name="生产环境", url="https://jenkins.example/view/prod/")
    task = card_task(view)
    plan = SimpleNamespace(id=101, name="订单服务发布", notify_dingtalk=True, status="RUNNING")
    config = SimpleNamespace(channel_type="DINGTALK", trigger_events=["start"], name="DingTalk")
    send = MagicMock()
    notif_module._sent_notifications_cache.discard("plan_start:101")
    monkeypatch.setattr(notif_module, "SyncSessionLocal", lambda: notification_db(notif_module, plan, [task], config))
    monkeypatch.setattr(notif_module, "send_notification_to_channel", send)

    notif_module.send_plan_start_notification(101)

    send.assert_called_once()
    _, title, plain, markdown = send.call_args.args
    assert title == "发布开始"
    assert plain == "计划: 订单服务发布, 环境: 生产环境, Job: 1, 状态: 执行中"
    assert markdown == (
        '### <font color="#1677FF">发布开始</font>\n\n'
        '**计划**：订单服务发布\n'
        '**环境**：生产环境\n'
        '**Job**：1\n'
        '**状态**：<font color="#1677FF">执行中</font>'
    )
    assert send.call_args.kwargs["btns"] == [
        {"title": "查看 Jenkins", "actionURL": "https://jenkins.example/view/prod/"}
    ]


@pytest.mark.parametrize(
    ("plan_id", "status", "task_status", "expected_event", "expected_title", "color"),
    [
        (102, "SUCCESS", "SUCCESS", "success", "发布成功", "#52C41A"),
        (103, "FAILED", "FAILED", "failed", "发布失败", "#FF4D4F"),
        (104, "CANCELLED", "CANCELLED", "failed", "发布取消", "#8C8C8C"),
    ],
)
def test_plan_summary_card_maps_terminal_status_and_colors(
    monkeypatch, plan_id, status, task_status, expected_event, expected_title, color
):
    import app.services.notification as notif_module

    view = SimpleNamespace(name="生产环境", url="https://jenkins.example/view/prod/")
    task = card_task(
        view,
        started_at=datetime(2026, 7, 29, 10, 0),
        finished_at=datetime(2026, 7, 29, 10, 3, 26),
    )
    task.status = task_status
    plan = SimpleNamespace(id=plan_id, name="订单服务发布", notify_dingtalk=True, status=status)
    config = SimpleNamespace(
        channel_type="DINGTALK",
        trigger_events=[expected_event],
        name="DingTalk",
    )
    send = MagicMock()
    notif_module._sent_notifications_cache.discard(f"plan_summary:{plan_id}")
    monkeypatch.setattr(notif_module, "SyncSessionLocal", lambda: notification_db(notif_module, plan, [task], config))
    monkeypatch.setattr(notif_module, "send_notification_to_channel", send)

    notif_module.send_plan_summary_notification(plan_id)

    send.assert_called_once()
    _, title, plain, markdown = send.call_args.args
    assert title == expected_title
    assert f'<font color="{color}">{expected_title}</font>' in markdown
    assert '**环境**：生产环境' in markdown
    assert '**耗时**：3m 26s' in markdown
    assert '**结果**：成功 ' in markdown
    assert '<font color="#52C41A">' in markdown
    assert '<font color="#FF4D4F">' in markdown
    assert not any(
        symbol in markdown
        for symbol in map(chr, (0x1F680, 0x2705, 0x274C, 0x23F9))
    )
    assert send.call_args.kwargs["btns"] == [
        {"title": "查看 Jenkins", "actionURL": "https://jenkins.example/view/prod/"}
    ]
    assert "错误" not in markdown
    assert "失败 Job" not in markdown
```

- [ ] **Step 2: Run the new card tests and verify RED**

```powershell
.\venv\Scripts\python.exe -m pytest -q tests/test_release_notify_dingtalk.py -k "plan_start_card or plan_summary_card"
```

Expected: failures show the old verbose title/body, missing environment/duration, no Jenkins button, and cancellation mapped as success.

- [ ] **Step 3: Replace only the start-card rendering block**

In `send_plan_start_notification`, after loading `plan_tasks`, replace the old status and markdown block with:

```python
        total_jobs = len(plan_tasks)
        environment, action_url, _ = _get_plan_card_context(plan_tasks)
        title = "发布开始"
        markdown_content = "\n".join([
            '### <font color="#1677FF">发布开始</font>',
            "",
            f"**计划**：{plan.name}",
            f"**环境**：{environment}",
            f"**Job**：{total_jobs}",
            '**状态**：<font color="#1677FF">执行中</font>',
        ])
        plain_content = (
            f"计划: {plan.name}, 环境: {environment}, "
            f"Job: {total_jobs}, 状态: 执行中"
        )
        btns = [{"title": "查看 Jenkins", "actionURL": action_url}]
```

Change the call inside the existing channel loop to:

```python
send_notification_to_channel(
    config, title, plain_content, markdown_content, btns=btns
)
```

- [ ] **Step 4: Replace only the summary-card rendering block**

In `send_plan_summary_notification`, replace the old `now_str`, environment, status and markdown construction with:

```python
        total_jobs = len(plan_tasks)
        success_count = sum(1 for task in plan_tasks if task.status == "SUCCESS")
        fail_count = sum(
            1 for task in plan_tasks if task.status in ["FAILED", "UNSTABLE"]
        )
        environment, action_url, duration = _get_plan_card_context(plan_tasks)

        if plan.status == "CANCELLED":
            plan_event = "failed"
            title = "发布取消"
            title_color = "#8C8C8C"
        elif plan.status == "FAILED" or fail_count:
            plan_event = "failed"
            title = "发布失败"
            title_color = "#FF4D4F"
        else:
            plan_event = "success"
            title = "发布成功"
            title_color = "#52C41A"

        markdown_content = "\n".join([
            f'### <font color="{title_color}">{title}</font>',
            "",
            f"**计划**：{plan.name}",
            f"**环境**：{environment}",
            (
                '**结果**：成功 <font color="#52C41A">'
                f'{success_count}</font> / 失败 <font color="#FF4D4F">'
                f'{fail_count}</font>'
            ),
            f"**耗时**：{duration}",
        ])
        plain_content = (
            f"计划: {plan.name}, 环境: {environment}, "
            f"成功: {success_count}, 失败: {fail_count}, 耗时: {duration}"
        )
        btns = [{"title": "查看 Jenkins", "actionURL": action_url}]
```

Change the call inside the existing channel loop to pass `btns=btns`.

- [ ] **Step 5: Run the notification tests and verify GREEN**

```powershell
.\venv\Scripts\python.exe -m pytest -q tests/test_release_notify_dingtalk.py
```

Expected: all tests in the file pass.

- [ ] **Step 6: Run related release tests**

```powershell
.\venv\Scripts\python.exe -m pytest -q tests/test_release_notify_dingtalk.py tests/test_release_cancellation.py tests/test_jenkins_consistency.py
```

Expected: all selected tests pass; notification mocks still observe one terminal side effect.

- [ ] **Step 7: Run syntax and full backend verification**

```powershell
.\venv\Scripts\python.exe -m py_compile app/services/notification.py tests/test_release_notify_dingtalk.py
.\venv\Scripts\python.exe -m pytest -q
```

Expected: syntax check exits `0`; the full backend suite passes.

- [ ] **Step 8: Commit Task 2**

```powershell
git add backend/app/services/notification.py backend/tests/test_release_notify_dingtalk.py
git commit -m "feat: simplify DingTalk release cards"
```

## Scope guard

- Do not change `send_notification_to_channel` signing, webhook validation, retry behavior, or channel payload formats.
- Do not add database migrations, dependencies, configuration fields, notification tables, or per-Job messages.
- Do not expose Jenkins credentials or webhook URLs in card content or logs.
- Preserve unrelated working-tree changes and stage only the two implementation files per task.
