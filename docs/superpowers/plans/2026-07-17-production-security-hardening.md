# Production Security Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Secure production defaults, encrypted Jenkins backups, server-side release validation, SSRF controls, and replay protection while preserving explicit local development mode and internal HTTP access.

**Architecture:** `APP_ENV` defaults to production and resolves secrets from Docker Secret files; development is explicitly enabled by `APP_ENV=development`. Existing FastAPI routes keep their shape where possible, but sensitive paths, Jenkins origins, resource relationships, and state transitions are validated server-side. Jenkins backup ZIP files are authenticated-encrypted with AES-GCM before persistent storage.

**Tech Stack:** Python 3.12, FastAPI, Pydantic Settings, SQLAlchemy, cryptography AESGCM, Vue 3, Docker Compose.

**Execution constraint:** The user explicitly requested code and Compose changes without starting services or running normality checks. Verification commands are documented but must not be executed in this session.

---

### Task 1: Production-default configuration and secret files

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/core/security.py`
- Modify: `backend/app/services/init_db.py`
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `docker-compose.mysql.yml`

- [ ] **Step 1: Add production-default environment fields**

Define `APP_ENV="production"`, `ALLOW_INSECURE_HTTP=True`, the four `/run/secrets/*` file paths, an eight-hour production token lifetime, trusted proxy IPs, and allowed Jenkins origins. Resolve required secrets from files in production and retain current development fallbacks only when `APP_ENV=development`.

- [ ] **Step 2: Make local Compose explicit**

Add `APP_ENV=development` to both existing Compose services because these files are currently used for local testing. Keep production secret file paths as application defaults rather than committing secret values.

- [ ] **Step 3: Read the initial admin password from resolved settings**

Keep bcrypt hashing and ensure an existing admin is never reset on restart.

- [ ] **Step 4: Document but do not run configuration checks**

```powershell
$env:APP_ENV='development'
backend/venv/Scripts/python.exe -c "from app.core.config import settings; assert settings.APP_ENV == 'development'"
```

Expected: exit 0. Production without Secret files should fail during settings creation.

### Task 2: Static path containment and Docker build boundary

**Files:**
- Modify: `backend/app/main.py`
- Create: `.dockerignore`
- Test: `backend/tests/test_security_boundaries.py`

- [ ] **Step 1: Add encoded traversal regression cases**

Cover `../`, `%2e%2e`, absolute paths, and a valid asset path against a small path resolver function.

- [ ] **Step 2: Resolve requested assets inside the static root**

Use `Path.resolve()` and `candidate.relative_to(dist_root)` before `FileResponse`; return 404 for any candidate outside the root.

- [ ] **Step 3: Exclude runtime data from Docker context**

Exclude `backend/data`, `backend/venv`, caches, `.env`, keys, databases, ZIP files, encrypted backups, and `sqlite_key`.

- [ ] **Step 4: Document but do not run the regression test**

```powershell
$env:APP_ENV='development'
backend/venv/Scripts/python.exe -m pytest backend/tests/test_security_boundaries.py -q
```

Expected: traversal cases return no candidate and valid assets resolve below the static root.

### Task 3: AES-GCM encrypted backup archives

**Files:**
- Create: `backend/app/core/backup_crypto.py`
- Create: `backend/scripts/decrypt_backup.py`
- Modify: `backend/app/services/jenkins_backup_service.py`
- Modify: `backend/app/api/jenkins.py`
- Modify: `backend/app/schemas/jenkins.py`
- Modify: `docker-compose.yml`
- Modify: `docker-compose.mysql.yml`
- Test: `backend/tests/test_backup_crypto.py`

- [ ] **Step 1: Implement the versioned encrypted-file format**

Use `MAGIC=b"JRCB1"`, a random 12-byte nonce, a SHA-256-derived 32-byte AES key, and `AESGCM.encrypt(nonce, plaintext, MAGIC)`. Persist `MAGIC + nonce + ciphertext_and_tag`.

- [ ] **Step 2: Encrypt before persistent storage**

Create the ZIP inside the temporary backup directory, encrypt it to `backup_<id>.zip.enc` in the persistent directory, then remove the temporary directory in `finally`.

- [ ] **Step 3: Read encrypted backup details in memory**

`read_backup_details` must decrypt to bytes and open `ZipFile(io.BytesIO(...))`. Development may show details over HTTP; production requires a request recognized as HTTPS through a trusted proxy.

- [ ] **Step 4: Return encrypted downloads**

Download `.zip.enc` with `application/octet-stream`. Keep admin authorization.

- [ ] **Step 5: Add an offline decrypt command**

The command accepts input path, output ZIP path, and `--key-file`; authentication failure exits non-zero without writing output.

- [ ] **Step 6: Put plaintext temporary backups on tmpfs**

Mount `/tmp/jenkins-release-backups` as Compose `tmpfs` and set `BACKUP_TMP_DIR` accordingly.

### Task 4: Release target integrity and Jenkins origin safety

**Files:**
- Create: `backend/app/core/url_security.py`
- Modify: `backend/app/schemas/release.py`
- Modify: `backend/app/api/release.py`
- Modify: `backend/app/api/jenkins.py`
- Modify: `backend/app/services/jenkins_client.py`
- Modify: `backend/app/services/notification.py`
- Test: `backend/tests/test_security_boundaries.py`

- [ ] **Step 1: Validate Jenkins origins**

Allow only HTTP(S), reject userinfo/fragments, require an exact configured origin in production, and compare normalized origins when URLs change.

- [ ] **Step 2: Prevent old-token forwarding**

When a server origin changes, require a non-empty new API token. Raise 400 instead of retaining the previous encrypted token.

- [ ] **Step 3: Resolve release jobs server-side**

Require `job_id`, query `JenkinsJob.id == job_id` and `JenkinsJob.server_id == server_id`, reject disabled or missing servers, reject `.`/`..` job segments, and store the database job name instead of trusting the request name.

- [ ] **Step 4: Constrain request models**

Use Pydantic `Literal` and `Field` constraints for plan type, failure strategy, strings, task count, sequence, interval, and parameter count.

- [ ] **Step 5: Stop cross-origin redirects**

Reject queue URLs whose normalized origin differs from the configured Jenkins origin. Disable redirects on credential-bearing requests and webhook posts.

### Task 5: Idempotency and atomic task claiming

**Files:**
- Modify: `backend/app/models/release.py`
- Modify: `backend/app/models/jenkins.py`
- Modify: `backend/app/services/init_db.py`
- Modify: `backend/app/api/release.py`
- Modify: `backend/app/api/jenkins.py`
- Modify: `backend/app/services/release_service.py`
- Test: `backend/tests/test_security_boundaries.py`

- [ ] **Step 1: Add nullable unique request keys**

Add `request_key` to `ReleasePlan` and `JenkinsBackup`; extend the existing idempotent schema upgrade code to add columns and unique indexes for existing SQLite/MySQL databases.

- [ ] **Step 2: Accept `Idempotency-Key`**

Normalize to `"<user-id>:<header-value>"`, reject values over 128 characters, and return the existing resource when the stored key already exists.

- [ ] **Step 3: Atomically claim plans**

Use a conditional SQLAlchemy `update` from `WAITING` to `RUNNING`; only the request updating one row may enqueue background work.

- [ ] **Step 4: Atomically claim tasks**

Replace the read-then-write status check with a conditional update from `WAITING` to `RUNNING` before calling Jenkins.

### Task 6: Proxy-aware disclosure controls and error hardening

**Files:**
- Modify: `backend/app/core/url_security.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/api/auth.py`
- Modify: `backend/app/api/system.py`
- Modify: `backend/app/api/history.py`
- Modify: `backend/app/core/exceptions.py`
- Modify: `backend/app/schemas/release.py`

- [ ] **Step 1: Recognize HTTPS only through trusted proxies**

Treat `request.url.scheme == "https"` as secure. Accept `X-Forwarded-Proto=https` only when `request.client.host` is in `TRUSTED_PROXY_IPS`.

- [ ] **Step 2: Add conditional security headers**

Always add `X-Content-Type-Options`, `Referrer-Policy`, and frame protection; add HSTS only for recognized HTTPS requests.

- [ ] **Step 3: Add minimal single-process login throttling**

Track failures per username and client address, block after five failures for fifteen minutes, clear on successful login, and skip the limiter in explicit development mode.

- [ ] **Step 4: Reduce sensitive responses**

Make system configuration listing admin-only, constrain pagination to 1-100, and use a history-list response model without `logs` or `raw_response`.

- [ ] **Step 5: Stop exception detail disclosure**

Return a fixed internal-error message while preserving full server-side logging.

### Task 7: Frontend encrypted-backup compatibility and documentation

**Files:**
- Modify: `frontend/src/views/jenkins/BackupDrawer.vue`
- Modify: `.env.example`
- Modify: `docs/superpowers/specs/2026-07-17-production-security-hardening-design.zh-CN.md` only if implementation reveals a necessary correction

- [ ] **Step 1: Download the encrypted filename**

Use the response `Content-Disposition` filename when present and default to `.zip.enc`.

- [ ] **Step 2: Explain direct-HTTP behavior**

If production details return 403, show that encrypted download remains available and credentials must be decrypted offline.

- [ ] **Step 3: Document local mode**

Set `.env.example` to `APP_ENV=development` and document that omitted `APP_ENV` means production.

- [ ] **Step 4: Do not execute verification in this session**

The normal completion commands would be backend pytest, frontend UI tests/build, Docker Compose config, and image-content inspection. They are intentionally skipped because the user explicitly requested no normality checks.
