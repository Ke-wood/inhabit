"""消息段与 OneBot 通道的单元测试（不需要真 QQ）。

    python tests/test_onebot.py

覆盖：
- CQ 码 / 数组 / 字符串三种输入的解析
- 文本化（图片变 [图片]，@ 变 @名字）
- 构造各种消息段
- OneBot 事件解析（群/私聊、图片、语音、自己发的）
- 发送失败时的降级路径
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile

os.environ.setdefault("DAFEIYU_ROOT", tempfile.mkdtemp(prefix="inhabit-ob-"))
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from channels import message as seg          # noqa: E402
from channels import onebot                  # noqa: E402

ok = 0
fail = 0


def check(name, fn):
    global ok, fail
    try:
        fn()
        print(f"  ok   {name}")
        ok += 1
    except Exception as e:
        print(f"  FAIL {name}: {type(e).__name__}: {e}")
        fail += 1


def test_parse_cq():
    segs = seg.parse("[CQ:image,file=abc.jpg]看这个")
    assert segs[0]["type"] == "image", segs
    assert segs[0]["data"]["file"] == "abc.jpg"
    assert segs[1] == {"type": "text", "data": {"text": "看这个"}}, segs[1]


def test_parse_cq_escape():
    segs = seg.parse("a&#91;b&#93;c")
    assert segs[0]["data"]["text"] == "a[b]c", segs


def test_parse_array():
    msg = [{"type": "text", "data": {"text": "一"}},
           {"type": "image", "data": {"file": "x.png"}},
           {"type": "text", "data": {"text": "二"}}]
    segs = seg.parse(msg)
    assert len(segs) == 3
    assert seg.to_text(segs) == "一[图片]二", seg.to_text(segs)


def test_parse_plain():
    assert seg.parse("就一句话")[0]["data"]["text"] == "就一句话"
    assert seg.parse(None) == []
    assert seg.parse({"type": "face", "data": {"id": "178"}})[0]["type"] == "face"


def test_to_text_at():
    segs = [seg.text("喂 "), seg.at(10001), seg.text(" 你来")]
    got = seg.to_text(segs)
    assert "@10001" in got, got
    assert seg.to_text([seg.at("all")]) == "@全体成员"


def test_text_only():
    segs = list(seg.parse("[CQ:image,file=a.jpg]"))
    assert seg.text_only(segs) == "", repr(seg.text_only(segs))
    segs2 = seg.parse("图[CQ:image,file=a.jpg]说完了")
    assert seg.text_only(segs2) == "图说完了"


def test_builders():
    assert seg.image("https://a/b.png")["data"]["file"] == "https://a/b.png"
    tmp = pathlib.Path(tempfile.mkdtemp()) / "t.png"
    tmp.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)
    d = seg.image(str(tmp))["data"]["file"]
    assert d.startswith("file:///"), d
    b = seg.image_file(str(tmp))["data"]["file"]
    assert b.startswith("base64://"), b[:20]
    assert seg.record("http://a/b.mp3")["type"] == "record"
    assert seg.reply(123)["data"]["id"] == "123"
    assert seg.face(178)["data"]["id"] == "178"
    assert seg.share("http://x", "标题")["data"]["title"] == "标题"
    assert seg.music(42)["data"]["type"] == "163"


def test_summary():
    segs = seg.parse("看这个[CQ:image,file=a.jpg]")
    s = seg.summary(segs)
    assert "看这个" in s and "image" in s, s


def test_event_group():
    ev = {
        "post_type": "message", "message_type": "group", "group_id": 111,
        "user_id": 222, "time": 1700000000, "message_id": 9,
        "sender": {"card": "群名片", "nickname": "昵称"},
        "message": [{"type": "text", "data": {"text": "在吗"}}],
    }
    m = onebot.parse_event(ev)
    assert m["scene"] == "qq_group_111", m
    assert m["actor_name"] == "群名片", m
    assert m["is_group"] is True
    assert m["text"] == "在吗"
    assert m["msgid"] == "9"


def test_event_private_cq():
    ev = {
        "post_type": "message", "message_type": "private", "user_id": 333,
        "time": 1700000001, "message_id": 10,
        "sender": {"nickname": "小明"},
        "message": "hi[CQ:face,id=178]",
    }
    m = onebot.parse_event(ev)
    assert m["scene"] == "qq_private_333", m
    assert m["is_group"] is False
    assert "[表情]" in m["text"], m["text"]


def test_event_image_only():
    ev = {
        "post_type": "message", "message_type": "group", "group_id": 1,
        "user_id": 2, "time": 1, "message_id": 3, "sender": {"nickname": "x"},
        "message": [{"type": "image", "data": {"file": "n.jpg", "url": ""}}],
    }
    m = onebot.parse_event(ev)
    assert m is not None, "纯图片消息不该被丢掉"
    assert m["text"] == "[图片]", m["text"]
    assert m["segments"][0]["type"] == "image"


def test_event_self_sent():
    ev = {"post_type": "message_sent", "message_type": "private", "user_id": 5,
          "time": 2, "message_id": 6, "sender": {"nickname": "自己"},
          "message": "我发的"}
    m = onebot.parse_event(ev)
    assert m and m["text"] == "我发的", m


def test_event_notice_ignored():
    assert onebot.parse_event({"post_type": "notice"}) is None
    assert onebot.parse_event({"post_type": "meta_event"}) is None
    assert onebot.parse_event({}) is None


def test_scene_target():
    assert onebot._target("qq_group_123") == ("group", 123)
    assert onebot._target("qq_private_456") == ("private", 456)
    assert onebot._target("wxid_x") is None


def test_no_base_url():
    # 把地址清空再试：默认配置里 QQ 指向 127.0.0.1:5700，那是「连不上」不是「没配」
    from core import config
    old = config.channels().get("qq") or {}
    saved = dict(old)
    try:
        (config._cache.setdefault("channels", config.channels()))["qq"] = {**saved, "base_url": ""}
        r = onebot.send_segments("qq_group_1", [seg.text("x")])
        assert r["ok"] is False and "没配" in r["error"], r
    finally:
        config.channels()["qq"] = saved
        config._cache.clear()


def test_router_downgrade():
    from channels import router
    r = router.send_segments("http_x", [seg.text("只有字")])
    assert isinstance(r, dict)
    r2 = router.send_segments("http_x", [seg.image("http://a/b.png")])
    assert r2["ok"] is False, r2


def main() -> int:
    print("消息段与 OneBot 通道测试")
    for name, fn in [
        ("CQ 码解析", test_parse_cq),
        ("CQ 转义", test_parse_cq_escape),
        ("数组格式", test_parse_array),
        ("纯文本/单段/None", test_parse_plain),
        ("文本化 @", test_to_text_at),
        ("只取文字", test_text_only),
        ("构造器", test_builders),
        ("摘要", test_summary),
        ("群消息事件", test_event_group),
        ("私聊 + CQ 表情", test_event_private_cq),
        ("纯图片消息", test_event_image_only),
        ("自己发的", test_event_self_sent),
        ("通知类事件忽略", test_event_notice_ignored),
        ("场景解析", test_scene_target),
        ("没配地址时失败", test_no_base_url),
        ("不支持富媒体时降级", test_router_downgrade),
    ]:
        check(name, fn)
    print(f"\n{ok} 通过 / {fail} 失败")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
