"""
image_gen.py —— 生图 skill(给大模型调用的工具)

这个模块解决三件事:

  1. **给大模型一个"生图工具"**:以 OpenAI 兼容的 function-calling 格式暴露
     `generate_product_images`,让模型能基于 Listing 自主决定出图。
     模型无关 —— DeepSeek / 通义 / Moonshot / GPT / 任意兼容接口都能用;
     不支持 tools 的模型也有纯 prompt 降级方案(见 SYSTEM_SKILL_PROMPT)。

  2. **把图片风格固定住**:风格模板(style preset)决定提示词前缀/后缀、
     负向提示词、画幅与供应商参数。模型只能从预设风格里挑,不能自由发挥 ——
     这样同一个店铺出来的图风格才是一致的。这是本模块的核心价值。

  3. **供应商适配**:默认走火山方舟(doubao-seedream),也支持通义万相 / 即梦 /
     SD 等(IMAGE_PROVIDER=openai|custom)。配置驱动,不配 IMAGE_API_KEY 时
     完全不发请求,工具返回"未配置",主流程照常跑。

  ⚠️ 方舟返回的图片 URL **只有 24 小时有效期**。要存进图库必须自己下载转存,
     不能直接把 URL 存库 —— 第二天就全是死链。

设计原则(与项目其他模块一致):
  - 密钥只从环境变量读,不硬编码。
  - 任何异常都不让主流程挂掉:生图失败只影响生图,不影响上架 / 监控 / 告警。
  - 网络层可替换,测试用桩即可覆盖全部自有逻辑。

依赖:requests(已在 requirements.txt)。
"""
import json
import logging
from typing import Any, Dict, List, Optional

import requests

import config

log = logging.getLogger(__name__)

TOOL_NAME = "generate_product_images"


# ============================================================
# 1. 风格模板 —— 固定图片风格的关键
# ============================================================
# 为什么要做成"模板"而不是让模型自由写提示词:
#   模型每次自由发挥,同一个 SKU 两次出图风格可能完全不同,
#   放到店铺里就是一盘散沙。模板把"画风、背景、光线、镜头"这些
#   主观但必须稳定的部分钉死,模型只负责填"商品主体 + 卖点"。
#
# 字段说明:
#   label        给人和模型看的中文名
#   prompt       提示词模板,{subject} / {points} 会被替换
#   negative     负向提示词(不希望出现的内容)
#   aspect_ratio 画幅,会换算成供应商的 size 参数
#   extra        直接合并进请求体的供应商私有参数(不同供应商字段名
#                不一样,留这个口子就不用改代码)
DEFAULT_STYLES: Dict[str, Dict[str, Any]] = {
    "amazon_main": {
        "label": "亚马逊白底主图",
        "prompt": (
            "Professional Amazon product main image of {subject}. "
            "Pure white background, evenly lit studio lighting, sharp focus, "
            "centered composition, high detail, e-commerce photography."
        ),
        "negative": "watermark, text, logo, props, human, cluttered background, shadow",
        "aspect_ratio": "1:1",
        "extra": {},
    },
    "scene": {
        "label": "场景氛围图",
        "prompt": (
            "Lifestyle scene photo of {subject} in a realistic everyday setting. "
            "Natural lighting, shallow depth of field, warm tone, "
            "premium commercial photography."
        ),
        "negative": "watermark, text, distorted hands, cluttered, low resolution",
        "aspect_ratio": "4:3",
        "extra": {},
    },
    "detail": {
        "label": "细节特写",
        "prompt": (
            "Extreme close-up macro detail shot of {subject}, "
            "showing material texture and craftsmanship, "
            "soft studio light, shallow depth of field."
        ),
        "negative": "watermark, text, blurry, distorted, cluttered background",
        "aspect_ratio": "1:1",
        "extra": {},
    },
    "lifestyle": {
        "label": "人物使用场景",
        "prompt": (
            "Person naturally using {subject} in daily life. "
            "Candid moment, natural light, authentic emotion, "
            "editorial photography style."
        ),
        "negative": "watermark, text, distorted face, extra limbs, low quality",
        "aspect_ratio": "3:4",
        "extra": {},
    },
}

# 画幅 -> 像素尺寸(OpenAI 兼容接口常用)。不同供应商支持的尺寸不同,
# 可以用风格模板里的 extra 覆盖 size。
_RATIO_TO_SIZE: Dict[str, str] = {
    "1:1": "1024x1024",
    "4:3": "1152x896",
    "3:4": "896x1152",
    "16:9": "1344x768",
    "9:16": "768x1344",
}

# 火山方舟(doubao-seedream)对 size 有硬性限制:
#   - 只接受 "2K"/"3K"/"4K" 或 "宽x高" 像素值
#   - 总像素必须在 [3686400, 16777216] 之间
# 1024x1024 只有 1M,**方舟会直接拒**,所以不能沿用上面那张 OpenAI 表。
# 下面这些像素值都落在合法区间内,且是干净的整数比。
_ARK_RATIO_TO_SIZE: Dict[str, str] = {
    "1:1": "2048x2048",   # 4.19M
    "4:3": "2304x1728",   # 3.98M
    "3:4": "1728x2304",   # 3.98M
    "16:9": "2848x1600",  # 4.56M
    "9:16": "1600x2848",  # 4.56M
}

# 供应商预设:只补**留空**的字段,显式配置了的环境变量永远优先。
PROVIDER_PRESETS: Dict[str, Dict[str, Any]] = {
    "ark": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "path": "/images/generations",
        "model": "doubao-seedream-4-0",
    },
    "openai": {
        "base_url": "",
        "path": "/images/generations",
        "model": "",
    },
    "custom": {"base_url": "", "path": "/images/generations", "model": ""},
}


def provider() -> str:
    name = str(getattr(config, "IMAGE_PROVIDER", "ark") or "ark").strip().lower()
    return name if name in PROVIDER_PRESETS else "custom"


def _preset(key: str) -> str:
    return str(PROVIDER_PRESETS.get(provider(), {}).get(key, "") or "")


def resolved_base_url() -> str:
    return str(getattr(config, "IMAGE_API_BASE_URL", "") or "").strip() or _preset("base_url")


def resolved_path() -> str:
    return str(getattr(config, "IMAGE_API_PATH", "/images/generations") or "").strip() \
        or _preset("path") or "/images/generations"


def resolved_model() -> str:
    return str(getattr(config, "IMAGE_API_MODEL", "") or "").strip() or _preset("model")


def endpoint() -> str:
    """最终请求的完整 URL(给排查/自检脚本用)。"""
    return resolved_base_url().rstrip("/") + "/" + resolved_path().lstrip("/")


def _size_for(ratio: str) -> str:
    table = _ARK_RATIO_TO_SIZE if provider() == "ark" else _RATIO_TO_SIZE
    default = "2048x2048" if provider() == "ark" else "1024x1024"
    return table.get(ratio, default)


def _load_custom_styles() -> Dict[str, Dict[str, Any]]:
    """
    从 IMAGE_STYLES_JSON 读取用户自定义/覆盖的风格模板。

    解析失败不能让进程起不来 —— 这里只打日志并忽略,退回内置模板。
    (和 config._int 同样的取舍:宁可参数不对,也不要整个服务挂掉。)
    """
    raw = getattr(config, "IMAGE_STYLES_JSON", "") or ""
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("IMAGE_STYLES_JSON 必须是 JSON 对象")
        return parsed
    except Exception as e:
        log.warning("IMAGE_STYLES_JSON 解析失败,已忽略自定义风格模板: %s", e)
        return {}


def all_styles() -> Dict[str, Dict[str, Any]]:
    """内置模板 + 用户自定义(同名则以用户为准)。"""
    styles = dict(DEFAULT_STYLES)
    for k, v in _load_custom_styles().items():
        if isinstance(v, dict):
            base = dict(styles.get(k, {}))
            base.update(v)
            styles[k] = base
    return styles


def get_style(name: Optional[str]) -> Dict[str, Any]:
    """取风格模板,取不到就用白底主图(最保守、最通用的主图风格)。"""
    styles = all_styles()
    if name and name in styles:
        return styles[name]
    return styles["amazon_main"]


# ============================================================
# 2. 工具定义 —— 这就是交给大模型的 skill
# ============================================================
# 用 OpenAI 兼容的 function-calling 格式,所以不绑定具体模型。
# style 用 enum 枚举出所有可用风格,是为了逼模型只能从预设风格里选,
# 不许自己编一个风格出来 —— 这正是"固定住图片风格"的落地点。
def tool_definition() -> Dict[str, Any]:
    """返回给大模型的工具定义(skill)。"""
    styles = all_styles()
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": (
                "根据商品主体与卖点生成商品图。只能使用 style 中列出的预设风格, "
                "不要自行发明风格,以保证同一店铺出图风格一致。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {
                        "type": "string",
                        "description": "商品主体描述,如 'wireless noise cancelling earbuds, black'",
                    },
                    "selling_points": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "需要在画面中体现的卖点,如 ['35dB 主动降噪', '30 小时续航']",
                    },
                    "style": {
                        "type": "string",
                        "enum": sorted(styles.keys()),
                        "description": "风格模板名,必须从枚举中选择",
                    },
                    "count": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 8,
                        "description": "生成几套图,不填则用系统默认",
                    },
                    "aspect_ratio": {
                        "type": "string",
                        "description": "画幅,如 1:1 / 4:3 / 3:4。不填则用风格模板自带的画幅",
                    },
                },
                "required": ["subject"],
            },
        },
    }


def tool_definitions() -> List[Dict[str, Any]]:
    return [tool_definition()]


# 给不支持 function calling 的模型用的降级提示词:
# 让模型直接输出一段 JSON,后端再用 parse_tool_call 解析。
SYSTEM_SKILL_PROMPT = (
    "你可以使用生图工具。如需生成商品图,请只输出如下 JSON(不要有多余文字):\n"
    '{"tool": "generate_product_images", "arguments": '
    '{"subject": "商品主体", "selling_points": ["卖点1"], "style": "风格名", "count": 4}}\n'
    "style 必须从这些值里选:" + ", ".join(sorted(DEFAULT_STYLES.keys())) + "。\n"
    "不需要生图时,正常回答即可,不要输出上面的 JSON。"
)


def parse_tool_call(text: str) -> Optional[Dict[str, Any]]:
    """
    从模型输出里解析出工具调用(降级方案用)。

    模型经常把 JSON 包在 ```json 代码块里,或者前后带解释文字,
    所以这里取第一个 '{' 到最后一个 '}' 之间尝试解析,而不是要求整段是纯 JSON。
    """
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("tool") != TOOL_NAME:
        return None
    args = parsed.get("arguments")
    return args if isinstance(args, dict) else None


# ============================================================
# 3. 请求组装 —— 把 Listing 信息 + 风格模板拼成生图请求
# ============================================================
def build_image_request(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    把模型给的参数 + 风格模板整理成一次生图请求。

    风格模板在这里真正生效:提示词 = 风格模板 + 商品主体 + 卖点。
    """
    args = args or {}
    subject = str(args.get("subject") or "").strip()
    if not subject:
        raise ValueError("subject 不能为空")

    style_name = args.get("style")
    style = get_style(style_name)

    points = args.get("selling_points") or []
    if isinstance(points, str):
        points = [points]
    points = [str(p).strip() for p in points if str(p).strip()]
    points_txt = ("; ".join(points)) if points else ""

    prompt = str(style.get("prompt", "{subject}"))
    # 模板里可能用了 {subject} / {points},没写则自动补在后面,保证信息不丢
    if "{subject}" in prompt:
        prompt = prompt.replace("{subject}", subject)
    else:
        prompt = prompt.rstrip(". ") + ". " + subject
    if "{points}" in prompt:
        prompt = prompt.replace("{points}", points_txt)
    elif points_txt:
        prompt = prompt.rstrip(". ") + ". Highlight: " + points_txt + "."

    count = args.get("count")
    try:
        count = int(count) if count is not None else int(getattr(config, "IMAGE_DEFAULT_COUNT", 4))
    except (TypeError, ValueError):
        count = int(getattr(config, "IMAGE_DEFAULT_COUNT", 4))
    count = max(1, min(8, count))

    ratio = str(args.get("aspect_ratio") or style.get("aspect_ratio") or "1:1")

    return {
        "subject": subject,
        "selling_points": points,
        "style": style_name if style_name in all_styles() else "amazon_main",
        "style_label": style.get("label", ""),
        "prompt": prompt.strip(),
        "negative": str(style.get("negative") or ""),
        "aspect_ratio": ratio,
        "size": _size_for(ratio),
        "count": count,
        "extra": dict(style.get("extra") or {}),
    }


# ============================================================
# 4. 供应商适配 —— 真正发出请求的地方
# ============================================================
def available() -> bool:
    """是否配置了生图服务。没配就完全不发请求(和 LLM_ENABLED 同样的取舍)。"""
    return bool(
        getattr(config, "IMAGE_ENABLED", False)
        and getattr(config, "IMAGE_API_KEY", "")
        and resolved_base_url()
    )


def _build_request_body(req: Dict[str, Any]) -> Dict[str, Any]:
    """
    组装请求体。按 provider 分两条路:

      - ark(火山方舟 doubao-seedream):字段跟标准 OpenAI **不兼容**,见 _build_ark_body
      - 其他:标准 OpenAI /images/generations 格式(prompt / n / size / negative_prompt)

    只是多了几个私有参数的话不用改代码 —— 在风格模板的 extra 里配上就会被合并进来,
    且 extra 放在最后,可以覆盖上面的任何字段。
    """
    if provider() == "ark":
        return _build_ark_body(req)
    return _build_openai_body(req)


def _build_openai_body(req: Dict[str, Any]) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "prompt": req["prompt"],
        "n": req["count"],
        "size": req["size"],
    }
    model = resolved_model()
    if model:
        body["model"] = model
    if req.get("negative"):
        body["negative_prompt"] = req["negative"]
    body.update(req.get("extra") or {})
    return body


def _build_ark_body(req: Dict[str, Any]) -> Dict[str, Any]:
    """
    火山方舟 doubao-seedream 的请求体。和 OpenAI 格式有 4 处关键差异,踩过才知道:

      1. **没有 n 参数**。想出多张要用组图模式:
         sequential_image_generation="auto" + options.max_images(1~15)。
         直接发 n 方舟会当未知字段处理,出图数量根本不对 —— "生成多套图"会静默退化成 1 张。
      2. **没有 negative_prompt 字段**。负向词只能拼进 prompt(IMAGE_NEGATIVE_IN_PROMPT)。
      3. **watermark 默认 True** —— 亚马逊主图带水印就是违规,这里强制下发 False。
      4. **size 只认 "2K"/"3K"/"4K" 或宽x高像素值,且总像素 ≥ 3686400**。
         常见的 1024x1024 会被直接拒(_ARK_RATIO_TO_SIZE 已避开)。
    """
    prompt = req["prompt"]
    negative = str(req.get("negative") or "")
    if negative and getattr(config, "IMAGE_NEGATIVE_IN_PROMPT", True):
        prompt = prompt.rstrip(". ") + ". Avoid: " + negative + "."

    body: Dict[str, Any] = {
        "model": resolved_model(),
        "prompt": prompt,
        "size": req["size"],
        "response_format": "url",
        "watermark": bool(getattr(config, "IMAGE_WATERMARK", False)),
    }
    count = int(req.get("count") or 1)
    if count > 1:
        body["sequential_image_generation"] = "auto"
        body["sequential_image_generation_options"] = {"max_images": count}
    else:
        body["sequential_image_generation"] = "disabled"
    # 供应商私有参数放最后,允许覆盖上面的任何字段
    body.update(req.get("extra") or {})
    return body


def _describe_api_error(data: Any) -> str:
    """
    把响应里的错误捞出来。方舟会在 data[].error 里放**单张图**的失败原因,
    顶层 error 为 null 但某一张失败是可能的 —— 只写"没解析出图片"会让人一头雾水。
    """
    if not isinstance(data, dict):
        return ""
    parts: List[str] = []
    top = data.get("error")
    if top:
        parts.append(f"error={top}")
    items = data.get("data")
    if isinstance(items, list):
        for i, it in enumerate(items):
            if isinstance(it, dict) and it.get("error"):
                parts.append(f"data[{i}].error={it['error']}")
    return "; ".join(parts)


def _extract_images(data: Any) -> List[str]:
    """
    从各家五花八门的响应里把图片取出来。

    只取我们认得的几种结构,取不到就返回空列表(调用方会给出明确报错),
    绝不假装成功。
    """
    if not isinstance(data, dict):
        return []

    # OpenAI 风格:{"data": [{"url": ...} | {"b64_json": ...}]}
    items = data.get("data")
    if isinstance(items, list):
        out: List[str] = []
        for it in items:
            if isinstance(it, dict):
                if it.get("url"):
                    out.append(str(it["url"]))
                elif it.get("b64_json"):
                    out.append("data:image/png;base64," + str(it["b64_json"]))
            elif isinstance(it, str):
                out.append(it)
        if out:
            return out

    # 常见变体:{"images": [...]}
    images = data.get("images")
    if isinstance(images, list):
        out = []
        for it in images:
            if isinstance(it, str):
                out.append(it)
            elif isinstance(it, dict):
                out.append(str(it.get("url") or it.get("image") or it.get("b64_json") or ""))
        return [x for x in out if x]

    return []


def generate(req: Dict[str, Any]) -> Dict[str, Any]:
    """
    执行一次生图。返回结构化结果,**任何失败都不抛异常**,由调用方决定怎么处理。

    返回:{"ok": bool, "images": [...], "error": str, "request": {...}}
    """
    if not available():
        return {
            "ok": False,
            "images": [],
            "error": "生图服务未配置(IMAGE_ENABLED / IMAGE_API_KEY / IMAGE_API_BASE_URL)",
            "request": req,
        }

    url = endpoint()
    body = _build_request_body(req)

    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {config.IMAGE_API_KEY}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=getattr(config, "IMAGE_TIMEOUT", 60),
        )
        # 非 2xx 一定要把状态码和响应体带出来:生图接口的错误原因(JSON 里那句人话)
        # 是排查的唯一线索,只抛一句 "HTTP Error 400" 等于没说。
        if resp.status_code >= 400:
            return {
                "ok": False,
                "images": [],
                "error": f"生图接口返回 HTTP {resp.status_code}: {resp.text[:300]}",
                "request": req,
            }
        payload = resp.json()
        images = _extract_images(payload)
        if not images:
            detail = _describe_api_error(payload)
            return {
                "ok": False,
                "images": [],
                "error": "生图接口返回成功但没解析出图片"
                         + (f"({detail})" if detail else "")
                         + f": {resp.text[:300]}",
                "request": req,
            }
        return {"ok": True, "images": images, "error": "", "request": req}
    except Exception as e:
        log.warning("生图失败: %s", e)
        return {"ok": False, "images": [], "error": f"生图失败: {e}", "request": req}


# ============================================================
# 5. 工具执行入口 —— 处理大模型发来的 tool_call
# ============================================================
def run_tool_call(arguments: Any) -> Dict[str, Any]:
    """
    执行工具调用。arguments 可以是 dict,也可以是模型传来的 JSON 字符串
    (function calling 里 arguments 常常是字符串,这点很容易踩坑)。
    """
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except Exception as e:
            return {"ok": False, "images": [], "error": f"工具参数不是合法 JSON: {e}"}
    if not isinstance(arguments, dict):
        return {"ok": False, "images": [], "error": "工具参数必须是对象"}

    try:
        req = build_image_request(arguments)
    except ValueError as e:
        return {"ok": False, "images": [], "error": str(e)}

    return generate(req)


# ============================================================
# 5.1 让大模型真正能调用这个工具(agent 循环)
# ============================================================
# 光有工具定义没用:必须把 tools 发给模型、拿到 tool_calls、执行完再把结果
# 喂回去让模型继续说。这一段就是补齐这个闭环。
def llm_available() -> bool:
    return bool(getattr(config, "LLM_ENABLED", False) and getattr(config, "LLM_API_KEY", ""))


def call_llm(messages: List[Dict[str, Any]], use_tools: bool = True) -> Optional[Dict[str, Any]]:
    """调一次大模型,返回 assistant 消息(可能带 tool_calls)。失败返回 None。"""
    if not llm_available():
        return None
    body: Dict[str, Any] = {
        "model": getattr(config, "LLM_MODEL", "deepseek-chat"),
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 2000,
    }
    if use_tools:
        body["tools"] = tool_definitions()
    try:
        resp = requests.post(
            f"{str(getattr(config, 'LLM_BASE_URL', '')).rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {config.LLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=getattr(config, "LLM_TIMEOUT", 20) + 40,   # 生图往返比纯文本慢
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]
    except Exception as e:
        log.warning("调用大模型失败: %s", e)
        return None


def run_with_image_tool(user_content: str, system: Optional[str] = None,
                        max_rounds: int = 3) -> Dict[str, Any]:
    """
    跑一轮带生图工具的对话:模型决定是否生图 → 执行 → 结果喂回 → 模型给最终答复。

    返回 {"ok", "content", "images", "used_tool", "error"}。
    不配 LLM 或调用失败都只影响生图,不让上游流程挂掉。
    """
    if not llm_available():
        return {"ok": False, "content": "", "images": [], "used_tool": False,
                "error": "大模型未配置(LLM_ENABLED / LLM_API_KEY)"}

    messages: List[Dict[str, Any]] = [
        {"role": "system",
         "content": system or "你是跨境电商运营专家,可以为商品配图。需要配图时使用生图工具。"},
        {"role": "user", "content": user_content},
    ]

    images: List[str] = []
    used_tool = False
    msg: Optional[Dict[str, Any]] = None

    for round_i in range(max_rounds):
        # 部分模型不支持 tools 参数,带 tools 失败就退化成不带工具再问一次
        msg = call_llm(messages, use_tools=True)
        if msg is None and round_i == 0:
            msg = call_llm(messages, use_tools=False)
        if msg is None:
            return {"ok": False, "content": "", "images": images, "used_tool": used_tool,
                    "error": "调用大模型失败"}

        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            break

        messages.append(msg)
        for tc in tool_calls:
            fn = tc.get("function") or {}
            if fn.get("name") != TOOL_NAME:
                continue
            used_tool = True
            result = run_tool_call(fn.get("arguments"))
            images.extend(result.get("images") or [])
            messages.append(tool_result_message(tc.get("id", ""), result))

    return {
        "ok": True,
        "content": (msg or {}).get("content") or "",
        "images": images,
        "used_tool": used_tool,
        "error": "",
    }


def tool_result_message(tool_call_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """
    把执行结果包装成可以喂回大模型的 tool message。

    失败也要如实告诉模型(而不是伪造成功),否则模型会以为图已经生成好了,
    接着编造图片描述 —— 这种"静默成功"比直接报错更难排查。
    """
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": json.dumps(
            {
                "ok": result.get("ok", False),
                "count": len(result.get("images") or []),
                "images": result.get("images") or [],
                "error": result.get("error", ""),
            },
            ensure_ascii=False,
        ),
    }
