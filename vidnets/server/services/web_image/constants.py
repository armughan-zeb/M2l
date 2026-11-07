"""Shared constants for web image pipeline."""
from typing import Dict, Set

BING_SEARCH_URL = "https://www.bing.com/images/search"

SEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Referer": "https://www.google.com/",
}

STOPWORDS: Set[str] = {
    "of", "in", "on", "at", "to", "by", "an", "a", "the", "and", "for", "with", "that", "this", "from", "into", "they", "them",
    "their", "there", "about", "after", "before", "while", "when", "what", "why",
    "as", "if", "or", "so", "up", "out", "down", "off", "then", "than",
    "he", "his", "him", "she", "her", "hers", "does", "doesnt", "don't", "dont",
    "did", "didnt", "isnt", "isn't", "wasnt", "wasn't",
    "have", "has", "had", "were", "was", "will", "would", "could", "should",
    "around", "under", "over", "through", "because", "during", "being",
    "just", "really", "right", "left", "very", "then", "than", "also", "video",
    "image", "images", "photo", "photos", "picture", "pictures", "footage",
    "scene", "moment", "today", "now", "latest", "news", "report", "reports",
    "analysis", "explained", "global", "coverage", "comment", "comments",
    "statement", "statements", "one", "ones", "someone", "something", "world",
    "strong", "strongest", "question", "not", "problem", "problems", "stage",
    "former", "political", "trust", "issue", "issues", "matter", "matters",
    "reality", "change", "changes", "thing", "things", "current",
    "past", "future", "present", "allies", "ally",
    "start", "starting", "read", "reading", "write", "writing", "take", "taking",
    "make", "making", "use", "using", "get", "getting", "go", "going",
    "its", "still", "upon", "across", "since",
    "but", "yet", "how", "these", "never", "ever", "even", "much", "many",
    "each", "every", "all", "any", "few", "more", "most", "other", "some",
    "such", "only", "own", "same", "too", "well", "back", "still",
    "are", "is", "was", "were", "been", "being", "am",
    "five", "six", "seven", "eight", "nine", "ten", "hundred", "thousand",
    "first", "second", "third", "last", "next", "another", "different",
    "together", "between", "without", "within", "along", "either", "neither",
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
}

LOW_CONFIDENCE_DOMAINS: Set[str] = {
    "tumblr.com", "defencetalk.com", "alchetron.com",
    "stablediffusionweb.com", "lexica.art", "seaart.ai", "midjourney.com",
    "polycount.com", "stock.adobe.com", "freepik.com", "vecteezy.com", "pixabay.com",
    "touristsecrets.com", "genius.com", "guinnessworldrecords.com",
    "msn.com", "scribd.com", "fity.club", "pump.fun", "opensea.io",
    "ifunny.co", "ragbong.blogspot.com", "draftsfromalgfg.blogspot.com", "promptally.com",
    "goldretired.com", "hotnews.otoarizasi.com", "edition.chainityai.com",
    "slaylebrity.com", "ansarisahab.com", "iphonewired.com",
    "binance.com", "edlainelima.github.io", "animalia-life.club", "editorialge.com",
    "craiyon.com", "xlonmusk.com", "ceoinfluencers.com",
    "academia.edu", "alibaba.com", "haikudeck.com", "ipic.ai", "exprofitness.com",
    "trainheroic.com", "shop.spartan.com",
    "clipart", "vectorstock", "123rf.com", "dreamstime.com", "depositphotos.com",
    "istockphoto.com", "gettyimages.com",
    "daraz.pk", "kitaabnow.com", "primeurdunovels.com", "universalbooksellers.com",
    "studentbookstore.pk", "linkshop.pk", "balonim.net",
    "meghatoszulinapiversek.hu", "matricakeszites.hu", "globox1997.github.io",
    "n-i-s.cz", "wroclawzbliska.pl", "pissedebout.fr", "sarb-hasbergen.com",
    "youareherehongkong.wordpress.com", "trainjebrein.nl",
    "ar.pngtree.com", "pngtree.com", "fineartamerica.com",
    "passionmilitaria.com", "capit.co.uk", "grandoptical.com",
    "booksamillion.com",
    "springer.com", "link.springer.com",
    "rundreisen.de", "onedio.com", "edrawsoft.com", "indiamart.com",
    "cubieserver.de", "blog.cubieserver.de",
    "gadgethacks.com", "samsung.gadgethacks.com",
    "hawtcelebs.com", "internewscast.com", "tvline.com", "healthyceleb.com",
    "yachtandboat.com.au", "ekimatravel.com",
    "rakuten.co.jp", "item.rakuten.co.jp",
    "androidsage.com", "makeuseof.com",
    "tripadvisor.com", "tripadvisor.com.tr", "tripadvisor.co.uk",
    "iol.co.za",
    "333travel.nl", "travellersoftheworld.nl",
    "hitechmarine.com.au", "quinmarine.com.au", "tackleworldmiami.com.au",
    "wccf.com.au", "celebmafia.com", "screenrant.com",
    "wendangwang.com", "digitalspy.com", "listal.com",
    "imdb.com", "themoviedb.org", "tvinsider.com", "diesuid-afrikaner.com",
    "dalekohledy.cz", "brisoft.ch", "borkies.de", "ofertecatalog.ro",
    "adobe.com", "cartamz.com", "motomegane.com", "malivrtuljak.hr",
    "bolha.com", "behance.net",
}

REJECT_TEXT_SIGNALS: Set[str] = {
    "nsfw", "porn", "nude", "naked", "explicit", "graphic", "gore", "bloody",
    "meme", "template", "wallpaper", "fan art", "fanart",
    "collage", "screenshot", "reaction image", "tweet", "instagram story",
    "tiktok", "reel", "shorts", "logo", "poster", "infographic",
    "ai generated", "ai-generated", "midjourney", "stable diffusion", "dall-e",
    "generative ai", "3d render", "rendered", "digital art", "ai art",
    "artstation", "civitai",
}

WATERMARK_SIGNALS: Set[str] = {
    "getty", "shutterstock", "alamy", "istock", "dreamstime", "depositphotos",
    "123rf", "stock photo", "watermark", "watermarked",
}

SUPPORTED_IMAGE_EXTS: Set[str] = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}

PERCEPTUAL_HASH_SIZE = 16
PERCEPTUAL_HASH_THRESHOLD = 12

BAD_QUERY_TERMS: Set[str] = {
    "constructed", "unmatched", "moments", "ability", "delivered", "defining",
    "attributes", "masterclass", "fortitude", "brilliance", "extraordinary",
    "remarkable", "incredible", "exceptional", "outstanding", "magnificent",
    "sheer", "artistry", "trademark", "strokeplay", "knock",
    "career", "legacy", "rise", "fall", "journey", "story", "history",
    "world", "power", "scaling", "explosive", "protectiveness", "comedic",
    "fierce", "occasional", "discussing", "discuss", "cannot", "perhaps",
    "certainly", "actually",
    "truly", "really", "very", "quite", "rather", "somewhat", "perhaps",
    "prove", "proven", "proved", "proving", "testament", "embodiment",
    "arguably", "undeniably", "undeniable", "simply", "merely", "barely",
    "former", "latter", "given", "despite", "regardless", "nevertheless",
    "furthermore", "moreover", "however", "although", "whereas", "thereby",
    "hence", "thus", "therefore", "consequently", "accordingly",
    "representation", "symbol", "symbolic", "symbolism", "metaphor",
    "narrative", "depicts", "depicting", "portrayal", "portrayed",
    "essence", "embodies", "embodied", "reflecting", "demonstrates",
    "illustrates", "showcases", "captures", "encapsulates", "exemplifies",
    "mental", "sight", "identifies", "remain", "remains", "masterclasses",
    "constructing", "underlying", "fundamental", "inherent",
    "profound", "resilient", "resilience", "transformative", "transformation",
    "transcends", "transcendent", "exemplifies", "quintessential",
    "strength", "weaknesses", "loses", "gaining",
    "allows", "allow", "execute", "executed", "executing", "execution",
    "exploits", "exploited", "exploiting",
    "ruthless", "ruthlessly", "efficiency", "efficient",
    "bigger", "reminder", "greatness", "blueprints", "touchstones",
    "aspiring", "aspirations", "aspirant", "aspire",
    "meeting", "inspiring", "inspired", "inspiration", "inspire",
    "elevate", "elevated", "elevates", "elevating",
    "separates", "separated", "separate", "separating",
    "accumulates", "accumulate", "accumulated", "accumulating",
    "respond", "responds", "responded", "responding",
    "inspires", "artist", "leader", "leaders", "leadership",
    "technical", "proficiency", "foundation", "foundations",
    "mental", "mental", "ability", "abilities",
    "higher", "highest", "lower", "lowest",
    "touchstone", "touchstones", "blueprint", "masterclass",
    "stakes", "stake", "courage", "talent", "talented",
    "peer", "peers", "peerless",
    "context", "contexts", "conditions", "condition",
    "define", "defined", "defines", "defining",
    "picture", "pictures", "success", "successful",
    "achievement", "achievements", "achievement",
    "impact", "lasting", "impactful",
    "assume", "assumes", "assumed", "suggest", "suggests", "suggested",
    "believe", "believes", "believed", "think", "thought", "claim", "claims", "claimed",
    "reality", "dollars", "evidence", "supposed", "supposedly",
    "realize", "realized", "wonder", "wondered", "editorial",
}

BING_IMAGE_URL = "https://www.bing.com/images/search?q="

# Terms that should NEVER be used for cricket subjects
NON_CRICKET_SPORTS_TERMS = {
    "dunk", "goal", "penalty", "free kick", "passing", "shooting",
    "dribbling", "tackling", "swimming", "sprint", "marathon",
    "knockout", "championship", "touchdown", "serve", "ace",
    "volley", "header", "throw", "pitch", "strike", "spike",
    "putt", "birdie", "lap", "heat", "medley",
}

# Terms that should NEVER be used for subjects other than cricket
NON_FOOTBALL_TERMS = {"wicket", "stumps", "crease", "googly", "leg spin", "off spin"}
NON_BASKETBALL_TERMS = {"batting", "bowling", "wicket", "stumps", "crease"}

# Cricket-only enrichments — no cross-sport pollution
CRICKET_ENRICHMENTS = [
    "batting", "bowling", "fielding", "catch", "six", "century",
    "wicket", "celebration", "trophy", "medal", "match",
    "stadium", "training", "warmup", "cover drive", "pull shot",
    "fast bowling", "spin bowling", "wicket keeping", "innings",
    "test match", "cup final", "tournament", "batting action",
    "cricket shot", "boundary", "running between wickets",
    "straight drive", "square cut", "on drive", "leg glance",
    "cricket portrait", "cricket action", "cricket batting",
    "cricket training", "cricket celebration", "cricket catch",
    "batsman", "bowler", "fielder",
]

VISUAL_ENRICHMENTS = {
    "person": [
        "portrait", "photo", "headshot", "speaking", "walking", "waving",
        "smiling", "training", "practice", "press conference", "interview",
        "celebration", "performance", "action", "official",
        "formal", "victory", "defeat", "emotional", "standing", 
        "sitting", "looking", "close up", "in action", "playing", 
        "celebrating", "on field",
    ],
    "cricket": [
        "batting", "bowling", "fielding", "catch", "six", "century",
        "wicket", "celebration", "trophy", "medal", "match",
        "stadium", "training", "warmup", "cover drive", "pull shot",
        "fast bowling", "spin bowling", "wicket keeping", "innings",
        "test match", "cup final", "tournament", "batting action",
        "cricket shot", "boundary", "running between wickets",
        "straight drive", "square cut", "on drive", "leg glance",
        "cricket portrait", "cricket action", "cricket batting",
        "cricket training", "cricket celebration", "cricket catch",
        "batsman", "bowler", "fielder",
    ],
    "sports": [
        "batting", "bowling", "fielding", "catch", "six", "century",
        "wicket", "celebration", "trophy", "medal", "match",
        "stadium", "training", "warmup", "cover drive", "pull shot",
        "fast bowling", "spin bowling", "wicket keeping", "dunk",
        "goal", "penalty", "free kick", "passing", "shooting",
        "dribbling", "tackling", "swimming", "running", "sprint",
        "marathon", "knockout", "championship", "tournament",
        "lifting", "cup final", "test match", "innings",
    ],
    "nature": [
        "landscape", "aerial", "sunset", "sunrise", "wildlife",
        "ocean", "mountain", "forest", "river", "waterfall",
        "valley", "island", "beach", "coast", "desert",
    ],
    "city": [
        "skyline", "building", "architecture", "street", "bridge",
        "monument", "landmark", "downtown", "night", "panorama",
    ],
    "history": [
        "old archive photo", "historical photograph", "historic site",
        "old newspaper", "historical view", "archival photo",
        "iceberg collision", "ship exterior", "ship sailing",
        "lifeboat rescue", "distress signal", "shipwreck",
        "documentary archival photo", "harbor scene", "vintage building",
    ],
    "general": [
        "photo", "image", "HD", "high resolution", "4K",
    ],
}

# Backward-compat aliases (old code used underscore prefixes)
_BING_SEARCH_URL = BING_SEARCH_URL
_SEARCH_HEADERS = SEARCH_HEADERS
_LOW_CONFIDENCE_DOMAINS = LOW_CONFIDENCE_DOMAINS
_REJECT_TEXT_SIGNALS = REJECT_TEXT_SIGNALS
_WATERMARK_SIGNALS = WATERMARK_SIGNALS
_STOPWORDS = STOPWORDS
_SUPPORTED_IMAGE_EXTS = SUPPORTED_IMAGE_EXTS

GENERIC_TOPIC_WORDS: Set[str] = {
    "united state", "united states", "america", "american", "india", "indian", "china", "chinese", 
    "pakistan", "sri lanka", "england", "australia", "asia", "europe", "wealth", "history", 
    "coffee", "planet", "science", "water", "nature", "health", "world", "country", "countries",
    "government", "politics", "economy", "economics", "inflation", "finance", "business"
}
_GENERIC_TOPIC_WORDS = GENERIC_TOPIC_WORDS

