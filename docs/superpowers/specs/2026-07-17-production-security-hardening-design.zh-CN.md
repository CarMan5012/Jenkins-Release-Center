# 生产环境安全加固设计

## 状态

- 日期：2026-07-17
- 状态：已完成方案讨论，等待用户评审本文档
- 范围：FastAPI 后端、Vue 登录流程、Jenkins 发布与备份、Docker 部署

## 背景

系统部署在公司内网，既要支持直接 HTTP 访问，也要支持经可信反向代理终止 TLS 后的 HTTPS 访问。本地开发可以使用便捷默认值，但生产必须默认进入生产模式，并在关键密钥缺失或仍为弱默认值时拒绝启动。

当前需要修复的主要风险包括：SPA 静态文件路径穿越、默认 JWT/AES/管理员口令、敏感文件进入 Docker 镜像、Jenkins 凭据明文备份、发布参数篡改、Jenkins URL 导致 SSRF 或旧 Token 外送，以及并发重放造成重复构建。

## 目标

- 默认按生产环境启动；本地测试必须显式配置 `APP_ENV=development`。
- 兼容可信代理 HTTPS 和公司内网直接 HTTP。
- 保留 Jenkins 账号、密码、Token 和 SSH 私钥的灾难恢复能力，但生产磁盘只保存加密备份。
- 服务端不信任客户端提交的 `job_name`、资源归属关系或状态。
- 同一个发布、触发或备份请求不会产生重复副作用。
- Docker 镜像、构建上下文和日志中不包含数据库、私钥、备份或运行时密钥。

## 非目标

- 本次不引入多租户或按团队隔离；现有 admin/operator 全局资源模型保持不变。
- 不实现 Token IP 绑定或每个 API 请求的自定义签名协议。
- 不强制生产必须具备公网证书。
- 不引入 Redis、外部 KMS 或新的安全框架；优先复用现有 `cryptography`、数据库约束和反向代理能力。

## 环境与启动规则

### 默认生产配置

应用默认值为：

```env
APP_ENV=production
ALLOW_INSECURE_HTTP=true
SECRET_KEY_FILE=/run/secrets/jwt_key
AES_SECRET_KEY_FILE=/run/secrets/aes_key
BACKUP_ENCRYPTION_KEY_FILE=/run/secrets/backup_key
INITIAL_ADMIN_PASSWORD_FILE=/run/secrets/admin_password
```

`APP_ENV` 未配置时必须视为 `production`。生产环境允许公司内网 HTTP，因此 `ALLOW_INSECURE_HTTP` 默认是 `true`；网络边界由防火墙、VLAN 或 VPN 限制，应用不把 HTTP 描述为等同于 HTTPS。

生产启动时必须满足：

- JWT、Jenkins Token 加密、备份加密和首次管理员口令对应的 Secret 文件存在、非空且不是仓库中的旧默认值。
- `INITIAL_ADMIN_PASSWORD_FILE` 只在首次创建管理员时读取；已有管理员不会在重启时被重置。
- 任一必要 Secret 缺失时拒绝启动，而不是仅记录警告。
- 不接受生产环境通过源码默认值兜底。

### 本地开发配置

本地测试必须显式配置：

```env
APP_ENV=development
```

开发环境可以使用环境变量或开发默认值，可以允许 RSA 解密失败后回退明文，并打印清晰的非生产警告。开发便利不能放宽路径边界、资源归属校验、SSRF 防护或幂等约束。

## HTTP 与可信代理 HTTPS

应用容器始终监听 HTTP。没有代理时，公司内网客户端直接访问 HTTP；有代理时，由代理负责证书和 TLS 终止。

- 只有 `TRUSTED_PROXY_IPS` 中的来源可以提供 `X-Forwarded-Proto`、`X-Forwarded-For` 等代理头。
- 来自非可信地址的转发头全部忽略，防止客户端伪造 HTTPS 状态或审计 IP。
- 当可信代理报告 `https` 时返回适用的安全响应头；直接 HTTP 不返回 HSTS，避免锁死内网访问。
- 生产访问 Token 默认有效期调整为 8 小时；开发环境可保留 7 天。
- 直接 HTTP 无法阻止同网段攻击者窃取并重放 Bearer Token，该风险由可信内网边界接受并记录。

登录密码数据库存储继续使用 bcrypt。前端 RSA 仅作为直接 HTTP 下的被动抓包缓解措施，不作为 TLS 替代品；生产环境 RSA 解密失败必须返回 400，不得回退到明文。RSA 私钥只在运行时数据卷生成或加载，权限为仅应用用户可读，不能进入镜像。

## 静态文件路径边界

SPA 兜底路由在返回物理文件前必须：

1. 对静态根目录和请求目标执行规范化解析。
2. 验证目标路径仍位于静态根目录内。
3. 拒绝绝对路径、`..`、编码后目录穿越和指向目录外的符号链接。
4. 不符合条件时返回 404，不回显服务器文件路径。

API 404 和 SPA 的 `index.html` 回退行为保持不变。

## Docker 构建边界

新增 `.dockerignore`，排除：

```gitignore
backend/data/
backend/venv/
**/__pycache__/
*.pyc
*.db
*.pem
*.key
*.zip
*.enc
.env
sqlite_key
```

镜像只复制后端源码、生产依赖清单和前端构建产物。已有登录 RSA 私钥、JWT/AES 密钥以及可能进入过镜像或镜像仓库的 Jenkins 凭据需要轮换。

## Jenkins 备份加密

生产仍然备份账号、密码、Token、SSH 私钥和口令，但最终持久化文件必须是加密包。

### 文件格式

- 先在 `tmpfs` 临时目录生成现有 ZIP 内容。
- 使用现有 `cryptography` 的 AES-256-GCM 对整个 ZIP 进行认证加密。
- 备份密钥由 `BACKUP_ENCRYPTION_KEY_FILE` 提供，独立于 JWT、数据库和 Jenkins Token 加密密钥。
- 密文格式为：固定版本标识 `JRCB1`、12 字节随机 nonce、AES-GCM 密文与认证标签。
- 最终仅保留 `backup_<id>.zip.enc`；数据库 `zip_path` 指向该密文文件。
- 加密成功或失败后都清理明文 ZIP 和临时目录。

Compose 为备份临时目录配置 `tmpfs`。持久化备份目录只允许应用用户读写。

### 查看与恢复

- 摘要继续存储不含明文凭据的结构化信息。
- 生产直接 HTTP 只允许下载 `.zip.enc`，不通过 API 返回解密后的凭据。
- 经可信 HTTPS 代理时，可以保留 admin-only 的详情查看；服务端只在内存中解密，并记录查看审计日志。
- 提供一个最小离线解密命令，接收 `.zip.enc` 和独立密钥文件，验证 GCM 标签后输出 ZIP。
- 密钥错误、文件被篡改或版本不支持时失败，不输出部分明文。

## 发布参数完整性

客户端以 `server_id` 和 `job_id` 标识目标。`job_name` 可为旧客户端兼容而暂时保留，但不能用于实际 Jenkins 调用。

后端必须：

- 通过 `job_id + server_id` 查询 Jenkins Job。
- 拒绝不存在、跨 Server、Server 已禁用或 Job 名称不一致的请求。
- 从数据库记录读取真实 `job_name`。
- 验证 `type`、失败策略、序号、依赖关系、任务数量、字符串长度和参数数量。
- 复用 Jenkins 参数定义查询，拒绝未知参数键；参数值仍允许由业务输入。
- Job 路径片段拒绝 `.`、`..` 和空片段。

这些校验在开发和生产环境一致启用。

## Jenkins URL 与 SSRF

- Jenkins URL 只允许 `http` 和 `https`，禁止 URL userinfo、片段和非预期端口。
- 生产 Jenkins origin 必须命中 `JENKINS_ALLOWED_ORIGINS`；允许配置公司内网地址，因此不使用“一律禁止私网 IP”的规则。
- 修改既有 Jenkins origin 时必须同时重新提交 Token；旧 Token 不得发送给新 origin。
- 连接测试权限提升为 operator/admin。
- Jenkins 请求不得自动跨 origin 重定向；队列 URL 必须与配置的 Jenkins origin 一致。
- Webhook 请求禁止自动重定向，并校验所有 DNS 解析地址，而非只校验第一个 IPv4 地址。

## 幂等与并发

创建发布计划和 Jenkins 备份支持 `Idempotency-Key`。在对应资源表增加可空唯一请求键；同一用户、同一接口重复提交相同键时返回首次创建的资源，不再次产生副作用。

计划触发和任务执行使用数据库原子状态领取：

```sql
UPDATE release_plan
SET status = 'RUNNING'
WHERE id = :id AND status = 'WAITING';
```

只有更新一行的请求可以创建后台任务，其余返回 409。`ReleaseTask` 使用相同方式从 `WAITING` 原子切换为 `RUNNING`，避免两个线程同时触发 Jenkins 构建。

## 信息暴露与滥用控制

- 登录按用户名和来源地址限制失败次数；单进程部署先使用进程内限流，重启会清空计数。若以后增加多副本，再迁移到共享存储。
- 系统配置列表改为 admin-only，并对敏感值掩码。
- 历史列表响应不包含完整日志和原始响应；日志只由专用详情接口分页读取。
- 所有分页 `limit` 限制在 1 到 100。
- 全局 500 响应只返回固定消息，完整异常仅写服务端日志。
- 备份创建、下载、HTTPS 在线查看、同步、计划触发和拒绝的幂等冲突写入审计日志。

## 错误处理

- 路径越界、origin 不允许和参数归属不一致返回 400 或 404，不包含内部绝对路径。
- 重复或已经领取的危险操作返回 409。
- Secret 缺失、默认弱密钥或备份密钥无效属于启动错误。
- 备份加密失败时记录状态 `FAILED`，删除全部明文临时文件，不生成可下载记录。
- 解密认证失败统一报告“备份密钥错误或文件已损坏”，不区分具体密码学错误。

## 验证

至少覆盖以下自动化检查：

- `/%2e%2e/...`、双重编码和符号链接路径不能读取静态目录外文件。
- 未配置 `APP_ENV` 时按生产运行；生产缺 Secret 时启动失败；显式 development 可使用开发默认值。
- Docker 构建上下文和最终镜像不包含数据库、私钥、备份或本地虚拟环境。
- 备份持久化目录只出现 `.zip.enc`，密文中搜索不到测试密码；正确密钥可恢复，错误密钥和篡改文件失败。
- 生产直接 HTTP 不能在线查看明文凭据，但可以下载密文；可信 HTTPS 代理可由管理员查看并产生审计记录。
- 修改 `server_id`、`job_id`、`job_name`、未知参数或 `..` Job 路径均被拒绝。
- 修改 Jenkins origin 且不重新提交 Token 被拒绝；跨 origin 重定向不携带 Token。
- 同一幂等键并发提交十次只创建一个计划或备份。
- 同一计划并发触发十次只调用一次 Jenkins。
- 前端 UI 测试、后端测试、生产镜像构建和离线备份恢复测试全部通过。

## 实施顺序

1. 修复路径穿越并加入 `.dockerignore`，切断未认证敏感文件读取链。
2. 实现生产默认环境和 Secret 文件加载，移除生产弱默认值。
3. 加密 Jenkins 备份并提供离线解密命令。
4. 增加发布资源关联校验和 Jenkins origin 防护。
5. 增加幂等键和原子状态领取。
6. 收紧信息暴露、限流、代理头和审计。
7. 完成测试、轮换历史密钥和 Jenkins 凭据，重建生产镜像。

## 已知剩余风险

- 公司内网直接 HTTP 下，Bearer Token 仍可被具备同网段抓包能力的攻击者重放；应用层无法完全消除此风险。
- 进程内登录限流在重启或未来多副本部署时不会共享状态。
- 经可信 HTTPS 代理在线查看凭据会让明文短暂存在于管理员浏览器内存和响应中，因此必须限制为管理员并审计。
