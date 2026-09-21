# Production Deployment Guide

Complete deployment of the Video Auto-Labeling pipeline. This template is **account-agnostic** — it can be deployed to any AWS account without modification.

## Architecture Overview

The pipeline has two processing tracks. Track B only fires when **both** tracks' prerequisites are met:

```
TRACK A — Visual Segment Detection (video only, no transcripts needed):
  Videos uploaded → Visual Detector (trained model per source)
                    ↳ no model for that source? → Black-Frame Detector
                    → segment_results/

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
| **Visual Detector (model-based)** | Classifies frames with a trained per-source model | Dispatcher routing (S3 event) |
| **Black-Frame Detector** | Fallback detection via black frames; Step Functions fan-out | Invoked by the model detector, or batch invocation |
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
- Trained detection models, if using model-based detection (see [USER_GUIDE.md](USER_GUIDE.md#training-detection-models))
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

1. **Frontend** — deploy Amplify app to get storage bucket names
2. **Backend Deployment** — deploy with `AmplifyMainBucket`/`AmplifyDevBucket` parameters for auto-sync
3. **Customization** -- route to relevant video processing pathway and adjust ai prompts for target content and types of segments
4. **Upload videos** — triggers the pipeline automatically

---

## Step 1: Deploy Amplify Frontend

The frontend is a React web application deployed with AWS Amplify Gen 2. It provides video browsing, segmentation timeline viewing, and editing tools. You have two options for deployment:

### Option A: Deploy directly from the pre-built GitHub repo (easiest)

Use this if you want to get started quickly without modifying the code.

1. **Sign in to AWS Console** → Search for "Amplify" → Open **AWS Amplify**
2. Click **"Create new app"**
3. Select **"GitHub"** as the source → Click **"Next"**
4. You'll be asked to authorize AWS Amplify to access your GitHub account. Click **"Authorize"**
5. In the repository dropdown, select: `HeardLibrary-VCIL/video-segmentation-ux`
   - If you don't see it, click "Install GitHub App" and grant access to the HeardLibrary-VCIL organization
6. Select the branch: **main**
7. Amplify auto-detects the build settings from `amplify.yml` in the repo — no changes needed
8. Click **"Next"** → **"Save and deploy"**
9. Wait 3-5 minutes for the build to complete. You'll see a green checkmark when done.
10. Your app URL will appear at the top (e.g., `https://main.d1234abcde.amplifyapp.com`)

### Option B: Fork and customize (for your own branding/modifications)

Use this if you want to change colors, add pages, or modify behavior.

1. **Fork the repo** to your own GitHub account:
   - Go to https://github.com/HeardLibrary-VCIL/video-segmentation-ux
   - Click **"Fork"** (top right) → Create the fork
2. **Clone your fork locally** and make changes:
   ```bash
   git clone https://github.com/YOUR_ORG/video-segmentation-ux.git
   cd video-segmentation-ux
   ```
3. **Customize** (see `frontend/README.md` for details):
   - Colors/theme: edit `src/index.css` (CSS variables at top)
   - Segment types: edit `src/utils/segment_types.ts`
   - Logo: replace `public/favicon.png`
   - Pages: add new files in `src/pages/` and register in `src/App.tsx`
4. **Push your changes:**
   ```bash
   git add -A && git commit -m "Customize frontend" && git push
   ```
5. **Deploy in Amplify Console** following the same steps as Option A, but select your forked repo instead

### After Deployment: Get the Storage Bucket Name

Once deployed, Amplify creates an S3 storage bucket for your app. You'll need this name for the backend deployment (to enable auto-sync of results).

```bash
aws s3 ls --profile <PROFILE> | grep amplify
```

Look for a bucket name like `amplify-xxxxx-ma-videosegmentationstorage-xxxxx`. Note it — you'll pass it as the `AmplifyMainBucket` parameter when deploying the backend.

### After Deployment: Create Your First User

The app requires sign-in. Create a user via the Amplify-generated Cognito User Pool:

1. In the AWS Console → Search for **"Cognito"** → Open your User Pool
2. Click **"Create user"**
3. Enter an email address and a temporary password
4. The user will be prompted to set a new password on first sign-in

Or via CLI:
```bash
# Find your User Pool ID
aws cognito-idp list-user-pools --max-results 10 --profile <PROFILE>

# Create a user
aws cognito-idp admin-create-user \
  --user-pool-id <USER_POOL_ID> \
  --username user@example.com \
  --user-attributes Name=email,Value=user@example.com \
  --temporary-password "TempPass123!" \
  --profile <PROFILE>
```

### After Deployment: Generate amplify_outputs.json (for local development)

If you want to run the frontend locally (e.g., for development), generate the config file:

```bash
npx ampx generate outputs --app-id <APP_ID> --branch main
```

Find your App ID in the Amplify Console → App settings → General.

---

## Step 2: Deploy Backend Infrastructure

The SAM template deploys all Lambda functions, S3 buckets, DynamoDB tables, Step Functions, and event triggers.

```bash
cd backend/infrastructure

# Build (uses Docker — no local Python 3.12 needed)
sam build --template-file video-segmentation-pipeline.yaml --use-container

# Deploy examples
sam deploy \
  --profile <PROFILE> \
  --stack-name video-autolabeling \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --region us-east-1 \
  --resolve-s3 --resolve-image-repos \
  --no-confirm-changeset
```
```
sam deploy \
  --profile <PROFILE> \
  --stack-name <STACKNAME> \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --region <REGION> \
  --resolve-s3 --resolve-image-repos \
  --parameter-overrides \
    BedrockModelId=<BEDROCK_MODEL_ID> \
```

With Amplify auto-sync and Claude Sonnet 4.5 (optional, recommended):

```bash
sam deploy \
  --profile <PROFILE> \
  --stack-name video-autolabeling \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --region us-east-1 \
  --resolve-s3 --resolve-image-repos \
  --parameter-overrides \
    BedrockModelId=global.anthropic.claude-sonnet-4-5-20250929-v1:0 \
    AmplifyMainBucket=amplify-xxxxx-main-storagebucket-xxxxx \
    AmplifyDevBucket=amplify-xxxxx-dev-storagebucket-xxxxx
```
```
am deploy \
  --profile <PROFILE> \
  --stack-name <STACKNAME> \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --region <REGION> \
  --resolve-s3 --resolve-image-repos \
  --parameter-overrides \
    BedrockModelId=<BEDROCK_MODEL_ID> \
    ExternalVideoBucket=<PREEXISTENT_VIDEO_SOURCE_BUCKET_NAME> \    ExternalTranscriptBucket=<PREEXISTENT_TRANSCRIPT_SOURCE_BUCKET_NAME> \
    AmplifyDevBucket=amplify-d2ghkzdvbaby3m-de-tvnewsvideostoragebucket-gc82ezcrxgqq \
    AmplifyMainBucket=amplify-d2ghkzdvbaby3m-ma-tvnewsvideostoragebucket-lz64ujkynmno
```

### What Gets Created

| Resource | Purpose |
|----------|---------|
| `{ProjectName}-videos-{accountId}` | S3 — source videos + final results |
| `{ProjectName}-transcriptions-{accountId}` | S3 — transcription output |
| `{ProjectName}-processing-{accountId}` | S3 — intermediate AI results |
| `{ProjectName}-vocabulary-{accountId}` | S3 — custom Transcribe vocabulary |
| Visual Detector (Worker/Merger/Launcher) | Docker Lambdas — black-frame fallback detection |
| Visual Detector (model-based) | Docker Lambda — primary, model-based detection |
| AI Segmentation | Python Lambda — LLM classification |
| Sub-Segment Detector | Python Lambda — transition detection |
| Results Merger | Python Lambda — combines all outputs |
| Evaluation | Python Lambda — computes accuracy metrics |
| Readiness Checker | Python Lambda — gates AI pipeline |
| Video Dispatcher | Python Lambda — routes S3 events |
| Step Functions State Machine | Orchestrates parallel visual detection |
| 4 DynamoDB Tables | Detection results and evaluation storage |
| Shared Lambda Layer | pydantic-ai, boto3, rich |

---

## Step 2: Source Data

### Sync Pre-existing Transcripts (Optional)

```bash
aws s3 sync s3://<TRANSCRIPT_SOURCE>/ s3://{ProjectName}-transcriptions-{accountId}/ --profile <PROFILE>
```
### Upload segmentation ground truth csv for evaluation (Optional)

```
aws s3 sync <GROUND_TRUTH_SOURCE_FILE> \
  s3://tvnews-processing-<ACCOUNT_NUMBER>/ground_truth/ \
  --profile <AWS_PROFILE>

```


### Sync Videos from External Source (Optional)

```bash
aws s3 sync s3://<SOURCE_BUCKET>/video/ s3://{ProjectName}-videos-{accountId}/video/ --profile <PROFILE>
```
### Or Upload videos to trigger the pipeline:

```bash
aws s3 cp my-video.mp4 s3://{ProjectName}-videos-{accountId}/video/ --profile <PROFILE>
```

The dispatcher automatically routes to transcription + visual detection.

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
## Step 3a Stuck?: Verify S3 Triggers (Before First Video Upload)

After deployment, verify that the S3 event notifications were created correctly. If they're missing or stale from a previous deployment, the pipeline won't trigger.

**Check all bucket notifications:**

```bash
# Video bucket — should have the dispatcher trigger
aws s3api get-bucket-notification-configuration \
  --bucket {ProjectName}-videos-{accountId} --profile <PROFILE>

# Transcription bucket — should have readiness checker trigger
aws s3api get-bucket-notification-configuration \
  --bucket {ProjectName}-transcriptions-{accountId} --profile <PROFILE>

# Processing bucket — should have AI results trigger
aws s3api get-bucket-notification-configuration \
  --bucket {ProjectName}-processing-{accountId} --profile <PROFILE>
```

**If notifications are missing or stale**, clear and redeploy:

```bash
# Clear stale notifications (from previous stacks or manual config)
aws s3api put-bucket-notification-configuration \
  --bucket {ProjectName}-processing-{accountId} \
  --notification-configuration '{}' \
  --profile <PROFILE>

# Then redeploy the stack to re-create them
sam deploy ...
```

**If the video bucket trigger is missing** after deploy (custom resource race condition), add it manually:

```bash
aws s3api put-bucket-notification-configuration \
  --bucket {ProjectName}-videos-{accountId} \
  --notification-configuration '{
    "LambdaFunctionConfigurations": [{
      "Id": "video-bucket-trigger-prod-0",
      "LambdaFunctionArn": "arn:aws:lambda:{region}:{accountId}:function:{ProjectName}-video-dispatcher-prod",
      "Events": ["s3:ObjectCreated:*"]
    }]
  }' \
  --profile <PROFILE>
```

**Important:** Use `s3:ObjectCreated:*` (not just `Put`) — large files use multipart upload which fires `CompleteMultipartUpload`, not `Put`.
----
## STEP 3b Optional Ground Truth details: Setting Up Evaluation

Evaluation compares pipeline output against human-annotated ground truth. It's optional — the pipeline runs fine without it, but evaluation gives you accuracy metrics to track quality over time.

### 1. Create Ground Truth CSV

Create a CSV file with human-labeled segments for one or more videos:

```csv
file,segment_type,segment_start,segment_end,segment_transcript
20260402CNN,n,3620,13313,"Opening news segment transcript..."
20260402CNN,c,13313,18500,""
20260402CNN,n,18500,25032,"Second news segment..."
```

**Required columns:**
- `file` — video name (must match exactly, e.g., `20260402CNN`)
- `segment_type` — lowercase type label (`n`, `c`, `t`, `p`, `g`, `ignore`)
- `segment_start` — start time in seconds
- `segment_end` — end time in seconds

**Optional column:**
- `segment_transcript` — transcript text for the segment

### 2. Upload Ground Truth to Processing Bucket

```bash
aws s3 cp my_ground_truth.csv \
  s3://{ProjectName}-processing-{accountId}/ground_truth/{source}_ground_truth.csv \
  --profile <PROFILE>
```

The evaluation handler looks for ground truth by extracting the source/network from the video filename. For `20260402CNN`, it looks for `ground_truth/cnn_ground_truth.csv`.

### 3. Run Evaluation

Evaluation runs automatically after AI segmentation completes (triggered by the results merger). To run it manually:

```bash
aws lambda invoke \
  --function-name {ProjectName}-evaluation-prod \
  --cli-binary-format raw-in-base64-out \
  --payload '{"Records":[{"s3":{"bucket":{"name":"{ProjectName}-processing-{accountId}"},"object":{"key":"ai_results/{VIDEO_NAME}_segments.json"}}}]}' \
  /tmp/eval_response.json && cat /tmp/eval_response.json
```

### 4. View Results

Evaluation output is written to:
```
s3://{ProjectName}-processing-{accountId}/evaluation/{VIDEO_NAME}_evaluation.json
```

Metrics include:
- **Frame accuracy** — per-second classification correctness
- **Segment F1** — at IoU thresholds 0.25, 0.5, 0.75
- **Boundary F1** — at tolerances 2s, 5s, 10s
- **Mean boundary error** — average seconds off from true boundaries

### 5. Building Ground Truth from Existing Trint Annotations

If you have segmentation data in Trint (XMEML format), upload the XML export to trigger automatic conversion:

```bash
aws s3 cp my_video_export.xml \
  s3://{ProjectName}-videos-{accountId}/config/trint_segments/ \
  --profile <PROFILE>
```

Add transformation condition to the dispatcher lambda.

The dispatcher triggers the ground truth converter, which parses clip boundaries and transcript markers from the XML and appends rows to the appropriate ground truth CSV.

**Trint clip naming convention for segment types:**
- `20260402CNN.mp4 - 5` → type `ignore` (just a number = unlabeled)
- `20260402CNN.mp4 - 5 - n` → type `n` (news)
- `20260402CNN.mp4 - 8 - c` → type `c` (commercial)
- `20260402CNN.mp4 - 12 - t` → type `t` (tease)

---

## Detection Models (Model-Based Visual Detection)

The visual detector classifies each sampled frame with a trained per-source model, selected from the source ID at the end of the video filename. If no usable model exists for a video, the detector hands it to the black-frame detector instead, so the pipeline still produces `segment_results/`.

Training is a local, offline step. **Full instructions are in [USER_GUIDE.md](USER_GUIDE.md#training-detection-models)** — this section covers only what deployment needs.

### What the Stack Expects in S3

| Location | Contents |
|----------|----------|
| `config/profiles/{SOURCE}_model.pkl` | One trained model per source |
| `config/ticker_network_config.json` | Per-source threshold, scan rate, and model key |

The config key is set by the `ModelConfigKey` stack parameter.

### Deploying a Model

```bash
# 1. Upload the trained model
aws s3 cp SOURCE_A_model.pkl \
  s3://{ProjectName}-videos-{accountId}/config/profiles/SOURCE_A_model.pkl \
  --profile <PROFILE>

# 2. Register the source in the config
aws s3 cp - s3://{ProjectName}-videos-{accountId}/config/ticker_network_config.json \
  --content-type application/json --profile <PROFILE> <<'EOF'
{
  "SOURCE_A": {
    "model_threshold": 0.5,
    "scan_fps": 0.5,
    "profile_key": "config/profiles/SOURCE_A_model.pkl"
  },
  "SOURCE_B": {
    "model_threshold": 0.5,
    "scan_fps": 0.5,
    "profile_key": "config/profiles/SOURCE_B_model.pkl"
  }
}
EOF
```

Neither step requires a redeploy — the detector reads both on every invocation.

### Configuration Parameters

| Parameter | Description | Tuning |
|-----------|-------------|--------|
| `model_threshold` | Commercial probability above which a frame counts as commercial | Start at 0.5; lower for more detections, raise to cut false positives |
| `scan_fps` | Frames sampled per second | 0.5 for hour-long videos, 1.0 for shorter ones |
| `profile_key` | S3 key of the source's `.pkl` model | Must match the uploaded path |

### Keeping Training and Runtime in Sync

Models are pickled scikit-learn objects. The training environment (`train-visual-detector/requirements.txt`) and the Lambda image (`backend/visual-detector/Dockerfile.model`) pin the same versions of scikit-learn, NumPy, and OpenCV. **Change one, change the other** — a mismatch can fail to unpickle, or load and silently predict differently.

Because loading a model unpickles it, write access to `config/profiles/` amounts to code execution inside the detector Lambda. Restrict it accordingly.

### When to Retrain

- The source changes its on-screen branding or graphics
- Detection accuracy drops on recent videos
- You add a new video source
- You start processing archival footage from a different era

### Testing a Model

```bash
aws lambda invoke \
  --function-name {ProjectName}-transition-detector-prod \
  --cli-binary-format raw-in-base64-out \
  --payload '{"s3_bucket":"{ProjectName}-videos-{accountId}","s3_key":"video/MY_VIDEO.mp4","video_name":"MY_VIDEO"}' \
  /tmp/model_test.json && cat /tmp/model_test.json
```

Reading the response:

- `segments_count` above zero — the model ran and detected segments.
- `"fallback": "black-frame"` — no usable model was found; the `message` field names the reason (no source ID in the filename, no config entry, or a missing `.pkl`).
- `segments_count: 0` with an `error` field — no usable model *and* no black-frame launcher configured.

If the model runs but detects nothing on video you know contains commercials, lower `model_threshold` or retrain from more representative ground truth.

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
  Video upload → Dispatcher → Visual Detector (trained model for the source)
                              ↳ no usable model, or 0 segments found?
                                → Black-Frame Detector (Step Functions fan-out)
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
- [ ] Detector used the model, not the fallback → check its logs for `falling back to black-frame`
- [ ] AI Segmentation triggers → check `ai_results/`
- [ ] Final results appear in `result/`
- [ ] Frontend loads and displays segments

---
***IMPORTANT***

## Customizing AI Prompts

The AI segmentation behavior is controlled by `backend/ai-segmentation/prompts.py`. Edit this file to adapt the pipeline to your video domain.

### Segment Type Definitions

Define what types of segments the AI should look for:

```python
SEGMENT_TYPES = {
    "content": "Main content segment (story, topic, presentation)",
    "transition": "Brief transition or preview of upcoming content",
    "break": "Commercial break or pause in content",
    "intro": "Opening or introduction",
    "outro": "Closing or sign-off",
}
```

**Examples for different domains:**

| Domain | Segment Types |
|--------|---------------|
| Broadcasts | `content`, `commercial`, `tease`, `interview`, `weather` |
| Lectures | `lecture`, `qa`, `break`, `demo`, `summary` |
| Sports | `play`, `replay`, `commentary`, `halftime`, `ad` |
| Podcasts | `discussion`, `ad_read`, `intro`, `outro`, `music` |

### System Prompt

The `SYSTEM_PROMPT` variable instructs Claude on how to segment. Key things to customize:

- **Identification rules** — what signals a boundary in your content (topic shifts, speaker changes, visual cues mentioned in transcript)
- **Boundary markers** — `[ BOUNDARY ]` markers from visual detection are hard stops; no segment spans across them
- **Domain context** — tell the model what kind of content it's analyzing so it applies appropriate judgment

### Structured Output

The `SegmentResult` Pydantic model defines what Claude returns per segment:

```python
class SegmentResult(BaseModel):
    title: str           # Brief descriptive title
    segment_type: str    # Must match a key in SEGMENT_TYPES
    first_sentence: str  # Verbatim — used for timestamp matching
    last_sentence: str   # Verbatim — used for timestamp matching
```

The `first_sentence` and `last_sentence` fields are critical — they're matched against the word-level transcript to determine precise start/end timestamps. They must be **exact quotes** from the transcript.

### After Editing Prompts

1. Redeploy the stack: `sam build --use-container && sam deploy ...`
2. Or update just the Lambda code: `aws lambda update-function-code ...`
3. Re-run the pipeline for a test video to validate results
4. Update `frontend/src/utils/segment_types.ts` to match your new type labels and colors

---

## Extending the Pipeline: Sub-Segmentation & Multi-Pass AI

The base pipeline performs a single AI segmentation pass. For more complex video types, you can add additional AI passes that refine or sub-classify segments.

### Sub-Segmentation (Detecting Segments Within Segments)

Sub-segmentation adds a second AI pass that takes the output of the first pass and identifies finer-grained segments within each primary segment. Examples:

| Domain | Primary Segments | Sub-Segments |
|--------|-----------------|--------------|
| Broadcasts | News, Commercial | Tease, Preview, Goodnight (within News) |
| Lectures | Lecture, Break | Example, Definition, Theorem (within Lecture) |
| Sports | Play, Ad | Replay, Penalty, Goal (within Play) |
| Podcasts | Discussion, Ad | Question, Anecdote, Tangent (within Discussion) |

**Implementation approach:**

1. **Create a new Lambda** (`backend/subsegment-detector/`) that:
   - Loads the primary `ai_results/{video}_segments.json`
   - Loads the transcript for the video
   - Sends each primary content segment's transcript to Claude with a sub-classification prompt
   - Writes output to `subsegment_results/{video}_with_subsegments.json`

2. **Add an S3 trigger** on `ai_results/*_segments.json` that invokes your sub-segment Lambda (instead of or in addition to the AIResultsMerger)

3. **Chain the pipeline**: AI Segmentation → Sub-Segment Detector → Evaluation → Results Merger

4. **Update the Results Merger** to read from `subsegment_results/` as the authoritative source when available

**Example sub-segmentation prompt:**

```python
SUB_SEGMENT_PROMPT = """
You are analyzing a content segment from a video. Your task is to identify
sub-segments within this segment.

SEGMENT TYPE: {segment_type}
SEGMENT TIMERANGE: {start} - {end}

TRANSCRIPT:
{transcript}

Identify any of these sub-segment types within the text:
{sub_segment_types}

Return the sub-segments with verbatim first/last sentences for timestamp matching.
"""
```

### Multi-Pass AI Segmentation

For complex content, you can chain multiple AI passes with different prompts:

```
Pass 1: Broad classification (content vs break vs transition)
Pass 2: Topic segmentation (within content segments)
Pass 3: Sub-type detection (teaser, preview, recap within transitions)
```

**Implementation:**

1. **Duplicate `backend/ai-segmentation/`** as `backend/ai-segmentation-pass2/`
2. **Create a different prompt** in `pass2/prompts.py` focused on your refinement task
3. **Add to the SAM template** as a new Lambda with its own trigger:
   ```yaml
   AISegmentationPass2:
     Type: AWS::Serverless::Function
     Properties:
       FunctionName: !Sub '${ProjectName}-ai-segmentation-pass2-${Environment}'
       Handler: segment_handler.lambda_handler
       CodeUri: ../ai-segmentation-pass2/
       Environment:
         Variables:
           INPUT_PREFIX: ai_results/
           OUTPUT_PREFIX: refined_results/
   ```
4. **Wire the trigger**: `ai_results/*_segments.json` → Pass 2 → `refined_results/` → Evaluation + Merger

### Adding a New Segment Type

To add a segment type (e.g., "interview") to the existing single-pass pipeline:

1. **Update `backend/ai-segmentation/prompts.py`:**
   ```python
   SEGMENT_TYPES = {
       "content": "Main content segment",
       "interview": "Interview or Q&A session",  # ← new
       "break": "Commercial or pause",
       "transition": "Brief transition",
   }
   ```

2. **Update the SAM template** `SegmentTypes` parameter default:
   ```yaml
   Default: 'content,interview,break,transition'
   ```

3. **Update the frontend** `segment_types.ts`:
   ```typescript
   export const SEGMENT_TYPE_COLORS = {
     'CONTENT': '#45B7D1',
     'INTERVIEW': '#9B59B6',  // ← new
     'BREAK': '#FF6B6B',
     'TRANSITION': '#4ECDC4',
   };
   ```

4. **Redeploy** backend and frontend.

### Architecture Patterns for Extensions

| Pattern | When to Use | Trigger |
|---------|-------------|---------|
| Serial chain | Each pass depends on previous | S3 event on previous output prefix |
| Fan-out | Multiple independent classifiers | Single trigger invokes N Lambdas |
| Conditional | Only run on certain segment types | Check segment type in Lambda before processing |
| Feedback loop | Human corrections improve next run | Edits saved to `edits/` prefix, loaded as few-shot examples |

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
| Detector always falls back to black-frame | Check its logs for the reason: filename has no source ID, no entry in `ticker_network_config.json`, or `profile_key` points at a `.pkl` that isn't in S3 |
| Model fails to unpickle | scikit-learn version mismatch. `train-visual-detector/requirements.txt` and `backend/visual-detector/Dockerfile.model` must pin the same version |
| Model detects far too much or too little | Tune `model_threshold` in the config — no redeploy needed. If tuning doesn't help, retrain and check the importance map |

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

---