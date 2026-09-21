# Video Auto-Labeling Pipeline — User Guide

This guide covers how to use, customize, and extend the pipeline for transcribing, classifying, and segmenting videos.

---

## Overview

The pipeline automatically processes videos (.mp4) and produces:
- **Visual segments** — commercial/content boundaries found by a trained per-source model, or by black-frame detection as a fallback
- **Content segments** — classified boundaries with titles, descriptions, and transcripts
- **Transitions** — filler or bridging segments between primary content

Results are viewable in the web frontend with an interactive timeline.

---

## Adding New Videos

Upload an MP4 file to the video bucket:

```
s3://{ProjectName}-videos-{accountId}/video/{filename}.mp4
```

The default project name is `video-autolabeling` (configurable via the `ProjectName` stack parameter).

### Filename Conventions

Name videos `{date}{source}` (e.g., `20260101ABC.mp4`). The trailing letters are the source ID, and the pipeline uses them to pick that source's trained detection model. A filename with no trailing source ID still processes — it falls back to black-frame detection.

### Upload via CLI

```bash
aws s3 cp /path/to/video.mp4 \
  s3://{ProjectName}-videos-{accountId}/video/ \
  --profile <PROFILE>
```

### What Happens Automatically

1. Video upload triggers transcription (AWS Transcribe)
2. Visual segment detection runs (trained model, falling back to black-frame)
3. Once both transcript + visual results exist → AI segmentation runs
4. Sub-segment detection (transitions, previews) runs on AI output
5. Results merger combines everything into `result/{video}.json`

---

## Configuration for Visual Detection

### How Detection Works

Visual detection has two methods, and the pipeline picks between them per video:

1. **Model-based detection (primary)** — a trained classifier decides, frame by
   frame, whether the frame is commercial or content. Each source has its own
   model, selected by the source ID in the filename.
2. **Black-frame detection (fallback)** — scans for the black frames that many
   broadcasters insert at segment boundaries, and pairs them into segments.

The pipeline falls back to black-frame detection whenever a model can't be used:

| Situation | Result |
|-----------|--------|
| Filename has no source ID | Black-frame detection |
| Source ID has no entry in the model config | Black-frame detection |
| Config names a model file that isn't in S3 | Black-frame detection |
| Model ran but found no segments | Black-frame detection |

Either way the output lands in `segment_results/{video}_segments.json`, so the
rest of the pipeline behaves identically.

### Model Config

The detector loads a per-source configuration from S3:

```
s3://{ProjectName}-videos-{accountId}/config/ticker_network_config.json
```

The key is configurable via the `ModelConfigKey` stack parameter.

#### Configuration Format

```json
{
  "SOURCE_A": {
    "model_threshold": 0.5,
    "scan_fps": 0.5,
    "profile_key": "config/profiles/SOURCE_A_model.pkl"
  },
  "SOURCE_B": {
    "model_threshold": 0.6,
    "scan_fps": 0.5,
    "profile_key": "config/profiles/SOURCE_B_model.pkl"
  }
}
```

There is no `default` entry. A source with no entry falls back to black-frame
detection rather than being scored by another source's model.

#### Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `model_threshold` | Commercial probability above which a frame counts as commercial (0.0–1.0) | 0.5 |
| `scan_fps` | Frames sampled per second (0.5 = one frame every two seconds) | 1 |
| `profile_key` | S3 key for the source's trained model (.pkl file) | `config/profiles/{SOURCE}_model.pkl` |

#### Tuning the Threshold

- **Too few segments detected** → lower the threshold (try 0.4, 0.3)
- **Too many false positives** → raise the threshold (try 0.6, 0.7)
- **Test without redeploying** — update the config JSON in S3 and re-invoke the detector

Post-processing constants (minimum segment length, merge gap, opening and
closing margins) are at the top of `backend/visual-detector/detect_segments.py`
and require a redeploy to change.

#### Updating Configuration

```bash
aws s3 cp ticker_network_config.json \
  s3://{ProjectName}-videos-{accountId}/config/ticker_network_config.json \
  --profile <PROFILE>
```

---

## Training Detection Models

Each source needs its own model, trained from ground truth on that source's
video. The trainer learns which *pixel positions* best separate commercial from
content frames — typically the area where a persistent ticker or logo sits — and
fits a logistic regression on just those pixels.

### When to Retrain

- When visual branding changes for a source
- When detection accuracy drops for recent videos
- When adding a new video source
- When processing archival footage from a different era (a source's 2005
  graphics are not its 2025 graphics)

### 1. Set Up the Training Environment

```bash
cd train-visual-detector
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

The pinned versions matter. The model is a pickle, and it is loaded by
`backend/visual-detector/Dockerfile.model`, which installs the same versions.
Training with a different scikit-learn can produce a model the Lambda cannot
load, or one that loads and quietly predicts differently. If you change a
version in one file, change it in the other.

### 2. Prepare Ground Truth

A CSV of labeled segments. Two column layouts are accepted:

```csv
Filename,SegmentType,BeginTime,EndTime
20260101SOURCE,c,996,1087
20260101SOURCE,n,1087,1465
```

```csv
file,segment_type,segment_start,segment_end
20260101SOURCE,c,996,1087
20260101SOURCE,n,1087,1465
```

Two things to get right:

- **`c` marks commercials.** Every other segment type is treated as
  non-commercial. The labels must include both, or training fails.
- **Times are frame numbers by default.** Pass `--time-unit seconds` if yours
  are seconds.

The trainer locates each video as `{first 8 characters of the filename column}{network}.mp4`
inside the videos directory — so a CSV row of `20260101SOURCE` with
`--network SOURCE` looks for `20260101SOURCE.mp4`.

### 3. Train

Videos referenced by the CSV must be on local disk.

```bash
python3 train_model.py \
  --csv ground_truth/SOURCE_ground_truth.csv \
  --videos /path/to/videos \
  --network SOURCE \
  --output SOURCE_model.pkl
```

Useful options:

| Option | Description | Default |
|--------|-------------|---------|
| `--time-unit` | `frames` or `seconds`, matching your CSV | `frames` |
| `--frame-size` | Resolution frames are reduced to before scoring, as WxH | `64x36` |
| `--top-k` | How many of the most informative pixel channels the classifier uses | `500` |
| `--output` | Output model path | `commercial_model.pkl` |

**Name the output `{SOURCE}_model.pkl`** to match the convention the pipeline
expects. The default output name is generic and will not be found by the
detector's fallback key.

Training prints its accuracy and writes an importance map next to the model
(`SOURCE_model.pkl` → `SOURCE_model_importance.png`) — a three-panel image
showing a reference frame, the pixels the model relies on, and the two overlaid. Check it: the hot regions should sit on the
persistent on-screen graphic. If they're scattered across the whole frame, the
model has latched onto something incidental and will generalize poorly.

### 4. Upload the Model

Model files go in the `config/profiles/` prefix of the video bucket:

```bash
aws s3 cp SOURCE_model.pkl \
  s3://{ProjectName}-videos-{accountId}/config/profiles/SOURCE_model.pkl \
  --profile <PROFILE>
```

Anything that can write to `config/profiles/` can run code inside the detector
Lambda, because loading a model unpickles it. Restrict write access to that
prefix accordingly.

### 5. Add the Source to the Config

```json
{
  "SOURCE": {
    "model_threshold": 0.5,
    "scan_fps": 0.5,
    "profile_key": "config/profiles/SOURCE_model.pkl"
  }
}
```

Upload it as shown in [Updating Configuration](#updating-configuration). The
next video whose filename ends in that source ID uses the model — no redeploy
needed.

### 6. Test the Model

```bash
aws lambda invoke \
  --function-name {ProjectName}-transition-detector-prod \
  --cli-binary-format raw-in-base64-out \
  --payload '{"s3_bucket":"{ProjectName}-videos-{accountId}","s3_key":"video/MY_VIDEO.mp4","video_name":"MY_VIDEO"}' \
  /tmp/model_test.json && cat /tmp/model_test.json
```

Read the response:

- `segments_count` above zero — the model worked.
- `"fallback": "black-frame"` — no usable model. The `message` field says
  which of the four fallback conditions was hit.

---

## Segment Types

The pipeline supports configurable segment types via the `SegmentTypes` stack parameter. The default is:

```
content,commercial,transition
```

Customize this to match your domain:

| Domain | Example Types |
|--------|---------------|
| Broadcast | `content,commercial,transition,preview` |
| Lectures | `lecture,break,qa,intro,outro` |
| Sports | `gameplay,replay,commentary,halftime,ad` |
| Podcasts | `discussion,ad_read,intro,outro,music` |

The AI segmentation prompt (`backend/ai-segmentation/prompts.py`) and the sub-segment detector should be updated to understand your segment vocabulary.

---

## Transcription Vocabulary

AWS Transcribe supports custom vocabularies to improve recognition of domain-specific terms.

### Custom Vocabulary Location

```
s3://{ProjectName}-vocabulary-{accountId}/
```

### Adding Custom Terms

1. Create a vocabulary file (tab-separated):

```
Phrase	IPA	SoundsLike	DisplayAs
CustomTerm		CUS-tom-term	CustomTerm
```

- **Phrase** (required): The word or phrase
- **IPA**: International Phonetic Alphabet pronunciation (optional)
- **SoundsLike**: Phonetic hints separated by hyphens (optional)
- **DisplayAs**: How it should appear in the transcript (optional)

2. Upload to the vocabulary bucket:

```bash
aws s3 cp custom_vocabulary.txt \
  s3://{ProjectName}-vocabulary-{accountId}/custom_vocabulary.txt \
  --profile <PROFILE>
```

3. Register with AWS Transcribe:

```bash
aws transcribe create-vocabulary \
  --vocabulary-name {project}-custom \
  --language-code en-US \
  --vocabulary-file-uri s3://{ProjectName}-vocabulary-{accountId}/custom_vocabulary.txt \
  --profile <PROFILE>
```

4. Wait for the vocabulary to be ready:

```bash
aws transcribe get-vocabulary --vocabulary-name {project}-custom --profile <PROFILE>
```

5. Update the transcription Lambda to use it (set `VOCABULARY_NAME` env var or modify the Transcribe job parameters).

### When to Update Vocabulary

- Recurring proper nouns with unusual pronunciations
- Technical jargon specific to your video content
- Brand names or acronyms that are frequently misrecognized

---

## Authentication (Cognito)

The web frontend is protected by AWS Cognito, deployed automatically via Amplify Gen 2. Users must sign in before accessing videos or results.

### How It Works

- Amplify creates a Cognito User Pool with email-based login
- The React app wraps all routes in an `<Authenticator>` component — unauthenticated users see the sign-in/sign-up form
- Authenticated users get scoped access to S3 storage paths (videos, results, edits)

### Managing Users

**Create a user (admin):**
```bash
aws cognito-idp admin-create-user \
  --user-pool-id <USER_POOL_ID> \
  --username user@example.com \
  --user-attributes Name=email,Value=user@example.com \
  --temporary-password "TempPass123!" \
  --profile <PROFILE>
```

**Find your User Pool ID:**
```bash
npx ampx generate outputs --app-id <APP_ID> --branch main
# Check amplify_outputs.json → auth.user_pool_id
```

**Self-service sign-up:** By default, users can self-register via the sign-up form. To restrict access, disable self-registration in the Cognito console or add a pre-sign-up Lambda trigger for approval logic.

### Customizing Auth

Edit `amplify/auth/resource.ts` to change login behavior:

```typescript
import { defineAuth } from '@aws-amplify/backend';

export const auth = defineAuth({
  loginWith: {
    email: true,
    // phone: true,        // Enable phone sign-in
    // externalProviders: { google: {...} }  // Social login
  },
});
```

After changes, push to trigger a new Amplify deployment.

---

## Viewing Results

### Web Frontend

1. Navigate to the Amplify-hosted site
2. Sign in with your credentials
3. Select a video from the Videos page
4. View the Results page with:
   - Color-coded timeline showing segment types
   - Video playback synced to segment markers
   - Segment details panel with titles and transcripts

### Edit Mode

Click "Edit Segments" to:
- Drag segment boundaries on the timeline
- Edit start/end times, segment types, titles, and transcripts inline
- Add or remove segments
- Merge adjacent segments
- Save corrections to S3 (for ground truth building)

### Raw JSON

The pipeline output for each video is at:
```
s3://{ProjectName}-videos-{accountId}/result/{video}.json
```

#### Output Format

```json
{
  "video": "20260101SOURCE",
  "source_file": "...",
  "segments": [
    {
      "segment_start": 0.0,
      "segment_end": 300.5,
      "segment_type": "content",
      "label": "content",
      "title": "Opening segment title",
      "transcript": "Full transcript text..."
    },
    {
      "segment_start": 300.5,
      "segment_end": 420.0,
      "segment_type": "commercial",
      "label": "commercial",
      "title": "",
      "transcript": ""
    }
  ],
  "transition_events": [],
  "evaluation": null
}
```

---

## Pipeline S3 Paths Reference

| Path | Contents |
|------|----------|
| `video/` | Source video files (.mp4) |
| `segment_results/` | Visual detection output per video |
| `result/` | Final merged JSON for frontend |
| `config/ticker_network_config.json` | Per-source model configuration |
| `config/profiles/` | Trained detection models (`{SOURCE}_model.pkl`) |

| Bucket | Purpose |
|--------|---------|
| `{ProjectName}-videos-{accountId}` | Videos, configs, final results |
| `{ProjectName}-transcriptions-{accountId}` | Transcription JSON output |
| `{ProjectName}-processing-{accountId}` | Intermediate AI/evaluation results |
| `{ProjectName}-vocabulary-{accountId}` | Custom Transcribe vocabularies |

---

## Troubleshooting

| Problem | Check |
|---------|-------|
| No results after upload | CloudWatch logs for the dispatcher Lambda |
| AI segmentation not triggering | Readiness checker — both transcript and visual results must exist |
| Poor segment detection | Tune `model_threshold` in the model config |
| Wrong segments detected | Retrain the source's model with current ground truth |
| Black-frame results when a model was expected | Check the detector's `message` field and CloudWatch logs — usually a missing `.pkl` or config entry |
| Transcription errors | Add terms to custom vocabulary |
| Frontend not updating | Check Amplify sync Lambda logs; verify `result/` prefix triggers |
