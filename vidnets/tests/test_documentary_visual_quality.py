import shutil
import subprocess
import sys
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))

from services.editorial_director.director import _safe_text_treatment, _director_runtime_capabilities, _normalized_emphasis_spans


def test_short_approved_diagram_actor_labels_survive_export_repair():
    import app
    props = {'steps': [{'title': 'Moon'}, {'title': 'Earth'}], 'node_copy_contract': {
        'source': 'llm', 'approved': True, 'nodes': [
            {'label': 'Moon', 'source_span': 'The Moon blocks sunlight'},
            {'label': 'Earth', 'source_span': 'shadow crosses Earth'},
        ]}}
    narration = 'The Moon blocks sunlight and its shadow crosses Earth.'
    scene = {'narration': narration, 'primary_visual': {'type': 'Diagram', 'props': props}, 'text': {'frame_mode': 'diagram'}}
    assert len(app._complete_diagram_steps(props, narration)) == 2
    assert app._repair_incomplete_diagram_scenes({'scenes': [scene]}) == []
    assert app._complete_diagram_steps(props, 'The Sun is bright.') == []


def test_scene_repair_diagram_labels_survive_export_repair():
    import app
    props = {'steps': [{'title': 'Butler'}, {'title': 'undrafted rookie'}], 'node_copy_contract': {
        'source': 'llm_scene_repair', 'approved': True, 'nodes': [
            {'label': 'Butler', 'source_span': 'Butler'},
            {'label': 'undrafted rookie', 'source_span': 'undrafted rookie'},
        ]}}
    narration = 'Butler was an undrafted rookie.'
    assert len(app._complete_diagram_steps(props, narration)) == 2


def test_failed_diagram_repair_removes_all_copy_contract_paths():
    import app
    scene = {'narration': 'The Moon blocks sunlight.', 'primary_visual': {'type': 'Diagram', 'props': {'steps': []}},
             'editorial_contract': {'approved': True, 'display_text': 'Moon'}, 'display_text': 'Moon',
             'text': {'frame_mode': 'diagram', 'display_text': 'Moon', 'overlay_policy': 'single_short_highlight'}}
    assert app._repair_incomplete_diagram_scenes({'scenes': [scene]}) == [0]
    assert scene['editorial_contract'] == {}
    assert scene['display_text'] == scene['text']['display_text'] == ''
    assert scene['text']['overlay_policy'] == 'none'


@pytest.mark.parametrize('succeeds', [False, True])
def test_composition_pass_is_bounded_and_preserves_valid_direction_on_failure(succeeds):
    import json
    from services.editorial_director import EditingDirector

    class Client:
        def __init__(self):
            self.budget = None

        def chat(self, **kwargs):
            stage = kwargs.get('json_schema_name')
            if stage == 'vidnets_art_direction_v1':
                return json.dumps({'genre': 'science-documentary', 'display_font': 'Archivo Black', 'body_font': 'DM Sans', 'accent_color': '#E8B75C'})
            if stage == 'vidnets_scene_composition_v1':
                self.budget = kwargs['total_timeout_seconds']
                if succeeds:
                    geometry = {'sphere': (12, 20, 36, 48), 'cone': (48, 26, 38, 28), 'arrow': (48, 48, 35, 6)}
                    objects = [{'kind': kind, **dict(zip(('x', 'y', 'width', 'height'), geometry[kind])),
                                'actor_label': 'Moon' if kind != 'cone' else 'sunlight', 'color_role': 'accent', 'motion': 'fade', 'start_seconds': .5}
                               for kind in ['sphere', 'cone', 'arrow']]
                    return json.dumps({'id': 'Eclipse Mechanism!', 'name': 'Eclipse mechanism', 'objects': objects})
                raise TimeoutError('simulated design failure')
            if stage == 'vidnets_scene_direction_v1':
                return json.dumps({'scenes': [{'scene_index': 0, 'type': 'Diagram', 'variant': 'step_by_step_process',
                    'frame_mode': 'diagram', 'overlay_policy': 'none', 'display_text': '', 'template_id': 'cause-effect',
                    'diagram_nodes': [{'label': 'Moon', 'source_span': 'The Moon blocks sunlight'},
                                      {'label': 'sunlight', 'source_span': 'The Moon blocks sunlight'}]}]})
            return '{}'

    client = Client()
    plan = EditingDirector(llm_client=client).plan_documentary(
        script_text='The Moon blocks sunlight.', scene_texts=['The Moon blocks sunlight.'], scene_durations=[4.0],
        media_items=[{'media_type': 'video'}], topic='Eclipse', category='science', fps=60)
    assert 0 < client.budget <= 40
    assert plan.director_metadata['creative_coverage'] == ('complete' if succeeds else 'partial')
    assert plan.director_metadata['composition_passes'][0]['status'] == ('accepted' if succeeds else 'preserved_scene:TimeoutError')
    assert plan.scenes[0].primary_visual.type == ('Diagram' if succeeds else 'SmartVideo')
    assert not plan.scenes[0].primary_visual.props.get('no_data')
    if not succeeds:
        assert plan.scenes[0].visual_intent == (
            'Hold the video full-frame for this narration beat: The Moon blocks sunlight. '
            'Keep the actual subject clear and add no overlay copy.'
        )
    if succeeds:
        assert plan.scenes[0].template_id == 'generated-recipe'
        assert plan.scenes[0].template_recipe['id'] == 'eclipse-mechanism'
        assert len(plan.scenes[0].template_recipe['layers']) == 4
        assert plan.scenes[0].template_recipe['layers'][1]['start_frame'] == 30


def test_scene_composition_preserves_efficient_route_and_explicit_custom_override(monkeypatch):
    from services.editorial_director.director import (
        DIRECTOR_DEFAULT_MODEL,
        _composition_model_route,
    )

    monkeypatch.delenv('VIDZEO_EDITORIAL_COMPOSITION_MODEL', raising=False)
    monkeypatch.delenv('VIDZEO_EDITORIAL_COMPOSITION_FALLBACK_MODELS', raising=False)

    class Worker:
        model = DIRECTOR_DEFAULT_MODEL
        fallback_models = ['openai/gpt-6-luna', 'openai/gpt-5.6-sol']

    primary, fallbacks = _composition_model_route({}, Worker())
    assert primary == 'qwen/qwen3.5-flash-02-23'
    assert fallbacks == []
    assert primary not in fallbacks

    primary, fallbacks = _composition_model_route(
        {'composition_model': 'custom/composition', 'composition_fallback_models': 'custom/backup,openai/gpt-5.6-luna'},
        Worker(),
    )
    assert primary == 'custom/composition'
    assert fallbacks[0] == 'custom/backup'
    assert fallbacks.count('openai/gpt-5.6-luna') == 1


def test_composition_emergency_media_hold_is_grounded_to_its_narration_beat():
    from services.editorial_director.director import _composition_media_fallback_intent

    intent = _composition_media_fallback_intent(
        'Jermaine Kearse keeps Seattle alive with a four-bobble catch. The drive continues.',
        'youtube',
    )
    assert intent == (
        'Hold the verified footage full-frame for this narration beat: '
        'Jermaine Kearse keeps Seattle alive with a four-bobble catch. '
        'Keep the actual subject clear and add no overlay copy.'
    )


def test_new_type_treatments_survive_the_llm_to_renderer_contract():
    palette = {'accent': '#E8B75C', 'surface': '#10242C', 'primary': '#FFFFFF'}
    styles = ['hero_type', 'stacked_type', 'outline_type', 'marker_type', 'editorial_caption']
    capabilities = _director_runtime_capabilities()
    for style in styles:
        result = _safe_text_treatment({'panel_style': style}, palette, True)
        assert result['panel_style'] == style
        assert style in capabilities['text_treatments']
    assert _safe_text_treatment({'panel_style': 'hero_type'}, palette, False) == {'panel_style': 'none'}
    assert capabilities['visual_systems']['Diagram']['remotion_component'] == 'ExplanatoryDiagram'


def test_text_treatment_colors_stay_in_their_palette_roles_and_numbers_use_bundled_mono():
    palette = {'primary':'#FFFBEB','text_contrast':'#FFFFFF','accent':'#4D9AA1','accent2':'#EAB308',
               'background':'#142525','surface':'#1D3030','border':'#362419'}
    result = _safe_text_treatment({'panel_style':'hero_type','text_color':'#EAB308',
        'accent_color':'#EAB308','panel_background':'#2D2E16'}, palette, True)
    assert result == {'panel_style':'hero_type','text_color':'#FFFBEB',
                      'accent_color':'#4D9AA1','panel_background':'#1D3030'}
    assert _director_runtime_capabilities()['bundled_typography']['numbers'] == 'JetBrains Mono'


def test_director_rejects_overwide_emphasis_without_guessing_a_replacement():
    assert _normalized_emphasis_spans('not a darkening', [{'start':0,'end':15}]) == []
    assert _normalized_emphasis_spans('not a darkening', [{'start':6,'end':15}]) == [{'start':6,'end':15}]
    assert _normalized_emphasis_spans('two oceans', [{'start':0,'end':10}]) == [{'start':0,'end':10}]


def test_shared_grading_preserves_document_evidence_and_varies_by_genre():
    code = r'''
const fs = require('fs'); const assert = require('assert');
(async()=>{
 const src=fs.readFileSync(process.argv[1],'utf8');
 const {resolveColorGrade}=await import('data:text/javascript;base64,'+Buffer.from(src).toString('base64'));
 const photo={primary_visual:{type:'SmartImage'}};
 const crime=resolveColorGrade(photo,{genre:'true-crime'});
 const travel=resolveColorGrade(photo,{genre:'travelogue'});
 assert.notEqual(crime.filter,travel.filter);
 assert.equal(crime.name,'investigative'); assert.equal(travel.name,'warm');
 const source=resolveColorGrade({primary_visual:{type:'DocumentHighlight'},design:{color_grade:'action'}},{genre:'true-crime'},{brightness:3.2});
 assert.equal(source.filter,'contrast(1) saturate(1) sepia(0) hue-rotate(0deg) brightness(1)');
 assert.equal(resolveColorGrade({...photo,design:{color_grade:'unknown'}},{genre:'sports-documentary'}).name,'action');
 const explicitlyGradedSource=resolveColorGrade({primary_visual:{type:'SmartImage'},media:{preserve_source_colors:true,color_grade:'archival'}});
 assert.equal(explicitlyGradedSource.name,'archival');
})().catch(e=>{console.error(e);process.exit(1)});
'''
    result = subprocess.run([shutil.which('node'), '-e', code, str(ROOT / 'src/remotion/engine/ColorGrade.js')], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_actor_word_timing_deduplicates_phrase_overlap():
    code = r'''
const fs=require('fs');const assert=require('assert');
(async()=>{
 const {spokenPhraseFrame}=await import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync(process.argv[1],'utf8')).toString('base64'));
 const scene={duration:4,word_cues:[{timed_words:[{index:0,text:'The',start:0},{index:1,text:'Moon',start:.6},{index:2,text:'blocks',start:.9}]},{timed_words:[{index:2,text:'blocks',start:.9},{index:3,text:'sunlight.',start:1.2}]}]};
 assert.equal(spokenPhraseFrame(scene,'Moon blocks sunlight',30),18);
 assert.equal(spokenPhraseFrame(scene,'sunlight',60),72);
 assert.equal(spokenPhraseFrame(scene,'invented actor',30),null);
})().catch(e=>{console.error(e);process.exit(1)});
'''
    result = subprocess.run([shutil.which('node'), '-e', code, str(ROOT / 'src/remotion/engine/DirectorMotion.js')], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_build_cache_pruning_preserves_assets_and_active_renders(tmp_path):
    code = r'''
const fs=require('fs'),path=require('path'),assert=require('assert');
const {pruneGeneratedBundleFiles}=require(process.argv[1]);
const root=process.argv[2];fs.mkdirSync(root,{recursive:true});
const make=(hash)=>{const dir=path.join(root,'bundle-'+hash);fs.mkdirSync(path.join(dir,'public'),{recursive:true});fs.writeFileSync(path.join(dir,'index.html'),'index');fs.writeFileSync(path.join(dir,'bundle.js'),'generated');fs.writeFileSync(path.join(dir,'bundle.js.map'),'generated map');fs.writeFileSync(path.join(dir,'public','asset.png'),'preserve');const old=new Date(Date.now()-7200000);fs.utimesSync(dir,old,old);return dir;};
const obsolete=make('11111111111111111111'),active=make('22222222222222222222'),current=make('33333333333333333333');
fs.writeFileSync(path.join(root,`active-22222222222222222222-${process.pid}.json`),'{}');
const result=pruneGeneratedBundleFiles(root,current,1);
assert.equal(result.files,3);assert.ok(!fs.existsSync(path.join(obsolete,'index.html')));
assert.ok(fs.existsSync(path.join(obsolete,'public','asset.png')));
assert.ok(fs.existsSync(path.join(active,'index.html')));
assert.ok(fs.existsSync(path.join(current,'index.html')));
'''
    result = subprocess.run([shutil.which('node'), '-e', code, str(ROOT / 'scripts/render-remotion-manifest.js'), str(tmp_path / 'cache')], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
