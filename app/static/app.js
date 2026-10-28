const $ = (id) => document.getElementById(id);

const els = {
  tree: $("tree"),
  search: $("search"),
  sidebar: $("sidebar"),
  resizer: $("resizer"),
  sidebarToggle: $("sidebar-toggle"),
  overlay: $("drag-overlay"),
  frame: $("oc-frame"),
  empty: $("empty"),
  loading: $("frame-loading"),
  crumbs: $("crumbs"),
  nodeSummary: $("node-summary"),
  updatedAt: $("updated-at"),
  newSession: $("new-session"),
  reload: $("reload-frame"),
  openWindow: $("open-window"),
  toast: $("toast"),
  stageSubtitle: $("stage-subtitle"),
  stageBody: $("stage-body"),
  stageStatus: $("stage-status"),
  statusGit: $("status-git"),
  statusBusy: $("status-busy"),
  statusPermissions: $("status-permissions"),
  statusQuestions: $("status-questions"),
  statusUpdated: $("status-updated"),
  panelTabs: $("panel-tabs"),
  panelOc: $("panel-oc"),
  panelHost: $("panel-host"),
  panelProject: $("panel-project"),
  panelGit: $("panel-git"),
  panelSessions: $("panel-sessions"),
  tabGitCount: $("tab-git-count"),
  tabSessionCount: $("tab-session-count"),
  hostSummary: $("host-summary"),
  hostBody: $("host-body"),
  hostRefresh: $("host-refresh"),
  projectSummary: $("project-summary"),
  projectBody: $("project-body"),
  projectRefresh: $("project-refresh"),
  gitSummary: $("git-summary"),
  gitFiles: $("git-files"),
  gitRefresh: $("git-refresh"),
  sessionsSummary: $("sessions-summary"),
  sessionsList: $("sessions-list"),
  sessionsNew: $("sessions-new"),
  sessionsRefresh: $("sessions-refresh"),
  copyLink: $("copy-link"),
  themeToggle: $("theme-toggle"),
  inboxButton: $("inbox-button"),
  inboxCount: $("inbox-count"),
  inboxView: $("inbox-view"),
  inboxBack: $("inbox-back"),
  inboxRefresh: $("inbox-refresh"),
  inboxSummary: $("inbox-summary"),
  inboxList: $("inbox-list"),
  inboxDetail: $("inbox-detail"),
  settingsButton: $("settings-button"),
  settingsDialog: $("settings-dialog"),
  settingsClose: $("settings-close"),
  settingsTheme: $("settings-theme"),
  settingsRefresh: $("settings-refresh"),
  settingRefresh: $("setting-refresh"),
  settingNodes: $("setting-nodes"),
  settingName: $("setting-name"),
  settingNameSave: $("setting-name-save"),
  hubName: $("hub-name"),
  hostsList: $("hosts-list"),
  hostAdd: $("host-add"),
  hostForm: $("host-form"),
  hostName: $("host-name"),
  hostApi: $("host-api"),
  hostWeb: $("host-web"),
  hostUsername: $("host-username"),
  hostPasswordEnv: $("host-password-env"),
  hostPort: $("host-port"),
  hostCancel: $("host-cancel"),
  hostSave: $("host-save"),
};

const storage = {
  get(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      return raw == null ? fallback : JSON.parse(raw);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    localStorage.setItem(key, JSON.stringify(value));
  },
};

const state = {
  config: null,
  tree: null,
  query: "",
  collapsedNodes: new Set(storage.get("agenthub.collapsedNodes", [])),
  expanded: new Set(storage.get("agenthub.expanded", [])),
  sessions: new Map(),
  favorites: new Set(storage.get("agenthub.favorites", [])),
  lastSessions: storage.get("agenthub.lastSessions", {}),
  selected: storage.get("agenthub.selected", null),
  sidebarRatio: loadSidebarRatio(),
  sidebarCollapsed: false,
  sidebarAuto: false,
  lastSidebarNarrow: window.innerWidth < 820,
  restored: false,
  frameTimer: null,
  projectStatus: null,
  statusLoading: false,
  statusPending: false,
  hostStatus: null,
  hostView: null,
  hostLoading: false,
  settings: null,
  editingHost: null,
  activePanel: storage.get("agenthub.activePanel", "oc"),
  openToken: 0,
  frameBooted: false,
  switching: false,
  pendingActivityRefresh: false,
  lastActivityRefresh: 0,
  treeLoading: false,
  treePending: false,
  inbox: null,
  inboxLoading: false,
  inboxOpen: false,
  inboxSelectedId: null,
  inboxContent: new Map(),
};

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

async function api(url, options = {}) {
  const headers = { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}) };
  const response = await fetch(url, { ...options, headers: { ...headers, ...(options.headers || {}) } });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const data = await response.json();
      if (data && data.detail) detail = data.detail;
    } catch {}
    throw new Error(detail);
  }
  return response.json();
}

let toastTimer = null;
function toast(message) {
  els.toast.textContent = message;
  els.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    els.toast.hidden = true;
  }, 4200);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {}
  const area = document.createElement("textarea");
  area.value = text;
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.append(area);
  area.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {}
  area.remove();
  return ok;
}

function favKey(nodeName, path) {
  return `${nodeName}:${path}`;
}

function sessionKey(nodeName, path) {
  return `${nodeName}:${path}`;
}

function rememberSession(node, project, sessionId, title) {
  if (!sessionId) return;
  state.lastSessions[sessionKey(node.name, project.path)] = {
    id: sessionId,
    title: title || null,
    at: Date.now(),
  };
  storage.set("agenthub.lastSessions", state.lastSessions);
}

function statusText(status) {
  if (!status) return "";
  if (typeof status === "string") return status;
  return status.type || status.status || "";
}

function isBusy(status) {
  const text = statusText(status).toLowerCase();
  return text.includes("busy") || text.includes("running") || text.includes("working");
}

function relTime(timestamp) {
  if (!timestamp) return "";
  const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
  if (seconds < 45) return "now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d`;
  const months = Math.round(days / 30);
  if (months < 12) return `${months}mo`;
  return `${Math.round(months / 12)}y`;
}

function clockTime(timestamp) {
  if (!timestamp) return "";
  return new Date(timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function nodeByName(name) {
  return state.tree?.nodes?.find((node) => node.name === name) || null;
}

function projectByName(node, path) {
  return node?.projects?.find((project) => project.path === path) || null;
}

function proxyOrigin(node) {
  const protocol = location.protocol === "https:" ? "https:" : "http:";
  return `${protocol}//${location.hostname}:${node.proxy_port}`;
}

function b64url(value) {
  const bytes = new TextEncoder().encode(value);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function serverSessionUrl(node, sessionId) {
  return `${proxyOrigin(node)}/server/${b64url(proxyOrigin(node))}/session/${encodeURIComponent(sessionId)}`;
}

function frameUrl(node, project, sessionId) {
  if (project.kind === "other") {
    return sessionId ? serverSessionUrl(node, sessionId) : `${proxyOrigin(node)}/`;
  }
  const base = `${proxyOrigin(node)}/${project.b64}`;
  return sessionId ? `${base}/session/${encodeURIComponent(sessionId)}` : base;
}

function draftUrl(node, draftId, directory) {
  const params = new URLSearchParams({ draftId, directory });
  return `${proxyOrigin(node)}/new-session?${params}`;
}

function selectionHash(selection) {
  if (!selection) return "";
  const session = selection.sessionId ? `/${encodeURIComponent(selection.sessionId)}` : "";
  return `#/${encodeURIComponent(selection.node)}/${selection.b64}${session}`;
}

function updateHash() {
  const hash = selectionHash(state.selected);
  if (hash && location.hash !== hash) history.replaceState(null, "", hash);
}

function handleFrameRoute(event) {
  if (!state.selected || !state.frameBooted) return;
  const data = event.data;
  if (!data || data.source !== "agenthub" || data.type !== "route") return;
  const node = nodeByName(state.selected.node);
  if (!node || event.origin !== proxyOrigin(node)) return;

  const pathname = typeof data.pathname === "string" ? data.pathname : "";
  const search = typeof data.search === "string" ? data.search : "";
  let draftId = null;
  try {
    draftId = new URLSearchParams(search).get("draftId");
  } catch {}
  if (draftId) {
    const project = projectByName(node, state.selected.path);
    if (project) {
      const key = sessionKey(node.name, project.path);
      const entry = state.lastSessions[key] || {};
      if (entry.draftId !== draftId) {
        state.lastSessions[key] = { ...entry, draftId, at: Date.now() };
        storage.set("agenthub.lastSessions", state.lastSessions);
      }
    }
  }

  const match = pathname.match(/\/session\/([^/?#]+)/);
  if (!match) return;

  let sessionId;
  try {
    sessionId = decodeURIComponent(match[1]);
  } catch {
    return;
  }
  if (!sessionId.startsWith("ses_") || state.selected.sessionId === sessionId) return;

  const project = projectByName(node, state.selected.path);
  if (!project) return;
  const entry = state.sessions.get(sessionKey(node.name, project.path));
  const hit = entry?.data?.find((session) => session.id === sessionId);

  state.selected.sessionId = sessionId;
  state.selected.title = hit?.title || null;
  storage.set("agenthub.selected", state.selected);
  rememberSession(node, project, sessionId, hit?.title);
  markActivityRead(node, project, sessionId);
  updateHash();
  updateCrumbs();
  renderTree();
  loadSessions(node, project, true, true);
}

function readHash() {
  const raw = location.hash.replace(/^#\/?/, "");
  if (!raw) return null;
  const [nodeName, b64, sessionId] = raw.split("/");
  if (!nodeName || !b64) return null;
  const node = nodeByName(decodeURIComponent(nodeName));
  const project = node?.projects?.find((item) => item.b64 === b64);
  if (!node || !project) return null;
  return { node, project, sessionId: sessionId ? decodeURIComponent(sessionId) : null };
}

function activityInfo(activity, compact = false) {
  if (!activity) return null;
  const permissions = Number(activity.permissions || 0);
  const questions = Number(activity.questions || 0);
  if (permissions) {
    return {
      kind: "permission",
      text: compact ? `perm${permissions > 1 ? ` ${permissions}` : ""}` : `permission${permissions > 1 ? ` ${permissions}` : ""}`,
      title: `${permissions} permission request(s)`,
    };
  }
  if (questions) {
    return {
      kind: "question",
      text: compact ? `ask${questions > 1 ? ` ${questions}` : ""}` : `answer${questions > 1 ? ` ${questions}` : ""}`,
      title: `${questions} question(s)`,
    };
  }
  if (activity.error) return { kind: "error", text: "error", title: "Session error" };
  if (activity.done) return { kind: "done", text: "done", title: "Response finished" };
  if (activity.busy) return { kind: "busy", text: compact ? "run" : "busy", title: "Session running" };
  return null;
}

function projectActivityInfo(project) {
  const permissions = Number(project.pending_permissions || 0);
  const questions = Number(project.pending_questions || 0);
  const errors = Number(project.error_sessions || 0);
  const done = Number(project.done_sessions || 0);
  const busy = Number(project.busy_sessions || 0);
  const info = activityInfo({
    permissions,
    questions,
    error: errors > 0,
    done: done > 0,
    busy: busy > 0,
  });
  if (!info) return null;
  if (info.kind === "error" && errors > 1) info.text = `error ${errors}`;
  if (info.kind === "done" && done > 1) info.text = `done ${done}`;
  if (info.kind === "busy" && busy > 1) info.text = `busy ${busy}`;
  const details = [];
  if (permissions) details.push(`${permissions} permission`);
  if (questions) details.push(`${questions} question`);
  if (errors) details.push(`${errors} error`);
  if (done) details.push(`${done} done`);
  if (busy) details.push(`${busy} busy`);
  if (details.length) info.title = details.join(" · ");
  return info;
}

function activityBadge(info, compact = false) {
  return el(
    "span",
    { class: `activity-badge ${info.kind}${compact ? " tiny" : ""}`, title: info.title },
    info.text,
  );
}

function waitingCount() {
  const tree = state.tree;
  if (!tree) return 0;
  return (tree.nodes || []).reduce(
    (count, node) =>
      count +
      (node.projects || []).reduce(
        (sum, project) =>
          sum +
          Number(project.pending_permissions || 0) +
          Number(project.pending_questions || 0) +
          Number(project.error_sessions || 0) +
          Number(project.done_sessions || 0),
        0,
      ),
    0,
  );
}

function updateInboxBadge() {
  if (!els.inboxCount) return;
  const count = waitingCount();
  els.inboxCount.textContent = String(count);
  els.inboxCount.hidden = count <= 0;
}

let inboxRefreshTimer = null;

function scheduleInboxRefresh(delay = 700) {
  if (!state.inboxOpen) return;
  clearTimeout(inboxRefreshTimer);
  inboxRefreshTimer = setTimeout(() => fetchInbox(), delay);
}

function inboxButton(label, handler) {
  return el("button", { class: "ghost-button small", onclick: handler }, label);
}

function inboxOpenButton(item) {
  return inboxButton("Open", () => inboxOpen(item));
}

function inboxErrorMessage(value) {
  if (!value) return "Session error";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function inboxContentKey(item) {
  return `${item.node}:${item.sessionID}:${item.updated || 0}`;
}

async function loadInboxContent(item) {
  const key = inboxContentKey(item);
  if (state.inboxContent.has(key)) return state.inboxContent.get(key);
  state.inboxContent.set(key, null);
  try {
    const params = new URLSearchParams({ node: item.node, sessionID: item.sessionID });
    const data = await api(`/api/inbox/content?${params}`);
    state.inboxContent.set(key, data);
    return data;
  } catch {
    const data = { content: "" };
    state.inboxContent.set(key, data);
    return data;
  }
}

function renderInboxDetail(item) {
  const head = el(
    "div",
    { class: "inbox-detail-head" },
    el("span", { class: `activity-badge ${item.kind}` }, item.kind === "question" ? "answer" : item.kind),
    el("span", { class: "inbox-item-title" }, [item.projectName, item.sessionTitle].filter(Boolean).join(" / ") || item.sessionTitle),
    el("span", { class: "inbox-item-meta" }, `${item.node} · ${relTime(item.updated)}`),
  );

  const content = el("div", { class: "inbox-content" });
  content.append(el("div", { class: "inbox-content-label" }, item.kind === "done" ? "Response" : "Recent context"));
  const contentText = el("div", { class: "inbox-content-text" }, "Loading…");
  content.append(contentText);
  loadInboxContent(item).then((data) => {
    if (state.inboxSelectedId !== item.id) return;
    contentText.textContent = (data?.content || "").trim() || "(No text content)";
  });

  const body = el("div", { class: "inbox-item-body" });
  const actions = el("div", { class: "inbox-detail-actions" });

  if (item.kind === "permission") {
    const details = [`Permission: ${item.permission || "request"}`];
    if (Array.isArray(item.patterns) && item.patterns.length) details.push(item.patterns.join(", "));
    body.append(el("div", {}, details.join(" · ")));
    actions.append(
      inboxButton("Allow once", () => inboxPermission(item, "once")),
      inboxButton("Always", () => inboxPermission(item, "always")),
      inboxButton("Reject", () => inboxPermission(item, "reject")),
      inboxOpenButton(item),
    );
  } else if (item.kind === "question") {
    const groups = [];
    const questions = el("div", { class: "inbox-questions" });
    const list = Array.isArray(item.questions) ? item.questions : [];
    list.forEach((question, index) => {
      const options = Array.isArray(question.options) ? question.options : [];
      const inputs = [];
      const name = `inbox_${item.requestID}_${index}`;
      const box = el(
        "div",
        { class: "inbox-question" },
        el("div", { class: "inbox-question-title" }, question.header || `Question ${index + 1}`),
        el("div", { class: "inbox-question-text" }, question.question || ""),
      );
      if (options.length) {
        const choices = el("div", { class: "inbox-options" });
        for (const option of options) {
          const input = el("input", {
            type: question.multiple ? "checkbox" : "radio",
            name,
            value: option.label || "",
          });
          inputs.push(input);
          choices.append(
            el(
              "label",
              { class: "inbox-option" },
              input,
              el(
                "span",
                {},
                option.label || "",
                option.description ? el("span", { class: "inbox-option-desc" }, ` — ${option.description}`) : null,
              ),
            ),
          );
        }
        box.append(choices);
      }
      let custom = null;
      if (question.custom || !options.length) {
        custom = el("input", { class: "inbox-input", type: "text", placeholder: "Your answer…" });
        box.append(custom);
      }
      groups.push({ inputs, custom });
      questions.append(box);
    });
    body.append(questions);
    actions.append(
      inboxButton("Send answer", async () => {
        const answers = groups.map((group) => {
          const selected = group.inputs.filter((input) => input.checked).map((input) => input.value);
          if (group.custom?.value.trim()) selected.push(group.custom.value.trim());
          return selected;
        });
        await inboxQuestion(item, answers, false);
      }),
      inboxButton("Reject", () => inboxQuestion(item, null, true)),
      inboxOpenButton(item),
    );
  } else if (item.kind === "error") {
    body.append(el("pre", { class: "inbox-error" }, inboxErrorMessage(item.error)));
    const textarea = el("textarea", { class: "inbox-textarea", placeholder: "Reply to this session…" });
    body.append(textarea);
    actions.append(
      inboxButton("Send reply", () => inboxPrompt(item, textarea.value)),
      inboxOpenButton(item),
      inboxButton("Mark read", () => inboxRead(item)),
    );
  } else {
    body.append(el("div", {}, "Reply to continue this session."));
    const textarea = el("textarea", { class: "inbox-textarea", placeholder: "Reply to this session…" });
    body.append(textarea);
    actions.append(
      inboxButton("Send reply", () => inboxPrompt(item, textarea.value)),
      inboxOpenButton(item),
      inboxButton("Mark read", () => inboxRead(item)),
    );
  }

  els.inboxDetail.replaceChildren(head, content, body, actions);
}

function renderInboxList() {
  if (!state.inbox) {
    els.inboxList.replaceChildren(el("div", { class: "inbox-empty" }, "Loading inbox…"));
    els.inboxDetail.replaceChildren(el("div", { class: "inbox-empty" }, "Loading…"));
    return;
  }
  const counts = state.inbox.counts || {};
  els.inboxSummary.textContent = counts.total
    ? `${counts.permission || 0} permission · ${counts.question || 0} answer · ${counts.error || 0} error · ${counts.done || 0} done`
    : "All caught up";

  const items = state.inbox.items || [];
  if (!items.length) {
    els.inboxList.replaceChildren(el("div", { class: "inbox-empty" }, "No items waiting."));
    els.inboxDetail.replaceChildren(el("div", { class: "inbox-empty" }, "All caught up."));
    state.inboxSelectedId = null;
    return;
  }

  if (!state.inboxSelectedId || !items.some((item) => item.id === state.inboxSelectedId)) {
    state.inboxSelectedId = items[0].id;
  }

  const fragment = document.createDocumentFragment();
  for (const item of items) {
    const selected = item.id === state.inboxSelectedId;
    fragment.append(
      el(
        "div",
        {
          class: `inbox-item${selected ? " selected" : ""}`,
          onclick: () => {
            state.inboxSelectedId = item.id;
            renderInboxList();
          },
        },
        el(
          "div",
          { class: "inbox-item-head" },
          el("span", { class: `activity-badge ${item.kind}` }, item.kind === "question" ? "answer" : item.kind),
          el("span", { class: "inbox-item-title" }, item.sessionTitle),
        ),
        el("div", { class: "inbox-item-meta" }, `${item.projectName || item.node} · ${relTime(item.updated)}`),
      ),
    );
  }
  els.inboxList.replaceChildren(fragment);
  const current = items.find((item) => item.id === state.inboxSelectedId);
  if (current) renderInboxDetail(current);
}

function renderInbox() {
  renderInboxList();
}

async function fetchInbox() {
  if (state.inboxLoading) return;
  state.inboxLoading = true;
  try {
    const previous = state.inboxSelectedId;
    state.inbox = await api("/api/inbox");
    const items = state.inbox.items || [];
    if (!items.some((item) => item.id === previous)) {
      state.inboxSelectedId = items[0]?.id || null;
    }
    renderInboxList();
  } catch (error) {
    toast(`Failed to load inbox: ${error.message || error}`);
  } finally {
    state.inboxLoading = false;
  }
  updateInboxBadge();
}

function openInbox() {
  state.inboxOpen = true;
  renderPanels();
  renderInboxList();
  fetchInbox();
}

function closeInbox() {
  state.inboxOpen = false;
  renderPanels();
}

async function inboxPermission(item, reply) {
  try {
    await api("/api/inbox/permission", {
      method: "POST",
      body: JSON.stringify({ node: item.node, requestID: item.requestID, reply }),
    });
    await fetchInbox();
    scheduleActivityRefresh();
  } catch (error) {
    toast(`Permission reply failed: ${error.message || error}`);
  }
}

async function inboxQuestion(item, answers, reject) {
  try {
    await api("/api/inbox/question", {
      method: "POST",
      body: JSON.stringify({ node: item.node, requestID: item.requestID, answers, reject }),
    });
    await fetchInbox();
    scheduleActivityRefresh();
  } catch (error) {
    toast(`Question reply failed: ${error.message || error}`);
  }
}

async function inboxPrompt(item, text) {
  if (!text.trim()) return;
  try {
    await api("/api/inbox/prompt", {
      method: "POST",
      body: JSON.stringify({ node: item.node, sessionID: item.sessionID, text }),
    });
    await fetchInbox();
    scheduleActivityRefresh();
  } catch (error) {
    toast(`Reply failed: ${error.message || error}`);
  }
}

async function inboxRead(item) {
  try {
    await api("/api/inbox/read", {
      method: "POST",
      body: JSON.stringify({ node: item.node, sessionID: item.sessionID }),
    });
    await fetchInbox();
    scheduleActivityRefresh();
  } catch (error) {
    toast(`Failed to mark read: ${error.message || error}`);
  }
}

function inboxOpen(item) {
  const node = nodeByName(item.node);
  if (!node) return;
  let project = projectByName(node, item.projectPath);
  if (!project && item.projectPath) {
    project = {
      path: item.projectPath,
      name: item.projectName || (item.projectPath === "__other__" ? "Other" : "Project"),
      b64: item.projectB64 || (item.projectPath === "__other__" ? "other" : ""),
      kind: item.projectPath === "__other__" ? "other" : "project",
      vcs: null,
    };
  }
  if (!project) return;
  closeInbox();
  openProject(node, project, item.sessionID, { panel: "oc" });
}

function renderHostIcon(online) {
  const wrapper = el("span", { class: "host-icon", title: online ? "online" : "offline" });
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("fill", "none");
  svg.setAttribute("aria-hidden", "true");

  const screen = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  screen.setAttribute("x", "1.5");
  screen.setAttribute("y", "2.5");
  screen.setAttribute("width", "13");
  screen.setAttribute("height", "9");
  screen.setAttribute("rx", "1.5");
  screen.setAttribute("stroke", "currentColor");
  screen.setAttribute("stroke-width", "1.2");

  const stand = document.createElementNS("http://www.w3.org/2000/svg", "path");
  stand.setAttribute("d", "M6 14h4M8 11.5V14");
  stand.setAttribute("stroke", "currentColor");
  stand.setAttribute("stroke-width", "1.2");
  stand.setAttribute("stroke-linecap", "round");

  svg.append(screen, stand);
  wrapper.append(svg, el("span", { class: `host-status ${online ? "online" : "offline"}` }));
  return wrapper;
}

function renderTree() {
  els.tree.replaceChildren();
  if (!state.tree) {
    els.tree.append(el("div", { class: "session-empty" }, "loading…"));
    return;
  }

  const query = state.query.trim().toLowerCase();
  const favorites = [];
  for (const node of state.tree.nodes) {
    for (const project of node.projects || []) {
      if (state.favorites.has(favKey(node.name, project.path))) {
        favorites.push({ node, project });
      }
    }
  }

  const visibleFavorites = favorites.filter(({ node, project }) => matches(node, project, query));
  if (visibleFavorites.length) {
    const favoritesSection = el("div", { class: "favorites" });
    favoritesSection.append(el("div", { class: "section-title" }, "Favorites"));
    for (const { node, project } of visibleFavorites) {
      favoritesSection.append(
        el(
          "div",
          { class: "favorite-row", title: `${node.name} ${project.path}`, onclick: () => openProject(node, project) },
          el(
            "button",
            {
              class: "star on",
              title: "Remove from favorites",
              onclick: (event) => {
                event.stopPropagation();
                toggleFavorite(node, project);
              },
            },
            "★",
          ),
          el("span", { class: "project-name" }, project.name),
          el("span", { class: "favorite-node" }, node.name),
        ),
      );
    }
    els.tree.append(favoritesSection);
  }

  let visibleNodes = 0;
  for (const node of state.tree.nodes) {
    const nodeMatches = !query || node.name.toLowerCase().includes(query);
    const projects = (node.projects || []).filter((project) => nodeMatches || matches(node, project, query));
    if (!nodeMatches && projects.length === 0) continue;
    visibleNodes += 1;

    const collapsed = !query && state.collapsedNodes.has(node.name);
    const head = el(
      "div",
      {
        class: `node-head${state.hostView === node.name ? " active" : ""}`,
        title: node.error
          ? `${node.name}: ${node.error}`
          : `${node.name} ${node.version || ""} · click to open host status`,
        onclick: () => openHost(node),
      },
      el(
        "button",
        {
          class: "host-toggle",
          title: collapsed ? "Expand projects" : "Collapse projects",
          "aria-label": collapsed ? "Expand projects" : "Collapse projects",
          onclick: (event) => {
            event.stopPropagation();
            if (collapsed) state.collapsedNodes.delete(node.name);
            else state.collapsedNodes.add(node.name);
            storage.set("agenthub.collapsedNodes", [...state.collapsedNodes]);
            renderTree();
          },
        },
        renderHostIcon(node.online),
      ),
      el("span", { class: "node-name" }, node.name),
      el("span", { class: "node-meta" }, node.error || node.version || ""),
    );

    const projectsEl = el("div", { class: "node-projects" });
    for (const project of projects) {
      projectsEl.append(renderProject(node, project));
    }
    if (!projects.length) {
      projectsEl.append(el("div", { class: "session-empty" }, node.online ? "no projects" : "offline"));
    }

    els.tree.append(el("div", { class: `node${collapsed ? " collapsed" : ""}` }, head, projectsEl));
  }

  if (!visibleNodes) {
    els.tree.append(el("div", { class: "session-empty" }, query ? "No matching projects" : "No projects yet"));
  }
}

function matches(node, project, query) {
  if (!query) return true;
  return `${node.name} ${project.name} ${project.path}`.toLowerCase().includes(query);
}

function renderProject(node, project) {
  const key = sessionKey(node.name, project.path);
  const selected = state.selected?.node === node.name && state.selected?.path === project.path;
  const expanded = state.expanded.has(key);
  const favorite = state.favorites.has(favKey(node.name, project.path));

  const caret = el(
    "button",
    {
      class: "project-caret",
      title: expanded ? "Collapse sessions" : "Expand sessions",
      onclick: (event) => {
        event.stopPropagation();
        toggleProject(node, project);
      },
    },
    expanded ? "▾" : "▸",
  );

  const star = el(
    "button",
    {
      class: `star${favorite ? " on" : ""}`,
      title: favorite ? "Remove from favorites" : "Add to favorites",
      onclick: (event) => {
        event.stopPropagation();
        toggleFavorite(node, project);
      },
    },
    favorite ? "★" : "☆",
  );

  const sessionCount = Number(project.session_count || 0);
  const lastActivity = project.last_session || project.updated;
  const updated = relTime(lastActivity);
  const pathLabel =
    project.kind === "other"
      ? `${Array.isArray(project.paths) ? project.paths.length : 0} directories`
      : project.path;
  const tip = [pathLabel, sessionCount ? `${sessionCount} sessions` : null, updated ? `updated ${updated} ago` : null]
    .filter(Boolean)
    .join("\n");

  const meta = el("span", { class: "project-meta" });
  if (sessionCount) meta.append(el("span", { class: "count", title: `${sessionCount} sessions` }, sessionCount));
  if (updated) meta.append(el("span", { title: "last session activity" }, updated));
  const activity = projectActivityInfo(project);

  const row = el(
    "div",
    {
      class: `project-row${selected ? " active" : ""}${project.kind === "other" ? " other" : ""}`,
      title: tip,
      role: "button",
      tabindex: "0",
      onclick: () => openProject(node, project),
      onkeydown: (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          openProject(node, project);
        }
      },
    },
    caret,
    el("span", { class: "project-main" }, el("span", { class: "project-name" }, project.name)),
    activity ? activityBadge(activity) : null,
    el(
      "span",
      { class: "project-tags" },
      project.kind === "other" ? el("span", { class: "vcs other" }, "other") : null,
      project.vcs ? el("span", { class: "vcs" }, project.vcs) : null,
    ),
    meta,
    el("span", { class: "project-actions" }, star),
  );

  const wrapper = el("div", { class: "project" }, row);
  if (expanded) {
    wrapper.append(renderSessions(node, project));
  }
  return wrapper;
}

function renderSessions(node, project) {
  const key = sessionKey(node.name, project.path);
  const entry = state.sessions.get(key);
  const container = el("div", { class: "sessions" });

  if (!entry || entry.loading) {
    container.append(el("div", { class: "session-loading" }, "loading sessions…"));
    return container;
  }
  if (entry.error) {
    container.append(el("div", { class: "session-empty" }, entry.error));
    return container;
  }
  if (!entry.data.length) {
    container.append(
      el(
        "div",
        { class: "session-empty" },
        "no sessions · ",
        el(
          "a",
          {
            href: "#",
            onclick: (event) => {
              event.preventDefault();
              openProject(node, project);
            },
          },
          "New session",
        ),
      ),
    );
    return container;
  }

  for (const session of entry.data) {
    const active = state.selected?.sessionId === session.id;
    container.append(
      el(
        "div",
        {
          class: `session-row${active ? " active" : ""}`,
          title: session.title,
          role: "button",
          tabindex: "0",
          onclick: () => openProject(node, project, session.id, { panel: "oc", title: session.title }),
          onkeydown: (event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              openProject(node, project, session.id, { panel: "oc", title: session.title });
            }
          },
        },
        el("span", { class: `dot ${isBusy(session.status) ? "busy" : ""}` }),
        el("span", { class: "session-title" }, session.title),
        activityInfo(session.activity, true) ? activityBadge(activityInfo(session.activity, true), true) : null,
        el("span", { class: "session-time" }, relTime(session.updated)),
      ),
    );
  }
  return container;
}

async function toggleFavorite(node, project) {
  const key = favKey(node.name, project.path);
  const adding = !state.favorites.has(key);
  if (adding) state.favorites.add(key);
  else state.favorites.delete(key);
  storage.set("agenthub.favorites", [...state.favorites]);
  renderTree();

  try {
    if (adding) {
      await api("/api/favorites", { method: "POST", body: JSON.stringify({ node: node.name, path: project.path }) });
    } else {
      const params = new URLSearchParams({ node: node.name, path: project.path });
      await api(`/api/favorites?${params}`, { method: "DELETE" });
    }
  } catch (error) {
    if (adding) state.favorites.delete(key);
    else state.favorites.add(key);
    storage.set("agenthub.favorites", [...state.favorites]);
    renderTree();
    toast(`Failed to sync favorite: ${error.message || error}`);
  }
}

async function loadFavorites() {
  const local = [...state.favorites];
  try {
    const data = await api("/api/favorites");
    if (Array.isArray(data.keys) && data.keys.length) {
      state.favorites = new Set(data.keys);
    } else if (local.length) {
      for (const key of local) {
        const index = key.indexOf(":");
        if (index < 0) continue;
        await api("/api/favorites", {
          method: "POST",
          body: JSON.stringify({ node: key.slice(0, index), path: key.slice(index + 1) }),
        }).catch(() => {});
      }
    }
    storage.set("agenthub.favorites", [...state.favorites]);
    renderTree();
  } catch {}
}

async function toggleProject(node, project) {
  const key = sessionKey(node.name, project.path);
  if (state.expanded.has(key)) {
    state.expanded.delete(key);
    storage.set("agenthub.expanded", [...state.expanded]);
    renderTree();
    return;
  }

  state.expanded.add(key);
  storage.set("agenthub.expanded", [...state.expanded]);
  renderTree();
  await loadSessions(node, project);
}

async function loadSessions(node, project, force = false, silent = false) {
  const key = sessionKey(node.name, project.path);
  const existing = state.sessions.get(key);
  if (existing?.loading) return existing.data || [];
  if (!force && existing?.data && !existing.error) return existing.data;

  if (!silent || !existing?.data) {
    state.sessions.set(key, { loading: true, data: existing?.data || [], error: null });
    renderTree();
    renderPanels();
  }

  try {
    const params = new URLSearchParams({ node: node.name, directory: project.path, limit: "500" });
    const data = await api(`/api/sessions?${params}`);
    const sessions = data.sessions || [];
    state.sessions.set(key, { loading: false, data: sessions, error: null });
    if (state.selected && sessionKey(state.selected.node, state.selected.path) === key && state.selected.sessionId) {
      const current = sessions.find((item) => item.id === state.selected.sessionId);
      if (current && current.title !== state.selected.title) {
        state.selected.title = current.title;
        storage.set("agenthub.selected", state.selected);
        updateCrumbs();
      }
    }
  } catch (error) {
    if (!silent) {
      state.sessions.set(key, { loading: false, data: [], error: String(error.message || error) });
    }
  }
  renderTree();
  renderPanels();
  return state.sessions.get(key)?.data || [];
}

async function waitForSessions(key, timeout = 8000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    const entry = state.sessions.get(key);
    if (entry && !entry.loading) return entry;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  return state.sessions.get(key) || null;
}

async function resolveLastSession(node, project) {
  const key = sessionKey(node.name, project.path);
  let entry = state.sessions.get(key);
  if (entry?.loading) entry = await waitForSessions(key);

  const remembered = state.lastSessions[key];
  const cached = entry?.data || [];
  const needsRefresh =
    !entry?.data ||
    Boolean(entry.error) ||
    cached.length === 0 ||
    Boolean(remembered?.id && !cached.some((session) => session.id === remembered.id));

  if (needsRefresh) {
    await loadSessions(node, project, true, true);
    entry = await waitForSessions(key);
  }

  const sessions = entry?.data || [];
  if (remembered?.id) {
    const hit = sessions.find((session) => session.id === remembered.id);
    if (hit) return hit;
  }
  return sessions[0] || null;
}

function hydrateExpanded() {
  if (!state.tree) return;
  for (const key of [...state.expanded]) {
    const index = key.indexOf(":");
    if (index < 0) continue;
    const nodeName = key.slice(0, index);
    const path = key.slice(index + 1);
    const node = nodeByName(nodeName);
    const project = projectByName(node, path);
    if (node && project) loadSessions(node, project);
  }
}

function restoreSelection() {
  if (state.restored) return;
  state.restored = true;
  const fromHash = readHash();
  if (fromHash) {
    openProject(fromHash.node, fromHash.project, fromHash.sessionId);
    return;
  }
  const selected = state.selected;
  if (!selected || !state.tree) return;
  const node = nodeByName(selected.node);
  const project = projectByName(node, selected.path);
  if (node && project) openProject(node, project, selected.sessionId);
  else {
    state.selected = null;
    storage.set("agenthub.selected", null);
    window.agenthubFilePane?.setProject(null);
  }
}

function setFrameSource(url, token) {
  let currentOrigin = null;
  let nextOrigin = null;
  try {
    currentOrigin = els.frame.src ? new URL(els.frame.src).origin : null;
  } catch {}
  try {
    nextOrigin = new URL(url).origin;
  } catch {}
  if (currentOrigin && nextOrigin && currentOrigin !== nextOrigin) {
    state.frameBooted = false;
    els.frame.src = "about:blank";
    requestAnimationFrame(() => {
      if (token === undefined || token === state.openToken) {
        state.frameBooted = false;
        els.frame.src = url;
      }
    });
    return;
  }
  state.frameBooted = false;
  els.frame.src = url;
}

async function openProject(node, project, sessionId = null, options = {}) {
  if (state.inboxOpen) closeInbox();
  state.hostView = null;
  const previous = state.selected;
  const sameProject = Boolean(previous && previous.node === node.name && previous.path === project.path);
  const key = sessionKey(node.name, project.path);
  const explicitSession = sessionId || null;
  const forceNew = options.forceNew === true;
  const token = ++state.openToken;
  const remembered = state.lastSessions[key];
  const cachedSessions = state.sessions.get(key)?.data || [];
  const cachedSession =
    !explicitSession && !forceNew
      ? remembered?.id
        ? cachedSessions.find((item) => item.id === remembered.id)
        : cachedSessions[0]
      : null;

  const initialSessionId =
    explicitSession ||
    (!forceNew ? remembered?.id : null) ||
    (!forceNew ? cachedSession?.id : null) ||
    null;
  const initialTitle =
    options.title ||
    (initialSessionId && initialSessionId === remembered?.id ? remembered.title : null) ||
    (cachedSession && initialSessionId === cachedSession.id ? cachedSession.title : null) ||
    null;
  const noSessions = Number(project.session_count || 0) === 0;
  const canShowFrame = Boolean(initialSessionId) || noSessions || forceNew;

  state.switching = true;
  state.frameBooted = false;
  state.selected = {
    node: node.name,
    path: project.path,
    b64: project.b64,
    name: project.name,
    sessionId: initialSessionId,
    title: initialTitle,
  };
  storage.set("agenthub.selected", state.selected);
  window.agenthubFilePane?.setProject({
    node: node.name,
    path: project.path,
    name: project.name,
    kind: project.kind || "project",
  });

  if (explicitSession) rememberSession(node, project, explicitSession, options.title);
  else if (initialSessionId && remembered?.id === initialSessionId && remembered.title && !state.selected.title) {
    state.selected.title = remembered.title;
  }

  if (options.panel) setActivePanel(options.panel, false);
  else if (explicitSession) setActivePanel("oc", false);
  else if (!state.activePanel) setActivePanel("oc", false);

  els.frame.hidden = false;
  els.empty.hidden = true;
  showFrameLoading();
  updateHash();
  updateCrumbs();
  updateActions();
  renderTree();
  markActivityRead(node, project, state.selected.sessionId);

  const finishSwitch = () => {
    if (token !== state.openToken) return;
    state.switching = false;
    if (state.pendingActivityRefresh) {
      state.pendingActivityRefresh = false;
      scheduleActivityRefresh(600);
    }
  };

  const applyFrame = () => {
    let url = frameUrl(node, project, state.selected.sessionId);
    if (!state.selected.sessionId && !forceNew && project.kind !== "other") {
      const draftId = state.lastSessions[key]?.draftId;
      if (draftId) url = draftUrl(node, draftId, project.path);
    }
    if (els.frame.src === url) reloadFrame();
    else setFrameSource(url, token);
    finishSwitch();
  };

  if (canShowFrame) {
    applyFrame();
  } else {
    let resolved = null;
    try {
      resolved = await resolveLastSession(node, project);
    } catch {}
    if (token !== state.openToken) return;

    if (resolved) {
      state.selected.sessionId = resolved.id;
      state.selected.title = resolved.title || null;
      storage.set("agenthub.selected", state.selected);
      rememberSession(node, project, resolved.id, resolved.title);
      updateHash();
      updateCrumbs();
      markActivityRead(node, project, resolved.id);
    } else {
      const entry = state.sessions.get(key);
      if (entry && !entry.error) toast("This project has no sessions yet. Opened a new session.");
    }
    applyFrame();
  }

  if (!explicitSession && !forceNew && initialSessionId) {
    const pendingSession = state.selected.sessionId;
    resolveLastSession(node, project)
      .then((resolved) => {
        if (token !== state.openToken || state.selected.sessionId !== pendingSession) return;
        if (!resolved || resolved.id === state.selected.sessionId) return;
        state.selected.sessionId = resolved.id;
        state.selected.title = resolved.title || null;
        storage.set("agenthub.selected", state.selected);
        rememberSession(node, project, resolved.id, resolved.title);
        updateHash();
        updateCrumbs();
        let url = frameUrl(node, project, resolved.id);
        if (els.frame.src === url) reloadFrame();
        else setFrameSource(url, token);
        markActivityRead(node, project, resolved.id);
      })
      .catch(() => {});
  }

  if (token !== state.openToken) return;
  finishSwitch();
  state.projectStatus = null;
  state.hostStatus = null;
  renderProjectStatus();
  loadProjectStatus();
  if (state.activePanel === "host") loadHostStatus();
  if (state.expanded.has(key) || state.activePanel === "sessions") {
    loadSessions(node, project, false, true);
  }
  renderPanels();
}

function showFrameLoading() {
  els.loading.hidden = false;
  clearTimeout(state.frameTimer);
  state.frameTimer = setTimeout(() => {
    els.loading.hidden = true;
  }, 15000);
}

function hideFrameLoading() {
  clearTimeout(state.frameTimer);
  els.loading.hidden = true;
}

function reloadFrame() {
  if (!state.selected) return;
  const node = nodeByName(state.selected.node);
  const project = projectByName(node, state.selected.path);
  if (!node || !project) return;
  const url = frameUrl(node, project, state.selected.sessionId);
  const separator = url.includes("?") ? "&" : "?";
  state.frameBooted = false;
  els.frame.src = `${url}${separator}_r=${Date.now()}`;
  showFrameLoading();
}

function updateCrumbs() {
  if (state.hostView) {
    const node = nodeByName(state.hostView);
    els.crumbs.textContent = node ? `${node.name}  /  host` : "Host status";
    return;
  }
  if (!state.selected) {
    els.crumbs.textContent = "Select a project";
    return;
  }
  const parts = [state.selected.node, state.selected.name];
  if (state.selected.sessionId) parts.push(state.selected.title || state.selected.sessionId);
  els.crumbs.textContent = parts.join("  /  ");
}

function updateActions() {
  const enabled = Boolean(state.selected);
  els.newSession.disabled = !enabled;
  els.reload.disabled = !enabled;
  els.openWindow.disabled = !enabled;
  els.copyLink.disabled = !enabled;
}

function setActivePanel(panel, rerender = true) {
  if (!["oc", "host", "project", "git", "sessions"].includes(panel)) panel = "oc";
  if (state.hostView && panel !== "host") {
    if (!state.selected) return;
    state.hostView = null;
  }
  state.activePanel = panel;
  storage.set("agenthub.activePanel", panel);
  window.agenthubFilePane?.setPanel(panel);
  if (!rerender) return;
  renderPanels();

  if (panel === "host" && hostTarget() && !state.hostStatus) loadHostStatus();
  if (panel === "project" && !state.projectStatus) loadProjectStatus();
  if (panel === "git" && !state.projectStatus) loadProjectStatus();
  if (panel === "sessions" && state.selected) {
    const node = nodeByName(state.selected.node);
    const project = projectByName(node, state.selected.path);
    if (node && project) loadSessions(node, project);
  }
}

function selectedProject() {
  if (!state.selected) return null;
  const node = nodeByName(state.selected.node);
  const project = projectByName(node, state.selected.path);
  return node && project ? { node, project } : null;
}

function updateTabBadges() {
  const status = state.projectStatus;
  const files = Number(status?.changes?.files || 0);
  els.tabGitCount.textContent = files ? String(files) : "";
  els.tabGitCount.hidden = !files;

  let count = 0;
  if (state.selected) {
    const entry = state.sessions.get(sessionKey(state.selected.node, state.selected.path));
    count = entry?.data?.length || 0;
  }
  els.tabSessionCount.textContent = count ? String(count) : "";
  els.tabSessionCount.hidden = !count;
}

function renderPanels() {
  if (state.inboxOpen) {
    els.inboxView.hidden = false;
    els.panelTabs.hidden = true;
    els.stageSubtitle.hidden = true;
    els.stageBody.hidden = true;
    return;
  }
  els.inboxView.hidden = true;
  els.stageBody.hidden = false;

  const hostView = Boolean(state.hostView && nodeByName(state.hostView));
  const selected = Boolean(state.selected);
  const visible = hostView || selected;
  const actions = els.panelTabs.querySelector(".panel-actions");
  const activePanel = hostView ? "host" : state.activePanel;

  els.panelTabs.hidden = !visible;
  els.stageSubtitle.hidden = !visible;
  if (actions) actions.hidden = hostView;

  if (!visible) {
    els.panelOc.hidden = false;
    els.panelHost.hidden = true;
    els.panelProject.hidden = true;
    els.panelGit.hidden = true;
    els.panelSessions.hidden = true;
    els.frame.hidden = true;
    els.empty.hidden = false;
    els.gitSummary.textContent = "";
    els.gitFiles.replaceChildren();
    els.sessionsSummary.textContent = "";
    els.sessionsList.replaceChildren();
    window.agenthubFilePane?.setProject(null);
    window.agenthubFilePane?.setPanel(activePanel);
    return;
  }

  for (const tab of els.panelTabs.querySelectorAll(".panel-tab")) {
    tab.hidden = hostView && tab.dataset.panel !== "host";
    tab.classList.toggle("active", tab.dataset.panel === activePanel);
  }

  els.panelOc.hidden = activePanel !== "oc";
  els.panelHost.hidden = activePanel !== "host";
  els.panelProject.hidden = activePanel !== "project";
  els.panelGit.hidden = activePanel !== "git";
  els.panelSessions.hidden = activePanel !== "sessions";
  if (hostView) {
    els.frame.hidden = true;
    els.empty.hidden = true;
  }
  renderHostPanel();
  renderProjectPanel();
  renderGitPanel();
  renderSessionsPanel();
  updateTabBadges();
  window.agenthubFilePane?.setPanel(activePanel);
}

function statusCard(title, meta, ...children) {
  return el(
    "div",
    { class: "status-card" },
    el("h3", {}, title, meta ? el("span", {}, meta) : null),
    ...children,
  );
}

function statusKv(entries) {
  const list = el("dl", { class: "status-kv" });
  for (const [key, value] of entries) {
    if (value == null || value === "") continue;
    list.append(el("dt", {}, key), el("dd", { title: String(value) }, String(value)));
  }
  return list;
}

function statusChips(items) {
  const wrap = el("div", { class: "status-chips" });
  for (const item of items) {
    const label = typeof item === "string" ? item : item.label;
    const tone = typeof item === "string" ? "" : item.tone;
    wrap.append(el("span", { class: `status-chip ${tone || ""}`.trim() }, label));
  }
  return wrap;
}

function statusList(items) {
  const list = el("div", { class: "status-list" });
  for (const item of items) {
    list.append(
      el(
        "div",
        { class: "status-list-row" },
        el("strong", {}, item.name),
        item.meta ? el("span", { title: String(item.meta) }, item.meta) : null,
      ),
    );
  }
  return list;
}

function mcpTone(status) {
  if (status === "connected") return "ok";
  if (status === "disabled") return "";
  return "err";
}

function hostTarget() {
  if (state.hostView) {
    const node = nodeByName(state.hostView);
    if (node) return { node, project: null };
  }
  return selectedProject();
}

async function loadHostStatus(force = false) {
  const picked = hostTarget();
  if (!picked) return;
  if (state.hostLoading) return;
  const requested = `${picked.node.name}:${picked.project?.path || ""}`;
  state.hostLoading = true;
  if (force) state.hostStatus = null;
  renderHostPanel();
  try {
    const params = new URLSearchParams({ node: picked.node.name });
    const data = await api(`/api/host-status?${params}`);
    const current = hostTarget();
    if (current && `${current.node.name}:${current.project?.path || ""}` === requested) {
      state.hostStatus = data;
      renderHostPanel();
    }
  } catch (error) {
    const current = hostTarget();
    if (current && `${current.node.name}:${current.project?.path || ""}` === requested) {
      state.hostStatus = { error: error.message || String(error) };
      renderHostPanel();
    }
  } finally {
    state.hostLoading = false;
  }
}

function openHost(node) {
  if (state.inboxOpen) closeInbox();
  state.hostView = node.name;
  state.hostStatus = null;
  state.activePanel = "host";
  storage.set("agenthub.activePanel", "host");
  els.frame.hidden = true;
  els.empty.hidden = true;
  updateCrumbs();
  updateActions();
  renderTree();
  renderPanels();
  loadHostStatus(true);
}

function renderHostPanel() {
  if (!els.hostBody) return;
  const status = state.hostStatus;
  if (!status) {
    els.hostSummary.textContent = "";
    els.hostBody.replaceChildren(
      el("div", { class: "panel-empty" }, state.hostLoading ? "loading host status…" : "No host status."),
    );
    return;
  }
  if (status.error) {
    els.hostSummary.textContent = "error";
    els.hostBody.replaceChildren(el("div", { class: "status-error" }, status.error));
    return;
  }

  const health = status.health || {};
  const config = status.config || {};
  const sessions = status.sessions || {};
  els.hostSummary.textContent = `${health.online ? "online" : "offline"}${health.version ? ` · v${health.version}` : ""}`;

  const cards = [];
  cards.push(
    statusCard(
      "Overview",
      null,
      statusKv([
        ["node", status.node],
        ["version", health.version || "—"],
        ["projects", status.projects],
        ["sessions", `${sessions.total || 0} (${sessions.busy || 0} busy)`],
        ["permissions", status.pending_permissions],
        ["questions", status.pending_questions],
      ]),
      health.error ? el("div", { class: "status-error" }, health.error) : null,
    ),
  );

  const modeChips = (config.modes || []).length
    ? statusChips(config.modes.map((mode) => ({ label: mode })))
    : null;
  cards.push(
    statusCard(
      "Config",
      null,
      statusKv([
        ["model", config.model || "—"],
        ["small model", config.small_model || "—"],
        ["user", config.username || "—"],
        ["theme", config.theme || "—"],
        ["share", config.share == null ? null : String(config.share)],
        ["autoupdate", config.autoupdate == null ? null : String(config.autoupdate)],
      ]),
      modeChips,
    ),
  );

  const providers = status.providers || {};
  cards.push(
    statusCard(
      "Providers",
      providers.total ? `${providers.connected?.length || 0}/${providers.total} connected` : null,
      (providers.connected || []).length
        ? statusChips(providers.connected.map((name) => ({ label: name, tone: "ok" })))
        : el("div", { class: "status-empty" }, "no connected providers"),
    ),
  );

  cards.push(
    statusCard(
      "MCP",
      `${(status.mcp || []).length} servers`,
      (status.mcp || []).length
        ? statusChips(
            status.mcp.map((server) => ({
              label: `${server.name} · ${server.status || "unknown"}`,
              tone: mcpTone(server.status),
            })),
          )
        : el("div", { class: "status-empty" }, "no MCP servers"),
    ),
  );

  cards.push(
    statusCard(
      "Skills",
      `${(status.skills || []).length}`,
      (status.skills || []).length
        ? statusList(
            status.skills.map((skill) => ({
              name: skill.name || "unnamed",
              meta: skill.description || skill.location || "",
            })),
          )
        : el("div", { class: "status-empty" }, "no skills"),
    ),
  );

  const primary = (status.agents || []).filter((agent) => agent.mode !== "subagent");
  const subagents = (status.agents || []).filter((agent) => agent.mode === "subagent");
  cards.push(
    statusCard(
      "Agents / work modes",
      `${primary.length} primary · ${subagents.length} subagent`,
      (status.agents || []).length
        ? statusList(
            status.agents.map((agent) => ({
              name: `${agent.name}${agent.mode ? ` (${agent.mode})` : ""}`,
              meta: agent.model || agent.description || "",
            })),
          )
        : el("div", { class: "status-empty" }, "no agents"),
    ),
  );

  cards.push(
    statusCard(
      "LSP / formatters",
      null,
      statusKv([
        ["LSP", (status.lsp || []).map((item) => item.name || item.id).join(", ") || "none"],
        ["formatters", (status.formatters || []).map((item) => item.name).join(", ") || "none"],
      ]),
    ),
  );

  const paths = status.paths || {};
  cards.push(
    statusCard(
      "Paths",
      null,
      statusKv([
        ["worktree", paths.worktree],
        ["directory", paths.directory],
        ["config", paths.config],
        ["state", paths.state],
        ["home", paths.home],
      ]),
    ),
  );

  const errors = Object.entries(status.errors || {});
  if (errors.length) {
    cards.push(
      statusCard(
        "Errors",
        `${errors.length}`,
        el(
          "div",
          { class: "status-list" },
          ...errors.map(([name, message]) =>
            el(
              "div",
              { class: "status-list-row" },
              el("strong", {}, name),
              el("span", { title: message }, message),
            ),
          ),
        ),
      ),
    );
  }

  els.hostBody.replaceChildren(...cards);
}

function renderProjectPanel() {
  if (!els.projectBody) return;
  const picked = selectedProject();
  if (!picked) {
    els.projectSummary.textContent = "";
    els.projectBody.replaceChildren(el("div", { class: "panel-empty" }, "No project selected."));
    return;
  }
  const status = state.projectStatus;
  if (!status) {
    els.projectSummary.textContent = "";
    els.projectBody.replaceChildren(el("div", { class: "panel-empty" }, "loading project status…"));
    return;
  }
  if (status.error) {
    els.projectSummary.textContent = "error";
    els.projectBody.replaceChildren(el("div", { class: "status-error" }, "project status unavailable"));
    return;
  }

  const changes = status.changes;
  els.projectSummary.textContent = `${status.total_sessions || 0} sessions · ${status.busy_sessions || 0} busy`;
  const cards = [];

  cards.push(
    statusCard(
      "Overview",
      null,
      statusKv([
        ["node", picked.node.name],
        ["project", picked.project.name],
        ["path", picked.project.path],
        ["sessions", status.total_sessions || 0],
        ["busy", status.busy_sessions || 0],
        ["permissions", status.pending_permissions || 0],
        ["questions", status.pending_questions || 0],
      ]),
    ),
  );

  cards.push(
    statusCard(
      "Working tree",
      changes ? `${changes.files} files · +${changes.additions} −${changes.deletions}` : "unavailable",
      changes
        ? changes.files
          ? statusList(
              (changes.items || []).map((item) => ({
                name: item.file || item.path || "file",
                meta: `+${item.additions || 0} −${item.deletions || 0}${item.status ? ` · ${item.status}` : ""}`,
              })),
            )
          : el("div", { class: "status-empty" }, "working tree clean")
        : el("div", { class: "status-empty" }, "git status unavailable"),
    ),
  );

  cards.push(
    statusCard(
      "Permissions",
      `${status.pending_permissions || 0}`,
      (status.permissions || []).length
        ? statusList(
            status.permissions.map((item) => ({
              name: item.permission || "permission",
              meta: [item.sessionID, ...(item.patterns || [])].filter(Boolean).join(" · "),
            })),
          )
        : el("div", { class: "status-empty" }, "no pending permissions"),
    ),
  );

  cards.push(
    statusCard(
      "Questions",
      `${status.pending_questions || 0}`,
      (status.questions || []).length
        ? statusList(
            status.questions.flatMap((item) =>
              (item.questions || []).map((question) => ({
                name: question.header || "question",
                meta: question.question || item.sessionID || "",
              })),
            ),
          )
        : el("div", { class: "status-empty" }, "no pending questions"),
    ),
  );

  const paths = status.paths || {};
  cards.push(
    statusCard(
      "Paths",
      null,
      statusKv([
        ["worktree", paths.worktree],
        ["directory", paths.directory],
        ["config", paths.config],
        ["state", paths.state],
      ]),
    ),
  );

  els.projectBody.replaceChildren(...cards);
}

function renderGitPanel() {
  if (!state.selected) return;
  const status = state.projectStatus;
  if (!status) {
    els.gitSummary.textContent = "";
    els.gitFiles.replaceChildren(el("div", { class: "panel-empty" }, "loading git status…"));
    return;
  }

  const changes = status.changes;
  if (!changes) {
    els.gitSummary.textContent = "";
    els.gitFiles.replaceChildren(el("div", { class: "panel-empty" }, "git status unavailable"));
    return;
  }

  els.gitSummary.replaceChildren(
    el("span", {}, el("strong", {}, changes.files), " files"),
    el("span", { class: "add" }, "+", changes.additions),
    el("span", { class: "del" }, "−", changes.deletions),
  );

  if (!changes.files) {
    els.gitFiles.replaceChildren(el("div", { class: "panel-empty" }, "Working tree clean"));
    return;
  }

  const labels = { added: "A", modified: "M", deleted: "D", untracked: "U", renamed: "R", copied: "C" };
  const fragment = document.createDocumentFragment();
  for (const item of changes.items || []) {
    const kind = String(item.status || "").toLowerCase();
    const numbers = [];
    if (item.additions) numbers.push(`+${item.additions}`);
    if (item.deletions) numbers.push(`−${item.deletions}`);
    fragment.append(
      el(
        "div",
        {
          class: "git-file",
          title: `${item.file}
click to copy path`,
          onclick: async () => {
            const ok = await copyText(item.file);
            toast(ok ? `Copied ${item.file}` : item.file);
          },
        },
        el("span", { class: `status-badge ${kind}` }, labels[kind] || "?"),
        el("span", { class: "file-path" }, item.file),
        el("span", { class: "file-numbers" }, numbers.join(" ")),
      ),
    );
  }
  els.gitFiles.replaceChildren(fragment);
}

function renderSessionsPanel() {
  if (!state.selected) return;
  const key = sessionKey(state.selected.node, state.selected.path);
  const entry = state.sessions.get(key);
  els.sessionsSummary.textContent = entry?.data?.length ? `${entry.data.length} sessions` : "";

  if (!entry || entry.loading) {
    els.sessionsList.replaceChildren(el("div", { class: "panel-empty" }, "loading sessions…"));
    return;
  }
  if (entry.error) {
    els.sessionsList.replaceChildren(el("div", { class: "panel-empty" }, entry.error));
    return;
  }
  if (!entry.data.length) {
    els.sessionsList.replaceChildren(el("div", { class: "panel-empty" }, "No sessions yet. Use New session to start."));
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const session of entry.data) {
    const node = nodeByName(state.selected.node);
    const project = projectByName(node, state.selected.path);
    const active = state.selected.sessionId === session.id;
    const model = [session.model?.providerID, session.model?.id].filter(Boolean).join("/");
    const root = (project?.path || "").replace(/\/+$/, "");
    const subPath =
      project?.kind === "other"
        ? session.directory
        : session.directory && root && session.directory !== project?.path
          ? session.directory.slice(root.length).replace(/^\/+/, "")
          : null;
    const sub = [session.agent, model, subPath, statusText(session.status)].filter(Boolean).join(" · ");
    const summary = session.summary || {};
    const cost = Number(session.cost || 0);
    const tokens = session.tokens || {};
    const tokenTotal = Number(tokens.input || 0) + Number(tokens.output || 0) + Number(tokens.reasoning || 0);
    const counts = [
      summary.additions ? `+${summary.additions}` : null,
      summary.deletions ? `−${summary.deletions}` : null,
    ]
      .filter(Boolean)
      .join(" ");

    fragment.append(
      el(
        "div",
        {
          class: `session-card${active ? " active" : ""}`,
          role: "button",
          tabindex: "0",
          title: session.title,
          onclick: () => node && project && openProject(node, project, session.id, { panel: "oc", title: session.title }),
          onkeydown: (event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              if (node && project) openProject(node, project, session.id, { panel: "oc", title: session.title });
            }
          },
        },
        el("span", { class: `dot ${isBusy(session.status) ? "busy" : ""}` }),
        el(
          "span",
          { class: "session-main" },
          el(
            "div",
            { class: "session-name" },
            session.title,
            activityInfo(session.activity, true) ? activityBadge(activityInfo(session.activity, true), true) : null,
          ),
          el("div", { class: "session-sub" }, [sub, counts].filter(Boolean).join("  ·  ") || "—"),
        ),
        el(
          "span",
          { class: "session-numbers" },
          el("div", { class: "cost" }, cost ? `$${cost.toFixed(3)}` : ""),
          el("div", {}, [tokenTotal ? `${Math.round(tokenTotal / 1000)}k tok` : null, relTime(session.updated)]
            .filter(Boolean)
            .join(" · ")),
        ),
      ),
    );
  }
  els.sessionsList.replaceChildren(fragment);
}

function renderProjectStatus() {
  const status = state.projectStatus;
  if (!state.selected || !status) {
    els.stageStatus.hidden = true;
    updateTabBadges();
    if (state.activePanel === "git") renderGitPanel();
    renderProjectPanel();
    return;
  }

  els.stageStatus.hidden = false;

  const changes = status.changes;
  if (changes) {
    const text = changes.files
      ? `git ${changes.files} files · +${changes.additions} −${changes.deletions}`
      : "git clean";
    els.statusGit.textContent = text;
    els.statusGit.className = `status-item ${changes.files ? "warn" : "good"}`;
    els.statusGit.title = changes.files ? "working tree has changes" : "working tree clean";
  } else {
    els.statusGit.textContent = "git n/a";
    els.statusGit.className = "status-item";
    els.statusGit.title = "vcs status unavailable";
  }

  const busy = Number(status.busy_sessions || 0);
  els.statusBusy.textContent = busy ? `${busy} busy` : "idle";
  els.statusBusy.className = `status-item ${busy ? "warn" : ""}`;
  els.statusBusy.title = `${busy} busy session(s)`;

  const permissions = Number(status.pending_permissions || 0);
  els.statusPermissions.textContent = permissions ? `${permissions} permission` : "";
  els.statusPermissions.className = `status-item ${permissions ? "warn" : ""}`;
  els.statusPermissions.hidden = !permissions;

  const questions = Number(status.pending_questions || 0);
  els.statusQuestions.textContent = questions ? `${questions} question` : "";
  els.statusQuestions.className = `status-item ${questions ? "warn" : ""}`;
  els.statusQuestions.hidden = !questions;

  els.statusUpdated.textContent = status.updated_at ? clockTime(status.updated_at) : "";
  updateTabBadges();
  renderGitPanel();
  renderProjectPanel();
}

async function loadProjectStatus() {
  if (!state.selected) {
    state.projectStatus = null;
    renderProjectStatus();
    return;
  }
  const node = nodeByName(state.selected.node);
  const project = projectByName(node, state.selected.path);
  if (!node || !project) {
    state.projectStatus = null;
    renderProjectStatus();
    return;
  }
  if (state.statusLoading) {
    state.statusPending = true;
    return;
  }

  const requested = `${node.name}:${project.path}`;
  state.statusLoading = true;
  try {
    const params = new URLSearchParams({ node: node.name, directory: project.path });
    const data = await api(`/api/project-status?${params}`);
    if (state.selected && `${state.selected.node}:${state.selected.path}` === requested) {
      state.projectStatus = data;
      renderProjectStatus();
    }
  } catch {
    if (state.selected && `${state.selected.node}:${state.selected.path}` === requested) {
      state.projectStatus = {
        changes: null,
        busy_sessions: 0,
        pending_permissions: 0,
        pending_questions: 0,
        updated_at: null,
        error: true,
      };
      renderProjectStatus();
    }
  } finally {
    state.statusLoading = false;
    if (state.statusPending) {
      state.statusPending = false;
      loadProjectStatus();
    }
  }
}

function applyTheme(theme) {
  const value = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = value;
  els.themeToggle.textContent = value === "dark" ? "☀" : "☾";
  els.themeToggle.title = value === "dark" ? "Switch to light theme" : "Switch to dark theme";
  els.settingsTheme.textContent = value === "dark" ? "Light" : "Dark";
}

function toggleTheme() {
  const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  applyTheme(next);
  localStorage.setItem("agenthub.theme", next);
}

function updateSettingsInfo() {
  if (state.tree) {
    const nodes = state.tree.nodes || [];
    const online = nodes.filter((node) => node.online).length;
    els.settingNodes.textContent = `${online}/${nodes.length} online`;
  }
  els.settingRefresh.textContent = `${state.config?.refresh_interval_s || 20}s`;
  const name = state.settings?.name || state.config?.name || "";
  if (name && document.activeElement !== els.settingName) els.settingName.value = name;
}

function renderHostsSettings() {
  const nodes = state.settings?.nodes || [];
  els.hostsList.replaceChildren();
  for (const node of nodes) {
    const row = el(
      "div",
      { class: "host-row" },
      el(
        "div",
        { class: "host-row-main" },
        el("div", { class: "host-row-name" }, node.name),
        el(
          "div",
          { class: "host-row-meta", title: `${node.api_url} → ${node.web_url}` },
          `${node.api_url} · proxy ${node.proxy_port}${node.secret_set ? "" : " · no secret"}`,
        ),
      ),
      el(
        "button",
        { class: "ghost-button small", onclick: () => showHostForm(node) },
        "Edit",
      ),
      el(
        "button",
        {
          class: "ghost-button small",
          onclick: () => removeHost(node.name),
        },
        "Remove",
      ),
    );
    els.hostsList.append(row);
  }
}

function showHostForm(node = null) {
  state.editingHost = node ? node.name : null;
  els.hostName.value = node?.name || "";
  els.hostApi.value = node?.api_url || "";
  els.hostWeb.value = node?.web_url || "";
  els.hostUsername.value = node?.username || "opencode";
  els.hostPasswordEnv.value = node?.password_env || "OPENCODE_SERVER_PASSWORD";
  els.hostPort.value = node ? String(node.proxy_port) : "";
  els.hostForm.hidden = false;
  els.hostName.focus();
}

function hideHostForm() {
  state.editingHost = null;
  els.hostForm.hidden = true;
}

async function loadSettings() {
  try {
    state.settings = await api("/api/settings");
  } catch (error) {
    toast(`Failed to load settings: ${error.message || error}`);
    return;
  }
  renderHostsSettings();
  updateSettingsInfo();
}

async function saveName() {
  const name = els.settingName.value.trim();
  if (!name) {
    toast("Name cannot be empty");
    return;
  }
  try {
    state.settings = await api("/api/settings", {
      method: "PATCH",
      body: JSON.stringify({ name }),
    });
    state.config = { ...(state.config || {}), name };
    if (els.hubName) els.hubName.textContent = name;
    document.title = `sah - ${name}`;
    updateSettingsInfo();
    toast("Name updated");
  } catch (error) {
    toast(`Failed to save name: ${error.message || error}`);
  }
}

async function saveHost(event) {
  event.preventDefault();
  const payload = {
    name: els.hostName.value.trim(),
    api_url: els.hostApi.value.trim(),
    web_url: els.hostWeb.value.trim(),
    username: els.hostUsername.value.trim() || "opencode",
    password_env: els.hostPasswordEnv.value.trim() || "OPENCODE_SERVER_PASSWORD",
  };
  const port = els.hostPort.value.trim();
  if (port) payload.proxy_port = port;
  if (!payload.name || !payload.api_url || !payload.web_url) {
    toast("name, api_url and web_url are required");
    return;
  }
  const editing = state.editingHost;
  try {
    state.settings = await api(
      editing ? `/api/hosts/${encodeURIComponent(editing)}` : "/api/hosts",
      { method: editing ? "PATCH" : "POST", body: JSON.stringify(payload) },
    );
    hideHostForm();
    renderHostsSettings();
    updateSettingsInfo();
    await loadTree(true);
    keepSelectionValid();
    toast(editing ? "Host updated" : "Host added");
  } catch (error) {
    toast(`Failed to save host: ${error.message || error}`);
  }
}

async function removeHost(name) {
  if (!window.confirm(`Remove host "${name}"?`)) return;
  try {
    state.settings = await api(`/api/hosts/${encodeURIComponent(name)}`, { method: "DELETE" });
    renderHostsSettings();
    updateSettingsInfo();
    await loadTree(true);
    keepSelectionValid();
    toast("Host removed");
  } catch (error) {
    toast(`Failed to remove host: ${error.message || error}`);
  }
}

function keepSelectionValid() {
  if (!state.selected) return;
  if (nodeByName(state.selected.node)) return;
  state.selected = null;
  state.projectStatus = null;
  state.hostStatus = null;
  storage.set("agenthub.selected", null);
  els.frame.removeAttribute("src");
  updateCrumbs();
  updateActions();
  renderPanels();
}

function openSettings() {
  updateSettingsInfo();
  loadSettings();
  els.settingsDialog.hidden = false;
}

function closeSettings() {
  els.settingsDialog.hidden = true;
  hideHostForm();
}

function updateSummary() {
  if (!state.tree) return;
  const nodes = state.tree.nodes || [];
  const online = nodes.filter((node) => node.online).length;
  const projects = nodes.reduce(
    (count, node) => count + (node.projects || []).filter((project) => project.kind !== "other").length,
    0,
  );
  const sessions = nodes.reduce(
    (count, node) =>
      count + (node.projects || []).reduce((sum, project) => sum + Number(project.session_count || 0), 0),
    0,
  );
  const waiting = waitingCount();
  els.nodeSummary.textContent = `${online}/${nodes.length} online · ${projects} projects · ${sessions} sessions`;
  els.updatedAt.textContent = `${waiting ? `${waiting} waiting · ` : ""}${clockTime(state.tree.generated_at)}`;
  els.updatedAt.classList.toggle("attention", waiting > 0);
  els.updatedAt.title = waiting ? `${waiting} item(s) need your attention` : "Last refresh";
  updateInboxBadge();
  if (!els.settingsDialog.hidden) updateSettingsInfo();
}

function clampSidebarRatio(value) {
  return Math.max(0.14, Math.min(0.45, Number(value) || 0.2));
}

function loadSidebarRatio() {
  const stored = Number(localStorage.getItem("agenthub.sidebarRatio"));
  if (Number.isFinite(stored) && stored > 0) return clampSidebarRatio(stored);
  const legacy = Number(localStorage.getItem("agenthub.sidebarWidth"));
  if (Number.isFinite(legacy) && legacy > 0) {
    return clampSidebarRatio(legacy / Math.max(1, window.innerWidth));
  }
  return 0.2;
}

function applySidebarWidth() {
  const width = Math.round(window.innerWidth * state.sidebarRatio);
  document.documentElement.style.setProperty("--sidebar-width", `${width}px`);
}

function setSidebarWidth(width) {
  state.sidebarRatio = clampSidebarRatio(Number(width) / Math.max(1, window.innerWidth));
  applySidebarWidth();
  window.agenthubFilePane?.layoutChanged?.();
}

function persistSidebarWidth() {
  localStorage.setItem("agenthub.sidebarRatio", String(state.sidebarRatio));
}

function applySidebarState() {
  applySidebarWidth();
  document.body.classList.toggle("sidebar-collapsed", state.sidebarCollapsed);
  if (els.sidebarToggle) {
    els.sidebarToggle.classList.toggle("active", !state.sidebarCollapsed);
    els.sidebarToggle.title = state.sidebarCollapsed ? "Show sidebar" : "Hide sidebar";
  }
  window.agenthubFilePane?.layoutChanged?.();
}

function toggleSidebar() {
  state.sidebarCollapsed = !state.sidebarCollapsed;
  state.sidebarAuto = false;
  applySidebarState();
}

function handleSidebarAuto() {
  const narrow = window.innerWidth < 820;
  if (narrow === state.lastSidebarNarrow) return;
  state.lastSidebarNarrow = narrow;
  if (narrow && !state.sidebarCollapsed) {
    state.sidebarCollapsed = true;
    state.sidebarAuto = true;
    applySidebarState();
  } else if (!narrow && state.sidebarAuto) {
    state.sidebarCollapsed = false;
    state.sidebarAuto = false;
    applySidebarState();
  }
}

function initResizer() {
  els.resizer.addEventListener("pointerdown", (event) => {
    if (state.sidebarCollapsed) return;
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = els.sidebar.getBoundingClientRect().width;

    document.body.classList.add("resizing");
    els.overlay.hidden = false;

    const move = (moveEvent) => {
      setSidebarWidth(startWidth + moveEvent.clientX - startX);
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
      document.body.classList.remove("resizing");
      els.overlay.hidden = true;
      persistSidebarWidth();
    };

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
    window.addEventListener("pointercancel", stop);
  });

  els.resizer.addEventListener("dblclick", () => {
    state.sidebarRatio = 0.2;
    applySidebarWidth();
    persistSidebarWidth();
  });

  window.addEventListener("resize", () => {
    handleSidebarAuto();
    applySidebarState();
  });
}

async function loadTree(force = false) {
  if (state.treeLoading) {
    state.treePending = state.treePending || force;
    return;
  }
  state.treeLoading = true;
  try {
    const suffix = force ? "?refresh=1" : "";
    state.tree = await api(`/api/tree${suffix}`);
    renderTree();
    updateSummary();
    updateCrumbs();
    hydrateExpanded();
    restoreSelection();
  } catch (error) {
    toast(`Failed to load nodes: ${error.message || error}`);
  } finally {
    state.treeLoading = false;
    if (state.treePending) {
      const pendingForce = state.treePending;
      state.treePending = false;
      setTimeout(() => loadTree(pendingForce), 150);
    }
  }
}

function refreshSelected() {
  if (!state.selected) return;
  const selected = selectedProject();
  if (!selected) return;
  loadProjectStatus();
  const key = sessionKey(selected.node.name, selected.project.path);
  if (state.expanded.has(key) || state.activePanel === "sessions") {
    loadSessions(selected.node, selected.project, true, true);
  }
}

let activityRefreshTimer = null;

function scheduleActivityRefresh(delay = 900) {
  if (state.switching) {
    state.pendingActivityRefresh = true;
    return;
  }
  const elapsed = Date.now() - state.lastActivityRefresh;
  const wait = Math.max(delay, 1500 - elapsed);
  clearTimeout(activityRefreshTimer);
  activityRefreshTimer = setTimeout(() => {
    if (document.visibilityState === "visible") {
      state.lastActivityRefresh = Date.now();
      loadTree(true);
    }
  }, wait);
}

async function markActivityRead(node, project, sessionId = null) {
  if (!sessionId) return;
  const key = sessionKey(node.name, project.path);
  const entry = state.sessions.get(key);
  const session = entry?.data?.find((item) => item.id === sessionId);
  const needsRead =
    Number(project.done_sessions || 0) > 0 ||
    Number(project.error_sessions || 0) > 0 ||
    Boolean(session?.activity?.done) ||
    Boolean(session?.activity?.error);
  if (!needsRead) return;

  try {
    await api("/api/activity/read", {
      method: "POST",
      body: JSON.stringify({ node: node.name, sessionID: sessionId }),
    });
  } catch {}
}

function connectActivityEvents() {
  if (!("EventSource" in window)) return;
  const source = new EventSource("/api/events");
  source.onopen = () => scheduleActivityRefresh(1500);
  source.onmessage = () => {
    scheduleActivityRefresh(900);
    scheduleInboxRefresh();
  };
}

function scheduleTreeRefresh() {
  const seconds = Math.max(10, Number(state.config?.refresh_interval_s || 20));
  let timer = null;
  const tick = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      if (document.visibilityState === "visible") await loadTree(true);
      tick();
    }, seconds * 1000);
  };
  tick();
}

function bindEvents() {
  els.search.addEventListener("input", () => {
    state.query = els.search.value;
    renderTree();
  });

  els.frame.addEventListener("load", () => {
    if (els.frame.src === "about:blank") return;
    state.frameBooted = true;
    if (!els.frame.hidden) hideFrameLoading();
    try {
      els.frame.contentWindow?.postMessage({ source: "agenthub", type: "ping" }, "*");
    } catch {}
  });

  els.reload.addEventListener("click", () => {
    if (!state.selected) return;
    const node = nodeByName(state.selected.node);
    const project = projectByName(node, state.selected.path);
    if (node && project) loadSessions(node, project, true);
    loadProjectStatus();
    loadTree(true);
    reloadFrame();
  });

  els.newSession.addEventListener("click", () => {
    if (!state.selected) return;
    const node = nodeByName(state.selected.node);
    const project = projectByName(node, state.selected.path);
    if (node && project) openProject(node, project, null, { panel: "oc", forceNew: true });
  });

  els.openWindow.addEventListener("click", () => {
    if (!state.selected) return;
    const node = nodeByName(state.selected.node);
    const project = projectByName(node, state.selected.path);
    if (node && project) window.open(frameUrl(node, project, state.selected.sessionId), "_blank");
  });

  els.themeToggle.addEventListener("click", toggleTheme);
  els.sidebarToggle?.addEventListener("click", toggleSidebar);
  els.settingsButton.addEventListener("click", openSettings);
  els.settingsClose.addEventListener("click", closeSettings);
  els.settingsTheme.addEventListener("click", toggleTheme);
  els.settingsRefresh.addEventListener("click", async () => {
    await loadTree(true);
    updateSettingsInfo();
  });
  els.settingNameSave.addEventListener("click", saveName);
  els.settingName.addEventListener("keydown", (event) => {
    if (event.key === "Enter") saveName();
  });
  els.hostAdd.addEventListener("click", () => showHostForm());
  els.hostCancel.addEventListener("click", hideHostForm);
  els.hostForm.addEventListener("submit", saveHost);
  els.settingsDialog.addEventListener("click", (event) => {
    if (event.target === els.settingsDialog) closeSettings();
  });

  els.inboxButton.addEventListener("click", () => {
    if (state.inboxOpen) closeInbox();
    else openInbox();
  });
  els.inboxBack.addEventListener("click", closeInbox);
  els.inboxRefresh.addEventListener("click", fetchInbox);

  window.addEventListener("hashchange", () => {
    const hit = readHash();
    if (hit) openProject(hit.node, hit.project, hit.sessionId);
  });

  window.addEventListener("message", handleFrameRoute);

  for (const tab of els.panelTabs.querySelectorAll(".panel-tab")) {
    tab.addEventListener("click", () => setActivePanel(tab.dataset.panel));
  }

  els.gitRefresh.addEventListener("click", () => loadProjectStatus());
  els.hostRefresh.addEventListener("click", () => loadHostStatus(true));
  els.projectRefresh.addEventListener("click", () => loadProjectStatus());

  els.sessionsRefresh.addEventListener("click", () => {
    if (!state.selected) return;
    const node = nodeByName(state.selected.node);
    const project = projectByName(node, state.selected.path);
    if (node && project) loadSessions(node, project, true);
  });

  els.sessionsNew.addEventListener("click", () => {
    if (!state.selected) return;
    const node = nodeByName(state.selected.node);
    const project = projectByName(node, state.selected.path);
    if (node && project) openProject(node, project, null, { panel: "oc", forceNew: true });
  });

  els.copyLink.addEventListener("click", async () => {
    const ok = await copyText(location.href);
    toast(ok ? "Link copied" : location.href);
  });

  document.addEventListener("keydown", (event) => {
    const target = event.target;
    const typing =
      target instanceof HTMLElement &&
      (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);

    if (event.key === "Escape") {
      if (state.inboxOpen) {
        closeInbox();
        return;
      }
      if (!els.settingsDialog.hidden) {
        closeSettings();
        return;
      }
      if (typing) {
        els.search.value = "";
        state.query = "";
        renderTree();
        els.search.blur();
      }
      return;
    }
    if (typing || event.metaKey || event.ctrlKey || event.altKey) return;

    if (event.key === "/") {
      event.preventDefault();
      els.search.focus();
      els.search.select();
    } else if (event.key.toLowerCase() === "r" && state.selected) {
      event.preventDefault();
      els.reload.click();
    } else if (event.key.toLowerCase() === "o" && state.selected) {
      setActivePanel("oc");
    } else if (event.key.toLowerCase() === "h" && state.selected) {
      setActivePanel("host");
    } else if (event.key.toLowerCase() === "p" && state.selected) {
      setActivePanel("project");
    } else if (event.key.toLowerCase() === "g" && state.selected) {
      setActivePanel("git");
    } else if (event.key.toLowerCase() === "s" && state.selected) {
      setActivePanel("sessions");
    }
  });
}

async function init() {
  applyTheme(localStorage.getItem("agenthub.theme") || "dark");
  if (!["oc", "host", "project", "git", "sessions"].includes(state.activePanel)) state.activePanel = "oc";
  if (state.lastSidebarNarrow) {
    state.sidebarCollapsed = true;
    state.sidebarAuto = true;
  }
  applySidebarState();
  initResizer();
  bindEvents();
  updateActions();
  try {
    state.config = await api("/api/config");
    if (state.config?.name) document.title = `sah - ${state.config.name}`;
  } catch (error) {
    toast(`Failed to load config: ${error.message || error}`);
  }
  await loadTree();
  await loadFavorites();
  renderPanels();
  connectActivityEvents();
  scheduleTreeRefresh();
  setInterval(() => {
    if (document.visibilityState === "visible") refreshSelected();
  }, 10000);
}

init();
