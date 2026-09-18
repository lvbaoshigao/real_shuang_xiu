/*!
 * web/app.js —— 「今天你双休了吗？」前端逻辑（SPEC v1.2 §4，F1–F12）
 *
 * 纯静态 / 零依赖 / 零外链。数据只来自 data/index.json 索引的分片（相对路径，GitHub Pages 子路径可用）：
 *   在线：先读 index.json，再按 index.companies / index.schools 的数组顺序合并分片；
 *   离线：file:// 下同源策略拒绝读取 data/ 时，回退 window.SHUANGXIU_SNAPSHOT（web/data-snapshot.js 的文件镜像）。
 *
 * 渲染完全「按键渲染」：不写死任何公司名/学校名/字段名/枚举清单。
 * 约定键（名称 / 休息情况 / 双休情况 / 岗位 / 评价 / 补课情况）只做视觉增强，其余键一律回退为「键: 值」行。
 */
(function () {
  'use strict';

  /* ============================================================
   * 1. 常量与配置
   * ============================================================ */

  var DATA_DIR = '../data/';                 // 相对 web/index.html（不得以 / 开头）
  var INDEX_FILE = 'index.json';

  var COMMENTS_FILE = 'comments.json';      /* SPEC v1.3 §3.9：评论分片（索引缺失时的回退名） */

  var BOARDS = {
    companies: { dataKey: '公司', entryClass: 'entry--company', label: '公司' },
    schools: { dataKey: '学校', entryClass: 'entry--school', label: '学校' },
    comments: { dataKey: '评论', entryClass: '', label: '评论' }
  };

  /* SPEC v1.3 §3.9 评论字段（冻结键名）与 §4.7 提交模板 */
  var COMMENT_TARGET_KEY = '目标';
  var COMMENT_BOARD_KEY = '板块';
  var COMMENT_NO_KEY = '编号';
  var COMMENT_TEXT_KEY = '内容';
  var COMMENT_NICK_KEY = '昵称';
  var COMMENT_DATE_KEY = '日期';
  var COMMENT_MARKER = '<!-- comment-submission -->';
  var COMMENT_MAX_CHARS = 1000;
  var REPO_PLACEHOLDER = 'OWNER/REPO';

  /* 约定键（SPEC §4.2）：仅用于视觉增强，键缺失时功能不受影响 */
  var TITLE_KEYS = ['名称'];
  var STATUS_KEYS = ['休息情况', '双休情况'];
  var JOB_KEYS = ['岗位'];
  var REVIEW_KEYS = ['评价'];
  var CATEGORY_KEYS = ['类别', '类型', '行业', '性质', '地区', '城市', '省份'];
  var REMARK_KEYS = ['备注'];                      /* 引用样式段落（承载负面舆情） */
  /* 岗位子表内可能承载「是否双休」信息的键（数据形态两种都要兼容） */
  var JOB_STATUS_KEYS = ['双休', '双休情况', '周末双休', '休息情况'];

  /* 状态令牌 → 徽标变体：颜色 + 文字 + 字形三重区分（不依赖颜色单独传义） */
  var TOKEN_VARIANT = {
    /* 肯定 */
    '是': 'yes', '有': 'yes', '双休': 'yes', '全部双休': 'yes',
    /* 否定（学校侧常用「无」；与「否」同级否定语义） */
    '否': 'no', '无': 'no', '没有': 'no', '单休': 'no', '无双休': 'no',
    /* 部分 */
    '大小周': 'partial', '部分': 'partial', '部分双休': 'partial',
    /* 未知 */
    '未知': 'unknown'
  };
  var VARIANT_GLYPH = { yes: '是', no: '否', partial: '半', unknown: '?' };
  /* 折叠态整体状态灯：变体 → 灯色（颜色只是冗余信号，灯内始终有可见文字） */
  var LIGHT_VARIANT = { yes: 'green', partial: 'yellow', no: 'red', unknown: 'unknown' };
  /* 学校气泡：只收「短标量」字段，超过该长度的文本放详情区 */
  var BUBBLE_MAX_CHARS = 18;
  var DERIVED_ORDER = ['全部双休', '部分双休', '无双休', '未知'];

  var DEBOUNCE_MS = 200;        // F6：输入防抖 ≤ 300ms
  var BATCH_SIZE = 40;          // §4.5：分批插入，避免一次性插入上万节点
  var EMPTY_TEXT = '暂无记录';

  /* ============================================================
   * 2. 运行时状态
   * ============================================================ */

  var dom = {};
  var state = {
    board: 'companies',
    rawQ: '',          /* 用户原始输入（保留大小写）：仅用于输入框回填与 URL 参数 */
    q: '',             /* 归一化匹配串（toLowerCase）：仅用于搜索匹配 */
    deepEntry: '',     /* URL 里的 entry 参数（深链待打开；打开/关闭后清空） */
    sort: 'default',
    status: '',
    offline: false,
    loaded: false,
    error: null
  };

  /* 每个板块的数据视图：entries + 状态键（显式键或推导）+ 分类键 */
  var model = {
    companies: { entries: [], statusKey: null, statusLabel: '', derived: true, categoryKey: null, statusValues: [], statusRank: {}, statusOf: null, total: 0 },
    schools: { entries: [], statusKey: null, statusLabel: '', derived: false, categoryKey: null, statusValues: [], statusRank: {}, statusOf: null, total: 0 }
  };

  /* SPEC v1.3 §3.9：评论与条目解耦，靠「板块 + 目标」关联；site 提供提交配置 */
  model.comments = [];
  model.site = { repo: '', issueLabel: '' };

  /* 抽屉运行时状态（SPEC §4.7.1） */
  var drawerState = { open: false, board: null, name: null, trigger: null };
  var entryNoticeTimer = 0;

  var renderToken = 0;
  var detailSeq = 0;          /* 详情渲染序号（每次重渲染重置，用于唯一 id/键） */

  /* ============================================================
   * 3. 通用工具
   * ============================================================ */

  function byId(id) { return document.getElementById(id); }

  function hasOwn(obj, key) { return Object.prototype.hasOwnProperty.call(obj, key); }

  function isObject(v) { return v !== null && typeof v === 'object' && !Array.isArray(v); }

  function isScalar(v) { return v === null || typeof v !== 'object'; }

  function scalarText(v) {
    if (v === null || typeof v === 'undefined') { return '未知'; }
    if (typeof v === 'boolean') { return v ? '是' : '否'; }
    return String(v);
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (typeof text === 'string' && text.length) { node.textContent = text; }
    return node;
  }

  function firstKeyOf(entry, keys) {
    for (var i = 0; i < keys.length; i++) {
      if (hasOwn(entry, keys[i]) && isScalar(entry[keys[i]]) && scalarText(entry[keys[i]]) !== '') { return keys[i]; }
    }
    return null;
  }

  function presentKeyOf(entries, keys) {
    for (var i = 0; i < keys.length; i++) {
      for (var j = 0; j < entries.length; j++) {
        if (hasOwn(entries[j], keys[i]) && isScalar(entries[j][keys[i]]) && scalarText(entries[j][keys[i]]) !== '') { return keys[i]; }
      }
    }
    return null;
  }

  function uniquePush(list, value) {
    if (typeof value === 'string' && value !== '' && list.indexOf(value) < 0) { list.push(value); }
  }

  /* 递归收集条目内所有标量值（F6 搜索范围：所有标量值，不区分大小写子串匹配） */
  function collectScalars(value, out) {
    if (isScalar(value)) {
      var text = scalarText(value);
      if (text !== '') { out.push(text); }
      return out;
    }
    if (Array.isArray(value)) {
      for (var i = 0; i < value.length; i++) { collectScalars(value[i], out); }
      return out;
    }
    var keys = Object.keys(value);
    for (var k = 0; k < keys.length; k++) { collectScalars(value[keys[k]], out); }
    return out;
  }

  function badge(label, variant, glyph, hint) {
    var node = el('span', 'badge' + (variant ? ' badge--' + variant : ''));
    var g = typeof glyph === 'string' ? glyph : (variant && VARIANT_GLYPH[variant] ? VARIANT_GLYPH[variant] : '');
    if (g) {
      var glyphNode = el('span', 'badge__glyph', g);
      glyphNode.setAttribute('aria-hidden', 'true');
      node.appendChild(glyphNode);
    }
    node.appendChild(el('span', 'badge__text', label));
    if (hint) { node.title = hint; }
    return node;
  }

  function row(key, valueNode, muted) {
    var rowNode = el('div', 'kv-row');
    rowNode.appendChild(el('span', 'kv-row__key', key));
    var valueWrap = el('span', 'kv-row__value' + (muted ? ' kv-row__value--muted' : ''));
    if (typeof valueNode === 'string') { valueWrap.textContent = valueNode; } else { valueWrap.appendChild(valueNode); }
    rowNode.appendChild(valueWrap);
    return rowNode;
  }

  function emptyNote(tagName) {
    var node = el(tagName || 'p', 'empty-note');
    node.appendChild(el('span', 'empty-note__text', EMPTY_TEXT));
    return node;
  }

  /* ============================================================
   * 4. 数据加载（索引驱动 + 离线快照兜底）
   * ============================================================ */

  function dataError(message) {
    var err = new Error(message);
    err.sxKind = 'data';       // 数据 / 网络 / 结构错误：一律进入加载失败状态，不静默兜底
    return err;
  }

  /* 显式离线判定：只有 file://（双击打开本地文件）才使用内置快照 */
  function isOfflineProtocol() {
    return typeof location !== 'undefined' && location.protocol === 'file:';
  }

  /* 在线读取：相对路径请求 data/<file>。
     http(s) 下任何失败（网络、同源限制、非 2xx、JSON 非法）都抛 dataError →
     load() 进入 #state-error 失败状态（含 #error-message 与 #retry-button），
     不再回退内置快照，避免把陈旧快照当作真实数据展示。 */
  function fetchJson(fileName) {
    return fetch(DATA_DIR + fileName).then(function (res) {
      if (!res.ok) { throw dataError('无法读取 data/' + fileName + '（HTTP ' + res.status + '）'); }
      return res.json().catch(function () { throw dataError('data/' + fileName + ' 不是合法的 JSON'); });
    }, function () {
      throw dataError('无法读取 data/' + fileName + '（网络或同源限制）');
    });
  }

  /* 离线读取：web/data-snapshot.js 暴露的文件镜像 */
  function snapshotJson(fileName) {
    var snap = window.SHUANGXIU_SNAPSHOT;
    if (!snap || typeof snap !== 'object' || Array.isArray(snap)) {
      return Promise.reject(dataError('离线快照不可用：未找到内置快照对象'));
    }
    if (!hasOwn(snap, fileName)) {
      return Promise.reject(dataError('离线快照缺少文件：' + fileName));
    }
    return Promise.resolve(snap[fileName]);
  }

  function readJson(fileName, offline) {
    return offline ? snapshotJson(fileName) : fetchJson(fileName);
  }

  function validateIndex(index) {
    if (!isObject(index)) { throw dataError('索引 ' + INDEX_FILE + ' 必须是对象'); }
    if (!Array.isArray(index.companies) || !Array.isArray(index.schools)) {
      throw dataError('索引 ' + INDEX_FILE + ' 必须包含 companies / schools 两个数组');
    }
    if (index.companies.length === 0 || index.schools.length === 0) {
      throw dataError('索引 ' + INDEX_FILE + ' 的 companies / schools 必须是非空数组');
    }
  }

  /* 评论分片文件名（SPEC v1.3 §3.1）：索引里没有 comments 时回退到 comments.json */
  function commentShards(index) {
    if (Array.isArray(index.comments) && index.comments.length) { return index.comments.slice(); }
    return [COMMENTS_FILE];
  }

  /* 按索引顺序读取并合并：先索引，再按数组顺序并发拉取全部数据分片（含评论分片） */
  function readAll(offline) {
    return readJson(INDEX_FILE, offline).then(function (index) {
      validateIndex(index);
      var names = [];
      index.companies.forEach(function (n) { names.push({ board: 'companies', file: n }); });
      index.schools.forEach(function (n) { names.push({ board: 'schools', file: n }); });
      commentShards(index).forEach(function (n) { names.push({ board: 'comments', file: n }); });
      var tasks = names.map(function (item) {
        return readJson(item.file, offline).then(function (data) {
          var key = BOARDS[item.board].dataKey;
          if (!isObject(data) || !Array.isArray(data[key])) {
            throw dataError(item.file + ' 顶层必须是 {"' + key + '": [...]}');
          }
          return { item: item, data: data };
        });
      });
      return Promise.all(tasks).then(function (results) {
        var groups = { companies: [], schools: [], comments: [] };
        results.forEach(function (r) {
          groups[r.item.board] = groups[r.item.board].concat(r.data[BOARDS[r.item.board].dataKey]);
        });
        return { index: index, groups: groups };
      });
    });
  }

  /* 加载状态机（SPEC §4.3）：
     - file://（显式离线判定）→ 内置快照 + #offline-note 提示；
     - http(s) 下任何失败（网络 reject / 非 2xx / JSON 非法 / 结构非法）→ 一律进入
       #state-error 失败状态（#error-message 文案 + #retry-button 重试），
       不显示离线提示、不用快照数据掩盖真实加载失败。 */
  function load() {
    setPhase('loading');
    var offline = isOfflineProtocol();
    readAll(offline).then(function (r) {
      buildModel(r.groups);
      setSiteConfig(r.index);
      state.offline = offline;
      state.loaded = true;
      state.error = null;
      setOfflineNote(offline);
      setPhase('ready');
      syncDrawerFromUrl();          /* 深链 ?board=&entry= 直开（§4.7.1）：先开抽屉，再渲染列表 */
      applyView();
    }).catch(function (err) {
      state.loaded = false;
      state.error = err;
      setOfflineNote(false);
      setPhase('error', err);
    });
  }

  /* ============================================================
   * 5. 数据模型：状态字段解析与「公司整体」推导
   * ============================================================ */

  /* 公司板块：按岗位级「双休」聚合推导（前端推导，非原始字段，不写回数据）
   *   同时存在 是 与 否 → 部分双休；只有 是 → 全部双休；只有 否 → 无双休；否则 未知 */
  function deriveStatus(entry) {
    var hasYes = false;
    var hasNo = false;
    var jobs = entry[JOB_KEYS[0]];
    if (Array.isArray(jobs)) {
      for (var i = 0; i < jobs.length; i++) {
        var job = jobs[i];
        if (!isObject(job)) { continue; }
        for (var k = 0; k < JOB_STATUS_KEYS.length; k++) {
          var key = JOB_STATUS_KEYS[k];
          if (!hasOwn(job, key)) { continue; }
          var token = scalarText(job[key]);
          if (token === '是' || token === '双休' || token === '全部双休') { hasYes = true; }
          else if (token === '否' || token === '单休' || token === '无双休') { hasNo = true; }
          break;
        }
      }
    }
    if (hasYes && hasNo) { return '部分双休'; }
    if (hasYes) { return '全部双休'; }
    if (hasNo) { return '无双休'; }
    return '未知';
  }

  /* 通用兜底：挑一个「取值几乎全落在状态令牌集」的顶层标量键 */
  function discoverTokenKey(entries) {
    if (!entries.length) { return null; }
    var keyCount = {};
    var tokenCount = {};
    entries.forEach(function (entry) {
      Object.keys(entry).forEach(function (key) {
        if (!isScalar(entry[key])) { return; }
        var text = scalarText(entry[key]);
        if (text === '') { return; }
        keyCount[key] = (keyCount[key] || 0) + 1;
        if (hasOwn(TOKEN_VARIANT, text)) { tokenCount[key] = (tokenCount[key] || 0) + 1; }
      });
    });
    var best = null;
    Object.keys(keyCount).forEach(function (key) {
      var total = keyCount[key];
      var tokens = tokenCount[key] || 0;
      if (total >= Math.max(1, Math.ceil(entries.length * 0.5)) && tokens === total) { best = best || key; }
    });
    return best;
  }

  function discoverCategoryKey(entries, exclude) {
    if (!entries.length) { return null; }
    var stats = {};
    entries.forEach(function (entry) {
      Object.keys(entry).forEach(function (key) {
        if (exclude.indexOf(key) >= 0) { return; }
        if (!isScalar(entry[key])) { return; }
        var text = scalarText(entry[key]);
        if (text === '') { return; }
        stats[key] = stats[key] || { total: 0, values: {} };
        stats[key].total++;
        stats[key].values[text] = true;
      });
    });
    var best = null;
    Object.keys(stats).forEach(function (key) {
      var s = stats[key];
      var distinct = Object.keys(s.values).length;
      if (s.total >= entries.length && distinct >= 2 && distinct <= 40 && !best) { best = key; }
    });
    return best;
  }

  function resolveStatus(board) {
    var m = model[board];
    var explicit = presentKeyOf(m.entries, STATUS_KEYS);
    if (explicit) {
      m.derived = false;
      m.statusKey = explicit;
      m.statusLabel = explicit;
      m.statusOf = function (entry) { return scalarText(entry[explicit]); };
      return;
    }
    if (board === 'companies') {                 // 公司：必须常驻状态筛选（由岗位级双休聚合推导）
      m.derived = true;
      m.statusKey = null;
      m.statusLabel = '公司整体（推导）';
      m.statusOf = deriveStatus;
      return;
    }
    var guess = discoverTokenKey(m.entries);     // 学校：退化为数据里的令牌键
    m.derived = false;
    m.statusKey = guess;
    m.statusLabel = guess || '';
    m.statusOf = guess ? function (entry) { return scalarText(entry[guess]); } : function () { return ''; };
  }

  function buildModel(groups) {
    ['companies', 'schools'].forEach(function (board) {
      var m = model[board];
      m.entries = (groups[board] || []).filter(isObject);
      m.total = m.entries.length;
      resolveStatus(board);
      var exclude = TITLE_KEYS.concat(STATUS_KEYS, JOB_KEYS, REVIEW_KEYS, ['补课情况']);
      m.categoryBadgeKey = presentKeyOf(m.entries, CATEGORY_KEYS);       /* 约定键：类别作标签 */
      m.categoryKey = m.categoryBadgeKey || discoverCategoryKey(m.entries, exclude);
      /* 状态取值清单（当前数据实际出现者）+ 稳定排序次序 */
      var values = [];
      m.entries.forEach(function (entry) { uniquePush(values, m.statusOf(entry)); });
      if (m.derived) {
        values.sort(function (a, b) { return DERIVED_ORDER.indexOf(a) - DERIVED_ORDER.indexOf(b); });
      } else if (m.categoryKey) {
        values.sort();
      } else {
        values.sort(function (a, b) { return a.localeCompare(b, 'zh-Hans-CN'); });
      }
      m.statusValues = values;
      m.statusRank = {};
      values.forEach(function (v, i) { m.statusRank[v] = i; });
    });
    model.comments = (groups.comments || []).filter(isObject);   /* §3.9 评论（与条目解耦） */
    model.site = model.site || { repo: '', issueLabel: '' };
  }

  /* site 配置来自 index.json（§3.1）：repo 仍是占位符时禁用提交 */
  function setSiteConfig(index) {
    var site = isObject(index) && isObject(index.site) ? index.site : {};
    model.site = {
      repo: typeof site.repo === 'string' ? site.repo : '',
      issueLabel: typeof site.issueLabel === 'string' ? site.issueLabel : ''
    };
  }

  function repoConfigured() {
    var repo = model.site.repo;
    return typeof repo === 'string' && repo !== '' && repo !== REPO_PLACEHOLDER && repo.indexOf('/') > 0;
  }

  /* ============================================================
   * 6. 过滤与排序（F6 / F7 / F8）
   * ============================================================ */

  function nameOf(entry) {
    var key = firstKeyOf(entry, TITLE_KEYS);
    if (key) { return scalarText(entry[key]); }
    var keys = Object.keys(entry);
    for (var i = 0; i < keys.length; i++) {
      if (isScalar(entry[keys[i]]) && scalarText(entry[keys[i]]) !== '') { return scalarText(entry[keys[i]]); }
    }
    return '未命名';
  }

  function haystackOf(entry) {
    return collectScalars(entry, []).join('\u0001').toLowerCase();
  }

  function matchesQuery(entry) {
    if (!state.q) { return true; }
    return haystackOf(entry).indexOf(state.q) >= 0;
  }

  function matchesStatus(entry) {
    if (!state.status) { return true; }
    return model[state.board].statusOf(entry) === state.status;
  }

  function compareName(a, b) {
    return nameOf(a).localeCompare(nameOf(b), 'zh-Hans-CN', { numeric: true, sensitivity: 'base' });
  }

  function sortedEntries(list) {
    var m = model[state.board];
    var order = new Map();
    m.entries.forEach(function (entry, i) { order.set(entry, i); });
    function original(a, b) { return (order.get(a) || 0) - (order.get(b) || 0); }
    var arr = list.slice();
    if (state.sort === 'name') {
      arr.sort(function (a, b) { return compareName(a, b) || original(a, b); });
    } else if (state.sort === 'status') {
      arr.sort(function (a, b) {
        var ra = m.statusRank[m.statusOf(a)];
        var rb = m.statusRank[m.statusOf(b)];
        return (ra - rb) || compareName(a, b) || original(a, b);
      });
    } else if (state.sort === 'category') {
      var key = m.categoryKey;
      arr.sort(function (a, b) {
        var va = key ? scalarText(a[key]) : '';
        var vb = key ? scalarText(b[key]) : '';
        return va.localeCompare(vb, 'zh-Hans-CN') || compareName(a, b) || original(a, b);
      });
    } else {
      arr.sort(original);
    }
    return arr;
  }

  /* ============================================================
   * 7. 按键渲染（SPEC §4.2）
   * ============================================================ */

  function badgeForToken(key, value) {
    var token = scalarText(value);
    var variant = hasOwn(TOKEN_VARIANT, token) ? TOKEN_VARIANT[token] : null;
    return badge(key ? key + '：' + token : token, variant, variant ? VARIANT_GLYPH[variant] : token.slice(0, 1));
  }

  /* 键值行的值：一律「键: 值」纯文本（SPEC §4.2）；徽标只出现在徽标区，避免重复键名 */
  function scalarNode(value) {
    if (value === null || typeof value === 'undefined') {
      return el('span', 'kv-row__value--muted', '未知');
    }
    return document.createTextNode(scalarText(value));
  }

  /* 通用对象渲染：每个键 → 一个键值行或一个分组 */
  function renderObject(container, obj, depth, skipKeys) {
    var keys = Object.keys(obj);
    var rows = el('div', 'kv-body');
    var hasRow = false;
    var remarks = [];
    for (var i = 0; i < keys.length; i++) {
      var key = keys[i];
      if (skipKeys && skipKeys.indexOf(key) >= 0) { continue; }
      var value = obj[key];
      if (REMARK_KEYS.indexOf(key) >= 0 && typeof value === 'string') {   /* 备注（任意层级）：引用样式段落 */
        remarks.push({ key: key, text: value });
        continue;
      }
      if (isScalar(value)) {
        rows.appendChild(row(key, scalarNode(value), value === null));
        hasRow = true;
      } else {
        container.appendChild(renderSection(key, value, depth + 1));
      }
    }
    if (hasRow) { container.appendChild(rows); }
    for (var r = 0; r < remarks.length; r++) { renderRemark(container, remarks[r].key, remarks[r].text); }
  }

  /* 数组 → 条目列表；空数组 → 该层空状态 */
  function renderArraySection(title, arr, depth) {
    var section = el('div', 'kv-section');
    section.appendChild(el('p', 'kv-section__title', title));
    var list = el('ul', 'kv-list kv-section__list');
    if (arr.length === 0) {
      list.appendChild(emptyNote('li'));
      section.appendChild(list);
      return section;
    }
    for (var i = 0; i < arr.length; i++) {
      var item = arr[i];
      var li = el('li', 'kv-list__item');
      if (isObject(item)) {
        renderObject(li, item, depth + 1);
      } else if (Array.isArray(item)) {
        renderSectionInto(li, String(i + 1), item, depth + 1);
      } else {
        li.appendChild(el('span', 'kv-row__value', scalarText(item)));
      }
      list.appendChild(li);
    }
    section.appendChild(list);
    return section;
  }

  function renderSection(title, value, depth) {
    var section = el('div', 'kv-section');
    if (Array.isArray(value)) { return renderArraySection(title, value, depth); }
    section.appendChild(el('p', 'kv-section__title', title));
    var list = el('div', 'kv-section__list');
    var item = el('div', 'kv-list__item');
    if (Object.keys(value).length === 0) {
      item.appendChild(emptyNote('p'));
    } else {
      renderObject(item, value, depth + 1);
    }
    list.appendChild(item);
    section.appendChild(list);
    return section;
  }

  function renderSectionInto(container, title, value, depth) {
    container.appendChild(renderSection(title, value, depth));
  }

  /* 岗位子表：兼容 {岗位名称,岗位情况} 与 {岗位名:{双休:是,...}} 两种形态，以及标量值 */
  function firstNestedKey(obj) {
    var keys = Object.keys(obj);
    for (var i = 0; i < keys.length; i++) {
      if (!isScalar(obj[keys[i]])) { return keys[i]; }
    }
    return null;
  }

  function renderJobs(container, jobs) {
    var section = el('div', 'kv-section');
    section.appendChild(el('p', 'kv-section__title', JOB_KEYS[0]));
    var list = el('div', 'kv-section__list');
    if (jobs.length === 0) {
      list.appendChild(emptyNote('p'));
      section.appendChild(list);
      container.appendChild(section);
      return;
    }
    for (var i = 0; i < jobs.length; i++) {
      var job = jobs[i];
      var block = el('div', 'kv-block');
      var head = el('div', 'kv-block__head');
      var badges = el('span', 'kv-block__badges');
      var body = el('div', 'kv-block__body');
      var title = '';
      var inner = null;
      var content = null;
      if (isObject(job)) {
        var keys = Object.keys(job);
        var skip = [];
        var nameKey = firstKeyOf(job, ['岗位名称']);
        if (nameKey) {
          title = scalarText(job[nameKey]);
          skip.push(nameKey);
          content = job;
        } else {
          inner = firstNestedKey(job);              /* 嵌套形态：{岗位名:{双休:是,...}} */
          if (inner) {
            title = inner;
            skip.push(inner);
            content = job[inner];
            if (Array.isArray(content)) { renderSectionInto(body, '内容', content, 1); content = null; }
          } else {
            title = keys.length ? keys[0] : '岗位';   /* 形如 {管理层: 未知} */
            content = job;
          }
        }
        if (content) {
          Object.keys(content).forEach(function (key) {   /* 令牌标量 → 徽标，正文不再重复 */
            var value = content[key];
            if (isScalar(value) && hasOwn(TOKEN_VARIANT, scalarText(value))) {
              badges.appendChild(badgeForToken(key, value));
              skip.push(key);
            }
          });
          renderObject(body, content, 1, skip);
        }
      } else if (Array.isArray(job)) {
        title = '岗位';
        renderSectionInto(body, '内容', job, 1);
      } else {
        title = scalarText(job);
      }
      head.appendChild(el('span', 'kv-block__title', String(title)));
      if (badges.childNodes.length) { head.appendChild(badges); }
      block.appendChild(head);
      if (body.childNodes.length) { block.appendChild(body); }
      list.appendChild(block);
    }
    section.appendChild(list);
    container.appendChild(section);
  }

  /* 备注：引用样式段落（承载负面舆情），带「备注」文字标签（SPEC §4.2） */
  function renderRemark(container, key, text) {
    var quote = el('div', 'quote');
    quote.appendChild(el('span', 'quote__no', key));
    quote.appendChild(el('span', 'quote__text', text));
    container.appendChild(quote);
  }

  /* 评价子表：引用样式（编号 + 内容） */
  function renderReviews(container, reviews) {
    var section = el('div', 'kv-section');
    section.appendChild(el('p', 'kv-section__title', REVIEW_KEYS[0]));
    var list = el('div', 'quote-list');
    if (reviews.length === 0) {
      list.appendChild(emptyNote('p'));
      section.appendChild(list);
      container.appendChild(section);
      return;
    }
    for (var i = 0; i < reviews.length; i++) {
      var item = reviews[i];
      var quote = el('div', 'quote');
      var no = (isObject(item) && hasOwn(item, '编号')) ? scalarText(item['编号']) : String(i + 1);
      quote.appendChild(el('span', 'quote__no', String(no)));
      var textNode = el('span', 'quote__text');
      if (isObject(item) && typeof item['内容'] === 'string') {
        textNode.textContent = item['内容'];
      } else if (isScalar(item)) {
        textNode.textContent = scalarText(item);
      } else if (isObject(item)) {
        collectScalars(item, []).forEach(function (part) { textNode.appendChild(document.createTextNode(part)); });
      }
      quote.appendChild(textNode);
      list.appendChild(quote);
    }
    section.appendChild(list);
    container.appendChild(section);
  }

  /* 整体状态灯（企业卡片）：颜色 + 字形 + 可见文字三重区分 */
  function buildLight(statusText, variant) {
    var light = el('span', 'entry__light entry__light--' + (LIGHT_VARIANT[variant] || 'unknown'));
    var glyph = el('span', 'entry__light__glyph', VARIANT_GLYPH[variant] || '?');
    glyph.setAttribute('aria-hidden', 'true');
    light.appendChild(glyph);
    light.appendChild(el('span', 'entry__light__text', '整体：' + statusText));
    light.title = '公司级推导状态（按岗位汇总，未知岗位未计入推导）：' + statusText + '；颜色仅作辅助，以文字为准';
    return light;
  }

  /* 气泡组（学校卡片）：按键渲染短标量字段 → li.bubble「键：值」
     规则：标量、非空、长度 <= BUBBLE_MAX_CHARS；备注/岗位/评价与长文本一律进详情区。 */
  function buildBubbles(entry) {
    var keys = Object.keys(entry);
    var items = [];
    for (var i = 0; i < keys.length; i++) {
      var key = keys[i];
      if (TITLE_KEYS.indexOf(key) >= 0) { continue; }
      if (REMARK_KEYS.indexOf(key) >= 0 || JOB_KEYS.indexOf(key) >= 0 || REVIEW_KEYS.indexOf(key) >= 0) { continue; }
      var value = entry[key];
      if (!isScalar(value)) { continue; }
      var text = scalarText(value);
      if (text === '' || text.length > BUBBLE_MAX_CHARS) { continue; }
      items.push({ key: key, text: text });
    }
    if (!items.length) { return null; }              /* 无可做气泡的键 → 不渲染气泡组 */
    var list = el('ul', 'bubbles');
    for (var j = 0; j < items.length; j++) {
      var item = items[j];
      var itemVariant = hasOwn(TOKEN_VARIANT, item.text) ? TOKEN_VARIANT[item.text] : null;
      var li = el('li', 'bubble' + (itemVariant ? ' bubble--' + itemVariant : ''));
      li.appendChild(el('span', 'bubble__key', item.key + '：'));
      li.appendChild(el('span', 'bubble__value', item.text));
      list.appendChild(li);
    }
    return list;
  }

  /* 条目完整按键渲染内容（SPEC §4.2 / §4.7.2 第 1 条）：抽屉正文与详情共用 */
  function buildEntryDetail(entry, board) {
    var m = model[board];
    var statusText = m.statusOf(entry);
    var hasStatusField = m.derived || !!(m.statusKey && hasOwn(entry, m.statusKey));
    if (hasStatusField && statusText === '') { statusText = '未知'; }
    var variant = statusText ? (hasOwn(TOKEN_VARIANT, statusText) ? TOKEN_VARIANT[statusText] : 'unknown') : null;
    var fragment = document.createDocumentFragment();

    var badges = el('div', 'entry__badges');
    if (statusText && m.derived) {                      /* 公司级推导徽标（标注推导 + 未知岗位未计入） */
      badges.appendChild(badge('按岗位汇总：' + statusText + '（未知岗位未计入）', variant, variant ? VARIANT_GLYPH[variant] : '',
        '公司级推导值（SPEC §4.6）：由该公司岗位级「双休」聚合得出，未知岗位未计入推导；非原始字段，不写回数据'));
    }
    if (m.categoryBadgeKey && isScalar(entry[m.categoryBadgeKey])) {     /* 约定键：类别作标签 */
      var categoryText = scalarText(entry[m.categoryBadgeKey]);
      if (categoryText !== '') { badges.appendChild(badge(categoryText, null, null, m.categoryBadgeKey)); }
    }
    if (badges.childNodes.length) { fragment.appendChild(badges); }

    var body = el('div', 'kv-body');
    var scalarRows = el('div', 'kv-body');
    var hasScalarRow = false;
    var keys = Object.keys(entry);
    for (var i = 0; i < keys.length; i++) {
      var key = keys[i];
      var value = entry[key];
      if (TITLE_KEYS.indexOf(key) >= 0) { continue; }
      if (REMARK_KEYS.indexOf(key) >= 0 && typeof value === 'string') {     /* 备注：引用样式段落 */
        renderRemark(fragment, key, value);
        continue;
      }
      if (JOB_KEYS.indexOf(key) >= 0 && Array.isArray(value)) { renderJobs(body, value); continue; }
      if (REVIEW_KEYS.indexOf(key) >= 0 && Array.isArray(value)) { renderReviews(body, value); continue; }
      if (isScalar(value)) {
        scalarRows.appendChild(row(key, scalarNode(value), value === null));
        hasScalarRow = true;
      } else {
        renderSectionInto(body, key, value, 1);
      }
    }
    if (hasScalarRow) { fragment.appendChild(scalarRows); }
    if (body.childNodes.length) { fragment.appendChild(body); }
    return fragment;
  }

  /* 单个条目卡：默认折叠（名称 + 整体状态灯/气泡 + 更多按钮）；
     详情不再内联展开，点击触发控件打开全屏抽屉（SPEC §4.7.1）。 */
  function buildEntry(entry, board) {
    var m = model[board];
    var statusText = m.statusOf(entry);
    /* data-status 与状态灯取值的兜底规则：
       - 命中映射表 → 对应变体；
       - 条目带状态字段但取值未收录（含空串）→ unknown（仍有颜色 + 字形 + 文字）；
       - 完全无状态字段 → 不写 data-status、不渲染状态灯。 */
    var hasStatusField = m.derived || !!(m.statusKey && hasOwn(entry, m.statusKey));
    if (hasStatusField && statusText === '') { statusText = '未知'; }
    var variant = null;
    if (statusText) { variant = hasOwn(TOKEN_VARIANT, statusText) ? TOKEN_VARIANT[statusText] : 'unknown'; }

    var name = nameOf(entry);
    var card = el('article', 'entry ' + BOARDS[board].entryClass + ' entry--collapsed');
    card.setAttribute('data-entry', name);              /* SPEC §4.1 冻结属性（V13） */
    if (variant) { card.setAttribute('data-status', variant); }

    var head = el('div', 'entry__head');
    head.appendChild(el('h3', 'entry__title', name));
    if (board === 'companies' && statusText) {          /* 企业：整体状态灯（绿/黄/红/灰 + 文字） */
      head.appendChild(buildLight(statusText, variant));
    }
    if (board === 'schools') {                          /* 学校：短标量气泡组 */
      var bubbles = buildBubbles(entry);
      if (bubbles) { head.appendChild(bubbles); }
    }
    var actions = el('div', 'entry__actions');
    var more = el('button', 'entry__more', board === 'schools' ? '更多' : '更多信息');
    more.type = 'button';
    more.setAttribute('data-more', name);               /* SPEC §4.1 冻结属性（V13） */
    more.setAttribute('aria-expanded', 'false');
    more.setAttribute('aria-controls', 'drawer');
    actions.appendChild(more);
    head.appendChild(actions);
    card.appendChild(head);
    return card;
  }

  /* 按名称定位条目（抽屉用）：公司/学校两个板块各自查找 */
  function entryByName(board, name) {
    var m = model[board];
    if (!m) { return null; }
    for (var i = 0; i < m.entries.length; i++) {
      if (nameOf(m.entries[i]) === name) { return m.entries[i]; }
    }
    return null;
  }

  /* ============================================================
   * 7b. 详情抽屉与评论（SPEC v1.3 §4.7 / F13 / F14）
   * ============================================================ */

  function drawerEl() {
    /* 冻结 id 优先；BEM 类仅作兜底查询（FE1 结构：.detail-overlay#drawer） */
    return byId('drawer') || document.querySelector('.detail-overlay');
  }

  /* 抽屉正文 = 条目完整按键渲染内容 */
  function renderDrawerBody(board, entry) {
    var host = byId('drawer-body');
    if (!host) { return; }
    host.textContent = '';
    host.appendChild(buildEntryDetail(entry, board));
  }

  /* 评论列表：板块 + 目标 同时匹配，按 编号 升序（§3.9 / §4.7.2 第 2 条） */
  function commentsFor(board, name) {
    var boardKey = BOARDS[board] ? BOARDS[board].dataKey : '';
    return model.comments.filter(function (c) {
      return c[COMMENT_BOARD_KEY] === boardKey && c[COMMENT_TARGET_KEY] === name;
    }).sort(function (a, b) {
      return (Number(a[COMMENT_NO_KEY]) || 0) - (Number(b[COMMENT_NO_KEY]) || 0);
    });
  }

  function renderComments(board, name) {
    var list = byId('comment-list');
    var empty = document.querySelector('.comment-empty');
    if (!list) { return; }
    list.textContent = '';
    var rows = commentsFor(board, name);
    rows.forEach(function (c) {
      var item = el('li', 'comment');
      var head = el('div', 'comment__head');
      if (c[COMMENT_NO_KEY] !== null && typeof c[COMMENT_NO_KEY] !== 'undefined') {
        head.appendChild(el('span', 'comment__no', '#' + scalarText(c[COMMENT_NO_KEY])));
      }
      var nick = typeof c[COMMENT_NICK_KEY] === 'string' && c[COMMENT_NICK_KEY].trim() !== ''
        ? c[COMMENT_NICK_KEY].trim() : '匿名';
      head.appendChild(el('span', 'comment__nick', nick));
      if (typeof c[COMMENT_DATE_KEY] === 'string' && c[COMMENT_DATE_KEY] !== '') {
        head.appendChild(el('span', 'comment__date', c[COMMENT_DATE_KEY]));
      }
      item.appendChild(head);
      /* 纯文本渲染（防 XSS）：只用 textContent，绝不 innerHTML */
      item.appendChild(el('div', 'comment__text', typeof c[COMMENT_TEXT_KEY] === 'string' ? c[COMMENT_TEXT_KEY] : ''));
      list.appendChild(item);
    });
    if (empty) { empty.hidden = rows.length > 0; }
  }

  /* 提交按钮/提示：site.repo 未配置（占位 OWNER/REPO）时禁用并提示 */
  function updateCommentFormState() {
    var submit = byId('comment-submit');
    var notice = byId('comment-notice');
    var ready = repoConfigured();
    if (submit) { submit.disabled = !ready; }
    if (notice) {
      if (notice.classList) { notice.classList.toggle('comment-form__hint--warn', !ready); }
      notice.textContent = ready
        ? '提交会打开 GitHub 新建 Issue 页面（需登录 GitHub 账号）；提交后由仓库的 GitHub Action 自动写入数据文件。'
        : '需先在 data/index.json 的 site.repo 填写你的仓库';
    }
  }

  function commentContentValue() {
    var area = byId('comment-content');
    return area && typeof area.value === 'string' ? area.value : '';
  }

  function commentNicknameValue() {
    var input = byId('comment-nickname');
    return input && typeof input.value === 'string' ? input.value.trim() : '';
  }

  function setCommentNotice(message, warn) {
    var notice = byId('comment-notice');
    if (!notice) { return; }
    if (notice.classList) { notice.classList.toggle('comment-form__hint--warn', !!warn); }
    notice.textContent = message;
  }

  /* 表单校验（§4.7.4）：内容必填、≤1000 字、repo 必须已配置 */
  function validateComment() {
    var text = commentContentValue().trim();
    if (text === '') { return { ok: false, message: '请填写评论内容（不能为空）' }; }
    if (text.length > COMMENT_MAX_CHARS) {
      return { ok: false, message: '评论内容过长（最多 ' + COMMENT_MAX_CHARS + ' 字，当前 ' + text.length + ' 字）' };
    }
    if (!repoConfigured()) { return { ok: false, message: '需先在 data/index.json 的 site.repo 填写你的仓库' }; }
    return { ok: true, text: text };
  }

  /* 预填 GitHub Issue 链接（§4.7.4 模板逐字冻结） */
  function buildIssueUrl(board, name, text, nickname) {
    var boardLabel = BOARDS[board] ? BOARDS[board].label : board;
    var title = '[评论] ' + boardLabel + '：' + name;         /* 全角冒号 */
    var body = COMMENT_MARKER + '\n'
      + '- 板块: ' + boardLabel + '\n'
      + '- 目标: ' + name + '\n'
      + '- 昵称: ' + (nickname || '匿名') + '\n'
      + '- 内容: ' + text;
    var label = model.site.issueLabel || 'comment-submission';
    return 'https://github.com/' + model.site.repo + '/issues/new'
      + '?title=' + encodeURIComponent(title)
      + '&body=' + encodeURIComponent(body)
      + '&labels=' + encodeURIComponent(label);
  }

  function submitComment() {
    var check = validateComment();
    if (!check.ok) { setCommentNotice(check.message, true); return null; }
    var url = buildIssueUrl(drawerState.board, drawerState.name, check.text, commentNicknameValue());
    setCommentNotice('已打开 GitHub 新建 Issue 页面（需登录 GitHub 账号）；提交后由仓库的 GitHub Action 自动写入数据文件。', false);
    try { window.open(url, '_blank'); } catch (e) { /* 弹窗被拦截时保留链接文案 */ }
    return url;
  }

  /* 深链/关闭时的轻量提示（不新增冻结 id，动态创建 role=status 段落） */
  function showEntryNotice(message) {
    var host = byId('state-region') || document.querySelector('.results') || document.body;
    var node = byId('entry-notice');
    if (!node) {
      node = el('p', 'comment-empty', '');
      node.id = 'entry-notice';
      node.setAttribute('role', 'status');
      host.insertBefore(node, host.firstChild);
    }
    node.textContent = message;
    node.hidden = false;
    window.clearTimeout(entryNoticeTimer);
    entryNoticeTimer = window.setTimeout(function () { node.hidden = true; }, 6000);
  }

  /* 焦点陷阱：Tab / Shift+Tab 只在抽屉内循环（§4.7.3） */
  function focusablesInDrawer() {
    var root = byId('drawer-panel') || drawerEl();
    if (!root) { return []; }
    var nodes = root.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
    return Array.prototype.filter.call(nodes, function (node) {
      if (node.disabled) { return false; }
      if (node.getAttribute && node.getAttribute('aria-hidden') === 'true') { return false; }
      return node.getClientRects().length > 0;
    });
  }

  function trapDrawerTab(event) {
    if (!drawerState.open || event.key !== 'Tab') { return; }
    var items = focusablesInDrawer();
    if (!items.length) { return; }
    var first = items[0];
    var last = items[items.length - 1];
    var active = document.activeElement;
    if (event.shiftKey) {
      if (active === first || items.indexOf(active) < 0) { event.preventDefault(); last.focus(); }
    } else if (active === last || items.indexOf(active) < 0) {
      event.preventDefault();
      first.focus();
    }
  }

  /* 背景 inert / aria-hidden：覆盖 body 全部子元素（含 .skip-link），只留抽屉 */
  function setBackgroundInert(on) {
    if (!document.body) { return; }
    var drawer = drawerEl();
    var kids = document.body.children;
    for (var i = 0; i < kids.length; i++) {
      var node = kids[i];
      if (node === drawer || node.tagName === 'SCRIPT') { continue; }
      if (on) {
        node.setAttribute('aria-hidden', 'true');
        if ('inert' in node) { node.inert = true; }
      } else {
        node.removeAttribute('aria-hidden');
        if ('inert' in node) { node.inert = false; }
      }
    }
  }

  function triggerOf(name) {
    var nodes = document.querySelectorAll('[data-more]');
    for (var i = 0; i < nodes.length; i++) {
      if (nodes[i].getAttribute('data-more') === name) { return nodes[i]; }
    }
    return null;
  }

  function setTriggerExpanded(button, expanded) {
    if (button && button.setAttribute) { button.setAttribute('aria-expanded', expanded ? 'true' : 'false'); }
  }

  function openDrawer(board, name, trigger, opts) {
    var entry = entryByName(board, name);
    if (!entry) { showEntryNotice('未找到条目「' + name + '」，无法打开详情'); return false; }
    var drawer = drawerEl();
    if (!drawer) { return false; }
    var previous = drawerState.open ? triggerOf(drawerState.name) : null;
    if (previous) { setTriggerExpanded(previous, false); }
    drawerState.open = true;
    drawerState.board = board;
    drawerState.name = name;
    drawerState.trigger = trigger || triggerOf(name) || null;
    state.deepEntry = '';                    /* 深链参数已消费 */
    renderDrawerBody(board, entry);
    renderComments(board, name);
    updateCommentFormState();
    var title = byId('drawer-title');
    if (title) { title.textContent = name + ' · 详情'; }
    drawer.hidden = false;
    if (document.body && document.body.classList) { document.body.classList.add('is-detail-open'); }
    setBackgroundInert(true);
    setTriggerExpanded(drawerState.trigger, true);
    var closeButton = byId('drawer-close');
    if (closeButton && closeButton.focus) { closeButton.focus(); }
    if (!opts || !opts.silentUrl) { pushDrawerUrl(true); }
    return true;
  }

  function closeDrawer(opts) {
    if (!drawerState.open) { return; }
    var trigger = drawerState.trigger;
    var drawer = drawerEl();
    drawerState.open = false;
    drawerState.board = null;
    drawerState.name = null;
    drawerState.trigger = null;
    state.deepEntry = '';
    if (drawer) { drawer.hidden = true; }
    if (document.body && document.body.classList) { document.body.classList.remove('is-detail-open'); }
    setBackgroundInert(false);
    setTriggerExpanded(trigger, false);
    if (!opts || !opts.silentUrl) { pushDrawerUrl(false); }
    if (opts && opts.noFocus) { return; }
    if (trigger && trigger.focus) { trigger.focus(); }
    else if (dom.search && dom.search.focus) { dom.search.focus(); }
  }

  /* 深链同步：?board=<板块>&entry=<名称>（§4.7.1） */
  function syncDrawerFromUrl() {
    var params = null;
    try { params = new URLSearchParams(location.search); } catch (e) { params = null; }
    var name = state.deepEntry || (params ? (params.get('entry') || '') : '');
    if (name) {
      if (drawerState.open && drawerState.name === name) { return; }
      var opened = openDrawer(state.board, name, null, { silentUrl: true });
      if (!opened) { state.deepEntry = ''; pushDrawerUrl(false); }
      return;
    }
    if (drawerState.open) { closeDrawer({ silentUrl: true, noFocus: true }); }
  }

  /* ============================================================
   * 8. 视图刷新（计数 / 筛选控件 / 列表 / 空状态）
   * ============================================================ */

  function setPhase(phase, err) {
    var loading = byId('state-loading');
    var empty = byId('state-empty');
    var error = byId('state-error');
    if (loading) { loading.hidden = phase !== 'loading'; }
    if (empty) { empty.hidden = phase !== 'empty'; }
    if (error) { error.hidden = phase !== 'error'; }
    if (error && phase === 'error' && err) {
      var msg = byId('error-message');
      if (msg) { msg.textContent = '数据加载失败：' + (err.message || String(err)); }
    }
    if (phase === 'error') {                 /* 加载失败时数据不可用，筛选控件一并收起 */
      var filter = byId('status-filter');
      if (filter) { filter.hidden = true; }
    }
    if (dom.boardRoot) {
      dom.boardRoot.setAttribute('aria-busy', phase === 'loading' ? 'true' : 'false');
      if (phase === 'loading' || phase === 'error') { dom.boardRoot.textContent = ''; }
    }
  }

  function setOfflineNote(show) {
    var note = byId('offline-note');
    if (note) { note.hidden = !show; }
  }

  function syncBoardButtons() {
    if (!dom.boardSwitch) { return; }
    var buttons = dom.boardSwitch.querySelectorAll('[data-board]');
    for (var i = 0; i < buttons.length; i++) {
      var btn = buttons[i];
      var active = btn.getAttribute('data-board') === state.board;
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
      if (active) { btn.setAttribute('aria-current', 'true'); } else { btn.removeAttribute('aria-current'); }
      btn.title = active ? '当前板块：' + BOARDS[state.board].label : '切换到' + (btn.getAttribute('data-board') === 'companies' ? '公司' : '学校') + '板块';
    }
  }

  function syncStatusFilter() {
    var m = model[state.board];
    var wrap = byId('status-filter');
    var select = byId('status-filter-select');
    if (!wrap || !select) { return; }
    var show = state.board === 'companies' ? true : (m.statusValues.length > 0);
    wrap.hidden = !show;
    while (select.options.length > 1) { select.remove(1); }
    m.statusValues.forEach(function (value) {
      var option = document.createElement('option');
      option.value = value;
      option.textContent = value;
      select.appendChild(option);
    });
    if (state.status && m.statusValues.indexOf(state.status) < 0) { state.status = ''; }
    select.value = state.status;
  }

  function syncSortSelect() {
    var select = byId('sort-select');
    if (!select) { return; }
    var m = model[state.board];
    var options = [
      { value: 'name', label: '名称' },
      { value: 'status', label: m.derived ? '状态（公司整体 · 推导）' : (m.statusLabel ? '状态（' + m.statusLabel + '）' : '状态') }
    ];
    if (state.board === 'companies' && m.categoryKey) { options.push({ value: 'category', label: m.categoryKey }); }
    while (select.options.length > 1) { select.remove(1); }
    options.forEach(function (opt) {
      var option = document.createElement('option');
      option.value = opt.value;
      option.textContent = opt.label;
      select.appendChild(option);
    });
    var allowed = ['default'];
    for (var i = 1; i < select.options.length; i++) { allowed.push(select.options[i].value); }
    if (allowed.indexOf(state.sort) < 0) { state.sort = 'default'; }
    select.value = state.sort;
  }

  function syncSearchControls() {
    if (dom.search) { dom.search.value = state.rawQ; }              /* 回填原始输入，不改写用户键入 */
    if (dom.searchClear) { dom.searchClear.hidden = state.rawQ === ''; }
  }

  function renderList(list, total) {
    renderToken++;
    detailSeq = 0;                       /* 详情区 id 重新计数（重渲染后默认全部折叠） */
    var token = renderToken;
    if (dom.boardRoot) { dom.boardRoot.textContent = ''; }
    if (!list.length) {
      showEmpty(total);
      return;
    }
    if (byId('state-empty')) { byId('state-empty').hidden = true; }
    if (dom.boardRoot) { dom.boardRoot.setAttribute('aria-busy', 'true'); }
    var grid = el('div', 'card-grid');
    dom.boardRoot.appendChild(grid);
    var i = 0;
    function step() {
      if (token !== renderToken) { return; }
      var frag = document.createDocumentFragment();
      var end = Math.min(i + BATCH_SIZE, list.length);
      for (; i < end; i++) { frag.appendChild(buildEntry(list[i], state.board)); }
      grid.appendChild(frag);
      if (i < list.length) {
        window.setTimeout(step, 0);
      } else if (dom.boardRoot) {
        dom.boardRoot.setAttribute('aria-busy', 'false');
      }
    }
    step();
  }

  function showEmpty(total) {
    var empty = byId('state-empty');
    if (!empty) { return; }
    var hint = empty.querySelector('.state-panel__hint');
    var hasFilter = state.rawQ !== '' || state.status !== '';
    if (hint) {
      hint.textContent = total === 0
        ? '当前板块的数据为空（data/ 中该板块暂无条目）。可运行 python add_prog/editor.py 添加数据后再刷新。'
        : '没有匹配「' + (state.rawQ || '当前条件') + '」的条目，试试减少关键词或清除筛选。';
    }
    var clear = byId('empty-clear');
    if (clear) { clear.hidden = !hasFilter; }
    empty.hidden = false;
  }

  /* 计数口径（SPEC §4.1 F8/F9）：公司 X 家 / 岗位 Y 条（命中与总数都给） */
  function visibleJobCount(list) {
    var key = JOB_KEYS[0];
    var total = 0;
    for (var i = 0; i < list.length; i++) {
      var jobs = list[i][key];
      if (Array.isArray(jobs)) { total += jobs.length; }
    }
    return total;
  }

  function totalJobCount(board) {
    return visibleJobCount(model[board].entries);
  }

  function countText(hitCount, list) {
    var m = model[state.board];
    if (state.board === 'companies') {
      return '公司 ' + hitCount + ' 家 / 共 ' + m.total + ' 家 · 岗位 ' + visibleJobCount(list) + ' 条 / 共 ' + totalJobCount('companies') + ' 条';
    }
    return '学校 ' + hitCount + ' 所 / 共 ' + m.total + ' 所';
  }

  function applyView() {
    if (!state.loaded) { return; }
    var m = model[state.board];
    syncStatusFilter();
    syncSortSelect();
    syncSearchControls();
    syncBoardButtons();
    var filtered = m.entries.filter(matchesQuery).filter(matchesStatus);
    var list = sortedEntries(filtered);
    var count = byId('result-count');
    if (count) { count.textContent = countText(list.length, list); }
    setPhase(list.length ? 'ready' : 'empty');
    renderList(list, m.total);
    writeUrl();
  }

  /* ============================================================
   * 9. URL 状态（F10：q / board / sort / status 可分享可恢复）
   * ============================================================ */

  function readUrl() {
    var params = null;
    try { params = new URLSearchParams(location.search); } catch (e) { params = null; }
    if (!params) { return; }
    var board = params.get('board');
    if (board === 'companies' || board === 'schools') { state.board = board; }
    state.rawQ = (params.get('q') || '').trim();   /* 原始输入（保留大小写） */
    state.q = state.rawQ.toLowerCase();             /* 仅匹配时归一化 */
    var sort = params.get('sort');
    if (sort) { state.sort = sort; }
    state.status = params.get('status') || '';
    state.deepEntry = (params.get('entry') || '').trim();   /* 深链：抽屉目标条目名 */
  }

  /* 统一构造 URL：board / q / sort / status +（抽屉打开时）entry —— §4.7.1 */
  function buildUrl(withEntry) {
    var params = new URLSearchParams();
    if (state.board !== 'companies') { params.set('board', state.board); }
    if (state.rawQ) { params.set('q', state.rawQ); }   /* URL 保留原始大小写 */
    if (state.sort && state.sort !== 'default') { params.set('sort', state.sort); }
    if (state.status) { params.set('status', state.status); }
    var entryName = drawerState.name || state.deepEntry;      /* 深链未打开时也保留 entry 参数 */
    if (withEntry && entryName) { params.set('entry', entryName); }
    var query = params.toString();
    return location.pathname + (query ? '?' + query : '') + location.hash;
  }

  function writeUrl() {
    /* 筛选/排序/搜索变化时用 replaceState，并保留当前抽屉的 entry 参数 */
    var url = buildUrl(drawerState.open);
    try {
      if (history && history.replaceState) { history.replaceState(null, '', url); }
    } catch (e) { /* file:// 下部分浏览器禁止改写历史记录，忽略即可 */ }
  }

  /* 打开/关闭抽屉用 pushState：支持浏览器前进/后退（§4.7.1） */
  function pushDrawerUrl(withEntry) {
    var url = buildUrl(withEntry);
    try {
      if (history && history.pushState) { history.pushState(null, '', url); return; }
      if (history && history.replaceState) { history.replaceState(null, '', url); }
    } catch (e) { /* file:// 下部分浏览器禁止改写历史记录，忽略即可 */ }
  }

  /* ============================================================
   * 10. 事件绑定
   * ============================================================ */

  function bindEvents() {
    if (dom.boardSwitch) {
      dom.boardSwitch.addEventListener('click', function (event) {
        var btn = event.target.closest ? event.target.closest('[data-board]') : null;
        if (!btn) { return; }
        var next = btn.getAttribute('data-board');
        if (!hasOwn(BOARDS, next) || next === state.board) { return; }
        state.board = next;
        state.status = '';
        state.sort = 'default';
        applyView();
      });
    }

    /* 详情抽屉：事件委托一次绑定在 #board-root 上（重渲染不失效），不逐卡绑定。
       Enter/Space 由原生 button 触发 click，无需额外键盘分支。 */
    if (dom.boardRoot) {
      dom.boardRoot.addEventListener('click', function (event) {
        var button = event.target && event.target.closest ? event.target.closest('[data-more]') : null;
        if (!button || !dom.boardRoot.contains(button)) { return; }
        var name = button.getAttribute('data-more');
        if (!name) { return; }
        openDrawer(state.board, name, button);
      });
    }

    /* 抽屉：Esc / 点击遮罩 / #drawer-close 三种关闭方式 + Tab 焦点陷阱 */
    var drawer = drawerEl();
    if (drawer) {
      drawer.addEventListener('click', function (event) {
        var target = event.target;
        var closeBtn = target && target.closest ? target.closest('#drawer-close') : null;
        if (closeBtn) { closeDrawer(); return; }
        if (target === drawer) { closeDrawer(); }          /* 点击遮罩（overlay 本体） */
      });
      drawer.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' || event.key === 'Esc') { event.preventDefault(); closeDrawer(); return; }
        trapDrawerTab(event);
      });
    }
    document.addEventListener('keydown', function (event) {
      if (!drawerState.open) { return; }
      if (event.key === 'Escape' || event.key === 'Esc') { event.preventDefault(); closeDrawer(); return; }
      trapDrawerTab(event);
    });

    /* 写评论表单：校验 + 拼 Issue 链接 */
    var form = byId('comment-form');
    if (form) {
      form.addEventListener('submit', function (event) {
        event.preventDefault();
        submitComment();
      });
    }
    var contentArea = byId('comment-content');
    if (contentArea) {
      contentArea.addEventListener('input', function () {
        var check = validateComment();
        if (check.ok) { updateCommentFormState(); }
        else if (contentArea.value.trim() === '') { setCommentNotice('请填写评论内容（不能为空）', true); }
        else if (contentArea.value.trim().length > COMMENT_MAX_CHARS) { setCommentNotice('评论内容过长（最多 ' + COMMENT_MAX_CHARS + ' 字）', true); }
      });
    }

    if (dom.search) {
      dom.search.addEventListener('input', function () {
        window.clearTimeout(bindEvents.timer);
        bindEvents.timer = window.setTimeout(function () {
          state.rawQ = dom.search.value.trim();          /* 保留原始输入 */
          state.q = state.rawQ.toLowerCase();            /* 仅匹配归一化 */
          applyView();
        }, DEBOUNCE_MS);
      });
      dom.search.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' || event.key === 'Esc') {
          event.preventDefault();
          if (dom.searchClear) { dom.searchClear.click(); }
        }
      });
    }

    if (dom.searchClear) {
      dom.searchClear.addEventListener('click', function () {
        if (dom.search) { dom.search.value = ''; }
        state.rawQ = '';
        state.q = '';
        applyView();
        if (dom.search) { dom.search.focus(); }
      });
    }

    if (dom.sort) {
      dom.sort.addEventListener('change', function () {
        state.sort = dom.sort.value;
        applyView();
      });
    }

    var statusSelect = byId('status-filter-select');
    if (statusSelect) {
      statusSelect.addEventListener('change', function () {
        state.status = statusSelect.value;
        applyView();
      });
    }

    var emptyClear = byId('empty-clear');
    if (emptyClear) {
      emptyClear.addEventListener('click', function () {
        state.rawQ = '';
        state.q = '';
        state.status = '';
        applyView();
      });
    }

    var retry = byId('retry-button');
    if (retry) {
      retry.addEventListener('click', function () { load(); });
    }

    window.addEventListener('popstate', function () {
      readUrl();
      syncDrawerFromUrl();          /* 前进/后退：按 URL 打开或关闭抽屉（§4.7.1） */
      applyView();
    });
  }

  function cacheDom() {
    dom.boardSwitch = byId('board-switch');
    dom.search = byId('search-input');
    dom.searchClear = byId('search-clear');
    dom.sort = byId('sort-select');
    dom.boardRoot = byId('board-root');
  }

  function start() {
    cacheDom();
    if (!dom.boardRoot) { return; }        // DOM 契约缺失时不抛错、不白屏
    readUrl();
    bindEvents();
    syncBoardButtons();
    load();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
