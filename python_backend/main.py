"""
主入口。

三个核心功能:
1. gen_listings(products)        —— 用大模型把中文商品信息生成可直接上架的 Listing
2. list_new_products(products)   —— 批量上架商品到亚马逊 / 拼多多
3. run_hourly_monitor()          —— 抓取两个平台的关键数据,做异常检测,触发告警

调度方式(二选一):
A) 系统 crontab,每小时跑一次(推荐,简单可靠):
   0 * * * * cd /path/to/ecommerce_monitor && /usr/bin/python3 main.py monitor >> monitor.log 2>&1

B) 用 APScheduler 常驻进程调度(如果你想让它作为一个服务一直跑着):
   pip install apscheduler
   然后取消下面 run_as_daemon() 里的注释并运行 `python main.py daemon`
"""
import json
import sys
import time
import datetime

from amazon_client import AmazonSPAPIClient
from pdd_client import PinduoduoClient
import anomaly_detector
import incident
import alerts
import listing_gen
import config


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
                    price_fen=int(float(p.get("price", 0) or 0) * 100),
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

    for item in products:
        try:
            if item["platform"] == "amazon":
                res = amazon.create_or_update_listing(item["sku"], item["payload"])
            elif item["platform"] == "pdd":
                res = pdd.add_goods(item["payload"])
            else:
                raise ValueError(f"未知平台: {item['platform']}")
            results.append({"item": item, "status": "success", "response": res})
        except Exception as e:
            results.append({"item": item, "status": "failed", "error": str(e)})
            name = item.get("sku") or item.get("payload", {}).get("goods_name", "未知商品")
            alerts.send_alert(f"⚠️ 商品上架失败: {name} - {e}")

    return results


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
                by_sku[sku] = s.get("totalQuantity", 0)

        for sku, qty in by_sku.items():
            checks.append({
                "platform": "amazon", "key": f"inventory:{sku}", "value": qty,
                "threshold": config.INVENTORY_DROP_THRESHOLD, "direction": "drop",
            })
        stats["amazon_skus"] = len(by_sku)

        # 一轮下来一个 SKU 都没拿到,通常是接口/权限出了问题,不要当成"一切正常"
        if not by_sku:
            error_messages.append("⚠️ 亚马逊本轮未获取到任何库存数据,请检查授权或接口状态")
        else:
            anomaly_detector.set_sync_cursor("amazon_inventory", _utc_iso(now))

        # 订单:getOrders 限流极低(约 1 次/分钟),一小时一次是安全的,别再提高频率
        one_hour_ago = _utc_iso(now - datetime.timedelta(hours=1))
        orders = amazon.get_recent_orders(one_hour_ago)
        checks.append({
            "platform": "amazon", "key": "order_count_last_hour", "value": len(orders),
            "threshold": config.ORDER_COUNT_DROP_THRESHOLD, "direction": "drop",
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
                "platform": "pdd", "key": f"stock:{g.get('goods_id')}", "value": g.get("quantity", 0),
                "threshold": config.INVENTORY_DROP_THRESHOLD, "direction": "drop",
            })
        stats["pdd_goods"] = len(goods)
        if not goods:
            error_messages.append("⚠️ 拼多多本轮未获取到任何商品数据,请检查授权或接口状态")
        print(f"[pdd] 本轮 {stats['pdd_goods']} 个商品")
    except Exception as e:
        error_messages.append(f"⚠️ 拼多多数据抓取失败: {e}")

    # ---------- 异常检测 + 分类 + 建议(一次读、一次写 state) ----------
    incidents = anomaly_detector.collect_incidents(checks)

    # 抓取失败类错误也纳入异常体系(会被归类为鉴权失败 / 限流 / 接口异常 / 数据缺失)
    for msg in error_messages:
        incidents.append({
            "platform": "all", "key": "monitor", "value": None, "previous": None,
            "change_ratio": None, "threshold": 0, "direction": "drop", "message": msg,
        })

    elapsed = time.time() - started
    report = incident.build_report(incidents)

    if incidents:
        content = incident.format_alert(report)
        alerts.send_alert(content)
        print(content)
    else:
        print(f"[{datetime.datetime.now()}] 本次监控无异常,共检查 {len(checks)} 项指标")

    print(f"[stat] 耗时 {elapsed:.1f}s | 亚马逊 {stats['amazon_skus']} SKU / "
          f"拼多多 {stats['pdd_goods']} 商品 / 订单 {stats['orders']} 单")

    # 单轮耗时逼近调度间隔时要预警,否则任务会越积越多
    if elapsed > config.MONITOR_INTERVAL_MIN * 60 * 0.8:
        alerts.send_alert(
            f"⚠️ 监控任务耗时 {elapsed:.0f}s,已接近调度间隔 {config.MONITOR_INTERVAL_MIN} 分钟,"
            f"建议缩短全量间隔或改用增量/分片策略"
        )


def run_as_daemon() -> None:
    """可选:用 APScheduler 常驻进程,每小时整点触发一次监控"""
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(run_hourly_monitor, "cron", minute=0)
    print("监控进程已启动,每小时整点执行一次...")
    scheduler.start()


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "monitor":
        run_hourly_monitor()
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
            sys.exit(1)
        generate_and_publish(items, plat)
    else:
        print(
            "用法:\n"
            "  python main.py monitor                # 手动/被 crontab 调用,跑一次监控\n"
            "  python main.py daemon                 # 常驻进程,内置每小时调度\n"
            "  python main.py genlist input.json     # 用大模型生成 Listing 并本地校验\n"
            "  python main.py genlist input.json pdd # 生成拼多多中文 Listing"
        )
