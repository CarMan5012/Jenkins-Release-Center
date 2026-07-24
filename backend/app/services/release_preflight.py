from datetime import datetime
from urllib.parse import quote, urljoin, urlparse

import requests
from sqlalchemy.orm import Session

from app.models.jenkins import JenkinsServer
from app.models.release import ReleasePlan
from app.services.jenkins_client import JenkinsClient


SEVERITY = {"PASSED": 0, "WARNING": 1, "FAILED": 2}


def url_origin(url: str) -> tuple[str, str, int]:
    if not isinstance(url, str):
        raise ValueError("Origin 必须是 URL 字符串")
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Origin 必须是有效的 HTTP URL")
    return parsed.scheme.lower(), parsed.hostname.lower(), parsed.port or (443 if parsed.scheme.lower() == "https" else 80)


def aggregate_status(statuses) -> str:
    return max(statuses, key=SEVERITY.get, default="PASSED")


def preflight_block_reason(status: str) -> str | None:
    return {"UNCHECKED": "发布计划尚未检查", "FAILED": "发布前检查未通过"}.get(status)


def _check(code: str, status: str, message: str) -> dict:
    return {"code": code, "status": status, "message": message}


def _server_probe(client: JenkinsClient) -> dict:
    try:
        root = client.session.get(
            urljoin(client.base_url, "api/json?tree=url"),
            timeout=10,
            allow_redirects=False,
        )
        canonical = client.session.get(
            client.base_url.rstrip("/"),
            timeout=10,
            allow_redirects=False,
        )
    except (requests.Timeout, requests.ConnectionError):
        return _check("connection", "WARNING", "Jenkins 暂时无法连接")
    except requests.RequestException:
        return _check("connection", "WARNING", "Jenkins 请求失败")

    if root.status_code in {401, 403} or canonical.status_code in {401, 403}:
        return _check("auth", "FAILED", "Jenkins 认证失败")
    if root.status_code >= 500 or canonical.status_code >= 500:
        return _check("connection", "WARNING", "Jenkins 服务异常")
    if root.status_code != 200 or canonical.status_code >= 400:
        return _check("connection", "FAILED", "Jenkins 根地址不可用")

    try:
        configured = url_origin(client.base_url)
        payload = root.json()
        if not isinstance(payload, dict) or url_origin(payload.get("url", "")) != configured:
            raise ValueError("Origin mismatch")
        location = canonical.headers.get("Location")
        if 300 <= canonical.status_code < 400 and location:
            if url_origin(urljoin(client.base_url, location)) != configured:
                raise ValueError("Origin mismatch")
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
    except (requests.Timeout, requests.ConnectionError):
        return [_check("job", "WARNING", "Jenkins 任务暂时无法连接")]
    except requests.RequestException:
        return [_check("job", "WARNING", "Jenkins 任务请求失败")]

    if response.status_code in {401, 403}:
        return [_check("auth", "FAILED", "Jenkins 认证失败")]
    if response.status_code == 404:
        return [_check("job", "FAILED", "Jenkins 任务不存在")]
    if response.status_code >= 500:
        return [_check("job", "WARNING", "Jenkins 服务异常")]
    if response.status_code != 200:
        return [_check("job", "FAILED", "Jenkins 任务不可用")]

    try:
        names = _parameter_names(response.json())
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


def run_release_preflight(db: Session, plan: ReleasePlan) -> ReleasePlan:
    server_cache = {}
    task_results = []
    for task in plan.tasks:
        if task.server_id not in server_cache:
            server = db.get(JenkinsServer, task.server_id)
            if not server:
                server_cache[task.server_id] = (None, _check("server", "FAILED", "Jenkins 配置不存在"))
            elif not server.is_active:
                server_cache[task.server_id] = (None, _check("server", "FAILED", "Jenkins 配置已禁用"))
            else:
                client = JenkinsClient(server.url, server.username, server.api_token)
                server_cache[task.server_id] = (client, _server_probe(client))

        client, server_check = server_cache[task.server_id]
        checks = [server_check]
        if server_check["status"] == "PASSED":
            checks.extend(_job_checks(client, task))
        task_results.append(
            {
                "task_id": task.id,
                "job_name": task.job_name,
                "status": aggregate_status(check["status"] for check in checks),
                "checks": checks,
            }
        )

    status = aggregate_status(task["status"] for task in task_results)
    plan.preflight_status = status
    plan.preflight_checked_at = datetime.now()
    plan.preflight_result = {
        "summary": f"{len(task_results)} 个任务，状态 {status}",
        "tasks": task_results,
    }
    db.commit()
    db.refresh(plan)
    return plan
