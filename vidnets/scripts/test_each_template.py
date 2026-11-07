"""Test each template individually to find failures."""
import sys, os
from pathlib import Path
os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/..')
sys.path.insert(0, 'server')
os.makedirs('output', exist_ok=True)

from services.native_engine.libraries.vox_templates import VOX_TEMPLATES
from services.native_engine.vox_composer import VoxComposer

PALETTE = {'background': '#0a0a12', 'surface': '#16263b', 'primary': '#f4f0e6', 'accent': '#f2c14e', 'secondary': '#49a6d8', 'danger': '#ef6f6c'}
PROPS = {'text': 'TEST TEXT', 'title': 'TEST', 'kicker': 'KICKER', 'detail': 'Detail text', 'label': 'LABEL',
         'value': '8473', 'quote': 'A test quote.', 'attribution': 'AUTHOR',
         'cause': 'CAUSE', 'effect': 'EFFECT', 'from': 'FROM', 'to': 'TO',
         'headline': 'HEADLINE', 'deck': 'DECK', 'message': 'MESSAGE',
         'stamp': 'STAMP', 'date': 'JULY 20', 'event': 'EVENT',
         'items': [{'label': 'A', 'value': 80}, {'label': 'B', 'value': 50}],
         'line1': 'LINE ONE', 'line2': 'LINE TWO',
         'before_label': 'BEFORE', 'before_value': '$0', 'after_label': 'AFTER', 'after_value': '$1M',
         'left_label': 'LEFT', 'right_label': 'RIGHT', 'left_value': '10', 'right_value': '5',
         'location': 'LOCATION', 'subtitle': 'SUBTITLE', 'source': 'SOURCE',
         '_font': 'BebasNeue-Regular.ttf', '_body_font': 'Poppins-Bold.ttf'}

ok = 0
fail = 0
for tid in sorted(VOX_TEMPLATES.keys()):
    try:
        out_path = str(Path(f'output/test_{tid}.mp4').resolve())
        c = VoxComposer()
        r = c.render({'scenes': [{'duration': 2.0, 'background_asset': '', 'template_id': tid,
                                   'props': PROPS, 'palette': PALETTE, 'image_treatment': 'static_hold',
                                   'transition': {'id': 'fadeblack', 'duration': 0.3}}]},
                     out_path)
        print(f'OK   {tid} ({r["size_mb"]}MB)')
        ok += 1
    except Exception as e:
        err = str(e)[:80]
        print(f'FAIL {tid}: {err}')
        fail += 1

print(f'\n{ok} OK, {fail} FAIL out of {ok+fail}')
