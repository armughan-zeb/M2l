'use strict';

const SOURCE_POLICIES = {
  official: { role: 'evidence', authorityTier: 'P0', maxConcurrency: 4, rawRetentionDays: 60, allowQuotes: true },
  rss: { role: 'discovery', authorityTier: 'P1', maxConcurrency: 3, rawRetentionDays: 60, allowQuotes: false },
  bing: { role: 'discovery', authorityTier: 'P4', maxConcurrency: 2, rawRetentionDays: 7, allowQuotes: false },
  duckduckgo: { role: 'discovery', authorityTier: 'P4', maxConcurrency: 1, rawRetentionDays: 7, allowQuotes: false },
  wikipedia: { role: 'reference', authorityTier: 'P2', maxConcurrency: 2, rawRetentionDays: 60, allowQuotes: true, attributionRequired: true },
  hackernews: { role: 'community', authorityTier: 'P3', maxConcurrency: 2, rawRetentionDays: 30, allowQuotes: false },
  reddit: { role: 'community', authorityTier: 'P3', maxConcurrency: 1, rawRetentionDays: 30, allowQuotes: false, deletionAware: true },
  x: { role: 'discovery', authorityTier: 'P3', maxConcurrency: 1, rawRetentionDays: 7, allowQuotes: false, deletionAware: true, killSwitch: true, publicReadOnly: true },
  github: { role: 'evidence', authorityTier: 'P0', maxConcurrency: 2, rawRetentionDays: 60, allowQuotes: true },
  gdelt: { role: 'discovery', authorityTier: 'P4', maxConcurrency: 2, rawRetentionDays: 7, allowQuotes: false },
  openalex: { role: 'discovery', authorityTier: 'P4', maxConcurrency: 1, rawRetentionDays: 30, allowQuotes: false },
  crossref: { role: 'discovery', authorityTier: 'P4', maxConcurrency: 1, rawRetentionDays: 30, allowQuotes: false },
  usgs: { role: 'evidence', authorityTier: 'P0', maxConcurrency: 2, rawRetentionDays: 60, allowQuotes: true },
};

module.exports = { SOURCE_POLICIES };
