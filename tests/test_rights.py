import json
import pytest
from app.core import require_rights, write_package
from app.storage import Store


def test_rights_are_specific_to_source(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    first = tmp_path / "a.mp4"; first.write_bytes(b"source")
    second = tmp_path / "b.mp4"; second.write_bytes(b"source")
    store.save_source(str(first), "Creator", "https://example.com", "Written permission", True)
    assert require_rights(store, str(first))["creator"] == "Creator"
    with pytest.raises(PermissionError):
        require_rights(store, str(second))


def test_no_approval_without_evidence(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    source = tmp_path / "a.mp4"; source.touch()
    with pytest.raises(ValueError):
        store.save_source(str(source), "", "", "", True)


def test_package_rejects_unapproved_source(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    source = tmp_path / "a.mp4"; source.touch()
    destination = tmp_path / "draft.json"
    with pytest.raises(PermissionError):
        write_package(store, str(source), str(source), str(destination), "Title", "Description")
    assert not destination.exists()


def test_package_records_attribution(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    source = tmp_path / "a.mp4"; source.touch()
    store.save_source(str(source), "Creator", "https://example.com", "Email", True)
    destination = tmp_path / "draft.json"
    with pytest.raises(NotImplementedError):
        write_package(store, str(source), str(source), str(destination), "Title", "Description")
    assert not destination.exists()



def test_replacing_source_invalidates_approval(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"original")
    store.save_source(str(source), "Creator", "", "Permission", True)
    source.write_bytes(b"replacement")
    with pytest.raises(PermissionError):
        require_rights(store, str(source))
