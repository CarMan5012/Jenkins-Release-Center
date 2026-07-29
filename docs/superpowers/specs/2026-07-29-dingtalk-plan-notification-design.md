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

颜色只用于标题、状态和成功/失败数字：

- 开始：蓝色 `#1677FF`。
- 成功：绿色 `#52C41A`。
- 失败：红色 `#FF4D4F`。
- 取消：灰色 `#8C8C8C`。

## 卡片

### 开始

```markdown
### <font color="#1677FF">发布开始</font>

**计划**：{plan_name}
**环境**：{jenkins_views}
**Job**：{total_jobs}
**状态**：<font color="#1677FF">执行中</font>
```

### 成功

```markdown
### <font color="#52C41A">发布成功</font>

**计划**：{plan_name}
**环境**：{jenkins_views}
**结果**：成功 <font color="#52C41A">{success_count}</font> / 失败 <font color="#FF4D4F">{failed_count}</font>
**耗时**：{duration}
```

### 失败或取消

正文与成功卡片相同。失败标题使用红色，取消标题使用灰色；成功数量保持绿色，失败数量保持红色。

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
- 颜色只出现在标题、状态和成功/失败数字。
- 卡片只有一个“查看 Jenkins”按钮，URL 按环境规则降级。
- 单 Job 过程通知继续保持关闭。
