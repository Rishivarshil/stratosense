import { useEffect, useRef, useMemo, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

// ── Atmospheric layer definitions ────────────────────────────────────────────
const LAYERS = [
  { name: 'Stratosphere',      minKm: 12, maxKm: 35, color: 0x5b1a7a, streamColor: 0xcc88ff, label: '#cc88ff' },
  { name: 'Upper Troposphere', minKm: 7,  maxKm: 12, color: 0x1a2a7e, streamColor: 0x7eb3e8, label: '#7eb3e8' },
  { name: 'Mid Troposphere',   minKm: 3,  maxKm: 7,  color: 0x1a3a6e, streamColor: 0x5ea8d8, label: '#5ea8d8' },
  { name: 'Lower Troposphere', minKm: 1,  maxKm: 3,  color: 0x1a5a4e, streamColor: 0x5ed8a8, label: '#5ed8a8' },
  { name: 'Surface',           minKm: 0,  maxKm: 1,  color: 0x1a4a2e, streamColor: 0x5ecf9a, label: '#5ecf9a' },
];

const MAX_KM  = 35;
const EARTH_R = 4.5;
const ATM_S   = 0.16;

// ── Component ────────────────────────────────────────────────────────────────
export default function AltitudeColumn({ frames, scrubIndex, analysis }) {
  const mountRef         = useRef(null);
  const threeRef         = useRef(null);
  const balloonPosRef    = useRef({ lat: 45, lon: -85, altKm: 1 });
  const streamDataRef    = useRef([]);
  const labelContainerRef = useRef(null);

  const validFrames = useMemo(
    () => (frames || []).filter(f => f.alt != null && f.temp != null).sort((a, b) => a.alt - b.alt),
    [frames]
  );
  const scrubFrame = frames && scrubIndex != null ? frames[scrubIndex] : null;
  const currentAlt   = scrubFrame?.alt;
  const currentTemp  = scrubFrame?.temp;
  const currentHumid = scrubFrame?.humidity;
  const currentVelV  = scrubFrame?.vel_v;

  const activeLayer = currentAlt != null
    ? LAYERS.find(l => currentAlt / 1000 >= l.minKm && currentAlt / 1000 < l.maxKm) ?? LAYERS[4]
    : null;

  const [selectedEffect, setSelectedEffect] = useState(null);

  // ── Per-effect data builder ───────────────────────────────────────────────
  function buildEffectData(label) {
    const na = '—';
    switch (label) {
      case 'Precipitation': {
        const rh = currentHumid;
        const pw = analysis?.precipitable_water_mm;
        const dewpoint = (currentTemp != null && rh != null)
          ? (currentTemp - ((100 - rh) / 5)).toFixed(1)
          : null;
        const likely = rh != null
          ? rh > 80 ? 'Likely — High RH' : rh > 60 ? 'Possible' : 'Unlikely'
          : na;
        return {
          color: '#5ea8d8',
          rows: [
            { key: 'Relative Humidity', val: rh != null ? `${Math.round(rh)}%` : na },
            { key: 'Precipitable Water', val: pw != null ? `${pw} mm` : na },
            { key: 'Dewpoint Est.', val: dewpoint != null ? `${dewpoint}°C` : na },
            { key: 'Precipitation', val: likely },
            { key: 'Current Temp', val: currentTemp != null ? `${currentTemp.toFixed(1)}°C` : na },
          ],
        };
      }
      case 'Ice Crystals': {
        const freezingLevel = validFrames.find(
          (f, i) => i > 0 && f.temp <= 0 && validFrames[i - 1].temp > 0
        );
        const inIceZone = currentTemp != null && currentTemp < 0;
        return {
          color: '#a8d8ff',
          rows: [
            { key: 'Current Temp', val: currentTemp != null ? `${currentTemp.toFixed(1)}°C` : na },
            { key: 'Current Alt', val: currentAlt != null ? `${(currentAlt / 1000).toFixed(1)} km` : na },
            { key: 'Freezing Level', val: freezingLevel ? `${(freezingLevel.alt / 1000).toFixed(1)} km` : 'Not detected' },
            { key: 'Ice Crystal Zone', val: inIceZone ? 'Yes — below 0 °C' : currentTemp != null ? 'No — above 0 °C' : na },
            { key: 'Humidity', val: currentHumid != null ? `${Math.round(currentHumid)}%` : na },
          ],
        };
      }
      case 'Wind Streams': {
        const profile = analysis?.wind_profile ?? [];
        const nearest = profile.length > 0 && currentAlt != null
          ? profile.reduce((best, w) => Math.abs(w.alt - currentAlt) < Math.abs(best.alt - currentAlt) ? w : best)
          : null;
        const layerWinds = profile.filter(w => {
          const km = w.alt / 1000;
          return activeLayer ? km >= activeLayer.minKm && km < activeLayer.maxKm : false;
        });
        const avgSpeed = layerWinds.length > 0
          ? (layerWinds.reduce((s, w) => s + w.speed_ms, 0) / layerWinds.length).toFixed(1)
          : null;
        return {
          color: '#7eb3e8',
          rows: [
            { key: 'Ascent Rate', val: currentVelV != null ? `${currentVelV.toFixed(1)} m/s` : na },
            { key: 'Nearest Wind Speed', val: nearest ? `${nearest.speed_ms} m/s (${nearest.speed_knots} kt)` : na },
            { key: 'Wind Direction', val: nearest ? `${nearest.direction_deg}°` : na },
            { key: `Avg in ${activeLayer?.name ?? 'Layer'}`, val: avgSpeed != null ? `${avgSpeed} m/s` : na },
            { key: 'Wind Samples', val: `${profile.length} pts` },
          ],
        };
      }
      case 'Lightning': {
        const cape = analysis?.cape;
        const cin = analysis?.cin;
        const risk = analysis?.storm_risk;
        const riskColor = cape == null ? '#7eb3e8'
          : cape < 300 ? '#5ecf9a'
          : cape < 1000 ? '#ffe066'
          : cape < 2500 ? '#ff8844'
          : '#ff4444';
        return {
          color: '#ffe066',
          rows: [
            { key: 'CAPE', val: cape != null ? `${Math.round(cape)} J/kg` : na, color: riskColor },
            { key: 'CIN', val: cin != null ? `${cin.toFixed(1)} J/kg` : na },
            { key: 'Storm Risk', val: risk ?? na, color: riskColor },
            { key: 'Lapse Rate', val: analysis?.lapse_rate_c_per_km != null ? `${analysis.lapse_rate_c_per_km} °C/km` : na },
            { key: 'Tropopause', val: analysis?.tropopause_alt_km != null ? `${analysis.tropopause_alt_km} km` : na },
          ],
        };
      }
      case 'Radiosonde': {
        return {
          color: '#ff4444',
          rows: [
            { key: 'Altitude', val: currentAlt != null ? `${(currentAlt / 1000).toFixed(2)} km` : na },
            { key: 'Temperature', val: currentTemp != null ? `${currentTemp.toFixed(1)}°C` : na },
            { key: 'Humidity', val: currentHumid != null ? `${Math.round(currentHumid)}%` : na },
            { key: 'Ascent Rate', val: currentVelV != null ? `${currentVelV.toFixed(1)} m/s` : na },
            { key: 'Layer', val: activeLayer?.name ?? na },
            { key: 'Frame', val: scrubFrame?.datetime ? new Date(scrubFrame.datetime).toUTCString().slice(0, 25) : na },
          ],
        };
      }
      case 'Aurora': {
        const tropo = analysis?.tropopause_alt_km;
        const lapse = analysis?.lapse_rate_c_per_km;
        const inStrato = activeLayer?.name === 'Stratosphere';
        return {
          color: '#cc88ff',
          rows: [
            { key: 'Tropopause Alt', val: tropo != null ? `${tropo} km` : na },
            { key: 'Lapse Rate', val: lapse != null ? `${lapse} °C/km` : na },
            { key: 'In Stratosphere', val: inStrato ? 'Yes' : currentAlt != null ? 'No' : na },
            { key: 'Current Layer', val: activeLayer?.name ?? na },
            { key: 'Stratosphere Starts', val: tropo != null ? `~${tropo} km` : '~12 km (est.)' },
          ],
        };
      }
      default: return null;
    }
  }

  const effectData = selectedEffect ? buildEffectData(selectedEffect) : null;

  // ── One-time Three.js scene init ──────────────────────────────────────────
  useEffect(() => {
    let initRafId;
    let animRafId;
    let cleanupFn = () => {};

    initRafId = requestAnimationFrame(() => {
      const mount = mountRef.current;
      if (!mount) return;

      const W = mount.clientWidth || 500;
      const H = mount.clientHeight || 600;

      // Renderer
      const renderer = new THREE.WebGLRenderer({ antialias: true });
      renderer.setPixelRatio(window.devicePixelRatio);
      renderer.setSize(W, H);
      renderer.setClearColor(0x020810);
      mount.appendChild(renderer.domElement);

      // Scene (no fog — globe looks better without it)
      const scene = new THREE.Scene();

      // Camera — angled to see the hemisphere from above and front
      const camera = new THREE.PerspectiveCamera(45, W / H, 0.01, 300);
      camera.position.set(0, 8, 20);
      camera.lookAt(0, 2, 0);

      // Controls — orbit the hemisphere, locked above ground
      const controls = new OrbitControls(camera, renderer.domElement);
      controls.target.set(0, 2, 0);
      controls.autoRotate = true;
      controls.autoRotateSpeed = 0.3;
      controls.enableDamping = true;
      controls.dampingFactor = 0.06;
      controls.minDistance = 1;
      controls.maxDistance = 50;
      controls.maxPolarAngle = Math.PI / 2.05; // prevent going below ground
      controls.update();

      // ── Stars — only above ground plane ────────────────────────────────────
      const starPos = new Float32Array(7000 * 3);
      for (let i = 0; i < 7000; i++) {
        starPos[i * 3]     = (Math.random() - 0.5) * 200;
        starPos[i * 3 + 1] = Math.random() * 100 + 2;
        starPos[i * 3 + 2] = (Math.random() - 0.5) * 200;
      }
      const starGeo = new THREE.BufferGeometry();
      starGeo.setAttribute('position', new THREE.BufferAttribute(starPos, 3));
      scene.add(new THREE.Points(starGeo,
        new THREE.PointsMaterial({ color: 0xffffff, size: 0.12, transparent: true, opacity: 0.7 })));

      // ── Atmospheric particles — top hemisphere only ────────────────────────
      const partCount = 800;
      const partPos = new Float32Array(partCount * 3);
      for (let i = 0; i < partCount; i++) {
        const layer = LAYERS[i % LAYERS.length];
        const r = EARTH_R + (layer.minKm + Math.random() * (layer.maxKm - layer.minKm)) * ATM_S;
        const phi   = Math.random() * Math.PI / 2; // top hemisphere only
        const theta = Math.random() * Math.PI * 2;
        partPos[i * 3]     = r * Math.sin(phi) * Math.cos(theta);
        partPos[i * 3 + 1] = r * Math.cos(phi);
        partPos[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta);
      }
      const partGeo = new THREE.BufferGeometry();
      partGeo.setAttribute('position', new THREE.BufferAttribute(partPos, 3));
      scene.add(new THREE.Points(partGeo,
        new THREE.PointsMaterial({ color: 0x7eb3e8, size: 0.055, transparent: true, opacity: 0.3 })));


      // Flat OSM map disc at ground level
      const mapCanvas = document.createElement('canvas');
      mapCanvas.width = 768; mapCanvas.height = 768;
      const mapCtx = mapCanvas.getContext('2d');
      mapCtx.fillStyle = '#0d1a0d';
      mapCtx.fillRect(0, 0, 768, 768);
      const mapTex = new THREE.CanvasTexture(mapCanvas);
      const mapMesh = new THREE.Mesh(
        new THREE.CircleGeometry(EARTH_R, 72),
        new THREE.MeshBasicMaterial({ map: mapTex, side: THREE.DoubleSide }),
      );
      mapMesh.rotation.x = -Math.PI / 2;
      mapMesh.position.y = 0.01;
      scene.add(mapMesh);

      // Large floor plane for ambience
      const floorMesh = new THREE.Mesh(
        new THREE.CircleGeometry(35, 64),
        new THREE.MeshBasicMaterial({ color: 0x040d04 }),
      );
      floorMesh.rotation.x = -Math.PI / 2;
      floorMesh.position.y = -0.02;
      scene.add(floorMesh);

      // Glowing rim ring at the base
      const rimMesh = new THREE.Mesh(
        new THREE.RingGeometry(EARTH_R - 0.04, EARTH_R + 0.2, 72),
        new THREE.MeshBasicMaterial({ color: 0x3aaa6a, transparent: true, opacity: 0.6, side: THREE.DoubleSide }),
      );
      rimMesh.rotation.x = -Math.PI / 2;
      rimMesh.position.y = 0.01;
      scene.add(rimMesh);

      // Surface atmosphere glow — hemisphere only
      scene.add(new THREE.Mesh(
        new THREE.SphereGeometry(EARTH_R + 0.05, 64, 32, 0, Math.PI * 2, 0, Math.PI / 2),
        new THREE.MeshBasicMaterial({ color: 0x1a4a2e, transparent: true, opacity: 0.18, depthWrite: false }),
      ));

      threeRef.current = { ...threeRef.current, mapMesh, mapTex };

      // ── Concentric atmospheric shells — top hemisphere only ────────────────
      LAYERS.forEach(layer => {
        const outerR = EARTH_R + layer.maxKm * ATM_S;
        // Transparent colour fill — hemisphere
        scene.add(new THREE.Mesh(
          new THREE.SphereGeometry(outerR, 48, 24, 0, Math.PI * 2, 0, Math.PI / 2),
          new THREE.MeshBasicMaterial({ color: layer.color, transparent: true, opacity: 0.07, depthWrite: false }),
        ));
        // Lat/lon wireframe at boundary — hemisphere
        scene.add(new THREE.Mesh(
          new THREE.SphereGeometry(outerR + 0.01, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2),
          new THREE.MeshBasicMaterial({ color: layer.streamColor, transparent: true, opacity: 0.1, wireframe: true, depthWrite: false }),
        ));
      });

      // ── Wind streamlines — animated latitude circles per layer ─────────────
      const streamData = [];
      LAYERS.forEach(layer => {
        const midR = EARTH_R + (layer.minKm + layer.maxKm) / 2 * ATM_S;
        for (let i = 0; i < 4; i++) {
          const basePhi = 0.1 + (i / 3) * (Math.PI / 2 - 0.15);
          const phase   = Math.random() * Math.PI * 2;
          const amp     = 0.04 + Math.random() * 0.06;
          const waveN   = 3 + Math.floor(Math.random() * 4);
          const STEPS   = 180;
          const pts = [];
          for (let j = 0; j <= STEPS; j++) {
            const theta = (j / STEPS) * Math.PI * 2;
            const phi = basePhi + amp * Math.sin(waveN * theta + phase);
            pts.push(new THREE.Vector3(
              midR * Math.sin(phi) * Math.cos(theta),
              midR * Math.cos(phi),
              midR * Math.sin(phi) * Math.sin(theta),
            ));
          }
          const line = new THREE.Line(
            new THREE.BufferGeometry().setFromPoints(pts),
            new THREE.LineBasicMaterial({ color: layer.streamColor, transparent: true, opacity: 0.5 }),
          );
          scene.add(line);
          streamData.push({ line, basePhi, midR, phase, amp, waveN, STEPS });
        }
      });
      streamDataRef.current = streamData;

      // ── Balloon marker ─────────────────────────────────────────────────────
      const balloonGroup = new THREE.Group();
      scene.add(balloonGroup);
      balloonGroup.add(new THREE.Mesh(
        new THREE.SphereGeometry(0.14, 20, 20),
        new THREE.MeshBasicMaterial({ color: 0xff3333 }),
      ));
      balloonGroup.add(new THREE.PointLight(0xff4444, 3, 5));
      const haloMesh = new THREE.Mesh(
        new THREE.RingGeometry(0.18, 0.25, 32),
        new THREE.MeshBasicMaterial({ color: 0xff6666, transparent: true, opacity: 0.55, side: THREE.DoubleSide }),
      );
      balloonGroup.add(haloMesh);

      // Initial balloon position — center of map disc, altitude = height above ground
      const { altKm: iAlt } = balloonPosRef.current;
      balloonGroup.position.set(0, iAlt * ATM_S + 0.1, 0);

      // ── Animation loop ─────────────────────────────────────────────────────
      let t = 0;
      const animate = () => {
        animRafId = requestAnimationFrame(animate);
        t += 0.007;

        // Animate latitude-circle streamlines — clamped to top hemisphere
        streamDataRef.current.forEach(({ line, basePhi, midR, phase, amp, waveN, STEPS }) => {
          const pos = line.geometry.attributes.position;
          for (let j = 0; j <= STEPS; j++) {
            const theta = (j / STEPS) * Math.PI * 2;
            const phi = Math.min(basePhi + amp * Math.sin(waveN * theta + phase + t * 1.4), Math.PI / 2 - 0.01);
            pos.setX(j, midR * Math.sin(phi) * Math.cos(theta));
            pos.setY(j, midR * Math.cos(phi));
            pos.setZ(j, midR * Math.sin(phi) * Math.sin(theta));
          }
          pos.needsUpdate = true;
        });

        // Balloon centered over map disc, height = altitude scaled to scene
        const { altKm } = balloonPosRef.current;
        balloonGroup.position.set(0, altKm * ATM_S + 0.1 + Math.sin(t * 1.4) * 0.02, 0);

        // Pulse halo
        haloMesh.material.opacity = 0.35 + Math.sin(t * 3) * 0.2;
        haloMesh.scale.setScalar(1 + Math.sin(t * 2.2) * 0.08);


        // Highlight label for atmospheric layer camera is currently inside
        const camAltKm = Math.max(0, camera.position.y / ATM_S);
        const zoomLayer = LAYERS.find(l => camAltKm >= l.minKm && camAltKm < l.maxKm)
          ?? (camAltKm >= MAX_KM ? LAYERS[0] : LAYERS[LAYERS.length - 1]);
        const lc = labelContainerRef.current;
        if (lc) {
          lc.querySelectorAll('[data-layer]').forEach(el => {
            el.classList.toggle('zoom-active', el.dataset.layer === zoomLayer.name);
          });
        }

        controls.update();
        renderer.render(scene, camera);
      };
      animate();

      // Resize
      const handleResize = () => {
        const w = mount.clientWidth || 500;
        const h = mount.clientHeight || 600;
        renderer.setPixelRatio(window.devicePixelRatio);
        renderer.setSize(w, h);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
      };
      const ro = new ResizeObserver(handleResize);
      ro.observe(mount);
      window.addEventListener('resize', handleResize);

      threeRef.current = { ...threeRef.current, renderer, controls, balloonGroup };

      cleanupFn = () => {
        cancelAnimationFrame(animRafId);
        ro.disconnect();
        window.removeEventListener('resize', handleResize);
        controls.dispose();
        renderer.dispose();
        if (mount.contains(renderer.domElement)) mount.removeChild(renderer.domElement);
      };
    });

    return () => {
      cancelAnimationFrame(initRafId);
      cleanupFn();
    };
  }, []);

  // Update balloon altitude
  useEffect(() => {
    if (currentAlt != null) balloonPosRef.current.altKm = currentAlt / 1000;
    if (scrubFrame?.lat != null) balloonPosRef.current.lat = scrubFrame.lat;
    if (scrubFrame?.lon != null) balloonPosRef.current.lon = scrubFrame.lon;
  }, [currentAlt, scrubFrame?.lat, scrubFrame?.lon]);

  // Load a 3×3 OSM tile grid (zoom 10) and apply to the Earth sphere texture
  useEffect(() => {
    const lat = scrubFrame?.lat;
    const lon = scrubFrame?.lon;
    const { mapMesh, mapTex } = threeRef.current ?? {};
    if (!mapMesh || lat == null || lon == null) return;

    const ZOOM = 10;
    const n   = Math.pow(2, ZOOM);
    const cx  = Math.floor((lon + 180) / 360 * n);
    const latRad = (lat * Math.PI) / 180;
    const cy  = Math.floor((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2 * n);

    const TILE_PX = 256;
    const GRID    = 3;
    const SIZE    = TILE_PX * GRID;

    const canvas = document.createElement('canvas');
    canvas.width = SIZE; canvas.height = SIZE;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#0d1a0d';
    ctx.fillRect(0, 0, SIZE, SIZE);

    let loaded = 0;
    const total = GRID * GRID;

    const finalize = () => {
      if (!threeRef.current?.mapMesh) return;
      const mesh = threeRef.current.mapMesh;
      if (mesh.material.map && mesh.material.map !== mapTex) mesh.material.map.dispose();
      const tex = new THREE.CanvasTexture(canvas);
      mesh.material.map = tex;
      mesh.material.needsUpdate = true;
      threeRef.current.mapTex = tex;
    };

    for (let dy = -1; dy <= 1; dy++) {
      for (let dx = -1; dx <= 1; dx++) {
        const img = new Image();
        img.crossOrigin = 'anonymous';
        img.onload = () => {
          ctx.drawImage(img, (dx + 1) * TILE_PX, (dy + 1) * TILE_PX, TILE_PX, TILE_PX);
          if (++loaded === total) finalize();
        };
        img.onerror = () => { if (++loaded === total) finalize(); };
        img.src = `https://tile.openstreetmap.org/${ZOOM}/${cx + dx}/${cy + dy}.png`;
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [Math.round((scrubFrame?.lat ?? 0) / 0.05), Math.round((scrubFrame?.lon ?? 0) / 0.05)]);

  // Layer label vertical % positions (bottom of each layer relative to full height)
  const layerLabelPositions = LAYERS.map(l => ({
    ...l,
    pct: (l.minKm / MAX_KM) * 100,
  }));

  return (
    <div className="altitude-column-wrap">
      <div className="altitude-col-header">
        <span>Altitude Profile 3D</span>
        {currentAlt != null && (
          <span className="alt-col-badge" style={{ background: activeLayer?.label ?? '#334' }}>
            {(currentAlt / 1000).toFixed(1)} km
          </span>
        )}
      </div>

      <div className="altitude-3d-scene-wrap">
        <div ref={mountRef} className="altitude-3d-mount" />

        {/* Atmospheric layer labels — left side */}
        <div className="atmo-layer-labels" ref={labelContainerRef}>
          {layerLabelPositions.map(layer => (
            <div
              key={layer.name}
              data-layer={layer.name}
              className={`atmo-layer-label${activeLayer?.name === layer.name ? ' active' : ''}`}
              style={{ bottom: `${layer.pct * 0.88 + 4}%`, color: layer.label }}
            >
              {layer.name}
            </div>
          ))}
        </div>

        {/* Weather effects legend — top right */}
        <div className="weather-effects-legend">
          <div className="weather-effects-title">WEATHER EFFECTS</div>
          {[
            { color: '#5ea8d8', symbol: '●', label: 'Precipitation' },
            { color: '#a8d8ff', symbol: '●', label: 'Ice Crystals' },
            { color: '#7eb3e8', symbol: '~', label: 'Wind Streams' },
            { color: '#ffe066', symbol: '⚡', label: 'Lightning' },
            { color: '#ff4444', symbol: '●', label: 'Radiosonde' },
            { color: '#cc88ff', symbol: '●', label: 'Aurora' },
          ].map(e => (
            <div
              key={e.label}
              className={`weather-effect-item${selectedEffect === e.label ? ' active' : ''}`}
              onClick={() => setSelectedEffect(sel => sel === e.label ? null : e.label)}
            >
              <span style={{ color: e.color }}>{e.symbol}</span>
              {e.label}
            </div>
          ))}
        </div>

        {/* Effect detail panel */}
        {effectData && (
          <div className="effect-detail-panel" style={{ borderColor: effectData.color }}>
            <div className="effect-detail-header" style={{ color: effectData.color }}>
              {selectedEffect}
              <button className="effect-detail-close" onClick={() => setSelectedEffect(null)}>✕</button>
            </div>
            {effectData.rows.map(row => (
              <div key={row.key} className="effect-detail-row">
                <span className="effect-detail-key">{row.key}</span>
                <span className="effect-detail-val" style={row.color ? { color: row.color } : undefined}>
                  {row.val}
                </span>
              </div>
            ))}
          </div>
        )}

        {validFrames.length === 0 && (
          <div className="altitude-3d-empty">
            <p>Click a balloon to view its altitude profile</p>
          </div>
        )}
      </div>

      {scrubFrame && (
        <div className="altitude-readout-3d">
          <div className="readout-pill">
            <span className="readout-pill-key">Altitude</span>
            <span className="readout-pill-val">
              {currentAlt != null ? `${(currentAlt / 1000).toFixed(1)} km` : '—'}
            </span>
          </div>
          <div className="readout-pill">
            <span className="readout-pill-key">Temp</span>
            <span className="readout-pill-val" style={{ color: '#e85c3a' }}>
              {currentTemp != null ? `${currentTemp.toFixed(1)}°C` : '—'}
            </span>
          </div>
          <div className="readout-pill">
            <span className="readout-pill-key">Humidity</span>
            <span className="readout-pill-val">
              {currentHumid != null ? `${Math.round(currentHumid)}%` : '—'}
            </span>
          </div>
          <div className="readout-pill">
            <span className="readout-pill-key">Ascent</span>
            <span className="readout-pill-val">
              {currentVelV != null ? `${currentVelV.toFixed(1)} m/s` : '—'}
            </span>
          </div>
          {analysis?.cape != null && (
            <div className="readout-pill">
              <span className="readout-pill-key">CAPE</span>
              <span className="readout-pill-val">{Math.round(analysis.cape)} J/kg</span>
            </div>
          )}
          {analysis?.storm_risk && (
            <div className="readout-pill" style={{ flex: 1 }}>
              <span className="readout-pill-key">Storm Risk</span>
              <span className="readout-pill-val" style={{ fontSize: '0.72rem', color: '#7eb3e8' }}>
                {analysis.storm_risk}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
