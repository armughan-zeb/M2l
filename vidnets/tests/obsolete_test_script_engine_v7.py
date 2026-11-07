
# =============================================================================
# TEST SUITE: Script Engine V7 - Production-Grade Architecture
# =============================================================================
#
# Tests for the 4-layer discipline system:
# 1. Meta-Language Kill Switch
# 2. Sentence Variance (de-AI-fication)
# 3. Banned Phrase Filtering
# 4. Full Generation Pipeline
# =============================================================================

import os
import sys
import re
from pathlib import Path
import logging

# Setup path
sys.path.insert(0, str(Path(__file__).parent.parent / "server"))

from services.script_engine_v7 import (
    ScriptEngineV7, 
    META_LANGUAGE_VIOLATIONS, 
    ACADEMIC_SLOP_PHRASES,
    NARRATOR_ROLE_LOCK
)
from config import BANNED_PHRASES_MASTER

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TestV7")


# =============================================================================
# TEST 1: META-LANGUAGE FILTER
# =============================================================================

def test_meta_language_filter():
    """Test that the meta-language detection correctly identifies violations"""
    logger.info("\n" + "="*60)
    logger.info("TEST 1: Meta-Language Filter")
    logger.info("="*60)
    
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        logger.warning("Skipping API tests - no GROQ_API_KEY")
        # Test the detection logic directly
        engine = type('MockEngine', (), {})()
        engine._check_meta_violations = ScriptEngineV7._check_meta_violations.__get__(engine)
        
        # Test cases that SHOULD trigger violations
        violation_cases = [
            "Here is your script about Rome.",
            "Sure! Let me write about the topic.",
            "Certainly, I'll provide a narration.",
            "Here's the script: The story begins...",
            "As an AI, I cannot...",
        ]
        
        for case in violation_cases:
            has_violation, violations = engine._check_meta_violations(case)
            status = "✅ PASS" if has_violation else "❌ FAIL"
            logger.info(f"   {status}: Detected in '{case[:40]}...' -> {violations}")
        
        # Test cases that should NOT trigger
        clean_cases = [
            "The Roman Empire fell in 476 AD.",
            "Nobody expected what happened next.",
            "But the truth was far stranger.",
        ]
        
        for case in clean_cases:
            has_violation, violations = engine._check_meta_violations(case)
            status = "✅ PASS" if not has_violation else "❌ FAIL (false positive)"
            logger.info(f"   {status}: Clean text '{case[:40]}...'")
    
    logger.info("TEST 1 COMPLETE")


# =============================================================================
# TEST 2: SENTENCE VARIANCE
# =============================================================================

def test_sentence_variance():
    """Test that generated scripts have varied sentence lengths"""
    logger.info("\n" + "="*60)
    logger.info("TEST 2: Sentence Variance (de-AI-fication)")
    logger.info("="*60)
    
    # Sample AI-ish text (bad)
    ai_text = """This is important to note. This is another important point. This is yet another consideration. This represents a significant factor. This demonstrates the key concept."""
    
    # Sample human-like text (good)
    human_text = """Rome fell. Not in a day, not even in a decade. It crumbled slowly, like sand through fingers that tried desperately to hold on. And the strangest part? Nobody at the time realized it was happening."""
    
    def analyze_variance(text: str) -> tuple:
        sentences = [s.strip() for s in text.split('.') if s.strip()]
        lengths = [len(s.split()) for s in sentences]
        if not lengths:
            return 0, 0, 0
        avg = sum(lengths) / len(lengths)
        variance = sum((l - avg) ** 2 for l in lengths) / len(lengths)
        std_dev = variance ** 0.5
        return avg, std_dev, lengths
    
    avg_ai, std_ai, lengths_ai = analyze_variance(ai_text)
    avg_human, std_human, lengths_human = analyze_variance(human_text)
    
    logger.info(f"   AI-ish text: avg={avg_ai:.1f} words, std_dev={std_ai:.1f}")
    logger.info(f"   Sentence lengths: {lengths_ai}")
    
    logger.info(f"   Human-like text: avg={avg_human:.1f} words, std_dev={std_human:.1f}")
    logger.info(f"   Sentence lengths: {lengths_human}")
    
    # Good variance should have std_dev > 3
    if std_human > std_ai:
        logger.info("   ✅ PASS: Human text has more variance")
    else:
        logger.info("   ❌ FAIL: AI text has more variance (unexpected)")
    
    logger.info("TEST 2 COMPLETE")


# =============================================================================
# TEST 3: BANNED PHRASES
# =============================================================================

def test_banned_phrases():
    """Test that all banned phrases would be detected"""
    logger.info("\n" + "="*60)
    logger.info("TEST 3: Banned Phrases Cross-Check")
    logger.info("="*60)
    
    all_banned = BANNED_PHRASES_MASTER + ACADEMIC_SLOP_PHRASES
    logger.info(f"   Total banned phrases: {len(all_banned)}")
    
    # Sample some key phrases
    critical_bans = [
        "delve", "journey", "tapestry", "testament",
        "in today's world", "it's important to note",
        "let's dive in", "without further ado",
    ]
    
    for phrase in critical_bans:
        if phrase in all_banned or phrase.lower() in [p.lower() for p in all_banned]:
            logger.info(f"   ✅ '{phrase}' is banned")
        else:
            logger.info(f"   ❌ '{phrase}' is NOT banned (should be)")
    
    logger.info("TEST 3 COMPLETE")


# =============================================================================
# TEST 4: ROLE LOCK VERIFICATION
# =============================================================================

def test_role_lock():
    """Verify the role lock prompt contains required constraints"""
    logger.info("\n" + "="*60)
    logger.info("TEST 4: Role Lock Verification")
    logger.info("="*60)
    
    required_constraints = [
        "NEVER", "here is", "below is", "as an AI",
        "spoken narration", "human voice", "FAILED"
    ]
    
    for constraint in required_constraints:
        if constraint.lower() in NARRATOR_ROLE_LOCK.lower():
            logger.info(f"   ✅ Role lock includes: '{constraint}'")
        else:
            logger.info(f"   ❌ Role lock MISSING: '{constraint}'")
    
    logger.info("TEST 4 COMPLETE")


# =============================================================================
# TEST 5: FULL GENERATION (API Required)
# =============================================================================

def test_full_generation():
    """End-to-end generation test"""
    logger.info("\n" + "="*60)
    logger.info("TEST 5: Full Generation Pipeline")
    logger.info("="*60)
    
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        logger.warning("   ⚠️ Skipping - GROQ_API_KEY not set")
        return
    
    try:
        engine = ScriptEngineV7(api_key=api_key)
        
        title = "Why Cats Rule the Internet"
        duration = "30 seconds"
        style = "Humorous, punchy, surprising"
        
        logger.info(f"   Generating: '{title}' ({duration})")
        script = engine.generate_script(title, duration, style)
        
        # Validation checks
        word_count = len(script.split())
        logger.info(f"   Word count: {word_count}")
        
        # Check for meta-language violations
        violations_found = []
        script_lower = script.lower()
        for phrase in META_LANGUAGE_VIOLATIONS:
            if phrase.lower() in script_lower:
                violations_found.append(phrase)
        
        if violations_found:
            logger.error(f"   ❌ FAIL: Meta-language found: {violations_found}")
        else:
            logger.info("   ✅ PASS: No meta-language violations")
        
        # Check for banned phrases
        banned_found = []
        for phrase in BANNED_PHRASES_MASTER[:20]:  # Check first 20
            if phrase.lower() in script_lower:
                banned_found.append(phrase)
        
        if banned_found:
            logger.warning(f"   ⚠️ Banned phrases found: {banned_found}")
        else:
            logger.info("   ✅ PASS: No common banned phrases")
        
        # Print script
        logger.info("\n" + "-"*40)
        logger.info("GENERATED SCRIPT:")
        logger.info("-"*40)
        print(script)
        logger.info("-"*40)
        
    except Exception as e:
        logger.error(f"   ❌ Generation failed: {e}", exc_info=True)
    
    logger.info("TEST 5 COMPLETE")


# =============================================================================
# RUN ALL TESTS
# =============================================================================

def run_all_tests():
    """Run the complete test suite"""
    print("\n" + "="*60)
    print("🧪 SCRIPT ENGINE V7 - TEST SUITE")
    print("="*60)
    
    test_role_lock()
    test_meta_language_filter()
    test_sentence_variance()
    test_banned_phrases()
    test_full_generation()
    
    print("\n" + "="*60)
    print("✅ ALL TESTS COMPLETE")
    print("="*60)


if __name__ == "__main__":
    run_all_tests()
