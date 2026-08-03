from app.danger_service import DangerService


def service():
    return DangerService()

def test_ballistic_wording_detected():
    s = service()
    for text in (
        "🔴 Балістика на Київ!",
        "Швидкісна ціль з півдня курсом на Київ!",
        "БАЛІСТИКА КИЇВ",
        "Балістика + Циркони",
        "Ще балістика",
        "Циркони",
        "циркон",
        "Загроза балістики з Курська",
    ):
        ev = s.evaluate(text)
        assert ev.detection is not None, text
        assert ev.detection.type == "ballistic", text

def test_irbm_detected():
    ev = service().evaluate("Увага! Загроза застосування балістики середньої дальності!")
    assert ev.detection is not None
    assert ev.detection.type == "irbm"

def test_non_ballistic_wording_is_ignored():
    s = service()
    for text in (
        "🛵 Шахеди, курсом на Оболонь!",
        "Крилаті ракети на Київ",
        "Оболонь!",
        "Буде гучно, в укриття!",
    ):
        ev = s.evaluate(text)
        assert ev.detection is None, text

def test_target_on_kyiv_is_inbound_ballistic():
    s = service()
    for text in ("Ціль на Київ", "Цілі на Київ з Курська", "4 цілі на Київ", "Ціль на нас"):
        ev = s.evaluate(text)
        assert ev.detection is not None, text
        assert ev.detection.type == "ballistic", text
        assert ev.detection.severity == "inbound", text

def test_targets_elsewhere_or_undirected_are_ignored():
    s = service()
    for text in ("Ціль на Сумщині", "Ще ціль", "4 цілі", "Була ціль по Сумщині"):
        assert s.evaluate(text).detection is None, text

def test_named_drone_aimed_at_kyiv_is_not_a_ballistic_target():
    s = service()
    for text in ("Реактивний Шахед, ціль на Київ", "Ціль на Київ — БпЛА"):
        assert s.evaluate(text).detection is None, text

def test_safety_is_flagged_not_detected():
    ev = service().evaluate("Відбій. Цілі зникли.")
    assert ev.safety is True
    assert ev.detection is None

def test_aftermath_and_negations_are_vetoed():
    s = service()
    for text in (
        "Влучання балістики по житловому будинку",
        "На Київ нічого наразі.",
        "Під час нічної атаки по Києву було збито 10 БпЛА.",
    ):
        ev = s.evaluate(text)
        assert ev.detection is None, text

def test_fundraising_posts_are_ignored():
    ev = service().evaluate("Банка на перехоплювачі балістики знову відкрита. Збір!")
    assert ev.detection is None

def test_long_news_posts_are_ignored():
    text = "Огляд: циркони, іскандери та інші ракети — довгий текст про матчастину. " * 3
    ev = service().evaluate(text)
    assert ev.detection is None

def test_launch_threat_wording_is_warning():
    s = service()
    for text in ("Загроза балістики з Курська", "Балістика Курськ!!"):
        ev = s.evaluate(text)
        assert ev.detection is not None, text
        assert ev.detection.severity == "warning", text

def test_inbound_wording_is_inbound():
    s = service()
    for text in (
        "Ціль на Київ, балістика",
        "Циркони на Київ!",
        "Ще балістика",
        "Цілі на Київ з Курська, балістика",
        "Пуск балістики з Курська",
        "2 балістики з Брянська",
    ):
        ev = s.evaluate(text)
        assert ev.detection is not None, text
        assert ev.detection.severity == "inbound", text
