#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
build_portal.py — 扫描知识库目录下所有已构建的书籍，生成「书架考试总控」索引页。

每本书一个卡片，显示：书名、题量、章节数、题型构成、考试网页入口。
总控页只做导航与进度总览，学习进度仍在各自考试页的 localStorage 中记录。

用法：
  python build_portal.py --lib-dir "<书库目录>" [--out "<lib_dir>\书架考试总控.html"]
"""
import argparse
import glob
import json
import os
import re

TPL = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>书架考试总控</title>
<style>
:root{--bg:#0f1117;--panel:#1a1d27;--border:#2e3348;--text:#e8eaf2;--muted:#8b91a8;--accent:#6c8cff}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:"Microsoft YaHei","PingFang SC",sans-serif;min-height:100vh}
.wrap{max-width:1000px;margin:0 auto;padding:32px 16px 60px}
h1{font-size:24px;margin-bottom:6px}
.sub{color:var(--muted);font-size:13px;margin-bottom:26px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}
.book{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:18px;display:flex;flex-direction:column;transition:.15s}
.book:hover{border-color:var(--accent);transform:translateY(-2px)}
.bname{font-size:16px;font-weight:bold;margin-bottom:4px}
.bmeta{font-size:12px;color:var(--muted);margin-bottom:12px}
.bar{display:flex;height:8px;border-radius:4px;overflow:hidden;background:#2a2f42;margin-bottom:10px}
.bar i{display:block;height:100%}
.legend{display:flex;gap:10px;flex-wrap:wrap;font-size:11px;color:var(--muted);margin-bottom:14px}
.dot{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:4px}
.go{margin-top:auto;text-align:center;background:linear-gradient(135deg,#6c8cff,#8a6cff);color:#fff;text-decoration:none;border-radius:9px;padding:9px;font-size:14px}
.total{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:16px 20px;margin-bottom:22px;display:flex;gap:30px;flex-wrap:wrap}
.total div{font-size:13px;color:var(--muted)}
.total b{display:block;font-size:22px;color:var(--text)}
</style>
</head>
<body>
<div class="wrap">
<h1>📚 书架考试总控</h1>
<div class="sub">每本书一套独立考试系统 · 正式考试 ≥90 分且错题清零 = 掌握该书</div>
<div class="total">__TOTAL_STATS__</div>
<div class="legend">
  <span><i class="dot" style="background:#6c8cff"></i>单选</span>
  <span><i class="dot" style="background:#6ee7b7"></i>场景</span>
  <span><i class="dot" style="background:#f0b67f"></i>案例</span>
</div>
<div class="grid">
__BOOK_CARDS__
</div>
</div>
</body>
</html>
'''

COLORS = {'choice': '#6c8cff', 'scenario': '#6ee7b7', 'case': '#f0b67f'}
LABELS = {'choice': '单选', 'scenario': '场景', 'case': '案例'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lib-dir', required=True)
    ap.add_argument('--out', default=None)
    args = ap.parse_args()
    lib = os.path.abspath(args.lib_dir)
    out = args.out or os.path.join(lib, '书架考试总控.html')

    books, grand_types, grand_n = [], {}, 0
    for kb in sorted(glob.glob(os.path.join(lib, '*', '考点知识库.json'))):
        book_dir = os.path.dirname(kb)
        try:
            cards = json.load(open(kb, encoding='utf-8'))
        except Exception:
            continue
        if not cards:
            continue
        name = os.path.basename(book_dir)
        types = {}
        for c in cards:
            types[c['type']] = types.get(c['type'], 0) + 1
        chapters = sorted(set(c['chapter'] for c in cards))
        parts = sorted(set(c.get('part', '') for c in cards if c.get('part')))
        total = len(cards)
        grand_n += total
        for k, v in types.items():
            grand_types[k] = grand_types.get(k, 0) + v
        bars = ''.join('<i style="width:%.1f%%;background:%s"></i>' % (types.get(k, 0) / total * 100, COLORS[k])
                       for k in ['choice', 'scenario', 'case'] if types.get(k))
        bars += ''.join('<i style="width:%.1f%%;background:%s"></i>' % (types.get(k, 0) / total * 100, COLORS[k])
                        for k in types if k not in ['choice', 'scenario', 'case'])
        html_name = '考试系统.html'
        books.append('''  <div class="book">
    <div class="bname">%s</div>
    <div class="bmeta">%d 章 · %d 部分 · %d 题</div>
    <div class="bar">%s</div>
    <div class="bmeta">%s</div>
    <a class="go" href="%s" target="_blank">进入考试 →</a>
  </div>''' % (
            name.replace('&', '&amp;'), len(chapters), len(parts), total, bars,
            ' · '.join('%s %d' % (LABELS.get(k, k), v) for k, v in sorted(types.items())),
            html_name))

    stats = '<div>已建库书籍<b>%d 本</b></div><div>考点总量<b>%d 题</b></div>' % (len(books), grand_n)
    stats += ''.join('<div>%s<b>%d</b></div>' % (LABELS.get(k, k), v) for k, v in sorted(grand_types.items()))
    html = TPL.replace('__TOTAL_STATS__', stats).replace('__BOOK_CARDS__', '\n'.join(books))
    open(out, 'w', encoding='utf-8').write(html)
    print('总控页已生成：%s' % out)
    print('已建库书籍：%d 本，考点合计 %d 题' % (len(books), grand_n))
    for b in books:
        name = re.search(r'class="bname">([^<]+)<', b).group(1)
        cnt = re.search(r'(\d+) 题', b).group(1)
        print('  - %s（%s题）' % (name, cnt))


if __name__ == '__main__':
    main()