from dataclasses import dataclass, field


BALLISTIC_MARKERS = ("‼️", "☄", "☄️")
MISSILE_MARKERS = ("❗️", "❗")
WARNING_MARKERS = ("🟣", "🛫")
DRONE_MARKERS = ("⚠️", "🅿️", "🔄", "🪃", "🛵")
QUIET_MARKERS = (
    "📡", "🟨", "🟧", "✈️", "↪️", "💣", "⚡️", "🇺🇦", "💬",
    "🦫", "🏹", "🔱", "✖️", "#", "🔵", "🟢",
)
CLEAR_MARKERS = ("⚪️", "⚪")


@dataclass(frozen=True)
class ChannelProfile:
    name: str
    structured: bool = False
    require_kyiv: bool = False
    allow_bare_target: bool = True
    target_is_ballistic: bool = False
    siren_needs_kyiv: bool = False
    trim_warning_push: bool = False
    extra_veto: tuple[str, ...] = field(default_factory=tuple)


WAR_MONITOR_VETO = (
    r"^\W*(?:[а-яіїєґ']+,\s*)?робота ворожої ппо",
    r"\bтриває\b",
    r"\bтривають\b",
    r"\bзгідно зі звітом\b",
    r"\bбуло знищено\b",
    r"\bнагадуємо\b",
    r"\bне варто наближатись\b",
    r"\bймовірність\b",
    r"\bобстановка станом\b",
    r"\bзагиблі\b",
    r"\bзагинул\w+",
    r"\bпостражда\w+",
)


KYIV_MONIT_VETO = (
    r"\bне\s+фіксую\b",
    r"\bвідпрацюва(?:в|ла|ли|ло)\b",
    r"\bбез\s+баліст\w*",
    r"\bзнищен\w+",
    r"\bпатріот\w*",
    r"\bпетріот\w*",
    r"\bpatriot\b",
    r"\bне\s+по\s+нам\b",
    r"\bрозвідспільнот\w*",
    r"\bнадійшла\s+інформаці\w*",
    r"\bпо\s+плану\s+противник\w*",
    r"^\W*ураженн\w+",
    r"\bзбільшив\w*",
    r"\bзеленськ\w+",
    r"\bщомісяц\w*",
    r"^\W*✍",
)


PROFILES = {
    "kyiv_nebo": ChannelProfile(name="kyiv_nebo", target_is_ballistic=True),
    "war_monitor": ChannelProfile(
        name="war_monitor",
        structured=True,
        require_kyiv=True,
        allow_bare_target=False,
        trim_warning_push=True,
        extra_veto=WAR_MONITOR_VETO,
    ),
    "kyiv_monit0ring": ChannelProfile(
        name="kyiv_monit0ring",
        target_is_ballistic=True,
        siren_needs_kyiv=True,
        extra_veto=KYIV_MONIT_VETO,
    ),
}

DEFAULT_PROFILE = ChannelProfile(name="default")


def profile_for(channel: str) -> ChannelProfile:
    return PROFILES.get(channel, DEFAULT_PROFILE)


def marker_of(text: str) -> str | None:
    head = text.lstrip()
    for group in (
        CLEAR_MARKERS, BALLISTIC_MARKERS, WARNING_MARKERS,
        DRONE_MARKERS, QUIET_MARKERS, MISSILE_MARKERS,
    ):
        for marker in group:
            if head.startswith(marker):
                return marker
    return None
