#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
书架.py — 本地书架网页

双击运行，浏览器自动打开，扫描你的书库，点书即可切章并生成给我执行的指令。

用法：
  python 书架.py                    # 扫描 E:\\0 书架
  python 书架.py "D:\\我的书"        # 扫别的目录
  python 书架.py --port 8080        # 换端口
  python 书架.py --no-browser       # 不自动开浏览器

只依赖标准库（切章时 EPUB/TXT 不需要任何库，PDF 需要 pymupdf）。
"""
import argparse
import http.server
import json
import os
import re
import socketserver
import subprocess
import sys
import threading
import urllib.parse
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
BOOK_EXTS = ('.pdf', '.epub', '.txt', '.md')
DEFAULT_SHELF = r'E:\0 书架'

# 扫书时的排除目录
SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.obsidian', '.workbuddy'}


def scan_shelf(root):
    """扫描书库，返回分类 ->书籍列表"""
    if not os.path.isdir(root):
        return None, '目录不存在：%s' % root
    tree = {}
    n = 0
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith('.')]
        books = []
        for f in fns:
            ext = os.path.splitext(f)[1].lower()
            if ext not in BOOK_EXTS:
                continue
            full = os.path.join(dp, f)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            books.append({
                'name': clean_title(f),
                'path': full,
                'ext': ext[1:].upper(),
                'size': size,
            })
            n += 1
        if books:
            cat = os.path.relpath(dp, root)
            cat = '.' if cat == '.' else cat.replace('\\', '/')
            tree[cat] = sorted(books, key=lambda b: b['name'])
    return tree, n


_NOISE = re.compile(r'\s*\((?:[^()]*?Z-Library|[^()]*?z-lib)[^()]*?\)\s*', re.I)


def clean_title(fname):
    """去掉文件名里的盗版站标记与多余后缀"""
    t = os.path.splitext(fname)[0]
    t = _NOISE.sub('', t)
    t = re.sub(r'\s{2,}', ' ', t).strip(' .-')
    return t or os.path.splitext(fname)[0]


# --------------------------------------------------------------- 切章（跑子进程）

HERE = os.path.dirname(os.path.abspath(__file__))
SPLIT_CANDIDATES = [
    os.path.join(HERE, 'split_book.py'),
    os.path.join(HERE, '..', 'book-to-exam', 'scripts', 'split_book.py'),
]


def find_splitter():
    for p in SPLIT_CANDIDATES:
        if os.path.isfile(p):
            return os.path.abspath(p)
    return None


def run_split(path, out_dir, splitter):
    """跑切章脚本。stdout 与 stderr 合并——脚本的错误提示走 stderr。"""
    cmd = [sys.executable, splitter, path, '--out', out_dir, '--force']
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=600)
        log = ((r.stdout or '') + (r.stderr or '')).strip()
        if not log:
            log = '脚本没有输出（返回码 %d）' % r.returncode
        return r.returncode, log
    except subprocess.TimeoutExpired:
        return 1, '切章超时（超过 10 分钟）。书可能太大，或章节标题写法特殊导致正则没匹配上。'


# --------------------------------------------------------------- HTTP

HTML = r'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>本地书架 · 转成考试系统</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0f1117;color:#e6e8ef;font:15px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;padding:28px 32px 60px}
h1{font-size:21px;font-weight:600;margin-bottom:6px}
.sub{color:#8b90a5;font-size:13px;margin-bottom:22px}
.bar{display:flex;gap:10px;align-items:center;margin-bottom:20px;flex-wrap:wrap}
input[type=search]{background:#181b24;border:1px solid #2a2f3d;color:#e6e8ef;padding:9px 14px;border-radius:9px;font-size:14px;min-width:230px}
select{background:#181b24;border:1px solid #2a2f3d;color:#e6e8ef;padding:9px 12px;border-radius:9px;font-size:14px}
.btn{background:#6d5cf0;border:none;color:#fff;padding:9px 18px;border-radius:9px;font-size:14px;cursor:pointer;font-weight:500}
.btn:hover{background:#7d6ef5}
.btn.g{background:#232838;color:#c9cede}
.btn.g:hover{background:#2c3242}
.cat{margin-bottom:10px}
.cname{font-size:13.5px;color:#a8adc0;margin-bottom:8px;padding:6px 9px;border-radius:7px;background:#12151d;
  cursor:pointer;display:flex;justify-content:space-between;align-items:center;gap:10px}
.cname:hover{background:#1a1e29;color:#dfe3ee}
.cname .cn-l{display:flex;align-items:center;gap:8px;min-width:0}
.cname .cn-name{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cname .cn-c{font-size:12px;color:#6f7488;flex:none}
/* 层级缩进：按路径深度 */
.d1{margin-left:0}
.d2{margin-left:16px}
.d3{margin-left:32px}
.d4{margin-left:48px}
.d5{margin-left:64px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px;margin-bottom:14px}
.book{background:#171a22;border:1px solid #262b39;border-radius:11px;padding:13px 15px;cursor:pointer;transition:.13s}
.book:hover{border-color:#6d5cf0;background:#1c2030;transform:translateY(-1px)}
.bn{font-size:14px;margin-bottom:7px;line-height:1.45;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.bm{font-size:12px;color:#6f7488;display:flex;gap:9px}
.tag{background:#232838;padding:1px 7px;border-radius:5px;font-size:11px;color:#9aa0b5}
.tag.p{background:#2b2440;color:#b3a3f5}
.tag.e{background:#1e3329;color:#7ddba8}
#detail{position:fixed;inset:0;background:rgba(0,0,0,.62);display:none;align-items:center;justify-content:center;padding:26px}
#detail.on{display:flex}
.dw{background:#151822;border:1px solid #2a2f3d;border-radius:15px;max-width:640px;width:100%;max-height:86vh;overflow:auto;padding:26px 28px}
.dh{font-size:18px;font-weight:600;margin-bottom:5px;line-height:1.45}
.dm{font-size:13px;color:#8b90a5;margin-bottom:18px;word-break:break-all}
pre{background:#10131b;border:1px solid #262b39;border-radius:10px;padding:14px;font-size:12.5px;color:#9fe0c0;white-space:pre-wrap;word-break:break-all;max-height:300px;overflow:auto;line-height:1.65}
.row{display:flex;gap:10px;margin-top:16px;flex-wrap:wrap}
.hint{font-size:13px;color:#8b90a5;margin-top:14px;padding:12px 14px;background:#1a1e29;border-radius:9px;border-left:3px solid #6d5cf0;line-height:1.7}
h2{font-size:15px;margin:18px 0 9px;color:#c9cede}
.empty{text-align:center;padding:60px 20px;color:#6f7488}
</style></head><body>
<h1>本地书架</h1>
<div class="sub" id="info">扫描中…</div>
<div class="bar">
  <input type="search" id="q" placeholder="搜索书名…" oninput="render()">
  <select id="cat" onchange="render()"><option value="">全部分类</option></select>
  <button class="btn g" onclick="expandAll()">展开/收起全部</button>
</div>
<div id="list"></div>
<div id="detail"><div class="dw">
  <div class="dh" id="dName"></div>
  <div class="dm" id="dMeta"></div>
  <div id="dBody"></div>
  <div class="row"><button class="btn g" onclick="closeD()">关闭</button></div>
</div></div>
<script>
let TREE={},CNT=0,OPEN={};
fetch('/api/scan').then(r=>r.json()).then(d=>{
  TREE=d.tree||{};CNT=d.total||0;
  const s=document.getElementById('cat');
  const cats=Object.keys(TREE).sort();
  cats.forEach(c=>{const o=document.createElement('option');o.value=c;
    o.textContent=(c.split('/').pop())+' ('+TREE[c].length+')';s.appendChild(o)});
  document.getElementById('info').textContent=CNT+' 本书 · '+cats.length+' 个分类 · 点击任意书切章';
  // 默认展开书籍最多的分类，一进来就能看到书长什么样
  if(cats.length){
    const top=cats.reduce((a,b)=>TREE[a].length>=TREE[b].length?a:b);
    OPEN[top]=true;
  }
  render();
});
function render(){
  const q=document.getElementById('q').value.trim().toLowerCase();
  const cf=document.getElementById('cat').value;
  const box=document.getElementById('list');box.innerHTML='';
  let shown=0;
  Object.keys(TREE).sort().forEach(c=>{
    if(cf&&c!==cf)return;
    const bs=TREE[c].filter(b=>!q||b.name.toLowerCase().includes(q));
    if(!bs.length)return;shown+=bs.length;
    const d=document.createElement('div');d.className='cat';
    const depth=Math.min(c.split('/').length-1,5);
    const last=c.split('/').pop();
    const h=document.createElement('div');
    h.className='cname d'+(depth+1);
    h.innerHTML='<span class="cn-l"><span style="color:#4a4f63">'+(OPEN[c]?'▾':'▸')+
      '</span><span class="cn-name">'+esc(last)+'</span></span>'+
      '<span class="cn-c">'+bs.length+' 本</span>';
    h.title=c;
    h.onclick=()=>{OPEN[c]=!OPEN[c];render()};
    d.appendChild(h);
    if(OPEN[c]){
      const g=document.createElement('div');g.className='grid';
      bs.forEach(b=>{
        const e=document.createElement('div');e.className='book';
        e.innerHTML='<div class="bn">'+esc(b.name)+'</div><div class="bm"><span class="tag '+
          (b.ext==='PDF'?'':b.ext==='EPUB'?'e':'p')+'">'+b.ext+'</span><span>'+
          (b.size>1048576?(b.size/1048576).toFixed(1)+' MB':Math.round(b.size/1024)+' KB')+'</span></div>';
        e.onclick=()=>openD(b);
        g.appendChild(e);
      });
      d.appendChild(g);
    }
    box.appendChild(d);
  });
  if(!shown)box.innerHTML='<div class="empty">没有匹配的书</div>';
}
function expandAll(){const v=!Object.keys(OPEN).some(k=>OPEN[k]);Object.keys(TREE).forEach(k=>OPEN[k]=v);render()}
function esc(s){return String(s).replace(/[&<>"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]))}
function closeD(){document.getElementById('detail').classList.remove('on')}
function openD(b){
  document.getElementById('detail').classList.add('on');
  document.getElementById('dName').textContent=b.name;
  document.getElementById('dMeta').textContent=b.path;
  document.getElementById('dBody').innerHTML='<div class="hint">点击下方按钮开始切章…</div>';
  window._cur=b;
  document.getElementById('dBody').innerHTML=
    '<button class="btn" onclick="doSplit()">开始切章</button>'+
    '<div class="hint">切章后会把章节交给我来出题，产出考试系统网页。<br>'+
    '这一步只拆书，不出题——出题需要理解全书语义，由我完成。</div>';
}
function doSplit(){
  const b=window._cur;
  document.getElementById('dBody').innerHTML='<div class="hint">切章中，大书可能要几十秒…</div>';
  const ctl=new AbortController();
  setTimeout(()=>ctl.abort(),15*60*1000);          // 15分钟兜底，别无限等
  fetch('/api/split',{method:'POST',headers:{'Content-Type':'application/json'},
    signal:ctl.signal,
    body:JSON.stringify({path:b.path,name:b.name})})
  .then(r=>r.json()).then(d=>{
    if(d.error){
      document.getElementById('dBody').innerHTML='<h2>切章失败</h2><pre>'+esc(d.log||d.error)+'</pre>'+
        '<h2>要我帮你看吗</h2><pre id="prompt">'+esc(d.prompt||'')+'</pre>'+
        '<div class="row"><button class="btn" onclick="copyP()">复制指令</button>'+
        '<button class="btn g" onclick="closeD()">关闭</button></div>';
      return;
    }
    document.getElementById('dBody').innerHTML=
      '<h2>切章完成</h2><pre>'+esc(d.log)+'</pre>'+
      '<h2>把这段发给我，我就能出题</h2><pre id="prompt">'+esc(d.prompt)+'</pre>'+
      '<div class="row"><button class="btn" onclick="copyP()">复制指令</button>'+
      '<button class="btn g" onclick="closeD()">关闭</button></div>';
  }).catch(e=>{document.getElementById('dBody').innerHTML='<h2>出错了</h2><pre>'+
    esc(e.name==='AbortError'?'超时了，这本书可能太大。可以用命令行切：\n  python split_book.py "路径" --out "输出"'
                            :String(e))+'</pre>'});
}
function copyP(){
  const t=document.getElementById('prompt');
  navigator.clipboard.writeText(t.textContent).then(()=>{t.style.color='#7ddba8';setTimeout(()=>t.style.color='#9fe0c0',1200)});
}
</script></body></html>'''


class Handler(http.server.BaseHTTPRequestHandler):
    shelf = DEFAULT_SHELF
    lib = DEFAULT_SHELF
    protocol_version = 'HTTP/1.1'          # 必须：否则连接会被直接关掉，客户端读不到响应

    def log_message(self, *a):
        pass                                   # 静默，别刷屏

    def _send(self, code, body, ctype='application/json; charset=utf-8'):
        b = body.encode('utf-8') if isinstance(body, str) else body
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        p = urllib.parse.urlparse(self.path).path
        if p == '/':
            self._send(200, HTML, 'text/html; charset=utf-8')
        elif p == '/api/scan':
            tree, n = scan_shelf(self.shelf)
            if tree is None:
                self._send(200, json.dumps({'error': n, 'tree': {}, 'total': 0}))
            else:
                self._send(200, json.dumps({'tree': tree, 'total': n,
                                            'shelf': self.shelf}))
        else:
            self._send(404, '{"error":"not found"}')

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != '/api/split':
            return self._send(404, '{"error":"not found"}')
        try:
            n = int(self.headers.get('Content-Length', 0))
            d = json.loads(self.rfile.read(n).decode('utf-8'))
        except Exception as e:
            return self._send(400, json.dumps({'error': str(e)}, ensure_ascii=False))

        path = d.get('path', '')
        if not os.path.isfile(path):
            return self._send(400, json.dumps({'error': '文件不存在', 'log': path},
                                              ensure_ascii=False))
        sp = find_splitter()
        if not sp:
            return self._send(400, json.dumps(
                {'error': '找不到 split_book.py',
                 'log': '请把 split_book.py 与本脚本放在同一目录'}, ensure_ascii=False))

        name = re.sub(r'[\\/:*?"<>|]', '', d.get('name', 'book'))[:50] or 'book'
        out = os.path.join(self.lib, name)

        # 同步跑：切章几秒到几十秒，前端本来就显示「切章中」。
        # 异步线程在 HTTP/1.1 下容易出现响应与连接关闭的竞态，直接同步最稳。
        try:
            rc, log = run_split(path, out, sp)
            payload = {'log': log, 'prompt': build_prompt(name, out, log, rc), 'out': out}
            if rc != 0:
                payload['error'] = '切章失败'
        except Exception:
            import traceback
            tb = traceback.format_exc()
            payload = {'error': '切章时出错', 'log': tb[-1200:],
                       'prompt': '这本书切章时脚本报错了，帮我看看：\n\n书籍：%s\n\n%s' % (name, tb[-800:])}
        self._send(200, json.dumps(payload, ensure_ascii=False))


def build_prompt(name, out_dir, log, rc):
    """生成给 AI 的施工指令"""
    # 书名去掉盗版站标记与作者后缀，看着干净
    clean = clean_title(name) if '(' in name else name
    if rc != 0:
        advice = ''
        if '扫描版' in log or '文字层' in log:
            advice = '\n\n提示：这本是扫描版，需要先找文字版或做OCR。'
        return ('这本书切章失败了，帮我看看问题。\n\n'
                '书籍：%s\n章节包目录：%s%s\n\n切章日志：\n%s'
                % (clean, out_dir, advice, log))
    info = os.path.join(out_dir, '书籍信息.json')
    summary = ''
    if os.path.isfile(info):
        try:
            d = json.load(open(info, encoding='utf-8'))
            summary = ('书名：%s\n作者：%s\n章节数：%d\n总字数：%d'
                       % (clean_title(d.get('书名', '')) or clean,
                          d.get('作者', ''), d.get('章节数', 0), d.get('总字数', 0)))
        except Exception:
            pass
    return ('用 book-to-exam 把这本书做成考试系统。\n\n'
            '章节包目录：%s\n%s\n\n'
            '请按 skill 的六步流程走完：读章节 → 写知识框架 → 出纯选择题 → 构建 → 校验 → 交付。\n'
            '要求全程零输入框，题型只用 choice/scenario/case。'
            % (out_dir, summary))


def main():
    ap = argparse.ArgumentParser(description='本地书架：扫书 → 切章 → 生成给AI 的指令')
    ap.add_argument('shelf', nargs='?', default=DEFAULT_SHELF, help='书库目录')
    ap.add_argument('--lib', default=None, help='章节包输出目录，默认与书库同级的 _章节包')
    ap.add_argument('--port', type=int, default=8760)
    ap.add_argument('--no-browser', action='store_true')
    a = ap.parse_args()

    shelf = os.path.abspath(a.shelf)
    if not os.path.isdir(shelf):
        sys.exit('错误：书库目录不存在 %s\n请用：python 书架.py "你的书库路径"' % shelf)

    lib = a.lib or os.path.join(os.path.dirname(shelf), '_章节包')
    os.makedirs(lib, exist_ok=True)
    Handler.shelf, Handler.lib = shelf, os.path.abspath(lib)

    sp = find_splitter()
    tree, n = scan_shelf(shelf)
    print('书库：%s' % shelf)
    print('扫到：%d 本书 / %d 个分类' % (n or 0, len(tree or {})))
    print('章节包输出：%s' % Handler.lib)
    print('切章脚本：%s' % (sp or '未找到（切章功能不可用）'))
    if n == 0:
        print('\n提示：这个目录下没找到 pdf/epub/txt/md，确认路径对不对？')

    Handler.port = a.port

    class S(socketserver.ThreadingTCPServer):
        allow_reuse_address = True

    for port in range(a.port, a.port + 20):
        try:
            S.daemon_threads = True
            httpd = S(('127.0.0.1', port), Handler)
            break
        except OSError:
            continue
    else:
        sys.exit('错误：%d~%d 端口都被占用' % (a.port, a.port + 19))

    url = 'http://127.0.0.1:%d/' % port
    print('\n打开：%s' % url)
    print('关闭：在本窗口按 Ctrl+C\n')
    if not a.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\n已关闭。')


if __name__ == '__main__':
    main()