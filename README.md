# Vidzeo Muse Worker Engine

This repository hosts the **ScriptIT** and **Vidnets** engines for headless autonomous execution by the **Muse Agent**.

## Overview
- **ScriptIT (`scriptit/`)**: Generates structured, high-retention narration beats and outlines using Muse's LLM reasoning.
- **Vidnets (`vidnets/`)**: Handles media retrieval (stock + clips), audio generation, Remotion composition rendering (1080p), and thumbnail generation.

## Execution Model
1. **CMS Queue Dispatch**: The CMS dispatches a title job (with target duration, voice, and channel guidelines).
2. **Muse Script Generation**: Muse uses ScriptIT's narration schema to craft the full spoken script.
3. **Muse Vidnets Rendering**: Vidnets compiles scenes, retrieves assets, executes the Remotion render, and exports the final video (`video.mp4`) and thumbnail (`thumbnail.jpg`).
4. **Return Artifacts**: Muse delivers the finalized artifacts back to the local CMS package directory for upload.

## Installation
```bash
# 1. Install ScriptIT dependencies
cd scriptit
npm install
cd ..

# 2. Install Vidnets dependencies
cd vidnets
npm install
cd ..
```

## Security Notice
- No hardcoded API keys or provider tokens are bundled in this repository.
- System operates in agent-direct mode using Muse's internal intelligence.
