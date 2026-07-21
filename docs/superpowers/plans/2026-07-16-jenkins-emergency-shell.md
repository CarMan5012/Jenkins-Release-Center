# Jenkins Emergency Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从 Jenkins 备份按视图多选 Job、获取 HTTPS GitLab 最新分支，并生成一个可预览下载的 Linux 应急发布脚本。

**Architecture:** 备份格式升级为 version 3，增加 Jenkins 全局工具安装路径、Job 选择的工具名称和 GitSCM 凭据。新增一个纯 Python 应急脚本服务负责 `git ls-remote` 分支查询及 Shell 生成，API 仅管理员可用；前端复用现有视图标签和备份详情完成多选、分支选择、预览与下载。

**Tech Stack:** Python 3.12、FastAPI、requests、subprocess、shlex、pytest、Vue 3、TypeScript、Naive UI、Bash、Git

---

### Task 1: 扩充 Jenkins 备份元数据

**Files:**
- Modify: `backend/app/services/jenkins_backup_service.py`
- Modify: `backend/tests/test_jenkins_backup.py`

- [ ] **Step 1: 写失败测试**

新增测试 XML 和 Jenkins 脚本响应，断言：

```python
assert details["version"] == 3
assert details["tool_installations"] == [
    {"type": "maven", "name": "maven-3.9", "home": "/opt/maven-3.9"},
    {"type": "nodejs", "name": "node-20", "home": "/opt/node-20"},
]
assert job["selected_tools"] == {"maven": "maven-3.9", "nodejs": "node-20"}
assert job["git_repositories"][0]["url"] == "https://gitlab.example/team/api.git"
assert job["git_repositories"][0]["credential"]["values"]["password"] == "git-token"
```

Run: `.\venv\Scripts\python.exe -m pytest tests/test_jenkins_backup.py -v`

Expected: FAIL，当前版本为 2 且字段不存在。

- [ ] **Step 2: 复用 Jenkins 管理脚本调用**

把 crumb + `/scriptText` POST 收敛为现有文件内的一个私有函数：

```python
def run_jenkins_script(session, base_url, script):
    crumb = session.get(urljoin(base_url, "crumbIssuer/api/json"), timeout=15)
    headers = {}
    if crumb.status_code == 200:
        data = crumb.json()
        headers[data["crumbRequestField"]] = data["crumb"]
    response = session.post(
        urljoin(base_url, "scriptText"),
        data={"script": script},
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()
    return response.text
```

现有凭据导出改为调用它，保持原异常回退。

- [ ] **Step 3: 采集工具安装路径**

增加通用 Groovy，通过 `ToolDescriptor` 遍历全局安装项，按 descriptor id 识别 `jdk/maven/nodejs/gradle/git`，输出：

```json
[{"type":"maven","name":"maven-3.9","home":"/opt/maven-3.9"}]
```

Python 只接受非空字符串的 `type/name/home`；无权限或插件异常时返回空数组，不影响备份。

- [ ] **Step 4: 保存 Job 工具和 SCM 凭据**

解析 Job XML 中的根级 `jdk`、Maven `mavenName`、NodeJS `nodeJSInstallationName`、Gradle `gradleName`。把 SCM repo 的 `credentials_id` 加入现有凭据导出 ID 集合，并写入：

```python
{
    "selected_tools": {"jdk": "jdk-17", "maven": "maven-3.9"},
    "git_repositories": [
        {"url": repo["url"], "credential": resolved_credential_or_none}
    ],
}
```

顶层写 `version: 3` 和 `tool_installations`，保留 version 2 所有字段。

- [ ] **Step 5: 运行备份测试**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_jenkins_backup.py -v`

Expected: 全部通过，凭据值不出现在 `summary.md`。

### Task 2: GitLab 分支查询和 Shell 生成器

**Files:**
- Create: `backend/app/services/jenkins_emergency_shell.py`
- Create: `backend/tests/test_jenkins_emergency_shell.py`
- Modify: `backend/Dockerfile`

- [ ] **Step 1: 写生成器失败测试**

测试固定详情对象，覆盖：多 Job 顺序、每 Job 分支、`shlex.quote`、工具 HOME/PATH、环境/参数/凭据、Shell/Maven/Gradle、缺 Git/脚本、Pipeline/Batch 拒绝。

```python
result = generate_emergency_script(details, [
    {"name": "api", "branch": "release/a b"},
    {"name": "web", "branch": "main"},
])
assert result.index("开始：api") < result.index("开始：web")
assert "MAVEN_HOME='/opt/maven-3.9'" in result
assert "git clone --branch 'release/a b'" in result
assert "export HARBOR_PASSWORD='p'\"'\"'ass'" in result
```

- [ ] **Step 2: 实现最小纯函数生成器**

实现：

```python
def generate_emergency_script(details: dict, requested_jobs: list[dict]) -> str:
    ...
```

使用 `shlex.quote`；每个 Job 是独立子 Shell。生成唯一 heredoc 标记并确认不在脚本正文中。脚本头包含 `set -Eeuo pipefail`、`umask 077`、明文凭据警告、临时目录 `trap`。工具按 `selected_tools` 的类型+名称精确匹配，写 HOME/PATH 并执行 `test -x`/`command -v`。所有请求先完整校验，再开始拼接，禁止半成品。

- [ ] **Step 3: 写分支查询失败测试**

mock `subprocess.run`，断言命令只有：

```python
["git", "ls-remote", "--heads", repo_url]
```

Token 不在 argv；`GIT_ASKPASS` 指向权限受限临时文件；解析 `refs/heads/release/x`；异常消息不包含用户名、密码或 Token；临时文件最终删除。

- [ ] **Step 4: 实现 HTTPS GitLab 分支查询**

实现：

```python
def list_remote_branches(job: dict) -> list[str]:
    ...
```

只接受 `https://` URL。`username_password` 使用原用户名/密码；`secret_text` 使用用户名 `oauth2` 和 Token。临时 askpass 脚本仅读取 `JRC_GIT_USERNAME/JRC_GIT_PASSWORD` 环境变量，不把值写入文件或命令参数。设置 `GIT_TERMINAL_PROMPT=0`，超时 30 秒，分支去重排序。

- [ ] **Step 5: 安装 Git 并运行测试**

`backend/Dockerfile` 的 apt 包加入 `git`，不新增 Python 依赖。

Run: `.\venv\Scripts\python.exe -m pytest tests/test_jenkins_emergency_shell.py -v`

Expected: 全部通过。

### Task 3: 管理员 API

**Files:**
- Modify: `backend/app/schemas/jenkins.py`
- Modify: `backend/app/api/jenkins.py`
- Modify: `backend/tests/test_jenkins.py`

- [ ] **Step 1: 写 API 失败测试**

覆盖：管理员成功、非管理员拒绝、备份不存在、Job 不属于备份、分支查询结果、脚本响应文件名/内容、底层错误脱敏。

- [ ] **Step 2: 增加请求模型**

```python
class EmergencyBranchRequest(BaseModel):
    job_name: str

class EmergencyJobSelection(BaseModel):
    name: str
    branch: str

class EmergencyScriptRequest(BaseModel):
    jobs: list[EmergencyJobSelection]
```

限制名称/分支非空，Job 数量 1 到 100。

- [ ] **Step 3: 增加两个管理员端点**

复用 `read_backup_details` 和现有备份归属/ZIP 路径校验：

```text
POST .../{backup_id}/emergency-branches
POST .../{backup_id}/emergency-script
```

前者返回 `{"branches": [...]}`；后者返回 `filename/content/warnings`。两者使用 `get_current_active_admin`，错误响应不包含 SCM 或构建凭据。

- [ ] **Step 4: 运行 API 测试**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_jenkins.py tests/test_jenkins_emergency_shell.py -v`

Expected: 全部通过。

### Task 4: 汇总页多选、分支和脚本预览

**Files:**
- Modify: `frontend/src/views/jenkins/BackupDrawer.vue`
- Modify: `frontend/src/utils/jenkins-backup.ts`
- Modify: `frontend/scripts/jenkins-backup-ui.test.mjs`

- [ ] **Step 1: 增加响应归一化测试**

新增 `normalizeEmergencyScriptResponse`，测试错误输入返回空文件名/内容/警告，正确输入保持内容；增加下载文件名安全化测试。

- [ ] **Step 2: 增加最小状态与交互**

记录当前 `backupId`；“生成应急脚本”按钮打开选择区。可选 Job 来自当前 `visibleJobs`，每行包含 checkbox、Job 名称、分支 select。勾选时调用分支接口；失败时显示输入框。切换备份/视图或关闭汇总时清空选择、分支和预览。

- [ ] **Step 3: 预览、复制和下载**

提交选中的 `{name, branch}` 获取脚本；使用现有复制函数。下载使用浏览器原生 Blob：

```ts
const blob = new Blob([content], { type: 'text/x-shellscript;charset=utf-8' });
const url = URL.createObjectURL(blob);
```

显示明文凭据警告和命令：`chmod 700 emergency-release.sh && ./emergency-release.sh`。不增加依赖。

- [ ] **Step 4: 前端验证**

Run: `npm.cmd run test:ui`

Expected: 全部通过。

Run: `npm.cmd run build`

Expected: vue-tsc 和 Vite 退出码 0。

### Task 5: 整体验证与部署

**Files:**
- Verify only: all files above

- [ ] **Step 1: 后端相关测试**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_jenkins_backup.py tests/test_jenkins.py tests/test_jenkins_emergency_shell.py -q`

Expected: 全部通过。

- [ ] **Step 2: 前端测试和构建**

Run: `npm.cmd run test:ui`

Run: `npm.cmd run build`

Expected: 均退出码 0。

- [ ] **Step 3: 部署（需要用户明确授权）**

```powershell
docker compose build backend web
docker compose up -d --no-deps backend web
docker compose ps backend web
```

最后访问 `http://localhost:83/` 和 `http://localhost:8021/docs`，期望 HTTP 200。

当前目录不是有效 Git 仓库，不初始化 Git，也不执行提交步骤。
