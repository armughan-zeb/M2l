const dns = require('dns');
if (typeof dns.setDefaultResultOrder === 'function') {
  dns.setDefaultResultOrder('ipv4first');
}
const ENDPOINT = 'https://openrouter.ai/api/v1/chat/completions';
const { MODEL, normalizeModel, lockPayload } = require('./modelPolicy');
const { isAgentHandle, agentChat } = require('./agentInference');
const { providerError, waitForRetry } = require('./errorPolicy');

const FALLBACK_MODELS = [];

async function singleChatAttempt({ apiKey, model, messages, temperature, maxTokens, stream, onChunk, signal, timeoutMs, reasoning, responseFormat, _noRequireParams }) {
  if (isAgentHandle(apiKey)) {
    const result = await agentChat({ apiKey, messages, responseFormat, maxTokens, signal, timeoutMs, operation: 'scriptit.completion', kind: 'text' });
    if (stream && onChunk && result.content) onChunk(result.content, result.content);
    return result;
  }
  await require('./modelPolicy').confirmModel(model);
  const body = {
    model,
    messages,
    temperature,
    max_tokens: maxTokens,
    stream,
    ...(stream ? {stream_options:{include_usage:true}} : {}),
  };
  // Visible narration/JSON must not lose its entire output allowance to the
  // selected model's default hidden reasoning. Keep explicit caller settings.
  if (reasoning) body.reasoning = reasoning;
  else if (/^nvidia\/nemotron/i.test(model || '')) body.reasoning = { enabled: false };
  if (responseFormat) {
    body.response_format = responseFormat;
    if (!_noRequireParams) {
      body.provider = { require_parameters: true };
    }
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  const finalSignal = signal ? AbortSignal.any([signal, controller.signal]) : controller.signal;

  try {
    const res = await fetch(ENDPOINT, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
        'HTTP-Referer': 'https://scriptit.local',
        'X-Title': 'ScriptIT',
        'Accept': stream ? 'text/event-stream' : 'application/json',
      },
      body: JSON.stringify(lockPayload(body)),
      signal: finalSignal,
    }).catch(err => {
      const name = err && err.name ? err.name : 'unknown';
      const msg = err && err.message ? err.message : '';
      if (name === 'AbortError' || /aborted/i.test(msg) || controller.signal.aborted) {
        throw new Error(`OpenRouter request timed out after ${Math.round(timeoutMs / 1000)}s (model: ${model})`);
      }
      throw err;
    });

    if (!res.ok) {
      const text = await res.text().catch(() => '');
      let data; try { data = JSON.parse(text); } catch { data = {}; }
      if (res.status === 404 && body.provider?.require_parameters && /no endpoints found/i.test(data.error?.message || '')) {
        return singleChatAttempt({ apiKey, model, messages, temperature, maxTokens, stream, onChunk, signal, timeoutMs, reasoning, responseFormat: { type: 'json_object' }, _noRequireParams: true });
      }
      throw providerError(res.status, data, res.headers);
    }

    // Some compatible gateways and deterministic test adapters answer a
    // streaming request with a normal JSON body. Treat that as a valid
    // completion instead of dereferencing a missing ReadableStream.
    if (!stream || !res.body || typeof res.body.getReader !== 'function') {
      const json = await res.json();
      if (json.error) {
        throw providerError(502, json, res.headers);
      }
      const content = json.choices?.[0]?.message?.content || '';
      const usage = json.usage || null;
      return { content, usage, raw: json };
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    let full = '';
    let usage = null;
    let generationId=null, resolvedModel=null, finishReason=null;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith('data:')) continue;
        const payload = trimmed.slice(5).trim();
        if (payload === '[DONE]') continue;
        try {
          const obj = JSON.parse(payload);
          generationId=obj.id || generationId; resolvedModel=obj.model || resolvedModel;
          finishReason=obj.choices?.[0]?.finish_reason || finishReason;
          const delta = obj.choices?.[0]?.delta?.content || '';
          if (delta) {
            full += delta;
            if (onChunk) onChunk(delta, full);
          }
          if (obj.usage) usage = obj.usage;
        } catch (_) {}
      }
    }
    return { content: full, usage, raw: {id:generationId,model:resolvedModel,choices:[{finish_reason:finishReason}]} };
  } catch (error) {
    if (controller.signal.aborted && !signal?.aborted) {
      const timeoutError = new Error(`OpenRouter request timed out after ${Math.round(timeoutMs / 1000)}s (model: ${model})`);
      timeoutError.cause = error;
      throw timeoutError;
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
  }
}

async function chatCompletion({
  apiKey,
  model,
  messages,
  temperature = 0.7,
  maxTokens = 4000,
  stream = true,
  onChunk,
  signal,
  timeoutMs = 300000,
  totalTimeoutMs = timeoutMs,
  perAttemptTimeoutMs = timeoutMs,
  maxAttemptsPerModel = 3,
  maxTotalAttempts = 3,
  fallbackModels = FALLBACK_MODELS,
  reasoning,
  responseFormat,
}) {
  if (!apiKey) throw new Error('Missing OpenRouter API key');
  if (isAgentHandle(apiKey)) {
    if (!Array.isArray(messages) || !messages.length) throw new Error('Missing messages');
    const result = await singleChatAttempt({ apiKey, messages, responseFormat, maxTokens, stream, onChunk, signal,
      timeoutMs: Math.max(1000, Math.min(Number(totalTimeoutMs) || timeoutMs, Number(perAttemptTimeoutMs) || timeoutMs)) });
    if (!result?.content) throw new Error('External agent returned an empty completion');
    return { ...result, modelUsed: 'external-agent', truncated: result.raw?.choices?.[0]?.finish_reason === 'length' };
  }
  model = normalizeModel(model || MODEL);
  fallbackModels = [];
  if (!model) throw new Error('Missing model');
  if (!Array.isArray(messages) || !messages.length) throw new Error('Missing messages');

  const modelsToTry = [model, ...(fallbackModels || []).filter(m => m !== model)];
  let lastError = null;
  let attempts = 0;
  const startedAt = Date.now();
  const totalBudget = Math.max(1000, Number(totalTimeoutMs) || Number(timeoutMs) || 300000);

  for (const currentModel of modelsToTry) {
    if (attempts >= maxTotalAttempts || signal?.aborted) break;
    const attemptLimit = Math.max(1, Number(maxAttemptsPerModel) || 1);
    for (let attempt = 0; attempt < attemptLimit; attempt++) {
      if (attempts >= maxTotalAttempts || signal?.aborted) break;
      const remainingMs = totalBudget - (Date.now() - startedAt);
      if (remainingMs < 1000) break;
      try {
        attempts += 1;
        const result = await singleChatAttempt({
          apiKey,
          model: currentModel,
          messages,
          temperature,
          maxTokens,
          stream,
          reasoning,
          responseFormat,
          onChunk,
          signal,
          timeoutMs: Math.max(1000, Math.min(Number(perAttemptTimeoutMs) || timeoutMs, 300000, remainingMs)),
        });
        if (result && result.content) {
          return { ...result, truncated: result.raw?.choices?.[0]?.finish_reason === 'length', modelUsed: currentModel, attempts, elapsedMs: Date.now() - startedAt };
        }
        const finishReason = result?.raw?.choices?.[0]?.finish_reason;
        lastError = new Error(finishReason === 'length'
          ? 'OpenRouter output budget exhausted before visible completion (model: ' + currentModel + ')'
          : 'OpenRouter returned an empty completion (model: ' + currentModel + ')');
      } catch (err) {
        lastError = err;
        const status = err.status || 0;
        if ([401, 402, 403].includes(status) || signal?.aborted) throw err;
        // If internet dropped, pause gracefully and wait for signals to return without burning attempts
        const netRes = require('../utils/internetResilience');
        if (netRes.isNetworkError(err) && !(await netRes.isInternetReachable(1500))) {
          console.warn('[OpenRouter] Network disconnected; pausing until internet returns…');
          await netRes.waitForInternetConnectivity({ signal });
          attempt--;
          continue;
        }
        // If network error, rate limited or server overloaded, wait briefly before retrying
        if (status === 0 || status === 429 || status >= 500) {
          const remaining = totalBudget - (Date.now() - startedAt);
          const delay = err.retryAfterMs || (attempt + 1) * 1200;
          if (delay >= remaining || attempts >= maxTotalAttempts) throw err;
          if (delay > 0) await waitForRetry(delay, signal);
          continue;
        }
        // Invalid requests fail without selecting another model.
        if (status === 404 || status === 400) {
          break;
        }
      }
    }
  }

  const elapsed = Date.now() - startedAt;
  const failure = lastError || new Error('All OpenRouter model attempts failed');
  failure.attempts = attempts;
  failure.elapsedMs = elapsed;
  throw failure;
}

module.exports = { chatCompletion, singleChatAttempt, FALLBACK_MODELS };
