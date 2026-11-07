// Muni Live Map frontend: a map of SF with route lines (Phase 5), a route
// filter saved in the URL (Phase 6), and one marker per vehicle, refreshed
// every 30 seconds from our own backend (/api/vehicles).
//
// 'use strict' turns some silent JavaScript mistakes (like typos that create
// accidental global variables) into errors you can see in the browser console.
'use strict';

const POLL_MS = 30_000;            // the _ is just a digit separator: 30000 ms = 30 s
const SF_CENTER = [37.7599, -122.4370];
const FALLBACK_COLOR = '#666666';  // used if a route isn't in routes.json
const STALE_AFTER_S = 180;         // fade vehicles that haven't moved in 3 minutes
const TICK_MS = 15_000;            // how often to refresh "moved X ago" and fading

const DIRECTION_LABEL = { IB: 'Inbound', OB: 'Outbound', N: 'Northbound', S: 'Southbound' };

const ICON_BY_CATEGORY = {
  bus: 'icons/bus.svg',
  lightrail: 'icons/train.svg',
  streetcar: 'icons/train.svg',
  cablecar: 'icons/train.svg',
};

// Panel group order and headings.
const CATEGORIES = [
  ['lightrail', 'Light rail'],
  ['streetcar', 'Streetcar'],
  ['cablecar', 'Cable car'],
  ['bus', 'Bus'],
];

// Rail lines are drawn thicker and on top of bus lines, so lower numbers go first.
const DRAW_ORDER = { bus: 0, cablecar: 1, streetcar: 2, lightrail: 3 };

// ---- App state ----
// Filled in from /data/routes.json at startup: route_id → {color, category, long_name, ...}.
// It's generated from Muni's GTFS by scripts/build_geojson.py.
const routeInfo = new Map();
let routeShapes = null;         // the routes.geojson data, kept so we can redraw filtered lines
let lastVehicles = [];          // latest /api/vehicles list, kept so the filter can re-apply instantly
// The routes the user picked. An empty set means "no filter: show everything".
const selected = new Set();

function isVisible(route) {
  return selected.size === 0 || selected.has(route);
}

// ---- Time ----
// The server sends its own clock ("now") with every update. We remember how far
// the browser's clock is from it, so ages stay right even if the phone's clock
// is wrong. Date.parse turns an ISO time string into milliseconds.
let clockOffsetMs = 0;

function serverNowMs() {
  return Date.now() + clockOffsetMs;
}

function secondsSinceMoved(v) {
  if (!v.last_moved) return null;
  return Math.max(0, (serverNowMs() - Date.parse(v.last_moved)) / 1000);
}

function isStale(v) {
  const age = secondsSinceMoved(v);
  return age !== null && age > STALE_AFTER_S;
}

function formatAge(seconds) {
  if (seconds === null) return 'unknown';
  if (seconds < 60) return `${Math.round(seconds)} s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  return 'over an hour ago';
}

function styleFor(route) {
  const info = routeInfo.get(route);
  return {
    color: info ? info.color : FALLBACK_COLOR,
    icon: ICON_BY_CATEGORY[info ? info.category : 'bus'],
  };
}

// Never put text from the network straight into HTML: a value like
// "<img onerror=...>" would run as code. This turns <, >, & and quotes into
// harmless text.
function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (ch) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
  ));
}

// ---- Map setup ----
// L is the global object that leaflet.js creates. L.map('map') turns the
// <div id="map"> into an interactive map. The zoom buttons move to the bottom
// right so the route panel can have the top left.
const map = L.map('map', { zoomControl: false }).setView(SF_CENTER, 13);
L.control.zoom({ position: 'bottomright' }).addTo(map);

L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: 'Unofficial site · Vehicle data: <a href="https://511.org">511.org</a> · ' +
               '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);

// ---- Route lines ----
// One L.geoJSON layer holds every visible route line. When the filter changes
// we empty it and add back only the selected routes; with ~70 routes that's instant.
const linesLayer = L.geoJSON(null, {
  // `style` is called once per route to pick its line color and width.
  style: (feature) => ({
    color: feature.properties.color,
    weight: feature.properties.category === 'bus' ? 3 : 5,
    opacity: 0.55,
  }),
  // `onEachFeature` runs once per route line; here it attaches a hover tooltip.
  onEachFeature: (feature, layer) => {
    const p = feature.properties;
    layer.bindTooltip(`${escapeHtml(p.short_name)} ${escapeHtml(p.long_name)}`, { sticky: true });
  },
}).addTo(map);
// Leaflet always keeps lines (overlay pane) below markers (marker pane),
// so vehicles stay on top without extra work.

function drawLines() {
  if (!routeShapes) return;
  linesLayer.clearLayers();
  linesLayer.addData(routeShapes.features.filter((f) => isVisible(f.properties.route_id)));
}

// Loads the route list and the route shapes at the same time.
// Promise.all waits for both downloads to finish.
async function loadRoutes() {
  const [list, shapes] = await Promise.all([
    fetch('data/routes.json').then((r) => r.json()),
    fetch('data/routes.geojson').then((r) => r.json()),
  ]);
  for (const route of list) routeInfo.set(route.route_id, route);
  shapes.features.sort((a, b) =>
    DRAW_ORDER[a.properties.category] - DRAW_ORDER[b.properties.category]);
  routeShapes = shapes;
}

// ---- Markers ----
// We keep one marker per vehicle id in a Map (a key → value dictionary), so on
// each refresh we can move existing markers instead of deleting and re-creating
// all ~400 of them. That avoids flicker and is much cheaper for the browser.
const markers = new Map();

// Builds the marker's HTML. L.divIcon lets a marker be any HTML we like
// (styled in style.css) instead of Leaflet's default blue pin image.
function makeIcon(v, stale) {
  const style = styleFor(v.route);
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
    html: `<div class="vehicle${stale ? ' stale' : ''}" style="--route-color:${style.color}; --icon:url('${style.icon}')">
             ${arrow}
             <div class="badge"></div>
             <div class="label">${escapeHtml(v.route)}</div>
           </div>`,
  });
}

// ---- Vehicle popup ----
// Built as an HTML string, so every value from the network goes through escapeHtml.
function popupHtml(v) {
  const info = routeInfo.get(v.route);
  const color = info ? info.color : FALLBACK_COLOR;
  const textColor = info ? info.text_color : '#FFFFFF';
  const name = info ? info.long_name : '';
  const direction = DIRECTION_LABEL[v.direction] || v.direction || '';
  const age = secondsSinceMoved(v);
  const rows = [
    ['To', v.destination],
    ['Direction', direction],
    ['Next stop', v.next_stop_name || 'Not reported'],
  ].filter(([, value]) => value)   // skip rows with no value; [, value] ignores the label
   .map(([label, value]) => `<dt>${label}</dt><dd>${escapeHtml(value)}</dd>`)
   .join('');
  return `
    <div class="popup">
      <div class="popup-title">
        <span class="chip" style="background:${color};color:${textColor}">${escapeHtml(v.route)}</span>
        ${escapeHtml(name)}
      </div>
      <dl>${rows}</dl>
      <div class="popup-age${isStale(v) ? ' stale' : ''}">Moved ${formatAge(age)} · vehicle ${escapeHtml(v.id)}</div>
    </div>`;
}

// Brings one marker's look up to date: rebuilds the icon only if something
// drawn on it changed, and refreshes the popup text if it's open.
function refreshMarker(marker) {
  const v = marker.vehicle;
  const stale = isStale(v);
  const drawn = marker.drawn;
  if (!drawn || drawn.route !== v.route || drawn.bearing !== v.bearing || drawn.stale !== stale) {
    marker.setIcon(makeIcon(v, stale));
    marker.drawn = { route: v.route, bearing: v.bearing, stale };
  }
  if (marker.isPopupOpen()) marker.setPopupContent(popupHtml(v));
}

// Makes the markers on the map match `vehicles`, showing only routes that pass the filter.
function updateMarkers(vehicles) {
  const seen = new Set();
  for (const v of vehicles) {
    if (!isVisible(v.route)) continue;
    seen.add(v.id);
    let marker = markers.get(v.id);
    if (marker) {
      marker.setLatLng([v.lat, v.lon]);
      marker.vehicle = v;       // remember the latest data for this vehicle
      refreshMarker(marker);
    } else {
      marker = L.marker([v.lat, v.lon], { title: `Route ${v.route}` });
      marker.vehicle = v;
      refreshMarker(marker);    // set our icon before it's shown, so Leaflet's default pin never flashes
      // Passing a function means the popup is built when it opens, so it
      // always shows the latest data for this vehicle.
      marker.bindPopup(() => popupHtml(marker.vehicle), { maxWidth: 260 });
      marker.addTo(map);
      markers.set(v.id, marker);
    }
  }
  // Remove vehicles that left service or are now filtered out.
  for (const [id, marker] of markers) {
    if (!seen.has(id)) {
      marker.remove();
      markers.delete(id);
    }
  }
  return seen.size;
}

// ---- Filter state in the URL ----
// The selection lives in the part of the URL after '#', e.g. #routes=38,N.
// That part never goes to the server, so changing it doesn't reload the page,
// and copying the URL shares the exact view.
function readHash() {
  const params = new URLSearchParams(location.hash.slice(1));   // drop the leading '#'
  const ids = (params.get('routes') || '').split(',').filter((id) => routeInfo.has(id));
  return new Set(ids);
}

function writeHash() {
  const hash = selected.size ? `#routes=${[...selected].join(',')}` : '';
  // replaceState changes the URL without adding a Back-button entry per click.
  history.replaceState(null, '', hash || location.pathname);
}

// Called whenever the selection changes: redraw lines, markers, panel and URL.
function applyFilter({ updateUrl = true } = {}) {
  drawLines();
  const shown = updateMarkers(lastVehicles);
  syncPanel();
  if (updateUrl) writeHash();
  updateCount(shown);
}

// Zooms the map to fit the selected routes' lines (used for shared links).
function zoomToSelection() {
  if (selected.size && linesLayer.getLayers().length) {
    // Leave room for the panel so the routes aren't hidden under it.
    // On phones the panel spans the top instead of the left side.
    const wide = window.innerWidth >= 640 && !panel.classList.contains('collapsed');
    map.fitBounds(linesLayer.getBounds(), {
      paddingTopLeft: wide ? [panel.offsetWidth + 30, 30] : [30, 70],
      paddingBottomRight: [30, 30],
      animate: false,   // jump straight there; animating on page load just delays the tiles
    });
  }
}

// ---- Route panel ----
const panel = document.getElementById('panel');
const panelToggle = document.getElementById('panel-toggle');
const searchInput = document.getElementById('route-search');
const routeListEl = document.getElementById('route-list');
const clearButton = document.getElementById('clear-routes');

// Builds the grouped checkbox list once. We use createElement and textContent
// instead of an HTML string: textContent always treats data as plain text, so
// no escaping is needed.
function buildPanel() {
  for (const [category, heading] of CATEGORIES) {
    const routes = [...routeInfo.values()].filter((r) => r.category === category);
    if (!routes.length) continue;

    const group = document.createElement('section');
    group.className = 'route-group';
    const h = document.createElement('h3');
    h.textContent = heading;
    group.append(h);

    for (const r of routes) {
      const label = document.createElement('label');
      label.className = 'route-item';
      // Saved on the element so search can match without re-reading routeInfo.
      label.dataset.search = `${r.short_name} ${r.long_name}`.toLowerCase();

      const box = document.createElement('input');
      box.type = 'checkbox';
      box.value = r.route_id;
      // 'change' fires when the box is ticked or unticked.
      box.addEventListener('change', () => {
        if (box.checked) selected.add(r.route_id);
        else selected.delete(r.route_id);
        applyFilter();
      });

      const chip = document.createElement('span');
      chip.className = 'chip';
      chip.style.background = r.color;
      chip.style.color = r.text_color;
      chip.textContent = r.short_name;

      const name = document.createElement('span');
      name.className = 'route-name';
      name.textContent = r.long_name;

      label.append(box, chip, name);
      group.append(label);
    }
    routeListEl.append(group);
  }
}

// Ticks the checkboxes that match `selected` (e.g. after loading a shared link).
function syncPanel() {
  for (const box of routeListEl.querySelectorAll('input[type=checkbox]')) {
    box.checked = selected.has(box.value);
  }
  clearButton.disabled = selected.size === 0;
  panelToggle.textContent = selected.size ? `Routes (${selected.size})` : 'Routes';
}

// Hides routes (and whole groups) that don't match the search text.
function filterPanel() {
  const query = searchInput.value.trim().toLowerCase();
  for (const group of routeListEl.querySelectorAll('.route-group')) {
    let anyVisible = false;
    for (const item of group.querySelectorAll('.route-item')) {
      const match = !query || item.dataset.search.includes(query);
      item.hidden = !match;
      if (match) anyVisible = true;
    }
    group.hidden = !anyVisible;
  }
}

searchInput.addEventListener('input', filterPanel);
clearButton.addEventListener('click', () => {
  selected.clear();
  applyFilter();
});
panelToggle.addEventListener('click', () => panel.classList.toggle('collapsed'));
// Start collapsed on phones so the map isn't covered.
if (window.innerWidth < 640) panel.classList.add('collapsed');

// If someone edits the URL by hand or pastes a link into this tab, follow it.
window.addEventListener('hashchange', () => {
  const fromUrl = readHash();
  selected.clear();
  fromUrl.forEach((id) => selected.add(id));
  applyFilter({ updateUrl: false });
  zoomToSelection();
});

// ---- Polling ----
const statusEl = document.getElementById('status');
let statusTime = null;

function setStatus(text, isError = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle('error', isError);
}

function updateCount(shown) {
  if (!statusTime) return;
  setStatus(`${shown} vehicles · updated ${statusTime}`);
}

// `async` functions can use `await`, which pauses until a network request
// finishes without freezing the page.
async function refresh() {
  try {
    const response = await fetch('/api/vehicles');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    clockOffsetMs = Date.parse(data.now) - Date.now();
    lastVehicles = data.vehicles;
    const shown = updateMarkers(lastVehicles);
    if (data.updated_at === null) {
      setStatus('Waiting for first update…');
    } else if (data.stale) {
      setStatus('Data is out of date. 511 may be down.', true);
    } else {
      statusTime = new Date(data.updated_at).toLocaleTimeString();
      updateCount(shown);
    }
  } catch (err) {
    console.error('Failed to load vehicles:', err);
    setStatus("Can't reach the server.", true);
  }
}

// Start-up: load routes first so markers get their colors and the URL filter
// can be checked against real route ids. If the route files fail to load,
// still show vehicles (in grey, unfiltered) instead of nothing.
async function start() {
  try {
    await loadRoutes();
    buildPanel();
    readHash().forEach((id) => selected.add(id));
    applyFilter({ updateUrl: false });
    zoomToSelection();
  } catch (err) {
    console.error('Failed to load route data:', err);
  }
  refresh();
  setInterval(refresh, POLL_MS);
  // Between data updates, keep ages and fading current (no network needed).
  setInterval(() => markers.forEach(refreshMarker), TICK_MS);
}

start();
