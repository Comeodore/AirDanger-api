from app.dedup import TTLSet, digest, normalize


def test_normalize_strips_emoji_links_whitespace():
    a = "🔴🚀 Балістика на Київ!  \n\n https://t.me/some/123"
    b = "Балістика на КИЇВ https://example.com/other?utm=1"
    assert normalize(a) == normalize(b) == "балістика на київ"

def test_digest_equal_for_reworded_formatting():
    assert digest("⚠️ Шахеди — курс на Оболонь!!!") == digest("шахеди курс на оболонь")

def test_digest_differs_for_different_text():
    assert digest("Балістика на Київ") != digest("Шахеди на Київ")

def test_ttlset_dedups_within_ttl_and_forgets_after():
    s = TTLSet(ttl_seconds=900)
    assert s.add("k", now=0.0) is True
    assert s.add("k", now=100.0) is False
    assert s.add("k", now=901.0) is True

def test_ttlset_independent_keys():
    s = TTLSet(ttl_seconds=900)
    assert s.add("a", now=0.0) is True
    assert s.add("b", now=0.0) is True
