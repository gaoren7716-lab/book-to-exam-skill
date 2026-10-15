#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
切章脚本回归测试。

用法：
  python tools/split_regression.py
  python tools/split_regression.py --shelf "E:\\0 书架"

从真实书库里各挑一本样本（PDF / EPUB / 扫描版 PDF / TXT），
验证split_book.py 的关键行为。找不到某类样本则跳过，不算失败。
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SPLIT = os.path.join(HERE, '..', 'book-to-exam', 'scripts', 'split_book.py')

PASS, FAIL, SKIP = [], [], []


def run(path, out):
    r = subprocess.run([sys.executable, SPLIT, path, '--out', out, '--force'],
                       capture_output=True, text=True, encoding='utf-8',
                       errors='replace', timeout=900)
    return (r.stdout or '') + (r.stderr or ''), r.returncode


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print('  %s  %s%s' % ('PASS' if cond else 'FAIL', name,
                           '' if cond else ('  <- ' + detail[:200])))


def find_sample(shelf, pattern, must_contain=None):
    """在书库里按 glob 找一本样本"""
    import glob
    hits = glob.glob(os.path.join(shelf, '**', pattern), recursive=True)
    for h in hits:
        if must_contain and must_contain not in h:
            continue
        return h
    return None


def scan_pdf_plain(p):
    """判断 PDF 是否有文字层"""
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz
        except ImportError:
            return None
    try:
        d = fitz.open(p)
        n = sum(len(pg.get_text('text').strip()) for pg in d)
        has = len(d) >= 8 and n >= max(400, len(d) * 30)
        d.close()
        return has
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--shelf', default=r'E:\0 书架')
    a = ap.parse_args()
    shelf = os.path.abspath(a.shelf)

    if not os.path.isdir(shelf):
        sys.exit('书库不存在：%s' % shelf)
    print('=== 切章回归（书库：%s）===' % shelf)

    tmp = tempfile.mkdtemp(prefix='split_reg_')

    # ---------- 1. PDF 文字版：走书签目录，产出 chapters ----------
    # 逐个试，直到找到一个有文字层的（扫描版不能用来测正常路径）
    import glob
    p, plain = None, False
    for cand in sorted(glob.glob(os.path.join(shelf, '**', '*.pdf'), recursive=True))[:60]:
        if scan_pdf_plain(cand):
            p, plain = cand, True
            break
    if p and plain:
        log, rc = run(p, os.path.join(tmp, 'pdf'))
        check('PDF 文字版切章成功', rc == 0, log)
        check('PDF 优先走书签目录', 'PDF 书签目录' in log or '正则扫描' in log, log)
        check('PDF 报告章节数', re.search(r'章节数：\d+', log), log)
        check('PDF 生成 chapters 目录',
              os.path.isdir(os.path.join(tmp, 'pdf', 'chapters')), log)
    else:
        SKIP.append('PDF 文字版')
        print('  SKIP  PDF 文字版（书库里没找到有文字层的样本）')

    # ---------- 2. EPUB：可解析，或无标题时明确报错 ----------
    e = find_sample(shelf, '*.epub')
    if e:
        log, rc = run(e, os.path.join(tmp, 'epub'))
        ok = rc == 0 or '标题取不到' in log          # 后者是正确行为
        check('EPUB 可解析或明确报错', ok, log)
        if rc == 0:
            check('EPUB 产出章节或分册',
                  re.search(r'章节数：\d+', log) or '按册拆分' in log, log)
    else:
        SKIP.append('EPUB')
        print('  SKIP  EPUB（书库里没有 epub）')

    # ---------- 3. 扫描版 PDF：应明确提示需要 OCR ----------
    import glob as _g
    s = None
    for cand in _g.glob(os.path.join(shelf, '**', '*.pdf'), recursive=True)[:60]:
        if scan_pdf_plain(cand) is False:
            s = cand
            break
    if s:
        log, rc = run(s, os.path.join(tmp, 'scan'))
        check('扫描版明确提示需 OCR', '扫描版' in log and rc != 0, log)
    else:
        SKIP.append('扫描版')
        print('  SKIP  扫描版（书库里没找到无文字层的样本）')

    # ---------- 4. TXT ----------
    t = find_sample(shelf, '*.txt')
    if t:
        log, rc = run(t, os.path.join(tmp, 'txt'))
        check('TXT 切章成功', rc == 0, log)
    else:
        SKIP.append('TXT')
        print('  SKIP  TXT（书库里没有 txt）')

    print('\n结果: PASS=%d  FAIL=%d  SKIP=%d'
          % (len(PASS), len(FAIL), len(SKIP)))
    if FAIL:
        print('失败项：%s' % '、'.join(FAIL))
        print('（样本留在 %s 可查看日志）' % tmp)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())