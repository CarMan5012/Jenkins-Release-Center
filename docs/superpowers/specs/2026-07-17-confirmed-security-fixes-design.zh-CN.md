# 已确认安全缺陷修复设计

## 目标

修复 2026-07-17 当前源码审查确认的安全与调度完整性问题，不改变现有 API 路径、角色模型或前端交互。

## 约束

- `docker-compose.yml` 与 `docker-compose.mysql.yml` 只用于本机开发。
- 生产仍默认要求四个 Secret 文件，缺失时拒绝启动。
- 不引入新依赖或新的安全框架。
- 所有行为修改先增加能够复现原问题的测试。

## 设计

### 本地 Compose

两个 Compose 的宿主端口只绑定 `127.0.0.1`。MySQL Compose 显式设置 `APP_ENV=development`，避免本地启动误入生产 Secret 校验。两个应用服务都把 `/tmp/jenkins-release-backups` 挂载为 `tmpfs`，并通过 `BACKUP_TMP_DIR` 指向该目录。

### Jenkins 请求边界

备份模块增加一个共享的同源请求入口：先比较目标 URL 与已配置 Jenkins 基准 URL 的规范化 origin，再执行请求；所有携带 Jenkins 凭据的请求都设置 `allow_redirects=False`。Jenkins 响应中的 Job、Folder 和 View URL 也必须经过该入口，防止 SSRF 和 Basic Auth 外送。

### 备份文件边界

备份工作目录和明文 ZIP 使用 `tempfile.mkdtemp()` 创建在 `BACKUP_TMP_DIR`。持久目录只写入加密后的 `.zip.enc`；失败路径统一清理临时目录和未完成的密文文件。

### 发布计划完整性

创建和更新计划在数据库写入或删除调度作业之前完成时区规范化及执行时间校验。更新计划在输入无效时不触碰旧任务或 APScheduler 作业。其余现有原子领取逻辑保持不变。

### 幂等与错误响应

幂等头去除首尾空白，空值视为未提供，原始值超过 128 字符返回 400，存储值为 `<user_id>:<key>`。发布计划与 Jenkins 备份复用同一规范化函数。全局 500 响应只返回固定消息，完整异常继续写服务端日志。

### 测试

新增安全边界测试，覆盖同源请求、禁止重定向、幂等键用户隔离、固定 500 响应、Compose 回环绑定与 tmpfs。发布 API 测试覆盖无效定时请求不写数据库、不移除旧调度。现有备份集成测试增加“持久目录不存在明文 ZIP”的断言。`conftest.py` 为测试进程显式设置 development，使标准 pytest 命令可运行。

## 非目标

- 不引入 KMS、Redis、多租户或新的调度事务框架。
- 不把本地 Compose 改造成生产部署模板。
- 不重构与上述缺陷无关的前后端代码。
