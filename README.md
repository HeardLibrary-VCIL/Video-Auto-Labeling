# Video Auto-Labeling

An automated video segmentation pipeline that detects scene boundaries, generates transcripts, classifies segments using AI, and presents results in an interactive web interface.

## What It Does

Upload a video → the system automatically:
1. **Detects visual boundaries** using a trained per-source frame classifier, falling back to black-frame detection
2. **Transcribes audio** via AWS Transcribe
3. **Classifies segments** using Claude AI (configurable segment types) or choose your model via deployment command
4. **Identifies transitions** (teaser/preview detection before breaks)
5. **Merges all results** into a unified timeline


Results are viewable in a React web application with interactive timeline, video playback, and editing tools.

## Architecture

```
Video Upload
  → [parallel]
      Track A: Visual Boundary Detection (trained model, black-frame fallback)
      Track B: Audio Transcription (AWS Transcribe)
  → Readiness Check (waits for both)
  → AI Segment Classification (Bedrock Claude)
  → Transition Detection (Bedrock Claude)
  → Results Merger (unified JSON)
  → Web Frontend (React + Amplify)
```

## Project Structure

```
Video-Auto-Labeling/
├── backend/
│   ├── infrastructure/
│   │   └── video-segmentation-pipeline.yaml  ← SAM template (deploys all resources)
│   ├── visual-detector/
│   │   ├── Dockerfile.model           ← Image for the model-based detector
│   │   ├── handler.py                 ← Model-based detector (primary)
│   │   ├── detect_segments.py         ← Frame classification + segment cleanup
│   │   ├── Dockerfile                 ← Image for the black-frame fallback
│   │   ├── worker.py                  ← Analyzes video chunks
│   │   ├── merger.py                  ← Pairs boundaries into segments
│   │   ├── launcher.py                ← Batch orchestration
│   │   └── statemachine.asl.json      ← Step Functions definition
│   ├── transcription/                 ← AWS Transcribe trigger
│   ├── ai-segmentation/               ← Claude-based classification
│   ├── transition-detector/           ← Bedrock transition detection (not currently deployed)
│   ├── results-merger/                ← Combines all pipeline outputs
│   └── layers/
│       └── dependencies/              ← Shared Lambda layer
├── train-visual-detector/              ← Offline training for detection models
│   ├── train_model.py                  ← Trains a per-source .pkl classifier
│   └── requirements.txt                ← Pinned to match the Lambda image
├── frontend/                           ← React + Amplify web application
├── landing-page/                       ← GitHub Pages documentation site
├── USER_GUIDE.md                       ← Usage, configuration, model training
├── PRODUCTION_DEPLOYMENT.md            ← Full deployment guide
└── README.md
```

## Customization

This pipeline is designed to be adapted to different video types:

| What to Customize | Where | Example |
|-------------------|-------|---------|
| Segment types | `ai-segmentation/prompts.py` | News, Commercial, Interview, Sports |
| Visual detection models | `train-visual-detector/train_model.py` | One trained model per video source |
| Detection sensitivity | `config/ticker_network_config.json` in S3 | Threshold and scan rate, no redeploy |
| Black-frame fallback | `visual-detector/worker.py` | Boundary thresholds, chunk sizing |
| AI model | Template parameter `BedrockModelId` | Claude Sonnet, Haiku |
| Timeline colors | `frontend/src/utils/segment_types.ts` | Per-type color mapping |

## Requirements

- AWS account with Bedrock access 
- Lambda concurrent execution quota ≥ 1000
- Docker (for building visual detector)
- Python 3.12+ locally, to train detection models
- Node.js 20+ (for frontend)

## Documentation

- [User Guide](USER_GUIDE.md) — Configuration, model training, troubleshooting
- [Deployment Guide](PRODUCTION_DEPLOYMENT.md) — Setup instructions
- [Video-Auto-Labeling website with demo and feature overview](https://heardlibrary-vcil.github.io/Video-Auto-Labeling/)

## License

See [LICENSE](LICENSE).
