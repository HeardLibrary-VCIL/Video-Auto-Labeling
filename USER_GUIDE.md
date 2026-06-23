# Video Auto-Labeling Pipeline — User Guide

This guide covers how to use, customize, and extend the pipeline for transcribing, classifying, and segmenting videos.

---

## Overview

The pipeline automatically processes videos (.mp4) and produces:
- **Visual segments** — timestamps where distinct visual transitions occur (e.g., black frames, scene changes)
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

If your videos follow the `{date}{source}` naming pattern (e.g., `20260101ABC.mp4`), the pipeline can automatically select source-specific detection profiles. Otherwise, the `default` configuration is used.

### Upload via CLI

```bash
aws s3 cp /path/to/video.mp4 \
  s3://{ProjectName}-videos-{accountId}/video/ \
  --profile <PROFILE>
```

### What Happens Automatically

1. Video upload triggers transcription (AWS Transcribe)
2. Visual segment detection runs (profile-based or black-frame)
3. Once both transcript + visual results exist → AI segmentation runs
4. Sub-segment detection (transitions, previews) runs on AI output
5. Results merger combines everything into `result/{video}.json`

---

## Configuration for Visual Detection

### Detection Config

The visual label detector loads a per-source configuration from S3:

```
s3://{ProjectName}-videos-{accountId}/config/detection_config.json
```

#### Configuration Format

```json
{
  "default": {
    "crop_top_fraction": 0.75,
    "crop_bottom_fraction": 1.0,
    "crop_left_fraction": 0.0,
    "crop_right_fraction": 1.0,
    "chi_square_threshold": 0.35,
    "scan_fps": 1,
    "profile_key": "config/profiles/default_profile.npy"
  },
  "SOURCE_A": {
    "crop_top_fraction": 0.80,
    "crop_bottom_fraction": 1.0,
    "crop_left_fraction": 0.0,
    "crop_right_fraction": 0.5,
    "chi_square_threshold": 0.30,
    "scan_fps": 1,
    "profile_key": "config/profiles/SOURCE_A_profile.npy"
  }
}
```

#### Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `crop_top_fraction` | Top of detection region as fraction of frame height (0.0 = top) | 0.75 |
| `crop_bottom_fraction` | Bottom of detection region as fraction of frame height | 1.0 |
| `crop_left_fraction` | Left edge of detection region as fraction of frame width | 0.0 |
| `crop_right_fraction` | Right edge of detection region as fraction of frame width | 1.0 |
| `chi_square_threshold` | Distance threshold — higher = more sensitive detection | 0.35 |
| `scan_fps` | Frames sampled per second (1 = one frame per second) | 1 |
| `profile_key` | S3 key for the source's color profile (.npy file) | — |

#### Tuning the Threshold

- **Too few segments detected** → lower the threshold (try 0.25, 0.20)
- **Too many false positives** → raise the threshold (try 0.40, 0.45)
- **Test without redeploying** — update the config JSON in S3 and re-invoke the detector

#### Updating Configuration

```bash
aws s3 cp detection_config.json \
  s3://{ProjectName}-videos-{accountId}/config/detection_config.json \
  --profile <PROFILE>
```

---

## Color Profiles

Each source/video type needs a precomputed color profile representing what the target visual region looks like during content segments. The detector compares each frame against this profile — frames that deviate significantly are classified as non-content (e.g., commercial breaks, interstitials).

### When to Rebuild a Profile

- When visual branding changes for a source
- When detection accuracy drops for recent videos
- When adding a new video source

### Building a Color Profile

1. **Prepare ground truth** — create a CSV with labeled segments:

```csv
Filename,SegmentType,BeginTime,EndTime
20260101SOURCE,content,300,996
20260101SOURCE,commercial,996,1087
20260101SOURCE,content,1087,1465
```

Segment types should match your pipeline's configured types (e.g., `content`, `commercial`, `transition`).

2. **Place videos locally** — the videos referenced in the CSV must be accessible

3. **Run the profile builder**:

```bash
cd backend/visual-detector
python3 color_profile_builder.py
```

Update `CSV_PATH` and `VIDEO_DIR` at the top of the script before running.

4. **Upload the profile to S3**:

```bash
aws s3 cp color_profile.npy \
  s3://{ProjectName}-videos-{accountId}/config/profiles/{SOURCE}_profile.npy \
  --profile <PROFILE>
```

5. **Update the detection config** to reference the new profile key.

### Adding a New Source

1. Build a color profile from ground truth videos
2. Upload the profile to `config/profiles/{SOURCE}_profile.npy`
3. Add the source entry to `detection_config.json`
4. Pipeline will automatically use the new config on next video upload matching that source identifier

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
| `config/detection_config.json` | Visual detector configuration |
| `config/profiles/` | Color profile .npy files |

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
| Poor segment detection | Tune `chi_square_threshold` in detection config |
| Wrong segments detected | Rebuild color profile with current ground truth |
| Transcription errors | Add terms to custom vocabulary |
| Frontend not updating | Check Amplify sync Lambda logs; verify `result/` prefix triggers |
