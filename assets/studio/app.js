"use strict";
const $ = (selector) => document.querySelector(selector);
const state = {
  token: "",
  config: null,
  id: null,
  detail: null,
  words: [],
  page: 0,
  busy: false,
  tab: "clips",
  selected: new Set(),
  noticeTimer: null,
};
const escapeHTML = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const basename = (source) =>
  source.split(/[\\/]/).filter(Boolean).pop() || source;
const projectName = (source) =>
  /^https?:/.test(source)
    ? source.replace(/^https?:\/\//, "").slice(0, 65)
    : basename(source);
function notice(message, error = false) {
  clearTimeout(state.noticeTimer);
  const box = $("#notice");
  box.hidden = false;
  box.textContent = message;
  box.classList.toggle("error", error);
  state.noticeTimer = setTimeout(
    () => {
      box.hidden = true;
    },
    error ? 15000 : 6000,
  );
}
async function api(path, data) {
  const response = await fetch(
    path,
    data === undefined
      ? {}
      : {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Chopify-Token": state.token,
          },
          body: JSON.stringify(data),
        },
  );
  const result = await response.json();
  if (!response.ok)
    throw new Error(result.error || `Request failed (${response.status})`);
  return result;
}
function safe(handler) {
  return async (event) => {
    try {
      await handler(event);
    } catch (error) {
      notice(error.message, true);
    }
  };
}
async function config() {
  state.config = await api("/api/config");
  state.token = state.config.token;
  updateBrands();
}
function updateBrands(selected = "") {
  $("#brand-select").innerHTML =
    '<option value="">Use caption style</option>' +
    state.config.brands
      .map(
        (name) =>
          `<option value="${escapeHTML(name)}">${escapeHTML(name)}</option>`,
      )
      .join("");
  $("#brand-select").value = selected;
}
async function refreshProjects() {
  const result = await api("/api/projects");
  $("#project-count").textContent = result.projects.length;
  const markup = result.projects.length
    ? result.projects
        .map(
          (p) =>
            `<button class="project-item ${p.id === state.id ? "active" : ""}" data-id="${p.id}"><strong>${escapeHTML(projectName(p.source))}</strong><small>${escapeHTML(p.status)} · ${escapeHTML(p.stage)}</small></button>`,
        )
        .join("")
    : '<p class="muted">Add a recording to begin.</p>';
  // Preserve keyboard focus while polling unchanged projects.
  if (state.projectMarkup !== markup) {
    $("#projects").innerHTML = markup;
    state.projectMarkup = markup;
  }
}
async function selectProject(id) {
  state.id = id;
  state.page = 0;
  state.tab = "clips";
  $("#new-view").hidden = true;
  $("#project-view").hidden = false;
  const detail = await api(`/api/projects/${id}`);
  if (state.id !== id) return;
  state.detail = detail;
  state.words = structuredClone(state.detail.transcript?.words || []);
  state.selected = new Set(state.detail.candidates.map((_, i) => i + 1));
  renderDetail();
  await refreshProjects();
}
function updateStatus(detail) {
  $("#status").textContent =
    `${detail.project.status} · ${detail.project.stage}`;
  $("#job-error").hidden = !detail.project.error;
  $("#job-error").textContent = detail.project.error || "";
  $("#log").textContent = detail.log || "No job has run yet.";
  state.busy = detail.busy;
  for (const selector of [
    "#prepare",
    "#preview",
    "#render",
    "#save-candidates",
    "#save-transcript",
    "#settings-form button",
  ])
    $(selector).disabled = detail.busy;
  for (const el of document.querySelectorAll(
    "#settings-form input, #settings-form select, #candidates input, #transcript-words input",
  ))
    el.disabled = detail.busy;
}
function renderDetail() {
  const detail = state.detail,
    p = detail.project,
    options = p.options;
  $("#project-id").textContent = `PROJECT / ${p.id}`;
  $("#project-title").textContent = projectName(p.source);
  $("#project-source").textContent = p.source;
  const player = $("#source-player");
  player.hidden = !detail.transcript;
  $("#player-hint").hidden = !!detail.transcript;
  const source = `/media/${p.id}/source`;
  if (detail.transcript && player.getAttribute("src") !== source)
    player.src = source;
  const form = $("#settings-form");
  for (const [key, value] of Object.entries(options)) {
    const input = form.elements.namedItem(key);
    if (input) {
      if (input.type === "checkbox") input.checked = value;
      else input.value = value ?? "";
    }
  }
  form.elements.center1.value = options.centers[0];
  form.elements.center2.value = options.centers[1];
  const brand = options.brand
    ? basename(options.brand).replace(/\.json$/, "")
    : "";
  updateBrands(brand);
  $("#search-mode").value = options.semantic
    ? "semantic"
    : options.search
      ? "search"
      : "auto";
  $("#query").value = options.semantic || options.search || "";
  $("#candidate-count").textContent = detail.candidates.length;
  $("#candidates").innerHTML = detail.candidates.length
    ? detail.candidates
        .map(
          (c, i) =>
            `<article class="candidate" data-index="${i}"><input type="checkbox" aria-label="Select clip ${i + 1}" data-select="${i + 1}" ${state.selected.has(i + 1) ? "checked" : ""}><div><div class="candidate-title"><input aria-label="Clip ${i + 1} title" data-field="hook" value="${escapeHTML(c.hook)}"></div><div class="candidate-meta">CLIP ${String(i + 1).padStart(2, "0")} · SCORE ${Number(c.overall).toFixed(1)}/10${c.similarity !== undefined ? " · SEMANTIC " + Number(c.similarity).toFixed(2) : ""}</div><div class="candidate-time"><label>Start (seconds)<input data-field="start" type="number" min="0" step="0.01" value="${c.start}"></label><label>End (seconds)<input data-field="end" type="number" min="0" step="0.01" value="${c.end}"></label></div></div><button data-play="${i}">▶ Watch</button></article>`,
        )
        .join("")
    : '<p class="muted">Find clips to see candidates here. You can adjust selection and timing before export.</p>';
  renderWords();
  updateSelection();
  renderOutputs(detail);
  updateStatus(detail);
  setTab(state.tab);
}
function setTab(tab) {
  state.tab = tab;
  $("#clip-panel").hidden = tab !== "clips";
  $("#transcript-panel").hidden = tab !== "transcript";
  $("#show-clips").setAttribute("aria-pressed", String(tab === "clips"));
  $("#show-transcript").setAttribute(
    "aria-pressed",
    String(tab === "transcript"),
  );
}
function updateSelection() {
  $("#selection-count").textContent = `${state.selected.size} selected`;
}
function renderWords() {
  const start = state.page * 100,
    visible = state.words.slice(start, start + 100);
  $("#transcript-words").innerHTML = visible.length
    ? '<div class="word-header"><span></span><span>WORD</span><span>START (S)</span><span>END (S)</span></div>' +
      visible
        .map(
          (word, i) =>
            `<div class="word-row" data-word="${start + i}"><button data-seek="${start + i}" aria-label="Play word ${start + i + 1}">${start + i + 1}</button><input aria-label="Word ${start + i + 1}" data-word-field="word" value="${escapeHTML(word.word)}"><input aria-label="Word ${start + i + 1} start" data-word-field="start" type="number" min="0" step="0.01" value="${word.start}"><input aria-label="Word ${start + i + 1} end" data-word-field="end" type="number" min="0" step="0.01" value="${word.end}"></div>`,
        )
        .join("")
    : '<p class="muted">Prepare the project to get an editable transcript.</p>';
  $("#word-page").textContent =
    `${state.words.length ? start + 1 : 0}–${Math.min(start + 100, state.words.length)} of ${state.words.length} words`;
  $("#words-prev").disabled = state.page === 0;
  $("#words-next").disabled = start + 100 >= state.words.length;
  for (const el of document.querySelectorAll("#transcript-words input"))
    el.disabled = state.busy;
}
function renderOutputs(detail) {
  const videos = detail.files.filter((f) => f.name.endsWith(".mp4"));
  $("#output-count").textContent = `${videos.length} CLIPS`;
  $("#outputs").className = videos.length ? "output-grid" : "";
  $("#outputs").innerHTML = videos.length
    ? videos
        .map((file) => {
          const stem = file.name.slice(0, -4),
            related = detail.files.filter(
              (f) =>
                f.kind === file.kind &&
                [stem + ".srt", stem + ".png", stem + ".meta.json"].includes(
                  f.name,
                ),
            );
          return `<article class="output-card"><video controls preload="none" src="${encodeURI(file.url)}"></video><div class="output-info"><p>${escapeHTML(file.kind === "previews" ? "DRAFT · " : "EXPORT · ")}${escapeHTML(stem)}</p><div class="output-links"><a href="${encodeURI(file.url)}" download>MP4 ↓</a>${related.map((f) => `<a href="${encodeURI(f.url)}" download>${f.name.endsWith(".srt") ? "SRT" : f.name.endsWith(".png") ? "POSTER" : "METADATA"} ↓</a>`).join("")}</div></div></article>`;
        })
        .join("")
    : '<p class="muted">Choose candidates and make a preview or export.</p>';
}
async function saveCandidates(showNotice = true) {
  if (!state.detail.candidates.length)
    throw new Error("Find clips before exporting.");
  await api(`/api/projects/${state.id}/candidates`, {
    candidates: state.detail.candidates,
  });
  if (showNotice) notice("Clip edits saved.");
}
async function run(action, preview = false) {
  if (action === "render") {
    if (!state.selected.size) throw new Error("Select at least one clip.");
    await saveCandidates(false);
  }
  await api(`/api/projects/${state.id}/run`, {
    action,
    preview,
    pick:
      action === "render"
        ? [...state.selected].sort((a, b) => a - b).join(",")
        : null,
  });
  state.detail.busy = true;
  state.detail.project.status = "queued";
  state.detail.project.stage = action;
  updateStatus(state.detail);
  notice(
    action === "prepare"
      ? "Queued. Saved stages will be reused when their inputs match."
      : "Queued selected clips.",
  );
  await refreshProjects();
}
$("#new-project").addEventListener("click", () => {
  state.id = null;
  $("#new-view").hidden = false;
  $("#project-view").hidden = true;
  $("#sources").focus();
  refreshProjects().catch((e) => notice(e.message, true));
});
$("#projects").addEventListener(
  "click",
  safe(async (event) => {
    const button = event.target.closest("[data-id]");
    if (button) await selectProject(button.dataset.id);
  }),
);
$("#create-form").addEventListener(
  "submit",
  safe(async (event) => {
    event.preventDefault();
    const sources = $("#sources")
      .value.split(/\r?\n/)
      .map((s) => s.trim().replace(/^"|"$/g, ""))
      .filter(Boolean);
    if (!sources.length) throw new Error("Add a video path or URL.");
    const button = event.target.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      const options = {
        aspect: $("#new-aspect").value,
        whisper_model: $("#new-model").value,
        style: $("#new-style").value,
      };
      const result = await api("/api/batch", { sources, options });
      await selectProject(result.projects[0].id);
      notice(
        `${sources.length} project${sources.length === 1 ? "" : "s"} queued for review.`,
      );
      $("#sources").value = "";
    } finally {
      button.disabled = false;
    }
  }),
);
$("#settings-form").addEventListener(
  "submit",
  safe(async (event) => {
    event.preventDefault();
    const form = event.target,
      values = Object.fromEntries(new FormData(form));
    for (const key of ["min_len", "max_len", "max_clips"])
      values[key] = Number(values[key]);
    values.min_score =
      values.min_score === "" ? null : Number(values.min_score);
    values.tighten = form.elements.tighten.checked;
    values.loudnorm = form.elements.loudnorm.checked;
    values.centers = [Number(values.center1), Number(values.center2)];
    delete values.center1;
    delete values.center2;
    values.llm = values.llm.trim() || null;
    values.search = null;
    values.semantic = null;
    const mode = $("#search-mode").value;
    if (mode !== "auto") {
      if (!$("#query").value.trim())
        throw new Error("Enter a topic or keywords.");
      values[mode] = $("#query").value.trim();
    }
    await api(`/api/projects/${state.id}/settings`, values);
    notice(
      "Settings saved. Find clips again for search changes, or export with your new style.",
    );
  }),
);
$("#brand-form").addEventListener(
  "submit",
  safe(async (event) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.target));
    const name = values.name;
    delete values.name;
    for (const key of ["font_size", "margin", "logo_width"])
      values[key] = Number(values[key]);
    values.logo = values.logo.trim() || null;
    await api("/api/brands", { name, preset: values });
    await config();
    $("#brand-select").value = name;
    notice("Brand preset saved. Save settings to apply it to this project.");
  }),
);
$("#candidates").addEventListener("input", (event) => {
  const selected = event.target.dataset.select;
  if (selected) {
    event.target.checked
      ? state.selected.add(Number(selected))
      : state.selected.delete(Number(selected));
    updateSelection();
  }
  const field = event.target.dataset.field;
  if (field) {
    const i = Number(event.target.closest("[data-index]").dataset.index);
    state.detail.candidates[i][field] =
      field === "hook" ? event.target.value : Number(event.target.value);
  }
});
let stopAt = null;
async function play(start, end = null) {
  const player = $("#source-player");
  stopAt = end;
  player.currentTime = start;
  await player.play();
}
$("#source-player").addEventListener("timeupdate", () => {
  if (stopAt !== null && $("#source-player").currentTime >= stopAt) {
    $("#source-player").pause();
    stopAt = null;
  }
});
$("#candidates").addEventListener(
  "click",
  safe(async (event) => {
    const button = event.target.closest("[data-play]");
    if (button) {
      const c = state.detail.candidates[Number(button.dataset.play)];
      await play(c.start, c.end);
    }
  }),
);
$("#transcript-words").addEventListener("input", (event) => {
  const field = event.target.dataset.wordField;
  if (field) {
    const i = Number(event.target.closest("[data-word]").dataset.word);
    state.words[i][field] =
      field === "word" ? event.target.value : Number(event.target.value);
  }
});
$("#transcript-words").addEventListener(
  "click",
  safe(async (event) => {
    const button = event.target.closest("[data-seek]");
    if (button) await play(state.words[Number(button.dataset.seek)].start);
  }),
);
$("#words-prev").addEventListener("click", () => {
  state.page--;
  renderWords();
});
$("#words-next").addEventListener("click", () => {
  state.page++;
  renderWords();
});
$("#show-clips").addEventListener("click", () => setTab("clips"));
$("#show-transcript").addEventListener("click", () => setTab("transcript"));
$("#prepare").addEventListener(
  "click",
  safe(() => run("prepare")),
);
$("#preview").addEventListener(
  "click",
  safe(() => run("render", true)),
);
$("#render").addEventListener(
  "click",
  safe(() => run("render")),
);
$("#save-candidates").addEventListener(
  "click",
  safe(() => saveCandidates()),
);
$("#save-transcript").addEventListener(
  "click",
  safe(async () => {
    await api(`/api/projects/${state.id}/transcript`, { words: state.words });
    notice("Caption edits saved. Export clips to apply them.");
  }),
);
let polling = false;
setInterval(async () => {
  if (polling) return;
  polling = true;
  try {
    await refreshProjects();
    if (state.id && state.busy) {
      const id = state.id,
        detail = await api(`/api/projects/${id}`);
      if (id !== state.id) return;
      const finished = !detail.busy;
      updateStatus(detail);
      if (finished) {
        state.detail = detail;
        state.words = structuredClone(detail.transcript?.words || []);
        state.selected = new Set(detail.candidates.map((_, i) => i + 1));
        renderDetail();
        notice(
          detail.project.error || "Project ready for review.",
          !!detail.project.error,
        );
      }
    }
    $("#connection").textContent = "● Local session";
  } catch (error) {
    $("#connection").textContent = "Connection lost · restart Studio";
  } finally {
    polling = false;
  }
}, 2000);
safe(async () => {
  await config();
  await refreshProjects();
})();
