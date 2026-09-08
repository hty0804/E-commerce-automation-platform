"""
配置文件 —— 所有密钥从环境变量读取,不要把密钥硬编码进代码里。
本地开发可以配合 python-dotenv 用 .env 文件加载;线上部署直接在系统/容器里设置环境变量。
"""
import os

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
INVENTORY_DROP_THRESHOLD = 0.3    # 库存较上次下降超过 30% 视为异常
ORDER_COUNT_DROP_THRESHOLD = 0.5  # 每小时订单量较上次下降超过 50% 视为异常

STATE_FILE = os.getenv("STATE_FILE", "state.json")  # 保存上一次监控数据,用于环比对比

# ------------------ 大模型建议(可选,默认关闭) ------------------
# 不配置 LLM_API_KEY 时完全不发起任何请求,告警照常发送(用内置规则建议)。
# 注意:大模型只用于「生成建议」,不参与异常判定 —— 判定必须是确定性的。
LLM_ENABLED = os.getenv("LLM_ENABLED", "false").lower() in ("1", "true", "yes")
LLM_API_KEY = os.getenv("LLM_API_KEY")
# 任何 OpenAI 兼容接口都可以(DeepSeek / 通义 / Moonshot / OpenAI 等)
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "20"))       # 超时秒数,超时自动降级到规则建议
LLM_COOLDOWN_MIN = int(os.getenv("LLM_COOLDOWN_MIN", "60"))  # 同类异常多久内不重复调用
LLM_MAX_INCIDENTS = int(os.getenv("LLM_MAX_INCIDENTS", "20"))  # 单次最多送多少条异常

# ------------------ Listing 生成(可选,复用上面的大模型配置) ------------------
# 这是整套方案里性价比最高的大模型场景:把中文商品信息转成可直接上架的英文 Listing。
# 不配 LLM_API_KEY 时用规则模板出草稿,流程照样跑得通。
LISTING_BRAND = os.getenv("LISTING_BRAND", "")  # 默认品牌名,留空则用商品自带的 brand
# 生成后是否直接调用平台上架接口。默认 False —— 生成是低成本可逆的,上架不是,先过一遍人眼。
LISTING_AUTO_PUBLISH = os.getenv("LISTING_AUTO_PUBLISH", "false").lower() in ("1", "true", "yes")
# 校验有 error 级问题时跳过上架(超长、违规词、必填属性缺失)。强烈建议保持 True。
LISTING_SKIP_ON_ERROR = os.getenv("LISTING_SKIP_ON_ERROR", "true").lower() in ("1", "true", "yes")

# ------------------ 调度 ------------------
MONITOR_INTERVAL_MIN = int(os.getenv("MONITOR_INTERVAL_MIN", "60"))  # 监控调度间隔(分钟),需与 crontab 一致

# ------------------ 大数据量抓取策略(见 README「容量与性能」) ------------------
# 全量扫描的间隔(小时)。SKU 多时不必每小时全量:日常走增量,每隔 N 小时做一次全量对账。
INVENTORY_FULL_SCAN_HOURS = float(os.getenv("INVENTORY_FULL_SCAN_HOURS", "6"))
# 增量拉取的回看冗余(分钟)。官方按"变更时间"返回,留冗余防止时钟偏差/写入延迟导致漏数据。
INVENTORY_LOOKBACK_MINUTES = int(os.getenv("INVENTORY_LOOKBACK_MINUTES", "90"))
# 翻页上限,防止接口异常时无限循环。
INVENTORY_MAX_PAGES = int(os.getenv("INVENTORY_MAX_PAGES", "2000"))
# 重点 SKU(逗号分隔):爆款、易断货的品,每轮都单独查一次,不受增量窗口影响。
FOCUS_SKUS = [s.strip() for s in os.getenv("FOCUS_SKUS", "").split(",") if s.strip()]
