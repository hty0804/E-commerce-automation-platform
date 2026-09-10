"""
配置文件 —— 所有密钥从环境变量读取,不要把密钥硬编码进代码里。
本地开发可以配合 python-dotenv 用 .env 文件加载;线上部署直接在系统/容器里设置环境变量。
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _int(name: str, default: int) -> int:
    """
    安全地读整数环境变量。

    以前直接写 int(os.getenv("X", "60")):环境变量一旦填成 "60 " 或 "abc",
    会在 **import 阶段** 就抛 ValueError —— 整个进程起不来,
    crontab 里表现为静默失败(日志里只有一行 ImportError,没有任何告警)。
    这里解析失败就退回默认值并打日志,宁可参数不对也不要整个监控挂掉。
    """
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        print(f"[config] 警告: {name}={raw!r} 不是合法整数,已回退为默认值 {default}")
        return default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(str(raw).strip())
    except ValueError:
        print(f"[config] 警告: {name}={raw!r} 不是合法数值,已回退为默认值 {default}")
        return default


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# ------------------ 亚马逊 SP-API ------------------
# 参考: https://developer-docs.amazon.com/sp-api/
AMAZON_REFRESH_TOKEN = os.getenv("AMAZON_REFRESH_TOKEN")
AMAZON_CLIENT_ID = os.getenv("AMAZON_CLIENT_ID")
AMAZON_CLIENT_SECRET = os.getenv("AMAZON_CLIENT_SECRET")
AMAZON_AWS_ACCESS_KEY = os.getenv("AMAZON_AWS_ACCESS_KEY")
AMAZON_AWS_SECRET_KEY = os.getenv("AMAZON_AWS_SECRET_KEY")
AMAZON_SELLER_ID = os.getenv("AMAZON_SELLER_ID")
AMAZON_MARKETPLACE_ID = os.getenv("AMAZON_MARKETPLACE_ID", "ATVPDKIKX0DER")  # 默认美国站
AMAZON_REGION = os.getenv("AMAZON_REGION", "us-east-1")
AMAZON_ENDPOINT = os.getenv("AMAZON_ENDPOINT", "https://sellingpartnerapi-na.amazon.com")

# ------------------ 拼多多开放平台 ------------------
# 参考: https://open.pinduoduo.com/application/document/api
PDD_CLIENT_ID = os.getenv("PDD_CLIENT_ID")
PDD_CLIENT_SECRET = os.getenv("PDD_CLIENT_SECRET")
PDD_ACCESS_TOKEN = os.getenv("PDD_ACCESS_TOKEN")  # 商家授权后拿到的 access_token
PDD_API_ENDPOINT = os.getenv("PDD_API_ENDPOINT", "https://gw-api.pinduoduo.com/api/router")

# ------------------ 告警渠道 ------------------
WECOM_WEBHOOK_URL = os.getenv("WECOM_WEBHOOK_URL")      # 企业微信群机器人 webhook
DINGTALK_WEBHOOK_URL = os.getenv("DINGTALK_WEBHOOK_URL")  # 钉钉群机器人 webhook
DINGTALK_SECRET = os.getenv("DINGTALK_SECRET")          # 钉钉机器人「加签」密钥(如果开启了加签)

# ------------------ 异常检测阈值(可按需调整) ------------------
INVENTORY_DROP_THRESHOLD = _float("INVENTORY_DROP_THRESHOLD", 0.3)   # 库存较上次下降超过 30% 视为异常
ORDER_COUNT_DROP_THRESHOLD = _float("ORDER_COUNT_DROP_THRESHOLD", 0.5)  # 每小时订单量下降超过 50% 视为异常

# 订单量的**绝对量下限**:基线低于这个量不做环比。
# 否则"1 单 → 0 单"就是 -100%,夜间/淡季低流量时段必然误报,
# 报多了人就不看告警了 —— 误报比漏报更伤系统。建议设为日均单小时的 20%~30%。
ORDER_MIN_PREVIOUS = _float("ORDER_MIN_PREVIOUS", 5)

# ------------------ 状态文件 ------------------
# 用绝对路径:以前是相对路径 "state.json",实际落在哪取决于 crontab 的 CWD,
# crontab 里少写一个 cd 就会在别处生成新文件,表现为"每次都是首次运行、永远不告警"。
STATE_FILE = os.getenv("STATE_FILE") or os.path.join(_HERE, "state.json")

# ------------------ 大模型建议(可选,默认关闭) ------------------
# 不配置 LLM_API_KEY 时完全不发起任何请求,告警照常发送(用内置规则建议)。
# 注意:大模型只用于「生成建议」,不参与异常判定 —— 判定必须是确定性的。
LLM_ENABLED = os.getenv("LLM_ENABLED", "false").lower() in ("1", "true", "yes")
LLM_API_KEY = os.getenv("LLM_API_KEY")
# 任何 OpenAI 兼容接口都可以(DeepSeek / 通义 / Moonshot / OpenAI 等)
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
LLM_TIMEOUT = _int("LLM_TIMEOUT", 20)            # 超时秒数,超时自动降级到规则建议
LLM_COOLDOWN_MIN = _int("LLM_COOLDOWN_MIN", 60)  # 同类异常多久内不重复调用
LLM_MAX_INCIDENTS = _int("LLM_MAX_INCIDENTS", 20)  # 单次最多送多少条异常

# ------------------ Listing 生成(可选,复用上面的大模型配置) ------------------
# 这是整套方案里性价比最高的大模型场景:把中文商品信息转成可直接上架的英文 Listing。
# 不配 LLM_API_KEY 时用规则模板出草稿,流程照样跑得通。
LISTING_BRAND = os.getenv("LISTING_BRAND", "")  # 默认品牌名,留空则用商品自带的 brand
# 生成后是否直接调用平台上架接口。默认 False —— 生成是低成本可逆的,上架不是,先过一遍人眼。
LISTING_AUTO_PUBLISH = _bool("LISTING_AUTO_PUBLISH")

# ------------------ 生图 skill(可选,默认关闭) ------------------
# 给大模型提供一个"生图工具",让它能基于 Listing 自主决定出图。
# 不配置 IMAGE_API_KEY 时**完全不发起任何请求**,工具会返回"未配置"结果,流程不中断。
IMAGE_ENABLED = _bool("IMAGE_ENABLED")
IMAGE_API_KEY = os.getenv("IMAGE_API_KEY")
# 供应商预设:ark(火山方舟/豆包) | openai | custom
#   ark    —— 用火山方舟 doubao-seedream,自动套用它的字段(组图、水印、尺寸规则)
#   openai —— 标准 OpenAI /images/generations 格式(n + negative_prompt)
#   custom —— 什么都不帮你填,完全按下面的环境变量来
# 预设只填充**留空**的字段,显式设置了的环境变量永远优先。
IMAGE_PROVIDER = os.getenv("IMAGE_PROVIDER", "ark").strip().lower()
# 任何兼容的生图接口都可以(火山方舟 / Seedance / 通义万相 / 即梦 / SD WebUI 等)
IMAGE_API_BASE_URL = os.getenv("IMAGE_API_BASE_URL", "")
IMAGE_API_PATH = os.getenv("IMAGE_API_PATH", "/images/generations")
IMAGE_API_MODEL = os.getenv("IMAGE_API_MODEL", "")   # 留空则用 provider 预设的默认模型
IMAGE_TIMEOUT = _int("IMAGE_TIMEOUT", 60)            # 生图比文本慢,默认给 60 秒
IMAGE_DEFAULT_COUNT = _int("IMAGE_DEFAULT_COUNT", 4)  # 不指定时每张图出几套
# 方舟的 watermark 默认是 True —— 带水印的图不能当亚马逊主图,这里默认关掉。
# (只有 provider=ark 时会下发该字段)
IMAGE_WATERMARK = _bool("IMAGE_WATERMARK", False)
# 方舟不支持 negative_prompt 字段。默认 True:把负向提示词拼进 prompt 末尾;
# 设为 False 则完全丢弃(提示词更干净,但水印/文字更可能出现)。
IMAGE_NEGATIVE_IN_PROMPT = _bool("IMAGE_NEGATIVE_IN_PROMPT", True)
# 风格模板覆盖/扩展:JSON 对象,key 为风格名。留空则用内置模板。
# 例: {"my_style": {"label":"我的风格","prompt":"...","negative":"...","aspect_ratio":"1:1"}}
IMAGE_STYLES_JSON = os.getenv("IMAGE_STYLES_JSON", "")
# 校验有 error 级问题时跳过上架(超长、违规词、必填属性缺失)。强烈建议保持 True。
LISTING_SKIP_ON_ERROR = _bool("LISTING_SKIP_ON_ERROR", True)

# ------------------ 调度 ------------------
MONITOR_INTERVAL_MIN = _int("MONITOR_INTERVAL_MIN", 60)  # 监控调度间隔(分钟),需与 crontab 一致

# ------------------ 大数据量抓取策略(见 README「容量与性能」) ------------------
# 全量扫描的间隔(小时)。SKU 多时不必每小时全量:日常走增量,每隔 N 小时做一次全量对账。
INVENTORY_FULL_SCAN_HOURS = _float("INVENTORY_FULL_SCAN_HOURS", 6)
# 增量拉取的回看冗余(分钟)。官方按"变更时间"返回,留冗余防止时钟偏差/写入延迟导致漏数据。
INVENTORY_LOOKBACK_MINUTES = _int("INVENTORY_LOOKBACK_MINUTES", 90)
# 翻页上限,防止接口异常时无限循环。
INVENTORY_MAX_PAGES = _int("INVENTORY_MAX_PAGES", 2000)
# 重点 SKU(逗号分隔):爆款、易断货的品,每轮都单独查一次,不受增量窗口影响。
FOCUS_SKUS = [s.strip() for s in os.getenv("FOCUS_SKUS", "").split(",") if s.strip()]

# ------------------ 网络与重试 ------------------
# 单次退避等待的上限(秒)。服务端返回的 Retry-After 不设上限时可能给 3600,
# 照睡会把整个调度窗口吃掉,后面几轮全部积压。
MAX_RETRY_WAIT = _int("MAX_RETRY_WAIT", 60)
# 拼多多翻页间隔(秒)。避免连续翻页触发限流。
PDD_PAGE_INTERVAL = _float("PDD_PAGE_INTERVAL", 0.6)

# ------------------ 告警去重 ------------------
# 同一类告警在冷却期内只发一次(分钟),0 表示不去重。
# 慢性问题(如任务长期超时)每轮都发会刷屏,刷到最后就没人看告警了。
ALERT_DEDUPE_MIN = _int("ALERT_DEDUPE_MIN", 60)

# ------------------ 指标历史 ------------------
# state.json 每个 key 只存"上一次的值",历史全丢 —— 既查不了历史,
# 也没法做同时段对比。这里单独开一个 append-only 的 SQLite 库存历史。
# 注意:游标 / LLM 冷却 / 上架幂等这些仍留在 state.json,不迁过来 ——
# 它们靠文件锁已经能并发安全了,迁库只是增加风险,没有收益。
HISTORY_DB = os.getenv("HISTORY_DB") or os.path.join(_HERE, "history.db")
# 历史保留天数。**实测**:单行约 132.6 字节,每指标每天 24 条 ——
#   1,000 个指标 × 90 天 = 216 万行 ≈ 273 MB
#   10,000 个指标 × 90 天 = 2,160 万行 ≈ 2.7 GB
#   50,000 个指标 × 90 天 ≈ 13.7 GB(不建议,见 HISTORY_FOCUS_ONLY)
# 万级指标以内用默认 90 天没问题;再大就缩短保留期,或只给重点 SKU 存历史。
HISTORY_RETENTION_DAYS = _int("HISTORY_RETENTION_DAYS", 90)

# ------------------ 生图转存(图库的底座) ------------------
# 火山方舟返回的图片 URL **只有 24 小时有效期**,不转存第二天就全是死链。
# 所以生完必须立刻下载落盘,图库存本地路径而不是 URL。
IMAGE_STORE_DIR = os.getenv("IMAGE_STORE_DIR") or os.path.join(_HERE, "images")
# 单张上限。方舟 4K 图 JPEG 一般 2~5 MB,20 MB 足够宽松,防的是异常大文件把磁盘写满。
IMAGE_STORE_MAX_BYTES = _int("IMAGE_STORE_MAX_BYTES", 20 * 1024 * 1024)
IMAGE_STORE_TIMEOUT = _int("IMAGE_STORE_TIMEOUT", 60)  # 下载单张的超时秒数
# 图片库索引与图片文件分离:图片体积大放 IMAGE_STORE_DIR,SQLite 只记元数据和路径。
IMAGE_LIBRARY_DB = os.getenv("IMAGE_LIBRARY_DB") or os.path.join(_HERE, "image_library.db")
# 少于这个数量的爆款图不参与风格反哺,防止单张偶然好图劫持全店风格。
IMAGE_HOT_MIN_SAMPLES = _int("IMAGE_HOT_MIN_SAMPLES", 3)
IMAGE_HOT_FEEDBACK_MAX_CHARS = _int("IMAGE_HOT_FEEDBACK_MAX_CHARS", 500)

# 只给重点 SKU 存历史。
# 历史**唯一**的用途是算基线,而长尾 SKU 本来交易就少、告警也少,存它的历史
# 收益很低,却占了绝大部分容量(它们是数量上的大头)。
# 开启后只记录 FOCUS_SKUS 里的 SKU;其余 SKU 不写历史 → 基线样本不足 →
# **一律放行**(退回纯环比)。这是安全降级:可能多报,不会漏报。
HISTORY_FOCUS_ONLY = _bool("HISTORY_FOCUS_ONLY", False)

# ------------------ 基线对比(降误报) ------------------
# 环比(和上一小时比)最大的问题是**上一小时本身可能就不正常**:
# 凌晨 2 点做个闪购冲到 80 单,3 点回落到常态的 30 单,环比就是 -62%,
# 于是每天这个点都来一条假告警。
# 基线对比是拿"历史上同一时段的正常水平"做参照,这种抖动就能被正确抑制。
BASELINE_ENABLED = _bool("BASELINE_ENABLED", True)
# 回看天数:取最近 N 天、同一小时(±1 小时窗口)的样本取中位数。
# 默认区分星期几时,7 天只会拿到同一星期几的 1 条样本,通常达不到最少 3 条;
# 生产环境建议通过环境变量设为 28 天左右,约有 4 条样本,还能容忍一两次采集缺失。
BASELINE_LOOKBACK_DAYS = _int("BASELINE_LOOKBACK_DAYS", 7)
# 样本不足时不做基线判断,退回纯环比 —— 宁可不抑制,也不要误抑制真异常。
BASELINE_MIN_SAMPLES = _int("BASELINE_MIN_SAMPLES", 3)
# 基线这道闸的松紧:要求「相对基线的变动」达到 阈值 × 该系数 才放行。
#   1.0 = 与阈值同口径(环比和基线都要超阈值才告警)—— 默认值,最稳
#   0.8 = 基线变动达到阈值的 80% 就放行 → 更敏感,抑制得更少
#   1.2 = 基线变动要达到阈值的 120% 才放行 → 更保守,误报最少但可能漏报
# 调低 = 更敏感;调高 = 更保守。
BASELINE_TOLERANCE = _float("BASELINE_TOLERANCE", 1.0)
# 是否要求"星期几也相同"。跨境电商周末和工作日的订单节奏差别很大,
# 拿工作日的量去衡量周末会系统性误判。
# ⚠️ 开启后样本会少一大截(7 天回看里每个星期几只有 1 条),
#    所以默认回看 28 天左右(每个星期几约 4 条),还能容忍一两次采集缺失。
#    样本不够时会自动降级为更粗的口径,不会静默漏报 —— 见 history.baseline_with_fallback。
BASELINE_MATCH_DOW = _bool("BASELINE_MATCH_DOW", True)

# ------------------ 日志 ------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
