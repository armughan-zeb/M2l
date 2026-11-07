# Vidnets: manual generation and local agent generation

## Manual generation

Use the desktop app normally. All text and vision decisions use **Qwen 3.5 Flash through OpenRouter** (`qwen/qwen3.5-flash-02-23`). Saved expensive model selections and fallback chains are normalized to Qwen. Voice generation and media providers have separate settings.

## Agent generation without the desktop window

1. Sign in to an active Vidnets account. In Settings, select **Enable local agent** once. This authorizes this Windows user and installation. Close the desktop app before starting the headless service.
2. Give Codex or another trusted local agent access to the complete app folder and this document. Run `VIDNETS_AGENT.cmd start` from that folder. The launcher uses bundled Python, starts the backend in the background, and prints its local address and log path. No Electron or browser is launched.
3. The agent writes a generation JSON file and runs `VIDNETS_AGENT.cmd submit "C:\path\video-request.json"`. The response contains `item.id`, the queue job ID.
4. While the job runs, use `VIDNETS_AGENT.cmd pending` to find reasoning/vision requests. Read each with `VIDNETS_AGENT.cmd request REQUEST_ID`.
5. Use **your own reasoning** to answer the messages and exact response schema. For vision requests, inspect every supplied `local_image` file with your image tools. Save the response as a UTF-8 JSON or text file and run `VIDNETS_AGENT.cmd answer REQUEST_ID "C:\path\response.json"`.
6. Poll `VIDNETS_AGENT.cmd job JOB_ID` for progress and the finished video path. Keep servicing requests throughout planning, retrieval, review, and rendering. Several requests may arrive in parallel; answer those with the earliest expiry first. Do not fabricate an image judgment without looking at the image.

Example generation request:

```json
{
  "request_schema_version": 1,
  "title": "How a solar eclipse works",
  "script": "Supply your complete researched narration here.",
  "image_source": "Mixed Media",
  "quality": "fast",
  "captions_enabled": true,
  "director_mode": true,
  "request_id": "solar-eclipse-agent-001"
}
```

Use a new request ID for a new video; reuse the same ID and settings for an idempotent submission. The launcher forces queued `external_agent` mode through the existing **POST /api/generate** pipeline. Use `capabilities` for the current contract. For relocated writable data, put `--runtime-root "C:\path\VidzeoData"` before the command.

To resume a paused or failed job from saved work, run `VIDNETS_AGENT.cmd retry JOB_ID`, then continue servicing its inbox. Active and completed jobs cannot be retried, and the key can retry only its account's agent jobs.

## What the agent supplies

The host agent supplies script reasoning, scene queries, niche/style selection, director decisions, composition plans, review judgments, and vision analysis. Vidnets supplies the existing media retrieval, scene/timeline validation, narration, sound effects, captions, Remotion components, and rendering. Each inbox item includes its own messages, operation, deadline, and response format; the format varies by pipeline stage. JSON answer files contain the requested object itself, not an extra `content` envelope. Plain text answers are supported where the request asks for them.

This is a cooperative local protocol. Simply submitting a job does **not** cause the backend to summon Codex. An active host agent must continue reading and answering the inbox. Expired, invalid, missing, or failed answers use existing bounded recovery behavior or leave a retryable failed job; they never switch to OpenRouter. Rendering still needs usable media and the installed runtime.

## Credentials, costs, and limits

- Settings pairing stores a scoped `vna_` key and delegated Vidnets session in a Windows DPAPI-encrypted file under the app's writable `secure` folder. The launcher reads this privately. Do not print, copy into prompts, distribute, or commit that file or the account tokens. Pairing is per Windows user; another Windows account must pair independently.
- The key authorizes only generation, the inference inbox, and that account's agent job status. It cannot administer accounts, billing, settings, or other users' jobs. Settings can revoke or replace it. Account and active-plan checks remain enforced.
- Agent jobs use the host agent's own inference and block OpenRouter calls, including paid OpenRouter voice routes. They use local Audiogram narration. Agent subscription limits, downloaded assets, and separately billed media services are not free or included by implication. Job cost receipts count external agent requests separately from OpenRouter requests.
- No ChatGPT account login or subscription API is used by this protocol. The agent runs under its own supported environment and access.
- Vidnets may refresh its delegated session. If it expires, is revoked, or cannot renew (including conflicts with desktop sign-in), sign in and pair again. Keep the desktop closed during headless work to avoid two independent queue managers accessing the same queue.
- Per-request expiry and video preparation deadlines remain enforced. Local Remotion rendering continues under resource and progress supervision. Use a complete packaged/unpackaged installation, not just the installer executable.

## Preserved records

Service logs: writable data folder `logs/agent-service.log` and `logs/server-runtime.log`. Each video retains its normal output package, manifests, checkpoints, and usage receipts. Pending request JSON and extracted vision frames are in `temp/agent-bridge`. These contain script/media context; treat them as private project data.

This source tree and future package builds include the launcher and guide. An already installed older release needs an updated build; adding this document alone does not upgrade its service.
