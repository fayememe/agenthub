(() => {
  const HIDDEN_NAMES = new Set([
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
  ]);
  const PROJECT_PREFIX = "agenthub.filepane.project.";
  const WIDTH_KEY = "agenthub.filepane.width";
  const OPEN_KEY = "agenthub.filepane.open";
  const TREE_KEY = "agenthub.filepane.tree";
  const TREE_WIDTH_KEY = "agenthub.filepane.treeWidth";
  const RATIO_KEY = "agenthub.filepane.ratio";
  const TREE_RATIO_KEY = "agenthub.filepane.treeRatio";
  const FILEPANE_MIN_WINDOW = 900;
  const TREE_MIN_PANE_WIDTH = 320;
  const els = {
    stageBody: document.getElementById("stage-body"),
    pane: document.getElementById("filepane"),
    resizer: document.getElementById("filepane-resizer"),
    tabs: document.getElementById("filepane-tabs"),
    body: document.getElementById("filepane-body"),
    title: document.getElementById("filepane-title"),
    meta: document.getElementById("filepane-meta"),
    refresh: document.getElementById("filepane-refresh"),
    close: document.getElementById("filepane-close"),
    toggle: document.getElementById("files-toggle"),
    treeToggle: document.getElementById("filepane-tree-toggle"),
  };
  if (!els.stageBody || !els.pane) return;

  const state = {
    open: localStorage.getItem(OPEN_KEY) === "1",
    width: 420,
    widthRatio: 0,
    autoHidden: false,
    lastNarrow: window.innerWidth < FILEPANE_MIN_WINDOW,
    treeVisible: localStorage.getItem(TREE_KEY) !== "0",
    treeRatio: 0,
    panel: "oc",
    project: null,
    entries: new Map(),
    expanded: new Set(["."]),
    tabs: [],
    active: null,
    contents: new Map(),
    loading: new Set(),
    errors: new Map(),
    diffs: new Map(),
    diffLoading: new Set(),
    diffErrors: new Map(),
    status: new Map(),
    showHidden: false,
    mode: new Map(),
    scroll: new Map(),
    treeScroll: 0,
    search: "",
    searchResults: null,
    searchError: null,
    searchLoading: false,
    searchToken: 0,
    searchTimer: null,
    saveTimer: null,
    dragging: false,
    treeDragging: false,
    treePaneEl: null,
    treeResizerEl: null,
  };
  state.widthRatio = loadPaneRatio();
  state.width = paneWidthPx();
  state.treeRatio = loadTreeRatio();
  state.autoHidden = state.open && state.lastNarrow;

  function stageWidth() {
    return Math.max(1, els.stageBody?.clientWidth || window.innerWidth);
  }

  function clampPaneRatio(value) {
    return Math.max(0.2, Math.min(1, Number(value) || 0.35));
  }

  function clampTreeRatio(value) {
    return Math.max(0.3, Math.min(0.8, Number(value) || 0.45));
  }

  function loadPaneRatio() {
    const stored = Number(localStorage.getItem(RATIO_KEY));
    if (Number.isFinite(stored) && stored > 0) return clampPaneRatio(stored);
    const legacy = Number(localStorage.getItem(WIDTH_KEY));
    if (Number.isFinite(legacy) && legacy > 0) {
      return clampPaneRatio(legacy / stageWidth());
    }
    return 0.35;
  }

  function loadTreeRatio() {
    const stored = Number(localStorage.getItem(TREE_RATIO_KEY));
    if (Number.isFinite(stored) && stored > 0) return clampTreeRatio(stored);
    const legacy = Number(localStorage.getItem(TREE_WIDTH_KEY));
    if (Number.isFinite(legacy) && legacy > 0) {
      return clampTreeRatio(legacy / Math.max(1, paneWidthPx()));
    }
    return 0.45;
  }

  function paneWidthPx() {
    return Math.round(stageWidth() * state.widthRatio);
  }

  function treeWidthPx() {
    return Math.round(state.width * state.treeRatio);
  }

  function effectiveTreeVisible() {
    return state.treeVisible && state.width >= TREE_MIN_PANE_WIDTH;
  }

  function applyPaneWidth() {
    state.widthRatio = clampPaneRatio(state.widthRatio);
    state.width = paneWidthPx();
    els.stageBody.style.setProperty("--filepane-width", `${state.width}px`);
  }

  function applyTreeWidth() {
    state.treeRatio = clampTreeRatio(state.treeRatio);
    const width = treeWidthPx();
    if (state.treePaneEl) state.treePaneEl.style.width = `${width}px`;
    els.stageBody.style.setProperty("--filepane-tree-width", `${width}px`);
  }

  function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on") && typeof value === "function") {
        node.addEventListener(key.slice(2), value);
      } else if (value === true) node.setAttribute(key, "");
      else node.setAttribute(key, value);
    }
    for (const child of children) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function basename(path) {
    const value = String(path || "").replace(/\/+$/, "");
    const index = value.lastIndexOf("/");
    return index >= 0 ? value.slice(index + 1) : value;
  }

  function dirKey(path) {
    if (!path || path === ".") return ".";
    return String(path).replace(/\/+$/, "");
  }

  function isMarkdown(path) {
    return /\.(md|markdown|mdown|mkd)$/i.test(path || "");
  }

  function projectAvailable() {
    return Boolean(state.project && state.project.kind !== "other");
  }

  function projectKey() {
    return state.project ? `${state.project.node}:${state.project.path}` : null;
  }

  function saveGlobal() {
    localStorage.setItem(RATIO_KEY, String(state.widthRatio));
    localStorage.setItem(OPEN_KEY, state.open ? "1" : "0");
    localStorage.setItem(TREE_KEY, state.treeVisible ? "1" : "0");
    localStorage.setItem(TREE_RATIO_KEY, String(state.treeRatio));
  }

  function scheduleSave() {
    clearTimeout(state.saveTimer);
    state.saveTimer = setTimeout(saveProject, 160);
  }

  function saveProject() {
    const key = projectKey();
    if (!key) return;
    const data = {
      tabs: state.tabs.map((tab) => ({ path: tab.path, pinned: Boolean(tab.pinned) })),
      active: state.active,
      expanded: [...state.expanded],
      showHidden: state.showHidden,
      mode: Object.fromEntries(state.mode),
      scroll: Object.fromEntries(state.scroll),
      treeScroll: state.treeScroll,
    };
    try {
      localStorage.setItem(PROJECT_PREFIX + key, JSON.stringify(data));
    } catch (error) {}
  }

  function loadProject() {
    state.tabs = [];
    state.active = null;
    state.expanded = new Set(["."]);
    state.showHidden = false;
    state.mode = new Map();
    state.scroll = new Map();
    state.treeScroll = 0;
    const key = projectKey();
    if (!key) return;
    let data = null;
    try {
      data = JSON.parse(localStorage.getItem(PROJECT_PREFIX + key) || "null");
    } catch (error) {
      data = null;
    }
    if (!data || typeof data !== "object") return;
    const rawTabs = Array.isArray(data.tabs) ? data.tabs : [];
    const seen = new Set();
    state.tabs = [];
    for (const item of rawTabs) {
      const path = typeof item === "string" ? item : item && item.path;
      if (typeof path !== "string" || !path || seen.has(path)) continue;
      seen.add(path);
      state.tabs.push({
        path,
        name: basename(path),
        pinned: typeof item === "string" ? true : Boolean(item.pinned),
      });
    }
    state.active = state.tabs.some((tab) => tab.path === data.active) ? data.active : null;
    state.expanded = new Set(
      [".", ...(Array.isArray(data.expanded) ? data.expanded.filter((item) => typeof item === "string") : [])]
    );
    state.showHidden = Boolean(data.showHidden);
    state.mode = new Map(
      data.mode && typeof data.mode === "object" ? Object.entries(data.mode) : []
    );
    state.scroll = new Map(
      data.scroll && typeof data.scroll === "object"
        ? Object.entries(data.scroll).map(([path, value]) => [path, Number(value) || 0])
        : []
    );
    state.treeScroll = Number(data.treeScroll) || 0;
  }

  function updateToggle() {
    if (!els.toggle) return;
    els.toggle.hidden = state.panel !== "oc";
    els.toggle.disabled = !projectAvailable();
    els.toggle.classList.toggle("active", state.open && !state.autoHidden);
    els.toggle.title = state.autoHidden ? "Open files (window is narrow)" : "Files";
  }

  function updateTreeToggle() {
    if (!els.treeToggle) return;
    const available = state.width >= TREE_MIN_PANE_WIDTH;
    els.treeToggle.disabled = !available;
    els.treeToggle.classList.toggle("active", state.treeVisible && available);
    els.treeToggle.title = available
      ? state.treeVisible
        ? "Hide file tree"
        : "Show file tree"
      : "Widen the file pane to show the tree";
  }

  function toggleTree(force) {
    if (state.width < TREE_MIN_PANE_WIDTH) return;
    state.treeVisible = force === undefined ? !state.treeVisible : Boolean(force);
    saveGlobal();
    updateTreeToggle();
    renderBody();
    if (state.treeVisible && projectAvailable() && !state.entries.has(".")) {
      loadRoot();
    }
  }

  function applyVisibility() {
    const show =
      state.open && !state.autoHidden && state.panel === "oc" && projectAvailable();
    els.pane.hidden = !show;
    if (els.resizer) els.resizer.hidden = !show;
    els.stageBody.classList.toggle("filepane-open", show);
    els.stageBody.style.setProperty("--filepane-width", `${state.width}px`);
    updateToggle();
  }

  function handleFilepaneAuto() {
    const narrow = window.innerWidth < FILEPANE_MIN_WINDOW;
    if (narrow === state.lastNarrow) return;
    state.lastNarrow = narrow;
    if (narrow && state.open) state.autoHidden = true;
    else if (!narrow && state.autoHidden) state.autoHidden = false;
    applyVisibility();
  }

  function layoutChanged() {
    const beforeTree = effectiveTreeVisible();
    applyPaneWidth();
    applyTreeWidth();
    handleFilepaneAuto();
    if (beforeTree !== effectiveTreeVisible()) {
      renderBody();
    } else {
      updateTreeToggle();
    }
    updateToggle();
  }

  function setOpen(open) {
    state.open = Boolean(open);
    if (state.open) state.autoHidden = false;
    saveGlobal();
    applyVisibility();
    if (state.open && projectAvailable() && !state.entries.has(".")) loadRoot();
  }

  function toggle(force) {
    if (force === undefined) {
      setOpen(state.autoHidden ? true : !state.open);
      return;
    }
    setOpen(Boolean(force));
  }

  function setProject(project) {
    const next = project && project.path && project.node ? project : null;
    const current = state.project;
    if (
      current &&
      next &&
      current.node === next.node &&
      current.path === next.path
    ) {
      state.project = next;
      updateToggle();
      applyVisibility();
      return;
    }
    saveProject();
    state.project = next;
    state.entries.clear();
    state.contents.clear();
    state.loading.clear();
    state.errors.clear();
    state.diffs.clear();
    state.diffLoading.clear();
    state.diffErrors.clear();
    state.status.clear();
    state.search = "";
    state.searchResults = null;
    state.searchError = null;
    state.searchLoading = false;
    state.searchToken += 1;
    loadProject();
    if (!next) {
      render();
      applyVisibility();
      return;
    }
    render();
    applyVisibility();
    if (state.open && projectAvailable()) loadRoot();
  }

  function setPanel(panel) {
    state.panel = panel;
    applyVisibility();
    if (state.open && panel === "oc" && projectAvailable() && !state.entries.has(".")) {
      loadRoot();
    }
  }

  async function api(path, params) {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params || {})) {
      if (value !== undefined && value !== null) query.set(key, String(value));
    }
    const response = await fetch(`${path}?${query.toString()}`, {
      headers: { Accept: "application/json" },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `${response.status} ${response.statusText}`);
    return data;
  }

  function projectParams() {
    return {
      node: state.project.node,
      directory: state.project.path,
    };
  }

  async function loadDir(path) {
    if (!projectAvailable()) return;
    const key = dirKey(path);
    if (state.loading.has(key)) return;
    state.loading.add(key);
    state.errors.delete(key);
    if (state.treeVisible) renderTreeArea();
    try {
      const data = await api("/api/files/list", {
        ...projectParams(),
        path: key,
      });
      state.entries.set(key, Array.isArray(data.entries) ? data.entries : []);
    } catch (error) {
      state.errors.set(key, String(error.message || error));
    } finally {
      state.loading.delete(key);
      if (state.treeVisible) renderTreeArea();
    }
  }

  async function loadRoot() {
    state.expanded.add(".");
    state.entries.delete(".");
    await loadDir(".");
    loadStatus();
  }

  async function loadStatus() {
    if (!projectAvailable()) return;
    try {
      const data = await api("/api/files/status", projectParams());
      state.status = new Map(
        (Array.isArray(data.files) ? data.files : []).map((item) => [item.path, item])
      );
      if (state.treeVisible) renderTreeArea();
    } catch (error) {
      state.status.clear();
    }
  }

  async function loadContent(path, force = false) {
    if (!projectAvailable()) return;
    if (state.loading.has(`file:${path}`)) return;
    if (!force && state.contents.has(path)) return;
    state.loading.add(`file:${path}`);
    state.errors.delete(`file:${path}`);
    if (state.active === path) renderBody();
    try {
      const data = await api("/api/files/content", {
        ...projectParams(),
        path,
      });
      state.contents.set(path, data);
    } catch (error) {
      state.errors.set(`file:${path}`, String(error.message || error));
    } finally {
      state.loading.delete(`file:${path}`);
      if (state.active === path) renderBody();
    }
  }

  function visible(entry) {
    if (!entry) return false;
    if (!state.showHidden && HIDDEN_NAMES.has(entry.name)) return false;
    if (!state.showHidden && entry.ignored) return false;
    return true;
  }

  function statusClass(path) {
    const status = state.status.get(path);
    return status && status.status ? ` status-${status.status}` : "";
  }

  function entryPath(entry) {
    return entry.type === "directory" ? dirKey(entry.path) : entry.path;
  }

  function render() {
    updateToggle();
    updateTreeToggle();
    renderTabs();
    renderBody();
    if (els.title) {
      els.title.textContent = "File pane";
    }
    if (els.meta) {
      els.meta.textContent = state.project
        ? `${state.project.path} · read-only`
        : "Select a project";
    }
  }

  function renderTabs() {
    if (!els.tabs) return;
    els.tabs.replaceChildren();
    if (!state.tabs.length) {
      els.tabs.append(
        el("span", { class: "filepane-tabs-empty", text: "No files open" })
      );
      return;
    }
    for (const tab of state.tabs) {
      const button = el(
        "button",
        {
          class:
            "filepane-tab" +
            (state.active === tab.path ? " active" : "") +
            (tab.pinned ? "" : " preview"),
          type: "button",
          title: tab.pinned ? tab.path : `${tab.path} (preview, double-click to pin)`,
          onclick: () => {
            state.active = tab.path;
            saveProject();
            render();
            if (!state.contents.has(tab.path)) loadContent(tab.path);
          },
          ondblclick: (event) => {
            event.preventDefault();
            openFile(tab.path, tab.name, true);
          },
        },
        [
          el("span", { class: "name", text: tab.name }),
          el("span", {
            class: "close",
            text: "×",
            title: "Close",
            onclick: (event) => {
              event.stopPropagation();
              closeTab(tab.path);
            },
          }),
        ]
      );
      els.tabs.append(button);
    }
  }

  function closeTab(path) {
    state.tabs = state.tabs.filter((tab) => tab.path !== path);
    state.contents.delete(path);
    state.mode.delete(path);
    state.scroll.delete(path);
    if (state.active === path) {
      state.active = state.tabs.length ? state.tabs[state.tabs.length - 1].path : null;
    }
    saveProject();
    render();
    if (state.active && !state.contents.has(state.active)) loadContent(state.active);
  }

  function openFile(path, name, pinned = false) {
    if (!projectAvailable()) return;
    const label = name || basename(path);
    const existing = state.tabs.find((tab) => tab.path === path);
    if (existing) {
      if (pinned) existing.pinned = true;
      state.active = path;
      saveProject();
      render();
      if (!state.contents.has(path)) loadContent(path);
      return;
    }
    if (!pinned) {
      const preview = state.tabs.find((tab) => !tab.pinned);
      if (preview) {
        const previous = preview.path;
        state.contents.delete(previous);
        state.mode.delete(previous);
        state.scroll.delete(previous);
        state.errors.delete(`file:${previous}`);
        preview.path = path;
        preview.name = label;
        preview.pinned = false;
        state.active = path;
        saveProject();
        render();
        loadContent(path);
        return;
      }
    }
    state.tabs.push({ path, name: label, pinned: Boolean(pinned) });
    state.active = path;
    saveProject();
    render();
    if (!state.contents.has(path)) loadContent(path);
  }

  function renderBody() {
    if (!els.body) return;
    if (!state.project) {
      els.body.replaceChildren(el("div", { class: "filepane-empty", text: "Select a project." }));
      return;
    }
    if (!projectAvailable()) {
      els.body.replaceChildren(
        el("div", { class: "filepane-empty", text: "Files are not available for the Other project." })
      );
      return;
    }
    updateTreeToggle();
    const split = el("div", {
      class: "filepane-split" + (state.treeVisible ? "" : " single"),
    });
    if (effectiveTreeVisible()) {
      split.append(renderTreePane());
      split.append(renderTreeResizer());
    }
    split.append(renderViewerPane());
    els.body.replaceChildren(split);
    applyTreeWidth();
  }

  function renderTreePane() {
    const input = el("input", {
      class: "filepane-search",
      type: "search",
      placeholder: "Find files…",
      spellcheck: "false",
      value: state.search,
    });
    input.addEventListener("input", () => {
      state.search = input.value.trim();
      clearTimeout(state.searchTimer);
      state.searchResults = null;
      state.searchError = null;
      state.searchLoading = false;
      state.searchToken += 1;
      if (!state.search) {
        renderTreeArea();
        return;
      }
      if (state.search.length < 2) {
        state.searchResults = [];
        renderTreeArea();
        return;
      }
      const token = state.searchToken;
      state.searchLoading = true;
      renderTreeArea();
      state.searchTimer = setTimeout(async () => {
        try {
          const data = await api("/api/files/find", {
            ...projectParams(),
            query: state.search,
            limit: 80,
          });
          if (token !== state.searchToken) return;
          state.searchResults = Array.isArray(data.paths) ? data.paths : [];
        } catch (error) {
          if (token !== state.searchToken) return;
          state.searchError = String(error.message || error);
        } finally {
          if (token === state.searchToken) {
            state.searchLoading = false;
            renderTreeArea();
          }
        }
      }, 260);
    });
    const hidden = el("input", { type: "checkbox" });
    hidden.checked = state.showHidden;
    hidden.addEventListener("change", () => {
      state.showHidden = hidden.checked;
      saveProject();
      renderTreeArea();
    });
    const toolbar = el("div", { class: "filepane-toolbar" }, [
      input,
      el("label", { class: "filepane-filter", title: "Show ignored and common hidden directories" }, [
        hidden,
        "hidden",
      ]),
    ]);
    const area = el("div", { class: "filepane-tree" });
    state.treeArea = area;
    area.addEventListener("scroll", () => {
      state.treeScroll = area.scrollTop;
      scheduleSave();
    });
    const pane = el("div", { class: "filepane-tree-pane" }, [toolbar, area]);
    state.treePaneEl = pane;
    pane.style.width = `${treeWidthPx()}px`;
    renderTreeArea();
    return pane;
  }

  function renderTreeResizer() {
    const handle = el("div", {
      class: "filepane-tree-resizer",
      title: "Resize file tree",
    });
    handle.addEventListener("pointerdown", (event) => {
      state.treeDragging = true;
      state.treeDragStartX = event.clientX;
      state.treeDragStartWidth = treeWidthPx();
      els.stageBody.classList.add("filepane-tree-resizing");
      if (handle.setPointerCapture) {
        try {
          handle.setPointerCapture(event.pointerId);
        } catch (error) {}
      }
      event.preventDefault();
    });
    state.treeResizerEl = handle;
    return handle;
  }

  async function loadDiff(path) {
    if (!projectAvailable() || state.diffLoading.has(path) || state.diffs.has(path)) return;
    state.diffLoading.add(path);
    state.diffErrors.delete(path);
    if (state.active === path) renderBody();
    try {
      const data = await api("/api/files/diff", {
        ...projectParams(),
        path,
      });
      state.diffs.set(path, data);
    } catch (error) {
      state.diffErrors.set(path, String(error.message || error));
    } finally {
      state.diffLoading.delete(path);
      if (state.active === path) renderBody();
    }
  }

  function renderTreeArea() {
    const area = state.treeArea;
    if (!area) return;
    if (state.search) {
      if (state.searchLoading) {
        area.replaceChildren(el("div", { class: "filepane-loading", text: "Searching…" }));
        return;
      }
      if (state.searchError) {
        area.replaceChildren(el("div", { class: "filepane-error", text: state.searchError }));
        return;
      }
      const results = state.searchResults || [];
      if (!results.length) {
        area.replaceChildren(el("div", { class: "filepane-empty", text: "No matching files." }));
        return;
      }
      area.replaceChildren(
        ...results.map((path) =>
          el("button", {
            class: "filepane-search-hit",
            type: "button",
            text: path,
            title: path,
            onclick: () => openFile(path, basename(path)),
            ondblclick: (event) => {
              event.preventDefault();
              openFile(path, basename(path), true);
            },
          })
        )
      );
      return;
    }
    const root = state.entries.get(".");
    if (root === undefined) {
      const error = state.errors.get(".");
      area.replaceChildren(
        error
          ? el("div", { class: "filepane-error", text: error })
          : el("div", { class: "filepane-loading", text: "Loading files…" })
      );
      return;
    }
    if (!root.length) {
      area.replaceChildren(el("div", { class: "filepane-empty", text: "Directory is empty." }));
      return;
    }
    const list = el("ul");
    appendEntries(list, root, 0);
    area.replaceChildren(list);
    requestAnimationFrame(() => {
      area.scrollTop = state.treeScroll;
    });
  }

  function appendEntries(parent, entries, depth) {
    const sorted = [...entries].filter(visible).sort((a, b) => {
      if (a.type !== b.type) return a.type === "directory" ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
    for (const entry of sorted) {
      const path = entryPath(entry);
      const isDir = entry.type === "directory";
      const expanded = isDir && state.expanded.has(path);
      const row = el(
        "div",
        {
          class:
            "filepane-row" +
            (isDir ? " directory" : "") +
            (entry.ignored ? " ignored" : "") +
            (isDir ? "" : statusClass(entry.path)),
          style: `padding-left:${8 + depth * 14}px`,
          title: entry.path,
          onclick: isDir
            ? () => toggleDir(path)
            : () => openFile(entry.path, entry.name),
          ondblclick: isDir
            ? null
            : (event) => {
                event.preventDefault();
                openFile(entry.path, entry.name, true);
              },
        },
        [
          el("span", { class: "chev", text: isDir ? (expanded ? "▾" : "▸") : "" }),
          el("span", { class: "icon", text: isDir ? "▸" : "·" }),
          el("span", { class: "name", text: entry.name }),
        ]
      );
      parent.append(el("li", {}, [row]));
      if (isDir && expanded) {
        const children = state.entries.get(path);
        if (children === undefined) {
          const error = state.errors.get(path);
          parent.append(
            el("li", {}, [
              el("div", {
                class: error ? "filepane-error" : "filepane-loading",
                style: `padding-left:${22 + depth * 14}px`,
                text: error || "Loading…",
              }),
            ])
          );
          if (!error) loadDir(path);
        } else if (children.length) {
          appendEntries(parent, children, depth + 1);
        }
      }
    }
  }

  function toggleDir(path) {
    if (state.expanded.has(path)) {
      state.expanded.delete(path);
      saveProject();
      renderTreeArea();
      return;
    }
    state.expanded.add(path);
    saveProject();
    renderTreeArea();
    if (!state.entries.has(path)) loadDir(path);
  }

  function renderViewerPane() {
    const pane = el("div", { class: "filepane-viewer" });
    if (state.active === null) {
      const hint = effectiveTreeVisible()
        ? "Select a file from the tree."
        : state.treeVisible
          ? "Widen the file pane to show the tree."
          : "File tree is hidden.";
      const empty = el("div", { class: "filepane-empty" }, [el("div", { text: hint })]);
      if (!state.treeVisible) {
        empty.append(
          el("button", {
            class: "ghost-button small",
            type: "button",
            text: "Show file tree",
            onclick: () => toggleTree(true),
          })
        );
      }
      pane.append(empty);
      return pane;
    }

    const tab = state.tabs.find((item) => item.path === state.active);
    if (!tab) {
      state.active = null;
      return renderViewerPane();
    }
    const path = tab.path;
    const payload = state.contents.get(path);
    const error = state.errors.get(`file:${path}`);
    const loading = state.loading.has(`file:${path}`);
    const textFile = !payload || payload.type !== "binary";
    let mode = state.mode.get(path) || (isMarkdown(path) ? "preview" : "source");
    if (!textFile) mode = "source";
    if (!isMarkdown(path) && mode === "preview") mode = "source";

    const modes = [];
    if (textFile) {
      if (isMarkdown(path)) modes.push(["preview", "Preview"]);
      modes.push(["source", "Source"]);
      modes.push(["diff", "Diff"]);
    }

    const head = el("div", { class: "filepane-viewer-head" }, [
      el("span", { class: "filepane-path", text: path, title: path }),
    ]);
    if (modes.length) {
      head.append(
        el(
          "span",
          { class: "filepane-mode" },
          modes.map(([id, label]) =>
            el("button", {
              class: mode === id ? "active" : "",
              type: "button",
              text: label,
              onclick: () => {
                state.mode.set(path, id);
                saveProject();
                renderBody();
              },
            })
          )
        )
      );
    }
    const diff = state.diffs.get(path);
    if (mode === "diff" && diff) {
      head.append(
        el("span", {
          class: "filepane-diff-stat",
          text: `+${diff.additions || 0} −${diff.deletions || 0}`,
          title: diff.status || "",
        })
      );
    }
    head.append(
      el("button", {
        class: "icon-button small",
        type: "button",
        title: "Copy file content",
        text: "⧉",
        onclick: () => copyContent(payload),
      }),
      el("button", {
        class: "icon-button small",
        type: "button",
        title: mode === "diff" ? "Reload diff" : "Reload file",
        text: "↻",
        onclick: () => {
          if (mode === "diff") {
            state.diffs.delete(path);
            state.diffErrors.delete(path);
            loadDiff(path);
          } else {
            loadContent(path, true);
          }
        },
      })
    );

    const content = el("div", { class: "filepane-content" });
    if (loading) {
      content.append(el("div", { class: "filepane-loading", text: "Loading…" }));
    } else if (error) {
      content.append(
        el("div", { class: "filepane-error" }, [
          error,
          el("div", {}, [
            el("button", {
              class: "ghost-button small",
              type: "button",
              text: "Retry",
              onclick: () => loadContent(path, true),
            }),
          ]),
        ])
      );
    } else if (!payload) {
      content.append(el("div", { class: "filepane-loading", text: "Loading…" }));
      loadContent(path);
    } else if (payload.type === "binary") {
      content.append(renderBinary(path, payload));
    } else if (mode === "diff") {
      const diffLoading = state.diffLoading.has(path);
      const diffError = state.diffErrors.get(path);
      const diffValue = state.diffs.get(path);
      if (diffLoading) {
        content.append(el("div", { class: "filepane-loading", text: "Loading diff…" }));
      } else if (diffError) {
        content.append(
          el("div", { class: "filepane-error" }, [
            diffError,
            el("div", {}, [
              el("button", {
                class: "ghost-button small",
                type: "button",
                text: "Retry",
                onclick: () => {
                  state.diffErrors.delete(path);
                  loadDiff(path);
                },
              }),
            ]),
          ])
        );
      } else if (!diffValue) {
        content.append(el("div", { class: "filepane-loading", text: "Loading diff…" }));
        loadDiff(path);
      } else if (!diffValue.patch) {
        content.append(
          el("div", { class: "filepane-empty", text: "No working-tree diff for this file." })
        );
      } else {
        content.append(renderDiff(diffValue));
      }
    } else if (isMarkdown(path) && mode === "preview") {
      const text = String(payload.content || "");
      const md = el("div", { class: "filepane-md" });
      md.innerHTML = renderMarkdown(text);
      content.append(md);
    } else {
      content.append(el("pre", { class: "filepane-pre", text: String(payload.content || "") }));
    }

    pane.append(head, content);
    content.addEventListener("scroll", () => {
      state.scroll.set(path, content.scrollTop);
      scheduleSave();
    });
    requestAnimationFrame(() => {
      content.scrollTop = state.scroll.get(path) || 0;
    });
    return pane;
  }

  function renderDiff(diff) {
    const wrap = el("div", { class: "filepane-diff" });
    if (diff.truncated) {
      wrap.append(
        el("div", { class: "filepane-diff-note", text: "Diff is truncated because it is too large." })
      );
    }
    const lines = String(diff.patch || "").split("\n");
    for (const line of lines) {
      let kind = "context";
      if (
        /^(diff --git|index |--- |\+\+\+ |new file mode|deleted file mode|similarity index|rename from|rename to|\\ No newline)/.test(
          line
        )
      ) {
        kind = "meta";
      } else if (line.startsWith("@@")) {
        kind = "hunk";
      } else if (line.startsWith("+")) {
        kind = "add";
      } else if (line.startsWith("-")) {
        kind = "del";
      }
      wrap.append(el("div", { class: `diff-line ${kind}`, text: line || " " }));
    }
    return wrap;
  }

  function renderBinary(path, payload) {
    const wrap = el("div", { class: "filepane-binary" });
    const mime = payload.mimeType || "application/octet-stream";
    const content = String(payload.content || "");
    if (mime.startsWith("image/") && payload.encoding === "base64") {
      wrap.append(
        el("img", {
          src: `data:${mime};base64,${content}`,
          alt: basename(path),
        })
      );
    }
    wrap.append(
      el("div", {
        text: `Binary file · ${mime} · ${formatBytes(content.length)}`,
      }),
      el("div", { style: "margin-top:8px" }, [
        el("button", {
          class: "ghost-button small",
          type: "button",
          text: "Download",
          onclick: () => downloadBinary(path, payload),
        }),
      ])
    );
    return wrap;
  }

  function formatBytes(value) {
    const bytes = Number(value) || 0;
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
  }

  function base64ToBlob(content, mime) {
    const raw = atob(content);
    const bytes = new Uint8Array(raw.length);
    for (let index = 0; index < raw.length; index += 1) bytes[index] = raw.charCodeAt(index);
    return new Blob([bytes], { type: mime || "application/octet-stream" });
  }

  function downloadBinary(path, payload) {
    try {
      const url = URL.createObjectURL(
        base64ToBlob(String(payload.content || ""), payload.mimeType)
      );
      const link = document.createElement("a");
      link.href = url;
      link.download = basename(path);
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (error) {}
  }

  async function copyContent(payload) {
    if (!payload || payload.type === "binary") return;
    const text = String(payload.content || "");
    try {
      await navigator.clipboard.writeText(text);
    } catch (error) {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.append(area);
      area.select();
      try {
        document.execCommand("copy");
      } catch (ignored) {}
      area.remove();
    }
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (character) => {
      switch (character) {
        case "&": return "&amp;";
        case "<": return "&lt;";
        case ">": return "&gt;";
        case '"': return "&quot;";
        case "'": return "&#39;";
        default: return character;
      }
    });
  }

  function safeHref(value) {
    const href = String(value || "").trim();
    if (!href) return null;
    if (/^(javascript|data|vbscript):/i.test(href)) return null;
    if (/^[a-z][a-z0-9+.-]*:/i.test(href) && !/^(https?|mailto):/i.test(href)) return null;
    return escapeHtml(href);
  }

  function inlineMarkdown(source) {
    let text = escapeHtml(source);
    const codes = [];
    text = text.replace(/`([^`\n]+)`/g, (match, code) => {
      const token = `\u0000C${codes.length}\u0000`;
      codes.push(`<code>${code}</code>`);
      return token;
    });
    text = text.replace(
      /!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g,
      (match, alt, href) => {
        const safe = safeHref(href);
        if (!safe) return alt;
        if (/^https?:\/\//i.test(href)) {
          return `<img src="${safe}" alt="${escapeHtml(alt)}">`;
        }
        return `<span class="md-image">[image: ${escapeHtml(alt)}]</span>`;
      }
    );
    text = text.replace(
      /\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g,
      (match, label, href) => {
        const safe = safeHref(href);
        return safe
          ? `<a href="${safe}" target="_blank" rel="noreferrer">${label}</a>`
          : label;
      }
    );
    text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    text = text.replace(/__([^_]+)__/g, "<strong>$1</strong>");
    text = text.replace(/~~([^~]+)~~/g, "<del>$1</del>");
    text = text.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    text = text.replace(/(^|[^_])_([^_\n]+)_/g, "$1<em>$2</em>");
    text = text.replace(/\u0000C(\d+)\u0000/g, (match, index) => codes[Number(index)] || "");
    return text;
  }

  function isTableSeparator(line) {
    return line.includes("-") && /^\s*\|?[\s:|-]+\|?\s*$/.test(line);
  }

  function splitTableRow(line) {
    let value = String(line).trim();
    if (value.startsWith("|")) value = value.slice(1);
    if (value.endsWith("|")) value = value.slice(0, -1);
    return value.split("|").map((cell) => cell.trim());
  }

  function renderMarkdown(source) {
    const lines = String(source || "").replace(/\r\n?/g, "\n").split("\n");
    const output = [];
    let index = 0;
    while (index < lines.length) {
      const line = lines[index];
      if (!line.trim()) {
        index += 1;
        continue;
      }
      const fence = line.match(/^\s*(```|~~~)\s*([A-Za-z0-9_+-]*)\s*$/);
      if (fence) {
        const marker = fence[1];
        const language = fence[2] || "";
        const code = [];
        index += 1;
        while (index < lines.length && !lines[index].trim().startsWith(marker)) {
          code.push(lines[index]);
          index += 1;
        }
        if (index < lines.length) index += 1;
        const cls = language ? ` class="language-${language.replace(/[^A-Za-z0-9_-]/g, "")}"` : "";
        output.push(`<pre><code${cls}>${escapeHtml(code.join("\n"))}</code></pre>`);
        continue;
      }
      const heading = line.match(/^\s*(#{1,6})\s+(.*)$/);
      if (heading) {
        const level = heading[1].length;
        output.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
        index += 1;
        continue;
      }
      if (/^\s*(---+|\*\*\*+|___+)\s*$/.test(line)) {
        output.push("<hr>");
        index += 1;
        continue;
      }
      if (/^\s*>/.test(line)) {
        const quote = [];
        while (index < lines.length && /^\s*>/.test(lines[index])) {
          quote.push(lines[index].replace(/^\s*>\s?/, ""));
          index += 1;
        }
        output.push(`<blockquote>${inlineMarkdown(quote.join(" "))}</blockquote>`);
        continue;
      }
      const listMatch = line.match(/^\s*([-*+]|\d+\.)\s+(.*)$/);
      if (listMatch) {
        const ordered = /^\d+\.$/.test(listMatch[1]);
        const items = [];
        while (index < lines.length) {
          const match = lines[index].match(/^\s*([-*+]|\d+\.)\s+(.*)$/);
          if (!match || /^\d+\.$/.test(match[1]) !== ordered) break;
          items.push(`<li>${inlineMarkdown(match[2])}</li>`);
          index += 1;
        }
        const tag = ordered ? "ol" : "ul";
        output.push(`<${tag}>${items.join("")}</${tag}>`);
        continue;
      }
      if (
        line.includes("|") &&
        index + 1 < lines.length &&
        isTableSeparator(lines[index + 1])
      ) {
        const header = splitTableRow(line);
        const rows = [];
        index += 2;
        while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
          rows.push(splitTableRow(lines[index]));
          index += 1;
        }
        const head = header.map((cell) => `<th>${inlineMarkdown(cell)}</th>`).join("");
        const body = rows
          .map(
            (row) =>
              `<tr>${row.map((cell) => `<td>${inlineMarkdown(cell)}</td>`).join("")}</tr>`
          )
          .join("");
        output.push(
          `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`
        );
        continue;
      }
      const paragraph = [line.trim()];
      index += 1;
      while (index < lines.length && lines[index].trim()) {
        const next = lines[index];
        if (
          /^\s*(```|~~~|#{1,6}\s|>|([-*+]|\d+\.)\s)/.test(next) ||
          /^\s*(---+|\*\*\*+|___+)\s*$/.test(next)
        ) {
          break;
        }
        paragraph.push(next.trim());
        index += 1;
      }
      output.push(`<p>${inlineMarkdown(paragraph.join(" "))}</p>`);
    }
    return output.join("\n");
  }

  if (els.toggle) els.toggle.addEventListener("click", () => toggle());
  if (els.treeToggle) els.treeToggle.addEventListener("click", () => toggleTree());
  if (els.close) els.close.addEventListener("click", () => setOpen(false));
  if (els.refresh) {
    els.refresh.addEventListener("click", () => {
      state.entries.clear();
      state.contents.clear();
      state.errors.clear();
      state.diffs.clear();
      state.diffLoading.clear();
      state.diffErrors.clear();
      state.status.clear();
      state.searchResults = null;
      state.searchError = null;
      if (state.active) {
        loadContent(state.active, true);
        if (state.mode.get(state.active) === "diff") loadDiff(state.active);
      }
      loadRoot();
      render();
    });
  }

  if (els.resizer) {
    els.resizer.addEventListener("pointerdown", (event) => {
      state.dragging = true;
      state.dragStartX = event.clientX;
      state.dragStartWidth = state.width;
      els.stageBody.classList.add("filepane-resizing");
      if (els.resizer.setPointerCapture) {
        try {
          els.resizer.setPointerCapture(event.pointerId);
        } catch (error) {}
      }
      event.preventDefault();
    });
    window.addEventListener("pointermove", (event) => {
      if (state.dragging) {
        const beforeTree = effectiveTreeVisible();
        const width = state.dragStartWidth + (state.dragStartX - event.clientX);
        state.widthRatio = clampPaneRatio(width / stageWidth());
        applyPaneWidth();
        applyTreeWidth();
        if (beforeTree !== effectiveTreeVisible()) renderBody();
        else updateTreeToggle();
        return;
      }
      if (state.treeDragging) {
        const width =
          state.treeDragStartWidth + (event.clientX - state.treeDragStartX);
        state.treeRatio = clampTreeRatio(width / Math.max(1, state.width));
        applyTreeWidth();
      }
    });
    window.addEventListener("pointerup", () => {
      if (!state.dragging && !state.treeDragging) return;
      state.dragging = false;
      state.treeDragging = false;
      els.stageBody.classList.remove("filepane-resizing", "filepane-tree-resizing");
      saveGlobal();
    });
  }

  window.addEventListener("resize", () => {
    const beforeTree = effectiveTreeVisible();
    applyPaneWidth();
    applyTreeWidth();
    handleFilepaneAuto();
    if (beforeTree !== effectiveTreeVisible()) renderBody();
    else updateTreeToggle();
    updateToggle();
  });

  window.agenthubFilePane = {
    setProject,
    setPanel,
    toggle,
    layoutChanged,
    open: () => setOpen(true),
    close: () => setOpen(false),
  };

  render();
  applyVisibility();
})();
