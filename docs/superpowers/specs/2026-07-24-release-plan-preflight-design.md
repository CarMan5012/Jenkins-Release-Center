# 发布计划预检设计

## 目标

发布计划创建或编辑后立即执行一次只读预检，并允许操作员随时手动重新检查，以便在触发 Jenkins 构建前发现确定性配置错误。

## 非目标

- 不触发测试构建或写入 Jenkins
- 不在实际运行前自动联网重检
- 不保存检查历史，只保存最近一次结果
- 不新增定时巡检、通知渠道或独立健康检查页面

## 用户流程

1. 创建或编辑发布计划。
2. 系统保存计划后同步执行一次预检；预检失败不回滚或删除计划。
3. 列表和详情页显示最近状态与检查时间。
4. 操作员可点击“发布前检查”覆盖最近结果。
5. `PASSED` 可运行，`WARNING` 可确认后运行，`FAILED` 和 `UNCHECKED` 不可运行。

由于运行前不会自动联网重检，界面必须展示 `preflight_checked_at`，让操作员识别结果是否陈旧。

## 状态模型

- `UNCHECKED`：尚未检查；禁止运行。
- `PASSED`：所有检查通过；允许运行。
- `WARNING`：只有临时性问题；允许运行，手动运行时要求确认。
- `FAILED`：存在确定性错误；禁止运行。

计划编辑后先重置为 `UNCHECKED`，保存完成后再自动检查。

## 数据模型

在 `release_plan` 保存最近一次结果：

- `preflight_status`：字符串，默认 `UNCHECKED`
- `preflight_checked_at`：可空时间
- `preflight_result`：可空 JSON，保存摘要与逐任务明细

现有计划迁移后统一为 `UNCHECKED`。不创建预检历史表。

`preflight_result` 的最小结构：

```json
{
  "summary": "2 个任务通过",
  "tasks": [
    {
      "task_id": 5,
      "job_name": "前端-测试",
      "status": "PASSED",
      "checks": [
        {"code": "jenkins_connection", "status": "PASSED", "message": "连接正常"}
      ]
    }
  ]
}
```

结果不得包含用户名、令牌、响应正文或其他敏感信息。

## 预检逻辑

新增一个共享的发布计划预检函数。创建、编辑和手动检查均调用它，避免三套规则漂移。

每个任务执行以下只读检查：

1. Jenkins 实例存在且启用。
2. Jenkins 根 API 可访问，凭据有效。
3. Job 仍然存在，并属于任务绑定的 Jenkins 实例。
4. 计划参数键与 Jenkins 当前参数定义一致。
5. 配置 URL、Jenkins 根 API 返回的 URL，以及不跟随跳转的规范路径重定向 URL 具有相同 Origin。

Origin 使用标准 URL 解析后的协议、主机和有效端口比较。所有请求继续使用配置的 Jenkins Origin，且不跟随跨 Origin 重定向。

### 结果分类

以下情况为 `FAILED`：

- Jenkins 实例不存在或已禁用
- 凭据被拒绝（HTTP 401/403）
- Job 不存在或归属错误
- 参数键不匹配
- URL 或重定向 Origin 不一致
- 返回内容无法满足确定性的接口契约

以下情况为 `WARNING`：

- 连接超时
- DNS 或临时网络错误
- Jenkins HTTP 5xx

只要任一任务 `FAILED`，计划为 `FAILED`；否则任一任务 `WARNING`，计划为 `WARNING`；全部通过则为 `PASSED`。

## API 与执行门禁

新增：

```text
POST /api/v1/release/plans/{plan_id}/preflight
```

接口执行检查、覆盖最近结果并返回更新后的预检状态和明细。仅现有发布操作员权限可调用。

创建和编辑接口在计划保存完成后同步调用同一预检函数，因此响应中可直接包含最终预检结果，无需前端轮询。网络失败只产生 `WARNING`，不会把已保存计划变成创建失败。

所有运行入口共用后端门禁：

- `PASSED`：继续
- `WARNING`：继续；前端手动运行前弹确认框
- `FAILED` 或 `UNCHECKED`：返回 400，不创建 Jenkins 队列项

定时任务触发时只读取已保存状态，不联网重检。`FAILED` 或 `UNCHECKED` 时不调用 Jenkins，计划与任务记录明确的预检阻止原因；`WARNING` 继续执行。

## 前端

发布计划列表新增“预检状态”列，并显示最近检查时间。列表和详情页均提供“发布前检查”按钮。

状态展示：

- `PASSED`：绿色“通过”
- `WARNING`：黄色“警告”
- `FAILED`：红色“未通过”
- `UNCHECKED`：灰色“未检查”

创建或编辑后的自动检查为 `FAILED` 时，立即展开错误明细。手动检查显示加载状态，并在完成后刷新计划数据。详情以任务为单位列出检查项和短错误信息。

运行按钮对 `FAILED` 和 `UNCHECKED` 禁用。`WARNING` 点击运行时显示确认框；后端门禁仍是最终权限边界。

## 错误处理与安全

- 单个任务异常被收集为该任务结果，不中断其他任务检查。
- 数据库只在汇总完成后覆盖最近结果，避免保存半份明细。
- 预检请求不触发构建、不修改 Jenkins，也不跟随跨 Origin 重定向。
- 日志记录计划、任务和检查代码，不记录凭据或敏感响应正文。

## 验证

后端最小覆盖：

- 全部通过得到 `PASSED`
- 网络超时和 HTTP 5xx 得到 `WARNING`
- 实例禁用、401/403、Job 缺失、参数错误和 Origin 不一致得到 `FAILED`
- 多任务按 `FAILED > WARNING > PASSED` 汇总
- 最近结果和检查时间被覆盖保存
- `FAILED/UNCHECKED` 阻止手动与定时执行，且没有 Jenkins 构建请求
- `WARNING` 允许执行
- 编辑计划重置并重新检查

前端最小覆盖：

- 四种状态正确展示
- 手动检查刷新结果
- `FAILED/UNCHECKED` 禁用运行
- `WARNING` 运行前确认

