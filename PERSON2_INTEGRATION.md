# Person 2 → Person 3 Integration Guide

How to connect the 3D globe (Person 2) to the atmospheric charts panel (Person 3).

---

## The One Thing You Need To Do

When a user clicks a balloon on your globe, fire this DOM event:

```javascript
document.dispatchEvent(new CustomEvent("balloonSelected", {
  detail: { serial: "X3833334" }  // the sonde serial string
}));
```

That's it. Person 3's code is already listening for it and will load the sounding chart, wind profile, and score card automatically.

---

## Event Contract

| Field | Type | Required | Description |
|---|---|---|---|
| `detail.serial` | `string` | Yes | The radiosonde serial number (e.g. `"X3833334"`, `"W4775082"`, `"383A6A83"`) |

The serial must match what comes back from the pipeline's `/balloons` endpoint — it's the same string you're using to plot the balloon on the globe.

---

## Where To Get Balloon Data For Your Globe

Person 1's Flask backend runs on **port 8080**. These are the endpoints you'll use:

| Endpoint | What It Returns |
|---|---|
| `GET /balloons` | All active balloons with latest lat/lon/alt, count, last_updated |
| `GET /balloon/<serial>` | Full flight path — every GPS frame in chronological order |
| `GET /status` | Server health check |

### Example: fetching active balloons for globe markers

```javascript
const res = await fetch('/balloons');  // proxied to localhost:8080
const data = await res.json();
// data.balloons = [{ serial, lat, lon, alt, temp, humidity, vel_v, ... }, ...]

data.balloons.forEach(balloon => {
  // Place marker on globe at balloon.lat, balloon.lon, balloon.alt
  // On click:
  marker.onClick = () => {
    document.dispatchEvent(new CustomEvent("balloonSelected", {
      detail: { serial: balloon.serial }
    }));
  };
});
```

### Example: fetching a flight path for a trail line

```javascript
const res = await fetch(`/balloon/${serial}`);
const data = await res.json();
// data.path = [{ lat, lon, alt, temp, humidity, datetime, vel_v }, ...]

// Draw a 3D polyline through data.path on the globe
```

---

## Vite Proxy

If you're running inside the same Vite dev server (recommended), the proxy is already configured in `frontend/vite.config.js` — all requests to `/balloon*`, `/balloons`, and `/status` forward to Flask on port 8080. You don't need to hardcode `http://localhost:8080` in your fetch calls.

If you're running a separate dev server, either:
- Add the same proxy config to your own Vite/webpack config
- Or fetch directly from `http://localhost:8080/balloons` (CORS is enabled, `cors_allowed_origins="*"`)

---

## Testing the Integration

You can verify Person 3's code picks up your event without needing the globe. Open the browser console on `http://localhost:5173` and paste:

```javascript
document.dispatchEvent(new CustomEvent("balloonSelected", {
  detail: { serial: "383A6A83" }
}));
```

The sounding chart should immediately start loading and animating for that balloon.

To get a valid serial to test with:

```javascript
const res = await fetch('/balloons');
const data = await res.json();
console.log(data.balloons.map(b => b.serial));
```

---

## If You're Using React

If your globe is a React component in the same app, you can skip the DOM event entirely and use shared state instead. Person 3's `App.jsx` accepts a serial via `setActiveSerial()`. Two options:

**Option A — Lift state up.** If both components share a parent, pass `setActiveSerial` down to your globe component as a prop.

**Option B — Zustand store.** Create a shared store:

```javascript
// store.js
import { create } from 'zustand';

export const useStore = create((set) => ({
  selectedSerial: null,
  selectBalloon: (serial) => set({ selectedSerial: serial }),
}));
```

Person 2 calls `selectBalloon(serial)` on click. Person 3 reads `selectedSerial` and passes it to `<SoundingChart>`. If you go this route, let Person 3 know and we'll swap out the DOM event listener.

**The DOM event works fine for now regardless of framework** — it's the simplest path for hackathon integration.

---

## What Person 3's Panel Shows

When you fire `balloonSelected`, Person 3 loads:

1. **Sounding Chart** — altitude vs temperature/dewpoint, animated replay then live-updating every 2 seconds
2. **Wind Profile** (coming soon) — wind speed and direction at each altitude band
3. **Instability Score Card** (coming soon) — CAPE, lapse rate, tropopause, storm risk

All three appear simultaneously in the analysis panel — no tabs.

---

## Quick Reference

```
Flask backend:     http://localhost:8080   (start with: python3 src/data_pipeline.py)
Vite frontend:     http://localhost:5173   (start with: cd frontend && npm run dev)
Event name:        "balloonSelected"
Event payload:     { detail: { serial: string } }
```
