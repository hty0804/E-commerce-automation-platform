# 电商 SOP 自动化控制台

把亚马逊 SP-API + 拼多多开放平台的自动上架、每小时监控、异常告警（企业微信 / 钉钉）
做成一套**打开即可运行的后台管理系统**。

## 直接打开

双击 `index.html` 即可（纯静态，无需安装、无需联网、无需后端）。

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
assets/js/store.js      数据层 + 模拟执行引擎 + 异常分类/建议 + Listing 生成（localStorage 持久化）
assets/js/ui.js         Toast / Modal / 标签组件 + 纯 SVG 图表
assets/js/views.js      9 个业务页面
assets/js/app.js        路由、登录、定时调度
python_backend/         原 Python 自动化框架（SP-API / 拼多多客户端、检测、分类、Listing 生成、告警、调度）
```

## AI 能力说明（可选，默认全关）

| 能力 | 何时调用 | 不配密钥时 |
|---|---|---|
| 异常**分类** | 永不调用大模型，纯规则判定（可复现、零延迟） | 正常工作 |
| 异常处理**建议** | 告警触发后才调用，批量一次 + 冷却 | 用内置规则建议，告警照发 |
| **Listing 生成** | 上架环节按需调用 | 用规则模板出草稿，流程照样跑通 |

模型接口走 OpenAI 兼容协议（DeepSeek / 通义 / Moonshot 均可），真实调用全部在 Python 后端执行
（避免密钥暴露在浏览器与跨域问题），网页端提供模拟预览。生成结果默认**只导出不自动上架**，
先过本地校验（长度红线、违规词、必填属性）再人工确认。

## 异常检测逻辑

与 `python_backend/anomaly_detector.py` 完全一致：

```
change_ratio = (current - previous) / previous
direction = "drop"  → change_ratio <= -threshold 时告警（库存、订单量）
direction = "both"  → |change_ratio| >= threshold 时告警（价格）
```

库存按 **SKU 维度逐项检测**，避免个别 SKU 暴跌被平台总量平均掉。
降幅达到阈值 2 倍判定为「严重」，否则为「警告」。

## 数据说明

所有数据保存在浏览器 `localStorage`（键名 `ecom_sop_admin_v1`），刷新不丢失，
清除浏览器数据或点击设置页「重置数据」即可恢复初始演示数据。
演示模式不会向任何外部地址发起请求。

## 开发与自测

本仓库**无需构建**：前端是纯静态文件，双击 `index.html` 即可运行。

```bash
# 1) 前端冒烟测试（验证 9 个页面渲染 + Listing 全流程，可选）
npm install        # 安装 jsdom 开发依赖
npm test          # 等价于 node smoke_test.js，应输出 PASS: 29  FAIL: 0

# 2) Python 后端（接真实平台 API 时才需要）
cd python_backend
pip install -r requirements.txt
python main.py --help
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

