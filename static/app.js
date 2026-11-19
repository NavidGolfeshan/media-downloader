"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  media: null,            // {is_playlist, title, items:[{url,title}], groups:[{url,title}]}
  stack: [],              // media views we drilled down from (for Back)
  selected: new Set(),    // indices into media.items
  filter: "",             // text the video list is narrowed to ("" = show everything)
  mode: "mp3",
  quality: "192",
  formatCache: new Map(),     // url -> {heights, subtitles}
  config: {},                 // download_dir, proxy, ... from the server
  found: new Map(),           // item id -> versions already in the download folder
  acknowledged: new Set(),    // "id|mode|quality" the person agreed to download again
  skipPrompts: false,         // "Skip this message" pressed for the current list
  formatRequest: 0,
  jobId: null,
  pollTimer: null,
  autoDownloaded: false,
};

const MP3_QUALITIES = ["128", "192", "320"];
const FALLBACK_HEIGHTS = [360, 480, 720, 1080];

/* ---------- region ---------- */

async function loadRegion() {
  const node = $("region");
  try {
    const r = await api("/api/region");
    const place = [r.city, r.region, r.country].filter(Boolean).join(", ");
    const code = r.country_code && r.country_code !== r.country ? ` (${r.country_code})` : "";
    const route = r.via_proxy ? "via proxy" : "direct connection";
    node.textContent = `Region: ${place}${code} · IP ${r.ip} · ${route}`;
  } catch (err) {
    node.textContent = "Region: couldn't be determined (" + err.message + ")";
  }
}

/* ---------- helpers ---------- */

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) node.append(child);
  return node;
}

async function api(path, options = {}) {
  const init = { method: options.method || "GET", headers: {} };
  if (options.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch (err) {
    throw new Error("Can't reach the downloader. Is `python main.py` still running?");
  }
  let data = {};
  try { data = await response.json(); } catch (err) { /* empty body */ }
  if (!response.ok) {
    const error = new Error(data.error || `Request failed (${response.status}).`);
    error.status = response.status;
    throw error;
  }
  return data;
}

function showError(id, message) {
  const node = $(id);
  node.textContent = message || "";
  node.hidden = !message;
}

function formatSize(bytes) {
  if (bytes >= 1e9) return (bytes / 1e9).toFixed(2) + " GB";
  if (bytes >= 1e6) return (bytes / 1e6).toFixed(1) + " MB";
  return Math.max(1, Math.round(bytes / 1e3)) + " KB";
}

function formatDuration(seconds) {
  if (!seconds) return "";
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

function formatSpeed(bytesPerSecond) {
  return bytesPerSecond ? `${formatSize(bytesPerSecond)}/s` : "";
}

function formatEta(seconds) {
  if (seconds === null || seconds === undefined || seconds < 0) return "";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${String(m).padStart(2, "0")}:${sec}`;
}

function trackDetail(track) {
  if (track.status === "downloading") {
    const sizes = track.total
      ? `${formatSize(track.downloaded)} / ${formatSize(track.total)}`
      : (track.downloaded ? formatSize(track.downloaded) : "");
    return [`${Math.round(track.percent)}%`, sizes, formatSpeed(track.speed)].filter(Boolean).join(" · ");
  }
  if (track.status === "done" && track.size) {
    const subs = track.subtitles ? ` · +${plural(track.subtitles, "subtitle")}` : "";
    return `${STATUS_TEXT.done} · ${formatSize(track.size)}${subs}`;
  }
  return STATUS_TEXT[track.status] || track.status;
}

function setRadio(group, value) {
  for (const button of group.querySelectorAll("[role=radio]")) {
    button.setAttribute("aria-checked", String(button.dataset.value === value));
  }
}

function plural(count, word) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

/* ---------- what is already downloaded ---------- */

const KIND = { mp3: "music", mp4: "video" };

function safeName(value) {
  const cleaned = String(value).replace(/[<>:"/\\|?*\x00-\x1f]/g, "_").replace(/^[ .]+|[ .]+$/g, "");
  return (cleaned || "unknown").slice(0, 100);
}

function qualityText(mode, quality) {
  return mode === "mp3" ? `${quality}kbps` : `${quality}p`;
}

function digitsOf(quality) {
  return String(quality || "").replace(/\D/g, "");
}

/* null when the item is new; otherwise how the wanted version relates to what we have. */
function classify(item) {
  const records = (item && item.id && state.found.get(item.id)) || [];
  if (!records.length) return null;
  const kind = KIND[state.mode];
  const same = records.filter((r) => r.kind === kind && digitsOf(r.quality) === state.quality);
  if (same.length) return { category: "same", have: same };
  const sameKind = records.filter((r) => r.kind === kind);
  if (sameKind.length) return { category: "quality", have: sameKind };
  return { category: "format", have: records };
}

function fullPath(record) {
  return `${state.config.download_dir || ""}/${record.rel}`;
}

async function refreshFound() {
  const ids = (state.media?.items || []).map((i) => i.id).filter(Boolean);
  if (!ids.length) { state.found = new Map(); renderBadges(); return; }
  try {
    const data = await api("/api/check", { method: "POST", body: { ids } });
    state.found = new Map(Object.entries(data.found));
  } catch (err) {
    state.found = new Map();
  }
  renderBadges();
}

function haveText(info) {
  const labels = [...new Set(info.have.map((r) => r.label))].map((l) => `'${l}'`).join(", ");
  return info.category === "same" ? `Already downloaded: ${labels}` : `You have: ${labels}`;
}

function renderBadges() {
  if (!state.media) return;
  state.media.items.forEach((item, index) => {
    const badge = document.querySelector(`#track-list [data-have="${index}"]`);
    if (!badge) return;
    const info = classify(item);
    badge.hidden = !info;
    badge.className = `have ${info ? info.category : ""}`;
    badge.textContent = info ? haveText(info) : "";
    badge.title = info ? info.have.map(fullPath).join("\n") : "";
  });

  const note = $("have-note");
  const single = !state.media.is_playlist ? classify(state.media.items[0]) : null;
  note.hidden = !single;
  if (single) {
    note.textContent = `${haveText(single)}\n${single.have.map(fullPath).join("\n")}`;
  }
}

function renderDest() {
  const media = state.media;
  if (!media) return;
  const parts = [state.config.download_dir || "…", safeName(media.channel || "downloads"),
    ...(media.section || ["videos"]).map(safeName)];
  $("dest-preview").textContent =
    `Saves to: ${parts.join("/")}  (file names end with [${qualityText(state.mode, state.quality)}])`;
}

/* One question for everything that would be downloaded twice. Resolves to the indices to keep,
   or null if the person closed the box (do nothing). */
async function confirmDuplicates(indices) {
  if (state.skipPrompts) return indices;
  const conflicts = [];
  for (const index of indices) {
    const item = state.media.items[index];
    const info = classify(item);
    if (info && !state.acknowledged.has(`${item.id}|${state.mode}|${state.quality}`)) {
      conflicts.push({ index, item, info });
    }
  }
  if (!conflicts.length) return indices;

  const answer = await askAboutDuplicates(conflicts);
  if (answer === "cancel") return null;
  if (answer === "no") {
    const drop = new Set(conflicts.map((c) => c.index));
    return indices.filter((i) => !drop.has(i));
  }
  if (answer === "skip") state.skipPrompts = true;
  else conflicts.forEach((c) => state.acknowledged.add(`${c.item.id}|${state.mode}|${state.quality}`));
  return indices;
}

const DUP_TEXT = {
  same: "You are trying to download some items again (with the same quality). Are you sure about this?",
  quality: "You are trying to download some items again (with a different quality). Are you sure about this?",
  format: "You are trying to download some items again (with a different format). Are you sure about this?",
};

function askAboutDuplicates(conflicts) {
  const body = $("dup-body");
  body.replaceChildren();
  for (const category of ["same", "quality", "format"]) {
    const group = conflicts.filter((c) => c.info.category === category);
    if (!group.length) continue;
    body.append(el("section", {}, [
      el("p", { text: DUP_TEXT[category] }),
      el("ul", {}, group.map((c) => {
        const labels = [...new Set(c.info.have.map((r) => r.label))].map((l) => `'${l}'`).join(", ");
        return el("li", {}, [
          el("span", { text: `${c.item.title} - you have the ${labels} version of this` }),
          el("span", { class: "path", text: c.info.have.map(fullPath).join("  |  ") }),
        ]);
      })),
    ]));
  }
  const dialog = $("dup-dialog");
  dialog.returnValue = "";
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue || "cancel"), { once: true });
    dialog.showModal();
  });
}

/* ---------- inspect ---------- */

$("inspect-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = $("url").value.trim();
  if (!url) return;

  showError("inspect-error", "");
  $("inspect-btn").disabled = true;
  $("inspect-btn").textContent = "Looking up…";
  try {
    const media = await api("/api/inspect", { method: "POST", body: { url } });
    state.stack = [];
    loadMedia(media);
  } catch (err) {
    showError("inspect-error", err.message);
  } finally {
    $("inspect-btn").disabled = false;
    $("inspect-btn").textContent = "Look up";
  }
});

function loadMedia(media) {
  state.media = media;
  const items = media.items || [];
  const groups = media.groups || [];
  state.selected = new Set(media.is_playlist ? [] : [0]);
  state.mode = "mp3";
  state.quality = "192";
  state.found = new Map();
  state.acknowledged = new Set();
  state.skipPrompts = false;

  $("job-card").hidden = true;
  $("media-card").hidden = false;
  $("options-card").hidden = items.length === 0;
  $("media-title").textContent = media.title;

  const parts = [];
  if (!media.is_playlist) parts.push("Single video");
  else {
    if (groups.length) parts.push(plural(groups.length, "playlist or section"));
    if (items.length) parts.push(plural(items.length, "video"));
  }
  if (!media.is_playlist && items[0]?.duration) parts.push(formatDuration(items[0].duration));
  $("media-sub").textContent = (media.is_playlist ? "Playlist · " : "") + parts.join(" · ");

  $("back-btn").hidden = state.stack.length === 0;
  $("playlist-tools").hidden = !(media.is_playlist && items.length);
  showError("range-error", "");
  showError("start-error", "");
  showError("browse-error", "");
  $("range").value = "";
  state.filter = "";
  $("filter").value = "";

  renderGroups();
  if (media.is_playlist && items.length) renderTrackPicker();
  setRadio($("format-group"), state.mode);
  renderQuality();
  updateStartButton();
  renderDest();
  refreshFound();
}

/* ---------- playlists / channel sections ---------- */

function renderGroups() {
  const groups = state.media.groups || [];
  const hasItems = (state.media.items || []).length > 0;
  $("group-tools").hidden = groups.length === 0;
  $("group-heading").textContent = hasItems ? "Playlists and sections in here" : "Choose one to open";
  $("group-list").replaceChildren(...groups.map((group) => {
    const button = el("button", { type: "button" }, [
      el("span", { text: group.title }),
      el("span", { class: "chev", text: "Open →" }),
    ]);
    button.addEventListener("click", () => openGroup(group, button));
    return el("li", {}, button);
  }));
}

async function openGroup(group, button) {
  showError("browse-error", "");
  const buttons = $("group-list").querySelectorAll("button");
  buttons.forEach((b) => { b.disabled = true; });
  const chev = button.querySelector(".chev");
  chev.textContent = "Loading…";
  try {
    const media = await api("/api/inspect", { method: "POST", body: { url: group.url, channel: state.media.channel } });
    if (!media.title || media.title === "playlist") media.title = group.title;
    state.stack.push(state.media);
    loadMedia(media);
  } catch (err) {
    showError("browse-error", err.message);
    buttons.forEach((b) => { b.disabled = false; });
    chev.textContent = "Open →";
  }
}

$("back-btn").addEventListener("click", () => {
  const previous = state.stack.pop();
  if (previous) loadMedia(previous);
});

/* ---------- playlist picker ---------- */

function renderTrackPicker() {
  const list = $("track-list");
  list.replaceChildren();
  state.media.items.forEach((item, index) => {
    const checkbox = el("input", { type: "checkbox", "data-index": String(index) });
    checkbox.checked = state.selected.has(index);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selected.add(index);
      else state.selected.delete(index);
      onSelectionChanged();
    });
    list.append(el("li", {}, el("label", {}, [
      checkbox,
      el("span", { class: "num", text: String(index + 1) }),
      el("span", { class: "name-col" }, [
        el("span", { class: "name", text: item.title }),
        el("span", { class: "have", "data-have": String(index), hidden: "" }),
      ]),
      el("span", { class: "dur", text: formatDuration(item.duration) }),
    ])));
  });
  applyFilter();
}

/* ---------- filter: show only titles containing some text ---------- */

function matchesFilter(item) {
  const words = state.filter.toLowerCase().split(/\s+/).filter(Boolean);
  const title = String(item.title || "").toLowerCase();
  return words.every((word) => title.includes(word));
}

/* Indices of the videos currently shown (all of them when no filter is typed). */
function visibleIndices() {
  const all = state.media.items.map((_, i) => i);
  return state.filter.trim() ? all.filter((i) => matchesFilter(state.media.items[i])) : all;
}

function applyFilter() {
  if (!state.media) return;
  const active = Boolean(state.filter.trim());
  const shown = new Set(visibleIndices());
  $("track-list").querySelectorAll("li").forEach((li, index) => { li.hidden = !shown.has(index); });
  $("select-all").textContent = active ? "Select shown" : "Select all";
  $("select-none").textContent = active ? "Clear shown" : "Clear";
  const note = $("filter-note");
  note.hidden = !active;
  if (active) {
    const picked = [...shown].filter((i) => state.selected.has(i)).length;
    note.textContent = `Showing ${shown.size} of ${state.media.items.length} · ${picked} of the shown ones selected. ` +
      `"Select shown" and "Clear shown" only change the videos you can see.`;
  }
}

$("filter").addEventListener("input", () => {
  state.filter = $("filter").value;
  applyFilter();
});

function syncCheckboxes() {
  for (const box of $("track-list").querySelectorAll("input[type=checkbox]")) {
    box.checked = state.selected.has(Number(box.dataset.index));
  }
}

function onSelectionChanged() {
  applyFilter();
  updateStartButton();
  if (state.mode === "mp4") renderQuality();
}

async function addToSelection(indices, replace = false) {
  const fresh = indices.filter((i) => !state.selected.has(i));
  const keep = await confirmDuplicates(fresh);
  if (keep === null) return;  // box closed: leave the selection as it was
  const kept = new Set(keep);
  state.selected = replace
    ? new Set(indices.filter((i) => state.selected.has(i) || kept.has(i)))
    : new Set([...state.selected, ...keep]);
  syncCheckboxes();
  onSelectionChanged();
}

$("select-all").addEventListener("click", () => addToSelection(visibleIndices()));

$("select-none").addEventListener("click", () => {
  if (state.filter.trim()) visibleIndices().forEach((i) => state.selected.delete(i));
  else state.selected.clear();
  syncCheckboxes();
  onSelectionChanged();
});

$("range-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("range-error", "");
  try {
    const data = await api("/api/parse-selection", {
      method: "POST",
      body: { text: $("range").value, total: state.media.items.length },
    });
    await addToSelection(data.indices, true);
  } catch (err) {
    showError("range-error", `${err.message} Try "all" or a selection like 1,3,5-10.`);
  }
});

/* ---------- options ---------- */

$("format-group").addEventListener("click", (event) => {
  const button = event.target.closest("[role=radio]");
  if (!button || button.dataset.value === state.mode) return;
  state.mode = button.dataset.value;
  setRadio($("format-group"), state.mode);
  renderQuality();
  renderBadges();
  renderDest();
});

$("quality-group").addEventListener("click", (event) => {
  const button = event.target.closest("[role=radio]");
  if (!button) return;
  state.quality = button.dataset.value;
  setRadio($("quality-group"), state.quality);
  renderBadges();
  renderDest();
});

function drawQualityButtons(values, labelFor, preferred) {
  const group = $("quality-group");
  group.replaceChildren();
  if (!values.includes(state.quality)) state.quality = preferred;
  for (const value of values) {
    group.append(el("button", {
      type: "button",
      role: "radio",
      "data-value": value,
      "aria-checked": String(value === state.quality),
      text: labelFor(value),
    }));
  }
  renderBadges();
  renderDest();
}

/* list: array = subtitles on offer, null = couldn't check, undefined = not known yet */
function renderSubtitles(list, message) {
  const fieldset = $("subs-fieldset");
  fieldset.hidden = state.mode !== "mp4";
  if (fieldset.hidden) return;

  const check = $("subs-check");
  const select = $("subs-lang");
  const previous = select.value;
  select.replaceChildren();

  if (!Array.isArray(list) || !list.length) {
    check.checked = false;
    check.disabled = true;
    select.disabled = true;
    select.hidden = true;
    $("subs-note").textContent = message
      || (list === null ? "Subtitles couldn't be checked."
        : list === undefined ? "Subtitles are checked against the first video you select."
        : "No subtitles available for this video.");
    return;
  }

  for (const track of list) {
    const option = el("option", { value: track.lang, text: `${track.name} [${track.lang}]` });
    option.dataset.auto = track.auto ? "1" : "0";
    select.append(option);
  }
  if (list.some((t) => t.lang === previous)) select.value = previous;
  check.disabled = false;
  select.hidden = false;
  select.disabled = !check.checked;
  $("subs-note").textContent = "Saved as .srt files next to the video.";
}

$("subs-check").addEventListener("change", () => { $("subs-lang").disabled = !$("subs-check").checked; });

function chosenSubtitle() {
  if (state.mode !== "mp4" || $("subs-check").disabled || !$("subs-check").checked) return null;
  const option = $("subs-lang").selectedOptions[0];
  return option ? { lang: option.value, auto: option.dataset.auto === "1" } : null;
}

async function renderQuality() {
  const note = $("quality-note");
  note.hidden = true;

  if (state.mode === "mp3") {
    $("quality-legend").textContent = "Audio quality";
    drawQualityButtons(MP3_QUALITIES, (v) => `${v} kbps`, "192");
    renderSubtitles(null);
    return;
  }

  $("quality-legend").textContent = "Maximum resolution";
  const first = [...state.selected].sort((a, b) => a - b)[0];
  if (first === undefined) {
    drawQualityButtons(FALLBACK_HEIGHTS.map(String), (v) => `${v}p`, "720");
    note.textContent = "Resolutions are checked against the first video you select.";
    note.hidden = false;
    renderSubtitles(undefined);
    return;
  }

  const url = state.media.items[first].url;
  const request = ++state.formatRequest;
  let formats = state.formatCache.get(url);

  if (!formats) {
    $("quality-group").replaceChildren(el("span", { class: "muted", text: "Checking available resolutions…" }));
    renderSubtitles(undefined, "Checking subtitles…");
    try {
      formats = await api(`/api/formats?url=${encodeURIComponent(url)}`);
      state.formatCache.set(url, formats);
    } catch (err) {
      if (request !== state.formatRequest || state.mode !== "mp4") return;
      formats = { heights: FALLBACK_HEIGHTS, subtitles: null };
      note.textContent = `${err.message} Showing common resolutions instead.`;
      note.hidden = false;
    }
  }
  if (request !== state.formatRequest || state.mode !== "mp4") return;

  const values = formats.heights.map(String);
  const sensible = values.filter((v) => Number(v) <= 1080);
  const preferred = values.includes("720") ? "720" : (sensible.at(-1) || values.at(-1));
  drawQualityButtons(values, (v) => `${v}p`, preferred);
  renderSubtitles(formats.subtitles);
}

function updateStartButton() {
  const count = state.selected.size;
  const button = $("start-btn");
  button.disabled = count === 0;
  if (!state.media.is_playlist) button.textContent = "Download";
  else if (!count) button.textContent = "Select videos to download";
  else {
    const picked = [...state.selected].map((i) => state.media.items[i].duration || 0);
    const total = picked.reduce((a, b) => a + b, 0);
    const partial = picked.some((d) => !d) ? "+" : "";
    button.textContent = `Download ${plural(count, "video")}` + (total ? ` (${formatDuration(total)}${partial})` : "");
  }
}

/* ---------- start job ---------- */

$("start-btn").addEventListener("click", async () => {
  showError("start-error", "");
  let indices = [...state.selected].sort((a, b) => a - b);
  if (!indices.length) return;

  $("start-btn").disabled = true;
  try {
    const kept = await confirmDuplicates(indices);
    if (kept === null) { updateStartButton(); return; }  // box closed: nothing started
    if (kept.length !== indices.length) {
      indices = kept;
      state.selected = new Set(kept);
      if (state.media.is_playlist) syncCheckboxes();
    }
    if (!indices.length) {
      showError("start-error", "Nothing left to download: everything selected was already downloaded.");
      updateStartButton();
      return;
    }
    const job = await api("/api/jobs", {
      method: "POST",
      body: {
        title: state.media.title,
        is_playlist: state.media.is_playlist,
        items: indices.map((i) => state.media.items[i]),
        mode: state.mode,
        quality: state.quality,
        channel: state.media.channel,
        section: state.media.section,
        subtitle: chosenSubtitle(),
      },
    });
    trackJob(job.id);
  } catch (err) {
    showError("start-error", err.message);
    updateStartButton();
  }
});

/* ---------- job progress ---------- */

function trackJob(jobId) {
  state.jobId = jobId;
  state.autoDownloaded = false;
  try { localStorage.setItem("mediaDownloaderJob", jobId); } catch (err) { /* storage unavailable */ }

  $("media-card").hidden = true;
  $("options-card").hidden = true;
  $("job-card").hidden = false;
  clearInterval(state.pollTimer);
  poll();
  state.pollTimer = setInterval(poll, 1000);
}

async function poll() {
  let job;
  try {
    job = await api(`/api/jobs/${state.jobId}`);
  } catch (err) {
    clearInterval(state.pollTimer);
    if (err.status === 404) {
      forgetJob();
      resetToStart();
    } else {
      showError("job-error", err.message);
    }
    return;
  }
  renderJob(job);
  if (["done", "failed", "cancelled"].includes(job.state)) clearInterval(state.pollTimer);
}

const STATUS_TEXT = {
  pending: "Waiting",
  downloading: "Downloading",
  processing: "Converting",
  done: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
};

function renderJob(job) {
  const finished = ["done", "failed", "cancelled"].includes(job.state);

  $("job-title").textContent = job.title;
  $("job-message").textContent = job.message;
  const bar = $("job-bar");
  bar.setAttribute("aria-valuenow", String(Math.round(job.percent)));
  bar.firstElementChild.style.width = `${job.percent}%`;

  const stats = [];
  if (job.state === "running" && job.speed) {
    stats.push(formatSpeed(job.speed));
    if (job.eta !== null && job.eta !== undefined) stats.push(`${formatEta(job.eta)} left for this item`);
  }
  if (job.downloaded_bytes) stats.push(`${formatSize(job.downloaded_bytes)} downloaded`);
  $("job-stats").textContent = stats.join(" · ");
  $("job-stats").hidden = stats.length === 0;

  const list = $("job-tracks");
  list.hidden = job.tracks.length < 2;
  list.replaceChildren(...job.tracks.map((track, index) => {
    const item = el("li", { class: track.status }, [
      el("span", { class: "fill", style: `width:${track.percent || 0}%` }),
      el("div", { class: "row" }, [
        el("span", { class: "num", text: String(index + 1) }),
        el("span", { class: "name", text: track.title }),
        el("span", { class: "state", text: trackDetail(track) }),
      ]),
    ]);
    if (track.error) item.append(el("div", { class: "err", text: track.error }));
    return item;
  }));

  showError("job-error", job.error);

  $("job-folder").hidden = false;
  $("job-folder").textContent = `${finished ? "Saved to" : "Saving to"}: ${job.folder}`;
  $("job-files").replaceChildren(...job.files.map((file) => el("li", {}, [
    el("span", { text: file.name }),
    el("span", { class: "size", text: formatSize(file.size) }),
  ])));

  $("cancel-btn").hidden = finished;
  $("reset-btn").hidden = !finished;

}

$("cancel-btn").addEventListener("click", async () => {
  $("cancel-btn").disabled = true;
  try { await api(`/api/jobs/${state.jobId}/cancel`, { method: "POST" }); } catch (err) { /* poll will report */ }
  $("cancel-btn").disabled = false;
});

$("reset-btn").addEventListener("click", async () => {
  try { await api(`/api/jobs/${state.jobId}`, { method: "DELETE" }); } catch (err) { /* already gone */ }
  forgetJob();
  resetToStart();
});

function forgetJob() {
  state.jobId = null;
  try { localStorage.removeItem("mediaDownloaderJob"); } catch (err) { /* ignore */ }
}

function resetToStart() {
  clearInterval(state.pollTimer);
  $("job-card").hidden = true;
  $("media-card").hidden = true;
  $("options-card").hidden = true;
  $("url").value = "";
  $("url").focus();
}

/* ---------- settings ---------- */

function showConfig(config) {
  state.config = config;
  $("footer-dir").textContent = config.download_dir;
  renderDest();
}

$("settings-btn").addEventListener("click", () => {
  const c = state.config;
  $("set-dir").value = c.download_dir || "";
  $("set-proxy").value = c.proxy || "";
  $("set-cookies").value = c.cookies_from_browser || "";
  $("set-cookie-file").value = c.cookie_file || "";
  $("set-jobs").value = c.max_concurrent_jobs || 1;
  $("set-ttl").value = c.job_ttl_minutes || 60;
  showError("settings-error", "");
  $("settings-dialog").showModal();
});

$("settings-cancel").addEventListener("click", () => $("settings-dialog").close());

$("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("settings-error", "");
  $("settings-save").disabled = true;
  try {
    const config = await api("/api/settings", {
      method: "POST",
      body: {
        download_dir: $("set-dir").value,
        proxy: $("set-proxy").value,
        cookies_from_browser: $("set-cookies").value,
        cookie_file: $("set-cookie-file").value,
        max_concurrent_jobs: $("set-jobs").value,
        job_ttl_minutes: $("set-ttl").value,
      },
    });
    showConfig(config);
    $("settings-dialog").close();
    loadRegion();
    if (state.media) refreshFound();  // the folder may have changed
  } catch (err) {
    showError("settings-error", err.message);
  } finally {
    $("settings-save").disabled = false;
  }
});

/* ---------- library ---------- */

$("library-btn").addEventListener("click", async () => {
  const body = $("library-body");
  body.replaceChildren(el("p", { class: "muted", text: "Loading…" }));
  $("library-summary").textContent = "";
  $("library-dialog").showModal();
  try {
    const lib = await api("/api/library");
    $("library-summary").textContent =
      `${lib.root} · ${plural(lib.count, "file")} · ${formatSize(lib.size)}`;
    if (!lib.folders.length) {
      body.replaceChildren(el("p", { class: "muted", text: "Nothing downloaded yet." }));
      return;
    }
    body.replaceChildren(...lib.folders.map((group) => el("details", {}, [
      el("summary", { text: `${group.folder}  (${plural(group.count, "file")}, ${formatSize(group.size)})` }),
      el("ul", {}, group.files.map((f) => el("li", {}, [
        el("span", { text: f.name + "  " }),
        el("span", { class: "size", text: formatSize(f.size) }),
      ]))),
    ])));
  } catch (err) {
    body.replaceChildren(el("p", { class: "notice error", text: err.message }));
  }
});

$("library-close").addEventListener("click", () => $("library-dialog").close());

/* ---------- boot ---------- */

(async function init() {
  try {
    loadRegion();
    showConfig(await api("/api/config"));
  } catch (err) { /* keep default text */ }

  let saved = null;
  try { saved = localStorage.getItem("mediaDownloaderJob"); } catch (err) { /* ignore */ }
  if (saved) trackJob(saved);
})();
