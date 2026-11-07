'use strict';
// Portable client shared by installed tools. External agents supply their own
// reasoning; this transport never calls a model provider or starts another server.
const PREFIX = 'external-agent:';
function isAgentHandle(value) { return String(value || '').startsWith(PREFIX); }
function baseUrl() {
  const url = new URL(process.env.VIDZEO_CMS_AGENT_URL || 'http://127.0.0.1:3210');
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw new Error('Agent inference requires a localhost CMS URL');
  return url.origin;
}
async function agentChat({ apiKey, messages, responseFormat, maxTokens, tools, toolChoice, signal, timeoutMs = 120000, operation = 'tool-completion', kind = 'text' }) {
  if (!isAgentHandle(apiKey) || !String(apiKey).slice(PREFIX.length).trim()) throw new Error('A paired external agent is required');
  const budget = Math.max(100, Math.min(600000, Number(timeoutMs) || 120000));
  const deadline = AbortSignal.timeout(budget + 1000);
  const response = await fetch(baseUrl() + '/api/agent/inference', {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Vidzeo-Agent-Key': String(apiKey).slice(PREFIX.length) },
    body: JSON.stringify({ messages, response_format: responseFormat, max_tokens: maxTokens, tools, tool_choice: toolChoice, timeoutMs: budget, operation, kind }),
    signal: signal ? AbortSignal.any([signal, deadline]) : deadline
  });
  const reply = await response.json();
  if (!response.ok || !reply.ok) {
    const error = new Error(reply.error || 'External agent inference failed');
    error.code = reply.code || 'AGENT_UNAVAILABLE'; error.retryable = false;
    throw error;
  }
  const message = { content: reply.content || '', ...(reply.tool_calls ? { tool_calls: reply.tool_calls } : {}) };
  const raw = { id: 'agent-' + reply.requestId, model: 'external-agent', provider: 'external_agent',
    choices: [{ message, finish_reason: reply.tool_calls ? 'tool_calls' : 'stop' }], usage: {} };
  return { content: message.content, raw, modelUsed: 'external-agent', attempts: 1 };
}
module.exports = { PREFIX, isAgentHandle, agentChat, baseUrl };
