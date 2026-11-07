"""
Reddit Source — Public JSON API (no auth).

Searches multiple subreddits for image posts.
Uses Reddit's public .json endpoint: reddit.com/r/{sub}/search.json
Filters for image domains (i.redd.it, i.imgur.com, preview.redd.it).
"""
from __future__ import annotations

import re
import time
from typing import Dict, List, Optional

import requests

from .base_source import BaseSource, ImageCandidate


IMAGE_DOMAINS = {
    'i.redd.it', 'i.imgur.com', 'preview.redd.it', 'imgur.com',
    'i.stack.imgur.com', 'v.redd.it',
}

REDDIT_API = "https://www.reddit.com"

DEFAULT_SUBREDDITS = {
    'cricket': ['Cricket', 'cricketshitposting', 'IndianCricketTeam'],
    'pakistan_cricket': ['Cricket', 'PakistanCricket', 'PakCricket', 'PSL'],
    'football': ['soccer', 'football', 'PremierLeague', 'ChampionsLeague', 'footballhighlights'],
    'tennis': ['tennis', '10s', 'sports'],
    'sports': ['sports', 'sport'],
    'tech': ['technology', 'gadgets', 'hardware'],
    'history': ['HistoryPorn', 'OldSchoolCool', 'AskHistorians'],
    'true_crime': ['TrueCrime', 'crime', 'UnsolvedMysteries'],
    'gaming': ['gaming', 'Games', 'pcgaming'],
    'celebrity': ['celebrities', 'movies', 'Entertainment', 'popculturechat', 'Fauxmoi'],
    'lifestyle': ['celebrities', 'popculturechat', 'fashion', 'pics'],
    'nature': ['NatureIsFuckingLit', 'wildlife', 'EarthPorn'],
    'business': ['business', 'finance', 'economics'],
    'science': ['space', 'science', 'astronomy'],
    'food': ['food', 'Cooking', 'EatCheapAndHealthy', 'foodporn'],
    'travel': ['travel', 'TravelPorn', 'solotravel'],
    'politics': ['politics', 'worldnews', 'news'],
    'pakistan_politics': ['pakistan', 'PAK', 'worldnews', 'news'],
}


class RedditSource(BaseSource):
    name = "reddit"
    trust_score = 0.85
    tier = 0
    max_results_per_query = 30
    timeout = 10.0

    def __init__(self):
        super().__init__()
        self.session = self._create_session()
        self._last_request = 0.0

    def _create_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'VidzeoLocal/1.0 (vidzeo.app; media pipeline)',
            'Accept': 'application/json',
        })
        return session

    def _rate_limit(self):
        elapsed = time.time() - self._last_request
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        self._last_request = time.time()

    def search(
        self,
        query: str,
        limit: int = 20,
        filters: Optional[Dict] = None,
    ) -> List[ImageCandidate]:
        filters = filters or {}
        subreddits = filters.get('subreddits', [])
        sort = filters.get('sort', 'top')
        time_filter = filters.get('time', 'year')

        if not subreddits:
            subreddits = ['Cricket', 'sports', 'news', 'pics']

        return self._search_multi_sub(
            query, subreddits, limit, sort, time_filter
        )

    def _search_multi_sub(
        self,
        query: str,
        subreddits: List[str],
        limit: int,
        sort: str,
        time_filter: str,
    ) -> List[ImageCandidate]:
        all_candidates = []
        seen_ids = set()
        sub_limit = max(5, limit // max(len(subreddits), 1))

        for sub in subreddits:
            if len(all_candidates) >= limit:
                break
            candidates = self._search_subreddit(
                sub, query, sub_limit, sort, time_filter
            )
            for c in candidates:
                if c.id not in seen_ids:
                    seen_ids.add(c.id)
                    all_candidates.append(c)

        return all_candidates[:limit]

    def _search_subreddit(
        self,
        subreddit: str,
        query: str,
        limit: int,
        sort: str,
        time_filter: str,
    ) -> List[ImageCandidate]:
        clean = self._clean_query(query, max_words=6)
        if not clean:
            return []

        candidates = []
        try:
            self._rate_limit()
            url = f"{REDDIT_API}/r/{subreddit}/search.json"
            params = {
                'q': clean,
                'sort': sort,
                't': time_filter,
                'limit': min(limit * 2, 100),
                'restrict_sr': 'on',
            }
            resp = self.session.get(url, params=params, timeout=self.timeout)
            if resp.status_code != 200:
                return []

            data = resp.json()
            posts = data.get('data', {}).get('children', [])

            for post_data in posts:
                if len(candidates) >= limit:
                    break

                post = post_data.get('data', {})
                candidate = self._post_to_candidate(post, subreddit)
                if candidate:
                    candidates.append(candidate)

        except Exception:
            pass

        return candidates

    def _post_to_candidate(
        self, post: Dict, subreddit: str
    ) -> Optional[ImageCandidate]:
        title = post.get('title', '')
        url = post.get('url', '')
        domain = post.get('domain', '')
        score = post.get('score', 0)
        post_id = post.get('id', '')
        permalink = post.get('permalink', '')

        if post.get('over_18') or post.get('quarantine'):
            return None

        if score < 10:
            return None

        if domain not in IMAGE_DOMAINS and not self._is_valid_image_url(url):
            return None

        preview = post.get('preview', {})
        preview_url = ''
        width = 0
        height = 0

        if preview:
            images = preview.get('images', [])
            if images:
                img = images[0]
                resolutions = img.get('resolutions', [])
                if resolutions:
                    best = resolutions[-1]
                    width = best.get('width', 0)
                    height = best.get('height', 0)
                    preview_url = best.get('url', '')
                    preview_url = preview_url.replace('&amp;', '&')

        if not preview_url and url:
            preview_url = url

        if not self._is_valid_image_url(preview_url) and domain not in IMAGE_DOMAINS:
            return None

        download_url = url
        if post.get('post_hint') == 'image' and preview.get('images'):
            img = preview['images'][0]
            source = img.get('source', {})
            if source.get('url'):
                download_url = source['url'].replace('&amp;', '&')
                width = source.get('width', width)
                height = source.get('height', height)

        description = f"r/{subreddit} | Score: {score} | {title}"

        return ImageCandidate(
            id=f"reddit_{post_id}",
            source='reddit',
            title=title[:200],
            description=description,
            download_url=self._normalize_url(download_url),
            preview_url=self._normalize_url(preview_url),
            width=width,
            height=height,
            media_type='image',
            license="User-generated (CC by author)",
            categories=[subreddit],
            metadata={
                'subreddit': subreddit,
                'score': score,
                'author': post.get('author', ''),
                'permalink': f"https://reddit.com{permalink}" if permalink else '',
            },
        )

    def search_top_posts(
        self,
        subreddits: List[str],
        limit: int = 20,
        time_filter: str = "year",
    ) -> List[ImageCandidate]:
        """Get top image posts from subreddits without a search query."""
        all_candidates = []
        seen_ids = set()
        sub_limit = max(5, limit // max(len(subreddits), 1))

        for sub in subreddits:
            if len(all_candidates) >= limit:
                break
            candidates = self._get_top_posts(sub, sub_limit, time_filter)
            for c in candidates:
                if c.id not in seen_ids:
                    seen_ids.add(c.id)
                    all_candidates.append(c)

        return all_candidates[:limit]

    def _get_top_posts(
        self, subreddit: str, limit: int, time_filter: str
    ) -> List[ImageCandidate]:
        candidates = []
        try:
            self._rate_limit()
            url = f"{REDDIT_API}/r/{subreddit}/top.json"
            params = {'t': time_filter, 'limit': min(limit * 2, 100)}
            resp = self.session.get(url, params=params, timeout=self.timeout)
            if resp.status_code != 200:
                return []

            data = resp.json()
            posts = data.get('data', {}).get('children', [])

            for post_data in posts:
                if len(candidates) >= limit:
                    break
                post = post_data.get('data', {})
                candidate = self._post_to_candidate(post, subreddit)
                if candidate:
                    candidates.append(candidate)
        except Exception:
            pass

        return candidates


def search_reddit(
    query: str,
    subreddits: List[str] = None,
    limit: int = 20,
) -> List[Dict]:
    source = RedditSource()
    filters = {'subreddits': subreddits or ['Cricket', 'sports', 'news']}
    items = source.search(query, limit=limit, filters=filters)
    return [item.to_dict() for item in items]


if __name__ == "__main__":
    print("Testing Reddit source...")
    source = RedditSource()

    results = source.search(
        "Virat Kohli century",
        limit=5,
        filters={
            'subreddits': ['Cricket', 'IndianCricketTeam'],
            'sort': 'top',
            'time': 'year',
        },
    )
    print(f"Found {len(results)} results:")
    for r in results:
        print(f"  - {r.title[:60]} (score={r.metadata.get('score', 0)})")
        print(f"    {r.download_url[:80]}...")

    print()
    results2 = source.search_top_posts(
        ['EarthPorn', 'NatureIsFuckingLit'],
        limit=3,
        time_filter='week',
    )
    print(f"Top posts: {len(results2)} results")
    for r in results2:
        print(f"  - {r.title[:60]} (r/{r.categories[0]})")
