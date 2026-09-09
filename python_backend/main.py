"""
主入口。

三个核心功能:
1. gen_listings(products)        —— 用大模型把中文商品信息生成可直接上架的 Listing
2. list_new_products(products)   —— 批量上架商品到亚马逊 / 拼多多
3. run_hourly_monitor()          —— 抓取两个平台的关键数据,做异常检测,触发告警

调度方式(二选一):
A) 系统 crontab(推荐,简单可靠)。**两行都要加**,第二行是死信检查,
   否则监控挂了不会有人知道:
   0 * * * * cd /path/to/ecommerce-sop-admin/python_backend && /usr/bin/python3 main.py monitor >> monitor.log 2>&1
   */10 * * * * cd /path/to/ecommerce-sop-admin/python_backend && /usr/bin/python3 main.py health >> monitor.log 2>&1

   注意 `*/10` 与后面的 `*` 之间必须有空格;写成 `*/10*` 只有 4 个字段,
   cron 会报 bad minute 并拒绝这一整行 —— 表现出来就是"配了但从来没执行过"。

B) 用 APScheduler 常驻进程调度(如果你想让它作为一个服务一直跑着):
   pip install apscheduler
   然后取消下面 run_as_daemon() 里的注释并运行 `python main.py daemon`
"""
import datetime
import json
import logging
import sys
import time
import traceback
from decimal import Decimal, InvalidOperation

from amazon_client import AmazonSPAPIClient
from pdd_client import PinduoduoClient
import anomaly_detector
import history
import incident
import alerts
import listing_gen
import config

log = logging.getLogger(__name__)


def _setup_logging() -> None:
    """
    配置日志。

    以前整个后端从没调用过 logging.basicConfig()(只有 listing_gen 的 __main__ 里配过),
    结果 `python main.py monitor` 运行时:
      - INFO 级日志全部被丢弃(root logger 默认只输出 WARNING 以上)
      - 没有时间戳、没有级别、没有模块名,crontab 重定向出来的日志没法排查
    监控系统出问题时,日志是唯一的取证手段,所以这里必须显式配置。
    """
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger().setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))


def _to_fen(price) -> int:
    """
    元 → 分。

    不能写成 int(float(price) * 100):浮点误差会让结果少 1 分 ——
        int(1.15 * 100)  == 114   (1.15*100 实际是 114.99999999999999)
        int(8.37 * 100)  == 836
    低客单价商品上这是直接亏钱,而且极难被发现。
    Decimal 先把输入当**字符串**解析,绕开二进制浮点误差。
    """
    if price is None or price == "":
        return 0
    try:
        return int((Decimal(str(price)) * 100).to_integral_value())
    except (InvalidOperation, ValueError):
        log.warning("无法解析价格 %r,按 0 处理", price)
        return 0


def gen_listings(products: list, platform: str = "amazon") -> list:
    """
    批量生成 Listing(这是大模型在这套方案里最划算的用法)。

    products 每项字段(只有 name 必填,给得越全生成质量越高):
      {
        "sku": "AMZ-1001",
        "name": "无线蓝牙耳机 主动降噪 超长续航",   # 中文商品名(必填)
        "brand": "SoundCore",                        # 留空则用 config.LISTING_BRAND
        "category": "3C数码",                        # 决定类目 schema 与必填属性
        "features": "降噪 35dB；续航 32 小时",       # 卖点,分号/换行分隔
        "specs": {"color": "Black"},                 # 规格参数,直接进 attributes
        "keywords": "wireless earbuds",              # 参考关键词
        "audience": "通勤人群",
        "price": 39.9
      }
    """
    if config.LISTING_BRAND:
        for p in products:
            p.setdefault("brand", config.LISTING_BRAND)

    results = listing_gen.batch_generate(products, platform)
    for r in results:
        print(listing_gen.render_preview(r))
        print("-" * 60)

    n_llm = sum(1 for r in results if r["source"] == "llm")
    n_ok = sum(1 for r in results if not r["has_error"])
    print(f"[stat] 共 {len(results)} 条 | 通过校验 {n_ok} 条 | "
          f"来源: 大模型 {n_llm} / 规则草稿 {len(results) - n_llm}")
    return results


def generate_and_publish(products: list, platform: str = "amazon"):
    """
    生成 -> 本地校验 -> 转 payload -> (可选)提交上架。
    默认只生成不提交(LISTING_AUTO_PUBLISH=false):文案写错可以改,错误上架要清理的成本高得多。
    """
    results = gen_listings(products, platform)
    payloads, skipped = [], []

    for r, p in zip(results, products):
        if r["has_error"] and config.LISTING_SKIP_ON_ERROR:
            skipped.append(r["sku"])
            reasons = "；".join(i["msg"] for i in r["issues"] if i["level"] == "error")
            print(f"[skip] {r['sku']} 校验未通过,跳过上架: {reasons}")
            alerts.send_alert(f"⚠️ Listing 校验未通过,已跳过上架: {r['sku']}\n{reasons}")
            continue

        if platform == "amazon":
            schema = listing_gen.schema_for(p.get("category", ""))
            payloads.append({
                "platform": "amazon",
                "sku": r["sku"],
                "payload": listing_gen.to_spapi_payload(r["listing"], r["sku"], schema["product_type"]),
            })
        else:
                payloads.append({
                    "platform": "pdd",
                    "payload": listing_gen.to_pdd_payload(
                        r["listing"],
                        goods_name=p.get("name", ""),
                        price_fen=_to_fen(p.get("price")),
                        quantity=int(p.get("quantity", 0) or 0),
                    ),
                })

    if skipped:
        print(f"[stat] 跳过 {len(skipped)} 条: {', '.join(skipped)}")

    if not config.LISTING_AUTO_PUBLISH:
        print("[dry-run] LISTING_AUTO_PUBLISH=false,只生成不提交。"
              "确认文案无误后在 .env 里打开该开关即可真正上架。")
        with open("listing_payloads.json", "w", encoding="utf-8") as f:
            json.dump(payloads, f, ensure_ascii=False, indent=2)
        print("[dry-run] payload 已写入 listing_payloads.json,可直接喂给 list_new_products()")
        return payloads

    return list_new_products(payloads)


def list_new_products(products: list) -> list:
    """
    products 示例:
    [
      {"platform": "amazon", "sku": "SKU123", "payload": {...}},
      {"platform": "pdd", "payload": {"goods_name": "...", "price": 9900, ...}},
    ]
    具体 payload 字段需按各平台商品接口的要求组装(标题、类目、价格、库存、图片等)。
    """
    amazon = AmazonSPAPIClient()
    pdd = PinduoduoClient()
    results = []
    failures = []

    for item in products:
        name = item.get("sku") or item.get("payload", {}).get("goods_name", "未知商品")
        try:
            if item["platform"] == "amazon":
                # 亚马逊是 PUT /items/{sellerId}/{sku},天然幂等,重复提交只是覆盖
                res = amazon.create_or_update_listing(item["sku"], item["payload"])
            elif item["platform"] == "pdd":
                res = _pdd_add_once(pdd, item, name)
            else:
                raise ValueError(f"未知平台: {item['platform']}")
            results.append({"item": item, "status": "success", "response": res})
        except Exception as e:
            results.append({"item": item, "status": "failed", "error": str(e)})
            failures.append((name, str(e)))
            log.error("商品上架失败: %s - %s", name, e)

    # 汇总成**一条**告警,而不是每个失败一条。
    # 批量上架一次失败几十个是很常见的(比如同一批缺同一个必填属性),
    # 一条一个会把群刷爆,真正需要人看到的那条反而被淹掉。
    if failures:
        lines = "\n".join(f"· {n}: {err[:120]}" for n, err in failures[:20])
        more = f"\n…另有 {len(failures) - 20} 条,详见运行日志" if len(failures) > 20 else ""
        alerts.send_alert(
            f"⚠️ 商品上架失败 {len(failures)} 条(共 {len(products)} 条)\n{lines}{more}",
            dedup_key="listing_failed_batch",
        )

    return results


def _pdd_add_once(pdd, item: dict, name: str) -> dict:
    """
    拼多多上架的幂等保护。

    pdd.goods.add **不是**幂等的:请求其实成功了、只是响应超时或解析失败时,
    直接重试会**再建一个一模一样的商品**,清理起来很麻烦。
    所以这里把"已成功创建的商品 id"记进 state,重试前先查,命中就跳过。
    """
    key = (item.get("idempotency_key")
           or item.get("sku")
           or (item.get("payload") or {}).get("goods_name")
           or "")
    if not key:
        # 没有可用标识就无法做幂等判断,宁可照常提交并记日志,也不要默默跳过
        log.warning("上架项缺少幂等标识,无法做重复保护: %s", name)
        return pdd.add_goods(item["payload"])

    state_key = f"_pdd_goods:{key}"
    with anomaly_detector.state_update() as state:
        existing = state.get(state_key)
        if existing:
            log.warning("检测到 %s 已创建过(goods_id=%s),跳过以避免重复建商品", name, existing)
            return {"skipped": True, "goods_id": existing, "reason": "idempotent"}

    res = pdd.add_goods(item["payload"])
    goods_id = (res or {}).get("goods_id") or (res or {}).get("goods_add_response", {}).get("goods_id")
    if goods_id:
        with anomaly_detector.state_update() as state:
            state[state_key] = str(goods_id)
    return res


def _utc_iso(dt: datetime.datetime) -> str:
    """转成 SP-API 要求的 ISO8601 UTC 格式(带 Z)"""
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _next_inventory_window(now: datetime.datetime):
    """
    决定这一轮是「全量」还是「增量」:
    - 首次运行 / 距上次全量超过 INVENTORY_FULL_SCAN_HOURS  -> 全量
    - 其余情况                                              -> 增量(只拉有变更的 SKU)
    增量是大数据量下最有效的一招:上千次翻页通常能降到几十次。
    """
    cursor = anomaly_detector.get_sync_cursor("amazon_inventory")
    if not cursor:
        return None, "全量(首次运行)"

    try:
        last = datetime.datetime.fromisoformat(cursor.replace("Z", "+00:00"))
    except ValueError:
        return None, "全量(位点解析失败)"

    if (now - last).total_seconds() >= config.INVENTORY_FULL_SCAN_HOURS * 3600:
        return None, "全量(定期全量对账)"

    # 回看冗余:防止时钟偏差或平台写入延迟导致漏掉变更
    start = last - datetime.timedelta(minutes=config.INVENTORY_LOOKBACK_MINUTES)
    return _utc_iso(start), "增量"


def run_hourly_monitor() -> None:
    started = time.time()
    now = datetime.datetime.now(datetime.timezone.utc)
    checks = []
    error_messages = []
    stats = {"amazon_skus": 0, "pdd_goods": 0, "orders": 0}

    # ---------- 亚马逊:库存 + 订单量 ----------
    try:
        amazon = AmazonSPAPIClient()

        start_dt, mode = _next_inventory_window(now)
        summaries = amazon.get_inventory_summaries(
            start_datetime=start_dt, max_pages=config.INVENTORY_MAX_PAGES
        )

        # 重点 SKU:每轮都单独查一次(单次最多 50 个),不受增量窗口影响
        if config.FOCUS_SKUS:
            for i in range(0, len(config.FOCUS_SKUS), 50):
                summaries.extend(
                    amazon.get_inventory_summaries(seller_skus=config.FOCUS_SKUS[i:i + 50])
                )

        # 按 SKU 去重(增量 + 重点 SKU 可能重复返回同一个 SKU)
        by_sku = {}
        for s in summaries:
            sku = s.get("sellerSku")
            if sku:
                # 注意不能用 s.get("totalQuantity", 0):默认值只在**键不存在**时生效,
                # 键存在但值为 null 时拿到的是 None。SP-API 对无 FBA 库存的 SKU 就会返回 null,
                # None 传进检测逻辑会触发 TypeError,把整个监控进程打挂(实测复现)。
                # 用 `or 0` 兜底,确保拿到的一定是数字。
                by_sku[sku] = s.get("totalQuantity") or 0

        for sku, qty in by_sku.items():
            checks.append({
                "platform": "amazon", "key": f"inventory:{sku}", "value": qty,
                "threshold": config.INVENTORY_DROP_THRESHOLD, "direction": "drop",
            })
        stats["amazon_skus"] = len(by_sku)

        # 一轮下来一个 SKU 都没拿到,通常是接口/权限出了问题,不要当成"一切正常"
        if not by_sku:
            error_messages.append("⚠️ 亚马逊本轮未获取到任何库存数据,请检查授权或接口状态")
        elif not getattr(summaries, "complete", True):
            # 翻页被 max_pages 截断:数据不完整,**绝不能**推进同步游标。
            # 否则下一轮增量从"现在"开始,没拉到的那批 SKU 就永远不会被增量覆盖,
            # 表现为"静默地少监控一批货",而且不报错、不告警。
            error_messages.append(
                f"⚠️ 亚马逊库存翻页达到上限 {config.INVENTORY_MAX_PAGES} 页,本轮数据不完整,"
                f"已保留同步游标不推进。请调大 INVENTORY_MAX_PAGES,或改用 Reports API 做全量对账。"
            )
        else:
            anomaly_detector.set_sync_cursor("amazon_inventory", _utc_iso(now))

        # 订单:getOrders 限流极低(约 1 次/分钟),一小时一次是安全的,别再提高频率
        one_hour_ago = _utc_iso(now - datetime.timedelta(hours=1))
        orders = amazon.get_recent_orders(one_hour_ago)
        checks.append({
            "platform": "amazon", "key": "order_count_last_hour", "value": len(orders),
            "threshold": config.ORDER_COUNT_DROP_THRESHOLD, "direction": "drop",
            # 基线低于 ORDER_MIN_PREVIOUS 单不做环比:1 单变 0 单就是 -100%,
            # 夜间低流量时段会天天误报,报多了人就再也不看告警了。
            "min_previous": config.ORDER_MIN_PREVIOUS,
        })
        stats["orders"] = len(orders)

        print(f"[amazon] 库存拉取模式={mode},本轮 {stats['amazon_skus']} 个 SKU,订单 {stats['orders']} 单")
    except Exception as e:
        error_messages.append(f"⚠️ 亚马逊数据抓取失败: {e}")

    # ---------- 拼多多:库存 ----------
    try:
        pdd = PinduoduoClient()
        goods = pdd.iter_goods_list()  # 自动翻页,避免只拿到第一页
        for g in goods:
            checks.append({
                # 同亚马逊:`or 0` 兜底,避免字段为 null 时把 None 传进检测逻辑导致进程崩溃
                "platform": "pdd", "key": f"stock:{g.get('goods_id')}", "value": g.get("quantity") or 0,
                "threshold": config.INVENTORY_DROP_THRESHOLD, "direction": "drop",
            })
        stats["pdd_goods"] = len(goods)
        if not goods:
            error_messages.append("⚠️ 拼多多本轮未获取到任何商品数据,请检查授权或接口状态")
        print(f"[pdd] 本轮 {stats['pdd_goods']} 个商品")
    except Exception as e:
        error_messages.append(f"⚠️ 拼多多数据抓取失败: {e}")

    # ---------- 异常检测 + 分类 + 建议 ----------
    # 整段包 try:这一段以前是裸奔的,任何异常(比如状态文件损坏、检测逻辑踩到意外数据)
    # 都会让进程直接退出 —— 而它是监控链路的最后一步,一崩就等于"这一轮什么都没发生",
    # 既没告警也没日志,外面看起来监控一直在跑,实际上早就死了。
    elapsed = time.time() - started
    try:
        # suppressed:被"同时段基线"抑制掉的环比异常。必须收上来并打出来 ——
        # 否则哪天检测逻辑写坏了、把所有告警都抑制掉,你看到的只是"群里安静了",
        # 跟"业务真的健康"长得一模一样,等真出事时才发现监控早就瞎了。
        suppressed: list = []
        incidents = anomaly_detector.collect_incidents(checks, suppressed=suppressed)
        if suppressed:
            print(f"[baseline] 本轮 {len(suppressed)} 项环比异常被同时段基线判定为正常波动,已抑制:")
            for s in suppressed:
                print(f"  - [{s['platform']}] {s['key']}: {s['previous']} → {s['value']}"
                      f" | 抑制原因: {s['reason']}")

        # 抓取失败类错误也纳入异常体系(会被归类为鉴权失败 / 限流 / 接口异常 / 数据缺失)
        for msg in error_messages:
            incidents.append({
                "platform": "all", "key": "monitor", "value": None, "previous": None,
                "change_ratio": None, "threshold": 0, "direction": "drop", "message": msg,
            })

        report = incident.build_report(incidents)

        if incidents:
            content = incident.format_alert(report)
            alerts.send_alert(content)
            print(content)
        else:
            print(f"[{datetime.datetime.now()}] 本次监控无异常,共检查 {len(checks)} 项指标")
    except Exception:
        # 到这里已经抓到数据了,绝不能因为"报告生成失败"就把整轮结果丢掉
        log.exception("异常检测/告警阶段失败,本轮告警未能发出(数据抓取已完成)")
        alerts.send_alert(
            "⚠️ 监控任务在生成告警阶段异常,本轮告警未能发出,请检查运行日志",
            dedup_key="monitor_report_failed",
        )

    # 心跳:记下"本轮成功跑完"的时间,供 `python main.py health` 判断监控是否还活着。
    # 监控系统自己挂了却没人知道,是这类系统最危险的失效模式 —— 所以必须留个死信开关。
    _mark_heartbeat(now)

    # 历史库清理:每天只在凌晨那一轮做一次。
    # 每小时都跑没意义 —— DELETE + 可能的 VACUUM 要扫全表,而多留 23 小时的数据毫无代价。
    if datetime.datetime.now().hour == 3:
        try:
            deleted = history.prune()
            if deleted:
                print(f"[history] 已清理 {deleted} 条超过 {config.HISTORY_RETENTION_DAYS} 天的历史")
        except Exception:
            log.warning("清理指标历史失败(不影响本轮监控)", exc_info=True)

    print(f"[stat] 耗时 {elapsed:.1f}s | 亚马逊 {stats['amazon_skus']} SKU / "
          f"拼多多 {stats['pdd_goods']} 商品 / 订单 {stats['orders']} 单")

    # 单轮耗时逼近调度间隔时要预警,否则任务会越积越多
    if elapsed > config.MONITOR_INTERVAL_MIN * 60 * 0.8:
        alerts.send_alert(
            f"⚠️ 监控任务耗时 {elapsed:.0f}s,已接近调度间隔 {config.MONITOR_INTERVAL_MIN} 分钟,"
            f"建议缩短全量间隔或改用增量/分片策略",
            # 慢性问题:加去重,否则每轮一条,几天后所有人都会把群静音
            dedup_key="monitor_slow",
            cooldown_min=max(config.MONITOR_INTERVAL_MIN * 6, 60),
        )


def _mark_heartbeat(now: datetime.datetime = None) -> None:
    """记录本轮监控成功完成的时间戳(供 health 命令判断监控是否还活着)"""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    try:
        with anomaly_detector.state_update() as state:
            state["_last_run"] = _utc_iso(now)
    except Exception:
        log.warning("写入心跳失败(不影响本轮监控结果)", exc_info=True)


def check_health() -> int:
    """
    死信检查:确认监控还在按时跑。

    用法(crontab 里和 monitor 并列加一条):
        */10 * * * * cd /path/to/ecommerce-sop-admin/python_backend && \
            /usr/bin/python3 main.py health >> monitor.log 2>&1

    为什么是 */10 而不是和 monitor 一样的整点:
    两条都排在整点的话,health 有可能**先于** monitor 执行,读到的是上一轮的心跳,
    于是每次都在临界点上抖动、偶尔误报。错开成每 10 分钟一次就稳了。
    (`*/10` 与后面的 `*` 之间必须有空格,写成 `*/10*` 只有 4 个字段,
    cron 会报 bad minute 并拒绝这一行 —— 表现是"配了却从来没跑过"。)

    为什么需要它:如果 monitor 进程因为任何原因不再被调用(crontab 被覆盖、
    机器重启后 cron 没起来、进程被 OOM kill),系统不会有任何异常 ——
    它只是"安静地不再监控"。这类失效只能靠外部心跳发现。

    :return: 0 健康 / 1 不健康
    """
    with anomaly_detector.state_lock():
        state = anomaly_detector._load_state()
    last = state.get("_last_run")
    if not last:
        msg = "⚠️ 监控心跳检查:从未记录到成功运行的监控任务,请确认 crontab/守护进程是否已配置"
        print(msg)
        alerts.send_alert(msg, dedup_key="heartbeat_missing", cooldown_min=60)
        return 1

    try:
        last_dt = datetime.datetime.fromisoformat(last.replace("Z", "+00:00"))
    except ValueError:
        msg = f"⚠️ 监控心跳检查:心跳时间格式异常({last}),请检查 state.json"
        print(msg)
        alerts.send_alert(msg, dedup_key="heartbeat_bad", cooldown_min=60)
        return 1

    age_min = (datetime.datetime.now(datetime.timezone.utc) - last_dt).total_seconds() / 60
    # 容忍 2.5 个周期:一轮偶发失败/重试不应该触发死信告警
    limit = max(config.MONITOR_INTERVAL_MIN * 2.5, 30)
    if age_min > limit:
        msg = (
            f"⚠️ 监控心跳检查:距上次成功运行已 {age_min:.0f} 分钟"
            f"(超过阈值 {limit:.0f} 分钟),监控可能已停止,请检查 crontab / 守护进程 / 机器状态"
        )
        print(msg)
        alerts.send_alert(msg, dedup_key="heartbeat_stale", cooldown_min=60)
        return 1

    print(f"[health] 正常,距上次成功运行 {age_min:.1f} 分钟")
    return 0


def run_as_daemon() -> None:
    """可选:用 APScheduler 常驻进程,每小时整点触发一次监控"""
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler(timezone="Asia/Shanghai")
    # max_instances=1:上一轮没跑完就不开新的,避免任务叠加把限流打爆
    # coalesce=True: 积压的轮次只补跑一次,而不是排队补跑 N 次
    scheduler.add_job(
        run_hourly_monitor, "cron", minute=0,
        max_instances=1, coalesce=True, misfire_grace_time=300,
    )
    print("监控进程已启动,每小时整点执行一次...")
    scheduler.start()


def _main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "monitor":
        run_hourly_monitor()
    elif command == "health":
        return check_health()
    elif command == "daemon":
        run_as_daemon()
    elif command == "genlist":
        # python main.py genlist [input.json] [amazon|pdd]
        src = sys.argv[2] if len(sys.argv) > 2 else "listing_input.json"
        plat = sys.argv[3] if len(sys.argv) > 3 else "amazon"
        try:
            with open(src, encoding="utf-8") as f:
                items = json.load(f)
        except FileNotFoundError:
            print(f"找不到输入文件 {src}。格式示例见 README「生成 Listing」。")
            return 1
        generate_and_publish(items, plat)
    elif command == "history":
        return _history_cmd(sys.argv[2:])
    else:
        print(
            "用法:\n"
            "  python main.py monitor                # 手动/被 crontab 调用,跑一次监控\n"
            "  python main.py health                 # 死信检查:监控是否还在按时跑\n"
            "  python main.py daemon                 # 常驻进程,内置每小时调度\n"
            "  python main.py genlist input.json     # 用大模型生成 Listing 并本地校验\n"
            "  python main.py genlist input.json pdd # 生成拼多多中文 Listing\n"
            "  python main.py history [子命令]       # 查看/清理指标历史库(基线用)\n"
            "      series                            # 列出所有指标序列与样本数\n"
            "      recent <platform> <key> [n]       # 看某个指标最近的取值\n"
            "      baseline <platform> <key>         # 看该指标当前时段的基线值\n"
            "      prune                             # 清理超过保留期的历史"
        )
    return 0


def _history_cmd(args: list) -> int:
    """指标历史的查看/维护入口。查不到东西时要把原因说清楚,别只打印个空表格。"""
    sub = args[0] if args else "series"

    if not history.is_available():
        print(f"指标历史库不可用或还没有数据: {config.HISTORY_DB}\n"
              f"先跑几轮 `python main.py monitor` 攒样本,基线对比才会生效。")
        return 1

    if sub == "series":
        rows = history.series()
        if not rows:
            print("历史库里还没有任何数据。")
            return 0
        print(f"{'platform':<10} {'key':<28} {'样本数':>7}  最早 / 最新")
        for platform, key, n, first, last in rows:
            f = datetime.datetime.fromtimestamp(first).strftime("%m-%d %H:%M") if first else "-"
            t = datetime.datetime.fromtimestamp(last).strftime("%m-%d %H:%M") if last else "-"
            print(f"{platform:<10} {key:<28} {n:>7}  {f} / {t}")
        return 0

    if sub == "recent":
        if len(args) < 3:
            print("用法: python main.py history recent <platform> <key> [n]")
            return 1
        limit = int(args[3]) if len(args) > 3 else 20
        rows = history.recent(args[1], args[2], limit)
        if not rows:
            print(f"没有 {args[1]} / {args[2]} 的历史。")
            return 0
        for ts, value in rows:
            print(f"  {datetime.datetime.fromtimestamp(ts).strftime('%m-%d %H:%M')}  {value:g}")
        return 0

    if sub == "baseline":
        if len(args) < 3:
            print("用法: python main.py history baseline <platform> <key>")
            return 1
        base, n = history.baseline_with_fallback(args[1], args[2])
        if base is None:
            print(f"{args[1]} / {args[2]} 当前时段样本不足(只有 {n} 条,"
                  f"需要 {config.BASELINE_MIN_SAMPLES} 条),暂不做基线抑制。")
            return 0
        print(f"{args[1]} / {args[2]} 当前时段基线 = {base:g}(基于 {n} 条样本)")
        return 0

    if sub == "prune":
        deleted = history.prune()
        print(f"已清理 {deleted} 条超过 {config.HISTORY_RETENTION_DAYS} 天的历史")
        return 0

    print(f"未知的 history 子命令: {sub}")
    return 1


if __name__ == "__main__":
    _setup_logging()
    try:
        sys.exit(_main())
    except SystemExit:
        raise
    except Exception:
        # 最后一道防线:任何漏网的异常都要留下完整堆栈再退出。
        # 以前是直接抛出去,crontab 里只留一行没有上下文的 Traceback(甚至被 2>&1 吞掉),
        # 排查时完全无从下手。同时返回非零退出码,方便外部(cron 邮件、监控)感知失败。
        log.exception("监控任务未捕获异常,进程退出")
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
