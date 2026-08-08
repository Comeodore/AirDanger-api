import re

RE_FLAGS = re.IGNORECASE | re.UNICODE


KYIV_CORE = [
    r"\bкиїв\w*",
    r"\bкиєв\w*",
    r"\bстолиц\w+",
    r"\bагломерац\w+",
    r"\bпередміст\w+",
    r"\bтроєщин\w*",
    r"\bоболон\w*",
    r"\bпозняк\w*",
    r"\bголосіїв\w*",
    r"\bсолом'?янк\w*",
    r"\bдарниц\w*",
    r"\bлук'?янівк\w*",
    r"\bсвятошин\w*",
    r"\bподол\w*",
    r"\bпечерськ\w*",
    r"\bдеснянськ\w*",
    r"\bшевченківськ\w*",
    r"\bтец-?\d",
]

KYIV_NEAR = [
    r"\bбровар\w*",
    r"\bбориспіл\w*",
    r"\bбориспільськ\w*",
    r"\bвишнев\w*",
    r"\bірпін\w*",
    r"\bбуч[аиіу]\b",
    r"\bбучанськ\w*",
    r"\bгостомел\w*",
    r"\bвишгород\w*",
    r"\bзазим'?[єя]\w*",
    r"\bдимерк\w*",
    r"\bкняжич\w*",
    r"\bщаслив\w*",
    r"\bборщагівк\w*",
    r"\bкоцюбинськ\w*",
    r"\bчайк[иі]\b",
    r"\bкрюківщин\w*",
    r"\bновосілк\w*",
    r"\bгатн\w*",
    r"\bбоярк\w*",
    r"\bвасильк[іоів]\w*",
    r"\bобухів\w*",
    r"\bукраїнк\w*",
    r"\bглевах\w*",
    r"\bпетрівц\w*",
    r"\bгоренк\w*",
    r"\bдударк\w*",
]

KYIV_REGION = [
    r"\bкиївщин\w*",
    r"\bкиївськ\w+\s+област\w*",
    r"\bкиївської\b",
]

KYIV = KYIV_CORE + KYIV_NEAR + KYIV_REGION


KYIV_OBLAST_FAR = [
    r"\bбіл[аоуої][^\n]{0,3}церкв\w*", r"\bбілоцерків\w*",
    r"\bфастів\w*", r"\bпереясл\w*", r"\bржищ\w*", r"\bкагарлик\w*",
    r"\bмиронівк\w*", r"\bбогуслав\w*", r"\bсквир\w*", r"\bтетіїв\w*",
    r"\bузин\w*", r"\bяготин\w*", r"\bіванк(ів|ов)\w*", r"\bполіськ\w*",
    r"\bбородянк\w*",
]


ELSEWHERE_PLACES = [
    r"одес\w*", r"одещин\w*",
    r"дніпр[оауі]\w*", r"дніпропетровщин\w*", r"кам'?янськ\w*",
    r"крив(ий|ому|ого)\s+ріг\w*", r"криворіз\w*",
    r"харк(ів|ов)\w*", r"харківщин\w*",
    r"миколаїв\w*", r"миколаївщин\w*",
    r"херсон\w*", r"херсонщин\w*",
    r"запоріж\w*", r"запорізьк\w*",
    r"полтав\w*", r"полтавщин\w*", r"кременчу\w*", r"лубн\w*",
    r"сум[иі]\b", r"сумщин\w*", r"конотоп\w*", r"шостк\w*",
    r"чернігів\w*", r"чернігов\w*", r"чернігівщин\w*", r"ніжин\w*",
    r"черкас\w*", r"черкащин\w*", r"сміл\w*", r"кан(ів|ев)\w*",
    r"вінниц\w*", r"вінниччин\w*",
    r"житомир\w*", r"житомирщин\w*",
    r"кропивниц\w*", r"кіровоградщин\w*", r"уман\w*",
    r"льв(ів|ов)\w*", r"львівщин\w*",
    r"луцьк\w*", r"волин\w*",
    r"рівн(е|ен)\w*", r"рівненщин\w*",
    r"тернопіл\w*", r"тернопільщин\w*",
    r"хмельниц\w*", r"хмельниччин\w*",
    r"ужгород\w*", r"закарпат\w*",
    r"івано[- ]?франк\w*", r"чернівц\w*", r"буковин\w*",
    r"луганщин\w*", r"донеччин\w*", r"краматорськ\w*",
    r"павлоград\w*", r"богодухів\w*", r"баштанк\w*", r"первомайськ\w*",
    r"заток[аиу]\b", r"чорноморськ\w*", r"чабанк\w*",
    r"коблев\w*", r"фонтанк\w*", r"пересип\w*", r"аркаді\w*",
    r"лиманк\w*", r"звенигородк\w*", r"новий\s+буг\b",
]

TOWARD = r"(?:\bна|\bдо|\bпо|\bв\s+бік|\bу\s+бік|\bу\s+напрямку|\bв\s+напрямку|\bдалі)\s+"


KYIV_AXIS_SITES = [
    r"\bбрянс[ьк]\w*", r"\bбрянщин\w*",
    r"\bкурс[ьк]\w*", r"\bкурьск\w*", r"\bкурщин\w*",
    r"\b(бє|бі|бе)лгород\w*", r"\b(бє|бі|бе)лгородщин\w*",
    r"\bворонеж\w*", r"\bорл(а|і|ом)\b", r"\bшаталов\w*", r"\bсєщ\w*",
    r"\bкапустин\w*",
]

OTHER_SITES = [
    r"\bкрим\w*", r"\bтаганро[гз]\w*", r"\bростов\w*", r"\bміллеров\w*",
    r"\bазовськ\w*", r"\bчорн(ого|е)\s+мор\w*", r"\bачм\b", r"\bакватор\w*",
    r"\bкраснодар\w*", r"\bставропол\w*", r"\bмосковськ\w*",
    r"\bсанкт-петербур\w*", r"\bневинномиськ\w*",
    r"\bпівд(ень|ня|енн\w*)\b", r"\bсход[уі]\b", r"\bзі\s+сходу\b",
]


def _compile(patterns: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(p, RE_FLAGS) for p in patterns]


_KYIV = _compile(KYIV)
_KYIV_FAR = _compile(KYIV_OBLAST_FAR)
_ELSEWHERE_BARE = _compile([rf"\b{p}" for p in ELSEWHERE_PLACES])
_ELSEWHERE_AIMED = _compile([rf"{TOWARD}{p}" for p in ELSEWHERE_PLACES])
_KYIV_AXIS_SITES = _compile(KYIV_AXIS_SITES)
_OTHER_SITES = _compile(OTHER_SITES)


def _any(patterns: list[re.Pattern[str]], text: str) -> bool:
    return any(p.search(text) for p in patterns)


def mentions_kyiv(text: str) -> bool:
    return _any(_KYIV, text) or _any(_KYIV_FAR, text)


def kyiv_axis_site(text: str) -> bool:
    return _any(_KYIV_AXIS_SITES, text)


def other_site(text: str) -> bool:
    return _any(_OTHER_SITES, text)


def aimed_elsewhere(text: str) -> bool:
    return _any(_ELSEWHERE_AIMED, text) and not mentions_kyiv(text)


def elsewhere_target(text: str) -> bool:
    return _any(_ELSEWHERE_BARE, text) and not mentions_kyiv(text)


def mentions_any_place(text: str) -> bool:
    return (
        mentions_kyiv(text)
        or _any(_ELSEWHERE_BARE, text)
        or kyiv_axis_site(text)
        or other_site(text)
    )


def kyiv_bound(text: str) -> bool:
    if _any(_KYIV, text):
        return True
    if elsewhere_target(text):
        return False
    return kyiv_axis_site(text) and not other_site(text)
