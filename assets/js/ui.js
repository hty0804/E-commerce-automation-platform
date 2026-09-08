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

  /* ---------------- Toast ---------------- */
  function toast(type, title, msg, ms) {
    var root = document.getElementById('toastRoot');
    var icons = { ok: '✅', err: '⛔', warn: '⚠️', info: 'ℹ️' };
    var el = document.createElement('div');
    el.className = 'toast ' + (type === 'ok' ? 'ok' : type === 'err' ? 'err' : type === 'warn' ? 'warn' : '');
    el.innerHTML = '<div class="t-ic">' + (icons[type] || icons.info) + '</div><div><div class="t-title">' +
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
    esc: esc, toast: toast, modal: modal, confirm: confirm,
    platformTag: platformTag, platformName: platformName, statusTag: statusTag, levelTag: levelTag, metricTag: metricTag,
    lineChart: lineChart, barChart: barChart, donut: donut, sparkline: sparkline, colors: C
  };
})(window);
