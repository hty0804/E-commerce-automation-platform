"""
告警通知 —— 企业微信群机器人 + 钉钉群机器人。
两者都是「群机器人 webhook」模式:在对应的群里添加机器人,拿到 webhook 地址填进环境变量即可,
不需要单独注册应用。
"""
import base64
import hashlib
import hmac
import logging
import time
import urllib.parse

import requests

import config

log = logging.getLogger(__name__)


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


def _dedup_pass(key: str, cooldown_min: int) -> bool:
    """
    同一类告警在冷却期内只发一次。

    为什么需要:像"监控任务耗时过长"这种慢性问题,每一轮都会触发,
    一小时一条能提醒人处理;每小时都发一条,三天后所有人都会把群静音 ——
    刷屏导致的漏看,比少发一条严重得多。

    :return: True 表示可以发(并已记录本次发送时间),False 表示应跳过。
    """
    if not key or cooldown_min <= 0:
        return True

    import anomaly_detector  # 延迟导入,避免与 config 以外的模块形成循环依赖

    now = time.time()
    state_key = f"_alert:{key}"
    with anomaly_detector.state_update() as state:
        last = state.get(state_key)
        if last:
            try:
                if now - float(last) < cooldown_min * 60:
                    return False
            except (TypeError, ValueError):
                pass
        state[state_key] = str(now)
    return True


def send_alert(content: str, dedup_key: str = None,
               cooldown_min: int = None) -> dict:
    """
    哪个渠道配置了 webhook 就发哪个,两个都配置了就都发。

    ⚠️ 每个渠道必须独立 try/except:
    告警是监控链路的最后一环,如果企业微信发失败(网络抖动 / 群机器人被禁 / 返回非 2xx)
    就抛异常,会导致钉钉也不发了,而且异常会一路冒泡到 run_hourly_monitor(),
    连"至少打印到日志"这个兜底都执行不到 —— 一次外部抖动就让整轮监控看起来像没跑。
    这里的原则:**告警失败只能记日志,不能影响主流程,更不能影响另一个渠道**。

    :param dedup_key:    去重键。同一个 key 在 cooldown_min 分钟内只发一次。
    :param cooldown_min: 去重冷却(分钟),None 时用 config.ALERT_DEDUPE_MIN。
    :return: {"wecom": "ok"/"failed:<原因>", "dingtalk": ...};
             被去重跳过时返回 {"skipped": "dedup:<key>"}。
    """
    if dedup_key:
        cd = config.ALERT_DEDUPE_MIN if cooldown_min is None else cooldown_min
        try:
            if not _dedup_pass(dedup_key, cd):
                log.info("告警去重命中,本条跳过: %s", dedup_key)
                return {"skipped": f"dedup:{dedup_key}"}
        except Exception as exc:
            # 去重本身出问题不能拦住告警:宁可多发一条,也不要把真故障吞掉
            log.warning("告警去重检查失败(%s),照常发送: %s", dedup_key, exc)

    results = {}

    if config.WECOM_WEBHOOK_URL:
        try:
            send_wecom_alert(content)
            results["wecom"] = "ok"
        except Exception as exc:  # noqa: BLE001 - 告警失败绝不能中断主流程
            results["wecom"] = f"failed: {exc}"
            log.warning("企业微信告警发送失败: %s", exc)

    if config.DINGTALK_WEBHOOK_URL:
        try:
            send_dingtalk_alert(content)
            results["dingtalk"] = "ok"
        except Exception as exc:  # noqa: BLE001 - 同上
            results["dingtalk"] = f"failed: {exc}"
            log.warning("钉钉告警发送失败: %s", exc)

    if not results:
        print(f"[未配置任何告警渠道,仅打印] {content}")
    elif all(v != "ok" for v in results.values()):
        # 配了渠道但全挂了:至少把内容留在日志里,否则这条告警就彻底丢了
        log.error("所有告警渠道均发送失败: %s", results)
        print(f"[所有告警渠道均失败,仅打印] {content}")

    return results
