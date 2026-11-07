import sys
sys.path.insert(0, 'server')
from services.editorial_director.script_analyzer import analyze_sentence_triggers

# Test 1: Sentence 3 with split clause
ctx3 = 'Vault security recorded zero alarms, bypassing 100 million lock combinations, 63 security cameras, and infrared Doppler detectors.'
t3 = analyze_sentence_triggers('bypassing 100 million lock combinations,', context_text=ctx3)
print('Test 1 (Chart):', t3.recommended_scene_type, t3.recommended_variant, t3.numbers)

# Test 2: Sentence 5 escape corridor
ctx5 = 'The perpetrators fled from Antwerp across the Belgian border to Italy, discarding DNA evidence on a sandwich crust in a nearby forest.'
t5 = analyze_sentence_triggers('The perpetrators fled from Antwerp across the Belgian border to Italy,', context_text=ctx5)
print('Test 2 (Map):', t5.recommended_scene_type, t5.recommended_variant, t5.country_corridor, t5.locations)

# Test 3: Sentence 6 quote
ctx6 = 'Police detectives later declared: "It was the most sophisticated bank burglary Europe had ever witnessed, leaving 80 percent of the diamonds unrecovered today."'
t6 = analyze_sentence_triggers('"It was the most sophisticated bank burglary Europe had ever witnessed,', context_text=ctx6)
print('Test 3 (Quote):', t6.recommended_scene_type, t6.recommended_variant, t6.quotes)
