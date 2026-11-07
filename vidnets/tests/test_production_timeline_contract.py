import copy
from services.editorial_director.production_contract import compile_production_timeline

def test_recompile_preserves_editor_windows_and_track_muting():
    overrides = {'version':1,'clock':'film_global_frames','fps':30,'tracks':{'graphics':{'enabled':False,'clips':[]},'copy':{'enabled':True,'clips':[{'scene_index':0,'start_frame':30,'duration_frames':45}]},'sfx':{'enabled':True,'clips':[{'scene_index':0,'start_frame':50,'duration_frames':18,'source_start_frame':4}]}}}
    film = {'fps':30,'duration':4,'scenes':[{'start':0,'duration':4,'primary_visual':{'type':'SmartImage'},'narration':'A reef.'}],'editor_timeline':copy.deepcopy(overrides)}
    compile_production_timeline(film)
    assert film['editor_timeline']==overrides
    compile_production_timeline(film)
    assert film['editor_timeline']==overrides


def test_copy_sound_follows_final_copy_window_and_orphans_are_removed():
    cue = {'event_id': 'scene:0:copy:reveal', 'visual_trigger': True,
           'time_local': .1, 'transient_offset_seconds': .2, 'trim_start_seconds': .05}
    scene = {'start': 2, 'duration': 4, 'narration': 'We see the Moon.',
             'primary_visual': {'type': 'KineticText'},
             'editorial_contract': {'version': 4, 'source': 'llm', 'approved': True,
                                   'source_span': 'Moon', 'display_text': 'Moon'},
             'motion_cues': [{'element': 'copy', 'time_local': 1.6}],
             'sound_cues': [copy.deepcopy(cue)]}
    film = {'fps': 30, 'duration': 6, 'scenes': [scene],
            'audio': {'effects': [copy.deepcopy(cue)]}}
    compile_production_timeline(film)
    sound = scene['sound_cues'][0]
    visual = scene['copy_window']['start_frame'] / 30
    assert abs(sound['time_local'] + .15 - visual) < .001
    assert sound['timing_quality'] == 'copy_window_aligned'
    assert sound['sound_anchor'] == 'Moon'
    assert film['audio']['effects'][0]['time_local'] == sound['time_local']
    scene['editorial_contract']['approved'] = False
    compile_production_timeline(film)
    assert scene['sound_cues'] == []
    assert film['audio']['effects'] == []
    assert film['editing_timeline']['tracks']['sfx'] == []
