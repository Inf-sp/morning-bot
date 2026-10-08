import recommendation_rotation as rotation


def test_history_is_unique_and_keeps_original_spelling():
    assert rotation.recent(["A", "B", "a", "C"], limit=3) == ["A", "B", "C"]
    assert rotation.remember(["A", "B"], "a", limit=3) == ["B", "a"]


def test_cycle_uses_every_fresh_candidate_before_repeating():
    pool = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    key = lambda item: item.get("id", "") if isinstance(item, dict) else str(item or "")

    assert rotation.candidates_for_cycle(pool, ["a"], key=key) == pool[1:]
    assert rotation.candidates_for_cycle(
        pool, ["a", "b", "c"], current="c", key=key,
    ) == pool[:2]


