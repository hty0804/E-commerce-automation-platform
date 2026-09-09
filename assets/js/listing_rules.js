/* ==========================================================
 * listing_rules.js —— 【自动生成，请勿手改】
 *
 * 数据源: shared/listing_rules.json
 * 生成器: tools/gen_listing_rules.py
 * 改了 JSON 之后请重跑: python tools/gen_listing_rules.py
 *
 * 这里放的是 Listing 生成与校验规则的唯一数据源,Python 后端
 * (python_backend/listing_gen.py) 加载的是同一个 JSON 文件。
 * 以前两端各硬编码一份,并且已经实际漂移过:前端漏了 best-seller /
 * cure / 100% cure 三个违规词,导致"前端显示校验通过、后端却拦截"。
 * ========================================================== */
window.LISTING_RULES = {
  "_meta": {
    "purpose": "Listing 生成与校验规则的唯一数据源。Python(python_backend/listing_gen.py) 与前端(assets/js) 都从这里加载,禁止在任何一端再硬编码副本。",
    "generated_files": [
      "assets/js/listing_rules.js  (由 tools/gen_listing_rules.py 生成,请勿手改)"
    ],
    "how_to_change": "改本文件后运行: python tools/gen_listing_rules.py ;然后跑 python_backend/tests 里的同步用例。",
    "version": 1
  },
  "limits": {
    "amazon": {
      "title_max": 200,
      "title_min": 40,
      "title_soft": 150,
      "bullet_max": 255,
      "bullet_count": 5,
      "desc_max": 2000,
      "keyword_bytes": 250,
      "lang": "en",
      "label": "英文(美国站)"
    },
    "pdd": {
      "title_max": 60,
      "title_min": 8,
      "title_soft": 60,
      "bullet_max": 200,
      "bullet_count": 5,
      "desc_max": 1500,
      "keyword_bytes": 250,
      "lang": "zh",
      "label": "中文(拼多多)"
    }
  },
  "banned_words": [
    [
      "best seller",
      "平台禁止使用销量排名类表述"
    ],
    [
      "best-seller",
      "平台禁止使用销量排名类表述"
    ],
    [
      "#1",
      "平台禁止使用排名类表述"
    ],
    [
      "no.1",
      "平台禁止使用排名类表述"
    ],
    [
      "free shipping",
      "配送政策由平台决定,不能自行承诺"
    ],
    [
      "free gift",
      "赠品表述易触发合规审核"
    ],
    [
      "100% cure",
      "绝对化/医疗功效表述"
    ],
    [
      "fda approved",
      "未取得认证不得宣称"
    ],
    [
      "cure",
      "医疗功效表述(非个护类目慎用)"
    ],
    [
      "guarantee",
      "绝对化承诺,易触发合规审核"
    ],
    [
      "sale",
      "促销词不得出现在标题"
    ],
    [
      "promotion",
      "促销词不得出现在标题"
    ],
    [
      "clearance",
      "促销词不得出现在标题"
    ],
    [
      "cheap",
      "低质表述影响转化与权重"
    ]
  ],
  "category_schema": {
    "_comment": "required 是完整必填项列表;其中 item_name / product_type 在 SP-API payload 里是顶层字段而非 attributes,前端按需过滤(top_level_fields)。",
    "3C数码": {
      "product_type": "ELECTRONIC_DEVICE",
      "item_type_keyword": "electronics",
      "required": [
        "item_name",
        "brand",
        "product_type",
        "color",
        "power_source"
      ],
      "recommended": [
        "connectivity_technology",
        "compatible_devices",
        "wattage",
        "battery_capacity",
        "warranty_description",
        "item_weight"
      ],
      "browse_hint": "Electronics > Computers & Accessories / Cell Phones & Accessories"
    },
    "家居厨房": {
      "product_type": "HOME_PRODUCT",
      "item_type_keyword": "home",
      "required": [
        "item_name",
        "brand",
        "product_type",
        "color",
        "material"
      ],
      "recommended": [
        "item_dimensions",
        "item_weight",
        "capacity",
        "is_dishwasher_safe",
        "care_instructions",
        "number_of_pieces"
      ],
      "browse_hint": "Home & Kitchen > Kitchen & Dining"
    },
    "户外运动": {
      "product_type": "SPORTING_GOODS",
      "item_type_keyword": "outdoor",
      "required": [
        "item_name",
        "brand",
        "product_type",
        "color",
        "material"
      ],
      "recommended": [
        "item_weight",
        "item_dimensions",
        "sport_type",
        "water_resistance_level",
        "capacity",
        "included_components"
      ],
      "browse_hint": "Sports & Outdoors > Outdoor Recreation"
    },
    "个护健康": {
      "product_type": "BEAUTY_PRODUCT",
      "item_type_keyword": "personal-care",
      "required": [
        "item_name",
        "brand",
        "product_type",
        "item_form",
        "material"
      ],
      "recommended": [
        "skin_type",
        "scent",
        "volume",
        "target_gender",
        "is_sensitive_skin_safe",
        "color"
      ],
      "browse_hint": "Beauty & Personal Care"
    },
    "母婴玩具": {
      "product_type": "TOY",
      "item_type_keyword": "toy",
      "required": [
        "item_name",
        "brand",
        "product_type",
        "color",
        "manufacturer_minimum_age"
      ],
      "recommended": [
        "material",
        "item_dimensions",
        "item_weight",
        "educational_objective",
        "batteries_required",
        "safety_warning"
      ],
      "browse_hint": "Toys & Games"
    },
    "服饰配饰": {
      "product_type": "APPAREL",
      "item_type_keyword": "apparel",
      "required": [
        "item_name",
        "brand",
        "product_type",
        "color",
        "size",
        "material"
      ],
      "recommended": [
        "fabric_type",
        "care_instructions",
        "fit_type",
        "target_gender",
        "style",
        "occasion"
      ],
      "browse_hint": "Clothing, Shoes & Jewelry"
    }
  },
  "default_schema": {
    "product_type": "PRODUCT",
    "item_type_keyword": "product",
    "required": [
      "item_name",
      "brand",
      "product_type"
    ],
    "recommended": [
      "color",
      "material",
      "item_dimensions",
      "item_weight"
    ],
    "browse_hint": "请先确认类目节点"
  },
  "top_level_fields": [
    "item_name",
    "product_type"
  ],
  "attr_defaults": {
    "power_source": "Battery Powered",
    "material": "Durable Material",
    "size": "One Size",
    "item_form": "Solid",
    "manufacturer_minimum_age": "36",
    "color": "As Shown",
    "brand": "Generic"
  },
  "scene_by_category": {
    "3C数码": "for Daily Commute and Travel",
    "家居厨房": "for Home Kitchen and Daily Use",
    "户外运动": "for Camping, Hiking and Outdoor Activities",
    "个护健康": "for Daily Personal Care",
    "母婴玩具": "for Kids and Family Fun",
    "服饰配饰": "for Everyday Wear"
  },
  "cn2en": [
    [
      "无线蓝牙耳机",
      "Wireless Bluetooth Earbuds"
    ],
    [
      "蓝牙耳机",
      "Bluetooth Earbuds"
    ],
    [
      "主动降噪",
      "Active Noise Cancelling"
    ],
    [
      "降噪",
      "Noise Cancelling"
    ],
    [
      "超长续航",
      "Long Battery Life"
    ],
    [
      "续航",
      "Battery Life"
    ],
    [
      "防水",
      "Waterproof"
    ],
    [
      "防摔",
      "Shockproof"
    ],
    [
      "硅胶",
      "Silicone"
    ],
    [
      "手机壳",
      "Phone Case"
    ],
    [
      "保护壳",
      "Protective Case"
    ],
    [
      "全包",
      "Full Coverage"
    ],
    [
      "保温杯",
      "Insulated Tumbler"
    ],
    [
      "不锈钢",
      "Stainless Steel"
    ],
    [
      "大容量",
      "Large Capacity"
    ],
    [
      "台灯",
      "Desk Lamp"
    ],
    [
      "护眼",
      "Eye-Caring"
    ],
    [
      "调光",
      "Dimmable"
    ],
    [
      "充电",
      "Rechargeable"
    ],
    [
      "快充",
      "Fast Charging"
    ],
    [
      "便携",
      "Portable"
    ],
    [
      "车载",
      "Car"
    ],
    [
      "吸尘器",
      "Vacuum Cleaner"
    ],
    [
      "无线",
      "Wireless"
    ],
    [
      "四件套",
      "Bedding Set"
    ],
    [
      "纯棉",
      "100% Cotton"
    ],
    [
      "床上用品",
      "Bedding"
    ],
    [
      "加厚",
      "Thickened"
    ],
    [
      "折叠",
      "Foldable"
    ],
    [
      "收纳",
      "Storage"
    ],
    [
      "厨房",
      "Kitchen"
    ],
    [
      "户外",
      "Outdoor"
    ],
    [
      "运动",
      "Sports"
    ],
    [
      "健身",
      "Fitness"
    ],
    [
      "瑜伽",
      "Yoga"
    ],
    [
      "露营",
      "Camping"
    ],
    [
      "儿童",
      "Kids"
    ],
    [
      "婴儿",
      "Baby"
    ],
    [
      "玩具",
      "Toy"
    ],
    [
      "套装",
      "Set"
    ],
    [
      "升级",
      "Upgraded"
    ],
    [
      "款",
      ""
    ],
    [
      "黑色",
      "Black"
    ],
    [
      "白色",
      "White"
    ],
    [
      "灰色",
      "Grey"
    ],
    [
      "蓝色",
      "Blue"
    ],
    [
      "红色",
      "Red"
    ],
    [
      "绿色",
      "Green"
    ],
    [
      "粉色",
      "Pink"
    ],
    [
      "透明",
      "Clear"
    ],
    [
      "大号",
      "Large"
    ],
    [
      "中号",
      "Medium"
    ],
    [
      "小号",
      "Small"
    ],
    [
      "英寸",
      "inch"
    ],
    [
      "厘米",
      "cm"
    ],
    [
      "毫安",
      "mAh"
    ],
    [
      "瓦",
      "W"
    ]
  ],
  "_comment_regex": "Python re 与 JS RegExp 的 Unicode 转义语法不同,所以这里存的是码点区间,两端各自构建正则 —— 保证两端判定范围严格一致。",
  "emoji_ranges": [
    [
      127744,
      129791
    ],
    [
      9728,
      10175
    ],
    [
      8592,
      8703
    ]
  ],
  "repeat_punct": "!！"
};
