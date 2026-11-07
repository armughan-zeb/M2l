"""Script Analyzer for the Programmatic Documentary Editor.

Extracts semantic triggers, entities, and pacing metrics from raw script prose:
- Locations (-> AnimatedMap)
- Dates & Eras (-> Timeline)
- Numbers & Percentages (-> StatCard / Chart)
- Direct Quotes & Testimonies (-> QuoteCard)
- People & Key Figures (-> PersonProfile)
- Documents, Audits & Leaks (-> DocumentHighlight)
- Press & Media Coverage (-> Headline)
- Comparisons & Contrasts (-> Comparison)
- System Processes & Chains (-> Diagram)
- Act / Chapter Breaks (-> ChapterTitle)
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .contract import PacingBeat, VisualSceneType


_SPOKEN_DIGIT_WORDS = (
    r"zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
    r"thirty|forty|fifty|sixty|seventy|eighty|ninety"
)
SPOKEN_MEASUREMENT_PATTERN = re.compile(
    rf"\b(?P<number>(?:{_SPOKEN_DIGIT_WORDS})(?:[\s-]+(?:{_SPOKEN_DIGIT_WORDS}|hundred|thousand|million|billion|trillion|and))*)"
    r"\s+(?P<unit>seconds?|minutes?|hours?|days?|weeks?|months?|years?|"
    r"yards?|meters?|metres?|kilometers?|kilometres?|miles?|feet|inches|"
    r"downs?|points?|goals?|people|passengers?|teams?|engines?|ships?|"
    r"dollars?|pounds?|euros?|percent)\b",
    re.IGNORECASE,
)

_NUMBER_PATTERNS = re.compile(
    r"(?:\$|£|€)?\b(?:\d+(?:[.,]\d+)?\s*(?:billion|million|trillion|percent|%|thousand|k|b|m)?|zero)\b|"
    + SPOKEN_MEASUREMENT_PATTERN.pattern,
    re.IGNORECASE,
)
_DATE_PATTERNS = re.compile(
    r"\b(?:18\d\d|19\d\d|20\d\d)\b|"
    r"\b\d{1,4}\s*(?:AD|A\.D\.|CE|C\.E\.|BC|B\.C\.|BCE|B\.C\.E\.)\b|"
    r"\b(?:circa|around|approximately|roughly|about)\s+\d{1,4}\s*(?:AD|BC|BCE|CE)?\b|"
    r"\b(?:1st|2nd|3rd|[4-9]th|1[0-9]th|20th|21st)\s+century\b|"
    r"\b(?:bronze\s+age|iron\s+age|middle\s+ages|dark\s+ages|renaissance|antiquity|medieval\s+(?:era|period|times)|classical\s+(?:era|period|antiquity)|roman\s+(?:era|period|empire|republic)|ancient\s+(?:era|times|world|period)|industrial\s+revolution|enlightenment)\b|"
    r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?\b|"
    r"\b\d{1,2}(?:st|nd|rd|th)?\s+of\s+(?:january|february|march|april|may|june|july|august|september|october|november|december)\b",
    re.IGNORECASE,
)
_QUOTE_PATTERNS = re.compile(
    r'["“]([^"”\n]{4,160})["”]|["“]([^"”\n]{8,160})|(?:declared|stated|noted|announced|said|wrote|claimed|proclaimed|testified):\s*["“]?([^"”\n,;]{6,140})',
    re.IGNORECASE,
)
_EXPLICIT_LOCATIONS = re.compile(
    r"\b(Vice City|Leonida|Florida|Miami|Silicon Valley|California|Tokyo|New York|London|Paris|Los Angeles|Rome|Tuscany|Sicily|Athens|Cairo|Venice|Berlin|Madrid|Beijing"
    r"|Antwerp|Brussels|Belgium|Palermo|Milan|Milano|Geneva|Amsterdam|Rotterdam|Zurich|Bavaria|Naples|Napoli|Turin|Torino|Bologna|Ghent|Bruges|Liege|Flanders|Wallonia|Lombardy"
    r"|Pompeii|Constantinople|Istanbul|Alexandria|Carthage|Jerusalem|Babylon|Memphis|Thebes|Sparta|Troy|Corinth|Syracuse|Antioch|Persepolis|Machu Picchu|Angkor Wat"
    r"|Damascus|Baghdad|Mecca|Medina|Timbuktu|Samarkand|Kyoto|Nanjing|Tenochtitlan|Cuzco"
    r"|Moscow|Sydney|Mumbai|Shanghai|Dubai|Singapore|Hong Kong|Seoul|Bangkok|Jakarta|Lagos|Nairobi|Cape Town|Rio de Janeiro|Buenos Aires|Lima|Bogota|Havana|Lisbon|Vienna|Prague|Warsaw|Stockholm|Oslo|Copenhagen|Helsinki|Dublin|Edinburgh|Montreal|Vancouver|Chicago|Houston|San Francisco|Washington|Philadelphia|Boston|Detroit|Atlanta|Seattle|Denver|Phoenix|Las Vegas|Minneapolis|Dallas|Nashville|New Orleans"
    r"|Colosseum|Parthenon|Pyramids|Sphinx|Acropolis|Forum|Pantheon|Stonehenge|Great Wall|Forbidden City|Kremlin|Eiffel Tower|Taj Mahal|Alhambra|Notre Dame|Hagia Sophia|Westminster|Vatican)\b",
    re.IGNORECASE,
)
_LOCATION_PATTERNS = re.compile(
    r"\b(?:[Ss]treets of|[Ss]tate of|[Cc]ity of|[Cc]oast of|[Tt]erritory of|[Tt]raveling to|[Ff]lying to|[Aa]rriving in|[Ee]scaped to|[Ff]led to|[Ff]led from)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b|"
    r"\b[Ff]rom\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b.*?\b[Tt]o\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b"
)
_DOCUMENT_KEYWORDS = re.compile(
    r"\b(memo|document|audit|report|contract|records?|classified|paper|ledger|file|email|transcripts?|evidence)\b",
    re.IGNORECASE,
)
_HEADLINE_KEYWORDS = re.compile(
    r"\b(reported|headline|newspaper|news|times|journal|post|press|announced|breaking|front\s+page)\b",
    re.IGNORECASE,
)
_COMPARISON_KEYWORDS = re.compile(
    r"\b(compared to|versus|while in|whereas|in contrast|before and after|on one hand|on the other hand|than ever before)\b",
    re.IGNORECASE,
)
_PROCESS_KEYWORDS = re.compile(
    r"\b(process|flow|caused|triggered|led to|as a result|system|chain of events|consequence|mechanism)\b",
    re.IGNORECASE,
)
_PERSON_HONORIFICS = re.compile(
    r"\b(?:Mr\.|Mrs\.|Ms\.|Dr\.|Prof\.|President|Prime Minister|King|Queen|Prince|Princess|CEO|Director|Officer"
    r"|Emperor|Empress|Pharaoh|Sultan|Caliph|Tsar|Kaiser|Shogun|Daimyo|Czar|Duke|Duchess|Baron|Baroness|Count|Countess"
    r"|General|Admiral|Commander|Captain|Colonel|Marshal|Warlord|Chieftain"
    r"|Pope|Cardinal|Archbishop|Bishop|Imam|Rabbi|Monk|Saint"
    r"|Architect|Engineer|Master|Inventor|Explorer|Navigator|Conqueror|Liberator"
    r"|Senator|Governor|Chancellor|Minister|Secretary|Ambassador|Diplomat"
    r"|Gladiator|Champion|Warrior|Knight|Samurai)\s+"
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})\b"
)
_HISTORICAL_FIGURE_PATTERNS = re.compile(
    r"\b([A-Z][a-z]+(?:\s+(?:the\s+)?[A-Z][a-z]+){0,3})\s+(?:declared|stated|noted|announced|said|wrote|claimed|ordered|commanded|built|constructed|conquered|defeated|ruled|founded|established|invaded|besieged|destroyed|commissioned|designed|discovered|invented|led|unified|abolished|crowned|overthrew|assassinated|martyred|executed|exiled|surrendered|negotiated|proclaimed)\b"
)
_CONSTRUCTION_KEYWORDS = re.compile(
    r"\b(built|constructed|erected|designed|engineered|assembled|architected|forged|crafted|manufactured"
    r"|foundation|structure|architecture|blueprint|engineering|construction|infrastructure|mechanism|apparatus"
    r"|columns|arches|vault|dome|aqueduct|amphitheater|arena|stadium|temple|fortress|castle|palace|cathedral|bridge|tunnel|dam|canal|wall|tower|gate|monument"
    r"|layers|floors|stories|levels|height|width|depth|diameter|circumference|tonnage|load-bearing"
    r"|battle plan|formation|tactics|strategy|siege|flanking|cavalry|infantry|artillery|fortification|blockade|defensive line)\b",
    re.IGNORECASE,
)
_KINETIC_TEXT_TRIGGERS = re.compile(
    r"\b(changed everything|altered the course|rewrote history|never the same|turned the tide|marked the beginning|sealed the fate"
    r"|the most|the largest|the greatest|the deadliest|the first|the last|the only|the final|the ultimate"
    r"|a new era|the end of|the dawn of|the rise of|the fall of|the death of|the birth of|the legacy of|the story of|the truth|the real|the secret|the hidden"
    r"|but there was a problem|what nobody expected|against all odds|the unthinkable happened|everything changed|nothing would be the same"
    r"|this is why|here is the thing|the question is|the answer|the key|the secret was|the reason)\b",
    re.IGNORECASE,
)
_COLLAGE_KEYWORDS = re.compile(
    r"\b(evidence|artifacts|relics|remains|ruins|fragments|pieces|specimens|samples|collection"
    r"|photographs|images|paintings|portraits|illustrations|engravings|carvings|murals|mosaics|sculptures"
    r"|gallery|museum|exhibition|archive|catalog|inventory|displayed|showcased|assembled|gathered|collected)\b",
    re.IGNORECASE,
)
_HEADLINE_EXPANDED = re.compile(
    r"\b(reported|headline|newspaper|news|times|journal|post|press|announced|breaking|front\s+page"
    r"|declared|proclamation|edict|decree|broadcast|bulletin|dispatch|telegram|communication|broadcast"
    r"|shocked the world|made history|captivated|stunned|astonished|horrified|outraged|celebrated|mourned)\b",
    re.IGNORECASE,
)


COUNTRY_ENTITY_MAP: Dict[str, str] = {
    # Do not treat unqualified ``America`` as the United States. In phrases
    # such as ``South America`` that legacy alias selected the wrong country
    # and could produce a confidently animated but false U.S. map.
    "united states": "USA", "usa": "USA", "us": "USA",
    "washington": "USA", "california": "USA", "florida": "USA", "texas": "USA",
    "united kingdom": "GBR", "uk": "GBR", "britain": "GBR", "great britain": "GBR", "england": "GBR", "london": "GBR",
    "germany": "DEU", "berlin": "DEU", "deutschland": "DEU",
    "france": "FRA", "paris": "FRA", "french": "FRA",
    "japan": "JPN", "tokyo": "JPN", "japanese": "JPN",
    "china": "CHN", "beijing": "CHN", "shanghai": "CHN", "chinese": "CHN",
    "india": "IND", "new delhi": "IND", "mumbai": "IND", "indian": "IND",
    "russia": "RUS", "moscow": "RUS", "russian": "RUS",
    "brazil": "BRA", "brasilia": "BRA", "rio": "BRA", "brazilian": "BRA",
    "australia": "AUS", "canberra": "AUS", "sydney": "AUS", "melbourne": "AUS",
    "canada": "CAN", "ottawa": "CAN", "canadian": "CAN", "toronto": "CAN",
    "saudi arabia": "SAU", "saudi": "SAU", "riyadh": "SAU", "arabia": "SAU",
    "south korea": "KOR", "korea": "KOR", "seoul": "KOR",
    "italy": "ITA", "rome": "ITA", "italian": "ITA", "milan": "ITA", "sicily": "ITA",
    "belgium": "BEL", "belgian": "BEL", "brussels": "BEL", "antwerp": "BEL", "flanders": "BEL", "wallonia": "BEL",
    "panama": "PAN",
    "netherlands": "NLD", "holland": "NLD", "dutch": "NLD", "amsterdam": "NLD", "rotterdam": "NLD",
    "switzerland": "CHE", "swiss": "CHE", "geneva": "CHE", "zurich": "CHE",
    "austria": "AUT", "vienna": "AUT",
    "spain": "ESP", "spanish": "ESP", "madrid": "ESP", "barcelona": "ESP",
}

_COUNTRY_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(COUNTRY_ENTITY_MAP.keys(), key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
_CORRIDOR_PATTERN = re.compile(
    r"\bfrom\s+(?:the\s+)?([A-Za-z\s]+?)\b.*?\bto\s+(?:the\s+)?([A-Za-z\s]+?)\b|"
    r"\b(?:between|connecting)\s+(?:the\s+)?([A-Za-z\s]+?)\b.*?\band\s+(?:the\s+)?([A-Za-z\s]+?)\b",
    re.IGNORECASE,
)


@dataclass
class SemanticTriggers:
    """Extracted semantic facts and triggers for a narration beat."""
    numbers: List[str] = field(default_factory=list)
    dates: List[str] = field(default_factory=list)
    quotes: List[str] = field(default_factory=list)
    locations: List[str] = field(default_factory=list)
    countries: List[str] = field(default_factory=list)
    country_corridor: Optional[Dict[str, str]] = None
    has_document: bool = False
    has_headline: bool = False
    has_comparison: bool = False
    has_process: bool = False
    has_construction: bool = False
    has_kinetic_moment: bool = False
    has_collage_content: bool = False
    persons: List[str] = field(default_factory=list)
    historical_figures: List[str] = field(default_factory=list)
    recommended_scene_type: str = VisualSceneType.SMART_IMAGE.value
    recommended_variant: str = "full_bleed_cinematic"


def analyze_sentence_triggers(text: str, is_first_scene: bool = False, context_text: str = "") -> SemanticTriggers:
    """Analyze a single narration sentence to extract entities and recommend scene family."""
    raw = str(text or "").strip()
    ctx = str(context_text or "").strip()

    # Suppress meaningless single-scale-word fragments
    _stripped = re.sub(r'[^a-zA-Z0-9\s]', '', text or '').strip().lower()
    _scale_only = re.fullmatch(r'(million|billion|trillion|thousand|hundred|percent)s?(?:\s+\w+){0,4}', _stripped)
    if _scale_only and not re.search(r'\d', text or ''):
        # This fragment has no numeric anchor — it's a leftover from a split compound number
        return {'type': 'smartimage', 'variant': 'full_bleed_cinematic', 'triggers': [], 'confidence': 0.1, 'is_scale_fragment': True}

    numbers = [m.group(0).strip() for m in _NUMBER_PATTERNS.finditer(raw)]
    dates = [m.group(0).strip() for m in _DATE_PATTERNS.finditer(raw)]
    if not dates and ctx:
        dates = [m.group(0).strip() for m in _DATE_PATTERNS.finditer(ctx)]

    quotes = [g for m in _QUOTE_PATTERNS.finditer(raw) for g in m.groups() if g]
    if not quotes and ctx:
        quotes = [g for m in _QUOTE_PATTERNS.finditer(ctx) for g in m.groups() if g]

    loc_matches = []
    for m in _LOCATION_PATTERNS.finditer(raw):
        for g in m.groups():
            if g:
                loc_matches.append(g.strip())
    for m in _EXPLICIT_LOCATIONS.finditer(raw):
        loc_matches.append(m.group(0).strip())
    raw_has_location = bool(loc_matches or _COUNTRY_PATTERN.search(raw))
    if raw_has_location and len(loc_matches) < 2 and ctx:
        for m in _LOCATION_PATTERNS.finditer(ctx):
            for g in m.groups():
                if g:
                    loc_matches.append(g.strip())
        for m in _EXPLICIT_LOCATIONS.finditer(ctx):
            loc_matches.append(m.group(0).strip())
    locations = list(dict.fromkeys(loc_matches))

    # Detect country sovereign entities
    detected_countries = []
    search_target = f"{raw} {ctx}".strip() if (ctx and raw_has_location) else raw
    for m in _COUNTRY_PATTERN.finditer(search_target):
        matched_str = m.group(0).lower()
        code = COUNTRY_ENTITY_MAP.get(matched_str)
        if code and code not in detected_countries:
            detected_countries.append(code)

    # Detect country corridors
    corridor_dict = None
    corridor_match = _CORRIDOR_PATTERN.search(raw) or (_CORRIDOR_PATTERN.search(ctx) if (ctx and raw_has_location) else None)
    # ``between X and Y`` also describes borders and comparisons. Do not turn
    # those relationships into a route arc unless the beat names transport,
    # movement, a corridor, or an actual connecting path.
    between_match = bool(corridor_match and (corridor_match.group(3) or corridor_match.group(4)))
    route_context = bool(re.search(
        r"\b(?:route|corridor|road|rail(?:way)?|train|flight|airlift|journey|travel(?:ed|ling)?|"
        r"move(?:d|ment)?|flew|sailed|voyage|transit|transport|shipping|path|connect(?:ed|ing|s)?|"
        r"link(?:ed|ing|s)?|carry|carried|deliver(?:ed|y)|depart(?:ed|ure)|arriv(?:ed|al)|fled|escape(?:d)?)\b",
        raw,
        re.I,
    ))
    if corridor_match and (not between_match or route_context):
        groups = [g for g in corridor_match.groups() if g]
        if len(groups) >= 2:
            orig = groups[0].strip().lower()
            dest = groups[1].strip().lower()
            code_orig = COUNTRY_ENTITY_MAP.get(orig)
            if not code_orig:
                for k, code in sorted(COUNTRY_ENTITY_MAP.items(), key=lambda x: len(x[0]), reverse=True):
                    if re.search(rf"\b{re.escape(k)}\b", orig):
                        code_orig = code
                        break
            code_dest = COUNTRY_ENTITY_MAP.get(dest)
            if not code_dest:
                for k, code in sorted(COUNTRY_ENTITY_MAP.items(), key=lambda x: len(x[0]), reverse=True):
                    if re.search(rf"\b{re.escape(k)}\b", dest):
                        code_dest = code
                        break
            if code_orig and code_dest and code_orig != code_dest:
                corridor_dict = {"from": code_orig, "to": code_dest}

    persons = [m.group(1).strip() for m in _PERSON_HONORIFICS.finditer(raw)]
    historical_figures = [m.group(1).strip() for m in _HISTORICAL_FIGURE_PATTERNS.finditer(raw)]
    if not persons and ctx:
        persons = [m.group(1).strip() for m in _PERSON_HONORIFICS.finditer(ctx)]
    if not historical_figures and ctx:
        historical_figures = [m.group(1).strip() for m in _HISTORICAL_FIGURE_PATTERNS.finditer(ctx)]
    # Merge: historical figures that aren't already in persons
    all_persons = list(persons)
    for hf in historical_figures:
        if hf not in all_persons:
            all_persons.append(hf)

    has_doc = bool(_DOCUMENT_KEYWORDS.search(raw) or (_DOCUMENT_KEYWORDS.search(ctx) if ctx else False))
    has_head = bool(_HEADLINE_EXPANDED.search(raw))
    has_comp = bool(_COMPARISON_KEYWORDS.search(raw))
    has_proc = bool(_PROCESS_KEYWORDS.search(raw))
    has_construction = bool(_CONSTRUCTION_KEYWORDS.search(raw))
    has_kinetic = bool(_KINETIC_TEXT_TRIGGERS.search(raw))
    has_collage = bool(_COLLAGE_KEYWORDS.search(raw))

    # -------------------------------------------------------------------
    # Content-Aware Scene Selection (Rich Editing Mode)
    # Every scene gets the BEST-FIT motion graphic from 15 systems.
    # Plain B-roll is only used when no meaningful semantic triggers exist.
    # -------------------------------------------------------------------

    is_location_meaningful = bool(
        locations and (
            re.search(r"\b(streets of|across the globe|state of|traveling to|coast of|corridor|coastline|radar|territory|geography|setting of|located in|situated|ruins of|walls of|gates of|heart of|city of|port of|harbor of|shores of)\b", raw, re.I)
            or (
                len(locations) >= 2
                and re.search(r"\b(from|to|between|connecting|across|travel|traveled|moved|journey|route|voyage|departed|arrived|fled)\b", raw, re.I)
            )
        )
    )

    is_country_meaningful = bool(
        corridor_dict
        or len(detected_countries) >= 2
        or (detected_countries and re.search(r"\b(nation|country|sovereign|territory|across|state(?: of)?|capital|landmass|geopolitics|radar|hub|borders?|outline|map|mapping|geography|empire|kingdom|province|colony|dominion)\b", raw, re.I))
    )

    # A year is chronology, not a measurement.  Keep it available to the
    # timeline selector, but never let it become a chart bar or KPI.
    date_numeric_tokens = {
        token
        for match in _DATE_PATTERNS.finditer(f"{raw} {ctx}" if ctx else raw)
        for token in re.findall(r"\d+(?:[.,]\d+)?", match.group(0))
    }
    data_numbers = [
        value for value in numbers
        if str(value).strip().replace(',', '') not in {token.replace(',', '') for token in date_numeric_tokens}
        and not re.fullmatch(r"(?:18|19|20)\d{2}", str(value).strip())
    ]

    # Context multi-metric extraction:
    context_data_numbers: List[str] = []
    if ctx:
        ctx_numbers = [m.group(0).strip() for m in _NUMBER_PATTERNS.finditer(ctx)]
        context_data_numbers = [
            value for value in ctx_numbers
            if str(value).strip().replace(',', '') not in {token.replace(',', '') for token in date_numeric_tokens}
            and not re.fullmatch(r"(?:18|19|20)\d{2}", str(value).strip())
        ]

    # A chart needs a narrated relationship between at least two grounded
    # values.  A lone number is a stat card; a lone year is neither.
    is_relational_data = bool(
        len(data_numbers) >= 2
        and (
            re.search(r"\b(record|budget|views|billion|million|trillion|percent|%|valuation|shattered|sales|revenue|cost|forecast|stat|ranked|growth|capacity|quota|jumped|share|breakdown|allocation|peak"
                      r"|built|lasted|killed|survived|destroyed|height|length|weight|population|capacity|seated|held|housed|carried|produced|harvested|mined|extracted|manufactured"
                      r"|casualties|victims|deaths|wounded|prisoners|soldiers|troops|warriors|gladiators|spectators|citizens|inhabitants|settlers|slaves"
                      r"|tons|meters|feet|miles|kilometers|acres|hectares|years|centuries|decades|generations)\b", raw, re.I)
            or re.search(r"\b(versus|vs|compared to|comparing|investment trajectories|divergence|breakdown of|allocation of|before and after|jumped from)\b", raw, re.I)
        )
    )

    # Multi-metric sentence boost: if context has >= 2 metrics and this scene carries one of the metrics or key nouns
    if not is_relational_data and len(context_data_numbers) >= 2 and (data_numbers or re.search(r"\b(alarms?|combinations?|cameras?|detectors?|sensors?|layers?|vault|security|breach|dollars?)\b", raw, re.I)):
        is_relational_data = True
        numbers = list(dict.fromkeys(numbers + context_data_numbers))
        data_numbers = list(dict.fromkeys(data_numbers + context_data_numbers))

    is_factual_data = is_relational_data

    # Is there a strong single stat that deserves its own hero counter?  The
    # noun/verb check covers factual prose such as “held 50,000 spectators”
    # without promoting historical years or arbitrary numbers.
    is_single_stat = bool(
        len(data_numbers) == 1
        and re.search(r"\b(only|exactly|approximately|nearly|over|more than|less than|up to|at least|a total of|a mere|an estimated|a staggering|a record|an unprecedented|held|seated|capacity|height|length|width|depth|diameter|weight|population|casualties|victims|deaths|survived|built|lasted|cost|budget|revenue|sold|produced|carried)\b", raw, re.I)
    )

    # Does the scene describe a process, system, or tactical/construction sequence?
    is_diagram_worthy = bool(
        (has_proc and has_construction)
        or (has_construction and re.search(r"\b(how|step|phase|stage|first|then|next|finally|series of|sequence of|involved|required|system|mechanism|process)\b", raw, re.I))
        or re.search(r"\b(step by step|phase by phase|layer by layer|brick by brick|stage by stage|one by one)\b", raw, re.I)
    )

    # Does the scene have chronological progression worthy of a timeline?
    is_timeline_worthy = bool(
        not data_numbers
        and (
            (dates and len(dates) >= 2)
            or (dates and re.search(r"\b(after|before|following|eventually|by the time|years later|decades after|centuries later|soon after|in the years that followed|over the next|leading up to|prior to|during this period|throughout|spanning)\b", raw, re.I))
            or (dates and re.search(r"\b(on|in|dated|born|died|departed|arrived|occurred|signed|launched|built|completed|struck|sank|began|ended)\b", raw, re.I))
        )
    )

    # Does the scene introduce a key person worth profiling?
    is_person_worthy = bool(
        all_persons
        and re.search(r"\b(who|known as|called|named|born|died|became|rose to|appointed|elected|crowned|famous for|responsible for|credited with|remembered|legendary|notorious|celebrated|feared)\b", raw, re.I)
    )

    # Scene type selection — ordered by editorial impact (highest to lowest)
    if is_first_scene and (is_country_meaningful or is_location_meaningful):
        # A strong opening should serve the narrated geography when the script
        # explicitly calls for a map; a generic chapter slate would hide it.
        rec_type = VisualSceneType.ANIMATED_MAP.value
        rec_variant = "country_spotlight" if detected_countries else "route_flight_arc"
    elif is_first_scene:
        rec_type = VisualSceneType.CHAPTER_TITLE.value
        rec_variant = "bold_editorial_kicker"
    elif quotes:
        rec_type = VisualSceneType.QUOTE_CARD.value
        rec_variant = "editorial_large_quote"
    elif is_single_stat and not is_timeline_worthy:
        rec_type = VisualSceneType.STAT_CARD.value
        if "%" in data_numbers[0] or "percent" in raw.lower():
            rec_variant = "percentage_ring"
        elif re.search(r"\b(growth|increase|rise|surge|jump)\b", raw, re.I):
            rec_variant = "delta_growth_badge"
        else:
            rec_variant = "hero_counter_large"
    elif is_factual_data and not is_timeline_worthy:
        rec_type = VisualSceneType.CHART.value
        # Select variety of graph based on narrative nuance (specific to broad precedence)
        if re.search(r"\b(jumped from|from \d+ (?:million|billion|k|m|%)? to \d+|increased by \d+%|growth of \d+%|before.*today|before and after)\b", raw, re.I):
            rec_variant = "before_after_delta"
        elif re.search(r"\b(breakdown|distributed across|share of the market|allocation|concentration in|market concentration|pie|split between)\b", raw, re.I):
            rec_variant = "donut_distribution"
        elif re.search(r"\b(versus|vs|compared to|comparing|trajectories|divergence|both nations|competitor|actual vs projected|leader vs)\b", raw, re.I):
            rec_variant = "multi_line_trend"
        elif re.search(r"\b(reached \d+%|\d+\s*percent capacity|\bcapacity saturation\b|\bquota\b|\bpenetration\b|threshold of \d+%)\b", raw, re.I):
            rec_variant = "radial_gauge"
        elif re.search(r"\b(exponential|surged to|soared|volume expansion|cumulative|market cap reached|total reached|skyrocketed)\b", raw, re.I):
            rec_variant = "area_growth_fill"
        elif re.search(r"\b(record|broken|shattered|ranked|rankings|top \d|leaderboard|surpassed)\b", raw, re.I):
            rec_variant = "ranked_horizontal_bars"
        elif re.search(r"\b(forecast|growth|trend|project|over time|trajectory|decade)\b", raw, re.I):
            rec_variant = "trend_line_with_markers"
        else:
            rec_variant = "editorial_bar"
    elif is_timeline_worthy:
        rec_type = VisualSceneType.TIMELINE.value
        if len(dates) >= 3:
            rec_variant = "horizontal_milestone_trail"
        elif re.search(r"\b(era|age|period|epoch|century|centuries)\b", raw, re.I):
            rec_variant = "era_bracket_card"
        elif re.search(r"\b(calendar|day|date|month|year)\b", raw, re.I):
            rec_variant = "date_flip_calendar"
        else:
            rec_variant = "vertical_chronology"
    elif is_person_worthy:
        rec_type = VisualSceneType.PERSON_PROFILE.value
        if re.search(r"\b(criminal|suspect|accused|fugitive|wanted|arrested|convicted|investigation)\b", raw, re.I):
            rec_variant = "minimal_profile_split"
        elif re.search(r"\b(ancient|emperor|pharaoh|king|queen|ruler|historical|medieval|dynasty)\b", raw, re.I):
            rec_variant = "portrait_full_bleed"
        else:
            rec_variant = "cinematic_lower_third_card"
    elif is_country_meaningful:
        rec_type = VisualSceneType.ANIMATED_MAP.value
        if corridor_dict:
            rec_variant = "country_corridor"
        elif len(detected_countries) >= 2:
            rec_variant = "global_network"
        else:
            rec_variant = "country_spotlight"
    elif is_location_meaningful:
        rec_type = VisualSceneType.ANIMATED_MAP.value
        rec_variant = "route_flight_arc"
    elif is_diagram_worthy:
        rec_type = VisualSceneType.DIAGRAM.value
        if re.search(r"\b(cause|effect|result|consequence|led to|triggered)\b", raw, re.I):
            rec_variant = "cause_and_effect_flow"
        elif re.search(r"\b(step|phase|stage|sequence)\b", raw, re.I):
            rec_variant = "step_by_step_process"
        elif re.search(r"\b(network|connected|linked|web of|chain of)\b", raw, re.I):
            rec_variant = "network_node_connect"
        else:
            rec_variant = "hierarchical_system_tree"
    elif has_comp:
        rec_type = VisualSceneType.COMPARISON.value
        if re.search(r"\b(before.*after|then.*now|old.*new|past.*present)\b", raw, re.I):
            rec_variant = "split_slider_before_after"
        elif re.search(r"\b(versus|vs|against|clashed|fight|battle|contest)\b", raw, re.I):
            rec_variant = "versus_contrast_clash"
        else:
            rec_variant = "side_by_side_cards"
    elif has_doc and re.search(r"\b(classified|declassified|leaked|court records?|investigative memo|sealed|confidential|top secret|restricted|forbidden|censored|audit|memo|document|report|contract|records?|ledger|evidence|revealed|concealed|liabilities)\b", raw, re.I):
        rec_type = VisualSceneType.DOCUMENT_HIGHLIGHT.value
        rec_variant = "yellow_marker_highlight"
    elif has_head and re.search(r"\b(shocked|stunned|announced|declared|broke|revealed|exposed|uncovered|headline|front page|breaking)\b", raw, re.I):
        rec_type = VisualSceneType.HEADLINE.value
        if re.search(r"\b(newspaper|paper|print|press|front page)\b", raw, re.I):
            rec_variant = "newspaper_front_page"
        elif re.search(r"\b(digital|online|website|article)\b", raw, re.I):
            rec_variant = "digital_article_crop"
        else:
            rec_variant = "floating_press_clipping"
    elif has_kinetic:
        rec_type = VisualSceneType.KINETIC_TEXT.value
        if re.search(r"\b(the most|the largest|the greatest|the first|the last|the only|the ultimate)\b", raw, re.I):
            rec_variant = "bold_minimal_center"
        elif re.search(r"\b(changed|altered|rewrote|never the same|turned the tide)\b", raw, re.I):
            rec_variant = "word_by_word_punch"
        else:
            rec_variant = "staggered_lines"
    elif has_collage and re.search(r"\b(multiple|many|several|various|dozens|hundreds|series|collection|array|assortment)\b", raw, re.I):
        rec_type = VisualSceneType.COLLAGE.value
        if re.search(r"\b(evidence|investigation|crime|case|suspect|clue)\b", raw, re.I):
            rec_variant = "evidence_pinboard"
        elif re.search(r"\b(photograph|photo|picture|image|portrait)\b", raw, re.I):
            rec_variant = "contact_sheet_grid"
        else:
            rec_variant = "three_panel_asymmetric"
    elif has_construction and not is_diagram_worthy and is_single_stat:
        # Architecture mentions without a grounded measurement stay
        # cinematic; never manufacture a KPI just to increase graphic density.
        rec_type = VisualSceneType.STAT_CARD.value
        rec_variant = "stat_with_context"
    else:
        # Natural cinematic breathing room: Let real footage and imagery shine
        rec_type = VisualSceneType.SMART_IMAGE.value
        rec_variant = "full_bleed_cinematic"

    return SemanticTriggers(
        numbers=numbers,
        dates=dates,
        quotes=quotes,
        locations=locations,
        countries=detected_countries,
        country_corridor=corridor_dict,
        has_document=has_doc,
        has_headline=has_head,
        has_comparison=has_comp,
        has_process=has_proc,
        has_construction=has_construction,
        has_kinetic_moment=has_kinetic,
        has_collage_content=has_collage,
        persons=all_persons,
        historical_figures=historical_figures,
        recommended_scene_type=rec_type,
        recommended_variant=rec_variant,
    )


def compute_beat_pacing(text: str, index: int, total_scenes: int) -> PacingBeat:
    """Compute importance, emotion, energy, and complexity scores (0.0 to 1.0)."""
    raw = text.lower()

    # Energy keywords
    high_energy = {"shock", "exploded", "collapsed", "war", "crisis", "fled", "suddenly", "chaos", "panic", "emergency"}
    # Emotion keywords
    high_emotion = {"tragic", "grief", "death", "survivors", "victims", "family", "mourning", "loss", "tears", "hope"}
    # Complexity keywords
    high_complexity = {"mechanism", "regulation", "financial", "derivatives", "partnership", "offshore", "structure", "technical"}

    energy_score = 0.5 + 0.3 * any(w in raw for w in high_energy)
    emotion_score = 0.5 + 0.35 * any(w in raw for w in high_emotion)
    complexity_score = 0.4 + 0.35 * any(w in raw for w in high_complexity)

    # First and last scenes have elevated importance
    if index == 0 or index == total_scenes - 1:
        importance_score = 0.95
    else:
        importance_score = 0.6 + 0.25 * bool(_NUMBER_PATTERNS.search(raw) or _QUOTE_PATTERNS.search(raw))

    return PacingBeat(
        importance=round(max(0.0, min(1.0, float(importance_score))), 2),
        emotion=round(max(0.0, min(1.0, float(emotion_score))), 2),
        energy=round(max(0.0, min(1.0, float(energy_score))), 2),
        complexity=round(max(0.0, min(1.0, float(complexity_score))), 2),
    )


_DANGLING_WORDS = {
    "at", "in", "to", "of", "on", "for", "from", "with", "by", "the", "a", "an",
    "and", "or", "so", "this", "that", "as", "is", "was", "were", "be", "followed",
    "took", "place", "releva", "relevant", "such", "into", "onto", "about", "over",
    "should", "use", "using", "scene", "visual", "photograph", "photo", "couple",
    "monarch", "became", "becoming"
}


def extract_documentary_highlight(text: str, max_words: int = 4) -> str:
    """Extract a minimal, punchy documentary title/label without trailing prepositions or filler."""
    if not text:
        return ""
    raw = str(text).strip().split("\n")[0].split(".")[0].strip()
    words = [w.strip(" \t\r\n\"'“”(),;:") for w in raw.split() if w.strip()]
    if len(words) > max_words:
        words = words[:max_words]
    while len(words) > 1 and words[-1].lower() in _DANGLING_WORDS:
        words.pop()
    return " ".join(words).strip(" ,;:-\"'\t\r\n")
