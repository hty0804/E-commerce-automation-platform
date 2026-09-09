# 容量与性能：SKU 多了，每小时扫描还来得及吗？

## 结论先行

**来得及，但瓶颈不在你以为的地方。**

按亚马逊 `getInventorySummaries` 的限流（rate 2 req/s、burst 2，每页约 50 条）算：

| SKU 数 | 需要的请求数 | 理论耗时 | 结论 |
|---|---|---|---|
| 1,000 | 20 页 | 10 秒 | 毫无压力 |
| 10,000 | 200 页 | ~100 秒 | 安全 |
| 50,000 | 1,000 页 | ~8 分钟 | 可以，但已明显占用调度窗口 |
| 100,000 | 2,000 页 | ~17 分钟 | 仍能跑完，但危险区 |
| 500,000+ | 10,000 页 | ~80 分钟 | **超出 1 小时窗口，必须换方案** |

但在到 10 万 SKU 之前，你更可能先踩到下面三个坑——它们跟 SKU 数量无关，现在就在生效。

---

## 三个已经被修复的真实问题

### 1. 只拉了第一页（最严重：静默漏数据）

`getInventorySummaries` **没有 pageSize 参数**，服务端按页返回（约 50 条/页），
必须靠响应里的 `payload.pagination.nextToken` 翻页。

原实现只调用一次、没处理 `nextToken`：

```python
inventory = amazon.get_inventory_summaries()
for summary in inventory.get("payload", {}).get("inventorySummaries", []):
    ...
```

结果：**超过 50 个 SKU 后，后面的 SKU 永远不会被监控，而且不报任何错。**
拼多多同理，`page=1, page_size=100` 超过 100 个商品就漏。

这是"看起来一直在跑、实际只监控了前 50 个"的典型场景，比跑不完危险得多。
现已改为自动翻页（`get_inventory_summaries()` 返回全部列表，`iter_goods_list()` 自动翻页）。

### 2. state.json 每个 SKU 都全量读写一次（真正的"慢"）

原 `check_metric()` 每比对一个 SKU 就 `_load_state()` + `_save_state()` 一次。
10,000 个 SKU = 10,000 次全量 JSON 反序列化 + 10,000 次全量写回。

**这一项的耗时经常超过拉数据本身**，是"监控越跑越慢"的真正来源。

现改为 `collect_anomalies()`：一次 load → 循环比对 → 一次 save。
`check_metric()` 保留但仅用于单条调试，不要在批量循环里调用。

### 3. 订单接口限流极低，且也有分页

`getOrders` 限流 **0.0167 req/s（约 1 次/分钟）**，burst 20。
一小时调一次是安全的；如果你想提到 5 分钟一次，会直接撞限流。

另外单页返回有上限，大促时一小时的订单可能超过一页，
不翻页会把订单数算少，进而**误触发**"订单量暴跌"告警。现已加翻页。

---

## 增量拉取：最有效的一招

`getInventorySummaries` 支持 `startDateTime`：只返回该时间点之后**有变更**的库存。
日常只拉变更的几十条，把 2,000 次翻页降到几十次。

注意两点：
- 传 `startDateTime` 时 `sellerSkus` / `sellerSku` 会被忽略；
- 官方要求 `startDateTime` 与 `nextToken` **一起传**，否则可能报错。

策略（已实现在 `main.py::_next_inventory_window`）：
- 首次运行 → 全量
- 距上次全量超过 `INVENTORY_FULL_SCAN_HOURS`（默认 6 小时）→ 全量对账
- 其余 → 增量，并回看 `INVENTORY_LOOKBACK_MINUTES`（默认 90 分钟）防止漏数据

---

## 相关配置

| 变量 | 默认 | 说明 |
|---|---|---|
| `INVENTORY_FULL_SCAN_HOURS` | 6 | 每隔几小时做一次全量对账，其余走增量 |
| `INVENTORY_LOOKBACK_MINUTES` | 90 | 增量回看冗余，防止时钟偏差漏数据 |
| `INVENTORY_MAX_PAGES` | 2000 | 翻页上限，防止接口异常时无限循环 |
| `FOCUS_SKUS` | 空 | 爆款/易断货 SKU，逗号分隔，每轮都单独查一次 |
| `MONITOR_INTERVAL_MIN` | 60 | 调度间隔，需与 crontab 保持一致 |

---

## 指标历史库（history.db）的容量

基线对比要存历史，这是新增的、会**持续增长**的存储，和 state.json 完全不是一个量级：
state.json 每个指标只存 1 条，history.db 每个指标每小时存 1 条、保留 `HISTORY_RETENTION_DAYS`（默认 90）天。

### 实测数字

24 万行实测（含两个索引）：

- 单行约 **132.6 字节**
- 批量插入 24 万行：6.4 s

按此外推 90 天（每指标 24 × 90 = 2160 条）：

| 指标数 | 行数 | 占用 |
|---|---:|---:|
| 500 | 108 万 | 137 MB |
| 1,000 | 216 万 | 273 MB |
| 5,000 | 1,080 万 | 1.4 GB |
| 10,000 | 2,160 万 | 2.7 GB |
| 50,000 | 1.08 亿 | 13.7 GB |

**结论**：万级 SKU 以内可以直接用默认配置；到十万级就要么缩短保留期，
要么只给 `FOCUS_SKUS` 里的重点 SKU 存历史（历史的唯一用途是算基线，
全量存历史对长尾 SKU 没什么价值）。

### 怎么省

- `HISTORY_RETENTION_DAYS` 是最直接的旋钮：90 → 30 天，占用直接砍到 1/3。
  但基线是"最近 N 天同一时段"，保留期短了样本就少，`BASELINE_LOOKBACK_DAYS` 跟着调。
- 每天凌晨那轮自动 `prune()` 清理（在 `monitor` 里，不用单独配 crontab）。
  `prune()` 会顺带 `VACUUM` 回收空间 —— 不做的话 db 文件只增不减。

---

## 基线查询的性能：连接复用带来的 200 倍

基线是**每个异常指标查一次**的，所以这一项的常数因子很关键。

24 万行的库里实测：

| 环节 | 耗时 |
|---|---:|
| 一次基线查询（SQL 本身，走 `idx_mh_hour` 索引） | **0.015 ms** |
| 新建连接 + `PRAGMA` | **~10 ms** |

也就是说，早期实现里 **99.9% 的时间花在反复开关连接上**，而不是查询本身。
每个指标都 `sqlite3.connect()` 一次，10,000 个指标同时异常就是 100 多秒。

改成按线程复用连接后（`history.py::_connect`，用 `threading.local()` 缓存）：

- 单指标从 **10.68 ms → 0.05 ms**，提速约 200 倍
- 10,000 个指标同时异常：**107 s → 0.5 s**

复用带来两个必须处理的坑，都已实现并加了测试：

1. **库路径变了要换连接**。缓存时记下 `HISTORY_DB` 路径，不一致就关掉重建 ——
   否则会读写到上一个库。这种"数据串了"的 bug 不报错、只有结果不对，极难排查。
2. **"表建好没"要作为独立状态跟着连接走**，不能隐含在"新建连接"这个动作里。
   `write=False` 打开的连接故意不建表（`is_available()` 靠"表不存在"判断库未初始化），
   顺序一旦是"先读后写"，复用的就是个没建表的连接，写入直接报 `no such table`。

另外 `PRAGMA journal_mode=WAL` **只需要在建库时设一次** —— WAL 是写在数据库文件头里的
持久属性，重复设置纯属浪费（它要动文件、要拿锁）。

---

## 还要更大？按顺序上这三招

### 招式一：分片轮转

把 SKU 按哈希分 N 片，每小时只扫 1 片，N 小时覆盖全量；
`FOCUS_SKUS` 里的爆款每轮都扫。适合十万级。

### 招式二：全量快照走 Reports API

`create_inventory_report()` 已封装。一次报告替代数千次调用，
代价是异步生成（几分钟到十几分钟），适合每天 1~2 次全量对账，而不是 hourly 监控。

常用 reportType：
- `GET_FBA_MYI_UNSUPPRESSED_INVENTORY_DATA` — FBA 可售库存
- `GET_MERCHANT_LISTINGS_ALL_DATA` — 全部在售 Listing

### 招式三：把轮询换成订阅推送

真正需要分钟级响应的其实是 **Buy Box 丢失、被跟卖、Listing 变狗、价格异常**，
这些用轮询既贵又慢。SP-API 的 Notifications API 支持订阅推送，
`ANY_OFFER_CHANGED` 可覆盖 top 20 offers / Buy Box 赢家 / Buy Box 价格变化。

⚠️ 两点提醒：
- 我没在文档里确认到"库存变更"类通知，**库存仍需靠轮询或报告**；接入前请自行核对
  [notification type values](https://developer-docs.amazon.com/sp-api/docs/notification-type-values)。
- 官方明确建议保留轮询作为兜底（"build a backup mechanism"），
  不要因为上了订阅就把每小时的对账任务删掉。

---

## 一个反直觉的点：库存数据本身就不是实时的

SP-API 返回的库存带 `lastUpdatedTime`，通常滞后 15~30 分钟，有时更久。
这意味着：**把扫描频率从 1 小时提到 15 分钟，拿到的很可能还是同一份滞后数据**，
只是多烧了几倍的请求配额。

所以合理的分层是：

| 数据 | 建议频率 | 方式 |
|---|---|---|
| 库存 | 1~2 小时（甚至 2~4 小时） | 增量轮询 + 定期全量报告 |
| 订单量 | 1 小时 | getOrders（限流极低，别再提高） |
| Buy Box / 价格 / Listing 状态 | 分钟级 | Notifications API 订阅 |
| 全量对账 | 每天 1~2 次 | Reports API |

**先把"每小时全量扫库存"改成"增量 + 定期全量"，再考虑提速。**
