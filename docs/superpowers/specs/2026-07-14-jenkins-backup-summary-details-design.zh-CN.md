# Jenkins 备份汇总详情设计

## 目标

把现有 Markdown 汇总改为可交互的结构化汇总：支持多构建脚本悬浮/点击预览、复制、横竖滚动，并备份和查看 Job 参数、普通环境变量及 Jenkins Credentials 的实际值。

## 已确认范围

- 保留现有备份历史、新建备份和 ZIP 下载流程。
- 汇总表新增“环境/参数”和“构建脚本”列。
- 多个脚本以数量标签展示，悬浮打开预览，点击后可稳定操作。
- 脚本预览支持文件切换、复制当前脚本、横向和纵向滚动。
- 普通环境变量支持复制名称、值和完整 `KEY=value`。
- 构建参数展示类型、名称、默认值和描述。
- Credentials 展示绑定变量名、凭据 ID、类型和实际值，并支持复制。
- 按用户要求，第一版不对凭据值进行二次加密；后续可在结构化详情的读写边界加入加解密，不改变前端数据结构。
- 不引入 Markdown、代码编辑器或语法高亮依赖。

## 数据来源

每次备份继续下载 Job `config.xml`，并从中解析：

- `hudson.model.ParametersDefinitionProperty` 下的构建参数。
- EnvInject 等 XML 中的 Job 环境变量。
- Credentials Binding 中的变量名、凭据 ID 和绑定类型。
- NodeJS 等现有构建环境配置。
- Shell、Batch、Maven、Gradle 和 Pipeline 构建步骤。

Job XML 不包含 Credentials 的实际值。备份服务使用当前 Jenkins 管理账号调用受权限保护的 Jenkins 脚本接口，一次性读取被 Job 引用的凭据，并仅保留这些引用项。第一版支持常见的 Secret Text、用户名密码和 SSH 私钥；无法读取或不支持的类型保留凭据 ID，并记录 `value_unavailable`，不能导致整个备份失败。

## 备份结构

每个 Job 原有的 `config.xml`、`info.json`、脚本文件保持不变。ZIP 根目录新增 `details.json`，作为汇总页面唯一的结构化数据源：

```json
{
  "version": 1,
  "jobs": [
    {
      "name": "api-job",
      "status": "enabled",
      "git_urls": ["https://git.example/api.git"],
      "branches": ["main"],
      "parameters": [],
      "environment": [{"name": "APP_ENV", "value": "test", "source": "job"}],
      "credentials": [
        {
          "id": "Harbor",
          "type": "username_password",
          "bindings": {"username": "HARBOR_USER", "password": "HARBOR_PASSWORD"},
          "values": {"username": "robot", "password": "plain-text-for-now"}
        }
      ],
      "scripts": [
        {"filename": "build_step_1.sh", "type": "shell", "phase": "builders", "content": "#!/bin/bash\n..."}
      ]
    }
  ]
}
```

现有 `summary.md` 继续生成，供下载后离线查看和兼容旧页面，但不包含 Credentials 实际值。

## 接口与权限

- 新增结构化详情接口，按备份 ID 从 ZIP 的 `details.json` 读取数据。
- 结构化详情接口和包含明文凭据的 ZIP 下载接口仅允许 `admin`。
- 旧备份没有 `details.json` 时返回兼容状态，前端继续显示原 Markdown 汇总。
- 接口错误不回显凭据内容；日志不记录 `details.json`、环境变量值或凭据值。

明文 Credentials 属于用户明确接受的临时风险。最低安全边界仍保留管理员权限、禁止日志输出和禁止在浏览器持久化。

## 前端交互

汇总弹窗宽度使用视口，主体高度限制在约 `75vh`。表格外层使用原生 `overflow: auto`，同时提供横向和纵向滚动；表头固定，Job 名称列固定。

“环境/参数”单元格显示数量标签：`环境 3`、`参数 2`、`凭据 1`。悬浮显示可交互 Popover，点击标签保持打开。Popover 内分组展示普通环境、构建参数、构建工具和 Credentials；长值使用等宽文本和内部滚动区域。

“构建脚本”单元格显示 `Shell × 2` 等标签。Popover 顶部切换文件，主体使用 `<pre><code>`，设置 `white-space: pre` 与 `overflow: auto`，支持复制当前脚本。

所有复制操作使用浏览器原生 `navigator.clipboard.writeText`，成功或失败通过 Naive UI Message 提示。

前端只把详情保存在当前组件内存中；关闭汇总弹窗后清空，禁止写入 `localStorage`。

## 错误处理

- Jenkins 脚本接口无权限：备份配置和脚本仍成功，凭据标记为不可用。
- 单个凭据类型不支持：只影响该凭据，不影响其他 Job。
- ZIP 缺失或 `details.json` 损坏：详情接口返回明确错误，旧 Markdown 仍可查看。
- Clipboard API 不可用：提示复制失败，代码和变量值仍可手动选择。

## 测试

- 解析测试覆盖参数、EnvInject、Credentials Binding 和多个 Shell 步骤。
- 凭据读取测试覆盖常见类型、无权限和未知类型。
- 备份测试确认 `details.json` 写入 ZIP，且 `summary.md` 不出现凭据实际值。
- 接口测试覆盖管理员访问、非管理员拒绝和旧备份兼容。
- 前端最小测试覆盖详情数据归一化、复制文本生成和多脚本选择逻辑。
- 最终执行后端相关测试、完整后端测试、前端测试和生产构建。

## 暂不实现

- Credentials 二次加密、重新认证和自动隐藏。
- Groovy Pipeline `environment {}` 的语法解析；内嵌 Pipeline 原文仍随 Jenkinsfile 备份并可查看。
- 变量引用分析、未定义变量告警和备份差异对比。
- Monaco Editor、语法高亮和在线编辑。

