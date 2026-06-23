# Video Auto-Labeling

An automated video segmentation pipeline that detects scene boundaries, generates transcripts, classifies segments using AI, and presents results in an interactive web interface.

## What It Does

Upload a video → the system automatically:
1. **Detects visual boundaries** (black frames, scene changes) via computer vision
2. **Transcribes audio** via AWS Transcribe
3. **Classifies segments** using Claude AI (configurable segment types) or choose your model via deployment command
4. **Identifies transitions** (teaser/preview detection before breaks)
5. **Merges all results** into a unified timeline


Results are viewable in a React web application with interactive timeline, video playback, and editing tools.

## Architecture

```
Video Upload
  → [parallel]
      Track A: Visual Boundary Detection (Step Functions + Lambda)
      Track B: Audio Transcription (AWS Transcribe)
  → Readiness Check (waits for both)
  → AI Segment Classification (Bedrock Claude)
  → Transition Detection (Bedrock Claude)
  → Results Merger (unified JSON)
  → Web Frontend (React + Amplify)
```

## Quick Start

```bash
# Prerequisites: AWS CLI, SAM CLI, Docker

# 1. Deploy infrastructure
cd backend/infrastructure
sam build --use-container
sam deploy --stack-name video-autolabeling \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --resolve-s3 --resolve-image-repos \
  --no-confirm-changeset

# 2. Upload a video
aws s3 cp my-video.mp4 s3://video-autolabeling-{accountId}/video/

# 3. Results appear automatically
aws s3 ls s3://video-autolabeling-{accountId}/result/
```

## Project Structure

```
Video-Auto-Labeling/
├── backend/
│   ├── infrastructure/
│   │   └── template.yaml              ← SAM template (deploys all resources)
│   ├── visual-detector/
│   │   ├── Dockerfile                 ← OpenCV-based boundary detection
│   │   ├── worker.py                  ← Analyzes video chunks
│   │   ├── merger.py                  ← Pairs boundaries into segments
│   │   ├── launcher.py                ← Batch orchestration
│   │   └── statemachine.asl.json      ← Step Functions definition
│   ├── transcription/                 ← AWS Transcribe trigger
│   ├── ai-segmentation/               ← Claude-based classification
│   ├── transition-detector/            ← Identifies transitions between segments
│   ├── results-merger/                 ← Combines all pipeline outputs
│   └── layers/
│       └── dependencies/               ← Shared Lambda layer
├── frontend/                           ← React + Amplify web application
├── landing-page/                       ← GitHub Pages documentation site
├── PRODUCTION_DEPLOYMENT.md            ← Full deployment guide
└── README.md
```

## Customization

This pipeline is designed to be adapted to different video types:

| What to Customize | Where | Example |
|-------------------|-------|---------|
| Segment types | `ai-segmentation/prompts.py` | News, Commercial, Interview, Sports |
| Visual detection method | `visual-detector/worker.py` | Black frames, logos |
| Transition types | `transition-detector/prompts.py` | Teaser, preview, recap |
| AI model | Template parameter `BedrockModelId` | Claude Sonnet, Haiku |
| Timeline colors | `frontend/src/utils/segment_types.ts` | Per-type color mapping |

## Requirements

- AWS account with Bedrock access 
- Lambda concurrent execution quota ≥ 1000
- Docker (for building visual detector)
- Node.js 20+ (for frontend)

## Documentation

- [Deployment Guide](PRODUCTION_DEPLOYMENT.md) — Full setup instructions
- [Landing Page](landing-page/) — Project overview site (GitHub Pages)

## License

See [LICENSE](LICENSE).
