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

const files = ['assets/js/store.js', 'assets/js/ui.js', 'assets/js/views.js', 'assets/js/app.js'];
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
for (const k of viewKeys) {
  try {
    const v = V[k];
    const out = v.render();
    if (v.mount) v.mount();
    if (typeof out === 'string' && out.length > 0) rendered++;
    else fails.push('view ' + k + ' empty');
  } catch (e) {
    fail++; fails.push('view ' + k + ' threw: ' + e.message);
  }
}
ok('all ' + viewKeys.length + ' views render', rendered === viewKeys.length);

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

console.log('\n==== SMOKE TEST RESULT ====');
console.log('PASS: ' + pass + '   FAIL: ' + fail);
if (fails.length) { console.log('FAILED CHECKS:'); fails.forEach(f => console.log('  - ' + f)); }
process.exit(fail ? 1 : 0);
