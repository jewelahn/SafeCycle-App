/**
 * app.js — Boston Bike Safety  |  Hackachusetts 2026
 *
 * What this file does:
 *  1. Loads crash-dispatch and bike-lane-obstruction data from bundled JSON.
 *  2. Renders colour-coded markers on a Google Map (green=bike, blue=ped,
 *     amber=MV, red=obstruction).
 *  3. Lets the user filter marker layers via checkboxes.
 *  4. Calls the Google Maps Directions API (mode: bicycling) to draw a
 *     turn-by-turn cycling route and display a step list in the sidebar.
 *
 * The Google Maps JS SDK is loaded by index.html with &callback=initMap,
 * so initMap() is the entry point called automatically once the SDK is ready.
 */

"use strict";

/* ── Constants ──────────────────────────────────────────────────────────── */

const BOSTON_CENTER = { lat: 42.3601, lng: -71.0589 };
const DEFAULT_ZOOM  = 13;

// Marker colours matching the CSS legend
const COLORS = {
  bike: "#2e7d32",   // green
  ped:  "#1565c0",   // blue
  mv:   "#f9a825",   // amber
  obs:  "#c62828",   // red
};

/* ── Module-level state ─────────────────────────────────────────────────── */

let map;
let directionsService;
let directionsRenderer;
let infoWindow;

// Marker groups — each value is an array of google.maps.Marker instances
const markerGroups = { bike: [], ped: [], mv: [], obs: [] };

/* ── Entry point (called by Maps SDK) ──────────────────────────────────── */

async function initMap() {
  map = new google.maps.Map(document.getElementById("map"), {
    center: BOSTON_CENTER,
    zoom:   DEFAULT_ZOOM,
    mapTypeControl: false,
    streetViewControl: false,
    fullscreenControl: false,
    styles: subtleMapStyles(),
  });

  directionsService  = new google.maps.DirectionsService();
  directionsRenderer = new google.maps.DirectionsRenderer({
    map,
    suppressMarkers: false,
    polylineOptions: { strokeColor: "#2e7d32", strokeWeight: 5, strokeOpacity: 0.85 },
  });

  infoWindow = new google.maps.InfoWindow();

  // Attach Places Autocomplete to both input fields
  attachAutocomplete("origin");
  attachAutocomplete("destination");

  // Wire up UI controls
  document.getElementById("route-btn").addEventListener("click", planRoute);
  document.getElementById("clear-btn").addEventListener("click", clearRoute);
  ["chk-bike", "chk-ped", "chk-mv", "chk-obs"].forEach(id => {
    document.getElementById(id).addEventListener("change", applyFilters);
  });

  // Load data and render markers
  await loadAndRenderData();
}

/* ── Data loading ───────────────────────────────────────────────────────── */

async function loadAndRenderData() {
  const [dispatch, obstructions] = await Promise.all([
    fetchJSON("data/dispatch.json"),
    fetchJSON("data/obstructions.json"),
  ]);

  renderDispatchMarkers(dispatch);
  renderObstructionMarkers(obstructions);
  renderStats(dispatch, obstructions);
}

async function fetchJSON(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`Failed to load ${path}: ${res.status}`);
  return res.json();
}

/* ── Marker rendering ───────────────────────────────────────────────────── */

function renderDispatchMarkers(records) {
  records.forEach(rec => {
    const group = rec.mode === "bike" ? "bike"
                : rec.mode === "ped"  ? "ped"
                : "mv";

    const marker = new google.maps.Marker({
      position: { lat: rec.lat, lng: rec.lng },
      map,
      icon: circleIcon(COLORS[group], 7),
      title: rec.street || rec.xstreet1 || "Crash site",
    });

    marker.addListener("click", () => {
      infoWindow.setContent(dispatchInfoHTML(rec));
      infoWindow.open(map, marker);
    });

    markerGroups[group].push(marker);
  });
}

function renderObstructionMarkers(records) {
  records.forEach(rec => {
    const marker = new google.maps.Marker({
      position: { lat: rec.lat, lng: rec.lng },
      map,
      icon: circleIcon(COLORS.obs, 9),
      title: rec.topic,
      zIndex: 10,
    });

    marker.addListener("click", () => {
      infoWindow.setContent(obstructionInfoHTML(rec));
      infoWindow.open(map, marker);
    });

    markerGroups.obs.push(marker);
  });
}

/* ── Marker icon helper ─────────────────────────────────────────────────── */

function circleIcon(color, radius) {
  return {
    path: google.maps.SymbolPath.CIRCLE,
    scale: radius,
    fillColor: color,
    fillOpacity: 0.85,
    strokeColor: "#fff",
    strokeWeight: 1.5,
  };
}

/* ── Info-window HTML ───────────────────────────────────────────────────── */

function dispatchInfoHTML(rec) {
  const modeLabel = { bike: "Bike crash", ped: "Pedestrian crash", mv: "MV crash" };
  const badgeClass = { bike: "badge-bike", ped: "badge-ped", mv: "badge-mv" };
  const label = modeLabel[rec.mode] ?? rec.mode;
  const badge = badgeClass[rec.mode] ?? "";

  const location = [rec.street, rec.xstreet1, rec.xstreet2]
    .filter(Boolean).join(" / ") || "Unknown location";

  const date = rec.date ? new Date(rec.date).toLocaleDateString("en-US", {
    year: "numeric", month: "short", day: "numeric",
  }) : "Unknown date";

  return `
    <div class="iw">
      <span class="badge ${badge}">${label}</span>
      <h3>${escapeHtml(location)}</h3>
      <p>${date}</p>
      <p>${escapeHtml(rec.type ?? "")}</p>
    </div>`;
}

function obstructionInfoHTML(rec) {
  const comment = rec.comment
    ? escapeHtml(rec.comment.slice(0, 160)) + (rec.comment.length > 160 ? "…" : "")
    : "No details";

  return `
    <div class="iw">
      <span class="badge badge-obs">Bike-lane obstruction</span>
      <h3>${escapeHtml(rec.topic ?? rec.service ?? "Obstruction")}</h3>
      <p>${comment}</p>
      <p style="margin-top:4px;font-size:.72rem;color:#9e9e9e">${escapeHtml(rec.id)}</p>
    </div>`;
}

/* ── Filter checkboxes ──────────────────────────────────────────────────── */

function applyFilters() {
  const visible = {
    bike: document.getElementById("chk-bike").checked,
    ped:  document.getElementById("chk-ped").checked,
    mv:   document.getElementById("chk-mv").checked,
    obs:  document.getElementById("chk-obs").checked,
  };

  Object.entries(markerGroups).forEach(([group, markers]) => {
    markers.forEach(m => m.setVisible(visible[group]));
  });
}

/* ── Route planning ─────────────────────────────────────────────────────── */

function planRoute() {
  const origin      = document.getElementById("origin").value.trim();
  const destination = document.getElementById("destination").value.trim();

  if (!origin || !destination) {
    alert("Please enter both a start and end location.");
    return;
  }

  directionsService.route(
    {
      origin,
      destination,
      travelMode: google.maps.TravelMode.BICYCLING,
      unitSystem: google.maps.UnitSystem.METRIC,
      provideRouteAlternatives: false,
    },
    (result, status) => {
      if (status === google.maps.DirectionsStatus.OK) {
        displayRoute(result);
      } else {
        alert(`Could not find a cycling route: ${status}.\nTry a more specific address.`);
      }
    }
  );
}

function displayRoute(result) {
  directionsRenderer.setDirections(result);

  const route = result.routes[0];
  const leg   = route.legs[0];

  // Summary strip
  const summaryEl = document.getElementById("route-summary");
  summaryEl.classList.remove("hidden");
  document.getElementById("route-name").textContent = route.summary || "—";
  document.getElementById("route-dist").textContent = leg.distance.text;
  document.getElementById("route-time").textContent = leg.duration.text;

  // Warnings (Google adds cycling-specific notices sometimes)
  const warnEl = document.getElementById("route-warnings");
  const warnings = route.warnings ?? [];
  if (warnings.length) {
    warnEl.textContent = warnings.join(" ");
    warnEl.classList.remove("hidden");
  } else {
    warnEl.classList.add("hidden");
  }

  // Turn-by-turn steps
  const stepsCard = document.getElementById("steps-card");
  const stepsList = document.getElementById("steps-list");
  stepsCard.classList.remove("hidden");
  stepsList.innerHTML = "";

  leg.steps.forEach(step => {
    const li = document.createElement("li");
    // Strip HTML tags Google includes in html_instructions
    const instruction = step.instructions.replace(/<[^>]+>/g, "");
    li.innerHTML = `${escapeHtml(instruction)}
      <br><span class="step-meta">${step.distance.text} · ~${step.duration.text}</span>`;
    stepsList.appendChild(li);
  });
}

function clearRoute() {
  directionsRenderer.setDirections({ routes: [] });
  document.getElementById("origin").value      = "";
  document.getElementById("destination").value = "";
  document.getElementById("route-summary").classList.add("hidden");
  document.getElementById("route-warnings").classList.add("hidden");
  document.getElementById("steps-card").classList.add("hidden");
}

/* ── Places Autocomplete ────────────────────────────────────────────────── */

function attachAutocomplete(inputId) {
  const input = document.getElementById(inputId);
  const ac = new google.maps.places.Autocomplete(input, {
    componentRestrictions: { country: "us" },
    fields: ["formatted_address", "geometry"],
    types: ["geocode", "establishment"],
  });
  // Prevent form submit on Enter inside autocomplete
  ac.addListener("place_changed", () => {});
}

/* ── Stats sidebar ──────────────────────────────────────────────────────── */

function renderStats(dispatch, obstructions) {
  const bike = dispatch.filter(r => r.mode === "bike").length;
  const ped  = dispatch.filter(r => r.mode === "ped").length;
  const mv   = dispatch.filter(r => r.mode === "mv").length;

  const list = document.getElementById("stats-list");
  const items = [
    ["Total crash records", dispatch.length],
    ["Bike crashes",        bike],
    ["Pedestrian crashes",  ped],
    ["Motor-vehicle crashes", mv],
    ["Bike-lane obstructions", obstructions.length],
  ];

  list.innerHTML = items
    .map(([label, val]) => `<li>${label}<span>${val.toLocaleString()}</span></li>`)
    .join("");
}

/* ── Utility ────────────────────────────────────────────────────────────── */

function escapeHtml(str) {
  if (!str) return "";
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* ── Subtle map style (keeps labels readable, de-emphasises roads) ───────── */

function subtleMapStyles() {
  return [
    { featureType: "poi",            elementType: "labels",      stylers: [{ visibility: "off" }] },
    { featureType: "transit",        elementType: "labels.icon", stylers: [{ visibility: "off" }] },
    { featureType: "road.highway",   elementType: "geometry",    stylers: [{ color: "#f5d98b" }]  },
    { featureType: "road.arterial",  elementType: "geometry",    stylers: [{ color: "#ffffff" }]  },
    { featureType: "water",          elementType: "geometry",    stylers: [{ color: "#c9e9f6" }]  },
    { featureType: "landscape.natural", elementType: "geometry", stylers: [{ color: "#e8f5e9" }]  },
  ];
}
