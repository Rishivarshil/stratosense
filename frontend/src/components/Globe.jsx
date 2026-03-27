import { useEffect, useRef, useState } from 'react';

const LEAFLET_CSS = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
const LEAFLET_JS  = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
const BALLOON_POLL_MS = 30000;
const STATION_LIMIT = 300;
const MIN_STATION_RADIUS_KM = 50;
const MAX_STATION_RADIUS_KM = 2500;
const STATION_FETCH_DEBOUNCE_MS = 320;

/** Half diagonal of visible bounds in km, clamped — matches Synoptic radius search. */
function getViewportStationParams(map) {
  const center = map.getCenter();
  const b = map.getBounds();
  const sw = b.getSouthWest();
  const ne = b.getNorthEast();
  const halfDiagM = sw.distanceTo(ne) / 2;
  const radiusKm = Math.min(
    MAX_STATION_RADIUS_KM,
    Math.max(MIN_STATION_RADIUS_KM, halfDiagM / 1000)
  );
  return { lat: center.lat, long: center.lng, radius: radiusKm };
}

function loadLeaflet() {
  return new Promise((resolve, reject) => {
    if (window.L) { resolve(); return; }

    // CSS
    if (!document.getElementById('leaflet-css')) {
      const link = document.createElement('link');
      link.id  = 'leaflet-css';
      link.rel = 'stylesheet';
      link.href = LEAFLET_CSS;
      document.head.appendChild(link);
    }

    // JS
    if (document.getElementById('leaflet-js')) {
      document.getElementById('leaflet-js').addEventListener('load', resolve);
      return;
    }
    const s = document.createElement('script');
    s.id  = 'leaflet-js';
    s.src = LEAFLET_JS;
    s.onload  = resolve;
    s.onerror = () => reject(new Error('Failed to load Leaflet'));
    document.head.appendChild(s);
  });
}

export default function Globe({ selectedSerial, scrubFrame }) {
  const mapRef         = useRef(null);   // Leaflet map instance
  const markersRef     = useRef({});     // serial → L.Marker
  const stationMarkersRef = useRef({});  // stid -> L.Marker
  const pathRef        = useRef(null);   // L.Polyline for flight trail
  const scrubMarkerRef = useRef(null);   // ghost scrub marker
  const pollRef        = useRef(null);
  const stationDebounceRef = useRef(null);
  const selectedSerialRef = useRef(selectedSerial);

  const [mapReady,    setMapReady]    = useState(false);
  const [balloonCount, setBalloonCount] = useState(0);
  const [stationCount, setStationCount] = useState(0);
  const [loadError,   setLoadError]   = useState(null);
  const [fetchError,  setFetchError]  = useState(null);

  useEffect(() => { selectedSerialRef.current = selectedSerial; }, [selectedSerial]);

  // ── Path trail ────────────────────────────────────────────────
  useEffect(() => {
    if (!mapReady || !selectedSerial) {
      if (pathRef.current) { pathRef.current.remove(); pathRef.current = null; }
      return;
    }
    loadPathTrail(selectedSerial);
  }, [selectedSerial, mapReady]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Scrub ghost marker ────────────────────────────────────────
  useEffect(() => {
    if (!mapReady || !window.L) return;
    if (scrubMarkerRef.current) { scrubMarkerRef.current.remove(); scrubMarkerRef.current = null; }
    if (!scrubFrame?.lat || !scrubFrame?.lon) return;

    scrubMarkerRef.current = window.L.circleMarker([scrubFrame.lat, scrubFrame.lon], {
      radius: 6,
      color: '#e8593c',
      weight: 2,
      fillColor: '#fff',
      fillOpacity: 1,
    }).addTo(mapRef.current);
  }, [scrubFrame, mapReady]);

  // ── Fetch & place balloon markers ─────────────────────────────
  async function fetchAndPlaceBalloons() {
    const map = mapRef.current;
    if (!map || !window.L) return;

    try {
      const res = await fetch('/balloons');
      if (!res.ok) throw new Error(`/balloons returned ${res.status}`);
      const data = await res.json();
      const balloons = (data.balloons || []).filter(b => b.lat != null && b.lon != null);

      const incoming = new Set(balloons.map(b => b.serial));

      // Remove stale markers
      for (const [serial, marker] of Object.entries(markersRef.current)) {
        if (!incoming.has(serial)) { marker.remove(); delete markersRef.current[serial]; }
      }

      // Add new markers
      balloons.forEach(b => {
        if (markersRef.current[b.serial]) return;

        const marker = window.L.circleMarker([b.lat, b.lon], {
          radius: 8,
          color: '#fff',
          weight: 2,
          fillColor: '#4a6cf7',
          fillOpacity: 1,
        });

        marker.bindPopup(
          `<b style="font-family:sans-serif">${b.serial}</b>` +
          `<br/>Alt: ${b.alt != null ? Math.round(b.alt) + ' m' : '?'}` +
          `<br/>Temp: ${b.temp != null ? b.temp + ' °C' : '?'}`
        );
        marker.on('click', () => {
          document.dispatchEvent(
            new CustomEvent('balloonSelected', { detail: { serial: b.serial } })
          );
        });
        marker.addTo(map);
        markersRef.current[b.serial] = marker;
      });

      setBalloonCount(balloons.length);
      setFetchError(null);
    } catch (err) {
      setFetchError(err.message);
    }
  }

  async function fetchAndPlaceStations() {
    const map = mapRef.current;
    if (!map || !window.L) return;

    try {
      const { lat, long, radius } = getViewportStationParams(map);
      const qs = new URLSearchParams({
        lat: String(lat),
        long: String(long),
        radius: String(Math.round(radius * 100) / 100),
        limit: String(STATION_LIMIT),
      });
      const res = await fetch(`/weather/stations/search?${qs}`);
      if (!res.ok) throw new Error(`/weather/stations/search returned ${res.status}`);
      const data = await res.json();
      const stations = (data.stations || []).filter(
        (s) => s.LATITUDE != null && s.LONGITUDE != null && s.STID
      );

      const incoming = new Set(stations.map((s) => s.STID));

      for (const [stid, marker] of Object.entries(stationMarkersRef.current)) {
        if (!incoming.has(stid)) {
          marker.remove();
          delete stationMarkersRef.current[stid];
        }
      }

      stations.forEach((s) => {
        if (stationMarkersRef.current[s.STID]) return;

        const marker = window.L.circleMarker([s.LATITUDE, s.LONGITUDE], {
          radius: 5,
          color: '#fff',
          weight: 1.5,
          fillColor: '#2f9e44',
          fillOpacity: 0.9,
        });

        marker.bindPopup(
          `<b style="font-family:sans-serif">${s.STID}</b>` +
          `<br/>${s.NAME || 'Ground station'}` +
          `<br/>State: ${s.STATE || '?'}` +
          `<br/>Elev: ${s.ELEVATION != null ? Math.round(s.ELEVATION) + ' m' : '?'}` +
          ``
        );
        marker.on('click', () => {
          document.dispatchEvent(
            new CustomEvent('stationSelected', { detail: { stid: s.STID } })
          );
        });
        marker.addTo(map);
        stationMarkersRef.current[s.STID] = marker;
      });

      setStationCount(stations.length);
      setFetchError(null);
    } catch (err) {
      setFetchError(err.message);
    }
  }

  async function loadPathTrail(serial) {
    const map = mapRef.current;
    if (!map || !window.L) return;

    if (pathRef.current) { pathRef.current.remove(); pathRef.current = null; }

    try {
      const res = await fetch(`/balloon/${serial}`);
      if (!res.ok) return;
      const data = await res.json();

      const coords = (data.path || [])
        .filter(f => f.lat != null && f.lon != null)
        .map(f => [f.lat, f.lon]);

      if (coords.length >= 2) {
        pathRef.current = window.L.polyline(coords, {
          color: '#e8593c',
          opacity: 0.85,
          weight: 2,
        }).addTo(map);

        map.flyTo(coords[coords.length - 1], 6, { duration: 1.2 });
      }
    } catch { /* non-critical */ }
  }

  // ── Initialize map ────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    let onViewportChange = null;

    async function init() {
      try {
        await loadLeaflet();
        if (cancelled || mapRef.current) return;

        const worldBounds = window.L.latLngBounds(
          window.L.latLng(-90, -180),
          window.L.latLng(90, 180)
        );

        const map = window.L.map('stratosense-map', {
          center: [20, 0],
          zoom: 2,
          zoomControl: true,
          scrollWheelZoom: true,
          maxBounds: worldBounds,
          maxBoundsViscosity: 1.0,
          minZoom: 2,
        });

        window.L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
          attribution: '© OpenStreetMap contributors',
          maxZoom: 19,
          noWrap: true,
          bounds: worldBounds,
        }).addTo(map);

        mapRef.current = map;

        function scheduleStationsFetch() {
          if (stationDebounceRef.current) clearTimeout(stationDebounceRef.current);
          stationDebounceRef.current = setTimeout(() => {
            stationDebounceRef.current = null;
            fetchAndPlaceStations();
          }, STATION_FETCH_DEBOUNCE_MS);
        }

        onViewportChange = () => {
          scheduleStationsFetch();
        };

        map.on('moveend', onViewportChange);
        map.on('zoomend', onViewportChange);

        if (!cancelled) {
          setMapReady(true);
          await fetchAndPlaceBalloons();
          scheduleStationsFetch();
          pollRef.current = setInterval(() => {
            fetchAndPlaceBalloons();
            fetchAndPlaceStations();
          }, BALLOON_POLL_MS);
        }
      } catch (err) {
        if (!cancelled) setLoadError(err.message || 'Failed to load map');
      }
    }

    init();

    return () => {
      cancelled = true;
      if (stationDebounceRef.current) {
        clearTimeout(stationDebounceRef.current);
        stationDebounceRef.current = null;
      }
      const m = mapRef.current;
      if (m && onViewportChange) {
        m.off('moveend', onViewportChange);
        m.off('zoomend', onViewportChange);
      }
      clearInterval(pollRef.current);
      pollRef.current = null;
      Object.values(markersRef.current).forEach(m => m.remove());
      markersRef.current = {};
      Object.values(stationMarkersRef.current).forEach(m => m.remove());
      stationMarkersRef.current = {};
      if (pathRef.current)     { pathRef.current.remove();     pathRef.current = null; }
      if (scrubMarkerRef.current) { scrubMarkerRef.current.remove(); scrubMarkerRef.current = null; }
      if (mapRef.current) { mapRef.current.remove(); mapRef.current = null; }
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="globe-container">
      <div className="chart-header">
        <h2>Live Balloon Tracker</h2>
        {mapReady && (
          <span className="serial-tag" title="Ground stations match the visible map area">
            {balloonCount} balloons · {stationCount} stations (near view)
          </span>
        )}
        {mapReady && !fetchError && <span className="live-badge">Live</span>}
        {fetchError && (
          <span style={{ fontSize: '0.75rem', color: '#ff6666', marginLeft: 'auto' }}>
            {fetchError}
          </span>
        )}
      </div>

      {!mapReady && !loadError && (
        <div className="globe-loading">
          <div className="spinner" />
          <p>Loading map...</p>
        </div>
      )}

      {loadError && (
        <div className="chart-overlay error">
          <p>{loadError}</p>
        </div>
      )}

      <div
        id="stratosense-map"
        className="globe-map"
        style={{ opacity: mapReady ? 1 : 0 }}
      />

      {mapReady && (
        <div className="globe-hint">
          Scroll to zoom · drag to pan · stations update for the current view
        </div>
      )}
    </div>
  );
}
