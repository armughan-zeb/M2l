"""Pipeline analysis test — query generation for both YouTube and Web image pipelines."""

import sys, os, json, re, unittest
from collections import Counter
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services import youtube_clips_service as ycs
from services.web_image import query as webq

SCRIPT = """You ever drive home and realize you remember nothing about the trip? You merged, turned, stopped at lights, and your conscious mind was completely checked out.

That's your brain on shortcuts. And it's doing it right now.

Your brain is lazy by design. It weighs about three pounds but uses twenty percent of your body's energy. That's expensive. So evolution built a system to conserve power. Instead of thinking through every single decision from scratch, your brain builds mental shortcuts called heuristics.

Think of them as cached files. Once you've done something enough times, your brain saves the pattern. Driving a familiar route. Tying your shoes. Recognizing a friend's face. These tasks get shifted from your conscious, effortful system to your automatic, effortless system. You free up mental RAM for bigger problems.

But here's the catch. The same machinery that helps you drive home on autopilot also keeps you doomscrolling at midnight. Because shortcuts aren't just for physical habits. They govern your cravings, your attention, your decisions.

When you scroll social media and hit a funny video, your brain releases a small pulse of dopamine. That feels good. So your brain learns: scroll, find reward, repeat. It builds a shortcut. Soon you don't decide to open the app. Your thumb does it before you've even thought about it.

This is the dopamine trap. Quick wins feel great, so your brain starts chasing the next quick win. It avoids the discomfort of effort. Reading a long article? That takes energy. Watching a ten-second clip? That's a shortcut. Your brain picks the shortcut every time.

Researchers call this cognitive load management. When your brain is tired, stressed, or overloaded, it defaults to heuristic-based thinking. It grabs the fastest answer, not the best one. That's why you impulse buy at the checkout line. Why you reach for sugar when you're exhausted. Why you believe the first headline you see.

Your brain isn't trying to be accurate. It's trying to be efficient.

And the problem is, these shortcuts aren't just neutral. They rewire you. When shortcuts become a pattern, they reshape your neural pathways. The more you take the easy route, the harder the hard route feels. You train your brain to avoid depth.

But there's good news. You can teach your brain new tricks.

Awareness is the first step. When you notice yourself reaching for your phone, pause. That urge is a shortcut firing. Just naming it weakens its power. You interrupt the automatic loop.

Next, you can deliberately create friction. Put your phone in another room. Use a website blocker. Make the shortcut harder to take. Your brain will resist at first because it craves efficiency. But if you force it to use effort, it adapts. New shortcuts form.

The most powerful tool is conscious repetition. Every time you choose the harder path, you strengthen a new neural connection. Over weeks, that effortful choice becomes automatic. You build a shortcut that serves you instead of sabotaging you.

That's the irony. Your brain loves shortcuts so much that you can use that love against it. Train it to shortcut toward good habits instead of bad ones.

So next time you catch yourself on autopilot, don't fight it. Just redirect it. Your brain is the most efficient machine in the universe. It just needs a better program to run."""

TITLE = "You ever drive home and realize"

def split_script_into_scenes(script):
    paragraphs = [p.strip() for p in script.split("\n\n") if p.strip()]
    return paragraphs

class TestPipelineAnalysis(unittest.TestCase):
    maxDiff = None

    def test_02_youtube_mechanical_queries(self):
        """Analyze YouTube mechanical query generation."""
        scenes = split_script_into_scenes(SCRIPT)
        queries = ycs._generate_queries_mechanical(scenes, TITLE)

        print(f"\n{'='*60}")
        print(f"YouTube Mechanical Query Generation ({len(queries)} scenes)")
        print(f"{'='*60}")

        weak_count = 0
        for q in queries:
            primary = q.get("search_query", "")
            alt = q.get("alt_search_query", "")
            score = self._rate_youtube_query(primary)
            if score < 3:
                weak_count += 1
            print(f"  Scene {q['scene_number']:2d}: [{score}/5] q={primary!r}")
            print(f"         alt={alt!r}")

        print(f"\n  Weak queries (score<3): {weak_count}/{len(queries)}")
        self.assertGreater(len(queries), 0)

    def test_03_web_image_subject_extraction(self):
        """Analyze Web Image subject extraction."""
        effective_title, words = webq._derive_effective_web_title(TITLE, SCRIPT)

        print(f"\n{'='*60}")
        print("Web Image Subject Extraction")
        print(f"{'='*60}")
        print(f"Effective title: {effective_title!r}")
        print(f"Subject words:  {words}")
        print(f"Topic family:   {webq._topic_family(effective_title)}")

        self.assertIsInstance(effective_title, str)
        self.assertNotEqual(effective_title, "Train It",
            "Web subject should not be 'Train It' (cricket bias bug)")

    def test_04_web_image_query_generation(self):
        """Analyze Web Image query generation for each scene."""
        scenes = split_script_into_scenes(SCRIPT)
        effective_title, _ = webq._derive_effective_web_title(TITLE, SCRIPT)

        print(f"\n{'='*60}")
        print(f"Web Image Query Generation ({len(scenes)} scenes)")
        print(f"{'='*60}")
        print(f"Effective subject: {effective_title!r}")
        print(f"{'='*60}")

        total = 0
        weak = 0
        for i, scene in enumerate(scenes):
            fallbacks = webq._topic_fallback_queries(effective_title, scene, limit=5)
            print(f"\n  Scene {i+1}: {scene[:60]}...")
            for q in fallbacks[:5]:
                is_weak = webq._query_is_weak_or_too_specific(q, effective_title, scene)
                has_leak = webq._query_has_leakage(q, effective_title, scene, SCRIPT)
                flag = "W" if is_weak else ("L" if has_leak else "OK")
                total += 1
                if is_weak or has_leak:
                    weak += 1
                print(f"    [{flag}] {q!r}")

        print(f"\n  Total queries: {total}, Weak/leaky: {weak}/{total}")

    def _rate_youtube_query(self, query: str) -> int:
        words = query.strip().split()
        if len(words) >= 8:
            return 1
        if len(words) >= 6:
            return 2
        lower = query.lower()
        narrative = {"when", "while", "during", "after", "before", "because",
                     "your", "youre", "youll", "youve", "dont", "wont", "cant",
                     "isnt", "doesnt", "didnt"}
        if any(re.search(r'\b' + w + r'\b', lower) for w in narrative):
            return 2
        visual = {"car", "driving", "road", "rain", "night", "highway", "brain",
                  "dopamine", "mental", "focus", "meditation", "habit", "addiction",
                  "psychology", "motivation", "mind", "storm", "traffic", "city",
                  "dark", "window", "light", "office", "desk", "computer", "phone",
                  "crowd", "street", "nature", "ocean", "mountain", "forest"}
        vis_count = sum(1 for w in words if w.lower() in visual)
        if len(words) <= 2 and vis_count == 0:
            return 2
        if len(words) <= 2 and vis_count > 0:
            return 4
        if 3 <= len(words) <= 5 and vis_count >= 1:
            return 5
        if 3 <= len(words) <= 5:
            return 3
        return 2


if __name__ == "__main__":
    unittest.main()
