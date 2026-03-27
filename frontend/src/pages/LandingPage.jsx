import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import '../styles/landing.css';

const FEATURES = [
  {
    title: 'Live',
    description: 'Global tracking.',
  },
  {
    title: 'Analysis',
    description: 'Atmospheric insight.',
  },
  {
    title: 'Clarity',
    description: 'Simple output.',
  },
];

const BALLOON_PREVIEW_TOTAL = 100;
const BALLOON_PREVIEW_ACTIVE = 10;

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
      <header className="apple-nav">
        <Link className="apple-wordmark" to="/">
          StratoSense
        </Link>

        <Link className="apple-nav-cta" to="/dashboard">
          Dashboard
        </Link>
      </header>

      <main>
        <section className="hero-wrap">
          <h1 className="hero-title">
            StratoSense
          </h1>
          <p className="hero-subtitle">
            Live weather-balloon intelligence.
          </p>

          <div className="hero-actions">
            <Link className="hero-primary" to="/dashboard">
              Open dashboard
            </Link>
            <a className="hero-secondary" href="https://sondehub.org">
              SondeHub
            </a>
          </div>

          <div className="hero-frame" aria-label="Platform overview">
            <div className="hero-frame-top">
              <div>
                <p className="frame-label">Deployed balloons</p>
                <h2>{activeBalloons}</h2>
              </div>
              <span className={`frame-status ${online ? 'online' : 'offline'}`}>
                {online ? 'Live' : 'Offline'}
              </span>
            </div>

            <div className="balloon-legend">
              <span className="balloon-legend-chip active">10 active</span>
              <span className="balloon-legend-chip">100 shown</span>
            </div>

            <div className="balloon-grid" aria-hidden="true">
              {Array.from({ length: BALLOON_PREVIEW_TOTAL }, (_, index) => (
                <span
                  key={index}
                  className={`balloon-icon ${
                    online && index < BALLOON_PREVIEW_ACTIVE ? 'active' : ''
                  }`}
                />
              ))}
            </div>
          </div>
        </section>

        <section className="feature-section">
          <div className="feature-grid">
            {FEATURES.map((feature) => (
              <article key={feature.title} className="feature-tile">
                <h3>{feature.title}</h3>
                <p>{feature.description}</p>
              </article>
            ))}
          </div>
        </section>
      </main>
    </div>
  );
}
