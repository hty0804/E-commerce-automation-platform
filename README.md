# 电商 SOP 自动化控制台

把亚马逊 SP-API + 拼多多开放平台的自动上架、每小时监控、异常告警（企业微信 / 钉钉）
做成一套**打开即可运行的后台管理系统**。

## 直接打开

- 🌐 **在线演示（已部署）**：https://0a4bc34f3155463184868b207d1f4d03.sg.agentos-app.run
- 💻 **本地打开**：双击 `index.html` 即可（纯静态，无需安装、无需联网、无需后端）

- 演示账号：**admin / admin123**
- 首次进入自动生成一套演示数据，**不需要配置任何 API 密钥**

## 页面结构

| 页面 | 说明 |
|---|---|
| 登录页 | 账号密码校验，记住登录状态 |
| 数据看板 | KPI 卡片、24 小时订单/库存趋势、告警等级分布、任务运行状态、最近告警 |
| 商品管理 | 双平台商品列表：搜索 / 平台 / 状态筛选、分页、新增、编辑、删除、单个或批量上架，可一键带入生成 Listing |
| AI 生成 Listing | 中文商品信息 → 可上架的英文/中文文案（标题/五点/描述/关键词）+ 类目属性补全 + 本地校验 + 导出 SP-API payload |
| 监控任务 | 任务的增删改、启停开关、频率与阈值配置、手动执行、注入异常验证告警链路 |
| 告警中心 | 按等级/平台/状态/**异常类型**筛选，标记已处理，查看推送状态与处理建议 |
| 运行日志 | 按级别筛选的执行日志（监控、上架、调度、告警模块） |
| 平台与凭证 | 亚马逊 / 拼多多密钥配置，企业微信 / 钉钉 webhook 与测试发送 |
| 系统设置 | 演示/真实模式、执行节奏、异常阈值、大模型建议、Listing 生成策略，导出 `.env` 与上架 Payload、重置数据 |
| 部署指南 | 接入真实 API 的完整实施步骤、容量测算与注意事项 |

## 三种「跑起来」的方式

1. **纯演示**（默认）：内置模拟引擎生成库存/订单数据，按 30 秒一轮自动执行，
   可点顶部「立即监控」或监控任务页「注入一次异常」立刻看到告警产生与推送。
2. **配好密钥但不真发请求**：在「平台与凭证」填写密钥，页面只做配置管理与记录，
   不发起任何网络请求（浏览器直连平台 API 会有跨域与密钥泄露风险）。
3. **接真实 API**：在设置页导出 `.env`，交给 `python_backend/` 下的 Python 脚本，
   由后端进程调用平台 API 并推送企微/钉钉告警。详见站内「部署指南」页。

## 目录结构

```
index.html              登录页 + 应用外壳
assets/css/style.css    样式（响应式：1180 / 992 / 860 / 640 四档断点）
assets/js/listing_rules.js  【自动生成】Listing 规则，由 shared/listing_rules.json 生成
assets/js/store.js      数据层 + 模拟执行引擎 + 异常分类/建议 + Listing 生成（localStorage 持久化）
assets/js/ui.js         Toast / Modal / 标签组件 + 纯 SVG 图表
assets/js/views.js      9 个业务页面
assets/js/app.js        路由、登录、定时调度
shared/listing_rules.json  ★ Listing 规则的唯一数据源（前后端共用，见下节）
tools/gen_listing_rules.py  JSON → JS 生成器
python_backend/         原 Python 自动化框架（SP-API / 拼多多客户端、检测、分类、Listing 生成、告警、调度）
```

## Listing 规则是单一数据源（改之前先看这节）

长度红线、违规词、类目 schema、中英词表、emoji 判定区间
**只准写在一处**：`shared/listing_rules.json`。
Python 后端（`listing_gen.py`）和前端都从它加载。

前端是零构建的（双击 `index.html` 就能跑），`file://` 下 `fetch()` 读本地 JSON 会被 CORS 拦，
所以前端吃的是生成出来的 `assets/js/listing_rules.js`。

**改规则的唯一正确姿势：**

```bash
# 1. 改 shared/listing_rules.json
# 2. 重新生成前端那份（漏了这步 CI 和测试会直接失败）
python tools/gen_listing_rules.py
# 3. 跑一遍测试确认两端一致
python -m unittest discover -s python_backend/tests -v
npm test
```

为什么这么麻烦：以前两端各硬编码一份，并且**已经实际漂移过** ——
前端漏了 `best-seller` / `cure` / `100% cure` 三个违规词，
于是出现「前端显示校验通过、后端却拦截」，而 `cure` 属于医疗功效类合规高危词。
现在 `python_backend/tests/test_rules_sync.py` 会在两端不一致时直接测试失败。

emoji 判定用的是**码点区间**而不是正则字面量：Python 写 `\U0001F000`、
JS 写 `\u{1F000}`（带 `u` 标志），转义语法不兼容，各自从同一份区间构建才能保证范围一致。

## AI 能力说明（可选，默认全关）

| 能力 | 何时调用 | 不配密钥时 |
|---|---|---|
| 异常**分类** | 永不调用大模型，纯规则判定（可复现、零延迟） | 正常工作 |
| 异常处理**建议** | 告警触发后才调用，批量一次 + 冷却 | 用内置规则建议，告警照发 |
| **Listing 生成** | 上架环节按需调用 | 用规则模板出草稿，流程照样跑通 |

模型接口走 OpenAI 兼容协议（DeepSeek / 通义 / Moonshot 均可），真实调用全部在 Python 后端执行
（避免密钥暴露在浏览器与跨域问题），网页端提供模拟预览。生成结果默认**只导出不自动上架**，
先过本地校验（长度红线、违规词、必填属性）再人工确认。

### 生图 Skill（`python_backend/image_gen.py`）

给大模型一个「生图工具」，让它基于 Listing 自主决定出图。**模型无关** ——
DeepSeek / 通义 / Moonshot / GPT / 任意 OpenAI 兼容接口都行，不绑定某一家。

核心不是「能调接口」，而是**把图片风格固定住**：风格模板钉死画风、背景、光线、镜头、
负向提示词和画幅，模型只能从预设里挑、不能自由发挥，商品主体和卖点才由它填。
否则一个 SKU 两次出图风格都可能不一样，放到店铺里就是一盘散沙。
固定风格的实现方式很直接 —— 工具 schema 里 `style` 用的是 `enum`，模型编不出第五种风格：

```python
>>> image_gen.tool_definition()["function"]["parameters"]["properties"]["style"]["enum"]
['amazon_main', 'detail', 'lifestyle', 'scene']
```

内置四套风格（可用 `IMAGE_STYLES_JSON` 整套覆盖或新增）：

| style | 中文名 | 画幅 | 用途 |
| --- | --- | --- | --- |
| `amazon_main` | 亚马逊白底主图 | 1:1 | 主图位，纯白背景、棚拍、居中 |
| `scene` | 场景氛围图 | 4:3 | A+ / 副图，真实使用场景 |
| `detail` | 细节特写 | 1:1 | 材质、工艺微距 |
| `lifestyle` | 人物使用场景 | 3:4 | 人物出镜的调性图 |

配置（`python_backend/config.py`，全部环境变量覆盖，**不配就不发任何请求**）：

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `IMAGE_ENABLED` | `false` | 总开关。关着时工具返回「未配置」，主流程照跑 |
| `IMAGE_PROVIDER` | `ark` | `ark` / `openai` / `custom`，见下 |
| `IMAGE_API_KEY` | 空 | 生图接口密钥 |
| `IMAGE_API_BASE_URL` | 空 | 留空则用 provider 预设 |
| `IMAGE_API_PATH` | `/images/generations` | 拼在 base 后面 |
| `IMAGE_API_MODEL` | 空 | 留空则用 provider 预设（方舟为 `doubao-seedream-4-0`） |
| `IMAGE_TIMEOUT` | `60` | 超时秒数 |
| `IMAGE_DEFAULT_COUNT` | `4` | 模型没指定 `count` 时的默认出图套数 |
| `IMAGE_WATERMARK` | `false` | 仅方舟下发。**方舟默认是 `true`，不关就是带水印的图** |
| `IMAGE_NEGATIVE_IN_PROMPT` | `true` | 仅方舟生效：把负向词拼进 prompt（方舟没有 `negative_prompt` 字段） |
| `IMAGE_STYLES_JSON` | 空 | 自定义风格，JSON 字符串（见下） |

预设只填充**留空**的字段 —— 显式设置了的环境变量永远优先。

### 对接火山方舟（豆包 doubao-seedream）

默认就是方舟，配两个变量即可：

```bash
export IMAGE_ENABLED=true
export IMAGE_API_KEY=你的方舟APIKey        # 控制台「API Key 管理」里拿
export IMAGE_API_MODEL=doubao-seedream-4-0 # 可省略，预设就是这个
```

自检（会真实调用、真实扣费，先把解析出的配置和实际请求体打出来再发）：

```bash
python tools/check_image_api.py --count 4 --points "35dB降噪,30小时续航"
```

**方舟跟标准 OpenAI 格式有 4 处不兼容**，直接套通用格式会踩坑，代码里已分别处理：

| # | 差异 | 踩了会怎样 |
| --- | --- | --- |
| 1 | 没有 `n` 参数，出多张要用 `sequential_image_generation:"auto"` + `options.max_images`（1–15） | 发 `n` 它不认，「生成多套图」**静默退化成 1 张** |
| 2 | 没有 `negative_prompt` 字段 | 负向词被丢掉，水印/文字更容易冒出来 → 拼进 prompt |
| 3 | `watermark` **默认 `true`** | 亚马逊主图带水印 = 违规，必须显式关 |
| 4 | `size` 只认 `2K`/`3K`/`4K` 或宽×高像素，且总像素 ∈ [3686400, 16777216] | 常见的 `1024x1024` 只有 1M，**会被直接拒** |

> ⚠️ 方舟返回的图片 URL **只有 24 小时有效期**。图库必须把图片**下载转存**，
> 不能直接把 URL 存进库 —— 第二天就全是死链。这一步在做图库时一定要实现。

想用别家：`IMAGE_PROVIDER=openai` 走标准 OpenAI 格式（`n` + `negative_prompt`）；
`IMAGE_PROVIDER=custom` 什么都不帮你填。

> 关于 `doubao-mcp-server`：那是给 **MCP 客户端**（Cursor / Claude Desktop 等）用的，
> 模型直接调 MCP 工具。本项目是 Python 后端，没有 MCP client，所以走上面的 REST 接口 ——
> 后端自己包装成 function-calling 工具，效果一样，且不依赖客户端支持 MCP。

自定义风格示例 —— 键是风格名，`{subject}` / `{points}` 会被替换，
`extra` 里的字段原样合并进请求体，**不同供应商的私有参数靠这个口子接，不用改代码**：

```bash
export IMAGE_STYLES_JSON='{
  "amazon_main": {
    "label": "亚马逊白底主图",
    "prompt": "Studio shot of {subject}, pure white background, softbox lighting.",
    "negative": "watermark, text, logo",
    "aspect_ratio": "1:1",
    "extra": {}
  },
  "爆款风": {
    "label": "爆款高饱和风",
    "prompt": "{subject}, vibrant colors, dramatic lighting, {points}",
    "negative": "dull, low contrast",
    "aspect_ratio": "1:1",
    "extra": {"seed": 12345}
  }
}'
```

JSON 解析失败只会打 warning 并忽略，**不会让服务起不来**。

两种用法：

```python
# 1) 直接出图(自己拼参数)
req  = image_gen.build_image_request({
    "subject": "wireless noise cancelling earbuds, black",
    "selling_points": ["35dB 主动降噪", "30 小时续航"],
    "style": "amazon_main",
    "count": 4,
})
res = image_gen.generate(req)          # 永远不抛异常
if res["ok"]:
    for url in res["images"]: ...      # url / data URI
else:
    log.warning(res["error"])

# 2) 让大模型自己决定要不要出图、出几套、用哪种风格
out = image_gen.run_with_image_tool(
    "给下面这个 Listing 配 4 张主图:\n" + listing_text,
    max_rounds=3,
)
# {"ok", "content", "images", "used_tool", "error"}
```

`run_with_image_tool` 内部是标准的 function-calling 循环：带 `tools` 调模型 →
拿到 `tool_calls` → 执行 → 结果喂回 → 模型给最终答复。
模型不支持 `tools`（第一次调用失败）会**自动降级成纯 prompt 模式**，
靠 `SYSTEM_SKILL_PROMPT` + `parse_tool_call()` 从输出里抠 JSON。
执行失败会**如实告诉模型**，而不是伪造成功 —— 否则模型会以为图已生成，接着编造图片描述。

返回值统一 `{"ok", "images", "error", "request"}`，HTTP 200 但解析不出图片**按失败处理**，
不假装成功。`generate()` 保证不抛异常：生图挂了只影响生图，不影响上架 / 监控 / 告警。

> 换供应商时只需改 `_build_request_body()`（分了 `_build_ark_body` / `_build_openai_body` 两条路）
> 和 `_extract_images()`，其余逻辑不用动。响应解析已兼容 `data[].url` /
> `data[].b64_json` / `data[].error` / `images[]` 几种形状。

### 图片转存（`python_backend/image_store.py`）

方舟的图片 URL 只有 24 小时有效期，**生完必须立刻下载落盘**，图库存本地路径、不存 URL。
这是延迟爆炸型故障：生成当时一切正常，过一天才坏，排查时根本想不到是 URL 过期。

```python
res  = image_gen.generate(image_gen.build_image_request({...}))
saved = image_store.store_generated(res)   # 下载 + 落盘 + 写 sidecar

saved["count"]          # 成功几张
saved["saved"][0]["path"]  # 本地绝对路径
saved["failed"]         # 失败清单(带原因,不会抛异常)
```

每个文件旁边会写一个同名 `.json` 的 sidecar，记下风格、商品主体、卖点、提示词、来源、
sha256、时间。没有它，磁盘上就是一堆没来历的孤儿文件 —— 做人工筛选时你连
「这张是什么风格、给哪个商品出的」都答不上来，而这正是筛选时最需要的两条信息。
图库直接扫目录就能建索引：`image_store.list_stored()`。

几个实现上的取舍：

- **文件名带内容哈希**（sha256 前 12 位）：同一张图重复生成只存一份，重跑不会把图库撑爆
- **扩展名不信 URL**：看 `Content-Type`，看不出来再嗅文件头魔数。URL 写 `.jpg`
  实际是 png 很常见，存错扩展名后面处理会莫名其妙
- **原子写**（`.part` + rename）：进程被杀不会留下半截损坏文件被当成正常图
- **边下边算大小**，超 `IMAGE_STORE_MAX_BYTES` 直接中止，一个字节都不落盘
- 任何失败都只进 `failed` 列表，**不抛异常** —— 转存失败不该影响主流程

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `IMAGE_STORE_DIR` | `python_backend/images` | 转存目录，会自动创建 |
| `IMAGE_STORE_MAX_BYTES` | `20MB` | 单张上限，防异常大文件写满磁盘 |
| `IMAGE_STORE_TIMEOUT` | `60` | 下载单张的超时秒数 |

该目录已在 `.gitignore` 里 —— 生成图是运行时产物，体积大且可重新生成，不该进仓库。

### 图片库与爆款反哺

转存之后要注册进图片库，图库不是把文件再复制一份，而是用 SQLite 保存索引、分类和风格反馈：

```python
saved = image_store.store_generated(result)
# store_generated 返回的 saved 里带 meta；注册每张图片资产
library = image_library.add_saved_batch(saved, gallery="unclassified")
```

人工筛选后把图放入爆款或普通图库。**只有爆款会反过来影响后续生图风格**，普通图只归档：

```bash
python python_backend/main.py images list unclassified
python python_backend/main.py images mark 12 hot "柔和暖光; 木质背景; 右侧留白; 产品占画面 60%"
python python_backend/main.py images mark 13 normal
python python_backend/main.py images stats
python python_backend/main.py images feedback scene
```

反哺有两个保险：

1. 默认至少积累 **3 张同风格爆款图** 才生效（`IMAGE_HOT_MIN_SAMPLES=3`），避免一张偶然好图劫持整个店铺风格
2. 只有人工填写的「视觉锚点」会反哺，不会把上一张具体商品的完整 prompt 生搬给新商品，避免主体污染

一旦 `scene` 有 3 张爆款且填写了视觉锚点，后续：

```python
req = image_gen.build_image_request({
    "subject": "新的商品主体",
    "style": "scene",
})
# req["prompt"] 自动追加:
# Maintain these proven visual anchors: 柔和暖光; 木质背景; 右侧留白.
```

图片库 SQLite 默认是 `python_backend/image_library.db`（已加入 `.gitignore`），配置项：

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `IMAGE_LIBRARY_DB` | `python_backend/image_library.db` | 图片元数据、分类、反馈索引 |
| `IMAGE_HOT_MIN_SAMPLES` | `3` | 同风格爆款最小样本数 |
| `IMAGE_HOT_FEEDBACK_MAX_CHARS` | `500` | 反哺提示最大长度 |

### 前端图片库 HTTP API

前端已经接入 `python_backend/image_api.py`，图片库页面可以直接查看转存图片、按待筛选/爆款/普通过滤，并点击「爆款」或「普通」。

启动后端 API：

```bash
python python_backend/image_api.py
```

本项目已提供一份公网 API 实例：

```text
https://629ff8cd6f86472a8e2792ea8a8a3ee9.sg2.agentos-app.run
```

登录控制台后，在「系统设置 → 图片库 API」填写地址，然后打开左侧「图片库」。本地运行时也可以使用 `http://127.0.0.1:8765`。

接口：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/images?gallery=hot&style=scene` | 图片列表 |
| GET | `/api/images/stats` | 待筛选 / 爆款 / 普通统计 |
| GET | `/api/images/feedback?style=scene` | 查看爆款风格反馈 |
| POST | `/api/images/{id}/mark` | 人工标记 gallery 与视觉锚点 |
| DELETE | `/api/images/{id}` | 删除图库索引，不删除本地图片 |
| GET | `/media/{file_name}` | 读取本地转存图片 |

默认 API 不可连接时，前端显示明确「图片库 API 未连接」状态，不会用假数据冒充真实图库。`/media/` 只允许图片文件 basename，阻止路径穿越；CORS、端口和监听地址可通过 `IMAGE_API_CORS_ORIGINS` / `IMAGE_API_PORT` / `IMAGE_API_HOST` 配置。

> 公网实例当前是空图库，图片文件与 SQLite 数据不会从本地开发环境自动同步过去。要把真实生成图放进去，需要在同一公网后端环境运行「生图 → 转存 → 注册图库」流程；当前公网 API 已先验证 `/health` 和 `/api/images/stats` 均返回 200。

### 前端 Mock 生图入口

目前可以先不调用大模型和方舟接口，直接在前端验证完整交互：

1. 进入「AI 生成 Listing」
2. 填入商品信息并点击「生成 Listing」
3. 在生成结果操作区点击「根据 Listing 生成商品图」
4. 选择风格和数量，点击「开始 Mock 生图」
5. 结果会保存到浏览器 localStorage，并显示在「图片库」的 Mock 待筛选区域

Mock 模式不会发起任何网络请求，使用的是占位图卡片；将来接真实接口时只替换 `makeMockImages()`，保留风格、数量、待筛选和人工分类交互。

自检脚本加了 `--save`，一次跑通「真实生图 → 下载落盘」：

```bash
python tools/check_image_api.py --count 4 --save
```

## 异常检测逻辑

与 `python_backend/anomaly_detector.py` 完全一致。分两道闸：**环比触发，基线复核**。

### 第一道：环比

```
change_ratio = (current - previous) / previous
direction = "drop"  → change_ratio <= -threshold 时告警（库存、订单量）
direction = "rise"  → change_ratio >=  threshold 时告警（成本价、运费上涨）
direction = "both"  → |change_ratio| >= threshold 时告警（价格）
```

`threshold` 是**比例**不是百分比：`0.3` 表示 30%，别写成 `30`。
传 `drop / rise / both` 之外的值会打 warning 并回退为 `drop`，不会静默失效。

### 第二道：同时段基线复核

光靠环比会天天误报，因为**上一小时本身可能就不正常**：凌晨 2 点做闪购冲到 80 单，
3 点回落到常态的 30 单，环比就是 -62%，每天这个点来一条假告警。报多了人就再也不看告警了 ——
一个被忽略的告警系统比没有更糟，因为它给的是虚假的安全感。

所以环比判定异常后，还要拿「最近 N 天**同一时段**的中位数」当基线再核一遍：
30 单 vs 基线 28 单 = 正常波动，抑制掉。真断货时（常态 28 单突然掉到 2 单）相对基线也是暴跌，不会被误杀。

- 用**中位数**不用平均数：一两次促销/断货会把均值拉偏，中位数对离群值不敏感。
- 样本不足 / 历史库不可用 / 基线为 0 → **一律放行**。
  宁可多报，也不能因为数据不够而漏掉真异常。
- 被抑制的项会打进监控日志（`[baseline] ...`）。抑制了什么必须可见，
  否则「告警变少了」到底是降噪成功还是检测坏了，你根本分不清。

相关配置（`python_backend/config.py`，均可用环境变量覆盖）：

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `BASELINE_ENABLED` | `true` | 关掉就退回纯环比 |
| `BASELINE_LOOKBACK_DAYS` | `7` | 回看多少天 |
| `BASELINE_MIN_SAMPLES` | `3` | 少于这个样本数就不做基线、直接放行 |
| `BASELINE_TOLERANCE` | `1.0` | 与阈值同口径；`0.8` 更敏感，`1.2` 更保守 |
| `BASELINE_MATCH_DOW` | `true` | 是否只跟"同一个星期几"比（见下） |

**关于 `BASELINE_MATCH_DOW`**：跨境电商周末和工作日的订单节奏差别很大，
拿工作日的量去衡量周末会系统性误判，所以默认只跟同一个星期几的历史比。

样本量也要和这个开关一起看：按完整星期几区分时，**7 天回看里每个星期几通常只有 1 条**，
而 `BASELINE_MIN_SAMPLES` 默认是 3，严格基线容易达不到门槛 —— 生产环境建议把
`BASELINE_LOOKBACK_DAYS` 配成 **28 天**左右，每个星期几约有 4 条样本，能够容忍一两次采集缺失。

样本不够时仍会自动降级（同星期几 + 同小时 → 同星期几 + 小时 ±1 →
不挑星期几 + 同小时 → 不挑星期几 + 小时 ±1），不会静默漏报；
但降级后意味着**当前基线可能工作在较粗的口径上**。
想确认当前用的哪一档，跑 `python main.py history baseline <platform> <key>` 看样本数。
| `HISTORY_RETENTION_DAYS` | `90` | 历史保留天数，每天凌晨那轮自动清理 |
| `HISTORY_FOCUS_ONLY` | `false` | 只给 `FOCUS_SKUS` 存历史，省容量（见下） |

大 SKU 量下可开 `HISTORY_FOCUS_ONLY` 只记重点 SKU 的历史。
**它的降级方向是"可能多报、不会漏报"**：长尾 SKU 查不到基线就一律放行，退回纯环比 ——
跟"为了省空间把告警吃掉"是两回事。开了却没配 `FOCUS_SKUS` 会打 WARNING。

### 四档防误报

- `current is None`（这轮没取到数）→ 不告警、也不写进基线。数据缺失 ≠ 跌到 0。
- `previous < min_previous` → 不做环比。否则「1 单 → 0 单」就是 -100%，夜间必误报。
- 相对同时段基线属正常波动 → 抑制（见上）。
- 库存按 **SKU 维度逐项检测**，避免个别 SKU 暴跌被平台总量平均掉。

降幅达到阈值 2 倍判定为「严重」，否则为「警告」。

## 数据说明

所有数据保存在浏览器 `localStorage`，刷新不丢失，
清除浏览器数据或点击设置页「重置数据」即可恢复初始演示数据。
演示模式不会向任何外部地址发起请求。

## 多店铺（防关联多账号）

一个控制台管多家店，数据按 `shop_id` 完全隔离。

### 为什么是「分库」而不是「每条记录加 shop_id 字段」

给每条记录加字段，意味着每个视图的每次查询都要记得带店铺过滤 ——
**漏掉任何一处就是串店**（把 A 店的商品显示在 B 店），而且这种错不报错、
只在数据上体现，属于最难查的一类问题。分库之后，内存里的那份数据
本身就是「当前店铺的全部数据」，商品 / 任务 / 告警 / 日志 / 指标 /
凭证 / 告警渠道 / 图片库全部天然隔离，视图层一行都不用改。

### 存储布局

```
ecom_sop_shops                  # 店铺注册表（全局唯一）
ecom_sop_active_shop            # 当前店铺 id
ecom_sop_admin_v1::<shop_id>    # 某家店的完整数据
```

> 老版本的单店数据（键名 `ecom_sop_admin_v1`）在首次打开时会**整体搬进「主店」**，
> 不是重建 —— 升级不会丢商品、任务、告警和凭证。

### 后端按店隔离

在「平台与凭证」页填好配置后点**下载 .env**，导出的文件里带 `SHOP_ID`。
后端用它隔离三处数据：

| 位置 | 隔离方式 |
| --- | --- |
| 状态文件 | `state_<SHOP_ID>.json`（游标 / LLM 冷却 / 告警去重 / 上架幂等 / 心跳） |
| 指标历史库 | `metric_history.shop_id`（基线对比按店取样，不会拿别家的量当基线） |
| 图片库 | `image_assets.shop_id`（唯一键是 `(shop_id, sha256)`，同一张图可各自登记） |

同一台机器跑多家店时，给每个 crontab 行设不同的 `SHOP_ID` 即可：

```bash
# 两家店各自一条 crontab，数据完全隔离
0 * * * * cd /path/to/python_backend && SHOP_ID=shop_us python3 main.py monitor
0 * * * * cd /path/to/python_backend && SHOP_ID=shop_uk python3 main.py monitor
```

图片库 API 的每个接口都接受 `?shop_id=`（前端图片库页面会自动带上当前店铺）：

```bash
curl "http://127.0.0.1:8765/api/images/stats?shop_id=shop_us"
python main.py images stats --shop shop_uk
```

`SHOP_ID=default` 时状态文件保持老路径 `state.json`，已在跑的部署升级后
能直接读到原有位点，不会因为换文件名就从「首次运行」重来。

## 安全须知（务必阅读）

这套系统是**纯前端演示 + 本地脚本**的定位，不是多租户 SaaS，接入真实密钥前请先看清边界：

1. **演示账号 `admin / admin123` 只做前端校验，不是安全机制。**
   账号密码写死在前端 `js` 里，任何人打开页面源码都能看到；它只用来区分「游客视图/管理视图」，
   **不提供任何真实的访问控制**。如果这个页面被部署到公网，别人不需要密码也能读到页面里的全部逻辑与配置。

2. **凭证不要留在浏览器里。**
   在「平台与凭证」页填写的亚马逊 / 拼多多密钥、webhook 地址，会随其他设置一起存进 `localStorage`
   （明文、无加密，同域下任何 JS 都能读取）。因此：
   - 演示/试用随便填，**生产环境不要在这台机器的浏览器里填真实密钥**；
   - 真实运行请用 `python_backend/`，密钥走环境变量或 `.env`（`.env` 已在 `.gitignore` 中），不要提交到仓库；
   - 换机器、交接电脑、或只是「试了一下」，记得点设置页的「重置数据」清掉。

3. **「导出 .env」导出的是明文。**
   导出的文件里包含你填过的密钥，请当作密钥本身保管，不要丢进聊天窗口或网盘。

4. **公网部署前请加一层真正的鉴权。**
   如果要给团队用，至少放在内网 / 加 HTTP Basic 或 SSO 网关；本项目自身不提供服务端鉴权与审计。

## 开发与自测

本仓库**无需构建**：前端是纯静态文件，双击 `index.html` 即可运行。

```bash
# 1) 前端冒烟测试（验证 10 个页面渲染 + Listing 全流程 + 多店铺隔离）
npm install        # 安装 jsdom 开发依赖
npm test          # 等价于 node smoke_test.js，应输出 PASS: 38  FAIL: 0

# 2) Python 后端回归测试（不需要装任何依赖，测试内部用桩替换 requests/botocore）
python -m unittest discover -s python_backend/tests -v   # 68 项，覆盖历次修复的 bug
#    其中 test_rules_sync.py 专门盯「前后端规则是否同源」，改了 shared/ 但忘了
#    重跑 tools/gen_listing_rules.py 会在这里失败
#    test_history.py 盯基线对比；其中被抑制的用例都配了反向验证
#    （关掉基线后同一份数据必须重新告警，否则说明基线是死代码）
#   或：npm run test:py      （需本机有 python 命令）
#   跑全部：npm run test:all

# 3) 接真实平台 API 时才需要
cd python_backend
pip install -r requirements.txt
python main.py --help
```

### 查指标历史（基线用）

每轮监控会把各指标取值写进 `python_backend/history.db`（SQLite，只追加不修改）。
新部署后要先跑几轮攒样本，基线对比才会生效——样本不够时系统**不会**抑制任何告警。

```bash
python main.py history series                     # 列出所有指标序列与样本数
python main.py history recent amazon stock:S1 20  # 某指标最近的取值
python main.py history baseline amazon orders     # 该指标当前时段的基线值
python main.py history prune                      # 清理超过保留期的历史
```

排查「为什么没告警」时先看 `series`：样本数为 0 说明历史还没攒起来，
基线压根没参与判断，此时的告警行为就是纯环比。

推送到 GitHub 后会自动跑 [CI](./.github/workflows/ci.yml)：三个 Python 版本编译检查 + 回归测试，
以及前端冒烟测试。

### 别漏了心跳检查

监控最大的风险是**它自己挂了而你不知道** —— crontab 被覆盖、机器重启后 cron 没起来、
进程被 OOM kill，这些情况下系统不会有任何异常，只是"安静地不再监控"。
所以每轮监控会写心跳，另外配一条 crontab 做死信检查：

```bash
# 每小时整点跑一次监控
0 * * * * cd /path/to/ecommerce-sop-admin/python_backend && /usr/bin/python3 main.py monitor >> monitor.log 2>&1

# 每 10 分钟做一次死信检查（注意 */10 后面必须有空格）
*/10 * * * * cd /path/to/ecommerce-sop-admin/python_backend && /usr/bin/python3 main.py health >> monitor.log 2>&1
```

`health` 发现超过 2.5 个调度周期没有成功运行就告警（退出码 1）。

**cron 的三个坑，踩中任何一个都会"静默不执行"：**

1. `*/10 * * * *` 是 5 个字段，`*/10` 和后面的 `*` 之间必须有空格。
   写成 `*/10* * * *` 只有 4 个字段，cron 会报 `bad minute` 并拒绝这一行 ——
   表现出来就是"明明配了，却从来没执行过"。
2. cron 的环境里没有你的 `PATH`，`python3` 要写绝对路径（用 `which python3` 查）。
   用 venv 就写 venv 里的那个：`/path/to/venv/bin/python`。
3. cron 的工作目录是 `$HOME`，不是你的项目目录，所以必须 `cd` 过去 ——
   否则会报找不到 `main.py`，或者更糟：读写到别处的 `state.json`。

改完务必确认一眼：

```bash
crontab -l          # 确认两行都在、格式正确
crontab -e          # 编辑
tail -f monitor.log # 看是否真的在跑
# cron 有没有真的执行，看系统日志
grep CRON /var/log/syslog        # Debian / Ubuntu
grep CRON /var/log/cron          # CentOS / RHEL
```

## 上传到 GitHub

```bash
git init
git add .
git commit -m "feat: 电商 SOP 自动化控制台（纯静态前端 + Python 后端）"
git remote add origin https://github.com/your-name/ecommerce-sop-admin.git
git push -u origin main
```

> 已内置 `.gitignore`：自动排除 `.workbuddy-ai/`（本地 AI 数据）、`node_modules/`、
> `__pycache__/`、`.env`、`listing_payloads.json`（运行产物）等，不会误传无关文件。
> 首次提交前请检查 `package.json` 里的 `repository.url` 与 LICENSE 的版权方是否需替换。

## 部署到线上

前端是纯静态站点，可一键托管到 **Hugging Face Spaces / Render / Railway** 拿到公开网址。
详见 [DEPLOY.md](./DEPLOY.md)（含三平台分步指引与 Dockerfile / render.yaml / railway.json 用法）。

- 🌐 **在线演示（已部署）**：https://0a4bc34f3155463184868b207d1f4d03.sg.agentos-app.run
  （演示账号 **admin / admin123**，无需任何配置即可体验完整功能）

