#!/usr/bin/env node
/**
 * tools/ingest-userdata.mjs — 把一条「共享信息」Issue 落盘进 data/userdata_company.json / userdata_school.json
 *
 * 契约（三方共用，不得单独改动）：
 *   - 提交体 marker：<!-- userdata-submission -->（与 web/app.js 的 USERDATA_MARKER 一致）
 *   - fenced JSON 记录键：板块 / 目标 / 岗位 / 双休情况 / 说明 / 昵称 / 日期
 *   - 落盘记录键：名称 / 休息情况 / 岗位(可选) / 评价 / 昵称 / 日期（与 web/app.js 的约定键渲染对齐）
 *
 * 纯 Node（ESM），零第三方依赖。由 .github/workflows/userdata-ingest.yml 在服务端调用；
 * 仓库凭据只存在于 Actions 运行时的临时 GITHUB_TOKEN，页面与工具本身不持有任何密钥。
 *
 * 输入：--body-file <路径>（工作流用）或 --body <文本>（本地测试用）；--root <路径>（默认 process.cwd()）
 * 退出码：0 = 已落盘；1 = 输入非法（不修改任何文件）。
 */
import fs from 'node:fs';
import path from 'node:path';

const MARKER = '<!-- userdata-submission -->';
const BOARD_TO_FILE = { '公司': 'userdata_company.json', '学校': 'userdata_school.json' };
const BOARD_TO_KEY = { '公司': '公司', '学校': '学校' };
const STATUS_TOKENS = ['是', '否', '大小周', '不清楚'];
const MAX_TARGET = 80;
const MAX_JOB = 80;
const MAX_TEXT = 1000;
const MAX_NICK = 40;

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

function readJsonStrict(p) {
  const buf = fs.readFileSync(p);
  if (buf.length >= 3 && buf[0] === 0xEF && buf[1] === 0xBB && buf[2] === 0xBF) die(p + ' 带 UTF-8 BOM，拒绝处理');
  return JSON.parse(buf.toString('utf8'));
}

function todayString(d) {
  const p = function (n) { return String(n).padStart(2, '0'); };
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
}

/* 从提交体提取 fenced JSON：marker 之后的第一段 ```json ... ``` */
function extractRecord(body) {
  if (body.indexOf(MARKER) < 0) die('提交体缺少 marker ' + MARKER + '，不是共享信息提交');
  const fence = /```json\s*\n([\s\S]*?)\n\s*```/.exec(body);
  if (!fence) die('提交体缺少 ```json 围栏代码块');
  let parsed;
  try { parsed = JSON.parse(fence[1]); } catch (e) { die('围栏内不是合法 JSON：' + e.message); }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) die('围栏内必须是 JSON 对象');
  return parsed;
}

function validDate(s) {
  return typeof s === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(s) && !Number.isNaN(Date.parse(s));
}

/* ---------- 主流程 ---------- */

const args = parseArgs(process.argv.slice(2));
const root = path.resolve(args.root || process.env.REPO_ROOT || process.cwd());

let body = '';
if (args['body-file']) { body = fs.readFileSync(path.resolve(root, args['body-file']), 'utf8'); }
else if (args.body) { body = String(args.body); }
else die('缺少输入：请用 --body-file 或 --body 提供 Issue 正文');

const raw = extractRecord(body);

const board = typeof raw['板块'] === 'string' ? raw['板块'].trim() : '';
if (!hasOwn2(BOARD_TO_FILE, board)) die('「板块」必须是「公司」或「学校」，当前：' + JSON.stringify(raw['板块'] || null));
const target = typeof raw['目标'] === 'string' ? raw['目标'].trim() : '';
if (target === '') die('「目标」不能为空');
if (target.length > MAX_TARGET) die('「目标」过长（最多 ' + MAX_TARGET + ' 字，当前 ' + target.length + ' 字）');
const status = typeof raw['双休情况'] === 'string' ? raw['双休情况'].trim() : '';
if (STATUS_TOKENS.indexOf(status) < 0) die('「双休情况」必须是 ' + STATUS_TOKENS.join(' / ') + ' 之一，当前：' + JSON.stringify(raw['双休情况'] || null));
const text = typeof raw['说明'] === 'string' ? raw['说明'].trim() : '';
if (text === '') die('「说明」不能为空');
if (text.length > MAX_TEXT) die('「说明」过长（最多 ' + MAX_TEXT + ' 字，当前 ' + text.length + ' 字）');
const job = typeof raw['岗位'] === 'string' ? raw['岗位'].trim() : '';
if (job.length > MAX_JOB) die('「岗位」过长（最多 ' + MAX_JOB + ' 字，当前 ' + job.length + ' 字）');
const nick = typeof raw['昵称'] === 'string' && raw['昵称'].trim() !== '' ? raw['昵称'].trim() : '匿名';
if (nick.length > MAX_NICK) die('「昵称」过长（最多 ' + MAX_NICK + ' 字，当前 ' + nick.length + ' 字）');
const date = validDate(raw['日期']) ? raw['日期'] : todayString(new Date());

/* 落盘记录：与主数据约定键对齐（名称/休息情况/岗位/评价/昵称/日期），前端按键渲染无需特判 */
const record = {
  '名称': target,
  '休息情况': status,
  '评价': [text],
  '昵称': nick,
  '日期': date
};
if (job !== '') {
  record['岗位'] = [{ '岗位名': job, '双休': status }];
}

const limitsPath = path.join(root, 'data', 'index.json');
let limits = { maxRecordsPerFile: 100, maxBytesPerFile: 262144 };
if (fs.existsSync(limitsPath)) {
  const idx = readJsonStrict(limitsPath);
  if (idx && typeof idx === 'object' && idx.limits && typeof idx.limits === 'object') {
    if (Number.isFinite(idx.limits.maxRecordsPerFile)) { limits.maxRecordsPerFile = idx.limits.maxRecordsPerFile; }
    if (Number.isFinite(idx.limits.maxBytesPerFile)) { limits.maxBytesPerFile = idx.limits.maxBytesPerFile; }
  }
}

const dataFile = path.join(root, 'data', BOARD_TO_FILE[board]);
if (!fs.existsSync(dataFile)) die('数据文件不存在：data/' + BOARD_TO_FILE[board] + '（请先在仓库中创建）');
const data = readJsonStrict(dataFile);
const key = BOARD_TO_KEY[board];
if (!data || typeof data !== 'object' || Array.isArray(data) || !Array.isArray(data[key])) {
  die('数据文件 ' + BOARD_TO_FILE[board] + ' 顶层必须是 {"' + key + '": [...]}');
}

data[key].push(record);
const out = JSON.stringify(data, null, 2) + '\n';
const bytes = Buffer.byteLength(out, 'utf8');
if (data[key].length > limits.maxRecordsPerFile) {
  die('超出分片上限：' + BOARD_TO_FILE[board] + ' 将有 ' + data[key].length + ' 条（上限 ' + limits.maxRecordsPerFile + '）。请先归档旧数据');
}
if (bytes > limits.maxBytesPerFile) {
  die('超出体积上限：' + BOARD_TO_FILE[board] + ' 将达 ' + bytes + ' 字节（上限 ' + limits.maxBytesPerFile + '）。请先归档旧数据');
}

fs.writeFileSync(dataFile, out, 'utf8');
log('✅ 已写入 data/' + BOARD_TO_FILE[board] + '：' + target + '（' + data[key].length + ' 条，' + bytes + ' 字节）');
writeOutputs({
  board: board,
  target: target,
  file: 'data/' + BOARD_TO_FILE[board],
  records: String(data[key].length),
  bytes: String(bytes)
});

function hasOwn2(obj, key) { return Object.prototype.hasOwnProperty.call(obj, key); }
