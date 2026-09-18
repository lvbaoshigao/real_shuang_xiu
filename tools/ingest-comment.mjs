#!/usr/bin/env node
/**
 * tools/ingest-comment.mjs — 把一条「评论提交」Issue 落盘进 data/comments.json
 *
 * 契约：docs/SPEC.md v1.3 §3.9（评论数据）、§4.7.3（Issue 模板，逐字冻结）、§6 第 11 条（启用步骤）。
 * 纯 Node（ESM），零第三方依赖。
 *
 * 输入（命令行参数优先，其次环境变量）：
 *   --title  <文本> / ISSUE_TITLE     Issue 标题，形如「[评论] 公司：腾讯」
 *   --body   <文本> / ISSUE_BODY      Issue 正文
 *   --body-file <路径>                从文件读取正文（本地测试用）
 *   --author <用户名> / ISSUE_AUTHOR  Issue 作者（昵称回退值）
 *   --labels <逗号分隔> / ISSUE_LABELS Issue 标签
 *   --root   <路径> / REPO_ROOT       仓库根目录（默认 process.cwd()）
 *
 * 退出码：0 = 已落盘或按规则跳过（均不报错）；1 = 输入非法（不修改任何文件）。
 * 设置了 GITHUB_OUTPUT 时写出 skipped / number / board / target / date / snapshot 供工作流使用。
 */
import fs from 'node:fs';
import path from 'node:path';

const MARKER = '<!-- comment-submission -->';
const BOARD_TO_KEY = { '公司': 'companies', '学校': 'schools' };
const BOARD_TO_LIST = { '公司': '公司', '学校': '学校' };
const FIELD_NAMES = ['板块', '目标', '昵称', '内容'];
const MAX_CONTENT = 1000;
const MAX_NICKNAME = 40;

function log(msg) { process.stdout.write(msg + '\n'); }
function die(msg) { process.stderr.write('❌ ' + msg + '\n'); process.exit(1); }

function writeOutputs(map) {
  const file = process.env.GITHUB_OUTPUT;
  if (!file) return;
  try {
    fs.appendFileSync(file, Object.keys(map).map(function (k) { return k + '=' + map[k]; }).join('\n') + '\n', 'utf8');
  } catch (e) { /* 本地运行时忽略 */ }
}

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const m = /^--([a-z-]+)(?:=([\s\S]*))?$/.exec(argv[i]);
    if (!m) continue;
    out[m[1]] = m[2] !== undefined ? m[2] : argv[++i];
  }
  return out;
}

function readJson(p) {
  const buf = fs.readFileSync(p);
  if (buf.length >= 3 && buf[0] === 0xEF && buf[1] === 0xBB && buf[2] === 0xBF) die(p + ' 带 UTF-8 BOM，拒绝处理');
  return JSON.parse(buf.toString('utf8'));
}

function todayString(d) {
  const p = function (n) { return String(n).padStart(2, '0'); };
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
}

/** 按 §3.6 上限把评论切成若干分片（每片自身 ≤ 上限）。 */
function planShards(records, maxRecords, maxBytes) {
  const chunks = [];
  let cur = [];
  for (const rec of records) {
    const candidate = cur.concat([rec]);
    const bytes = Buffer.byteLength(JSON.stringify({ '评论': candidate }, null, 2) + '\n', 'utf8');
    if (cur.length && (candidate.length > maxRecords || bytes > maxBytes)) { chunks.push(cur); cur = [rec]; }
    else cur = candidate;
  }
  if (cur.length || chunks.length === 0) chunks.push(cur);
  return chunks;
}

/** 由 data/ 的真实文件重建离线快照（SPEC v1.3 §4.3）。 */
function buildSnapshot(rootDir) {
  const dir = path.join(rootDir, 'data');
  const idx = JSON.parse(fs.readFileSync(path.join(dir, 'index.json'), 'utf8'));
  const files = ['index.json'].concat(idx.companies || [], idx.schools || [], idx.comments || []);
  const snap = {};
  for (const f of files) snap[f] = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8'));
  const json = JSON.stringify(snap, null, 2);
  const bodyLines = json.split('\n').map(function (line, i) {
    return i === 0 ? '  root.SHUANGXIU_SNAPSHOT = ' + line : '  ' + line;
  }).join('\n');
  const header = [
    '/*!',
    ' * web/data-snapshot.js - offline read-only snapshot (SPEC v1.3 §4.3)',
    ' *',
    ' * file:// 下浏览器同源策略拒绝读取 data/*.json 时，app.js 回退读取这里挂载的',
    ' * window.SHUANGXIU_SNAPSHOT：data/ 各文件的逐文件镜像（键 = 文件名）。',
    ' *',
    ' * 键集合恰为 index.json 加上 index.json 中 companies / schools / comments 列出的全部分片（共 ' + files.length + ' 个：',
    ' * ' + files.join('、') + '）；每个键的值与同名 data/ 文件 JSON.parse 后的结果深等价。',
    ' * 由仓库内 data/ 的真实文件一次性生成，请勿手工编辑；数据变更后请重新生成。',
    ' * 依赖：无；不引用任何脚本、字体、图片或外部地址，file:// 下完全可用。',
    ' */',
    '(function (root) {',
    '  "use strict";'
  ].join('\n');
  return header + '\n' + bodyLines + ';\n}(typeof window !== "undefined" ? window : this));\n';
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const root = path.resolve(args.root || process.env.REPO_ROOT || process.cwd());
  const dataDir = path.join(root, 'data');
  const title = args.title !== undefined ? args.title : (process.env.ISSUE_TITLE || '');
  const body = args['body-file'] !== undefined
    ? fs.readFileSync(path.resolve(args['body-file']), 'utf8')
    : (args.body !== undefined ? args.body : (process.env.ISSUE_BODY || ''));
  const author = (args.author !== undefined ? args.author : (process.env.ISSUE_AUTHOR || '')).trim();
  const labels = String(args.labels !== undefined ? args.labels : (process.env.ISSUE_LABELS || ''))
    .split(',').map(function (s) { return s.trim(); }).filter(Boolean);

  const indexPath = path.join(dataDir, 'index.json');
  if (!fs.existsSync(indexPath)) die('找不到 data/index.json（root=' + root + '）');
  const index = readJson(indexPath);
  const shards = {
    companies: Array.isArray(index.companies) && index.companies.length ? index.companies : ['companies.json'],
    schools: Array.isArray(index.schools) && index.schools.length ? index.schools : ['schools.json'],
    comments: Array.isArray(index.comments) && index.comments.length ? index.comments : ['comments.json']
  };
  const limits = {
    maxRecords: (index.limits && index.limits.maxRecordsPerFile) || 100,
    maxBytes: (index.limits && index.limits.maxBytesPerFile) || 262144
  };
  const issueLabel = (index.site && index.site.issueLabel) || 'comment-submission';

  // ---- 门控：只有「带该标签」或「正文含标记」的 Issue 才处理，其余跳过（exit 0，不写任何文件）
  const hasMarker = body.indexOf(MARKER) >= 0;
  if (labels.indexOf(issueLabel) < 0 && !hasMarker) {
    log('⏭ 跳过：Issue 未带标签 ' + issueLabel + '，正文也不含 ' + MARKER);
    writeOutputs({ skipped: 'true' });
    return;
  }

  // ---- 标题（冻结模板：[评论] <板块>：<目标>，全角冒号）
  const titleMatch = /^\[评论\]\s*(公司|学校)\s*：\s*(\S(?:[\s\S]*\S)?)$/.exec(title.trim());
  if (!titleMatch) die('Issue 标题不符合冻结模板「[评论] <板块>：<目标>」：' + JSON.stringify(title));
  const titleBoard = titleMatch[1];
  const titleTarget = titleMatch[2];

  // ---- 正文（首行标记 + 四行字段，内容可多行）
  const lines = body.replace(/\r\n/g, '\n').split('\n');
  if (lines[0].trim() !== MARKER) die('正文首行必须是 ' + MARKER + '（前后无空格），实际为：' + JSON.stringify(lines[0]));
  const fieldIdx = {};
  let cursor = 1;
  for (const name of FIELD_NAMES) {
    const prefix = '- ' + name + ': ';
    const i = lines.findIndex(function (l, n) { return n >= cursor && l.startsWith(prefix); });
    if (i < 0) die('正文缺少字段行「' + name + '」（必须以「' + prefix + '」开头，且按 板块 / 目标 / 昵称 / 内容 顺序出现）');
    fieldIdx[name] = i;
    cursor = i + 1;
  }
  const board = lines[fieldIdx['板块']].slice('- 板块: '.length).trim();
  const target = lines[fieldIdx['目标']].slice('- 目标: '.length).trim();
  const nicknameField = lines[fieldIdx['昵称']].slice('- 昵称: '.length).trim();
  const content = [lines[fieldIdx['内容']].slice('- 内容: '.length)]
    .concat(lines.slice(fieldIdx['内容'] + 1)).join('\n').trim();

  // ---- 校验（全部通过后才写文件）
  if (!Object.prototype.hasOwnProperty.call(BOARD_TO_KEY, board)) die('板块非法：' + JSON.stringify(board) + '（只允许「公司」或「学校」）');
  if (board !== titleBoard) die('标题板块「' + titleBoard + '」与正文板块「' + board + '」不一致');
  if (!target) die('目标不能为空');
  if (target !== titleTarget) die('标题目标「' + titleTarget + '」与正文目标「' + target + '」不一致');
  if (!content) die('内容不能为空（去掉首尾空白后）');
  const contentLen = Array.from(content).length;
  if (contentLen > MAX_CONTENT) die('内容超长：' + contentLen + ' 字 > 上限 ' + MAX_CONTENT + ' 字');

  const boardKey = BOARD_TO_KEY[board];
  const listKey = BOARD_TO_LIST[board];
  const entries = [];
  for (const f of shards[boardKey]) {
    const p = path.join(dataDir, f);
    if (!fs.existsSync(p)) die('找不到分片 ' + f);
    const parsed = readJson(p);
    if (!Array.isArray(parsed[listKey])) die(f + ' 顶层缺少数组「' + listKey + '」');
    entries.push.apply(entries, parsed[listKey]);
  }
  if (!entries.some(function (e) { return e && e['名称'] === target; })) {
    die('目标不存在：' + board + ' 板块中没有名称为「' + target + '」的条目（孤儿评论拒绝写入）');
  }

  let nickname = nicknameField || author;
  if (Array.from(nickname).length > MAX_NICKNAME) die('昵称超长：' + Array.from(nickname).length + ' 字 > 上限 ' + MAX_NICKNAME + ' 字');

  const records = [];
  for (const f of shards.comments) {
    const p = path.join(dataDir, f);
    if (!fs.existsSync(p)) die('找不到评论分片 ' + f);
    const parsed = readJson(p);
    if (!Array.isArray(parsed['评论'])) die(f + ' 顶层缺少数组「评论」');
    records.push.apply(records, parsed['评论']);
  }
  records.sort(function (a, b) { return (Number(a['编号']) || 0) - (Number(b['编号']) || 0); });
  const maxNo = records.reduce(function (m, r) { return Math.max(m, Number(r['编号']) || 0); }, 0);
  const record = { '编号': maxNo + 1, '板块': board, '目标': target, '内容': content };
  if (nickname) record['昵称'] = nickname;
  record['日期'] = todayString(new Date());
  const next = records.concat([record]);

  // ---- 落盘（UTF-8 无 BOM、2 空格缩进、中文不转义）
  const chunks = planShards(next, limits.maxRecords, limits.maxBytes);
  const written = [];
  for (let i = 0; i < chunks.length; i++) {
    const name = chunks.length === 1 ? 'comments.json' : 'comments-' + (i + 1) + '.json';
    fs.writeFileSync(path.join(dataDir, name), JSON.stringify({ '评论': chunks[i] }, null, 2) + '\n', 'utf8');
    written.push(name);
  }
  for (const f of shards.comments) {
    if (written.indexOf(f) < 0 && /^comments(-\d+)?\.json$/.test(f)) fs.rmSync(path.join(dataDir, f), { force: true });
  }
  const listUnchanged = written.length === shards.comments.length && written.every(function (f, i) { return f === shards.comments[i]; });
  if (!listUnchanged) {
    index.comments = written;
    fs.writeFileSync(indexPath, JSON.stringify(index, null, 2) + '\n', 'utf8');
  }

  // ---- 同步重建离线快照
  let snapshot = 'unchanged';
  if (fs.existsSync(path.join(root, 'web'))) {
    fs.writeFileSync(path.join(root, 'web', 'data-snapshot.js'), buildSnapshot(root), 'utf8');
    snapshot = 'updated';
  } else {
    log('⚠️ 未找到 web/ 目录，跳过离线快照重建');
  }

  log('✅ 评论已落盘：编号=' + record['编号'] + ' 板块=' + board + ' 目标=' + target
    + ' 昵称=' + (nickname || '(未填写，前端显示匿名)') + ' 日期=' + record['日期']);
  log('   评论分片：' + written.join(', ') + '（共 ' + next.length + ' 条）');
  log('   离线快照：' + (snapshot === 'updated' ? '已重建 web/data-snapshot.js' : '未重建'));
  log(JSON.stringify({ ok: true, '编号': record['编号'], '板块': board, '目标': target, '日期': record['日期'], '快照已更新': snapshot === 'updated' }));
  writeOutputs({
    skipped: 'false',
    number: String(record['编号']),
    board: board,
    target: target,
    date: record['日期'],
    snapshot: snapshot
  });
}

try {
  main();
} catch (e) {
  die('未预期的错误：' + (e && e.message ? e.message : String(e)));
}
