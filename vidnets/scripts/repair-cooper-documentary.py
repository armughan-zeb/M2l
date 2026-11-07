"""Evidence-led repair of the saved test edit; no narration or retrieval rerun."""
import copy
import json
import os
from pathlib import Path
import sys
import shutil
from concurrent.futures import ThreadPoolExecutor

import requests
from dotenv import load_dotenv
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/cooper_one_minute_20261005'
ASSETS = OUT / 'evidence'
ASSETS.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT / 'server'))
for filename in ('.env.public', '.env.defaults', '.env'):
    load_dotenv(ROOT / filename, override=True)
os.environ['VIDZEO_OPENROUTER_USAGE_LOG'] = str(OUT / 'openrouter-usage.jsonl')
from services.openrouter_llm import OpenRouterLLM

SOURCES = {
    'ticket': 'https://www.fbi.gov/image-repository/artifacts/aotm-july-2016-db-cooper-plane-ticket.jpg/@@images/image/high',
    'sketch': 'https://www.fbi.gov/image-repository/cooper-sketches.jpeg/@@images/image/high',
    'tie': 'https://www.fbi.gov/image-repository/black-tie.jpg/@@images/image/large',
    'money': 'https://www.fbi.gov/image-repository/cooper-money.jpg/@@images/image/high',
    'parachute': 'https://www.fbi.gov/image-repository/parachute.jpg/@@images/image/high',
    'bag': 'https://www.fbi.gov/image-repository/parachute-bag.jpg/@@images/image/high',
    'aircraft': 'https://upload.wikimedia.org/wikipedia/commons/7/71/Northwest_Orient_Airlines_Boeing_727-251_N257US.jpg',
}


def fetch(item):
    name, url = item
    path = ASSETS / (name + '.jpg')
    if not path.exists():
        response = requests.get(url, timeout=35)
        response.raise_for_status()
        path.write_bytes(response.content)
    with Image.open(path) as im:
        size = im.size
        im.verify()
    return name, {'src': str(path), 'path': str(path), 'type': 'image',
        'width': size[0], 'height': size[1], 'aspect_ratio': size[0] / size[1],
        'fit': 'contain', 'source': 'web', 'source_url': url,
        'publisher': 'Clint Groves / Wikimedia Commons' if name == 'aircraft' else 'Federal Bureau of Investigation',
        'license': 'GFDL-1.2' if name == 'aircraft' else 'FBI published historical evidence',
        'verification_status': 'primary_source_evidence',
        'verification': {'method': 'official_fbi_artifact_caption_and_file_decode', 'actual_asset_checked': True},
        'contextual_evidence': True, 'is_graphic_fallback': False}


def main():
    with ThreadPoolExecutor(max_workers=4) as pool:
        assets = dict(pool.map(fetch, SOURCES.items()))
    film = json.loads((OUT / 'failed-review/manifest.json').read_text(encoding='utf-8'))
    original = film['scenes']
    audio_plan = json.loads((OUT / 'generation-package/checkpoints/audio_plan.json').read_text(encoding='utf-8'))
    words = audio_plan['whisper_words']
    # Whisper split a single abbreviation into three scenes. Rejoin its exact
    # audio interval and preserve every word's relative timestamp.
    joined = copy.deepcopy(original[7])
    joined['narration'] = 'A little after eight p.m., somewhere between Seattle and Reno, he jumped from the rear of the aircraft into the darkness.'
    joined['duration'] = original[9]['start'] + original[9]['duration'] - joined['start']
    joined['duration_in_frames'] = sum(s['duration_in_frames'] for s in original[7:10])
    joined['word_cues'] = []
    joined['caption_words'] = []
    for scene in original[7:10]:
        offset = scene['start'] - joined['start']
        for cue in scene.get('word_cues') or []:
            cue = copy.deepcopy(cue)
            for word in cue.get('timed_words') or []:
                for field in ('start', 'end'):
                    if field in word: word[field] += offset
            joined['word_cues'].append(cue)
        for word in scene.get('caption_words') or []:
            word = copy.deepcopy(word)
            for field in ('start', 'end', 'start_time', 'end_time'):
                if field in word: word[field] += offset
            joined['caption_words'].append(word)
    scenes = original[:7] + [joined] + original[10:]
    # Primary-source artifacts explain the subject; no unrelated YouTube
    # thumbnail, claimed jump location or simulated archival footage.
    assignments = ['ticket','sketch','ticket','parachute','sketch','bag','aircraft',
                   'sketch','tie','sketch','money','money','bag','ticket','sketch','ticket','money']
    variants = ['framed_print','editorial_crop','spotlight_reveal','framed_print',
                'editorial_crop','photo_stack','framed_print','full_bleed_cinematic',
                'framed_print','editorial_crop','framed_print','spotlight_reveal',
                'framed_print','framed_print','editorial_crop','spotlight_reveal','framed_print']
    brief = {'story_arc': [s['narration'] for s in scenes], 'niche': 'crime',
        'rules': 'Dark charcoal, white type, restrained red; no decorative geometry. Short exact narration highlights, tight backing, protect evidence. Vary layouts purposefully. Choose empty copy for quiet evidence holds.',
        'scenes': [{'index': i, 'narration': s['narration'], 'duration': s['duration'],
                    'asset': assignments[i]} for i,s in enumerate(scenes)]}
    client = OpenRouterLLM(api_key=os.environ.get('OPENROUTER_API_KEY') or os.environ.get('VIDZEO_OPENROUTER_API_KEY'),
        model='openai/gpt-6-luna', fallback_models=[], use_server_fallbacks=False)
    answer = (OUT / 'repair-director.json').read_text(encoding='utf-8') if (OUT / 'repair-director.json').exists() else client.chat(user_prompt=json.dumps(brief), system_prompt=
        'You are an evidence-led documentary editor. Return JSON with a scenes array, each item index, highlight, position, variant. '
        'highlight MUST be an exact contiguous 1-4 word phrase copied from its own narration, or empty. '
        'Positions: bottom_left, bottom_right, top_left, center. Image variants: framed_print, editorial_crop, spotlight_reveal, archival_polaroid. '
        'Highlight only about half the scenes. Retain names, key money values and unresolved outcome; keep text clear of the artifact. '
        'Do not introduce facts. For scene 7 use empty highlight; its geography is a specialist map. For scene 13 use empty highlight; a timeline supplies dates.',
        json_mode=True, temperature=.35, max_tokens=2200, total_timeout_seconds=40,
        max_attempts=1, json_schema_name='cooper_evidence_edit_repair')
    decisions = json.loads(answer)
    (OUT / 'repair-director.json').write_text(json.dumps(decisions, indent=2), encoding='utf-8')
    decision_map = {int(d['index']): d for d in decisions['scenes']}
    for i, scene in enumerate(scenes):
        scene['scene_index'] = i
        scene['id'] = f'cooper-evidence-{i}'
        scene['media'] = copy.deepcopy(assets[assignments[i]])
        scene['media_plan'] = {'decision_source': 'llm_director', 'repair': 'primary_source_evidence'}
        scene['primary_visual'] = {'type': 'SmartImage', 'variant': variants[i], 'props': {}, 'asset': copy.deepcopy(scene['media'])}
        scene['visual_role'] = 'media_only'
        scene['visual_intent'] = 'Primary-source artifact context; emphasize verified evidence rather than claim footage of the actual event.'
        scene['visual_role_contract_version'] = 2
        scene['media_composition'] = {'hero_layer': 'media', 'render_mode': 'media_only'}
        scene['overlays'] = []
        scene['template_recipe'] = None
        scene['graphic'] = {}
        scene['motion_cues'] = []
        scene['design']['image_treatment'] = 'hold'
        scene['atmosphere'] = {'camera_shake': 0, 'grain': 0}
        scene['camera'] = {'motion': 'slow_push'}
        scene['motion'] = {'camera': 'slow_push', 'entrance': 'fade'}
        decision = decision_map[i]
        variant = decision.get('variant')
        if variant in ('framed_print','editorial_crop','spotlight_reveal','archival_polaroid'):
            scene['primary_visual']['variant'] = variant
        highlight = str(decision.get('highlight') or '').strip()
        if highlight and highlight.casefold() not in scene['narration'].casefold():
            raise ValueError('Unapproved paraphrase from director')
        position = decision.get('position', 'bottom_left')
        if position not in ('bottom_left','bottom_right','top_left','top_right','center'):
            raise ValueError('Unknown director text position')
        # Faces and artifact writing occupy the center and upper canvas.
        if highlight and position in ('center','top_left','top_right'):
            position = 'bottom_left'
        scene['editorial_contract'] = {'version': 4, 'source': 'llm', 'approved': bool(highlight),
            'display_text': highlight, 'source_span': scene['narration'],
            'emphasis_spans': [{'start': 0, 'end': len(highlight)}] if highlight else []}
        scene['display_text'] = highlight
        scene['text'] = {'display_text': highlight, 'primary': highlight, 'frame_mode': 'media_only',
            'position': position, 'panel_style': 'none', 'copy_source': 'llm',
            'template_id': 'headline-center', 'variant': ['impact_slab','mask_slide_up','word_by_word_punch'][i%3],
            'accent_color': '#D32F2F', 'text_color': '#FFFFFF'}
        scene['copy_window'] = {'start_frame': min(5, scene['duration_in_frames']-1),
            'duration_frames': min(round(1.8 * film['fps']), scene['duration_in_frames'])}
        if not highlight:
            scene.pop('copy_window', None)
        else:
            tokens = lambda value: ''.join(c for c in value.lower() if c.isalnum())
            target = [tokens(w) for w in highlight.split()]
            for j in range(len(words)-len(target)+1):
                if all(tokens(words[j+k]['word']) == token for k,token in enumerate(target)) and scene['start'] <= words[j]['start'] < scene['start']+scene['duration']:
                    scene['copy_window']['start_frame'] = round((words[j]['start']-scene['start'])*film['fps'])
                    break
        scene['captions_enabled'] = i not in (3,7,9,13)
        scene['caption_suppressed'] = i in (3,7,9,13)
        scene['caption_words'] = [{'text': w['word'], 'start':w['start'], 'end':w['end']} for w in words
            if w['end']>scene['start'] and w['start']<scene['start']+scene['duration']]
        scene['primary_visual']['props'].update({'variant': scene['primary_visual']['variant'], 'panel_style': 'none'})
    # Meaningful explanatory graphics occupy approximately one third of the film.
    for index, target, prefix, suffix in ((3,200000,'$',''),(9,800,'','suspects')):
        scene = scenes[index]
        scene['primary_visual'] = {'type': 'StatCard', 'variant': 'hero_counter_large',
            'props': {'variant':'hero_counter_large','value':target,'prefix':prefix,
                'suffix':suffix,'qualifier':'more than' if index==9 else '',
                'label':'','position':'center','panel_style':'none'}}
        scene['visual_role'] = 'primary_explanation'
        scene['text']['frame_mode'] = 'data'
        scene['motion_cues'] = [{'element':'data', 'action':'count', 'time_local':.15,
            'duration_seconds':.4, 'trigger_phrase':'$200,000' if index==3 else '800 suspects'}]
    scene = scenes[7]
    scene['primary_visual'] = {'type':'AnimatedMap','variant':'city_to_city_transit','props':{
        'variant':'city_to_city_transit','from':'Seattle','to':'Reno',
        'from_coordinates':{'lon':-122.3321,'lat':47.6062},
        'to_coordinates':{'lon':-119.8138,'lat':39.5296},
        'connection_kind':'context_only','route_supported':False,
        'map_shot':{'location_names':['Seattle','Reno'],'motion_profile':'route_trace'},
        'map_label':'','date':''}}
    # Location context only: no invented route or exact jump pin.
    scene['visual_role'] = 'map_canvas'
    scene['camera'] = {'disabled':True}
    scene['motion'] = {'camera':'hold','entrance':'fade'}
    scene['text']['frame_mode'] = 'map'
    scenes[3]['primary_visual']['props']['metrics'] = [
        {'value':4,'label':'parachutes','source_span':'four parachutes','time_local':.15},
        {'value':200000,'prefix':'$','label':'','source_span':'$200,000','time_local':3.715}]
    scenes[3]['primary_visual']['variant'] = 'multi_stat_grid'
    scenes[3]['primary_visual']['props']['variant'] = 'multi_stat_grid'
    scenes[3]['editorial_contract'] = {}
    scenes[3].pop('copy_window', None)
    scene = scenes[13]
    scene['primary_visual'] = {'type':'Timeline','variant':'horizontal_milestone_trail','props':{
        'variant':'horizontal_milestone_trail','events':[
            {'date':'1971','label':'Hijacking'},{'date':'1980','label':'Ransom recovered'},
            {'date':'2016','label':'Resources redirected'}]}}
    scene['visual_role'] = 'primary_explanation'
    scene['text']['frame_mode'] = 'data'
    scene['editorial_contract'] = {}
    film['scenes'] = scenes
    film['captions'] = {'enabled':True,'style':'clean','font':'DM Sans','mode':'phrase','position':'bottom'}
    film['transitions'] = []
    for scene in scenes:
        scene['transition'] = {'template_id':'cut','duration':0}
        scene['transition_out'] = 'cut'
        scene['visual_dna']['palette']['accent'] = '#D32F2F'
    # Drop sounds for discarded camera flourishes and re-anchor the data
    # transients to the actual specialist reveal after the repair.
    effects = []
    effect_keys = set()
    for effect in film['audio'].get('effects') or []:
        if effect.get('sound_intent') == 'visual_action' and ':data:' not in effect.get('event_id',''):
            continue
        if effect.get('kind') == 'cash_counter':
            effect['start'] = scenes[3]['start'] + 3.715
            effect['sound_anchor'] = '$200,000'
            effect['timing_quality'] = 'repaired_visual_frame_aligned'
        key = (effect['kind'], round(effect['start'], 3))
        if key not in effect_keys:
            effects.append(effect)
            effect_keys.add(key)
    film['audio']['effects'] = effects
    # The preserved test must remain reproducible when temporary files clear.
    voice = Path(film['audio']['voiceover']['src'])
    saved_voice = ASSETS / 'narration.mp3'
    shutil.copyfile(voice, saved_voice)
    film['audio']['voiceover'] = {'src':str(saved_voice),'path':str(saved_voice)}
    film['category'] = 'crime'
    film['review_frames'] = sorted(set(min(film['duration_in_frames']-1, s['from']+max(3,round(s['duration_in_frames']*fraction)))
        for s in scenes for fraction in (.25,.65)))
    film['editorial_cues'] = []
    film['editorial_cues_shadow'] = []
    film['creative_review'] = {'status':'awaiting_repaired_preview', 'original_failure_preserved':True}
    film['repair_audit'] = {'method':'curated_primary_source_evidence_and_llm_copy',
        'media_sources':SOURCES,'merged_abbreviation_scenes':[7,8,9],
        'note':'This is a repaired pipeline test, not proof of unattended generation reliability.'}
    film['repair_audit']['aircraft_context'] = 'Northwest Orient aircraft at San Francisco, 1970; related archival context, not a photo of the hijacking or its exact plane.'
    for stale in ('editing_timeline','media_shot_plan','production_audit','edit_plan_validation','render_metadata'):
        film.pop(stale,None)
    (OUT / 'repaired-manifest.json').write_text(json.dumps(film,indent=2),encoding='utf-8')
    print(json.dumps({'scenes':len(scenes),'primary_source_assets':len(assets),'duration':film['duration']}))


if __name__ == '__main__': main()
