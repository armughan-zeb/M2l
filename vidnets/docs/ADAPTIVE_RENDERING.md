# Adaptive local rendering

Updated October 9, 2026.

## Time policy

Remotion exports continue while useful work is occurring. The default no longer imposes a two-hour render cutoff or a fixed extension ceiling. Preparation providers still have bounded deadlines. Once a local render starts, the shared delivery watchdog yields to render supervision, including queued jobs and the direct generation API.

The renderer observes advancing frame/encoding counters and process-tree CPU/I/O activity. A quiet, inactive renderer still triggers the stall guard. Low-memory waits retain the saved edit and remain cancellable. The preview and individual asset/frame waits retain their own bounds.

## Resource policy

- One video export owns the render slot. Frames within that export can use one to eight workers, bounded by CPU and currently available RAM.
- Resource admission includes the OS/app reserve, worker memory, decoded frame buffers and cache/encoding headroom. The old forced start with only 256–384 MB available was removed.
- Automatic concurrency is re-evaluated between sections, rather than locked to the count measured before bundling. An explicit worker setting remains a ceiling.
- Under memory pressure, the renderer uses smaller caches, fewer decode threads and separate frame rendering/encoding. It waits for enough memory instead of attempting an unsafe start.
- The selected export resolution and documentary graphics remain unchanged.

## Saved sections

Exports use sections of approximately 30 seconds. Each section renders the original composition's absolute frame range, preserving its animation clock and existing transition overlaps.

Section video is H.264. Its audio is saved separately as 48 kHz PCM, then combined and encoded to AAC once at final assembly. This avoids repeated AAC padding at section boundaries. Remotion's chunk combiner joins the video streams and audio.

Completed sections include byte hashes, frame ranges and the actual worker/cache policy. Their cache key includes the saved manifest, render source/bundle, source-file versions and output settings. Changing the edit invalidates previous sections. Retrying the identical saved edit can reuse intact completed sections. A resource-related failure retries the current section with one worker; repeated errors still surface rather than looping indefinitely.

Section files remain beside the output in `.<output filename>.render-sections`. They consume disk space and should be retained until the export is accepted. Disk admission checks preserve completed sections if space is insufficient.

## Defaults

```dotenv
VIDZEO_RENDER_CONTINUE_WHILE_ACTIVE=1
VIDZEO_REMOTION_MAX_WORKERS=8
VIDZEO_REMOTION_CHUNK_SECONDS=30
```

`VIDZEO_REMOTION_CONCURRENCY` or the job's explicit concurrency setting constrains the automatic worker count. `VIDZEO_REMOTION_STALL_TIMEOUT_SECONDS` supervises inactivity, not total film render duration. Setting `VIDZEO_RENDER_CONTINUE_WHILE_ACTIVE=0` restores time-budget supervision for callers that need it.

## Validation scope

The modified Python sources and Node renderer passed syntax checks. A low-RAM benchmark, long-duration run and audiovisual verification of section boundaries have not been performed in this change set. The installed/published EXE has not been rebuilt with these source changes.
