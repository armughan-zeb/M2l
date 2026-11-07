'use strict';

function timeoutSignal(timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs || 12000);
  return { signal: controller.signal, clear: () => clearTimeout(timer) };
}

async function getJson(url, timeoutMs = 12000) {
  const timeout = timeoutSignal(timeoutMs);
  try {
    const response = await fetch(url, {
      headers: {
        Accept: 'application/json',
        'User-Agent': 'ScriptIT/1.0 (research; local desktop app)',
      },
      signal: timeout.signal,
    });
    if (!response.ok) {
      const error = new Error(`HTTP ${response.status} from ${new URL(url).hostname}`);
      error.status = response.status;
      throw error;
    }
    return await response.json();
  } finally {
    timeout.clear();
  }
}

function dateFromGdelt(value) {
  const text = String(value || '');
  const match = text.match(/^(\d{4})(\d{2})(\d{2})/);
  return match ? `${match[1]}-${match[2]}-${match[3]}` : '';
}

async function gdeltSearch(query, { limit = 8 } = {}) {
  const url = `https://api.gdeltproject.org/api/v2/doc/doc?query=${encodeURIComponent(query)}&mode=artlist&maxrecords=${Math.min(25, Math.max(1, limit))}&format=json`;
  const json = await getJson(url);
  return (json.articles || []).map((item) => ({
    title: item.title || item.seendate || 'GDELT result',
    url: item.url || '',
    snippet: item.socialimage ? `Indexed by GDELT from ${item.domain || 'publisher'}.` : `Indexed by GDELT from ${item.domain || 'publisher'}.`,
    source: 'GDELT',
    domain: item.domain || '',
    date: dateFromGdelt(item.seendate),
    category: 'discovery',
  })).filter((item) => item.url);
}

async function openAlexSearch(query, { limit = 6 } = {}) {
  const url = `https://api.openalex.org/works?search=${encodeURIComponent(query)}&per-page=${Math.min(25, Math.max(1, limit))}&select=id,doi,title,publication_date,primary_location,authorships,cited_by_count,open_access`;
  const json = await getJson(url);
  return (json.results || []).map((item) => ({
    title: item.title || 'OpenAlex work',
    url: item.primary_location?.landing_page_url || item.doi || item.id || '',
    snippet: `Scholarly metadata indexed by OpenAlex; cited by ${item.cited_by_count || 0} works.`,
    source: 'OpenAlex',
    date: item.publication_date || '',
    category: 'discovery',
    metadata: { doi: item.doi || '', openAccess: item.open_access || null },
  })).filter((item) => item.url);
}

async function crossrefSearch(query, { limit = 6 } = {}) {
  const url = `https://api.crossref.org/works?query.bibliographic=${encodeURIComponent(query)}&rows=${Math.min(25, Math.max(1, limit))}&select=DOI,title,URL,published,container-title,license,author`; 
  const json = await getJson(url);
  return (json.message?.items || []).map((item) => ({
    title: item.title?.[0] || 'Crossref work',
    url: item.URL || (item.DOI ? `https://doi.org/${item.DOI}` : ''),
    snippet: `Publication metadata indexed by Crossref${item['container-title']?.[0] ? ` in ${item['container-title'][0]}` : ''}.`,
    source: 'Crossref',
    date: item.published?.['date-parts']?.[0]?.join('-') || '',
    category: 'discovery',
    metadata: { doi: item.DOI || '', license: item.license || [] },
  })).filter((item) => item.url);
}

async function usgsSearch(query, { limit = 6 } = {}) {
  if (!/earthquake|earthquakes|seismic|magnitude|aftershock/i.test(query)) return [];
  const url = `https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&orderby=time&limit=${Math.min(25, Math.max(1, limit))}&starttime=${encodeURIComponent(new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString())}`;
  const json = await getJson(url);
  return (json.features || []).map((item) => ({
    title: item.properties?.title || 'USGS earthquake event',
    url: item.properties?.url || `https://earthquake.usgs.gov/earthquakes/eventpage/${item.id}`,
    snippet: `${item.properties?.mag ?? 'Unknown'} magnitude event near ${item.properties?.place || 'an undisclosed location'}.`,
    source: 'USGS',
    date: item.properties?.time ? new Date(item.properties.time).toISOString().slice(0, 10) : '',
    category: 'official',
    articleText: `${item.properties?.title || 'USGS earthquake event'}. Magnitude ${item.properties?.mag ?? 'unknown'}.`,
    hasArticleBody: true,
  }));
}

module.exports = { gdeltSearch, openAlexSearch, crossrefSearch, usgsSearch };
