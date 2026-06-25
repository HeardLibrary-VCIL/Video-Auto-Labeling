import { useNavigate } from 'react-router-dom';

export default function Home() {
  const navigate = useNavigate();

  return (
    <div style={{ maxWidth: '800px', margin: '4rem auto', padding: '0 2rem', textAlign: 'center' }}>
      <h1 style={{ fontSize: '2.5rem', marginBottom: '1rem' }}>Video Auto-Labeling</h1>
      <p style={{ fontSize: '1.2rem', color: '#666', marginBottom: '3rem' }}>
        Automated video segmentation with visual boundary detection, transcription, and AI classification.
      </p>
      <div style={{ display: 'flex', gap: '1rem', justifyContent: 'center' }}>
        <button
          onClick={() => navigate('/videos')}
          style={{
            padding: '1rem 2rem',
            fontSize: '1rem',
            background: '#3f3b3bff',
            color: '#fff',
            border: 'none',
            borderRadius: '8px',
            cursor: 'pointer',
          }}
        >
          Browse Videos
        </button>
        <button
          onClick={() => navigate('/results')}
          style={{
            padding: '1rem 2rem',
            fontSize: '1rem',
            background: '#3f3b3bff',
            color: '#fff',
            border: 'none',
            borderRadius: '8px',
            cursor: 'pointer',
          }}
        >
          View Results
        </button>
      </div>
    </div>
  );
}
