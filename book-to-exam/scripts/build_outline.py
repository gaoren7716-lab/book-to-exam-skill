# -*- coding: utf-8 -*-
"""
build_outline.py — 从《知识框架.md》解析出「部分 → 章 → 核心知识点」三级大纲树，
并把题库中每一道题自动挂载到最匹配的知识点上。

产出：
  1) 大纲数据 OUTLINE_DATA（注入网页 __OUTLINE_DATA__ 占位符）
     结构：[{"part": 部分名, "chapters":[
              {"ch": 1, "title": "先听再说", "summary": "结构化倾听",
               "points": [{"id": "p1-1", "name": "结构化倾听三框", "kw": ["事实","情绪","期待"],
                           "cards": ["c01-01", ...], "total": 4, "right": 1}]}]}]
  2) 大纲速览页大纲.html（按大纲顺序的知识点清单，纯阅读用）
  3) 控制台报告：每章挂载覆盖率、零挂载知识点

用法：
  python build_outline.py --book-dir <书籍目录> [--framework 知识框架.md] [--cards 考点知识库.json]
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict

# ---------- 知识点候选：按大纲行文自动抽取关键词 ----------

# 章节标题里的圈号 → 序号
CIRCLED = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳㉑㉒㉓㉔'

# 明确的「概念=定义」「A+B+C」型公式行，优先当成知识点
FORMULA_RE = re.compile(r'[=＝]\s*\S')
# 加粗的**术语**作为强关键词
BOLD_RE = re.compile(r'\*\*(.+?)\*\*')
# 全角/半角引号包住的术语 —— 知识框架里方法名几乎都用引号包，是最强的信号
QUOTE_RE = re.compile(r'"([^"]{2,12})"')
# 分隔符：顿号、斜杠、加号、箭头、连接号、分号、逗号、句读、问号、叹号
# 问叹号必须算分隔符：否则「是同一件事吗？」会被当成 7 字整词，超过 6 字上限被丢弃，
# 整个节点只剩「自查句式」这种废关键词，题目永远挂不上。
SPLIT_RE = re.compile(r'[、/／＋+；;，,。？?！!｜|~～\-—–→⇒>》」』\s]+')
# 需要剔除的通用词（不作为知识点关键词）
STOPWORDS = {
    '动作', '步骤', '方法', '要点', '上台阶', '核心', '本质', '定位', '心态', '前提', '认知',
    '目标', '理念', '公式', '三步', '两动作', '三动作', '五动作', '四招', '三句式', '句式',
    '要素', '原则', '标准', '结构', '功能', '作用', '特点', '场景', '实战', '练习', '总结',
    '意义', '重点', '关键', '底层', '内容', '部分', '情况', '问题', '方式', '角度', '层面',
    '阶段', '结果', '目的', '对象', '范围', '条件', '要求', '注意', '提醒', '建议', '原因',
    '我们', '自己', '对方', '别人', '时候', '可以', '不能', '不要', '就是', '这个', '那个',
}

# 通用/无区分度的标签 —— 不能作为知识点（否则会造出「场景应用」这种废节点）
GENERIC_TAGS = {
    '场景应用', '案例应用', '职场场景', '生活场景', '综合应用', '易错', '难点', '重点',
    '选择题', '单选题', '知识点', '复习', '自测', '常考', '陷阱',
}

# 名称里常见的功能性前缀，剥离后取尾部实词（"开启对方开放性" → "开放性"）
NAME_PREFIX = [
    '开启对方', '开启', '沟通的', '表达的', '说话的', '聊天的', '恰当的', '正确的',
    '关于', '如何', '怎样', '为什么', '所谓', '一个', '两个', '三个', '四个', '五个',
]


def clean(s):
    s = re.sub(r'\s+', ' ', s or '').strip()
    s = s.replace('**', '').replace('`', '')
    # 统一各种引号，避免中英文引号导致子串匹配失败
    for a, b in [('“', '"'), ('”', '"'), ('‘', "'"), ('’', "'"),
                 ('「', '"'), ('」', '"'), ('『', '"'), ('』', '"')]:
        s = s.replace(a, b)
    return s.strip(' ：:，,。；;、"\'()（）')


def core_of(name):
    """从知识点名称里剥出实词主干，用于做子串匹配"""
    n = clean(name)
    for pre in NAME_PREFIX:
        if n.startswith(pre) and len(n) - len(pre) >= 2:
            n = n[len(pre):]
            break
    return n


def clean_name(s):
    """节点标题专用清理。

    clean() 只剥首尾字符，遇到 `三个"千万别"` 这种「引号包住术语」的写法时，
    首尾各剥掉一个引号，会留下 `三个"千万别` 这种不配对引号，大纲里很难看。
    引号语义已经在 split_kw 的 QUOTE_RE 里用过了，这里直接全部去掉。
    """
    return clean(s).replace('"', '').strip()


def split_kw(text, name=''):
    """从一行文字里抽候选关键词（按信号强度排序）。

    三类信号：
      1. 引号包住的术语 —— 知识框架里方法名几乎都用「"…"」包起来，命中率最高
      2. 加粗术语、公式两侧（A=B 里的 A 与 B）
      3. 按分隔符切出的短词（含箭头 →，早期漏掉它导致整行被丢弃）
    切不出词时用整句前缀兜底，保证知识点不会因为没有关键词而被静默丢掉。
    """
    raw = text
    text = clean(text)
    kws = []

    def add(k, hi=False):
        k = clean(k).strip('"')
        if 2 <= len(k) <= 12 and k not in STOPWORDS and k not in kws:
            kws.append(k)
        return k

    # 1) 引号术语（先在原文上取，避免 clean 破坏引号语义）
    for q in QUOTE_RE.findall(raw):
        add(q)
    # 2) 加粗术语
    for b in BOLD_RE.findall(raw):
        add(clean(b))
    # 3) 冒号后的说明文字：按全量分隔符切分
    body = text
    if '：' in text:
        body = text.split('：', 1)[1]
    elif ':' in text:
        body = text.split(':', 1)[1]
    for piece in SPLIT_RE.split(body):
        p = clean(piece)
        if 2 <= len(p) <= 6:
            add(p)
    # 4) 公式两侧："目标感=方案力" → 目标感 / 方案力
    if re.search(r'[=＝]', text):
        for side in re.split(r'[=＝]', text):
            s2 = clean(side)
            if 2 <= len(s2) <= 6:
                add(s2)
    # 5) 名称主干（"开启对方开放性" → "开放性"）
    core = core_of(name)
    if 2 <= len(core) <= 8:
        add(core)
    # 6) 兜底：整句前缀（子串匹配，题目提到同一表述即可命中）
    if not kws:
        if len(text) >= 4:
            kws.append(text[:8])
        else:
            kws.append(text)
    return kws[:12]


def parse_framework(md_path):
    """解析知识框架.md → 部分 / 章 / 知识点"""
    text = open(md_path, encoding='utf-8').read()
    lines = text.split('\n')

    parts = []          # [{name, chapters:[]}]
    cur_part = None
    cur_ch = None
    in_overview = False  # 跳过"全书总纲"代码块与结尾"一图流"

    for ln in lines:
        s = ln.strip()
        if s.startswith('```'):
            in_overview = not in_overview
            continue
        if in_overview:
            continue
        # ## 第X部分：xxx  或  ## 第X部分·xxx
        m = re.match(r'^##\s*(第[一二三四五六七八九十]+部分[：:·]?\s*.+)$', s)
        if m:
            cur_part = {'name': clean(m.group(1)), 'chapters': []}
            parts.append(cur_part)
            cur_ch = None
            continue
        # ### ① 先听再说 —— 结构化倾听
        m = re.match(r'^###\s*([①-⑳㉑-㉔])\s*(.+)$', s)
        if m:
            if cur_part is None:
                cur_part = {'name': '正文', 'chapters': []}
                parts.append(cur_part)
            circled, rest = m.group(1), m.group(2)
            title, summary = rest, ''
            if '——' in rest:
                title, summary = [x.strip() for x in rest.split('——', 1)]
            elif '—' in rest:
                title, summary = [x.strip() for x in rest.split('—', 1)]
            cur_ch = {
                'circled': circled,
                'title': clean(title),
                'summary': clean(summary),
                'points': [],
            }
            cur_part['chapters'].append(cur_ch)
            continue
        # 章节内的一行 = 一个候选知识点
        if cur_ch is None or not s or s.startswith('>'):
            continue
        # 跳过 markdown 分隔线 / 表格分隔行（--- |---| ---）
        if re.fullmatch(r'[-=|\s:*]{3,}', s):
            continue
        # 表格行：每行即一条要点（如"四种沟通风格"对照表）
        if s.startswith('|'):
            cells = [clean(x) for x in s.strip('|').split('|')]
            cells = [x for x in cells if x and not re.fullmatch(r'[-: ]+', x)]
            if len(cells) < 2:
                continue
            # 跳过表头行：第一格若是「类型/维度」等表头词
            if cells[0] in ('类型', '维度', '项目', '类别', '要素'):
                continue
            name = cells[0]
            if len(name) > 20:
                name = name[:14]
            # 表格每行 = 一条要点：单元格文本全部作为关键词来源
            kws = []
            for cell in cells:
                for piece in SPLIT_RE.split(clean(cell)):
                    p = clean(piece)
                    if 2 <= len(p) <= 6 and p not in STOPWORDS and p not in kws:
                        kws.append(p)
            if not kws:
                kws = [clean(name)]
            cur_ch['points'].append({
                'name': clean_name(name),
                'desc': ' ｜ '.join(cells),
                'kw': kws[:12],
            })
            continue
        if s.startswith('-') or re.match(r'^\d+[.、]', s):
            body = re.sub(r'^[-*]\s*|^\d+[.、]\s*', '', s)
            if not body:
                continue
            name = ''
            b = BOLD_RE.search(body)
            if b:
                name = clean(b.group(1))
            elif '：' in body or ':' in body:
                name = clean(re.split('[：:]', body, maxsplit=1)[0])
            else:
                name = clean(body)[:16]
            if not name or len(name) > 20:
                # 太长就不适合当知识点标题，改用正文前若干字
                name = clean(body)[:14]
            kws = split_kw(body, name)
            if not kws:
                continue
            cur_ch['points'].append({'name': clean_name(name),
                                     'desc': clean(body), 'kw': kws})

    return parts


def chapter_index(parts):
    """把圈号映射为 章号（1..24），并保证顺序"""
    idx, seq = {}, 0
    for p in parts:
        for ch in p['chapters']:
            if ch['circled'] in idx:
                continue
            seq += 1
            idx[ch['circled']] = seq
    return idx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--book-dir', required=True)
    ap.add_argument('--framework', default='知识框架.md')
    ap.add_argument('--cards', default='考点知识库.json')
    ap.add_argument('--out-html', default='大纲速览.html')
    args = ap.parse_args()

    fw = os.path.join(args.book_dir, args.framework)
    cd = os.path.join(args.book_dir, args.cards)
    if not os.path.exists(fw):
        sys.exit('找不到知识框架文件：%s' % fw)
    if not os.path.exists(cd):
        sys.exit('找不到题库文件：%s' % cd)

    parts = parse_framework(fw)
    idx = chapter_index(parts)
    cards = json.load(open(cd, encoding='utf-8'))

    # 卡片按章归组
    by_ch = defaultdict(list)
    for c in cards:
        by_ch[c['chapter']].append(c)

    # 题目关键词分两级，这是为了对付「干扰项污染」：
    #   核心信号 = 题干 + 标签 + 解析 + 正确选项
    #   完整信号 = 核心信号 + 错误选项
    # 错误选项是按规则从其他章/其他方法借来的干扰项，含正确答案的术语时会把题目
    # 挂到错的知识点上（实测：一题答案为「可视化」，因干扰项是「吹风/慢熬」，
    # 被挂到了「凿穿心理防线」节点下）。所以先用核心信号匹配，不达标才允许
    # 干扰项兜底——保证「挂错位置」比「挂不上」更罕见。
    def card_blobs(c):
        opts = c.get('options') or []
        ans = (c.get('answer') or '').strip()
        right, wrong = '', ''
        if len(ans) == 1 and 'A' <= ans <= 'Z':
            i = ord(ans) - ord('A')
            if 0 <= i < len(opts):
                right = opts[i] or ''
                wrong = ' '.join(x for j, x in enumerate(opts)
                                 if j != i and x)
        core = ' '.join([c.get('question') or '', ' '.join(c.get('tags') or []),
                         right, c.get('explanation') or ''])
        return core, (core + ' ' + wrong).strip()

    matched, unmapped, coverage = set(), [], []
    report = []

    for p in parts:
        for ch in p['chapters']:
            n = idx[ch['circled']]
            ch['ch'] = n
            pool = by_ch.get(n, [])
            ch['total'] = len(pool)
            ch['right'] = 0
            used = set()

            # 给每个知识点编号并建立关键词索引
            for i, pt in enumerate(ch['points'], 1):
                pt['id'] = 'p%d-%d' % (n, i)
                pt['cards'] = []
                pt['total'] = 0
                pt['right'] = 0

            # 第一轮：框架要点挂载
            # 两级匹配，专治「干扰项污染」：
            #   核心信号 = 题干 + 标签 + 解析 + 正确选项
            #   完整信号 = 核心信号 + 错误选项
            # 错误选项是按规则从其他章/其他方法借来的干扰项，会把题目挂到错的知识点上
            # （实测：一题答案为「可视化」，因干扰项是「吹风/慢熬」，被挂到了
            # 「凿穿心理防线」节点下）。所以核心信号达标就直接采用，不达标才回退
            # 到完整信号——既治挂错，也不丢原本靠选项才能挂上的题。
            for c in pool:
                core, full = card_blobs(c)
                tags = ' '.join(c.get('tags') or [])

                def score_of(blob):
                    best, bs = None, 0.0
                    for pt in ch['points']:
                        s = 0.0
                        for k in pt['kw']:
                            if not k or len(k) < 2:
                                continue
                            if k in blob:
                                # 关键词越长越可信；命中标签权重加倍
                                s += min(len(k), 8) * (2 if k in tags else 1)
                        if s > bs:
                            best, bs = pt, s
                    return best, bs

                best, best_score = score_of(core)
                if best_score < 3:
                    best, best_score = score_of(full)
                # 阈值：加权得分 ≥3 才算可靠命中（避免把「本章其他要点」撑爆）
                if best is not None and best_score >= 3:
                    best['cards'].append(c['id'])
                    best['total'] += 1
                    matched.add(c['id'])
                    used.add(c['id'])
                else:
                    unmapped.append((n, c['id'], c['question'][:40]))

            # 第二轮：框架太粗时，用题目标签补足知识点
            # 场景：知识框架每章只写 3~4 条要点，但一章有 15 道题 → 大量题挂不进去。
            # 此时按高频标签聚类，为每个标签生成一个补充知识点。
            rest_ids = [c['id'] for c in pool if c['id'] not in used]
            tag_cnt = defaultdict(list)
            for c in pool:
                if c['id'] in used:
                    continue
                for t in (c.get('tags') or []):
                    t = clean(t)
                    if 2 <= len(t) <= 8 and t not in GENERIC_TAGS:
                        tag_cnt[t].append(c['id'])
            # 合并互为子串的标签（"赞美" 与 "赞美公式" → 取更长的那个当节点名）
            tags_sorted = sorted(tag_cnt, key=lambda x: (-len(tag_cnt[x]), -len(x), x))
            merged_into = {}
            for t in tags_sorted:
                host = None
                for u in tags_sorted:
                    if u == t or u in merged_into:
                        continue
                    if t in u and len(tag_cnt[t]) <= len(tag_cnt[u]):
                        host = u
                        break
                if host:
                    merged_into[t] = host
            for t, host in merged_into.items():
                if t in tag_cnt and host in tag_cnt:
                    tag_cnt[host].extend(tag_cnt[t])
                    del tag_cnt[t]
            # 只为「够热」的标签建节点：>=2 张题；单章补足节点上限 6 个（避免大纲碎成渣）
            cands = [t for t in sorted(tag_cnt, key=lambda x: -len(tag_cnt[x]))
                     if len(set(tag_cnt[t])) >= 2][:6]
            for t in cands:
                ids = sorted(set(tag_cnt[t]))
                # 一张题可以带多个标签，不筛就会挂到多个节点上（实测 c16-06 同时
                # 挂在「会前寒暄」和「请教」两个节点下，学习时会重复出现同一题）
                ids = [cid for cid in ids if cid not in used]
                if not ids:
                    continue
                ch['points'].append({
                    'id': 'p%d-t%d' % (n, len(ch['points']) + 1),
                    'name': t,
                    'desc': '由题目标签聚合（知识框架未单列此要点）',
                    'kw': [t],
                    'cards': ids,
                    'total': len(ids),
                    'right': 0,
                    'fromTag': True,
                })
                for cid in ids:
                    used.add(cid)
                    matched.add(cid)
                rest_ids = [x for x in rest_ids if x not in ids]

            # 兜底：仍未挂载的题目归入「本章其他要点」
            rest = [c for c in pool if c['id'] in rest_ids]
            if rest:
                ch['points'].append({
                    'id': 'p%d-0' % n,
                    'name': '本章其他要点',
                    'desc': '未单独归类的题目（可作兜底复习）',
                    'kw': [],
                    'cards': [c['id'] for c in rest],
                    'total': len(rest),
                    'right': 0,
                    'other': True,
                })
                matched.update(c['id'] for c in rest)
            for pt in ch['points']:
                pt['cards'] = sorted(set(pt['cards']))
                # 节点名过长会被截断成半句话，改用其冒号前的实词或加粗术语
                if len(pt['name']) > 14:
                    head = clean(re.split('[：:，,。]', pt['desc'], maxsplit=1)[0])
                    if 2 <= len(head) <= 14:
                        pt['name'] = head
                    else:
                        pt['name'] = pt['name'][:12] + '…'

            # 丢掉 0 题的空节点（框架写了但题目没覆盖，对学习者没有价值）
            ch['points'] = [pt for pt in ch['points'] if pt['total'] > 0]

            hit = sum(pt['total'] for pt in ch['points'])
            fallback = next((pt['total'] for pt in ch['points'] if pt.get('other')), 0)
            coverage.append((n, ch['title'], hit, len(pool), fallback))

    data = [{
        'part': p['name'],
        'chapters': [{
            'ch': ch['ch'],
            'title': ch['title'],
            'summary': ch['summary'],
            'total': ch['total'],
            'points': ch['points'],
        } for ch in p['chapters']],
    } for p in parts]

    # ---------- 输出 1：大纲数据 JSON ----------
    out_json = os.path.join(args.book_dir, '知识大纲.json')
    json.dump(data, open(out_json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    # ---------- 输出 2：注入网页 ----------
    tpl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            '..', 'assets', '考试系统_模板.html')
    exam = os.path.join(args.book_dir, '考试系统.html')
    payload = json.dumps(data, ensure_ascii=False).replace('</', '<\\/')
    if os.path.exists(exam):
        html = open(exam, encoding='utf-8').read()
        if '__OUTLINE_DATA__' in html:
            html = html.replace('__OUTLINE_DATA__', payload)
        else:
            # 已注入过数据的旧版：替换 const OUTLINE = ...; 那一整段
            html = re.sub(r'const OUTLINE = \[.*?\];',
                          lambda m: 'const OUTLINE = ' + payload + ';',
                          html, count=1, flags=re.S)
        open(exam, 'w', encoding='utf-8').write(html)
        print('已把大纲数据注入：%s' % exam)
    else:
        tpl = open(tpl_path, encoding='utf-8').read()
        if '__OUTLINE_DATA__' in tpl:
            open(exam, 'w', encoding='utf-8').write(
                tpl.replace('__OUTLINE_DATA__', payload))
            print('已生成考试网页（含大纲树）：%s' % exam)
        else:
            print('⚠ 模板缺少 __OUTLINE_DATA__ 占位符，仅输出 JSON')

    # ---------- 输出 3：大纲速览 HTML（纯阅读） ----------
    write_outline_html(data, os.path.join(args.book_dir, args.out_html))

    # ---------- 报告 ----------
    total_pts = sum(len(c['points']) for p in data for c in p['chapters'])
    named_pts = sum(1 for p in data for c in p['chapters']
                    for pt in c['points'] if not pt.get('other'))
    print('\n大纲层级：%d 部分 / %d 章 / %d 知识点（其中兜底节点 %d 个）'
          % (len(data), sum(len(p['chapters']) for p in data),
             total_pts, total_pts - named_pts))
    fb_total = sum(c[4] for c in coverage)
    print('题目挂载：%d/%d 张归入知识点；其中 %d 张落入兜底节点（%.1f%%）'
          % (len(matched), len(cards), fb_total,
             fb_total / max(len(cards), 1) * 100))
    heavy = [c for c in coverage if c[4] >= max(3, c[3] * 0.4)]
    if heavy:
        print('⚠ 兜底偏多的章节（大纲条目可能偏粗，建议补细知识框架）：')
        for n, t, hit, tot, fb in heavy:
            print('   第%d章 %s：兜底 %d/%d' % (n, t, fb, tot))
    print('\n产出：\n  %s\n  %s' % (out_json, os.path.join(args.book_dir, args.out_html)))


def write_outline_html(data, path):
    """纯静态大纲速览页：可折叠、可点知识点看题目，适合快速通读与挑选"""
    total_pts = sum(len(c['points']) for p in data for c in p['chapters'])
    h = ["""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>知识大纲速览</title><style>
:root{--bg:#0f1117;--panel:#1a1d27;--panel2:#222636;--border:#2e3348;--text:#e8eaf2;--muted:#8b91a8;--accent:#6c8cff;--ok:#3ddc84}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:"Microsoft YaHei","PingFang SC",sans-serif;line-height:1.7;padding:24px 16px 80px}
.wrap{max-width:900px;margin:0 auto}
h1{font-size:22px}.sub{color:var(--muted);font-size:13px;margin-bottom:20px}
.part{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:16px 20px;margin-bottom:14px}
.part h2{font-size:17px;margin-bottom:10px;color:var(--accent)}
.ch{border-left:2px solid var(--border);padding:8px 0 8px 14px;margin-bottom:10px}
.ch h3{font-size:15px;margin-bottom:4px}
.ch .sum{color:var(--muted);font-size:12px;margin-bottom:6px}
.pt{background:var(--panel2);border-radius:8px;padding:8px 12px;margin:6px 0;font-size:14px;cursor:pointer}
.pt:hover{border:1px solid var(--accent)}
.pt .n{color:var(--ok);font-weight:bold;margin-right:6px}
.pt .cnt{color:var(--muted);font-size:12px;float:right}
.pt .d{color:var(--muted);font-size:12px;display:none;margin-top:6px;padding-top:6px;border-top:1px dashed var(--border)}
.pt.open .d{display:block}
</style></head><body><div class="wrap">
<h1>📖 知识大纲速览</h1>
<div class="sub">共 %d 个部分 · %d 个知识点 —— 点击任意知识点展开要点说明</div>
""" % (len(data), total_pts)]
    for p in data:
        h.append('<div class="part"><h2>%s</h2>' % p['part'])
        for c in p['chapters']:
            h.append('<div class="ch"><h3>第%d章 %s</h3>' % (c['ch'], c['title']))
            if c['summary']:
                h.append('<div class="sum">核心：%s</div>' % c['summary'])
            for pt in c['points']:
                h.append('<div class="pt" onclick="this.classList.toggle(\'open\')">'
                         '<span class="cnt">%d 题</span>'
                         '<span class="n">·</span>%s<div class="d">%s</div></div>'
                         % (pt['total'], pt['name'], pt['desc']))
            h.append('</div>')
        h.append('</div>')
    h.append('</div><script>document.querySelectorAll(".part .ch").forEach(function(c,i){'
             'if(i<2)c.querySelectorAll(".pt").forEach(function(p){p.classList.add("open")})});</script>'
             '</body></html>')
    open(path, 'w', encoding='utf-8').write(''.join(h))


if __name__ == '__main__':
    main()
