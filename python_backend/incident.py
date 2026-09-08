"""
异常分类 + 优化建议。

设计原则(重要):
- **分类用规则,不用大模型。** 异常类型是确定性判断(看指标名、变化幅度、错误码),
  交给 LLM 反而不可复现、多一秒延迟、还烧钱。
- **建议才用大模型。** 且只在「已经触发告警之后」批量调用一次,
  失败/超时一律降级到内置规则建议 —— LLM 挂了只影响建议质量,绝不影响告警送达。
- 没有配置 LLM_API_KEY 时,本模块完全不发起任何网络请求,行为与之前一致。

输出分三段:根因假设 / 立即执行的动作 / 长期优化。
"""
import json
import logging
import re
import time
from typing import Dict, List, Optional

import requests

import config

log = logging.getLogger(__name__)

# ============================================================
# 1. 异常类型知识库(规则建议,LLM 不可用时的兜底)
# ============================================================
CATEGORIES: Dict[str, Dict] = {
    "stockout": {
        "name": "已断货", "emoji": "🚨",
        "causes": ["库存已归零,Listing 转为不可售", "在途补货尚未入库", "库存被其他渠道占用"],
        "actions": [
            "立即确认补货单/入库单状态,货是否已在途",
            "断货期间把广告预算下调 50%~70%,避免花费打在不可售 Listing 上",
            "可小幅提价减缓出单速度,为补货争取时间(注意别丢 Buy Box)",
        ],
        "longterm": [
            "按「日均销量 × 补货周期 + 安全库存」设补货线,提前 30~45 天下单",
            "把周转快的 SKU 加进 FOCUS_SKUS,提高监控频率",
        ],
    },
    "stockout_risk": {
        "name": "断货风险", "emoji": "⚠️",
        "causes": ["销量突然放大(可能是促销/站外/榜单效应)", "补货节奏跟不上", "库存数据同步滞后造成的假性下降"],
        "actions": [
            "先确认是真跌还是数据滞后:比对 FBA 后台可售数量",
            "按当前速度算剩余可售天数,不足 7 天立即补货",
            "同步下调广告预算,把花费集中到库存充足的款",
        ],
        "longterm": ["给该 SKU 单独设置更敏感的阈值(如 20%)", "建立安全库存水位,低于水位自动触发补货流程"],
    },
    "inventory_drop": {
        "name": "库存异常下降", "emoji": "📉",
        "causes": ["正常销售波动", "批量订单/企业采购", "库存被错分或盘亏"],
        "actions": [
            "核对近 24 小时订单量是否与降幅匹配",
            "不匹配则检查是否有盘亏、错发或库存调拨",
        ],
        "longterm": ["库存与订单做交叉校验,避免单看库存产生误报"],
    },
    "demand_drop": {
        "name": "订单量下滑", "emoji": "📉",
        "causes": [
            "Listing 被 suppression / 变狗(主图、合规、类目审核)",
            "Buy Box 丢失(被跟卖或价格失去竞争力)",
            "广告断投:预算耗尽、活动暂停、账户异常",
            "竞品降价或大促分流",
        ],
        "actions": [
            "第一步先看 Buy Box 是否还在,不在就查跟卖和价格",
            "检查 Listing 状态是否正常在售(不是 suppressed/inactive)",
            "确认广告活动是否仍在投放、预算是否提前耗尽",
        ],
        "longterm": [
            "把 Buy Box 与 Listing 状态纳入监控(Notifications API,分钟级)",
            "订单量告警与库存告警联动:库存没降而订单降,大概率是卖不动而非断货",
        ],
    },
    "price_anomaly": {
        "name": "价格异常波动", "emoji": "💰",
        "causes": ["自动调价规则触发", "被跟卖压价", "改价时填错货币/单位"],
        "actions": [
            "核对当前前台售价与预期是否一致,不一致立即改回",
            "检查是否触发了自动调价规则或被跟卖抢占",
        ],
        "longterm": ["设置价格上下限护栏,避免调价规则失控", "对核心 SKU 开启跟卖监控"],
    },
    "auth_failure": {
        "name": "鉴权失败", "emoji": "🔑",
        "causes": ["refresh_token 过期或被撤销", "AWS IAM 密钥被轮换/禁用", "卖家解除了应用授权"],
        "actions": [
            "重新走一次授权流程拿新的 refresh_token",
            "确认 IAM 用户的 AccessKey 仍有效且未被轮换",
        ],
        "longterm": ["把 token 过期做成主动告警,别等到监控失败才发现"],
    },
    "rate_limit": {
        "name": "触发接口限流", "emoji": "🐢",
        "causes": ["翻页过快", "多个进程共用同一套密钥", "调度间隔短于单轮耗时"],
        "actions": [
            "确认没有多个监控进程同时跑(crontab 是否配重了)",
            "改用增量拉取,减少翻页次数",
        ],
        "longterm": ["单轮耗时超过调度间隔 80% 时自动告警", "SKU 量级大时改用 Reports API 全量对账"],
    },
    "api_error": {
        "name": "接口调用失败", "emoji": "🔌",
        "causes": ["平台侧 5xx 或网络抖动", "请求参数不合法", "单个请求数据量过大"],
        "actions": ["已自动重试,若持续失败请检查参数与网络", "降低单页数据量或拆分请求"],
        "longterm": ["给每类错误建独立告警通道,避免和库存告警混在一起被忽略"],
    },
    "listing_failed": {
        "name": "商品上架失败", "emoji": "📦",
        "causes": ["类目必填属性缺失", "图片/合规材料不符合要求", "SKU 与已存在 Listing 冲突"],
        "actions": [
            "用 Product Type Definitions 拉取该类目 schema,核对必填字段",
            "检查主图规格与类目要求的合规材料",
        ],
        "longterm": ["上架前用 schema 做一次本地校验,不要把错误留到线上"],
    },
    "data_missing": {
        "name": "数据缺失", "emoji": "🕳",
        "causes": ["授权失效导致返回空", "接口分页/参数异常", "筛选条件把数据全过滤掉了"],
        "actions": [
            "**不要当成库存归零处理** —— 先确认接口是否返回了空数据",
            "手动跑一次 python main.py monitor 看原始输出",
        ],
        "longterm": ["把「本轮未获取到任何数据」单独列为一种异常,不要静默跳过"],
    },
    "unknown": {
        "name": "未分类异常", "emoji": "❓",
        "causes": ["未知"],
        "actions": ["人工核对原始日志"],
        "longterm": ["补充分类规则"],
    },
}


def category_label(cat: str) -> str:
    c = CATEGORIES.get(cat, CATEGORIES["unknown"])
    return f"{c['emoji']} {c['name']}"


# ============================================================
# 2. 分类(纯规则,不调大模型)
# ============================================================
def classify_by_message(message: str) -> Optional[str]:
    """按错误信息里的关键词分类,接口类问题优先级最高"""
    m = (message or "").lower()

    if any(k in m for k in ("invalid_grant", "unauthorized", "401", "403", "refresh_token", "鉴权")):
        return "auth_failure"
    if "429" in m or "too many requests" in m or "限流" in m:
        return "rate_limit"
    if any(k in m for k in ("上架失败", "listing_failed", "类目", "attribute")):
        return "listing_failed"
    if any(k in m for k in ("未获取到任何", "数据缺失", "empty", "no data")):
        return "data_missing"
    # 网络层异常是抓取失败里**最常见**的一类(连接被重置、DNS 失败、返回非 JSON),
    # 必须认出来。以前只认 "500/timeout" 这几个词,结果 Connection aborted、
    # Expecting value 这类真实高频错误全部掉进 unknown,
    # 而 unknown 的建议只有一句"人工核对日志",等于分类体系在最需要的地方失效了。
    if any(k in m for k in (
        "500", "502", "503", "504", "timeout", "超时", "接口调用失败",
        "connection", "connectionerror", "connection aborted", "reset by peer",
        "timed out", "name resolution", "ssl", "max retries", "proxyerror",
        "expecting value", "not valid json", "json", "解析",
        "抓取失败", "请求失败", "网络",
    )):
        return "api_error"
    return None


def classify(incident: Dict) -> str:
    """
    给一条异常打类型。判断顺序:错误关键词 > 指标语义 + 变化幅度。
    """
    hit = classify_by_message(incident.get("message", ""))
    if hit:
        return hit

    key = incident.get("key", "")
    value = incident.get("value")
    ratio = incident.get("change_ratio") or 0

    # 兜底:key 为 monitor 的条目是"本轮抓取/监控失败"这类事件,
    # 无论具体报什么错,它本质上都是接口类故障。以前落到 unknown,
    # 而 unknown 只有"人工核对日志"一条建议,等于把最该被处理的故障归成了哑类。
    if key == "monitor":
        return "api_error"

    if key.startswith("inventory:") or key.startswith("stock:"):
        if value == 0:
            return "stockout"
        if ratio <= -0.5:
            return "stockout_risk"
        return "inventory_drop"

    if "order" in key:
        return "demand_drop"
    if "price" in key:
        return "price_anomaly"

    return "unknown"


def enrich(incidents: List[Dict]) -> List[Dict]:
    """给每条异常补上 category 字段"""
    for inc in incidents:
        inc["category"] = inc.get("category") or classify(inc)
    return incidents


# ============================================================
# 3. 规则建议(兜底,零成本)
# ============================================================
def rule_advice(incidents: List[Dict]) -> str:
    cats = []
    for inc in incidents:
        if inc["category"] not in cats:
            cats.append(inc["category"])

    blocks = []
    for cat in cats:
        c = CATEGORIES.get(cat, CATEGORIES["unknown"])
        same = [i for i in incidents if i["category"] == cat]
        head = f"{c['emoji']} {c['name']}（{len(same)} 项）"
        if len(same) <= 3:
            head += "：" + "、".join(_brief(i) for i in same)

        lines = [head, "  可能原因：" + "；".join(c["causes"][:2])]
        lines.append("  立即处理：" + "；".join(c["actions"][:3]))
        if c["longterm"]:
            lines.append("  长期优化：" + "；".join(c["longterm"][:2]))
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


BRIEF_NAMES = {
    "order_count_last_hour": "订单量",
    "price": "价格",
    "monitor": "监控任务",
    "listing": "商品上架",
}


def _brief(inc: Dict) -> str:
    key = inc.get("key", "")
    if key.startswith("inventory:") or key.startswith("stock:"):
        return key.split(":", 1)[1]
    return BRIEF_NAMES.get(key, key)


# ============================================================
# 4. 大模型建议(可选,默认关闭)
# ============================================================
def llm_available() -> bool:
    return bool(getattr(config, "LLM_ENABLED", False) and getattr(config, "LLM_API_KEY", ""))


def _state_update():
    """
    统一走 anomaly_detector.state_update():加锁 → 读 → 改 → 写回。

    以前这里直接调 _load_state()/_save_state(),绕过了文件锁,
    等于同一个 state.json 有"加锁"和"不锁"两条写入路径 ——
    并发保护只在其中一条上生效,等于没有。
    """
    import anomaly_detector
    return anomaly_detector.state_update()


def _cooldown_key(category: str, platform: str) -> str:
    return f"_llm:{platform}:{category}"


def _filter_cooldown(pending: List[Dict]) -> List[Dict]:
    """
    在**一把锁内**完成「读冷却位点 → 筛出可调用 → 写入新位点」。

    拆成"先 load 判断、再 save 标记"两步各加一把锁是没用的:
    两个进程可能都读到"未冷却",然后都去调大模型,冷却形同虚设。
    """
    cooldown_sec = getattr(config, "LLM_COOLDOWN_MIN", 60) * 60
    now = time.time()
    keep = []

    with _state_update() as state:
        for inc in pending:
            key = _cooldown_key(inc["category"], inc.get("platform", "all"))
            last = state.get(key)
            if last:
                try:
                    if now - float(last) < cooldown_sec:
                        continue
                except (TypeError, ValueError):
                    pass
            state[key] = str(now)
            keep.append(inc)

    return keep


def llm_advice(incidents: List[Dict]) -> Optional[str]:
    """
    批量把异常交给大模型,要「根因 + 立即动作 + 长期优化」。
    任何异常都返回 None,调用方会自动退回规则建议。
    """
    if not llm_available() or not incidents:
        return None

    pending = _filter_cooldown(incidents)
    if not pending:
        log.info("所有异常类型都在 LLM 冷却期内,跳过调用")
        return None

    pending = pending[: getattr(config, "LLM_MAX_INCIDENTS", 20)]
    detail = "\n".join(
        "- [{platform}] 类型：{cat} | 对象：{obj} | 详情：{msg}".format(
            platform=i.get("platform", "all"),
            cat=category_label(i["category"]),
            obj=_brief(i),
            msg=i.get("message", ""),
        )
        for i in pending
    )

    prompt = (
        "你是资深跨境电商运营顾问(亚马逊 + 拼多多)。以下是自动化监控刚检测到的异常。\n\n"
        "请输出三部分,不要复述异常内容,不要客套话,直接给可执行结论:\n"
        "【根因】最可能的 1-2 个原因\n"
        "【立即处理】按优先级给 3 条具体动作\n"
        "【长期优化】1-2 条,避免再次发生\n"
        "总字数控制在 300 字以内。\n\n"
        f"异常清单:\n{detail}"
    )

    try:
        resp = requests.post(
            f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {config.LLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": config.LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 700,
            },
            timeout=getattr(config, "LLM_TIMEOUT", 20),
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        # 冷却位点在 _filter_cooldown 里已经写过了:
        # 放在调用成功后才写的话,调用失败/超时的那些轮次会反复重试,冷却就没意义了。
        return content

    except Exception as e:
        # 降级:LLM 出问题不影响告警本身
        log.warning("大模型建议生成失败,回退到规则建议: %s", e)
        return None


def parse_advice(text: str) -> Dict[str, str]:
    """把模型返回的三段文本拆成结构化字段,拆不出来就整段放在 raw 里"""
    out = {"root_cause": "", "actions": "", "longterm": "", "raw": text or ""}
    if not text:
        return out

    pattern = r"【(.+?)】"
    parts = re.split(pattern, text)
    # re.split 后形如 ['', '根因', '内容', '立即处理', '内容', ...]
    mapping = {"根因": "root_cause", "可能原因": "root_cause", "原因": "root_cause",
               "立即处理": "actions", "处理": "actions", "动作": "actions",
               "长期优化": "longterm", "优化": "longterm"}
    for idx in range(1, len(parts) - 1, 2):
        title, body = parts[idx].strip(), parts[idx + 1].strip()
        for k, field in mapping.items():
            if k in title:
                out[field] = (out[field] + "\n" + body).strip() if out[field] else body
                break
    return out


# ============================================================
# 5. 组装最终报告
# ============================================================
def build_report(incidents: List[Dict]) -> Dict:
    """
    输入异常列表,输出:
      { "summary": 一句话概览, "category_stats": 各类数量,
        "rule_advice": 规则建议(必有), "llm_advice": 大模型建议(可能为 None),
        "final_advice": 最终采用的建议, "source": "llm" | "rule" }
    """
    if not incidents:
        return {"summary": "无异常", "category_stats": {}, "rule_advice": "",
                "llm_advice": None, "final_advice": "", "source": "rule"}

    enrich(incidents)

    stats: Dict[str, int] = {}
    for inc in incidents:
        stats[inc["category"]] = stats.get(inc["category"], 0) + 1

    names = "、".join(
        f"{category_label(c)}×{n}" for c, n in sorted(stats.items(), key=lambda x: -x[1])
    )
    summary = f"共 {len(incidents)} 项异常：{names}"

    rule_text = rule_advice(incidents)
    llm_text = llm_advice(incidents)

    return {
        "summary": summary,
        "category_stats": stats,
        "rule_advice": rule_text,
        "llm_advice": llm_text,
        "final_advice": llm_text or rule_text,
        "source": "llm" if llm_text else "rule",
    }


def format_alert(report: Dict, header: str = "🚨 电商数据监控告警",
                 max_len: int = 1800) -> str:
    """
    把报告渲染成适合企业微信/钉钉的文本。
    企业微信 text 消息上限 2048 字节,这里留足余量做截断,避免整条消息发送失败。
    """
    src = "🤖 大模型建议" if report["source"] == "llm" else "📋 规则建议"
    text = (
        f"{header}\n\n"
        f"■ 异常概览\n{report['summary']}\n\n"
        f"■ 处理建议（{src}）\n{report['final_advice']}"
    )
    # ⚠️ 必须按**字节**截断:max_len 是字节上限(企业微信 text 2048 字节),
    # 而中文一个字占 3 字节。如果按字符切片,max_len=1800 个字符 ≈ 5400 字节,
    # 仍然是超限的,消息照样发不出去 —— 判断和切片要用同一把尺子。
    encoded = text.encode("utf-8")
    if len(encoded) > max_len:
        suffix = "\n…(内容过长已截断,完整建议见运行日志)"
        suffix_len = len(suffix.encode("utf-8"))
        # 给后缀留位置;从字节边界切,errors="ignore" 丢弃被切断的多字节字符
        keep = max(max_len - suffix_len, 0)
        text = encoded[:keep].decode("utf-8", errors="ignore") + suffix
    return text
