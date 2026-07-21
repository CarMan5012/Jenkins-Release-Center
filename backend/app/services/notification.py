import json
import requests
import hmac
import hashlib
import time
import base64
from typing import Dict, Any, List
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
        from app.core.config import settings
        if settings.APP_ENV == "development":
            return True
            
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname
        if not host:
            return False
            
        # Resolve hostname to all IP addresses
        addr_info = socket.getaddrinfo(host, None)
        ips = set()
        for item in addr_info:
            sockaddr = item[4]
            if sockaddr:
                ips.add(sockaddr[0])
                
        if not ips:
            return False
            
        for ip in ips:
            clean_ip = ip.split('%')[0]
            ip_obj = ipaddress.ip_address(clean_ip)
            if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_reserved or ip_obj.is_link_local:
                return False
        return True
    except Exception:
        return False

def send_notification_to_channel(config: NotifyConfig, title: str, content: str, markdown_content: str):
    """
    Send messages to DingTalk, Enterprise WeChat (WeCom), or Custom Webhook.
    """
    if not is_safe_url(config.webhook_url):
        raise ValueError("不合法的 Webhook 地址，禁止指向本地或私有内网 IP")

    headers = {"Content-Type": "application/json"}
    payload = {}
    
    url = config.webhook_url
    
    if config.channel_type == "DINGTALK":
        # Handle DingTalk signature verification if secret is set
        if config.secret:
            timestamp = str(round(time.time() * 1000))
            secret_enc = config.secret.encode('utf-8')
            string_to_sign = f'{timestamp}\n{config.secret}'
            string_to_sign_enc = string_to_sign.encode('utf-8')
            hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
            sign = base64.b64encode(hmac_code).decode('utf-8')
            import urllib.parse
            url = f"{config.webhook_url}&timestamp={timestamp}&sign={urllib.parse.quote_plus(sign)}"
            
        # If custom keyword is configured, append it as an HTML comment.
        # This satisfies DingTalk's security filtering policy (keyword is present in the raw text payload)
        # while hiding it completely from the user in the rendered chat message bubble.
        text_content = markdown_content
        keyword_val = getattr(config, "keyword", None)
        if keyword_val:
            text_content = f"{markdown_content}\n<!-- {keyword_val} -->"

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": text_content
            }
        }
    elif config.channel_type == "WECHAT":
        # Enterprise WeChat Webhook Markdown Format
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
            "timestamp": time.time()
        }
        if config.secret:
            headers["X-Webhook-Signature"] = hmac.new(
                config.secret.encode(), 
                json.dumps(payload).encode(), 
                hashlib.sha256
            ).hexdigest()

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10, allow_redirects=False)
        if response.status_code not in [200, 204]:
            raise RuntimeError(f"HTTP {response.status_code} - {response.text}")
        
        # Parse and check response JSON business error code
        try:
            res_json = response.json()
            if isinstance(res_json, dict) and res_json.get("errcode") not in [0, None]:
                raise RuntimeError(f"业务错误 {res_json.get('errcode')}: {res_json.get('errmsg')}")
        except ValueError:
            # Non-JSON responses are ignored (e.g. general webhook success)
            pass
            
        logger.info(f"Notification sent successfully to {config.name}")
    except Exception as e:
        logger.error(f"Error sending notification to {config.name}: {str(e)}")
        raise e

def send_release_notification(task_id: int, event: str):
    """
    Load task information and broadcast notifications to all matching active channels.
    event: 'start', 'success', 'failed'
    """
    db: Session = SyncSessionLocal()
    try:
        task = db.query(ReleaseTask).filter(ReleaseTask.id == task_id).first()
        if not task:
            logger.error(f"Notification error: Task {task_id} not found.")
            return
        
        plan = db.query(ReleasePlan).filter(ReleasePlan.id == task.plan_id).first()
        # Find environment from the job's view mapping
        environment = "Default"
        if task.job and task.job.view:
            environment = task.job.view.name
            
        duration_str = f"{task.duration}s" if task.duration else "N/A"
        
        status_emoji = "⏳" if event == "start" else ("✅" if event == "success" else "❌")
        status_text = "发布中..." if event == "start" else ("发布成功" if event == "success" else "发布失败")
        
        title = f"[{status_text}] {task.job_name} - {task.branch}"
        
        # Build standard Markdown template
        markdown_lines = [
            f"### {status_emoji} **Jenkins 调度发布通知**",
            f"**项目名称**: `{task.job_name}`",
            f"**发布分支**: `{task.branch}`",
            f"**发布环境**: {environment}",
            f"**执行状态**: **{status_text}**",
            f"**计划名称**: {plan.name if plan else 'N/A'}"
        ]
        
        if task.build_number:
            markdown_lines.append(f"**构建编号**: #{task.build_number}")
        if task.console_url:
            markdown_lines.append(f"**日志链接**: [查看 Console 日志]({task.console_url})")
        if task.build_url:
            markdown_lines.append(f"**构建链接**: [查看 Jenkins Build]({task.build_url})")
        if event != "start":
            markdown_lines.append(f"**任务耗时**: {duration_str}")
        if task.error_message and event == "failed":
            markdown_lines.append(f"**异常原因**: <font color=\"warning\">{task.error_message}</font>")
            
        markdown_content = "\n\n".join(markdown_lines)
        plain_content = f"{title}. Environment: {environment}, Build: #{task.build_number or 'N/A'}"
        
        # Query active configs
        configs = db.query(NotifyConfig).filter(NotifyConfig.is_active == 1).all()
        for config in configs:
            # Check if this channel subscribes to the triggered event
            if event in config.trigger_events:
                # Run in a simple thread pool or synchronous requests
                # Here we invoke synchronously since it is running in background workers
                try:
                    send_notification_to_channel(config, title, plain_content, markdown_content)
                except Exception as channel_err:
                    logger.error(f"Failed to send release notification to channel {config.name}: {str(channel_err)}")
                
    except Exception as e:
        logger.error(f"Error in send_release_notification: {str(e)}")
    finally:
        db.close()
