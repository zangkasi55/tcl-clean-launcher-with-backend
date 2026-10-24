from datetime import datetime, timedelta, timezone

from tvcontrol.auth import Auth
from tvcontrol.models import Rule
from tvcontrol.store import Store


def test_rule_and_daily_counter_survive_an_actual_database_reopen(tmp_path):
    path = tmp_path / "persistent.sqlite3"
    start = datetime(2026, 10, 20, 2, tzinfo=timezone.utc)
    first = Store(path)
    first.put("rule", Rule(enabled=True, minutes=37, package="com.netflix.ninja").model_dump())
    first.add_interval("com.netflix.ninja", "Asia/Bangkok", start, start + timedelta(seconds=123))
    first.close()
    second = Store(path)
    try:
        assert second.rule().minutes == 37
        assert second.rule().package == "com.netflix.ninja"
        assert second.rule().enabled
        assert second.usage("com.netflix.ninja", "Asia/Bangkok", start) == 123
    finally:
        second.close()


def test_changing_parent_password_invalidates_old_sessions(tmp_path):
    store = Store(tmp_path / "auth.sqlite3")
    try:
        original = Auth(tmp_path, store, "original-parent-password")
        token, _ = original.login("original-parent-password", "127.0.0.1")
        assert original.session(token) is not None
        unchanged = Auth(tmp_path, store, "original-parent-password")
        assert unchanged.session(token) is not None
        changed = Auth(tmp_path, store, "a-new-parent-password")
        assert changed.session(token) is None
        assert changed.login("original-parent-password", "127.0.0.1") is None
        assert changed.login("a-new-parent-password", "127.0.0.1") is not None
    finally:
        store.close()
