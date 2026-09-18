"""上传文档的解析与切块。

## 支持什么

| 扩展名 | 解析方式 | 说明 |
|---|---|---|
| `.md` / `.markdown` | 直接解码 | 质量最好 —— 标题层级天然适合切块,出处也能定位到小节 |
| `.txt` / `.text` | 直接解码 | 先按标题切,没有标题就走长度兜底 |
| `.pdf` | markitdown | **只支持有文字层的**,扫描件明确拒绝 |
| `.png` / `.jpg` / `.jpeg` / `.webp` / `.bmp` / `.gif` | 视觉模型读图 | 攻略截图、门票说明、长图笔记 |

## 图片:为什么现在做了(2026-09-18 新增)

原来这里是"刻意不做",两条理由:**图里没有可检索的文字** + **模型不支持图像**。
换成 `qwen3.7-plus` 之后第二条不成立了,于是接进来。

但**第一条理由依然成立** —— 纯风景照确实没有可检索的文字。所以实现上
用两件事把住它,而不是"既然能做就什么都往库里塞":

1. **要求模型明确回答"没有文字"**(`NO_TEXT_MARKER`)。模型在图里没文字时
   很爱描述画面或道歉(「这张图似乎是一张风景照…」)—— 那种句子**是文字**,
   但零检索价值,入库只会稀释检索结果。所以用一个确定的标记来区分,
   命中就明确拒绝,而不是"识别成功但存了一堆废话"。
2. **入库内容带一条 warning**,界面上提示"内容由图片识别得到,数字请自行核对"。
   识别误差集中在价格、时间、预约规则这些最要紧的字段上,必须让用户知道。

⚠️ **扫描件 PDF 仍然不支持**(那需要先把 PDF 每页转成图,是另一件事),
但报错信息会指向"截图后按图片上传"这条现在真的可行的路。

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
from .llm_service import looks_like_no_text

# 扩展名 → 内部 origin 标识。用白名单而不是黑名单:
# 用户可能传任何东西上来,黑名单永远漏。
SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".md": "md",
    ".markdown": "md",
    ".txt": "txt",
    ".text": "txt",
    ".pdf": "pdf",
    # 图片走视觉模型。这些是常见到"用户随手截图就是它"的格式;
    # HEIC(iPhone 默认)没放进来 —— Pillow 不带这个解码器,
    # 收了也只会在后面报一个看不懂的错,不如在这里明确拒绝。
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".bmp": "image",
    ".gif": "image",
}

# 扩展名 → MIME。拼 base64 的 data URL 要用。
IMAGE_MIME: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
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

# 图片最长边超过它就缩。1600 是权衡:
# 攻略截图的文字在这个分辨率下依然清晰可读,而再大只是徒增 token 与耗时
# —— 视觉模型按图块计费,缩一半面积就省一半钱,识别质量几乎不变。
IMAGE_MAX_EDGE = 1600

# 字节数超过它就重新编码(即使尺寸没超)。
# 触发它的大多是手机实拍的照片(单张 3~5 MB),重新编码能压到几百 KB,
# 既避开接口的体积上限,也避免 base64 膨胀后再超限。
IMAGE_REENCODE_BYTES = 1_500_000

# 送去识别前的最终体积上限。原图我们已经在 upload 层卡到 5 MB,
# 这里卡的是**压缩之后**的结果 —— 还超说明是离谱的图(超大尺寸 + 无 alpha 也不可压),
# 与其让接口报一个看不懂的错,不如自己先拒绝。
IMAGE_MAX_SEND_BYTES = 4_000_000



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
            "本系统不做 PDF 转图,但你可以把里面的关键页**截图**,"
            "然后按图片上传 —— 那条路是通的。"
        )
    return text


# ===========================================================================
# 三、图片(视觉模型)
# ===========================================================================


def _vision_extractor():
    """取"图片转文字"的实现。

    单独一个函数是为了留出**唯一的替换点**:测试里把它换成假的,
    就能把图片这条链路的编排逻辑(压缩、无文字判定、warning、报错)

    全部覆盖掉,而不用真的连网调模型。

    延迟 import 也有实际作用:本模块的其余部分都是纯函数,
    单独 import doc_parser 不该被迫拉起整个 LLM 依赖链。
    """
    from .llm_service import extract_text_from_image

    return extract_text_from_image


def _prepare_image(data: bytes, ext: str) -> tuple[bytes, str]:
    """校验图片,必要时缩放/重编码。返回 `(字节, mime)`。

    **格式以 Pillow 认出来的为准,不信扩展名** —— 手机和聊天工具
    改后缀名是常事,一个叫 `.png` 的 JPEG 其实完全能用,没理由拒。
    反过来,一个叫 `.png` 的压缩包必须在这里被拦住。

    缩放策略:
    - 最长边 > `IMAGE_MAX_EDGE` → 缩
    - 字节数 > `IMAGE_REENCODE_BYTES` → 重编码(照片通常在这里被压到几百 KB)

    重编码时的格式选择:**带 alpha 用 PNG(保透明与文字锐利),
    不带 alpha 用 JPEG(照片体积小得多)**。给截图用 JPEG 会糊掉小字,
    给照片用 PNG 会大到离谱 —— 按内容选,不是按喜好选。
    """
    try:
        from PIL import Image
    except Exception as exc:  # pragma: no cover - 依赖缺失时才走到
        raise ParseError(
            "服务端缺少图片处理依赖 Pillow,读不了图片。"
            "请在 backend 目录执行 pip install -r requirements.txt 后重启后端。"
        ) from exc

    try:
        img = Image.open(io.BytesIO(data))
        img.load()  # 真正解码一次 —— 只 open 不 load 的话,坏图要到后面才炸
    except Exception as exc:
        raise ParseError(
            f"这个文件不是有效的图片(扩展名是「{ext or '无'}」,但内容不是)。"
            "请确认选对了文件。"
        ) from exc

    fmt = (img.format or "").upper()
    need_shrink = max(img.size) > IMAGE_MAX_EDGE
    need_reencode = len(data) > IMAGE_REENCODE_BYTES

    if not need_shrink and not need_reencode:
        # 小图原样送 —— 重编码一次只会白白损失一次质量
        return data, _mime_of(fmt, ext)

    if need_shrink:
        img.thumbnail((IMAGE_MAX_EDGE, IMAGE_MAX_EDGE), Image.LANCZOS)

    has_alpha = img.mode in ("RGBA", "LA") or (
        img.mode == "P" and "transparency" in img.info
    )

    buf = io.BytesIO()
    if has_alpha:
        img.convert("RGBA").save(buf, format="PNG", optimize=True)
        mime = "image/png"
    else:
        img.convert("RGB").save(buf, format="JPEG", quality=90, optimize=True)
        mime = "image/jpeg"

    out = buf.getvalue()
    if len(out) > IMAGE_MAX_SEND_BYTES:
        raise ParseError(
            f"图片压缩后仍有 {len(out) / 1024 / 1024:.1f} MB,太大了。"
            "请裁剪出有文字的部分再上传。"
        )
    return out, mime


def _mime_of(pillow_format: str, ext: str) -> str:
    """决定发给模型的 MIME **以 Pillow 认出来的为准,不信扩展名**。

    ⚠️ 这里踩过一次:一开始写的是 `IMAGE_MIME.get(ext) or ...` —— 扩展名优先。
    结果一张内容其实是 JPEG、但被改名成 `.png` 的图,data URL 里会写成
    `data:image/png;base64,<JPEG 字节>`,**类型与内容不符**。
    多数接口会去嗅探真实字节所以侥幸能过,但这是靠运气;
    正经做法是让 MIME 跟内容一致。

    扩展名只在 Pillow 认不出格式时兜底(几乎不会发生,因为能 open 就有 format)。
    """
    table = {
        "JPEG": "image/jpeg",
        "JPG": "image/jpeg",
        "PNG": "image/png",
        "WEBP": "image/webp",
        "BMP": "image/bmp",
        "GIF": "image/gif",
    }
    return table.get(pillow_format) or IMAGE_MIME.get(ext) or "image/png"


def _vision_error_message(exc: Exception) -> str:
    """把 LLM 侧的异常翻成用户能照做的话。

    这里**故意带上原始报错的片段**:出错的是用户自己的模型配置
    (没配 key / 模型不支持图像 / 配额用尽),他需要看到原因才能改。
    这与"内部错误不回显细节"不冲突 —— 那是防止泄漏服务端实现,
    而这是用户自己请求自己账号时的一次可行动失败。
    """
    blob = f"{type(exc).__name__}: {exc}"
    low = blob.lower()

    if any(k in low for k in ("image", "vision", "multimodal", "data:image")) and any(
        k in low for k in ("support", "invalid", "unsupported", "not allowed", "不支持")
    ):
        return (
            "当前配置的模型不支持图像理解,读不了这张图。"
            "请在 backend/.env 里换成支持视觉的模型(例如 qwen3.7-plus),"
            f"或改用「粘贴文字」。原始报错:{blob[:200]}"
        )
    if any(k in low for k in ("401", "unauthorized", "invalid api key", "api key")):
        return f"LLM 鉴权失败,图片识别用不了。请检查 backend/.env 里的 LLM_API_KEY。原始报错:{blob[:200]}"
    if any(k in low for k in ("timeout", "timed out", "timedout")):
        return f"图片识别超时了。图片可能过大或网络不稳,请稍后重试或裁剪后再传。原始报错:{blob[:200]}"

    return f"图片识别失败:{blob[:300]}"


def _parse_image(data: bytes, filename: str) -> tuple[str, list[str]]:
    """用视觉模型把图片里的文字读出来。返回 `(文本, 警告列表)`。"""
    image_bytes, mime = _prepare_image(data, extension_of(filename))

    extractor = _vision_extractor()
    try:
        raw_text = extractor(
            image_bytes, mime, hint=_title_from_filename(filename)
        )
    except ParseError:
        # _prepare_image 抛的已经是面向用户的话,原样往外传
        raise
    except Exception as exc:
        raise ParseError(_vision_error_message(exc)) from exc

    # 图里确实没有文字 —— 明确拒绝,而不是把模型描述画面的废话入库
    if looks_like_no_text(raw_text or ""):
        raise ParseError(
            "这张图里没有识别出文字。知识库只能检索文字,风景照/纯图片库进去也没用 —— "
            "请换一张带文字的截图(攻略、门票说明、笔记都行),或直接把文字粘贴进来。"
        )

    text = (raw_text or "").strip()
    if len(text) < MIN_USEFUL_CHARS:
        raise ParseError(
            f"这张图只识别出 {len(text)} 个字,太少,没法入库。"
            "如果图里确实有攻略内容,请换一张更清晰、文字更完整的截图。"
        )

    return text, [
        "内容由图片识别得到,可能存在识别误差(表格与手写体尤其明显)。"
        "请自行核对门票价格、开放时间、预约规则这类关键数字。"
    ]


# ===========================================================================
# 四、切块
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
# 五、对外入口
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
        # 按大类列出,而不是把所有扩展名摊平 —— 用户要判断的是
        # "我这份东西能不能传",不是认全 11 个后缀。
        raise ParseError(
            f"不支持的文件类型「{ext or '无扩展名'}」。"
            "支持:Markdown / TXT / PDF(需有文字层)/ 图片(png、jpg、webp、bmp、gif)。"
            "iPhone 的 HEIC 照片请先转成 jpg。"
        )
    if not data:
        raise ParseError("文件是空的。")

    if origin == "pdf":
        raw_text, warnings = _parse_pdf(data), []
    elif origin == "image":
        raw_text, warnings = _parse_image(data, filename)
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
