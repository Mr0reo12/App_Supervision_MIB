from backend.app import build_status


def test_build_status_all_ok():
    raw = [{"status": "ok", "description": "ping"}]
    res = build_status(raw)
    assert res["global_status"] == "OK"
    assert res["monitored_services"]["ping"] == "OK"
