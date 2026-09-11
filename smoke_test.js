/* Frontend smoke test — boots the SPA in jsdom, renders every view,
 * and exercises the Listing-generation pipeline.
 * Setup:   npm install   (installs jsdom devDependency)
 * Run:     npm test      (or: node smoke_test.js)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const ROOT = __dirname;
const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');

const dom = new JSDOM(html, {
  url: 'http://localhost/',
  runScripts: 'outside-only',
  pretendToBeVisual: true,
});
const { window } = dom;
window.scrollTo = () => {};
if (!window.matchMedia) {
  window.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {} });
}

// 直接从 index.html 里解析，不要在这里另维护一份顺序：
// 新增脚本（比如 listing_rules.js）时漏改这里会直接炸，而炸在这里正是我们想要的。
// 容忍 ?v=2 这类缓存版本号：浏览器/代理会忽略 query 段去取实际文件，
// 我们读本地文件时也得把 query 段剥掉，否则 `?` 在 Windows 是非法文件名字符。
const files = [...html.matchAll(/<script src="([^"]+)"><\/script>/g)]
  .map(m => m[1].split('?')[0]);
if (!files.length) { console.error('index.html 里没解析到任何 <script src>'); process.exit(1); }
for (const f of files) {
  window.eval(fs.readFileSync(path.join(ROOT, f), 'utf8'));
}

let pass = 0, fail = 0;
const fails = [];
function ok(name, cond) {
  if (cond) { pass++; } else { fail++; fails.push(name); }
}

// boot
try { window.App.init(); ok('App.init runs', true); }
catch (e) { ok('App.init runs', false); console.log('  -> ' + e.message + '\n' + e.stack); }

const S = window.Store, V = window.Views, U = window.UI;
ok('Store global', !!S);
ok('Views global', !!V);
ok('UI global', !!U);

// login
try { const r = S.login('admin', 'admin123'); ok('login ok', r && r.ok); }
catch (e) { ok('login ok', false); console.log('  -> ' + e.message); }

// render every view
const viewKeys = Object.keys(V || {});
let rendered = 0;
const renderedHtml = {};
for (const k of viewKeys) {
  try {
    const v = V[k];
    const out = v.render();
    if (v.mount) v.mount();
    if (typeof out === 'string' && out.length > 0) { rendered++; renderedHtml[k] = out; }
    else fails.push('view ' + k + ' empty');
  } catch (e) {
    fail++; fails.push('view ' + k + ' threw: ' + e.message);
  }
}
ok('all ' + viewKeys.length + ' views render', rendered === viewKeys.length);

/* ---------- 图标渲染回归 ----------
 * 之前踩过两次坑，这里钉死：
 *  1) kpi() 拿到图标名却忘了调 U.icon()，结果把 'bell'/'cart' 当文本渲染；
 *  2) sub 被 esc() 转义，内嵌的 SVG 变成 &lt;svg ...&gt; 源码漏在页面上。
 */
const allHtml = Object.keys(renderedHtml).map(k => renderedHtml[k]).join('');
ok('没有转义后的 SVG 源码漏出(&lt;svg)', !/&lt;svg/.test(allHtml));
ok('确实渲染出了内联 SVG 图标', /<svg class="ic-svg"/.test(allHtml));
['bell', 'box', 'cart', 'gear'].forEach(function (nm) {
  ok('KPI 未把图标名 "' + nm + '" 当文本渲染',
     !new RegExp('>' + nm + '<').test(allHtml));
});

/* ---------- 回归：Listing 表单默认状态不能被日志筛选状态覆盖 ----------
 * views.js 里曾经用同一个变量名 lf 同时表示「Listing 表单状态」和「日志筛选状态」。
 * 同一个 IIFE 作用域里 var 重复声明不会报错，后者在模块加载时把前者整个覆盖掉 ——
 * 于是 Listing 表单的 platform/category 默认值全丢，类目与平台下拉渲染不出 selected
 * （实测各 0 个）。这条断言把"默认选中项必须存在"钉死。
 */
try {
  const lh = V.listing.render();
  const catBlock = (lh.match(/<select class="select" id="lgCategory">[\s\S]*?<\/select>/) || [''])[0];
  const platBlock = (lh.match(/<select class="select" id="lgPlatform">[\s\S]*?<\/select>/) || [''])[0];
  ok('Listing 类目下拉有且只有 1 个默认选中项',
     (catBlock.match(/ selected/g) || []).length === 1);
  ok('Listing 平台下拉有且只有 1 个默认选中项',
     (platBlock.match(/ selected/g) || []).length === 1);
} catch (e) { ok('Listing 默认选中项', false); console.log('  -> ' + e.message); }

/* ---------- 回归：看板时间线的 class 必须与 style.css 一致 ----------
 * style.css 定义的是 .tl / .tl-item（配合 .tl-dot/.tl-body/.tl-title/.tl-meta）。
 * 视图层曾经输出 class="timeline" 且 <li> 不带类名 —— 容器和条目两个类都对不上，
 * 列表退回浏览器默认样式（出现项目符号、不是 flex、圆点与文字错位）。
 */
try {
  const dh = V.dashboard.render();
  ok('看板时间线使用 .tl 容器', /<ul class="tl">/.test(dh));
  ok('看板时间线条目使用 .tl-item', /<li class="tl-item">/.test(dh));
  ok('看板不再出现未定义的 .timeline', !/class="timeline"/.test(dh));
} catch (e) { ok('看板时间线 class', false); console.log('  -> ' + e.message); }

/* ---------- 回归：「记住登录状态」勾选框必须真的生效 ----------
 * 以前 login() 完全不看这个勾选框，勾不勾都写 localStorage ——
 * 用户以为"不记住"能少留一份登录态，实际仍然长期驻留。
 */
try {
  const LS = 'ecom_sop_session', SS = 'ecom_sop_session_tmp';
  window.localStorage.removeItem(LS);
  window.sessionStorage.removeItem(SS);

  S.login('admin', 'admin123', false);            // 不勾「记住」
  ok('不勾「记住」时不写 localStorage', !window.localStorage.getItem(LS));
  ok('不勾「记住」时写 sessionStorage', !!window.sessionStorage.getItem(SS));
  ok('不勾「记住」时仍能读到会话', !!S.session());

  S.login('admin', 'admin123', true);             // 勾「记住」
  ok('勾「记住」时写 localStorage', !!window.localStorage.getItem(LS));
  ok('勾「记住」时清掉临时会话', !window.sessionStorage.getItem(SS));
} catch (e) { ok('记住登录状态', false); console.log('  -> ' + e.message); }

// v2 -> v3 migration (realistic seed: current v3 DB, downgraded to v2)
try {
  const cur = S.get();
  const v2 = JSON.parse(JSON.stringify(cur));
  v2.version = 2;
  delete v2.settings.listing;
  // 写回当前店铺自己的键（多店铺分库后键名带 shop_id），
  // 不再硬编码旧键名 —— 将来改分库格式时这条测试仍然有效。
  window.localStorage.setItem(S.storageKey(S.activeShopId()), JSON.stringify(v2));
  S.load();
  const d = S.get();
  ok('v2->v3 bumps to version 3', d.version === 3);
  ok('v2->v3 adds listing settings', !!(d.settings && d.settings.listing));
  ok('v2->v3 adds image library settings', !!(d.settings && d.settings.imageLibrary));
  ok('v2->v3 preserves stats', !!(d.stats && typeof d.stats.totalRuns === 'number'));
} catch (e) { ok('v2->v3 migration', false); console.log('  -> ' + e.message + '\n' + e.stack); }

/* ---------- 多店铺：注册表 / 隔离 / 迁移 / 切换 ---------- */
try {
  const main = S.activeShop();
  ok('默认存在一个主店', !!main && main.id === 'shop_default');
  ok('主店承载了老数据（迁移是搬家不是重建）', S.get().products.length > 0);
  ok('数据自带 shopId 归属', S.get().shopId === main.id);
  ok('注册表里有主店', S.shops().some(s => s.id === main.id));

  const beforeMain = S.get().products.length;

  // 新建一个**空库**店铺：不能塞演示数据，否则真店和假数据混在一起
  const made = S.createShop('测试二店', { seed: false });
  ok('createShop 成功', made.ok);
  ok('同名店铺被拒绝', S.createShop('测试二店').ok === false);
  ok('新建店铺不影响当前店铺', S.activeShopId() === main.id && S.get().products.length === beforeMain);

  // 切到二店：必须是空库，且各视图/统计都不能因为空数据而炸
  S.switchShop(made.shop.id);
  ok('切到二店后 activeShopId 正确', S.activeShopId() === made.shop.id);
  ok('空店不塞假商品', S.get().products.length === 0);
  ok('空店统计不炸', typeof S.stats().total === 'number');
  try {
    Object.keys(V).forEach(k => {
      const out = V[k].render();
      if (!(typeof out === 'string' && out.length > 0)) throw new Error(k + ' 渲染为空');
    });
    ok('空店能渲染所有视图', true);
  } catch (e) { ok('空店能渲染所有视图', false); console.log('  -> ' + e.message); }

  // 隔离验证：在二店加一个商品，主店不能看见
  S.saveProduct({ id: null, platform: 'amazon', sku: 'SHOP2-001', title: '二店专属商品', category: '3C数码', price: 9.9, currency: 'USD', stock: 5, status: 'pending', autoSync: true });
  ok('二店能看到自己的商品', S.get().products.some(p => p.sku === 'SHOP2-001'));

  S.switchShop(main.id);
  ok('主店看不到二店的商品（分库隔离）', !S.get().products.some(p => p.sku === 'SHOP2-001'));
  ok('主店商品数未被二店影响', S.get().products.length === beforeMain);
  ok('切换后 activeShopId 正确', S.activeShopId() === main.id);

  // 重命名
  ok('renameShop 成功', S.renameShop(made.shop.id, '测试二店-改名').ok);
  ok('重命名后注册表可见', S.shops().some(s => s.name === '测试二店-改名'));
  ok('重名被拒绝', S.renameShop(made.shop.id, '主店').ok === false);

  // 删除：不能删到 0 个，且删店要连带删掉它那一库数据
  ok('removeShop 成功', S.removeShop(made.shop.id).ok);
  ok('删店后注册表只剩主店', S.shops().length === 1);
  ok('删店后存储键被清掉', !window.localStorage.getItem(S.storageKey(made.shop.id)));
  ok('不允许删掉最后一个店铺', S.removeShop(main.id).ok === false);

  // .env 导出必须带上 SHOP_ID，后端靠它做数据隔离
  const env = S.exportEnv();
  ok('exportEnv 含 SHOP_ID', /^SHOP_ID=/m.test(env));
  ok('exportEnv 的 SHOP_ID 与当前店铺一致', env.indexOf('SHOP_ID=' + main.id) >= 0);
} catch (e) { ok('多店铺数据层', false); console.log('  -> ' + e.message + '\n' + e.stack); }

/* ---------- 多店铺：顶栏切换器 + 店铺管理视图 ---------- */
try {
  const sel = window.document.getElementById('shopSelect');
  ok('顶栏存在店铺切换器', !!sel);
  window.App.renderShopSwitcher();
  ok('切换器渲染出全部店铺', sel.options.length === S.shops().length);
  ok('切换器选中当前店铺', sel.value === S.activeShopId());

  const shopsHtml = V.shops.render();
  ok('店铺管理视图渲染出店铺卡片', /class="shop-card/.test(shopsHtml));
  ok('店铺管理视图含隔离说明', /SHOP_ID/.test(shopsHtml));

  // 端到端：新建 → 顶栏切换 → 顶栏同步 → 删掉
  const before = S.activeShopId();
  const made = S.createShop('UI 测试店', { seed: false });
  window.App.switchShop(made.shop.id);
  ok('App.switchShop 生效', S.activeShopId() === made.shop.id);
  ok('切换后顶栏同步', window.document.getElementById('shopSelect').value === made.shop.id);
  window.App.stopTimer();      // switchShop 会重启定时器，别让 setInterval 拖住进程
  window.App.switchShop(before);
  window.App.stopTimer();
  ok('切回原店铺', S.activeShopId() === before);
  ok('清理测试店铺', S.removeShop(made.shop.id).ok);
} catch (e) { ok('店铺切换器', false); console.log('  -> ' + e.message + '\n' + e.stack); }

// Listing generation pipeline (amazon, rule fallback)
try {
  const input = {
    category: '3C数码', name: '无线蓝牙耳机降噪', brand: 'Aurora',
    features: '主动降噪;蓝牙5.3;续航30小时;IPX5防水', specs: '颜色:黑/白;重量:45g',
    keywords: 'wireless earbuds, noise cancelling', audience: '通勤运动人群'
  };
  const r = S.genListing(input, 'amazon', false);
  ok('genListing amazon returns result', r && typeof r.listing === 'object');
// Mock 生图入口：不应发网络请求，结果保存到本地待筛选集合
try {
  const beforeMock = S.listMockImageSets().length;
  const mockSet = { id: 'smoke_mock', status: 'unclassified', gallery: 'unclassified', style: 'scene', styleLabel: '场景氛围图', subject: '耳机', count: 2, createdAt: S.now(), listingTitle: r.listing.title, images: [{ id: 'm1', index: 1, tone: '#fff' }, { id: 'm2', index: 2, tone: '#eee' }] };
  S.saveMockImageSet(mockSet);
  const afterMock = S.listMockImageSets();
  ok('mock image set saved locally', afterMock.length === beforeMock + 1 && afterMock[0].status === 'unclassified');
  ok('mock image set has selected style', afterMock[0].style === 'scene');
} catch (e) { ok('mock image set saved locally', false); ok('mock image set has selected style', false); }

/* ---------- AI 生图独立板块 ----------
 * 生图从「Listing 页的一个按钮 + 弹窗」提升成独立板块。
 * 这里锁住两件事：页面本身可用，以及生成动作只有一处（不再有第二套弹窗表单）。
 */
try {
  ok('存在 imageGen 视图', !!V.imageGen && typeof V.imageGen.render === 'function');
  ok('生图已从 Listing 页拆出（不再有第二套弹窗表单）',
     typeof V.listing.openMockImageModal === 'undefined' &&
     typeof V.listing.makeMockImages === 'undefined');

  const gh = V.imageGen.render();
  ok('生图页含商品主体输入', /id="igSubject"/.test(gh));
  ok('生图页含四套风格预设', (gh.match(/name="igStyle"/g) || []).length === 4);
  ok('生图页含生成套数', /id="igCount"/.test(gh));
  ok('生图页含"从商品带入"', /id="igFromProduct"/.test(gh));
  ok('生图页标明是前端 Mock', /不调用大模型/.test(gh));
  ok('生图页指向图片库', /imageLibrary/.test(gh));

  // 从 Listing 带入
  V.imageGen.prefill({ subject: '带入测试主体', points: '卖点A；卖点B', style: 'detail' });
  const gh2 = V.imageGen.render();
  ok('prefill 带入主体', /带入测试主体/.test(gh2));
  ok('prefill 带入卖点', /卖点A；卖点B/.test(gh2));
  ok('prefill 带入风格', /value="detail" checked/.test(gh2));

  // 组装逻辑（纯函数）：按当前设置出对应张数、初始为待筛选
  const built = V.imageGen.buildSet();
  ok('buildSet 用当前风格', built.style === 'detail' && built.styleLabel === '细节特写');
  ok('buildSet 按套数出图', built.images.length === built.count && built.images.length === 4);
  ok('buildSet 初始为待筛选', built.gallery === 'unclassified' && built.status === 'unclassified');
  const beforeGen = S.listMockImageSets().length;
  S.saveMockImageSet(built);
  ok('生成结果进入图库待筛选',
     S.listMockImageSets().length === beforeGen + 1 && S.listMockImageSets()[0].id === built.id);

  // DOM 接线：点「生成商品图」后按钮必须进入生成中（证明 handler 挂上了且校验通过）
  window.App.renderNav();
  ok('导航含「AI 生图」板块', /AI 生图/.test(window.document.getElementById('nav').innerHTML));
  window.App.go('imageGen');
  window.document.getElementById('igSubject').value = '端到端生图主体';
  const gbtn = window.document.getElementById('igGen');
  gbtn.click();
  ok('点生成后按钮进入生成中', gbtn.disabled === true && /生成中/.test(gbtn.textContent));
  window.App.stopTimer();
} catch (e) { ok('AI 生图板块', false); console.log('  -> ' + e.message + '\n' + e.stack); }

  ok('genListing amazon title non-empty', r && r.listing.title.length > 0);
  ok('genListing amazon 5 bullets', r && Array.isArray(r.listing.bullets) && r.listing.bullets.length === 5);
  ok('genListing amazon cnToEn applied',
    r && /Noise Cancelling/i.test(r.listing.title + ' ' + r.listing.bullets.join(' ')));
  ok('genListing amazon no residual CJK in title', r && !/[一-鿿]/.test(r.listing.title));
  ok('genListing returns issues[]', r && Array.isArray(r.issues));
  ok('genListing has no error-level issues', r && r.issues.every(i => i.level !== 'error'));

  const payload = S.listingToSpapi(r.listing, 'SKU-AUR-01', 'HEADPHONES');
  ok('toSpapi payload attributes is object', payload && typeof payload.attributes === 'object' && Object.keys(payload.attributes).length > 0);
  ok('toSpapi item_name present', !!(payload.attributes && payload.attributes.item_name));

  const pid = S.get().products[0].id;
  const saved = S.saveListing(pid, r);
  ok('saveListing ok', !!saved && !!saved.listing);
} catch (e) { ok('amazon listing pipeline', false); console.log('  -> ' + e.message + '\n' + e.stack); }

// Listing generation pipeline (pdd, Chinese)
try {
  const input = {
    category: '家居厨房', name: '不锈钢保温杯', brand: '暖house',
    features: '316不锈钢;12小时保温;一键开盖', specs: '容量:500ml', keywords: '', audience: '学生上班族'
  };
  const r = S.genListing(input, 'pdd', false);
  ok('genListing pdd ZH title', r && r.listing.title.length > 0);
  ok('genListing pdd no banned EN words',
    r && !/\b(best seller|free shipping)\b/i.test(r.listing.title + ' ' + r.listing.bullets.join(' ')));
} catch (e) { ok('pdd listing pipeline', false); console.log('  -> ' + e.message + '\n' + e.stack); }

// byte length / cnToEn
ok('byteLen ascii=3', S.byteLen('abc') === 3);
ok('byteLen cjk=6', S.byteLen('中文') === 6);
ok('cnToEn maps 降噪', /Noise Cancelling/i.test(S.cnToEn('主动降噪耳机')));

// .env export includes listing keys
try {
  const env = S.exportEnv();
  ok('exportEnv has LISTING_BRAND', /LISTING_BRAND=/.test(env));
  ok('exportEnv has LISTING_AUTO_PUBLISH', /LISTING_AUTO_PUBLISH=/.test(env));
  ok('exportEnv has LISTING_SKIP_ON_ERROR', /LISTING_SKIP_ON_ERROR=/.test(env));
} catch (e) { ok('exportEnv listing keys', false); console.log('  -> ' + e.message); }

// run monitor returns structure (runs on a valid v3 DB post-migration)
try {
  const r = S.runMonitor();
  ok('runMonitor returns checked', r && typeof r.checked === 'number');
  ok('runMonitor returns anomalies[]', r && Array.isArray(r.anomalies));
} catch (e) { ok('runMonitor', false); console.log('  -> ' + e.message + '\n' + e.stack); }

/* ---------- 规则必须来自共享数据源，且与后端一致 ----------
 * 前端曾经自己抄了一份违规词表，漏掉 best-seller / cure / 100% cure，
 * 结果「前端显示校验通过、后端却拦截」。下面把这条钉死。 */
const RULES = window.LISTING_RULES;
ok('LISTING_RULES 已加载', !!RULES);
if (RULES) {
  ok('违规词与共享源数量一致(14)', RULES.banned_words.length === 14);
  ['cure', 'best-seller', '100% cure'].forEach(function (w) {
    ok('违规词表含 ' + w, RULES.banned_words.some(function (b) { return b[0] === w; }));
  });
  ok('类目 required 含 item_name(未过滤前)',
    RULES.category_schema['3C数码'].required.indexOf('item_name') >= 0);
}
// 过滤掉顶层字段后，前端 required 不应再含 item_name / product_type
ok('schemaForListing 过滤顶层字段', (function () {
  const s = S.schemaForListing('3C数码');
  return s && s.required.indexOf('item_name') < 0 && s.required.indexOf('product_type') < 0;
})());
// 真正的行为验证：标题里出现 cure 必须被判 error
try {
  const bad = S.validateListing(
    { title: 'Acne Cure Gel Fast Treatment', bullets: [], description: '', keywords: [], attributes: {} },
    'amazon', S.schemaForListing('3C数码'), {});
  ok('标题含 cure 被判 error', bad.some(function (i) { return i.level === 'error' && /cure/i.test(i.msg); }));
  const good = S.validateListing(
    { title: 'Wireless Bluetooth Earbuds Waterproof Black', bullets: [], description: '', keywords: [], attributes: {} },
    'amazon', S.schemaForListing('3C数码'), {});
  ok('正常标题不报违规词', !good.some(function (i) { return /受限词/.test(i.msg); }));
} catch (e) { ok('validateListing 违规词', false); console.log('  -> ' + e.message); }

console.log('\n==== SMOKE TEST RESULT ====');
console.log('PASS: ' + pass + '   FAIL: ' + fail);
if (fails.length) { console.log('FAILED CHECKS:'); fails.forEach(f => console.log('  - ' + f)); }
process.exit(fail ? 1 : 0);
