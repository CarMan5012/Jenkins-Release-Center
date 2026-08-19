import json
import requests
import hmac
import hashlib
import time
import base64
from datetime import datetime
from typing import Dict, Any, List, Optional
from loguru import logger
from sqlalchemy.orm import Session
from app.core.database import SyncSessionLocal
from app.models.system import NotifyConfig, SystemConfig
from app.models.release import ReleaseTask, ReleasePlan
from app.models.jenkins import JenkinsServer, JenkinsJob, JenkinsView
import urllib.parse
import socket
import ipaddress


def is_safe_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname
        if not host:
            return False
        return True
    except Exception:
        return False


def send_notification_to_channel(
    config: NotifyConfig,
    title: str,
    content: str,
    markdown_content: str,
    btns: Optional[List[Dict[str, str]]] = None,
    single_url: Optional[str] = None,
):
    """
    Send messages to DingTalk, Enterprise WeChat (WeCom), or Custom Webhook as Rich ActionCards.
    """
    if not is_safe_url(config.webhook_url):
        raise ValueError("不合法的 Webhook 地址")

    headers = {"Content-Type": "application/json"}
    payload = {}
    url = config.webhook_url

    if config.channel_type == "DINGTALK":
        if config.secret:
            timestamp = str(round(time.time() * 1000))
            secret_enc = config.secret.encode("utf-8")
            string_to_sign = f"{timestamp}\n{config.secret}"
            string_to_sign_enc = string_to_sign.encode("utf-8")
            hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
            sign = base64.b64encode(hmac_code).decode("utf-8")
            sep = "&" if "?" in config.webhook_url else "?"
            url = f"{config.webhook_url}{sep}timestamp={timestamp}&sign={urllib.parse.quote_plus(sign)}"

        text_content = markdown_content
        keyword_val = getattr(config, "keyword", None)
        if keyword_val and keyword_val not in text_content:
            text_content = f"{markdown_content}  \n<font color=\"transparent\">{keyword_val}</font>"

        if btns and len(btns) > 0:
            payload = {
                "msgtype": "actionCard",
                "actionCard": {
                    "title": title,
                    "text": text_content,
                    "btnOrientation": "0",
                    "btns": btns,
                },
            }
        else:
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "title": title,
                    "text": text_content,
                },
            }
    elif config.channel_type == "WECHAT":
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": markdown_content,
            },
        }
    else:  # Custom WEBHOOK
        payload = {
            "event": title,
            "message": content,
            "markdown": markdown_content,
            "btns": btns or [],
            "timestamp": time.time(),
        }
        if config.secret:
            headers["X-Webhook-Signature"] = hmac.new(
                config.secret.encode(),
                json.dumps(payload).encode(),
                hashlib.sha256,
            ).hexdigest()

    try:
        logger.info(f"Sending notification via {config.channel_type} to {config.name}...")
        response = requests.post(url, headers=headers, json=payload, timeout=10, allow_redirects=False)
        if response.status_code not in [200, 204]:
            raise RuntimeError(f"HTTP {response.status_code} - {response.text}")

        try:
            res_json = response.json()
            if isinstance(res_json, dict) and res_json.get("errcode") not in [0, None]:
                raise RuntimeError(f"钉钉/推送通道返回业务错误 code={res_json.get('errcode')}: {res_json.get('errmsg')}")
        except ValueError:
            pass

        logger.info(f"Notification card sent successfully to {config.name}")
    except Exception as e:
        logger.error(f"Error sending notification to {config.name}: {str(e)}")
        raise e


_sent_notifications_cache = set()


def clear_plan_notifications_cache(plan_id: int):
    """
    清除指定发布计划的通知发送防重缓存，用于重试、重新执行场景。
    """
    _sent_notifications_cache.discard(f"plan_start:{plan_id}")
    _sent_notifications_cache.discard(f"plan_summary:{plan_id}")


def get_plan_environment(db: Session, plan_tasks: List[ReleaseTask]) -> str:
    """
    提取发布计划关联的所有 Jenkins Job 的 View（环境）名称。
    - 单个 View：直接显示 View 名称
    - 多个 View：去重后使用“ / ”连接
    - 没有 View：显示“未分类”
    """
    views = []
    for t in plan_tasks:
        view_name = None
        try:
            if hasattr(t, "job") and t.job and hasattr(t.job, "view") and t.job.view and getattr(t.job.view, "name", None):
                view_name = t.job.view.name
            elif getattr(t, "job_id", None):
                job_obj = db.query(JenkinsJob).filter(JenkinsJob.id == t.job_id).first()
                if job_obj and getattr(job_obj, "view", None) and getattr(job_obj.view, "name", None):
                    view_name = job_obj.view.name
            else:
                job_obj = db.query(JenkinsJob).filter(
                    JenkinsJob.server_id == t.server_id,
                    JenkinsJob.name == t.job_name
                ).first()
                if job_obj and getattr(job_obj, "view", None) and getattr(job_obj.view, "name", None):
                    view_name = job_obj.view.name
        except Exception:
            view_name = None

        if view_name and isinstance(view_name, str) and view_name.strip():
            views.append(view_name.strip())

    unique_views = list(dict.fromkeys(views))
    if not unique_views:
        return "未分类"
    return " / ".join(unique_views)



def format_duration(seconds: int) -> str:
    """格式化耗时为人类可读字符串"""
    if seconds <= 0:
        return "0秒"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    parts = []
    if hours > 0:
        parts.append(f"{hours}小时")
    if minutes > 0:
        parts.append(f"{minutes}分")
    if secs > 0 or not parts:
        parts.append(f"{secs}秒")
    return "".join(parts)


def send_plan_start_notification(plan_id: int):
    """
    当发布计划开始触发时发送【发布开始】ActionCard 卡片。
    仅在通道配置了 "start" 触发事件时推送。
    """
    cache_key = f"plan_start:{plan_id}"
    if cache_key in _sent_notifications_cache:
        return

    db: Session = SyncSessionLocal()
    try:
        configs = db.query(NotifyConfig).filter(NotifyConfig.is_active == 1).all()
        if not configs:
            return

        plan = db.query(ReleasePlan).filter(ReleasePlan.id == plan_id).first()
        if not plan:
            return

        plan_tasks = db.query(ReleaseTask).filter(ReleaseTask.plan_id == plan.id).all()
        total_jobs = len(plan_tasks)
        environment = get_plan_environment(db, plan_tasks)

        title = "🚀 发布任务已启动"
        markdown_lines = [
            f"### 🚀 发布任务已启动",
            f"> **计划名称**：{plan.name}",
            f"> **目标视图**：{environment}",
            f"> ",
            f"> **包含任务**：{total_jobs} 个",
            f'> **当前状态**：<font color="#1677FF">执行中...</font>'
        ]

        markdown_content = "  \n".join(markdown_lines)
        plain_content = f"计划名称: {plan.name}, 视图: {environment}, Job 数量: {total_jobs} 个, 当前状态: 执行中"
        system_url_config = db.query(SystemConfig).filter(SystemConfig.config_key == "system_url").first()
        base_url = system_url_config.config_value.rstrip('/') if system_url_config and system_url_config.config_value else "http://localhost:3000"
        detail_url = f"{base_url}/release/{plan.id}"

        for config in configs:
            if config.channel_type == "DINGTALK":
                if not getattr(plan, "notify_dingtalk", False):
                    continue
            if "start" in config.trigger_events:
                try:
                    send_notification_to_channel(config, title, plain_content, markdown_content, btns=None, single_url=detail_url)
                except Exception as channel_err:
                    logger.error(f"Failed to send plan start notification: {str(channel_err)}")

        _sent_notifications_cache.add(cache_key)
    except Exception as e:
        logger.error(f"Error in send_plan_start_notification: {str(e)}")
    finally:
        db.close()


def send_plan_summary_notification(plan_id: int):
    """
    当整个发布计划下的所有 Job 运行完成时，发送最终结果 ActionCard 卡片。
    精确防重机制：(plan_id) 保证只在最终跑完时发送 1 次。
    """
    cache_key = f"plan_summary:{plan_id}"
    if cache_key in _sent_notifications_cache:
        logger.info(f"Plan summary notification for plan {plan_id} already sent. Skipping duplicate.")
        return

    db: Session = SyncSessionLocal()
    try:
        configs = db.query(NotifyConfig).filter(NotifyConfig.is_active == 1).all()
        if not configs:
            return

        plan = db.query(ReleasePlan).filter(ReleasePlan.id == plan_id).first()
        if not plan:
            return

        plan_tasks = db.query(ReleaseTask).filter(ReleaseTask.plan_id == plan.id).all()
        if not plan_tasks:
            return

        total_jobs = len(plan_tasks)
        success_count = sum(1 for t in plan_tasks if t.status == "SUCCESS")
        fail_count = sum(1 for t in plan_tasks if t.status in ["FAILED", "UNSTABLE"])
        environment = get_plan_environment(db, plan_tasks)

        # 计算总耗时
        start_times = [t.started_at for t in plan_tasks if t.started_at]
        finish_times = [t.finished_at for t in plan_tasks if t.finished_at]
        if start_times:
            earliest_start = min(start_times)
            latest_finish = max(finish_times) if finish_times else datetime.now()
            duration_seconds = max(0, int((latest_finish - earliest_start).total_seconds()))
        else:
            duration_seconds = sum(t.duration for t in plan_tasks if t.duration)

        duration_str = format_duration(duration_seconds)
        system_url_config = db.query(SystemConfig).filter(SystemConfig.config_key == "system_url").first()
        base_url = system_url_config.config_value.rstrip('/') if system_url_config and system_url_config.config_value else "http://localhost:3000"
        detail_url = f"{base_url}/release/{plan.id}"

        # 划分终态类型：发布取消 / 发布失败 / 发布成功
        if plan.status == "CANCELLED":
            title = "⚠️ 发布已取消"
            plan_event = "failed"
            markdown_lines = [
                f"### ⚠️ 发布已取消",
                f"> **计划名称**：{plan.name}",
                f"> **目标视图**：{environment}",
                f"> ",
                f'> **执行结果**：<font color="#8C8C8C">已手动取消</font>',
                f"> **总耗时**：{duration_str}"
            ]
        elif fail_count > 0 or plan.status == "FAILED":
            title = "❌ 发布出现失败"
            plan_event = "failed"
            markdown_lines = [
                f"### ❌ 发布出现失败",
                f"> **计划名称**：{plan.name}",
                f"> **目标视图**：{environment}",
                f"> ",
                f'> **执行结果**：<font color="#52C41A">{success_count} 成功</font> / <font color="#FF4D4F">{fail_count} 失败</font>',
                f"> **总耗时**：{duration_str}"
            ]
        else:
            title = "🎉 发布已完成 (成功)"
            plan_event = "success"
            markdown_lines = [
                f"### 🎉 发布已完成 (成功)",
                f"> **计划名称**：{plan.name}",
                f"> **目标视图**：{environment}",
                f"> ",
                f'> **执行结果**：<font color="#52C41A">{success_count} 成功</font> / <font color="#FF4D4F">0 失败</font>',
                f"> **总耗时**：{duration_str}"
            ]

        markdown_content = "  \n".join(markdown_lines)
        plain_content = f"计划名称: {plan.name}, 视图: {environment}, 成功: {success_count}, 失败: {fail_count}, 总耗时: {duration_str}"

        for config in configs:
            if config.channel_type == "DINGTALK":
                if not getattr(plan, "notify_dingtalk", False):
                    logger.info(f"Release plan {plan_id} disabled DingTalk notification, skipping channel {config.name}.")
                    continue

            if plan_event in config.trigger_events:
                try:
                    send_notification_to_channel(config, title, plain_content, markdown_content, btns=None, single_url=detail_url)
                except Exception as channel_err:
                    logger.error(f"Failed to send plan summary notification to channel {config.name}: {str(channel_err)}")

        _sent_notifications_cache.add(cache_key)
    except Exception as e:
        logger.error(f"Error in send_plan_summary_notification: {str(e)}")
    finally:
        db.close()


def send_release_notification(task_id: int, event: str):
    """
    不发送单个 Jenkins Job 的过程通知。
    发布计划下属 Task 的通知由 send_plan_start_notification 与 send_plan_summary_notification 统一负责。
    """
    logger.info(f"Per-job intermediate notification suppressed for task {task_id}.")
    return
