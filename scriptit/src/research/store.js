'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const initSqlJs = require('sql.js');
const { SOURCE_POLICIES } = require('./sourcePolicies');

const SCHEMA_VERSION = 1;
const DB_FILENAME = 'scriptit-research.sqlite';

const SCHEMA = `
CREATE TABLE IF NOT EXISTS schema_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_jobs (
  id TEXT PRIMARY KEY,
  idempotency_key TEXT UNIQUE,
  topic TEXT NOT NULL,
  niche TEXT,
  status TEXT NOT NULL,
  phase TEXT,
  progress INTEGER NOT NULL DEFAULT 0,
  input_json TEXT NOT NULL DEFAULT '{}',
  result_json TEXT NOT NULL DEFAULT '{}',
  warnings_json TEXT NOT NULL DEFAULT '[]',
  errors_json TEXT NOT NULL DEFAULT '[]',
  retryable INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_research_jobs_topic ON research_jobs(topic, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_jobs_status ON research_jobs(status, updated_at);
CREATE TABLE IF NOT EXISTS research_questions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL REFERENCES research_jobs(id) ON DELETE CASCADE,
  question TEXT NOT NULL,
  kind TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  answer_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(job_id, question)
);
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  job_id TEXT REFERENCES research_jobs(id) ON DELETE SET NULL,
  provider TEXT,
  canonical_url TEXT NOT NULL UNIQUE,
  publisher TEXT,
  title TEXT,
  source_tier TEXT,
  authority_tier TEXT,
  published_at TEXT,
  retrieved_at TEXT,
  content_hash TEXT,
  license_status TEXT NOT NULL DEFAULT 'unknown',
  deletion_state TEXT NOT NULL DEFAULT 'active',
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_sources_job ON sources(job_id);
CREATE INDEX IF NOT EXISTS idx_sources_published ON sources(published_at);
CREATE TABLE IF NOT EXISTS source_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  body_text TEXT,
  response_json TEXT NOT NULL DEFAULT '{}',
  content_hash TEXT,
  retrieved_at TEXT NOT NULL,
  expires_at TEXT,
  UNIQUE(source_id, content_hash)
);
CREATE TABLE IF NOT EXISTS claims (
  id TEXT PRIMARY KEY,
  job_id TEXT REFERENCES research_jobs(id) ON DELETE CASCADE,
  fingerprint TEXT NOT NULL,
  claim_text TEXT NOT NULL,
  confidence TEXT,
  authority_tier TEXT,
  freshness_state TEXT,
  published_at TEXT,
  retrieved_at TEXT,
  verification_state TEXT NOT NULL DEFAULT 'unverified',
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_claims_job ON claims(job_id);
CREATE INDEX IF NOT EXISTS idx_claims_fingerprint ON claims(fingerprint);
CREATE TABLE IF NOT EXISTS claim_support (
  claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
  source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  excerpt TEXT,
  locator TEXT,
  stance TEXT NOT NULL DEFAULT 'supports',
  PRIMARY KEY(claim_id, source_id, stance)
);
CREATE TABLE IF NOT EXISTS claim_conflicts (
  claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
  conflicting_claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
  reason TEXT,
  PRIMARY KEY(claim_id, conflicting_claim_id)
);
CREATE TABLE IF NOT EXISTS script_runs (
  id TEXT PRIMARY KEY,
  job_id TEXT REFERENCES research_jobs(id) ON DELETE SET NULL,
  topic TEXT NOT NULL,
  status TEXT NOT NULL,
  script_text TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS provider_health (
  provider TEXT PRIMARY KEY,
  state_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_policies (
  provider TEXT PRIMARY KEY,
  policy_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS retention_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id TEXT,
  action TEXT NOT NULL,
  reason TEXT,
  created_at TEXT NOT NULL
);
`;

function now() { return new Date().toISOString(); }

function defaultDbPath() {
  let directory = path.join(process.cwd(), '.cache');
  try {
    // Electron is optional when the agents are run from the test suite.
    // eslint-disable-next-line global-require
    const { app } = require('electron');
    if (app && typeof app.getPath === 'function' && app.isReady && app.isReady()) directory = app.getPath('userData');
  } catch (_) {}
  return path.join(directory, DB_FILENAME);
}

function safeJson(value, fallback) {
  try { return JSON.stringify(value === undefined ? fallback : value); } catch (_) { return JSON.stringify(fallback); }
}

function parseJson(value, fallback) {
  try { return value ? JSON.parse(value) : fallback; } catch (_) { return fallback; }
}

function hash(value) {
  return crypto.createHash('sha256').update(String(value || '')).digest('hex');
}

function rowsFrom(result) {
  if (!result || !result.length) return [];
  const [first] = result;
  return first.values.map((values) => Object.fromEntries(first.columns.map((column, index) => [column, values[index]])));
}

class ResearchStore {
  constructor({ dbPath = defaultDbPath(), wasmPath } = {}) {
    this.dbPath = dbPath;
    this.wasmPath = wasmPath || path.join(path.dirname(require.resolve('sql.js')), 'sql-wasm.wasm');
    this.db = null;
    this.SQL = null;
    this.ready = false;
    this.recovered = false;
    this.writeQueue = Promise.resolve();
  }

  async init() {
    if (this.ready) return this;
    fs.mkdirSync(path.dirname(this.dbPath), { recursive: true });
    this.SQL = await initSqlJs({ locateFile: () => this.wasmPath });
    let bytes = null;
    if (fs.existsSync(this.dbPath)) {
      try { bytes = fs.readFileSync(this.dbPath); } catch (_) { bytes = null; }
    }
    try {
      this.db = bytes ? new this.SQL.Database(bytes) : new this.SQL.Database();
      this.db.run('PRAGMA foreign_keys = ON; PRAGMA journal_mode = WAL;');
      this.db.run('BEGIN;');
      this.db.run(SCHEMA);
      this.db.run(`INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', '${SCHEMA_VERSION}')`);
      this.seedPolicies();
      this.db.run('COMMIT;');
    } catch (err) {
      try { this.db && this.db.run('ROLLBACK;'); } catch (_) {}
      const backup = `${this.dbPath}.corrupt-${Date.now()}`;
      try { if (fs.existsSync(this.dbPath)) fs.copyFileSync(this.dbPath, backup); } catch (_) {}
      this.db = new this.SQL.Database();
      this.db.run('PRAGMA foreign_keys = ON; PRAGMA journal_mode = WAL;');
      this.db.run(SCHEMA);
      this.db.run(`INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', '${SCHEMA_VERSION}')`);
      this.seedPolicies();
      this.recovered = true;
    }
    this.ready = true;
    await this.persist();
    return this;
  }

  seedPolicies() {
    const timestamp = now();
    for (const [provider, policy] of Object.entries(SOURCE_POLICIES)) {
      this.db.run('INSERT OR IGNORE INTO source_policies(provider, policy_json, updated_at) VALUES (?, ?, ?)', [provider, safeJson(policy, {}), timestamp]);
    }
  }

  async persist() {
    if (!this.db) return;
    const bytes = Buffer.from(this.db.export());
    const temp = `${this.dbPath}.tmp`;
    const backup = `${this.dbPath}.bak`;
    fs.writeFileSync(temp, bytes);
    if (fs.existsSync(this.dbPath)) {
      try { fs.copyFileSync(this.dbPath, backup); } catch (_) {}
      try { fs.rmSync(this.dbPath, { force: true }); } catch (_) {}
    }
    fs.renameSync(temp, this.dbPath);
  }

  async write(fn) {
    await this.init();
    const operation = this.writeQueue.catch(() => undefined).then(async () => {
      const result = fn(this.db);
      await this.persist();
      return result;
    });
    this.writeQueue = operation.catch(() => undefined);
    return operation;
  }

  async read(fn) {
    await this.init();
    await this.writeQueue;
    return fn(this.db);
  }

  async createJob(input = {}) {
    const topic = String(input.topic || '').trim();
    if (!topic) throw new Error('Research topic is required');
    const idempotencyKey = String(input.idempotencyKey || `${topic.toLowerCase()}|${input.channelId || ''}|${input.duration || ''}`);
    return this.write((db) => {
      const existing = rowsFrom(db.exec('SELECT * FROM research_jobs WHERE idempotency_key = ?', [idempotencyKey]))[0] || null;
      if (existing) return this.hydrateJob(existing);
      const id = crypto.randomUUID();
      const timestamp = now();
      db.run(`INSERT INTO research_jobs(id, idempotency_key, topic, niche, status, phase, progress, input_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'queued', 'queued', 0, ?, ?, ?)`, [id, idempotencyKey, topic, input.niche || '', safeJson({ ...input, apiKey: undefined }, {}), timestamp, timestamp]);
      return this.hydrateJob({ id, topic, niche: input.niche || '', status: 'queued', phase: 'queued', progress: 0, input_json: safeJson(input, {}), warnings_json: '[]', errors_json: '[]', retryable: 1, created_at: timestamp, updated_at: timestamp });
    });
  }

  hydrateJob(row) {
    if (!row) return null;
    return {
      ...row,
      input: parseJson(row.input_json, {}),
      result: parseJson(row.result_json, {}),
      warnings: parseJson(row.warnings_json, []),
      errors: parseJson(row.errors_json, []),
      retryable: Boolean(row.retryable),
    };
  }

  async getJob(id) {
    return this.read((db) => this.hydrateJob(rowsFrom(db.exec('SELECT * FROM research_jobs WHERE id = ?', [id]))[0] || null));
  }

  async searchClaims(query, limit = 40) {
    const text = String(query || '').trim();
    if (!text) return [];
    return this.read((db) => rowsFrom(db.exec('SELECT * FROM claims WHERE claim_text LIKE ? ORDER BY retrieved_at DESC LIMIT ?', [`%${text}%`, Math.max(1, Math.min(200, Number(limit) || 40))])).map((row) => ({
      ...row,
      metadata: parseJson(row.metadata_json, {}),
    })));
  }

  async getEvidenceMap(jobId) {
    const job = await this.getJob(jobId);
    return job && job.result ? job.result.evidence || null : null;
  }

  async latestJob(topic) {
    return this.read((db) => this.hydrateJob(rowsFrom(db.exec('SELECT * FROM research_jobs WHERE topic = ? ORDER BY updated_at DESC LIMIT 1', [String(topic || '').trim()]))[0] || null));
  }

  async checkpointJob(id, patch = {}) {
    const allowed = new Set(['queued', 'fast_research', 'fast_ready', 'deep_research', 'context_ready', 'writing', 'auditing', 'ready_to_publish', 'published', 'partial', 'retrying', 'failed', 'dead_letter']);
    return this.write((db) => {
      const status = allowed.has(patch.status) ? patch.status : undefined;
      const timestamp = now();
      const updates = [];
      const values = [];
      if (status) { updates.push('status = ?'); values.push(status); }
      if (patch.phase !== undefined) { updates.push('phase = ?'); values.push(String(patch.phase)); }
      if (patch.progress !== undefined) { updates.push('progress = ?'); values.push(Math.max(0, Math.min(100, Number(patch.progress) || 0))); }
      if (patch.result !== undefined) { updates.push('result_json = ?'); values.push(safeJson(patch.result, {})); }
      if (patch.warnings !== undefined) { updates.push('warnings_json = ?'); values.push(safeJson(patch.warnings, [])); }
      if (patch.errors !== undefined) { updates.push('errors_json = ?'); values.push(safeJson(patch.errors, [])); }
      if (patch.retryable !== undefined) { updates.push('retryable = ?'); values.push(patch.retryable ? 1 : 0); }
      updates.push('updated_at = ?'); values.push(timestamp);
      if (['published', 'failed', 'dead_letter'].includes(status)) { updates.push('completed_at = ?'); values.push(timestamp); }
      values.push(id);
      if (updates.length) db.run(`UPDATE research_jobs SET ${updates.join(', ')} WHERE id = ?`, values);
      return this.hydrateJob(rowsFrom(db.exec('SELECT * FROM research_jobs WHERE id = ?', [id]))[0] || null);
    });
  }

  async saveResearchPlan({ jobId, plan, coverage } = {}) {
    if (!jobId || !plan || !Array.isArray(plan.questions)) return { jobId, questionCount: 0 };
    return this.write((db) => {
      const timestamp = now();
      const coverageById = new Map((coverage?.rows || []).map((row) => [row.id, row]));
      for (const item of plan.questions) {
        const row = coverageById.get(item.id);
        const status = row?.status || 'pending';
        const answer = row ? { status: row.status, claimIds: row.claimIds || [], hasPrimary: Boolean(row.hasPrimary) } : {};
        db.run(`INSERT INTO research_questions(job_id, question, kind, status, answer_json, created_at, updated_at)
          VALUES (?, ?, ?, ?, ?, ?, ?)
          ON CONFLICT(job_id, question) DO UPDATE SET kind=excluded.kind, status=excluded.status, answer_json=excluded.answer_json, updated_at=excluded.updated_at`, [
          jobId, item.question, item.kind || '', status, safeJson(answer, {}), timestamp, timestamp,
        ]);
      }
      return { jobId, questionCount: plan.questions.length };
    });
  }

  async saveResearchBundle({ jobId, research, evidence, brief, plan, coverage } = {}) {
    if (!research || !evidence) return;
    return this.write((db) => {
      const timestamp = now();
      const items = Object.entries(research)
        .filter(([key, value]) => Array.isArray(value) && !['errors'].includes(key))
        .flatMap(([provider, value]) => value.map((item) => ({ ...item, provider })));
      db.run('BEGIN;');
      try {
        if (plan?.questions) {
          const coverageById = new Map((coverage?.rows || []).map((row) => [row.id, row]));
          for (const item of plan.questions) {
            const row = coverageById.get(item.id);
            db.run(`INSERT INTO research_questions(job_id, question, kind, status, answer_json, created_at, updated_at)
              VALUES (?, ?, ?, ?, ?, ?, ?)
              ON CONFLICT(job_id, question) DO UPDATE SET kind=excluded.kind, status=excluded.status, answer_json=excluded.answer_json, updated_at=excluded.updated_at`, [
              jobId, item.question, item.kind || '', row?.status || 'pending', safeJson(row || {}, {}), timestamp, timestamp,
            ]);
          }
        }
        for (const item of items) {
          const url = String(item.url || '').trim();
          if (!url) continue;
          const sourceId = `source-${hash(url).slice(0, 24)}`;
          const text = String(item.articleText || item.snippet || item.title || '').slice(0, 100000);
          const contentHash = hash(text);
          db.run(`INSERT INTO sources(id, job_id, provider, canonical_url, publisher, title, source_tier, authority_tier, published_at, retrieved_at, content_hash, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(canonical_url) DO UPDATE SET job_id=excluded.job_id, publisher=excluded.publisher, title=excluded.title, published_at=excluded.published_at, retrieved_at=excluded.retrieved_at, content_hash=excluded.content_hash, metadata_json=excluded.metadata_json`, [
            sourceId, jobId || null, item.provider || item.source || '', url, item.source || item.feedSource || '', item.title || '', item.sourceTier || '', item.authorityTier || '', item.articleDate || item.date || '', timestamp, contentHash, safeJson(item, {}),
          ]);
          db.run(`INSERT OR IGNORE INTO source_snapshots(source_id, body_text, response_json, content_hash, retrieved_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)`, [sourceId, text, safeJson(item, {}), contentHash, timestamp, new Date(Date.now() + 60 * 24 * 60 * 60 * 1000).toISOString()]);
        }
        for (const claim of evidence.claims || []) {
          const fingerprint = hash(String(claim.text || '').toLowerCase().replace(/\s+/g, ' ').trim());
          db.run(`INSERT INTO claims(id, job_id, fingerprint, claim_text, confidence, authority_tier, freshness_state, published_at, retrieved_at, verification_state, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET job_id=excluded.job_id, claim_text=excluded.claim_text, confidence=excluded.confidence, authority_tier=excluded.authority_tier, freshness_state=excluded.freshness_state, published_at=excluded.published_at, retrieved_at=excluded.retrieved_at, verification_state=excluded.verification_state, metadata_json=excluded.metadata_json`, [
            claim.id, jobId || null, fingerprint, claim.text || '', claim.confidence || 'low', claim.authorityTier || claim.sourceTierCode || '', claim.ageDays === null ? 'undated' : (claim.ageDays <= 31 ? 'fresh' : 'stale'), claim.publishedAt || '', claim.retrievedAt || timestamp, claim.confidence === 'high' ? 'supported' : 'needs_review', safeJson(claim, {}),
          ]);
          const sourceId = claim.url ? `source-${hash(claim.url).slice(0, 24)}` : null;
          if (sourceId) db.run('INSERT OR IGNORE INTO claim_support(claim_id, source_id, excerpt, locator, stance) VALUES (?, ?, ?, ?, ?)', [claim.id, sourceId, String(claim.text || '').slice(0, 1200), claim.url || '', 'supports']);
        }
        for (const claim of evidence.claims || []) {
          db.run('DELETE FROM claim_conflicts WHERE claim_id = ? OR conflicting_claim_id = ?', [claim.id, claim.id]);
        }
        for (const conflict of evidence.conflicts || []) {
          if (!conflict.claimId || !conflict.conflictingClaimId) continue;
          db.run('INSERT OR REPLACE INTO claim_conflicts(claim_id, conflicting_claim_id, reason) VALUES (?, ?, ?)', [conflict.claimId, conflict.conflictingClaimId, conflict.reason || 'conflicting evidence']);
        }
        db.run('COMMIT;');
      } catch (err) {
        db.run('ROLLBACK;');
        throw err;
      }
      return { jobId, sourceCount: items.filter((item) => item.url).length, claimCount: (evidence.claims || []).length, brief }; 
    });
  }

  async saveScriptRun({ id = crypto.randomUUID(), jobId, topic, status, script, metadata } = {}) {
    return this.write((db) => {
      const timestamp = now();
      db.run(`INSERT OR REPLACE INTO script_runs(id, job_id, topic, status, script_text, metadata_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM script_runs WHERE id = ?), ?), ?)`, [id, jobId || null, topic || '', status || 'draft', script || '', safeJson(metadata, {}), id, timestamp, timestamp]);
      return id;
    });
  }

  async retentionSweep({ nowIso = now() } = {}) {
    return this.write((db) => {
      const rows = rowsFrom(db.exec('SELECT id, source_id FROM source_snapshots WHERE expires_at IS NOT NULL AND expires_at < ?', [nowIso]));
      for (const row of rows) {
        db.run('DELETE FROM source_snapshots WHERE id = ?', [row.id]);
        db.run('INSERT INTO retention_events(source_id, action, reason, created_at) VALUES (?, ?, ?, ?)', [row.source_id, 'purge_snapshot', 'retention_expired', nowIso]);
      }
      return rows.length;
    });
  }

  async integrityCheck() {
    return this.read((db) => rowsFrom(db.exec('PRAGMA integrity_check;'))[0]?.integrity_check === 'ok');
  }

  async close() {
    await this.writeQueue;
    if (this.db) this.db.close();
    this.db = null;
    this.ready = false;
  }
}

let sharedStore = null;
let sharedStorePromise = null;

async function getResearchStore(options = {}) {
  if (!sharedStorePromise || options.dbPath) {
    const store = new ResearchStore(options);
    const promise = store.init().then(async () => {
      await store.retentionSweep().catch(() => undefined);
      if (!options.dbPath) sharedStore = store;
      return store;
    });
    if (!options.dbPath) sharedStorePromise = promise;
    return promise;
  }
  return sharedStorePromise;
}

module.exports = { ResearchStore, getResearchStore, defaultDbPath, SCHEMA_VERSION };
