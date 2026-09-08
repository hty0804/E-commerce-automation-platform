"""
告警通知 —— 企业微信群机器人 + 钉钉群机器人。
两者都是「群机器人 webhook」模式:在对应的群里添加机器人,拿到 webhook 地址填进环境变量即可,
不需要单独注册应用。
"""
import base64
import hashlib
import hmac
import time
import urllib.parse

import requests

import config


def send_wecom_alert(content: str) -> dict:
    """企业微信群机器人 - 发文本消息"""
    if not config.WECOM_WEBHOOK_URL:
        raise RuntimeError("未配置 WECOM_WEBHOOK_URL")
    payload = {"msgtype": "text", "text": {"content": content}}
    resp = requests.post(config.WECOM_WEBHOOK_URL, json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()


def send_dingtalk_alert(content: str) -> dict:
    """
    钉钉群机器人 - 发文本消息。
    如果机器人开启了「加签」安全设置,需要用 DINGTALK_SECRET 计算 sign 并拼到 webhook 后面;
    如果用的是「自定义关键词」或「IP 白名单」,可以不设置 DINGTALK_SECRET。
    """
    if not config.DINGTALK_WEBHOOK_URL:
        raise RuntimeError("未配置 DINGTALK_WEBHOOK_URL")

    url = config.DINGTALK_WEBHOOK_URL
    if config.DINGTALK_SECRET:
        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{config.DINGTALK_SECRET}".encode("utf-8")
        hmac_code = hmac.new(
            config.DINGTALK_SECRET.encode("utf-8"), string_to_sign, digestmod=hashlib.sha256
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        url = f"{url}&timestamp={timestamp}&sign={sign}"

    payload = {"msgtype": "text", "text": {"content": content}}
    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()


def send_alert(content: str) -> None:
    """哪个渠道配置了 webhook 就发哪个,两个都配置了就都发"""
    sent = False
    if config.WECOM_WEBHOOK_URL:
        send_wecom_alert(content)
        sent = True
    if config.DINGTALK_WEBHOOK_URL:
        send_dingtalk_alert(content)
        sent = True
    if not sent:
        print(f"[未配置任何告警渠道,仅打印] {content}")
