#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「今天你双休了吗？」数据可视化编辑器（契约：docs/SPEC.md v1.2 第 5 节 E1–E8）。

技术栈：Python 3 + tkinter，仅使用标准库（tkinter / json / os / shutil / sys / re / datetime），
单文件运行，不引入第三方依赖，也不需要联网。

用法
----
    python add_prog/editor.py                   # 启动 GUI（默认加载 ../data/index.json 与公司板块）
    python add_prog/editor.py --check            # 无界面只读校验，退出码 0 / 1
    python add_prog/editor.py --check --board schools
    python add_prog/editor.py --data-dir <目录>   # 指定数据目录（默认脚本同级 ../data）

数据契约（v1.2.1 冻结）
----------------------
    data/index.json     顶层四键：version=1、limits={maxRecordsPerFile:100, maxBytesPerFile:262144}、
                        companies=[分片文件名…]、schools=[分片文件名…]（数组顺序 = 加载顺序）
    data/companies.json 顶层键「公司」→ 条目数组：名称（必填，1–80 字，同板块内唯一）/
                        类别（必填：制造业 / 消费零售·餐饮 / 新能源汽车 / 互联网·科技）/
                        岗位（必填的非空数组）[{岗位名称(必填), 双休(是/否/未知，可省略),
                        八小时工作制(是/否/未知，可省略), 情况(原文，可省略)}] /
                        备注(可选) / 评价[{编号, 内容}]
    data/schools.json   顶层键「学校」→ 条目数组：名称 / 双休情况 / 补课情况 / 评价[{编号, 内容}]

单文件上限（100 条记录 / 262144 字节，先到为准）超限时拆分为 companies-N.json / schools-N.json：
序号从 1 连续递增、无前导 0，每个分片自身也不得超限；拆分后不带编号的原文件必须删除，
并同步 index.json 的数组顺序。数据文件一律 UTF-8 无 BOM。

编辑器行为（E1–E8）
-------------------
    E1 启动定位到脚本同级 ../data，默认 index.json + companies.json，可用对话框选择 data/ 下的 JSON
    E2 公司 / 学校两个板块，按 index.json 的数组顺序拼接多分片为可编辑列表，保存时按 §3.5 回写
    E3 条目新增 / 删除 / 重命名 / 上移 / 下移；名称非空（1–80 字）且同一板块内不重复
    E4 岗位子表与评价子表的增删改与上下移；保存时「评价.编号」按当前顺序从 1 自动重排
    E5 顶层形状、非对象条目、空名称、重名、非法类别、空岗位数组、非法枚举值一律提示并拒绝写入
    E6 保存前按 index.json 的 limits 校验；超限时提示并可一键拆分 + 同步 index.json + 提示快照过期
    E7 写回 ensure_ascii=False、indent=2、UTF-8 无 BOM；保存前备份到仓库根 backups/（不写 data/）
    E8 窗口标题含当前数据路径，菜单 / 工具栏按钮 / Ctrl+S 保存 / Ctrl+O 打开，messagebox 反馈
"""

import datetime
import json
import os
import re
import shutil
import sys
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, simpledialog, ttk

APP_TITLE = "今天你双休了吗？ · 数据编辑器"

INDEX_FILENAME = "index.json"
DATA_DIRNAME = "data"
BACKUP_DIRNAME = "backups"
SNAPSHOT_RELATIVE = os.path.join("web", "data-snapshot.js")

DEFAULT_LIMITS = {"maxRecordsPerFile": 100, "maxBytesPerFile": 262144}
NAME_MAX = 80
COMPANY_CATEGORY_VALUES = ["制造业", "消费零售·餐饮", "新能源汽车", "互联网·科技"]
YES_NO_UNKNOWN_VALUES = ["是", "否", "未知"]
NOT_SET = "（未设置）"
BOARD_ORDER = ["companies", "schools", "comments"]
BOARDS = {
    "companies": {
        "key": "公司",
        "base": "companies",
        "label": "公司",
        "kind": "company",
        "category_key": "类别",
        "has_jobs": True,
    },
    "schools": {
        "key": "学校",
        "base": "schools",
        "label": "学校",
        "kind": "school",
        "status_key": "双休情况",
        "has_jobs": False,
    },
    "comments": {
        "key": "评论",
        "base": "comments",
        "label": "评论",
        "kind": "comment",
        "has_jobs": False,
    },
}
RECORD_RESERVED_KEYS = {
    "companies": ["名称", "类别", "岗位", "备注", "评价"],
    "schools": ["名称", "双休情况", "补课情况", "评价"],
    "comments": ["编号", "板块", "目标", "内容", "昵称", "日期"],
}
COMMENT_BOARD_VALUES = ["公司", "学校"]
COMMENT_CONTENT_MAX = 1000
NICKNAME_MAX = 40
DEFAULT_SITE_REPO = "OWNER/REPO"
DEFAULT_SITE_LABEL = "comment-submission"
DATE_RE = re.compile("^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
JOB_RESERVED_KEYS = ["岗位名称", "双休", "八小时工作制", "情况"]
REVIEW_RESERVED_KEYS = ["编号", "内容"]
NUMBER_RE = re.compile("^-?[0-9]+$")
DECIMAL_RE = re.compile("^-?[0-9]*[.][0-9]+$")


# --------------------------------------------------------------------------- #
# 异常
# --------------------------------------------------------------------------- #
class EditorError(Exception):
    """编辑器可预期的错误（GUI 会转成 messagebox）。"""


class ValidationFailed(EditorError):
    """校验失败：携带全部问题描述。"""

    def __init__(self, errors):
        self.errors = list(errors)
        EditorError.__init__(self, "数据校验未通过（%d 处问题）" % len(self.errors))


class LimitError(EditorError):
    """超出单文件上限且无法通过拆分解决。"""


# --------------------------------------------------------------------------- #
# 路径与通用工具
# --------------------------------------------------------------------------- #
def posix_path(path):
    """把本地路径转成文档约定的 POSIX 风格字符串。"""
    if not path:
        return ""
    return str(path).replace(os.sep, "/")


def script_dir():
    return os.path.dirname(os.path.abspath(__file__))


def default_paths():
    """E1：data 目录 = 脚本同级 ../data；备份目录 = 仓库根 backups/。"""
    root = os.path.normpath(os.path.join(script_dir(), os.pardir))
    return os.path.join(root, DATA_DIRNAME), os.path.join(root, BACKUP_DIRNAME)


def type_name(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "布尔"
    if isinstance(value, (int, float)):
        return "数字"
    if isinstance(value, str):
        return "字符串"
    if isinstance(value, list):
        return "数组"
    if isinstance(value, dict):
        return "对象"
    return type(value).__name__


def json_text_compact(value):
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(value)


def string_or_empty(value):
    return value if isinstance(value, str) else ""


def shard_pattern(base):
    """匹配 <base>.json 与 <base>-N.json，用于清理本板块的过期分片。"""
    return re.compile("^" + re.escape(base) + "(-[0-9]+)?[.]json$")


def parse_value_text(text):
    """附加字段的值：尽量还原 JSON 标量 / 结构，否则按字符串处理。"""
    stripped = text.strip()
    if stripped == "":
        return ""
    lowered = stripped.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    if NUMBER_RE.match(stripped):
        return int(stripped)
    if DECIMAL_RE.match(stripped):
        return float(stripped)
    if stripped[0] in "[{":
        try:
            return json.loads(stripped)
        except ValueError:
            return text
    return text


def format_value_text(value):
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def apply_optional_choice(target, key, value):
    """下拉的「未设置」表示删除该可选键。"""
    if not isinstance(target, dict):
        return
    if value == NOT_SET or value not in YES_NO_UNKNOWN_VALUES:
        target.pop(key, None)
    else:
        target[key] = value


# --------------------------------------------------------------------------- #
# JSON 读写（UTF-8 无 BOM、ensure_ascii=False、indent=2）
# --------------------------------------------------------------------------- #
def read_text(path):
    """返回 (文本, 是否带 BOM)；编码不是 UTF-8 时抛出 EditorError。"""
    with open(path, "rb") as handle:
        raw = handle.read()
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    if had_bom:
        raw = raw[3:]
    try:
        return raw.decode("utf-8"), had_bom
    except UnicodeDecodeError as exc:
        raise EditorError("%s 不是合法的 UTF-8 文件：%s" % (posix_path(path), exc))


def read_json_document(path):
    """返回 (解析结果, 是否带 BOM)。"""
    text, had_bom = read_text(path)
    try:
        return json.loads(text), had_bom
    except ValueError as exc:
        raise EditorError("%s 不是合法 JSON：%s" % (posix_path(path), exc))


def json_text(document):
    """E7：ensure_ascii=False、indent=2、结尾换行。"""
    return json.dumps(document, ensure_ascii=False, indent=2) + chr(10)


def json_byte_size(document):
    return len(json_text(document).encode("utf-8"))


def write_json_document(path, document):
    """UTF-8 无 BOM 写盘，换行固定为换行符，返回写入字节数。"""
    text = json_text(document)
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, "w", encoding="utf-8", newline=chr(10)) as handle:
        handle.write(text)
    return len(text.encode("utf-8"))


# --------------------------------------------------------------------------- #
# index.json
# --------------------------------------------------------------------------- #
def default_index():
    return {
        "version": 1,
        "limits": dict(DEFAULT_LIMITS),
        "companies": [BOARDS["companies"]["base"] + ".json"],
        "schools": [BOARDS["schools"]["base"] + ".json"],
        "comments": [BOARDS["comments"]["base"] + ".json"],
        "site": {"repo": DEFAULT_SITE_REPO, "issueLabel": DEFAULT_SITE_LABEL},
    }


def normalize_limits(raw, warnings=None):
    limits = dict(DEFAULT_LIMITS)
    if isinstance(raw, dict):
        for key in ("maxRecordsPerFile", "maxBytesPerFile"):
            value = raw.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                limits[key] = value
            elif warnings is not None:
                warnings.append("index.json limits.%s 非法，已按默认值 %d 处理。" % (key, DEFAULT_LIMITS[key]))
    elif warnings is not None:
        warnings.append(
            "index.json 缺少 limits 对象，已按默认上限 %d 条 / %d 字节处理。"
            % (DEFAULT_LIMITS["maxRecordsPerFile"], DEFAULT_LIMITS["maxBytesPerFile"])
        )
    return limits


def sanitize_shard_names(value, base, label, warnings):
    names = []
    if isinstance(value, list):
        for item in value:
            if (
                isinstance(item, str)
                and item != ""
                and item.lower().endswith(".json")
                and ".." not in item
                and "/" not in item
                and "\\" not in item
            ):
                names.append(item)
            else:
                warnings.append("index.json 中 %s 的分片名非法：%s（已忽略）。" % (label, json_text_compact(item)))
    if not names:
        names = [base + ".json"]
        warnings.append("index.json 中 %s 的分片数组为空或非法，已按 %s.json 处理。" % (label, base))
    return names


def load_index(data_dir):
    """返回 (index 字典, 提示列表)；任何异常都降级为默认索引，不抛错。"""
    warnings = []
    path = os.path.join(data_dir, INDEX_FILENAME)
    if not os.path.isfile(path):
        warnings.append("未找到 data/%s，已按默认索引处理（保存时会重建）。" % INDEX_FILENAME)
        return default_index(), warnings
    try:
        raw, had_bom = read_json_document(path)
    except EditorError as exc:
        warnings.append("%s（已按默认索引处理）" % exc)
        return default_index(), warnings
    if had_bom:
        warnings.append("data/%s 带 UTF-8 BOM，保存时会重写为无 BOM。" % INDEX_FILENAME)
    if not isinstance(raw, dict):
        warnings.append("data/%s 顶层不是对象（实际为%s），已按默认索引处理。" % (INDEX_FILENAME, type_name(raw)))
        return default_index(), warnings
    index = default_index()
    index["version"] = 1
    index["limits"] = normalize_limits(raw.get("limits"), warnings)
    for board_id in BOARD_ORDER:
        spec = BOARDS[board_id]
        index[board_id] = sanitize_shard_names(raw.get(board_id), spec["base"], spec["label"], warnings)
    index["site"] = normalize_site(raw.get("site"), warnings)
    return index, warnings


def shard_names_for(index, board):
    spec = BOARDS[board]
    value = index.get(board)
    if isinstance(value, list) and value:
        return [item for item in value if isinstance(item, str) and item]
    return [spec["base"] + ".json"]


def index_shard_names(index):
    """index.json 列出的全部分片名（§3.1 六个顶层键中的三个分片数组）。"""
    names = []
    for board_id in BOARD_ORDER:
        names.extend(shard_names_for(index, board_id))
    return names


def normalize_site(raw, warnings=None):
    """§3.1：site = {repo: OWNER/REPO 形式, issueLabel: comment-submission}。"""
    site = {"repo": DEFAULT_SITE_REPO, "issueLabel": DEFAULT_SITE_LABEL}
    if isinstance(raw, dict):
        repo = raw.get("repo")
        if isinstance(repo, str) and repo.strip() != "" and "/" in repo:
            site["repo"] = repo
        elif warnings is not None:
            warnings.append(
                "index.json site.repo 非法（应为 OWNER/REPO 形式），已按占位值 %s 处理。" % DEFAULT_SITE_REPO
            )
        label = raw.get("issueLabel")
        if isinstance(label, str) and label.strip() != "":
            site["issueLabel"] = label
        elif warnings is not None:
            warnings.append("index.json site.issueLabel 非法，已按 %s 处理。" % DEFAULT_SITE_LABEL)
    elif warnings is not None:
        warnings.append("index.json 缺少 site 对象，已按占位值处理（发布前请填写 site.repo）。")
    return site


def board_target_names(loaded):
    """把各板块条目的「名称」收集成 {板块键: [名称…]}，供评论目标存在性校验使用。"""
    result = {}
    for board_id, records in loaded.items():
        spec = BOARDS[board_id]
        names = []
        for record in records:
            if isinstance(record, dict) and isinstance(record.get("名称"), str):
                name = record["名称"].strip()
                if name:
                    names.append(name)
        result[spec["key"]] = names
    return result


def load_target_names(data_dir, index, warnings=None):
    """只读加载公司与学校条目的名称集合（评论目标校验用）。"""
    scratch = warnings if warnings is not None else []
    loaded = {}
    for board_id in ("companies", "schools"):
        try:
            records, _names = load_board(data_dir, board_id, index, scratch)
        except EditorError:
            records = []
        loaded[board_id] = records
    return board_target_names(loaded)


# --------------------------------------------------------------------------- #
# 数据文件加载
# --------------------------------------------------------------------------- #
def load_board(data_dir, board, index, warnings=None):
    """E2：按 index.json 的数组顺序拼接多分片，返回 (条目列表, 分片名列表)。"""
    if warnings is None:
        warnings = []
    spec = BOARDS[board]
    key = spec["key"]
    names = shard_names_for(index, board)
    records = []
    for name in names:
        path = os.path.join(data_dir, name)
        if not os.path.isfile(path):
            warnings.append("index.json 列出的分片不存在：data/%s（已按空分片处理）。" % name)
            continue
        raw, had_bom = read_json_document(path)
        if had_bom:
            warnings.append("data/%s 带 UTF-8 BOM，保存时会重写为无 BOM。" % name)
        if not isinstance(raw, dict) or key not in raw:
            raise EditorError("data/%s 顶层必须是「%s」数组的对象形式（实际为%s）。" % (name, key, type_name(raw)))
        array = raw[key]
        if not isinstance(array, list):
            raise EditorError("data/%s 的「%s」必须是数组（实际为%s）。" % (name, key, type_name(array)))
        records.extend(array)
    existing = sorted(name for name in os.listdir(data_dir) if name.lower().endswith(".json"))
    known = set([INDEX_FILENAME] + index_shard_names(index))
    extra = [name for name in existing if name not in known]
    if extra:
        warnings.append("data/ 中存在未被 index.json 列出的文件：%s。" % "、".join(extra))
    return records, names


def data_dir_consistency(data_dir, index):
    """返回 (多余文件, 缺失文件)；索引 = 真实分片集合 = 加载顺序 三者必须一致。"""
    actual = sorted(name for name in os.listdir(data_dir) if name.lower().endswith(".json"))
    expected = sorted(set([INDEX_FILENAME] + index_shard_names(index)))
    extra = [name for name in actual if name not in expected]
    missing = [name for name in expected if name not in actual]
    return extra, missing


# --------------------------------------------------------------------------- #
# 校验（E5）与规范化
# --------------------------------------------------------------------------- #
def validate_comment_record(record, where, targets, errors):
    """§3.9 评论字段校验：编号 / 板块 / 目标存在性 / 内容 1–1000 / 昵称 / 日期。"""
    number = record.get("编号")
    if isinstance(number, bool) or not isinstance(number, int):
        errors.append("%s的「编号」必须是整数（实际为%s）。" % (where, type_name(number)))
    elif number < 1:
        errors.append("%s的「编号」必须 ≥ 1（实际为%d）。" % (where, number))
    board_value = record.get("板块")
    if not isinstance(board_value, str) or board_value not in COMMENT_BOARD_VALUES:
        errors.append(
            "%s的「板块」必须是 %s 之一（实际为%s）。"
            % (where, " / ".join(COMMENT_BOARD_VALUES), json_text_compact(board_value))
        )
    target = record.get("目标")
    if not isinstance(target, str) or target.strip() == "":
        errors.append("%s缺少非空字符串「目标」。" % where)
    else:
        trimmed = target.strip()
        if isinstance(board_value, str) and board_value in COMMENT_BOARD_VALUES:
            available = list((targets or {}).get(board_value) or []) if isinstance(targets, dict) else []
            if trimmed not in available:
                errors.append(
                    "%s的目标「%s」在%s板块的数据文件中不存在（共 %d 个可选条目）；评论目标必须是现有条目的「名称」。"
                    % (where, trimmed, board_value, len(available))
                )
    content = record.get("内容")
    if not isinstance(content, str) or content.strip() == "":
        errors.append("%s缺少非空字符串「内容」。" % where)
    elif len(content.strip()) > COMMENT_CONTENT_MAX:
        errors.append("%s的「内容」长度 %d 超过上限 %d。" % (where, len(content.strip()), COMMENT_CONTENT_MAX))
    nickname = record.get("昵称")
    if nickname is not None and (not isinstance(nickname, str) or len(nickname) > NICKNAME_MAX):
        errors.append(
            "%s的「昵称」必须是 ≤ %d 字的字符串（实际为%s）。" % (where, NICKNAME_MAX, json_text_compact(nickname))
        )
    date_value = record.get("日期")
    if date_value is not None and (not isinstance(date_value, str) or not DATE_RE.match(date_value)):
        errors.append("%s的「日期」必须是 YYYY-MM-DD（实际为%s）。" % (where, json_text_compact(date_value)))


def check_comment_numbering(records):
    """§3.9 约束 2：编号必须唯一且 1..N 连续；返回问题描述或 None。"""
    numbers = [record.get("编号") for record in records if isinstance(record, dict)]
    expected = list(range(1, len(numbers) + 1))
    if numbers != expected:
        return "评论「编号」必须从 1 连续且唯一（1..N）：当前为 %s。" % json_text_compact(numbers[:20])
    return None


def validate_records(board, records, targets=None):
    """返回问题描述列表（空列表表示通过）；targets = {板块键: [名称…]} 供评论目标校验。"""
    spec = BOARDS[board]
    errors = []
    seen_names = {}
    for position, record in enumerate(records):
        where = "第 %d 条%s记录" % (position + 1, spec["label"])
        if not isinstance(record, dict):
            errors.append("%s不是对象（实际为%s），必须修正或删除后才能保存。" % (where, type_name(record)))
            continue
        if spec["kind"] == "comment":
            validate_comment_record(record, where, targets, errors)
            continue
        name = record.get("名称")
        if not isinstance(name, str) or name.strip() == "":
            errors.append("%s缺少非空字符串「名称」。" % where)
        else:
            trimmed = name.strip()
            if len(trimmed) > NAME_MAX:
                errors.append("%s的「名称」长度 %d 超过上限 %d。" % (where, len(trimmed), NAME_MAX))
            if trimmed in seen_names:
                errors.append(
                    "%s的名称「%s」与第 %d 条重复；同一板块内名称不得重复。"
                    % (where, trimmed, seen_names[trimmed])
                )
            else:
                seen_names[trimmed] = position + 1
        if spec["has_jobs"]:
            category = record.get("类别")
            if not isinstance(category, str) or category not in COMPANY_CATEGORY_VALUES:
                errors.append(
                    "%s的「类别」必须是 %s 之一（实际为%s）。"
                    % (where, " / ".join(COMPANY_CATEGORY_VALUES), json_text_compact(category))
                )
            note = record.get("备注")
            if note is not None and not isinstance(note, str):
                errors.append("%s的「备注」必须是字符串（实际为%s）。" % (where, type_name(note)))
            jobs = record.get("岗位")
            if not isinstance(jobs, list):
                errors.append("%s的「岗位」必须是数组（实际为%s）。" % (where, type_name(jobs)))
            elif len(jobs) == 0:
                errors.append("%s的「岗位」不能为空数组：公司条目必须至少有一个岗位。" % where)
            else:
                seen_jobs = {}
                for job_position, job in enumerate(jobs):
                    job_where = "%s的第 %d 个岗位" % (where, job_position + 1)
                    if not isinstance(job, dict):
                        errors.append("%s不是对象（实际为%s）。" % (job_where, type_name(job)))
                        continue
                    job_name = job.get("岗位名称")
                    if not isinstance(job_name, str) or job_name.strip() == "":
                        errors.append("%s缺少非空字符串「岗位名称」。" % job_where)
                    else:
                        trimmed_job = job_name.strip()
                        if trimmed_job in seen_jobs:
                            errors.append(
                                "%s的岗位名称「%s」与第 %d 个岗位重复；同一公司内岗位名称不得重复。"
                                % (job_where, trimmed_job, seen_jobs[trimmed_job])
                            )
                        else:
                            seen_jobs[trimmed_job] = job_position + 1
                    for optional_key in ("双休", "八小时工作制"):
                        if optional_key not in job:
                            continue
                        value = job.get(optional_key)
                        if not isinstance(value, str) or value not in YES_NO_UNKNOWN_VALUES:
                            errors.append(
                                "%s的「%s」只能是 %s 之一，或整键省略（实际为%s）。"
                                % (job_where, optional_key, " / ".join(YES_NO_UNKNOWN_VALUES), json_text_compact(value))
                            )
                    job_situation = job.get("情况")
                    if job_situation is not None and not isinstance(job_situation, str):
                        errors.append("%s的「情况」必须是字符串（实际为%s）。" % (job_where, type_name(job_situation)))
        else:
            weekend = record.get("双休情况")
            if not isinstance(weekend, str) or weekend.strip() == "":
                errors.append("%s缺少非空字符串「双休情况」。" % where)
            makeup = record.get("补课情况")
            if makeup is not None and not isinstance(makeup, str):
                errors.append("%s的「补课情况」必须是字符串（实际为%s）。" % (where, type_name(makeup)))
        reviews = record.get("评价")
        if reviews is None:
            pass
        elif not isinstance(reviews, list):
            errors.append("%s的「评价」必须是数组（实际为%s）。" % (where, type_name(reviews)))
        else:
            for review_position, review in enumerate(reviews):
                review_where = "%s的第 %d 条评价" % (where, review_position + 1)
                if not isinstance(review, dict):
                    errors.append("%s不是对象（实际为%s）。" % (review_where, type_name(review)))
                    continue
                content = review.get("内容")
                if not isinstance(content, str) or content.strip() == "":
                    errors.append("%s缺少非空字符串「内容」。" % review_where)
                number = review.get("编号")
                if number is not None and (isinstance(number, bool) or not isinstance(number, int)):
                    errors.append(
                        "%s的「编号」必须是整数（实际为%s）；保存时会按顺序自动重排。"
                        % (review_where, type_name(number))
                    )
    return errors


def normalize_record(board, record, notes=None):
    """把条目整理成 §3.4 的键顺序，并重排评价编号（1..N）。"""
    spec = BOARDS[board]
    if not isinstance(record, dict):
        return record
    name = record.get("名称")
    name = name.strip() if isinstance(name, str) else name
    ordered = {"名称": name}
    if spec["has_jobs"]:
        ordered["类别"] = record.get("类别")
        jobs = record.get("岗位")
        ordered_jobs = []
        if isinstance(jobs, list):
            for job in jobs:
                if not isinstance(job, dict):
                    continue
                job_name = job.get("岗位名称")
                ordered_job = {"岗位名称": job_name.strip() if isinstance(job_name, str) else job_name}
                for optional_key in ("双休", "八小时工作制"):
                    value = job.get(optional_key)
                    if isinstance(value, str) and value in YES_NO_UNKNOWN_VALUES:
                        ordered_job[optional_key] = value
                if "情况" in job:
                    ordered_job["情况"] = job.get("情况")
                for extra_key, extra_value in job.items():
                    if extra_key not in ordered_job:
                        ordered_job[extra_key] = extra_value
                ordered_jobs.append(ordered_job)
        ordered["岗位"] = ordered_jobs
        note = record.get("备注")
        if isinstance(note, str) and note.strip() != "":
            ordered["备注"] = record.get("备注")
    else:
        ordered["双休情况"] = record.get("双休情况")
        makeup = record.get("补课情况")
        if isinstance(makeup, str) and makeup.strip() != "":
            ordered["补课情况"] = record.get("补课情况")
    reviews = record.get("评价")
    if isinstance(reviews, list):
        ordered_reviews = []
        renumbered = False
        for position, review in enumerate(reviews):
            if not isinstance(review, dict):
                continue
            content = review.get("内容")
            content = content.strip() if isinstance(content, str) else content
            ordered_review = {"编号": position + 1, "内容": content}
            for extra_key, extra_value in review.items():
                if extra_key not in ordered_review:
                    ordered_review[extra_key] = extra_value
            if review.get("编号") != position + 1:
                renumbered = True
            ordered_reviews.append(ordered_review)
        ordered["评价"] = ordered_reviews
        if renumbered and notes is not None:
            notes.append("「%s」的评价编号已按当前顺序重排为 1..%d。" % (name, len(ordered_reviews)))
    elif reviews is None:
        ordered["评价"] = []
    else:
        ordered["评价"] = reviews
    reserved = RECORD_RESERVED_KEYS[board]
    for extra_key, extra_value in record.items():
        if extra_key not in ordered and extra_key not in reserved:
            ordered[extra_key] = extra_value
    return ordered


def normalize_comment_record(record):
    """§3.9 键顺序：编号 / 板块 / 目标 / 内容 / 昵称? / 日期?（+ 表外新键）。"""
    if not isinstance(record, dict):
        return record
    target = record.get("目标")
    content = record.get("内容")
    ordered = {
        "编号": record.get("编号"),
        "板块": record.get("板块"),
        "目标": target.strip() if isinstance(target, str) else target,
        "内容": content.strip() if isinstance(content, str) else content,
    }
    nickname = record.get("昵称")
    if isinstance(nickname, str) and nickname.strip() != "":
        ordered["昵称"] = nickname.strip()
    date_value = record.get("日期")
    if isinstance(date_value, str) and date_value.strip() != "":
        ordered["日期"] = date_value.strip()
    reserved = RECORD_RESERVED_KEYS["comments"]
    for extra_key, extra_value in record.items():
        if extra_key not in ordered and extra_key not in reserved:
            ordered[extra_key] = extra_value
    return ordered


def normalize_comment_records(records, notes=None):
    """按当前顺序把「编号」重排为 1..N（与评价编号策略一致）。"""
    ordered = []
    renumbered = False
    for position, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        item = normalize_comment_record(record)
        if record.get("编号") != position + 1:
            renumbered = True
        item["编号"] = position + 1
        ordered.append(item)
    if renumbered and notes is not None:
        notes.append("评论「编号」已按当前顺序重排为 1..%d。" % len(ordered))
    return ordered


def normalize_records(board, records, notes=None):
    if BOARDS[board]["kind"] == "comment":
        return normalize_comment_records(records, notes)
    return [normalize_record(board, record, notes) for record in records]


# --------------------------------------------------------------------------- #
# 上限与拆分（E6 / §3.5）
# --------------------------------------------------------------------------- #
def plan_shards(board, records, limits):
    """返回 [(文件名, 记录列表)]；文件名遵循 §3.5（未拆分时是 <base>.json）。"""
    spec = BOARDS[board]
    key = spec["key"]
    base = spec["base"]
    max_records = limits["maxRecordsPerFile"]
    max_bytes = limits["maxBytesPerFile"]
    chunks = []
    current = []
    for position, record in enumerate(records):
        candidate = current + [record]
        if current and (len(candidate) > max_records or json_byte_size({key: candidate}) > max_bytes):
            chunks.append(current)
            current = [record]
        else:
            current = candidate
        size = json_byte_size({key: current})
        if len(current) > max_records:
            raise LimitError(
                "第 %d 条记录使分片达到 %d 条，超过记录数上限 %d 且无法再拆。" % (position + 1, len(current), max_records)
            )
        if size > max_bytes:
            raise LimitError(
                "第 %d 条记录单独成文件后仍有 %d 字节，超过单文件字节上限 %d，无法通过拆分解决；请缩短该条记录的文本。"
                % (position + 1, size, max_bytes)
            )
    chunks.append(current)
    if len(chunks) == 1:
        return [(base + ".json", chunks[0])]
    return [("%s-%d.json" % (base, position + 1), chunk) for position, chunk in enumerate(chunks)]


def backup_file(path, backup_dir):
    """E7：备份到仓库根 backups/，命名为 <文件名>.<时间戳>.bak，并保留一份 <文件名>.bak。"""
    if not os.path.isfile(path):
        return []
    os.makedirs(backup_dir, exist_ok=True)
    name = os.path.basename(path)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    made = []
    for target_name in ["%s.%s.bak" % (name, stamp), "%s.bak" % name]:
        target = os.path.join(backup_dir, target_name)
        shutil.copyfile(path, target)
        made.append(target)
    return made


def snapshot_outdated(repo_root, index):
    """离线快照 web/data-snapshot.js 是否可能已过期（缺少当前任一分片名即视为过期）。"""
    path = os.path.join(repo_root, SNAPSHOT_RELATIVE)
    if not os.path.isfile(path):
        return True
    try:
        text, _had_bom = read_text(path)
    except (EditorError, OSError):
        return True
    for name in list(index.get("companies") or []) + list(index.get("schools") or []):
        if name not in text:
            return True
    return False


def save_board(data_dir, backup_dir, board, records, index=None, limits=None, do_backup=True, targets=None):
    """校验 → 规范化 → 拆分 → 写盘 → 同步 index.json。返回结果报告字典。"""
    spec = BOARDS[board]
    data_dir = os.path.abspath(data_dir)
    backup_dir = os.path.abspath(backup_dir)
    if backup_dir == data_dir or backup_dir.startswith(data_dir + os.sep):
        raise EditorError("备份目录不能位于 data/ 内（会破坏 data/ 文件白名单）：%s" % posix_path(backup_dir))
    errors = validate_records(board, records, targets)
    if errors:
        raise ValidationFailed(errors)
    notes = []
    normalized = normalize_records(board, records, notes)
    warnings = []
    if index is None:
        index, index_warnings = load_index(data_dir)
        warnings.extend(index_warnings)
    if limits is None:
        limits = normalize_limits(index.get("limits"), warnings)
    chunks = plan_shards(board, normalized, limits)
    new_names = [name for name, _chunk in chunks]
    old_names = list(shard_names_for(index, board))
    pattern = shard_pattern(spec["base"])
    for file_name in sorted(os.listdir(data_dir)):
        if pattern.match(file_name) and file_name not in old_names:
            old_names.append(file_name)
    backups = []
    backup_targets = list(new_names) + [INDEX_FILENAME]
    for file_name in old_names:
        if file_name not in backup_targets:
            backup_targets.append(file_name)
    for file_name in backup_targets:
        target = os.path.join(data_dir, file_name)
        if do_backup and os.path.isfile(target):
            backups.extend(backup_file(target, backup_dir))
    written = {}
    for file_name, chunk in chunks:
        written[file_name] = write_json_document(os.path.join(data_dir, file_name), {spec["key"]: chunk})
    removed = []
    for file_name in old_names:
        if file_name in new_names:
            continue
        target = os.path.join(data_dir, file_name)
        if os.path.isfile(target):
            os.remove(target)
            removed.append(file_name)
    updated_index = {
        "version": 1,
        "limits": {
            "maxRecordsPerFile": limits["maxRecordsPerFile"],
            "maxBytesPerFile": limits["maxBytesPerFile"],
        },
        "companies": [],
        "schools": [],
        "comments": [],
        "site": normalize_site(index.get("site"), None),
    }
    for board_id in BOARD_ORDER:
        values = list(new_names) if board_id == board else list(index.get(board_id) or [])
        if not values:
            values = [BOARDS[board_id]["base"] + ".json"]
        updated_index[board_id] = values
    write_json_document(os.path.join(data_dir, INDEX_FILENAME), updated_index)
    extra, missing = data_dir_consistency(data_dir, updated_index)
    for file_name in missing:
        warnings.append("index.json 列出的分片不存在：data/%s。" % file_name)
    for file_name in extra:
        warnings.append("data/ 中存在未被 index.json 列出的文件：%s。" % file_name)
    return {
        "board": board,
        "shards": new_names,
        "entries": len(normalized),
        "split": len(new_names) > 1,
        "bytes": written,
        "removed": removed,
        "backups": backups,
        "notes": notes,
        "warnings": warnings,
        "index": updated_index,
        "snapshot_stale": snapshot_outdated(os.path.dirname(data_dir), updated_index),
    }


def describe_plan(board, chunks):
    spec = BOARDS[board]
    key = spec["key"]
    parts = []
    for name, chunk in chunks:
        parts.append("· %s：%d 条 / %d 字节" % (name, len(chunk), json_byte_size({key: chunk})))
    return chr(10).join(parts)



# --------------------------------------------------------------------------- #
# 附加键值表（前向兼容：SPEC §3.4 允许表外新键，新增键无需改前端）
# --------------------------------------------------------------------------- #
class KeyValueEditor(object):
    def __init__(self, parent, get_target, reserved, on_change, title, height=4):
        self.get_target = get_target
        self.reserved = reserved
        self.on_change = on_change
        self.frame = ttk.LabelFrame(parent, text=title, padding=8)
        body = ttk.Frame(self.frame)
        body.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            body, columns=("key", "value"), show="headings", height=height, selectmode="browse"
        )
        self.tree.heading("key", text="键")
        self.tree.heading("value", text="值")
        self.tree.column("key", width=140, anchor="w", stretch=False)
        self.tree.column("value", width=360, anchor="w")
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        editor = ttk.Frame(self.frame)
        editor.pack(fill="x", pady=(6, 0))
        self.key_var = tk.StringVar()
        self.value_var = tk.StringVar()
        ttk.Label(editor, text="键").pack(side="left")
        ttk.Entry(editor, textvariable=self.key_var, width=14).pack(side="left", padx=(4, 10))
        ttk.Label(editor, text="值").pack(side="left")
        ttk.Entry(editor, textvariable=self.value_var).pack(side="left", padx=(4, 10), fill="x", expand=True)
        for label, command in (("添加 / 更新", self.apply), ("删除", self.remove), ("清空输入", self.clear_inputs)):
            ttk.Button(editor, text=label, command=command).pack(side="left", padx=2)
        ttk.Label(
            self.frame,
            text="值按 JSON 标量解析（数字 / true / false / null / 字符串）；这里可维护 SPEC 允许的表外新键。",
            foreground="#666666",
        ).pack(anchor="w", pady=(4, 0))

    def reserved_keys(self):
        keys = self.reserved() if callable(self.reserved) else self.reserved
        return list(keys or [])

    def _target(self):
        target = self.get_target()
        return target if isinstance(target, dict) else None

    def extras(self, target=None):
        if target is None:
            target = self._target()
        if not isinstance(target, dict):
            return []
        reserved = self.reserved_keys()
        return [(key, value) for key, value in target.items() if key not in reserved]

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        target = self._target()
        for position, (key, value) in enumerate(self.extras(target)):
            self.tree.insert("", "end", iid=str(position), values=(key, format_value_text(value)))
        if target is None:
            self.clear_inputs()

    def _on_select(self, _event=None):
        selection = self.tree.selection()
        if not selection:
            return
        extras = self.extras()
        position = int(selection[0])
        if 0 <= position < len(extras):
            key, value = extras[position]
            self.key_var.set(key)
            self.value_var.set(format_value_text(value))

    def apply(self):
        target = self._target()
        if target is None:
            messagebox.showwarning("无法编辑", "请先在左侧选择一个条目。", parent=self.frame)
            return
        key = self.key_var.get().strip()
        if key == "":
            messagebox.showwarning("键不能为空", "请填写字段名。", parent=self.frame)
            return
        if key in self.reserved_keys():
            messagebox.showwarning(
                "保留字段", "「%s」由专用控件编辑，请勿在这里重复填写。" % key, parent=self.frame
            )
            return
        target[key] = parse_value_text(self.value_var.get())
        self.refresh()
        self.on_change()

    def remove(self):
        target = self._target()
        if target is None:
            return
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("未选择", "请先在列表中选择要删除的字段。", parent=self.frame)
            return
        extras = self.extras()
        position = int(selection[0])
        if 0 <= position < len(extras):
            target.pop(extras[position][0], None)
            self.clear_inputs()
            self.refresh()
            self.on_change()

    def clear_inputs(self):
        self.key_var.set("")
        self.value_var.set("")


# --------------------------------------------------------------------------- #
# 主界面
# --------------------------------------------------------------------------- #
class EditorApp(object):
    def __init__(self, root, board="companies", data_dir=None, backup_dir=None):
        default_data, default_backup = default_paths()
        self.root = root
        self.data_dir = os.path.abspath(data_dir or default_data)
        self.backup_dir = os.path.abspath(backup_dir or default_backup)
        self.repo_root = os.path.dirname(self.data_dir)
        self.board = board if board in BOARDS else "companies"
        self.index = default_index()
        self.limits = dict(DEFAULT_LIMITS)
        self.records = []
        self.shards = []
        self.blocked = ""
        self.dirty = False
        self.selected = -1
        self.job_selected = -1
        self.review_selected = -1
        self._syncing = False

        self.board_var = tk.StringVar(value=self.board)
        self.path_var = tk.StringVar(value="")
        self.limits_var = tk.StringVar(value="")
        self.count_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="就绪")
        self.notice_var = tk.StringVar(value="")
        self.form_hint_var = tk.StringVar(value="")
        self.name_var = tk.StringVar()
        self.category_var = tk.StringVar()
        self.school_weekend_var = tk.StringVar()
        self.school_makeup_var = tk.StringVar()
        self._note_loaded = ""
        self.job_name_var = tk.StringVar()
        self.job_situation_var = tk.StringVar()
        self.job_weekend_var = tk.StringVar(value=NOT_SET)
        self.job_hours_var = tk.StringVar(value=NOT_SET)
        self.review_content_var = tk.StringVar()
        self.review_number_var = tk.StringVar(value="-")
        self.comment_board_var = tk.StringVar(value=COMMENT_BOARD_VALUES[0])
        self.comment_target_var = tk.StringVar()
        self.comment_nickname_var = tk.StringVar()
        self.comment_date_var = tk.StringVar()
        self.comment_filter_board_var = tk.StringVar(value="全部")
        self.comment_filter_target_var = tk.StringVar()
        self._comment_content_loaded = ""
        self.target_names = {}
        self.visible = []

        self._build_ui()
        self._bind_shortcuts()
        self._bind_traces()
        self.reload(first=True)

    # ------------------------------ 界面搭建 ------------------------------ #
    def _build_ui(self):
        self.root.title(APP_TITLE)
        self.root.minsize(980, 640)
        self.root.geometry("1200x780")
        self._build_menu()

        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)

        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="板块：").pack(side="left")
        for board_id in BOARD_ORDER:
            ttk.Radiobutton(
                toolbar,
                text=BOARDS[board_id]["label"],
                value=board_id,
                variable=self.board_var,
                command=self._on_board_change,
            ).pack(side="left", padx=(0, 8))
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(toolbar, text="打开文件… (Ctrl+O)", command=self.choose_file).pack(side="left", padx=2)
        ttk.Button(toolbar, text="重新加载", command=self.reload).pack(side="left", padx=2)
        ttk.Button(toolbar, text="保存 (Ctrl+S)", command=self.save).pack(side="left", padx=2)
        ttk.Button(toolbar, text="检查上限并拆分…", command=self.check_limits).pack(side="left", padx=2)
        ttk.Button(toolbar, text="帮助", command=self.show_help).pack(side="left", padx=2)

        info = ttk.Frame(outer)
        info.pack(fill="x", pady=(8, 4))
        ttk.Label(info, textvariable=self.path_var, foreground="#444444").pack(side="left")
        ttk.Label(info, textvariable=self.limits_var, foreground="#666666").pack(side="right")

        self.notice_label = ttk.Label(
            outer, textvariable=self.notice_var, foreground="#a05000", wraplength=1100, justify="left"
        )
        self.notice_label.pack(fill="x")

        paned = ttk.Panedwindow(outer, orient="horizontal")
        paned.pack(fill="both", expand=True, pady=(6, 0))

        left = ttk.Frame(paned)
        paned.add(left, weight=1)
        ttk.Label(left, text="条目列表", font=("", 10, "bold")).pack(anchor="w")
        list_holder = ttk.Frame(left)
        list_holder.pack(fill="both", expand=True, pady=(4, 0))
        self.listbox = tk.Listbox(list_holder, exportselection=False, activestyle="none")
        list_scroll = ttk.Scrollbar(list_holder, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=list_scroll.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        list_scroll.pack(side="right", fill="y")
        self.listbox.bind("<<ListboxSelect>>", self._on_record_select)

        record_buttons = ttk.Frame(left)
        record_buttons.pack(fill="x", pady=4)
        for label, command in (
            ("新增", self.add_record),
            ("删除", self.delete_record),
            ("重命名", self.rename_record),
            ("上移", lambda: self.move_record(-1)),
            ("下移", lambda: self.move_record(1)),
        ):
            ttk.Button(record_buttons, text=label, command=command).pack(side="left", padx=2)
        ttk.Label(left, textvariable=self.count_var, foreground="#444444").pack(anchor="w")

        self.comment_filter_frame = ttk.Frame(left)
        self.comment_filter_frame.pack(fill="x", pady=(4, 0))
        ttk.Label(self.comment_filter_frame, text="筛选：板块").pack(side="left")
        self.comment_filter_board_combo = ttk.Combobox(
            self.comment_filter_frame, textvariable=self.comment_filter_board_var,
            values=["全部"] + COMMENT_BOARD_VALUES, state="readonly", width=8,
        )
        self.comment_filter_board_combo.pack(side="left", padx=(4, 8))
        self.comment_filter_board_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_list())
        ttk.Label(self.comment_filter_frame, text="目标").pack(side="left")
        self.comment_filter_target_entry = ttk.Entry(
            self.comment_filter_frame, textvariable=self.comment_filter_target_var, width=14
        )
        self.comment_filter_target_entry.pack(side="left", padx=(4, 0), fill="x", expand=True)
        self.comment_filter_target_var.trace_add("write", lambda *_args: self._on_comment_filter_changed())
        self.comment_filter_board_var.trace_add("write", lambda *_args: self._on_comment_filter_changed())
        self.comment_filter_board_var.trace_add("write", lambda *_args: self._on_comment_filter_changed())

        right = ttk.Frame(paned)
        paned.add(right, weight=3)
        self.notebook = ttk.Notebook(right)
        self.notebook.pack(fill="both", expand=True)

        self.tab_record = ttk.Frame(self.notebook, padding=10)
        self.tab_jobs = ttk.Frame(self.notebook, padding=10)
        self.tab_reviews = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.tab_record, text="条目信息")
        self.notebook.add(self.tab_jobs, text="岗位")
        self.notebook.add(self.tab_reviews, text="评价")

        self._build_record_tab()
        self._build_jobs_tab()
        self._build_reviews_tab()

        status = ttk.Frame(outer)
        status.pack(fill="x", pady=(6, 0))
        ttk.Label(status, textvariable=self.status_var, anchor="w", foreground="#333333").pack(
            side="left", fill="x", expand=True
        )

    def _build_menu(self):
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="打开文件…", accelerator="Ctrl+O", command=self.choose_file)
        file_menu.add_command(label="重新加载", command=self.reload)
        file_menu.add_separator()
        file_menu.add_command(label="保存", accelerator="Ctrl+S", command=self.save)
        file_menu.add_command(label="检查上限并拆分…", command=self.check_limits)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.on_close)
        menubar.add_cascade(label="文件", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=False)
        edit_menu.add_command(label="新增条目", command=self.add_record)
        edit_menu.add_command(label="删除条目", command=self.delete_record)
        edit_menu.add_command(label="重命名条目", command=self.rename_record)
        edit_menu.add_command(label="上移条目", command=lambda: self.move_record(-1))
        edit_menu.add_command(label="下移条目", command=lambda: self.move_record(1))
        menubar.add_cascade(label="编辑", menu=edit_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="字段与校验说明", command=self.show_help)
        help_menu.add_command(label="关于", command=self.show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)
        self.root.configure(menu=menubar)

    def _build_record_tab(self):
        tab = self.tab_record
        tab.columnconfigure(0, weight=1)

        self.name_row = ttk.Frame(tab)
        self.name_row.grid(row=0, column=0, sticky="we", pady=3)
        row = self.name_row
        ttk.Label(row, text="名称 *", width=12).pack(side="left")
        self.name_entry = ttk.Entry(row, textvariable=self.name_var)
        self.name_entry.pack(side="left", fill="x", expand=True)

        self.company_row = ttk.Frame(tab)
        self.company_row.grid(row=1, column=0, sticky="we", pady=3)
        ttk.Label(self.company_row, text="类别 *", width=12).pack(side="left")
        self.category_combo = ttk.Combobox(
            self.company_row, textvariable=self.category_var, values=COMPANY_CATEGORY_VALUES, state="readonly", width=16
        )
        self.category_combo.pack(side="left")
        ttk.Label(self.company_row, text="  枚举：制造业 / 消费零售·餐饮 / 新能源汽车 / 互联网·科技").pack(side="left")

        self.school_weekend_row = ttk.Frame(tab)
        self.school_weekend_row.grid(row=2, column=0, sticky="we", pady=3)
        ttk.Label(self.school_weekend_row, text="双休情况 *", width=12).pack(side="left")
        self.school_weekend_entry = ttk.Entry(self.school_weekend_row, textvariable=self.school_weekend_var)
        self.school_weekend_entry.pack(side="left", fill="x", expand=True)

        self.school_makeup_row = ttk.Frame(tab)
        self.school_makeup_row.grid(row=3, column=0, sticky="we", pady=3)
        ttk.Label(self.school_makeup_row, text="补课情况", width=12).pack(side="left")
        self.school_makeup_entry = ttk.Entry(self.school_makeup_row, textvariable=self.school_makeup_var)
        self.school_makeup_entry.pack(side="left", fill="x", expand=True)

        self.note_row = ttk.Frame(tab)
        self.note_row.grid(row=4, column=0, sticky="we", pady=3)
        note_row = self.note_row
        ttk.Label(note_row, text="备注", width=12).pack(side="left", anchor="n")
        note_holder = ttk.Frame(note_row)
        note_holder.pack(side="left", fill="x", expand=True)
        self.note_text = tk.Text(note_holder, height=3, wrap="word", undo=True)
        note_scroll = ttk.Scrollbar(note_holder, orient="vertical", command=self.note_text.yview)
        self.note_text.configure(yscrollcommand=note_scroll.set)
        self.note_text.pack(side="left", fill="both", expand=True)
        note_scroll.pack(side="right", fill="y")
        self.note_text.bind("<<Modified>>", self._on_note_modified)

        self.comment_board_row = ttk.Frame(tab)
        self.comment_board_row.grid(row=7, column=0, sticky="we", pady=3)
        ttk.Label(self.comment_board_row, text="板块 *", width=12).pack(side="left")
        self.comment_board_combo = ttk.Combobox(
            self.comment_board_row, textvariable=self.comment_board_var,
            values=COMMENT_BOARD_VALUES, state="readonly", width=10,
        )
        self.comment_board_combo.pack(side="left")
        ttk.Label(self.comment_board_row, text="  枚举：公司 / 学校").pack(side="left")

        self.comment_target_row = ttk.Frame(tab)
        self.comment_target_row.grid(row=8, column=0, sticky="we", pady=3)
        ttk.Label(self.comment_target_row, text="目标 *", width=12).pack(side="left")
        self.comment_target_combo = ttk.Combobox(self.comment_target_row, textvariable=self.comment_target_var)
        self.comment_target_combo.pack(side="left", fill="x", expand=True)
        ttk.Label(self.comment_target_row, text="必须是该板块条目的「名称」", foreground="#666666").pack(
            side="left", padx=(6, 0)
        )

        self.comment_content_row = ttk.Frame(tab)
        self.comment_content_row.grid(row=9, column=0, sticky="nsew", pady=3)
        ttk.Label(self.comment_content_row, text="内容 *", width=12).pack(side="left", anchor="n")
        comment_holder = ttk.Frame(self.comment_content_row)
        comment_holder.pack(side="left", fill="both", expand=True)
        self.comment_content_text = tk.Text(comment_holder, height=5, wrap="word", undo=True)
        comment_scroll = ttk.Scrollbar(comment_holder, orient="vertical", command=self.comment_content_text.yview)
        self.comment_content_text.configure(yscrollcommand=comment_scroll.set)
        self.comment_content_text.pack(side="left", fill="both", expand=True)
        comment_scroll.pack(side="right", fill="y")
        self.comment_content_text.bind("<<Modified>>", self._on_comment_content_modified)
        tab.rowconfigure(9, weight=1)

        self.comment_meta_row = ttk.Frame(tab)
        self.comment_meta_row.grid(row=10, column=0, sticky="we", pady=3)
        ttk.Label(self.comment_meta_row, text="昵称", width=12).pack(side="left")
        self.comment_nickname_entry = ttk.Entry(self.comment_meta_row, textvariable=self.comment_nickname_var, width=18)
        self.comment_nickname_entry.pack(side="left")
        ttk.Label(self.comment_meta_row, text="日期").pack(side="left", padx=(12, 4))
        self.comment_date_entry = ttk.Entry(self.comment_meta_row, textvariable=self.comment_date_var, width=14)
        self.comment_date_entry.pack(side="left")
        ttk.Label(self.comment_meta_row, text="YYYY-MM-DD（默认当天）", foreground="#666666").pack(side="left", padx=(6, 0))

        hint = ttk.Label(tab, textvariable=self.form_hint_var, foreground="#666666", wraplength=760, justify="left")
        hint.grid(row=5, column=0, sticky="we", pady=(4, 8))

        self.record_kv = KeyValueEditor(
            tab, self.current_record, self._record_reserved, lambda: self._touch(refresh_list=True),
            "条目其它字段（SPEC 允许的表外新键，如 类别 / 行业）",
        )
        self.record_kv.frame.grid(row=6, column=0, sticky="nsew")
        tab.rowconfigure(6, weight=1)

    def _build_jobs_tab(self):
        tab = self.tab_jobs
        tab.columnconfigure(0, weight=1)

        self.tree_jobs = ttk.Treeview(
            tab,
            columns=("name", "situation", "weekend", "hours"),
            show="headings",
            height=8,
            selectmode="browse",
        )
        for column, title, width in (
            ("name", "岗位名称", 180),
            ("situation", "情况", 300),
            ("weekend", "双休", 90),
            ("hours", "八小时工作制", 110),
        ):
            self.tree_jobs.heading(column, text=title)
            self.tree_jobs.column(column, width=width, anchor="w")
        jobs_scroll = ttk.Scrollbar(tab, orient="vertical", command=self.tree_jobs.yview)
        self.tree_jobs.configure(yscrollcommand=jobs_scroll.set)
        self.tree_jobs.grid(row=0, column=0, sticky="nsew")
        jobs_scroll.grid(row=0, column=1, sticky="ns")
        self.tree_jobs.bind("<<TreeviewSelect>>", self._on_job_select)

        buttons = ttk.Frame(tab)
        buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        for label, command in (
            ("新增岗位", self.add_job),
            ("删除岗位", self.delete_job),
            ("上移", lambda: self.move_job(-1)),
            ("下移", lambda: self.move_job(1)),
        ):
            ttk.Button(buttons, text=label, command=command).pack(side="left", padx=2)
        ttk.Label(buttons, text="选中行后直接修改下方字段即可生效", foreground="#666666").pack(side="left", padx=8)

        form = ttk.Frame(tab)
        form.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="岗位名称 *").grid(row=0, column=0, sticky="e", padx=(0, 8), pady=3)
        self.job_name_entry = ttk.Entry(form, textvariable=self.job_name_var)
        self.job_name_entry.grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Label(form, text="情况").grid(row=1, column=0, sticky="e", padx=(0, 8), pady=3)
        self.job_situation_entry = ttk.Entry(form, textvariable=self.job_situation_var)
        self.job_situation_entry.grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Label(form, text="双休").grid(row=2, column=0, sticky="e", padx=(0, 8), pady=3)
        self.job_weekend_combo = ttk.Combobox(
            form, textvariable=self.job_weekend_var, values=[NOT_SET] + YES_NO_UNKNOWN_VALUES, state="readonly", width=10
        )
        self.job_weekend_combo.grid(row=2, column=1, sticky="w", pady=3)
        ttk.Label(form, text="八小时工作制").grid(row=3, column=0, sticky="e", padx=(0, 8), pady=3)
        self.job_hours_combo = ttk.Combobox(
            form, textvariable=self.job_hours_var, values=[NOT_SET] + YES_NO_UNKNOWN_VALUES, state="readonly", width=10
        )
        self.job_hours_combo.grid(row=3, column=1, sticky="w", pady=3)

        self.job_kv = KeyValueEditor(
            tab, self.current_job, JOB_RESERVED_KEYS, lambda: self._touch(refresh_list=True),
            "岗位其它字段", height=3,
        )
        self.job_kv.frame.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=(10, 0))
        tab.rowconfigure(0, weight=1)
        tab.rowconfigure(3, weight=1)

    def _build_reviews_tab(self):
        tab = self.tab_reviews
        tab.columnconfigure(0, weight=1)

        self.tree_reviews = ttk.Treeview(
            tab, columns=("number", "content"), show="headings", height=8, selectmode="browse"
        )
        self.tree_reviews.heading("number", text="编号")
        self.tree_reviews.heading("content", text="内容")
        self.tree_reviews.column("number", width=60, anchor="center", stretch=False)
        self.tree_reviews.column("content", width=460, anchor="w")
        reviews_scroll = ttk.Scrollbar(tab, orient="vertical", command=self.tree_reviews.yview)
        self.tree_reviews.configure(yscrollcommand=reviews_scroll.set)
        self.tree_reviews.grid(row=0, column=0, sticky="nsew")
        reviews_scroll.grid(row=0, column=1, sticky="ns")
        self.tree_reviews.bind("<<TreeviewSelect>>", self._on_review_select)

        buttons = ttk.Frame(tab)
        buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        for label, command in (
            ("新增评价", self.add_review),
            ("删除评价", self.delete_review),
            ("上移", lambda: self.move_review(-1)),
            ("下移", lambda: self.move_review(1)),
        ):
            ttk.Button(buttons, text=label, command=command).pack(side="left", padx=2)
        ttk.Label(buttons, text="编号保存时按当前顺序从 1 自动重排", foreground="#666666").pack(side="left", padx=8)

        form = ttk.Frame(tab)
        form.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="编号").grid(row=0, column=0, sticky="e", padx=(0, 8), pady=3)
        ttk.Label(form, textvariable=self.review_number_var, foreground="#444444").grid(
            row=0, column=1, sticky="w", pady=3
        )
        ttk.Label(form, text="内容 *").grid(row=1, column=0, sticky="e", padx=(0, 8), pady=3)
        self.review_content_entry = ttk.Entry(form, textvariable=self.review_content_var)
        self.review_content_entry.grid(row=1, column=1, sticky="ew", pady=3)

        self.review_kv = KeyValueEditor(
            tab, self.current_review, REVIEW_RESERVED_KEYS, lambda: self._touch(refresh_list=True),
            "评价其它字段", height=3,
        )
        self.review_kv.frame.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=(10, 0))
        tab.rowconfigure(0, weight=1)
        tab.rowconfigure(3, weight=1)

    def _bind_shortcuts(self):
        for sequence in ("<Control-s>", "<Control-S>"):
            self.root.bind_all(sequence, self._on_save_event)
        for sequence in ("<Control-o>", "<Control-O>"):
            self.root.bind_all(sequence, self._on_open_event)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _bind_traces(self):
        for var in (self.name_var, self.category_var, self.school_weekend_var, self.school_makeup_var):
            var.trace_add("write", self._on_record_field)
        for var in (self.job_name_var, self.job_situation_var, self.job_weekend_var, self.job_hours_var):
            var.trace_add("write", self._on_job_field)
        self.review_content_var.trace_add("write", self._on_review_field)
        for var in (self.comment_board_var, self.comment_target_var, self.comment_nickname_var, self.comment_date_var):
            var.trace_add("write", self._on_comment_field)

    # ------------------------------ 数据加载 ------------------------------ #
    def reload(self, board=None, first=False):
        if board and board in BOARDS:
            self.board = board
        self.board_var.set(self.board)
        warnings = []
        self.index, index_warnings = load_index(self.data_dir)
        warnings.extend(index_warnings)
        self.limits = normalize_limits(self.index.get("limits"), warnings)
        self.blocked = ""
        try:
            self.records, self.shards = load_board(self.data_dir, self.board, self.index, warnings)
        except EditorError as exc:
            self.records = []
            self.shards = shard_names_for(self.index, self.board)
            self.blocked = str(exc) + chr(10) + "为避免覆盖，保存已被阻止；请先修正该文件，再点击「重新加载」。"
        self.dirty = False
        self.selected = -1
        self.job_selected = -1
        self.review_selected = -1
        self.target_names = load_target_names(self.data_dir, self.index)
        self._apply_board_layout()
        self._refresh_all()
        self._update_title()
        self._update_counts()
        self.limits_var.set(
            "单文件上限：%d 条 / %d 字节（先到为准）"
            % (self.limits["maxRecordsPerFile"], self.limits["maxBytesPerFile"])
        )
        notes = list(warnings)
        if self.blocked:
            notes.append(self.blocked.replace(chr(10), " "))
        self.notice_var.set("；".join(notes[:4]))
        if first and self.blocked:
            messagebox.showerror("数据文件结构非法", self.blocked, parent=self.root)
        self._set_status(
            "已加载%s板块：%d 条记录；分片：%s。"
            % (BOARDS[self.board]["label"], len(self.records), "、".join(self.shards) if self.shards else "-")
        )
        return True

    def _apply_board_layout(self):
        spec = BOARDS[self.board]
        kind = spec["kind"]
        if kind == "company":
            self.name_row.grid()
            self.note_row.grid()
            self.company_row.grid()
            self.school_weekend_row.grid_remove()
            self.school_makeup_row.grid_remove()
            self._show_comment_rows(False)
            self._show_filter(False)
            self._show_tab(self.tab_jobs, True)
            self._show_tab(self.tab_reviews, True)
            detail = "类别必选（制造业 / 消费零售·餐饮 / 新能源汽车 / 互联网·科技），岗位至少一条且岗位名称不得重复"
            self.form_hint_var.set("名称必填、同一板块内不得重复（1–80 字）；%s。" % detail)
        elif kind == "school":
            self.name_row.grid()
            self.note_row.grid()
            self.company_row.grid_remove()
            self.school_weekend_row.grid()
            self.school_makeup_row.grid()
            self._show_comment_rows(False)
            self._show_filter(False)
            self._show_tab(self.tab_jobs, False)
            self._show_tab(self.tab_reviews, True)
            detail = "双休情况必填，补课情况可留空"
            self.form_hint_var.set("名称必填、同一板块内不得重复（1–80 字）；%s。" % detail)
        else:
            self.name_row.grid_remove()
            self.note_row.grid_remove()
            self.company_row.grid_remove()
            self.school_weekend_row.grid_remove()
            self.school_makeup_row.grid_remove()
            self._show_comment_rows(True)
            self._show_filter(True)
            self._show_tab(self.tab_jobs, False)
            self._show_tab(self.tab_reviews, False)
            self.form_hint_var.set(
                "评论字段：板块（公司 / 学校）与目标必填，目标必须是该板块现有条目的「名称」；"
                "内容 1–1000 字；昵称可选（≤ 40 字）；日期默认当天（YYYY-MM-DD）；编号保存时从 1 连续。"
            )
        self._refresh_target_choices()

    def _show_comment_rows(self, visible):
        for widget in (self.comment_board_row, self.comment_target_row, self.comment_content_row, self.comment_meta_row):
            if visible:
                widget.grid()
            else:
                widget.grid_remove()

    def _show_filter(self, visible):
        if visible:
            self.comment_filter_frame.pack(fill="x", pady=(4, 0))
        else:
            self.comment_filter_frame.pack_forget()

    def _refresh_target_choices(self):
        names = list(self.target_names.get(self.comment_board_var.get()) or [])
        self.comment_target_combo.configure(values=names)

    def _show_tab(self, tab, visible):
        try:
            self.notebook.tab(tab, state="normal" if visible else "hidden")
        except tk.TclError:
            try:
                if visible:
                    self.notebook.add(tab, text="岗位")
                else:
                    self.notebook.hide(tab)
            except tk.TclError:
                pass

    # ------------------------------ 刷新 ------------------------------ #
    def _refresh_all(self):
        self._refresh_list(select=0 if self.records else None)
        self._refresh_form()

    def _record_label(self, record, position):
        if not isinstance(record, dict):
            return "%d. （非法条目：不是对象）" % (position + 1)
        if BOARDS[self.board]["kind"] == "comment":
            return self._comment_label(record)
        name = record.get("名称")
        name = name.strip() if isinstance(name, str) and name.strip() else "（未命名）"
        spec = BOARDS[self.board]
        parts = []
        if spec["has_jobs"]:
            category = record.get("类别")
            parts.append(category if isinstance(category, str) and category else "类别未设置")
            jobs = record.get("岗位")
            parts.append("岗位 %d" % (len(jobs) if isinstance(jobs, list) else 0))
        else:
            weekend = record.get("双休情况")
            parts.append(weekend if isinstance(weekend, str) and weekend else "双休情况未填写")
        reviews = record.get("评价")
        parts.append("评价 %d" % (len(reviews) if isinstance(reviews, list) else 0))
        return "%d. %s  —  %s" % (position + 1, name, " · ".join(parts))

    def _comment_label(self, record):
        number = record.get("编号")
        number_text = str(number) if isinstance(number, int) and not isinstance(number, bool) else "?"
        board_value = record.get("板块")
        target = record.get("目标")
        nickname = record.get("昵称")
        date_value = record.get("日期")
        content = record.get("内容")
        summary = content.strip().replace(chr(10), " ") if isinstance(content, str) else ""
        if len(summary) > 40:
            summary = summary[:40] + "…"
        parts = [
            "[%s]" % (board_value if isinstance(board_value, str) and board_value else "板块未设置"),
            target if isinstance(target, str) and target else "目标未设置",
            nickname if isinstance(nickname, str) and nickname.strip() else "匿名",
            date_value if isinstance(date_value, str) and date_value else "无日期",
            summary if summary else "（内容为空）",
        ]
        return "%s. %s" % (number_text, " · ".join(parts))

    def _visible_indices(self):
        if BOARDS[self.board]["kind"] != "comment":
            return list(range(len(self.records)))
        board_filter = self.comment_filter_board_var.get()
        target_filter = self.comment_filter_target_var.get().strip()
        result = []
        for position, record in enumerate(self.records):
            if not isinstance(record, dict):
                result.append(position)
                continue
            if board_filter and board_filter != "全部" and record.get("板块") != board_filter:
                continue
            if target_filter:
                target = record.get("目标")
                if not isinstance(target, str) or target_filter not in target:
                    continue
            result.append(position)
        return result

    def _refresh_list(self, select=None):
        self.listbox.delete(0, "end")
        self.visible = self._visible_indices()
        for position in self.visible:
            self.listbox.insert("end", self._record_label(self.records[position], position))
        if not self.records:
            self.selected = -1
            return
        if select is None:
            select = self.selected
        if select not in self.visible:
            select = self.visible[0] if self.visible else -1
        self.selected = select
        self.listbox.selection_clear(0, "end")
        if select < 0:
            return
        row = self.visible.index(select)
        self.listbox.selection_set(row)
        self.listbox.activate(row)
        self.listbox.see(row)

    def _update_row_label(self, position):
        if not (0 <= position < len(self.records)):
            return
        if position not in self.visible:
            self._refresh_list()
            return
        row = self.visible.index(position)
        self.listbox.delete(row)
        self.listbox.insert(row, self._record_label(self.records[position], position))
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(row)

    def _refresh_form(self):
        record = self.current_record()
        editable = isinstance(record, dict)
        kind = BOARDS[self.board]["kind"]
        self._syncing = True
        try:
            if editable and kind == "comment":
                board_value = record.get("板块")
                self.comment_board_var.set(
                    board_value if isinstance(board_value, str) and board_value else COMMENT_BOARD_VALUES[0]
                )
                target = record.get("目标")
                self.comment_target_var.set(target if isinstance(target, str) else "")
                nickname = record.get("昵称")
                self.comment_nickname_var.set(nickname if isinstance(nickname, str) else "")
                date_value = record.get("日期")
                self.comment_date_var.set(date_value if isinstance(date_value, str) else "")
                content = record.get("内容")
                self._set_comment_content(content if isinstance(content, str) else "")
            elif editable:
                name = record.get("名称")
                self.name_var.set(name if isinstance(name, str) else "")
                if kind == "company":
                    category = record.get("类别")
                    self.category_var.set(category if isinstance(category, str) else "")
                else:
                    weekend = record.get("双休情况")
                    self.school_weekend_var.set(weekend if isinstance(weekend, str) else "")
                    makeup = record.get("补课情况")
                    self.school_makeup_var.set(makeup if isinstance(makeup, str) else "")
                note = record.get("备注")
                self._set_note_text(note if isinstance(note, str) else "")
            else:
                self.name_var.set("")
                self.category_var.set("")
                self.school_weekend_var.set("")
                self.school_makeup_var.set("")
                self.comment_board_var.set(COMMENT_BOARD_VALUES[0])
                self.comment_target_var.set("")
                self.comment_nickname_var.set("")
                self.comment_date_var.set("")
                self._set_note_text("")
                self._set_comment_content("")
        finally:
            self._syncing = False
        self._refresh_target_choices()
        self._set_record_widgets_enabled(editable)
        self.record_kv.refresh()
        self._refresh_jobs()
        self._refresh_reviews()

    def _set_record_widgets_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        combo_state = "readonly" if enabled else "disabled"
        for widget in (self.name_entry, self.school_weekend_entry, self.school_makeup_entry, self.note_text):
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass
        try:
            self.category_combo.configure(state=combo_state)
        except tk.TclError:
            pass
        for widget in (
            self.comment_target_combo,
            self.comment_nickname_entry,
            self.comment_date_entry,
            self.comment_content_text,
        ):
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass
        try:
            self.comment_board_combo.configure(state=combo_state)
        except tk.TclError:
            pass

    def _refresh_jobs(self):
        record = self.current_record()
        jobs = record.get("岗位") if isinstance(record, dict) else None
        if not isinstance(jobs, list):
            jobs = []
        self._syncing = True
        try:
            self.tree_jobs.delete(*self.tree_jobs.get_children())
            for position, job in enumerate(jobs):
                if isinstance(job, dict):
                    values = self._job_values(job)
                else:
                    values = ("（非法岗位：不是对象）", "", "", "")
                self.tree_jobs.insert("", "end", iid=str(position), values=values)
            if not jobs:
                self.job_selected = -1
            elif self.job_selected < 0 or self.job_selected >= len(jobs):
                self.job_selected = 0
            self._sync_job_fields()
        finally:
            self._syncing = False

    def _job_values(self, job):
        return (
            string_or_empty(job.get("岗位名称")),
            string_or_empty(job.get("情况")),
            string_or_empty(job.get("双休")) or NOT_SET,
            string_or_empty(job.get("八小时工作制")) or NOT_SET,
        )

    def _select_tree_row(self, tree, position):
        """只在选中行确实变化时调用 selection_set，避免 <<TreeviewSelect>> 自激循环。"""
        if position < 0:
            return
        iid = str(position)
        if tree.exists(iid) and tree.selection() != (iid,):
            tree.selection_set(iid)

    def _sync_job_fields(self):
        job = self.current_job()
        self._syncing = True
        try:
            if job is None:
                self.job_name_var.set("")
                self.job_situation_var.set("")
                self.job_weekend_var.set(NOT_SET)
                self.job_hours_var.set(NOT_SET)
            else:
                self.job_name_var.set(string_or_empty(job.get("岗位名称")))
                self.job_situation_var.set(string_or_empty(job.get("情况")))
                self.job_weekend_var.set(string_or_empty(job.get("双休")) or NOT_SET)
                self.job_hours_var.set(string_or_empty(job.get("八小时工作制")) or NOT_SET)
        finally:
            self._syncing = False
        self.job_kv.refresh()
        self._select_tree_row(self.tree_jobs, self.job_selected if job is not None else -1)

    def _refresh_reviews(self):
        record = self.current_record()
        reviews = record.get("评价") if isinstance(record, dict) else None
        if not isinstance(reviews, list):
            reviews = []
        self._syncing = True
        try:
            self.tree_reviews.delete(*self.tree_reviews.get_children())
            for position, review in enumerate(reviews):
                if isinstance(review, dict):
                    values = (position + 1, string_or_empty(review.get("内容")))
                else:
                    values = ("-", "（非法评价：不是对象）")
                self.tree_reviews.insert("", "end", iid=str(position), values=values)
            if not reviews:
                self.review_selected = -1
            elif self.review_selected < 0 or self.review_selected >= len(reviews):
                self.review_selected = 0
            self._sync_review_fields()
        finally:
            self._syncing = False

    def _sync_review_fields(self):
        review = self.current_review()
        self._syncing = True
        try:
            if review is None:
                self.review_number_var.set("-")
                self.review_content_var.set("")
            else:
                self.review_number_var.set("%d（保存时按当前顺序重排）" % (self.review_selected + 1))
                self.review_content_var.set(string_or_empty(review.get("内容")))
        finally:
            self._syncing = False
        self.review_kv.refresh()
        self._select_tree_row(self.tree_reviews, self.review_selected if review is not None else -1)

    def _touch(self, refresh_list=False, refresh_row=False):
        self.dirty = True
        if refresh_list:
            self._refresh_list()
        elif refresh_row:
            self._update_row_label(self.selected)
        self._update_title()
        self._update_counts()

    def _update_title(self):
        spec = BOARDS[self.board]
        if len(self.shards) <= 3:
            target = "、".join("data/" + name for name in self.shards) if self.shards else spec["base"] + ".json"
        else:
            target = "data/%d 个分片" % len(self.shards)
        marker = " *（未保存）" if self.dirty else ""
        self.root.title("%s — %s板块 · %s%s" % (APP_TITLE, spec["label"], target, marker))
        self.path_var.set(
            "数据目录：%s　|　当前%s分片：%s"
            % (posix_path(self.data_dir), spec["label"], "、".join(self.shards) if self.shards else "-")
        )

    def _update_counts(self):
        spec = BOARDS[self.board]
        self.count_var.set(
            "共 %d 条%s记录%s" % (len(self.records), spec["label"], "（有未保存修改）" if self.dirty else "")
        )

    def _set_status(self, message):
        self.status_var.set(message)

    # ------------------------------ 选中与字段联动 ------------------------------ #
    def current_record(self):
        if 0 <= self.selected < len(self.records):
            return self.records[self.selected]
        return None

    def current_job(self):
        record = self.current_record()
        jobs = record.get("岗位") if isinstance(record, dict) else None
        if isinstance(jobs, list) and 0 <= self.job_selected < len(jobs):
            job = jobs[self.job_selected]
            return job if isinstance(job, dict) else None
        return None

    def current_review(self):
        record = self.current_record()
        reviews = record.get("评价") if isinstance(record, dict) else None
        if isinstance(reviews, list) and 0 <= self.review_selected < len(reviews):
            review = reviews[self.review_selected]
            return review if isinstance(review, dict) else None
        return None

    def _record_reserved(self):
        return RECORD_RESERVED_KEYS[self.board]

    def _on_record_select(self, _event=None):
        selection = self.listbox.curselection()
        if not selection:
            return
        row = int(selection[0])
        if not (0 <= row < len(self.visible)):
            return
        self.selected = self.visible[row]
        self.job_selected = -1
        self.review_selected = -1
        self._refresh_form()
        self._update_counts()

    def _on_job_select(self, _event=None):
        selection = self.tree_jobs.selection()
        if not selection:
            return
        self.job_selected = int(selection[0])
        self._sync_job_fields()

    def _on_review_select(self, _event=None):
        selection = self.tree_reviews.selection()
        if not selection:
            return
        self.review_selected = int(selection[0])
        self._sync_review_fields()

    def _set_comment_content(self, value):
        """把评论内容写入多行文本框（不触发脏标记）。"""
        self.comment_content_text.delete("1.0", "end")
        if value:
            self.comment_content_text.insert("1.0", value)
        self._comment_content_loaded = self.comment_content_text.get("1.0", "end-1c")
        self.comment_content_text.edit_modified(False)

    def _on_comment_content_modified(self, _event=None):
        if not self.comment_content_text.edit_modified():
            return
        self.comment_content_text.edit_modified(False)
        if self._syncing:
            return
        value = self.comment_content_text.get("1.0", "end-1c")
        if value == self._comment_content_loaded:
            return
        self._comment_content_loaded = value
        record = self.current_record()
        if not isinstance(record, dict):
            return
        record["内容"] = value
        self._touch(refresh_row=True)

    def _on_comment_field(self, *_args):
        if self._syncing:
            return
        if BOARDS[self.board]["kind"] != "comment":
            return
        record = self.current_record()
        if not isinstance(record, dict):
            return
        record["板块"] = self.comment_board_var.get()
        record["目标"] = self.comment_target_var.get()
        nickname = self.comment_nickname_var.get()
        if nickname.strip() == "":
            record.pop("昵称", None)
        else:
            record["昵称"] = nickname
        date_value = self.comment_date_var.get()
        if date_value.strip() == "":
            record.pop("日期", None)
        else:
            record["日期"] = date_value
        self._refresh_target_choices()
        self._touch(refresh_row=True)

    def _on_comment_filter_changed(self):
        if self._syncing:
            return
        self._refresh_list()

    def _on_record_field(self, *_args):
        if self._syncing:
            return
        if BOARDS[self.board]["kind"] == "comment":
            return
        record = self.current_record()
        if not isinstance(record, dict):
            return
        record["名称"] = self.name_var.get()
        if BOARDS[self.board]["has_jobs"]:
            record["类别"] = self.category_var.get()
        else:
            record["双休情况"] = self.school_weekend_var.get()
            makeup = self.school_makeup_var.get()
            if makeup.strip() == "":
                record.pop("补课情况", None)
            else:
                record["补课情况"] = makeup
        self._touch(refresh_row=True)

    def _set_note_text(self, value):
        """把备注写入多行文本框（不触发脏标记）。"""
        self.note_text.delete("1.0", "end")
        if value:
            self.note_text.insert("1.0", value)
        self._note_loaded = self.note_text.get("1.0", "end-1c")
        self.note_text.edit_modified(False)

    def _on_note_modified(self, _event=None):
        if not self.note_text.edit_modified():
            return
        self.note_text.edit_modified(False)
        if self._syncing:
            return
        value = self.note_text.get("1.0", "end-1c")
        if value == self._note_loaded:
            return
        self._note_loaded = value
        record = self.current_record()
        if not isinstance(record, dict):
            return
        if value.strip() == "":
            record.pop("备注", None)
        else:
            record["备注"] = value
        self._touch(refresh_row=True)

    def _on_job_field(self, *_args):
        if self._syncing:
            return
        job = self.current_job()
        if job is None:
            return
        job["岗位名称"] = self.job_name_var.get()
        job["情况"] = self.job_situation_var.get()
        apply_optional_choice(job, "双休", self.job_weekend_var.get())
        apply_optional_choice(job, "八小时工作制", self.job_hours_var.get())
        iid = str(self.job_selected)
        if self.tree_jobs.exists(iid):
            self.tree_jobs.item(iid, values=self._job_values(job))
        self._touch(refresh_row=True)

    def _on_review_field(self, *_args):
        if self._syncing:
            return
        review = self.current_review()
        if review is None:
            return
        review["内容"] = self.review_content_var.get()
        iid = str(self.review_selected)
        if self.tree_reviews.exists(iid):
            self.tree_reviews.item(iid, values=(self.review_selected + 1, self.review_content_var.get()))
        self._touch(refresh_row=True)

    # ------------------------------ 条目维护（E3） ------------------------------ #
    def add_record(self):
        if self.blocked:
            messagebox.showerror("无法编辑", self.blocked, parent=self.root)
            return
        spec = BOARDS[self.board]
        base = "新公司" if spec["has_jobs"] else "新学校"
        existing = set()
        for record in self.records:
            if isinstance(record, dict) and isinstance(record.get("名称"), str):
                existing.add(record["名称"].strip())
        name = base
        counter = 2
        while name in existing:
            name = "%s %d" % (base, counter)
            counter += 1
        if spec["kind"] == "comment":
            numbers = [
                item.get("编号")
                for item in self.records
                if isinstance(item, dict)
                and isinstance(item.get("编号"), int)
                and not isinstance(item.get("编号"), bool)
            ]
            next_number = (max(numbers) + 1) if numbers else 1
            board_value = COMMENT_BOARD_VALUES[0]
            choices = list(self.target_names.get(board_value) or [])
            record = {
                "编号": next_number,
                "板块": board_value,
                "目标": choices[0] if choices else "",
                "内容": "",
                "昵称": "",
                "日期": datetime.date.today().isoformat(),
            }
        elif spec["has_jobs"]:
            record = {"名称": name, "类别": "", "岗位": [{"岗位名称": "新岗位", "情况": ""}], "评价": []}
        else:
            record = {"名称": name, "双休情况": "未知", "评价": []}
        self.records.append(record)
        self.selected = len(self.records) - 1
        self.job_selected = -1
        self.review_selected = -1
        self._refresh_list()
        self._refresh_form()
        self._touch()
        if spec["kind"] == "comment":
            self.comment_content_text.focus_set()
            self._set_status("已新增评论（编号 %s），请填写内容后保存。" % record.get("编号"))
        else:
            self.name_entry.focus_set()
            self.name_entry.selection_range(0, "end")
            self._set_status("已新增%s条目「%s」，请修改名称后保存。" % (spec["label"], name))

    def delete_record(self):
        record = self.current_record()
        if record is None:
            messagebox.showwarning("未选择", "请先在左侧列表中选择要删除的条目。", parent=self.root)
            return
        label = record.get("名称") if isinstance(record, dict) else "（非法条目）"
        if not messagebox.askyesno(
            "确认删除", "确定删除「%s」？该操作在保存后才会写入磁盘。" % label, parent=self.root
        ):
            return
        removed = self.records.pop(self.selected)
        self._refresh_list(select=min(self.selected, len(self.records) - 1))
        self._refresh_form()
        self._touch()
        self._set_status("已删除条目「%s」（尚未写入磁盘）。" % (removed.get("名称") if isinstance(removed, dict) else "非法条目"))

    def rename_record(self):
        if BOARDS[self.board]["kind"] == "comment":
            messagebox.showinfo(
                "评论没有名称字段",
                "评论没有「名称」字段；请直接编辑 板块 / 目标 / 内容 / 昵称 / 日期。",
                parent=self.root,
            )
            return
        record = self.current_record()
        if record is None:
            messagebox.showwarning("未选择", "请先在左侧列表中选择要重命名的条目。", parent=self.root)
            return
        if not isinstance(record, dict):
            messagebox.showwarning("无法重命名", "该条目不是对象，请先删除。", parent=self.root)
            return
        current = string_or_empty(record.get("名称"))
        name = simpledialog.askstring("重命名", "新的名称（1–80 字）：", initialvalue=current, parent=self.root)
        if name is None:
            return
        name = name.strip()
        if name == "":
            messagebox.showerror("名称非法", "名称不能为空。", parent=self.root)
            return
        if len(name) > NAME_MAX:
            messagebox.showerror("名称非法", "名称长度 %d 超过上限 %d。" % (len(name), NAME_MAX), parent=self.root)
            return
        for position, other in enumerate(self.records):
            if position == self.selected or not isinstance(other, dict):
                continue
            other_name = other.get("名称")
            if isinstance(other_name, str) and other_name.strip() == name:
                messagebox.showerror("名称重复", "「%s」与第 %d 条重复。" % (name, position + 1), parent=self.root)
                return
        record["名称"] = name
        self._refresh_list()
        self._refresh_form()
        self._touch()
        self._set_status("已重命名为「%s」（尚未写入磁盘）。" % name)

    def move_record(self, step):
        target = move_in_list(self.records, self.selected, step)
        if target == self.selected:
            return
        self.selected = target
        self._refresh_list()
        self._refresh_form()
        self._touch()

    # ------------------------------ 岗位与评价子表（E4） ------------------------------ #
    def _jobs_list(self, create=False):
        record = self.current_record()
        if not isinstance(record, dict):
            return None
        jobs = record.get("岗位")
        if not isinstance(jobs, list):
            if not create:
                return None
            jobs = []
            record["岗位"] = jobs
        return jobs

    def _reviews_list(self, create=False):
        record = self.current_record()
        if not isinstance(record, dict):
            return None
        reviews = record.get("评价")
        if not isinstance(reviews, list):
            if not create:
                return None
            reviews = []
            record["评价"] = reviews
        return reviews

    def add_job(self):
        jobs = self._jobs_list(create=True)
        if jobs is None:
            messagebox.showwarning("未选择条目", "请先在左侧选择一个公司条目。", parent=self.root)
            return
        jobs.append({"岗位名称": "新岗位", "情况": ""})
        self.job_selected = len(jobs) - 1
        self._refresh_jobs()
        self._touch(refresh_list=True)
        self.job_name_entry.focus_set()
        self.job_name_entry.selection_range(0, "end")

    def delete_job(self):
        jobs = self._jobs_list()
        job = self.current_job()
        if jobs is None or job is None:
            messagebox.showwarning("未选择岗位", "请先在上方列表中选择一个岗位。", parent=self.root)
            return
        if not messagebox.askyesno("确认删除", "确定删除岗位「%s」？" % string_or_empty(job.get("岗位名称")), parent=self.root):
            return
        jobs.pop(self.job_selected)
        self.job_selected = min(self.job_selected, len(jobs) - 1)
        self._refresh_jobs()
        self._touch(refresh_list=True)

    def move_job(self, step):
        jobs = self._jobs_list()
        if jobs is None:
            return
        target = move_in_list(jobs, self.job_selected, step)
        if target == self.job_selected:
            return
        self.job_selected = target
        self._refresh_jobs()
        self._touch(refresh_list=True)

    def add_review(self):
        reviews = self._reviews_list(create=True)
        if reviews is None:
            messagebox.showwarning("未选择条目", "请先在左侧选择一个条目。", parent=self.root)
            return
        reviews.append({"编号": len(reviews) + 1, "内容": "新评价"})
        self.review_selected = len(reviews) - 1
        self._refresh_reviews()
        self._touch(refresh_list=True)
        self.review_content_entry.focus_set()
        self.review_content_entry.selection_range(0, "end")

    def delete_review(self):
        reviews = self._reviews_list()
        review = self.current_review()
        if reviews is None or review is None:
            messagebox.showwarning("未选择评价", "请先在上方列表中选择一条评价。", parent=self.root)
            return
        if not messagebox.askyesno("确认删除", "确定删除第 %d 条评价？" % (self.review_selected + 1), parent=self.root):
            return
        reviews.pop(self.review_selected)
        self.review_selected = min(self.review_selected, len(reviews) - 1)
        self._refresh_reviews()
        self._touch(refresh_list=True)

    def move_review(self, step):
        reviews = self._reviews_list()
        if reviews is None:
            return
        target = move_in_list(reviews, self.review_selected, step)
        if target == self.review_selected:
            return
        self.review_selected = target
        self._refresh_reviews()
        self._touch(refresh_list=True)

    # ------------------------------ 保存（E5 / E6 / E7） ------------------------------ #
    def _show_errors(self, errors):
        limit = 20
        lines = ["保存被拒绝，请先修正以下 %d 处问题：" % len(errors), ""]
        for item in errors[:limit]:
            lines.append("· %s" % item)
        if len(errors) > limit:
            lines.append("… 其余 %d 处问题已省略。" % (len(errors) - limit))
        messagebox.showerror("数据校验未通过", chr(10).join(lines), parent=self.root)
        self._set_status("保存被拒绝：共 %d 处校验问题，数据未写入。" % len(errors))

    def check_limits(self):
        if self.blocked:
            messagebox.showerror("无法检查", self.blocked, parent=self.root)
            return False
        errors = validate_records(self.board, self.records, self.target_names)
        if errors:
            self._show_errors(errors)
            return False
        normalized = normalize_records(self.board, self.records, [])
        try:
            chunks = plan_shards(self.board, normalized, self.limits)
        except LimitError as exc:
            messagebox.showerror("超出单文件上限", str(exc), parent=self.root)
            return False
        plan = describe_plan(self.board, chunks)
        if len(chunks) <= 1:
            messagebox.showinfo(
                "上限检查",
                "当前 %d 条%s记录，未超过单文件上限（%d 条 / %d 字节）。"
                % (len(normalized), BOARDS[self.board]["label"], self.limits["maxRecordsPerFile"], self.limits["maxBytesPerFile"])
                + chr(10) + chr(10) + "保存后的写入计划：" + chr(10) + plan,
                parent=self.root,
            )
            self._set_status("上限检查通过：未超限。")
            return True
        answer = messagebox.askyesno(
            "需要拆分",
            "当前 %d 条%s记录超过单文件上限（%d 条 / %d 字节），一键拆分方案："
            % (len(normalized), BOARDS[self.board]["label"], self.limits["maxRecordsPerFile"], self.limits["maxBytesPerFile"])
            + chr(10) + chr(10) + plan + chr(10) + chr(10)
            + "拆分后不带编号的原文件将被删除，并同步更新 data/index.json（数组顺序 = 加载顺序）。是否立即拆分并保存？",
            parent=self.root,
        )
        if not answer:
            self._set_status("已取消：数据超限且未拆分。")
            return False
        return self.save(force_split=True)

    def save(self, force_split=False):
        if self.blocked:
            messagebox.showerror("无法保存", self.blocked, parent=self.root)
            return False
        errors = validate_records(self.board, self.records, self.target_names)
        if errors:
            self._show_errors(errors)
            return False
        notes = []
        normalized = normalize_records(self.board, self.records, notes)
        try:
            chunks = plan_shards(self.board, normalized, self.limits)
        except LimitError as exc:
            messagebox.showerror("超出单文件上限", str(exc), parent=self.root)
            self._set_status("保存被拒绝：超过上限且无法通过拆分解决。")
            return False
        if len(chunks) > 1 and not force_split:
            answer = messagebox.askyesno(
                "需要拆分",
                "合并后共 %d 条%s记录，超过单文件上限（%d 条 / %d 字节）。"
                % (len(normalized), BOARDS[self.board]["label"], self.limits["maxRecordsPerFile"], self.limits["maxBytesPerFile"])
                + chr(10) + chr(10) + "一键拆分方案：" + chr(10) + describe_plan(self.board, chunks)
                + chr(10) + chr(10)
                + "拆分后不带编号的 data/%s 将被删除，并同步更新 index.json（数组顺序 = 加载顺序）。是否继续保存？"
                % (BOARDS[self.board]["base"] + ".json"),
                parent=self.root,
            )
            if not answer:
                self._set_status("已取消保存：数据超限且未拆分。")
                return False
        try:
            result = save_board(
                self.data_dir,
                self.backup_dir,
                self.board,
                self.records,
                index=self.index,
                limits=self.limits,
                targets=self.target_names,
            )
        except ValidationFailed as exc:
            self._show_errors(exc.errors)
            return False
        except (EditorError, OSError) as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.root)
            self._set_status("保存失败：%s" % exc)
            return False
        board = self.board
        self.reload(board=board)
        self._report_save(result)
        return True

    def _report_save(self, result):
        spec = BOARDS[result["board"]]
        lines = ["已写入 data/%s" % "、data/".join(result["shards"])]
        lines.append("%s条目：%d 条" % (spec["label"], result["entries"]))
        for name in result["shards"]:
            lines.append("  · %s：%d 字节" % (name, result["bytes"].get(name, 0)))
        if result["split"]:
            lines.append(
                "已按单文件上限拆分为 %d 个分片，并同步更新 data/index.json（数组顺序 = 加载顺序）。"
                % len(result["shards"])
            )
        if result["removed"]:
            lines.append("已删除过期分片：%s" % "、".join(result["removed"]))
        if result["backups"]:
            lines.append("备份已写入仓库根 backups/（不在 data/ 内）：")
            for path in result["backups"]:
                lines.append("  · %s" % posix_path(path))
        for note in list(result["notes"]) + list(result["warnings"]):
            lines.append("提示：%s" % note)
        if result["snapshot_stale"]:
            lines.append("提示：离线快照 web/data-snapshot.js 可能已过期，需同步。")
        messagebox.showinfo("保存成功", chr(10).join(lines), parent=self.root)
        self._set_status(
            "已保存 %d 条%s记录；分片：%s。" % (result["entries"], spec["label"], "、".join(result["shards"]))
        )

    # ------------------------------ 板换、打开与退出（E1 / E8） ------------------------------ #
    def _confirm_save_if_dirty(self, action):
        if not self.dirty:
            return True
        answer = messagebox.askyesnocancel(
            "未保存的修改",
            "当前%s板块有未保存的修改。" % BOARDS[self.board]["label"]
            + chr(10) + chr(10) + "是否先保存再%s？" % action,
            parent=self.root,
        )
        if answer is None:
            return False
        if answer:
            return self.save()
        return True

    def _on_board_change(self):
        target = self.board_var.get()
        if target == self.board or target not in BOARDS:
            return
        if not self._confirm_save_if_dirty("切换板块"):
            self.board_var.set(self.board)
            return
        self.reload(board=target)

    def choose_file(self):
        if not self._confirm_save_if_dirty("打开其它文件"):
            return
        path = filedialog.askopenfilename(
            title="选择 data/ 下的 JSON 文件",
            initialdir=self.data_dir,
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
            parent=self.root,
        )
        if not path:
            return
        path = os.path.abspath(path)
        try:
            raw, _had_bom = read_json_document(path)
        except EditorError as exc:
            messagebox.showerror("无法读取", str(exc), parent=self.root)
            return
        board = None
        if isinstance(raw, dict):
            for board_id in BOARD_ORDER:
                if BOARDS[board_id]["key"] in raw:
                    board = board_id
                    break
        if board is None:
            messagebox.showerror(
                "无法识别板块", "文件顶层必须含「公司」或「学校」键：%s" % posix_path(path), parent=self.root
            )
            return
        if os.path.normcase(os.path.dirname(path)) != os.path.normcase(self.data_dir):
            messagebox.showwarning(
                "不在默认数据目录",
                "所选文件不在 %s 内；编辑器仍按 index.json 读取 %s 板块的全部分片。"
                % (posix_path(self.data_dir), BOARDS[board]["label"]),
                parent=self.root,
            )
        self.reload(board=board)
        self._set_status("已切换到%s板块（来源文件 %s）。" % (BOARDS[board]["label"], posix_path(path)))

    def _on_save_event(self, _event=None):
        self.save()
        return "break"

    def _on_open_event(self, _event=None):
        self.choose_file()
        return "break"

    def on_close(self):
        if self._confirm_save_if_dirty("退出"):
            self.root.destroy()

    def show_help(self):
        messagebox.showinfo(
            "字段与校验说明",
            chr(10).join(
                [
                    "板块：公司（data/companies.json）与学校（data/schools.json），分片由 data/index.json 决定。",
                    "",
                    "公司条目：名称（必填，1–80 字，同一板块内不重复）、类别（必填：制造业 / 消费零售·餐饮 /",
                    "　　　　　新能源汽车 / 互联网·科技）、岗位（必填的非空数组；岗位名称必填且同一公司内不重复，",
                    "　　　　　双休 / 八小时工作制 为 是/否/未知 或留空表示省略，情况为原文可留空）、备注（可选，多行）、",
                    "　　　　　评价子表（内容必填，编号保存时按顺序从 1 自动重排）。",
                    "学校条目：名称（同上）、双休情况（必填）、补课情况（可留空）、评价子表。",
                    "评论条目（data/comments.json）：编号（保存时从 1 连续重排）、板块（公司 / 学校）、",
                    "　　　　　目标（必须是该板块现有条目的「名称」，否则拒绝保存）、内容（1–1000 字）、",
                    "　　　　　昵称（可选 ≤ 40 字）、日期（YYYY-MM-DD，默认当天）；列表可按 板块 / 目标 过滤。",
                    "",
                    "单文件上限：%d 条记录 / %d 字节，先到为准；超限时保存会提示并支持一键拆分，"
                    % (DEFAULT_LIMITS["maxRecordsPerFile"], DEFAULT_LIMITS["maxBytesPerFile"]),
                    "拆分为 companies-N.json / schools-N.json（N 从 1 连续），原不带编号文件被删除，",
                    "并同步 index.json 的数组顺序（数组顺序 = 加载顺序）。",
                    "",
                    "保存前会校验：顶层形状、条目必须为对象、名称非空且不重复、类别与枚举取值、",
                    "岗位非空且岗位名称不重复、评价子表结构等；校验失败一律拒绝写入并给出明确提示。",
                    "保存前自动备份到仓库根 backups/（命名为 <文件名>.<时间戳>.bak；不在 data/ 内）。",
                    "写入统一为 UTF-8 无 BOM、ensure_ascii=False、indent=2。",
                    "数据变更后请同步 web/data-snapshot.js，否则 file:// 离线快照会过期。",
                ]
            ),
            parent=self.root,
        )

    def show_about(self):
        messagebox.showinfo(
            "关于",
            APP_TITLE + chr(10) + chr(10)
            + "用于可视化编辑 data/companies.json 与 data/schools.json（含上限校验与一键拆分）。" + chr(10)
            + "仅使用 Python 3 标准库（tkinter），无第三方依赖、无需联网。" + chr(10)
            + "契约文件：docs/SPEC.md v1.2 第 5 节 E1–E8。" + chr(10) + chr(10)
            + "数据目录：%s" % posix_path(self.data_dir),
            parent=self.root,
        )


def move_in_list(items, index, step):
    """在列表内上移 / 下移一项，返回新的位置。"""
    target = index + step
    if index < 0 or index >= len(items) or target < 0 or target >= len(items):
        return index
    items[index], items[target] = items[target], items[index]
    return target


# --------------------------------------------------------------------------- #
# 只读校验（--check）与入口
# --------------------------------------------------------------------------- #
def run_check(data_dir, board=None):
    warnings = []
    index, index_warnings = load_index(data_dir)
    warnings.extend(index_warnings)
    problems = []
    limits = normalize_limits(index.get("limits"), [])
    targets = [board] if board in BOARDS else list(BOARD_ORDER)
    loaded = {}
    for board_id in BOARD_ORDER:
        try:
            records, _names = load_board(data_dir, board_id, index, warnings)
        except EditorError as exc:
            records = []
            if board_id in targets:
                problems.append(str(exc))
        loaded[board_id] = records
    target_names = board_target_names(loaded)
    for board_id in targets:
        spec = BOARDS[board_id]
        records = loaded[board_id]
        errors = validate_records(board_id, records, target_names)
        if errors:
            problems.extend(errors)
            continue
        if spec["kind"] == "comment":
            numbering = check_comment_numbering(records)
            if numbering:
                problems.append(numbering)
                continue
        normalized = normalize_records(board_id, records, [])
        try:
            chunks = plan_shards(board_id, normalized, limits)
        except LimitError as exc:
            problems.append(str(exc))
            continue
        print(
            "[%s] %d 条记录；分片计划：%s"
            % (spec["label"], len(records), describe_plan(board_id, chunks).replace(chr(10), " | "))
        )
        if spec["kind"] == "company":
            job_total = 0
            categories = {}
            with_note = 0
            with_review = 0
            for record in normalized:
                if not isinstance(record, dict):
                    continue
                jobs = record.get("岗位")
                job_total += len(jobs) if isinstance(jobs, list) else 0
                category = record.get("类别")
                if isinstance(category, str):
                    categories[category] = categories.get(category, 0) + 1
                note = record.get("备注")
                if isinstance(note, str) and note.strip() != "":
                    with_note += 1
                reviews = record.get("评价")
                if isinstance(reviews, list) and reviews:
                    with_review += 1
            print("    岗位条目数：%d；带备注的公司：%d；非空评价的公司：%d" % (job_total, with_note, with_review))
            if categories:
                print(
                    "    类别分布：%s"
                    % "、".join(
                        "%s %d" % (key, categories[key]) for key in COMPANY_CATEGORY_VALUES if key in categories
                    )
                )
        elif spec["kind"] == "school":
            with_review = 0
            for record in normalized:
                if isinstance(record, dict):
                    reviews = record.get("评价")
                    if isinstance(reviews, list) and reviews:
                        with_review += 1
            print("    非空评价的学校：%d" % with_review)
        else:
            by_board = {}
            with_nickname = 0
            with_date = 0
            for record in normalized:
                if not isinstance(record, dict):
                    continue
                board_value = record.get("板块")
                if isinstance(board_value, str):
                    by_board[board_value] = by_board.get(board_value, 0) + 1
                nickname = record.get("昵称")
                if isinstance(nickname, str) and nickname.strip() != "":
                    with_nickname += 1
                date_value = record.get("日期")
                if isinstance(date_value, str) and date_value.strip() != "":
                    with_date += 1
            print(
                "    评论条数：%d；按板块：%s；带昵称：%d；带日期：%d"
                % (
                    len(normalized),
                    "、".join("%s %d" % (key, by_board.get(key, 0)) for key in COMMENT_BOARD_VALUES),
                    with_nickname,
                    with_date,
                )
            )
    extra, missing = data_dir_consistency(data_dir, index)
    for name in missing:
        problems.append("index.json 列出的分片不存在：data/%s" % name)
    for name in extra:
        problems.append("data/ 中存在未被 index.json 列出的文件：%s" % name)
    for message in list(dict.fromkeys(warnings)):
        print("提示：%s" % message)
    if problems:
        for message in problems:
            print("错误：%s" % message)
        print("校验失败：共 %d 处问题。" % len(problems))
        return 1
    print("校验通过：数据符合 docs/SPEC.md v1.3（%s板块）。" % "、".join(BOARDS[b]["label"] for b in targets))
    return 0


def apply_fonts(root):
    """优先使用系统中文字体，避免中文显示为方框。"""
    try:
        families = set(tkfont.families(root))
    except tk.TclError:
        return
    for candidate in ("Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑", "PingFang SC", "Noto Sans CJK SC", "SimHei"):
        if candidate in families:
            for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
                try:
                    tkfont.nametofont(name).configure(family=candidate, size=10)
                except tk.TclError:
                    pass
            return


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    data_dir = None
    board = None
    check = False
    position = 0
    while position < len(args):
        arg = args[position]
        if arg in ("--check", "--validate"):
            check = True
        elif arg == "--data-dir" and position + 1 < len(args):
            position += 1
            data_dir = args[position]
        elif arg == "--board" and position + 1 < len(args):
            position += 1
            board = args[position]
        elif arg in ("-h", "--help"):
            print(__doc__ or "")
            return 0
        position += 1
    default_data, default_backup = default_paths()
    data_dir = os.path.abspath(data_dir) if data_dir else default_data
    if check:
        return run_check(data_dir, board)
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print("无法启动图形界面：%s" % exc)
        print("可改用只读校验模式：python add_prog/editor.py --check")
        return 2
    apply_fonts(root)
    EditorApp(root, board=board, data_dir=data_dir, backup_dir=default_backup)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
