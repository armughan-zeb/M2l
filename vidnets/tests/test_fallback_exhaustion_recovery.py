"""Fallbacks + exhaustion: closest verified media before any graphic.

Regression cases from GTA VI proof job a57cb5c7 (media_plan.json,
checkpoints/media_history). The job finished 6/8 real; scenes 2 and 6
went to grounded graphics with ``all_verified_real_media_exhausted`` even
though verified media from the same job sat unused:

* Scene 2 (Leonida Keys): the pre-stock web rescue picked scene 1's own
  still (``scene_0002.jpg``, score 55.0) over scene 1's unused alternates
  (scores 0.89 / 0.42 / 0.25). The still was scene 1's selection, adjacent
  to scene 2, so the final gate (``excess_media_reuse_indices``) rejected
  the repeat and the scene fell to a graphic. The YouTube frame pool ran
  and correctly returned only GTA V frames, which the hard gate rejected
  for a Leonida scene (GTA V is wrong for GTA VI-content scenes).
* Scene 6 (GTA VI launch platforms): the frame fallback "accepted" a
  Rockstar-logo frame with a deterministic compare verdict -- no vision,
  no bound pixel review -- so the final gate rejected it with
  ``visual_verification_pending``. The YouTube frame pool never ran (the
  scene counted as "accepted"), and the pre-stock rescue only covered
  neutral/graphic scenes, so nothing ever tried the job's unused Vice City
  alternates. Its own web candidates were the key-art poster already used
  twice (cap reached).

Scene texts, requirement blocks, candidate titles/URLs/scores below are
copied verbatim from the proof job's media_plan.json; the image files are
small generated stand-ins with the same distinctness relations (the
selected still, three alternates, and the poster bytes shared under two
paths).
"""

import copy
import hashlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services.media_resolution_coordinator import (
    _normalize_rescue_score,
    _rescue_general_media_eligible,
    resolve_storyboard_media_v2,
)
from services.editorial_director.production_contract import repair_media_defects

CONTAINER = "ctr_a998c3955fcac86a9a3d"

# Real visual_requirement blocks, copied verbatim from the proof job's final
# media_plan.json (proof scenes 1, 2, 3, 6, 7), keyed by requirement_hash.
# The full blocks matter: positive_evidence / domain_evidence are what make
# a metadata mismatch "pending visual" for the hard gate instead of a
# confirmed rejection.
_REAL_REQUIREMENTS = {   '2e9f05f5837a0507162f7e2f1f94604dab33ef39a734bf6ca8873b002926b07d': {   'action_terms': [   'show',
                                                                                                'swamps',
                                                                                                'neon',
                                                                                                'city',
                                                                                                'detailed',
                                                                                                'built'],
                                                                            'aliases': [   ],
                                                                            'ambiguity_status': 'resolved',
                                                                            'canonical_entity': 'Leonida '
                                                                                                'Keys',
                                                                            'confidence': 0.85,
                                                                            'confusable_entities': [   ],
                                                                            'domain': 'documentary',
                                                                            'domain_evidence': [   'Grand '
                                                                                                   'Theft '
                                                                                                   'Auto '
                                                                                                   'VI',
                                                                                                   'The '
                                                                                                   'trailers '
                                                                                                   'show '
                                                                                                   'the '
                                                                                                   'Leonida '
                                                                                                   'Keys, '
                                                                                                   'the '
                                                                                                   'swamps '
                                                                                                   'of '
                                                                                                   'the '
                                                                                                   'Grassrivers, '
                                                                                                   'and '
                                                                                                   'the '
                                                                                                   'neon '
                                                                                                   'of '
                                                                                                   'Vice '
                                                                                                   'City '
                                                                                                   'itself, '
                                                                                                   'a '
                                                                                                   'world '
                                                                                                   'far '
                                                                                                   'bigger '
                                                                                                   'and '
                                                                                                   'more '
                                                                                                   'detailed '
                                                                                                   'than '
                                                                                                   'anything '
                                                                                                   'Rockstar '
                                                                                                   'has '
                                                                                                   'built '
                                                                                                   'before.'],
                                                                            'fallback_template': 'kinetic_statement',
                                                                            'forbidden_subjects': [   ],
                                                                            'positive_evidence': [   'Grand '
                                                                                                     'Theft '
                                                                                                     'Auto '
                                                                                                     'VI',
                                                                                                     'The '
                                                                                                     'trailers '
                                                                                                     'show '
                                                                                                     'the '
                                                                                                     'Leonida '
                                                                                                     'Keys, '
                                                                                                     'the '
                                                                                                     'swamps '
                                                                                                     'of '
                                                                                                     'the '
                                                                                                     'Grassrivers, '
                                                                                                     'and '
                                                                                                     'the '
                                                                                                     'neon '
                                                                                                     'of '
                                                                                                     'Vice '
                                                                                                     'City '
                                                                                                     'itself, '
                                                                                                     'a '
                                                                                                     'world '
                                                                                                     'far '
                                                                                                     'bigger '
                                                                                                     'and '
                                                                                                     'more '
                                                                                                     'detailed '
                                                                                                     'than '
                                                                                                     'anything '
                                                                                                     'Rockstar '
                                                                                                     'has '
                                                                                                     'built '
                                                                                                     'before.'],
                                                                            'required_attributes': {   },
                                                                            'required_terms': [   'Leonida '
                                                                                                  'Keys'],
                                                                            'requirement_hash': '2e9f05f5837a0507162f7e2f1f94604dab33ef39a734bf6ca8873b002926b07d',
                                                                            'schema_version': 'visual_requirement.v2',
                                                                            'source_preference': 'verified_external_or_graphic',
                                                                            'strict_identity': True,
                                                                            'subject_context': {   'directed_type': 'place',
                                                                                                   'film_subject': 'Grand '
                                                                                                                   'Theft '
                                                                                                                   'Auto '
                                                                                                                   'VI',
                                                                                                   'identity_scope': 'subject',
                                                                                                   'origin': 'grounded_director',
                                                                                                   'scene_local': True,
                                                                                                   'scene_subject': 'Leonida '
                                                                                                                    'Keys',
                                                                                                   'subject_evidence': 'The '
                                                                                                                       'trailers '
                                                                                                                       'show '
                                                                                                                       'the '
                                                                                                                       'Leonida '
                                                                                                                       'Keys',
                                                                                                   'subject_type': 'place',
                                                                                                   'visible_target': 'Tropical '
                                                                                                                     'island '
                                                                                                                     'keys '
                                                                                                                     'with '
                                                                                                                     'bridges '
                                                                                                                     'and '
                                                                                                                     'boats'},
                                                                            'subject_type': 'place',
                                                                            'visual_intent': 'documentary_context'},
    '37d444b180ec01dff745cb39716f878c8afb04dd1707d048359ead4dbf54dc10': {   'action_terms': [   'soaked',
                                                                                                'reimagining',
                                                                                                'filled',
                                                                                                'swamps',
                                                                                                'neon',
                                                                                                'streets'],
                                                                            'aliases': [   ],
                                                                            'ambiguity_status': 'resolved',
                                                                            'canonical_entity': 'Vice '
                                                                                                'City',
                                                                            'confidence': 0.85,
                                                                            'confusable_entities': [   ],
                                                                            'domain': 'documentary',
                                                                            'domain_evidence': [   'Grand '
                                                                                                   'Theft '
                                                                                                   'Auto '
                                                                                                   'VI',
                                                                                                   'The '
                                                                                                   'game '
                                                                                                   'returns '
                                                                                                   'to '
                                                                                                   'Vice '
                                                                                                   'City, '
                                                                                                   'now '
                                                                                                   'set '
                                                                                                   'in '
                                                                                                   'the '
                                                                                                   'fictional '
                                                                                                   'state '
                                                                                                   'of '
                                                                                                   'Leonida, '
                                                                                                   'a '
                                                                                                   'sun-soaked '
                                                                                                   'reimagining '
                                                                                                   'of '
                                                                                                   'Florida '
                                                                                                   'filled '
                                                                                                   'with '
                                                                                                   'beaches, '
                                                                                                   'swamps, '
                                                                                                   'and '
                                                                                                   'neon '
                                                                                                   'streets.'],
                                                                            'fallback_template': 'kinetic_statement',
                                                                            'forbidden_subjects': [   ],
                                                                            'positive_evidence': [   'Grand '
                                                                                                     'Theft '
                                                                                                     'Auto '
                                                                                                     'VI',
                                                                                                     'The '
                                                                                                     'game '
                                                                                                     'returns '
                                                                                                     'to '
                                                                                                     'Vice '
                                                                                                     'City, '
                                                                                                     'now '
                                                                                                     'set '
                                                                                                     'in '
                                                                                                     'the '
                                                                                                     'fictional '
                                                                                                     'state '
                                                                                                     'of '
                                                                                                     'Leonida, '
                                                                                                     'a '
                                                                                                     'sun-soaked '
                                                                                                     'reimagining '
                                                                                                     'of '
                                                                                                     'Florida '
                                                                                                     'filled '
                                                                                                     'with '
                                                                                                     'beaches, '
                                                                                                     'swamps, '
                                                                                                     'and '
                                                                                                     'neon '
                                                                                                     'streets.'],
                                                                            'required_attributes': {   },
                                                                            'required_terms': [   'Vice '
                                                                                                  'City'],
                                                                            'requirement_hash': '37d444b180ec01dff745cb39716f878c8afb04dd1707d048359ead4dbf54dc10',
                                                                            'schema_version': 'visual_requirement.v2',
                                                                            'source_preference': 'verified_external_or_graphic',
                                                                            'strict_identity': True,
                                                                            'subject_context': {   'directed_type': 'place',
                                                                                                   'film_subject': 'Grand '
                                                                                                                   'Theft '
                                                                                                                   'Auto '
                                                                                                                   'VI',
                                                                                                   'identity_scope': 'subject',
                                                                                                   'origin': 'grounded_director',
                                                                                                   'scene_local': True,
                                                                                                   'scene_subject': 'Vice '
                                                                                                                    'City',
                                                                                                   'subject_evidence': 'The '
                                                                                                                       'game '
                                                                                                                       'returns '
                                                                                                                       'to '
                                                                                                                       'Vice '
                                                                                                                       'City',
                                                                                                   'subject_type': 'place',
                                                                                                   'visible_target': 'Neon '
                                                                                                                     'streets, '
                                                                                                                     'beaches '
                                                                                                                     'and '
                                                                                                                     'swamps '
                                                                                                                     'of '
                                                                                                                     'Vice '
                                                                                                                     'City'},
                                                                            'subject_type': 'place',
                                                                            'visual_intent': 'documentary_context'},
    '5fafa75cc0d29fd58cf258688667c730c5c8b4071b29f90210ed0faf370e6b26': {   'action_terms': [   'announced'],
                                                                            'aliases': [   'GTA '
                                                                                           '6',
                                                                                           'GTA '
                                                                                           'VI',
                                                                                           'GTA6',
                                                                                           'Grand '
                                                                                           'Theft '
                                                                                           'Auto '
                                                                                           '6'],
                                                                            'ambiguity_status': 'resolved',
                                                                            'canonical_entity': 'Grand '
                                                                                                'Theft '
                                                                                                'Auto '
                                                                                                'VI',
                                                                            'confidence': 0.85,
                                                                            'confusable_entities': [   ],
                                                                            'domain': 'documentary',
                                                                            'domain_evidence': [   'Grand '
                                                                                                   'Theft '
                                                                                                   'Auto '
                                                                                                   'VI',
                                                                                                   'When '
                                                                                                   'it '
                                                                                                   'arrives, '
                                                                                                   'it '
                                                                                                   'launches '
                                                                                                   'first '
                                                                                                   'on '
                                                                                                   'PlayStation '
                                                                                                   '5 '
                                                                                                   'and '
                                                                                                   'Xbox '
                                                                                                   'Series '
                                                                                                   'X '
                                                                                                   'and '
                                                                                                   'S, '
                                                                                                   'with '
                                                                                                   'a '
                                                                                                   'PC '
                                                                                                   'version '
                                                                                                   'yet '
                                                                                                   'to '
                                                                                                   'be '
                                                                                                   'announced.'],
                                                                            'fallback_template': 'kinetic_statement',
                                                                            'forbidden_subjects': [   ],
                                                                            'positive_evidence': [   'Grand '
                                                                                                     'Theft '
                                                                                                     'Auto '
                                                                                                     'VI',
                                                                                                     'When '
                                                                                                     'it '
                                                                                                     'arrives, '
                                                                                                     'it '
                                                                                                     'launches '
                                                                                                     'first '
                                                                                                     'on '
                                                                                                     'PlayStation '
                                                                                                     '5 '
                                                                                                     'and '
                                                                                                     'Xbox '
                                                                                                     'Series '
                                                                                                     'X '
                                                                                                     'and '
                                                                                                     'S, '
                                                                                                     'with '
                                                                                                     'a '
                                                                                                     'PC '
                                                                                                     'version '
                                                                                                     'yet '
                                                                                                     'to '
                                                                                                     'be '
                                                                                                     'announced.'],
                                                                            'required_attributes': {   'model': 'xbox '
                                                                                                                'series'},
                                                                            'required_terms': [   'Grand '
                                                                                                  'Theft '
                                                                                                  'Auto '
                                                                                                  'VI',
                                                                                                  'xbox '
                                                                                                  'series'],
                                                                            'requirement_hash': '5fafa75cc0d29fd58cf258688667c730c5c8b4071b29f90210ed0faf370e6b26',
                                                                            'schema_version': 'visual_requirement.v2',
                                                                            'source_preference': 'verified_external_or_graphic',
                                                                            'strict_identity': True,
                                                                            'subject_context': {   'film_subject': 'Grand '
                                                                                                                   'Theft '
                                                                                                                   'Auto '
                                                                                                                   'VI',
                                                                                                   'origin': 'film_context',
                                                                                                   'scene_local': False},
                                                                            'subject_type': 'video_game',
                                                                            'visual_intent': 'documentary_context'},
    'a28853372527acebb6cb2c45e5cfdfe214256c88ffffed1d339fffc38b49ba29': {   'action_terms': [   'walking',
                                                                                                'prison',
                                                                                                'reuniting'],
                                                                            'aliases': [   ],
                                                                            'ambiguity_status': 'resolved',
                                                                            'canonical_entity': 'Lucia '
                                                                                                'Caminos '
                                                                                                'and '
                                                                                                'Jason '
                                                                                                'Duval',
                                                                            'confidence': 0.85,
                                                                            'confusable_entities': [   ],
                                                                            'domain': 'person',
                                                                            'domain_evidence': [   'Grand '
                                                                                                   'Theft '
                                                                                                   'Auto '
                                                                                                   'VI',
                                                                                                   'At '
                                                                                                   'the '
                                                                                                   'center '
                                                                                                   'of '
                                                                                                   'the '
                                                                                                   'story '
                                                                                                   'are '
                                                                                                   'Lucia '
                                                                                                   'Caminos '
                                                                                                   'and '
                                                                                                   'Jason '
                                                                                                   'Duval, '
                                                                                                   'two '
                                                                                                   'partners '
                                                                                                   'in '
                                                                                                   'crime '
                                                                                                   'whose '
                                                                                                   'story '
                                                                                                   'begins '
                                                                                                   'with '
                                                                                                   'Lucia '
                                                                                                   'walking '
                                                                                                   'out '
                                                                                                   'of '
                                                                                                   'prison '
                                                                                                   'and '
                                                                                                   'reuniting '
                                                                                                   'with '
                                                                                                   'Jason.'],
                                                                            'fallback_template': 'kinetic_statement',
                                                                            'forbidden_subjects': [   'gameplay',
                                                                                                      'walkthrough',
                                                                                                      'playthrough',
                                                                                                      'xbox '
                                                                                                      '360',
                                                                                                      'speedrun',
                                                                                                      'b-roll: '
                                                                                                      'harry '
                                                                                                      'potter'],
                                                                            'positive_evidence': [   'Grand '
                                                                                                     'Theft '
                                                                                                     'Auto '
                                                                                                     'VI',
                                                                                                     'At '
                                                                                                     'the '
                                                                                                     'center '
                                                                                                     'of '
                                                                                                     'the '
                                                                                                     'story '
                                                                                                     'are '
                                                                                                     'Lucia '
                                                                                                     'Caminos '
                                                                                                     'and '
                                                                                                     'Jason '
                                                                                                     'Duval, '
                                                                                                     'two '
                                                                                                     'partners '
                                                                                                     'in '
                                                                                                     'crime '
                                                                                                     'whose '
                                                                                                     'story '
                                                                                                     'begins '
                                                                                                     'with '
                                                                                                     'Lucia '
                                                                                                     'walking '
                                                                                                     'out '
                                                                                                     'of '
                                                                                                     'prison '
                                                                                                     'and '
                                                                                                     'reuniting '
                                                                                                     'with '
                                                                                                     'Jason.'],
                                                                            'required_attributes': {   },
                                                                            'required_terms': [   'Lucia '
                                                                                                  'Caminos '
                                                                                                  'and '
                                                                                                  'Jason '
                                                                                                  'Duval'],
                                                                            'requirement_hash': 'a28853372527acebb6cb2c45e5cfdfe214256c88ffffed1d339fffc38b49ba29',
                                                                            'schema_version': 'visual_requirement.v2',
                                                                            'source_preference': 'verified_external_or_graphic',
                                                                            'strict_identity': True,
                                                                            'subject_context': {   'directed_type': 'person',
                                                                                                   'film_subject': 'Grand '
                                                                                                                   'Theft '
                                                                                                                   'Auto '
                                                                                                                   'VI',
                                                                                                   'identity_scope': 'subject',
                                                                                                   'origin': 'grounded_director',
                                                                                                   'scene_local': True,
                                                                                                   'scene_subject': 'Lucia '
                                                                                                                    'Caminos '
                                                                                                                    'and '
                                                                                                                    'Jason '
                                                                                                                    'Duval',
                                                                                                   'subject_evidence': 'Lucia '
                                                                                                                       'walking '
                                                                                                                       'out '
                                                                                                                       'of '
                                                                                                                       'prison '
                                                                                                                       'and '
                                                                                                                       'reuniting '
                                                                                                                       'with '
                                                                                                                       'Jason',
                                                                                                   'subject_type': 'person',
                                                                                                   'visible_target': 'Lucia '
                                                                                                                     'walking '
                                                                                                                     'out '
                                                                                                                     'of '
                                                                                                                     'prison '
                                                                                                                     'toward '
                                                                                                                     'Jason'},
                                                                            'subject_type': 'person',
                                                                            'visual_intent': 'documentary_context'},
    'cef40a1aa6fe545bed2bc5cfafa77747148c5c876f7584152b530d91d644d0c5': {   'action_terms': [   ],
                                                                            'aliases': [   'GTA '
                                                                                           '6',
                                                                                           'GTA '
                                                                                           'VI',
                                                                                           'GTA6',
                                                                                           'Grand '
                                                                                           'Theft '
                                                                                           'Auto '
                                                                                           '6'],
                                                                            'ambiguity_status': 'resolved',
                                                                            'canonical_entity': 'Grand '
                                                                                                'Theft '
                                                                                                'Auto '
                                                                                                'VI',
                                                                            'confidence': 0.85,
                                                                            'confusable_entities': [   ],
                                                                            'domain': 'documentary',
                                                                            'domain_evidence': [   'Grand '
                                                                                                   'Theft '
                                                                                                   'Auto '
                                                                                                   'VI',
                                                                                                   'For '
                                                                                                   'Rockstar, '
                                                                                                   'Grand '
                                                                                                   'Theft '
                                                                                                   'Auto '
                                                                                                   'Six '
                                                                                                   'is '
                                                                                                   'not '
                                                                                                   'just '
                                                                                                   'a '
                                                                                                   'sequel, '
                                                                                                   'it '
                                                                                                   'is '
                                                                                                   'the '
                                                                                                   'next '
                                                                                                   'chapter '
                                                                                                   'of '
                                                                                                   'the '
                                                                                                   'biggest '
                                                                                                   'entertainment '
                                                                                                   'series '
                                                                                                   'in '
                                                                                                   'the '
                                                                                                   'world.'],
                                                                            'fallback_template': 'kinetic_statement',
                                                                            'forbidden_subjects': [   ],
                                                                            'positive_evidence': [   'Grand '
                                                                                                     'Theft '
                                                                                                     'Auto '
                                                                                                     'VI',
                                                                                                     'For '
                                                                                                     'Rockstar, '
                                                                                                     'Grand '
                                                                                                     'Theft '
                                                                                                     'Auto '
                                                                                                     'Six '
                                                                                                     'is '
                                                                                                     'not '
                                                                                                     'just '
                                                                                                     'a '
                                                                                                     'sequel, '
                                                                                                     'it '
                                                                                                     'is '
                                                                                                     'the '
                                                                                                     'next '
                                                                                                     'chapter '
                                                                                                     'of '
                                                                                                     'the '
                                                                                                     'biggest '
                                                                                                     'entertainment '
                                                                                                     'series '
                                                                                                     'in '
                                                                                                     'the '
                                                                                                     'world.'],
                                                                            'required_attributes': {   'model': 'entertainment '
                                                                                                                'series'},
                                                                            'required_terms': [   'Grand '
                                                                                                  'Theft '
                                                                                                  'Auto '
                                                                                                  'VI',
                                                                                                  'entertainment '
                                                                                                  'series'],
                                                                            'requirement_hash': 'cef40a1aa6fe545bed2bc5cfafa77747148c5c876f7584152b530d91d644d0c5',
                                                                            'schema_version': 'visual_requirement.v2',
                                                                            'source_preference': 'verified_external_or_graphic',
                                                                            'strict_identity': True,
                                                                            'subject_context': {   'film_subject': 'Grand '
                                                                                                                   'Theft '
                                                                                                                   'Auto '
                                                                                                                   'VI',
                                                                                                   'origin': 'film_context',
                                                                                                   'scene_local': False},
                                                                            'subject_type': 'video_game',
                                                                            'visual_intent': 'documentary_context'}}

def _requirement(canonical, subject_type, required_terms, requirement_hash):
    """Full verbatim requirement block for this hash (see _REAL_REQUIREMENTS)."""
    return copy.deepcopy(_REAL_REQUIREMENTS[requirement_hash])


def _scene(index, scene_id, text, requirement, positive_subjects):
    return {
        "scene_index": index,
        "scene_id": scene_id,
        "order": index,
        "container_id": CONTAINER,
        "text": text,
        "positive_subjects": list(positive_subjects),
        "reuse_policy": {"scope": "container", "max_uses": 2, "non_adjacent": True},
        "source_policy": {
            "primary_sources": ["youtube", "web_image"],
            "media_source_priority": ["youtube", "web", "stock"],
            "youtube_quota": True,
            "allow_verified_topic_reuse": True,
            "unique_web_assets_required": True,
            "strict_real_media": True,
        },
        "visual_requirement": requirement,
    }


# Real scene contracts from proof job a57cb5c7 (texts + requirements verbatim).
SCENE_VICE_CITY = _scene(
    0, "vice-city",
    "The game returns to Vice City, now set in the fictional state of Leonida, "
    "a sun-soaked reimagining of Florida filled with beaches, swamps, and neon streets.",
    _requirement("Vice City", "place", ["Vice City"],
                 "37d444b180ec01dff745cb39716f878c8afb04dd1707d048359ead4dbf54dc10"),
    ["Vice City"],
)
SCENE_LEONIDA = _scene(
    1, "leonida-keys",
    "The trailers show the Leonida Keys, the swamps of the Grassrivers, and "
    "the neon of Vice City itself, a world far bigger and more detailed than "
    "anything Rockstar has built before.",
    _requirement("Leonida Keys", "place", ["Leonida Keys"],
                 "2e9f05f5837a0507162f7e2f1f94604dab33ef39a734bf6ca8873b002926b07d"),
    ["Leonida Keys"],
)
SCENE_LUCIA_JASON = _scene(
    2, "lucia-jason",
    "At the center of the story are Lucia Caminos and Jason Duval, two partners "
    "in crime whose story begins with Lucia walking out of prison and reuniting "
    "with Jason.",
    _requirement("Lucia Caminos and Jason Duval", "person", ["Lucia Caminos and Jason Duval"],
                 "a28853372527acebb6cb2c45e5cfdfe214256c88ffffed1d339fffc38b49ba29"),
    ["Lucia Caminos and Jason Duval"],
)
SCENE_PLATFORMS = _scene(
    3, "platforms",
    "When it arrives, it launches first on PlayStation 5 and Xbox Series X and S, "
    "with a PC version yet to be announced.",
    _requirement("Grand Theft Auto VI", "video_game",
                 ["Grand Theft Auto VI", "xbox series"],
                 "5fafa75cc0d29fd58cf258688667c730c5c8b4071b29f90210ed0faf370e6b26"),
    ["Grand Theft Auto VI", "xbox series"],
)
SCENE_SEQUEL = _scene(
    4, "sequel",
    "For Rockstar, Grand Theft Auto Six is not just a sequel, it is the next "
    "chapter of the biggest entertainment series in the world.",
    _requirement("Grand Theft Auto VI", "video_game",
                 ["Grand Theft Auto VI", "entertainment series"],
                 "cef40a1aa6fe545bed2bc5cfafa77747148c5c876f7584152b530d91d644d0c5"),
    ["Grand Theft Auto VI", "entertainment series"],
)


def _write(tmp_path, name, payload):
    path = tmp_path / name
    path.write_bytes(payload)
    return str(path)


def _web_candidate(path, *, title, url, provider, score, candidate_id):
    return {
        "path": path,
        "source": provider,
        "provider": provider,
        "title": title,
        "url": url,
        "source_url": url,
        "page_url": url,
        "candidate_id": candidate_id,
        "score": score,
        "metadata_score": score,
        "final_score": score,
        "container_id": CONTAINER,
    }


def _vision_accepted(candidate):
    return {
        "accepted": True,
        "status": "accepted",
        "visual_checked": True,
        "semantic_verified": True,
        "visual_available": True,
        "entity_conflict": False,
        "fatal_mismatch": False,
        "final_score": float(candidate.get("final_score") or 70.0),
        "identity_score": 90.0,
    }


def _attempts(resolution, stage, status=None):
    return [
        attempt for attempt in (resolution.attempts or [])
        if attempt.get("stage") == stage and (status is None or attempt.get("status") == status)
    ]


@pytest.fixture()
def gta_files(tmp_path):
    """Stand-in files with the proof job's distinctness relations."""
    return {
        "selected": _write(tmp_path, "scene_0002.jpg", b"vice-city-selected-still"),
        "alt1": _write(tmp_path, "alt1.jpg", b"vice-city-neon-alt1"),
        "alt2": _write(tmp_path, "alt2.jpg", b"miami-skyline-neon-alt2"),
        "alt3": _write(tmp_path, "alt3.jpg", b"vice-city-neon-alt3"),
        # Same bytes as alt1 under a different path (poster re-download).
        "alt1_copy": _write(tmp_path, "alt1_copy.jpg", b"vice-city-neon-alt1"),
        "clip": _write(tmp_path, "qq76pQsI1iw.mp4", b"fake-trailer-clip-bytes"),
    }


def _gta_web_rows(files):
    """Scene 1's real web pool: selected still + three ranked alternates."""
    return [
        _web_candidate(
            files["selected"],
            title="Vice City Miami South Beach neon night photo: browse and download images \u2014 Yandex\u00a0Images",
            url="https://i.pinimg.com/736x/4f/61/82/4f6182bed8a2288fa0d6d1e818f754de.jpg",
            provider="web", score=55.0, candidate_id="web:scn_9d3c414661a07bf7f74b",
        ),
        _web_candidate(
            files["alt1"],
            title="Vice City Miami South Beach neon night photo: browse and download images \u2014 Yandex\u00a0Images",
            url="https://i.pinimg.com/736x/4f/61/82/4f6182bed8a2288fa0d6d1e818f754de.jpg",
            provider="universal_yandex", score=0.8935714285714287,
            candidate_id="web:scn_9d3c414661a07bf7f74b:alt1",
        ),
        _web_candidate(
            files["alt2"],
            title="Miami Skyline Night Neon Vice City Style",
            url="https://i.pinimg.com/originals/a8/64/66/a864662522185fdb6ad7b56bac6cf0e2.jpg?nii=t",
            provider="universal_yandex", score=0.41714285714285726,
            candidate_id="web:scn_9d3c414661a07bf7f74b:alt2",
        ),
        _web_candidate(
            files["alt3"],
            title="Vice City Miami South Beach neon night photo: browse and download images \u2014 Yandex\u00a0Images",
            url="https://i.pinimg.com/originals/ec/24/09/ec2409c6c4ae0d2242691dbe99512ff3.jpg?nii=t",
            provider="universal_yandex", score=0.25,
            candidate_id="web:scn_9d3c414661a07bf7f74b:alt3",
        ),
    ]


def _gta_scenes():
    return [SCENE_VICE_CITY, SCENE_LEONIDA, SCENE_LUCIA_JASON, SCENE_PLATFORMS, SCENE_SEQUEL]


def _gta_lanes(files, *, youtube_clip_for_platforms=True):
    rows = _gta_web_rows(files)

    def web_lane(scene, _cancel):
        index = scene.get("scene_index", scene.get("order", 0))
        if index == 0:
            return [dict(row) for row in rows]
        if index in (2, 4):
            # Lucia/Jason and the sequel both resolved to the alt1 still live.
            return [dict(rows[1])]
        return []

    def youtube_lane(scene, _cancel):
        index = scene.get("scene_index", scene.get("order", 0))
        if index == 3 and youtube_clip_for_platforms:
            return [{
                "path": files["clip"],
                "source": "youtube",
                "provider": "youtube",
                "video_id": "qq76pQsI1iw",
                "candidate_id": "youtube:qq76pQsI1iw",
                "title": "Grand Theft Auto VI Trailer 2",
                "description": "Grand Theft Auto VI Trailer 2",
                "channel": "Rockstar Games",
                "container_id": CONTAINER,
                "score": 80.0,
                "metadata_score": 80.0,
                "final_score": 80.0,
            }]
        return []

    return web_lane, youtube_lane


def _gta_verifier(files, *, bound_platforms=False):
    def verify(scene, candidate):
        index = scene.get("scene_index", scene.get("order", 0))
        source = str(candidate.get("source") or candidate.get("provider") or "").lower()
        if index == 3 and source == "youtube":
            if bound_platforms:
                # A real vision verdict bound to this scene and these bytes.
                digest = hashlib.sha256(Path(files["clip"]).read_bytes()).hexdigest()
                return {
                    "accepted": True,
                    "status": "accepted",
                    "visual_checked": True,
                    "semantic_verified": True,
                    "visual_available": True,
                    "entity_conflict": False,
                    "fatal_mismatch": False,
                    "final_score": 70.0,
                    "identity_score": 80.0,
                    "verified_requirement_hash": scene["visual_requirement"]["requirement_hash"],
                    "verified_asset_sha256": digest,
                }
            # The proof job's frame verdict: deterministic, no vision, no hashes.
            return {
                "accepted": True,
                "status": "deterministic_degraded",
                "final_score": 0.4,
                "final_score_source": "compare_relevance",
                "relevance": 0.4,
                "quality": 0.7,
                "fatal_mismatch": False,
                "entity_conflict": False,
                "synthetic_content": True,
                "visual_checked": False,
                "visual_available": False,
                "reason": "youtube_frame_recovery_soft_visual_warning",
            }
        return _vision_accepted(candidate)

    return verify


def _resolve(files, **verifier_kwargs):
    web_lane, youtube_lane = _gta_lanes(files)
    return resolve_storyboard_media_v2(
        _gta_scenes(),
        youtube_lane=youtube_lane,
        web_lane=web_lane,
        visual_verifier=_gta_verifier(files, **verifier_kwargs),
        neutral_factory=lambda _scene: None,
        deadline_seconds=120,
        max_workers=1,
    )


# ---------------------------------------------------------------------------
# Coordinator: pre-stock web rescue
# ---------------------------------------------------------------------------

def test_leonida_scene_rescues_unused_alternate_not_adjacent_selected_still(gta_files):
    """Scene 2 must resolve to an unused alternate, not scene 1's still."""
    result = _resolve(gta_files)
    leonida = result[1]
    assert leonida.status == "accepted"
    assert leonida.provider == "web"
    assert leonida.resolution_kind == "pre_stock_web_rescue"
    assert leonida.selected_path == gta_files["alt2"]
    # The adjacent selected still was considered and explicitly refused.
    assert _attempts(leonida, "pre_stock_web_rescue", "rejected_adjacent_reuse")
    assert not any(
        attempt.get("path") == gta_files["selected"]
        and attempt.get("status") == "accepted"
        for attempt in (leonida.attempts or [])
    )


def test_platforms_scene_unbound_youtube_acceptance_falls_through_to_real_media(gta_files):
    """Scene 6's unbound frame must not short-circuit recovery to a graphic."""
    result = _resolve(gta_files)
    platforms = result[3]
    assert _attempts(platforms, "bound_pixel_review", "accepted_without_bound_pixel_review")
    assert platforms.status == "accepted"
    assert platforms.provider == "web"
    assert platforms.resolution_kind == "pre_stock_web_rescue"
    assert platforms.selected_path == gta_files["alt3"]
    assert platforms.selected_path != gta_files["clip"]
    # The rescue admitted it through the general same-job tier, not the
    # strict identity gate: the ledger must say so.
    assert _attempts(platforms, "pre_stock_web_rescue", "general_same_job_media")


def test_platforms_scene_bound_youtube_acceptance_is_kept(gta_files):
    """A vision verdict bound to the scene and the bytes still commits."""
    result = _resolve(gta_files, bound_platforms=True)
    platforms = result[3]
    assert platforms.status == "accepted"
    assert platforms.provider == "youtube"
    assert platforms.selected_path == gta_files["clip"]
    assert not _attempts(platforms, "bound_pixel_review", "accepted_without_bound_pixel_review")


def test_rescue_skips_same_bytes_under_a_different_path(gta_files):
    """Same pixels re-downloaded under a new path are still an adjacent repeat."""
    from services.media_resolution_coordinator import _adjacent_asset_keys

    assert _normalize_rescue_score(55.0) == pytest.approx(0.55)
    assert _normalize_rescue_score(0.89) == pytest.approx(0.89)
    # Direct unit check: alt1_copy shares alt1's bytes, so it keys adjacent
    # to a scene showing alt1 even though the path and URL differ.
    from services.media_resolution_coordinator import SceneResolution

    resolutions = {
        0: SceneResolution(
            scene_index=0, scene_id="a", container_id=CONTAINER,
            contract_hash="", final_scene_context={},
            selected_path=gta_files["alt1"], media_type="image", provider="web",
        ),
    }
    _identities, digests = _adjacent_asset_keys(resolutions, 1)
    assert hashlib.sha256(Path(gta_files["alt1_copy"]).read_bytes()).hexdigest() in digests


def _real_alt1_candidate(path):
    """Scene 1's alternate 1, verbatim from the proof job's candidate ledger.

    Its vision verdict accepted the pixels (identity 100, context 55) while
    still requiring visual verification -- the exact shape a general
    same-job fallback must admit, and must not mistake for a bound review.
    """
    return {
        "path": path,
        "source": "universal_yandex",
        "provider": "universal_yandex",
        "title": "Vice City Miami South Beach neon night photo: browse and download images \u2014 Yandex\u00a0Images",
        "url": "https://i.pinimg.com/736x/4f/61/82/4f6182bed8a2288fa0d6d1e818f754de.jpg",
        "source_url": "https://i.pinimg.com/736x/4f/61/82/4f6182bed8a2288fa0d6d1e818f754de.jpg",
        "page_url": "https://i.pinimg.com/736x/4f/61/82/4f6182bed8a2288fa0d6d1e818f754de.jpg",
        "candidate_id": "web:scn_9d3c414661a07bf7f74b:alt1",
        "container_id": CONTAINER,
        "qwen_result": {
            "candidate_id": "web:scn_9d3c414661a07bf7f74b:alt1",
            "entity_conflict": False,
            "identity_score": 100.0,
            "action_score": 100.0,
            "context_score": 55.0,
            "quality": 85.0,
            "final_score": 55.0,
            "visible_overlays": [],
            "subject_box": {},
            "safe_text_regions": [],
            "accepted": True,
            "status": "accepted",
            "source": "web_image_resolver",
            "verification_status": "pending_visual_verification",
            "visual_verification_required": True,
        },
    }


def test_rescue_general_media_admits_verified_same_job_asset(gta_files):
    """A vision-accepted Vice City still may serve the platforms scene.

    The strict gate rejects it (canonical_entity_mismatch: the metadata does
    not prove "Grand Theft Auto VI launches on PS5/Xbox"), but it is GTA VI
    world imagery vision-accepted for this job, same container, no
    conflicting identity -- general same-job media, not a graphic.
    """
    scene = SCENE_PLATFORMS
    candidate = _real_alt1_candidate(gta_files["alt1"])
    assert _rescue_general_media_eligible(scene, candidate, "canonical_entity_mismatch", {})


def test_rescue_general_media_admits_via_gated_outcome_without_own_verdict(gta_files):
    """An asset the visual gate verified for another scene counts too."""
    scene = SCENE_PLATFORMS
    candidate = _real_alt1_candidate(gta_files["alt1"])
    candidate.pop("qwen_result")
    gated = {
        (0, gta_files["alt1"]): (
            True, "passed", True, "visual_pass", 55.0,
            {"accepted": True, "visual_checked": True, "entity_conflict": False, "fatal_mismatch": False},
        ),
    }
    assert _rescue_general_media_eligible(scene, candidate, "canonical_entity_mismatch", gated)


def test_rescue_general_media_rejects_hard_gate_reasons(gta_files):
    """Hard identity rejections never pass through the general tier."""
    scene = SCENE_PLATFORMS
    candidate = _real_alt1_candidate(gta_files["alt1"])
    for reason in (
        "forbidden_confusable_entity",
        "person_confusable_entity",
        "wrong_subject_type_animal",
        "container_mismatch",
        "deterministic_gate_failed",
        "domain_conflict: wrong film",
    ):
        assert not _rescue_general_media_eligible(scene, candidate, reason, {})


def test_rescue_general_media_rejects_other_container_and_unverified(gta_files):
    """General media must be from the SAME job and vision-accepted."""
    scene = SCENE_PLATFORMS
    other_job = _real_alt1_candidate(gta_files["alt1"])
    other_job["container_id"] = "ctr_other_job"
    assert not _rescue_general_media_eligible(scene, other_job, "canonical_entity_mismatch", {})
    never_checked = _real_alt1_candidate(gta_files["alt1"])
    never_checked.pop("qwen_result")
    assert not _rescue_general_media_eligible(scene, never_checked, "canonical_entity_mismatch", {})
    conflicting = _real_alt1_candidate(gta_files["alt1"])
    conflicting["qwen_result"] = dict(conflicting["qwen_result"], entity_conflict=True)
    assert not _rescue_general_media_eligible(scene, conflicting, "canonical_entity_mismatch", {})


# ---------------------------------------------------------------------------
# production_contract: repair prefers verified media; graphics never repeat silently
# ---------------------------------------------------------------------------

def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _requirement_context(entity, subject_type, requirement_hash):
    return {
        "primary_visual": {
            "type": "SmartImage",
            "asset": {
                "resolution": {
                    "final_scene_context": {
                        "visual_requirement": {
                            "canonical_entity": entity,
                            "subject_type": subject_type,
                            "requirement_hash": requirement_hash,
                            "strict_identity": True,
                        },
                        "reuse_policy": {"scope": "container", "max_uses": 2, "non_adjacent": True},
                    },
                },
            },
        },
    }


def _verified_donor_scene(path, *, narration, entity, subject_type, requirement_hash):
    sha = _sha(path)
    scene = {
        "start": 0,
        "duration": 3,
        "narration": narration,
        "media": {
            "src": str(path),
            "type": "image",
            "shots": [{
                "path": str(path),
                "asset_path": str(path),
                "media_type": "image",
                "type": "image",
                "verification": "verified",
                "verification_status": "verified",
                "verified_asset_sha256": sha,
                "asset_sha256": sha,
                "asset_fingerprint": sha,
                "visual_decision": {
                    "accepted": True,
                    "visual_checked": True,
                    "verified_asset_sha256": sha,
                },
            }],
        },
    }
    scene.update(_requirement_context(entity, subject_type, requirement_hash))
    return scene


def _defective_target_scene(path, *, narration, entity, subject_type, requirement_hash,
                            alternates=None):
    """A shot whose verified digest does not match the file on disk."""
    file_sha = _sha(path)
    scene = {
        "start": 0,
        "duration": 3,
        "narration": narration,
        "media": {
            "src": str(path),
            "type": "image",
            "shots": [{
                "path": str(path),
                "asset_path": str(path),
                "media_type": "image",
                "type": "image",
                "verification": "verified",
                "verification_status": "verified",
                "verified_asset_sha256": "0" * 64,
                "asset_sha256": file_sha,
                "asset_fingerprint": file_sha,
                "visual_decision": {
                    "accepted": True,
                    "visual_checked": True,
                    "verified_asset_sha256": "0" * 64,
                },
            }],
        },
    }
    scene.update(_requirement_context(entity, subject_type, requirement_hash))
    if alternates is not None:
        scene["primary_visual"]["asset"]["alternates"] = alternates
    return scene


def test_repair_prefers_same_entity_verified_media_over_graphic(tmp_path):
    donor = _write(tmp_path, "donor.jpg", b"leonida-keys-still")
    broken = _write(tmp_path, "broken.jpg", b"defective-shot-bytes")
    filler = _write(tmp_path, "filler.jpg", b"unrelated-filler-still")
    donor_scene = _verified_donor_scene(
        donor, narration="The trailers show the Leonida Keys and the neon of Vice City.",
        entity="Leonida Keys", subject_type="place", requirement_hash="a" * 64,
    )
    filler_scene = _verified_donor_scene(
        filler, narration="Unrelated establishing beat.",
        entity="Vice City", subject_type="place", requirement_hash="b" * 64,
    )
    target = _defective_target_scene(
        broken, narration="The swamps of the Grassrivers stretch behind the Keys.",
        entity="Leonida Keys", subject_type="place", requirement_hash="c" * 64,
    )
    manifest = {"fps": 30, "scenes": [donor_scene, filler_scene, target]}
    result = repair_media_defects(manifest)
    assert result["audit"]["errors"] == []
    assert result["substitutions"] == [{
        "scene_index": 2,
        "code": "verified_shot_changed_after_review",
        "from": str(broken),
        "to": "closest_verified_media",
        "path": str(donor),
        "donor_scene_index": 0,
        "reuse_kind": "same_entity",
    }]
    assert manifest["scenes"][2]["media"]["shots"][0]["path"] == str(donor)


def test_repair_general_same_job_media_before_graphic(tmp_path):
    donor = _write(tmp_path, "donor.jpg", b"vice-city-general-still")
    broken = _write(tmp_path, "broken.jpg", b"defective-shot-bytes")
    donor_scene = _verified_donor_scene(
        donor, narration="The game returns to Vice City, now set in Leonida.",
        entity="Vice City", subject_type="place", requirement_hash="a" * 64,
    )
    target = _defective_target_scene(
        broken, narration="When it arrives, it launches first on PlayStation 5 and Xbox Series X and S.",
        entity="Grand Theft Auto VI", subject_type="video_game", requirement_hash="c" * 64,
    )
    manifest = {"fps": 30, "scenes": [donor_scene, _verified_donor_scene(
        _write(tmp_path, "filler.jpg", b"filler"),
        narration="Filler.", entity="Vice City", subject_type="place",
        requirement_hash="b" * 64,
    ), target]}
    result = repair_media_defects(manifest)
    assert result["audit"]["errors"] == []
    assert result["substitutions"][0]["to"] == "closest_verified_media"
    assert result["substitutions"][0]["reuse_kind"] == "general_same_job"
    assert result["substitutions"][0]["donor_scene_index"] == 0


def test_repair_entityless_scene_keeps_graphic(tmp_path):
    """No named entity, no "closest" media: the graphic stands.

    A defective scene with no canonical entity must not borrow an arbitrary
    other scene's still (the Berlin Wall a48fdf1 fixtures have no requirement
    context, and their graphic counts are the contract). The donor tier
    needs a subject to measure closeness against.
    """
    donor = _write(tmp_path, "donor.jpg", b"healthy-still")
    broken = _write(tmp_path, "broken.jpg", b"defective-shot-bytes")
    donor_scene = _verified_donor_scene(
        donor, narration="The wall stood for decades.",
        entity="Berlin Wall", subject_type="history", requirement_hash="a" * 64,
    )
    target = _defective_target_scene(
        broken, narration="Schabowski reads the new travel rules.",
        entity="", subject_type="", requirement_hash="c" * 64,
    )
    # Strip the requirement context: this target names no entity at all.
    target["primary_visual"] = {"type": "SmartImage"}
    manifest = {"fps": 30, "scenes": [donor_scene, _verified_donor_scene(
        _write(tmp_path, "filler.jpg", b"filler"),
        narration="Filler.", entity="Berlin Wall", subject_type="history",
        requirement_hash="b" * 64,
    ), target]}
    result = repair_media_defects(manifest)
    assert result["substitutions"][0]["to"] == "graphic"


def test_repair_never_lends_wrong_game_footage(tmp_path):
    """A GTA V still verified for a GTA V scene is not a donor for GTA VI."""
    donor = _write(tmp_path, "gta5.jpg", b"gta-five-gameplay-still")
    broken = _write(tmp_path, "broken.jpg", b"defective-shot-bytes")
    donor_scene = _verified_donor_scene(
        donor, narration="Grand Theft Auto Five sold more than two hundred million copies.",
        entity="Grand Theft Auto V", subject_type="video_game", requirement_hash="a" * 64,
    )
    target = _defective_target_scene(
        broken, narration="When it arrives, it launches first on PlayStation 5 and Xbox Series X and S.",
        entity="Grand Theft Auto VI", subject_type="video_game", requirement_hash="c" * 64,
    )
    filler = _verified_donor_scene(
        _write(tmp_path, "filler.jpg", b"filler"),
        narration="Filler.", entity="Vice City", subject_type="place",
        requirement_hash="b" * 64,
    )
    manifest = {"fps": 30, "scenes": [donor_scene, filler, target]}
    result = repair_media_defects(manifest)
    assert result["substitutions"][0]["to"] == "graphic"
    assert result["substitutions"][0]["path"] == str(broken)


def test_repair_records_repeated_graphic_explicitly(tmp_path):
    """Two defective scenes sharing one file: the repeat is logged, not silent."""
    shared = _write(tmp_path, "shared.jpg", b"shared-defective-bytes")
    first = _defective_target_scene(
        shared, narration="First defective beat.",
        entity="Leonida Keys", subject_type="place", requirement_hash="a" * 64,
    )
    second = _defective_target_scene(
        shared, narration="Second defective beat.",
        entity="Grand Theft Auto VI", subject_type="video_game", requirement_hash="c" * 64,
    )
    manifest = {"fps": 30, "scenes": [first, second]}
    result = repair_media_defects(manifest)
    assert [s["to"] for s in result["substitutions"]] == ["graphic", "graphic"]
    assert result["substitutions"][1]["graphic_repeat_of_scene"] == 0
    assert result["substitutions"][1]["graphic_distinct"] is False
    assert any(
        warning.get("code") == "graphic_repeat"
        for warning in result["audit"].get("warnings", [])
    )
