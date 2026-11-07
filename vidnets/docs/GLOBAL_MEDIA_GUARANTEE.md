# Global media resolution contract

Vidnets Studio is designed to work across broad subject categories, not only
gaming and documentary topics. A job now follows one auditable contract:

1. The scene brain identifies the most specific supported niche from the full
   title and script. An explicit user category wins over a weak title guess.
2. Every provider query keeps the full canonical entity first and adds a
   domain anchor (for example, `Apple food cuisine` or `Apple technology`).
   This prevents short names from drifting into unrelated results.
3. YouTube, web, and stock lanes are searched according to the niche profile;
   candidates are deduplicated, decoded, checked for safety/overlays, and
   verified by the Qwen visual gate when available.
4. If a provider times out or returns no usable asset, the resolver retries the
   opposite lane and verified YouTube timestamp/frame rescue before giving up.
5. For mixed/global jobs only, the final fallback is a grounded graphic that
   contains the canonical subject and scene text. It is logged as
   `Grounded Graphic Fallback`, never silently presented as external footage,
   and prevents blank frames. YouTube-only mode remains strict and requires a
   real downloaded clip.

The guarantee is operational: the render receives a nonblank, auditable visual
for every scene whenever the local renderer can create an image. It is not a
claim that an external search index is factually perfect for every possible
title. The audit metadata records the selected provider, fallback count,
verification state, and whether any blank asset remained so support can inspect
degraded jobs instead of guessing.
