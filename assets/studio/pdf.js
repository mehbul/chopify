"use strict";
const pdfEl = (selector) => document.querySelector(selector);
const pdfState = {
  token: "",
  config: null,
  pages: [],
  selected: null,
  options: {},
  busy: false,
  previewTimer: null,
  previewRequest: 0,
  previewController: null,
  previewUrl: null,
  dragKey: null,
};
const pdfEscape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const pdfSafe = (handler) => async (event) => {
  try {
    await handler(event);
  } catch (error) {
    pdfNotice(error.message, true);
  }
};
function pdfNotice(message, error = false) {
  const box = pdfEl("#pdf-notice");
  box.hidden = false;
  box.textContent = message;
  box.classList.toggle("error", error);
}
async function pdfApi(path, body) {
  const response = await fetch(
    path,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Chopify-Token": pdfState.token,
          },
          body: JSON.stringify(body),
        },
  );
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "PDF request failed.");
  return result;
}
function pdfPage() {
  return pdfState.pages.find((page) => page.key === pdfState.selected);
}
function pdfWirePages() {
  return pdfState.pages.map(({ asset, settings }) => ({ asset, settings }));
}
function pdfAsset(id) {
  return pdfState.config.assets.find((asset) => asset.id === id);
}
function pdfDirty() {
  pdfEl("#pdf-result").hidden = true;
  try {
    localStorage.setItem(
      "chopify.pdf.draft.v1",
      JSON.stringify({
        pages: pdfState.pages,
        options: pdfState.options,
        selected: pdfState.selected,
      }),
    );
  } catch (_) {
    /* Storage can be disabled; export still works. */
  }
}
function pdfBusy(value, message = "") {
  pdfState.busy = value;
  for (const item of document.querySelectorAll(
    "main button, main input, main select",
  ))
    item.disabled = value;
  pdfEl("#pdf-progress").hidden = !message;
  pdfEl("#pdf-progress").textContent = message;
  pdfEl("#pdf-export").disabled = value || !pdfState.pages.length;
  pdfEl("#pdf-image-fields").disabled = value || !pdfPage();
  for (const item of document.querySelectorAll(
    "#pdf-history button, #pdf-library button",
  ))
    item.disabled = value;
  for (const item of document.querySelectorAll(".pdf-page"))
    item.draggable = !value;
  const cards = Array.from(document.querySelectorAll(".pdf-page"));
  cards.forEach((card, index) => {
    card.querySelector('[data-action="up"]').disabled = value || index === 0;
    card.querySelector('[data-action="down"]').disabled =
      value || index === cards.length - 1;
  });
}
function pdfRenderPages() {
  pdfEl("#pdf-count").textContent = `${pdfState.pages.length} PAGES`;
  pdfEl("#pdf-pages").innerHTML = pdfState.pages.length
    ? pdfState.pages
        .map((page, index) => {
          const asset = pdfAsset(page.asset);
          return `<article class="pdf-page ${page.key === pdfState.selected ? "selected" : ""}" data-key="${page.key}" draggable="true"><button class="pdf-page-select" data-action="select" aria-label="Select page ${index + 1}: ${pdfEscape(asset.name)}"><img src="/api/pdf/assets/${page.asset}/thumbnail" alt="" loading="lazy"><span class="pdf-page-title">${index + 1}. ${pdfEscape(asset.name)}</span><span class="pdf-page-meta">${asset.width} × ${asset.height} · ${pdfEscape(page.settings.filter)}</span></button><div class="pdf-page-actions"><button data-action="up" aria-label="Move page ${index + 1} up" ${index === 0 ? "disabled" : ""}>↑</button><button data-action="down" aria-label="Move page ${index + 1} down" ${index === pdfState.pages.length - 1 ? "disabled" : ""}>↓</button><button data-action="duplicate" aria-label="Duplicate page ${index + 1}">＋</button><button data-action="remove" aria-label="Remove page ${index + 1}">×</button></div></article>`;
        })
        .join("")
    : '<p class="muted">Add pictures to start your document.</p>';
  pdfEl("#pdf-export").disabled = pdfState.busy || !pdfState.pages.length;
  pdfSyncImageForm();
  pdfSchedulePreview();
}
function pdfSyncImageForm() {
  const page = pdfPage(),
    fields = pdfEl("#pdf-image-fields");
  fields.disabled = !page || pdfState.busy;
  pdfEl("#pdf-image-hint").textContent = page
    ? `Adjusting page ${pdfState.pages.indexOf(page) + 1} · ${pdfAsset(page.asset).name}`
    : "Select a page to adjust it.";
  if (!page) return;
  const form = pdfEl("#pdf-image-form");
  for (const [name, value] of Object.entries(page.settings)) {
    const input = form.elements.namedItem(name);
    if (input) {
      if (input.type === "checkbox") input.checked = value;
      else input.value = value;
    }
  }
  ["left", "top", "right", "bottom"].forEach((name, i) => {
    form.elements.namedItem(`crop_${name}`).value = page.settings.crop[i];
  });
  pdfSliderValues();
}
function pdfSyncDocumentForm() {
  const form = pdfEl("#pdf-document-form");
  for (const [name, value] of Object.entries(pdfState.options)) {
    const input = form.elements.namedItem(name);
    if (input) {
      if (input.type === "checkbox") input.checked = value;
      else input.value = value;
    }
  }
  pdfSliderValues();
}
function pdfSliderValues() {
  for (const name of [
    "brightness",
    "contrast",
    "saturation",
    "sharpness",
    "threshold",
    "quality",
  ]) {
    const input = document.querySelector(`[name="${name}"]`),
      output = pdfEl(`#pdf-${name}-value`);
    output.value = ["threshold", "quality"].includes(name)
      ? input.value
      : Number(input.value).toFixed(2);
  }
}
function pdfSchedulePreview() {
  clearTimeout(pdfState.previewTimer);
  pdfState.previewController?.abort();
  pdfState.previewRequest++;
  const page = pdfPage();
  pdfEl("#pdf-preview-label").textContent = page
    ? `PAGE ${pdfState.pages.indexOf(page) + 1} OF ${pdfState.pages.length}`
    : "SELECT A PAGE";
  pdfEl("#pdf-preview").hidden = true;
  pdfEl("#pdf-preview-empty").hidden = false;
  pdfEl("#pdf-preview-empty").textContent = page
    ? "Updating preview…"
    : "Your selected page will appear here.";
  if (!page) return;
  pdfState.previewTimer = setTimeout(pdfSafe(pdfUpdatePreview), 350);
}
async function pdfUpdatePreview() {
  const page = pdfPage();
  if (!page) return;
  const requestId = pdfState.previewRequest,
    controller = new AbortController();
  pdfState.previewController = controller;
  try {
    const response = await fetch("/api/pdf/preview", {
      method: "POST",
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        "X-Chopify-Token": pdfState.token,
      },
      body: JSON.stringify({
        page: { asset: page.asset, settings: page.settings },
        options: pdfState.options,
        index: pdfState.pages.indexOf(page) + 1,
        total: pdfState.pages.length,
      }),
    });
    if (!response.ok)
      throw new Error((await response.json()).error || "Preview failed.");
    const blob = await response.blob();
    if (requestId !== pdfState.previewRequest) return;
    if (pdfState.previewUrl) URL.revokeObjectURL(pdfState.previewUrl);
    pdfState.previewUrl = URL.createObjectURL(blob);
    pdfEl("#pdf-preview").src = pdfState.previewUrl;
    pdfEl("#pdf-preview").hidden = false;
    pdfEl("#pdf-preview-empty").hidden = true;
  } catch (error) {
    if (error.name === "AbortError" || requestId !== pdfState.previewRequest)
      return;
    pdfEl("#pdf-preview-empty").textContent = error.message;
    pdfNotice(error.message, true);
  }
}
function pdfAddAsset(asset) {
  if (pdfState.pages.length >= 100)
    throw new Error("A PDF can contain up to 100 pages.");
  const page = {
    asset: asset.id,
    settings: structuredClone(pdfState.config.image_defaults),
    key: crypto.randomUUID(),
  };
  pdfState.pages.push(page);
  pdfState.selected = page.key;
}
function pdfRenderLibrary() {
  pdfEl("#pdf-library").innerHTML = pdfState.config.assets.length
    ? pdfState.config.assets
        .map(
          (asset) =>
            `<button type="button" data-asset="${asset.id}" title="Add image to document">＋ ${pdfEscape(asset.name)}</button>`,
        )
        .join("")
    : '<p class="hint">Uploaded images stay available here.</p>';
}
async function pdfUpload(files) {
  if (pdfState.busy || !files.length) return;
  if (pdfState.pages.length + files.length > 100)
    throw new Error("Add at most 100 pages per document.");
  pdfBusy(true, "Uploading images to your local workspace…");
  try {
    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      if (file.size > 20 * 1024 * 1024)
        throw new Error(`${file.name} is larger than 20 MB.`);
      pdfEl("#pdf-progress").textContent =
        `Adding ${i + 1} of ${files.length}: ${file.name}`;
      const response = await fetch("/api/pdf/assets", {
        method: "POST",
        headers: {
          "Content-Type": "application/octet-stream",
          "X-Chopify-Token": pdfState.token,
          "X-File-Name": encodeURIComponent(file.name),
        },
        body: file,
      });
      const asset = await response.json();
      if (!response.ok) throw new Error(asset.error || "Upload failed.");
      pdfState.config.assets.push(asset);
      pdfAddAsset(asset);
      pdfDirty();
    }
    pdfNotice(
      `${files.length} picture${files.length === 1 ? "" : "s"} added in selection order. Reorder pages before export if needed.`,
    );
  } finally {
    pdfBusy(false);
    pdfRenderPages();
    pdfRenderLibrary();
    pdfEl("#pdf-files").value = "";
  }
}
async function pdfRefreshHistory() {
  const config = await pdfApi("/api/pdf/config");
  pdfState.config.history = config.history;
  pdfEl("#pdf-history-count").textContent = config.history.length;
  pdfEl("#pdf-history").innerHTML = config.history.length
    ? config.history
        .map(
          (item) =>
            `<button data-document="${item.id}"><strong>${pdfEscape(item.title)}</strong><small>${item.count} PAGES · ${(item.size / 1024 / 1024).toFixed(1)} MB</small></button>`,
        )
        .join("")
    : '<p class="muted">Your saved PDFs appear here.</p>';
}
function pdfShowResult(result) {
  pdfEl("#pdf-result").hidden = false;
  pdfEl("#pdf-result-info").textContent =
    `${result.filename} · ${result.count} pages · ${(result.size / 1024 / 1024).toFixed(2)} MB`;
  pdfEl("#pdf-download").href = `/api/pdf/documents/${result.id}/download`;
  pdfEl("#pdf-download").download = result.filename;
}
pdfEl("#pdf-add").addEventListener("click", () => pdfEl("#pdf-files").click());
pdfEl("#pdf-files").addEventListener(
  "change",
  pdfSafe((event) => pdfUpload(Array.from(event.target.files))),
);
const pdfDrop = pdfEl("#pdf-drop");
pdfDrop.addEventListener("dragover", (event) => {
  event.preventDefault();
  pdfDrop.classList.add("drag-over");
});
pdfDrop.addEventListener("dragleave", () =>
  pdfDrop.classList.remove("drag-over"),
);
pdfDrop.addEventListener(
  "drop",
  pdfSafe(async (event) => {
    event.preventDefault();
    pdfDrop.classList.remove("drag-over");
    await pdfUpload(Array.from(event.dataTransfer.files));
  }),
);
// Keep a dropped file from navigating away from the document outside the drop area.
document.addEventListener("dragover", (event) => {
  if (event.dataTransfer.types.includes("Files")) event.preventDefault();
});
document.addEventListener("drop", (event) => {
  if (event.dataTransfer.files.length) event.preventDefault();
});
pdfEl("#pdf-pages").addEventListener(
  "click",
  pdfSafe((event) => {
    if (pdfState.busy) return;
    const button = event.target.closest("[data-action]"),
      card = event.target.closest("[data-key]");
    if (!button || !card) return;
    const index = pdfState.pages.findIndex(
        (page) => page.key === card.dataset.key,
      ),
      page = pdfState.pages[index];
    switch (button.dataset.action) {
      case "select":
        pdfState.selected = page.key;
        break;
      case "up":
        if (index > 0)
          [pdfState.pages[index - 1], pdfState.pages[index]] = [
            page,
            pdfState.pages[index - 1],
          ];
        break;
      case "down":
        if (index < pdfState.pages.length - 1)
          [pdfState.pages[index], pdfState.pages[index + 1]] = [
            pdfState.pages[index + 1],
            page,
          ];
        break;
      case "duplicate":
        if (pdfState.pages.length >= 100)
          throw new Error("A PDF can contain up to 100 pages.");
        {
          const duplicate = {
            ...structuredClone(page),
            key: crypto.randomUUID(),
          };
          pdfState.pages.splice(index + 1, 0, duplicate);
          pdfState.selected = duplicate.key;
        }
        break;
      case "remove":
        pdfState.pages.splice(index, 1);
        if (pdfState.selected === page.key)
          pdfState.selected =
            pdfState.pages[Math.min(index, pdfState.pages.length - 1)]?.key ||
            null;
        break;
    }
    pdfDirty();
    pdfRenderPages();
  }),
);
pdfEl("#pdf-pages").addEventListener("dragstart", (event) => {
  if (pdfState.busy) {
    event.preventDefault();
    return;
  }
  pdfState.dragKey = event.target.closest("[data-key]")?.dataset.key;
  if (pdfState.dragKey) {
    event.dataTransfer.setData("text/plain", pdfState.dragKey);
    event.dataTransfer.effectAllowed = "move";
  }
});
pdfEl("#pdf-pages").addEventListener("dragover", (event) => {
  if (pdfState.dragKey) {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  }
});
pdfEl("#pdf-pages").addEventListener("drop", (event) => {
  if (pdfState.busy || !pdfState.dragKey) return;
  event.preventDefault();
  const target = event.target.closest("[data-key]");
  if (!target || target.dataset.key === pdfState.dragKey) return;
  const from = pdfState.pages.findIndex(
      (page) => page.key === pdfState.dragKey,
    ),
    to = pdfState.pages.findIndex((page) => page.key === target.dataset.key);
  const [page] = pdfState.pages.splice(from, 1);
  pdfState.pages.splice(to, 0, page);
  pdfState.dragKey = null;
  pdfDirty();
  pdfRenderPages();
});
pdfEl("#pdf-pages").addEventListener("dragend", () => {
  pdfState.dragKey = null;
});
pdfEl("#pdf-sort").addEventListener("click", () => {
  pdfState.pages.sort((a, b) =>
    pdfAsset(a.asset).name.localeCompare(pdfAsset(b.asset).name, undefined, {
      numeric: true,
      sensitivity: "base",
    }),
  );
  pdfDirty();
  pdfRenderPages();
});
pdfEl("#pdf-reverse").addEventListener("click", () => {
  pdfState.pages.reverse();
  pdfDirty();
  pdfRenderPages();
});
pdfEl("#pdf-clear").addEventListener("click", () => {
  pdfState.pages = [];
  pdfState.selected = null;
  pdfState.options = structuredClone(pdfState.config.pdf_defaults);
  pdfDirty();
  pdfSyncDocumentForm();
  pdfRenderPages();
  pdfNotice(
    "New document started. Existing PDFs and uploaded images remain available.",
  );
});
pdfEl("#pdf-library").addEventListener(
  "click",
  pdfSafe((event) => {
    const button = event.target.closest("[data-asset]");
    if (!button || pdfState.busy) return;
    pdfAddAsset(pdfAsset(button.dataset.asset));
    pdfDirty();
    pdfRenderPages();
  }),
);
pdfEl("#pdf-image-form").addEventListener("submit", (event) =>
  event.preventDefault(),
);
pdfEl("#pdf-image-form").addEventListener("input", () => {
  const page = pdfPage();
  if (!page || pdfState.busy) return;
  const form = pdfEl("#pdf-image-form"),
    settings = { ...page.settings };
  for (const key of Object.keys(pdfState.config.image_defaults)) {
    const input = form.elements.namedItem(key);
    if (!input) continue;
    settings[key] =
      input.type === "checkbox"
        ? input.checked
        : key === "filter"
          ? input.value
          : Number(input.value);
  }
  settings.crop = ["left", "top", "right", "bottom"].map((key) =>
    Number(form.elements.namedItem(`crop_${key}`).value),
  );
  page.settings = settings;
  pdfSliderValues();
  pdfDirty();
  pdfSchedulePreview();
  const meta = document.querySelector(
    `.pdf-page[data-key="${page.key}"] .pdf-page-meta`,
  );
  if (meta) {
    const asset = pdfAsset(page.asset);
    meta.textContent = `${asset.width} × ${asset.height} · ${settings.filter}`;
  }
});
pdfEl("#pdf-document-form").addEventListener("submit", (event) =>
  event.preventDefault(),
);
pdfEl("#pdf-document-form").addEventListener("input", () => {
  const form = pdfEl("#pdf-document-form");
  for (const key of Object.keys(pdfState.config.pdf_defaults)) {
    const input = form.elements.namedItem(key);
    pdfState.options[key] =
      input.type === "checkbox"
        ? input.checked
        : ["margin", "dpi", "quality"].includes(key)
          ? Number(input.value)
          : input.value;
  }
  pdfSliderValues();
  pdfDirty();
  pdfSchedulePreview();
});
pdfEl("#pdf-reset-image").addEventListener("click", () => {
  const page = pdfPage();
  if (!page) return;
  page.settings = structuredClone(pdfState.config.image_defaults);
  pdfDirty();
  pdfRenderPages();
});
pdfEl("#pdf-apply-all").addEventListener("click", () => {
  const page = pdfPage();
  if (!page) return;
  for (const other of pdfState.pages)
    other.settings = structuredClone(page.settings);
  pdfDirty();
  pdfRenderPages();
  pdfNotice("Image adjustments applied to all pages.");
});
function pdfTab(image) {
  pdfEl("#pdf-image-form").hidden = !image;
  pdfEl("#pdf-document-form").hidden = image;
  pdfEl("#pdf-tab-image").setAttribute("aria-pressed", String(image));
  pdfEl("#pdf-tab-document").setAttribute("aria-pressed", String(!image));
}
pdfEl("#pdf-tab-image").addEventListener("click", () => pdfTab(true));
pdfEl("#pdf-tab-document").addEventListener("click", () => pdfTab(false));
pdfEl("#pdf-export").addEventListener(
  "click",
  pdfSafe(async () => {
    if (!pdfState.pages.length || pdfState.busy) return;
    if (!pdfEl("#pdf-document-form").checkValidity()) {
      pdfTab(false);
      pdfEl("#pdf-document-form").reportValidity();
      return;
    }
    pdfBusy(true, `Creating a PDF from ${pdfState.pages.length} pages…`);
    try {
      const result = await pdfApi("/api/pdf/export", {
        pages: pdfWirePages(),
        options: pdfState.options,
      });
      pdfShowResult(result);
      await pdfRefreshHistory();
      pdfNotice("PDF created. Download it below the preview.");
    } finally {
      pdfBusy(false);
    }
  }),
);
pdfEl("#pdf-history").addEventListener(
  "click",
  pdfSafe(async (event) => {
    const button = event.target.closest("[data-document]");
    if (!button || pdfState.busy) return;
    const document = await pdfApi(
      `/api/pdf/documents/${button.dataset.document}`,
    );
    pdfState.pages = document.pages.map((page) => ({
      ...page,
      key: crypto.randomUUID(),
    }));
    pdfState.selected = pdfState.pages[0]?.key || null;
    pdfState.options = document.options;
    pdfDirty();
    pdfSyncDocumentForm();
    pdfRenderPages();
    pdfShowResult(document);
    pdfNotice(
      "Saved PDF reopened. Adjust pages and create a new export when ready.",
    );
  }),
);
pdfSafe(async () => {
  pdfBusy(true, "Opening your local image workspace…");
  try {
    const [session, config] = await Promise.all([
      pdfApi("/api/config"),
      pdfApi("/api/pdf/config"),
    ]);
    pdfState.token = session.token;
    pdfState.config = config;
    pdfState.options = structuredClone(config.pdf_defaults);
    try {
      const draft = JSON.parse(
        localStorage.getItem("chopify.pdf.draft.v1") || "null",
      );
      if (draft && Array.isArray(draft.pages)) {
        pdfState.pages = draft.pages
          .filter((page) => pdfAsset(page.asset))
          .slice(0, 100)
          .map((page) => ({
            asset: page.asset,
            key: crypto.randomUUID(),
            settings: {
              ...structuredClone(config.image_defaults),
              ...page.settings,
            },
          }));
        pdfState.selected = pdfState.pages[0]?.key || null;
        pdfState.options = { ...pdfState.options, ...draft.options };
      }
    } catch (_) {
      /* A corrupt browser draft does not affect saved images/PDFs. */
    }
    pdfSyncDocumentForm();
    pdfRenderLibrary();
    await pdfRefreshHistory();
  } finally {
    pdfBusy(false);
    pdfRenderPages();
  }
})();
