import { useState, useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';
import { getUrl } from 'aws-amplify/storage';
import { SEGMENT_TYPE_COLORS, SEGMENT_TYPE_LABELS } from '../utils/segment_types';

interface Segment {
  id: number;
  start_time: string;
  end_time: string;
  label: string;
  title: string;
  segment_position: string;
}

export default function Results() {
  const location = useLocation();
  const videoRef = useRef<HTMLVideoElement>(null);

  const [segments, setSegments] = useState<Segment[]>([]);
  const [videoUrl, setVideoUrl] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [videoDuration, setVideoDuration] = useState<number | null>(null);
  const [highlightedSegment, setHighlightedSegment] = useState<number | null>(null);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const handleLoaded = () => setVideoDuration(video.duration);
    video.addEventListener('loadedmetadata', handleLoaded);
    if (video.readyState >= 1) setVideoDuration(video.duration);
    return () => video.removeEventListener('loadedmetadata', handleLoaded);
  }, [videoUrl]);

  useEffect(() => {
    const init = async () => {
      try {
        setLoading(true);
        const videoFileName = location.state?.videoFileName || '';

        if (location.state?.videoUrl) {
          setVideoUrl(location.state.videoUrl);
        }

        if (videoFileName) {
          const videoBaseName = videoFileName.replace(/\.[^/.]+$/, '');
          await fetchResults(videoBaseName);
        }
      } catch (error) {
        console.error('Error:', error);
      } finally {
        setLoading(false);
      }
    };
    init();
  }, []);

  const fetchResults = async (videoBaseName: string) => {
    const resultPath = `result/${videoBaseName}.json`;
    try {
      const { url } = await getUrl({ path: resultPath });
      const response = await fetch(url.toString());
      if (!response.ok) return;

      const data = await response.json();
      const parsed: Segment[] = data.segments.map((seg: Record<string, unknown>, i: number) => ({
        id: i + 1,
        start_time: secToTime(Number(seg.segment_start ?? seg.start_time ?? 0)),
        end_time: secToTime(Number(seg.segment_end ?? seg.end_time ?? 0)),
        label: mapSegmentType(String(seg.segment_type || seg.label || 'C')),
        title: String(seg.title || ''),
        segment_position: String(i + 1),
      }));

      setSegments(parsed);
    } catch (error) {
      console.log(`No results found at ${resultPath}`);
    }
  };

  const mapSegmentType = (type: string): string => {
    const normalized = type.toUpperCase().trim();
    if (normalized.includes('CONTENT') || normalized.includes('NEWS')) return 'C';
    if (normalized.includes('BREAK') || normalized.includes('COMMERCIAL')) return 'B';
    if (normalized.includes('TRANSITION') || normalized.includes('TEASE')) return 'T';
    if (normalized.includes('INTRO')) return 'I';
    if (normalized.includes('OUTRO')) return 'O';
    if (normalized.length === 1) return normalized;
    return 'C';
  };

  const timeToSec = (t: string): number => {
    if (!t) return 0;
    if (!isNaN(Number(t))) return parseFloat(t);
    const parts = t.split(':').map(Number);
    if (parts.length === 2) return parts[0] * 60 + parts[1];
    if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
    return 0;
  };

  const secToTime = (s: number): string => {
    if (isNaN(s) || s < 0) return '0:00';
    const mm = Math.floor(s / 60);
    const ss = Math.floor(s % 60);
    return `${mm}:${ss.toString().padStart(2, '0')}`;
  };

  const getSegmentColor = (label: string): string => {
    return SEGMENT_TYPE_COLORS[label?.toUpperCase().trim()] || SEGMENT_TYPE_COLORS.DEFAULT;
  };

  const getSegmentLabel = (label: string): string => {
    return SEGMENT_TYPE_LABELS[label?.toUpperCase().trim()] || SEGMENT_TYPE_LABELS.DEFAULT;
  };

  const jumpToTime = (time: string) => {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = timeToSec(time);
    video.play();
  };

  if (!location.state?.videoFileName) {
    return (
      <div style={{ padding: '3rem', textAlign: 'center' }}>
        <h2>No video selected</h2>
        <p style={{ color: '#666' }}>Go to Videos to select a video first.</p>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: '1200px', margin: '2rem auto', padding: '0 2rem' }}>
      <h2 style={{ marginBottom: '1.5rem' }}>Segmentation Results</h2>

      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '2rem' }}>
        {/* Video + Timeline */}
        <div>
          <video ref={videoRef} src={videoUrl} controls style={{
            width: '100%', borderRadius: '8px', marginBottom: '1rem',
          }} />

          {/* Timeline */}
          <div style={{
            position: 'relative', height: '40px', background: '#e2e8f0',
            borderRadius: '6px', overflow: 'hidden',
          }}>
            {videoDuration && segments.map((s) => {
              const startPct = (timeToSec(s.start_time) / videoDuration) * 100;
              const widthPct = ((timeToSec(s.end_time) - timeToSec(s.start_time)) / videoDuration) * 100;
              return (
                <div
                  key={s.id}
                  onClick={() => jumpToTime(s.start_time)}
                  onMouseEnter={() => setHighlightedSegment(s.id)}
                  onMouseLeave={() => setHighlightedSegment(null)}
                  title={`${getSegmentLabel(s.label)}: ${s.start_time} - ${s.end_time}`}
                  style={{
                    position: 'absolute',
                    left: `${startPct}%`,
                    width: `${Math.max(widthPct, 0.5)}%`,
                    height: '100%',
                    backgroundColor: getSegmentColor(s.label),
                    opacity: highlightedSegment === s.id ? 1 : 0.7,
                    cursor: 'pointer',
                    transition: 'opacity 0.2s',
                  }}
                />
              );
            })}
          </div>

          {/* Legend */}
          <div style={{ display: 'flex', gap: '1rem', marginTop: '0.75rem', flexWrap: 'wrap' }}>
            {Object.entries(SEGMENT_TYPE_LABELS)
              .filter(([key]) => key.length === 1)
              .map(([key, label]) => (
                <div key={key} style={{ display: 'flex', alignItems: 'center', gap: '0.3rem' }}>
                  <div style={{
                    width: '12px', height: '12px', borderRadius: '2px',
                    backgroundColor: SEGMENT_TYPE_COLORS[key],
                  }} />
                  <span style={{ fontSize: '0.8rem', color: '#666' }}>{label}</span>
                </div>
              ))}
          </div>
        </div>

        {/* Segment List */}
        <div style={{ maxHeight: '600px', overflowY: 'auto' }}>
          {loading ? (
            <p>Loading results...</p>
          ) : segments.length === 0 ? (
            <p style={{ color: '#666' }}>No segmentation results found for this video.</p>
          ) : (
            segments.map((s) => (
              <div
                key={s.id}
                onClick={() => jumpToTime(s.start_time)}
                onMouseEnter={() => setHighlightedSegment(s.id)}
                onMouseLeave={() => setHighlightedSegment(null)}
                style={{
                  padding: '0.75rem',
                  marginBottom: '0.5rem',
                  background: highlightedSegment === s.id ? '#edf2f7' : '#fff',
                  border: '1px solid #e2e8f0',
                  borderLeft: `4px solid ${getSegmentColor(s.label)}`,
                  borderRadius: '4px',
                  cursor: 'pointer',
                  transition: 'background 0.2s',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.25rem' }}>
                  <strong style={{ fontSize: '0.85rem' }}>Segment {s.segment_position}</strong>
                  <span style={{ fontSize: '0.75rem', color: getSegmentColor(s.label) }}>
                    {getSegmentLabel(s.label)}
                  </span>
                </div>
                <div style={{ fontSize: '0.8rem', color: '#666' }}>
                  {s.start_time} → {s.end_time}
                </div>
                {s.title && (
                  <div style={{ fontSize: '0.8rem', color: '#888', marginTop: '0.25rem' }}>
                    {s.title}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
