"""v3多产物中断恢复、混合版本拒绝和并发用户写入保护。"""
import json

import pytest

from auto_battery_research.util import stage4_publication as publication


def test_interrupted_publication_recovered_and_mixed_rejected(tmp_path, monkeypatch):
    (tmp_path / "scheme_ledger.json").write_text("old-ledger", encoding="utf-8")
    (tmp_path / "design_scheme.json").write_text("old-design", encoding="utf-8")
    original = publication.atomic_write
    def fail(path, text):
        if path.name == "design_scheme.json":
            raise OSError("注入跨文件中断")
        original(path, text)
    monkeypatch.setattr(publication, "atomic_write", fail)
    with pytest.raises(OSError):
        publication.publish_files(tmp_path, {"scheme_ledger.json": "new-ledger", "design_scheme.json": "new-design"})
    assert publication.publication_errors(tmp_path)
    assert (tmp_path / "scheme_ledger.json").read_text() == "new-ledger"
    monkeypatch.setattr(publication, "atomic_write", original)
    publication.recover_publication(tmp_path)
    assert not publication.publication_errors(tmp_path)
    assert (tmp_path / "design_scheme.json").read_text() == "new-design"


def test_recovery_refuses_unrelated_user_write(tmp_path, monkeypatch):
    original = publication.atomic_write
    def fail(path, text):
        if path.name == "second.json":
            raise OSError("注入中断")
        original(path, text)
    monkeypatch.setattr(publication, "atomic_write", fail)
    with pytest.raises(OSError):
        publication.publish_files(tmp_path, {"first.json": "ours", "second.json": "ours"})
    monkeypatch.setattr(publication, "atomic_write", original)
    (tmp_path / "first.json").write_text("user-update", encoding="utf-8")
    with pytest.raises(ValueError, match="事务外部写入"):
        publication.recover_publication(tmp_path)
    assert (tmp_path / "first.json").read_text() == "user-update"


def test_rollback_only_recorded_versions(tmp_path, monkeypatch):
    (tmp_path / "first.json").write_text("old", encoding="utf-8")
    original = publication.atomic_write
    def fail(path, text):
        if path.name == "second.json":
            raise OSError("中断")
        original(path, text)
    monkeypatch.setattr(publication, "atomic_write", fail)
    with pytest.raises(OSError):
        publication.publish_files(tmp_path, {"first.json": "new", "second.json": "new"})
    monkeypatch.setattr(publication, "atomic_write", original)
    publication.recover_publication(tmp_path, rollback=True)
    assert (tmp_path / "first.json").read_text() == "old"
    assert not (tmp_path / "second.json").exists()
    assert publication.publication_errors(tmp_path)
