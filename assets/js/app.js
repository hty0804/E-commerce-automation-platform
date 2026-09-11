/* ==========================================================
 * app.js —— 路由、登录、定时调度
 * ========================================================== */
(function (global) {
  'use strict';

  var S = global.Store, U = global.UI, V = global.Views;

  var NAV = [
    { group: '概览', items: [{ key: 'dashboard', name: '数据看板', icon: 'dashboard' }] },
    {
      group: '运营', items: [
        { key: 'products', name: '商品管理', icon: 'box' },
        { key: 'listing', name: 'AI 生成 Listing', icon: 'robot' },
        { key: 'imageLibrary', name: '图片库', icon: 'image' },
        { key: 'tasks', name: '监控任务', icon: 'gear' }
      ]
    },
    {
      group: '监控', items: [
        { key: 'alerts', name: '告警中心', icon: 'bell', badge: 'unhandled' },
        { key: 'logs', name: '运行日志', icon: 'list' }
      ]
    },
    {
      group: '系统', items: [
        { key: 'shops', name: '店铺管理', icon: 'store' },
        { key: 'credentials', name: '平台与凭证', icon: 'key' },
        { key: 'settings', name: '系统设置', icon: 'sliders' },
        { key: 'deploy', name: '部署指南', icon: 'book' }
      ]
    }
  ];

  var RUN_LABEL = '<svg class="ic-svg" viewBox="0 0 24 24" width="15" height="15" fill="currentColor" stroke="none"><path d="M8 5.5v13l11-6.5z"/></svg> 立即监控';

  var current = 'dashboard';
  var timer = null;

  var App = {
    /* ---------- 启动 ---------- */
    init: function () {
      S.load();
      if (S.session()) { this.enterApp(); return; }
      this.bindLogin();
    },

    bindLogin: function () {
      var form = document.getElementById('loginForm');
      var pwd = document.getElementById('password');
      document.getElementById('eyeBtn').onclick = function () {
        pwd.type = pwd.type === 'password' ? 'text' : 'password';
      };
      document.getElementById('forgotLink').onclick = function () {
        U.toast('info', '演示环境', '演示账号：admin / admin123');
      };
      form.onsubmit = function (e) {
        e.preventDefault();
        var u = document.getElementById('username').value.trim();
        var p = pwd.value;
        var rememberEl = document.getElementById('remember');
        var btn = document.getElementById('loginBtn');
        // 「记住登录状态」必须真的传下去：不传的话勾选框就是个摆设，
        // 用户以为不勾能少留一份登录态，实际仍然长期写在 localStorage 里。
        var r = S.login(u, p, rememberEl ? rememberEl.checked : true);
        if (!r.ok) { U.toast('err', '登录失败', r.msg); return; }
        btn.disabled = true; btn.textContent = '登录中...';
        setTimeout(function () { App.enterApp(); }, 350);
      };
    },

    enterApp: function () {
      document.getElementById('loginScreen').classList.add('hidden');
      document.getElementById('appShell').classList.remove('hidden');
      this.renderNav();
      this.go('dashboard');
      this.bindShell();
      this.applyMode();
      this.startTimer();
      this.startClock();
    },

    /* ---------- 导航 ---------- */
    renderNav: function () {
      var d = S.get();
      var unhandled = d.alerts.filter(function (a) { return a.status === 'unhandled'; }).length;
      var html = NAV.map(function (g) {
        return '<div class="nav-group">' + g.group + '</div>' + g.items.map(function (it) {
          return '<div class="nav-item' + (it.key === current ? ' active' : '') + '" data-key="' + it.key + '">' +
            '<span class="n-ic">' + U.icon(it.icon, 18) + '</span><span>' + it.name + '</span>' +
            (it.badge === 'unhandled' && unhandled ? '<span class="badge-mini">' + unhandled + '</span>' : '') +
            '</div>';
        }).join('');
      }).join('');
      var nav = document.getElementById('nav');
      nav.innerHTML = html;
      Array.prototype.forEach.call(nav.querySelectorAll('.nav-item'), function (el) {
        el.onclick = function () { App.go(el.dataset.key); App.closeSidebar(); };
      });
    },

    go: function (key) {
      if (!V[key]) key = 'dashboard';
      current = key;
      var view = V[key];
      document.getElementById('pageTitle').textContent = view.title;
      document.getElementById('pageDesc').textContent = view.desc || '';
      var content = document.getElementById('content');
      content.innerHTML = '<div class="view">' + view.render() + '</div>';
      if (view.mount) view.mount();
      this.renderNav();
      this.renderShopSwitcher();   // 顶栏店铺名必须和当前数据保持一致
      window.scrollTo(0, 0);
    },

    refresh: function () { this.go(current); },

    /* ---------- 多店铺 ---------- */
    renderShopSwitcher: function () {
      var sel = document.getElementById('shopSelect');
      if (!sel || !S.shops) return;
      var list = S.shops(), cur = S.activeShopId();
      sel.innerHTML = list.map(function (s) {
        return '<option value="' + U.esc(s.id) + '"' + (s.id === cur ? ' selected' : '') + '>' + U.esc(s.name) + '</option>';
      }).join('');
      sel.onchange = function () { App.switchShop(sel.value); };
      var chip = document.getElementById('shopCount');
      if (chip) chip.textContent = list.length + ' 家';
    },

    /**
     * 切换店铺。切换后必须重跑 applyMode / 定时器 ——
     * 每家店的演示模式、监控节奏、是否自动执行都是**各自独立**的配置，
     * 不重跑的话会继续沿用上一家店的节奏，表现是"切了店但定时任务还是按老店跑"。
     */
    switchShop: function (id) {
      var r = S.switchShop(id);
      if (!r.ok) { U.toast('err', '切换失败', r.msg); this.renderShopSwitcher(); return; }
      if (!r.changed) return;
      U.toast('ok', '已切换到「' + r.shop.name + '」', '商品 / 任务 / 告警 / 日志 / 凭证均已按店隔离');
      this.applyMode();
      this.restartTimer();
      this.go(current);
    },

    bindShell: function () {
      var self = this;
      document.getElementById('hamburger').onclick = function () { self.toggleSidebar(); };
      document.getElementById('runNowBtn').onclick = function () { App.runMonitor(); };
      document.getElementById('modeChip').onclick = function () {
        var d = S.get();
        S.updateSettings({ demoMode: !d.settings.demoMode });
        U.toast(d.settings.demoMode ? 'warn' : 'ok',
          d.settings.demoMode ? '已切换到真实模式' : '已切换到演示模式',
          d.settings.demoMode ? '真实模式下推送交由后端执行' : '演示模式使用内置模拟数据');
        self.applyMode(); self.refresh();
      };
      document.getElementById('avatarBtn').onclick = function () {
        U.confirm('退出登录', '确定要退出当前账号吗？', function () {
          S.logout(); location.reload();
        }, '退出');
      };
    },

    toggleSidebar: function () {
      var sb = document.getElementById('sidebar');
      sb.classList.toggle('open');
      var exist = document.querySelector('.backdrop');
      if (sb.classList.contains('open')) {
        if (!exist) {
          var bd = document.createElement('div');
          bd.className = 'backdrop';
          bd.onclick = function () { App.closeSidebar(); };
          document.body.appendChild(bd);
        }
      } else if (exist) { exist.remove(); }
    },
    closeSidebar: function () {
      document.getElementById('sidebar').classList.remove('open');
      var bd = document.querySelector('.backdrop');
      if (bd) bd.remove();
    },

    applyMode: function () {
      var d = S.get();
      var chip = document.getElementById('modeChip');
      var txt = document.getElementById('modeChipText');
      if (d.settings.demoMode) {
        chip.className = 'mode-chip'; txt.textContent = '演示模式';
      } else {
        chip.className = 'mode-chip live'; txt.textContent = '真实模式';
      }
    },

    /* ---------- 调度 ---------- */
    startTimer: function () {
      var self = this;
      this.stopTimer();
      var d = S.get();
      if (!d.settings.autoRun) return;
      var ms = Math.max(5, d.settings.demoIntervalSec) * 1000;
      timer = setInterval(function () {
        var r = S.runMonitor();
        if (r.anomalies.length) {
          U.toast('err', '检测到 ' + r.anomalies.length + ' 项异常', r.anomalies[0].title);
        }
        if (current === 'dashboard' || current === 'alerts' || current === 'logs' || current === 'tasks') self.refresh();
        else self.renderNav();
      }, ms);
    },
    stopTimer: function () { if (timer) { clearInterval(timer); timer = null; } },
    restartTimer: function () { this.startTimer(); },

    runMonitor: function (opts) {
      var btn = document.getElementById('runNowBtn');
      if (btn) { btn.disabled = true; btn.textContent = '执行中...'; }
      var d = S.get();
      var demoMode = d.settings.demoMode;
      setTimeout(function () {
        var r = S.runMonitor(opts);
        if (btn) { btn.disabled = false; btn.innerHTML = RUN_LABEL; }
        if (r.anomalies.length) {
          U.toast('err', '发现 ' + r.anomalies.length + ' 项异常',
            r.anomalies[0].title + (r.pushedTo.length ? ' · 已推送' + r.pushedTo.join('/') : ' · 仅站内记录'));
        } else {
          U.toast('ok', '监控执行完成', '共检查 ' + r.checked + ' 项指标，未发现异常' + (demoMode ? '（演示数据）' : ''));
        }
        App.refresh();
      }, 520);
    },

    startClock: function () {
      var el = document.getElementById('sidebarClock');
      function tick() { el.textContent = S.fmtClock(S.now()); }
      tick(); setInterval(tick, 1000);
    }
  };

  global.App = App;
  document.addEventListener('DOMContentLoaded', function () { App.init(); });
})(window);
