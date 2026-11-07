# Unified Visual Intelligence Pipeline

Vidzeo's production media path converts every narration scene into one source-neutral
`VisualRequirement`. YouTube, Archive.org, Vimeo (when configured), Openverse, Flickr,
Yandex Images,
Wikimedia Commons, Unsplash (when configured), Pexels, and Pixabay receive the same
query variants. A source selector is treated as a media preference, not as a routing
wall.

## Runtime flow

1. Reuse the upstream AI query plan and normalize it into visual goal, objects,
   actions, environment, style, media preference, entities, negative terms, and
   query variants.
2. Search enabled adapters concurrently. YouTube searches and downloads use the
   encrypted browser-session broker; no cookie material enters the asset database.
3. Normalize all results into `AssetCandidate` records.
4. Apply metadata relevance, negative-title, resolution, licensing, and source
   reliability filters before downloading previews.
5. Analyze only the preview shortlist for resolution, contrast, sharpness, duplicate
   visuals, and—when local models are configured—CLIP and YOLO evidence.
6. Download a maximum of two shortlisted video segments by default and sample their
   timelines. The best verified time window is cut for the scene.
7. Rank candidates with the production formula: semantic 40%, objects 30%, quality
   15%, source reliability 10%, licensing 5%. If an optional model is unavailable,
   its signal is explicitly marked as metadata-derived; the system never fabricates
   a model score.
8. Download the original winning image or reuse the verified winning video segment.
9. Persist the normalized scene decision (including overlay regions, provenance,
   confidence, cost, and asset fingerprint) and local path so a matching future scene
   can resolve without internet access.

YouTube clips with a confident relevance match but a small, stable caption or corner
logo enter the cleanup lane. Vision supplies normalized boxes; one FFmpeg encode blurs
all eligible boxes, and the cleaned clip is revalidated before promotion. Large,
moving, central, or uncertain overlays are rejected.

## Asset database

The default root is `cache/asset_database`, configurable with
`VIDZEO_ASSET_DATABASE_DIR`. It contains a SQLite/WAL index and source asset folders
with `metadata.json`, `previews`, `thumbnails`, `frames`, and `embeddings`. CLIP image
embeddings are stored as NumPy files when a local CLIP model is enabled.

Credentials, API keys, browser profiles, and YouTube cookies are never written here.

## Local vision on an 8 GB machine

Vision models are optional, lazy-loaded once, and inference is serialized to avoid RAM
spikes. Vidzeo does not silently download models. Configure local files explicitly:

```dotenv
VIDZEO_CLIP_MODEL_DIR=C:\models\clip-onnx
VIDZEO_YOLO_MODEL=C:\models\yolov8n.onnx
```

For the lowest-memory CLIP path, place `vision_model.onnx`, `text_model.onnx`, and
`tokenizer.json` in `VIDZEO_CLIP_MODEL_DIR`. They run through the already bundled
ONNX Runtime and tokenizer stack, without PyTorch. Explicit file overrides are also
available as `VIDZEO_CLIP_VISION_ONNX`, `VIDZEO_CLIP_TEXT_ONNX`, and
`VIDZEO_CLIP_TOKENIZER`. A local Transformers-format CLIP directory is supported as a
fallback when Transformers and PyTorch are installed.

Without them, the engine remains operational using metadata semantics and preview
quality analysis, and reports the backend status in generation metadata.

## Speed controls

Defaults are deliberately conservative for 8 GB systems:

```dotenv
VIDZEO_VISUAL_CANDIDATES_PER_SOURCE=10
VIDZEO_VISUAL_METADATA_LIMIT=50
VIDZEO_VISUAL_PREVIEW_LIMIT=15
VIDZEO_VISUAL_DEEP_VIDEO_LIMIT=2
VIDZEO_VISUAL_QUERY_VARIANTS=2
VIDZEO_VISUAL_SCENE_WORKERS=3
```

The scene workers also bound concurrent winner delivery, including authenticated
YouTube segment downloads. Raising them may improve throughput on faster connections
but increases memory, disk, and network pressure.

## Provider configuration

Pexels and Pixabay use keys already supplied by the Vidzeo UI. Unsplash and Vimeo are
enabled when their credentials are present:

```dotenv
UNSPLASH_ACCESS_KEY=
VIMEO_ACCESS_TOKEN=
```

When Pexels or Pixabay has no API key, its existing safe HTML adapter remains available
as a fallback.

## Transcript-first YouTube windows

YouTube V2 does not probe random timestamps when timestamped captions are available.
The retrieval order is:

1. Rank source videos from title, description, channel, duration, container subject,
   and negative entities.
2. Extract full yt-dlp metadata and one preferred manual/automatic caption track for
   a bounded set of strong long-form sources.
3. Parse JSON3 or WebVTT into timestamped segments and cache/coalesce the result by
   video ID.
4. Rank transcript windows against each final Whisper narration scene, its container,
   positive facets, and conflicting terms.
5. Assign distinct, non-adjacent windows. One source may serve several scenes when
   each scene has separate transcript evidence.
6. Download all assigned ranges from a shared source in one yt-dlp session, reusing
   the already-extracted source record. Normalize each clip to H.264 1280x720.
7. Send three 480-pixel frames, the exact narration, and the local transcript excerpt
   to the visual gate. Transcript evidence selects time; vision still decides whether
   the visible entity/action is correct.
8. A confident visual mismatch advances to the next transcript-ranked source. It
   never triggers a random timestamp from the same source.

The transcript stage is recall-oriented because the visual gate is authoritative.
Missing captions preserve the metadata/chapter fallback instead of leaving a scene
blank. Runtime metrics are stored in each YouTube run's `retrieval_audit.json`.

```dotenv
VIDZEO_YT_TRANSCRIPT_RANKING=1
VIDZEO_YT_TRANSCRIPT_MAX_SOURCES=8
VIDZEO_YT_TRANSCRIPT_SOURCES_PER_CONTAINER=3
VIDZEO_YT_TRANSCRIPT_CONCURRENCY=4
VIDZEO_YT_TRANSCRIPT_DEADLINE_SECONDS=25
VIDZEO_YT_TRANSCRIPT_LANGUAGES=en,en-US,en-GB
VIDZEO_YT_TRANSCRIPT_MIN_SCORE=22
VIDZEO_YT_TRANSCRIPT_WINDOW_GAP_SECONDS=20
VIDZEO_YT_TRANSCRIPT_MAX_WINDOWS_PER_SOURCE=16
VIDZEO_YT_TRANSCRIPT_GROUPED_DOWNLOADS=1
VIDZEO_YT_GROUPED_SOURCE_WORKERS=4
```
