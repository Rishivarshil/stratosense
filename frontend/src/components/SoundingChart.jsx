import { useEffect, useRef, useState } from 'react';
import {
  Chart as ChartJS,
  LineController,
  LineElement,
  PointElement,
  LinearScale,
  Tooltip,
  Legend,
  Filler,
} from 'chart.js';
import annotationPlugin from 'chartjs-plugin-annotation';
import zoomPlugin from 'chartjs-plugin-zoom';
import { calcDewpoint } from '../utils/atmospheric';

ChartJS.register(
  LineController,
  LineElement,
  PointElement,
  LinearScale,
  Tooltip,
  Legend,
  Filler,
  annotationPlugin,
  zoomPlugin
);

const ANIMATION_INTERVAL_MS = 80;
const MAX_ANIMATION_FRAMES = 400;
const LIVE_POLL_MS = 2000;

function subsample(arr, maxLen) {
  if (arr.length <= maxLen) return arr;
  const step = arr.length / maxLen;
  const result = [];
  for (let i = 0; i < maxLen; i++) {
    result.push(arr[Math.floor(i * step)]);
  }
  if (result[result.length - 1] !== arr[arr.length - 1]) {
    result.push(arr[arr.length - 1]);
  }
  return result;
}

export default function SoundingChart({ serial }) {
  const canvasRef = useRef(null);
  const chartRef = useRef(null);
  const intervalRef = useRef(null);
  const pollRef = useRef(null);
  const knownDatetimesRef = useRef(new Set());
  const [status, setStatus] = useState('idle');
  const [error, setError] = useState(null);
  const [progress, setProgress] = useState({ current: 0, total: 0 });
  const [liveCount, setLiveCount] = useState(0);

  useEffect(() => {
    if (!serial) return;

    let cancelled = false;

    function cleanup() {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
      if (chartRef.current) {
        chartRef.current.destroy();
        chartRef.current = null;
      }
      knownDatetimesRef.current.clear();
    }

    async function load() {
      setStatus('loading');
      setError(null);
      setProgress({ current: 0, total: 0 });
      setLiveCount(0);

      cleanup();

      const existingChart = ChartJS.getChart(canvasRef.current);
      if (existingChart) existingChart.destroy();

      try {
        const [pathRes, analysisRes] = await Promise.all([
          fetch(`/balloon/${serial}`),
          fetch(`/balloon/${serial}/analysis`),
        ]);

        if (!pathRes.ok) throw new Error(`Path fetch failed: ${pathRes.status}`);
        if (cancelled) return;

        const pathData = await pathRes.json();
        const analysisData = analysisRes.ok ? await analysisRes.json() : null;

        const allFrames = pathData.path.filter(
          (f) => f.temp != null && f.alt != null
        );

        if (allFrames.length === 0) {
          throw new Error('No frames with valid temperature + altitude data');
        }

        const frames = subsample(allFrames, MAX_ANIMATION_FRAMES);

        allFrames.forEach((f) => {
          if (f.datetime) knownDatetimesRef.current.add(f.datetime);
        });

        const allTemps = [];
        const allDewpoints = [];
        allFrames.forEach((f) => {
          allTemps.push(f.temp);
          const dp = calcDewpoint(f.temp, f.humidity);
          if (dp != null) allDewpoints.push(dp);
        });
        const allTempValues = [...allTemps, ...allDewpoints];
        const xMin = Math.floor(Math.min(...allTempValues) - 5);
        const xMax = Math.ceil(Math.max(...allTempValues) + 5);
        const maxAlt = Math.max(...allFrames.map((f) => f.alt));
        const yMax = Math.ceil((maxAlt / 1000) * 1.1);

        if (cancelled) return;

        setProgress({ current: 0, total: frames.length });

        let tropopauseKm = analysisData?.tropopause_alt_km ?? null;

        const chart = new ChartJS(canvasRef.current, {
          type: 'line',
          data: {
            datasets: [
              {
                label: 'Temperature (°C)',
                data: [],
                borderColor: '#e8593c',
                backgroundColor: 'rgba(232, 89, 60, 0.05)',
                borderWidth: 2.5,
                pointRadius: 0,
                tension: 0.3,
                fill: false,
              },
              {
                label: 'Dewpoint (°C)',
                data: [],
                borderColor: '#3b8bd4',
                backgroundColor: 'rgba(59, 139, 212, 0.05)',
                borderWidth: 2,
                pointRadius: 0,
                tension: 0.3,
                borderDash: [4, 2],
                fill: false,
              },
            ],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            parsing: false,
            normalized: true,
            scales: {
              x: {
                type: 'linear',
                min: xMin,
                max: xMax,
                title: {
                  display: true,
                  text: 'Temperature (°C)',
                  color: '#444',
                  font: { size: 13 },
                },
                grid: { color: 'rgba(0,0,0,0.07)' },
                ticks: { color: '#666' },
              },
              y: {
                type: 'linear',
                min: 0,
                max: yMax,
                title: {
                  display: true,
                  text: 'Altitude (km)',
                  color: '#444',
                  font: { size: 13 },
                },
                grid: { color: 'rgba(0,0,0,0.07)' },
                ticks: { color: '#666' },
              },
            },
            plugins: {
              legend: {
                labels: { color: '#444', usePointStyle: true, pointStyle: 'line' },
              },
              tooltip: {
                callbacks: {
                  label: (ctx) => {
                    const label = ctx.dataset.label || '';
                    return `${label}: ${ctx.parsed.x.toFixed(1)}°C at ${ctx.parsed.y.toFixed(1)} km`;
                  },
                },
              },
              annotation: { annotations: {} },
              zoom: {
                pan: { enabled: true, mode: 'xy' },
                zoom: {
                  wheel: { enabled: true },
                  pinch: { enabled: true },
                  mode: 'xy',
                },
              },
            },
          },
        });

        chartRef.current = chart;
        setStatus('animating');

        let frameIndex = 0;

        intervalRef.current = setInterval(() => {
          if (cancelled || frameIndex >= frames.length) {
            clearInterval(intervalRef.current);
            intervalRef.current = null;

            if (!cancelled && tropopauseKm != null) {
              chart.options.plugins.annotation.annotations.tropopause = {
                type: 'line',
                yMin: tropopauseKm,
                yMax: tropopauseKm,
                borderColor: '#aa44aa',
                borderWidth: 1.5,
                borderDash: [6, 3],
                label: {
                  display: true,
                  content: `Tropopause ${tropopauseKm.toFixed(1)} km`,
                  color: '#7b2d8e',
                  backgroundColor: 'rgba(255, 255, 255, 0.85)',
                  font: { size: 11 },
                  position: 'start',
                },
              };
              chart.update('none');
            }

            if (!cancelled) {
              setStatus('live');
              startLivePolling(chart, serial, tropopauseKm);
            }
            return;
          }

          const f = frames[frameIndex];
          const altKm = f.alt / 1000;

          chart.data.datasets[0].data.push({ x: f.temp, y: altKm });

          const dp = calcDewpoint(f.temp, f.humidity);
          if (dp != null) {
            chart.data.datasets[1].data.push({ x: dp, y: altKm });
          }

          chart.update('none');
          frameIndex++;
          setProgress({ current: frameIndex, total: frames.length });
        }, ANIMATION_INTERVAL_MS);
      } catch (err) {
        if (!cancelled) {
          setError(err.message);
          setStatus('error');
        }
      }
    }

    function startLivePolling(chart, ser, currentTropopause) {
      let added = 0;

      pollRef.current = setInterval(async () => {
        if (cancelled) return;

        try {
          const [pathRes, analysisRes] = await Promise.all([
            fetch(`/balloon/${ser}`),
            fetch(`/balloon/${ser}/analysis`),
          ]);
          if (!pathRes.ok || cancelled) return;

          const pathData = await pathRes.json();
          const analysisData = analysisRes.ok ? await analysisRes.json() : null;

          const newFrames = pathData.path.filter(
            (f) =>
              f.temp != null &&
              f.alt != null &&
              f.datetime &&
              !knownDatetimesRef.current.has(f.datetime)
          );

          if (newFrames.length === 0) return;

          newFrames.forEach((f) => {
            knownDatetimesRef.current.add(f.datetime);
            const altKm = f.alt / 1000;

            chart.data.datasets[0].data.push({ x: f.temp, y: altKm });
            const dp = calcDewpoint(f.temp, f.humidity);
            if (dp != null) {
              chart.data.datasets[1].data.push({ x: dp, y: altKm });
            }

            const currentYMax = chart.options.scales.y.max;
            if (altKm > currentYMax * 0.9) {
              chart.options.scales.y.max = Math.ceil(altKm * 1.15);
            }

            const currentXMin = chart.options.scales.x.min;
            const currentXMax = chart.options.scales.x.max;
            if (f.temp < currentXMin + 3) {
              chart.options.scales.x.min = Math.floor(f.temp - 5);
            }
            if (f.temp > currentXMax - 3) {
              chart.options.scales.x.max = Math.ceil(f.temp + 5);
            }
          });

          const newTropo = analysisData?.tropopause_alt_km ?? null;
          if (newTropo != null && newTropo !== currentTropopause) {
            currentTropopause = newTropo;
            chart.options.plugins.annotation.annotations.tropopause = {
              type: 'line',
              yMin: newTropo,
              yMax: newTropo,
              borderColor: '#aa44aa',
              borderWidth: 1.5,
              borderDash: [6, 3],
              label: {
                display: true,
                content: `Tropopause ${newTropo.toFixed(1)} km`,
                color: '#7b2d8e',
                backgroundColor: 'rgba(255, 255, 255, 0.85)',
                font: { size: 11 },
                position: 'start',
              },
            };
          }

          chart.update('none');
          added += newFrames.length;
          setLiveCount(added);
        } catch {
          // network hiccup — keep polling
        }
      }, LIVE_POLL_MS);
    }

    load();

    return () => {
      cancelled = true;
      cleanup();
    };
  }, [serial]);

  const pct = progress.total > 0 ? Math.round((progress.current / progress.total) * 100) : 0;

  return (
    <div className="sounding-chart-container">
      <div className="chart-header">
        <h2>Sounding Chart</h2>
        {serial && <span className="serial-tag">{serial}</span>}
        {status === 'animating' && (
          <span className="progress-badge">
            {progress.current}/{progress.total} frames ({pct}%)
          </span>
        )}
        {status === 'live' && (
          <span className="live-badge">
            Live{liveCount > 0 ? ` — ${liveCount} new` : ''}
          </span>
        )}
        {status === 'done' && <span className="done-badge">Complete</span>}
      </div>

      {status === 'loading' && (
        <div className="chart-overlay">
          <div className="spinner" />
          <p>Fetching telemetry...</p>
        </div>
      )}

      {status === 'error' && (
        <div className="chart-overlay error">
          <p>Error: {error}</p>
        </div>
      )}

      {status === 'idle' && !serial && (
        <div className="chart-overlay">
          <p>Select a balloon to view its sounding profile</p>
        </div>
      )}

      <div className="chart-canvas-wrapper" style={{ opacity: status === 'loading' ? 0.3 : 1 }}>
        <canvas ref={canvasRef} />
      </div>
    </div>
  );
}
