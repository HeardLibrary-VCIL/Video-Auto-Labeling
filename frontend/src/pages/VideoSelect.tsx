import { useEffect, useState } from 'react';
import { list, getUrl } from 'aws-amplify/storage';
import { useNavigate } from 'react-router-dom';

interface VideoItem {
  key: string;
  url?: string;
}

export default function VideoSelect() {
  const navigate = useNavigate();
  const [videos, setVideos] = useState<VideoItem[]>([]);
  const [selectedVideo, setSelectedVideo] = useState<VideoItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');

  useEffect(() => {
    const fetchVideos = async () => {
      try {
        setLoading(true);
        const result = await list({ path: 'video/' });
        const videoFiles = result.items.filter(
          (item) => item.path && /\.(mp4|mov|avi|mkv|webm)$/i.test(item.path)
        );
        setVideos(videoFiles.map((file) => ({ key: file.path! })));
      } catch (err) {
        console.error('Error fetching videos:', err);
        setError('Failed to load videos');
      } finally {
        setLoading(false);
      }
    };
    fetchVideos();
  }, []);

  const filteredVideos = videos.filter(video =>
    video.key.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const getFileName = (key: string): string => {
    return key.replace('video/', '').split('/').pop() || key;
  };

  const handleVideoSelect = async (video: VideoItem) => {
    try {
      const result = await getUrl({ path: video.key });
      setSelectedVideo({ ...video, url: result.url.href });
    } catch (err) {
      console.error('Error getting video URL:', err);
      setError('Failed to load video URL');
    }
  };

  const handleProceedToResults = () => {
    if (selectedVideo?.url) {
      navigate('/results', {
        state: {
          videoUrl: selectedVideo.url,
          videoKey: selectedVideo.key,
          videoFileName: getFileName(selectedVideo.key),
        },
      });
    }
  };

  if (loading) return <div style={{ padding: '2rem', textAlign: 'center' }}>Loading videos...</div>;
  if (error) return <div style={{ padding: '2rem', textAlign: 'center', color: 'red' }}>{error}</div>;

  return (
    <div style={{ maxWidth: '900px', margin: '2rem auto', padding: '0 2rem' }}>
      <h2 style={{ marginBottom: '1.5rem' }}>Select a Video</h2>

      {!selectedVideo ? (
        <>
          <input
            type="text"
            placeholder="Search videos..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            style={{
              width: '100%', padding: '0.75rem', marginBottom: '1rem',
              border: '1px solid #ddd', borderRadius: '6px', fontSize: '1rem',
            }}
          />
          <p style={{ color: '#666', marginBottom: '1rem' }}>
            {filteredVideos.length} video{filteredVideos.length !== 1 ? 's' : ''} available
          </p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            {filteredVideos.map((video) => (
              <div key={video.key} style={{
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                padding: '0.75rem 1rem', background: '#fff', borderRadius: '6px',
                border: '1px solid #eee',
              }}>
                <span>{getFileName(video.key)}</span>
                <button
                  onClick={() => handleVideoSelect(video)}
                  style={{
                    padding: '0.4rem 1rem', background: '#4299e1', color: '#fff',
                    border: 'none', borderRadius: '4px', cursor: 'pointer',
                  }}
                >
                  Select
                </button>
              </div>
            ))}
          </div>
        </>
      ) : (
        <div>
          <h3 style={{ marginBottom: '1rem' }}>Preview: {getFileName(selectedVideo.key)}</h3>
          <video controls style={{ width: '100%', maxHeight: '400px', borderRadius: '8px' }}>
            <source src={selectedVideo.url} type="video/mp4" />
          </video>
          <div style={{ display: 'flex', gap: '1rem', marginTop: '1rem' }}>
            <button onClick={() => setSelectedVideo(null)} style={{
              padding: '0.75rem 1.5rem', background: '#e2e8f0', border: 'none',
              borderRadius: '6px', cursor: 'pointer',
            }}>
              Choose Different Video
            </button>
            <button onClick={handleProceedToResults} style={{
              padding: '0.75rem 1.5rem', background: '#48bb78', color: '#fff',
              border: 'none', borderRadius: '6px', cursor: 'pointer',
            }}>
              View Segmentation Results →
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
