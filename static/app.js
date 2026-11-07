// Muni Live Map frontend (Phase 4): a map of SF with one marker per vehicle,
// refreshed every 30 seconds from our own backend (/api/vehicles).
//
// 'use strict' turns some silent JavaScript mistakes (like typos that create
// accidental global variables) into errors you can see in the browser console.
'use strict';

const POLL_MS = 30_000;            // the _ is just a digit separator: 30000 ms = 30 s
const SF_CENTER = [37.7599, -122.4370];

// Until Phase 5 adds the real GTFS route colors, color by vehicle type.
const LIGHT_RAIL = new Set(['J', 'K', 'L', 'M', 'N', 'T']);
const CABLE_CAR = new Set(['CA', 'PH', 'PM']);
const CATEGORY_STYLE = {
  bus:       { color: '#005B95', icon: 'icons/bus.svg' },
  lightrail: { color: '#942D83', icon: 'icons/train.svg' },
  streetcar: { color: '#B49A36', icon: 'icons/train.svg' },
  cablecar:  { color: '#8B4513', icon: 'icons/train.svg' },
};

function categoryOf(route) {
  if (route === 'F') return 'streetcar';
  if (LIGHT_RAIL.has(route)) return 'lightrail';
  if (CABLE_CAR.has(route)) return 'cablecar';
  return 'bus';
}

// ---- Map setup ----
// L is the global object that leaflet.js creates. L.map('map') turns the
// <div id="map"> into an interactive map.
const map = L.map('map').setView(SF_CENTER, 13);

L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: 'Unofficial site · Vehicle data: <a href="https://511.org">511.org</a> · ' +
               '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);

// ---- Markers ----
// We keep one marker per vehicle id in a Map (a key → value dictionary), so on
// each refresh we can move existing markers instead of deleting and re-creating
// all ~400 of them. That avoids flicker and is much cheaper for the browser.
const markers = new Map();

// Builds the marker's HTML. L.divIcon lets a marker be any HTML we like
// (styled in style.css) instead of Leaflet's default blue pin image.
function makeIcon(v) {
  const style = CATEGORY_STYLE[categoryOf(v.route)];
  // A bearing of null means "unknown": hide the arrow rather than point north.
  const arrow = v.bearing == null
    ? ''
    : `<div class="heading" style="transform: rotate(${v.bearing}deg)"></div>`;
  // Backtick strings are "template literals": ${...} inserts a value.
  // CSS custom properties (--route-color, --icon) pass values into style.css.
  return L.divIcon({
    className: '',              // drop Leaflet's default white-box styling
    iconSize: [28, 28],
    iconAnchor: [14, 14],       // the center of the circle sits on the vehicle's position
    html: `<div class="vehicle" style="--route-color:${style.color}; --icon:url('${style.icon}')">
             ${arrow}
             <div class="badge"></div>
             <div class="label">${escapeHtml(v.route)}</div>
           </div>`,
  });
}

// Never put text from the network straight into HTML: a value like
// "<img onerror=...>" would run as code. This turns <, >, & and quotes into
// harmless text.
function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (ch) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
  ));
}

function updateMarkers(vehicles) {
  const seen = new Set();
  for (const v of vehicles) {
    seen.add(v.id);
    const existing = markers.get(v.id);
    if (existing) {
      existing.setLatLng([v.lat, v.lon]);
      // Rebuilding the icon replaces its HTML, so only do it when something
      // drawn on it changed.
      if (existing.vehicle.route !== v.route || existing.vehicle.bearing !== v.bearing) {
        existing.setIcon(makeIcon(v));
      }
      existing.vehicle = v;
    } else {
      const marker = L.marker([v.lat, v.lon], { icon: makeIcon(v), title: `Route ${v.route}` });
      marker.vehicle = v;       // remember the data we drew it with
      marker.addTo(map);
      markers.set(v.id, marker);
    }
  }
  // Vehicles missing from this update have gone out of service: remove them.
  for (const [id, marker] of markers) {
    if (!seen.has(id)) {
      marker.remove();
      markers.delete(id);
    }
  }
}

// ---- Polling ----
const statusEl = document.getElementById('status');

function setStatus(text, isError = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle('error', isError);
}

// `async` functions can use `await`, which pauses until a network request
// finishes without freezing the page.
async function refresh() {
  try {
    const response = await fetch('/api/vehicles');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    updateMarkers(data.vehicles);
    if (data.updated_at === null) {
      setStatus('Waiting for first update…');
    } else if (data.stale) {
      setStatus('Data is out of date. 511 may be down.', true);
    } else {
      const time = new Date(data.updated_at).toLocaleTimeString();
      setStatus(`${data.vehicles.length} vehicles · updated ${time}`);
    }
  } catch (err) {
    console.error('Failed to load vehicles:', err);
    setStatus("Can't reach the server.", true);
  }
}

refresh();
setInterval(refresh, POLL_MS);
