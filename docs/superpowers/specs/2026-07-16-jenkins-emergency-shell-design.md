# Jenkins 应急发布 Shell 设计

## 目标

Jenkins 不可用时，管理员从某次备份中选择一个视图和多个 Job，预览并下载一个 `emergency-release.sh`，由运维人员在 Linux 机器手动执行。

## 最小范围

- 复用备份 `details.json` 中的视图、Job、Git 地址、分支、参数、环境变量、凭据变量和脚本，并补充 Jenkins 全局工具安装路径。
- 在当前备份汇总中增加“生成应急脚本”。选择当前视图内多个 Job，按勾选顺序生成。
- 每个选中 Job 直接从 HTTPS GitLab 仓库获取最新分支列表并由运维手动选择；查询失败时允许手动输入。
- 页面展示完整脚本，支持复制和下载单个 `.sh` 文件。
- 不新增数据库表，不生成 ZIP，不通过 SSH 执行，不回传运行日志。
- 支持 Shell、Maven、Gradle；只有 Jenkins Pipeline DSL 或 Windows Batch 的 Job 不允许生成，并显示中文原因。

## 后端

增加管理员接口：

```text
POST /jenkins/servers/{server_id}/backups/{backup_id}/emergency-branches
{"jobs": [{"name": "api", "branch": "release/2026-07"}]}
```

接口从指定备份 ZIP 读取结构化详情，按请求顺序校验 Job，并返回：

```json
{
  "filename": "emergency-release.sh",
  "content": "#!/usr/bin/env bash\n...",
  "warnings": []
}
```

生成器只使用 Python 标准库。变量值使用 `shlex.quote`，脚本正文使用不会与内容冲突的带引号 heredoc 分隔符。不存在的 Job、没有 Git 地址、没有可执行脚本或包含不支持步骤时返回明确错误，不生成半成品。

备份服务通过现有 Jenkins 管理脚本接口同时读取全局 `ToolInstallation`，保存工具类型、Jenkins 配置名称和实际 `home`。第一版识别 JDK、Maven、NodeJS、Gradle 和 Git。Job 详情保存其明确选择的 JDK、Maven、NodeJS、Gradle 工具名称；生成器按名称精确匹配安装路径。Job 未指定某类工具时沿用系统 `PATH`，不擅自选择 Jenkins 中的其他版本。

备份同时保存 GitSCM 的 `credentialsId`，并把该凭据纳入现有管理员明文凭据导出。分支接口使用临时 `GIT_ASKPASS` 和 `git ls-remote --heads` 直接查询 HTTPS GitLab，解析并排序分支；不把用户名、密码或 Token 放入命令参数、响应错误或日志。临时文件权限为 `600`，请求结束立即删除。

## Shell 行为

- 使用 `#!/usr/bin/env bash`、`set -Eeuo pipefail`、`umask 077`。
- 根据 Job 选择的工具写入 `JAVA_HOME`、`MAVEN_HOME`、`NODEJS_HOME`、`GRADLE_HOME`，并把各自 `bin` 加入 `PATH`。
- 开始时检查工具目录以及 `git`、`java`、`mvn`、`node`、`npm`、`gradle` 等实际需要的命令；缺失时立即停止并显示 Jenkins 配置名称与期望路径。
- 每个 Job 在独立临时目录中使用临时 `GIT_ASKPASS` 拉取第一个 HTTPS Git 地址，并切换页面为该 Job 选择的分支。
- 普通环境变量、构建参数默认值和凭据绑定值只在该 Job 的子 Shell 中导出。
- 按备份中的脚本顺序执行，Job 之间串行；任一命令失败立即停止。
- 退出时清理临时工作目录。
- 脚本设计为在原 Jenkins Linux 主机运行，因此复用备份时记录的绝对工具路径；不复制 JDK、Maven、NodeJS 或 Gradle 安装目录。
- HTTPS 私有 GitLab 使用备份中的 SCM 用户名/密码或 Token；脚本内的认证辅助文件在退出时删除，不改写 Git URL。

生成的文件包含明文凭据。接口仅管理员可用，页面和脚本头部均显示风险提示；下载后应设置 `chmod 700`，使用完立即删除。

## 前端

- 使用现有横向视图标签决定可选 Job 范围。
- 勾选 Job 后分别加载 GitLab 最新分支下拉框；每个 Job 必须选择分支，加载失败时切换为手动输入。
- 点击“生成应急脚本”后显示紧凑的 Job 多选列表，默认不全选，避免误发布。
- 生成后打开脚本预览，提供“复制脚本”和“下载 .sh”。
- 切换备份、视图或关闭汇总时清空选择和脚本内容。

## 验证

- 后端测试覆盖 HTTPS GitLab 分支查询、SCM 凭据临时认证和脱敏、多 Job 与各自分支顺序、安全引用、环境/参数/凭据、全局工具路径采集与按 Job 名称匹配、Shell/Maven/Gradle、缺少 Job 和不支持类型。
- 前端 UI 测试覆盖响应归一化与下载文件名；生产构建必须通过。
- 只重建 `backend`、`web` 容器，并验证两个 HTTP 服务返回 200。

## 暂不实现

- Jenkinsfile DSL 转换、Windows Batch、并行发布、失败后继续。
- 工具自动下载、安装、复制，以及 Jenkins 构建节点与执行机器路径不一致的映射。
- 构建参数在线编辑、在线执行、SSH、运行日志和回滚。
- 凭据二次加密；沿用当前管理员明文备份边界。
