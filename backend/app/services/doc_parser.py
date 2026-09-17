"""上传文档的解析与切块。

## 支持什么

| 扩展名 | 解析方式 | 说明 |
|---|---|---|
| `.md` / `.markdown` | 直接解码 | 质量最好 —— 标题层级天然适合切块,出处也能定位到小节 |
| `.txt` / `.text` | 直接解码 | 先按标题切,没有标题就走长度兜底 |
| `.pdf` | markitdown | **只支持有文字层的**,扫描件明确拒绝 |

## 刻意**不支持**图片

这不是没来得及做,是判断后不做:风景照里没有可检索的文字;攻略截图要先 OCR,
要么引一套 OCR 服务、要么换成支持视觉的模型(本项目的 deepseek-flash 不支持图像)。
花两三天接进去,检索质量还是不如用户直接把文字复制过来粘贴。

文档解析(OCR / 版面还原 / 表格抽取)属于**数据工程的深水区**,
不是这个项目要展示的后端工程能力 —— 与「不自己实现 HNSW/BM25」是同一个取舍。

## 两个必须处理的坑

**一、编码。** 中文 txt 有相当一部分是 GBK/GB18030。直接 `utf-8` 解码会抛
`UnicodeDecodeError`,而用户看到的只是一句"解析失败",根本不知道原因。
这里按 UTF-8 → GB18030 顺序试,都不行才降级,并且**明确告知**降级了。

**二、没有标题的长文本。** `split_markdown_sections()` 是按标题切的,所以一段
从小红书复制来的、通篇没有 `#` 的 3000 字攻略会被切成**一个巨块**。
巨块的向量会被平均掉,检索时要么整篇命中、要么完全命中不了,等于没有检索。
所以标题切分之后再加一道**按长度兜底**:超过上限的块,按空行段落再切到 600 字左右。

⚠️ 这道兜底**只作用于上传内容**,不改内置攻略的切块行为 ——
内置库那 30 块的形态是 P7 评测的基线,动了它基线就不可比了。
"""

from __future__ import annotations

import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .knowledge_service import split_markdown_sections

# 扩展名 → 内部 origin 标识。用白名单而不是黑名单:
# 用户可能传任何东西上来,黑名单永远漏。
SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".md": "md",
    ".markdown": "md",
    ".txt": "txt",
    ".text": "txt",
    ".pdf": "pdf",
}

MAX_TITLE_CHARS = 80

# 解析出来的正文少于这个字数就认为"没解析出内容"。
# 取 40 是因为它比任何一段正常攻略都短,又比页眉页脚那种噪音长。
MIN_USEFUL_CHARS = 40

# 单篇正文上限。5 MB 的文件本来也到不了这个量级,
# 这是防止畸形输入把 SQLite 撑爆的兜底。
MAX_DOC_CHARS = 200_000

CHUNK_TARGET_CHARS = 600
CHUNK_MAX_CHARS = 1200


class ParseError(Exception):
    """解析失败。

    `str(exc)` 是**面向用户**的话术 —— 接口层会原样返回给前端显示,
    所以这里每次抛异常都要写清楚"是什么问题、该怎么办",
    不能只写"解析失败"。
    """


@dataclass
class ParsedDocument:
    """解析结果。`doc_id` 由正文哈希得来,所以同一份内容天然是同一个 id。"""

    doc_id: str
    title: str
    origin: str
    text: str
    char_count: int
    content_hash: str = ""
    chunks: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)


# ===========================================================================
# 一、解码
# ===========================================================================


def extension_of(filename: str) -> str:
    name = (filename or "").strip().lower()
    dot = name.rfind(".")
    return name[dot:] if dot >= 0 else ""


def _decode_text(data: bytes) -> tuple[str, list[str]]:
    """猜编码解码。返回 `(文本, 警告列表)`。"""
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(enc), []
        except UnicodeDecodeError:
            continue

    # GB18030 几乎能"成功"解出任何字节序列,所以**必须**排在 UTF-8 之后 ——
    # 放前面的话,UTF-8 文件也会被解成一脸的乱码而毫无报错。
    try:
        return data.decode("gb18030"), []
    except UnicodeDecodeError:
        return (
            data.decode("utf-8", errors="replace"),
            ["文件编码识别不了,已按 UTF-8 强制解码,部分字符可能是乱码。建议另存为 UTF-8 后重新上传。"],
        )


# ===========================================================================
# 二、PDF
# ===========================================================================


def _parse_pdf(data: bytes) -> str:
    """用 markitdown 抽 PDF 的文字层。

    用 markitdown 而不是 pypdf:它在 requirements 里已经声明了
    (当初是为别的用途引入的),PDF 只是它的一个可选后端 —— 装 `markitdown[pdf]`
    即可,不必再添一个新库。
    """
    try:
        from markitdown import MarkItDown
    except Exception as exc:  # pragma: no cover - 依赖缺失时才走到
        raise ParseError(
            "服务端缺少文档解析依赖 markitdown,读不了 PDF。"
            "请在 backend 目录执行 pip install 'markitdown[pdf]' 后重启后端。"
        ) from exc

    try:
        # pdfminer 对常见 PDF(比如 Chromium 导出的)会刷一大堆
        # 「Could not get FontBBox…」WARNING —— 不影响结果,但会把后端日志刷满,
        # 真正的报错反而被淹没。压到 ERROR。
        import logging

        logging.getLogger("pdfminer").setLevel(logging.ERROR)

        result = MarkItDown(enable_plugins=False).convert_stream(
            io.BytesIO(data), file_extension=".pdf"
        )
        text = result.text_content or ""
    except Exception as exc:
        blob = f"{type(exc).__name__}: {exc}"
        # markitdown 的 PDF 后端是可选依赖,缺它时会抛一大段英文说明。
        # 原样透给用户等于什么都没说 —— 换成一句能照做的。
        if "MissingDependency" in blob or "pdfminer" in blob.lower() or "[pdf]" in blob:
            raise ParseError(
                "服务端未安装 PDF 解析依赖。请在 backend 目录执行 "
                "pip install 'markitdown[pdf]' 后重启后端。"
            ) from exc
        raise ParseError(f"这份 PDF 读不出来:{exc}") from exc

    if len(text.strip()) < MIN_USEFUL_CHARS:
        # 扫描件的典型症状:文件本身能打开,但抽不出文字层。
        # 这里**必须说清楚**是"没有文字层",否则用户会以为是自己文件坏了,
        # 反复重传同一个文件。
        raise ParseError(
            "这份 PDF 抽不出文字 —— 它多半是扫描件或图片导出的(没有文字层)。"
            "本系统不做 OCR。请把正文复制出来,用「粘贴文字」上传。"
        )
    return text


# ===========================================================================
# 三、切块
# ===========================================================================


def _paragraphs(text: str) -> list[str]:
    """按空行切段,丢掉空段。"""
    out: list[str] = []
    buf: list[str] = []
    for line in text.splitlines():
        if line.strip():
            buf.append(line.rstrip())
        elif buf:
            out.append("\n".join(buf))
            buf = []
    if buf:
        out.append("\n".join(buf))
    return out


def _hard_split(para: str, max_chars: int) -> list[str]:
    """单段自身就超上限时(例如 PDF 抽出来的一大坨没有空行的正文)硬切。

    优先在句末标点处切 —— 从句子中间断开会让这一块的语义变残。
    找不到合适标点时才按字数切断。
    """
    pieces: list[str] = []
    rest = para
    while len(rest) > max_chars:
        window = rest[:max_chars]
        cut = max(window.rfind(ch) for ch in "。！？!?；;\n")
        if cut < max_chars // 2:
            cut = max_chars
        pieces.append(rest[:cut].strip())
        rest = rest[cut:]
    if rest.strip():
        pieces.append(rest.strip())
    return [p for p in pieces if p]


def _split_long_chunk(chunk: str, heading_path: str, source: str) -> list[str]:
    """把超长块按段落合并到 `CHUNK_TARGET_CHARS` 左右。
    """
    merged: list[str] = []
    cur = ""
    for para in _paragraphs(chunk):
        if len(para) > CHUNK_MAX_CHARS:
            if cur:
                merged.append(cur)
                cur = ""
            merged.extend(_hard_split(para, CHUNK_MAX_CHARS))
            continue

        candidate = f"{cur}\n{para}" if cur else para
        # 到目标长度就收口。判断里带 `cur` 是为了避免把单个略超目标的段落
        # 独自丢进一轮 —— 那会切出一堆只有一两行的碎块。
        if len(candidate) > CHUNK_TARGET_CHARS and cur:
            merged.append(cur)
            cur = para
        else:
            cur = candidate

    if cur:
        merged.append(cur)

    head = heading_path or source
    out: list[str] = []
    for i, piece in enumerate(merged):
        # 第二块往后要**补回标题**。`split_markdown_sections` 之所以把标题拼进
        # 正文,就是因为光看正文常常看不出这段在说哪个城市;切碎了更不能丢。
        if i > 0 and head and not piece.startswith(head):
            out.append(f"{head}\n{piece}")
        else:
            out.append(piece)
    return out


def chunk_document(text: str, source: str = "") -> list[tuple[str, str]]:
    """先按标题切,再对超长块做长度兜底。返回 `[(heading_path, chunk_text)]`。"""
    # 标题路径以文档标题开头时去掉它:metadata 里的 source 本来就是标题,
    # 不去掉的话出处会显示成「成都三日游 > 成都三日游 > 美食」。
    # 注意只动 metadata,**不动正文** —— 正文里带着标题是为了给向量上下文。
    prefix = f"{source} > " if source else ""

    out: list[tuple[str, str]] = []
    for heading_path, chunk in split_markdown_sections(text, source):
        if prefix and heading_path.startswith(prefix):
            heading_path = heading_path[len(prefix):]

        if len(chunk) <= CHUNK_MAX_CHARS:
            out.append((heading_path, chunk))
            continue

        pieces = _split_long_chunk(chunk, heading_path, source)
        for i, piece in enumerate(pieces):
            # 只有真的切碎了才编号。没切碎的保持原 heading_path,
            # 免得出处里出现「北京 > 门票与预约（第 1 段）」这种怪名字。
            path = (
                heading_path
                if len(pieces) == 1
                else (f"{heading_path} · 第 {i + 1} 段" if heading_path else f"第 {i + 1} 段")
            )
            out.append((path, piece))
    return out


# ===========================================================================
# 四、对外入口
# ===========================================================================


def _pick_title(text: str, fallback: str) -> str:
    """优先用正文里的第一个一级标题。

    文件名往往是「未命名.pdf」「下载(3).md」这种,用正文标题更像话;
    而且行程页展示出处时,用户要认的也是攻略自己的名字。
    """
    for line in text.splitlines()[:20]:
        m = re.match(r"^#\s+(.+)$", line.strip())
        if m:
            title = m.group(1).strip()
            if title:
                return title[:MAX_TITLE_CHARS]
    return fallback[:MAX_TITLE_CHARS]


def _title_from_filename(filename: str) -> str:
    stem = Path(filename or "").name
    if "." in stem:
        stem = stem[: stem.rfind(".")]
    return (stem.strip() or "未命名攻略")[:MAX_TITLE_CHARS]


def _normalize(raw: str) -> str:
    """归一化。这一步**直接影响检索能不能命中**,不是可有可无的清理。

    实测(Edge 打印的 PDF)会抽出一类**兼容字符**:「日」被抽成康熙部首
    `⽇`(U+2F47)而不是「日」(U+65E5)。人眼看上去一模一样,
    但它是不同的码点 —— 向量不同,用户拿正常写法去检索就**检索不到**,
    而且界面上完全看不出哪里不对。

    NFKC 正规化把这类字符折回原形,顺带处理全角/半角、连字等同类问题。
    代价是正文与原文逐字不再完全一致 —— 对检索库来说这是净收益。
    """
    return unicodedata.normalize("NFKC", raw.replace("\r\n", "\n").replace("\r", "\n")).strip()


def _build(title: str, origin: str, raw: str, warnings: list[str]) -> ParsedDocument:
    normalized = _normalize(raw)

    if len(normalized) < MIN_USEFUL_CHARS:
        raise ParseError(
            f"只解析出 {len(normalized)} 个字,太少,没法入库。"
            "请确认文件里确实有文字(而不是只有图片或空格)。"
        )
    if len(normalized) > MAX_DOC_CHARS:
        raise ParseError(
            f"正文有 {len(normalized):,} 字,超过单篇上限 {MAX_DOC_CHARS:,} 字。"
            "请拆成几份分别上传。"
        )

    chunks = chunk_document(normalized, source=title)
    if not chunks:
        raise ParseError("这份文档切不出任何内容块(可能只有标题、没有正文)。")

    return ParsedDocument(
        # 用正文哈希当 id:同一份内容无论传几次都是同一个 doc_id,
        # 重复上传因此能被识别,chunk id 也天然稳定(upsert 幂等)。
        content_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        doc_id=hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12],
        title=title,
        origin=origin,
        text=normalized,
        char_count=len(normalized),
        chunks=chunks,
        warnings=warnings,
    )


def parse_file(filename: str, data: bytes) -> ParsedDocument:
    """解析上传的文件。类型不支持 / 读不出来都抛 `ParseError`。"""
    ext = extension_of(filename)
    origin = SUPPORTED_EXTENSIONS.get(ext)
    if origin is None:
        supported = "、".join(sorted({e.lstrip('.') for e in SUPPORTED_EXTENSIONS}))
        raise ParseError(
            f"不支持的文件类型「{ext or '无扩展名'}」。目前支持:{supported}。"
            "(图片不做 OCR —— 请把攻略里的文字复制出来用「粘贴文字」上传。)"
        )
    if not data:
        raise ParseError("文件是空的。")
    if origin == "pdf":
        raw_text, warnings = _parse_pdf(data), []
    else:
        raw_text, warnings = _decode_text(data)

    # 先归一化再选标题:标题也要是正常写法,否则出处里出现「成都三⽇游」,
    # 用户复制它去搜索照样搜不到。
    data_text = _normalize(raw_text)

    fallback = _title_from_filename(filename)
    return _build(_pick_title(data_text, fallback), origin, data_text, warnings)


def parse_paste(text: str, title: str = "") -> ParsedDocument:
    """解析粘贴进来的文字。"""
    if not (text or "").strip():
        raise ParseError("粘贴的内容是空的。")

    text = _normalize(text)
    fallback = (title or "").strip()[:MAX_TITLE_CHARS] or "粘贴的攻略"
    return _build(_pick_title(text, fallback), "paste", text, [])
