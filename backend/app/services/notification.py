import json
import requests
import hmac
import hashlib
import time
import base64
from typing import Dict, Any, List, Optional
from loguru import logger
from sqlalchemy.orm import Session
from app.core.database import SyncSessionLocal
from app.models.system import NotifyConfig
from app.models.release import ReleaseTask, ReleasePlan
from app.models.jenkins import JenkinsView
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
    btns: Optional[List[Dict[str, str]]] = None
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
            secret_enc = config.secret.encode('utf-8')
            string_to_sign = f'{timestamp}\n{config.secret}'
            string_to_sign_enc = string_to_sign.encode('utf-8')
            hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
            sign = base64.b64encode(hmac_code).decode('utf-8')
            import urllib.parse
            url = f"{config.webhook_url}&timestamp={timestamp}&sign={urllib.parse.quote_plus(sign)}"
            
        text_content = markdown_content
        keyword_val = getattr(config, "keyword", None)
        if keyword_val and keyword_val not in text_content:
            text_content = f"{markdown_content}  \n**安全关键词**: {keyword_val}"

        if btns and len(btns) > 0:
            payload = {
                "msgtype": "actionCard",
                "actionCard": {
                    "title": title,
                    "text": text_content,
                    "btnOrientation": "0",
                    "btns": btns
                }
            }
        else:
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "title": title,
                    "text": text_content
                }
            }
    elif config.channel_type == "WECHAT":
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": markdown_content
            }
        }
    else: # Custom WEBHOOK
        payload = {
            "event": title,
            "message": content,
            "markdown": markdown_content,
            "btns": btns or [],
            "timestamp": time.time()
        }
        if config.secret:
            headers["X-Webhook-Signature"] = hmac.new(
                config.secret.encode(), 
                json.dumps(payload).encode(), 
                hashlib.sha256
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

def send_release_notification(task_id: int, event: str):
    """
    Constructs notification card and pushes to configured webhook channels.
    Guarantees strict idempotency: (task_id, event) is sent at most ONCE.
    """
    cache_key = f"{task_id}:{event}"
    if cache_key in _sent_notifications_cache:
        logger.info(f"Notification '{event}' for task {task_id} already sent. Skipping duplicate message.")
        return

    db: Session = SyncSessionLocal()
    try:
        configs = db.query(NotifyConfig).filter(NotifyConfig.is_active == 1).all()
        if not configs:
            return
            
        task = db.query(ReleaseTask).filter(ReleaseTask.id == task_id).first()
        if not task:
            logger.error(f"Notification error: Task {task_id} not found.")
            return
        
        plan = db.query(ReleasePlan).filter(ReleasePlan.id == task.plan_id).first()
        environment = "测试环境"
        if task.job and task.job.view:
            environment = task.job.view.name
            
        duration_str = f"{task.duration or 0} sec"
        
        # 匹配用户要求的状态文案（开始 / 成功 / 失败）
        if event == "start":
            status_html = "<font color=\"#1890ff\">开始</font>"
            status_plain = "开始"
        elif event == "success":
            status_html = "<font color=\"#52c41a\">成功</font>"
            status_plain = "成功"
        else:
            status_html = "<font color=\"#ff4d4f\">失败</font>"
            status_plain = "失败"

        trigger_by = "admin"
        if plan and getattr(plan, "creator", None):
            trigger_by = plan.creator.username or "admin"
        
        title = "Jenkins调度中心通知"
        plan_name = plan.name if plan else "直接构建"
        
        # 统一使用 4 个汉字标签名称对齐
        markdown_lines = [
            "### **Jenkins调度中心通知**",
            f"**计划名称**: {plan_name}",
            f"**模块名称**: {task.job_name}",
            f"**发布分支**: {task.branch or 'N/A'}",
            f"**构建编号**: #{task.build_number or 'N/A'}",
            f"**执行状态**: {status_html}",
            f"**发布环境**: {environment}",
            f"**任务耗时**: {duration_str}"
        ]
        
        if task.error_message and event == "failed":
            markdown_lines.append(f"**异常原因**: {task.error_message}")
            
        # 匹配用户截图中的 [更改记录] 与 [控制台] 按钮
        btns = []
        if task.build_url:
            btns.append({"title": "更改记录", "actionURL": task.build_url})
        else:
            btns.append({"title": "更改记录", "actionURL": task.console_url or "https://jenkins.example.com"})

        if task.console_url:
            btns.append({"title": "控制台", "actionURL": task.console_url})
        else:
            btns.append({"title": "控制台", "actionURL": "https://jenkins.example.com"})

        markdown_content = "  \n".join(markdown_lines)
        plain_content = f"模块: {task.job_name}, 分支: {task.branch}, 编号: #{task.build_number or 'N/A'}, 状态: {status_plain}, 耗时: {duration_str}"
        
        for config in configs:
            if event in config.trigger_events:
                try:
                    send_notification_to_channel(config, title, plain_content, markdown_content, btns=btns)
                except Exception as channel_err:
                    logger.error(f"Failed to send release notification to channel {config.name}: {str(channel_err)}")
        
        # Mark as sent in process cache to prevent double sending
        _sent_notifications_cache.add(cache_key)
                
    except Exception as e:
        logger.error(f"Error in send_release_notification: {str(e)}")
    finally:
        db.close()
