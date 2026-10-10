// The explorer of a site written by `bib site`: the graph's lenses as
// `bib view` draws them, with the time bar. Everything was computed when the
// site was built (graph.json): each lens's layout, colours, sizes and
// labels, and the time windows of every record. This file only filters by
// time and draws, with 3d-force-graph for the graph lenses and globe.gl
// (loaded when first needed) for a globe lens.

const GLOBE_LIB = "https://cdn.jsdelivr.net/npm/globe.gl@2.46.2/dist/globe.gl.min.js";
const EARTH = {
  dark: "https://cdn.jsdelivr.net/npm/three-globe@2/example/img/earth-dark.jpg",
  earth: "https://cdn.jsdelivr.net/npm/three-globe@2/example/img/earth-blue-marble.jpg",
  light: "https://cdn.jsdelivr.net/npm/three-globe@2/example/img/earth-day.jpg",
  blueprint: "https://cdn.jsdelivr.net/npm/three-globe@2/example/img/earth-dark.jpg",
  wire: "https://cdn.jsdelivr.net/npm/three-globe@2/example/img/earth-dark.jpg",
};
const WORLD = 320; // layout units (the unit cube) to scene units
const NODE_SCALE = 0.9; // a node's radius per unit of `bib view`'s size
const DIM = 0.82; // how far a node outside the hovered neighbourhood fades into the canvas
const PLAY_SECONDS = 20;
const PLAY_FPS = 15;
const STEP = 0.01; // share of the span one , or . moves
const JUMP = 0.1; // with < and >
const ARC_TIERS = [[1, 0.24, 0.35], [2, 0.42, 0.5], [4, 0.6, 0.65]]; // fewest links, opacity, width

const $ = (id) => document.getElementById(id);
const state = {
  data: null,
  lens: 0,
  at: null, // null: everything; else {axis: "valid" | "known", t: days since 1970}
  axis: "valid",
  hidden: new Set(), // legend rows switched off
  focus: null, // node (graph lens) or place (globe) index under the pointer
  pinned: null,
  labels: true,
  playing: null,
};
let fg = null; // the 3d-force-graph instance
let globe = null; // the globe.gl instance
let scene = null; // the current graph lens, ready to draw
const pages = new Map(); // node id -> its json, once fetched

// -- time ---------------------------------------------------------------------

function sees(w) {
  const at = state.at;
  if (!at) return true;
  const [from, to, known] = w;
  if (at.axis === "known") return known === null || known <= at.t;
  return (from === null || from <= at.t) && (to === null || to > at.t);
}

// Graph.list_nodes: a node is in view when a record at either end of it is,
// or when it has none; under a valid time, a node whose type has a `starts`
// rule only from when it starts.
function nodeInView(i) {
  if (!state.at) return true;
  const node = state.data.nodes[i];
  if (state.at.axis === "valid" && node.b !== undefined && (node.b < 0 || node.b > state.at.t)) return false;
  return node.w === undefined || node.w.some(sees);
}

function day(t) {
  return new Date(t * 86400000).toISOString().slice(0, 10);
}

function span() {
  return state.data.spans[state.axis];
}

function setTime(fraction, byUser = true) {
  const s = span();
  if (!s) return;
  if (byUser) stopPlaying();
  fraction = Math.min(1, Math.max(0, fraction));
  $("slider").value = Math.round(fraction * 1000);
  state.at = { axis: state.axis, t: s[0] + (s[1] - s[0]) * fraction };
  showTime();
  redraw();
}

function allTime() {
  stopPlaying();
  state.at = null;
  $("slider").value = 1000;
  showTime();
  redraw();
}

function flipAxis() {
  const other = state.axis === "valid" ? "known" : "valid";
  if (!state.data.spans[other]) return;
  state.axis = other;
  const s = span();
  $("axis").textContent = other;
  $("first").textContent = day(s[0]);
  $("last").textContent = day(s[1]);
  if (state.at) setTime(1);
  else showTime();
}

function showTime() {
  $("when").textContent = state.at ? day(state.at.t) : "";
  $("all").classList.toggle("on", !state.at);
}

function togglePlay() {
  if (state.playing) return stopPlaying();
  if (!span()) return;
  let fraction = state.at ? $("slider").value / 1000 : 0;
  if (fraction >= 1) fraction = 0;
  setTime(fraction, false);
  $("play").textContent = "❚❚";
  state.playing = setInterval(() => {
    fraction += 1 / (PLAY_FPS * PLAY_SECONDS);
    setTime(fraction, false);
    if (fraction >= 1) stopPlaying();
  }, 1000 / PLAY_FPS);
}

function stopPlaying() {
  if (!state.playing) return;
  clearInterval(state.playing);
  state.playing = null;
  $("play").textContent = "▶";
}

function step(by) {
  setTime((state.at ? $("slider").value / 1000 : 1) + by);
}

// -- colours ------------------------------------------------------------------

function rgb(hex) {
  return [1, 3, 5].map((k) => parseInt(hex.slice(k, k + 2), 16));
}

function mix(hex, into, f) {
  const a = rgb(hex), b = rgb(into);
  return "#" + a.map((v, k) => Math.round(v * (1 - f) + b[k] * f).toString(16).padStart(2, "0")).join("");
}

function rgba(hex, alpha) {
  const [r, g, b] = rgb(hex);
  return `rgba(${r},${g},${b},${alpha})`;
}

function canvas() {
  const lens = state.data.lenses[state.lens];
  return (lens.theme && lens.theme.canvas) || state.data.chrome.canvas;
}

// -- lenses -------------------------------------------------------------------

function current() {
  return state.data.lenses[state.lens];
}

async function showLens(i, select = null) {
  stopPlaying();
  state.lens = (i + state.data.lenses.length) % state.data.lenses.length;
  state.hidden.clear();
  state.focus = state.pinned = null;
  const lens = current();
  for (const [k, a] of document.querySelectorAll("#lenses a").entries()) a.classList.toggle("on", k === state.lens);
  history.replaceState(null, "", select === null ? `#lens:${encodeURIComponent(lens.name)}` : `#${state.data.nodes[select].id}`);
  const isGlobe = lens.layout === "globe";
  $("plot").hidden = isGlobe;
  $("globe").hidden = !isGlobe;
  $("labels").replaceChildren();
  if (isGlobe) await drawGlobe(lens);
  else drawGraph(lens);
  drawLegend();
  if (select !== null) selectNode(select, true);
  else overview();
}

function drawGraph(lens) {
  const nodes = lens.nodes.map((n, k) => {
    const [x, y, z] = n.p.map((v) => v * WORLD);
    return { k, i: n.i, x, y, z, fx: x, fy: y, fz: z, c: n.c, s: n.s, l: n.l };
  });
  const neighbours = nodes.map(() => new Set());
  const links = lens.edges.map(([a, b, rel, from, to, known]) => {
    neighbours[a].add(b);
    neighbours[b].add(a);
    return { source: a, target: b, rel: lens.rels[rel], w: [from, to, known] };
  });
  const byNode = new Map(nodes.map((n) => [n.i, n.k]));
  scene = { lens, nodes, links, neighbours, byNode, visible: [] };
  computeVisible();
  if (!fg) {
    fg = ForceGraph3D({ controlType: "orbit" })($("plot"))
      .nodeId("k")
      .cooldownTicks(0)
      .warmupTicks(0)
      .enableNodeDrag(false)
      .nodeRelSize(1)
      .nodeResolution(10)
      .nodeOpacity(0.95)
      .linkWidth(0)
      .showNavInfo(false)
      .nodeLabel((n) => escapeHtml(state.data.nodes[n.i].name))
      .onNodeHover((n) => {
        $("plot").style.cursor = n ? "pointer" : null;
        if (state.pinned === null) light(n ? n.k : null);
      })
      .onNodeClick((n) => selectNode(n.i))
      .onBackgroundClick(() => deselect());
    window.addEventListener("resize", () => fg.width($("stage").clientWidth).height($("stage").clientHeight));
    requestAnimationFrame(placeLabels);
  }
  fg.width($("stage").clientWidth).height($("stage").clientHeight);
  fg.backgroundColor(canvas())
    .linkOpacity(lens.theme.edgeAlpha)
    .nodeVal((n) => (n.s * NODE_SCALE) ** 3)
    .graphData({ nodes, links });
  paint();
  fg.cameraPosition({ x: 0, y: 0, z: WORLD * 2.6 }, { x: 0, y: 0, z: 0 }, 0);
  const box = $("labels");
  scene.labelled = nodes.filter((n) => n.l).map((n) => {
    const div = document.createElement("div");
    div.textContent = n.l;
    box.append(div);
    return [n, div];
  });
}

function computeVisible() {
  const lens = scene.lens;
  scene.visible = scene.nodes.map((n) => nodeInView(n.i) && !state.hidden.has(lens.categories[n.c].name));
}

function linkEnds(l) {
  return [typeof l.source === "object" ? l.source.k : l.source, typeof l.target === "object" ? l.target.k : l.target];
}

function paint() {
  if (!fg || !scene) return;
  const lens = scene.lens;
  const near = state.focus === null ? null : new Set([state.focus, ...scene.neighbours[state.focus]]);
  const colour = (n) => lens.categories[n.c].color;
  fg.nodeVisibility((n) => scene.visible[n.k])
    .linkVisibility((l) => {
      const [a, b] = linkEnds(l);
      return scene.visible[a] && scene.visible[b] && sees(l.w);
    })
    .nodeColor((n) => (near && !near.has(n.k) ? mix(colour(n), canvas(), DIM) : colour(n)))
    .linkColor((l) => {
      const [a, b] = linkEnds(l);
      if (near) return a === state.focus || b === state.focus ? state.data.chrome.body : mix(colour(scene.nodes[a]), canvas(), 0.9);
      return lens.edgeColors[l.rel] || colour(scene.nodes[a]);
    })
    .linkOpacity(near ? 0.6 : lens.theme.edgeAlpha);
}

function light(k) {
  if (state.focus === k) return;
  state.focus = k;
  paint();
}

// Labels are HTML over the canvas, kept on their nodes every frame.
function placeLabels() {
  requestAnimationFrame(placeLabels);
  if (!scene || !scene.labelled || $("plot").hidden) return;
  const near = state.focus === null ? null : new Set([state.focus, ...scene.neighbours[state.focus]]);
  for (const [n, div] of scene.labelled) {
    const shown = state.labels && scene.visible[n.k] && (!near || near.has(n.k));
    div.hidden = !shown;
    if (!shown) continue;
    const p = fg.graph2ScreenCoords(n.x, n.y, n.z);
    div.style.transform = `translate(${p.x}px, ${p.y}px)`;
  }
}

// -- the globe ----------------------------------------------------------------

function loadScript(src) {
  return new Promise((done, fail) => {
    const tag = document.createElement("script");
    tag.src = src;
    tag.onload = done;
    tag.onerror = fail;
    document.head.append(tag);
  });
}

async function drawGlobe(lens) {
  scene = null;
  if (!window.Globe) await loadScript(GLOBE_LIB);
  if (!globe) {
    globe = Globe()($("globe"))
      .pointLat("lat")
      .pointLng("lon")
      .pointAltitude(0.012)
      .pointColor((p) => p.color)
      .pointRadius((p) => p.s * 0.09)
      .pointLabel((p) => `${escapeHtml(p.name)} · ${p.m.length}`)
      .onPointHover((p) => {
        $("globe").style.cursor = p ? "pointer" : null;
        if (state.pinned === null) lightPlace(p ? p.k : null);
      })
      .onPointClick((p) => selectPlace(p.k))
      .onGlobeClick(() => deselect())
      .arcStartLat((a) => a.from.lat)
      .arcStartLng((a) => a.from.lon)
      .arcEndLat((a) => a.to.lat)
      .arcEndLng((a) => a.to.lon)
      .arcAltitude((a) => a.h)
      .labelLat("lat")
      .labelLng("lon")
      .labelText((p) => `${p.name} · ${p.m.length}`)
      .labelSize(0.9)
      .labelDotRadius(0)
      .labelAltitude(0.015)
      .labelColor(() => state.data.chrome.body);
    window.addEventListener("resize", () => globe.width($("stage").clientWidth).height($("stage").clientHeight));
  }
  globe.width($("stage").clientWidth).height($("stage").clientHeight);
  globe.backgroundColor(canvas()).globeImageUrl(EARTH[lens.theme.globe] || EARTH.dark);
  const places = lens.places.map((p, k) => ({
    k, ...p, name: state.data.nodes[p.i].name, color: lens.categories[p.c].color,
  }));
  const arcs = lens.arcs.map((a) => ({ ...a, from: places[a.a], to: places[a.b], tier: tier(a.by.length) }));
  globe.scene = { lens, places, arcs };
  paintGlobe();
  const busiest = places.reduce((best, p) => (!best || p.m.length > best.m.length ? p : best), null);
  if (busiest) globe.pointOfView({ lat: busiest.lat, lng: busiest.lon, altitude: 2.2 }, 0);
}

function tier(count) {
  let found = 0;
  ARC_TIERS.forEach(([least], k) => { if (count >= least) found = k; });
  return found;
}

function placeInView(p) {
  return !state.hidden.has(globe.scene.lens.categories[p.c].name) && p.m.some(nodeInView);
}

function paintGlobe() {
  const { places, arcs } = globe.scene;
  const shown = places.filter(placeInView);
  const on = new Set(shown.map((p) => p.k));
  const lit = state.focus;
  globe.pointsData(shown)
    .labelsData(shown.filter((p) => p.l))
    .arcsData(arcs.filter((a) => on.has(a.a) && on.has(a.b) && a.by.some(nodeInView)))
    .arcColor((a) => {
      const [, alpha] = ARC_TIERS[a.tier];
      if (lit === null) return rgba("#78c8e6", alpha);
      return a.a === lit || a.b === lit ? rgba("#e1f5ff", 1) : rgba("#78c8e6", 0.06);
    })
    .arcStroke((a) => ARC_TIERS[a.tier][2]);
}

function lightPlace(k) {
  if (state.focus === k) return;
  state.focus = k;
  globe.arcColor(globe.arcColor());
}

function selectPlace(k) {
  state.pinned = k;
  lightPlace(k);
  state.focus = k;
  paintGlobe();
  const { places, arcs } = globe.scene;
  const place = places[k];
  const nodes = state.data.nodes;
  const byType = new Map();
  for (const m of place.m.filter(nodeInView)) {
    const type = nodes[m].type;
    if (!byType.has(type)) byType.set(type, []);
    byType.get(type).push(m);
  }
  let html = `<h2>${link(place.i)}</h2><p class="meta">${escapeHtml(nodes[place.i].type)} · ${place.lat.toFixed(2)}, ${place.lon.toFixed(2)}</p>`;
  for (const [type, members] of [...byType].sort()) {
    members.sort((a, b) => nodes[a].name.localeCompare(nodes[b].name));
    html += `<h3>${escapeHtml(type)} <span class="count">${members.length}</span></h3><ul>${members.map((m) => `<li>${link(m)}</li>`).join("")}</ul>`;
  }
  const linked = arcs.filter((a) => a.a === k || a.b === k).sort((x, y) => y.by.length - x.by.length);
  if (linked.length) {
    html += `<h3>Linked places</h3><ul>${linked.map((a) => {
      const other = places[a.a === k ? a.b : a.a];
      return `<li>${link(other.i)} <span class="t">${a.by.length}</span></li>`;
    }).join("")}</ul>`;
  }
  $("sidebar").innerHTML = html;
}

// -- the legend ---------------------------------------------------------------

function drawLegend() {
  const lens = current();
  const rows = lens.categories.map((c, k) => [c, k]).filter(([c]) => c.legend);
  $("legend").hidden = !rows.length;
  $("legend").innerHTML = rows.map(([c], n) =>
    `<button data-name="${escapeHtml(c.name)}" class="${state.hidden.has(c.name) ? "off" : ""}" title="Show or hide (${n + 1})">` +
    `<i style="background:${c.color}"></i>${escapeHtml(c.name)}</button>`).join("");
  for (const button of $("legend").querySelectorAll("button")) button.onclick = () => toggleCategory(button.dataset.name);
}

function toggleCategory(name) {
  if (state.hidden.has(name)) state.hidden.delete(name);
  else state.hidden.add(name);
  drawLegend();
  redraw();
}

function redraw() {
  if (current().layout === "globe") {
    if (globe && globe.scene) paintGlobe();
  } else if (scene) {
    computeVisible();
    paint();
  }
  if (state.pinned === null) overview();
}

// -- the sidebar --------------------------------------------------------------

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function link(i) {
  const node = state.data.nodes[i];
  return `<a href="n/${encodeURIComponent(node.id)}.html" data-i="${i}">${escapeHtml(node.name)}</a>`;
}

function overview() {
  const data = state.data;
  const lens = current();
  let counts;
  if (lens.layout === "globe") {
    const shown = globe && globe.scene ? globe.scene.places.filter(placeInView) : [];
    const placed = new Set(shown.flatMap((p) => p.m.filter(nodeInView)));
    counts = `${shown.length} places, ${placed.size} placed`;
  } else if (scene) {
    const nodes = scene.visible.filter(Boolean).length;
    const edges = scene.links.filter((l) => {
      const [a, b] = linkEnds(l);
      return scene.visible[a] && scene.visible[b] && sees(l.w);
    }).length;
    counts = `${nodes} nodes, ${edges} edges`;
  }
  const when = state.at ? `<p class="meta">${state.at.axis} ${day(state.at.t)}</p>` : "";
  $("sidebar").innerHTML =
    `<h2>${escapeHtml(lens.name)}</h2>` +
    (lens.description ? `<p>${escapeHtml(lens.description)}</p>` : "") +
    `<p class="meta">${counts || ""}</p>${when}` +
    (data.description ? `<h3>This graph</h3><p>${escapeHtml(data.description)}</p>` : "") +
    `<h3>Lenses</h3><ol class="lenses">${data.lenses.map((l, k) =>
      `<li class="${k === state.lens ? "on" : ""}"><a href="#lens:${encodeURIComponent(l.name)}" data-lens="${k}">${escapeHtml(l.name)}</a>` +
      (l.description ? ` <span class="t">${escapeHtml(l.description)}</span>` : "") + "</li>").join("")}</ol>` +
    `<h3>Keys</h3><p class="keys">drag: turn · right-drag: pan · scroll: zoom<br>` +
    `hover: neighbourhood · click: details<br>j / k: lens · t: labels · 1–9: legend rows<br>` +
    `space: play · , and .: step · &lt; and &gt;: jump · a: valid or known time<br>/: search · esc: clear</p>`;
}

async function selectNode(i, fly = false) {
  if (current().layout === "globe") return;
  const k = scene.byNode.get(i);
  if (k === undefined) return;
  state.pinned = k;
  light(k);
  if (fly) {
    const n = scene.nodes[k];
    // From outside the graph, on the side the node is on, so nothing stands between.
    const d = Math.hypot(n.x, n.y, n.z) || 1;
    const out = WORLD * 1.4;
    fg.cameraPosition({ x: n.x + (n.x / d) * out, y: n.y + (n.y / d) * out, z: n.z + (n.z / d) * out }, n, 900);
  }
  history.replaceState(null, "", `#${state.data.nodes[i].id}`);
  await showNode(i);
}

async function showNode(i) {
  const node = state.data.nodes[i];
  $("sidebar").innerHTML = `<h2>${escapeHtml(node.name)}</h2><p class="meta">${escapeHtml(node.type)}</p>`;
  let page = pages.get(node.id);
  if (!page) {
    try {
      page = await (await fetch(`n/${encodeURIComponent(node.id)}.json`)).json();
      pages.set(node.id, page);
    } catch {
      page = null;
    }
  }
  if (state.pinned === null || state.data.nodes[i] !== node) return;
  const ids = new Map(state.data.nodes.map((n, k) => [n.id, k]));
  const name = (id) => (ids.has(id) ? link(ids.get(id)) : escapeHtml(id));
  const now = (r) => !r.superseded_at;
  let html = `<h2>${escapeHtml(node.name)}</h2>` +
    `<p class="meta">${escapeHtml(node.type)} · <a href="n/${encodeURIComponent(node.id)}.html">open the page →</a></p>`;
  if (page) {
    if (page.notes) html += `<p>${escapeHtml(page.notes)}</p>`;
    const facts = page.facts.filter(now);
    if (facts.length) {
      html += `<h3>Facts</h3><dl>${facts.map((f) => `<dt>${escapeHtml(f.key)}</dt><dd>${value(f.value)}${when(f)}</dd>`).join("")}</dl>`;
    }
    for (const [heading, edges, end] of [["Edges out", page.out_edges.filter(now), "to"], ["Edges in", page.in_edges.filter(now), "from"]]) {
      if (!edges.length) continue;
      const byRel = new Map();
      for (const e of edges) {
        if (!byRel.has(e.rel)) byRel.set(e.rel, []);
        byRel.get(e.rel).push(e);
      }
      html += `<h3>${heading}</h3><dl>${[...byRel].sort().map(([rel, es]) =>
        `<dt>${escapeHtml(rel)} <span class="count">${es.length}</span></dt>` +
        es.slice(0, 40).map((e) => `<dd>${name(e[end])}${when(e)}</dd>`).join("") +
        (es.length > 40 ? `<dd class="t">and ${es.length - 40} more on the page</dd>` : "")).join("")}</dl>`;
    }
  }
  $("sidebar").innerHTML = html;
}

function value(v) {
  const text = String(v);
  return /^https?:\/\/\S+$/.test(text) ? `<a href="${escapeHtml(text)}" rel="nofollow">${escapeHtml(text)}</a>` : escapeHtml(text);
}

function when(r) {
  const parts = r.valid_from && r.valid_to ? `${r.valid_from} – ${r.valid_to}` : r.valid_from ? `from ${r.valid_from}` : r.valid_to ? `until ${r.valid_to}` : "";
  return parts ? ` <span class="t">${escapeHtml(parts)}</span>` : "";
}

function deselect() {
  state.pinned = null;
  if (current().layout === "globe") {
    state.focus = null;
    if (globe && globe.scene) paintGlobe();
  } else light(null);
  history.replaceState(null, "", `#lens:${encodeURIComponent(current().name)}`);
  overview();
}

// A click on a node's name in the sidebar shows it in this lens when it is
// drawn here, and opens its page when it is not.
$("sidebar").addEventListener("click", (event) => {
  const a = event.target.closest("a");
  if (!a) return;
  if (a.dataset.lens !== undefined) {
    event.preventDefault();
    showLens(Number(a.dataset.lens));
    return;
  }
  if (a.dataset.i === undefined || current().layout === "globe" || !scene) return;
  const i = Number(a.dataset.i);
  if (scene.byNode.has(i)) {
    event.preventDefault();
    selectNode(i, true);
  }
});

// -- search -------------------------------------------------------------------

function fold(text) {
  return text.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

function search(text) {
  const needle = fold(text.trim());
  const hits = $("hits");
  if (!needle) {
    hits.hidden = true;
    return;
  }
  const found = [];
  for (const [i, node] of state.data.nodes.entries()) {
    const at = node.folded.indexOf(needle);
    if (at >= 0 || fold(node.id).includes(needle)) found.push([at < 0 ? 999 : at, node.name.length, i]);
  }
  found.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  hits.innerHTML = found.slice(0, 12).map(([, , i]) =>
    `<li data-i="${i}"><span>${escapeHtml(state.data.nodes[i].name)}</span><span class="t">${escapeHtml(state.data.nodes[i].type)}</span></li>`).join("");
  hits.hidden = !found.length;
  hits.firstChild && hits.firstChild.classList.add("on");
}

function go(i) {
  $("hits").hidden = true;
  $("search").value = "";
  $("search").blur();
  const lens = findLens(i);
  if (lens === null) location.href = `n/${encodeURIComponent(state.data.nodes[i].id)}.html`;
  else if (lens === state.lens) selectNode(i, true);
  else showLens(lens, i);
}

// The lens to show a node in: the current one when it is drawn there, else
// the first graph lens that draws it, else none (its page).
function findLens(i) {
  const lenses = state.data.lenses;
  const has = (k) => lenses[k].layout !== "globe" && lenses[k].nodes.some((n) => n.i === i);
  if (has(state.lens)) return state.lens;
  for (let k = 0; k < lenses.length; k++) if (has(k)) return k;
  return null;
}

$("search").addEventListener("input", (e) => search(e.target.value));
$("search").addEventListener("keydown", (e) => {
  const items = [...$("hits").children];
  const on = items.findIndex((li) => li.classList.contains("on"));
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    if (!items.length) return;
    items[on]?.classList.remove("on");
    items[(on + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length].classList.add("on");
  } else if (e.key === "Enter" && on >= 0) {
    go(Number(items[on].dataset.i));
  } else if (e.key === "Escape") {
    $("hits").hidden = true;
    e.target.blur();
  }
  e.stopPropagation();
});
$("hits").addEventListener("mousedown", (e) => {
  const li = e.target.closest("li");
  if (li) go(Number(li.dataset.i));
});
$("search").addEventListener("blur", () => setTimeout(() => ($("hits").hidden = true), 150));

// -- keys and start -----------------------------------------------------------

document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" && e.target.type !== "range") return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const keys = {
    j: () => showLens(state.lens + 1),
    k: () => showLens(state.lens - 1),
    Tab: () => showLens(state.lens + (e.shiftKey ? -1 : 1)),
    t: () => { state.labels = !state.labels; },
    " ": togglePlay,
    a: flipAxis,
    ",": () => step(-STEP),
    ".": () => step(STEP),
    "<": () => step(-JUMP),
    ">": () => step(JUMP),
    Escape: deselect,
    "/": () => $("search").focus(),
  };
  if (/^[1-9]$/.test(e.key)) {
    const rows = current().categories.filter((c) => c.legend);
    const row = rows[Number(e.key) - 1];
    if (row) toggleCategory(row.name);
    e.preventDefault();
    return;
  }
  if (keys[e.key]) {
    e.preventDefault();
    keys[e.key]();
  }
});

function fromHash() {
  const hash = decodeURIComponent(location.hash.slice(1));
  if (hash.startsWith("lens:")) {
    const k = state.data.lenses.findIndex((l) => l.name.toLowerCase() === hash.slice(5).toLowerCase());
    return [k < 0 ? 0 : k, null];
  }
  const i = state.data.nodes.findIndex((n) => n.id === hash);
  if (i >= 0) {
    const k = findLens(i);
    if (k !== null) return [k, i];
  }
  return [0, null];
}

async function start() {
  const data = await (await fetch("graph.json")).json();
  state.data = data;
  for (const node of data.nodes) node.folded = fold(node.name);
  for (const [key, value] of Object.entries(data.chrome)) document.documentElement.style.setProperty(`--${key}`, value);
  $("lenses").innerHTML = data.lenses.map((l, k) =>
    `<a href="#lens:${encodeURIComponent(l.name)}" data-lens="${k}" title="${escapeHtml(l.description)}">${escapeHtml(l.name)}</a>`).join("");
  for (const a of $("lenses").querySelectorAll("a")) a.onclick = (e) => { e.preventDefault(); showLens(Number(a.dataset.lens)); };
  if (data.spans.valid || data.spans.known) {
    if (!data.spans.valid) state.axis = "known";
    $("time").hidden = false;
    $("axis").textContent = state.axis;
    $("first").textContent = day(span()[0]);
    $("last").textContent = day(span()[1]);
    $("slider").addEventListener("input", (e) => setTime(e.target.value / 1000));
    $("play").onclick = togglePlay;
    $("axis").onclick = flipAxis;
    $("all").onclick = allTime;
    showTime();
  }
  $("loading").remove();
  const [lens, node] = fromHash();
  await showLens(lens, node);
  window.addEventListener("hashchange", () => {
    const [k, i] = fromHash();
    if (k !== state.lens) showLens(k, i);
    else if (i !== null) selectNode(i, true);
  });
}

start().catch((error) => {
  $("loading").textContent = `Could not load the graph: ${error}`;
});
