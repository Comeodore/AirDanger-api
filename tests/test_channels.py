from datetime import timedelta

from test_pipeline import T0, make_ctx


NEBO = "kyiv_nebo"
WAR = "war_monitor"


async def test_the_fastest_channel_wins_and_the_rest_are_one_stream():
    ctx = make_ctx()
    await ctx.handle_message(NEBO, "Балістика на Київ", T0)
    await ctx.handle_message(WAR, "‼️ Київ — спуск балістики!", T0)
    await ctx.handle_message(WAR, "‼️ Київ — спуск балістики! Друга",
                             T0 + timedelta(seconds=20))
    await ctx.handle_message(NEBO, "Ще балістика", T0 + timedelta(seconds=118))
    assert ctx.push.sent == ["Балістика на Київ"]


async def test_a_launch_is_still_announced_after_the_burst_ends():
    ctx = make_ctx()
    await ctx.handle_message(WAR, "‼️ Київ — спуск балістики!", T0)
    await ctx.handle_message(NEBO, "Балістика на Київ", T0 + timedelta(seconds=121))
    assert len(ctx.push.sent) == 2


async def test_a_launch_breaks_the_cooldown_a_warning_started():
    ctx = make_ctx(push_warnings=True)
    await ctx.handle_message(WAR, "🟣 Загроза балістики з Брянська. Увага.", T0)
    await ctx.handle_message(NEBO, "Ціль на Київ", T0 + timedelta(seconds=30))
    assert len(ctx.push.sent) == 2
    await ctx.handle_message(WAR, "‼️ Київ — спуск балістики! Друга",
                             T0 + timedelta(seconds=45))
    assert len(ctx.push.sent) == 2


async def test_escalation_can_be_switched_off():
    ctx = make_ctx(push_warnings=True, push_escalation=False)
    await ctx.handle_message(WAR, "🟣 Загроза балістики з Брянська. Увага.", T0)
    await ctx.handle_message(NEBO, "Ціль на Київ", T0 + timedelta(seconds=30))
    assert len(ctx.push.sent) == 1


async def test_war_monitor_launch_on_kyiv_pushes():
    ctx = make_ctx()
    await ctx.handle_message(WAR, "‼️ Київ — спуск балістики!", T0)
    assert ctx.push.sent == ["‼️ Київ — спуск балістики!"]


async def test_war_monitor_launch_on_another_city_is_silent():
    ctx = make_ctx()
    await ctx.handle_message(WAR, "‼️ Одеса/Чорноморське — спуск балістики!", T0)
    await ctx.handle_message(WAR, "‼️Харків — спуск балістики!", T0)
    await ctx.handle_message(WAR, "☄ Дніпро балістика", T0)
    await ctx.handle_message(WAR, "☄ БР далі Кривий Ріг", T0)
    assert ctx.push.sent == []


async def test_war_monitor_suburbs_count_as_kyiv():
    for text in (
        "‼️ Київ Бровари — спуск балістики!",
        "❗️ 2х Циркони у напрямку Бровари/Київ",
        "❗️ 1х КР Циркон на Українка / Васильків",
        "☄ балістика через Полтавщину на Київщину",
    ):
        ctx = make_ctx()
        await ctx.handle_message(WAR, text, T0)
        assert ctx.push.sent == [text], text


async def test_war_monitor_warns_for_every_ballistic_launch_site():
    for text in (
        "🟣 Загроза балістики з Брянська. Увага.",
        "🟣 Загроза балістики з Криму. Увага.",
        "🟣 Загроза балістики з Таганрога/Ростова. Увага.",
        "🟣 Загроза балістики зі Сходу.",
    ):
        ctx = make_ctx(push_warnings=True)
        await ctx.handle_message(WAR, text, T0)
        assert len(ctx.push.sent) == 1, text


async def test_war_monitor_warning_push_is_cut_to_the_first_sentence():
    ctx = make_ctx(push_warnings=True)
    await ctx.handle_message(
        WAR,
        "🟣 Загроза балістики з Криму. Увага. Імовірний пуск ракет системи "
        "«Іскандер», або робота ворожої ППО С-300/С-400.",
        T0,
    )
    assert ctx.push.sent == ["🟣 Загроза балістики з Криму"]


async def test_war_monitor_drones_and_aftermath_never_push():
    ctx = make_ctx()
    for text in (
        "⚠️ 10х БпЛА з Чернігівщини на Броварський район Київщини у напрямку Києва.",
        "🅿️ 1х реактивний БпЛА повз Бровари.",
        "💥 Київ та передмістя вибухи. Загроза балістики з Брянська триває.",
        "📡 Обстановка станом на 00:00 03.07.26",
        "🟨 Ймовірність комбінованої атаки на низькому рівні.",
        "✈️ Активність тактичної авіації в акваторії Чорного моря.",
        "💣 Пуски КАБів у напрямку Херсон.",
    ):
        await ctx.handle_message(WAR, text, T0)
    assert ctx.push.sent == []


async def test_war_monitor_all_clear_is_silent_but_reopens_the_alarm():
    ctx = make_ctx()
    await ctx.handle_message(WAR, "☄ Вихід балістики Брянськ", T0)
    ctx.push.sent.clear()
    await ctx.handle_message(WAR, "⚪️ Відбій загрози балістики.",
                             T0 + timedelta(minutes=1))
    assert ctx.push.sent == []
    await ctx.handle_message(WAR, "‼️ Київ — спуск балістики!",
                             T0 + timedelta(minutes=2))
    assert ctx.push.sent == ["‼️ Київ — спуск балістики!"]


async def test_all_clear_closes_the_context_for_telegraphic_follow_ups():
    ctx = make_ctx()
    await ctx.handle_message(NEBO, "Загроза балістики з Брянська", T0)
    await ctx.handle_message(NEBO, "Вже все", T0 + timedelta(minutes=1))
    await ctx.handle_message(NEBO, "Ще цілі", T0 + timedelta(minutes=5))
    assert ctx.push.sent == []


async def test_far_kyiv_oblast_alone_does_not_wake_the_city():
    ctx = make_ctx()
    await ctx.handle_message(WAR, "🅿️1х реактив у Білоцерківському районі.", T0)
    await ctx.handle_message(WAR, "❗️ 1х реактив Переяслав", T0)
    assert ctx.push.sent == []


async def test_watching_a_real_launch_still_counts_as_inbound():
    ctx = make_ctx(push_warnings=False)
    await ctx.handle_message(NEBO, "Знову є виходи з Брянська, слідкуємо", T0)
    assert ctx.push.sent == ["Знову є виходи з Брянська, слідкуємо"]


async def test_a_kyiv_channel_still_hears_launches_from_the_south():
    ctx = make_ctx(push_warnings=True)
    await ctx.handle_message(NEBO, "Загроза Цирконів з Криму", T0)
    assert len(ctx.push.sent) == 1


async def test_drone_traffic_elsewhere_does_not_mute_another_channel():
    ctx = make_ctx()
    await ctx.handle_message(NEBO, "Загроза балістики з Брянська", T0)
    await ctx.handle_message(
        WAR, "🅿️ 4х реактивних БпЛА у напрямку Одеса / порт.",
        T0 + timedelta(minutes=1),
    )
    await ctx.handle_message(NEBO, "Ще цілі", T0 + timedelta(minutes=3))
    assert ctx.push.sent == ["Ще цілі"]


async def test_war_monitor_launch_opens_nebo_bare_window():
    ctx = make_ctx()
    await ctx.handle_message(WAR, "☄ Вихід у напрямку Києва", T0)
    ctx.push.sent.clear()
    await ctx.handle_message(NEBO, "Ще ціль", T0 + timedelta(minutes=3))
    assert ctx.push.sent == ["Ще ціль"]


async def test_drone_context_alone_does_not_open_bare():
    ctx = make_ctx()
    await ctx.handle_message(
        WAR,
        "⚠️ 10х БпЛА з Чернігівщини на Броварський район Київщини у напрямку Києва.",
        T0,
    )
    await ctx.handle_message(NEBO, "Ще ціль", T0 + timedelta(minutes=1))
    assert ctx.push.sent == []


async def test_all_clear_on_war_monitor_closes_the_bare_window():
    ctx = make_ctx()
    await ctx.handle_message(WAR, "☄ Вихід у напрямку Києва", T0)
    ctx.push.sent.clear()
    await ctx.handle_message(WAR, "⚪️ Відбій загрози балістики.",
                             T0 + timedelta(minutes=1))
    await ctx.handle_message(NEBO, "Ще ціль", T0 + timedelta(minutes=3))
    assert ctx.push.sent == []


async def test_all_clear_on_one_channel_closes_the_other_channels_window():
    ctx = make_ctx()
    await ctx.handle_message(NEBO, "Загроза балістики з Брянська", T0)
    await ctx.handle_message(WAR, "⚪️ Відбій загрози балістики.",
                             T0 + timedelta(minutes=1))
    await ctx.handle_message(NEBO, "Ще цілі", T0 + timedelta(minutes=3))
    assert ctx.push.sent == []


async def test_all_clear_about_another_city_keeps_the_kyiv_window():
    ctx = make_ctx()
    await ctx.handle_message(NEBO, "Загроза балістики з Брянська", T0)
    await ctx.handle_message(WAR, "⚪️ Відбій загрози БпЛА по Харкову.",
                             T0 + timedelta(minutes=1))
    await ctx.handle_message(NEBO, "Ще цілі", T0 + timedelta(minutes=3))
    assert ctx.push.sent == ["Ще цілі"]


async def test_no_launches_report_is_not_an_alert():
    ctx = make_ctx()
    await ctx.handle_message(WAR, "Без виходів балістики на Київ.", T0)
    await ctx.handle_message(NEBO, "Загроза балістики з Брянська", T0)
    await ctx.handle_message(NEBO, "Наразі без пусків", T0 + timedelta(minutes=1))
    await ctx.handle_message(WAR, "Без виходів балістики на Київ.",
                             T0 + timedelta(minutes=2))
    assert ctx.push.sent == []


async def test_target_toward_kyiv_pushes_without_any_context():
    ctx = make_ctx()
    await ctx.handle_message(NEBO, "Ціль в бік Києва", T0)
    assert ctx.push.sent == ["Ціль в бік Києва"]


async def test_misspelled_zircon_launch_on_war_monitor_pushes():
    ctx = make_ctx()
    await ctx.handle_message(WAR, "❗️ 2х Цирокни на Київ.", T0)
    assert ctx.push.sent == ["❗️ 2х Цирокни на Київ."]


async def test_bryansk_continuations_stay_inbound_inside_context():
    ctx = make_ctx()
    await ctx.handle_message(NEBO, "Балістика на Київ", T0)
    ctx.push.sent.clear()
    await ctx.handle_message(NEBO, "З Брянська летять також",
                             T0 + timedelta(seconds=125))
    await ctx.handle_message(NEBO, "Ще з Брянська", T0 + timedelta(seconds=250))
    await ctx.handle_message(NEBO, "Ще ціль з Брянська", T0 + timedelta(seconds=380))
    assert ctx.push.sent == [
        "З Брянська летять також", "Ще з Брянська", "Ще ціль з Брянська",
    ]


async def test_hypothetical_launches_from_a_site_stay_silent():
    ctx = make_ctx()
    await ctx.handle_message(NEBO, "Балістика на Київ", T0)
    ctx.push.sent.clear()
    await ctx.handle_message(NEBO, "Ще можуть бути пуски, поки не висовуйтеся",
                             T0 + timedelta(seconds=125))
    await ctx.handle_message(NEBO, "Може бути пуск", T0 + timedelta(seconds=250))
    assert ctx.push.sent == []


async def test_zircons_toward_kyiv_from_a_launch_site_are_inbound():
    for text in (
        "❗️ 2х КР Циркон з Міллерово у напрямку Київщини.",
        "❗️ 2х Циркони у напрямку Київщини з Курьска",
        "❗️ Попередньо КР Циркон з Курщини у напрямку Києва.",
        "❗️ Вихід Циркону з Курьска",
        "❗️ вихід ймовірно КР Циркон з Курщини",
    ):
        ctx = make_ctx()
        await ctx.handle_message(WAR, text, T0)
        assert ctx.push.sent == [text], text


async def test_s400_working_kyiv_oblast_is_inbound_not_a_warning():
    ctx = make_ctx()
    await ctx.handle_message(WAR, "По Київщині відпрацювання С-400 з Брянщини.", T0)
    assert ctx.push.sent == ["По Київщині відпрацювання С-400 з Брянщини."]


async def test_a_bare_second_launch_from_monitor_stays_silent():
    ctx = make_ctx()
    await ctx.handle_message(WAR, "☄ Вихід у напрямку Києва", T0)
    ctx.push.sent.clear()
    await ctx.handle_message(WAR, "☄ Другий вихід", T0 + timedelta(seconds=121))
    assert ctx.push.sent == []


async def test_a_bare_launch_without_kyiv_context_is_silent():
    ctx = make_ctx()
    await ctx.handle_message(WAR, "☄ Дніпро балістика", T0)
    await ctx.handle_message(WAR, "☄ Другий вихід", T0 + timedelta(seconds=30))
    assert ctx.push.sent == []


async def test_a_marker_variant_still_alerts_via_the_text_pipeline():
    ctx = make_ctx()
    await ctx.handle_message(WAR, "‼ Київ — спуск балістики!", T0)
    assert len(ctx.push.sent) == 1


async def test_explosion_reports_stay_silent_even_with_warnings_on():
    ctx = make_ctx(push_warnings=True)
    await ctx.handle_message(
        WAR, "💥 Київ та передмістя вибухи. Загроза балістики з Брянська триває.", T0,
    )
    await ctx.handle_message(
        WAR, "💥 Вибухи Дніпро, загроза балістики з Таганрогу триває",
        T0 + timedelta(minutes=5),
    )
    assert ctx.push.sent == []
