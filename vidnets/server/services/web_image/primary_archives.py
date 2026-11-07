"""Niche-sensitive publisher discovery; publisher identity is not license proof."""
import re


def archive_search_query(query):
    query = str(query or '').strip()
    choices = [
        (r'\b(?:crime|hijack|hijacking|murder|ransom|investigation|fbi|police|suspect)\b', ('fbi.gov', 'justice.gov', 'archives.gov')),
        (r'\b(?:space|nasa|moon|orbit|planet|eclipse|astronaut)\b', ('nasa.gov', 'esa.int', 'noaa.gov')),
        (r'\b(?:aircraft|aviation|ship|maritime|ocean|engineering)\b', ('si.edu', 'nasa.gov', 'archives.gov')),
        (r'\b(?:sports|nfl|football|olympic|athlete)\b', ('nfl.com', 'olympics.com', 'commons.wikimedia.org')),
        (r'\b(?:politics|president|royal|parliament|government)\b', ('archives.gov', 'parliament.uk', 'royal.uk')),
    ]
    domains = next((domains for pattern, domains in choices if re.search(pattern, query, re.I)),
                   ('commons.wikimedia.org', 'si.edu', 'archives.gov'))
    return f"{query} ({' OR '.join('site:' + domain for domain in domains)})"
