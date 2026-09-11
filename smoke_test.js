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
  window.localStorage.setItem('ecom_sop_admin_v1', JSON.stringify(v2));
  S.load();
  const d = S.get();
  ok('v2->v3 bumps to version 3', d.version === 3);
  ok('v2->v3 adds listing settings', !!(d.settings && d.settings.listing));
  ok('v2->v3 adds image library settings', !!(d.settings && d.settings.imageLibrary));
  ok('v2->v3 preserves stats', !!(d.stats && typeof d.stats.totalRuns === 'number'));
} catch (e) { ok('v2->v3 migration', false); console.log('  -> ' + e.message + '\n' + e.stack); }

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
