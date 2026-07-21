# Jenkins Backup Summary Details Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an interactive Jenkins backup summary with complete script previews, copy actions, two-axis scrolling, job environment/parameter data, and plaintext Jenkins credential values as explicitly requested.

**Architecture:** Extend the existing XML parser to produce JSON-safe job details and write one versioned `details.json` into each backup ZIP. Fetch only credential IDs referenced by backed-up jobs through Jenkins' authenticated script endpoint, expose the structured details through an admin-only API, and render them with native Vue/Naive UI components; retain the current Markdown endpoint for old backups.

**Tech Stack:** Python 3.14, FastAPI, ElementTree, zipfile, pytest, Vue 3, TypeScript, Naive UI, Node assert.

---

## File map

- Modify `backend/app/services/jenkins_backup_service.py`: parse parameters, environment and credential bindings; fetch referenced credential values; build and package `details.json`.
- Modify `backend/app/api/jenkins.py`: return structured details from ZIP and restrict plaintext-bearing endpoints to admins.
- Modify `backend/tests/test_jenkins_backup.py`: parser, credential export and archive regression coverage.
- Modify `backend/tests/test_jenkins.py`: details endpoint authorization and compatibility coverage.
- Create `frontend/src/utils/jenkins-backup.ts`: small pure helpers for normalizing details and formatting copy values.
- Create `frontend/scripts/jenkins-backup-ui.test.mjs`: framework-free tests matching the existing frontend test style.
- Modify `frontend/package.json`: add the Jenkins UI helper test to the existing UI test command.
- Modify `frontend/src/views/jenkins/BackupDrawer.vue`: structured table, popovers, copy operations and native scrolling.

### Task 1: Parse job parameters, environment and credential bindings

**Files:**
- Modify: `backend/tests/test_jenkins_backup.py`
- Modify: `backend/app/services/jenkins_backup_service.py`

- [ ] **Step 1: Write failing parser tests**

Add tests which instantiate `JenkinsConfigParser` with XML containing `ParametersDefinitionProperty`, EnvInject `propertiesContent`, `SecretBuildWrapper`, NodeJS, and two Shell builders. Assert this stable shape:

```python
parsed = JenkinsConfigParser(xml).parse()
assert parsed["parameters"] == [{
    "name": "BRANCH",
    "type": "StringParameterDefinition",
    "default_value": "main",
    "description": "Git branch",
}]
assert parsed["environment"]["variables"] == [
    {"name": "APP_ENV", "value": "test", "source": "envinject"}
]
assert parsed["environment"]["tools"]["nodejs"] == "node-v14.17.5"
assert parsed["credentials"] == [{
    "id": "Harbor",
    "type": "StringBinding",
    "bindings": {"value": "HARBOR_PASSWORD"},
}]
assert [step["script"] for step in parsed["build_steps"]] == ["echo one", "echo two"]
```

- [ ] **Step 2: Run the parser test and verify RED**

Run: `backend\venv\Scripts\python.exe -m pytest backend\tests\test_jenkins_backup.py -k parser -v`

Expected: failure because `parameters`, normalized environment variables, and `credentials` do not exist.

- [ ] **Step 3: Implement minimal XML parsing**

Add `_parse_parameters()` and `_parse_credentials()` and change `parse()` to include their results. Keep `_parse_environment()` but return:

```python
{
    "variables": [{"name": name, "value": value, "source": "envinject"}],
    "tools": {"nodejs": "node-v14.17.5"},
}
```

Parse EnvInject `propertiesContent` with `splitlines()`, ignoring blank/comment lines and splitting only on the first `=` so values may contain `=`. Parse binding tags without guessing values: `variable` maps to `bindings.value`, while `usernameVariable` and `passwordVariable` retain their distinct keys.

- [ ] **Step 4: Run the parser test and verify GREEN**

Run: `backend\venv\Scripts\python.exe -m pytest backend\tests\test_jenkins_backup.py -k parser -v`

Expected: all selected tests pass.

- [ ] **Step 5: Commit the parser slice**

```powershell
git add backend/app/services/jenkins_backup_service.py backend/tests/test_jenkins_backup.py
git commit -m "feat: parse Jenkins backup environment details"
```

### Task 2: Fetch referenced Jenkins credential values

**Files:**
- Modify: `backend/tests/test_jenkins_backup.py`
- Modify: `backend/app/services/jenkins_backup_service.py`

- [ ] **Step 1: Write failing credential fetch tests**

Test a new `fetch_credential_values(session, base_url, credential_ids)` function with a fake `/scriptText` response. Cover Secret Text, username/password, SSH private key, an unknown type, and HTTP 403. Assert that only requested IDs are returned and 403 returns an empty mapping without raising:

```python
assert result["Harbor"] == {
    "type": "username_password",
    "values": {"username": "robot", "password": "secret"},
}
assert fetch_credential_values(forbidden_session, base_url, {"Harbor"}) == {}
```

- [ ] **Step 2: Run the credential tests and verify RED**

Run: `backend\venv\Scripts\python.exe -m pytest backend\tests\test_jenkins_backup.py -k credential -v`

Expected: import or attribute failure because the function does not exist.

- [ ] **Step 3: Implement one fixed Jenkins Groovy export**

POST a fixed, non-user-interpolated Groovy script to `scriptText`. The script serializes standard Jenkins credentials to JSON with these normalized types: `secret_text`, `username_password`, and `ssh_private_key`. Send the requested IDs as a JSON request parameter and filter again in Python before returning. Set a 15-second timeout, call `raise_for_status()`, catch request/JSON errors, log only the error class/status, and return `{}`.

Merge values into each job credential reference by ID. Missing values produce:

```python
{"value_unavailable": True, "values": {}}
```

- [ ] **Step 4: Run the credential tests and verify GREEN**

Run: `backend\venv\Scripts\python.exe -m pytest backend\tests\test_jenkins_backup.py -k credential -v`

Expected: all selected tests pass with no credential values printed.

- [ ] **Step 5: Commit the credential slice**

```powershell
git add backend/app/services/jenkins_backup_service.py backend/tests/test_jenkins_backup.py
git commit -m "feat: back up referenced Jenkins credential values"
```

### Task 3: Generate versioned structured backup details

**Files:**
- Modify: `backend/tests/test_jenkins_backup.py`
- Modify: `backend/app/services/jenkins_backup_service.py`

- [ ] **Step 1: Extend the backup test and verify RED**

Use two Shell steps, one parameter, one environment variable, and one credential binding in the mocked Job XML. Mock the script endpoint, open the produced ZIP, and assert:

```python
with zipfile.ZipFile(updated_backup.zip_path) as archive:
    details = json.loads(archive.read("details.json"))
assert details["version"] == 1
assert details["jobs"][0]["scripts"][1]["filename"] == "build_step_2.sh"
assert details["jobs"][0]["credentials"][0]["values"]["password"] == "secret"
assert "secret" not in updated_backup.summary_md
```

Run: `backend\venv\Scripts\python.exe -m pytest backend\tests\test_jenkins_backup.py::test_execute_jenkins_backup_success -v`

Expected: failure because `details.json` is absent.

- [ ] **Step 2: Build one JSON-safe job detail record**

Within the existing backup loop, build job records containing `name`, `project_type`, `status`, `git_urls`, `branches`, `triggers`, `parameters`, `environment`, `credentials`, and `scripts`. Reuse the same filename rule already used for exported scripts. Store full content for Shell, Batch and inline Pipeline scripts; store Maven/Gradle commands as textual script entries.

- [ ] **Step 3: Write `details.json` before packaging**

Write `{"version": 1, "jobs": job_details}` to the temporary root with UTF-8 and `ensure_ascii=False`. Do not add credential values to `summary.md` or logging.

- [ ] **Step 4: Run the backup test and full backup test file**

Run: `backend\venv\Scripts\python.exe -m pytest backend\tests\test_jenkins_backup.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit the archive slice**

```powershell
git add backend/app/services/jenkins_backup_service.py backend/tests/test_jenkins_backup.py
git commit -m "feat: add structured Jenkins backup details"
```

### Task 4: Add the admin-only structured details endpoint

**Files:**
- Modify: `backend/tests/test_jenkins.py`
- Modify: `backend/app/api/jenkins.py`

- [ ] **Step 1: Write failing API tests**

Add tests for `GET /servers/{server_id}/backups/{backup_id}/details`: admin receives parsed `details.json`; operator receives 403; a legacy ZIP without `details.json` receives `{"available": false, "jobs": []}`. Add a test proving ZIP download rejects an operator once an archive may contain plaintext credentials.

- [ ] **Step 2: Run the API tests and verify RED**

Run: `backend\venv\Scripts\python.exe -m pytest backend\tests\test_jenkins.py -k 'backup and (details or download)' -v`

Expected: 404 for the missing endpoint and/or authorization assertion failures.

- [ ] **Step 3: Implement ZIP reading with path validation**

Add the details route using `get_current_active_admin`. Validate backup ownership, success status and file existence, then open `details.json` directly with `zipfile.ZipFile`. Return:

```python
{"available": True, "version": details.get("version", 1), "jobs": details.get("jobs", [])}
```

Catch missing member as the legacy response and malformed ZIP/JSON as HTTP 500 without returning raw content. Reuse `get_current_active_admin` for ZIP downloads and remove the manual query-token authentication path, so bearer-authenticated admin requests download through Axios as a Blob rather than `window.open`.

- [ ] **Step 4: Run the API tests and verify GREEN**

Run: `backend\venv\Scripts\python.exe -m pytest backend\tests\test_jenkins.py -k 'backup and (details or download)' -v`

Expected: all selected tests pass.

- [ ] **Step 5: Commit the API slice**

```powershell
git add backend/app/api/jenkins.py backend/tests/test_jenkins.py
git commit -m "feat: expose admin-only Jenkins backup details"
```

### Task 5: Add tested frontend detail helpers

**Files:**
- Create: `frontend/src/utils/jenkins-backup.ts`
- Create: `frontend/scripts/jenkins-backup-ui.test.mjs`
- Modify: `frontend/package.json`

- [ ] **Step 1: Write the failing Node test**

Follow `release-ui.test.mjs`: transpile the TypeScript helper with the installed TypeScript package. Assert `normalizeBackupDetails()` supplies empty arrays for missing legacy fields, `environmentCopyText()` returns `APP_ENV=test`, and `credentialCopyText()` emits binding-aware lines such as `HARBOR_USER=robot\nHARBOR_PASSWORD=secret`.

- [ ] **Step 2: Run the helper test and verify RED**

Run: `npm run test:jenkins-ui --prefix frontend`

Expected: failure because the script/helper or npm command does not exist.

- [ ] **Step 3: Implement the pure helpers**

Define minimal TypeScript interfaces matching `details.json`; normalize only optional arrays/objects and format copy text without touching browser APIs. Add `test:jenkins-ui` and make `test:ui` run both existing Node scripts.

- [ ] **Step 4: Run the frontend helper tests and verify GREEN**

Run: `npm run test:ui --prefix frontend`

Expected: both Node scripts exit 0.

- [ ] **Step 5: Commit the frontend helper slice**

```powershell
git add frontend/src/utils/jenkins-backup.ts frontend/scripts/jenkins-backup-ui.test.mjs frontend/package.json
git commit -m "test: cover Jenkins backup detail formatting"
```

### Task 6: Replace the Markdown-only modal with interactive details

**Files:**
- Modify: `frontend/src/views/jenkins/BackupDrawer.vue`

- [ ] **Step 1: Load structured details with legacy fallback**

When “查看汇总” opens, request `/details` first. Normalize and render structured jobs when `available` is true; otherwise call the existing `/summary` endpoint and retain the legacy renderer. Clear both structured and legacy state when the modal closes.

- [ ] **Step 2: Render the scrollable table**

Use a native table inside a `max-height: 75vh; overflow: auto` wrapper. Give the table a content-based minimum width, make `thead th` sticky at top, and make the first Job column sticky at left. Do not add a table/grid dependency.

- [ ] **Step 3: Add environment/parameter/credential popovers**

Use Naive UI `NPopover` with `trigger="hover"` and clickable tags/buttons inside the popover. Group environment variables, parameters, tools and credentials. Preserve whitespace in values, cap the popover viewport, and set `overflow: auto` in both axes. Provide copy-name, copy-value and copy-assignment buttons; call `navigator.clipboard.writeText` and show a message.

- [ ] **Step 4: Add multi-script popovers**

Show script count/type tags. Inside the popover, switch the active script with small buttons, render full content in `<pre><code>`, and copy the active script. Use `white-space: pre`, `max-height`, `max-width`, and `overflow: auto` so long lines and long files each get native scrollbars.

- [ ] **Step 5: Download through authenticated Axios Blob**

Replace the query-token `window.open` flow with `request.get(url, {responseType: 'blob'})`, create a temporary object URL, click a temporary anchor, then revoke the URL. This keeps the administrator bearer token in the Authorization header and out of URLs.

- [ ] **Step 6: Run typecheck/build**

Run: `npm run build --prefix frontend`

Expected: `vue-tsc -b` and Vite production build exit 0.

- [ ] **Step 7: Commit the UI slice**

```powershell
git add frontend/src/views/jenkins/BackupDrawer.vue
git commit -m "feat: add interactive Jenkins backup summary"
```

### Task 7: Full verification and review

**Files:**
- Verify all modified files.

- [ ] **Step 1: Run focused backend tests**

Run: `backend\venv\Scripts\python.exe -m pytest backend\tests\test_jenkins_backup.py backend\tests\test_jenkins.py -v`

Expected: zero failures.

- [ ] **Step 2: Run the complete backend suite**

Run: `backend\venv\Scripts\python.exe -m pytest backend\tests -v`

Expected: zero failures.

- [ ] **Step 3: Run frontend tests and build**

Run: `npm run test:ui --prefix frontend`

Run: `npm run build --prefix frontend`

Expected: both commands exit 0.

- [ ] **Step 4: Inspect the final diff for secret leakage**

Confirm credential values are absent from log calls, Markdown generation, exception text, URLs and browser persistence. Confirm plaintext exposure is limited to `details.json`, the admin-only details response and the in-memory UI as approved.

- [ ] **Step 5: Run the repository code-review skill**

Review the final diff against this specification and repository conventions. Resolve actionable findings, then repeat Steps 1-3.

- [ ] **Step 6: Commit any review fixes**

```powershell
git add backend frontend docs/superpowers
git commit -m "fix: address Jenkins backup summary review"
```

If Git is still unavailable because the workspace has no recognized `.git`, report that exact blocker and leave the verified working-tree files unchanged.

