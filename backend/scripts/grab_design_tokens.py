"""抓参考站的设计令牌(颜色 / 圆角 / 阴影 / 渐变 / 字体),而不是靠截图猜色值。

用法::

    ./venv/Scripts/python.exe scripts/grab_design_tokens.py
    ./venv/Scripts/python.exe scripts/grab_design_tokens.py https://other-site.com/

为什么要有这个脚本
------------------
拿到一个参考站链接后,用肉眼对着截图调色值的结果通常是"差一点" ——
而差的那些点(圆角的档位、阴影的层数、灰阶的取法)恰恰是质感所在。
这个脚本把 HTML 与 CSS 拉下来,用正则把 `--color-*` / `border-radius` /
`box-shadow` / `linear-gradient` / `font-family` 全部列出来,
再按出现频次排序 —— 频次最高的那几个就是这套设计真正在用的值。

⚠️ 产物写到**系统临时目录**,不落在仓库里:参考站的 CSS/HTML 是第三方内容,
   放进本仓库会带来许可问题,也没必要。

只用标准库。本机有系统代理(127.0.0.1:7877)且开发环境还会注入 http_proxy,
所以这里**两条路都试**:先直连,失败再走代理。
"""

from __future__ import annotations

import re
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

SITE = sys.argv[1] if len(sys.argv) > 1 else "https://www.helloaitrip.cn/"
HOST = re.sub(r"^https?://", "", SITE).split("/")[0]
OUT = Path(tempfile.gettempdir()) / "tripmind_refsite"
OUT.mkdir(exist_ok=True)

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
PROXIED = urllib.request.build_opener()


def fetch(url: str) -> tuple[str, bytes]:
    last: Exception | None = None
    for label, op in (("直连", DIRECT), ("走系统代理", PROXIED)):
        req = urllib.request.Request(url, headers=UA)
        try:
            with op.open(req, timeout=25) as r:
                return label, r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"    {label} 失败: {type(e).__name__}: {e}")
    raise SystemExit(f"两种方式都拿不到 {url}: {last}")


print(f"1) 抓首页 HTML: {SITE}")
how, raw = fetch(SITE)
html = raw.decode("utf-8", errors="replace")
(OUT / "index.html").write_text(html, encoding="utf-8")
print(f"   {how}成功,{len(html)} 字符 -> index.html")

print("\n2) 找样式表与脚本")
links = re.findall(r'<link[^>]+href=["\']([^"\']+\.css[^"\']*)["\']', html)
links += re.findall(r'<link[^>]+rel=["\']stylesheet["\'][^>]*href=["\']([^"\']+)["\']', html)
links = list(dict.fromkeys(links))
for l in links:
    print(f"   css: {l}")

# Next.js 常见形态:内联 <style> 里已经有 @font-face 与 CSS 变量
inline = re.findall(r"<style[^>]*>(.*?)</style>", html, re.S)
print(f"   内联 <style> 块: {len(inline)} 个,共 {sum(len(s) for s in inline)} 字符")
(OUT / "inline.css").write_text("\n\n/* ---- next style block ---- */\n\n".join(inline), encoding="utf-8")


def absolutize(href: str) -> str:
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return f"https://{HOST}" + href
    if href.startswith("http"):
        return href
    return f"https://{HOST}/" + href


print("\n3) 下载外部样式表")
got = 0
for l in links:
    url = absolutize(l)
    try:
        _, css = fetch(url)
    except SystemExit as e:
        print(f"   跳过 {url}: {e}")
        continue
    name = re.sub(r"[^A-Za-z0-9._-]", "_", url.split("//")[-1])[-60:] or f"css{got}.css"
    (OUT / f"{got:02d}_{name}").write_bytes(css)
    print(f"   -> {got:02d}_{name}  ({len(css)} 字节)")
    got += 1

print("\n4) 从所有 CSS 里提取设计令牌")
all_css = (OUT / "inline.css").read_text(encoding="utf-8", errors="replace")
for f in sorted(OUT.glob("[0-9][0-9]_*")):
    all_css += "\n" + f.read_text(encoding="utf-8", errors="replace")

vars_found = re.findall(r"(--[A-Za-z0-9_-]+)\s*:\s*([^;{}]+);", all_css)
uniq: dict[str, str] = {}
for k, v in vars_found:
    uniq.setdefault(k, v.strip())
print(f"   CSS 变量 {len(uniq)} 个")
for k, v in sorted(uniq.items())[:80]:
    print(f"     {k}: {v}")

print("\n   —— 颜色字面量 top ——")
hexes = re.findall(r"#[0-9a-fA-F]{3,8}\b", all_css)
from collections import Counter  # noqa: E402

for h, n in Counter(x.lower() for x in hexes).most_common(25):
    print(f"     {h}  ×{n}")

print("\n   —— rgba()/oklch() top ——")
for c, n in Counter(re.findall(r"(?:rgba?|oklch|lab)\([^)]*\)", all_css)).most_common(15):
    print(f"     {c}  ×{n}")

print("\n   —— border-radius top ——")
for c, n in Counter(re.findall(r"border-radius:\s*([^;]+);", all_css)).most_common(15):
    print(f"     {c.strip()}  ×{n}")

print("\n   —— box-shadow top ——")
for c, n in Counter(re.findall(r"box-shadow:\s*([^;]+);", all_css)).most_common(12):
    print(f"     {c.strip()}  ×{n}")

print("\n   —— linear-gradient top ——")
for c, n in Counter(re.findall(r"linear-gradient\([^;]*?\)(?=[;\s\"'])", all_css)).most_common(12):
    print(f"     {c}  ×{n}")

print("\n   —— font-family top ——")
for c, n in Counter(re.findall(r"font-family:\s*([^;}}]+)", all_css)).most_common(10):
    print(f"     {c.strip()[:110]}  ×{n}")

print("\n5) 首页里出现的可见中文文案(取前 60 条,用于理解信息架构)")
text = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
text = re.sub(r"<style.*?</style>", " ", text, flags=re.S)
text = re.sub(r"<[^>]+>", "\n", text)
lines = [t.strip() for t in text.split("\n")]
seen = []
for t in lines:
    if 1 < len(t) < 60 and re.search(r"[\u4e00-\u9fff]", t) and t not in seen:
        seen.append(t)
for t in seen[:60]:
    print(f"     {t}")

print(f"\n产物目录: {OUT}")
sys.exit(0)
