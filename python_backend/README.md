# 电商 SOP 自动化 —— 亚马逊 + 拼多多

调用亚马逊 SP-API 和拼多多开放平台 API,实现商品自动上架、每小时数据监控、异常告警(企业微信/钉钉),
并支持用大模型生成 Listing 与异常处理建议(均为可选,不配密钥也能跑通)。

## 目录结构

```
python_backend/
├── config.py             # 所有密钥/阈值配置(从环境变量读取)
├── amazon_client.py      # 亚马逊 SP-API 客户端(鉴权 + 上架 + 库存/订单查询)
├── pdd_client.py         # 拼多多开放平台客户端(签名 + 上架 + 商品/订单查询)
├── anomaly_detector.py   # 异常检测:环比触发 + 同时段基线复核
├── history.py            # 指标历史库(SQLite,只追加),为基线提供"历史同时段"数据
├── incident.py           # 异常分类(规则) + 大模型处理建议(可选)
├── listing_gen.py        # 大模型生成 Listing:标题/五点/描述/关键词 + 类目属性补全
├── alerts.py             # 企业微信 / 钉钉群机器人告警
├── main.py               # 主入口:monitor / health / daemon / genlist / history
├── listing_input.example.json  # genlist 的输入格式示例
├── PERFORMANCE.md        # 大 SKU 量下的容量测算与优化策略
├── requirements.txt
└── README.md
```

**两个状态文件各管各的，别混淆：**

| 文件 | 存什么 | 为什么分开 |
| --- | --- | --- |
| `state.json` | 每个指标的**上一次取值**、同步游标、LLM 冷却、上架幂等 | 这些都是"当前状态"，只在文件锁保护下做读-改-写 |
| `history.db` | 每个指标的**全部历史取值** | 只追加不修改，用来算"历史同时段基线" |

`state.json` 每个 key 只存上一次的值，历史全丢，所以光靠它做不了同比 ——
这也是为什么历史单独用 SQLite 存，而不是把 state.json 整个迁过去：
迁过去要动游标/冷却/幂等这些已经在文件锁下并发安全的东西，纯属增加风险、没有收益。

## 1. 安装依赖

```bash
pip install -r requirements.txt
```

## 2. 配置环境变量

### 亚马逊 SP-API
需要先在 Amazon Seller Central 完成开发者注册,拿到以下信息:

| 变量 | 说明 |
|---|---|
| `AMAZON_REFRESH_TOKEN` | 授权后拿到的长期 refresh token |
| `AMAZON_CLIENT_ID` / `AMAZON_CLIENT_SECRET` | LWA 应用的 client id/secret |
| `AMAZON_AWS_ACCESS_KEY` / `AMAZON_AWS_SECRET_KEY` | 用于给请求做 SigV4 签名的 AWS IAM 密钥 |
| `AMAZON_SELLER_ID` | 你的卖家 ID |
| `AMAZON_MARKETPLACE_ID` | 站点 ID(默认美国站 `ATVPDKIKX0DER`) |

### 拼多多开放平台
需要先在开放平台创建应用并完成店铺授权,拿到:

| 变量 | 说明 |
|---|---|
| `PDD_CLIENT_ID` / `PDD_CLIENT_SECRET` | 应用的 client id/secret |
| `PDD_ACCESS_TOKEN` | 店铺授权后拿到的 access_token |

### 告警(二选一或都配置)

| 变量 | 说明 |
|---|---|
| `WECOM_WEBHOOK_URL` | 企业微信群机器人 webhook 地址 |
| `DINGTALK_WEBHOOK_URL` | 钉钉群机器人 webhook 地址 |
| `DINGTALK_SECRET` | 钉钉机器人如果开启了「加签」,填这里;没开可不填 |

### 大模型(可选,用于生成 Listing 与异常建议)

| 变量 | 说明 |
|---|---|
| `LLM_ENABLED` | 总开关,默认 `false`;不配 Key 时完全不发起任何请求 |
| `LLM_API_KEY` | OpenAI 兼容接口的密钥(DeepSeek / 通义 / Moonshot 等均可) |
| `LLM_BASE_URL` | 默认 `https://api.deepseek.com/v1` |
| `LLM_MODEL` | 默认 `deepseek-chat` |
| `LISTING_BRAND` | 生成 Listing 时的默认品牌名(可留空) |
| `LISTING_AUTO_PUBLISH` | 生成通过校验后是否直接提交上架,默认 `false`(只导出 payload) |
| `LISTING_SKIP_ON_ERROR` | 校验有阻断级问题时跳过上架,默认 `true` |

建议用 `.env` 文件 + `python-dotenv`,或者直接在服务器 / 容器里设置环境变量,不要把密钥写进代码。

## 3. 跑一次监控(手动测试)

```bash
python main.py monitor
```

正常情况下会打印「本次监控无异常」;如果指标环比下降超过阈值,会打印告警内容并推送到企业微信/钉钉。

## 4. 生成 Listing(推荐先跑这一步)

把中文商品信息交给大模型,生成可直接上架的 Listing(英文标题 / 五点 / 描述 / 搜索关键词),
并按类目 schema 补全必填属性,生成结果会先过本地校验(长度红线、违规词、必填属性缺失)。

```bash
# 输入格式见 listing_input.example.json
python main.py genlist listing_input.json amazon   # 生成亚马逊英文 Listing
python main.py genlist listing_input.json pdd      # 生成拼多多中文 Listing
```

流程:生成 → 本地校验 → 写出 `listing_payloads.json`(默认不自动上架,确认文案后把
`LISTING_AUTO_PUBLISH=true` 打开即可)。不配 `LLM_API_KEY` 时用规则模板出草稿,流程照样跑得通;
模型超时/返回非法 JSON 会自动降级,绝不让上架流程卡住。

「不同类目 payload 字段不同」的权威解决方案在 `listing_gen.fetch_product_type_definition()`:
它调 SP-API 的 getDefinitionsProductType 拉真实 schema,本地 CATEGORY_SCHEMA 只是兜底。

## 5. 上架商品

在你自己的脚本里组装商品数据,调用:

```python
from main import list_new_products

products = [
    {"platform": "amazon", "sku": "SKU123", "payload": {...}},
    {"platform": "pdd", "payload": {"goods_name": "...", "price": 9900, ...}},
]
list_new_products(products)
```

`payload` 的具体字段需要按各平台商品接口的类目要求组装(标题、类目、价格、库存、图片、规格等),
建议先跑通一个商品,确认字段没问题后再批量执行。

## 6. 设置每小时自动执行

**方式一:系统 crontab(推荐,简单可靠)**

```bash
crontab -e
# 下面两行都要加。注意 */10 后面的空格不能少,否则这行会被 cron 拒绝。
0 * * * * cd /path/to/ecommerce-sop-admin/python_backend && /usr/bin/python3 main.py monitor >> monitor.log 2>&1
*/10 * * * * cd /path/to/ecommerce-sop-admin/python_backend && /usr/bin/python3 main.py health >> monitor.log 2>&1
```

第二行是**死信检查**:监控最大的风险是它自己挂了而你不知道 ——
crontab 被覆盖、机器重启后 cron 没起来、进程被 OOM kill,
这些情况下不会有任何异常,只是"安静地不再监控"。
`health` 发现超过 2.5 个调度周期没有成功运行就告警(退出码 1)。

**方式二:常驻进程(APScheduler)**

```bash
python main.py daemon
```

适合部署成一个长期运行的服务(比如用 systemd / supervisor / docker 常驻)。

## 7. 调整异常检测阈值

在 `config.py` 里改:

```python
INVENTORY_DROP_THRESHOLD = 0.3    # 库存环比下降超过 30% 告警
ORDER_COUNT_DROP_THRESHOLD = 0.5  # 每小时订单量环比下降超过 50% 告警
```

## 注意事项

- 亚马逊 SP-API 和拼多多开放平台的接口字段、限流规则会不定期调整,正式接入前请对照官方最新文档核对:
  - 亚马逊: https://developer-docs.amazon.com/sp-api/
  - 拼多多: https://open.pinduoduo.com/application/document/api
- 异常检测是「环比触发 + 同时段基线复核」两道闸,不是单点环比;
  误报仍然可能有(比如这个时段历史上本来就波动很大),
  真要再降一档可以接时序数据库做移动平均/季节性分解。
- `state.json` 和 `history.db` 都是**单机本地文件**。
  如果部署在多台机器,或容器会重启丢盘,建议换成 Redis(存状态)+ 时序库(存历史);
  多台机器各写各的本地库会让各自的基线都不完整。
- 基线需要**攒样本**:新部署或新增 SKU 后,前 `BASELINE_MIN_SAMPLES` 轮不会抑制任何告警
  (样本不足一律放行)。这段时间的告警行为等同于纯环比,属于预期表现。
