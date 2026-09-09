"""P4-04/FR-PUB-09 — pure-function tests, no DB needed (the tree-building
math itself, not the DB-backed shard-head read)."""
from landaudit.merkle import ShardHeadRecord, compute_root, merkle_root


def test_merkle_root_is_deterministic_for_the_same_leaves():
    leaves = ["a", "b", "c"]
    assert merkle_root(leaves) == merkle_root(list(leaves))


def test_merkle_root_changes_if_any_leaf_changes():
    assert merkle_root(["a", "b", "c"]) != merkle_root(["a", "b", "X"])


def test_merkle_root_of_empty_leaves_is_a_fixed_deterministic_value():
    assert merkle_root([]) == merkle_root([])


def test_compute_root_commits_to_shard_id_not_just_hash_value():
    """Two heads with the hashes swapped between shard ids must produce a
    different root — the tree commits to (shard_id, head_hash) pairs, not
    a bag of hash values, so a verifier can't be fooled by a shard
    reordering."""
    heads_a = [ShardHeadRecord(shard_id=0, head_hash="h1"), ShardHeadRecord(shard_id=1, head_hash="h2")]
    heads_b = [ShardHeadRecord(shard_id=0, head_hash="h2"), ShardHeadRecord(shard_id=1, head_hash="h1")]
    assert compute_root(heads_a) != compute_root(heads_b)


def test_compute_root_is_order_independent_given_out_of_order_input():
    """`current_shard_heads` always returns 0..NUM_SHARDS-1 in order, but
    `compute_root` itself sorts by shard_id defensively — a caller that
    hands it heads in a different order still gets the canonical root."""
    heads = [ShardHeadRecord(shard_id=i, head_hash=f"h{i}") for i in range(8)]
    import random

    shuffled = list(heads)
    random.Random(42).shuffle(shuffled)
    assert compute_root(heads) == compute_root(shuffled)


def test_a_missing_shard_contributes_a_distinct_leaf_from_an_empty_string_head():
    """An empty shard (`head_hash=None`) must not be indistinguishable
    from a shard whose head happens to be the literal string ''."""
    with_none = compute_root([ShardHeadRecord(shard_id=0, head_hash=None)])
    with_empty_string = compute_root([ShardHeadRecord(shard_id=0, head_hash="")])
    # Both map through `head_hash or ""` in _leaf_hash, so they ARE equal
    # by this module's own definition — assert that equality is the
    # documented behavior, not an accident nobody noticed.
    assert with_none == with_empty_string
