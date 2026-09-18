"""图片攻略(截图)识别链路的测试

这条链路**每一环都可能静默出错**,所以测试重点不是"函数有返回值":

- **压缩**:缩过头 → 小字糊掉、识别出错;不缩 → 接口体积超限被拒
- **MIME 与内容不符**:data URL 写成 image/png 而字节是 JPEG。
  多数接口会嗅探真实字节所以**侥幸能过**,一换供应商就炸 —— 已踩过一次
- **图里没文字**:模型很爱描述画面或道歉,那些句子是文字但零检索价值,
  入库只会稀释检索结果。必须靠标记明确拒绝
- **模型不支持图像**:换回文本模型时,报错必须指向"换模型"这个动作,
  而不是让用户对着一段英文报错猜

网络调用通过替换 `doc_parser._vision_extractor` 切开 —— 这些测试**不连网、
不花钱**。真实模型的端到端验证另外做过(见提交说明)。
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.services import doc_parser as dp
from app.services.llm_service import (
    NO_TEXT_MARKER,
    extract_text_from_image,
    looks_like_no_text,
)


# ===========================================================================
# 造图工具
# ===========================================================================


def _png(size=(400, 300), mode="RGB", color="white") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg(size=(400, 300)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, format="JPEG")
    return buf.getvalue()


def _webp(size=(400, 300)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, format="WEBP")
    return buf.getvalue()


# 一段形状正确的"识别结果",用来验证编排而不是识别质量
OCR_TEXT = (
    "# 成都三日游攻略\n\n"
    "## 门票与预约\n"
    "- 武侯祠 门票 50 元,需提前 1 天预约\n"
    "- 杜甫草堂 门票 50 元,周一闭馆\n\n"
    "## 交通\n"
    "- 地铁 1 号线到天府广场,单程 2 元起\n"
)


@pytest.fixture()
def fake_vision(monkeypatch):
    """把视觉模型换成假的。返回一个记录调用参数的容器。"""

    calls: list[dict] = []

    def _make(result=OCR_TEXT, raises: Exception | None = None):
        def _fake(data: bytes, mime: str, *, hint: str = ""):
            calls.append({"data": data, "mime": mime, "hint": hint})
            if raises is not None:
                raise raises
            return result

        monkeypatch.setattr(dp, "_vision_extractor", lambda: _fake)
        return calls

    return _make


# ===========================================================================
# 一、白名单
# ===========================================================================


class TestExtensionWhitelist:
    @pytest.mark.parametrize("ext", [".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"])
    def test_图片扩展名被登记(self, ext):
        assert dp.SUPPORTED_EXTENSIONS[ext] == "image"

    @pytest.mark.parametrize("ext", [".PNG", ".Jpg", ".JPEG"])
    def test_大小写不敏感(self, ext):
        """用户从手机传上来的文件名各种大小写都有。"""
        assert dp.extension_of(f"攻略{ext}") in dp.SUPPORTED_EXTENSIONS

    def test_HEIC明确拒绝而不是含糊报错(self):
        """iPhone 默认格式。Pillow 不带这个解码器 —— 收了也只会在后面
        报一个看不懂的错,不如在前面说清楚。"""
        msg = str(pytest.raises(dp.ParseError, dp._prepare_image, b"xx", ".heic").value)
        assert "不是有效的图片" in msg

    def test_不支持的扩展名的提示里提到了图片(self):
        with pytest.raises(dp.ParseError) as ei:
            dp.parse_file("a.zip", b"xx")
        msg = str(ei.value)
        assert "图片" in msg and "HEIC" in msg


# ===========================================================================
# 二、图片预处理(不连网)
# ===========================================================================


class TestPrepareImage:
    def test_小图原样透传(self):
        """不超限就不重编码 —— 每重编码一次都是白白损失一次质量。"""
        data = _png((400, 300))
        out, mime = dp._prepare_image(data, ".png")
        assert out is data or out == data
        assert mime == "image/png"

    def test_超大图被缩到上限(self):
        data = _png((4000, 3000))
        out, _ = dp._prepare_image(data, ".png")
        assert max(Image.open(io.BytesIO(out)).size) == dp.IMAGE_MAX_EDGE

    def test_缩放后体积显著下降(self):
        data = _png((4000, 3000))
        out, _ = dp._prepare_image(data, ".png")
        assert len(out) < len(data)

    def test_带透明的图走PNG(self):
        """给带 alpha 的图用 JPEG 会把透明区变成黑块。"""
        data = _png((2500, 2500), mode="RGBA", color=(255, 255, 255, 0))
        _, mime = dp._prepare_image(data, ".png")
        assert mime == "image/png"

    def test_不带透明的图走JPEG(self):
        """照片转 PNG 会大到离谱。"""
        data = _png((2500, 2500), mode="RGB", color="white")
        _, mime = dp._prepare_image(data, ".png")
        assert mime == "image/jpeg"

    def test_MIME以真实格式为准而不是扩展名(self):
        """**这条对应一个真踩过的 bug。**

        JPEG 内容 + `.png` 后缀:一开始按扩展名给了 `image/png`,
        data URL 就成了 `data:image/png;base64,<JPEG 字节>` ——
        类型与内容不符。多数接口嗅探真实字节所以侥幸能过,一换供应商就炸。
        """
        _, mime = dp._prepare_image(_jpeg(), ".png")
        assert mime == "image/jpeg"

    @pytest.mark.parametrize(
        "data,ext,expected",
        [
            (lambda: _png(), ".png", "image/png"),
            (lambda: _jpeg(), ".jpg", "image/jpeg"),
            (lambda: _webp(), ".webp", "image/webp"),
        ],
    )
    def test_各格式的MIME(self, data, ext, expected):
        _, mime = dp._prepare_image(data(), ext)
        assert mime == expected

    def test_改了后缀的图仍然能用(self):
        """手机/聊天工具改后缀是常事,一个叫 .jpg 的 PNG 完全能用,没理由拒。"""
        out, mime = dp._prepare_image(_png(), ".jpg")
        assert Image.open(io.BytesIO(out)) is not None
        assert mime == "image/png"

    @pytest.mark.parametrize(
        "bad",
        [b"", b"not an image", b"PK\x03\x04zip", b"\x00" * 100],
        ids=["空字节", "纯文本", "改了后缀的压缩包", "全零字节"],
    )
    def test_不是图片的文件被拦住(self, bad):
        """改了后缀的压缩包不能往下走 —— 否则会在模型那里报一个看不懂的错。"""
        with pytest.raises(dp.ParseError) as ei:
            dp._prepare_image(bad, ".png")
        assert "不是有效的图片" in str(ei.value)


# ===========================================================================
# 三、parse_file 的图片编排(假视觉模型)
# ===========================================================================


class TestParseFileImage:
    def test_正常识别并切块(self, fake_vision):
        fake_vision()
        doc = dp.parse_file("成都攻略.png", _png())

        assert doc.origin == "image"
        assert doc.title == "成都三日游攻略"  # 取自识别出的 # 标题
        assert doc.chunk_count >= 2

    def test_来自图片的正文带警告(self, fake_vision):
        """识别误差集中在价格、时间、预约规则上 —— 必须让用户知道要核对。"""
        fake_vision()
        doc = dp.parse_file("成都攻略.png", _png())
        assert doc.warnings
        assert "图片识别" in doc.warnings[0]
        assert "核对" in doc.warnings[0]

    def test_标题被用来做切块出处(self, fake_vision):
        """识别结果里的 markdown 标题要真的参与切块 ——
        拼提示词时就要求模型输出 `#` 层级,正是为了这个:
        有标题,检索命中后才能告诉用户「出自 门票与预约」这一节。"""
        fake_vision()
        doc = dp.parse_file("成都攻略.png", _png())
        paths = [p for p, _ in doc.chunks]
        assert any("门票" in p for p in paths), paths

    def test_视觉模型收到的是压缩后的字节(self, fake_vision):
        calls = fake_vision()
        dp.parse_file("大图.png", _png((4000, 3000)))
        assert calls, "没有调用视觉模型"
        assert max(Image.open(io.BytesIO(calls[0]["data"])).size) == dp.IMAGE_MAX_EDGE

    def test_视觉模型收到正确的mime和文件名提示(self, fake_vision):
        calls = fake_vision()
        dp.parse_file("成都攻略.webp", _webp())
        assert calls[0]["mime"] == "image/webp"
        assert "成都攻略" in calls[0]["hint"]

    def test_图里没有文字时明确拒绝(self, fake_vision):
        """**这条是本次功能的核心守卫。**

        模型在图没文字时很爱描述画面或道歉,那种句子是文字、但零检索价值。
        入库只会稀释检索结果 —— 所以要让模型回固定标记,命中就拒。
        """
        fake_vision(result=NO_TEXT_MARKER)
        with pytest.raises(dp.ParseError) as ei:
            dp.parse_file("海边风景.png", _png())
        msg = str(ei.value)
        assert "没有识别出文字" in msg
        assert "截图" in msg or "粘贴" in msg  # 要给出可照做的下一步

    def test_识别结果太短时拒绝(self, fake_vision):
        """⚠️ 断言必须钉住**图片层**的话术。

        第一版这里断言的是 `"太少" in msg` —— 而 `_build()` 在更外层
        也会因为同样的原因抛"只解析出 N 个字,太少"。
        于是把图片层的守卫整个删掉,这条测试**依然会绿**。
        变异检查抓到了这一点,所以现在钉 `_parse_image` 独有的用词。
        """
        fake_vision(result="门票")
        with pytest.raises(dp.ParseError) as ei:
            dp.parse_file("碎片.png", _png())
        assert "这张图只识别出" in str(ei.value)

    def test_模型不支持图像时提示换模型(self, fake_vision):
        """换回文本模型时最常见的失败。报错必须指向"换模型"这个动作,
        而不是把一段英文 API 报错丢给用户。"""
        fake_vision(raises=Exception("Error code: 400 - invalid image_url: model does not support image input"))
        with pytest.raises(dp.ParseError) as ei:
            dp.parse_file("成都攻略.png", _png())
        msg = str(ei.value)
        assert "不支持图像" in msg
        assert "qwen3.7-plus" in msg  # 给出一个可用的替代

    def test_鉴权失败时提示查key(self, fake_vision):
        fake_vision(raises=Exception("Error code: 401 - Unauthorized: invalid api key"))
        with pytest.raises(dp.ParseError) as ei:
            dp.parse_file("成都攻略.png", _png())
        assert "LLM_API_KEY" in str(ei.value)

    def test_超时时提示重试(self, fake_vision):
        fake_vision(raises=Exception("Request timed out after 60s"))
        with pytest.raises(dp.ParseError) as ei:
            dp.parse_file("成都攻略.png", _png())
        assert "超时" in str(ei.value)

    def test_未知异常也带上原始信息(self, fake_vision):
        """出错的是用户自己的模型配置,他需要看到原因才能改。"""
        fake_vision(raises=Exception("某个没见过的错误 xyz"))
        with pytest.raises(dp.ParseError) as ei:
            dp.parse_file("成都攻略.png", _png())
        assert "xyz" in str(ei.value)

    def test_ParseError原样透传不被改写(self, fake_vision):
        """_prepare_image 抛的已经是面向用户的话,不该被包成"识别失败"。"""
        fake_vision = fake_vision()
        with pytest.raises(dp.ParseError) as ei:
            dp.parse_file("假的.png", b"not an image")
        assert "不是有效的图片" in str(ei.value)


# ===========================================================================
# 四、llm_service 的视觉封装
# ===========================================================================


class TestLooksLikeNoText:
    @pytest.mark.parametrize(
        "text",
        [NO_TEXT_MARKER, "no_text_found", "NO_TEXT_FOUND.", " No_Text_Found ", "NO_TEXT_FOUND。"],
    )
    def test_宽松匹配(self, text):
        """模型不会每次都一字不差地回同一个写法,大小写和句号都要容错。"""
        assert looks_like_no_text(text) is True

    @pytest.mark.parametrize(
        "text",
        ["", "门票 50 元", "这张图似乎是一张风景照,没有可提取的文字", "NO_TEXT_FOUND 但后面还有字"],
    )
    def test_不是标记的都不算(self, text):
        """最后一例很关键:标记**混在别的文字里**不算 ——
        那说明模型还是写了别的内容,需要人来判断,不能当"没文字"直接丢。"""
        assert looks_like_no_text(text) is False


class TestExtractTextFromImage:
    def _patch_llm(self, monkeypatch, reply: str, capture: dict):
        from app.services import llm_service as ls

        class _FakeLLM:
            def invoke(self, messages, **kwargs):
                capture["messages"] = messages
                capture["kwargs"] = kwargs
                return reply

        monkeypatch.setattr(ls, "get_llm", lambda: _FakeLLM())

    def test_构造多云模态消息(self, monkeypatch):
        cap: dict = {}
        self._patch_llm(monkeypatch, "识别结果", cap)

        out = extract_text_from_image(b"\x89PNG\r\n", "image/png", hint="成都.png")

        assert out == "识别结果"
        content = cap["messages"][1]["content"]
        assert isinstance(content, list), "多模态消息的 content 必须是数组"
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"
        # data URL 的 mime 段必须与传进来的 mime 一致
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")

    def test_要求确定性输出(self, monkeypatch):
        """提取任务不该有创造性 —— temperature 必须为 0。"""
        cap: dict = {}
        self._patch_llm(monkeypatch, "x", cap)
        extract_text_from_image(b"a", "image/png")
        assert cap["kwargs"]["temperature"] == 0

    def test_整段被代码块包住时剥掉(self, monkeypatch):
        """模型偶尔会把整段内容塞进 ``` 里,兜底剥掉 ——
        否则这些反引号会一起入库,检索时看着很脏。"""
        cap: dict = {}
        self._patch_llm(monkeypatch, "```markdown\n# 成都\n门票 50 元\n```", cap)
        out = extract_text_from_image(b"a", "image/png")
        assert out.startswith("# 成都")
        assert "```" not in out

    def test_正文内部的代码块不动(self, monkeypatch):
        """只剥"整段被包住"的情况。正文里本来就有局部代码块时不能动。"""
        cap: dict = {}
        original = "# 成都\n\n```bash\nnpm run dev\n```\n\n门票 50 元"
        self._patch_llm(monkeypatch, original, cap)
        assert extract_text_from_image(b"a", "image/png") == original

    def test_返回空字符串不炸(self, monkeypatch):
        cap: dict = {}
        self._patch_llm(monkeypatch, "", cap)
        assert extract_text_from_image(b"a", "image/png") == ""

    def test_返回None不炸(self, monkeypatch):
        cap: dict = {}
        self._patch_llm(monkeypatch, None, cap)
        assert extract_text_from_image(b"a", "image/png") == ""

    @pytest.mark.parametrize("reply", [NO_TEXT_MARKER, "no_text_found", "NO_TEXT_FOUND。", " No_text_Found "])
    def test_模型说没有文字时归一成标记(self, monkeypatch, reply):
        """**这条是变异检查补上的。**

        原来没有它:上一层的"图里没文字"测试是用**假提取器直接注入标记**的,
        所以 `extract_text_from_image` 里那段"把模型的松散回复归一成标记"
        的逻辑**从没被测到** —— 把它删掉,所有测试照样全绿。

        模型不会每次都一字不差地回同一个写法,大小写和标点都要归一。
        不归一的话,模型回一句 "No_Text_Found." 就会被当成正常正文入库。
        """
        cap: dict = {}
        self._patch_llm(monkeypatch, reply, cap)
        assert extract_text_from_image(b"a", "image/png") == NO_TEXT_MARKER

    def test_围栏里包着标记也能认出来(self, monkeypatch):
        """模型有可能会把标记也塞进代码块 —— 剥围栏要在判定标记之前发生。"""
        cap: dict = {}
        self._patch_llm(monkeypatch, "```\nNO_TEXT_FOUND\n```", cap)
        assert extract_text_from_image(b"a", "image/png") == NO_TEXT_MARKER


# ===========================================================================
# 五、计量:多模态消息不能把 token 估成天文数字
# ===========================================================================


class TestMultimodalMetering:
    def test_多模态消息的估算不失控(self):
        """`str(content)` 那种写法会把 base64 图片那一大串字符也算成"文字",
        估出来的数字离谱地大。

        影响面很窄(只在服务端没返回 usage 的兜底分支生效),但一旦生效
        就是"成本统计莫名其妙地高",而且很难联想到原因。
        """
        from app.observability.metering import estimate_messages_tokens

        image_tokens = estimate_messages_tokens(
            [
                {"role": "system", "content": "提示词"},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请提取文字"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 500_000}},
                    ],
                },
            ]
        )
        text_only = estimate_messages_tokens(
            [{"role": "user", "content": "请提取文字"}]
        )
        # 一张图的占位成本是有限值,不该因为 base64 有多长就跟着涨
        assert image_tokens < 5000, f"估成了 {image_tokens},说明把 base64 也算进去了"
        assert image_tokens > text_only, "带图应该比纯文字估得多"

    def test_纯文本消息不受影响(self):
        from app.observability.metering import estimate_messages_tokens

        assert estimate_messages_tokens([{"role": "user", "content": "abcd"}]) > 0

    def test_join_message_text只取文字片段(self):
        from app.observability.metering import join_message_text

        text = join_message_text(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "甲"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                        {"type": "text", "text": "乙"},
                    ],
                },
                {"role": "user", "content": "丙"},
            ]
        )
        assert text == "甲乙丙"
        assert "base64" not in text
