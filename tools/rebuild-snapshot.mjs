#!/usr/bin/env node
/**
 * tools/rebuild-snapshot.mjs — 由 data/ 真实文件重建 web/data-snapshot.js。
 * 逻辑与 tools/ingest-comment.mjs 的 buildSnapshot 完全一致（SPEC v1.3 §4.3）：
 * data/ 变更后手动运行一次即可保持离线快照镜像同步。
 * 纯 Node（ESM），零第三方依赖。
 *
 * 用法：node tools/rebuild-snapshot.mjs [repoRoot]
 */
import fs from 'node:fs';
import path from 'node:path';

const root = path.resolve(process.argv[2] || process.cwd());
const dir = path.join(root, 'data');
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
fs.writeFileSync(path.join(root, 'web', 'data-snapshot.js'), header + '\n' + bodyLines + ';\n}(typeof window !== "undefined" ? window : this));\n', 'utf8');
process.stdout.write('snapshot rebuilt from ' + files.length + ' files: ' + files.join(', ') + '\n');
