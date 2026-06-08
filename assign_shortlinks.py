#!/usr/bin/env python3
"""各記事に短縮リンク用の連番エイリアス /p/<n>/ をフロントマターに付与する。
- 既に /p/ エイリアスがある記事はスキップ（id固定・再採番しない）
- 新規記事には「既存の最大id+1」から順に採番
- フロントマター先頭に aliases 行を追記するだけ（既存内容は変更しない）
"""
import os, re, glob

POSTS = "/Users/dai/AI/ブログ/content/posts"

def front_matter_bounds(lines):
    """先頭が '---' のYAMLフロントマターの開始・終了行indexを返す。なければNone"""
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return (0, i)
    return None

def existing_pid(fm_lines):
    for ln in fm_lines:
        m = re.search(r'/p/(\d+)/?', ln)
        if m and "alias" in "".join(fm_lines).lower():
            return int(m.group(1))
    # aliasesブロック内の /p/ を素直に探す
    return None

files = sorted(glob.glob(os.path.join(POSTS, "*.md")))

# パス1: 既存idを収集して最大を求める
used = {}
for f in files:
    with open(f, encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(r'aliases:\s*(?:\n\s*-\s*"?/p/(\d+)/?"?|\[\s*"?/p/(\d+)/?"?)', text)
    if m:
        used[f] = int(m.group(1) or m.group(2))

max_id = max(used.values()) if used else 0

# パス2: idの無い記事に採番して挿入
assigned = 0
for f in files:
    if f in used:
        continue
    with open(f, encoding="utf-8") as fh:
        lines = fh.readlines()
    b = front_matter_bounds(lines)
    if b is None:
        print("⚠ フロントマター無し、スキップ:", os.path.basename(f))
        continue
    max_id += 1
    pid = max_id
    insert = f'aliases:\n  - "/p/{pid}/"\n'
    # 開始 '---' の直後に挿入
    lines.insert(1, insert)
    with open(f, "w", encoding="utf-8") as fh:
        fh.writelines(lines)
    assigned += 1

print(f"既存id付き: {len(used)}件 / 新規採番: {assigned}件 / 最終id: {max_id}")
