"""FR-ING-07 (P2-06) — the config-validation check that the primary and
secondary object stores are actually independent, not the same location
checked twice."""
import pytest

from landstorage.distinct import DistinctCredentialsError, assert_distinct_stores


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith("OBJECT_STORE_") or key.startswith("SECONDARY_OBJECT_STORE_"):
            monkeypatch.delenv(key, raising=False)


def test_two_local_fs_stores_at_different_roots_are_distinct(monkeypatch):
    monkeypatch.setenv("OBJECT_STORE_ROOT", "/tmp/primary")
    monkeypatch.setenv("SECONDARY_OBJECT_STORE_ROOT", "/tmp/secondary")
    assert_distinct_stores()  # does not raise


def test_two_local_fs_stores_at_the_same_root_are_rejected(monkeypatch):
    monkeypatch.setenv("OBJECT_STORE_ROOT", "/tmp/same-root")
    monkeypatch.setenv("SECONDARY_OBJECT_STORE_ROOT", "/tmp/same-root")
    with pytest.raises(DistinctCredentialsError):
        assert_distinct_stores()


def test_defaulting_both_to_the_same_implicit_root_is_also_rejected(monkeypatch):
    # Neither OBJECT_STORE_ROOT nor SECONDARY_OBJECT_STORE_ROOT set — both
    # fall back to the same default root, which is exactly the
    # misconfiguration this check exists to catch.
    with pytest.raises(DistinctCredentialsError):
        assert_distinct_stores()


def test_two_s3_stores_at_the_same_bucket_and_key_are_rejected(monkeypatch):
    for prefix in ("OBJECT_STORE", "SECONDARY_OBJECT_STORE"):
        monkeypatch.setenv(f"{prefix}_DRIVER", "s3")
        monkeypatch.setenv(f"{prefix}_BUCKET", "landrecords")
        monkeypatch.setenv(f"{prefix}_URL", "http://minio:9000")
        monkeypatch.setenv(f"{prefix}_ACCESS_KEY_ID", "shared-key")
    with pytest.raises(DistinctCredentialsError):
        assert_distinct_stores()


def test_two_s3_stores_with_different_access_keys_are_distinct(monkeypatch):
    for prefix in ("OBJECT_STORE", "SECONDARY_OBJECT_STORE"):
        monkeypatch.setenv(f"{prefix}_DRIVER", "s3")
        monkeypatch.setenv(f"{prefix}_BUCKET", "landrecords")
        monkeypatch.setenv(f"{prefix}_URL", "http://minio:9000")
    monkeypatch.setenv("OBJECT_STORE_ACCESS_KEY_ID", "primary-key")
    monkeypatch.setenv("SECONDARY_OBJECT_STORE_ACCESS_KEY_ID", "secondary-key")
    assert_distinct_stores()  # does not raise
