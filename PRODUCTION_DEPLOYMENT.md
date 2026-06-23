# Production Deployment Guide

Complete deployment of the TV News Segmentation pipeline. This template is **account-agnostic** — it can be deployed to any AWS account without modification.

## Accounts
### VCIL
| Account | ID | Profile | Purpose |
|---------|-----|---------|---------|
| Dev | `XXXXXXXXXXXX` | `vcil` | Source videos, existing transcripts |
| Production | `XXXXXXXXXXXX` | `vcil-admin` | Pipeline deployment |

### LTAS
| Account | ID | Profile | Purpose |
|---------|-----|---------|---------|
| Dev | `XXXXXXXXXXXX` | `vcil` | Source videos, existing transcripts |
| Production | `XXXXXXXXXXXX` | `vcil-admin` | Pipeline deployment |

To deploy to a **different account**, just use that account's profile. All resource names use `${AWS::AccountId}` automatically.

## Architecture Overview

The pipeline has two processing tracks. Track B only fires when **both** tracks' prerequisites are met:

```
TRACK A — Commercial Detection (video only, no transcripts needed):
  Videos uploaded → run_batches.sh → Commercial Detector → commercial_results/

TRACK B — AI Segmentation (requires BOTH transcript AND commercial detection output):
  Video uploaded → [auto] Transcription (AWS Transcribe)
  Readiness Checker waits for: transcript ✓ AND commercial results ✓
    → AI Segmentation (Claude) → news_results/
      → Teaser Detector (loads commercial_results/ + news_results/, merges N/C timeline, detects teasers)
        → teaser_results/{video}_with_teasers.json
          → Evaluation (runs FIRST on teaser output)
            → Results Merger (enriches with titles + full transcripts → unified JSON)

Final output: result/{video}.json → Amplify Frontend
```

## Components

| Component | What It Does | Trigger |
|-----------|-------------|---------|
| **Transcription** | Generates word-level transcript from video via AWS Transcribe | S3 event (video upload to `video/*.mp4`) |
| **Commercial Detector** | Black frame analysis → commercial segments | Batch invocation via `run_batches.sh` |
| **Readiness Checker** | Waits for both transcript + commercial results before proceeding | S3 event (transcript OR commercial result arrives) |
| **AI Segmentation** | Claude segments news stories from transcripts | Invoked by Readiness Checker when both inputs exist |
| **Teaser Detector** | Loads commercial results (from VIDEO_BUCKET) + news segments (from PROCESSING_BUCKET), merges into N/C timeline, detects teasers at N→C transitions | S3 event (`news_results/` prefix in processing bucket) |
| **Evaluation** | Compares predictions to ground truth | Invoked after teaser detection completes (runs BEFORE merger) |
| **Results Merger** | Uses teaser JSON as authoritative timeline, enriches N segments with titles + full transcripts from transcription bucket | Invoked after evaluation completes |
| **Amplify Frontend** | React app for viewing/editing results | Git push CI/CD |

## Prerequisites

- AWS CLI configured with target account SSO profile
- SAM CLI installed
- Docker Desktop running (for Commercial Detector Lambda builds)
- Node.js 20+ and npm

### AWS Account Requirements

The target account must have:
- **Lambda**: concurrent execution quota ≥ 1000 (request increase from default 10 for new accounts)
- **Bedrock**: `bedrock:InvokeModel` allowed for `us-east-1` and `us-east-2` regions
  - The `us.` inference profile prefix routes across US regions
  - If SCPs restrict regions, ensure both `us-east-1` and `us-east-2` are allowed for Bedrock
- **S3**: no restrictions on bucket creation
- **Step Functions**: standard workflows enabled
- **ECR**: for Docker-based Lambda images

** Check for Lambda request concurrency limits:**

```bash
aws lambda get-account-settings --profile <PROFILE> --query "AccountLimit.ConcurrentExecutions"
```

**Check for SCP restrictions:**
```bash
# Test Bedrock access
aws bedrock list-inference-profiles --profile <PROFILE> --region us-east-1

# If you get AccessDenied, the SCP blocks Bedrock — use a less restrictive account or contact admin
```

### Environment Setup

**1. Install required tools (macOS):**

```bash
# AWS CLI — official installer
curl "https://awscli.amazonaws.com/AWSCLIV2.pkg" -o "AWSCLIV2.pkg"
sudo installer -pkg AWSCLIV2.pkg -target /

# SAM CLI — official installer
curl -L "https://github.com/aws/aws-sam-cli/releases/latest/download/aws-sam-cli-macos-arm64.pkg" -o "sam-cli.pkg"
sudo installer -pkg sam-cli.pkg -target /

# Docker Desktop — download from https://www.docker.com/products/docker-desktop/
# Make sure Docker is running before building

# Node.js (for Amplify frontend) — official installer or use brew
curl -o node.pkg "https://nodejs.org/dist/v20.18.0/node-v20.18.0.pkg"
sudo installer -pkg node.pkg -target /
```

**2. Configure AWS SSO profiles:**

```bash
# For whatever account you're deploying to
aws configure sso
# SSO session name: vcil
# SSO start URL: https://vanderbilt-vcil.awsapps.com/start
# SSO region: us-east-1
# CLI profile name: <your-profile-name>
```





**3. Log in:**

```bash
aws sso login --profile <PROFILE>
aws sts get-caller-identity --profile <PROFILE> # verify correct account
```

**4. Start Docker:**

Docker Desktop must be running before `sam build` — the Commercial Detector Lambdas use Docker images (OpenCV).

```bash
docker info  # verify Docker is running
```

---

## Deployment Order

Deploy in this order for auto-sync between pipeline and frontend:

1. **Frontend first** — deploy Amplify app to get storage bucket names
2. **Backend second** — deploy with `AmplifyMainBucket`/`AmplifyDevBucket` parameters so results auto-sync
3. **Sync videos** — upload source videos to trigger the pipeline

If you deploy the backend first (without Amplify bucket names), the pipeline still works — results land in `result/` in the video bucket. You can add auto-sync later by updating the stack with the bucket names.

---

## Step 0: Deploy Amplify Frontend

Deploy the frontend first to get the Amplify storage bucket names:

1. Sign in to the target account → **AWS Amplify** → **Create new app**
2. Connect to GitHub repo → select branch → Deploy
3. After deploy completes, get the bucket name:

```bash
aws s3 ls --profile <PROFILE> | grep amplify | grep tvnews
```

Note the bucket name(s) — you'll pass them to the backend deployment.

---

## Step 1: Deploy All Backend Infrastructure

The unified SAM template deploys everything except the Amplify frontend. It is **account-agnostic** — all resource names use `${AWS::AccountId}` automatically.

```bash
cd infrastructure

# Build (uses Docker containers — no local Python 3.12 needed)
sam build --template-file tvnews-pipeline-complete.yaml --use-container

# Deploy (replace --profile with your target account's profile)
sam deploy \
  --profile <PROFILE> \
  --stack-name autolabeling \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --region us-east-1 \
  --resolve-s3 --resolve-image-repos \
```

To enable auto-sync to Amplify buckets (optional):

```bash
sam deploy \
  --profile vcil-admin \
  --stack-name autolabeling \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --region us-east-1 \
  --resolve-s3 --resolve-image-repos \
  --parameter-overrides \
    AmplifyMainBucket=amplify-xxxxx-ma-tvnewsvideostoragebucket-xxxxx \
    AmplifyDevBucket=amplify-xxxxx-de-tvnewsvideostoragebucket-xxxxx
```

To use an existing transcription bucket (optional):

```bash
sam deploy \
  --profile <PROFILE> \
  --stack-name autolabeling \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --region us-east-1 \
  --resolve-s3 --resolve-image-repos \
  --parameter-overrides \
    TranscriptionBucketName=my-existing-transcription-bucket
```

> **Note:** The `TranscriptionBucketName` parameter is optional. If provided, the stack uses the specified existing bucket instead of creating `tvnews-transcriptions-{accountId}`. Useful when transcripts already exist in another bucket.

### Deploying to a Different Account

Just change the `--profile`. Resources auto-name with that account's ID:

```bash
sam deploy \
  --profile <other-account-profile> \
  --stack-name autolabeling \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --region us-east-1 \
  --resolve-s3 --resolve-image-repos \
  --no-confirm-changeset
```

### What Gets Created

| Resource | Type |
|----------|------|
| `tvnews-videos-{accountId}` | S3 bucket for source videos |
| `tvnews-transcriptions-{accountId}` | S3 bucket for transcripts (or existing bucket via `TranscriptionBucketName` parameter) |
| `tvnews-processing-{accountId}` | S3 bucket for intermediate results |
| `tvnews-vocabulary-{accountId}` | S3 bucket for custom vocabulary |
| `tvnews-transcription-prod` | Lambda — transcribes videos (AWS Transcribe) |
| `tv-news-readiness-checker-prod` | Lambda — checks if both inputs are ready |
| `commercial-detector-worker-prod` | Lambda (Docker/ARM64) — scans for black frames |
| `commercial-detector-merger-prod` | Lambda (Docker/ARM64) — pairs black frames |
| `commercial-detector-launcher-prod` | Lambda (Docker/ARM64) — starts batch |
| `commercial-detector-batch-prod` | Step Functions state machine |
| `tv-news-segmentation-prod` | Lambda (Python 3.12) — AI segmentation |
| `tv-news-teaser-detector-prod` | Lambda (Python 3.12) — teaser detection |
| `tv-news-results-merger-prod` | Lambda — merges all results into unified JSON |
| `tv-news-evaluation-prod` | Lambda (Python 3.12) — computes metrics |
| `tv-news-fanout-prod` | Lambda — dispatches after segmentation |
| `tv-news-teaser-merger-prod` | Lambda — orchestrates merge + evaluation |
| 4 DynamoDB tables | Detection results storage |
| S3 event triggers | Auto-chain pipeline stages |
| Shared Lambda Layer | pydantic-ai, pydantic, boto3, rich |

---

## Step 2: Sync Source Videos

Videos may exist in a different account. Set up cross-account access for direct sync.

### 2a. Set Up Cross-Account Bucket Policy (one-time, on source account)

First, get the existing policy so you don't overwrite it:

```bash
aws s3api get-bucket-policy --bucket <SOURCE_BUCKET> --profile <SOURCE_PROFILE> --output text
```

Add the target account as a trusted reader:

```bash
aws s3api put-bucket-policy --bucket <SOURCE_BUCKET> --profile <SOURCE_PROFILE> --policy '{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowTargetAccountRead",
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::<TARGET_ACCOUNT_ID>:root"
      },
      "Action": [
        "s3:GetObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::<SOURCE_BUCKET>",
        "arn:aws:s3:::<SOURCE_BUCKET>/*"
      ]
    }
  ]
}'
```

**Important:** `put-bucket-policy` **replaces** the entire policy. If the bucket already has a policy, merge your new statement into the existing `Statement` array.

### 2b. Verify Cross-Account Access

```bash
aws s3 ls "s3://<SOURCE_BUCKET>/video/" --profile <TARGET_PROFILE>
```

### 2c. Sync Videos

```bash
aws s3 sync "s3://<SOURCE_BUCKET>/video/" "s3://tvnews-videos-<TARGET_ACCOUNT_ID>/video/" \
  --profile <TARGET_PROFILE> --region us-east-1
```

### 2d. Sync Transcripts (if available)

```bash
aws s3 sync "s3://<TRANSCRIPT_SOURCE>/" "s3://tvnews-transcriptions-<TARGET_ACCOUNT_ID>/" \
  --profile <TARGET_PROFILE> --region us-east-1
```

### Fallback: Two-Step Sync (no cross-account policy)

If you can't modify the source bucket policy:

```bash
aws s3 sync "s3://<SOURCE_BUCKET>/video/" /tmp/tvnews-videos/ --profile <SOURCE_PROFILE>
aws s3 sync /tmp/tvnews-videos/ "s3://tvnews-videos-<TARGET_ACCOUNT_ID>/video/" --profile <TARGET_PROFILE>
```

---

## Step 3: Run Commercial Detection (Track A)

Commercial detection only needs video files — no transcripts required.

```bash
cd Commercial-Segment-Detector-Video-Approach-

# Update run_batches.sh with prod bucket/profile, then:
./run_batches.sh --batch-size 20
```

Results written to: `s3://tvnews-videos-{accountId}/commercial_results/{video}_segments.json`

Once complete, this triggers the **Readiness Checker** which checks if a transcript also exists for each video.

---

## Step 4: Transcription (Automatic)

Transcription is **automatic** — when videos are uploaded to `video/*.mp4`, the Transcription Lambda fires and starts an AWS Transcribe job. Results land in:

`s3://tvnews-transcriptions-{accountId}/{video}.json`

This also triggers the **Readiness Checker**.

**If you already have transcripts** (from the dev account), sync them directly:

```bash
aws s3 sync "s3://<TRANSCRIPT_SOURCE>/" /tmp/transcripts/ --profile <SOURCE_PROFILE>
aws s3 sync /tmp/transcripts/ "s3://tvnews-transcriptions-{accountId}/" --profile vcil-admin
```

---

## Step 5: Configure S3 Event Triggers (Post-Deploy)

After deployment, add these S3 event notifications manually via the AWS Console. These connect the pipeline stages automatically.

### Processing Bucket (`tvnews-processing-{accountId}`)

Go to **S3** → `tvnews-processing-{accountId}` → **Properties** → **Event notifications** → **Create event notification**:

| # | Name | Prefix | Suffix | Event Type | Destination Lambda |
|---|------|--------|--------|------------|-------------------|
| 1 | `news-results-trigger` | `news_results/` | `_segments.json` | `s3:ObjectCreated:*` | `tv-news-teaser-detector-prod` |
| 2 | `teaser-results-trigger` | `teaser_results/` | `_with_teasers.json` | `s3:ObjectCreated:*` | `tv-news-teaser-merger-prod` |

### Transcription Bucket (`tvnews-transcriptions-{accountId}`)

| # | Name | Prefix | Suffix | Event Type | Destination Lambda |
|---|------|--------|--------|------------|-------------------|
| 1 | `readiness-transcript` | _(empty)_ | `.json` | `s3:ObjectCreated:*` | `tv-news-readiness-checker-prod` |

### Video Bucket (`tvnews-videos-{accountId}`)

| # | Name | Prefix | Suffix | Event Type | Destination Lambda |
|---|------|--------|--------|------------|-------------------|
| 1 | `video-upload-trigger` | `video/` | `.mp4` | `s3:ObjectCreated:*` | `tv-news-video-bucket-dispatcher-prod` |
| 2 | `commercial-results-trigger` | `commercial_results/` | `_segments.json` | `s3:ObjectCreated:*` | `tv-news-readiness-checker-prod` |

> **Note:** The Lambda resource-based permissions are created by the SAM template. You only need to add the S3 notification configuration.

---

## Step 6: AI Segmentation + Teaser Detection (Automatic)

Once the **Readiness Checker** confirms both inputs exist for a video:
1. AI Segmentation (Claude) runs → outputs news segments to `news_results/{video}_segments.json` in the processing bucket
2. Teaser Detector independently loads commercial results (from VIDEO_BUCKET) + news segments (from PROCESSING_BUCKET), merges them into a unified N/C timeline, and detects teasers at N→C transitions → outputs `teaser_results/{video}_with_teasers.json`
3. Evaluation runs FIRST on the teaser output (ensures evaluation data is available)
4. Results Merger uses the teaser JSON as its authoritative source, enriches N segments with titles from AI segmentation and full transcripts from the transcription bucket → unified JSON

All automatic — no manual intervention needed after Steps 2-5.

Final output: `s3://tvnews-videos-{accountId}/result/{video}.json`

---

## Step 6: Amplify Frontend

### Create the Amplify App

1. Sign in to prod account → **AWS Amplify** → **Create new app**
2. Connect to GitHub: `HeardLibrary-VCIL/demo-site`
3. Select `main` branch → Deploy

Or via CLI:

```bash
aws amplify create-app \
  --name tvnews-segmentation \
  --repository https://github.com/HeardLibrary-VCIL/demo-site \
  --platform WEB_COMPUTE \
  --region us-east-1 \
  --profile vcil-admin
```

### Verify Storage Paths

The `amplify/storage/resource.ts` defines access to:
- `video/*` — source broadcasts
- `result/*` — merged results (unified JSON)
- `commercial_results/*` — commercial detection output
- `evaluation/*` — evaluation metrics
- `edits/*` — human corrections

### Sync Results to Amplify Bucket

```bash
AMPLIFY_BUCKET=$(aws s3 ls --profile vcil-admin | grep amplify | grep tvnews | awk '{print $3}')

# Merged results for Results page
aws s3 sync "s3://tvnews-videos-{accountId}/result/" "s3://${AMPLIFY_BUCKET}/result/" --profile vcil-admin

# Commercial results for TVNEWSSTAFF page
aws s3 sync "s3://tvnews-videos-{accountId}/commercial_results/" "s3://${AMPLIFY_BUCKET}/commercial_results/" --profile vcil-admin

# Videos for playback
aws s3 sync "s3://tvnews-videos-{accountId}/video/" "s3://${AMPLIFY_BUCKET}/video/" --profile vcil-admin
```

### Pin Backend Dependencies

Prevent deploy failures from npm version drift:

```json
"devDependencies": {
  "@aws-amplify/backend": "1.5.0",
  "@aws-amplify/backend-cli": "1.2.9",
  "aws-cdk": "2.138.0",
  "aws-cdk-lib": "2.138.0",
  "constructs": "10.3.0"
}
```

---

## Pipeline Data Flow

```
INPUTS:
  s3://tvnews-videos-{accountId}/video/              ← source broadcasts (.mp4)

TRACK A — Commercial Detection:
  run_batches.sh → Launcher → Step Functions → Worker × N → Merger
  Output: s3://tvnews-videos-{accountId}/commercial_results/{video}_segments.json
    → [auto] Readiness Checker fires

AUTOMATIC TRANSCRIPTION:
  Video lands in video/*.mp4 → [S3 trigger] → Transcription Lambda (AWS Transcribe)
  Output: s3://tvnews-transcriptions-{accountId}/{video}.json
    → [auto] Readiness Checker fires

READINESS CHECKER:
  Checks: Does transcript exist? Does commercial_results exist?
  If BOTH → triggers AI Segmentation

TRACK B — AI Segmentation + Teaser + Evaluation + Merge:
  AI Segmentation (Claude) → s3://tvnews-processing-{accountId}/news_results/{video}_segments.json
    → [auto] Fanout → Teaser Detector
      (loads commercial_results/ from VIDEO_BUCKET + news_results/ from PROCESSING_BUCKET)
      (merges into unified N/C timeline, detects teasers at N→C transitions)
      → s3://tvnews-processing-{accountId}/teaser_results/{video}_with_teasers.json
        → [auto] TeaserMerger:
            1. Evaluation (runs FIRST on teaser output)
            2. Results Merger (uses teaser JSON as primary source, enriches with titles + full transcripts)
               → s3://tvnews-videos-{accountId}/result/{video}.json

FRONTEND:
  Results.tsx reads: s3://{amplify-bucket}/result/{video}.json
  TVNEWSSTAFF.tsx reads: s3://{amplify-bucket}/commercial_results/{video}_segments.json
```

---

## Verification Checklist

### Pre-Deployment (run before deploying)

- [ ] `aws sts get-caller-identity --profile <PROFILE>` → correct account
- [ ] `aws bedrock-runtime invoke-model \
  --model-id global.anthropic.claude-opus-4-8 \
  --body '{"messages":[{"role":"user","content":[{"type":"text","text":"hi"}]}],"max_tokens":10,"anthropic_version":"bedrock-2023-05-31"}' \
  --content-type application/json \
  --accept application/json \
  --cli-binary-format raw-in-base64-out \
  --profile <PROFILE> 
  ` → no AccessDenied
- [ ] `aws lambda get-account-settings --profile <PROFILE> --query "AccountLimit.ConcurrentExecutions"` → ≥ 1000
- [ ] Docker running: `docker info`

### Post-Deployment

- [ ] Stack deployed: `aws cloudformation describe-stacks --stack-name autolabeling --profile <PROFILE>`
- [ ] Videos synced: `aws s3 ls s3://tvnews-videos-{accountId}/video/ --profile <PROFILE>`
- [ ] Transcription works: upload a test `.mp4` to `video/`, check logs for `tvnews-transcription-prod`
- [ ] Commercial detection: `./run_batches.sh --batch-size 1`, check Step Functions for SUCCEEDED
- [ ] Bedrock access: invoke segmentation directly and check for 200 response
- [ ] Readiness checker: verify logs show "Both ready, triggering segmentation"
- [ ] AI Segmentation: check `s3://tvnews-processing-{accountId}/news_results/` for output
- [ ] Merged results: `aws s3 ls s3://tvnews-videos-{accountId}/result/ --profile <PROFILE>`
- [ ] Amplify site loads, videos play, segments display on timeline

---

## Troubleshooting

**SAM build fails (Docker):**
- Ensure Docker Desktop is running: `docker info`
- Use `sam build --use-container` (required for correct arm64 layer builds)
- If Docker is unresponsive: `killall -9 Docker` then reopen

**Layer builds with wrong architecture (pydantic_core import error):**
- Verify build output: `ls .aws-sam/build/PipelineDependenciesLayer/python/pydantic_core/*.so`
- Must show `aarch64` in the filename, not `x86_64`
- Template must have `BuildArchitecture: arm64` in the layer's Metadata
- Clean build: `rm -rf .aws-sam && sam build --use-container`

**Bedrock 403 — SCP blocking:**
- Error: "explicit deny in a service control policy"
- The `us.` inference profile routes to multiple US regions (us-east-1, us-east-2)
- The account's SCP must allow `bedrock:InvokeModel` in both `us-east-1` and `us-east-2`
- Test: `aws bedrock list-inference-profiles --profile <PROFILE> --region us-east-1`
- **If blocked: deploy to a less restrictive account**

**Bedrock 400 — "Retry with inference profile":**
- Raw model IDs (e.g., `anthropic.claude-sonnet-4-5-20250929-v1:0`) no longer support on-demand
- Use inference profile: `us.anthropic.claude-sonnet-4-5-20250929-v1:0`


**Lambda throttling (TooManyRequestsException):**
- New accounts have 10 concurrent executions
- Request increase: `aws service-quotas request-service-quota-increase --service-code lambda --quota-code L-B99A9384 --desired-value 1000 --profile <PROFILE>`
- Reduce batch size until approved: `./run_batches.sh --batch-size 1`

**Cross-account S3 access denied:**
- Use two-step sync (pull to local, push to target)
- Or add bucket policy on source bucket granting target account read access
- `put-bucket-policy` REPLACES the entire policy — get existing policy first

**Readiness Checker not triggering segmentation:**
- Check logs: `aws logs tail /aws/lambda/tv-news-readiness-checker-prod --since 15m --profile <PROFILE>`
- Verify both inputs exist: transcript in `tvnews-transcriptions-*` and commercial results in `commercial_results/`
- Common bug: filename extraction — check that `get_video_base_name` strips `_segments` suffix

**S3 notification overlap error during deploy:**
- S3 doesn't allow multiple notifications with overlapping prefix+suffix on the same bucket
- Solution: use a single dispatcher Lambda per bucket (already implemented in template)
- Clear stale notifications: `aws s3api put-bucket-notification-configuration --bucket <BUCKET> --notification-configuration '{}' --profile <PROFILE>`
- Delete stuck stack: `aws cloudformation delete-stack --stack-name autolabeling --retain-resources <FAILED_RESOURCE>`

**Lambda package too large (>250MB):**
- Heavy dependencies are in a shared Lambda Layer (not bundled in function zip)
- `transcriptionAITool/lambda/requirements.txt` must be empty (comments only)
- scikit-learn/pandas removed — replaced with pure Python implementations

**Amplify deploy fails (CDK Assembly Error):**
- Pin devDependencies (remove `^` carets)
- Restore `package-lock.json` from last successful deploy
- Use `npm ci` not `npm install`

**Reinvoke segmentation process after redoploy**
aws lambda invoke \
  --function-name tv-news-readiness-checker-prod \
  --payload '{"Records":[{"s3":{"bucket":{"name":"tvnews-videos-XXXXXXXXXXXX"},"object":{"key":"commercial_results/20240828MSNBC_segments.json"}}}]}' \
  --cli-binary-format raw-in-base64-out \
  --profile dev-ltas \
  /tmp/response.json

---

## Cost Estimates

| Component | Per 1-hour video |
|-----------|-----------------|
| Commercial Detection (Lambda + Step Functions) | ~$0.05 |
| Transcription (AWS Transcribe) | ~$1.44 |
| AI Segmentation (Bedrock Claude) | ~$0.60-$0.80 |
| Teaser Detection (Bedrock Claude) | ~$0.10-$0.30 |
| **Total (full pipeline)** | **~$1.50** |
| **Total (transcripts already available)** | **~$1.00** |

Note: Bedrock has no free tier. Lambda free tier covers commercial detection easily. AWS Transcribe charges $0.024/min.
