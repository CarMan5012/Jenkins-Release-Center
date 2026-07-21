# Confirmed Security Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the confirmed Compose exposure, Jenkins SSRF, plaintext backup, schedule integrity, idempotency isolation, exception disclosure, and test startup defects.

**Architecture:** Keep the existing modules and add only two shared guards: a same-origin Jenkins request wrapper in the backup service and an idempotency-key normalizer in dependency helpers. Reorder schedule validation before side effects and move plaintext backup artifacts to tmpfs-backed temporary storage.

**Tech Stack:** Python 3.14 local runtime, FastAPI, SQLAlchemy, requests, APScheduler, pytest, Docker Compose, Vue 3/TypeScript.

---

### Task 1: Test bootstrap and security boundary regressions

**Files:**
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_security_boundaries.py`

- [ ] Set `APP_ENV=development` with `os.environ.setdefault` in `conftest.py` before test module imports.
- [ ] Add failing tests asserting: cross-origin Jenkins URLs raise `ValueError`; same-origin requests set `allow_redirects=False`; idempotency keys are scoped as `<user_id>:<key>` and reject values over 128 characters; global 500 responses omit exception text; both Compose files bind service ports to `127.0.0.1`, select local development, and configure backup tmpfs.
- [ ] Run `backend/venv/Scripts/python.exe -m pytest tests/test_security_boundaries.py -q -p no:cacheprovider`; expect failures because the helpers and fixed response do not exist yet.

### Task 2: Shared request and idempotency guards

**Files:**
- Modify: `backend/app/services/deps_helper.py`
- Modify: `backend/app/services/jenkins_backup_service.py`
- Modify: `backend/app/api/release.py`
- Modify: `backend/app/api/jenkins.py`
- Modify: `backend/app/core/exceptions.py`

- [ ] Add `normalize_idempotency_key(raw_key, user_id)` returning `None` for blank input, raising `ValueError` over 128 characters, otherwise returning `f"{user_id}:{key}"`.
- [ ] Use the normalized key for both lookup and persistence in release-plan and backup creation; translate validation failures to HTTP 400.
- [ ] Add `jenkins_request(session, method, url, base_url, **kwargs)` which compares normalized scheme/host/effective port and calls `session.request(..., allow_redirects=False)` only when origins match.
- [ ] Route every credential-bearing backup GET/POST through the wrapper, including Job/Folder/View URLs returned by Jenkins.
- [ ] Change the global exception body to the fixed message `Internal Server Error` while preserving `logger.exception`.
- [ ] Run the focused security test; expect all Task 1 behavior tests to pass.

### Task 3: Schedule side-effect ordering

**Files:**
- Create: `backend/tests/test_release_api_safety.py`
- Modify: `backend/app/api/release.py`

- [ ] Add a failing create test where a scheduled plan without a future time receives HTTP 400 and a DB sentinel proves no DB method was called.
- [ ] Add a failing update test where an invalid time receives HTTP 400 and a scheduler spy proves the existing job was not removed.
- [ ] Add `normalize_and_validate_execute_time(plan_type, execute_time)` and call it before `validate_release_plan_input`, database writes, task deletion, or scheduler removal in both endpoints.
- [ ] Remove the later duplicate time checks.
- [ ] Run `backend/venv/Scripts/python.exe -m pytest tests/test_release_api_safety.py -q -p no:cacheprovider`; expect pass.

### Task 4: Tmpfs-only plaintext backups

**Files:**
- Modify: `backend/tests/test_jenkins_backup.py`
- Modify: `backend/app/services/jenkins_backup_service.py`
- Modify: `docker-compose.yml`
- Modify: `docker-compose.mysql.yml`

- [ ] Extend the backup integration test to set `BACKUP_TMP_DIR`, assert the final path ends in `.zip.enc`, and assert no plaintext `.zip` is present in the persistent backup directory.
- [ ] Run the focused test and confirm it fails because the current ZIP is created under `backend/data/backups`.
- [ ] Create the work directory with `tempfile.mkdtemp(dir=BACKUP_TMP_DIR)`, create the plaintext ZIP inside it, write only encrypted bytes under the persistent backup directory, and always remove the temporary directory.
- [ ] Bind application/MySQL ports to loopback, set MySQL app `APP_ENV=development`, and configure `BACKUP_TMP_DIR` plus tmpfs in both Compose files.
- [ ] Run the focused backup and Compose boundary tests; expect pass.

### Task 5: Full verification and review

**Files:**
- Review all modified files above.

- [ ] Run backend full suite: `backend/venv/Scripts/python.exe -m pytest -q -p no:cacheprovider` from `backend`; expect zero failures.
- [ ] Run frontend UI tests: `npm.cmd run test:ui`; expect exit 0.
- [ ] Run frontend typecheck: `node_modules/.bin/vue-tsc.cmd --noEmit -p tsconfig.app.json --pretty false`; expect exit 0.
- [ ] Run frontend build: `npm.cmd run build`; expect exit 0.
- [ ] Run `docker compose config` for both Compose files if Docker is available; otherwise report the unavailable verifier.
- [ ] Run the repository code-review workflow against the current snapshot because Git metadata is unavailable.
- [ ] Git commit steps are skipped only because this workspace is not a Git repository.
