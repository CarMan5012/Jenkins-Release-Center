# Jenkins 数据一致性修复设计

## 背景

当前实现通过“最近构建”推测发布任务对应的 Jenkins build，并以最近 20 条构建作为删除历史的依据。这会在并发构建、多 Jenkins 实例和服务重启场景中产生错误关联、跨实例去重和历史误删。计划查询接口还会执行校正并产生写库、通知和流水线推进等副作用，导致多个页面轮询与后台任务竞争。

本设计将 Jenkins 返回的稳定身份作为唯一事实来源，不再根据“最新”进行推断。

## 目标

- 发布任务始终关联到自己触发的 Jenkins queue item 和 build。
- 同名 Job、同构建号在不同 Jenkins 实例之间互不影响。
- 只有 Jenkins 完整构建清单明确缺失的外部历史才允许删除。
- 发布任务与外部历史使用同一套状态和时间转换规则。
- 并发校正最多执行一次通知、历史写入和流水线推进。
- SQLite 和 MySQL 均可保留现有数据完成幂等升级。

## 非目标

- 不引入 Jenkins 插件、Webhook 或消息队列。
- 不恢复已经被旧逻辑删除且 Jenkins 也不再保留的历史详情。
- 不重构发布、调度或通知模块中与本问题无关的代码。

## 核心不变量

1. `ReleaseTask.build_number` 一旦由对应 queue item 解析成功，不得被其他构建覆盖。
2. Jenkins build 的本地身份是 `(server_id, job_name, build_number)`。
3. `recent_builds` 只能用于新增和更新，不能证明旧构建已被删除。
4. Jenkins 完整构建清单不可用时，不执行任何历史删除。
5. GET API 不触发 Jenkins 请求、数据库写入、通知或流水线推进。
6. 同一任务的最终状态只能由一个并发执行者成功认领。

## 数据模型

### `release_task`

新增可空整数列 `jenkins_queue_id`。触发 Jenkins 后，从响应的 queue URL 提取并立即保存。现有任务保持 `NULL`，不做猜测性回填。

### `release_history`

新增可空整数列 `server_id`，作为 Jenkins 实例快照标识，不建立级联删除外键。历史记录需要在 Jenkins 实例配置删除后继续保留。

新增唯一索引：

```text
(server_id, job_name, build_number)
```

旧数据先通过唯一的 `JenkinsServer.name` 回填 `server_id`。只在相同 `server_id`、`job_name`、`build_number` 内清理重复项；无法匹配实例的旧记录保留且不参与破坏性合并。

## 任务触发与恢复流程

### 正常触发

1. 调用 Jenkins build API，得到 queue URL。
2. 校验 queue URL 与已配置 Jenkins 同源。
3. 从 `/queue/item/{id}/` 提取 `jenkins_queue_id` 并提交数据库。
4. 轮询该 queue item，得到 executable build number。
5. 通过条件更新保存 `build_number`，条件为任务仍处于活动状态且当前构建号为空或相同。
6. 后续状态查询只使用该 build number。

### 服务重启恢复

当活动任务缺少 `build_number` 时：

1. 如果存在 `jenkins_queue_id`，先查询对应 queue item。
2. queue item 仍存在且包含 executable 时，保存其 build number。
3. queue item 已过期时，查询包含 `queueId` 的近期 builds，并只接受 `queueId` 完全相同的 build。
4. 仍无法匹配时，保持 `QUEUED`，记录可观察的同步原因并等待人工处理；不得选择最大构建号或最新活动构建。

已有 `build_number` 的任务只查询该构建，不再扫描其他 build 进行纠偏。

### 取消

只有任务已经精确关联 `build_number` 时才调用该 build 的 `/stop`。缺少 build number 的排队任务取消本地调度并标记取消；不扫描并停止同 Job 的其他活动构建，避免终止其他用户或系统触发的任务。

## 外部历史同步

每个 Jenkins 实例和 Job 分别执行：

1. 获取最近 20 条构建，按 `(server_id, job_name, build_number)` 幂等新增或更新状态、参数、触发人、时间和日志。
2. 获取 Jenkins 完整 build number 集合。
3. 完整集合获取成功时，只删除该实例、该 Job 下集合中不存在的 `is_external=true` 历史。
4. 完整集合获取失败时跳过删除并记录 warning。

同步不得跨实例查询或去重。数据库唯一索引作为最后一道并发保护；唯一键冲突时重新读取并更新现有记录。

## 状态和时间规则

状态只在共享转换函数中映射：

| Jenkins 数据 | 本地状态 |
| --- | --- |
| `building=true` | `BUILDING` |
| `result=SUCCESS` | `SUCCESS` |
| `result=UNSTABLE` | `UNSTABLE` |
| `result=ABORTED` | `CANCELLED` |
| `result=FAILURE` 或 `NOT_BUILT` | `FAILED` |
| `building=false, result=null` | 不提前终结现有任务；外部历史显示 `UNKNOWN` |

`raw_response` 保留 Jenkins 原始 result，方便排障。

- 完成后的 `duration` 只使用 Jenkins 返回的毫秒值换算为秒。
- `started_at` 统一保存 Jenkins API 的 `timestamp`。
- 完成构建的 `finished_at` 使用 `timestamp + duration`。
- 构建中的前端动态耗时是展示估算值，不写回数据库；完成后立即以 Jenkins `duration` 覆盖展示。

## 并发与副作用

计划列表和详情 GET 接口改为纯查询。自动校正只由后台定时任务执行，手动校正只由明确的 POST 接口执行。

任务从活动状态进入最终状态时使用条件更新：

```text
UPDATE release_task
SET status = ..., duration = ..., finished_at = ...
WHERE id = ... AND status IN ('QUEUED', 'BUILDING', 'RUNNING')
```

只有更新行数为 1 的执行者可以继续发送通知、写历史和推进流水线。其他执行者重新读取结果并退出。历史唯一索引与现有按 task 更新逻辑共同保证幂等。

## 数据库升级

启动升级保持幂等并按以下顺序执行：

1. 增加 `release_task.jenkins_queue_id`。
2. 增加 `release_history.server_id`。
3. 根据唯一 `server_name` 回填历史 `server_id`。
4. 在同一实例范围内清理旧重复历史。
5. 创建 `(server_id, job_name, build_number)` 唯一索引。

任一步失败都终止启动，避免应用在半升级结构上继续运行。部署前保留数据库备份。

## 错误处理

- queue URL 无法解析或跨 Origin：触发流程失败，不保存猜测数据。
- queue item 暂时不可用：任务保持 `QUEUED`，由下次后台校正重试。
- build 状态请求失败：保留现有状态，不转换为 `FAILED`。
- 完整构建清单失败：保留全部历史。
- 日志获取失败：保留状态元数据，日志为空并记录 warning，不回滚已确认的构建状态。
- 通知失败：记录错误，不回滚 Jenkins 最终状态；后续不因状态轮询重复发送通知。

## 验证

必须增加以下回归测试：

- 已解析的 build `#42` 不会被并发 `#43` 替换。
- 重启后通过 queue item 或 `queueId` 恢复精确 build number。
- 无精确匹配时保持排队，不选择最新 build。
- 两个 Jenkins 实例的同名同号构建保留为两条历史。
- Jenkins 完整清单仍包含的第 21 条历史不会被删除。
- 完整清单请求失败时不删除历史。
- `ABORTED` 在任务和外部历史中均映射为 `CANCELLED`。
- 并发最终化只发送一次通知并推进一次流水线。
- 计划 GET 接口不调用 Jenkins，也不修改数据库。
- 外部同步保存可获得的 Jenkins 日志。
- 前端构建及 UI 自检通过，完成耗时以 Jenkins duration 为准。

## 发布顺序

1. 备份生产数据库。
2. 部署包含幂等数据库升级和新读取逻辑的版本。
3. 运行定向后端测试和前端自检。
4. 启动应用，确认字段回填、去重和唯一索引创建成功。
5. 手动同步一个测试 Job，核对 Jenkins build number、状态、duration 和历史数量。
6. 观察至少一个后台校正周期，确认没有重复通知或历史删除。

已经被旧逻辑删除的历史不在本次自动恢复范围内；如 Jenkins 仍保留这些构建，可另行执行一次性全量回填。
