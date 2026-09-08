"""
listing_gen.py —— 上架环节用大模型生成 Listing(标题 / 五点 / 描述 / 关键词 / 类目属性)。

这是整套方案里「性价比最高」的一处大模型应用:
  - 输入:中文商品信息(商品名、卖点、规格参数、类目、目标站点)
  - 输出:可直接塞进 Listings Items API 的英文(或中文)Listing + 类目属性补全
原因是它属于纯文本生成任务,成熟、稳定、直接省人力,且**结果可以离线复核**——
生成完先本地校验 + 人工过一眼再上架,不会像"让模型直接改价"那样有线上风险。

设计原则(与 incident.py 一致):
  - 不配 LLM_API_KEY 时**完全不调用模型**,用内置规则模板生成一份可用草稿
  - 模型超时 / 报错 / 返回非法 JSON → 自动降级到规则草稿,绝不让上架流程卡住
  - 生成结果必须过 validate(),超长 / 违规词 / 必填属性缺失都会列出来

依赖:requests(已在 requirements.txt)。不依赖任何平台 SDK。
"""
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

import requests

import config

log = logging.getLogger(__name__)

# ============================================================
# 1. 平台文案长度红线(校验用)
# ============================================================
LIMITS = {
    "amazon": {
        "title_max": 200,        # 多数类目硬上限 200 字符,实际建议 80~150
        "title_min": 40,
        "title_soft": 150,       # 超过只是警告,不阻塞
        "bullet_max": 255,       # 每条五点硬上限
        "bullet_count": 5,
        "desc_max": 2000,
        "keyword_bytes": 250,    # 后台搜索词总字节上限
        "lang": "en",
    },
    "pdd": {
        "title_max": 60,         # 拼多多商品标题建议 30~60 个汉字
        "title_min": 8,
        "title_soft": 60,
        "bullet_max": 200,
        "bullet_count": 5,
        "desc_max": 1500,
        "keyword_bytes": 250,
        "lang": "zh",
    },
}

# 平台明确禁止或高风险的表述(出现在标题/五点里会被 suppression 或降权)
BANNED_WORDS = [
    ("best seller", "平台禁止使用销量排名类表述"),
    ("best-seller", "平台禁止使用销量排名类表述"),
    ("#1", "平台禁止使用排名类表述"),
    ("no.1", "平台禁止使用排名类表述"),
    ("free shipping", "配送政策由平台决定,不能自行承诺"),
    ("free gift", "赠品表述易触发合规审核"),
    ("100% cure", "绝对化/医疗功效表述"),
    ("fda approved", "未取得认证不得宣称"),
    ("cure", "医疗功效表述(非个护类目慎用)"),
    ("guarantee", "绝对化承诺,易触发合规审核"),
    ("sale", "促销词不得出现在标题"),
    ("promotion", "促销词不得出现在标题"),
    ("clearance", "促销词不得出现在标题"),
    ("cheap", "低质表述影响转化与权重"),
]

# ============================================================
# 2. 类目 schema 兜底表
# 真正的必填字段请以 SP-API getDefinitionsProductType 为准(见 fetch_product_type_definition),
# 这里只作为"没拉到 schema 时"的兜底与生成提示。
# ============================================================
CATEGORY_SCHEMA: Dict[str, Dict[str, Any]] = {
    "3C数码": {
        "product_type": "ELECTRONIC_DEVICE",
        "item_type_keyword": "electronics",
        "required": ["item_name", "brand", "product_type", "color", "power_source"],
        "recommended": ["connectivity_technology", "compatible_devices", "wattage",
                        "battery_capacity", "warranty_description", "item_weight"],
        "browse_hint": "Electronics > Computers & Accessories / Cell Phones & Accessories",
    },
    "家居厨房": {
        "product_type": "HOME_PRODUCT",
        "item_type_keyword": "home",
        "required": ["item_name", "brand", "product_type", "color", "material"],
        "recommended": ["item_dimensions", "item_weight", "capacity", "is_dishwasher_safe",
                        "care_instructions", "number_of_pieces"],
        "browse_hint": "Home & Kitchen > Kitchen & Dining",
    },
    "户外运动": {
        "product_type": "SPORTING_GOODS",
        "item_type_keyword": "outdoor",
        "required": ["item_name", "brand", "product_type", "color", "material"],
        "recommended": ["item_weight", "item_dimensions", "sport_type", "water_resistance_level",
                        "capacity", "included_components"],
        "browse_hint": "Sports & Outdoors > Outdoor Recreation",
    },
    "个护健康": {
        "product_type": "BEAUTY_PRODUCT",
        "item_type_keyword": "personal-care",
        "required": ["item_name", "brand", "product_type", "item_form", "material"],
        "recommended": ["skin_type", "scent", "volume", "target_gender", "is_sensitive_skin_safe"],
        "browse_hint": "Beauty & Personal Care",
    },
    "母婴玩具": {
        "product_type": "TOY",
        "item_type_keyword": "toy",
        "required": ["item_name", "brand", "product_type", "color", "manufacturer_minimum_age"],
        "recommended": ["material", "item_dimensions", "item_weight", "educational_objective",
                        "batteries_required", "safety_warning"],
        "browse_hint": "Toys & Games",
    },
    "服饰配饰": {
        "product_type": "APPAREL",
        "item_type_keyword": "apparel",
        "required": ["item_name", "brand", "product_type", "color", "size", "material"],
        "recommended": ["fabric_type", "care_instructions", "fit_type", "target_gender",
                        "style", "occasion"],
        "browse_hint": "Clothing, Shoes & Jewelry",
    },
}
DEFAULT_SCHEMA = {
    "product_type": "PRODUCT",
    "item_type_keyword": "product",
    "required": ["item_name", "brand", "product_type"],
    "recommended": ["color", "material", "item_dimensions", "item_weight"],
    "browse_hint": "请先确认类目节点",
}


def schema_for(category: str) -> Dict[str, Any]:
    return CATEGORY_SCHEMA.get((category or "").strip(), DEFAULT_SCHEMA)


# 必填属性没给值时的保守默认值。会被 validate 标成"待确认"，别当成最终答案直接上架。
ATTR_DEFAULTS = {
    "power_source": "Battery Powered",
    "material": "Durable Material",
    "size": "One Size",
    "item_form": "Solid",
    "manufacturer_minimum_age": "36",
    "color": "As Shown",
    "brand": "Generic",
}


def fetch_product_type_definition(product_type: str, marketplace_id: Optional[str] = None) -> Optional[dict]:
    """
    用 SP-API 拉真实的类目 schema(Product Type Definitions)。
    这才是「不同类目 payload 字段不同」的唯一权威来源 —— 本地那张表只是兜底。
    失败返回 None,调用方继续用兜底表即可。
    """
    try:
        import amazon_client
        client = amazon_client.AmazonSPAPIClient()
        mid = marketplace_id or config.AMAZON_MARKETPLACE_ID
        path = f"/definitions/2020-09-01/productTypes/{product_type}"
        return client._signed_request("GET", path, params={"marketplaceIds": mid, "requirements": "LISTING"})
    except Exception as e:
        log.warning("拉取类目 schema 失败(%s),改用本地兜底表: %s", product_type, e)
        return None


def required_attributes_from_schema(definition: dict) -> List[str]:
    """从 SP-API 返回的 schema 里挑出必填属性名"""
    if not isinstance(definition, dict):
        return []
    props = (definition.get("requirementsEnforced", {}) or {}).get("attributes", [])
    if not props:
        props = definition.get("requiredAttributes") or []
    out = []
    for p in props:
        if isinstance(p, str):
            out.append(p)
        elif isinstance(p, dict) and p.get("name"):
            out.append(p["name"])
    return out


# ============================================================
# 3. 提示词
# ============================================================
def _fmt_features(product: Dict) -> str:
    f = product.get("features") or []
    if isinstance(f, str):
        f = [x.strip() for x in re.split(r"[\n;；]+", f) if x.strip()]
    return "\n".join(f"- {x}" for x in f) if f else "（未提供，请结合商品名合理推断，不要编造具体参数）"


def _fmt_specs(product: Dict) -> str:
    s = product.get("specs") or {}
    if isinstance(s, str):
        return s
    return "\n".join(f"- {k}: {v}" for k, v in s.items()) if s else "（未提供）"


def build_prompt(product: Dict, platform: str = "amazon", schema: Optional[Dict] = None) -> str:
    schema = schema or schema_for(product.get("category", ""))
    lang = LIMITS[platform]["lang"]
    target = "英文（美国站买家习惯，不要直译中文语序）" if lang == "en" else "中文（拼多多买家习惯，口语化、突出卖点）"
    title_max = LIMITS[platform]["title_max"]

    attr_hint = "、".join(schema.get("required", []) + schema.get("recommended", []))
    extra = ""
    if product.get("keywords"):
        extra += f"\n参考关键词：{product['keywords']}"
    if product.get("audience"):
        extra += f"\n目标人群：{product['audience']}"
    if product.get("price"):
        extra += f"\n售价：{product['price']}"

    return f"""你是资深跨境电商品类运营，负责把中文商品信息转成可直接上架的{platform.upper()} Listing。

【商品中文名】{product.get('name') or product.get('title') or ''}
【品牌】{product.get('brand') or '（未指定，用通用描述，不要编造品牌名）'}
【类目】{product.get('category') or '（未指定）'} → product_type: {schema.get('product_type')}
【卖点】
{_fmt_features(product)}
【规格参数】
{_fmt_specs(product)}{extra}

【输出要求】
1. 语言：{target}
2. 标题：≤{title_max} 字符，结构「品牌 + 核心关键词 + 关键属性/规格 + 适用场景」，首字母大写，禁止全大写单词、禁止促销词（sale / free shipping / best seller 等）、禁止 emoji 和特殊符号。
3. 五点描述：恰好 {LIMITS[platform]['bullet_count']} 条，每条 ≤{LIMITS[platform]['bullet_max']} 字符。每条第一词是大写的卖点标签（如 "Long Battery Life:"），后面跟具体参数支撑，不要空话。
4. 产品描述：2~3 段，讲清使用场景与差异化。
5. 搜索关键词：8~12 个，买家真实会搜的词，总字节 ≤{LIMITS[platform]['keyword_bytes']}。
6. 类目属性：按 {schema.get('product_type')} 的要求补全，重点覆盖：{attr_hint}。规格参数里已有的值直接沿用，没有的填合理默认值并视为待确认。

【输出格式】只输出一个 JSON 对象，不要 markdown 代码块，不要任何解释文字：
{{
  "title": "...",
  "bullet_points": ["...", "...", "...", "...", "..."],
  "description": "...",
  "search_keywords": ["...", "..."],
  "item_type_keyword": "{schema.get('item_type_keyword')}",
  "attributes": {{"color": "...", "material": "...", "size": "..."}}
}}"""


def _extract_json(text: str) -> Optional[dict]:
    """模型偶尔会裹 ```json 或多说两句，这里做一层容错提取"""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", t)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


# ============================================================
# 4. 模型调用（可选）+ 规则兜底
# ============================================================
def llm_available() -> bool:
    return bool(getattr(config, "LLM_ENABLED", False) and getattr(config, "LLM_API_KEY", ""))


def llm_generate(product: Dict, platform: str = "amazon", schema: Optional[Dict] = None) -> Optional[dict]:
    """调用大模型生成 Listing。任何异常都返回 None，由调用方降级。"""
    if not llm_available():
        return None

    prompt = build_prompt(product, platform, schema)
    try:
        resp = requests.post(
            f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {config.LLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": config.LLM_MODEL,
                "messages": [
                    {"role": "system", "content": "你是跨境电商 Listing 撰写专家，输出严格符合要求的 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.7,      # 生成类任务比诊断类需要更高的多样性
                "max_tokens": 2000,
            },
            timeout=getattr(config, "LLM_TIMEOUT", 20) + 20,   # 生成比诊断慢，多给 20 秒
        )
        resp.raise_for_status()
        data = resp.json()["choices"][0]["message"]["content"].strip()
        parsed = _extract_json(data)
        if not parsed or not parsed.get("title"):
            log.warning("模型返回内容不是合法 JSON 或缺少 title，降级到规则草稿: %s", data[:200])
            return None
        return parsed
    except Exception as e:
        log.warning("大模型生成 Listing 失败，降级到规则草稿: %s", e)
        return None


# --- 规则兜底：不配 Key 也能出一份能用的草稿 -------------------
_CN2EN = [
    ("无线蓝牙耳机", "Wireless Bluetooth Earbuds"), ("蓝牙耳机", "Bluetooth Earbuds"),
    ("降噪", "Noise Cancelling"), ("主动降噪", "Active Noise Cancelling"),
    ("超长续航", "Long Battery Life"), ("续航", "Battery Life"),
    ("防水", "Waterproof"), ("防摔", "Shockproof"), ("硅胶", "Silicone"),
    ("手机壳", "Phone Case"), ("保护壳", "Protective Case"), ("全包", "Full Coverage"),
    ("保温杯", "Insulated Tumbler"), ("不锈钢", "Stainless Steel"), ("大容量", "Large Capacity"),
    ("台灯", "Desk Lamp"), ("护眼", "Eye-Caring"), ("调光", "Dimmable"),
    ("充电", "Rechargeable"), ("快充", "Fast Charging"), ("便携", "Portable"),
    ("车载", "Car"), ("吸尘器", "Vacuum Cleaner"), ("无线", "Wireless"),
    ("四件套", "Bedding Set"), ("纯棉", "100% Cotton"), ("床上用品", "Bedding"),
    ("加厚", "Thickened"), ("折叠", "Foldable"), ("收纳", "Storage"),
    ("厨房", "Kitchen"), ("户外", "Outdoor"), ("运动", "Sports"),
    ("健身", "Fitness"), ("瑜伽", "Yoga"), ("露营", "Camping"),
    ("儿童", "Kids"), ("婴儿", "Baby"), ("玩具", "Toy"),
    ("套装", "Set"), ("升级", "Upgraded"), ("款", ""),
    # 通用规格
    ("黑色", "Black"), ("白色", "White"), ("灰色", "Grey"), ("蓝色", "Blue"),
    ("红色", "Red"), ("绿色", "Green"), ("粉色", "Pink"), ("透明", "Clear"),
    ("大号", "Large"), ("中号", "Medium"), ("小号", "Small"),
    ("英寸", "inch"), ("厘米", "cm"), ("毫安", "mAh"), ("瓦", "W"),
]
_SCENE_BY_CATEGORY = {
    "3C数码": "for Daily Commute and Travel",
    "家居厨房": "for Home Kitchen and Daily Use",
    "户外运动": "for Camping, Hiking and Outdoor Activities",
    "个护健康": "for Daily Personal Care",
    "母婴玩具": "for Kids and Family Fun",
    "服饰配饰": "for Everyday Wear",
}


def _strip_cjk(s: str) -> str:
    """英文站点文案里把没翻译掉的残留中文去掉，避免出现中英混排"""
    s = re.sub(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+", " ", s or "")
    return re.sub(r"\s+", " ", s).strip(" -,")


# 长词优先，避免「主动降噪」被先替换掉「降噪」而留下「主动」
_CN2EN_SORTED = sorted(_CN2EN, key=lambda kv: len(kv[0]), reverse=True)


def _cn_to_en(name: str) -> str:
    out = (name or "").strip()
    for cn, en in _CN2EN_SORTED:
        # 两侧补空格:中文原文没有词边界,不补会粘成 "ThickenedStainless Steel"
        out = out.replace(cn, f" {en} ")
    out = _strip_cjk(out)
    return out or "Product"


def fallback_generate(product: Dict, platform: str = "amazon", schema: Optional[Dict] = None) -> dict:
    """
    规则草稿：不配任何模型密钥时的默认路径。
    产出的是"能过校验、结构完整、待人工润色"的草稿，而不是空值。
    """
    schema = schema or schema_for(product.get("category", ""))
    name = product.get("name") or product.get("title") or "Product"
    brand = (product.get("brand") or "").strip()
    category = product.get("category") or ""

    if LIMITS[platform]["lang"] == "zh":
        title = name if len(name) <= LIMITS[platform]["title_max"] else name[: LIMITS[platform]["title_max"]]
        core = name
    else:
        core = _cn_to_en(name)
        scene = _SCENE_BY_CATEGORY.get(category, "")
        title = " ".join(x for x in [brand, core, scene] if x).strip(" -")

    feats = product.get("features") or []
    if isinstance(feats, str):
        feats = [x.strip() for x in re.split(r"[\n;；]+", feats) if x.strip()]
    specs = product.get("specs") or {}
    if isinstance(specs, str):
        specs = {}

    # 英文五点的惯例是「首词为大写卖点标签」，规则草稿也照这个结构来
    en_labels = ["Key Feature", "Performance", "Design", "Easy to Use", "What You Get"]
    bullets: List[str] = []
    for idx, f in enumerate(feats[: LIMITS[platform]["bullet_count"]]):
        if LIMITS[platform]["lang"] == "zh":
            bullets.append(f)
        else:
            body = _cn_to_en(f).rstrip(".")
            label = en_labels[idx] if idx < len(en_labels) else "Highlight"
            bullets.append(f"{label}: {body}.")
    fillers = {
        "en": ["Premium Material: Built with durable materials for long-lasting daily use.",
               "Easy to Use: Simple setup, no tools required — ready to go out of the box.",
               "Wide Application: Suitable for home, office and travel scenarios.",
               "Quality Assured: Each unit is inspected before shipping.",
               "What You Get: 1 x product, 1 x user manual, and friendly customer support."],
        "zh": ["品质材质：选用耐用材料，日常使用不易损坏。",
               "简单易用：免工具安装，开箱即用。",
               "适用场景广：居家、办公、出行都能用。",
               "品质保障：发货前逐件检验。",
               "包装清单：商品 x1、说明书 x1，售后无忧。"],
    }
    lang = LIMITS[platform]["lang"]
    i = 0
    while len(bullets) < LIMITS[platform]["bullet_count"]:
        bullets.append(fillers[lang][i % len(fillers[lang])])
        i += 1

    if lang == "zh":
        spec_lines = "；".join(f"{k}: {v}" for k, v in list(specs.items())[:6])
        description = f"{name}。{spec_lines}" if spec_lines else name
    else:
        spec_lines = "; ".join(f"{k}: {v}" for k, v in list(specs.items())[:6])
        description = (f"{core}. {spec_lines}." if spec_lines else f"{core}.") + \
                      " Designed for reliable everyday performance and backed by responsive customer support."

    attrs: Dict[str, str] = {}
    for k in schema.get("required", []) + schema.get("recommended", []):
        if k in specs:
            attrs[k] = str(specs[k])
    attrs.setdefault("brand", brand or "Generic")
    attrs.setdefault("item_type_keyword", schema.get("item_type_keyword", "product"))
    if "color" not in attrs:
        for cn, en in _CN2EN:
            if cn in ("黑色", "白色", "灰色", "蓝色", "红色", "绿色", "粉色", "透明") and cn in name:
                attrs["color"] = en
                break
    attrs.setdefault("color", "As Shown")

    # 必填属性没给值时填保守默认值，并记下来让 validate 标成"待确认"而不是硬报错
    defaulted: List[str] = []
    for k in schema.get("required", []):
        if k in ("item_name", "product_type"):
            continue
        if not attrs.get(k) and k in ATTR_DEFAULTS:
            attrs[k] = ATTR_DEFAULTS[k]
            defaulted.append(k)

    kws = product.get("keywords") or ""
    if isinstance(kws, str):
        kw_list = [k.strip() for k in re.split(r"[,，;；\s]+", kws) if k.strip()]
    else:
        kw_list = list(kws)
    if lang == "en":
        kw_list += [w for w in re.split(r"\s+", core) if len(w) > 3][:6]
        kw_list = [_strip_cjk(k) for k in kw_list]
    else:
        kw_list += [name]
    # 去重保序
    seen, final_kw = set(), []
    for k in kw_list:
        if k and k not in seen:
            seen.add(k)
            final_kw.append(k)

    return {
        "title": title[: LIMITS[platform]["title_max"]],
        "bullet_points": [b[: LIMITS[platform]["bullet_max"]] for b in bullets[: LIMITS[platform]["bullet_count"]]],
        "description": description[: LIMITS[platform]["desc_max"]],
        "search_keywords": final_kw[:12],
        "item_type_keyword": schema.get("item_type_keyword", "product"),
        "attributes": attrs,
        "default_attrs": defaulted,
    }


# ============================================================
# 5. 校验
# ============================================================
def _bytes_len(s: str) -> int:
    return len((s or "").encode("utf-8"))


def validate(listing: Dict, platform: str = "amazon", schema: Optional[Dict] = None) -> List[Dict[str, str]]:
    """
    本地校验。返回问题列表，每项 {level, field, msg}。
    level: error(建议改完再上架) / warn(可上架但不理想)
    """
    issues: List[Dict[str, str]] = []
    lim = LIMITS.get(platform, LIMITS["amazon"])
    schema = schema or DEFAULT_SCHEMA
    title = (listing.get("title") or "").strip()
    bullets = listing.get("bullet_points") or []
    desc = (listing.get("description") or "").strip()
    kws = listing.get("search_keywords") or []
    attrs = listing.get("attributes") or {}

    # --- 标题 ---
    if not title:
        issues.append({"level": "error", "field": "title", "msg": "标题为空"})
    else:
        if len(title) > lim["title_max"]:
            issues.append({"level": "error", "field": "title",
                           "msg": f"标题 {len(title)} 字符，超过平台上限 {lim['title_max']}"})
        elif len(title) > lim["title_soft"]:
            issues.append({"level": "warn", "field": "title",
                           "msg": f"标题 {len(title)} 字符，超过建议长度 {lim['title_soft']}，移动端会被截断"})
        if len(title) < lim["title_min"]:
            issues.append({"level": "warn", "field": "title", "msg": "标题过短，关键词覆盖不足"})
        # Python re 不支持 \p{...}，这里显式列出常见 emoji 与符号区间
        if re.search(r"[!！]{2,}|[\U0001F000-\U0001FAFF☀-➿←-⇿★☆❤♥]", title):
            issues.append({"level": "error", "field": "title", "msg": "标题含 emoji 或连续感叹号，平台禁止"})
        if lim["lang"] == "en":
            # 品牌名本身是中文/非拉丁字符很常见(注册商标),不算问题
            probe = title
            for b in {attrs.get("brand", ""), listing.get("brand", "")}:
                if b:
                    probe = probe.replace(b, "")
            if re.search(r"[^\x00-\x7F]", probe):
                issues.append({"level": "warn", "field": "title", "msg": "英文站标题含非 ASCII 字符，请检查是否为乱码"})
        lowered = title.lower()
        for w, reason in BANNED_WORDS:
            if w in lowered:
                issues.append({"level": "error", "field": "title", "msg": f"标题含受限词「{w}」：{reason}"})
        if lim["lang"] == "en":
            caps = re.findall(r"\b[A-Z]{4,}\b", title)
            if caps:
                issues.append({"level": "warn", "field": "title",
                               "msg": f"标题含全大写单词（{'、'.join(caps)}），建议改为首字母大写"})

    # --- 五点 ---
    if len(bullets) < lim["bullet_count"]:
        issues.append({"level": "warn", "field": "bullet_points",
                       "msg": f"只有 {len(bullets)} 条五点，建议补齐 {lim['bullet_count']} 条"})
    for idx, b in enumerate(bullets, 1):
        if len(b) > lim["bullet_max"]:
            issues.append({"level": "error", "field": f"bullet_points[{idx}]",
                           "msg": f"第 {idx} 条 {len(b)} 字符，超过上限 {lim['bullet_max']}"})
        lowered = (b or "").lower()
        for w, reason in BANNED_WORDS:
            if w in lowered:
                issues.append({"level": "error", "field": f"bullet_points[{idx}]",
                               "msg": f"第 {idx} 条含受限词「{w}」：{reason}"})

    # --- 描述 ---
    if not desc:
        issues.append({"level": "warn", "field": "description", "msg": "产品描述为空，影响转化"})
    elif len(desc) > lim["desc_max"]:
        issues.append({"level": "warn", "field": "description",
                       "msg": f"描述 {len(desc)} 字符，超过建议上限 {lim['desc_max']}"})

    # --- 关键词 ---
    kw_bytes = _bytes_len(" ".join(kws))
    if kw_bytes > lim["keyword_bytes"]:
        issues.append({"level": "error", "field": "search_keywords",
                       "msg": f"搜索关键词共 {kw_bytes} 字节，超过上限 {lim['keyword_bytes']} 字节"})

    # --- 类目属性 ---
    # item_name 就是标题、product_type 在 payload 顶层单独传，都不算在 attributes 缺失里
    skip = {"item_name", "product_type"}
    defaulted = set(listing.get("default_attrs") or [])
    missing = [a for a in schema.get("required", []) if a not in skip and not attrs.get(a)]
    if missing:
        issues.append({"level": "error", "field": "attributes",
                       "msg": "缺少类目必填属性：" + "、".join(missing)})
    if defaulted:
        issues.append({"level": "warn", "field": "attributes",
                       "msg": "以下属性用了默认值，上架前请核实：" + "、".join(sorted(defaulted))})
    missing_opt = [a for a in schema.get("recommended", []) if a not in skip and not attrs.get(a)]
    if missing_opt:
        issues.append({"level": "warn", "field": "attributes",
                       "msg": "推荐属性未填（影响搜索曝光）：" + "、".join(missing_opt[:6])})

    return issues


def has_error(issues: List[Dict[str, str]]) -> bool:
    return any(i["level"] == "error" for i in issues)


# ============================================================
# 6. 主入口
# ============================================================
def generate(product: Dict, platform: str = "amazon", use_llm: Optional[bool] = None) -> Dict:
    """
    生成一条 Listing。返回:
      {sku, platform, source, listing, issues, has_error, elapsed_ms, degraded_reason}
    source = llm | rule；失败自动降级且 degraded_reason 会写明原因。
    """
    t0 = time.time()
    platform = platform if platform in LIMITS else "amazon"
    schema = schema_for(product.get("category", ""))

    if use_llm is None:
        use_llm = llm_available()

    degraded = None
    listing = None
    source = "rule"

    if use_llm:
        listing = llm_generate(product, platform, schema)
        if listing:
            source = "llm"
        else:
            degraded = "模型不可用或返回非法结果，已自动降级到规则草稿"
    else:
        degraded = "未启用大模型或未配置 LLM_API_KEY，使用规则草稿"

    if not listing:
        listing = fallback_generate(product, platform, schema)

    # 兜底补齐字段，避免下游 KeyError
    listing.setdefault("bullet_points", [])
    listing.setdefault("description", "")
    listing.setdefault("search_keywords", [])
    listing.setdefault("attributes", {})
    listing.setdefault("item_type_keyword", schema.get("item_type_keyword", "product"))

    issues = validate(listing, platform, schema)
    return {
        "sku": product.get("sku", ""),
        "platform": platform,
        "source": source,
        "listing": listing,
        "issues": issues,
        "has_error": has_error(issues),
        "elapsed_ms": int((time.time() - t0) * 1000),
        "degraded_reason": degraded,
    }


def batch_generate(products: List[Dict], platform: str = "amazon") -> List[Dict]:
    """批量生成。顺序执行即可 —— 上架是低频动作，没必要为了省几秒引入并发复杂度。"""
    return [generate(p, platform) for p in products]


# ============================================================
# 7. 转成平台 Payload
# ============================================================
def to_spapi_payload(listing: Dict, sku: str, product_type: str,
                     marketplace_id: Optional[str] = None) -> Dict:
    """转成 Listings Items API (PUT /listings/2021-08-01/items/{sellerId}/{sku}) 的 body"""
    mid = marketplace_id or config.AMAZON_MARKETPLACE_ID
    attrs: Dict[str, Any] = {}
    for k, v in (listing.get("attributes") or {}).items():
        if v in (None, "", []):
            continue
        attrs[k] = [{"value": v, "marketplace_id": mid}]

    attrs["item_name"] = [{"value": listing.get("title", ""), "marketplace_id": mid}]
    if listing.get("bullet_points"):
        attrs["bullet_point"] = [{"value": b, "marketplace_id": mid} for b in listing["bullet_points"]]
    if listing.get("description"):
        attrs["product_description"] = [{"value": listing["description"], "marketplace_id": mid}]
    if listing.get("item_type_keyword"):
        attrs["item_type_keyword"] = [{"value": listing["item_type_keyword"], "marketplace_id": mid}]

    return {
        "productType": product_type,
        "requirements": "LISTING",
        "attributes": attrs,
    }


def to_pdd_payload(listing: Dict, goods_name: str, price_fen: int, quantity: int) -> Dict:
    """转成拼多多 goods.add 的简化 payload（字段以开放平台文档为准）"""
    return {
        "goods_name": listing.get("title") or goods_name,
        "goods_desc": listing.get("description", ""),
        "price": price_fen,
        "quantity": quantity,
        "keywords": ",".join(listing.get("search_keywords", [])[:8]),
        "attributes": listing.get("attributes", {}),
    }


def search_keywords_bytes(listing: Dict) -> str:
    """后台搜索词拼接后的字节串，SUPPRESS 掉重复词后返回"""
    kws = listing.get("search_keywords") or []
    seen, out = set(), []
    for k in kws:
        k = (k or "").strip()
        if k and k.lower() not in seen:
            seen.add(k.lower())
            out.append(k)
    s = " ".join(out)
    while _bytes_len(s) > LIMITS["amazon"]["keyword_bytes"]:
        out.pop()
        s = " ".join(out)
    return s


# ============================================================
# 8. 预览（CLI / 日志用）
# ============================================================
def render_preview(result: Dict) -> str:
    l = result["listing"]
    lines = [
        f"[{result['platform']}] {result['sku']}  ·  来源：{'🤖 大模型' if result['source'] == 'llm' else '📋 规则草稿'}  ·  {result['elapsed_ms']}ms",
        "",
        f"标题：{l.get('title', '')}",
        "",
        "五点描述：",
    ]
    for i, b in enumerate(l.get("bullet_points", []), 1):
        lines.append(f"  {i}. {b}")
    lines += ["", f"描述：{l.get('description', '')}", "",
              f"关键词：{search_keywords_bytes(l)}", "", "属性："]
    for k, v in (l.get("attributes") or {}).items():
        lines.append(f"  - {k}: {v}")
    if result["issues"]:
        lines += ["", "校验结果："]
        for i in result["issues"]:
            flag = "❌" if i["level"] == "error" else "⚠️"
            lines.append(f"  {flag} [{i['field']}] {i['msg']}")
    else:
        lines += ["", "校验结果：✅ 无问题"]
    if result.get("degraded_reason"):
        lines += ["", f"（{result['degraded_reason']}）"]
    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    demo = {
        "sku": "AMZ-1001",
        "name": "无线蓝牙耳机 主动降噪 超长续航 防水",
        "brand": "SoundCore",
        "category": "3C数码",
        "features": "主动降噪，深度 35dB；单次续航 8 小时，配充电盒共 32 小时；IPX5 防水； Type-C 快充",
        "specs": {"color": "Black", "battery_capacity": "400mAh", "connectivity_technology": "Bluetooth 5.3"},
        "keywords": "wireless earbuds, noise cancelling earbuds, bluetooth headphones",
    }
    print(render_preview(generate(demo, "amazon")))
