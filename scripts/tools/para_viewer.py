#!/usr/bin/env python3
"""段落浏览器 — 将 paragraphs_before_classify.json 展示到前端"""

import json, re, http.server, socketserver, os, sys, urllib.parse

PORT = 8511
DATA_PATH = "/home/ls/xiaoyue/LLM2/LMLLM/paragraphs_no_maxchunk.json"

# ── 加载数据 ──
with open(DATA_PATH, encoding="utf-8") as f:
    PARAGRAPHS = json.load(f)

print(f"[server] 加载 {len(PARAGRAPHS)} 段落")

# ── 标签颜色 ──
COMP_COLORS = {
    "anode": "#4A90D9",
    "cathode": "#E67E22",
    "electrolyte": "#27AE60",
}

HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>段落浏览器 — 前分类段落</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, 'Segoe UI', sans-serif; background: #f5f6fa; color: #2c3e50; }
.header { background: #2c3e50; color: #fff; padding: 20px; position: sticky; top: 0; z-index: 100; }
.header h1 { font-size: 20px; font-weight: 600; }
.header .stats { font-size: 13px; color: #bbb; margin-top: 4px; }
.controls { display: flex; gap: 12px; padding: 16px 20px; background: #fff; border-bottom: 1px solid #ddd; flex-wrap: wrap; align-items: center; }
.controls input, .controls select { padding: 8px 12px; border: 1px solid #ccc; border-radius: 6px; font-size: 14px; }
.controls input { flex: 1; min-width: 200px; }
.controls select { min-width: 100px; }
.controls .count { font-size: 13px; color: #888; margin-left: auto; }
#list { max-width: 1000px; margin: 20px auto; padding: 0 16px; }
.card { background: #fff; border-radius: 8px; padding: 16px 20px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.card .meta { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.card .tag { display: inline-block; padding: 2px 10px; border-radius: 12px; color: #fff; font-size: 12px; font-weight: 500; }
.card .doi { color: #4A90D9; text-decoration: none; }
.card .doi:hover { text-decoration: underline; }
.card .len { color: #999; }
.card .file { color: #666; }
.card .content { line-height: 1.7; font-size: 14px; }
.card .content .hl { background: #fff3b0; padding: 0 2px; }
.pagination { display: flex; justify-content: center; gap: 8px; padding: 20px; }
.pagination button { padding: 6px 16px; border: 1px solid #ccc; border-radius: 6px; background: #fff; cursor: pointer; }
.pagination button:hover { background: #eee; }
.pagination button.active { background: #2c3e50; color: #fff; border-color: #2c3e50; }
.pagination button:disabled { opacity: .4; cursor: default; }
.highlight { background: #fff3b0; }
</style>
</head>
<body>
<div class="header">
  <h1>📄 前分类段落浏览</h1>
  <div class="stats" id="stats"></div>
</div>
<div class="controls">
  <input type="text" id="search" placeholder="搜索段落内容/DOI..." autofocus>
  <select id="compFilter">
    <option value="">全部组件</option>
    <option value="anode">anode</option>
    <option value="cathode">cathode</option>
    <option value="electrolyte">electrolyte</option>
  </select>
  <select id="sort">
    <option value="idx">默认排序</option>
    <option value="len_desc">长度 ↓</option>
    <option value="len_asc">长度 ↑</option>
  </select>
  <span class="count" id="count"></span>
</div>
<div id="list"></div>
<div class="pagination" id="pagination"></div>

<script>
let DATA = [];
fetch("/data")
.then(r=>r.json())
.then(d=>{DATA=d; render();})
.catch(e=>document.getElementById("list").innerHTML="<p>加载数据失败</p>");

const PAGE_SIZE = 20;
let page = 0, filtered = [];

function render() {
  const search = document.getElementById('search').value.toLowerCase();
  const comp = document.getElementById('compFilter').value;
  const sort = document.getElementById('sort').value;

  filtered = DATA.filter(d => {
    if (comp && d.component !== comp) return false;
    if (search) {
      const q = search.toLowerCase();
      return d.paragraph.toLowerCase().includes(q) || d.doi.toLowerCase().includes(q) || d.title.toLowerCase().includes(q) || d.source_file.toLowerCase().includes(q);
    }
    return true;
  });

  if (sort === 'len_desc') filtered.sort((a,b) => b.length - a.length);
  else if (sort === 'len_asc') filtered.sort((a,b) => a.length - b.length);
  else filtered.sort((a,b) => a.index - b.index);

  document.getElementById('count').textContent = filtered.length + ' 段';

  const totalPages = Math.ceil(filtered.length / PAGE_SIZE);
  if (page >= totalPages) page = totalPages - 1;
  if (page < 0) page = 0;

  const start = page * PAGE_SIZE;
  const items = filtered.slice(start, start + PAGE_SIZE);

  const list = document.getElementById('list');
  list.innerHTML = items.map((d, i) => {
    const idx = start + i + 1;
    const color = %COLORS%;
    const c = color[d.component] || '#888';
    let para = d.paragraph;
    if (search) {
      const re = new RegExp('(' + search.replace(/[.*+?^${}()|[\]\\]/g, '\\\\$&') + ')', 'gi');
      para = para.replace(re, '<span class="hl">$1</span>');
    }
    const titleDisplay = d.title ? d.title : '(无标题)';
    return `<div class="card">
      <div class="meta">
        <span class="tag" style="background:${c}">${d.component}</span>
        <a class="doi" href="https://doi.org/${d.doi}" target="_blank">${d.doi}</a>
        <span class="len">${d.length} chars</span>
        <span style="color:#999">#${idx}</span>
      </div>
      <div class="meta" style="margin-top:2px">
        <span class="file" style="font-size:12px">📁 ${d.source_file}</span>
      </div>
      <div style="font-size:13px;color:#555;margin:4px 0 8px 0;padding:4px 8px;background:#f8f9fa;border-radius:4px;border-left:3px solid ${c}">📖 ${titleDisplay}</div>
      <div class="content">${para}</div>
    </div>`;
  }).join('');

  // 分页
  const pg = document.getElementById('pagination');
  let html = '<button onclick="goto(0)" ' + (page===0?'disabled':'') + '>首页</button>';
  html += '<button onclick="goto(' + (page-1) + ')" ' + (page===0?'disabled':'') + '>上一页</button>';
  html += '<span style="padding:6px 12px">' + (page+1) + '/' + totalPages + '</span>';
  html += '<button onclick="goto(' + (page+1) + ')" ' + (page>=totalPages-1?'disabled':'') + '>下一页</button>';
  html += '<button onclick="goto(' + (totalPages-1) + ')" ' + (page>=totalPages-1?'disabled':'') + '>末页</button>';
  pg.innerHTML = html;
}

function goto(p) { page = p; render(); }

document.getElementById('search').addEventListener('input', () => { page=0; render(); });
document.getElementById('compFilter').addEventListener('change', () => { page=0; render(); });
document.getElementById('sort').addEventListener('change', () => { page=0; render(); });

// 注入 index
DATA.forEach((d,i) => d.index = i);

// render called after data loads
</script>
</body>
</html>"""

# 替换占位符
COMP_COLORS_JSON = json.dumps(COMP_COLORS)
HTML_FINAL = HTML.replace("%JSON%", "[]")
HTML_FINAL = HTML_FINAL.replace("%COLORS%", COMP_COLORS_JSON)

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(HTML_FINAL.encode("utf-8"))
        elif parsed.path == "/data":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(PARAGRAPHS, ensure_ascii=False).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {args[0]} {args[1]} {args[2]}")

if __name__ == "__main__":
    print(f"\n  \033[92m✓ 段落浏览器已启动\033[0m")
    print(f"  \033[94m  http://localhost:{PORT}\033[0m")
    print(f"  数据: {len(PARAGRAPHS)} 段落 | 端口: {PORT}")
    print(f"  \033[90m按 Ctrl+C 停止\033[0m\n")
    with socketserver.TCPServer(("0.0.0.0", PORT), Handler) as httpd:
        httpd.serve_forever()
