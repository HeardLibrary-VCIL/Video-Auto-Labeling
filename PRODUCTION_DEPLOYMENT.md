# Production Deployment Guide

Complete deployment of the Video Auto-Labeling pipeline. This template is **account-agnostic** — it can be deployed to any AWS account without modification.

## Architecture Overview

The pipeline has two processing tracks. Track B only fires when **both** tracks' prerequisites are met:

```
TRACK A — Visual Segment Detection (video only, no transcripts needed):
  Videos uploaded → Visual Detector → segment_results/

TRACK B — AI Segmentation (requires BOTH transcript AND visual detection output):
  Video uploaded → [auto] Transcription (AWS Transcribe)
  Readiness Checker waits for: transcript ✓ AND segment results ✓
    → AI Segmentation (Claude Sonnet is default-- choose the model of your choice during deployment) → ai_results/
      → Sub-Segment Detector (loads segment_results/ + ai_results/, merges timeline)
        → subsegment_results/{video}_with_subsegments.json
          → Evaluation → Results Merger (enriches with titles + full transcripts)

Final output: result/{video}.json → Amplify Frontend
```

## Components

| Component | What It Does | Trigger |
|-----------|-------------|---------|
| **Transcription** | Generates word-level transcript via AWS Transcribe | S3 event (video upload) |
| **Visual Detector** | Black frame / profile-based segment detection | S3 event or batch invocation |
| **Transition Detector** | Profile-based detection (alternative method) | Dispatcher routing |
| **Readiness Checker** | Waits for both transcript + segment results | S3 event (transcript OR segment result arrives) |
| **AI Segmentation** | LLM classifies content segments from transcripts | Invoked by Readiness Checker |
| **Sub-Segment Detector** | Merges visual + AI segments, detects transitions | S3 event (`ai_results/` in processing bucket) |
| **Evaluation** | Compares predictions to ground truth | Invoked after sub-segment detection |
| **Results Merger** | Produces unified JSON with titles + transcripts | Invoked after evaluation |
| **Amplify Frontend** | React app for viewing/editing results | Git push CI/CD |

## Prerequisites

- AWS CLI configured with target account profile
- SAM CLI installed
- Docker Desktop running (for visual detector Lambda builds)
- Node.js 20+ and npm

### AWS Account Requirements

- **Lambda**: concurrent execution quota ≥ 1000 (request increase from default 10 for new accounts)
- **Bedrock**: `bedrock:InvokeModel` allowed for the configured regions
- **S3**: no restrictions on bucket creation
- **Step Functions**: standard workflows enabled
- **ECR**: for Docker-based Lambda images

**Check Lambda concurrency:**
```bash
aws lambda get-account-settings --profile <PROFILE> --query "AccountLimit.ConcurrentExecutions"
```

**Check Bedrock access:**
```bash
aws bedrock list-inference-profiles --profile <PROFILE> --region us-east-1
```

### Environment Setup

**1. Install required tools (macOS):**

```bash
# AWS CLI
curl "https://awscli.amazonaws.com/AWSCLIV2.pkg" -o "AWSCLIV2.pkg"
sudo installer -pkg AWSCLIV2.pkg -target /

# SAM CLI
curl -L "https://github.com/aws/aws-sam-cli/releases/latest/download/aws-sam-cli-macos-arm64.pkg" -o "sam-cli.pkg"
sudo installer -pkg sam-cli.pkg -target /

# Docker Desktop — https://www.docker.com/products/docker-desktop/

# Node.js 20+
curl -o node.pkg "https://nodejs.org/dist/v20.18.0/node-v20.18.0.pkg"
sudo installer -pkg node.pkg -target /
```

**2. Configure AWS profile:**

```bash
aws configure sso
# Or use access keys:
aws configure --profile <PROFILE>
```

**3. Verify:**

```bash
aws sso login --profile <PROFILE>
aws sts get-caller-identity --profile <PROFILE>
docker info  # Docker must be running
```

---

## Deployment Order

1. **Frontend first** — deploy Amplify app to get storage bucket names
2. **Backend second** — deploy with `AmplifyMainBucket`/`AmplifyDevBucket` parameters for auto-sync
3. **Upload videos** — triggers the pipeline automatically

---

## Step 0: Deploy Amplify Frontend

1. **AWS Amplify Console** → Create new app → Connect to GitHub repo → Deploy
2. After deploy, get the storage bucket name:

```bash
aws s3 ls --profile <PROFILE> | grep amplify
```

Note the bucket name(s) for Step 1.

---

## Step 1: Deploy Backend Infrastructure

The SAM template deploys all Lambda functions, S3 buckets, DynamoDB tables, Step Functions, and event triggers.

```bash
cd backend/infrastructure

# Build (uses Docker — no local Python 3.12 needed)
sam build --template-file video-segmentation-pipeline-complete.yaml --use-container

# Deploy
sam deploy \
  --profile <PROFILE> \
  --stack-name video-autolabeling \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --region us-east-1 \
  --resolve-s3 --resolve-image-repos \
  --no-confirm-changeset
```

With Amplify auto-sync (optional):

```bash
sam deploy \
  --profile <PROFILE> \
  --stack-name video-autolabeling \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --region us-east-1 \
  --resolve-s3 --resolve-image-repos \
  --parameter-overrides \
    AmplifyMainBucket=amplify-xxxxx-main-storagebucket-xxxxx \
    AmplifyDevBucket=amplify-xxxxx-dev-storagebucket-xxxxx
```

### What Gets Created

| Resource | Purpose |
|----------|---------|
| `{ProjectName}-videos-{accountId}` | S3 — source videos + final results |
| `{ProjectName}-transcriptions-{accountId}` | S3 — transcription output |
| `{ProjectName}-processing-{accountId}` | S3 — intermediate AI results |
| `{ProjectName}-vocabulary-{accountId}` | S3 — custom Transcribe vocabulary |
| Visual Detector (Worker/Merger/Launcher) | Docker Lambdas — black frame detection |
| Transition Detector | Docker Lambda — profile-based detection |
| AI Segmentation | Python Lambda — LLM classification |
| Sub-Segment Detector | Python Lambda — transition detection |
| Results Merger | Python Lambda — combines all outputs |
| Evaluation | Python Lambda — computes accuracy metrics |
| Readiness Checker | Python Lambda — gates AI pipeline |
| Video Dispatcher | Python Lambda — routes S3 events |
| Step Functions State Machine | Orchestrates parallel visual detection |
| 4 DynamoDB Tables | Detection results storage |
| Shared Lambda Layer | pydantic-ai, boto3, rich |

---

## Step 2: Upload Videos

Upload videos to trigger the pipeline:

```bash
aws s3 cp my-video.mp4 s3://{ProjectName}-videos-{accountId}/video/ --profile <PROFILE>
```

The dispatcher automatically routes to transcription + visual detection.

### Sync from External Source

```bash
aws s3 sync s3://<SOURCE_BUCKET>/video/ s3://{ProjectName}-videos-{accountId}/video/ --profile <PROFILE>
```

### Sync Pre-existing Transcripts

```bash
aws s3 sync s3://<TRANSCRIPT_SOURCE>/ s3://{ProjectName}-transcriptions-{accountId}/ --profile <PROFILE>
```

---

## Step 3: Verify Pipeline

Once videos are uploaded, the pipeline runs automatically:

1. **Transcription** starts (3-5 min per hour of video)
2. **Visual detection** runs in parallel
3. **Readiness Checker** fires when both complete
4. **AI Segmentation** → **Sub-Segment Detection** → **Evaluation** → **Results Merger**
5. Final output appears in `result/{video}.json`

Check progress:

```bash
# Visual detection results
aws s3 ls s3://{ProjectName}-videos-{accountId}/segment_results/ --profile <PROFILE>

# AI results
aws s3 ls s3://{ProjectName}-processing-{accountId}/ai_results/ --profile <PROFILE>

# Final merged results
aws s3 ls s3://{ProjectName}-videos-{accountId}/result/ --profile <PROFILE>
```

---

## Manual Pipeline Invocation

Re-trigger the pipeline for an existing video without re-uploading:

```bash
# Full pipeline (dispatcher)
aws lambda invoke \
  --function-name {ProjectName}-video-dispatcher-prod \
  --cli-binary-format raw-in-base64-out \
  --payload '{"Records":[{"s3":{"bucket":{"name":"{ProjectName}-videos-{accountId}"},"object":{"key":"video/MY_VIDEO.mp4"}}}]}' \
  /tmp/response.json

# Just readiness check (triggers AI segmentation if both inputs exist)
aws lambda invoke \
  --function-name {ProjectName}-readiness-checker-prod \
  --cli-binary-format raw-in-base64-out \
  --payload '{"Records":[{"s3":{"bucket":{"name":"{ProjectName}-videos-{accountId}"},"object":{"key":"segment_results/MY_VIDEO_segments.json"}}}]}' \
  /tmp/response.json

# Just evaluation
aws lambda invoke \
  --function-name {ProjectName}-evaluation-prod \
  --cli-binary-format raw-in-base64-out \
  --payload '{"Records":[{"s3":{"bucket":{"name":"{ProjectName}-processing-{accountId}"},"object":{"key":"subsegment_results/MY_VIDEO_with_subsegments.json"}}}]}' \
  /tmp/response.json
```

---

## S3 Event Triggers

The template creates these S3 notifications automatically via a custom resource Lambda. If they don't appear after deploy, re-apply manually:

### Video Bucket

| Event | Filter | Target |
|-------|--------|--------|
| `s3:ObjectCreated:*` | (all) | Video Dispatcher |

### Transcription Bucket

| Event | Filter | Target |
|-------|--------|--------|
| `s3:ObjectCreated:*` | suffix `.json` | Readiness Checker |

### Processing Bucket

| Event | Filter | Target |
|-------|--------|--------|
| `s3:ObjectCreated:*` | prefix `ai_results/`, suffix `_segments.json` | Sub-Segment Detector |
| `s3:ObjectCreated:*` | prefix `subsegment_results/`, suffix `_with_subsegments.json` | Sub-Segment Merger |

Verify notifications:
```bash
aws s3api get-bucket-notification-configuration --bucket {ProjectName}-videos-{accountId}
aws s3api get-bucket-notification-configuration --bucket {ProjectName}-transcriptions-{accountId}
aws s3api get-bucket-notification-configuration --bucket {ProjectName}-processing-{accountId}
```

---

## Pipeline Data Flow

```
INPUTS:
  s3://{ProjectName}-videos-{accountId}/video/     ← source videos (.mp4)

TRACK A — Visual Segment Detection:
  Video upload → Dispatcher → Visual Detector (Step Functions)
  Output: s3://{ProjectName}-videos-{accountId}/segment_results/{video}_segments.json
    → [auto] Readiness Checker

AUTOMATIC TRANSCRIPTION:
  Video upload → Dispatcher → Transcription Lambda (AWS Transcribe)
  Output: s3://{ProjectName}-transcriptions-{accountId}/{video}.json
    → [auto] Readiness Checker

READINESS CHECKER:
  Checks: transcript exists? + segment_results exists?
  If BOTH → triggers AI Segmentation

TRACK B — AI Pipeline:
  AI Segmentation → s3://{ProjectName}-processing-{accountId}/ai_results/{video}_segments.json
    → [auto] Sub-Segment Detector → subsegment_results/{video}_with_subsegments.json
      → [auto] Sub-Segment Merger:
          1. Evaluation
          2. Results Merger → s3://{ProjectName}-videos-{accountId}/result/{video}.json

FRONTEND:
  Reads: s3://{amplify-bucket}/result/{video}.json
```

---

## Verification Checklist

### Pre-Deployment

- [ ] Correct account: `aws sts get-caller-identity --profile <PROFILE>`
- [ ] Bedrock access: `aws bedrock list-inference-profiles --profile <PROFILE> --region us-east-1`
- [ ] Lambda quota ≥ 1000: `aws lambda get-account-settings --profile <PROFILE>`
- [ ] Docker running: `docker info`

### Post-Deployment

- [ ] Stack deployed: `aws cloudformation describe-stacks --stack-name video-autolabeling --profile <PROFILE>`
- [ ] Upload test video → check dispatcher logs
- [ ] Transcription completes → check transcription bucket
- [ ] Visual detection runs → check `segment_results/`
- [ ] AI Segmentation triggers → check `ai_results/`
- [ ] Final results appear in `result/`
- [ ] Frontend loads and displays segments

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| SAM build fails | Ensure Docker is running. Use `sam build --use-container` |
| Layer wrong architecture | Clean: `rm -rf .aws-sam && sam build --use-container`. Check `BuildArchitecture: arm64` in template |
| Bedrock 403 (SCP) | Ensure `bedrock:InvokeModel` allowed in target regions. Try a less restrictive account |
| Bedrock 400 ("use inference profile") | Use `global.` or `us.` prefix, not raw model IDs |
| Lambda throttling | Request quota increase to 1000. Reduce batch sizes until approved |
| S3 trigger not firing | Check `get-bucket-notification-configuration`. Multipart uploads need `s3:ObjectCreated:*` not just `Put` |
| Readiness Checker not triggering | Verify both transcript AND segment_results exist. Check filename extraction logic in logs |
| Amplify deploy fails | Use `npm install` not `npm ci`. Pin CDK dependencies |
| pydantic_core import error | Rebuild layer with `--use-container`. Verify `.so` files show `aarch64` |

---

## Cost Estimates

| Component | Per 1-hour video |
|-----------|-----------------|
| Visual Detection (Lambda + Step Functions) | ~$0.05 |
| Transcription (AWS Transcribe) | ~$1.44 |
| AI Segmentation (Bedrock Claude) | ~$0.60–$0.80 |
| Sub-Segment Detection (Bedrock Claude) | ~$0.10–$0.30 |
| **Total (full pipeline)** | **~$2.00–$2.50** |
| **Total (transcripts already available)** | **~$0.75–$1.15** |

Lambda free tier covers visual detection easily. AWS Transcribe charges $0.024/min. Bedrock has no free tier.
