from dataclasses import dataclass

from custom_components.aerial_danger.danger import DangerDetector
from custom_components.aerial_danger.danger.keywords import IRBM_DANGER, SAFETY


BALLISTIC_WORDS = [
    r"\bбалістик\w+",
    r"\bбалістичн\w+",
    r"\bциркон\w*\b",
    r"\bцикрон\w*\b",
    r"\bкинджал\w*\b",
    r"\bіскандер\w*\b",
    r"\bотрк\b",
    r"\bшвидкісн\w+",
    r"\bвих(ід|оди|оду|одів)\w*\b[^\n]{0,24}\b(брянськ|курськ|бєлгород|білгород|воронеж|таганрог)\w*",
]


TARGET_ON_KYIV = [
    r"\bціл\w*\b[^\n]{0,24}\bна\s+(?:київ|нас|столиц)\w*",
    r"\bна\s+київ\w*[^\n]{0,24}\bціл\w*",
]


DRONE_WORDS = [
    r"\bшахед\w*\b",
    r"\bбпла\b",
    r"\bбезпілотник\w*\b",
    r"\bгерань\w*\b",
    r"\bреактивн\w+",
]


OTHER_WEAPONS = [
    r"\bкалібр\w*",
    r"\bтуш(к|ок)\w*",
    r"\bту-?\d+",
    r"\bкрилат\w+",
    r"\bх-?101\b",
    r"\bх-?59\b",
    r"\bбандерол\w*",
]


_PLACES = (
    r"черкас|полтав|сум|харків|дніпр|одес|вінниц|житомир|чернігів|кременчук|"
    r"запоріж|миколаїв|кропивниц|умань|луцьк|рівн|тернопіл|хмельниц|ужгород|івано|"
    r"львів|бровар|білу церкву|кривий ріг|"
    r"сумщин|чернігівщин|харківщин|полтавщин|черкащин|житомирщин|вінниччин|"
    r"кіровоградщин|дніпропетровщин|миколаївщин|одещин|хмельниччин|рівненщин|"
    r"львівщин|тернопільщин|луганщин|донеччин|херсонщин|"
    r"ржищ|українк|бориспіл|обухів|васильк|фастів|ірпін|буч[аі]|боярк|вишнев|"
    r"глевах|кагарлик|миронівк"
)
ELSEWHERE = [
    rf"\bна\s+({_PLACES})\w*",
    rf"\bдо\s+({_PLACES})\w*",
    rf"\bв\s+бік\s+({_PLACES})\w*",
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
    r"\bбільше не\b",
    r"\bперестал\w+",
    r"\bбез фіксацій\b",
    r"\bвлучанн\w+",
    r"\bзавал\w+",
    r"\bпоранен\w+",
    r"\bпожеж\w+",
    r"\bгор(ить|ять)\b",
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
]


INBOUND_MARKERS = [
    r"\bна київ\b",
    r"\bлет(ить|ять|іла|іли)\b",
    r"\bціл(ь|і|ей|ям|ями)\b",
    r"\bпідліт\w*",
    r"\bпуск\w*\b",
    r"\bвих(ід|оди)\b",
    r"\bкурс(ом|у|и)?\b",
    r"\bповз\b",
    r"\bдо нас\b",
    r"\bу наш бік\b",
    r"\bнад (київ|нами)\w*",
    r"\bспуск\w*\b",
    r"\bшвидкісн\w+",
    r"\bзаходить\b",
    r"\bнаближа\w+",
    r"\b\d+\s*(?:балістик|циркон|кинджал|іскандер)\w*",
    r"\b\d+\s*[-–]?\s*\d*\s*ракет\w*",
]
WARNING_MARKERS = [
    r"\bзагроз\w+",
    r"\bзліт\w*\b",
    r"\bможуть\b",
    r"\bможлив\w+",
    r"\bочіку\w+",
    r"\bготується\b",
    r"\bпротягом ночі\b",
    r"\bпам'ятати\b",
    r"\b(з|із)\s+(курськ|брянськ|воронеж|таганрог|орл|шаталов|міллеров|крим|капустин)\w*",
    r"\b(курськ|брянськ|воронеж|таганрог|шаталов|міллеров)\w*",
    r"\b(з|із)\s+(брянщин|курщин|бєлгородщин|білгородщин)\w*",
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

class DangerService:
    def __init__(self) -> None:
        matcher = DangerDetector([], [])
        self._matcher = matcher
        self._safety = matcher.compile_patterns(SAFETY)
        self._veto = matcher.compile_patterns(BACKEND_VETO)
        self._ignore = matcher.compile_patterns(IGNORE)
        self._irbm = matcher.compile_patterns(IRBM_DANGER)
        self._ballistic = matcher.compile_patterns(BALLISTIC_WORDS)
        self._target = matcher.compile_patterns(TARGET_ON_KYIV)
        self._drone_words = matcher.compile_patterns(DRONE_WORDS)
        self._other_weapons = matcher.compile_patterns(OTHER_WEAPONS)
        self._elsewhere = matcher.compile_patterns(ELSEWHERE)
        self._ours = matcher.compile_patterns(OURS)
        self._inbound_markers = matcher.compile_patterns(INBOUND_MARKERS)
        self._warning_markers = matcher.compile_patterns(WARNING_MARKERS)

    def severity(self, text: str) -> str:
        if self._matcher.match_first(self._inbound_markers, text):
            return "inbound"
        if self._matcher.match_first(self._warning_markers, text):
            return "warning"
        return "inbound"

    def bare_severity(self, text: str) -> str:
        if self._matcher.match_first(self._warning_markers, text):
            return "warning"
        return "inbound"

    def aimed_elsewhere(self, text: str) -> bool:
        return bool(
            self._matcher.match_first(self._elsewhere, text)
            and not self._matcher.match_first(self._ours, text)
        )

    def evaluate(self, text: str) -> Evaluation:
        if self._matcher.match_first(self._safety, text):
            return Evaluation(safety=True)
        if self._matcher.match_first(self._veto, text):
            return Evaluation()
        if self._matcher.match_first(self._irbm, text):
            return Evaluation(detection=DetectedThreat(
                type="irbm", text=text, severity=self.severity(text),
            ))
        if len(text) > MAX_LEN or self._matcher.match_first(self._ignore, text):
            return Evaluation()
        if self.aimed_elsewhere(text):
            return Evaluation()
        if self._matcher.match_first(self._ballistic, text):
            return Evaluation(detection=DetectedThreat(
                type="ballistic", text=text, severity=self.severity(text),
            ))

        if self._matcher.match_first(self._drone_words, text):
            return Evaluation(other_weapon=True)
        if self._matcher.match_first(self._other_weapons, text):
            return Evaluation(other_weapon=True)
        if self._matcher.match_first(self._target, text):
            return Evaluation(detection=DetectedThreat(
                type="ballistic", text=text, severity="inbound",
            ))
        if self._matcher.match_first(self._inbound_markers, text):
            return Evaluation(bare_target=True)
        return Evaluation()
