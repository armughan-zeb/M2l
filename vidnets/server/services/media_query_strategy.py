"""Scene-local retrieval facets, with distinct shots and identity-safe recall."""
import re
import os
import math
from collections.abc import Mapping

_STOP = set('the a an and or for with from into about that this these those their them his her its they he she it was were is are been being have has had will would could should then than when where which who how why before after during because also very more most much only just still even through became become changed change transformed forever world story history documentary video footage scene years year months days seconds minutes time today once first last one two three'.split())
_ABSTRACT = set('idea ideas mission purpose simple familiar useful ordinary extraordinary journey journeys success legacy represents represented symbolize symbolized imagine believe'.split())
_STOP.update('few fewer fewest several many can may might must anyone anything something nothing everything'.split())
_STOP.update('over under four five six seven eight nine ten hundred thousand million billion'.split())
_STOP.update('some companies company firms firm brands brand british royal warrants warrant monarch business businesses'.split())
_VISIBLE = ('touchscreen', 'dashboard', 'charger', 'battery', 'batteries', 'storage', 'screen', 'interface',
    'engine', 'suspension', 'cabin', 'interior', 'exterior', 'wheels', 'factory',
    'driving', 'sprint', 'sprinting', 'running', 'charging', 'recharging', 'solar', 'panels',
    'race', 'stadium', 'field', 'courtroom', 'police', 'rubble', 'rescue', 'street',
    'bridge', 'harbor', 'ship', 'airport', 'aircraft', 'laboratory', 'rocket', 'portrait', 'document',
    'carriage', 'carriages', 'cars', 'chapel', 'insignia', 'procession', 'throne', 'banquet', 'dining', 'regalia')


def supported_specific_topic(title, narration, broad_subject):
    """Narrow a broad royal pool to a short, explicitly narrated title topic.

    This is a search hint only, never a replacement scene identity or evidence.
    Marketing predicates and unsupported title fragments cannot become anchors.
    """
    if str(broad_subject or '').strip().casefold() not in {
        'british royal family', 'royal family', 'british monarchy', 'monarchy',
    }:
        return ''
    topic = re.sub(r'\s+', ' ', str(title or '').split(':', 1)[0]).strip()
    words = re.findall(r'[\w-]+', topic)
    if not 2 <= len(words) <= 8 or re.search(r'\b(?:why|how|what|everyone|missed)\b', topic, re.I):
        return ''
    if topic.casefold() == str(broad_subject).strip().casefold():
        return ''
    # Allow the optional leading article, preserving the original Unicode
    # name in the actual query. Require contiguous source support.
    phrase = re.sub(r'^(?:the|a|an)\s+', '', topic, flags=re.I)
    normalized_source = re.sub(r'\s+', ' ', str(narration or ''))
    if len([word for word in words if word.casefold() not in _STOP]) < 2:
        return ''
    return topic if re.search(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', normalized_source, re.I) else ''


def grounded_institutional_focus(planned, narration):
    """Keep a director's named company/object when this beat actually names it."""
    if not isinstance(planned, Mapping) or not planned.get('_llm'):
        return ''
    subject = re.sub(r"['’]s$", '', str(planned.get('section_subject') or '').strip())
    generic = _STOP | _ABSTRACT | set('holder holders list association system seal supplier suppliers supply standard standards grantor quality certificate bureaucracy bureaucratic machinery government household'.split())
    for part in re.split(r'\s+(?:and|&)\s+', subject):
        words = re.findall(r'[\w-]+', part)
        if not words or len(words) > 5 or all(word.casefold() in generic for word in words):
            continue
        if any(word.casefold() in {'royal', 'warrant', 'warrants'} for word in words):
            continue
        match = re.search(r'(?<!\w)' + re.escape(part) + r'(?!\w)', str(narration or ''), re.I)
        if not match:
            continue
        # A director can title-case an ordinary noun such as "Commerce".
        # Capitalization evidence must come from the actual narration.
        spoken_words = re.findall(r'[\w-]+', match.group(0))
        if not any(word[0].isupper() for word in spoken_words):
            continue
        return match.group(0)
    return ''


def query_planning_budget(chunk_count, worker_count):
    """Scale planning time with parallel waves, without imposing a film limit."""
    try:
        per_wave = float(os.environ.get('VIDZEO_QUERY_PLAN_WAVE_SECONDS', '120') or 120)
        if not math.isfinite(per_wave):
            per_wave = 120.0
    except (TypeError, ValueError):
        per_wave = 120.0
    per_wave = max(45.0, min(300.0, per_wave))
    waves = max(1, math.ceil(max(1, chunk_count) / max(1, worker_count)))
    return waves * per_wave


def enrich_scene_queries(planned, requirement, narration, *, provider, focus_terms=(), preserve_planned_order=False):
    req = requirement if isinstance(requirement, Mapping) else {}
    anchor = str(req.get('canonical_entity') or '').strip()
    if not anchor:
        return list(dict.fromkeys(str(value or '').strip() for value in planned if str(value or '').strip()))
    text = re.sub(r'\s+', ' ', str(narration or '')).strip()
    anchor_tokens = set(re.findall(r'[\w]+', anchor.casefold()))
    facets = []
    # Model-selected facets are permitted only when backed by this scene.
    if isinstance(focus_terms, str):
        focus_terms = [focus_terms]
    for value in focus_terms or ():
        phrase = str(value or '').strip()
        if phrase and phrase.casefold() in text.casefold() and phrase.casefold() not in anchor.casefold():
            facets.append(phrase)
    # Prefer objects/actions a camera can show over abstract narration glue.
    # Every added word must occur in this scene; the LLM's specific queries
    # remain ahead of this grounded mechanical recall supplement.
    for word in _VISIBLE:
        if (re.search(r'\b' + re.escape(word) + r'\b', text, re.I)
                and word not in anchor_tokens and word not in {x.casefold() for x in facets}):
            facets.append(word)
    named_facets = [word for word in re.findall(r'\b[A-Z][A-Za-z0-9-]{2,}\b', text)
                    if word.casefold() not in anchor_tokens | _STOP
                    and word not in {'Inside', 'Outside', 'Larger', 'Smaller', 'However', 'Meanwhile'}]
    for word in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", text):
        if word.casefold() not in _STOP | _ABSTRACT | anchor_tokens and word.casefold() not in {x.casefold() for x in facets}:
            facets.append(word)
    local = ' '.join(facets[:3])
    attributes = req.get('required_attributes') or {}
    year = str(attributes.get('year') or '') if isinstance(attributes, Mapping) else ''
    suffix = 'footage' if provider == 'youtube' else 'photo'
    specific = ' '.join(part for part in (anchor, local, year, suffix) if part)
    candidates = [str(value or '').strip() for value in planned if str(value or '').strip()]
    # Strong LLM queries stay first; repeated topic-only queries gain a local facet.
    visible_facets = [facet for facet in facets if facet.casefold() in _VISIBLE] + named_facets
    concrete = [value for value in candidates if any(facet.casefold() in value.casefold() for facet in visible_facets)]
    useful = [value for value in candidates if any(facet.casefold() in value.casefold() for facet in facets[:5])]
    ordered = (concrete + ([specific] if local else []) + useful if visible_facets
               else useful + ([specific] if local else []))
    ordered += candidates + [f'{anchor} {year} {suffix}'.replace('  ', ' ')]
    if preserve_planned_order:
        # An explicit director query already names a visible shot. Narrative
        # filler such as "the article describes" must not outrank that query.
        ordered = candidates + ordered
    if (req.get('subject_context') or {}).get('scene_local'):
        # A stale global-title query must not consume the local subject's
        # download budget. Keep grounded identity searches at the front.
        from .visual_requirement import provider_query_variants
        identities = [anchor, *(req.get('aliases') or ())]
        anchored = [value for value in ordered if any(
            str(identity).casefold() in value.casefold() for identity in identities if identity)]
        ordered = anchored + provider_query_variants(req, provider=provider)
        subject_context = req.get('subject_context') or {}
        if subject_context.get('directed_type') in {'component', 'object', 'activity', 'process', 'mechanism'}:
            film = str(subject_context.get('film_subject') or '').strip()
            target = str(subject_context.get('visible_target') or '').strip()
            if subject_context.get('identity_scope') == 'context' and target:
                # Supporting gearbox/clutch examples need the physical object,
                # not the film's camera viewpoint (e.g. "inside the cab").
                target = re.split(r'\b(?:beside|alongside|next to|versus|compared with|compared to)\b',
                                  target, maxsplit=1, flags=re.I)[0].strip()
                target = ' '.join(target.split()[:7])
                local = [re.sub(re.escape(film), '', value, flags=re.I).strip()
                         if film else value for value in ordered]
                # The planner may use an equally valid noun phrase, e.g.
                # "large industrial clutch assembly" / "large clutch".
                # Do not lose that stronger query to contiguous-name pruning.
                anchor_words = set(re.findall(r'\w+', anchor.casefold())) - _STOP
                target_words = set(re.findall(r'\w+', target.casefold())) - anchor_words - _STOP
                preferred = []
                for value in candidates:
                    words = set(re.findall(r'\w+', value.casefold()))
                    if not (words & anchor_words):
                        continue
                    if film and film.casefold() in value.casefold() and not (words & target_words):
                        continue
                    value = re.sub(re.escape(film), '', value, flags=re.I).strip() if film else value
                    if value.casefold().startswith(anchor.casefold() + ' '):
                        rest = value[len(anchor):].strip()
                        if len(rest.split()) >= 3 and set(re.findall(r'\w+', rest.casefold())) & anchor_words:
                            value = rest
                    preferred.append(value)
                ordered = preferred + [f'{anchor} {target} {suffix}'] + [
                    value if target.casefold() in value.casefold() else f'{target} {value}'
                    for value in local]
            elif film and film.casefold() != anchor.casefold():
                # Keep every recovery variant in the same physical context.
                # A bare "clutch" query can mean eggs rather than machinery.
                ordered = [f'{film} {anchor} {suffix}'] + [
                    value if film.casefold() in value.casefold() else f'{film} {value}'
                    for value in ordered]
    if req.get('subject_type') == 'organization' and req.get('aliases'):
        # Preserve short names of the same institutional artifact before
        # narration-derived phrases fill the eight-query window. The full
        # canonical contract remains mandatory at the final visual gate.
        aliases = [re.sub(r'\s+', ' ', str(alias or '')).strip().casefold()
                   for alias in req['aliases'] if str(alias or '').strip()]
        short_recalls = [value for value in candidates
                         if anchor.casefold() not in value.casefold()
                         and any((' ' + alias + ' ') in (' ' + value.casefold() + ' ')
                                 for alias in aliases)]
        # Concrete narrated names/objects get a scene-specific short alias
        # search first. Sending certificate/seal/holders first for all 123
        # beats exhausted one tiny shared pool instead of finding new shots.
        artifact_facets = [facet for facet in visible_facets
                           if facet.casefold() not in _STOP and facet.casefold() not in _ABSTRACT]
        focused_recalls = []
        if artifact_facets:
            alias = next((str(value).strip() for value in req['aliases'] if str(value).strip()), anchor)
            focused_recalls = [f'{alias} {" ".join(artifact_facets[:3])} {suffix}']
        ordered = focused_recalls + concrete + short_recalls[:3] + ordered
    output = []
    seen = set()
    for value in ordered:
        value = re.sub(r'\s+', ' ', value).strip()
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            output.append(value)
    return output[:8]


def recovery_web_query_pack(original, requirement, narration):
    """Keep the director's concrete shot queries through terminal rescue.

    Container identity remains immutable; it qualifies a query rather than
    replacing the narrated object with a broad brand photograph search.
    """
    from .visual_requirement import provider_query_variants
    row = original if isinstance(original, Mapping) else {}
    req = requirement if isinstance(requirement, Mapping) else {}
    anchor = str(req.get('canonical_entity') or '').strip()
    planned = []
    if row.get('_llm') or row.get('director_web_query') or row.get('_semantic_locked'):
        for key in ('search_query', 'supporting_search_query', 'alt_search_query', 'fallback_query'):
            value = str(row.get(key) or '').strip()
            if value:
                planned.append(value)
        for key in ('web_query_variants', 'query_variants'):
            values = row.get(key)
            if isinstance(values, (list, tuple)):
                planned.extend(str(value or '').strip() for value in values if str(value or '').strip())
    anchored = [value if not anchor or anchor.casefold() in value.casefold()
                else f'{anchor} {value}' for value in planned]
    variants = enrich_scene_queries(anchored + provider_query_variants(req, provider='web'),
                                   req, narration, provider='web', focus_terms=row.get('scene_focus_terms') or (),
                                   preserve_planned_order=bool(row.get('_llm') or row.get('director_web_query')))
    return {'search_query': variants[0] if variants else anchor,
            'supporting_search_query': variants[min(1, len(variants) - 1)] if variants else anchor,
            'alt_search_query': variants[min(2, len(variants) - 1)] if variants else anchor,
            'web_query_variants': variants, 'query_variants': variants,
            'director_web_query': bool(planned)}
