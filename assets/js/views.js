/* ==========================================================
 * views.js —— 各业务页面（看板 / 商品 / Listing 生成 / 任务 / 告警 / 日志 / 凭证 / 设置 / 部署）
 * ========================================================== */
(function (global) {
  'use strict';

  var S = global.Store, U = global.UI;
  var esc = U.esc;
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  /* 公共：空态 */
  function empty(text, icon) {
    return '<div class="empty"><div class="e-ic">' + (icon ? U.icon(icon, 34) : U.icon('folder', 34)) + '</div><div>' + esc(text) + '</div></div>';
  }

  /* 异常类型标签 */
  function categoryTag(cat) {
    var c = S.categoryInfo(cat);
    return '<span class="tag ' + c.tag + '">' + U.icon(c.icon, 13) + ' ' + esc(c.name) + '</span>';
  }

  /* 公共：分页 */
  function pager(total, page, size, fnName) {
    var pages = Math.max(1, Math.ceil(total / size));
    if (pages <= 1) return '<span>共 ' + total + ' 条</span>';
    var btns = '';
    var start = Math.max(1, Math.min(page - 2, pages - 4));
    var end = Math.min(pages, start + 4);
    if (start > 1) btns += '<button data-p="1" onclick="' + fnName + '(1)">1</button><span>…</span>';
    for (var i = start; i <= end; i++) {
      btns += '<button data-p="' + i + '" class="' + (i === page ? 'active' : '') + '" onclick="' + fnName + '(' + i + ')">' + i + '</button>';
    }
    if (end < pages) btns += '<span>…</span><button onclick="' + fnName + '(' + pages + ')">' + pages + '</button>';
    return '<span>共 ' + total + ' 条</span><div class="pager">' +
      '<button onclick="' + fnName + '(' + (page - 1) + ')"' + (page <= 1 ? ' disabled' : '') + '>‹</button>' +
      btns +
      '<button onclick="' + fnName + '(' + (page + 1) + ')"' + (page >= pages ? ' disabled' : '') + '>›</button></div>';
  }

  /* ==========================================================
   * 1. 数据看板
   * ========================================================== */
  var dashboard = {
    title: '数据看板', desc: '全平台运行状态总览',
    render: function () {
      var d = S.get(), st = S.stats();
      var m = d.metrics.slice(-24);
      var orderPts = m.map(function (x) { return { label: S.fmtHour(x.t), value: x.orders }; });
      var stockPts = m.map(function (x) { return { label: S.fmtHour(x.t), value: x.stock }; });

      var amz = d.products.filter(function (p) { return p.platform === 'amazon'; }).length;
      var pdd = d.products.filter(function (p) { return p.platform === 'pdd'; }).length;

      var byLevel = ['critical', 'warning', 'info'].map(function (l) {
        return {
          label: l === 'critical' ? '严重' : l === 'warning' ? '警告' : '提示',
          value: d.alerts.filter(function (a) { return a.level === l; }).length,
          color: l === 'critical' ? U.colors.red : l === 'warning' ? U.colors.orange : U.colors.blue
        };
      });

      var trendCls = st.orderTrend >= 0 ? 'up' : 'down';
      var trendTxt = U.icon(st.orderTrend >= 0 ? 'trend-up' : 'trend-down', 12) + ' ' + Math.abs(st.orderTrend).toFixed(1) + '%';

      var html = '';

      /* KPI */
      html += '<div class="grid grid-4 mb16">' +
        kpi('未处理告警', st.unhandled, '共 ' + st.totalAlerts + ' 条告警记录', st.unhandled > 0 ? 'down' : '', 'bell', 'var(--danger-soft)', '#dc3a3a') +
        kpi('在架商品', st.online, '总计 ' + st.total + ' 个 SKU', '', 'box', 'var(--primary-soft)', '#fe2c55') +
        kpi('本小时订单', st.curOrders, trendTxt + ' 环比上小时', trendCls, 'cart', '#e6f7f0', '#15a46b') +
        kpi('运行中任务', st.running, '共 ' + st.totalTasks + ' 个监控任务', '', 'gear', '#f1ebff', '#7c4dff') +
        '</div>';

      /* 图表 */
      html += '<div class="grid grid-2 mb16">' +
        '<div class="card"><div class="card-head"><h3>近 24 小时订单量</h3><span class="desc">每小时采集一次</span></div>' +
          '<div class="chart-box">' + U.lineChart(orderPts, { color: U.colors.blue, height: 210, unit: ' 单' }) + '</div></div>' +
        '<div class="card"><div class="card-head"><h3>库存总量趋势</h3><span class="desc">双平台合计</span></div>' +
          '<div class="chart-box">' + U.lineChart(stockPts, { color: U.colors.green, height: 210, unit: ' 件' }) + '</div></div>' +
        '</div>';

      /* 分布 + 任务状态 */
      html += '<div class="grid grid-2-1 mb16">' +
        '<div class="card"><div class="card-head"><h3>任务运行状态</h3>' +
          '<div style="margin-left:auto"><button class="btn btn-sm" onclick="App.go(\'tasks\')">管理任务</button></div></div>' +
          '<div class="card-body" style="padding-top:6px">' + taskStatusList(d.tasks) + '</div></div>' +
        '<div class="card"><div class="card-head"><h3>告警等级分布</h3></div>' +
          '<div class="card-body">' + U.donut(byLevel, { centerLabel: '告警总数' }) + '</div></div>' +
        '</div>';

      /* 商品平台分布 + 最近告警 */
      html += '<div class="grid grid-1-2">' +
        '<div class="card"><div class="card-head"><h3>商品平台分布</h3></div><div class="card-body">' +
          U.donut([
            { label: '亚马逊', value: amz, color: U.colors.blue },
            { label: '拼多多', value: pdd, color: U.colors.red }
          ], { centerLabel: '商品总数' }) +
          '<div style="margin-top:16px">' +
            statLine('库存预警商品（<100 件）', st.lowStock + ' 个') +
            statLine('监控累计执行', st.totalRuns + ' 次') +
            statLine('最近执行时间', st.lastRunAt ? S.fmtTime(st.lastRunAt) : '尚未执行') +
          '</div></div></div>' +
        '<div class="card"><div class="card-head"><h3>最近告警</h3>' +
          '<div style="margin-left:auto"><button class="btn btn-sm" onclick="App.go(\'alerts\')">查看全部</button></div></div>' +
          '<div class="card-body" style="padding-top:6px">' + alertTimeline(d.alerts.slice(0, 6)) + '</div></div>' +
        '</div>';

      return html;
    }
  };

  function kpi(label, value, sub, subCls, icon, bg, color) {
    return '<div class="card kpi"><div class="k-ic" style="background:' + bg + ';color:' + color + '">' + icon + '</div>' +
      '<div class="k-label">' + esc(label) + '</div>' +
      '<div class="k-value">' + value + '</div>' +
      '<div class="k-sub ' + (subCls || '') + '">' + esc(sub) + '</div></div>';
  }
  function statLine(k, v) {
    return '<div class="stat-line"><span style="color:var(--text-2)">' + esc(k) + '</span><span class="v">' + esc(v) + '</span></div>';
  }
  function taskStatusList(tasks) {
    if (!tasks.length) return empty('暂无监控任务');
    return '<ul class="timeline">' + tasks.map(function (t) {
      var color = t.status === 'warning' ? U.colors.orange : t.status === 'success' ? U.colors.green : '#c3cad8';
      return '<li><span class="tl-dot" style="background:' + color + '"></span><div class="tl-body">' +
        '<div class="tl-title">' + esc(t.name) + U.platformTag(t.platform) + U.metricTag(t.metric) + '</div>' +
        '<div class="tl-meta">每 ' + t.interval + ' 分钟 · 阈值 ' + Math.round(t.threshold * 100) + '% · ' +
        (t.enabled ? (t.lastRunAt ? '上次运行 ' + S.fromNow(t.lastRunAt) : '等待运行') : '已停用') + '</div>' +
        '</div>' + (t.lastValue ? '<div style="text-align:right"><div style="font-weight:600">' + t.lastValue + '</div>' +
        '<div class="cell-sub">当前值</div></div>' : '') + '</li>';
    }).join('') + '</ul>';
  }
  function alertTimeline(list) {
    if (!list.length) return empty('暂无告警，一切正常');
    return '<ul class="timeline">' + list.map(function (a) {
      var color = a.level === 'critical' ? U.colors.red : a.level === 'warning' ? U.colors.orange : U.colors.blue;
      return '<li><span class="tl-dot" style="background:' + color + '"></span><div class="tl-body">' +
        '<div class="tl-title">' + esc(a.title) + U.platformTag(a.platform) + U.levelTag(a.level) + '</div>' +
        '<div class="tl-msg">' + esc(a.message) + '</div>' +
        '<div class="tl-meta">' + S.fmtTime(a.createdAt) + ' · ' + (a.status === 'handled' ? '已处理' : '未处理') + '</div>' +
        '</div></li>';
    }).join('') + '</ul>';
  }

  /* ==========================================================
   * 2. 商品管理
   * ========================================================== */
  var pf = { q: '', platform: '', status: '', page: 1, size: 8, selected: {} };

  var products = {
    title: '商品管理', desc: '双平台商品上架与信息维护',
    render: function () {
      var d = S.get();
      var list = d.products.filter(function (p) {
        if (pf.platform && p.platform !== pf.platform) return false;
        if (pf.status && p.status !== pf.status) return false;
        if (pf.q) {
          var q = pf.q.toLowerCase();
          if ((p.title + p.sku + p.category).toLowerCase().indexOf(q) < 0) return false;
        }
        return true;
      });
      var total = list.length;
      var pages = Math.max(1, Math.ceil(total / pf.size));
      if (pf.page > pages) pf.page = pages;
      var pageList = list.slice((pf.page - 1) * pf.size, pf.page * pf.size);
      var selCount = Object.keys(pf.selected).filter(function (k) { return pf.selected[k]; }).length;

      var html = '<div class="card">' +
        '<div class="filter-bar">' +
          '<input class="input grow" id="pQ" placeholder="搜索标题 / SKU / 类目" value="' + esc(pf.q) + '" />' +
          '<select class="select" id="pPlatform"><option value="">全部平台</option>' +
            '<option value="amazon"' + (pf.platform === 'amazon' ? ' selected' : '') + '>亚马逊</option>' +
            '<option value="pdd"' + (pf.platform === 'pdd' ? ' selected' : '') + '>拼多多</option></select>' +
          '<select class="select" id="pStatus"><option value="">全部状态</option>' +
            ['online:在售', 'pending:处理中', 'offline:已下架', 'failed:上架失败'].map(function (s) {
              var kv = s.split(':');
              return '<option value="' + kv[0] + '"' + (pf.status === kv[0] ? ' selected' : '') + '>' + kv[1] + '</option>';
            }).join('') + '</select>' +
          '<button class="btn" id="pReset">重置</button>' +
          '<div style="margin-left:auto;display:flex;gap:8px">' +
            (selCount ? '<button class="btn" id="pPublish">批量上架 (' + selCount + ')</button>' : '') +
            '<button class="btn btn-primary" id="pAdd">+ 新增商品</button>' +
          '</div>' +
        '</div>';

      if (!pageList.length) {
        html += empty('没有符合条件的商品');
      } else {
        html += '<div class="table-wrap"><table class="tbl"><thead><tr>' +
          '<th style="width:34px"><input type="checkbox" id="pAll" /></th>' +
          '<th>商品信息</th><th>平台</th><th>类目</th><th>价格</th><th>库存</th><th>状态</th><th>同步时间</th><th style="width:195px">操作</th>' +
          '</tr></thead><tbody>' +
          pageList.map(function (p) {
            var low = p.stock < 100;
            return '<tr>' +
              '<td data-label="选择"><input type="checkbox" class="pSel" data-id="' + p.id + '"' + (pf.selected[p.id] ? ' checked' : '') + ' /></td>' +
              '<td data-label="商品"><div class="cell-main">' + esc(p.title) + '</div><div class="cell-sub">' + esc(p.sku) +
                (p.listing ? ' <span class="tag tag-purple">' + U.icon('robot', 13) + ' 含 AI Listing</span>' : '') + '</div></td>' +
              '<td data-label="平台">' + U.platformTag(p.platform) + '</td>' +
              '<td data-label="类目">' + esc(p.category) + '</td>' +
              '<td data-label="价格">' + (p.currency === 'USD' ? '$' : '¥') + p.price + '</td>' +
              '<td data-label="库存"><span style="color:' + (low ? 'var(--danger)' : 'inherit') + ';font-weight:' + (low ? 600 : 400) + '">' + p.stock + '</span>' +
                (low ? ' <span class="tag tag-red">偏低</span>' : '') + '</td>' +
              '<td data-label="状态">' + U.statusTag(p.status) + '</td>' +
              '<td data-label="同步时间" style="white-space:nowrap">' + S.fromNow(p.lastSync) + '</td>' +
              '<td data-label="操作">' +
                '<button class="btn-link" data-edit="' + p.id + '">编辑</button>' +
                '<button class="btn-link" data-pub="' + p.id + '">上架</button>' +
                '<button class="btn-link" data-gen="' + p.id + '">' + U.icon('robot', 15) + ' 生成</button>' +
                '<button class="btn-link danger" data-del="' + p.id + '">删除</button>' +
              '</td></tr>';
          }).join('') +
          '</tbody></table></div>' +
          '<div class="table-foot">' + pager(total, pf.page, pf.size, 'Views.products.goPage') + '</div>';
      }
      html += '</div>';
      return html;
    },
    mount: function () {
      var self = this;
      var q = $('#pQ');
      if (q) {
        var timer;
        q.oninput = function () {
          clearTimeout(timer);
          timer = setTimeout(function () { pf.q = q.value; pf.page = 1; App.refresh(); var nq = $('#pQ'); if (nq) { nq.focus(); nq.setSelectionRange(nq.value.length, nq.value.length); } }, 300);
        };
      }
      var pl = $('#pPlatform'); if (pl) pl.onchange = function () { pf.platform = pl.value; pf.page = 1; App.refresh(); };
      var ps = $('#pStatus'); if (ps) ps.onchange = function () { pf.status = ps.value; pf.page = 1; App.refresh(); };
      var pr = $('#pReset'); if (pr) pr.onclick = function () { pf.q = ''; pf.platform = ''; pf.status = ''; pf.page = 1; App.refresh(); };
      var pa = $('#pAdd'); if (pa) pa.onclick = function () { self.form(null); };
      var pp = $('#pPublish'); if (pp) pp.onclick = function () {
        var ids = Object.keys(pf.selected).filter(function (k) { return pf.selected[k]; });
        var r = S.publishProducts(ids);
        pf.selected = {};
        U.toast(r.ok ? 'ok' : 'warn', '批量上架完成', '成功 ' + r.ok + ' 个' + (r.fail ? '，失败 ' + r.fail + ' 个' : ''));
        App.refresh();
      };
      var pall = $('#pAll');
      if (pall) pall.onchange = function () {
        $$('.pSel').forEach(function (c) { c.checked = pall.checked; pf.selected[c.dataset.id] = pall.checked; });
        App.refresh();
      };
      $$('.pSel').forEach(function (c) {
        c.onchange = function () { pf.selected[c.dataset.id] = c.checked; App.refresh(); };
      });
      $$('[data-edit]').forEach(function (b) { b.onclick = function () { self.form(b.dataset.edit); }; });
      $$('[data-gen]').forEach(function (b) {
        b.onclick = function () {
          var p = S.get().products.filter(function (x) { return x.id === b.dataset.gen; })[0];
          if (!p) return;
          lf.platform = p.platform;
          lf.category = CATS.indexOf(p.category) >= 0 ? p.category : '3C数码';
          lf.name = p.title;
          lf.brand = '';
          lf.features = '';
          lf.specs = '';
          lf.keywords = '';
          lf.audience = '';
          lf.result = null;
          lf.applyId = p.id;
          App.go('listing');
          U.toast('info', '已带入「' + p.sku + '」', '补充卖点与规格参数后点击生成');
        };
      });
      $$('[data-pub]').forEach(function (b) {
        b.onclick = function () {
          var r = S.publishProducts([b.dataset.pub]);
          U.toast(r.ok ? 'ok' : 'err', r.ok ? '上架成功' : '上架失败', r.ok ? '商品已提交至平台' : '请检查类目必填属性');
          App.refresh();
        };
      });
      $$('[data-del]').forEach(function (b) {
        b.onclick = function () {
          var p = S.get().products.filter(function (x) { return x.id === b.dataset.del; })[0];
          U.confirm('删除商品', '确定删除「' + (p ? p.title : '') + '」？该操作不可恢复。', function () {
            S.removeProduct(b.dataset.del); U.toast('ok', '已删除'); App.refresh();
          }, '删除');
        };
      });
    },
    goPage: function (p) { pf.page = p; App.refresh(); },
    form: function (id) {
      var d = S.get();
      var p = id ? d.products.filter(function (x) { return x.id === id; })[0] : {
        platform: 'amazon', sku: '', title: '', category: '3C数码', price: 19.9, currency: 'USD', stock: 100, status: 'pending', autoSync: true
      };
      var isEdit = !!id;
      var body = '' +
        '<div class="field-row">' +
          '<div class="field"><label>平台 <span class="req">*</span></label><select class="select" id="fPlatform">' +
            '<option value="amazon"' + (p.platform === 'amazon' ? ' selected' : '') + '>亚马逊 SP-API</option>' +
            '<option value="pdd"' + (p.platform === 'pdd' ? ' selected' : '') + '>拼多多开放平台</option></select></div>' +
          '<div class="field"><label>SKU / 商品编码 <span class="req">*</span></label>' +
            '<input class="input" id="fSku" value="' + esc(p.sku) + '" placeholder="如 AMZ-1001" /></div>' +
        '</div>' +
        '<div class="field"><label>商品标题 <span class="req">*</span></label>' +
          '<input class="input" id="fTitle" value="' + esc(p.title) + '" placeholder="商品名称" /></div>' +
        '<div class="field-row">' +
          '<div class="field"><label>类目</label><select class="select" id="fCategory">' +
            ['3C数码', '家居厨房', '户外运动', '个护健康', '母婴玩具', '服饰配饰'].map(function (c) {
              return '<option' + (p.category === c ? ' selected' : '') + '>' + c + '</option>';
            }).join('') + '</select></div>' +
          '<div class="field"><label>状态</label><select class="select" id="fStatus">' +
            [['online', '在售'], ['pending', '待上架'], ['offline', '已下架'], ['failed', '上架失败']].map(function (s) {
              return '<option value="' + s[0] + '"' + (p.status === s[0] ? ' selected' : '') + '>' + s[1] + '</option>';
            }).join('') + '</select></div>' +
        '</div>' +
        '<div class="field-row">' +
          '<div class="field"><label>价格 <span class="req">*</span></label>' +
            '<div style="display:flex;gap:8px"><select class="select" id="fCurrency" style="width:88px">' +
              '<option value="USD"' + (p.currency === 'USD' ? ' selected' : '') + '>USD</option>' +
              '<option value="CNY"' + (p.currency === 'CNY' ? ' selected' : '') + '>CNY</option></select>' +
              '<input class="input" id="fPrice" type="number" step="0.01" value="' + p.price + '" /></div></div>' +
          '<div class="field"><label>库存 <span class="req">*</span></label>' +
            '<input class="input" id="fStock" type="number" value="' + p.stock + '" /></div>' +
        '</div>' +
        '<label class="check-line"><input type="checkbox" id="fAuto"' + (p.autoSync ? ' checked' : '') + ' /> 纳入每小时自动监控（库存 / 订单环比检测）</label>' +
        '<div class="hint">保存后可在列表点击「上架」提交到对应平台；演示模式下上架结果由模拟引擎生成。</div>';

      U.modal({
        title: isEdit ? '编辑商品' : '新增商品',
        body: body, width: 640,
        okText: isEdit ? '保存修改' : '创建',
        onOk: function (w) {
          var sku = $('#fSku', w).value.trim(), title = $('#fTitle', w).value.trim();
          var price = parseFloat($('#fPrice', w).value), stock = parseInt($('#fStock', w).value, 10);
          if (!sku || !title) { U.toast('err', '请填写完整', 'SKU 和商品标题为必填项'); return false; }
          if (isNaN(price) || price <= 0) { U.toast('err', '价格不合法', '请填写大于 0 的价格'); return false; }
          if (isNaN(stock) || stock < 0) { U.toast('err', '库存不合法', '库存不能为负数'); return false; }
          var data = {
            id: id || null, platform: $('#fPlatform', w).value, sku: sku, title: title,
            category: $('#fCategory', w).value, status: $('#fStatus', w).value,
            currency: $('#fCurrency', w).value, price: +price.toFixed(2), stock: stock,
            autoSync: $('#fAuto', w).checked
          };
          S.saveProduct(data);
          U.toast('ok', isEdit ? '已保存' : '创建成功', data.sku);
          App.refresh();
        }
      });
    }
  };

  /* ==========================================================
   * 3. AI 生成 Listing（上架环节）
   * 这是整套方案里大模型性价比最高的一处：中文商品信息 → 可直接上架的 Listing。
   * 网页端只做「模拟预览」，真实模型调用在 Python 后端（避免密钥暴露 + 跨域）。
   * ========================================================== */
  var lf = {
    platform: 'amazon', category: '3C数码', name: '', brand: '',
    features: '', specs: '', keywords: '', audience: '', result: null, applyId: ''
  };

  var CATS = ['3C数码', '家居厨房', '户外运动', '个护健康', '母婴玩具', '服饰配饰'];

  function parseSpecs(text) {
    var out = {};
    (text || '').split(/[\n;；]+/).forEach(function (line) {
      var m = line.split(/\s*[:：=]\s*/);
      if (m.length >= 2 && m[0].trim()) out[m[0].trim()] = m.slice(1).join(':').trim();
    });
    return out;
  }

  function issueClass(lv) { return lv === 'error' ? 'tag tag-red' : 'tag tag-yellow'; }

  var listing = {
    title: 'AI 生成 Listing', desc: '中文商品信息 → 可直接上架的英文 / 中文 Listing',
    render: function () {
      var d = S.get();
      var ls = d.settings.listing || {};
      var llmOn = !!(d.settings.llm && d.settings.llm.simulate);
      var lim = S.LISTING_LIMITS[lf.platform] || S.LISTING_LIMITS.amazon;
      var schema = S.schemaForListing(lf.category);
      var r = lf.result;

      var html = '<div class="card mb16"><div class="card-head"><h3>商品信息（中文）</h3>' +
        '<span class="desc">给得越全，生成质量越高</span></div><div class="card-body">' +
        '<div class="field-row">' +
          '<div class="field"><label>目标平台</label><select class="select" id="lgPlatform">' +
            '<option value="amazon"' + (lf.platform === 'amazon' ? ' selected' : '') + '>亚马逊（生成英文）</option>' +
            '<option value="pdd"' + (lf.platform === 'pdd' ? ' selected' : '') + '>拼多多（生成中文）</option>' +
          '</select></div>' +
          '<div class="field"><label>商品类目<span class="req">*</span></label><select class="select" id="lgCategory">' +
            CATS.map(function (c) { return '<option' + (lf.category === c ? ' selected' : '') + '>' + c + '</option>'; }).join('') +
          '</select></div>' +
        '</div>' +
        '<div class="field-row">' +
          '<div class="field"><label>商品中文名<span class="req">*</span></label>' +
            '<input class="input" id="lgName" value="' + esc(lf.name) + '" placeholder="如：无线蓝牙耳机 主动降噪 超长续航" /></div>' +
          '<div class="field"><label>品牌</label>' +
            '<input class="input" id="lgBrand" value="' + esc(lf.brand || (ls.brand || '')) + '" placeholder="留空则用系统默认品牌" /></div>' +
        '</div>' +
        '<div class="field"><label>卖点（分号或换行分隔）</label>' +
          '<textarea class="input" id="lgFeat" rows="3" placeholder="主动降噪 35dB；单次续航 8 小时；IPX5 防水；Type-C 快充">' + esc(lf.features) + '</textarea></div>' +
        '<div class="field"><label>规格参数（每行一条，格式「属性名: 值」）</label>' +
          '<textarea class="input" id="lgSpec" rows="3" placeholder="color: Black&#10;battery_capacity: 400mAh&#10;power_source: Battery Powered">' + esc(lf.specs) + '</textarea></div>' +
        '<div class="field-row">' +
          '<div class="field"><label>参考关键词</label>' +
            '<input class="input" id="lgKwIn" value="' + esc(lf.keywords) + '" placeholder="wireless earbuds, noise cancelling" /></div>' +
          '<div class="field"><label>目标人群 / 场景</label>' +
            '<input class="input" id="lgAud" value="' + esc(lf.audience) + '" placeholder="通勤 / 出差人群" /></div>' +
        '</div>' +
        '<div class="row-between mt16">' +
          '<div class="hint" style="margin:0">当前来源：<b>' + (llmOn ? U.icon('robot', 13) + ' 模拟大模型' : U.icon('doc', 13) + ' 规则草稿') + '</b>' +
            '（在系统设置 → 大模型建议里切换）。真实模型调用由 Python 后端执行。</div>' +
          '<div style="display:flex;gap:8px;flex-shrink:0">' +
            '<button class="btn" id="lgDemo">填入示例</button>' +
            '<button class="btn" id="lgClear">清空</button>' +
            '<button class="btn btn-primary" id="lgGen">' + U.icon('robot', 15) + ' 生成 Listing</button>' +
          '</div>' +
        '</div></div></div>';

      if (!r) {
        html += '<div class="card">' + empty('填入左侧商品信息后点击「生成 Listing」', 'robot') + '</div>';
        return html;
      }

      var L = r.listing;
      var errs = r.issues.filter(function (i) { return i.level === 'error'; });
      var warns = r.issues.filter(function (i) { return i.level === 'warn'; });

      html += '<div class="card"><div class="card-head"><h3>生成结果</h3>' +
        '<span class="desc">' + (r.source === 'llm' ? U.icon('robot', 13) + ' 模拟大模型' : U.icon('doc', 13) + ' 规则草稿') + ' · ' +
        esc(schema.productType) + ' · ' + esc(schema.node) + '</span></div><div class="card-body">' +

        '<div class="field"><label>校验结果 ' +
          (errs.length ? '<span class="tag tag-red">' + U.icon('cross', 13) + ' ' + errs.length + ' 项待修正</span> ' : '<span class="tag tag-green">' + U.icon('check', 13) + ' 无阻断问题</span> ') +
          (warns.length ? '<span class="tag tag-yellow">' + U.icon('warn', 13) + ' ' + warns.length + ' 项提示</span>' : '') +
        '</label>' +
        (r.issues.length
          ? '<div style="border:1px solid var(--border);border-radius:8px;padding:10px 12px;max-height:180px;overflow:auto">' +
            r.issues.map(function (i) {
              return '<div style="display:flex;gap:8px;padding:4px 0;border-bottom:1px dashed var(--border-2)">' +
                '<span class="' + issueClass(i.level) + '" style="flex-shrink:0">' + U.icon(i.level === 'error' ? 'cross' : 'warn', 13) + ' ' + esc(i.field) + '</span>' +
                '<span style="font-size:13px">' + esc(i.msg) + '</span></div>';
            }).join('') + '</div>'
          : '<div class="hint">全部检查通过，可以直接转 payload 上架。</div>') +
        '</div>' +

        '<div class="field"><label>标题 <span class="cell-sub">' + L.title.length + ' / ' + lim.titleMax + ' 字符' +
          (lim.titleSoft < lim.titleMax ? '（建议 ≤' + lim.titleSoft + '）' : '') + '</span></label>' +
          '<textarea class="input" id="lgTitle" rows="2">' + esc(L.title) + '</textarea></div>' +

        '<div class="field"><label>五点描述 <span class="cell-sub">每条 ≤' + lim.bulletMax + ' 字符</span></label>' +
          L.bullets.map(function (b, i) {
            return '<textarea class="input lgBullet" rows="1" style="margin-bottom:6px">' + esc(b) + '</textarea>' +
              '<div class="cell-sub" style="margin:-4px 0 8px;text-align:right">' + b.length + ' 字符</div>';
          }).join('') + '</div>' +

        '<div class="field"><label>产品描述</label>' +
          '<textarea class="input" id="lgDesc" rows="3">' + esc(L.description) + '</textarea></div>' +

        '<div class="field"><label>搜索关键词 <span class="cell-sub">' +
          S.byteLen(L.keywords.join(' ')) + ' / ' + lim.kwBytes + ' 字节</span></label>' +
          '<input class="input" id="lgKw" value="' + esc(L.keywords.join(' ')) + '" /></div>' +

        '<div class="field"><label>类目属性</label>' +
          '<div style="border:1px solid var(--border);border-radius:8px;overflow:hidden">' +
          Object.keys(L.attributes).map(function (k) {
            var req = schema.required.indexOf(k) >= 0;
            return '<div style="display:flex;align-items:center;gap:10px;padding:8px 12px;border-bottom:1px solid var(--border-2)">' +
              '<span style="width:200px;flex-shrink:0;font-size:13px">' + esc(k) +
                (req ? ' <span class="tag tag-red">必填</span>' : ' <span class="tag tag-gray">推荐</span>') + '</span>' +
              '<input class="input lgAttr" data-k="' + esc(k) + '" value="' + esc(L.attributes[k]) + '" /></div>';
          }).join('') + '</div>' +
          '<div class="hint">必填属性由「' + esc(lf.category) + '」的类目 schema 决定。真实环境的权威来源是 SP-API 的 ' +
            'getDefinitionsProductType（Python 端 listing_gen.fetch_product_type_definition 已封装），本地这张表只是兜底。</div>' +
        '</div>' +

        '<div class="row-between mt16" style="flex-wrap:wrap;gap:10px">' +
          '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">' +
            '<select class="select" id="lgApply" style="width:220px"><option value="">应用到商品…</option>' +
              d.products.filter(function (p) { return p.platform === r.platform; }).map(function (p) {
                return '<option value="' + p.id + '"' + (lf.applyId === p.id ? ' selected' : '') + '>' + esc(p.sku) + ' · ' + esc(p.title.slice(0, 18)) + '</option>';
              }).join('') +
            '</select>' +
            '<button class="btn" id="lgApplyBtn">保存为该商品 Listing</button>' +
          '</div>' +
          '<div style="display:flex;gap:8px;flex-wrap:wrap">' +
            '<button class="btn" id="lgRecheck">重新校验</button>' +
            '<button class="btn" id="lgCopy">复制文案</button>' +
            '<button class="btn btn-primary" id="lgExport">导出 SP-API Payload</button>' +
          '</div>' +
        '</div>' +
        '</div></div>';
      return html;
    },

    readForm: function () {
      var r = lf.result;
      if (!r) return null;
      var t = $('#lgTitle'); if (!t) return r;
      r.listing.title = t.value;
      r.listing.bullets = $$('.lgBullet').map(function (i) { return i.value; });
      r.listing.description = $('#lgDesc').value;
      r.listing.keywords = $('#lgKw').value.split(/[,，;；\s]+/).filter(function (x) { return x; });
      $$('.lgAttr').forEach(function (i) { r.listing.attributes[i.dataset.k] = i.value; });
      r.issues = S.validateListing(r.listing, r.platform, S.schemaForListing(lf.category), r.defaultAttrs);
      r.hasError = r.issues.filter(function (i) { return i.level === 'error'; }).length > 0;
      return r;
    },

    mount: function () {
      var self = this;
      var pl = $('#lgPlatform'); if (pl) pl.onchange = function () { lf.platform = pl.value; App.refresh(); };
      var ct = $('#lgCategory'); if (ct) ct.onchange = function () { lf.category = ct.value; App.refresh(); };

      var gd = $('#lgDemo');
      if (gd) gd.onclick = function () {
        lf.category = '3C数码';
        lf.name = '无线蓝牙耳机 主动降噪 超长续航 防水';
        lf.brand = 'SoundCore';
        lf.features = '主动降噪，深度 35dB；单次续航 8 小时，配充电盒共 32 小时；IPX5 防水；Type-C 快充';
        lf.specs = 'color: Black\nbattery_capacity: 400mAh\nconnectivity_technology: Bluetooth 5.3\npower_source: Battery Powered';
        lf.keywords = 'wireless earbuds, noise cancelling earbuds, bluetooth headphones';
        lf.audience = '通勤 / 出差人群';
        App.refresh();
      };
      var gc = $('#lgClear');
      if (gc) gc.onclick = function () {
        lf.name = ''; lf.brand = ''; lf.features = ''; lf.specs = ''; lf.keywords = ''; lf.audience = '';
        lf.result = null; lf.applyId = ''; App.refresh();
      };

      var gg = $('#lgGen');
      if (gg) gg.onclick = function () {
        lf.name = ($('#lgName').value || '').trim();
        lf.brand = ($('#lgBrand').value || '').trim();
        lf.category = $('#lgCategory').value || lf.category;
        lf.platform = $('#lgPlatform').value || lf.platform;
        lf.features = $('#lgFeat').value;
        lf.specs = $('#lgSpec').value;
        lf.keywords = $('#lgKwIn').value;
        lf.audience = ($('#lgAud').value || '').trim();

        if (!lf.name) { U.toast('err', '请填写商品中文名', '商品名是生成的唯一必需输入'); return; }

        var d = S.get();
        var useLlm = !!(d.settings.llm && d.settings.llm.simulate);
        var btn = gg; btn.disabled = true; btn.textContent = '生成中...';
        setTimeout(function () {
          lf.result = S.genListing({
            name: lf.name, brand: lf.brand, category: lf.category,
            features: lf.features, specs: parseSpecs(lf.specs),
            keywords: lf.keywords, audience: lf.audience
          }, lf.platform, useLlm);
          S.addLog('info', 'listing', '生成 Listing：' + lf.name.slice(0, 20) +
            '（' + (useLlm ? '模拟大模型' : '规则草稿') + '，' + lf.result.issues.length + ' 项校验提示）');
          var e = lf.result.issues.filter(function (i) { return i.level === 'error'; }).length;
          U.toast(e ? 'warn' : 'ok', '生成完成', e ? '有 ' + e + ' 项需要修正' : '校验通过，可直接导出 payload');
          App.refresh();
        }, 620);
      };

      var rc = $('#lgRecheck');
      if (rc) rc.onclick = function () {
        self.readForm();
        var e = lf.result.issues.filter(function (i) { return i.level === 'error'; }).length;
        U.toast(e ? 'warn' : 'ok', '校验完成', e ? '还有 ' + e + ' 项待修正' : '全部通过');
        App.refresh();
      };

      var cp = $('#lgCopy');
      if (cp) cp.onclick = function () {
        var r = self.readForm(), L = r.listing;
        var text = ['【标题】' + L.title, '', '【五点描述】']
          .concat(L.bullets.map(function (b, i) { return (i + 1) + '. ' + b; }))
          .concat(['', '【产品描述】' + L.description, '', '【关键词】' + L.keywords.join(' '), '', '【类目属性】'])
          .concat(Object.keys(L.attributes).map(function (k) { return k + ': ' + L.attributes[k]; }))
          .join('\n');
        var ta = document.createElement('textarea');
        ta.value = text; ta.style.position = 'fixed'; ta.style.left = '-9999px';
        document.body.appendChild(ta); ta.select();
        try { document.execCommand('copy'); U.toast('ok', '已复制', '文案已复制到剪贴板'); }
        catch (err) { U.toast('err', '复制失败', '请手动选中复制'); }
        ta.remove();
      };

      var ex = $('#lgExport');
      if (ex) ex.onclick = function () {
        var r = self.readForm();
        if (r.hasError && (S.get().settings.listing || {}).skipOnError !== false) {
          U.confirm('校验未通过', '存在 ' + r.issues.filter(function (i) { return i.level === 'error'; }).length +
            ' 项阻断级问题，建议修正后再导出。仍要导出吗？', function () { self.doExport(r); }, '仍然导出');
          return;
        }
        self.doExport(r);
      };

      var ap = $('#lgApply');
      if (ap) ap.onchange = function () { lf.applyId = ap.value; };
      var ab = $('#lgApplyBtn');
      if (ab) ab.onclick = function () {
        var id = lf.applyId || (ap && ap.value);
        if (!id) { U.toast('warn', '请先选择商品', '在左侧下拉里选一个要应用的目标商品'); return; }
        var r = self.readForm();
        var p = S.saveListing(id, r);
        if (!p) { U.toast('err', '保存失败', '未找到该商品'); return; }
        U.toast('ok', '已应用到 ' + p.sku, '可在商品管理页查看该商品的 Listing');
        App.refresh();
      };
    },

    doExport: function (r) {
      var schema = S.schemaForListing(lf.category);
      var payload = r.platform === 'amazon'
        ? S.listingToSpapi(r.listing, r.sku || 'NEW-SKU', schema.productType)
        : { platform: 'pdd', payload: { goods_name: r.listing.title, goods_desc: r.listing.description, keywords: r.listing.keywords.join(','), attributes: r.listing.attributes } };
      S.download('listing_payload.json', JSON.stringify(payload, null, 2));
      S.addLog('info', 'listing', '已导出 Listing payload（' + schema.productType + '）');
      U.toast('ok', '已导出', 'listing_payload.json 可直接喂给上架接口');
    }
  };

  /* ==========================================================
   * 4. 监控任务
   * ========================================================== */
  var tasks = {
    title: '监控任务', desc: '每小时自动轮询与异常检测规则',
    render: function () {
      var d = S.get();
      var html = '<div class="card">' +
        '<div class="filter-bar">' +
          '<div style="color:var(--text-2);font-size:13px">共 <b>' + d.tasks.length + '</b> 个任务，启用 <b>' +
            d.tasks.filter(function (t) { return t.enabled; }).length + '</b> 个</div>' +
          '<div style="margin-left:auto;display:flex;gap:8px;flex-wrap:wrap">' +
            '<button class="btn" id="tInject">' + U.icon('bolt', 15) + ' 注入一次异常</button>' +
            '<button class="btn" id="tRunAll">' + U.icon('play', 15) + ' 执行全部</button>' +
            '<button class="btn btn-primary" id="tAdd">+ 新建任务</button>' +
          '</div>' +
        '</div>';

      if (!d.tasks.length) {
        html += empty('暂无监控任务，点击右上角新建');
      } else {
        html += '<div class="table-wrap"><table class="tbl"><thead><tr>' +
          '<th>任务名称</th><th>平台</th><th>监控指标</th><th>频率</th><th>阈值 / 方向</th><th>当前值</th><th>上次运行</th><th>状态</th><th>操作</th>' +
          '</tr></thead><tbody>' +
          d.tasks.map(function (t) {
            return '<tr>' +
              '<td data-label="任务"><div class="cell-main">' + esc(t.name) + '</div><div class="cell-sub">已执行 ' + t.runCount + ' 次</div></td>' +
              '<td data-label="平台">' + U.platformTag(t.platform) + '</td>' +
              '<td data-label="指标">' + U.metricTag(t.metric) + ' ' + S.metricLabel(t.metric) + '</td>' +
              '<td data-label="频率">每 ' + t.interval + ' 分钟</td>' +
              '<td data-label="阈值">' + Math.round(t.threshold * 100) + '% · ' + (t.direction === 'drop' ? '仅下降告警' : '涨跌均告警') + '</td>' +
              '<td data-label="当前值"><b>' + (t.lastValue || '—') + '</b></td>' +
              '<td data-label="上次运行" style="white-space:nowrap">' + (t.lastRunAt ? S.fromNow(t.lastRunAt) : '—') + '</td>' +
              '<td data-label="状态">' + U.statusTag(t.status) + '</td>' +
              '<td data-label="操作">' +
                '<label class="switch" style="vertical-align:middle;margin-right:8px"><input type="checkbox" data-tog="' + t.id + '"' + (t.enabled ? ' checked' : '') + ' /><span class="slider"></span></label>' +
                '<button class="btn-link" data-tedit="' + t.id + '">编辑</button>' +
                '<button class="btn-link danger" data-tdel="' + t.id + '">删除</button>' +
              '</td></tr>';
          }).join('') + '</tbody></table></div>';
      }
      html += '</div>';

      html += '<div class="card mt16"><div class="card-head"><h3>异常检测原理</h3></div><div class="card-body doc">' +
        '<p>每次执行时，引擎拉取各平台最新指标，与上一次采集值做<b>环比</b>比较：</p>' +
        '<div class="code-block"><span class="c"># 与 Python 版 anomaly_detector.check_metric 完全一致</span>\nchange_ratio = (current_value - previous) / previous\n\n<span class="k">if</span> direction == <span class="g">"drop"</span> <span class="k">and</span> change_ratio &lt;= -threshold:   <span class="c"># 库存 / 订单量</span>\n    触发告警\n<span class="k">elif</span> direction == <span class="g">"both"</span> <span class="k">and</span> abs(change_ratio) &gt;= threshold: <span class="c"># 价格</span>\n    触发告警</div>' +
        '<p style="margin-top:12px">降幅达到阈值的 <b>2 倍</b>以上判定为「严重」，否则为「警告」。数据点会写入趋势序列，用于看板绘图。</p>' +
        '</div></div>';
      return html;
    },
    mount: function () {
      var self = this;
      var add = $('#tAdd'); if (add) add.onclick = function () { self.form(null); };
      var runAll = $('#tRunAll');
      if (runAll) runAll.onclick = function () { App.runMonitor(); };
      var inj = $('#tInject');
      if (inj) inj.onclick = function () {
        App.runMonitor({ inject: true });
        U.toast('warn', '已注入库存异常', '模拟某 SKU 库存暴跌，用于验证告警链路');
      };
      $$('[data-tog]').forEach(function (c) {
        c.onchange = function () {
          var t = S.toggleTask(c.dataset.tog, c.checked);
          U.toast('ok', t.enabled ? '任务已启用' : '任务已停用', t.name);
          App.refresh();
        };
      });
      $$('[data-tedit]').forEach(function (b) { b.onclick = function () { self.form(b.dataset.tedit); }; });
      $$('[data-tdel]').forEach(function (b) {
        b.onclick = function () {
          U.confirm('删除任务', '确定删除该监控任务？', function () { S.removeTask(b.dataset.tdel); U.toast('ok', '已删除'); App.refresh(); }, '删除');
        };
      });
    },
    form: function (id) {
      var d = S.get();
      var t = id ? d.tasks.filter(function (x) { return x.id === id; })[0] : {
        name: '', platform: 'amazon', metric: 'inventory', interval: 60, threshold: 0.3, direction: 'drop', enabled: true
      };
      var body = '' +
        '<div class="field"><label>任务名称 <span class="req">*</span></label>' +
          '<input class="input" id="tName" value="' + esc(t.name) + '" placeholder="如：亚马逊 FBA 库存监控" /></div>' +
        '<div class="field-row">' +
          '<div class="field"><label>平台</label><select class="select" id="tPlatform">' +
            '<option value="amazon"' + (t.platform === 'amazon' ? ' selected' : '') + '>亚马逊</option>' +
            '<option value="pdd"' + (t.platform === 'pdd' ? ' selected' : '') + '>拼多多</option>' +
            '<option value="all"' + (t.platform === 'all' ? ' selected' : '') + '>全平台</option></select></div>' +
          '<div class="field"><label>监控指标</label><select class="select" id="tMetric">' +
            [['inventory', '库存总量'], ['orders', '小时订单量'], ['price', '平均价格'], ['gmv', '小时 GMV']].map(function (m) {
              return '<option value="' + m[0] + '"' + (t.metric === m[0] ? ' selected' : '') + '>' + m[1] + '</option>';
            }).join('') + '</select></div>' +
        '</div>' +
        '<div class="field-row">' +
          '<div class="field"><label>执行频率（分钟）</label>' +
            '<input class="input" id="tInterval" type="number" min="5" value="' + t.interval + '" /></div>' +
          '<div class="field"><label>告警方向</label><select class="select" id="tDirection">' +
            '<option value="drop"' + (t.direction === 'drop' ? ' selected' : '') + '>仅下降告警（库存/订单）</option>' +
            '<option value="both"' + (t.direction === 'both' ? ' selected' : '') + '>涨跌均告警（价格）</option></select></div>' +
        '</div>' +
        '<div class="field"><label>异常阈值：<b id="tThresholdTxt">' + Math.round(t.threshold * 100) + '%</b></label>' +
          '<input type="range" id="tThreshold" min="5" max="90" step="5" value="' + Math.round(t.threshold * 100) + '" style="width:100%" />' +
          '<div class="hint">环比变化超过该比例即触发告警。阈值越低越敏感，误报也会增多，建议按实际业务波动调整。</div></div>' +
        '<label class="check-line"><input type="checkbox" id="tEnabled"' + (t.enabled ? ' checked' : '') + ' /> 立即启用该任务</label>';

      U.modal({
        title: id ? '编辑监控任务' : '新建监控任务',
        body: body, width: 620,
        onOk: function (w) {
          var name = $('#tName', w).value.trim();
          if (!name) { U.toast('err', '请填写任务名称'); return false; }
          var interval = parseInt($('#tInterval', w).value, 10);
          if (isNaN(interval) || interval < 5) { U.toast('err', '频率不合法', '执行频率不能小于 5 分钟'); return false; }
          S.saveTask({
            id: id || null, name: name, platform: $('#tPlatform', w).value, metric: $('#tMetric', w).value,
            interval: interval, threshold: parseInt($('#tThreshold', w).value, 10) / 100,
            direction: $('#tDirection', w).value, enabled: $('#tEnabled', w).checked
          });
          U.toast('ok', id ? '任务已更新' : '任务已创建', name);
          App.refresh();
        },
        onOpen: function (w) {
          var r = $('#tThreshold', w), txt = $('#tThresholdTxt', w);
          r.oninput = function () { txt.textContent = r.value + '%'; };
        }
      });
    }
  };

  /* ==========================================================
   * 5. 告警中心
   * ========================================================== */
  var af = { level: '', platform: '', status: '', category: '', page: 1, size: 8 };

  var alerts = {
    title: '告警中心', desc: '异常分类与优化建议',
    render: function () {
      var d = S.get();
      var list = d.alerts.filter(function (a) {
        if (af.level && a.level !== af.level) return false;
        if (af.platform && a.platform !== af.platform) return false;
        if (af.status && a.status !== af.status) return false;
        if (af.category && a.category !== af.category) return false;
        return true;
      });
      var total = list.length;
      var pages = Math.max(1, Math.ceil(total / af.size));
      if (af.page > pages) af.page = pages;
      var pageList = list.slice((af.page - 1) * af.size, af.page * af.size);
      var unhandled = d.alerts.filter(function (a) { return a.status === 'unhandled'; }).length;

      var html = '<div class="card"><div class="filter-bar">' +
        '<select class="select" id="aLevel"><option value="">全部等级</option>' +
          ['critical:严重', 'warning:警告', 'info:提示'].map(function (s) {
            var kv = s.split(':'); return '<option value="' + kv[0] + '"' + (af.level === kv[0] ? ' selected' : '') + '>' + kv[1] + '</option>';
          }).join('') + '</select>' +
        '<select class="select" id="aPlatform"><option value="">全部平台</option>' +
          ['amazon:亚马逊', 'pdd:拼多多', 'all:全平台'].map(function (s) {
            var kv = s.split(':'); return '<option value="' + kv[0] + '"' + (af.platform === kv[0] ? ' selected' : '') + '>' + kv[1] + '</option>';
          }).join('') + '</select>' +
        '<select class="select" id="aStatus"><option value="">全部状态</option>' +
          '<option value="unhandled"' + (af.status === 'unhandled' ? ' selected' : '') + '>未处理</option>' +
          '<option value="handled"' + (af.status === 'handled' ? ' selected' : '') + '>已处理</option></select>' +
        '<select class="select" id="aCategory"><option value="">全部类型</option>' +
          Object.keys(S.CATEGORIES).map(function (k) {
              return '<option value="' + k + '"' + (af.category === k ? ' selected' : '') + '>' +
              S.CATEGORIES[k].name + '</option>';
          }).join('') + '</select>' +
        '<div style="margin-left:auto;display:flex;gap:8px;align-items:center">' +
          '<span style="color:var(--text-2);font-size:13px">未处理 <b style="color:var(--danger)">' + unhandled + '</b> 条</span>' +
          (unhandled ? '<button class="btn" id="aResolveAll">全部标记已处理</button>' : '') +
        '</div></div>';

      if (!pageList.length) {
        html += empty('没有符合条件的告警', 'bell-off');
      } else {
        html += '<div class="table-wrap"><table class="tbl"><thead><tr>' +
          '<th>等级</th><th>异常类型</th><th>标题</th><th>平台</th><th>告警内容</th><th>时间</th><th>状态</th><th>操作</th>' +
          '</tr></thead><tbody>' +
          pageList.map(function (a) {
            return '<tr>' +
              '<td data-label="等级">' + U.levelTag(a.level) + '</td>' +
              '<td data-label="类型">' + categoryTag(a.category) + '</td>' +
              '<td data-label="标题"><div class="cell-main"><a href="javascript:void(0)" data-detail="' + a.id + '">' + esc(a.title) + '</a></div>' +
                (a.advice ? '<div class="cell-sub">' + (a.advice.source === 'llm' ? U.icon('robot', 13) + ' 含 AI 建议' : U.icon('doc', 13) + ' 含规则建议') + '</div>' : '') + '</td>' +
              '<td data-label="平台">' + U.platformTag(a.platform) + '</td>' +
              '<td data-label="内容" style="max-width:320px"><div style="color:var(--text-2);font-size:12.8px;line-height:1.6">' + esc(a.message) + '</div></td>' +
              '<td data-label="时间" style="white-space:nowrap">' + S.fmtTime(a.createdAt) + '<div class="cell-sub">' + S.fromNow(a.createdAt) + '</div></td>' +
              '<td data-label="状态">' + U.statusTag(a.status) + '</td>' +
              '<td data-label="操作">' + (a.status === 'unhandled'
                ? '<button class="btn-link" data-res="' + a.id + '">标记已处理</button>'
                : '<span style="color:var(--muted);font-size:12.5px">—</span>') + '</td></tr>';
          }).join('') + '</tbody></table></div>' +
          '<div class="table-foot">' + pager(total, af.page, af.size, 'Views.alerts.goPage') + '</div>';
      }
      html += '</div>';
      return html;
    },
    mount: function () {
      var lv = $('#aLevel'); if (lv) lv.onchange = function () { af.level = lv.value; af.page = 1; App.refresh(); };
      var pl = $('#aPlatform'); if (pl) pl.onchange = function () { af.platform = pl.value; af.page = 1; App.refresh(); };
      var st = $('#aStatus'); if (st) st.onchange = function () { af.status = st.value; af.page = 1; App.refresh(); };
      var ct = $('#aCategory'); if (ct) ct.onchange = function () { af.category = ct.value; af.page = 1; App.refresh(); };
      var ra = $('#aResolveAll');
      if (ra) ra.onclick = function () { S.resolveAllAlerts(); U.toast('ok', '已全部标记为已处理'); App.refresh(); };
      $$('[data-res]').forEach(function (b) {
        b.onclick = function () { S.resolveAlert(b.dataset.res); U.toast('ok', '已处理'); App.refresh(); };
      });
      $$('[data-detail]').forEach(function (b) {
        b.onclick = function () { alerts.detail(b.dataset.detail); };
      });
    },
    goPage: function (p) { af.page = p; App.refresh(); },

    /** 告警详情：异常类型 + 根因 + 处理建议 */
    detail: function (id) {
      var a = S.get().alerts.filter(function (x) { return x.id === id; })[0];
      if (!a) return;
      var adv = a.advice || S.buildAdvice(a.category || 'unknown', {}, false);
      var isLlm = adv.source === 'llm';

      var body =
        '<div class="row-between" style="margin-bottom:14px">' +
          '<div>' + U.levelTag(a.level) + ' ' + categoryTag(a.category) + ' ' + U.platformTag(a.platform) + '</div>' +
          '<span style="font-size:12px;color:var(--muted)">' + S.fmtTime(a.createdAt) + '</span>' +
        '</div>' +
        '<div style="background:var(--panel-2);border:1px solid var(--border-2);border-radius:8px;padding:12px 14px;margin-bottom:16px">' +
          '<div style="font-weight:600;margin-bottom:4px">' + esc(a.title) + '</div>' +
          '<div style="color:var(--text-2);font-size:13px;line-height:1.7">' + esc(a.message) + '</div>' +
        '</div>' +
        '<div class="row-between" style="margin-bottom:10px">' +
          '<b style="font-size:14px">处理建议</b>' +
          '<span class="tag ' + (isLlm ? 'tag-purple' : 'tag-gray') + '">' +
            (isLlm ? U.icon('robot', 13) + ' 大模型生成' : U.icon('doc', 13) + ' 内置规则') + '</span>' +
        '</div>' +
        adviceBlock('可能原因', adv.rootCause, 'var(--warn)') +
        adviceBlock('立即处理', adv.actions, 'var(--primary)') +
        adviceBlock('长期优化', adv.longterm, 'var(--success)');

      if (isLlm && adv.text) {
        body += '<details style="margin-top:12px"><summary style="cursor:pointer;font-size:13px;color:var(--text-2)">查看模型原始输出</summary>' +
          '<div class="code-block" style="margin-top:10px;white-space:pre-wrap">' + esc(adv.text) + '</div></details>';
      }

      U.modal({
        title: '告警详情', width: 640, body: body,
        okText: '关闭', cancelText: a.status === 'unhandled' ? '标记已处理' : '',
        onOk: function () {},
        onOpen: function (w) {
          var r = w.querySelector('[data-act="cancel"]');
          if (r && a.status === 'unhandled') {
            r.className = 'btn btn-primary';
            r.onclick = function () { S.resolveAlert(id); w.remove(); U.toast('ok', '已标记为已处理'); App.refresh(); };
          } else if (r) {
            r.style.display = 'none';
          }
        }
      });
    }
  };

  function adviceBlock(title, content, color) {
    if (!content) return '';
    return '<div style="display:flex;gap:10px;padding:10px 0;border-bottom:1px dashed var(--border-2)">' +
      '<span style="flex:none;width:5px;border-radius:3px;background:' + color + '"></span>' +
      '<div><div style="font-size:12.5px;color:var(--muted);margin-bottom:3px">' + esc(title) + '</div>' +
      '<div style="font-size:13.3px;line-height:1.75;white-space:pre-wrap">' + esc(content) + '</div></div></div>';
  }

  /* ==========================================================
   * 6. 运行日志
   * ========================================================== */
  var lf = { level: '', page: 1, size: 12 };

  var logs = {
    title: '运行日志', desc: '监控与上架任务执行记录',
    render: function () {
      var d = S.get();
      var list = d.logs.filter(function (l) { return !lf.level || l.level === lf.level; });
      var total = list.length;
      var pages = Math.max(1, Math.ceil(total / lf.size));
      if (lf.page > pages) lf.page = pages;
      var pageList = list.slice((lf.page - 1) * lf.size, lf.page * lf.size);

      var html = '<div class="card"><div class="filter-bar">' +
        '<select class="select" id="lLevel"><option value="">全部级别</option>' +
          ['info:信息', 'warn:警告', 'error:错误'].map(function (s) {
            var kv = s.split(':'); return '<option value="' + kv[0] + '"' + (lf.level === kv[0] ? ' selected' : '') + '>' + kv[1] + '</option>';
          }).join('') + '</select>' +
        '<div style="margin-left:auto;color:var(--text-2);font-size:13px">共 ' + total + ' 条，仅保留最近 300 条</div>' +
        '</div>';

      if (!pageList.length) { html += empty('暂无日志'); }
      else {
        html += '<div class="table-wrap"><table class="tbl"><thead><tr>' +
          '<th style="width:150px">时间</th><th style="width:80px">级别</th><th style="width:110px">模块</th><th>内容</th>' +
          '</tr></thead><tbody>' +
          pageList.map(function (l) {
            var color = l.level === 'error' ? 'var(--danger)' : l.level === 'warn' ? '#a5640d' : 'var(--text-2)';
            return '<tr>' +
              '<td data-label="时间" style="white-space:nowrap">' + S.fmtTime(l.ts) + '</td>' +
              '<td data-label="级别">' + U.levelTag(l.level) + '</td>' +
              '<td data-label="模块"><span class="tag tag-gray">' + esc(l.module) + '</span></td>' +
              '<td data-label="内容" style="color:' + color + '">' + esc(l.message) + '</td></tr>';
          }).join('') + '</tbody></table></div>' +
          '<div class="table-foot">' + pager(total, lf.page, lf.size, 'Views.logs.goPage') + '</div>';
      }
      html += '</div>';
      return html;
    },
    mount: function () {
      var lv = $('#lLevel'); if (lv) lv.onchange = function () { lf.level = lv.value; lf.page = 1; App.refresh(); };
    },
    goPage: function (p) { lf.page = p; App.refresh(); }
  };

  /* ==========================================================
   * 7. 平台与凭证
   * ========================================================== */
  var credentials = {
    title: '平台与凭证', desc: 'API 密钥与告警渠道配置',
    render: function () {
      var d = S.get(), c = d.credentials, ch = d.channels;
      var html = '';

      html += '<div class="grid grid-2 mb16">' +
        '<div class="card"><div class="card-head"><h3>亚马逊 SP-API</h3>' +
          U.statusTag(c.amazon.enabled ? 'online' : 'offline') +
          '<div style="margin-left:auto"><label class="switch"><input type="checkbox" id="amzEnabled"' + (c.amazon.enabled ? ' checked' : '') + ' /><span class="slider"></span></label></div>' +
          '</div><div class="card-body">' +
          '<div class="field-row">' +
            '<div class="field"><label>Seller ID</label><input class="input" id="amzSeller" value="' + esc(c.amazon.sellerId) + '" placeholder="A1B2C3D4E5" /></div>' +
            '<div class="field"><label>Marketplace ID</label><input class="input" id="amzMkt" value="' + esc(c.amazon.marketplaceId) + '" /></div>' +
          '</div>' +
          '<div class="field-row">' +
            '<div class="field"><label>LWA Client ID</label><input class="input" id="amzCid" value="' + esc(c.amazon.clientId) + '" /></div>' +
            '<div class="field"><label>LWA Client Secret</label><input class="input" type="password" id="amzCs" value="' + esc(c.amazon.clientSecret) + '" /></div>' +
          '</div>' +
          '<div class="field"><label>Refresh Token</label><input class="input" type="password" id="amzRt" value="' + esc(c.amazon.refreshToken) + '" /></div>' +
          '<div class="field-row">' +
            '<div class="field"><label>AWS Access Key</label><input class="input" id="amzAk" value="' + esc(c.amazon.awsAccessKey) + '" /></div>' +
            '<div class="field"><label>AWS Secret Key</label><input class="input" type="password" id="amzSk" value="' + esc(c.amazon.awsSecretKey) + '" /></div>' +
          '</div>' +
          '<div class="hint">鉴权流程：refresh_token → LWA 换取 access_token → AWS SigV4 签名请求。留空则使用演示数据。</div>' +
        '</div></div>' +

        '<div class="card"><div class="card-head"><h3>拼多多开放平台</h3>' +
          U.statusTag(c.pdd.enabled ? 'online' : 'offline') +
          '<div style="margin-left:auto"><label class="switch"><input type="checkbox" id="pddEnabled"' + (c.pdd.enabled ? ' checked' : '') + ' /><span class="slider"></span></label></div>' +
          '</div><div class="card-body">' +
          '<div class="field"><label>Client ID</label><input class="input" id="pddCid" value="' + esc(c.pdd.clientId) + '" /></div>' +
          '<div class="field"><label>Client Secret</label><input class="input" type="password" id="pddCs" value="' + esc(c.pdd.clientSecret) + '" /></div>' +
          '<div class="field"><label>Access Token</label><input class="input" type="password" id="pddAt" value="' + esc(c.pdd.accessToken) + '" /></div>' +
          '<div class="field"><label>API Endpoint</label><input class="input" id="pddEp" value="' + esc(c.pdd.endpoint) + '" /></div>' +
          '<div class="hint">签名规则：sign = MD5(secret + 按 key 升序拼接 key+value + secret).upper()</div>' +
        '</div></div>' +
        '</div>';

      html += '<div class="card mb16"><div class="card-head"><h3>告警渠道</h3><span class="desc">企业微信 / 钉钉群机器人</span></div>' +
        '<div class="card-body"><div class="grid grid-2">' +
        '<div>' +
          '<label class="check-line" style="margin-bottom:10px"><input type="checkbox" id="wecomEnabled"' + (ch.wecom.enabled ? ' checked' : '') + ' /> 启用企业微信机器人</label>' +
          '<div class="field"><label>Webhook 地址</label><input class="input" id="wecomUrl" value="' + esc(ch.wecom.url) + '" placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..." /></div>' +
          '<button class="btn btn-sm" id="wecomTest">发送测试消息</button>' +
        '</div>' +
        '<div>' +
          '<label class="check-line" style="margin-bottom:10px"><input type="checkbox" id="dtEnabled"' + (ch.dingtalk.enabled ? ' checked' : '') + ' /> 启用钉钉机器人</label>' +
          '<div class="field"><label>Webhook 地址</label><input class="input" id="dtUrl" value="' + esc(ch.dingtalk.url) + '" placeholder="https://oapi.dingtalk.com/robot/send?access_token=..." /></div>' +
          '<div class="field"><label>加签 Secret（可选）</label><input class="input" type="password" id="dtSecret" value="' + esc(ch.dingtalk.secret) + '" placeholder="机器人开启「加签」时填写" /></div>' +
          '<button class="btn btn-sm" id="dtTest">发送测试消息</button>' +
        '</div>' +
        '</div>' +
        '<div class="hint" style="margin-top:6px">演示模式下不会真正发起网络请求，仅记录推送日志；切换为真实模式后由后端进程执行推送。</div>' +
        '</div></div>';

      html += '<div class="row-between"><div></div><button class="btn btn-primary" id="saveCred">保存全部配置</button></div>';
      return html;
    },
    mount: function () {
      var btn = $('#saveCred');
      if (btn) btn.onclick = function () {
        var v = function (id) { var e = document.getElementById(id); return e ? e.value.trim() : ''; };
        var ck = function (id) { var e = document.getElementById(id); return e ? e.checked : false; };
        S.updateCredentials('amazon', {
          enabled: ck('amzEnabled'), sellerId: v('amzSeller'), marketplaceId: v('amzMkt'),
          clientId: v('amzCid'), clientSecret: v('amzCs'), refreshToken: v('amzRt'),
          awsAccessKey: v('amzAk'), awsSecretKey: v('amzSk')
        });
        S.updateCredentials('pdd', {
          enabled: ck('pddEnabled'), clientId: v('pddCid'), clientSecret: v('pddCs'),
          accessToken: v('pddAt'), endpoint: v('pddEp')
        });
        S.updateChannels('wecom', { enabled: ck('wecomEnabled'), url: v('wecomUrl') });
        S.updateChannels('dingtalk', { enabled: ck('dtEnabled'), url: v('dtUrl'), secret: v('dtSecret') });
        U.toast('ok', '配置已保存', '凭证仅保存在浏览器本地，不会上传');
      };
      var wt = $('#wecomTest');
      if (wt) wt.onclick = function () {
        S.updateChannels('wecom', { enabled: $('#wecomEnabled').checked, url: $('#wecomUrl').value.trim() });
        var r = S.testChannel('wecom');
        U.toast(r.ok ? 'ok' : 'err', r.ok ? '企业微信测试成功' : '测试失败', r.msg);
        App.refresh();
      };
      var dt = $('#dtTest');
      if (dt) dt.onclick = function () {
        S.updateChannels('dingtalk', { enabled: $('#dtEnabled').checked, url: $('#dtUrl').value.trim(), secret: $('#dtSecret').value.trim() });
        var r = S.testChannel('dingtalk');
        U.toast(r.ok ? 'ok' : 'err', r.ok ? '钉钉测试成功' : '测试失败', r.msg);
        App.refresh();
      };
    }
  };

  /* ==========================================================
   * 8. 系统设置
   * ========================================================== */
  var settings = {
    title: '系统设置', desc: '运行参数与数据管理',
    render: function () {
      var s = S.get().settings;
      var html = '<div class="grid grid-2 mb16">' +
        '<div class="card"><div class="card-head"><h3>运行模式</h3></div><div class="card-body">' +
          '<div class="stat-line"><span>演示模式（不调用真实接口）</span>' +
            '<label class="switch"><input type="checkbox" id="sDemo"' + (s.demoMode ? ' checked' : '') + ' /><span class="slider"></span></label></div>' +
          '<div class="stat-line"><span>浏览器内自动执行监控</span>' +
            '<label class="switch"><input type="checkbox" id="sAuto"' + (s.autoRun ? ' checked' : '') + ' /><span class="slider"></span></label></div>' +
          '<div class="field mt16"><label>演示模式执行节奏（秒）</label>' +
            '<input class="input" id="sDemoInt" type="number" min="5" value="' + s.demoIntervalSec + '" />' +
            '<div class="hint">真实环境由 crontab 每小时调度（3600 秒）；演示模式可加快到 30 秒一轮便于观察。</div></div>' +
          '<div class="field"><label>监控间隔（分钟）</label>' +
            '<input class="input" id="sInterval" type="number" min="5" value="' + s.monitorIntervalMin + '" /></div>' +
          '<button class="btn btn-primary" id="saveRun">保存运行设置</button>' +
        '</div></div>' +

        '<div class="card"><div class="card-head"><h3>异常检测阈值</h3></div><div class="card-body">' +
          '<div class="field"><label>库存环比下降阈值：<b id="thInvTxt">' + Math.round(s.thresholdInventory * 100) + '%</b></label>' +
            '<input type="range" id="thInv" min="5" max="90" step="5" value="' + Math.round(s.thresholdInventory * 100) + '" style="width:100%" /></div>' +
          '<div class="field"><label>订单量环比下降阈值：<b id="thOrdTxt">' + Math.round(s.thresholdOrders * 100) + '%</b></label>' +
            '<input type="range" id="thOrd" min="5" max="90" step="5" value="' + Math.round(s.thresholdOrders * 100) + '" style="width:100%" /></div>' +
          '<div class="field"><label>价格波动阈值：<b id="thPriTxt">' + Math.round(s.thresholdPrice * 100) + '%</b></label>' +
            '<input type="range" id="thPri" min="5" max="90" step="5" value="' + Math.round(s.thresholdPrice * 100) + '" style="width:100%" /></div>' +
          '<div class="hint">阈值越高越迟钝（漏报），越低越敏感（误报）。建议先用 30% / 50% 跑一周，再按实际波动微调。</div>' +
          '<button class="btn btn-primary mt16" id="saveTh">保存阈值</button>' +
        '</div></div>' +
        '</div>';

      var llm = s.llm || {};
      html += '<div class="card mb16"><div class="card-head"><h3>大模型建议（可选）</h3>' +
        '<span class="desc">不配置密钥则完全不调用，告警照常用内置规则建议</span></div>' +
        '<div class="card-body">' +
          '<div class="stat-line"><span>启用大模型生成处理建议</span>' +
            '<label class="switch"><input type="checkbox" id="llmEnabled"' + (llm.enabled ? ' checked' : '') + ' /><span class="slider"></span></label></div>' +
          '<div class="stat-line"><span>演示模式模拟输出（不发起真实请求）</span>' +
            '<label class="switch"><input type="checkbox" id="llmSim"' + (llm.simulate ? ' checked' : '') + ' /><span class="slider"></span></label></div>' +
          '<div class="field-row mt16">' +
            '<div class="field"><label>Base URL</label>' +
              '<input class="input" id="llmBase" value="' + esc(llm.baseUrl || '') + '" placeholder="https://api.deepseek.com/v1" /></div>' +
            '<div class="field"><label>模型</label>' +
              '<input class="input" id="llmModel" value="' + esc(llm.model || '') + '" placeholder="deepseek-chat" /></div>' +
          '</div>' +
          '<div class="field"><label>API Key</label>' +
            '<input class="input" type="password" id="llmKey" value="' + esc(llm.apiKey || '') + '" placeholder="留空则不调用任何模型" /></div>' +
          '<div class="field"><label>同类异常冷却（分钟）</label>' +
            '<input class="input" id="llmCool" type="number" min="0" value="' + (llm.cooldownMin || 60) + '" />' +
            '<div class="hint">同一类异常在该时间内不重复调用，避免重复烧钱。</div></div>' +
          '<div class="hint" style="border-top:1px dashed var(--border-2);padding-top:12px;margin-top:4px">' +
            '<b>设计原则：</b>异常<b>分类</b>走规则（可复现、零延迟、不花钱），<b>建议</b>才交给大模型，' +
            '且只在告警触发后批量调用一次。模型超时或失败会自动降级到内置规则建议，绝不影响告警送达。</div>' +
          '<div class="row-between mt16">' +
            '<button class="btn" id="llmTest">测试配置</button>' +
            '<button class="btn btn-primary" id="saveLlm">保存大模型设置</button>' +
          '</div>' +
        '</div></div>';

      var lsc = s.listing || {};
      html += '<div class="card mb16"><div class="card-head"><h3>Listing 生成（上架环节）</h3>' +
        '<span class="desc">这是大模型性价比最高的场景：中文商品信息 → 可上架文案</span></div>' +
        '<div class="card-body">' +
          '<div class="field-row">' +
            '<div class="field"><label>默认品牌名</label>' +
              '<input class="input" id="lsBrand" value="' + esc(lsc.brand || '') + '" placeholder="留空则要求每个商品单独填" /></div>' +
            '<div class="field"><label>生成后动作</label>' +
              '<select class="select" id="lsAuto">' +
                '<option value="false"' + (!lsc.autoPublish ? ' selected' : '') + '>只生成，人工确认后上架（推荐）</option>' +
                '<option value="true"' + (lsc.autoPublish ? ' selected' : '') + '>生成通过校验后自动提交上架</option>' +
              '</select></div>' +
          '</div>' +
          '<div class="stat-line"><span>校验有阻断级问题时跳过上架（超长 / 违规词 / 必填属性缺失）</span>' +
            '<label class="switch"><input type="checkbox" id="lsSkip"' + (lsc.skipOnError !== false ? ' checked' : '') + ' /><span class="slider"></span></label></div>' +
          '<div class="hint" style="border-top:1px dashed var(--border-2);padding-top:12px;margin-top:4px">' +
            '<b>为什么默认不自动上架：</b>文案写错可以改，错误上架的清理成本高得多。' +
            '推荐流程是「生成 → 本地校验 → 人工过一眼 → 批量提交」，跑顺一周后再考虑打开自动提交。</div>' +
          '<div class="row-between mt16"><span></span>' +
            '<button class="btn btn-primary" id="saveLs">保存 Listing 设置</button></div>' +
        '</div></div>';

      html += '<div class="card"><div class="card-head"><h3>数据管理</h3><span class="desc">导出配置供 Python 后端使用</span></div>' +
        '<div class="card-body"><div class="grid grid-3">' +
          '<div><div style="font-weight:600;margin-bottom:6px">导出 .env 配置</div>' +
            '<div class="hint" style="margin-bottom:10px">把页面填写的密钥与阈值导出为环境变量文件，供 Python 脚本直接加载。</div>' +
            '<button class="btn btn-sm" id="expEnv">下载 .env</button></div>' +
          '<div><div style="font-weight:600;margin-bottom:6px">导出上架 Payload</div>' +
            '<div class="hint" style="margin-bottom:10px">导出未上架商品为 list_new_products() 可直接消费的 JSON。</div>' +
            '<button class="btn btn-sm" id="expProd">下载 products.json</button></div>' +
          '<div><div style="font-weight:600;margin-bottom:6px">重置演示数据</div>' +
            '<div class="hint" style="margin-bottom:10px">清空当前浏览器数据并重新生成一套演示数据。</div>' +
            '<button class="btn btn-sm btn-danger" id="resetData">重置数据</button></div>' +
        '</div></div></div>';
      return html;
    },
    mount: function () {
      var bindRange = function (id, txtId) {
        var r = document.getElementById(id), t = document.getElementById(txtId);
        if (r && t) r.oninput = function () { t.textContent = r.value + '%'; };
      };
      bindRange('thInv', 'thInvTxt'); bindRange('thOrd', 'thOrdTxt'); bindRange('thPri', 'thPriTxt');

      var sr = $('#saveRun');
      if (sr) sr.onclick = function () {
        var di = parseInt($('#sDemoInt').value, 10), mi = parseInt($('#sInterval').value, 10);
        if (isNaN(di) || di < 5) { U.toast('err', '演示节奏不能小于 5 秒'); return; }
        if (isNaN(mi) || mi < 5) { U.toast('err', '监控间隔不能小于 5 分钟'); return; }
        S.updateSettings({
          demoMode: $('#sDemo').checked, autoRun: $('#sAuto').checked,
          demoIntervalSec: di, monitorIntervalMin: mi
        });
        App.applyMode(); App.restartTimer();
        U.toast('ok', '已保存', '运行设置已生效');
        App.refresh();
      };
      var st = $('#saveTh');
      if (st) st.onclick = function () {
        S.updateSettings({
          thresholdInventory: +$('#thInv').value / 100,
          thresholdOrders: +$('#thOrd').value / 100,
          thresholdPrice: +$('#thPri').value / 100
        });
        U.toast('ok', '阈值已保存', '下次监控执行时生效');
      };
      var sl = $('#saveLlm');
      if (sl) sl.onclick = function () {
        var cool = parseInt($('#llmCool').value, 10);
        S.updateSettings({
          llm: {
            enabled: $('#llmEnabled').checked,
            simulate: $('#llmSim').checked,
            baseUrl: $('#llmBase').value.trim(),
            model: $('#llmModel').value.trim(),
            apiKey: $('#llmKey').value.trim(),
            cooldownMin: isNaN(cool) ? 60 : cool
          }
        });
        U.toast('ok', '已保存', $('#llmEnabled').checked ? '大模型建议已启用' : '当前使用内置规则建议');
        App.refresh();
      };
      var lt = $('#llmTest');
      if (lt) lt.onclick = function () {
        var d = S.get();
        if (d.settings.demoMode) {
          U.toast('info', '演示模式', '网页端不会发起真实模型请求；开启「模拟输出」可预览效果，真实调用由 Python 后端执行');
        } else if (!$('#llmKey').value.trim()) {
          U.toast('warn', '未填写 API Key', '留空时不会调用任何模型，告警仍会按规则建议发送');
        } else {
          U.toast('ok', '配置已就绪', '真实调用在 Python 后端进行（避免密钥暴露在浏览器与跨域问题）');
        }
      };
      var sls = $('#saveLs');
      if (sls) sls.onclick = function () {
        S.updateSettings({
          listing: {
            brand: $('#lsBrand').value.trim(),
            autoPublish: $('#lsAuto').value === 'true',
            skipOnError: $('#lsSkip').checked
          }
        });
        U.toast('ok', '已保存', $('#lsAuto').value === 'true' ? '生成后自动提交上架（注意风险）' : '生成后仅导出 payload，人工确认再上架');
      };
      var ee = $('#expEnv'); if (ee) ee.onclick = function () { S.download('.env', S.exportEnv()); U.toast('ok', '已导出 .env'); };
      var ep = $('#expProd'); if (ep) ep.onclick = function () { S.download('products.json', S.exportProductsPayload()); U.toast('ok', '已导出商品 Payload'); };
      var rd = $('#resetData');
      if (rd) rd.onclick = function () {
        U.confirm('重置演示数据', '将清空当前所有商品、任务、告警与日志，并重新生成演示数据。确定继续？', function () {
          S.reset(); U.toast('ok', '已重置'); App.refresh();
        }, '确定重置');
      };
    }
  };

  /* ==========================================================
   * 9. 部署指南
   * ========================================================== */
  var deploy = {
    title: '部署指南', desc: '接入真实 API 的实施步骤',
    render: function () {
      var perf =
        '<div class="card mb16"><div class="card-head"><h3>容量与性能：SKU 多了，每小时扫描来得及吗</h3>' +
        '<span class="desc">按 getInventorySummaries 限流 2 req/s、每页约 50 条测算</span></div>' +
        '<div class="card-body doc">' +
        '<p><b>结论：来得及，但瓶颈不在你以为的地方。</b></p>' +
        '<div class="table-wrap"><table class="tbl"><thead><tr><th>SKU 数</th><th>请求数</th><th>理论耗时</th><th>结论</th></tr></thead><tbody>' +
        '<tr><td data-label="SKU 数">1,000</td><td data-label="请求数">20 页</td><td data-label="耗时">10 秒</td><td data-label="结论">毫无压力</td></tr>' +
        '<tr><td data-label="SKU 数">10,000</td><td data-label="请求数">200 页</td><td data-label="耗时">~100 秒</td><td data-label="结论">安全</td></tr>' +
        '<tr><td data-label="SKU 数">50,000</td><td data-label="请求数">1,000 页</td><td data-label="耗时">~8 分钟</td><td data-label="结论">可以，但已占用调度窗口</td></tr>' +
        '<tr><td data-label="SKU 数">100,000</td><td data-label="请求数">2,000 页</td><td data-label="耗时">~17 分钟</td><td data-label="结论">危险区</td></tr>' +
        '<tr><td data-label="SKU 数">500,000+</td><td data-label="请求数">10,000 页</td><td data-label="耗时">~80 分钟</td><td data-label="结论">超出 1 小时窗口，必须换方案</td></tr>' +
        '</tbody></table></div>' +
        '<h4>到 10 万 SKU 之前，你会先踩这三个坑</h4>' +
        '<ul>' +
          '<li><b>只拉了第一页（最严重）</b>：库存接口没有 pageSize 参数，必须靠 <code>nextToken</code> 翻页，' +
          '否则超过 50 个 SKU 后<b>静默漏监控、不报错</b>。拼多多同理，单页 100 条。</li>' +
          '<li><b>state.json 每个 SKU 全量读写一次</b>：1 万个 SKU = 1 万次 JSON 全量读写，' +
          '这一项的耗时经常超过拉数据本身，是"越跑越慢"的真正来源。</li>' +
          '<li><b>订单接口限流极低</b>：<code>getOrders</code> 仅 0.0167 req/s（约 1 次/分钟），' +
          '一小时一次安全；且单页有上限，不翻页会少算订单、<b>误报</b>"订单量暴跌"。</li>' +
        '</ul>' +
        '<h4>优化顺序</h4>' +
        '<ul>' +
          '<li><b>增量拉取</b>（最有效）：传 <code>startDateTime</code> 只拉有变更的库存，' +
          '2,000 次翻页通常降到几十次。注意传了它 <code>sellerSkus</code> 会被忽略。</li>' +
          '<li><b>分片轮转</b>：SKU 分 N 片每小时扫 1 片，爆款走 <code>FOCUS_SKUS</code> 每轮都扫。</li>' +
          '<li><b>全量快照走 Reports API</b>：一次报告替代数千次调用，异步生成，适合每天 1~2 次对账。</li>' +
          '<li><b>订阅代替轮询</b>：Buy Box / 价格 / Listing 状态用 Notifications API 推送。' +
          '库存变更通知未确认存在，且官方建议保留轮询兜底。</li>' +
        '</ul>' +
        '<p><b>反直觉的一点</b>：SP-API 库存自带 <code>lastUpdatedTime</code>，通常滞后 15~30 分钟。' +
        '把频率从 1 小时提到 15 分钟，拿到的很可能还是同一份滞后数据，只是多烧了几倍配额。' +
        '所以库存 1~2 小时一次完全够，真正需要分钟级的是 Buy Box 与 Listing 状态——那个该用订阅。</p>' +
        '</div></div>';

      return perf + '<div class="grid grid-2">' +
        '<div class="card"><div class="card-head"><h3>整体架构</h3></div><div class="card-body doc">' +
          '<p>网页控制台负责<b>配置、可视化与规则管理</b>，Python 脚本负责<b>真实调用平台 API 与推送告警</b>。两者通过导出的 <code>.env</code> 衔接。</p>' +
          '<h4>目录结构</h4>' +
          '<div class="code-block">ecommerce_monitor/\n├── config.py            <span class="c"># 密钥 / 阈值（读环境变量）</span>\n├── amazon_client.py     <span class="c"># SP-API：LWA 鉴权 + SigV4 签名</span>\n├── pdd_client.py        <span class="c"># 拼多多：MD5 签名 + 商品/订单</span>\n├── anomaly_detector.py  <span class="c"># 环比阈值检测（state.json）</span>\n├── incident.py          <span class="c"># 异常分类 + 大模型处理建议</span>\n├── listing_gen.py       <span class="c"># 大模型生成 Listing + 类目 schema</span>\n├── alerts.py            <span class="c"># 企业微信 / 钉钉机器人</span>\n└── main.py              <span class="c"># monitor / daemon / genlist 入口</span></div>' +
          '<h4>需要准备的密钥</h4>' +
          '<ul>' +
            '<li><b>亚马逊</b>：refresh_token、LWA client id/secret、AWS IAM access/secret key、Seller ID、Marketplace ID</li>' +
            '<li><b>拼多多</b>：client_id、client_secret、店铺授权后的 access_token</li>' +
            '<li><b>告警</b>：企业微信群机器人 webhook、钉钉群机器人 webhook（开启加签还需 secret）</li>' +
          '</ul>' +
          '<p>密钥全部走环境变量，不要写进代码。可在「平台与凭证」页填写后点击<b>下载 .env</b> 直接生成。</p>' +
        '</div></div>' +

        '<div class="card"><div class="card-head"><h3>上线步骤</h3></div><div class="card-body doc">' +
          '<h4>1. 安装依赖</h4>' +
          '<div class="code-block"><span class="k">pip install</span> -r requirements.txt</div>' +
          '<h4>2. 先手动跑一次，确认链路通</h4>' +
          '<div class="code-block"><span class="k">python</span> main.py monitor</div>' +
          '<p>无输出异常即表示鉴权与抓取正常；若报 <code>invalid_grant</code> 通常是 refresh_token 过期，需重新授权。</p>' +
          '<h4>3. 配置每小时自动执行</h4>' +
          '<div class="code-block"><span class="c"># crontab -e，每小时整点跑一次</span>\n<span class="g">0</span> * * * * cd /path/to/ecommerce_monitor &amp;&amp; \\\n  /usr/bin/python3 main.py monitor &gt;&gt; monitor.log 2&gt;&amp;1</div>' +
          '<p>或常驻进程方式（需 <code>pip install apscheduler</code>）：<code>python main.py daemon</code>，配合 systemd / supervisor / docker 托管。</p>' +
          '<h4>4. 生成 Listing（推荐先跑这一步）</h4>' +
          '<p>把中文商品信息交给大模型，生成英文标题 / 五点 / 描述 / 关键词，并按类目 schema 补全必填属性，' +
          '生成结果会先过本地校验（长度红线、违规词、必填属性），默认<b>只导出 payload 不自动上架</b>。</p>' +
          '<div class="code-block"><span class="c"># 输入格式见 listing_input.example.json</span>\n<span class="k">python</span> main.py genlist listing_input.json amazon\n<span class="k">python</span> main.py genlist listing_input.json pdd  <span class="c"># 生成拼多多中文文案</span></div>' +
          '<p>确认 <code>listing_payloads.json</code> 文案无误后，在 .env 打开 <code>LISTING_AUTO_PUBLISH=true</code> 即可一键上架。' +
          '不配 <code>LLM_API_KEY</code> 时用规则模板出草稿，流程照样跑得通。</p>' +
          '<h4>5. 上架商品</h4>' +
          '<div class="code-block"><span class="k">from</span> main <span class="k">import</span> list_new_products\n\nlist_new_products([\n  {<span class="g">"platform"</span>: <span class="g">"amazon"</span>, <span class="g">"sku"</span>: <span class="g">"SKU123"</span>, <span class="g">"payload"</span>: {...}},\n  {<span class="g">"platform"</span>: <span class="g">"pdd"</span>, <span class="g">"payload"</span>: {...}},\n])</div>' +
          '<p>不同类目要求的字段不同，权威来源是 SP-API 的 <code>getDefinitionsProductType</code>' +
          '（listing_gen.fetch_product_type_definition 已封装）；建议先跑通单个商品再批量执行。</p>' +
          '<h4>注意事项</h4>' +
          '<ul>' +
            '<li>平台接口字段与限流规则会不定期调整，接入前请对照官方文档核对</li>' +
            '<li><code>state.json</code> 是本地文件存储，多机部署建议换成 Redis 或数据库</li>' +
            '<li>数据量变大后建议把环比检测升级为移动平均 / 同比，减少误报</li>' +
          '</ul>' +
        '</div></div>' +
        '</div>';
    }
  };

  global.Views = {
    dashboard: dashboard, products: products, listing: listing,
    tasks: tasks, alerts: alerts,
    logs: logs, credentials: credentials, settings: settings, deploy: deploy
  };
})(window);
