// ─── CUSTOMIZE THESE FOR YOUR VIDEO TYPE ─────────────────────────────────────
// Define segment types, colors, and labels for the timeline visualization.
// Examples: News (Commercial, Story, Tease), Lectures (Topic, Q&A, Break), Sports (Play, Replay, Ad)

export const SEGMENT_TYPE_COLORS: Record<string, string> = {
  'C': '#45B7D1',     // Content
  'CONTENT': '#45B7D1',
  'B': '#FF6B6B',     // Break / Boundary
  'BREAK': '#FF6B6B',
  'T': '#4ECDC4',     // Transition
  'TRANSITION': '#4ECDC4',
  'I': '#DDA0DD',     // Intro
  'INTRO': '#DDA0DD',
  'O': '#A9A9A9',     // Outro
  'OUTRO': '#A9A9A9',
  'DEFAULT': '#CFAE70',
};

export const SEGMENT_TYPE_LABELS: Record<string, string> = {
  'C': 'Content',
  'CONTENT': 'Content',
  'B': 'Break',
  'BREAK': 'Break',
  'T': 'Transition',
  'TRANSITION': 'Transition',
  'I': 'Intro',
  'INTRO': 'Intro',
  'O': 'Outro',
  'OUTRO': 'Outro',
  'DEFAULT': 'Segment',
};
