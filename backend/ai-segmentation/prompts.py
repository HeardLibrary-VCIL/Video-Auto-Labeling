"""
AI Segmentation Prompts — Customize for your video type.

This file defines:
1. The system prompt that guides Claude's segmentation
2. Pydantic models for structured output

Examples of customization:
- News broadcasts: Commercial, News Segment, Tease, Interview
- Lectures: Introduction, Topic, Q&A, Break
- Sports: Pre-game, Play, Replay, Commentary, Commercial
- Podcasts: Intro, Segment, Ad Read, Outro
"""

from pydantic import BaseModel, Field
from typing import Optional


# ─── SEGMENT TYPE DEFINITIONS (customize these) ──────────────────────────────

SEGMENT_TYPES = {
    "content": "Main content segment (story, topic, presentation)",
    "transition": "Brief transition or preview of upcoming content",
    "break": "Commercial break or pause in content",
    "intro": "Opening or introduction",
    "outro": "Closing or sign-off",
}

# Single-letter codes for timeline display
SEGMENT_CODES = {
    "content": "C",
    "transition": "T",
    "break": "B",
    "intro": "I",
    "outro": "O",
}


# ─── PYDANTIC MODELS ─────────────────────────────────────────────────────────

class SegmentResult(BaseModel):
    """A single detected segment."""
    title: str = Field(description="Brief descriptive title for this segment")
    segment_type: str = Field(description="One of: content, transition, break, intro, outro")
    first_sentence: str = Field(description="Verbatim first sentence of the segment")
    last_sentence: str = Field(description="Verbatim last sentence of the segment")


class TranscriptAnalysis(BaseModel):
    """Complete segmentation result."""
    segments: list[SegmentResult]


# ─── SYSTEM PROMPT (customize for your domain) ───────────────────────────────

SYSTEM_PROMPT = """
You are an expert video content analyst. Your task is to analyze transcripts and identify
distinct content segments according to defined segment types.

NOTE: Visual boundaries have been removed and replaced with [ BOUNDARY ] markers.
Each marker indicates a hard break — no segment should span across a [ BOUNDARY ] marker.

SEGMENT TYPES:
{segment_types}

IDENTIFICATION RULES:
1. Look for topic changes, speaker transitions, or shifts in content focus
2. [ BOUNDARY ] markers are absolute hard boundaries between segments
3. Extract first_sentence and last_sentence VERBATIM from the transcript
4. Do not create overlapping segments
5. Ensure complete coverage — every part of the transcript belongs to a segment

EXTRACTION RULES:
- first_sentence and last_sentence must be word-for-word from the transcript
- Include filler words (um, uh) exactly as they appear
- These are used for programmatic timestamp matching
""".format(
    segment_types="\n".join(f"- {name}: {desc}" for name, desc in SEGMENT_TYPES.items())
)
