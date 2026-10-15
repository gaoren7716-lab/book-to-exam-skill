#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
split_pdf.py — 把一本书切成 chapters/ 目录，每章一个 txt，并产出书籍信息.json。

用法：
  python split_pdf.py "D:\书架\沟通的方法.pdf" [--out "D:\书架\沟通的方法"] [--min-len 200]

切分优先级：
  1. PDF 书签目录（doc.get_toc()）—— 最可靠，保留层级
  2. 无书签时用 --regex 识别章节标题行，默认兼容「第1章/第一章/Chapter 1」等常见写法

产出（落在 --out 目录）：
  chapters/01_章名.txt ...
  书籍信息.json          章节清单 + 字数统计 + 抽取方式
  全文.txt              整本拼接（便于直接投喂 AI）
"""
import argparse
import json
import os
import re
import sys

DEFAULT_REGEX = r'^\s*(第\s*[0-9一二三四五六七八九十百千]+\s*[章节節篇回卷部]|Chapter\s+\d+|CHAPTER\s+\d+)\s*[\.、：:]?\s*(.{0,50})$'

# 目录页/版权页噪声：出现这些词的页多半不是正文
NOISE = re.compile(r'(目\s*录|contents|前言|序言|自\s*序|版权|出版说明|致\s*谢|索\s*引|参考文献)')


def read_pages(path):
    try:
        import pymupdf
    except ImportError:
        sys.exit('缺少依赖，请先安装：pip install pymupdf')
    doc = pymupdf.open(path)
    return doc, [p.get_text() for p in doc]


def _norm_toc(toc):
    """兼容不同 pymupdf 版本的 toc 结构：旧版是 (level, title, page) 或 (level,title,page,dest)，
    新版统一为 (level, title, page)。这里一律归一成 (level, title, page)。"""
    out = []
    for e in toc:
        if len(e) >= 4:
            out.append((e[0], e[1], e[2]))       # (level, title, page, dest)
        elif len(e) == 3:
            out.append((e[0], e[1], e[2]))
        elif len(e) == 2:
            out.append((1, e[0], e[1]))
    return out


def split_by_toc(doc, pages):
    """按书签目录切，返回 [(title, page_idx, 0)] 或 None"""
    try:
        toc = _norm_toc(doc.get_toc())
    except Exception:
        return None
    toc = [t for t in toc if isinstance(t[2], int) and t[2] > 0]
    if not toc:
        return None
    # 取层级最深的标题作为「章」（通常 lvl==2 是章）
    lvls = sorted(set(l for l, _, _ in toc))
    target = lvls[1] if len(lvls) > 1 else lvls[0]
    items = [(t.strip(), p - 1, 0) for l, t, p in toc if l == target and p > 1]
    if len(items) < 2:                      # 书签太少或层级不对，退回全部条目
        items = [(t.strip(), p - 1, 0) for l, t, p in toc if p > 1]
    if len(items) < 2:
        return None
    return items


def split_by_regex(pages, pattern, min_len):
    rx = re.compile(pattern)
    marks = []                                     # [(title, page_idx, line_idx)]
    for pi, text in enumerate(pages):
        if NOISE.search(text[:200]) and pi < 20:  # 跳过疑似目录页
            continue
        for li, ln in enumerate(text.split('\n')[:40]):   # 章标题通常在页首
            s = ln.strip()
            if s and len(s) < 60 and rx.match(s):
                marks.append((s, pi, li))
                break
    # 去掉页首重复（同一页只保留第一个）
    uniq, seen = [], set()
    for t, pi, li in marks:
        if pi in seen:
            continue
        seen.add(pi)
        uniq.append((t, pi, li))
    return uniq


def build_chapters(pages, marks):
    """按标记点切片组装每章正文"""
    chs = []
    for i, (title, pi, li) in enumerate(marks):
        end = marks[i + 1][1] if i + 1 < len(marks) else len(pages)
        buf = []
        for p in range(pi, end):
            lines = pages[p].split('\n')
            start = li if p == pi else 0
            buf.extend(lines[start:])
        text = re.sub(r'\n{3,}', '\n\n', '\n'.join(buf)).strip()
        chs.append({'title': title, 'text': text})
    return chs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--out', default=None, help='输出目录，默认与PDF 同级的 书名/')
    ap.add_argument('--regex', default=DEFAULT_REGEX)
    ap.add_argument('--min-len', type=int, default=200, help='最短章长（字），低于则丢弃')
    ap.add_argument('--force', action='store_true', help='即使已有章节也重建')
    args = ap.parse_args()

    pdf = os.path.abspath(args.pdf)
    if not os.path.exists(pdf):
        sys.exit('找不到文件：%s' % pdf)
    name = os.path.splitext(os.path.basename(pdf))[0]
    out = os.path.abspath(args.out) if args.out else os.path.join(os.path.dirname(pdf), name)
    chap_dir = os.path.join(out, 'chapters')

    if os.path.isdir(chap_dir) and os.listdir(chap_dir) and not args.force:
        n = len([f for f in os.listdir(chap_dir) if f.endswith('.txt')])
        print('已存在 %d 章（%s）。加 --force 可重建。' % (n, chap_dir))
        return

    doc, pages = read_pages(pdf)
    total_chars = sum(len(p.replace(' ', '')) for p in pages)
    print('=' * 52)
    print('书名：%s' % name)
    print('页数：%d　总字数：%d' % (len(pages), total_chars))

    marks = split_by_toc(doc, pages)
    how = 'PDF 书签目录'
    if not marks:
        print('未找到可用书签，改用正则识别章节标题…')
        marks = split_by_regex(pages, args.regex, args.min_len)
        how = '正则：' + args.regex
        if not marks:
            sys.exit(
                '\n没能识别出章节。请用 --regex 指定你这本书的章节写法，例如：\n'
                '  --regex "^\\\\s*第.{1,6}章"\n'
                '也可以先用 pdftotext 等工具导出文本，确认正文里章节标题长什么样。')

    chs = build_chapters(pages, marks)
    chs = [c for c in chs if len(c['text'].replace(' ', '')) >= args.min_len]
    if not chs:
        sys.exit('切分后没有合格章节，试试调小 --min-len')

    os.makedirs(chap_dir, exist_ok=True)
    for f in os.listdir(chap_dir):
        if f.endswith('.txt'):
            os.remove(os.path.join(chap_dir, f))

    print('-' * 52)
    print('切分方式：%s' % how)
    print('章节数：%d' % len(chs))
    print('-' * 52)

    files, total = [], 0
    for i, c in enumerate(chs, 1):
        safe = re.sub(r'[\\/:*?"<>|]', '_', c['title'])[:40]
        fn = '%02d_%s.txt' % (i, safe)
        path = os.path.join(chap_dir, fn)
        open(path, 'w', encoding='utf-8').write(c['text'])
        n = len(c['text'].replace(' ', ''))
        total += n
        files.append({'序号': i, '标题': c['title'], '文件': 'chapters/' + fn, '字数': n})
        print('  第%2d章 %-34s %6d 字' % (i, c['title'][:32], n))

    meta = {
        '书名': name,
        '来源文件': os.path.basename(pdf),
        '切分方式': how,
        '页数': len(pages),
        '章节数': len(chs),
        '总字数': total,
        '生成时间': __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        '章节清单': files,
    }
    json.dump(meta, open(os.path.join(out, '书籍信息.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    open(os.path.join(out, '全文.txt'), 'w', encoding='utf-8').write(
        '\n\n'.join('========== 第%d章 %s ==========\n\n%s' % (f['序号'], f['标题'], open(os.path.join(out, f['文件']), encoding='utf-8').read()) for f in files))

    print('=' * 52)
    print('章节目录：%s' % chap_dir)
    print('书籍信息：%s' % os.path.join(out, '书籍信息.json'))
    print('拼接全文：%s' % os.path.join(out, '全文.txt'))
    print('\n下一步：把这个目录告诉 AI，说「按 book-to-exam 流程出考试系统」。')
    doc.close()


if __name__ == '__main__':
    main()