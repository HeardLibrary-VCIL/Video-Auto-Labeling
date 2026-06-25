import { useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import './Navbar.css';

interface NavbarProps {
  onSignOut?: () => void;
}

export default function Navbar({ onSignOut }: NavbarProps) {
  const [isOpen, setIsOpen] = useState(false);
  const location = useLocation();

  const toggleMenu = () => setIsOpen(!isOpen);
  const handleLinkClick = () => setIsOpen(false);
  const isCurrentPage = (path: string) => location.pathname === path;

  return (
    <nav className="navbar">
      <Link to="/" onClick={handleLinkClick}>
        <img src="/favicon.png" alt="VCIL Logo" className="navbar-logo" />
      </Link>

      <div className="menu-toggle" onClick={toggleMenu}>
        <div className={`hamburger ${isOpen ? 'active' : ''}`}></div>
      </div>

      <ul className={`nav-links ${isOpen ? 'active' : ''}`}>
        <li>
          <Link
            to="/"
            className={isCurrentPage('/') ? 'current-page' : ''}
            onClick={handleLinkClick}
          >
            Home
          </Link>
        </li>
        <li>
          <Link
            to="/videos"
            className={isCurrentPage('/videos') ? 'current-page' : ''}
            onClick={handleLinkClick}
          >
            Videos
          </Link>
        </li>
        <li>
          <Link
            to="/results"
            className={isCurrentPage('/results') ? 'current-page' : ''}
            onClick={handleLinkClick}
          >
            Results
          </Link>
        </li>
      </ul>

      {onSignOut && (
        <button className="sign-out-button" onClick={onSignOut}>
          Sign Out
        </button>
      )}
    </nav>
  );
}
