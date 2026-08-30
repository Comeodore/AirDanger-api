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
    await ctx.handle_message(NEBO, "Підлітають", T0 + timedelta(minutes=5))
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
    await ctx.handle_message(NEBO, "Підлітають", T0 + timedelta(minutes=3))
    assert ctx.push.sent == ["Підлітають"]


async def test_war_monitor_launch_opens_nebo_bare_window():
    ctx = make_ctx()
    await ctx.handle_message(WAR, "☄ Вихід у напрямку Києва", T0)
    ctx.push.sent.clear()
    await ctx.handle_message(NEBO, "Підлітають", T0 + timedelta(minutes=3))
    assert ctx.push.sent == ["Підлітають"]


async def test_drone_context_alone_does_not_open_bare():
    ctx = make_ctx()
    await ctx.handle_message(
        WAR,
        "⚠️ 10х БпЛА з Чернігівщини на Броварський район Київщини у напрямку Києва.",
        T0,
    )
    await ctx.handle_message(NEBO, "Підлітають", T0 + timedelta(minutes=1))
    assert ctx.push.sent == []


async def test_all_clear_on_war_monitor_closes_the_bare_window():
    ctx = make_ctx()
    await ctx.handle_message(WAR, "☄ Вихід у напрямку Києва", T0)
    ctx.push.sent.clear()
    await ctx.handle_message(WAR, "⚪️ Відбій загрози балістики.",
                             T0 + timedelta(minutes=1))
    await ctx.handle_message(NEBO, "Підлітають", T0 + timedelta(minutes=3))
    assert ctx.push.sent == []


async def test_all_clear_on_one_channel_closes_the_other_channels_window():
    ctx = make_ctx()
    await ctx.handle_message(NEBO, "Загроза балістики з Брянська", T0)
    await ctx.handle_message(WAR, "⚪️ Відбій загрози балістики.",
                             T0 + timedelta(minutes=1))
    await ctx.handle_message(NEBO, "Підлітають", T0 + timedelta(minutes=3))
    assert ctx.push.sent == []


async def test_all_clear_about_another_city_keeps_the_kyiv_window():
    ctx = make_ctx()
    await ctx.handle_message(NEBO, "Загроза балістики з Брянська", T0)
    await ctx.handle_message(WAR, "⚪️ Відбій загрози БпЛА по Харкову.",
                             T0 + timedelta(minutes=1))
    await ctx.handle_message(NEBO, "Підлітають", T0 + timedelta(minutes=3))
    assert ctx.push.sent == ["Підлітають"]


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


async def test_a_bare_target_on_nebo_pushes_without_context():
    ctx = make_ctx()
    await ctx.handle_message(NEBO, "Ціль", T0)
    assert ctx.push.sent == ["Ціль"]
    assert ctx.db.pushes == [(NEBO, "ballistic", "inbound", "Ціль", True)]


async def test_a_bare_target_opens_the_window_for_later_follow_ups():
    ctx = make_ctx()
    await ctx.handle_message(NEBO, "Ціль", T0)
    ctx.push.sent.clear()
    await ctx.handle_message(NEBO, "Підлітають", T0 + timedelta(seconds=125))
    assert ctx.push.sent == ["Підлітають"]


async def test_a_bare_target_after_the_all_clear_still_pushes():
    ctx = make_ctx()
    await ctx.handle_message(NEBO, "Загроза балістики з Брянська", T0)
    await ctx.handle_message(NEBO, "Відбій", T0 + timedelta(minutes=1))
    await ctx.handle_message(NEBO, "Ще цілі", T0 + timedelta(minutes=5))
    assert ctx.push.sent == ["Ще цілі"]


MONIT = "kyiv_monit0ring"


async def test_a_launch_call_on_monit0ring_pushes():
    ctx = make_ctx()
    await ctx.handle_message(MONIT, "Вихід балістики з Брянска.", T0)
    assert ctx.push.sent == ["Вихід балістики з Брянска."]
    assert ctx.db.pushes == [
        (MONIT, "ballistic", "inbound", "Вихід балістики з Брянска.", True),
    ]


async def test_monit0ring_takes_the_slot_when_it_is_first():
    ctx = make_ctx()
    await ctx.handle_message(MONIT, "Балістика на Київ/передмістя.", T0)
    await ctx.handle_message(NEBO, "Цілі на Київ з Брянська",
                             T0 + timedelta(seconds=58))
    assert ctx.push.sent == ["Балістика на Київ/передмістя."]


async def test_monit0ring_warnings_and_launches_share_one_stream():
    ctx = make_ctx(push_warnings=True)
    await ctx.handle_message(MONIT, "Загроза балістики з Курська.", T0)
    await ctx.handle_message(NEBO, "Ціль з Курська", T0 + timedelta(seconds=17))
    assert ctx.push.sent == ["Загроза балістики з Курська."]


NOT_A_LAUNCH_ON_MONIT0RING = (
    "Не фіксую ракет до нас.",
    "Активність літаків, флоту, ОТРК - не фіксую.",
    "Без балістичних ракет наразі.",
    "Загроза балістики з Воронежа.\n\nUPD: Не по нам.",
    "Розвідспільнота США дала попередженння тільки що.\n\n"
    "25 балістичних ракет/цирконів протягом 48 годин.",
    "❗️Надійшла інформація про ймовірне застосування КР Іскандер-К під ранок.",
    "По плану противника який перехопила наша розвідка:\n\n12 Іскандерів.",
    "❗️Ураження установки С-400 в Брянській області, "
    "яка регулярно наносить удари по столиці.",
    "❗️❗️Увага\n\nВорог збільшив кількість установок Іскандер-М "
    "в Брянській області.",
    "❗️Військові будуть знищувати пускові установки балістики, — Зеленський",
    "1 серпня...\nКиїв же приготувався до осінньо-зимової кампанії "
    "100+ балістичних ракет щомісяця по ТЕЦкам ??",
    "✍🏼 -2 Іскандера з Брянска, а також -2/3 Циркона з Курська.",
)


async def test_monit0ring_news_and_negations_stay_silent():
    for text in NOT_A_LAUNCH_ON_MONIT0RING:
        ctx = make_ctx(push_warnings=True)
        await ctx.handle_message(MONIT, text, T0)
        assert ctx.push.sent == [], text
        assert ctx.db.pushes == [], text


async def test_the_monit0ring_veto_does_not_reach_other_channels():
    ctx = make_ctx(push_warnings=True)
    await ctx.handle_message(WAR, "☄ Вихід балістики Брянськ", T0)
    assert ctx.push.sent == ["☄ Вихід балістики Брянськ"]


async def test_a_warning_opens_the_bare_target_window():
    ctx = make_ctx(push_warnings=True)
    await ctx.handle_message(NEBO, "Загроза балістики з Курська", T0)
    ctx.push.sent.clear()
    await ctx.handle_message(NEBO, "Підлітає", T0 + timedelta(minutes=4))
    assert ctx.push.sent == ["Підлітає"]


async def test_a_new_one_is_a_drone_continuation_not_a_ballistic_target():
    ctx = make_ctx(push_warnings=True)
    await ctx.handle_message(NEBO, "Загроза балістики з Курська", T0)
    ctx.push.sent.clear()
    await ctx.handle_message(NEBO, "Новий підлітає до Троєщини",
                             T0 + timedelta(minutes=2))
    assert ctx.push.sent == []
    assert [row[3] for row in ctx.db.pushes] == ["Загроза балістики з Курська"]


async def test_a_new_one_does_not_ride_a_confirmed_launch_either():
    ctx = make_ctx()
    await ctx.handle_message(NEBO, "Балістика на Київ", T0)
    ctx.push.sent.clear()
    await ctx.handle_message(NEBO, "Новий підлітає до Троєщини",
                             T0 + timedelta(minutes=4))
    assert ctx.push.sent == []


async def test_a_new_one_marks_the_drone_sky():
    ctx = make_ctx(push_warnings=True)
    await ctx.handle_message(NEBO, "Загроза балістики з Курська", T0)
    await ctx.handle_message(NEBO, "Новий підлітає до Броварів",
                             T0 + timedelta(minutes=1))
    ctx.push.sent.clear()
    await ctx.handle_message(NEBO, "Підлітає", T0 + timedelta(minutes=4))
    assert ctx.push.sent == []


async def test_a_series_word_with_a_target_is_still_ballistic():
    ctx = make_ctx()
    await ctx.handle_message(NEBO, "Балістика на Київ", T0)
    ctx.push.sent.clear()
    await ctx.handle_message(NEBO, "Ще нові цілі", T0 + timedelta(minutes=4))
    assert ctx.push.sent == ["Ще нові цілі"]


async def test_a_warning_after_a_launch_keeps_the_window_open():
    ctx = make_ctx(push_warnings=True)
    await ctx.handle_message(NEBO, "Балістика на Київ", T0)
    await ctx.handle_message(NEBO, "Загроза балістики з Курська",
                             T0 + timedelta(minutes=1))
    ctx.push.sent.clear()
    await ctx.handle_message(NEBO, "Підлітають", T0 + timedelta(minutes=4))
    assert ctx.push.sent == ["Підлітають"]


async def test_drone_recon_wording_is_not_a_ballistic_target():
    ctx = make_ctx(push_warnings=True)
    await ctx.handle_message(NEBO, "Маневрують, розвідують, шукають цілі", T0)
    assert ctx.push.sent == []
    assert ctx.db.pushes == []


async def test_recon_wording_still_needs_a_confirmed_launch():
    ctx = make_ctx()
    for text in ("Дрони маневрують, ціль над Оболонню",
                 "Розвідують, шукають цілі"):
        ctx = make_ctx()
        await ctx.handle_message(NEBO, text, T0)
        assert ctx.push.sent == [], text


async def test_a_plain_target_on_nebo_is_untouched_by_the_recon_guard():
    ctx = make_ctx()
    await ctx.handle_message(NEBO, "Ціль", T0)
    assert ctx.push.sent == ["Ціль"]
    ctx = make_ctx(push_warnings=True)
    await ctx.handle_message(NEBO, "Ціль з Курська", T0)
    assert ctx.push.sent == ["Ціль з Курська"]
    assert ctx.db.pushes[0][2] == "warning"


async def test_a_past_tense_salvo_tally_is_not_a_launch():
    ctx = make_ctx(push_warnings=True)
    await ctx.handle_message(MONIT, "Знову відпрацювали 2 установки С-400.", T0)
    assert ctx.push.sent == []


async def test_live_firing_is_still_a_launch():
    ctx = make_ctx()
    await ctx.handle_message(WAR, "По Київщині відпрацювання С-400 з Брянщини.", T0)
    assert ctx.push.sent == ["По Київщині відпрацювання С-400 з Брянщини."]
    ctx = make_ctx()
    await ctx.handle_message(MONIT, "Вихід С-400 з Брянська.", T0)
    assert ctx.push.sent == ["Вихід С-400 з Брянська."]


async def test_a_past_tense_salvo_recap_is_not_a_launch():
    ctx = make_ctx(push_warnings=True)
    await ctx.handle_message(MONIT, "Знову відпрацювали 2 установки С-400.", T0)
    assert ctx.push.sent == []
    assert ctx.db.pushes == []


async def test_live_s400_firing_on_war_monitor_still_alerts():
    ctx = make_ctx()
    await ctx.handle_message(WAR, "По Київщині відпрацювання С-400 з Брянщини.", T0)
    assert ctx.push.sent == ["По Київщині відпрацювання С-400 з Брянщини."]


async def test_a_bare_target_on_monit0ring_is_ballistic():
    ctx = make_ctx()
    await ctx.handle_message(MONIT, "Є ціль, центр!", T0)
    assert ctx.push.sent == ["Є ціль, центр!"]
    assert ctx.db.pushes == [
        (MONIT, "ballistic", "inbound", "Є ціль, центр!", True),
    ]


NOT_A_TARGET_ON_MONIT0RING = (
    "Робота наших систем Patriot по ворожим цілям 2 липня 2026.",
    "За попередніми даними - повітряні цілі знищені.",
    "Щонайменше 3 ракети збито Петріотом.",
    "Головна ціль цього удару - зрозуміти місце розташування систем ПРО Києва.",
    "Ціль реактивних дронів - витрата засобів ППО.",
    "Просто літають і шукають собі цілі.",
)


async def test_monit0ring_prose_targets_are_not_launches():
    for text in NOT_A_TARGET_ON_MONIT0RING:
        ctx = make_ctx(push_warnings=True)
        await ctx.handle_message(MONIT, text, T0)
        assert ctx.push.sent == [], text
        assert ctx.db.pushes == [], text


async def test_a_run_in_over_the_city_is_a_drone_track():
    ctx = make_ctx(push_warnings=True)
    await ctx.handle_message(MONIT, "Загроза балістики з Курська.", T0)
    ctx.push.sent.clear()
    await ctx.handle_message(MONIT, "Заходить на правий берег Києва.",
                             T0 + timedelta(minutes=2))
    assert ctx.push.sent == []
    assert ctx.db.pushes == [
        (MONIT, "ballistic", "warning", "Загроза балістики з Курська.", True),
    ]


async def test_a_run_in_closes_the_ballistic_window_behind_it():
    ctx = make_ctx(push_warnings=True)
    await ctx.handle_message(MONIT, "Загроза балістики з Курська.", T0)
    await ctx.handle_message(MONIT, "Заходить в Київ по Дніпру.",
                             T0 + timedelta(minutes=1))
    ctx.push.sent.clear()
    await ctx.handle_message(NEBO, "Підлітає", T0 + timedelta(minutes=4))
    assert ctx.push.sent == []


async def test_a_western_bearing_is_not_a_run_in():
    ctx = make_ctx()
    await ctx.handle_message(NEBO, "Балістика на Київ", T0)
    ctx.push.sent.clear()
    await ctx.handle_message(NEBO, "Ще цілі із заходу", T0 + timedelta(minutes=4))
    assert ctx.push.sent == ["Ще цілі із заходу"]


async def test_a_forecast_for_the_night_is_a_warning_not_a_siren():
    ctx = make_ctx(push_warnings=True)
    await ctx.handle_message(
        MONIT,
        "Сьогодні вночі останні 24 години на балістику від «вагомого» джерела.",
        T0,
    )
    assert ctx.db.pushes[0][2] == "warning"


async def test_a_strike_in_progress_outranks_the_hours_it_mentions():
    ctx = make_ctx()
    await ctx.handle_message(
        MONIT, "Криють балістикою. Я вас попереджав про 72 години.", T0)
    assert ctx.db.pushes[0][2] == "inbound"


async def test_a_bare_weapon_name_stays_a_siren():
    for text in ("Ще Циркони", "Балістика !!!", "Циркон", "Балістика + Циркони"):
        ctx = make_ctx()
        await ctx.handle_message(NEBO, text, T0)
        assert ctx.push.sent == [text], text
        assert ctx.db.pushes[0][2] == "inbound", text
