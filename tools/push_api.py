#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
push_api.py — 用 GitHub API 推送本地 commit（绕过不稳定的 git 传输代理）。

用法：
  python tools/push_api.py --dry-run    # 只看要推哪些文件
  python tools/push_api.py              # 推送本地 HEAD 相对 origin/<ref> 的全部变更

原理：读本地 git diff 拿到变更 → 逐个把 blob 塞进一次 tree 提交 → 更新 ref。
一次提交包含全部变更，比逐文件提交更清晰。
"""
import argparse
import base64
import json
import subprocess
import sys
import urllib.request


def sh(*args):
    r = subprocess.run(args, capture_output=True, text=True, encoding='utf-8',
                       errors='replace')
    return r.returncode, (r.stdout or '') + (r.stderr or '')


def get_token():
    rc, out = sh('gh', 'auth', 'token')
    return out.strip() if rc == 0 else ''


def api(url, tok, data=None, method='GET'):
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('Authorization', 'Bearer ' + tok)
    req.add_header('Accept', 'application/vnd.github+json')
    req.add_header('User-Agent', 'book-to-exam-push')
    if data:
        req.add_header('Content-Type', 'application/json')
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default='gaoren7716-lab/book-to-exam-skill')
    ap.add_argument('--ref', default='main')
    ap.add_argument('--message', default=None, help='默认沿用本地 HEAD 的提交信息')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    repo = 'https://api.github.com/repos/%s' % a.repo
    tok = get_token()
    if not tok:
        sys.exit('拿不到 GitHub token，请先 gh auth login')

    # 本地待推送的变更（相对本地记录的 origin/ref）
    # -z：用 NUL 分隔，避免中文/特殊字符被 C-style 转义加引号
    rc, out = sh('git', 'diff', '-z', '--name-status',
                 'origin/%s' % a.ref, 'HEAD')
    if rc != 0:
        sys.exit('读取本地变更失败：\n' + out)

    fields = [f for f in out.split('\0') if f]
    changes = []
    i = 0
    while i < len(fields):
        st = fields[i][0]
        if st in 'RC':                 # 重命名 / 复制：后跟旧名、新名
            changes.append(('M', fields[i + 2]))
            i += 3
            continue
        changes.append((st, fields[i + 1]))
        i += 2

    if not changes:
        print('没有待推送的变更')
        return 0

    label = {'A': '新增', 'M': '修改', 'D': '删除', 'R': '重命名'}
    print('待推送 %d 个文件：' % len(changes))
    for st, p in changes:
        print('  %-4s %s' % (label.get(st, st), p))

    msg = a.message
    if not msg:
        rc, out = sh('git', 'log', '-1', '--pretty=%B')
        msg = out.strip() or '更新文件'

    if a.dry_run:
        print('\n[dry-run] 未实际推送')
        return 0

    head = api('%s/git/ref/heads/%s' % (repo, a.ref), tok)['object']['sha']

    entries = []
    for st, path in changes:
        if st == 'D':
            entries.append({'path': path, 'mode': '100644',
                            'type': 'blob', 'sha': None})     # None = 删除
            continue
        with open(path, 'rb') as f:
            content = f.read()
        blob = api('%s/git/blobs' % repo, tok,
                   json.dumps({'content': base64.b64encode(content).decode(),
                               'encoding': 'base64'}).encode(), 'POST')
        entries.append({'path': path, 'mode': '100644', 'type': 'blob',
                        'sha': blob['sha']})

    new_tree = api('%s/git/trees' % repo, tok,
                   json.dumps({'base_tree': head, 'tree': entries}).encode(),
                   'POST')
    if 'sha' not in new_tree:
        sys.exit('构造 tree 失败：%s' % json.dumps(new_tree, ensure_ascii=False)[:500])

    commit = api('%s/git/commits' % repo, tok,
                 json.dumps({'message': msg, 'tree': new_tree['sha'],
                             'parents': [head]}).encode(), 'POST')
    api('%s/git/refs/heads/%s' % (repo, a.ref), tok,
        json.dumps({'sha': commit['sha']}).encode(), 'PATCH')

    # 同步本地引用，避免下次重复推送
    sh('git', 'update-ref', 'refs/remotes/origin/%s' % a.ref, commit['sha'])

    print('\n已推送 %s  %s' % (commit['sha'][:7], msg.splitlines()[0]))
    print('仓库 https://github.com/%s' % a.repo)
    return 0


if __name__ == '__main__':
    sys.exit(main())