# 极简钉钉发布卡片设计

## 目标

每个发布计划只发送两张钉钉 ActionCard：开始一张、终态一张。计划内单个 Job 不发送通知。

卡片不展示失败详情、错误堆栈或 Job 列表，只保留计划名称、Jenkins View 环境、执行数量和耗时。

## 环境

从计划任务的 `task.job.view.name` 读取 Jenkins View，按任务顺序去重：

- 单 View：显示该名称。
- 多 View：用 ` / ` 连接。
- 无 View：显示 `未分类`。

卡片底部保留一个“查看 Jenkins”按钮，链接到第一个有效 Jenkins View URL。没有 View URL 时链接到第一个任务所属 Jenkins Server URL。

## 卡片

### 开始

```markdown
### 发布开始

**计划**：{plan_name}
**环境**：{jenkins_views}
**Job**：{total_jobs}
**状态**：执行中
```

### 成功

```markdown
### 发布成功

**计划**：{plan_name}
**环境**：{jenkins_views}
**结果**：成功 {success_count} / 失败 {failed_count}
**耗时**：{duration}
```

### 失败或取消

正文与成功卡片相同，只把标题替换为 `发布失败` 或 `发布取消`，结果使用实际成功/失败数量。

## 数据规则

- 开始通知匹配渠道的 `start` 事件。
- 成功终态匹配 `success` 事件。
- 失败和取消终态复用 `failed` 事件。
- 耗时取最早任务 `started_at` 到最晚任务 `finished_at`；时间不完整时显示 `-`。
- 继续使用现有计划级防重键，不新增表、队列或配置项。

## 验收

- 同一计划只发送一张开始卡片和一张终态卡片。
- 环境正确处理单 View、多 View 和无 View。
- 成功、失败、取消标题正确，数量和耗时正确。
- 卡片只有一个“查看 Jenkins”按钮，URL 按环境规则降级。
- 单 Job 过程通知继续保持关闭。
