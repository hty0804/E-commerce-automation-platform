/* ==========================================================
 * store.js —— 数据层 + 模拟执行引擎
 * 所有数据保存在 localStorage，刷新不丢失。
 * 演示模式下不发起任何网络请求，数据由内置引擎生成，
 * 逻辑与原 Python 版（anomaly_detector / main）保持一致：
 *   环比 = (当前值 - 上次值) / 上次值，超过阈值即判定异常。
 * ========================================================== */
(function (global) {
  'use strict';

  var KEY = 'ecom_sop_admin_v1';
  var SESSION_KEY = 'ecom_sop_session';

  /* ---------------- 工具函数 ---------------- */
  function uid(p) { return (p || 'id') + '_' + Math.random().toString(36).slice(2, 9); }
  function now() { return Date.now(); }
  function pad(n) { return n < 10 ? '0' + n : '' + n; }

  function fmtTime(ts) {
    var d = new Date(ts);
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
  }
  function fmtClock(ts) {
    var d = new Date(ts);
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
  }
  function fmtHour(ts) { var d = new Date(ts); return pad(d.getHours()) + ':00'; }
  function fromNow(ts) {
    var s = Math.floor((now() - ts) / 1000);
    if (s < 60) return s + ' 秒前';
    if (s < 3600) return Math.floor(s / 60) + ' 分钟前';
    if (s < 86400) return Math.floor(s / 3600) + ' 小时前';
    return Math.floor(s / 86400) + ' 天前';
  }
  function rnd(a, b) { return Math.random() * (b - a) + a; }
  function rndInt(a, b) { return Math.round(rnd(a, b)); }
  function pick(arr) { return arr[Math.floor(Math.random() * arr.length)]; }
  function clamp(v, min, max) { return Math.max(min, Math.min(max, v)); }

  /* ==========================================================
   * 异常分类 + 优化建议
   * 与 python_backend/incident.py 保持同一套语义:
   *   分类用规则(可复现),建议可由大模型生成(可选、可降级)
   * ========================================================== */
  var CATEGORIES = {
    stockout: { name: '已断货', icon: 'alert', tag: 'tag-red' },
    stockout_risk: { name: '断货风险', icon: 'warn', tag: 'tag-yellow' },
    inventory_drop: { name: '库存异常下降', icon: 'trend-down', tag: 'tag-yellow' },
    demand_drop: { name: '订单量下滑', icon: 'trend-down', tag: 'tag-orange' },
    price_anomaly: { name: '价格异常波动', icon: 'dollar', tag: 'tag-purple' },
    auth_failure: { name: '鉴权失败', icon: 'key', tag: 'tag-red' },
    rate_limit: { name: '触发接口限流', icon: 'clock', tag: 'tag-yellow' },
    api_error: { name: '接口调用失败', icon: 'plug', tag: 'tag-red' },
    listing_failed: { name: '商品上架失败', icon: 'box', tag: 'tag-red' },
    data_missing: { name: '数据缺失', icon: 'help', tag: 'tag-gray' },
    notice: { name: '系统通知', icon: 'info', tag: 'tag-blue' },
    unknown: { name: '未分类异常', icon: 'help', tag: 'tag-gray' }
  };

  var ADVICE = {
    stockout: {
      causes: ['库存归零，Listing 转为不可售', '在途补货尚未入库'],
      actions: ['确认补货单 / 入库单状态，货是否已在途', '断货期间把广告预算下调 50%~70%', '可小幅提价减缓出单，为补货争取时间（注意别丢 Buy Box）'],
      longterm: ['按「日均销量 × 补货周期 + 安全库存」设补货线，提前 30~45 天下单', '把周转快的 SKU 加入重点监控']
    },
    stockout_risk: {
      causes: ['销量突然放大（促销 / 站外 / 榜单效应）', '补货节奏跟不上'],
      actions: ['先确认是真跌还是数据滞后：比对后台可售数量', '按当前速度算剩余可售天数，不足 7 天立即补货', '同步下调广告预算，把花费集中到库存充足的款'],
      longterm: ['给该 SKU 单独设置更敏感的阈值（如 20%）', '建立安全库存水位，低于水位自动触发补货流程']
    },
    inventory_drop: {
      causes: ['正常销售波动', '批量订单 / 企业采购', '库存被错分或盘亏'],
      actions: ['核对近 24 小时订单量是否与降幅匹配', '不匹配则检查是否有盘亏、错发或库存调拨'],
      longterm: ['库存与订单做交叉校验，避免单看库存产生误报']
    },
    demand_drop: {
      causes: ['Listing 被 suppression / 变狗（主图、合规、类目审核）', 'Buy Box 丢失（被跟卖或价格失去竞争力）', '广告断投：预算耗尽、活动暂停'],
      actions: ['先看 Buy Box 是否还在，不在就查跟卖和价格', '检查 Listing 状态是否正常在售', '确认广告活动是否仍在投放、预算是否提前耗尽'],
      longterm: ['把 Buy Box 与 Listing 状态纳入监控（Notifications API，分钟级）', '订单与库存告警联动：库存没降而订单降，多半是卖不动而非断货']
    },
    price_anomaly: {
      causes: ['自动调价规则触发', '被跟卖压价', '改价时填错货币 / 单位'],
      actions: ['核对前台售价与预期是否一致，不一致立即改回', '检查是否触发了自动调价规则或被跟卖抢占'],
      longterm: ['设置价格上下限护栏，避免调价规则失控']
    },
    auth_failure: {
      causes: ['refresh_token 过期或被撤销', 'AWS IAM 密钥被轮换 / 禁用'],
      actions: ['重新走授权流程拿新的 refresh_token', '确认 IAM 用户的 AccessKey 仍有效'],
      longterm: ['把 token 过期做成主动告警，别等监控失败才发现']
    },
    rate_limit: {
      causes: ['翻页过快', '多个进程共用同一套密钥', '调度间隔短于单轮耗时'],
      actions: ['确认没有多个监控进程同时跑', '改用增量拉取，减少翻页次数'],
      longterm: ['单轮耗时超过调度间隔 80% 时自动告警']
    },
    api_error: {
      causes: ['平台侧 5xx 或网络抖动', '请求参数不合法'],
      actions: ['已自动重试，持续失败请检查参数与网络', '降低单页数据量或拆分请求'],
      longterm: ['给每类错误建独立告警通道，避免与库存告警混在一起被忽略']
    },
    listing_failed: {
      causes: ['类目必填属性缺失', '图片 / 合规材料不符合要求', 'SKU 与已存在 Listing 冲突'],
      actions: ['用 Product Type Definitions 拉取类目 schema，核对必填字段', '检查主图规格与类目要求的合规材料'],
      longterm: ['上架前用 schema 做一次本地校验，不要把错误留到线上']
    },
    data_missing: {
      causes: ['授权失效导致返回空', '接口分页 / 参数异常'],
      actions: ['不要当成库存归零处理，先确认接口是否返回了空数据', '手动跑一次监控看原始输出'],
      longterm: ['把「本轮未获取到任何数据」单独列为一种异常，不要静默跳过']
    },
    notice: { causes: ['系统正常运行提示'], actions: ['无需处理'], longterm: [] },
    unknown: { causes: ['未知'], actions: ['人工核对原始日志'], longterm: ['补充分类规则'] }
  };

  var LLM_TEMPLATES = {
    stockout: '【根因】{obj} 库存从 {prev} 直接归零，结合近 7 天日均出单稳定，最可能是 FBA 入库单延迟上架或库存被盘亏，而非真实售罄。\n【立即处理】1. 核对 Shipment 入库单是否已签收；2. 广告预算先降 60%，避免花费打在不可售 Listing 上；3. 若确认断货，小幅提价保住排名。\n【长期优化】按日均销量 × 补货周期 + 安全库存设自动补货线，提前 30~45 天下单。',
    stockout_risk: '【根因】{obj} 库存从 {prev} 降至 {cur}，降幅远超日常波动。若同期订单量并未放大，优先考虑数据同步滞后；若订单同步放大，则是真实动销加速。\n【立即处理】1. 比对后台可售数量确认真伪；2. 按当前速度算剩余可售天数，不足 7 天立即补货；3. 下调该 SKU 广告预算，把花费让给库存充足的款。\n【长期优化】给该 SKU 单独设 20% 的敏感阈值，并纳入重点监控清单。',
    inventory_drop: '【根因】{obj} 库存从 {prev} 降至 {cur}，降幅接近临界值。\n【立即处理】1. 核对近 24 小时订单是否与降幅匹配；2. 不匹配则排查盘亏、错发、库存调拨。\n【长期优化】库存与订单做交叉校验，避免单看库存产生误报。',
    demand_drop: '【根因】订单量环比大幅下滑而库存未见同步下降，说明不是断货导致，更可能是 Buy Box 丢失、Listing 被 suppression，或广告断投。\n【立即处理】1. 先看 Buy Box 是否还在，不在就查跟卖与价格；2. 检查 Listing 是否为在售状态；3. 确认广告活动预算是否提前耗尽。\n【长期优化】把 Buy Box 与 Listing 状态改用 Notifications API 做分钟级订阅，别靠小时级轮询发现。',
    price_anomaly: '【根因】{obj} 价格出现超阈值变动，多为自动调价规则触发或被跟卖压价。\n【立即处理】1. 核对前台售价是否正确；2. 检查调价规则是否被触发；3. 确认是否被跟卖抢占。\n【长期优化】设置价格上下限护栏，并对核心 SKU 开启跟卖监控。',
    auth_failure: '【根因】鉴权失败，通常是 refresh_token 过期/被撤销，或 AWS IAM 密钥被轮换。\n【立即处理】1. 重新授权获取 refresh_token；2. 确认 IAM AccessKey 仍有效；3. 检查卖家是否解除了应用授权。\n【长期优化】把 token 有效期做成主动预警。',
    rate_limit: '【根因】请求触发平台限流，通常是翻页过快或有多个进程共用同一套密钥。\n【立即处理】1. 确认 crontab 没有重复配置导致多进程并发；2. 改用增量拉取减少翻页；3. 适当拉长调度间隔。\n【长期优化】单轮耗时超过调度间隔 80% 时主动告警。',
    api_error: '【根因】接口调用失败，多为平台侧 5xx、网络抖动或请求参数异常。\n【立即处理】1. 已自动重试，若持续失败请核对参数；2. 降低单页数据量或拆分请求；3. 查看运行日志定位具体错误。\n【长期优化】给接口类错误建独立告警通道。',
    listing_failed: '【根因】上架失败，几乎都是类目必填属性缺失或图片/合规材料不达标。\n【立即处理】1. 用 Product Type Definitions 拉取该类目 schema 核对必填字段；2. 检查主图规格；3. 确认 SKU 是否与已有 Listing 冲突。\n【长期优化】上架前做一次本地 schema 校验。',
    data_missing: '【根因】本轮未获取到任何数据，可能是授权失效、参数异常或筛选条件把数据全过滤了。\n【立即处理】1. 不要当成库存归零处理；2. 手动跑一次监控看原始输出；3. 检查授权状态。\n【长期优化】把「数据缺失」单独列为一种异常，不要静默跳过。',
    unknown: '【根因】该异常未匹配到已知类型。\n【立即处理】人工核对运行日志与原始数据。\n【长期优化】补充分类规则。'
  };

  function categoryInfo(cat) { return CATEGORIES[cat] || CATEGORIES.unknown; }

  /** 分类:纯规则判断,与 Python 版 classify() 一致 */
  function classifyAlert(key, value, message, ratio) {
    var m = (message || '').toLowerCase();
    if (/invalid_grant|unauthorized|401|403|refresh_token|鉴权/.test(m)) return 'auth_failure';
    if (/429|too many requests|限流/.test(m)) return 'rate_limit';
    if (/上架失败|类目|attribute/.test(m)) return 'listing_failed';
    if (/未获取到任何|数据缺失|no data/.test(m)) return 'data_missing';
    if (/500|502|503|504|timeout|超时|调用失败/.test(m)) return 'api_error';

    key = key || '';
    if (key.indexOf('inventory:') === 0 || key.indexOf('stock:') === 0) {
      if (value === 0) return 'stockout';
      if (ratio != null && ratio <= -0.5) return 'stockout_risk';
      return 'inventory_drop';
    }
    if (key.indexOf('order') >= 0) return 'demand_drop';
    if (key.indexOf('price') >= 0) return 'price_anomaly';
    return 'unknown';
  }

  /** 生成建议。useLlm=true 时返回模拟的大模型输出,否则用内置规则建议 */
  function buildAdvice(cat, ctx, useLlm) {
    ctx = ctx || {};
    var a = ADVICE[cat] || ADVICE.unknown;
    var base = {
      rootCause: a.causes.join('；'),
      actions: a.actions.join('；'),
      longterm: (a.longterm || []).join('；')
    };
    if (useLlm) {
      var tpl = LLM_TEMPLATES[cat] || LLM_TEMPLATES.unknown;
      var text = tpl
        .replace(/\{obj\}/g, ctx.obj || '该商品')
        .replace(/\{prev\}/g, ctx.prev == null ? '—' : ctx.prev)
        .replace(/\{cur\}/g, ctx.cur == null ? '—' : ctx.cur);
      var parsed = parseLlm(text);
      return { source: 'llm', text: text, rootCause: parsed.rootCause, actions: parsed.actions, longterm: parsed.longterm, fallback: base };
    }
    return { source: 'rule', text: '', rootCause: base.rootCause, actions: base.actions, longterm: base.longterm };
  }

  /** 把【根因】【立即处理】【长期优化】三段文本拆成结构化字段 */
  function parseLlm(text) {
    var out = { rootCause: '', actions: '', longterm: '' };
    var re = /【([^】]+)】([^【]*)/g, m;
    while ((m = re.exec(text))) {
      var title = m[1], body = (m[2] || '').trim();
      if (/根因|原因/.test(title)) out.rootCause = body;
      else if (/立即处理|处理|动作/.test(title)) out.actions = body;
      else if (/长期优化|优化/.test(title)) out.longterm = body;
    }
    return out;
  }

  /* ---------------- 演示数据种子 ---------------- */
  /* ==========================================================
   * Listing 生成（上架环节）
   * 与 python_backend/listing_gen.py 同一套语义：
   *   类目 schema 决定必填属性 → 生成标题/五点/描述/关键词 → 本地校验 → 转 payload
   *   不配密钥时用规则草稿，配了才走大模型（网页端只做模拟预览，真实调用在后端）
   *
   * 规则数据全部来自 window.LISTING_RULES（由 shared/listing_rules.json 生成），
   * 与 Python 后端是同一份数据源。以前本文件另抄了一份，并且**已经实际漂移过**：
   * 前端漏了 best-seller / cure / 100% cure 三个违规词，于是出现
   * 「前端显示校验通过、后端却拦截」，而 cure 属于医疗功效类合规高危词。
   * 不要再在本文件里硬编码任何规则数据。
   * ========================================================== */
  var RULES = global.LISTING_RULES;
  if (!RULES) throw new Error('assets/js/listing_rules.js 未加载：Listing 规则缺失，请在 store.js 之前引入');

  var LIMITS = RULES.limits;
  var BANNED_WORDS = RULES.banned_words;
  var ATTR_DEFAULTS = RULES.attr_defaults;
  var TOP_LEVEL_FIELDS = RULES.top_level_fields || [];

  // 视图层用的是 camelCase（lim.titleMax），这里做一次机械转换。
  // 转换只写在这一处，视图层继续用 LISTING_LIMITS，不用改。
  var LISTING_LIMITS = {};
  Object.keys(LIMITS).forEach(function (p) {
    var l = LIMITS[p];
    LISTING_LIMITS[p] = {
      titleMax: l.title_max, titleSoft: l.title_soft, titleMin: l.title_min,
      bulletMax: l.bullet_max, bulletCount: l.bullet_count,
      descMax: l.desc_max, kwBytes: l.keyword_bytes,
      lang: l.lang, label: l.label
    };
  });

  // required 里的 item_name / product_type 在 SP-API payload 是顶层字段，
  // 不进 attributes，所以前端展示时要过滤掉。过滤规则也来自共享文件。
  function schemaEntry(raw) {
    return {
      productType: raw.product_type,
      itemType: raw.item_type_keyword,
      node: raw.browse_hint,
      required: (raw.required || []).filter(function (f) {
        return TOP_LEVEL_FIELDS.indexOf(f) < 0;
      }),
      recommended: raw.recommended || []
    };
  }

  var LISTING_SCHEMA = {};
  Object.keys(RULES.category_schema).forEach(function (k) {
    if (k.charAt(0) === '_') return;  // 下划线开头的是说明字段，不是类目
    LISTING_SCHEMA[k] = schemaEntry(RULES.category_schema[k]);
  });
  var LISTING_SCHEMA_DEFAULT = schemaEntry(RULES.default_schema);

  // 长词优先，避免「主动降噪」被先替换掉「降噪」而留下「主动」
  var CN2EN = RULES.cn2en.slice().sort(function (a, b) { return b[0].length - a[0].length; });
  var SCENE_BY_CATEGORY = RULES.scene_by_category;

  // emoji 判定区间与后端同源。
  // Python re 写 \U0001F000、JS RegExp 写 \u{1F000}（带 u 标志），转义语法不兼容，
  // 所以共享文件里存的是码点区间，两端各自构建 —— 判定范围才真的对得上。
  // 以前前端用代理对覆盖整个 astral plane、后端只覆盖 1F000-1FAFF，
  // 存在「前端判违规、后端放行」的区间。
  // 下面用 String.fromCharCode(92) 拼反斜杠，避免源码里的转义歧义。
  var EMOJI_RE = (function () {
    var bs = String.fromCharCode(92);
    var parts = RULES.emoji_ranges.map(function (r) {
      return bs + 'u{' + r[0].toString(16) + '}-' + bs + 'u{' + r[1].toString(16) + '}';
    }).join('');
    return new RegExp('[' + RULES.repeat_punct + ']{2,}|[' + parts + ']', 'u');
  })();

  function byteLen(s) {
    var n = 0, str = s || '';
    for (var i = 0; i < str.length; i++) {
      var c = str.charCodeAt(i);
      n += c < 0x80 ? 1 : (c < 0x800 ? 2 : 3);
    }
    return n;
  }

  function cnToEn(name) {
    var out = (name || '').trim(), i;
    for (i = 0; i < CN2EN.length; i++) out = out.split(CN2EN[i][0]).join(' ' + CN2EN[i][1] + ' ');
    out = out.replace(/[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+/g, ' ');
    return out.replace(/\s+/g, ' ').replace(/^[-,\s]+|[-,\s]+$/g, '') || 'Product';
  }

  function schemaForListing(cat) { return LISTING_SCHEMA[cat] || LISTING_SCHEMA_DEFAULT; }

  function splitFeatures(f) {
    if (!f) return [];
    if (Object.prototype.toString.call(f) === '[object Array]') return f;
    return String(f).split(/[\n;；]+/).map(function (x) { return x.trim(); }).filter(function (x) { return x; });
  }

  /**
   * 生成 Listing。useLlm=true 走"模拟大模型"（文案更完整），否则规则草稿。
   * 返回 {source, title, bullets, description, keywords, itemType, attributes, defaultAttrs, issues, hasError}
   */
  function genListing(input, platform, useLlm) {
    platform = LISTING_LIMITS[platform] ? platform : 'amazon';
    var lim = LISTING_LIMITS[platform];
    var schema = schemaForListing(input.category);
    var name = (input.name || input.title || 'Product').trim();
    var brand = (input.brand || '').trim();
    var feats = splitFeatures(input.features);
    var specs = input.specs || {};
    var en = lim.lang === 'en';

    // ---- 标题 ----
    var title;
    if (en) {
      var core = cnToEn(name);
      var scene = SCENE_BY_CATEGORY[input.category] || '';
      title = [brand, core, scene].filter(Boolean).join(' ').replace(/\s+/g, ' ').replace(/^[-,\s]+|[-,\s]+$/g, '');
    } else {
      title = name;
    }

    // ---- 五点 ----
    var bullets = [], labels = en
      ? ['Key Feature', 'Performance', 'Design', 'Easy to Use', 'What You Get']
      : ['核心卖点', '性能表现', '设计细节', '使用便捷', '包装清单'];
    var i, body;
    for (i = 0; i < feats.length && bullets.length < lim.bulletCount; i++) {
      body = en ? cnToEn(feats[i]).replace(/\.$/, '') : feats[i];
      bullets.push(labels[bullets.length] + (en ? ': ' : '：') + body + (en ? '.' : ''));
    }
    var fillers = en
      ? ['Premium Material: Built with durable materials for long-lasting daily use.',
        'Easy to Use: Tool-free setup, ready to go straight out of the box.',
        'Wide Application: Works well at home, in the office, and on the go.',
        'Quality Assured: Every unit is inspected before shipping.',
        'What You Get: 1 x product, 1 x user manual, and responsive customer support.']
      : ['品质材质：选用耐用材料，日常使用不易损坏。',
        '简单易用：免工具安装，开箱即用。',
        '适用场景广：居家、办公、出行都能用。',
        '品质保障：发货前逐件检验。',
        '包装清单：商品 x1、说明书 x1，售后无忧。'];
    i = 0;
    while (bullets.length < lim.bulletCount) { bullets.push(fillers[i % fillers.length]); i++; }

    // "模拟大模型" 会额外补一条场景化描述，并把卖点写成完整句 —— 用来演示真实模型的效果差异
    if (useLlm && en) {
      var scene2 = (input.audience || '').trim();
      if (scene2) bullets[bullets.length - 1] = 'Built For: ' + cnToEn(scene2) + '.';
    }

    // ---- 描述 ----
    var specArr = [];
    Object.keys(specs).slice(0, 6).forEach(function (k) { if (specs[k]) specArr.push(k + ': ' + specs[k]); });
    var specLine = specArr.join(en ? '; ' : '；');
    var description;
    if (en) {
      description = (core + '. ' + (specLine ? specLine + '. ' : '')) +
        'Designed for reliable everyday performance and backed by responsive customer support.';
    } else {
      description = name + (specLine ? '。' + specLine : '');
    }

    // ---- 关键词 ----
    var kws = [];
    if (input.keywords) {
      String(input.keywords).split(/[,，;；\s]+/).forEach(function (k) { if (k.trim()) kws.push(k.trim()); });
    }
    if (en) {
      cnToEn(name).split(/\s+/).forEach(function (w) { if (w.length > 3) kws.push(w); });
      kws = kws.map(function (k) { return k.replace(/[\u4e00-\u9fff]+/g, '').trim(); }).filter(Boolean);
    } else {
      kws.push(name);
    }
    var seen = {}, finalKw = [];
    kws.forEach(function (k) { if (k && !seen[k]) { seen[k] = 1; finalKw.push(k); } });

    // ---- 属性 ----
    var attrs = {};
    schema.required.concat(schema.recommended).forEach(function (k) { if (specs[k]) attrs[k] = String(specs[k]); });
    if (!attrs.brand) attrs.brand = brand || 'Generic';
    if (!attrs.item_type_keyword) attrs.item_type_keyword = schema.itemType;
    var defaultAttrs = [];
    schema.required.forEach(function (k) {
      if (!attrs[k] && ATTR_DEFAULTS[k]) { attrs[k] = ATTR_DEFAULTS[k]; defaultAttrs.push(k); }
    });

    var listing = {
      title: title.slice(0, lim.titleMax),
      bullets: bullets.slice(0, lim.bulletCount).map(function (b) { return b.slice(0, lim.bulletMax); }),
      description: description.slice(0, lim.descMax),
      keywords: finalKw.slice(0, 12),
      itemType: schema.itemType,
      attributes: attrs
    };

    var issues = validateListing(listing, platform, schema, defaultAttrs);
    return {
      source: useLlm ? 'llm' : 'rule',
      platform: platform,
      sku: input.sku || '',
      listing: listing,
      defaultAttrs: defaultAttrs,
      issues: issues,
      hasError: hasErrorOf(issues)
    };
  }

  function hasErrorOf(issues) {
    for (var i = 0; i < issues.length; i++) if (issues[i].level === 'error') return true;
    return false;
  }

  /** 本地校验，规则与 python listing_gen.validate() 对齐 */
  function validateListing(listing, platform, schema, defaultAttrs) {
    var lim = LISTING_LIMITS[platform] || LISTING_LIMITS.amazon;
    schema = schema || LISTING_SCHEMA_DEFAULT;
    var issues = [];
    var title = (listing.title || '').trim();
    var bullets = listing.bullets || [];
    var attrs = listing.attributes || {};

    if (!title) {
      issues.push({ level: 'error', field: '标题', msg: '标题为空' });
    } else {
      if (title.length > lim.titleMax) {
        issues.push({ level: 'error', field: '标题', msg: '标题 ' + title.length + ' 字符，超过平台上限 ' + lim.titleMax });
      } else if (title.length > lim.titleSoft) {
        issues.push({ level: 'warn', field: '标题', msg: '标题 ' + title.length + ' 字符，超过建议长度 ' + lim.titleSoft + '，移动端会被截断' });
      }
      if (title.length < lim.titleMin) issues.push({ level: 'warn', field: '标题', msg: '标题过短，关键词覆盖不足' });
      // 判定范围与 Python 后端同源（区间来自共享规则文件）
      if (EMOJI_RE.test(title)) {
        issues.push({ level: 'error', field: '标题', msg: '标题含 emoji 或连续感叹号，平台禁止' });
      }
      if (lim.lang === 'en') {
        var probe = title.split(attrs.brand || '\u0000').join('');
        if (/[^\x00-\x7F]/.test(probe)) issues.push({ level: 'warn', field: '标题', msg: '英文站标题含非 ASCII 字符，请检查是否乱码' });
        var caps = title.match(/\b[A-Z]{4,}\b/g);
        if (caps) issues.push({ level: 'warn', field: '标题', msg: '标题含全大写单词（' + caps.join('、') + '），建议改为首字母大写' });
      }
      var low = title.toLowerCase();
      BANNED_WORDS.forEach(function (bw) {
        if (low.indexOf(bw[0]) >= 0) issues.push({ level: 'error', field: '标题', msg: '标题含受限词「' + bw[0] + '」：' + bw[1] });
      });
    }

    if (bullets.length < lim.bulletCount) {
      issues.push({ level: 'warn', field: '五点描述', msg: '只有 ' + bullets.length + ' 条，建议补齐 ' + lim.bulletCount + ' 条' });
    }
    bullets.forEach(function (b, idx) {
      if (b.length > lim.bulletMax) {
        issues.push({ level: 'error', field: '五点描述', msg: '第 ' + (idx + 1) + ' 条 ' + b.length + ' 字符，超过上限 ' + lim.bulletMax });
      }
      var low = (b || '').toLowerCase();
      BANNED_WORDS.forEach(function (bw) {
        if (low.indexOf(bw[0]) >= 0) issues.push({ level: 'error', field: '五点描述', msg: '第 ' + (idx + 1) + ' 条含受限词「' + bw[0] + '」：' + bw[1] });
      });
    });

    if (!listing.description) issues.push({ level: 'warn', field: '产品描述', msg: '描述为空，影响转化' });
    else if (listing.description.length > lim.descMax) issues.push({ level: 'warn', field: '产品描述', msg: '描述 ' + listing.description.length + ' 字符，超过建议上限 ' + lim.descMax });

    var kb = byteLen((listing.keywords || []).join(' '));
    if (kb > lim.kwBytes) issues.push({ level: 'error', field: '搜索关键词', msg: '关键词共 ' + kb + ' 字节，超过上限 ' + lim.kwBytes + ' 字节' });

    var missing = schema.required.filter(function (a) { return !attrs[a]; });
    if (missing.length) issues.push({ level: 'error', field: '类目属性', msg: '缺少类目必填属性：' + missing.join('、') });
    if (defaultAttrs && defaultAttrs.length) {
      issues.push({ level: 'warn', field: '类目属性', msg: '以下属性用了默认值，上架前请核实：' + defaultAttrs.join('、') });
    }
    var missOpt = schema.recommended.filter(function (a) { return !attrs[a]; });
    if (missOpt.length) issues.push({ level: 'warn', field: '类目属性', msg: '推荐属性未填（影响搜索曝光）：' + missOpt.slice(0, 6).join('、') });

    return issues;
  }

  /** 转成 Listings Items API 的 body */
  function listingToSpapi(listing, sku, productType) {
    var mid = (get().credentials && get().credentials.amazon && get().credentials.amazon.marketplaceId) || 'ATVPDKIKX0DER';
    var attrs = {}, k;
    for (k in listing.attributes) {
      if (!listing.attributes[k]) continue;
      attrs[k] = [{ value: listing.attributes[k], marketplace_id: mid }];
    }
    attrs.item_name = [{ value: listing.title, marketplace_id: mid }];
    attrs.bullet_point = listing.bullets.map(function (b) { return { value: b, marketplace_id: mid }; });
    if (listing.description) attrs.product_description = [{ value: listing.description, marketplace_id: mid }];
    attrs.item_type_keyword = [{ value: listing.itemType, marketplace_id: mid }];
    return { productType: productType, requirements: 'LISTING', attributes: attrs };
  }

  function saveListing(productId, result) {
    var d = get();
    var p = d.products.filter(function (x) { return x.id === productId; })[0];
    if (!p) return null;
    p.listing = {
      platform: result.platform, source: result.source,
      title: result.listing.title, bullets: result.listing.bullets,
      description: result.listing.description, keywords: result.listing.keywords,
      itemType: result.listing.itemType, attributes: result.listing.attributes,
      updatedAt: now()
    };
    p.lastSync = now();
    addLog('info', 'listing', p.sku + ' 的 Listing 已生成（' + (result.source === 'llm' ? '大模型' : '规则草稿') + '）');
    save();
    return p;
  }

  var AMAZON_TITLES = [
    'Wireless Earbuds Bluetooth 5.3 Noise Cancelling',
    'Stainless Steel Water Bottle 32oz Insulated',
    'LED Desk Lamp with USB Charging Port',
    'Silicone Phone Case for iPhone 15 Pro',
    'Portable Air Compressor for Car Tires',
    'Memory Foam Cushion for Office Chair',
    'Fitness Resistance Band Set (5 Levels)'
  ];
  var PDD_TITLES = [
    '无线蓝牙耳机 降噪 超长续航',
    '加厚不锈钢保温杯 大容量',
    '护眼台灯 三档调光 USB充电',
    '手机壳 防摔硅胶全包',
    '车载吸尘器 无线便携',
    '纯棉四件套 床上用品'
  ];
  // 注意命名:PRODUCT_CATEGORIES 是商品类目,CATEGORIES 是异常类型,别混用
  var PRODUCT_CATEGORIES = ['3C数码', '家居厨房', '户外运动', '个护健康', '母婴玩具', '服饰配饰'];

  function seedProducts() {
    var list = [], i;
    for (i = 0; i < AMAZON_TITLES.length; i++) {
      list.push({
        id: uid('p'), platform: 'amazon',
        sku: 'AMZ-' + (1001 + i),
        title: AMAZON_TITLES[i],
        category: pick(PRODUCT_CATEGORIES),
        price: +rnd(9.9, 89.9).toFixed(2),
        currency: 'USD',
        stock: rndInt(80, 620),
        status: i === 4 ? 'pending' : 'online',
        lastSync: now() - rndInt(1, 300) * 60000,
        autoSync: true
      });
    }
    for (i = 0; i < PDD_TITLES.length; i++) {
      list.push({
        id: uid('p'), platform: 'pdd',
        sku: 'PDD-' + (2001 + i),
        title: PDD_TITLES[i],
        category: pick(PRODUCT_CATEGORIES),
        price: +rnd(19, 299).toFixed(2),
        currency: 'CNY',
        stock: rndInt(120, 1800),
        status: i === 3 ? 'offline' : 'online',
        lastSync: now() - rndInt(1, 300) * 60000,
        autoSync: i !== 3
      });
    }
    return list;
  }

  function seedTasks() {
    return [
      { id: uid('t'), name: '亚马逊 FBA 库存监控', platform: 'amazon', metric: 'inventory', interval: 60, threshold: 0.3, direction: 'drop', enabled: true, lastRunAt: now() - 1000 * 60 * 62, lastValue: 0, status: 'success', runCount: 148 },
      { id: uid('t'), name: '亚马逊订单量监控', platform: 'amazon', metric: 'orders', interval: 60, threshold: 0.5, direction: 'drop', enabled: true, lastRunAt: now() - 1000 * 60 * 58, lastValue: 0, status: 'success', runCount: 148 },
      { id: uid('t'), name: '拼多多商品库存监控', platform: 'pdd', metric: 'inventory', interval: 60, threshold: 0.3, direction: 'drop', enabled: true, lastRunAt: now() - 1000 * 60 * 55, lastValue: 0, status: 'warning', runCount: 132 },
      { id: uid('t'), name: '拼多多订单量监控', platform: 'pdd', metric: 'orders', interval: 60, threshold: 0.5, direction: 'drop', enabled: true, lastRunAt: now() - 1000 * 60 * 52, lastValue: 0, status: 'success', runCount: 132 },
      { id: uid('t'), name: '双平台价格波动监控', platform: 'all', metric: 'price', interval: 60, threshold: 0.2, direction: 'both', enabled: true, lastRunAt: now() - 1000 * 60 * 40, lastValue: 0, status: 'success', runCount: 96 },
      { id: uid('t'), name: 'GMV 小时环比监控', platform: 'all', metric: 'gmv', interval: 120, threshold: 0.4, direction: 'drop', enabled: false, lastRunAt: now() - 1000 * 60 * 130, lastValue: 0, status: 'idle', runCount: 41 }
    ];
  }

  function seedAlerts(products, useLlm) {
    var presets = [
      ['critical', 'amazon', 'AMZ-1005 库存暴跌', '库存从 428 降至 132（降幅 69.2%，超过阈值 30%）', 'stockout_risk'],
      ['warning', 'pdd', '订单量环比下滑', '近一小时订单量从 46 降至 21（降幅 54.3%，超过阈值 50%）', 'demand_drop'],
      ['critical', 'amazon', 'SP-API 鉴权失败', 'LWA token 换取失败：invalid_grant，请检查 refresh_token 是否过期', 'auth_failure'],
      ['warning', 'pdd', 'PDD-2003 库存偏低', '库存从 860 降至 402（降幅 53.3%，超过阈值 30%）', 'stockout_risk'],
      ['info', 'all', '商品上架成功', 'AMZ-1007 已提交至亚马逊，Listing 状态：处理中', 'notice'],
      ['warning', 'amazon', '价格波动异常', 'AMZ-1002 价格从 24.90 变为 31.20（变动 25.3%，超过阈值 20%）', 'price_anomaly'],
      ['info', 'all', '监控任务启动', '常驻调度进程已启动，每小时整点执行一次', 'notice'],
      ['critical', 'pdd', '接口调用失败', 'pdd.goods.list.get 返回错误：access_token 已失效', 'api_error']
    ];
    var statusPool = ['unhandled', 'unhandled', 'handled'];
    return presets.map(function (p, i) {
      var obj = (p[3].match(/库存从|从\s?\d/) ? (p[2].match(/[A-Z]{3}-\d+/) || ['该商品'])[0] : '该商品');
      return {
        id: uid('a'), level: p[0], platform: p[1], title: p[2], message: p[3],
        category: p[4],
        advice: buildAdvice(p[4], { obj: obj, prev: '—', cur: '—' }, useLlm),
        createdAt: now() - (i * 37 + 6) * 60000,
        status: i < 2 ? 'unhandled' : pick(statusPool),
        pushed: i % 3 !== 1
      };
    });
  }

  function seedMetrics() {
    var arr = [], base = now() - 23 * 3600 * 1000, orders = 38, stock = 5200;
    for (var i = 0; i < 24; i++) {
      orders = clamp(orders + rnd(-7, 8), 12, 72);
      stock = clamp(stock + rnd(-160, 130), 3200, 6400);
      arr.push({
        t: base + i * 3600 * 1000,
        orders: Math.round(orders),
        stock: Math.round(stock),
        gmv: Math.round(orders * rnd(22, 38))
      });
    }
    return arr;
  }

  function seedLogs() {
    var tpl = [
      ['info', 'monitor', '本次监控完成，共检查 14 项指标，无异常'],
      ['info', 'listing', 'AMZ-1003 商品信息同步完成'],
      ['warn', 'monitor', '[pdd] stock:PDD-2003 从 860 降至 402（降幅 53.3%，超过阈值 30%）'],
      ['info', 'alert', '告警已推送至企业微信群机器人'],
      ['error', 'amazon', 'get_recent_orders 调用失败：HTTP 401 unauthorized'],
      ['info', 'scheduler', '定时任务触发，开始执行第 148 轮监控'],
      ['info', 'listing', 'PDD-2002 上架成功，goods_id = 883920114'],
      ['warn', 'monitor', '[amazon] order_count_last_hour 从 46 降至 21（降幅 54.3%，超过阈值 50%）'],
      ['info', 'monitor', '本次监控完成，共检查 14 项指标，发现 1 项异常'],
      ['info', 'alert', '告警已推送至钉钉群机器人'],
      ['error', 'pdd', 'pdd.goods.list.get 返回错误：access_token 已失效'],
      ['info', 'scheduler', '定时任务触发，开始执行第 147 轮监控']
    ];
    return tpl.map(function (l, i) {
      return { id: uid('l'), ts: now() - (i * 23 + 3) * 60000, level: l[0], module: l[1], message: l[2] };
    });
  }

  function defaultDB() {
    var products = seedProducts();
    var settings = {
      demoMode: true,
      thresholdInventory: 0.3,
      thresholdOrders: 0.5,
      thresholdPrice: 0.2,
      monitorIntervalMin: 60,
      autoRun: true,
      demoIntervalSec: 30,      // 演示模式下的执行节奏（真实环境为 3600 秒）
      alertCooldownMin: 30
    };
    // 大模型建议:不配置密钥时不会发起任何请求,告警照常用内置规则建议
    settings.llm = {
      enabled: false,
      simulate: true,           // 演示模式下模拟大模型输出(不发真实请求)
      baseUrl: 'https://api.deepseek.com/v1',
      model: 'deepseek-chat',
      apiKey: '',
      cooldownMin: 60           // 同类异常多久内不重复调用
    };
    // Listing 生成：默认只生成不自动提交，文案写错可以改，错误上架的清理成本高得多
    settings.listing = {
      brand: '',
      platform: 'amazon',
      autoPublish: false,
      skipOnError: true
    };
    settings.imageLibrary = {
      apiBaseUrl: 'https://629ff8cd6f86472a8e2792ea8a8a3ee9.sg2.agentos-app.run'
    };
    return {
      version: 3,
      createdAt: now(),
      settings: settings,
      credentials: {
        amazon: { enabled: false, sellerId: '', marketplaceId: 'ATVPDKIKX0DER', region: 'us-east-1', clientId: '', clientSecret: '', refreshToken: '', awsAccessKey: '', awsSecretKey: '', endpoint: 'https://sellingpartnerapi-na.amazon.com' },
        pdd: { enabled: false, clientId: '', clientSecret: '', accessToken: '', endpoint: 'https://gw-api.pinduoduo.com/api/router' }
      },
      channels: {
        wecom: { enabled: false, url: '' },
        dingtalk: { enabled: false, url: '', secret: '' }
      },
      products: products,
      tasks: seedTasks(),
      alerts: seedAlerts(products, settings.llm.simulate),
      metrics: seedMetrics(),
      logs: seedLogs(),
      mockImageSets: [],
      stats: { totalRuns: 0, totalAlerts: 0, totalListing: 0, lastRunAt: 0 }
    };
  }

  /* ---------------- 读写 ---------------- */
  var db = null;

  /** v1 -> v2:补充大模型配置,并给历史告警补上异常类型与建议(不重置用户数据) */
  function migrate(d) {
    d.settings.llm = d.settings.llm || {
      enabled: false, simulate: true, baseUrl: 'https://api.deepseek.com/v1',
      model: 'deepseek-chat', apiKey: '', cooldownMin: 60
    };
    (d.alerts || []).forEach(function (a) {
      if (!a.category) a.category = classifyAlert(a.key || '', a.value, a.message || '');
      if (!a.advice) a.advice = buildAdvice(a.category, { obj: '该商品' }, d.settings.llm.simulate);
    });
    d.version = 2;
    addLog('info', 'system', '数据已升级到 v2：新增异常分类与优化建议');
  }

  /** v2 -> v3:补充 Listing 生成配置(不动已有商品数据) */
  function migrateV3(d) {
    if (!d.settings.listing) {
      d.settings.listing = { brand: '', platform: 'amazon', autoPublish: false, skipOnError: true };
    }
    if (!d.settings.imageLibrary) d.settings.imageLibrary = { apiBaseUrl: 'https://629ff8cd6f86472a8e2792ea8a8a3ee9.sg2.agentos-app.run' };
    d.version = 3;
    addLog('info', 'system', '数据已升级到 v3：新增 AI 生成 Listing');
  }

  function load() {
    try {
      var raw = localStorage.getItem(KEY);
      if (raw) { db = JSON.parse(raw); }
    } catch (e) { db = null; }
    if (!db || !db.settings) { db = defaultDB(); save(); }
    else {
      if (!db.version || db.version < 2) { migrate(db); save(); }
      if (db.version < 3) { migrateV3(db); save(); }
      if (!db.settings.imageLibrary) { db.settings.imageLibrary = { apiBaseUrl: 'https://629ff8cd6f86472a8e2792ea8a8a3ee9.sg2.agentos-app.run' }; save(); }
      if (!Array.isArray(db.mockImageSets)) { db.mockImageSets = []; save(); }
    }
    return db;
  }
  function save() {
    try { localStorage.setItem(KEY, JSON.stringify(db)); }
    catch (e) { console.error('保存失败', e); }
  }
  function get() { return db || load(); }
  function reset() { db = defaultDB(); save(); return db; }

  function addLog(level, module, message) {
    get().logs.unshift({ id: uid('l'), ts: now(), level: level, module: module, message: message });
    if (get().logs.length > 300) get().logs.length = 300;
  }

  /* ---------------- 核心：模拟一次监控执行 ----------------
   * 对应 Python 版 main.run_hourly_monitor()：
   *   抓取指标 -> anomaly_detector 环比检测 -> alerts 推送
   * -------------------------------------------------------- */
  function metricValue(task) {
    var d = get(), prods = d.products.filter(function (p) {
      return task.platform === 'all' || p.platform === task.platform;
    });
    var sum = function (k) { return prods.reduce(function (a, p) { return a + (p[k] || 0); }, 0); };
    switch (task.metric) {
      case 'inventory': return sum('stock');
      case 'orders': return rndInt(18, 68);
      case 'price': return +(sum('price') / Math.max(prods.length, 1)).toFixed(2);
      case 'gmv': return rndInt(600, 2600);
      default: return 0;
    }
  }

  function metricLabel(m) {
    return { inventory: '库存总量', orders: '小时订单量', price: '平均价格', gmv: '小时 GMV' }[m] || m;
  }

  var METRIC_UNIT = { inventory: '', orders: '单', price: '', gmv: '' };

  function runMonitor(opts) {
    opts = opts || {};
    var d = get();
    var inject = !!opts.inject;                 // 注入一次异常（演示用）
    var checked = 0, anomalies = [], pushedTo = [];
    var ts = now();

    addLog('info', 'scheduler', '定时任务触发，开始执行第 ' + (d.stats.totalRuns + 1) + ' 轮监控');

    // 1) 抓取：刷新商品库存（模拟平台接口返回）
    var prods = d.products.filter(function (p) { return p.autoSync && p.status !== 'offline'; });
    // 注入异常时，两个平台各挑一个 SKU 制造大幅跌库，用于验证告警链路
    var injectIdx = [];
    if (inject) {
      injectIdx.push(0);
      for (var k = 0; k < prods.length; k++) { if (prods[k].platform === 'pdd') { injectIdx.push(k); break; } }
    }
    prods.forEach(function (p, idx) {
      var drop = injectIdx.indexOf(idx) >= 0 || Math.random() < 0.06;   // 6% 自然波动概率
      var ratio = drop ? -rnd(0.42, 0.72) : rnd(-0.06, 0.07);
      p.stock = Math.max(0, Math.round(p.stock * (1 + ratio)));
      p.lastSync = ts;
    });

    // 2) 检测：逐任务做环比对比
    //    库存按 SKU 维度逐项检测（与原 Python 版 inventory:{sku} 语义一致），
    //    避免个别 SKU 暴跌被平台总量平均掉。
    d.tasks.forEach(function (t) {
      if (!t.enabled) return;
      var pname = t.platform === 'all' ? '全平台' : (t.platform === 'amazon' ? '亚马逊' : '拼多多');
      t.status = 'success';
      t.lastRunAt = ts;
      t.runCount++;

      if (t.metric === 'inventory') {
        var scope = d.products.filter(function (p) {
          return (t.platform === 'all' || p.platform === t.platform) && p.status !== 'offline';
        });
        var prevMap = t.lastValues || {}, curMap = {};
        scope.forEach(function (p) { curMap[p.sku] = p.stock; });
        scope.forEach(function (p) {
          checked++;
          var r = detect(t.platform, '库存 ' + p.sku, prevMap[p.sku], p.stock, t.threshold, t.direction);
          if (r.anomaly) {
            t.status = 'warning';
            var level = r.ratioAbs >= t.threshold * 2 ? 'critical' : 'warning';
            anomalies.push({
              task: t, level: level, title: pname + ' 库存异常 · ' + p.sku, message: r.message,
              key: 'inventory:' + p.sku, value: p.stock, prev: prevMap[p.sku], ratio: r.ratio
            });
          }
        });
        t.lastValues = curMap;
        t.lastValue = scope.reduce(function (a, p) { return a + p.stock; }, 0);
      } else {
        checked++;
        var cur = metricValue(t);
        var r2 = detect(t.platform, metricLabel(t.metric), t.lastValue, cur, t.threshold, t.direction);
        if (r2.anomaly) {
          t.status = 'warning';
          var lvl = r2.ratioAbs >= t.threshold * 2 ? 'critical' : 'warning';
          anomalies.push({
            task: t, level: lvl, title: pname + ' ' + metricLabel(t.metric) + '异常', message: r2.message,
            key: t.metric === 'orders' ? 'order_count_last_hour' : t.metric,
            value: cur, prev: t.lastValue, ratio: r2.ratio
          });
        }
        t.lastValue = cur;
      }
    });

    // 3) 告警：写入告警中心 + 推送
    var useLlm = !!(d.settings.llm && d.settings.llm.simulate);
    anomalies.forEach(function (a) {
      var cat = classifyAlert(a.key, a.value, a.message, a.ratio);
      var obj = /^(inventory|stock):/.test(a.key) ? a.key.split(':')[1] : '整体';
      d.alerts.unshift({
        id: uid('a'), level: a.level, platform: a.task.platform === 'all' ? 'all' : a.task.platform,
        title: a.title, message: a.message, createdAt: ts, status: 'unhandled', pushed: false,
        taskId: a.task.id, category: cat,
        advice: buildAdvice(cat, { obj: obj, prev: a.prev, cur: a.value }, useLlm)
      });
      addLog(a.level === 'critical' ? 'error' : 'warn', 'monitor', a.message);
    });

    if (anomalies.length) {
      pushedTo = pushAlerts('电商数据监控告警\n' + anomalies.map(function (a) { return '· ' + a.title + '：' + a.message; }).join('\n'));
      d.alerts.slice(0, anomalies.length).forEach(function (al) { al.pushed = pushedTo.length > 0; });
    }

    // 4) 记录趋势点
    var last = d.metrics[d.metrics.length - 1] || { orders: 40, stock: 5000 };
    d.metrics.push({
      t: ts,
      orders: rndInt(18, 68),
      stock: d.products.reduce(function (a, p) { return a + p.stock; }, 0),
      gmv: rndInt(600, 2600)
    });
    if (d.metrics.length > 96) d.metrics.shift();

    addLog(anomalies.length ? 'warn' : 'info', 'monitor',
      anomalies.length
        ? '本次监控完成，共检查 ' + checked + ' 项指标，发现 ' + anomalies.length + ' 项异常'
        : '本次监控完成，共检查 ' + checked + ' 项指标，无异常');

    d.stats.totalRuns++;
    d.stats.totalAlerts += anomalies.length;
    d.stats.lastRunAt = ts;
    save();

    return { checked: checked, anomalies: anomalies, pushedTo: pushedTo, at: ts };
  }

  function detect(platform, key, previous, current, threshold, direction) {
    var result = { anomaly: false, message: '', ratio: 0, ratioAbs: 0 };
    if (previous && previous > 0) {
      var change = (current - previous) / previous;
      result.ratio = change;
      result.ratioAbs = Math.abs(change);
      var pname = platform === 'all' ? '全平台' : (platform === 'amazon' ? '亚马逊' : '拼多多');
      if (direction === 'drop' && change <= -threshold) {
        result.anomaly = true;
        result.message = '[' + pname + '] ' + key + ' 从 ' + previous + ' 降至 ' + current +
          '（降幅 ' + (Math.abs(change) * 100).toFixed(1) + '%，超过阈值 ' + (threshold * 100).toFixed(0) + '%）';
      } else if (direction === 'both' && Math.abs(change) >= threshold) {
        result.anomaly = true;
        result.message = '[' + pname + '] ' + key + ' 从 ' + previous + ' 变为 ' + current +
          '（变动 ' + (change * 100).toFixed(1) + '%，超过阈值 ' + (threshold * 100).toFixed(0) + '%）';
      }
    }
    return result;
  }

  function pushAlerts(content) {
    var d = get(), sent = [];
    if (d.settings.demoMode) {
      if (d.channels.wecom.enabled || d.channels.dingtalk.enabled) {
        if (d.channels.wecom.enabled) sent.push('企业微信');
        if (d.channels.dingtalk.enabled) sent.push('钉钉');
        addLog('info', 'alert', '【模拟推送】告警已发送至 ' + sent.join(' / ') + ' 群机器人（含异常类型与处理建议）');
      } else {
        addLog('warn', 'alert', '未配置任何告警渠道，告警仅记录在站内（可在「平台与凭证」中配置 webhook）');
      }
      return sent;
    }
    // 真实模式：由后端进程推送，这里只登记
    addLog('info', 'alert', '告警已写入队列，等待后端推送：' + content.replace(/\n/g, ' ').slice(0, 60) + '...');
    if (d.channels.wecom.enabled) sent.push('企业微信');
    if (d.channels.dingtalk.enabled) sent.push('钉钉');
    return sent;
  }

  /* ---------------- 业务操作 ---------------- */
  function saveProduct(data) {
    var d = get();
    if (data.id) {
      var t = d.products.filter(function (p) { return p.id === data.id; })[0];
      if (t) { Object.keys(data).forEach(function (k) { t[k] = data[k]; }); t.lastSync = now(); }
      addLog('info', 'listing', (data.sku || '') + ' 商品信息已更新');
    } else {
      data.id = uid('p'); data.lastSync = now();
      d.products.unshift(data);
      addLog('info', 'listing', (data.sku || '') + ' 商品已创建');
    }
    save(); return data;
  }

  function removeProduct(id) {
    var d = get();
    d.products = d.products.filter(function (p) { return p.id !== id; });
    addLog('info', 'listing', '商品已删除');
    save();
  }

  function publishProducts(ids) {
    var d = get(), ok = 0, fail = 0;
    ids.forEach(function (id) {
      var p = d.products.filter(function (x) { return x.id === id; })[0];
      if (!p) return;
      var success = d.settings.demoMode ? Math.random() > 0.15 : true;
      if (success) {
        p.status = 'online'; p.lastSync = now(); ok++;
        addLog('info', 'listing', (p.platform === 'amazon' ? '亚马逊' : '拼多多') + ' ' + p.sku + ' 上架成功');
      } else {
        p.status = 'failed'; fail++;
        addLog('error', 'listing', (p.platform === 'amazon' ? '亚马逊' : '拼多多') + ' ' + p.sku + ' 上架失败：类目属性不完整');
        var llmOn = !!(d.settings.llm && d.settings.llm.simulate);
        d.alerts.unshift({
          id: uid('a'), level: 'critical', platform: p.platform, title: '商品上架失败: ' + p.sku,
          message: '类目必填属性缺失（item_name / product_type），请补全后重试',
          category: 'listing_failed',
          advice: buildAdvice('listing_failed', { obj: p.sku }, llmOn),
          createdAt: now(), status: 'unhandled', pushed: false
        });
      }
    });
    d.stats.totalListing += ok;
    save();
    return { ok: ok, fail: fail };
  }

  function saveTask(data) {
    var d = get();
    if (data.id) {
      var t = d.tasks.filter(function (x) { return x.id === data.id; })[0];
      if (t) Object.keys(data).forEach(function (k) { t[k] = data[k]; });
    } else {
      data.id = uid('t'); data.runCount = 0; data.lastValue = 0; data.status = 'idle'; data.lastRunAt = 0;
      d.tasks.push(data);
    }
    addLog('info', 'scheduler', '监控任务「' + data.name + '」已保存');
    save(); return data;
  }

  function removeTask(id) {
    var d = get();
    d.tasks = d.tasks.filter(function (t) { return t.id !== id; });
    save();
  }

  function toggleTask(id, enabled) {
    var d = get(), t = d.tasks.filter(function (x) { return x.id === id; })[0];
    if (t) { t.enabled = enabled; t.status = enabled ? 'success' : 'idle'; }
    save(); return t;
  }

  function resolveAlert(id) {
    var d = get(), a = d.alerts.filter(function (x) { return x.id === id; })[0];
    if (a) { a.status = 'handled'; a.handledAt = now(); }
    save(); return a;
  }

  function resolveAllAlerts() {
    var d = get();
    d.alerts.forEach(function (a) { if (a.status === 'unhandled') { a.status = 'handled'; a.handledAt = now(); } });
    save();
  }

  function updateSettings(patch) {
    var d = get();
    Object.keys(patch).forEach(function (k) { d.settings[k] = patch[k]; });
    save();
  }
  function updateCredentials(platform, patch) {
    var d = get();
    Object.keys(patch).forEach(function (k) { d.credentials[platform][k] = patch[k]; });
    save();
  }
  function updateChannels(key, patch) {
    var d = get();
    Object.keys(patch).forEach(function (k) { d.channels[key][k] = patch[k]; });
    save();
  }

  function testChannel(key) {
    var d = get(), c = d.channels[key];
    if (!c.url) return { ok: false, msg: '请先填写 webhook 地址' };
    addLog(d.settings.demoMode ? 'info' : 'info', 'alert',
      (d.settings.demoMode ? '【模拟】' : '') + '测试消息已发送至' + (key === 'wecom' ? '企业微信' : '钉钉') + '群机器人');
    save();
    return { ok: true, msg: (d.settings.demoMode ? '模拟发送成功（演示模式不产生真实请求）' : '已加入推送队列') };
  }

  /* ---------------- 统计 ---------------- */
  function stats() {
    var d = get();
    var unhandled = d.alerts.filter(function (a) { return a.status === 'unhandled'; }).length;
    var online = d.products.filter(function (p) { return p.status === 'online'; }).length;
    var m = d.metrics.slice(-24);
    var curOrders = m.length ? m[m.length - 1].orders : 0;
    var prevOrders = m.length > 1 ? m[m.length - 2].orders : 0;
    var orderTrend = prevOrders ? ((curOrders - prevOrders) / prevOrders) * 100 : 0;
    var running = d.tasks.filter(function (t) { return t.enabled; }).length;
    var lowStock = d.products.filter(function (p) { return p.stock < 100; }).length;
    return {
      unhandled: unhandled, online: online, total: d.products.length,
      curOrders: curOrders, orderTrend: orderTrend,
      running: running, totalTasks: d.tasks.length,
      lowStock: lowStock,
      totalRuns: d.stats.totalRuns, totalAlerts: d.alerts.length,
      lastRunAt: d.stats.lastRunAt
    };
  }

  /* ---------------- 导出 ---------------- */
  function exportEnv() {
    var d = get(), c = d.credentials || {}, ch = d.channels || {}, s = d.settings || {};
    var amz = c.amazon || {}, pdd = c.pdd || {}, wc = ch.wecom || {}, dt = ch.dingtalk || {};
    return [
      '# ===== 亚马逊 SP-API =====',
      'AMAZON_REFRESH_TOKEN=' + (amz.refreshToken || ''),
      'AMAZON_CLIENT_ID=' + (amz.clientId || ''),
      'AMAZON_CLIENT_SECRET=' + (amz.clientSecret || ''),
      'AMAZON_AWS_ACCESS_KEY=' + (amz.awsAccessKey || ''),
      'AMAZON_AWS_SECRET_KEY=' + (amz.awsSecretKey || ''),
      'AMAZON_SELLER_ID=' + (amz.sellerId || ''),
      'AMAZON_MARKETPLACE_ID=' + (amz.marketplaceId || 'ATVPDKIKX0DER'),
      '',
      '# ===== 拼多多开放平台 =====',
      'PDD_CLIENT_ID=' + (pdd.clientId || ''),
      'PDD_CLIENT_SECRET=' + (pdd.clientSecret || ''),
      'PDD_ACCESS_TOKEN=' + (pdd.accessToken || ''),
      '',
      '# ===== 告警渠道 =====',
      'WECOM_WEBHOOK_URL=' + (wc.url || ''),
      'DINGTALK_WEBHOOK_URL=' + (dt.url || ''),
      'DINGTALK_SECRET=' + (dt.secret || ''),
      '',
      '# ===== 阈值 =====',
      'INVENTORY_DROP_THRESHOLD=' + s.thresholdInventory,
      'ORDER_COUNT_DROP_THRESHOLD=' + s.thresholdOrders,
      '',
      '# ===== 大模型建议(可选,留空则不调用) =====',
      'LLM_ENABLED=' + (s.llm && s.llm.enabled ? 'true' : 'false'),
      'LLM_BASE_URL=' + ((s.llm && s.llm.baseUrl) || ''),
      'LLM_MODEL=' + ((s.llm && s.llm.model) || ''),
      'LLM_API_KEY=' + ((s.llm && s.llm.apiKey) || ''),
      'LLM_COOLDOWN_MIN=' + ((s.llm && s.llm.cooldownMin) || 60),
      '',
      '# ===== Listing 生成(上架环节) =====',
      'LISTING_BRAND=' + ((s.listing && s.listing.brand) || ''),
      'LISTING_AUTO_PUBLISH=' + (s.listing && s.listing.autoPublish ? 'true' : 'false'),
      'LISTING_SKIP_ON_ERROR=' + (s.listing && s.listing.skipOnError === false ? 'false' : 'true')
    ].join('\n');
  }

  function exportProductsPayload() {
    var d = get();
    return JSON.stringify(d.products.filter(function (p) { return p.status !== 'online'; }).map(function (p) {
      return p.platform === 'amazon'
        ? { platform: 'amazon', sku: p.sku, payload: { item_name: p.title, price: p.price, quantity: p.stock, category: p.category } }
        : { platform: 'pdd', payload: { goods_name: p.title, price: Math.round(p.price * 100), quantity: p.stock, cat_name: p.category } };
    }), null, 2);
  }

  function download(filename, text) {
    var blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 100);
  }

  /* ---------------- Mock 商品图 ---------------- */
  function saveMockImageSet(set) {
    var d = get();
    d.mockImageSets = d.mockImageSets || [];
    d.mockImageSets.unshift(set);
    if (d.mockImageSets.length > 30) d.mockImageSets.length = 30;
    save();
    return set;
  }
  function listMockImageSets() { return (get().mockImageSets || []).slice(); }
  function markMockImageSet(id, gallery) {
    var d = get(), set = (d.mockImageSets || []).find(function (x) { return x.id === id; });
    if (!set) return null;
    set.gallery = gallery; set.status = gallery; set.updatedAt = now(); save(); return set;
  }

  /* ---------------- 会话 ---------------- */
  var SESSION_USER = { username: 'admin', password: 'admin123' };
  function login(u, p) {
    if (u === SESSION_USER.username && p === SESSION_USER.password) {
      localStorage.setItem(SESSION_KEY, JSON.stringify({ user: u, at: now() }));
      return { ok: true };
    }
    return { ok: false, msg: '账号或密码不正确（演示账号 admin / admin123）' };
  }
  function logout() { localStorage.removeItem(SESSION_KEY); }
  function session() {
    try { return JSON.parse(localStorage.getItem(SESSION_KEY)); } catch (e) { return null; }
  }

  /* ---------------- 导出 ---------------- */
  global.Store = {
    load: load, save: save, get: get, reset: reset,
    runMonitor: runMonitor, detect: detect, metricLabel: metricLabel, metricValue: metricValue,
    saveProduct: saveProduct, removeProduct: removeProduct, publishProducts: publishProducts,
    saveTask: saveTask, removeTask: removeTask, toggleTask: toggleTask,
    resolveAlert: resolveAlert, resolveAllAlerts: resolveAllAlerts,
    updateSettings: updateSettings, updateCredentials: updateCredentials, updateChannels: updateChannels,
    testChannel: testChannel, stats: stats, addLog: addLog,
    CATEGORIES: CATEGORIES, ADVICE: ADVICE, categoryInfo: categoryInfo,
    classifyAlert: classifyAlert, buildAdvice: buildAdvice, parseLlm: parseLlm,
    LISTING_LIMITS: LISTING_LIMITS, LISTING_SCHEMA: LISTING_SCHEMA, BANNED_WORDS: BANNED_WORDS,
    schemaForListing: schemaForListing, genListing: genListing, validateListing: validateListing,
    listingToSpapi: listingToSpapi, saveListing: saveListing, byteLen: byteLen, cnToEn: cnToEn,
    exportEnv: exportEnv, exportProductsPayload: exportProductsPayload, download: download,
    saveMockImageSet: saveMockImageSet, listMockImageSets: listMockImageSets, markMockImageSet: markMockImageSet,
    login: login, logout: logout, session: session,
    uid: uid, now: now, fmtTime: fmtTime, fmtClock: fmtClock, fmtHour: fmtHour, fromNow: fromNow, pad: pad
  };
})(window);
