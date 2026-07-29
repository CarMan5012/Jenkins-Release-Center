# 钉钉计划级通知设计

## 目标

发布计划只发送两类钉钉 ActionCard：计划开始一次、计划终态一次。计划内单个 Job 的开始、成功或失败不单独推送，避免群消息刷屏。

环境名称统一来自任务关联的 Jenkins View，不使用固定“生产环境”文案，也不只取第一个任务的 View。

## 消息策略

- 开始：计划进入执行流程时发送一次，匹配通知渠道的 `start` 事件。
- 完成：计划聚合为 `SUCCESS` 时发送成功卡片，匹配 `success` 事件。
- 异常终态：计划为 `FAILED` 或 `CANCELLED` 时发送对应标题的终态卡片，复用 `failed` 事件配置。
- 继续使用现有 `plan_start:{plan_id}` 与 `plan_summary:{plan_id}` 进程内防重键；本次不新增通知表或消息队列。

## Jenkins View 环境规则

从计划任务的 `task.job.view` 收集 View，按任务顺序去重：

- 一个 View：显示该 View 名称。
- 多个 View：使用 ` / ` 连接全部唯一名称。
- 没有关联 View：显示 `未分类`。

若 View 有 URL，ActionCard 生成对应的“查看 {View 名称}”按钮；无 URL 时只展示环境文本。这样不依赖当前硬编码的 localhost 发布详情地址。

## 模板

### 发布开始

```markdown
### 🚀 发布开始｜{plan_name}

- **环境**：{jenkins_views}
- **Job 数量**：{total_jobs}
- **开始时间**：{started_at}
- **当前状态**：执行中
```

### 发布成功

```markdown
### ✅ 发布成功｜{plan_name}

- **环境**：{jenkins_views}
- **执行结果**：{success_count}/{total_jobs} 成功
- **总耗时**：{duration}
- **完成时间**：{finished_at}
```

### 发布失败

```markdown
### ❌ 发布失败｜{plan_name}

- **环境**：{jenkins_views}
- **执行结果**：成功 {success_count} / 失败 {failed_count}
- **失败 Job**：{failed_jobs}
- **失败原因**：{error_message}
- **总耗时**：{duration}
```

失败 Job 最多展示三个，其余显示“另有 N 个失败”；错误原因压缩为单行并截断到 200 个字符。

### 发布取消

```markdown
### ⏹ 发布已取消｜{plan_name}

- **环境**：{jenkins_views}
- **已完成**：{completed_count}/{total_jobs}
- **取消位置**：{current_job}
- **取消时间**：{finished_at}
```

## 时间与耗时

- 开始时间取发送开始通知时的当前时间。
- 完成时间取任务中最晚的 `finished_at`，缺失时取发送终态通知的当前时间。
- 总耗时取最早 `started_at` 到最晚 `finished_at` 的墙钟时间；时间不完整时显示 `-`，不把并行 Job 的 duration 相加。

## 错误处理与兼容

- View、Job、时间或错误信息缺失时使用上述降级值，不阻止通知发送。
- 钉钉继续使用现有 ActionCard；企业微信和自定义 Webhook继续接收相同 plain/markdown 内容。
- 单个渠道发送失败只记录日志，不改变发布终态，也不向其他渠道传播异常。

## 验收

- 同一计划正常执行只产生一条开始通知和一条终态通知。
- 单 View、多 View、无 View 三种场景的环境文本正确。
- `SUCCESS`、`FAILED`、`CANCELLED` 使用正确标题、事件和字段。
- 失败 Job 列表与错误原因遵守数量和长度限制。
- 通知发送失败不影响发布状态。
