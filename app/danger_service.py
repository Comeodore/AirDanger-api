from dataclasses import dataclass

from custom_components.aerial_danger.danger import DangerDetector
from custom_components.aerial_danger.danger.keywords import IRBM_DANGER, SAFETY

from . import geo
from .profiles import (
    BALLISTIC_MARKERS,
    CLEAR_MARKERS,
    DRONE_MARKERS,
    MISSILE_MARKERS,
    QUIET_MARKERS,
    WARNING_MARKERS as STRUCTURED_WARNING_MARKERS,
    ChannelProfile,
    DEFAULT_PROFILE,
    marker_of,
)


BALLISTIC_WORDS = [
    r"\bбалістик\w+",
    r"\bбалістичн\w+",
    r"\bциркон\w*\b",
    r"\bцикрон\w*\b",
    r"\bцирокн\w*\b",
    r"\bкинджал\w*\b",
    r"\bіскандер\w*\b",
    r"\bотрк\b",
    r"\bшвидкісн\w+",
    r"\bспуск(?:и)?\b",
    r"\bс-?400\b",
    r"\bкн-?23\b",
    r"\bбр\b[^\n]{0,24}\b(київ|києв|київщин|вихід|виходи|спуск)\w*",
    r"\bвих(ід|оди|оду|одів)\w*\b[^\n]{0,24}\b(брянськ|курськ|бєлгород|білгород|воронеж|таганрог)\w*",
]


TARGET_WORDS = [
    r"\bціл(ь|і|ей|ям|ями)\b",
]


RECON_WORDS = [
    r"\bманевру\w+",
    r"\bрозвіду\w+",
    r"\bшука(?:ють|є|ти|ючи)\w*",
]


DRONE_TRACK_WORDS = [
    r"\bнов(?:ий|а|е|і|их|ими)\b",
    r"\bзаходить\b",
    r"\bзаходять\b",
    r"\bзаходити\b",
]


TARGET_ON_KYIV = [
    r"\bціл\w*\b[^\n]{0,24}\b(?:на|(?:в|у)\s+бік)\s+(?:київ|києв|нас|столиц)\w*",
    r"\bна\s+київ\w*[^\n]{0,24}\bціл\w*",
]


DRONE_WORDS = [
    r"\bшахед\w*\b",
    r"\bбпла\b",
    r"\bбезпілотник\w*\b",
    r"\bгерань\w*\b",
    r"\bдрон\w*\b",
    r"\bреактивн\w+",
    r"\bреактив\b",
]


OTHER_WEAPONS = [
    r"\bкалібр\w*",
    r"\bтуш(к|ок)\w*",
    r"\bту-?\d+",
    r"\bкрилат\w+",
    r"\bх-?101\b",
    r"\bх-?59\b",
    r"\bх-?31\w*",
    r"\bх-?22\b",
    r"\bонікс\w*",
    r"\bкаб(?:ів|и|у)?\b",
    r"\bбандерол\w*",
]


OURS = [
    r"\bкиїв\w*",
    r"\bкиєв\w*",
    r"\b(на|до) нас\b",
]


IGNORE = [
    r"\b(банк[аи]|збір|збори|донат\w*|гривень|грн|реквізит\w*|monobank|підписуйтесь|перехоплювач\w*)\b",
]
MAX_LEN = 160


BACKEND_VETO = [
    r"\bнічого\b",
    r"\bнемає\b",
    r"\bне летить\b",
    r"\bне фіксується\b",
    r"\bне видно\b",
    r"\bбільше не\b",
    r"\bперестал\w+",
    r"\bбез\s+вих\w*",
    r"\bбез\s+пуск\w*",
    r"\bбез\s+загроз\w*",
    r"\bвлучанн\w+",
    r"\bзавал\w+",
    r"\bпоранен\w+",
    r"\bпожеж\w+",
    r"\bгор(ить|ять)\b",
    r"\bпоган\w+\s+фіксац\w*",
    r"\bрозвідувальн\w+",
    r"\bдорозвідк\w+",
    r"\bмаршрут\w+",
    r"^\W*щодо\b",
    r"\bбул[аои]\b",
    r"\bчисто\b",
    r"\bобстріл\w*",
    r"\bнеактивн\w+",
    r"\bзбій\b",
    r"\bбез фіксац\w*",
    r"\bпоки все\b",
    r"\bначе все\b",
    r"\bвсе поки\b",
    r"\bзникл\w+",
    r"\bочікуємо\b",
    r"\bдо відбою\b",
    r"\bвсе спокійно\b",
    r"\bне до нас\b",
    r"\bне на нас\b",
    r"\bне на київ\w*",
    r"\bрежимі ппо\b",
    r"\bвибух\w*",
    r"\bне балістика\b",
    r"\bне відмічен\w+",
    r"\bне відмічал\w+",
    r"\bфантомн\w+",
    r"\bне летять\b",
    r"\bзбит[оиі]\b",
    r"\bзнял[ио]\b",
    r"\bзнят[оі]\b",
    r"\bзастосовано\b",
    r"\bатакован\w+",
    r"\bлетіл\w+",
    r"\bслужб\w+",
    r"\bсклало\b",
    r"\bвірогідн\w+",
    r"\bна місцях\b",
    r"\bтреба бути\b",
    r"\bцього удару\b",
    r"\bвдалось\b",
    r"\bпідвезли\b",
    r"\bв готовності\b",
    r"\bготу(є|ють)\b",
]


ALL_CLEAR = [
    r"\bвідбій\b",
    r"\bатака завершена\b",
    r"\bціл(?:і|ей) (?:зникл[аи]?|більше немає|вже немає|немає)\b",
    r"\b(?:без цілей|локаційно чисто)\b",
    r"\b(?:наразі )?загроз[аи] [^\n]{0,48}\bнемає\b",
]


CLEAR_FUTURE = [
    r"\bбуде\s+відбій\b",
    r"\bвідбій\b[^\n]{0,32}\bбуде\b",
    r"\bочіку\w+[^\n]{0,16}\bвідб(?:ій|ою)\b",
    r"\bне\s+дали\s+відбій\b",
    r"\bдо\s+відбою\b",
]

CLEAR_OTHER_SCOPE = [
    r"\b(?:бпла|шахед|дрон|безпілотник|геран)\w*",
]

CLEAR_OUR_SCOPE = [
    r"\b(?:баліст|ракет|авіац|кинджал|циркон|іскандер|мбр)\w*",
]


BARE_LIVE_MARKERS = [
    r"\bлет(ить|ять)\b",
    r"\bще\s+(?:\w+\s+){0,2}з\s+(?:брянськ|курськ|бєлгород|білгород|воронеж|брянщин|курщин)\w*",
]
INBOUND_MARKERS = [
    r"\bвідпрацюванн\w+",
    r"\bще\s+(?:\w+\s+){0,2}з\s+(?:брянськ|курськ|бєлгород|білгород|воронеж|брянщин|курщин)\w*",
    r"\bна київ\b",
    r"\bлет(ить|ять)\b",
    r"\bціл(ь|і|ей|ям|ями)\b",
    r"\bпідліт\w*",
    r"\bпуск\w*\b",
    r"\bвих(ід|оди)\b",
    r"\bкурс(ом|у|и)?\b",
    r"\bповз\b",
    r"\bдо нас\b",
    r"\bу наш бік\b",
    r"\bнад (київ|нами)\w*",
    r"\b(?:у|в)\s+напрямку\s+(?:київ|києв|столиц)\w*",
    r"\bспуск(?:и)?\b",
    r"\bшвидкісн\w+",
    r"\bзаходить\b",
    r"\bкрию(?:ть|є)\b",
    r"\bнаближа\w+",
    r"\b\d+\s*(?:балістик|циркон|кинджал|іскандер)\w*",
    r"\b\d+\s*[-–]?\s*\d*\s*ракет\w*",
]
WARNING_MARKERS = [
    r"\bзагроз\w+",
    r"\bзліт\w*\b",
    r"\bможуть\b",
    r"\bможе\b",
    r"\bможлив\w+",
    r"\bімовірн\w+",
    r"\bймовірн\w+",
    r"\bочіку\w+",
    r"\bготується\b",
    r"\bпротягом ночі\b",
    r"\bвночі\b",
    r"\b\d+\s+годин\w*",
    r"\bпам'ятати\b",
    r"\b(з|із)\s+(курськ|курьск|курск|брянськ|брянск|воронеж|таганрог|орл|шаталов|міллеров|крим|капустин)\w*",
    r"\b(курськ|курьск|курск|брянськ|брянск|воронеж|таганрог|шаталов|міллеров)\w*",
    r"\b(бє|бі|бе)лгород\w*",
    r"\b(з|із)\s+(брянщин|курщин|бєлгородщин|білгородщин)\w*",
    r"\b(крим|криму|таганро[гз]|ростов|міллеров)\w*",
]


@dataclass(frozen=True)
class DetectedThreat:
    type: str
    text: str
    severity: str = "inbound"

@dataclass(frozen=True)
class Evaluation:
    safety: bool = False
    detection: DetectedThreat | None = None
    other_weapon: bool = False
    bare_target: bool = False
    all_clear: bool = False

class DangerService:
    def __init__(self) -> None:
        matcher = DangerDetector([], [])
        self._matcher = matcher
        self._safety = matcher.compile_patterns(SAFETY)
        self._all_clear = matcher.compile_patterns(ALL_CLEAR)
        self._clear_future = matcher.compile_patterns(CLEAR_FUTURE)
        self._clear_other_scope = matcher.compile_patterns(CLEAR_OTHER_SCOPE)
        self._clear_our_scope = matcher.compile_patterns(CLEAR_OUR_SCOPE)
        self._veto = matcher.compile_patterns(BACKEND_VETO)
        self._ignore = matcher.compile_patterns(IGNORE)
        self._irbm = matcher.compile_patterns(IRBM_DANGER)
        self._ballistic = matcher.compile_patterns(BALLISTIC_WORDS)
        self._target = matcher.compile_patterns(TARGET_ON_KYIV)
        self._target_words = matcher.compile_patterns(TARGET_WORDS)
        self._recon = matcher.compile_patterns(RECON_WORDS)
        self._drone_track = matcher.compile_patterns(DRONE_TRACK_WORDS)
        self._drone_words = matcher.compile_patterns(DRONE_WORDS)
        self._other_weapons = matcher.compile_patterns(OTHER_WEAPONS)
        self._ours = matcher.compile_patterns(OURS)
        self._bare_live = matcher.compile_patterns(BARE_LIVE_MARKERS)
        self._inbound_markers = matcher.compile_patterns(INBOUND_MARKERS)
        self._warning_markers = matcher.compile_patterns(WARNING_MARKERS)
        self._profile_veto: dict[str, list] = {}

    def is_all_clear(self, text: str) -> bool:
        if not self._matcher.match_first(self._all_clear, text):
            return False
        return not self._clear_vetoed(text)

    def _clear_vetoed(self, text: str) -> bool:
        if self._matcher.match_first(self._clear_future, text):
            return True
        return bool(
            self._matcher.match_first(self._clear_other_scope, text)
            and not self._matcher.match_first(self._clear_our_scope, text)
        )

    def severity(self, text: str) -> str:
        if self._matcher.match_first(self._inbound_markers, text):
            return "inbound"
        if self._matcher.match_first(self._warning_markers, text):
            return "warning"
        return "inbound"

    def bare_severity(self, text: str) -> str:
        if self._matcher.match_first(self._bare_live, text):
            return "inbound"
        if self._matcher.match_first(self._warning_markers, text):
            return "warning"
        return "inbound"

    def aimed_elsewhere(self, text: str) -> bool:
        return geo.aimed_elsewhere(text)

    def _vetoed(self, text: str, profile: ChannelProfile) -> bool:
        if self._matcher.match_first(self._veto, text):
            return True
        if not profile.extra_veto:
            return False
        cached = self._profile_veto.get(profile.name)
        if cached is None:
            cached = self._matcher.compile_patterns(list(profile.extra_veto))
            self._profile_veto[profile.name] = cached
        return bool(self._matcher.match_first(cached, text))

    def _ballistic_hit(self, text: str, severity: str) -> Evaluation:
        return Evaluation(detection=DetectedThreat(
            type="ballistic", text=text, severity=severity,
        ))

    def _structured(
        self, text: str, marker: str, profile: ChannelProfile,
    ) -> Evaluation | None:
        if marker in CLEAR_MARKERS:
            if geo.elsewhere_target(text):
                return Evaluation()
            return Evaluation(safety=True, all_clear=not self._clear_vetoed(text))
        if marker in QUIET_MARKERS:
            return Evaluation()
        if marker in DRONE_MARKERS:
            return Evaluation(other_weapon=not geo.elsewhere_target(text))

        if marker in BALLISTIC_MARKERS:
            if geo.kyiv_bound(text):
                return self._ballistic_hit(text, self.severity(text))
            if geo.mentions_any_place(text):
                return Evaluation()
            return Evaluation(bare_target=profile.allow_bare_target)
        if marker in STRUCTURED_WARNING_MARKERS:
            if geo.elsewhere_target(text):
                return Evaluation()
            return self._ballistic_hit(text, "warning")
        if marker in MISSILE_MARKERS:
            if self._matcher.match_first(self._ballistic, text):
                if geo.kyiv_bound(text):
                    return self._ballistic_hit(text, self.severity(text))
                return Evaluation()
            if not geo.mentions_kyiv(text):
                return Evaluation()
            if self._matcher.match_first(self._drone_words, text):
                return Evaluation(other_weapon=True)
            if self._matcher.match_first(self._other_weapons, text):
                return Evaluation(other_weapon=True)
            return Evaluation()
        return None

    def evaluate(
        self, text: str, profile: ChannelProfile = DEFAULT_PROFILE,
    ) -> Evaluation:
        if self._matcher.match_first(self._safety, text):
            if geo.elsewhere_target(text):
                return Evaluation()
            return Evaluation(safety=True, all_clear=self.is_all_clear(text))

        marker = marker_of(text) if profile.structured else None
        if marker in CLEAR_MARKERS:
            if geo.elsewhere_target(text):
                return Evaluation()
            return Evaluation(safety=True, all_clear=not self._clear_vetoed(text))

        if self._vetoed(text, profile):
            return Evaluation()
        if self._matcher.match_first(self._irbm, text):
            return Evaluation(detection=DetectedThreat(
                type="irbm", text=text, severity=self.severity(text),
            ))
        if len(text) > MAX_LEN or self._matcher.match_first(self._ignore, text):
            return Evaluation()

        if marker is not None:
            structured = self._structured(text, marker, profile)
            if structured is not None:
                return structured

        if profile.require_kyiv:
            if not geo.kyiv_bound(text):
                return Evaluation()
        elif geo.aimed_elsewhere(text):
            return Evaluation()

        if self._matcher.match_first(self._ballistic, text):
            return self._ballistic_hit(text, self.severity(text))
        if self._matcher.match_first(self._drone_words, text):
            return Evaluation(other_weapon=not geo.elsewhere_target(text))
        if self._matcher.match_first(self._other_weapons, text):
            return Evaluation(other_weapon=not geo.elsewhere_target(text))
        if self._matcher.match_first(self._target, text):
            return self._ballistic_hit(text, "inbound")
        if profile.allow_bare_target and self._matcher.match_first(
            self._inbound_markers, text
        ):
            if (
                profile.target_is_ballistic
                and self._matcher.match_first(self._target_words, text)
                and not self._matcher.match_first(self._recon, text)
                and not geo.elsewhere_target(text)
            ):
                return self._ballistic_hit(text, self.bare_severity(text))
            if self._matcher.match_first(self._drone_track, text):
                return Evaluation(other_weapon=not geo.elsewhere_target(text))
            return Evaluation(bare_target=True)
        return Evaluation()
