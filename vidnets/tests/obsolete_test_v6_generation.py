
import os
import sys
from pathlib import Path
import logging

# Setup path
sys.path.insert(0, str(Path(__file__).parent.parent / "server"))

from services.script_engine_v6 import ScriptEngineV6

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TestV6")

def test_generation():
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        logger.error("GROQ_API_KEY not found in environment variables.")
        return

    logger.info("Initializing ScriptEngineV6...")
    engine = ScriptEngineV6(api_key=api_key)

    title = "Why Cats Are Secretly Geniuses"
    duration = "1 minute"
    style_prompt = "Humorous, scientific but fun"

    logger.info(f"Generating script for: '{title}' ({duration})")
    
    try:
        script = engine.generate_script(title, duration, style_prompt)
        
        logger.info("\n" + "="*50)
        logger.info("GENERATED SCRIPT START")
        logger.info("="*50)
        print(script)
        logger.info("="*50)
        logger.info("GENERATED SCRIPT END")
        logger.info("="*50)

        # Basic Validation
        if "Here is your script" in script:
            logger.error("FAILED: 'Here is your script' meta-text found.")
        else:
            logger.info("PASSED: No meta-text found.")
            
        if "**" in script:
             logger.warning("WARNING: Markdown '**' found (should be stripped).")
        
        words = len(script.split())
        logger.info(f"Word Count: {words}")
        
    except Exception as e:
        logger.error(f"Generation Failed: {e}", exc_info=True)

if __name__ == "__main__":
    test_generation()
