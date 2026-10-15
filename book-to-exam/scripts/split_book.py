#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
split_book.py — 把一本书切成章节，供AI 出题用。

支持 PDF / EPUB / TXT / Markdown，纯标准库（PDF 需 pymupdf）。

用法：
  python split_book.py "D:\\书架\\书名.pdf" --out "D:\\书架\\书名"
  python split_book.py "D:\\书架\\书名.epub" --out "D:\\书架\\书名"
  python split_book.py "D:\\书架\\书名.txt" --out "D:\\书架\\书名"

产出（与PDF 版一致）：
  chapters/NN_章名.txt每章一个 txt，按顺序编号
  书籍信息.json              书名 / 作者 / 章数 / 总字数
  全文.txt                   全部章节拼接，便于整体阅读
"""
import argparse
import html
import json
import os
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

# 常见章节标题写法（按优先级）
CHAPTER_RES = [
    re.compile(r'^\s*(第\s*[0-9一二三四五六七八九十百零〇]+\s*[章回节卷部篇]\s*[^\n]{0,40})$'),
    re.compile(r'^\s*(Chapter\s+[0-9IVXivx]+\b[^\n]{0,40})$', re.I),
    re.compile(r'^\s*(第\s*[0-9]+\s*[章回节]\s*[^\n]{0,40})$'),
]
# EPUB 的 spine 里，标题写在 <h1>~<h3> 或 nav toc 里
HEADING_RES = [
    re.compile(r'<h[1-3][^>]*>(.*?)</h[1-3]>', re.S | re.I),
]

# 正式章节（用于把 EPUB 的碎片重新合并）
LEVEL1 = re.compile(
    r'^\s*(?:第\s*[0-9一二三四五六七八九十百千零〇]+\s*[章回节卷部篇]'
    r'|Chapter\s+[0-9IVXivx]+\b|Part\s+[IVXLC0-9]+\b)', re.I)
# 版权/前言等要丢弃的前置内容
FRONT_MATTER = re.compile(
    r'^\s*(?:版权|著作权|出版|版权声明|免责|制作|校订|扉页|封面|书名页|内容简介|作者简介|'
    r'目录|前言|序言|序|引言|导言|出版说明|Copyright|ISBN|本书)', re.I)


# ---------------------------------------------------------------- PDF

def load_pdf(path):
    """返回 (每页文本, [(标题, 起始页index)])"""
    try:
        import pymupdf as fitz          # 新版包名，无弃用告警
    except ImportError:
        import fitz                     # 老版本

    doc = fitz.open(path)
    pages = []
    for pg in doc:
        pages.append(pg.get_text('text'))

    # 扫描版检测：页数不少但几乎提不出文字（正文是图片）
    chars = sum(len(t.strip()) for t in pages)
    if len(pages) >= 8 and chars < max(400, len(pages) * 30):
        doc.close()
        sys.exit(
            '\n这是扫描版 PDF（没有文字层），提取不到正文，无法切章。\n'
            '  页数 %d，但只提到 %d 个字符，基本都是图片。\n\n'
            '解决办法（任选其一）：\n'
            '  1. 找同名的文字版 PDF / EPUB，扫描版转出来通常字更少\n'
            '  2. 用 Adobe Acrobat 或 ABBYY FineReader 做 OCR 后另存为文字版\n'
            '  3. 用手机「微信读书」「多看」打开后导出（有内置 OCR）\n'
            % (len(pages), chars))

    marks = split_pdf_by_toc(doc)
    doc.close()
    return pages, marks


def _norm_toc(raw):
    """不同 pymupdf 版本的 get_toc() 返回 3 元组或 4 元组，统一成 (lvl, title, page)"""
    out = []
    for it in raw or []:
        if len(it) >= 3:
            lvl, title, page = it[0], it[1], it[-1]
            if isinstance(page, int):
                out.append((lvl, title, page))
    return out


def split_pdf_by_toc(doc):
    """按书签目录切，返回 [(title, page_index)] 或None"""
    try:
        toc = _norm_toc(doc.get_toc())
    except Exception:
        return None
    toc = [t for t in toc if t[2] > 0]
    if not toc:
        return None
    lvls = sorted(set(l for l, _, _ in toc))
    target = lvls[1] if len(lvls) > 1 else lvls[0]
    items = [(t.strip(), p - 1) for l, t, p in toc if l == target and p > 0]
    if len(items) < 2:                      # 书签太少或层级不对，退回全部条目
        items = [(t.strip(), p - 1) for l, t, p in toc]
    return items if len(items) >= 2 else None


def split_pdf_by_regex(pages):
    """无书签时用正则扫章节标题"""
    marks = []
    for i, txt in enumerate(pages):
        for line in txt.splitlines():
            for rx in CHAPTER_RES[:1]:
                if rx.match(line):
                    marks.append((line.strip(), i))
                    break
            else:
                continue
            break
    # 去重：同一页只保留第一个标题
    out = []
    seen = set()
    for t, p in marks:
        if p in seen:
            continue
        seen.add(p)
        out.append((t, p))
    return out if len(out) >= 2 else None


# ---------------------------------------------------------------- EPUB

def load_epub(path):
    """解析 EPUB，返回 (chapters=[(标题, 纯文本)], 元信息dict)"""
    z = zipfile.ZipFile(path)
    meta = {}
    chapters = []

    # 1. 定位 OPF（优先读 container.xml，否则全扫）
    opf_name = None
    try:
        cx = ET.fromstring(z.read('META-INF/container.xml'))
        for el in cx.iter():
            if el.tag.endswith('rootfile'):
                opf_name = el.get('full-path')
                break
    except Exception:
        pass
    if not opf_name:
        opf_name = next((n for n in z.namelist() if n.lower().endswith('.opf')), None)
    if not opf_name:
        sys.exit('错误：EPUB 里找不到 OPF，可能不是标准 EPUB')

    base = os.path.dirname(opf_name)
    pkg = ET.fromstring(z.read(opf_name))
    ns = {'opf': 'http://www.idpf.org/2007/opf',
          'dc': 'http://purl.org/dc/elements/1.1/'}

    def _t(tag):
        el = pkg.find(tag, ns)
        return (el.text or '').strip() if el is not None and el.text else ''

    meta['书名'] = _t('dc:title') or os.path.splitext(os.path.basename(path))[0]
    meta['作者'] = _t('dc:creator')
    meta['语言'] = _t('dc:language')

    # 2. manifest：id -> href
    manifest = {}
    for el in pkg.iter():
        if el.tag.endswith('item'):
            i = el.get('id')
            href = el.get('href')
            if i and href:
                manifest[i] = os.path.normpath(os.path.join(base, href)).replace('\\', '/')

    # 3. spine：按顺序取 xhtml
    ordered = []
    for el in pkg.iter():
        if el.tag.endswith('itemref'):
            href = manifest.get(el.get('idref'))
            if href and href.lower().endswith(('.xhtml', '.html', '.htm')):
                ordered.append(href)

    # 4. 逐个取文本；标题取该文件第一个 heading
    for href in ordered:
        try:
            raw = z.read(href).decode('utf-8', 'ignore')
        except KeyError:
            continue
        title = ''
        for rx in HEADING_RES:
            m = rx.search(raw)
            if m:
                title = clean_html(m.group(1))
                break
        body = clean_html(raw)
        if len(body.strip()) < 100:          # 太短的多半是版权页/封面
            continue
        chapters.append((title or f'未命名章节', body))
    z.close()
    return chapters, meta


# ---------------------------------------------------------------- TXT / MD

def load_txt(path):
    txt = open(path, encoding='utf-8', errors='ignore').read()
    blocks, cur, curtitle = [], [], ''
    for line in txt.splitlines():
        hit = any(rx.match(line) for rx in CHAPTER_RES)
        if hit:
            if curtitle and ''.join(cur).strip():
                blocks.append((curtitle, '\n'.join(cur)))
            curtitle, cur = line.strip(), []
        else:
            cur.append(line)
    if curtitle and ''.join(cur).strip():
        blocks.append((curtitle, '\n'.join(cur)))
    if len(blocks) < 2:
        body = txt
        n = max(1, len(body) // 6000)
        for i in range(n):
            seg = body[i * 6000:(i + 1) * 6000]
            blocks.append((f'第{i + 1} 部分', seg))
    return blocks


def normalize_chapters(chapters, mode='auto'):
    """把 EPUB 的碎片合并成正式章节。

    EPUB 一个文件里有 700+ 个 spine 片段，直接当章节会把「第X章」切碎。
    这里按标题层级重新归并：遇到「第X章」开新章，章内小节并入当前章，
    章前的版权页/目录等丢弃。
    """
    if len(chapters) < 3:
        return chapters

    lvl1 = sum(1 for t, _ in chapters if LEVEL1.match(t))
    # 一级标题太少说明书脊乱序，别乱合并，原样返回
    if lvl1 < 3:
        return chapters

    out, cur, curtitle = [], [], None
    lead = []                       # 第一个一级标题之前的正文（序言之类）
    for title, body in chapters:
        if LEVEL1.match(title):
            if curtitle and ''.join(cur).strip():
                out.append((curtitle, '\n\n'.join(cur).strip()))
            curtitle, cur = title, [body]          # curtitle 用字符串，cur 存各小节正文
        elif FRONT_MATTER.match(title):
            continue                      # 前置页直接丢
        elif curtitle is None:
            if len(body.strip()) > 800:   # 序言、引言这类够长的才留
                lead.append((title, body))
            continue
        else:
            cur.append('## %s\n%s' % (title, body))
    if curtitle and ''.join(cur).strip():
        out.append((curtitle, '\n\n'.join(cur).strip()))

    # 一级标题之前的内容放最前面
    out = lead + out

    # 合并后仍太碎的丢掉；同名未命名的一并去掉（无法出题）
    out = [(t, b) for t, b in out
           if len(b.strip()) >= 400 and t != '未命名章节']
    return out or chapters


def detect_volumes(chapters):
    """检测多册合集：若「第一章」反复出现，说明是 N 册合集。

    返回 (册数, 每册起点的下标)，无法判定时返回 (1, [])。
    合集书应按册拆开出题——358 章没法考，一次只考一本。
    """
    idx = [i for i, (t, _) in enumerate(chapters) if LEVEL1.match(t)]
    if len(idx) < 6:
        return 1, []
    # 找「第一章 / 第1章 / Chapter 1」出现的位置
    starts = [i for i in idx if re.match(
        r'^\s*(?:第\s*[一1]\s*[章回节卷部篇]|Chapter\s+1\b|Part\s+I\b)', chapters[i][0], re.I)]
    if len(starts) >= 2 and len(starts) == len(starts[0:]) and \
            (len(starts) >= 2 and len(starts) / len(chapters) < 0.25):
        return len(starts), starts
    return 1, []


def split_volumes(chapters, count, starts):
    """把合集切成多册"""
    bounds = starts + [len(chapters)]
    out = []
    for k in range(count):
        seg = chapters[bounds[k]:bounds[k + 1]]
        out.append(('第%d册' % (k + 1), seg))
    return out


# ---------------------------------------------------------------- 工具

_TAG = re.compile(r'<[^>]+>')
_WS = re.compile(r'\n{3,}')


def clean_html(raw):
    """去标签、去脚本样式、还原实体、压缩空行"""
    raw = re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', '', raw)
    raw = re.sub(r'(?i)<br\s*/?>|</p>|</div>|</h[1-6]>', '\n', raw)
    raw = _TAG.sub('', raw)
    raw = html.unescape(raw)
    raw = '\n'.join(l.rstrip() for l in raw.splitlines())
    return _WS.sub('\n\n', raw).strip()


def safe_name(title, maxlen=40):
    """把标题变成合法文件名"""
    t = re.sub(r'[\\/:*?"<>|\r\n\t]', '', str(title))
    t = t.replace('\u3000', ' ').replace('\xa0', ' ')   # 全角/不间断空格
    t = re.sub(r'\s+', '_', t).strip('_. ')
    return t[:maxlen] or '未命名'


def write_out(out_dir, chapters, meta, source):
    ch_dir = os.path.join(out_dir, 'chapters')
    os.makedirs(ch_dir, exist_ok=True)
    total = 0
    used = {}
    files = []
    for i, (title, body) in enumerate(chapters, 1):
        base = '%02d_%s' % (i, safe_name(title))
        if base in used:                # 同名标题加序号，避免互相覆盖
            used[base] += 1
            base = '%02d_%s_%d' % (i, safe_name(title), used[base])
        else:
            used[base] = 1
        fp = os.path.join(ch_dir, base + '.txt')
        open(fp, 'w', encoding='utf-8').write(body)
        total += len(body)
        files.append(base + '.txt')

    open(os.path.join(out_dir, '全文.txt'), 'w', encoding='utf-8').write(
        '\n\n'.join('===== %s =====\n%s' % (t, b) for t, b in chapters))

    info = {
        '书名': meta.get('书名', ''),
        '作者': meta.get('作者', ''),
        '来源文件': os.path.basename(source),
        '格式': source.rsplit('.', 1)[-1].lower(),
        '章节数': len(chapters),
        '总字数': total,
        '章节文件': files,
        '章节清单': [t for t, _ in chapters],
    }
    json.dump(info, open(os.path.join(out_dir, '书籍信息.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    return info


# ---------------------------------------------------------------- 主流程

def split(path, out_dir, regex=None, min_len=200, force=False, split_by_volume=True):
    if not os.path.isfile(path):
        sys.exit('错误：文件不存在 %s' % path)
    ext = path.rsplit('.', 1)[-1].lower()

    if ext == 'pdf':
        pages, marks = load_pdf(path)
        how = 'PDF 书签目录' if marks else None
        if not marks:
            marks = split_pdf_by_regex(pages)
            how = '正则扫描标题' if marks else None
        if not marks and regex:
            rx = re.compile(regex)
            found = []
            for i, t in enumerate(pages):
                for line in t.splitlines():
                    if rx.match(line):
                        found.append((line.strip(), i))
                        break
            marks = found if len(found) >= 2 else None
            how = '自定义正则' if marks else None
        if not marks:
            sys.exit('\n没能识别出章节。可用 --regex 指定你这本书的章节写法，例如：\n'
                     '  --regex "^\\s*第.{1,6}章"\n'
                     '（先用 PDF 阅读器看一眼目录行长什么样）')
        chapters, meta = [], {'书名': os.path.splitext(os.path.basename(path))[0]}
        for k, (title, start) in enumerate(marks):
            end = marks[k + 1][1] if k + 1 < len(marks) else len(pages)
            body = '\n'.join(pages[start:end]).strip()
            if len(body) >= min_len:
                chapters.append((title, body))
        meta.setdefault('书名', os.path.splitext(os.path.basename(path))[0])

    elif ext == 'epub':
        raw, meta = load_epub(path)
        named = sum(1 for t, _ in raw if t != '未命名章节')
        if named == 0:
            sys.exit(
                '\n这本 EPUB 的标题取不到——每个文件都只有图片里的标题，HTML 里没有文字标题。\n'
                '（共 %d 个文件，全部未命名）\n\n'
                '这种情况无法按章切分，因为不知道哪一段是哪一章。\n'
                '建议：找同名的 PDF 文字版，或换一本。\n' % len(raw))
        chapters = normalize_chapters(raw)
        how = 'EPUB spine + 标题层级归并'
        if len(chapters) < 2:
            sys.exit('\nEPUB 解析出的章节太少（%d 章）。可能是扫描版或结构异常。' % len(chapters))
        if len(chapters) < len(raw) * 0.5:
            print('（已将 %d 个片段归并为 %d 章）' % (len(raw), len(chapters)))
        unnamed = sum(1 for t, _ in chapters if t == '未命名章节')
        if unnamed:
            print('⚠ 其中 %d/%d 章没有标题（该书用图片做标题）。'
                  '这些章可能需要合并后再出题。' % (unnamed, len(chapters)))

    elif ext in ('txt', 'md', 'markdown'):
        chapters = load_txt(path)
        meta = {'书名': os.path.splitext(os.path.basename(path))[0]}
        how = '正则扫描标题'

    else:
        sys.exit('错误：不支持的格式 .%s（支持 pdf / epub / txt / md）' % ext)

    if not chapters:
        sys.exit('错误：切分结果为空')

    if os.path.isdir(out_dir) and force:
        import shutil
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    # 合集书（多册）章节数会爆炸，检测到就按册拆开
    if split_by_volume or ext == 'epub':
        nvol, starts = detect_volumes(chapters)
        if nvol >= 2:
            vols = split_volumes(chapters, nvol, starts)
            print('检测到多册合集，按册拆分为 %d 册：' % nvol)
            written = []
            for vname, seg in vols:
                vdir = os.path.join(out_dir, vname)
                os.makedirs(vdir, exist_ok=True)
                vi = write_out(vdir, seg, meta, path)
                written.append((vname, vi['章节数'], vdir))
                print('   %s → %d 章  %s' % (vname, vi['章节数'], vdir))
            print('\n⚠ 这是一本合集，一次只考一册。挑一册继续出题即可。')
            return {'书名': meta.get('书名', ''), '章节数': sum(w[1] for w in written),
                    '总字数': 0, '分册': written}

    info = write_out(out_dir, chapters, meta, path)
    print('切分方式：%s' % how)
    print('章节数：%d' % info['章节数'])
    print('总字数：%d' % info['总字数'])
    print('产出目录：%s' % out_dir)
    for f in info['章节文件'][:8]:
        print('   - %s' % f)
    if len(info['章节文件']) > 8:
        print('   ... 共 %d 章' % len(info['章节文件']))
    return info


def main():
    ap = argparse.ArgumentParser(
        description='把书切成章节（PDF/EPUB/TXT/MD），供 AI 出题使用',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='示例：\n'
               '  python split_book.py "D:\\书架\\书名.pdf" --out "./书名"\n'
               '  python split_book.py "D:\\书架\\书名.epub" --out "./书名"\n'
               '  python split_book.py "D:\\书架\\书名.pdf" --regex "^\\\\s*第.{1,6}章"')
    ap.add_argument('path', help='书籍文件路径')
    ap.add_argument('--out', default=None, help='产出目录，默认与源文件同名目录')
    ap.add_argument('--regex', default=None, help='自定义章节标题正则（仅 PDF 无书签时用）')
    ap.add_argument('--min-len', type=int, default=200, help='每章最少字数，过短的章丢弃')
    ap.add_argument('--force', action='store_true', help='产出目录已存在时先清空')
    ap.add_argument('--no-split-volume', action='store_true', help='合集书不按册拆分')
    a = ap.parse_args()
    out = a.out or os.path.splitext(a.path)[0]
    split(a.path, out, a.regex, a.min_len, a.force, not a.no_split_volume)


if __name__ == '__main__':
    main()