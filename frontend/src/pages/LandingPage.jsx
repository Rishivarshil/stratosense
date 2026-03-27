import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import heroImage from '../assets/hero-stratosphere.png';
import '../styles/landing.css';

const FEATURES = [
  {
    icon: '◈',
    title: '3D Flight Profiles',
    description: 'Track balloon paths through altitude, position, and time in one view.',
  },
  {
    icon: '〰',
    title: 'Atmospheric Sounding',
    description: 'Read temperature, moisture, and vertical structure with less friction.',
  },
  {
    icon: '⊹',
    title: 'Wind Analysis',
    description: 'Surface shear, flow, and profile changes faster than a raw feed can.',
  },
  {
    icon: '◎',
    title: 'Decision Support',
    description: 'Move from telemetry to actionable atmospheric context in seconds.',
  },
];

export default function LandingPage() {
  const [serverStatus, setServerStatus] = useState(null);

  useEffect(() => {
    fetch('/status')
      .then((response) => response.json())
      .then(setServerStatus)
      .catch(() => setServerStatus({ status: 'offline' }));
  }, []);

  const online = serverStatus?.status === 'running';
  const activeBalloons =
    typeof serverStatus?.active_balloons === 'number'
      ? serverStatus.active_balloons.toLocaleString()
      : '--';

  return (
    <div className="landing-shell">
      <header className="landing-nav">
        <Link className="landing-wordmark" to="/">
          <span className="nav-cloud-outline" aria-hidden="true">
            <span className="nav-cloud-bump nav-cloud-bump-left" />
            <span className="nav-cloud-bump nav-cloud-bump-center" />
            <span className="nav-cloud-bump nav-cloud-bump-right" />
            <span className="nav-cloud-base" />
          </span>
          <span className="landing-wordmark-text">StratoSense</span>
        </Link>

        <div className="landing-nav-actions">
          <span className="landing-status">
            <span className={`status-dot ${online ? 'online' : 'offline'}`} />
            <span>{online ? 'Live data' : 'Offline'}</span>
          </span>
          <Link className="landing-nav-cta" to="/dashboard">
            Dashboard
          </Link>
        </div>
      </header>

      <main>
        <section className="hero-section">
          <div className="hero-backdrop" aria-hidden="true">
            <img
              src={heroImage}
              alt=""
              className="hero-image"
            />
            <div className="hero-image-overlay" />
            <div className="hero-glow hero-glow-primary" />
            <div className="hero-glow hero-glow-accent" />
            <div className="hero-grid" />
          </div>

          <div className="hero-content">
            <p className="hero-kicker">Atmospheric Intelligence Platform</p>

            <div className="hero-title-wrap">
              <div className="cloud-outline" aria-hidden="true">
                <span className="cloud-bump cloud-bump-left" />
                <span className="cloud-bump cloud-bump-center" />
                <span className="cloud-bump cloud-bump-right" />
                <span className="cloud-base" />
              </div>
              <h1 className="hero-title">StratoSense</h1>
            </div>

            <p className="hero-subtitle">
              Live weather-balloon tracking, atmospheric sounding, and flight analysis in one instrument panel.
            </p>

            <div className="hero-actions">
              <Link className="hero-primary" to="/dashboard">
                Open Dashboard
              </Link>
              <a
                className="hero-secondary"
                href="https://sondehub.org"
                target="_blank"
                rel="noopener noreferrer"
              >
                SondeHub
              </a>
            </div>

            <div className="stats-strip">
              <div className="stat-item">
                <p>Active Balloons</p>
                <strong>{activeBalloons}</strong>
              </div>
              <div className="stat-item">
                <p>Coverage</p>
                <strong>Global</strong>
              </div>
              <div className="stat-item">
                <p>View</p>
                <strong>Realtime</strong>
              </div>
            </div>
          </div>
        </section>

        <section className="feature-section">
          <div className="section-heading">
            <p className="hero-kicker">Capabilities</p>
            <h2>Full-spectrum analysis</h2>
          </div>

          <div className="feature-grid">
            {FEATURES.map((feature) => (
              <article key={feature.title} className="feature-tile">
                <span className="feature-icon">{feature.icon}</span>
                <h3>{feature.title}</h3>
                <p>{feature.description}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="cta-section">
          <div className="cta-panel">
            <h2>Ready to explore the atmosphere?</h2>
            <p>Open the dashboard and inspect live radiosonde flights in real time.</p>
            <Link className="hero-primary" to="/dashboard">
              Launch Dashboard
            </Link>
          </div>
        </section>
      </main>
    </div>
  );
}
