
import unittest
import sys
import os
from pathlib import Path

# Add server directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "server"))

from services.script_engine_short_form import (
    generate_short_form_script, 
    calculate_target_words,
    analyze_hook_strength,
    clean_script,
    SHORT_FORM_STYLES
)

class TestShortFormEngine(unittest.TestCase):
    
    def test_word_count_calc(self):
        """Test strict word count calculations"""
        self.assertEqual(calculate_target_words("15 seconds"), 40)
        self.assertEqual(calculate_target_words("30 seconds"), 80)
        self.assertEqual(calculate_target_words("60 seconds"), 160)
        self.assertEqual(calculate_target_words("90 seconds"), 240)
        # Default
        self.assertEqual(calculate_target_words("unknown"), 80)
        
    def test_hook_analysis(self):
        """Test hook strength scorer"""
        # Weak hook
        weak = "So basically today I want to talk about AI."
        score = analyze_hook_strength(weak)
        # Score starts at 100, -40 for weak starter = 60
        self.assertLessEqual(score['score'], 60)
        self.assertIn("Weak starter word", score['issues'])
        
        # Power hook
        strong = "Only 3% of people know this secret about AI."
        score = analyze_hook_strength(strong)
        self.assertGreater(score['score'], 80)
        
    def test_clean_script(self):
        """Test banned phrase removal"""
        dirty = "Hello everyone! In this video I will show you AI. Don't forget to subscribe!"
        clean = clean_script(dirty)
        self.assertNotIn("In this video", clean)
        self.assertNotIn("subscribe", clean)
        self.assertIn("AI", clean)

    def test_styles_exist(self):
        """Verify all viral styles are defined"""
        expected = ["viral_hook", "fast_story", "fact_bombs", "tutorial_quick", "hot_take", "list_rapid"]
        for style in expected:
            self.assertIn(style, SHORT_FORM_STYLES)
            self.assertTrue(len(SHORT_FORM_STYLES[style]['instructions']) > 50)

    def test_fallback_generation(self):
        """Test fallback generation works without API key"""
        result = generate_short_form_script(
            title="Test Topic",
            duration="30 seconds",
            style="viral_hook",
            api_key=None  # Force fallback
        )
        self.assertTrue(result['fallback'])
        self.assertGreater(result['word_count'], 10)
        self.assertEqual(result['style_used'], "viral_hook")

if __name__ == '__main__':
    unittest.main(verbosity=2)
