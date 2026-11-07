# Generate a complete video with one call

Start Vidnets, sign in, and configure your provider keys in Settings. Other tools
then call `POST http://127.0.0.1:5000/api/generate` with an active account's bearer
token. The call owns script preparation, media retrieval, narration, alignment,
direction, captions, sound and Remotion export. No separate start or render call
is required. Server authentication and plan limits still apply.

## Return the MP4 directly

Save this as `request.json`. Use a new request ID for each intended new video;
keep that ID when retrying the same request after a connection failure.

```json
{
  "request_id": "my-channel-video-001",
  "response_mode": "video",
  "cost_profile": "efficient",
  "type": "long",
  "title": "The Passing Game",
  "script": "An NFL field is one hundred yards long. A passing play depends on space and timing. Before the throw, routes reshape the defense.",
  "image_source": "Mixed Media",
  "category": "sports",
  "captions_enabled": true,
  "sound_effects_enabled": true,
  "graphic_density": 0.3,
  "render_resolution": [1920, 1080]
}
```

PowerShell example, using your own token in `VIDNETS_TOKEN`:

```powershell
curl.exe --fail-with-body --max-time 1800 `
  -H "Authorization: Bearer $env:VIDNETS_TOKEN" `
  -H "Content-Type: application/json" `
  --data-binary "@request.json" `
  --output "video.mp4" `
  "http://127.0.0.1:5000/api/generate"
```

On success the response is `video/mp4`. On error, the response is JSON; check the
HTTP status before treating the saved file as a video. Choose a client timeout
longer than the generation budget; long films and HD exports take longer.
The 1,800-second example is a client timeout, not a promised render time.

## Return the video location and receipts

Omit `response_mode`, or set it to `json`. Synchronous generation is the default.
The finished response includes `success`, `video_path`, `video_url`, `job_id`,
`output_package_dir`, `usage_ledger_path` and `cost_receipt_path`. Additional
manifest and editing metadata can accompany the first response. Saved project
logs and checkpoints are preserved for recovery and auditing.

Export completion and publishing approval are separate. A playable export can
return `success: true` with `ready_to_publish: false` and
`quality_status: "review_needed"`. Check `review_status` and `warnings` before
publishing. A missing or incomplete visual review is not approval. Recorded
graphics coverage below the requested share also carries a review notice.
These notices preserve the export instead of failing a finished job.

For `response_mode: "video"`, inspect `X-Vidnets-Ready-To-Publish` and
`X-Vidnets-Quality-Status` in the response headers. The MP4 download alone does
not certify accuracy or editing quality.

You can replay a completed request with `response_mode: "video"` to receive the
MP4 without generating it again. The replayed JSON response is intentionally
compact and may omit the first response's full manifest.

## Cost controls

The default `efficient` profile routes text planning, queries, direction,
composition, research and vision through `qwen/qwen3.5-flash-02-23`. It disables
hidden premium-model escalation, optional Jev decisions and reasoning tokens.
Normal provider retries are bounded. Successful schema-valid text responses are
cached within their own project for seven days; changed inputs or schemas miss
the cache. Invalid responses are never cached or accepted as editing plans.

Narration is separate: the selected TTS provider and voice remain in use.
Edge TTS has no OpenRouter charge. Other TTS, stock-media and external services
may have their own charges. Receipts distinguish reported provider cost from
calls for which a price is unavailable; an unknown price is not zero.

`cost_profile: "custom"` remains accepted for compatibility. Manual OpenRouter
generation uses Qwen 3.5 Flash for all brain/vision roles in both profiles;
supplied premium model names do not override that policy. Narration choices
remain independent. Paired external-agent jobs use their separate provider path.

## Safe retries and background jobs

`request_id`, or an `Idempotency-Key` header, makes deduplication durable and
account-scoped. If both are supplied, their values must match.

- Same ID and same input: completed video is returned without new provider calls.
- Same ID and different input: HTTP 409; use a new ID for a different video.
- Same ID still running: HTTP 409; retry that ID later rather than starting a duplicate.
- Failed or interrupted attempt: retry the same input and ID; saved work is reused
  where its checkpoints support recovery. Missing stages may still incur charges.
- Completed video subsequently deleted: HTTP 410. A new ID is required to rebuild.

For the app's background queue, send `execution_mode: "queue"`. This same
endpoint adds the job and starts processing, returning HTTP 202 with `item.id`.
Replay the same ID through `/api/generate` to retrieve its current status or its
completed result. Queue mode cannot return MP4 bytes immediately. The UI retains
its queue status, cancellation and retry controls; these are management APIs,
not separate generation stages. Only one export runs at a time.

## Limits

Invalid input, inactive accounts, unavailable providers, exhausted credits,
missing assets and disk or memory limits can still produce a clear error. The
pipeline uses bounded workers, grounded fallback direction and saved checkpoints
to reduce wasted work. It does not invent evidence or guarantee external services.
Fresh Tesla checks use live web/YouTube retrieval and measured narration, but
have exposed incorrect-media approvals and insufficient graphics. See the
pipeline reliability review for the current evidence and remaining release checks.
