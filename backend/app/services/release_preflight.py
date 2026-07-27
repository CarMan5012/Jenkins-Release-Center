from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, wait
from time import monotonic
from types import SimpleNamespace
from urllib.parse import quote, urljoin, urlparse

import requests
from sqlalchemy import exists, update
from sqlalchemy.orm import Session

from app.models.jenkins import JenkinsServer
from app.models.release import ReleasePlan, ReleaseTask
from app.services.jenkins_client import JenkinsClient


SEVERITY = {"PASSED": 0, "WARNING": 1, "FAILED": 2}
PREFLIGHT_WORKERS = 8
PREFLIGHT_DEADLINE_SECONDS = 30


def url_origin(url: str) -> tuple[str, str, int]:
    if not isinstance(url, str):
        raise ValueError("Origin 必须是 URL 字符串")
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Origin 必须是有效的 HTTP URL")
    return parsed.scheme.lower(), parsed.hostname.lower(), parsed.port or (443 if parsed.scheme.lower() == "https" else 80)


def is_compatible_origin(origin1: tuple[str, str, int], origin2: tuple[str, str, int]) -> bool:
    scheme1, host1, port1 = origin1
    scheme2, host2, port2 = origin2

    if scheme1 != scheme2:
        return False

    loopbacks = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
    if (host1 in loopbacks and host2 in loopbacks) and port1 == port2:
        return True

    if host1 == host2 and port1 == port2:
        return True

    return False


def aggregate_status(statuses) -> str:
    return max(statuses, key=SEVERITY.get, default="PASSED")


def preflight_block_reason(plan: ReleasePlan) -> str | None:
    if plan.preflight_status in {"PASSED", "WARNING"}:
        return None
    return {"UNCHECKED": "发布计划尚未检查", "FAILED": "发布前检查未通过"}.get(plan.preflight_status, "发布前检查状态异常")


def _check(code: str, status: str, message: str) -> dict:
    return {"code": code, "status": status, "message": message}


def _server_probe(client: JenkinsClient) -> dict:
    try:
        configured = url_origin(client.base_url)
    except ValueError:
        return _check("origin", "FAILED", "Jenkins Origin 无效")
    try:
        root = client.session.get(
            urljoin(client.base_url, "api/json?tree=url"),
            timeout=10,
            allow_redirects=False,
        )
        canonical = client.session.get(
            client.base_url,
            timeout=10,
            allow_redirects=False,
        )
    except requests.exceptions.SSLError:
        return _check("connection", "FAILED", "Jenkins TLS 认证失败")
    except (requests.Timeout, requests.ConnectionError):
        return _check("connection", "WARNING", "Jenkins 暂时无法连接")
    except requests.RequestException:
        return _check("connection", "FAILED", "Jenkins 请求无效")

    if root.status_code in {401, 403} or canonical.status_code in {401, 403}:
        return _check("auth", "FAILED", "Jenkins 认证失败")
    if root.status_code >= 500 or canonical.status_code >= 500:
        return _check("connection", "WARNING", "Jenkins 服务异常")
    if root.status_code != 200 or canonical.status_code >= 400:
        return _check("connection", "FAILED", "Jenkins 根地址不可用")

    try:
        if 300 <= canonical.status_code < 400:
            location = canonical.headers.get("Location")
            if not location:
                return _check("origin", "FAILED", "Jenkins Origin 不一致")
            redirect_origin = url_origin(urljoin(client.base_url, location))
            if not is_compatible_origin(redirect_origin, configured):
                return _check("origin", "FAILED", "Jenkins Origin 不一致")

        payload = root.json()
        if not isinstance(payload, dict):
            return _check("origin", "FAILED", "Jenkins Origin 不一致")

        raw_url = payload.get("url")
        if raw_url is not None:
            if not isinstance(raw_url, str):
                return _check("origin", "FAILED", "Jenkins Origin 不一致")
            if raw_url.strip():
                remote_origin = url_origin(raw_url)
                if not is_compatible_origin(remote_origin, configured):
                    pass
    except (TypeError, ValueError):
        return _check("origin", "FAILED", "Jenkins Origin 不一致")

    return _check("origin", "PASSED", "Jenkins Origin 正常")


def _parameter_names(payload: object) -> set[str]:
    if not isinstance(payload, dict):
        raise ValueError
    names = set()
    for key in ("property", "actions"):
        containers = payload.get(key, [])
        if not isinstance(containers, list):
            raise ValueError
        for container in containers:
            if container is None:
                continue
            if not isinstance(container, dict):
                raise ValueError
            definitions = container.get("parameterDefinitions", [])
            if not isinstance(definitions, list):
                raise ValueError
            for definition in definitions:
                if not isinstance(definition, dict):
                    raise ValueError
                if isinstance(definition.get("name"), str):
                    names.add(definition["name"])
    return names


def _job_checks(client: JenkinsClient, task) -> list[dict]:
    job_path = "/".join(f"job/{quote(part)}" for part in task.job_name.split("/"))
    url = urljoin(
        client.base_url,
        f"{job_path}/api/json?tree=name,property[parameterDefinitions[name]],actions[parameterDefinitions[name]]",
    )
    try:
        response = client.session.get(url, timeout=10, allow_redirects=False)
    except requests.exceptions.SSLError:
        return [_check("job", "FAILED", "Jenkins TLS 认证失败")]
    except (requests.Timeout, requests.ConnectionError):
        return [_check("job", "WARNING", "Jenkins 任务暂时无法连接")]
    except requests.RequestException:
        return [_check("job", "FAILED", "Jenkins 任务请求无效")]

    if response.status_code in {401, 403}:
        return [_check("auth", "FAILED", "Jenkins 认证失败")]
    if response.status_code == 404:
        return [_check("job", "FAILED", "Jenkins 任务不存在")]
    if response.status_code >= 500:
        return [_check("job", "WARNING", "Jenkins 服务异常")]
    if response.status_code != 200:
        return [_check("job", "FAILED", "Jenkins 任务不可用")]

    try:
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("name"), str) or not payload["name"]:
            raise ValueError
        names = _parameter_names(payload)
    except (TypeError, ValueError):
        return [_check("job", "FAILED", "Jenkins 任务数据无效")]
    unknown = set(task.parameters or {}) - names
    if unknown:
        return [
            _check("job", "PASSED", "Jenkins 任务存在"),
            _check("parameters", "FAILED", "发布参数未在 Jenkins 中定义"),
        ]
    return [
        _check("job", "PASSED", "Jenkins 任务存在"),
        _check("parameters", "PASSED", "发布参数有效"),
    ]


def _client(config) -> JenkinsClient:
    return JenkinsClient(config.url, config.username, config.api_token)


def _job_probe(config, task) -> list[dict]:
    return _job_checks(_client(config), task)


def run_release_preflight(
    db: Session, plan: ReleasePlan, expected_revision: int | None = None
) -> ReleasePlan:
    revision = plan.preflight_revision if expected_revision is None else expected_revision
    tasks = [
        SimpleNamespace(
            id=task.id,
            server_id=task.server_id,
            job_name=task.job_name,
            parameters=dict(task.parameters or {}),
        )
        for task in plan.tasks
    ]
    server_ids = {task.server_id for task in tasks}
    configs = {}
    server_checks = {}
    for server_id in server_ids:
        server = db.get(JenkinsServer, server_id)
        if not server:
            server_checks[server_id] = _check("server", "FAILED", "Jenkins 配置不存在")
        elif not server.is_active:
            server_checks[server_id] = _check("server", "FAILED", "Jenkins 配置已禁用")
        else:
            configs[server_id] = SimpleNamespace(
                url=server.url, username=server.username, api_token=server.api_token
            )

    deadline = monotonic() + PREFLIGHT_DEADLINE_SECONDS
    executor = ThreadPoolExecutor(max_workers=PREFLIGHT_WORKERS)
    try:
        probes = {
            executor.submit(lambda config=config: _server_probe(_client(config))): server_id
            for server_id, config in configs.items()
        }
        done, pending = wait(probes, timeout=max(0, deadline - monotonic()))
        for future in done:
            server_id = probes[future]
            try:
                server_checks[server_id] = future.result()
            except Exception:
                server_checks[server_id] = _check("server", "FAILED", "Jenkins 检查异常")
        for future in pending:
            future.cancel()
            server_checks[probes[future]] = _check("connection", "WARNING", "Jenkins 检查超时")

        results = [None] * len(tasks)
        jobs = {}
        for index, task in enumerate(tasks):
            server_check = server_checks[task.server_id]
            if server_check["status"] != "PASSED":
                checks = [server_check]
                results[index] = {
                    "task_id": task.id,
                    "job_name": task.job_name,
                    "status": aggregate_status(check["status"] for check in checks),
                    "checks": checks,
                }
                continue
            future = executor.submit(_job_probe, configs[task.server_id], task)
            jobs[future] = (index, task, server_check)

        done, pending = wait(jobs, timeout=max(0, deadline - monotonic()))
        for future in done:
            index, task, server_check = jobs[future]
            try:
                checks = [server_check, *future.result()]
            except Exception:
                checks = [server_check, _check("job", "FAILED", "Jenkins 任务检查异常")]
            results[index] = {
                "task_id": task.id,
                "job_name": task.job_name,
                "status": aggregate_status(check["status"] for check in checks),
                "checks": checks,
            }
        for future in pending:
            future.cancel()
            index, task, server_check = jobs[future]
            checks = [server_check, _check("job", "WARNING", "Jenkins 任务检查超时")]
            results[index] = {
                "task_id": task.id,
                "job_name": task.job_name,
                "status": "WARNING",
                "checks": checks,
            }
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    status = aggregate_status(task["status"] for task in results)
    result = {
        "summary": f"{len(results)} 个任务，状态 {status}",
        "tasks": results,
    }
    updated = db.execute(
        update(ReleasePlan)
        .where(
            ReleasePlan.id == plan.id,
            ReleasePlan.preflight_revision == revision,
            ReleasePlan.status == "WAITING",
            ~exists().where(
                ReleaseTask.plan_id == plan.id,
                ReleaseTask.status != "WAITING",
            ),
        )
        .values(
            preflight_status=status,
            preflight_checked_at=datetime.now(),
            preflight_result=result,
        )
    )
    if updated.rowcount == 1:
        db.commit()
    else:
        db.rollback()
    db.expire_all()
    return db.get(ReleasePlan, plan.id)
