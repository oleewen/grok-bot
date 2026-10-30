from baohe.models import Message, Session


def test_session_to_dict():
    s = Session(id="2100", name="日志采集治理", chat_type=1, group=True)
    assert s.to_dict() == {
        "id": "2100",
        "name": "日志采集治理",
        "chatType": 1,
        "group": True,
    }


def test_message_to_dict_fields():
    m = Message(
        msg_id="1",
        sender="高明",
        sender_id="u1",
        body="hello",
        send_time=1720000000000,
        chat_type=1,
        session_id="2100",
        session_name="日志采集治理",
        source="indexeddb",
    )
    d = m.to_dict()
    assert d["msgId"] == "1"
    assert d["sender"] == "高明"
    assert d["body"] == "hello"
    assert d["source"] == "indexeddb"
