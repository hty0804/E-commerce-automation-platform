/* ==========================================================
 * ui.js —— 通用组件（Toast / Modal / 标签）与纯 SVG 图表
 * 图表全部手写 SVG，不依赖任何外部 CDN，断网也能正常渲染。
 * ========================================================== */
(function (global) {
  'use strict';

  /* ---------------- HTML 转义 ---------------- */
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /* ---------------- 统一内联 SVG 图标集 ----------------
   * 24x24 viewBox / 描边风格 / currentColor 着色，零 CDN、零 emoji、零图标字体。
   * 每个图标只存内部路径，icon() 负责包裹 <svg>。size 默认为 20px。 */
  var ICONS = {
    dashboard: '<rect x="3" y="3" width="7.5" height="7.5" rx="1.6"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.6"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="1.6"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.6"/>',
    box: '<path d="M21 8 12 3 3 8v8l9 5 9-5z"/><path d="M3 8l9 5 9-5"/><path d="M12 13v8"/>',
    robot: '<rect x="4" y="8" width="16" height="12" rx="2.4"/><path d="M12 8V4"/><circle cx="12" cy="3" r="1.4"/><circle cx="9" cy="14" r="1.3"/><circle cx="15" cy="14" r="1.3"/><path d="M9.5 17h5"/>',
    gear: '<path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"/><path d="M19.4 13.5a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"/>',
    bell: '<path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>',
    bellOff: '<path d="M8.7 3.7A6 6 0 0 1 18 8c0 7 3 9 3 9H8"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/><path d="M3 3l18 18"/>',
    list: '<line x1="9" y1="6" x2="20" y2="6"/><line x1="9" y1="12" x2="20" y2="12"/><line x1="9" y1="18" x2="20" y2="18"/><circle cx="4.5" cy="6" r="1.1"/><circle cx="4.5" cy="12" r="1.1"/><circle cx="4.5" cy="18" r="1.1"/>',
    key: '<circle cx="7.5" cy="15.5" r="4.5"/><path d="M10.5 12.5 21 2"/><path d="M16 7l3 3"/>',
    sliders: '<line x1="4" y1="6" x2="20" y2="6"/><circle cx="9" cy="6" r="2.4" fill="currentColor" stroke="none"/><line x1="4" y1="12" x2="20" y2="12"/><circle cx="15" cy="12" r="2.4" fill="currentColor" stroke="none"/><line x1="4" y1="18" x2="20" y2="18"/><circle cx="7" cy="18" r="2.4" fill="currentColor" stroke="none"/>',
    book: '<path d="M5 4h11a2 2 0 0 1 2 2v13a1 1 0 0 1-1 1H6a2 2 0 0 0-2 2V4z"/><line x1="8.5" y1="8" x2="14" y2="8"/><line x1="8.5" y1="12" x2="14" y2="12"/>',
    cart: '<circle cx="9" cy="20" r="1.5"/><circle cx="18" cy="20" r="1.5"/><path d="M2 3h3l2.6 13h12l2-9H6"/>',
    eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/>',
    menu: '<line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/>',
    play: '<path d="M8 5.5v13l11-6.5z" fill="currentColor" stroke="none"/>',
    check: '<circle cx="12" cy="12" r="9"/><path d="M8.5 12.5l2.5 2.5 4.5-5"/>',
    cross: '<circle cx="12" cy="12" r="9"/><line x1="9" y1="9" x2="15" y2="15"/><line x1="15" y1="9" x2="9" y2="15"/>',
    warn: '<path d="M12 3 2 20h20z"/><line x1="12" y1="9" x2="12" y2="14"/><circle cx="12" cy="17" r="1.1" fill="currentColor" stroke="none"/>',
    info: '<circle cx="12" cy="12" r="9"/><line x1="12" y1="11" x2="12" y2="16"/><circle cx="12" cy="8" r="1.1" fill="currentColor" stroke="none"/>',
    trendUp: '<path d="M3 17l6-6 4 4 8-8"/><path d="M21 7v6h-6"/>',
    trendDown: '<path d="M3 7l6 6 4-4 8 8"/><path d="M21 17v-6h-6"/>',
    doc: '<path d="M6 2h8l6 6v14H6z"/><path d="M14 2v6h6"/><line x1="9.5" y1="13" x2="15" y2="13"/><line x1="9.5" y1="17" x2="15" y2="17"/>',
    bolt: '<path d="M13 2 4 14h7l-1 8 9-12h-7z" fill="currentColor" stroke="none"/>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    alert: '<path d="M6 18V11a6 6 0 0 1 12 0v7l2 1.5V21H4v-1.5z"/><path d="M10 21a2 2 0 0 0 4 0"/><line x1="12" y1="7" x2="12" y2="4"/>',
    dollar: '<circle cx="12" cy="12" r="9"/><path d="M12 7v10"/><path d="M14.6 9.3C14 8.5 13 8 12 8c-1.6 0-2.6 1-2.6 2.1 0 1.4 2 1.9 2.6 2.4.6.5 2.6 1 2.6 2.5S13.6 18 12 18c-1 0-2-.5-2.6-1.4"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
    plug: '<path d="M9 2v6M15 2v6M7 8h10v3a5 5 0 0 1-10 0z"/><path d="M12 16v6"/>',
    help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.2a2.5 2.5 0 0 1 4.2 1.8c0 1.6-2 2.2-2 3.6"/><circle cx="12" cy="17" r="1.1" fill="currentColor" stroke="none"/>',
    image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9" r="1.5"/><path d="m4 17 5-5 3.5 3 2.5-2 5 5"/>'
  };

  function icon(name, size) {
    size = size || 20;
    var p = ICONS[name] || ICONS.folder;
    return '<svg class="ic-svg" width="' + size + '" height="' + size + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + p + '</svg>';
  }

  /* ---------------- Toast ---------------- */
  function toast(type, title, msg, ms) {
    var root = document.getElementById('toastRoot');
    var icons = { ok: 'check', err: 'cross', warn: 'warn', info: 'info' };
    var el = document.createElement('div');
    el.className = 'toast ' + (type === 'ok' ? 'ok' : type === 'err' ? 'err' : type === 'warn' ? 'warn' : '');
    el.innerHTML = '<div class="t-ic">' + icon(icons[type] || 'info', 20) + '</div><div><div class="t-title">' +
      esc(title) + '</div>' + (msg ? '<div class="t-msg">' + esc(msg) + '</div>' : '') + '</div>';
    root.appendChild(el);
    setTimeout(function () {
      el.style.opacity = '0'; el.style.transform = 'translateX(30px)'; el.style.transition = '.25s';
      setTimeout(function () { el.remove(); }, 260);
    }, ms || 3000);
  }

  /* ---------------- Modal ---------------- */
  function modal(opts) {
    var root = document.getElementById('modalRoot');
    var wrap = document.createElement('div');
    wrap.className = 'modal-mask';
    wrap.innerHTML =
      '<div class="modal" style="max-width:' + (opts.width || 620) + 'px">' +
        '<div class="modal-head"><h3>' + esc(opts.title || '') + '</h3><button class="modal-close">×</button></div>' +
        '<div class="modal-body">' + (opts.body || '') + '</div>' +
        (opts.hideFoot ? '' :
          '<div class="modal-foot">' +
            '<button class="btn" data-act="cancel">' + esc(opts.cancelText || '取消') + '</button>' +
            (opts.extraBtn || '') +
            '<button class="btn btn-primary" data-act="ok">' + esc(opts.okText || '保存') + '</button>' +
          '</div>') +
      '</div>';
    root.appendChild(wrap);

    function close() { wrap.remove(); }
    wrap.querySelector('.modal-close').onclick = close;
    wrap.addEventListener('click', function (e) { if (e.target === wrap) close(); });
    var cancelBtn = wrap.querySelector('[data-act="cancel"]');
    if (cancelBtn) cancelBtn.onclick = close;
    var okBtn = wrap.querySelector('[data-act="ok"]');
    if (okBtn && opts.onOk) {
      okBtn.onclick = function () {
        if (opts.onOk(wrap, okBtn) === false) return;
        close();
      };
    }
    if (opts.onOpen) opts.onOpen(wrap);
    return { el: wrap, close: close };
  }

  function confirm(title, msg, onOk, okText) {
    modal({
      title: title, width: 420,
      body: '<div style="line-height:1.8;color:var(--text-2)">' + esc(msg) + '</div>',
      okText: okText || '确定',
      onOk: function () { onOk && onOk(); }
    });
    var okBtn = document.querySelector('.modal-mask [data-act="ok"]');
    if (okBtn) okBtn.className = 'btn btn-danger';
  }

  /* ---------------- 标签 ---------------- */
  function platformTag(p) {
    var map = {
      amazon: ['亚马逊', 'tag-blue'],
      pdd: ['拼多多', 'tag-red'],
      all: ['全平台', 'tag-purple']
    };
    var m = map[p] || [p, 'tag-gray'];
    return '<span class="tag ' + m[1] + '">' + m[0] + '</span>';
  }
  function platformName(p) { return p === 'amazon' ? '亚马逊' : p === 'pdd' ? '拼多多' : '全平台'; }

  function statusTag(s) {
    var map = {
      online: ['在售', 'tag-green'], pending: ['处理中', 'tag-yellow'],
      offline: ['已下架', 'tag-gray'], failed: ['上架失败', 'tag-red'],
      success: ['正常', 'tag-green'], warning: ['异常', 'tag-yellow'],
      idle: ['未运行', 'tag-gray'], running: ['运行中', 'tag-blue'],
      handled: ['已处理', 'tag-gray'], unhandled: ['未处理', 'tag-red']
    };
    var m = map[s] || [s, 'tag-gray'];
    return '<span class="tag ' + m[1] + '">' + m[0] + '</span>';
  }

  function levelTag(l) {
    var map = {
      critical: ['严重', 'tag-red'], warning: ['警告', 'tag-yellow'],
      info: ['提示', 'tag-blue'], error: ['错误', 'tag-red'], warn: ['警告', 'tag-yellow']
    };
    var m = map[l] || [l, 'tag-gray'];
    return '<span class="tag ' + m[1] + '">' + m[0] + '</span>';
  }

  function metricTag(m) {
    var map = { inventory: ['库存', 'tag-cyan'], orders: ['订单', 'tag-blue'], price: ['价格', 'tag-purple'], gmv: ['GMV', 'tag-green'] };
    var t = map[m] || [m, 'tag-gray'];
    return '<span class="tag ' + t[1] + '">' + t[0] + '</span>';
  }

  /* ==========================================================
   * SVG 图表
   * ========================================================== */
  var C = {
    axis: '#e6eaf2', grid: '#f0f3f9', label: '#8b96a8',
    blue: '#3563e9', green: '#15a46b', purple: '#7c4dff', orange: '#e08c1a', red: '#dc3a3a', cyan: '#0e9dbd'
  };

  /** 折线 + 面积图
   * points: [{label, value}], opts: {color, height, unit, fill} */
  function lineChart(points, opts) {
    opts = opts || {};
    if (!points || !points.length) return '<div class="empty">暂无数据</div>';
    var W = 800, H = opts.height || 230;
    var padL = 46, padR = 16, padT = 14, padB = 26;
    var iw = W - padL - padR, ih = H - padT - padB;
    var vals = points.map(function (p) { return p.value; });
    var max = Math.max.apply(null, vals), min = Math.min.apply(null, vals);
    var top = max + (max - min) * 0.18 + 1, bot = Math.max(0, min - (max - min) * 0.18);
    if (top === bot) top = bot + 10;
    var color = opts.color || C.blue;

    var x = function (i) { return padL + (points.length === 1 ? iw / 2 : (i * iw) / (points.length - 1)); };
    var y = function (v) { return padT + ih - ((v - bot) / (top - bot)) * ih; };

    var grid = '', i;
    for (i = 0; i <= 4; i++) {
      var gy = padT + (ih * i) / 4;
      var gv = top - ((top - bot) * i) / 4;
      grid += '<line x1="' + padL + '" y1="' + gy + '" x2="' + (W - padR) + '" y2="' + gy + '" stroke="' + C.grid + '" stroke-width="1"/>';
      grid += '<text x="' + (padL - 8) + '" y="' + (gy + 4) + '" font-size="11" fill="' + C.label + '" text-anchor="end">' + Math.round(gv) + '</text>';
    }

    var step = Math.ceil(points.length / 8);
    var xlabels = '';
    points.forEach(function (p, idx) {
      if (idx % step === 0 || idx === points.length - 1) {
        xlabels += '<text x="' + x(idx) + '" y="' + (H - 7) + '" font-size="11" fill="' + C.label + '" text-anchor="middle">' + esc(p.label) + '</text>';
      }
    });

    var dLine = points.map(function (p, idx) { return (idx ? 'L' : 'M') + x(idx).toFixed(1) + ' ' + y(p.value).toFixed(1); }).join(' ');
    var dArea = dLine + ' L' + x(points.length - 1).toFixed(1) + ' ' + (padT + ih) + ' L' + x(0).toFixed(1) + ' ' + (padT + ih) + ' Z';
    var gid = 'g_' + Math.random().toString(36).slice(2, 8);

    var dots = points.map(function (p, idx) {
      return '<circle cx="' + x(idx).toFixed(1) + '" cy="' + y(p.value).toFixed(1) + '" r="3.2" fill="#fff" stroke="' + color + '" stroke-width="2">' +
        '<title>' + esc(p.label) + '：' + p.value + (opts.unit || '') + '</title></circle>';
    }).join('');

    return '<svg viewBox="0 0 ' + W + ' ' + H + '" style="width:100%;height:' + H + 'px;display:block">' +
      '<defs><linearGradient id="' + gid + '" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0%" stop-color="' + color + '" stop-opacity="0.22"/>' +
      '<stop offset="100%" stop-color="' + color + '" stop-opacity="0.02"/></linearGradient></defs>' +
      grid + xlabels +
      '<path d="' + dArea + '" fill="url(#' + gid + ')"/>' +
      '<path d="' + dLine + '" fill="none" stroke="' + color + '" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>' +
      dots + '</svg>';
  }

  /** 柱状图 */
  function barChart(points, opts) {
    opts = opts || {};
    if (!points || !points.length) return '<div class="empty">暂无数据</div>';
    var W = 800, H = opts.height || 200;
    var padL = 40, padR = 12, padT = 14, padB = 26;
    var iw = W - padL - padR, ih = H - padT - padB;
    var max = Math.max.apply(null, points.map(function (p) { return p.value; })) * 1.15 + 1;
    var color = opts.color || C.blue;
    var bw = (iw / points.length) * 0.56;
    var bars = points.map(function (p, i) {
      var bx = padL + (i * iw) / points.length + (iw / points.length - bw) / 2;
      var bh = (p.value / max) * ih;
      var by = padT + ih - bh;
      var c = p.color || color;
      return '<rect x="' + bx.toFixed(1) + '" y="' + by.toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + Math.max(bh, 2).toFixed(1) + '" rx="4" fill="' + c + '">' +
        '<title>' + esc(p.label) + '：' + p.value + '</title></rect>' +
        '<text x="' + (bx + bw / 2).toFixed(1) + '" y="' + (H - 8) + '" font-size="11" fill="' + C.label + '" text-anchor="middle">' + esc(p.label) + '</text>';
    }).join('');
    var grid = '';
    for (var i = 0; i <= 3; i++) {
      var gy = padT + (ih * i) / 3;
      grid += '<line x1="' + padL + '" y1="' + gy + '" x2="' + (W - padR) + '" y2="' + gy + '" stroke="' + C.grid + '"/>' +
        '<text x="' + (padL - 7) + '" y="' + (gy + 4) + '" font-size="11" fill="' + C.label + '" text-anchor="end">' + Math.round(max - (max * i) / 3) + '</text>';
    }
    return '<svg viewBox="0 0 ' + W + ' ' + H + '" style="width:100%;height:' + H + 'px;display:block">' + grid + bars + '</svg>';
  }

  /** 环形图 */
  function donut(items, opts) {
    opts = opts || {};
    var total = items.reduce(function (a, b) { return a + b.value; }, 0);
    var size = 200, r = 74, cx = size / 2, cy = size / 2, sw = 24;
    var circ = 2 * Math.PI * r, offset = 0;
    var segs = items.map(function (it) {
      var frac = total ? it.value / total : 0;
      var len = frac * circ;
      var s = '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="none" stroke="' + it.color + '" stroke-width="' + sw +
        '" stroke-dasharray="' + len.toFixed(2) + ' ' + (circ - len).toFixed(2) + '" stroke-dashoffset="' + (-offset).toFixed(2) +
        '" transform="rotate(-90 ' + cx + ' ' + cy + ')"><title>' + esc(it.label) + '：' + it.value + '</title></circle>';
      offset += len;
      return s;
    }).join('');
    return '<div style="display:flex;align-items:center;gap:22px;flex-wrap:wrap;justify-content:center">' +
      '<svg viewBox="0 0 ' + size + ' ' + size + '" style="width:180px;height:180px;flex:none">' +
        '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="none" stroke="#f0f3f9" stroke-width="' + sw + '"/>' + segs +
        '<text x="' + cx + '" y="' + (cy - 2) + '" text-anchor="middle" font-size="26" font-weight="700" fill="#1b2436">' + total + '</text>' +
        '<text x="' + cx + '" y="' + (cy + 18) + '" text-anchor="middle" font-size="11" fill="#8b96a8">' + esc(opts.centerLabel || '总计') + '</text>' +
      '</svg>' +
      '<div style="display:flex;flex-direction:column;gap:9px">' + items.map(function (it) {
        var pct = total ? Math.round((it.value / total) * 100) : 0;
        return '<div style="display:flex;align-items:center;gap:8px;font-size:13px">' +
          '<i style="width:10px;height:10px;border-radius:3px;background:' + it.color + ';display:inline-block"></i>' +
          '<span style="color:var(--text-2);min-width:66px">' + esc(it.label) + '</span>' +
          '<b>' + it.value + '</b><span style="color:var(--muted)">' + pct + '%</span></div>';
      }).join('') + '</div></div>';
  }

  /** 迷你趋势线 */
  function sparkline(values, color) {
    if (!values || values.length < 2) return '';
    var W = 120, H = 34, max = Math.max.apply(null, values), min = Math.min.apply(null, values);
    if (max === min) max = min + 1;
    var d = values.map(function (v, i) {
      var x = (i * W) / (values.length - 1);
      var y = H - ((v - min) / (max - min)) * (H - 6) - 3;
      return (i ? 'L' : 'M') + x.toFixed(1) + ' ' + y.toFixed(1);
    }).join(' ');
    return '<svg viewBox="0 0 ' + W + ' ' + H + '" style="width:120px;height:34px"><path d="' + d +
      '" fill="none" stroke="' + (color || C.blue) + '" stroke-width="2" stroke-linecap="round"/></svg>';
  }

  global.UI = {
    esc: esc, icon: icon, toast: toast, modal: modal, confirm: confirm,
    platformTag: platformTag, platformName: platformName, statusTag: statusTag, levelTag: levelTag, metricTag: metricTag,
    lineChart: lineChart, barChart: barChart, donut: donut, sparkline: sparkline, colors: C
  };
})(window);
