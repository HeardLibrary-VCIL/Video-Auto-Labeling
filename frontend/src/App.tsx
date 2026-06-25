import { BrowserRouter as Router, Route, Routes } from 'react-router-dom';
import { Authenticator } from '@aws-amplify/ui-react';
import '@aws-amplify/ui-react/styles.css';

import Navbar from './components/Navbar';
import Home from './pages/Home';
import VideoSelect from './pages/VideoSelect';
import Results from './pages/Results';

function App() {
  return (
    <Authenticator>
      {({ signOut }) => (
        <main>
          <Router>
            <Navbar onSignOut={signOut} />
            <div style={{ paddingTop: '60px' }}>
              <Routes>
                <Route path="/" element={<Home />} />
                <Route path="/videos" element={<VideoSelect />} />
                <Route path="/results" element={<Results />} />
              </Routes>
            </div>
          </Router>
        </main>
      )}
    </Authenticator>
  );
}

export default App;
