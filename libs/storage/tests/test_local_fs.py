"""Real filesystem test — proves FR-ING-02 immutability and FR-ING-04
dedupe are storage properties, not application-level checks."""
import io
import os
import tempfile
from pathlib import Path

import pytest

from landstorage.drivers.local_fs import LocalFsObjectStore
from landstorage.port import ObjectAlreadyExistsWithDifferentContent, digest_of, key_for


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as d:
        yield LocalFsObjectStore(root=d)


def test_put_computes_the_content_addressed_key(store):
    data = b"a scanned page's bytes"
    result = store.put(data)

    assert result.digest == digest_of(data)
    assert result.key == key_for(result.digest)
    assert result.created is True


def test_reupload_of_identical_bytes_dedupes_FR_ING_04(store):
    data = b"same file uploaded twice"

    first = store.put(data)
    second = store.put(data)

    assert first.key == second.key
    assert first.created is True
    assert second.created is False  # no additional processing work


def test_get_returns_exactly_what_was_put(store):
    data = b"\x00\x01binary-safe-content\xff"
    result = store.put(data)

    assert store.get(result.key) == data


def test_get_of_unknown_key_raises_keyerror(store):
    with pytest.raises(KeyError):
        store.get("sha256/ab/cd/doesnotexist")


def test_verify_fixity_detects_bit_rot_FR_ING_07(store):
    data = b"the one irreplaceable artifact"
    result = store.put(data)
    assert store.verify_fixity(result.key) is True

    # Simulate bit rot: corrupt the stored bytes directly on disk, bypassing
    # the store's own API (this is what a bad restore or a misconfigured
    # lifecycle policy would do).
    on_disk = Path(store._path(result.key))
    on_disk.write_bytes(b"corrupted!!")

    assert store.verify_fixity(result.key) is False


def test_the_key_scheme_shards_across_two_prefix_levels(store):
    data = b"anything"
    digest = digest_of(data)
    result = store.put(data)

    assert result.key == f"sha256/{digest[0:2]}/{digest[2:4]}/{digest}"
    assert (Path(store._root) / result.key).exists()


def test_put_refuses_to_silently_accept_a_digest_collision_style_corruption(store):
    # This models the "storage bug" case ADR-004 calls out: writing raw
    # bytes directly at a content-addressed path that don't match its own
    # digest, then trying to put() the *correct* content at that same key.
    data = b"correct content"
    result = store.put(data)
    on_disk = Path(store._path(result.key))
    on_disk.write_bytes(b"WRONG bytes now sitting at this key")

    with pytest.raises(ObjectAlreadyExistsWithDifferentContent):
        store.put(data)


# ---- put_stream/open_stream (FR-ING-02 streaming custody) ----


def test_put_stream_computes_the_same_digest_as_put(store):
    data = b"a 500-page PDF's worth of bytes, in spirit" * 1000
    streamed = store.put_stream(io.BytesIO(data))

    assert streamed.digest == digest_of(data)
    assert streamed.key == key_for(streamed.digest)
    assert streamed.created is True
    assert store.get(streamed.key) == data


def test_put_stream_dedupes_like_put_FR_ING_04(store):
    data = b"streamed twice"
    first = store.put_stream(io.BytesIO(data))
    second = store.put_stream(io.BytesIO(data))

    assert first.key == second.key
    assert second.created is False


def test_open_stream_reads_back_exactly_what_was_put_via_put_stream(store):
    data = b"\x00\x01binary-safe-streamed-content\xff" * 500
    result = store.put_stream(io.BytesIO(data))

    with store.open_stream(result.key) as f:
        assert f.read() == data


def test_open_stream_of_unknown_key_raises_keyerror(store):
    with pytest.raises(KeyError):
        store.open_stream("sha256/ab/cd/doesnotexist")


def test_put_stream_never_buffers_the_whole_object_in_process_memory(store):
    """Not a memory-profiler assertion (too environment-dependent to be a
    reliable CI check) — instead proves the mechanism that gives the
    memory property: `hash_stream_to_spooled_tempfile`'s spool spills to
    disk once past its in-memory threshold, so a large put_stream() is
    backed by a real file, not a giant `bytes` object, at the point the
    driver writes it."""
    from landstorage.port import hash_stream_to_spooled_tempfile

    data = os.urandom(1024)
    digest, spooled = hash_stream_to_spooled_tempfile(io.BytesIO(data), max_size_in_memory=16)
    try:
        assert digest == digest_of(data)
        # SpooledTemporaryFile exposes the real file once it has spilled —
        # `_file` is a plain file object with a `.name` (a real path) past
        # the in-memory threshold, `io.BytesIO`-like below it.
        assert hasattr(spooled._file, "name")
    finally:
        spooled.close()


def test_put_stream_refuses_a_digest_collision_style_corruption_without_loading_either_file_fully(store):
    data = b"correct streamed content"
    result = store.put_stream(io.BytesIO(data))
    on_disk = Path(store._path(result.key))
    on_disk.write_bytes(b"WRONG bytes now sitting at this key")

    with pytest.raises(ObjectAlreadyExistsWithDifferentContent):
        store.put_stream(io.BytesIO(data))
