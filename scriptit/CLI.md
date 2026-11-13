# ScriptIT headless CLI

`scriptit/cli/generate.js` runs ScriptIT's script engine without Electron, for
the Vidzeo worker (and any headless operator). It is the worker's default
narration engine: `adapters/script.js` calls it when `MUSE_SCRIPT_COMMAND` is
unset.

## Quick use

```sh
# One title, agent-bridge mode (no key needed — Muse answers via the inbox):
node scriptit/cli/generate.js --topic "Apollo 11 Moon landing" --duration 1min

# Worker contract (job JSON on stdin, one JSON object on stdout):
node scriptit/cli/generate.js < job.json

# OpenRouter mode (needs a key):
SCRIPTIT_API_KEY=sk-or-... node scriptit/cli/generate.js --topic "..." --duration 10min
```

Flags: `--topic`, `--description`, `--duration` (`1min` … `60min`),
`--voice`, `--api-key`, `--job-id`, `--work-dir`, `--script-path`,
`--research-mode` (`quick` | `thorough`, default `thorough`).
All diagnostics go to stderr; **stdout carries exactly one JSON object**:
`{scriptText, meta}` on success, `{"error": "..."}` on failure.

## Exit codes

| Code | Meaning |
| ---- | ------- |
| 0 | Publishable script. `meta.publishable === true`. |
| 2 | A draft exists but the editorial gate did not pass. The draft is printed with `publishable: false` and saved to `script-draft.json` in the work dir for a human to review. It is never written to `script.txt`, so a retry cannot silently reuse it. |
| 1 | Generation failed (no usable research, transport failure, bad input). |

## How inference works (no OpenRouter key needed)

By default the CLI uses the **agent-bridge** provider
(`src/providers/agentBridge.js`, handle `agent-bridge:local-user`). Each
inference call writes a request file under the Vidnets agent-bridge directory
(`SCRIPTIT_AGENT_BRIDGE_DIR`, else `VIDNETS_AGENT_BRIDGE_DIR`, else
`~/workspace/vidnets-runtime/temp/agent-bridge`) and waits up to 300s for the
operator to write `response.json` next to it. The operator is usually Muse,
woken by the `vidnets-inbox-watch` hook, which answers:

- **Draft / revision requests** — write the spoken narration the prompt asks
  for, grounded only in the evidence listed in the prompt. The response
  content *is* the narration (raw text, not JSON).
- **Hook requests** (prompt contains `OPENING HOOK`) — answer with raw JSON
  text in exactly the format shown in the prompt (`{"variants": [...]}`).

Set `SCRIPTIT_JOB_ID` (or pass a job JSON) so requests carry the job id.
`--api-key` with any other value switches to OpenRouter mode; `--model`
selects the model (default `nvidia/nemotron-3.5-super-vl:free`, a
policy-allowed free model — see `src/providers/modelPolicy.js`).

## Quality: what "publishable" actually means

Thorough mode runs the same editorial gate as quick mode (it used to return
with only a length check):

- `evaluateDraft` — hook, pacing, topic fit, evidence support, and a
  **specificity gate** (a script must name concrete dates, numbers, and
  people: ≥4 distinct entities for ≥180 words, ≥2 for shorter).
- Bounded revision loop (≤2 thorough / ≤1 quick) with the gate's issues as
  feedback, then `cleanVoiceRules` de-slop and evidence-marker repair.
- `generateHook` scores three opening-hook variants (kept as alternates in
  `meta.hookVariants`; the draft's own opening must pass the hook gate, so
  no hook is ever prepended).

Publishable requires: gate pass + at least one external (non-synthetic)
evidence claim + model available. Quick mode is a preview by design and is
never publishable.

## Evidence honesty

- Research claims come from real providers (web search, Wikipedia, RSS,
  etc.). Model-written dossier text is labelled `synthetic: true`, counts as
  tier `synthetic` (P4) with low confidence, and **never** counts toward
  sufficiency, support checks, or publishability.
- When research returns zero external claims, generation fails with
  `RESEARCH_INSUFFICIENT` instead of inventing anchors. A failed job is
  honest; a generic script is not a substitute.

## Worker wiring

`adapters/script.js` (M2l root):

- No `MUSE_SCRIPT_COMMAND` → runs `node scriptit/cli/generate.js`
  (45-minute overall workflow deadline; every Muse inbox request is capped
  at 120 seconds with a 60-second answer target).
- `MUSE_SCRIPT_COMMAND` set → that command wins (unchanged behaviour).
- `MUSE_SCRIPT_ALLOW_TEMPLATE_FALLBACK=1` → the old template synthesizer
  runs, labelled `template-synthesizer-generic`. It is opt-in only and never
  the silent default.
- A non-publishable draft (exit 2) fails the job with the gate's issues;
  the draft is left in `script-draft.json` for review.

## Tests

```sh
cd scriptit
npm test   # includes tests/agent-bridge.js and tests/cli-generate.js
```

`tests/cli-generate.js` runs the full worker-contract path with a fake
operator (live web research, bridge answers from the test) and asserts a
publishable script with a real hook, concrete entities, and zero synthetic
claims.
