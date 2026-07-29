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
            # 透明隐藏关键词，保证通过钉钉安全校验的同时卡片界面完全不显示 “安全关键词: xxx” 的字样
            text_content = f"{markdown_content}  \n<font color=\"transparent\">{keyword_val}</font>"

        # 构造钉钉官方标准的 ActionCard 交互卡片
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
                "msgtype": "actionCard",
                "actionCard": {
                    "title": title,
                    "text": text_content,
                    "singleTitle": "查看详情",
                    "singleURL": "http://localhost:3000/#/release"
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

def send_plan_start_notification(plan_id: int):
    """
    当发布计划开始触发时发送【开始通知】ActionCard 卡片。
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

        status_text = '<font color="#1890ff">开始执行</font>'

        markdown_lines = [
            '## <font color="#1890ff">发布计划任务执行报告</font>',
            "",
            f"- **任务名称**: {plan.name}",
            f"- **Job 总数**: <font color=\"#1890ff\">{total_jobs}</font> 个",
            f"- **执行状态**: {status_text}",
            f"- **Job 数量**: 待构建 <font color=\"#1890ff\">{total_jobs}</font> 个"
        ]

        markdown_content = "\n".join(markdown_lines)
        plain_content = f"任务名称: {plan.name}, Job 总数: {total_jobs} 个, 状态: 开始执行"

        for config in configs:
            if config.channel_type == "DINGTALK":
                if not getattr(plan, "notify_dingtalk", False):
                    continue
            if "start" in config.trigger_events:
                try:
                    send_notification_to_channel(config, "发布计划任务执行报告", plain_content, markdown_content, btns=None)
                except Exception as channel_err:
                    logger.error(f"Failed to send plan start notification: {str(channel_err)}")

        _sent_notifications_cache.add(cache_key)
    except Exception as e:
        logger.error(f"Error in send_plan_start_notification: {str(e)}")
    finally:
        db.close()

def send_plan_summary_notification(plan_id: int):
    """
    当整个发布计划下的所有 Job / Task 均运行完成后，发送 1 条最终汇总 ActionCard 卡片通知。
    精确具备防重/防重复机制：(plan_id) 保证只在最终跑完时发送一次。
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

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 尝试提取运行环境
        environment = "生产环境"
        if plan_tasks and plan_tasks[0].job and plan_tasks[0].job.view:
            environment = plan_tasks[0].job.view.name

        # 精准判定整体计划执行完成后的终态事件类型
        if fail_count > 0:
            plan_event = "failed"
            status_text = '<font color="#ff4d4f">执行失败</font>'
            title = "发布计划任务执行报告"
        else:
            plan_event = "success"
            status_text = '<font color="#52c41a">执行成功</font>'
            title = "发布计划任务执行报告"

        # 精美极简 ActionCard 排版格式（已移除运行环境与底部链接按钮）
        markdown_lines = [
            '## <font color="#1890ff">发布计划任务执行报告</font>',
            "",
            f"- **任务名称**: {plan.name}",
            f"- **Job 总数**: <font color=\"#1890ff\">{total_jobs}</font> 个",
            f"- **执行状态**: {status_text}",
            f"- **Job 数量**: 成功 <font color=\"#52c41a\">{success_count}</font> 个 / 失败 <font color=\"#ff4d4f\">{fail_count}</font> 个"
        ]

        markdown_content = "\n".join(markdown_lines)
        plain_content = f"任务名称: {plan.name}, Job 总数: {total_jobs} 个, 成功: {success_count} 个, 失败: {fail_count} 个"

        for config in configs:
            if config.channel_type == "DINGTALK":
                if not getattr(plan, "notify_dingtalk", False):
                    logger.info(f"Release plan {plan_id} disabled DingTalk notification, skipping channel {config.name}.")
                    continue
            
            # 严格匹配渠道订阅的触发事件：例如用户若关闭了“失败”事件且计划结果存在失败，则该渠道静默不发
            if plan_event in config.trigger_events:
                try:
                    send_notification_to_channel(config, title, plain_content, markdown_content, btns=None)
                except Exception as channel_err:
                    logger.error(f"Failed to send plan summary notification to channel {config.name}: {str(channel_err)}")

        _sent_notifications_cache.add(cache_key)
    except Exception as e:
        logger.error(f"Error in send_plan_summary_notification: {str(e)}")
    finally:
        db.close()

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
        task = db.query(ReleaseTask).filter(ReleaseTask.id == task_id).first()
        if not task:
            logger.error(f"Notification error: Task {task_id} not found.")
            return

        plan = db.query(ReleasePlan).filter(ReleasePlan.id == task.plan_id).first() if task.plan_id else None
        
        # 若任务属于某个发布计划，过程消息不再单条刷屏，统一等待发布计划的全部 n 个 Job 彻底跑完后发送 ActionCard 汇总卡片
        if plan:
            logger.info(f"Task {task_id} belongs to plan {plan.id}, skipping intermediate per-job notification to avoid noise.")
            return
        
        # 统计该计划下关联的任务总数与成功/失败数量
        if plan:
            plan_name = plan.name
            plan_tasks = db.query(ReleaseTask).filter(ReleaseTask.plan_id == plan.id).all()
            total_jobs = len(plan_tasks)
            success_count = sum(1 for t in plan_tasks if t.status == "SUCCESS")
            fail_count = sum(1 for t in plan_tasks if t.status in ["FAILED", "UNSTABLE"])
        else:
            plan_name = task.job_name
            total_jobs = 1
            success_count = 1 if event == "success" else 0
            fail_count = 1 if event == "failed" else 0
        
        # 成功与失败独立分开发送通知
        if event == "success":
            title = "发布计划通知 (成功)"
            markdown_lines = [
                "### **发布计划通知 (成功)**",
                f"**任务名称**: {plan_name}",
                f"**Job 总数**: {total_jobs}",
                f"**成功多少**: {success_count}"
            ]
            plain_content = f"任务名称: {plan_name}, Job 总数: {total_jobs}, 成功多少: {success_count}"
        elif event == "failed":
            title = "发布计划通知 (失败)"
            markdown_lines = [
                "### **发布计划通知 (失败)**",
                f"**任务名称**: {plan_name}",
                f"**Job 总数**: {total_jobs}",
                f"**失败多少**: {fail_count}"
            ]
            plain_content = f"任务名称: {plan_name}, Job 总数: {total_jobs}, 失败多少: {fail_count}"
        else:
            title = "发布计划通知 (开始)"
            markdown_lines = [
                "### **发布计划通知 (开始)**",
                f"**任务名称**: {plan_name}",
                f"**Job 总数**: {total_jobs}"
            ]
            plain_content = f"任务名称: {plan_name}, Job 总数: {total_jobs}"
            
        markdown_content = "  \n".join(markdown_lines)
        btns = None
        
        for config in configs:
            if config.channel_type == "DINGTALK":
                if plan and not getattr(plan, "notify_dingtalk", False):
                    logger.info(f"Release plan {getattr(plan, 'id', None)} disabled DingTalk notification, skipping channel {config.name}.")
                    continue
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
