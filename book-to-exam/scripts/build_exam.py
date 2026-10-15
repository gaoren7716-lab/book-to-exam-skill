#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_exam.py — 书籍知识库构建器的最后一步：把考点卡片合并成考试系统。

输入（放在同一书籍目录下）：
  cards_*.json    记忆型卡片（choice / blank / short）
  applied_*.json  应用型卡片（scenario / case）
每张卡片字段：id, chapter, chapterTitle, type, difficulty, question, options, answer, explanation, tags

输出：
  考点知识库.json         合并去重后的全量卡片
  考试系统.html           自包含考试网页（模板注入数据）
  Anki闪卡_<书名>.txt     Anki 可导入的 TSV

用法：
  python build_exam.py --book-dir "<书籍目录>" --title "《书名》" --subtitle "作者 · N章" [--pass-score 90]
"""
import argparse
import glob
import json
import os
import re
import sys
from collections import Counter

VALID_TYPES = {'choice', 'scenario', 'case'}
CHOICE_TYPES = VALID_TYPES
REQUIRED = ('id', 'chapter', 'chapterTitle', 'type', 'difficulty', 'question', 'answer', 'explanation')
# 只做选择题：所有题型都是"点选"，不出现填空框和长文本输入框
# 各题型的 ID 前缀：不同来源的代理可能用同一套命名（如都叫 c01-01），
# 合并时按题型重新编号，保证全局唯一且可读。
ID_PREFIX = {'choice': 'c', 'scenario': 'x', 'case': 'k'}

# 「需要用户输入文字」的痕迹——不只是下划线，还包括全角横线、空括号、
# 以及"填空/依次应填/请简述"这类措辞。命中即拒收，强制出题方改成纯选择判断。
BLANK_PATTERNS = [
    (r'_{2,}', '半角填空横线'),
    (r'＿+', '全角填空横线'),
    (r'[（(]\s*[）)]', '空括号'),
    (r'填空', '含"填空"措辞'),
    (r'空缺处', '含"空缺处"措辞'),
    (r'依次应填', '含"依次应填"措辞'),
    (r'请填写', '含"请填写"措辞'),
    (r'请简述|请回答|试述|试填写', '含主观作答要求'),
    (r'被填进|被填入|填入的是|填进去的词', '含"填入某词"句式'),
    (r'横线|空格|划掉|此处填|方括号', '含留白标记'),
]
# 注意：不要把"空缺""哪个词"一刀切拦截——"竞聘一个空缺岗位""以下哪个词不属于…"是正当题目表达。
# 只拦高精度信号，避免误杀合法选择题。


def load_cards(book_dir):
    files = sorted(glob.glob(os.path.join(book_dir, 'cards_*.json')) +
                   glob.glob(os.path.join(book_dir, 'applied_*.json')))
    if not files:
        sys.exit('错误：在 %s 下没有找到任何 cards_*.json / applied_*.json' % book_dir)
    cards, problems = [], []
    for f in files:
        try:
            data = json.load(open(f, encoding='utf-8'))
        except Exception as e:
            problems.append('%s 解析失败: %s' % (os.path.basename(f), e))
            continue
        for c in data:
            missing = [k for k in REQUIRED if k not in c or c[k] in (None, '')]
            if missing:
                problems.append('%s 缺字段 %s' % (c.get('id', '?'), missing))
                continue
            if c['type'] not in VALID_TYPES:
                hint = '（本系统只做选择题，请先把该题改造成选择题）' if c['type'] in ('blank', 'short') else ''
                problems.append('%s 不支持的题型 %s%s' % (c.get('id', '?'), c['type'], hint))
                continue
            c.setdefault('options', [])
            c.setdefault('tags', [])
            c['question'] = re.sub(r'\s+', ' ', str(c['question'])).strip()
            if isinstance(c['answer'], list):
                c['answer'] = '；'.join(str(x) for x in c['answer'])
            c['answer'] = str(c['answer'])
            c['explanation'] = re.sub(r'\s+', ' ', str(c['explanation'])).strip()
            if c['type'] in CHOICE_TYPES:
                if len(c['options']) < 2:
                    problems.append('%s选择题选项不足' % c.get('id', '?'))
                    continue
                c['options'] = [str(o).strip() for o in c['options']]
                scan = c['question'] + ' ' + ' '.join(c['options'])
                bad = [label for pat, label in BLANK_PATTERNS if re.search(pat, scan)]
                if bad:
                    problems.append('%s 仍需文字输入：%s（须改成纯选择判断）' % (c.get('id', '?'), '、'.join(bad)))
                    continue
            cards.append(c)
    if problems:
        print('⚠ 发现 %d 个问题（已跳过相关卡片）：' % len(problems))
        for p in problems[:10]:
            print('   -', p)
        if len(problems) > 10:
            print('   ... 其余 %d 条省略' % (len(problems) - 10))

    # 按 章+题型 重新编号，确保 ID 唯一（c=单选 x=场景 k=案例）
    order = {'choice': 0, 'scenario': 1, 'case': 2}
    counters = {}
    for c in sorted(cards, key=lambda x: (x['chapter'], order[x['type']])):
        key = (c['chapter'], c['type'])
        counters[key] = counters.get(key, 0) + 1
        c['srcId'] = c['id']
        c['id'] = '%s%02d-%02d' % (ID_PREFIX[c['type']], c['chapter'], counters[key])
    cards.sort(key=lambda c: (c['chapter'], order[c['type']]))
    return cards


def build_anki(cards, path, book_title):
    lines = []
    for c in cards:
        front = c['question'].replace('\t', ' ')
        label = {'choice': '正确答案', 'scenario': '正确做法', 'case': '正确答案'}[c['type']]
        back = '%s：%s' % (label, c['answer'])
        back += '\n' + '\n'.join(c['options'])
        back += '\n解析：' + c['explanation']
        tag = '第%02d章::%s' % (c['chapter'], c['chapterTitle'])
        lines.append(front.replace('\n', '<br>') + '\t' + back.replace('\n', '<br>') + '\t' + tag)
    open(path, 'w', encoding='utf-8').write('\n'.join(lines))
    return len(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--book-dir', required=True)
    ap.add_argument('--title', required=True)
    ap.add_argument('--subtitle', default='')
    ap.add_argument('--pass-score', type=int, default=90)
    ap.add_argument('--template', default=os.path.join(os.path.dirname(__file__), '..', 'assets', '考试系统_模板.html'))
    args = ap.parse_args()

    book_dir = os.path.abspath(args.book_dir)
    cards = load_cards(book_dir)
    if not cards:
        sys.exit('错误：没有可用卡片')

    tpl_path = os.path.abspath(args.template)
    tpl = open(tpl_path, encoding='utf-8').read()
    for ph in ('__CARDS_DATA__', '__BOOK_TITLE__', '__BOOK_SUB__', '__PASS_SCORE__',
               '__BOOK_TITLE_JSON__', '__OUTLINE_DATA__'):
        if ph not in tpl:
            sys.exit('错误：模板缺少占位符 %s' % ph)
    payload = json.dumps(cards, ensure_ascii=False).replace('</', '<\\/')
    full_title = args.title + ' 考点考试系统'
    html = (tpl.replace('__CARDS_DATA__', payload)
               .replace('__OUTLINE_DATA__', '[]')   # 占位，随后由 build_outline 注入真实大纲
               .replace('__BOOK_TITLE_JSON__', json.dumps(full_title, ensure_ascii=False))
               .replace('__BOOK_TITLE__', full_title)
               .replace('__BOOK_SUB__', (args.subtitle + ' · ' if args.subtitle else '') +
                        '共 %d 题 · %d 章 · %d分通过即视为掌握' % (len(cards), max(c['chapter'] for c in cards), args.pass_score))
               .replace('__PASS_SCORE__', str(args.pass_score)))

    json_path = os.path.join(book_dir, '考点知识库.json')
    html_path = os.path.join(book_dir, '考试系统.html')
    anki_path = os.path.join(book_dir, 'Anki闪卡_%s.txt' % args.title.strip('《》'))
    json.dump(cards, open(json_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    open(html_path, 'w', encoding='utf-8').write(html)
    n_anki = build_anki(cards, anki_path, args.title)

    # 生成知识大纲树（部分→章→知识点），并把题目挂载上去
    outline_info = ''
    outline_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'build_outline.py')
    fw = os.path.join(book_dir, '知识框架.md')
    if os.path.exists(outline_py) and os.path.exists(fw):
        import subprocess
        r = subprocess.run([sys.executable, outline_py, '--book-dir', book_dir],
                           capture_output=True, text=True, encoding='utf-8', errors='replace')
        outline_info = (r.stdout or '').strip()
        if r.returncode != 0:
            print('⚠ 大纲生成失败（不影响考试功能）：\n%s' % (r.stderr or '').strip()[:300])
    else:
        outline_info = '未找到 知识框架.md，跳过大纲（网页会自动降级为纯章节视图）'

    types = Counter(c['type'] for c in cards)
    chapters = len(set(c['chapter'] for c in cards))
    print('=' * 52)
    print('构建完成：%s' % args.title)
    print('  卡片总数  %d（全部为选择题：概念 %d + 场景 %d + 案例 %d）' % (
        len(cards), types.get('choice', 0), types.get('scenario', 0), types.get('case', 0)))
    print('  章节覆盖  %d 章' % chapters)
    print('  题型分布  %s' % dict(types))
    print('  知识库    %s' % json_path)
    print('  考试网页  %s (%.0f KB)' % (html_path, os.path.getsize(html_path) / 1024))
    print('  Anki闪卡  %s (%d 条)' % (anki_path, n_anki))
    if outline_info:
        print('-' * 52)
        print(outline_info)
    print('=' * 52)


if __name__ == '__main__':
    main()