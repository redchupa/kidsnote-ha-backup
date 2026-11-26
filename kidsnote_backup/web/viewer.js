"use strict";

const main = document.querySelector("main");
const tabs = [...document.querySelectorAll(".tabs a")];
const labels = {
  reports: "알림장", albums: "앨범", notices: "공지", menus: "식단",
  schedules: "일정표", medications: "투약의뢰서", return_homes: "귀가동의서", temperatures: "체온기록",
  supplies: "준비물", calendar: "달력", stats: "통계분석",
};
const catalogKinds = new Set([
  "reports", "albums", "notices", "menus", "schedules", "medications", "return_homes", "temperatures",
]);
const childKinds = new Set(["reports", "albums", "medications", "return_homes", "temperatures"]);
const videoFilterKinds = new Set(["reports", "albums", "notices"]);
const pageSize = 12;
const DEFAULT_TITLE = "키즈노트 보관함";
const STORAGE_KEY = "kidsnote_ui_settings";

const THEME_LIST = [
  { id: "auto", name: "시스템 자동", desc: "기기 라이트/다크 자동" },
  { id: "white", name: "화이트", desc: "깔끔한 순백 미니멀" },
  { id: "black", name: "블랙", desc: "눈이 편한 딥 다크" },
  { id: "blue", name: "블루", desc: "시원한 스카이 오션" },
  { id: "ivory", name: "아이보리", desc: "따뜻한 종이 다이어리" },
  { id: "cute", name: "귀여운 컨셉", desc: "파스텔 둥근 다이어리" },
  { id: "futuristic", name: "미래지향적", desc: "네온 사이버 하이테크" },
];

let routeController;
let lastList = "reports";
let currentSettings = { title: DEFAULT_TITLE, theme: "auto", default_tab: "reports" };
let backupPollTimer = null;
let backupWasRunning = false;
let lastBackupInfo = null;
let toastTimer = null;
let hideCompletedSupplies = false;
let showReviewedFlags = false;

// Lightbox state
let lightboxItems = [];
let lightboxIndex = 0;
let touchStartX = 0;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== "") node.textContent = text;
  return node;
}

function link(text, href, className) {
  const node = element("a", className, text);
  node.href = href;
  return node;
}

function button(text, action, className) {
  const node = element("button", className, text);
  node.type = "button";
  node.addEventListener("click", action);
  return node;
}

function mediaUrl(key, file, download = false) {
  return "./media/" + key.split("/").map(encodeURIComponent).join("/") + "/" +
    encodeURIComponent(file) + (download ? "?download=1" : "");
}

function zipUrl(key) {
  return "./media-zip/" + key.split("/").map(encodeURIComponent).join("/");
}

function time(value) {
  const node = element("time", "date", value);
  if (value) node.dateTime = value;
  return node;
}

function message(text) {
  const node = element("p", "empty", text);
  node.setAttribute("role", "status");
  return node;
}

function showToast(text) {
  const node = document.querySelector("#toast");
  node.textContent = text;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, 3200);
}

async function get(endpoint, query, signal) {
  const qs = query && Object.keys(query).length ? "?" + new URLSearchParams(query) : "";
  const response = await fetch("./api/" + endpoint + qs, { signal });
  if (!response.ok) throw new Error("Could not load " + endpoint);
  return response.json();
}

async function post(endpoint, payload) {
  const response = await fetch("./api/" + endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  return { ok: response.ok, status: response.status, data };
}

const VALID_THEMES = new Set(THEME_LIST.map(t => t.id));

function applySettings(settings) {
  const safeTitle = typeof settings?.title === "string" && settings.title.trim()
    ? settings.title.trim().slice(0, 40) : DEFAULT_TITLE;
  const safeTheme = VALID_THEMES.has(settings?.theme) ? settings.theme : "auto";
  const safeTab = Object.hasOwn(labels, settings?.default_tab) ? settings.default_tab : "reports";
  currentSettings = { title: safeTitle, theme: safeTheme, default_tab: safeTab };
  document.documentElement.setAttribute("data-theme", currentSettings.theme);
  document.title = currentSettings.title;
  const heading = document.querySelector("#archive-title");
  if (heading) heading.textContent = currentSettings.title;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(currentSettings));
  } catch { /* ignore storage restrictions */ }
}

function loadCachedSettings() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) applySettings(JSON.parse(raw));
  } catch { /* ignore */ }
}

function formatEta(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds)) return "";
  const mins = Math.round(Math.max(0, seconds) / 60);
  if (mins < 1) return "1분 미만 남음";
  if (mins < 60) return "약 " + mins + "분 남음";
  const hours = Math.floor(mins / 60);
  const rem = mins % 60;
  return rem ? "약 " + hours + "시간 " + rem + "분 남음" : "약 " + hours + "시간 남음";
}

function formatKstTime(epochSeconds) {
  if (typeof epochSeconds !== "number" || !Number.isFinite(epochSeconds) || epochSeconds <= 0) return "";
  return new Intl.DateTimeFormat("ko", {
    hour: "2-digit", minute: "2-digit", timeZone: "Asia/Seoul",
  }).format(new Date(epochSeconds * 1000));
}

function setStatus(status, running = false, progress = null, nextRun = 0) {
  const stamp = status?.last_success_at;
  let state;
  if (running) {
    const phaseLabel = labels[progress?.phase] || "";
    const countPart = progress?.phase_total
      ? " (" + progress.phase_seen + "/" + progress.phase_total + ")"
      : "";
    const parts = [(phaseLabel ? phaseLabel + " 백업 중" : "백업 진행 중") + countPart];
    if (progress) {
      const eta = formatEta(progress.eta_seconds);
      if (eta) parts.push(eta);
      if (progress.records) parts.push("저장 " + progress.records + "건");
      if (progress.downloaded) parts.push("첨부 " + progress.downloaded + "개");
    }
    state = parts.join(" · ");
  } else if (!stamp && nextRun && nextRun * 1000 > Date.now()) {
    state = "첫 백업 예정 " + formatKstTime(nextRun) + "경";
  } else {
    state = {
      failed: "최근 백업 실패", partial: "일부 백업 실패",
      stopped: "백업 중지됨", deferred: "키즈노트 응답으로 백업 일시 중지",
    }[status?.state];
  }
  const formatted = stamp ? new Intl.DateTimeFormat("ko", {
    dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Seoul",
  }).format(new Date(stamp)) : "";
  document.querySelector("#backup-status").textContent =
    [formatted ? "마지막 완료 " + formatted : "", state].filter(Boolean).join(" · ");
}

function updateBackupButton(info) {
  lastBackupInfo = info;
  const btn = document.querySelector("#backup-now");
  if (!btn) return;
  if (!info.can_trigger) {
    btn.hidden = true;
    return;
  }
  btn.hidden = false;
  btn.disabled = false;
  const inCooldown = info.cooldown_until && info.cooldown_until * 1000 > Date.now();
  const initialPending = !info.status?.last_success_at;
  if (info.running) {
    btn.classList.add("is-busy");
    btn.setAttribute("aria-disabled", "true");
    btn.textContent = "백업 중...";
  } else if (inCooldown) {
    btn.classList.add("is-busy");
    btn.setAttribute("aria-disabled", "true");
    btn.textContent = "대기 중";
  } else if (initialPending) {
    btn.classList.add("is-busy");
    btn.setAttribute("aria-disabled", "true");
    btn.textContent = "초기 백업 대기 중";
  } else {
    btn.classList.remove("is-busy");
    btn.removeAttribute("aria-disabled");
    btn.textContent = "지금 백업";
  }
  setStatus(info.status, info.running, info.progress, info.next_run);
}

async function pollBackupStatus() {
  try {
    const info = await get("backup/status", {});
    updateBackupButton(info);
    clearTimeout(backupPollTimer);
    backupPollTimer = null;
    if (info.running) {
      backupWasRunning = true;
      backupPollTimer = setTimeout(pollBackupStatus, 3000);
    } else if (backupWasRunning) {
      backupWasRunning = false;
      showToast("백업이 완료되었습니다.");
      render(false);
    } else if (info.can_trigger && !info.status?.last_success_at) {
      backupPollTimer = setTimeout(pollBackupStatus, 10000);
    }
  } catch { /* ignore transient status errors */ }
}

async function triggerManualBackup() {
  const info = lastBackupInfo;
  if (info?.running) {
    const phaseLabel = labels[info.progress?.phase] || "";
    showToast(phaseLabel
      ? "현재 " + phaseLabel + " 백업이 진행 중입니다. 완료될 때까지 기다려 주세요."
      : "현재 백업이 진행 중입니다. 완료될 때까지 기다려 주세요.");
    return;
  }
  if (info?.cooldown_until && info.cooldown_until * 1000 > Date.now()) {
    const mins = Math.max(1, Math.ceil((info.cooldown_until * 1000 - Date.now()) / 60000));
    showToast("키즈노트 서버 보호 대기 중입니다. 약 " + mins + "분 후에 다시 시도할 수 있습니다.");
    return;
  }
  if (info && !info.status?.last_success_at) {
    const when = info.next_run && info.next_run * 1000 > Date.now() ? formatKstTime(info.next_run) : "";
    showToast(when
      ? "첫 백업이 " + when + "경 자동으로 시작됩니다. 완료된 후 수동 백업을 사용할 수 있습니다."
      : "첫 백업이 완료된 후 수동 백업을 사용할 수 있습니다.");
    return;
  }
  const btn = document.querySelector("#backup-now");
  btn.classList.add("is-busy");
  btn.setAttribute("aria-disabled", "true");
  btn.textContent = "요청 중...";
  try {
    const result = await post("backup/trigger", {});
    showToast(result.data.message || (result.ok ? "백업을 시작합니다." : "백업을 시작할 수 없습니다."));
    if (result.ok) {
      backupWasRunning = true;
      clearTimeout(backupPollTimer);
      backupPollTimer = setTimeout(pollBackupStatus, 1500);
      btn.textContent = "백업 중...";
    } else {
      pollBackupStatus();
    }
  } catch {
    showToast("백업 요청에 실패했습니다.");
    pollBackupStatus();
  }
}

// ------------------------------------------------------------------ Settings modal

function initSettingsModal() {
  const dialog = document.querySelector("#settings-dialog");
  const openBtn = document.querySelector("#open-settings");
  const closeBtn = document.querySelector("#close-settings");
  const cancelBtn = document.querySelector("#cancel-settings");
  const saveBtn = document.querySelector("#save-settings");
  const resetTitleBtn = document.querySelector("#reset-title");
  const titleInput = document.querySelector("#setting-title");
  const tabSelect = document.querySelector("#setting-tab");
  const themeGrid = document.querySelector("#theme-grid");

  let draftTheme = currentSettings.theme;

  function renderThemeChoices() {
    themeGrid.replaceChildren(...THEME_LIST.map(t => {
      const btn = element("button", "theme-option");
      btn.type = "button";
      btn.setAttribute("role", "radio");
      btn.setAttribute("aria-checked", String(draftTheme === t.id));
      const swatch = element("span", "theme-swatch swatch-" + t.id);
      swatch.setAttribute("aria-hidden", "true");
      const text = element("span", "theme-text");
      text.append(element("span", "theme-name", t.name), element("span", "theme-desc", t.desc));
      btn.append(swatch, text);
      btn.addEventListener("click", () => {
        draftTheme = t.id;
        document.documentElement.setAttribute("data-theme", draftTheme);
        renderThemeChoices();
      });
      return btn;
    }));
  }

  function closeAndRevert() {
    document.documentElement.setAttribute("data-theme", currentSettings.theme);
    dialog.close();
  }

  openBtn.addEventListener("click", () => {
    titleInput.value = currentSettings.title;
    tabSelect.value = currentSettings.default_tab;
    draftTheme = currentSettings.theme;
    renderThemeChoices();
    dialog.showModal();
  });

  resetTitleBtn.addEventListener("click", () => {
    titleInput.value = DEFAULT_TITLE;
    titleInput.focus();
  });

  closeBtn.addEventListener("click", closeAndRevert);
  cancelBtn.addEventListener("click", closeAndRevert);
  dialog.addEventListener("cancel", event => {
    event.preventDefault();
    closeAndRevert();
  });
  dialog.addEventListener("click", event => {
    if (event.target === dialog) closeAndRevert();
  });

  saveBtn.addEventListener("click", async () => {
    const payload = {
      title: titleInput.value.trim() || DEFAULT_TITLE,
      theme: draftTheme,
      default_tab: tabSelect.value,
    };
    saveBtn.disabled = true;
    try {
      const result = await post("settings", payload);
      if (result.ok) {
        applySettings(result.data);
        dialog.close();
        showToast("설정이 저장되었습니다.");
      } else {
        showToast("설정을 저장하지 못했습니다.");
      }
    } catch {
      showToast("설정을 저장하지 못했습니다.");
    } finally {
      saveBtn.disabled = false;
    }
  });
}

// ------------------------------------------------------------------ Lightbox modal

function initLightbox() {
  const dialog = document.querySelector("#lightbox-dialog");
  const prevBtn = document.querySelector("#lightbox-prev");
  const nextBtn = document.querySelector("#lightbox-next");
  const closeBtn = document.querySelector("#lightbox-close");
  const stage = document.querySelector("#lightbox-stage");

  function showSlide(index) {
    if (!lightboxItems.length) return;
    lightboxIndex = (index + lightboxItems.length) % lightboxItems.length;
    const current = lightboxItems[lightboxIndex];
    const img = document.querySelector("#lightbox-img");
    img.src = current.src;
    img.alt = current.alt;
    document.querySelector("#lightbox-counter").textContent = (lightboxIndex + 1) + " / " + lightboxItems.length;
    document.querySelector("#lightbox-name").textContent = current.name;
    document.querySelector("#lightbox-download").href = current.download;
    document.querySelector("#lightbox-open").href = current.src;
    const single = lightboxItems.length <= 1;
    prevBtn.hidden = single;
    nextBtn.hidden = single;
  }

  prevBtn.addEventListener("click", () => showSlide(lightboxIndex - 1));
  nextBtn.addEventListener("click", () => showSlide(lightboxIndex + 1));
  closeBtn.addEventListener("click", () => dialog.close());

  dialog.addEventListener("keydown", event => {
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      showSlide(lightboxIndex - 1);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      showSlide(lightboxIndex + 1);
    }
  });

  stage.addEventListener("click", event => {
    if (event.target === stage) dialog.close();
  });

  stage.addEventListener("touchstart", event => {
    if (event.touches.length === 1) touchStartX = event.touches[0].clientX;
  }, { passive: true });

  stage.addEventListener("touchend", event => {
    if (!touchStartX || event.changedTouches.length !== 1) return;
    const diff = event.changedTouches[0].clientX - touchStartX;
    touchStartX = 0;
    if (Math.abs(diff) > 45) showSlide(lightboxIndex + (diff < 0 ? 1 : -1));
  }, { passive: true });

  window.openLightbox = (items, startIndex) => {
    lightboxItems = items;
    showSlide(startIndex);
    dialog.showModal();
  };
}

// ------------------------------------------------------------------ Helpers

function field(text, control) {
  const label = element("label", "field");
  label.append(element("span", "", text), control);
  return label;
}

function fillSelect(select, items, value) {
  for (const opt of items) {
    const option = element("option", "", opt.name);
    option.value = opt.id;
    select.append(option);
  }
  select.value = value || "";
}

function goList(kind, params) {
  const query = new URLSearchParams();
  for (const [name, value] of Object.entries(params)) {
    if (value) query.set(name, value);
  }
  location.hash = kind + (query.size ? "?" + query : "");
}

function card(item, kind) {
  const anchor = link("", "#item/" + item.key, "card");
  if (item.cover) {
    const img = element("img");
    img.alt = "";
    img.loading = "lazy";
    img.src = mediaUrl(item.key, item.cover);
    anchor.append(img);
  }
  const text = element("div", "card-content");
  if (item.date || item.has_video) {
    const meta = element("div", "card-meta");
    if (item.date) meta.append(time(item.date));
    if (item.has_video) meta.append(element("span", "video-badge", "🎬 동영상"));
    text.append(meta);
  }
  text.append(element("h3", "", item.title || labels[kind]));
  if (item.excerpt && item.excerpt !== item.title) text.append(element("p", "", item.excerpt));
  anchor.append(text);
  return anchor;
}

// ------------------------------------------------------------------ Catalog list

async function renderList(kind, params, signal) {
  const filters = {
    q: params.get("q") || "",
    owner: params.get("owner") || "",
    month: params.get("month") || "",
    video: params.get("video") === "1" ? "1" : "",
  };
  const page = Math.max(Number(params.get("page")) || 1, 1);
  const data = await get("catalog", { kind, ...filters, page }, signal);
  if (signal.aborted) return;
  setStatus(data.status);
  lastList = location.hash.slice(1) || kind;

  const search = element("input");
  search.type = "search";
  search.value = filters.q;
  search.maxLength = 200;
  search.placeholder = "제목, 본문, 댓글, 첨부문서";
  const owner = element("select");
  const ownerLabel = childKinds.has(kind) ? "자녀" : "기관";
  fillSelect(owner, [
    { id: "", name: "모든 " + ownerLabel },
    ...data.owners.map(group => ({
      id: group.id,
      name: group.name ? group.name + " (ID " + group.id + ")" : ownerLabel + " ID " + group.id,
    })),
  ], filters.owner);
  const month = element("select");
  fillSelect(month, [{ id: "", name: "모든 달" }, ...data.months.map(day => ({ id: day, name: day }))], filters.month);
  let videoOnly = null;
  if (videoFilterKinds.has(kind)) {
    videoOnly = element("input");
    videoOnly.type = "checkbox";
    videoOnly.checked = filters.video === "1";
  }
  const refine = () => goList(kind, {
    q: search.value.trim(),
    owner: owner.value,
    month: month.value,
    video: videoOnly && videoOnly.checked ? "1" : "",
  });
  const row = element("div", "filters");
  search.addEventListener("keydown", event => {
    if (event.key === "Enter") {
      event.preventDefault();
      refine();
    }
  });
  const apply = button("검색", refine, "search-button");
  owner.addEventListener("change", refine);
  month.addEventListener("change", refine);
  row.append(field("검색", search), apply, field(ownerLabel, owner), field("날짜", month));
  if (videoOnly) {
    videoOnly.addEventListener("change", refine);
    const videoToggle = element("label", "video-filter");
    videoToggle.append(videoOnly, element("span", "", "🎬 동영상만 보기"));
    row.append(videoToggle);
  }

  const fragment = document.createDocumentFragment();
  fragment.append(element("h2", "", labels[kind]), row, element("p", "result-count", data.total + "건"));
  if (data.items.length) {
    const grid = element("div", "grid");
    grid.append(...data.items.map(item => card(item, kind)));
    fragment.append(grid);
  } else {
    const optionalMenus = new Set(["schedules", "medications", "return_homes", "temperatures"]);
    const hasFilter = Object.values(filters).some(Boolean);
    let emptyText;
    if (hasFilter) {
      emptyText = "조건에 맞는 기록이 없습니다.";
    } else if (data.status.last_success_at || data.total) {
      emptyText = optionalMenus.has(kind)
        ? "저장된 " + labels[kind] + " 기록이 없습니다. 이용 중인 원에서 해당 메뉴를 사용하지 않거나 등록된 기록이 없을 수 있습니다."
        : "저장된 기록이 없습니다.";
    } else {
      emptyText = "저장된 기록이 없습니다. 첫 백업이 끝나면 여기에 표시됩니다.";
    }
    fragment.append(message(emptyText));
  }
  const pages = Math.ceil(data.total / pageSize);
  if (pages > 1) {
    const bar = element("nav", "pager");
    bar.setAttribute("aria-label", "결과 페이지");
    const prev = button("이전", () => goList(kind, { ...filters, page: page - 1 }));
    prev.disabled = page <= 1;
    const next = button("다음", () => goList(kind, { ...filters, page: page + 1 }));
    next.disabled = page >= pages;
    bar.append(prev, element("span", "", page + " / " + pages), next);
    fragment.append(bar);
  }
  main.replaceChildren(fragment);
}

// ------------------------------------------------------------------ Item detail

function mediaBlock(assets, key, allImages) {
  const gallery = element("div", "gallery");
  for (const asset of assets) {
    const source = mediaUrl(key, asset.file);
    if (asset.type === "image") {
      const idx = allImages.length;
      const alt = asset.name === asset.file ? "첨부 사진" : asset.name;
      allImages.push({ src: source, download: mediaUrl(key, asset.file, true), name: asset.name, alt });
      const img = element("img");
      img.loading = "lazy";
      img.alt = alt;
      img.src = source;
      const btn = button("", () => window.openLightbox(allImages, idx), "gallery-photo-btn");
      btn.setAttribute("aria-label", alt + " 크게 보기");
      btn.append(img);
      gallery.append(btn);
    } else if (asset.type === "video") {
      const video = element("video");
      video.src = source;
      video.preload = "metadata";
      video.controls = true;
      video.playsInline = true;
      video.setAttribute("aria-label", "첨부 동영상");
      gallery.append(video);
    } else {
      const file = element("div", "file");
      file.append(link(asset.name + " 다운로드", mediaUrl(key, asset.file, true)));
      if (asset.text) {
        const detail = element("details");
        detail.append(element("summary", "", "문서 텍스트 보기"), element("p", "prose", asset.text));
        file.append(detail);
      }
      gallery.append(file);
    }
  }
  return gallery;
}

function section(title, content) {
  const node = element("section", "section");
  node.append(element("h3", "", title), content);
  return node;
}

async function renderItem(key, signal) {
  const data = await get("item", { key }, signal);
  if (signal.aborted) return;
  const kind = key.split("/")[0];
  const prior = lastList.split("?")[0];
  const listing = prior === kind || ["calendar", "supplies", "stats"].includes(prior) ? lastList : kind;
  const article = element("article", "entry");

  const topbar = element("div", "entry-topbar");
  topbar.append(link("← " + labels[listing.split("?")[0]] + " 목록", "#" + listing, "back"));
  const tools = element("div", "entry-tools");
  const totalAssets = data.attachments.length + data.comments.reduce((acc, c) => acc + c.attachments.length, 0);
  if (totalAssets > 0) {
    const zipLink = link("첨부 전체 ZIP (" + totalAssets + ")", zipUrl(key), "tool-link");
    zipLink.setAttribute("download", "");
    tools.append(zipLink);
  }
  tools.append(button("인쇄 / PDF", () => window.print(), "tool-link"));
  topbar.append(tools);
  article.append(topbar);

  const heading = element("header", "entry-header");
  if (data.date) heading.append(time(data.date));
  heading.append(element("h2", "", data.title || labels[kind]));
  if (data.author) heading.append(element("p", "", data.author));
  article.append(heading);
  if (data.body) article.append(element("p", "prose", data.body));
  if (data.home_note) {
    const note = element("div", "home-note");
    const meta = element("div", "supply-meta");
    for (const label of data.home_note.labels) meta.append(element("span", "kind-badge soft-badge", label));
    note.append(meta);
    if (data.home_note.memo) note.append(element("p", "prose", data.home_note.memo));
    article.append(section("🏠 이날 집에서 남긴 기록", note));
  }
  if (data.fields.length) {
    const fields = element("dl", "facts");
    for (const info of data.fields) {
      const fact = element("div", "fact");
      fact.append(element("dt", "", info.label), element("dd", "", info.value));
      fields.append(fact);
    }
    const sectionTitles = {
      menus: "하루 식단",
      reports: "생활 기록",
      schedules: "일정 정보",
      medications: "투약 의뢰 내용",
      return_homes: "귀가 동의 내용",
      temperatures: "월간 체온 기록",
    };
    article.append(section(sectionTitles[kind] || "상세 정보", fields));
  }

  const allImages = [];
  if (data.attachments.length) {
    article.append(section("사진·영상·파일", mediaBlock(data.attachments, key, allImages)));
  }
  if (data.comments.length) {
    const list = element("div");
    for (const comment of data.comments) {
      const box = element("article", "comment");
      box.append(element("strong", "", comment.name || "작성자"));
      if (comment.date) box.append(time(comment.date));
      if (comment.body) box.append(element("p", "prose", comment.body));
      if (comment.attachments.length) box.append(mediaBlock(comment.attachments, key, allImages));
      list.append(box);
    }
    article.append(section("댓글 " + data.comments.length + "개", list));
  }
  main.replaceChildren(article);
}

// ------------------------------------------------------------------ Supplies checklist

function supplyId(entry) {
  return (entry.page_id || String(entry.item_id)) + "|" + entry.text.slice(0, 200);
}

async function renderSupplies(signal) {
  const data = await get("board", {}, signal);
  if (signal.aborted) return;
  lastList = "supplies";
  const checkedSet = new Set(data.checked || []);

  const container = element("div");

  function drawList() {
    const total = data.requests.length;
    const remaining = data.requests.filter(entry => !checkedSet.has(supplyId(entry))).length;
    const visible = hideCompletedSupplies
      ? data.requests.filter(entry => !checkedSet.has(supplyId(entry)))
      : data.requests;

    const toolbar = element("div", "supplies-toolbar");
    const summaryText = element("span", "supplies-summary");
    summaryText.append(
      document.createTextNode("남은 준비물 "),
      element("strong", "", remaining + "건"),
      document.createTextNode(" / 전체 " + total + "건"),
    );
    const toggle = element("label", "toggle-label");
    const toggleBox = element("input");
    toggleBox.type = "checkbox";
    toggleBox.checked = hideCompletedSupplies;
    toggleBox.addEventListener("change", () => {
      hideCompletedSupplies = toggleBox.checked;
      drawList();
    });
    toggle.append(toggleBox, element("span", "", "챙김 완료 숨기기"));
    toolbar.append(summaryText, toggle);

    const nodes = [element("h2", "section-heading", labels.supplies), toolbar];
    if (!total) {
      nodes.push(message("최근 30일간 확인된 준비물 요청이 없습니다."));
    } else if (!visible.length) {
      nodes.push(message("모든 준비물을 챙겼습니다! 🎉"));
    } else {
      for (const entry of visible) {
        const id = supplyId(entry);
        const isChecked = checkedSet.has(id);
        const row = element("article", "supply-entry" + (isChecked ? " is-checked" : ""));
        const box = element("input", "supply-check");
        box.type = "checkbox";
        box.checked = isChecked;
        box.setAttribute("aria-label", entry.text + " 챙김 완료");
        box.addEventListener("change", async () => {
          const nextState = box.checked;
          if (nextState) checkedSet.add(id);
          else checkedSet.delete(id);
          drawList();
          try {
            const res = await post("supplies/check", { id, checked: nextState });
            if (!res.ok) throw new Error("save failed");
          } catch {
            if (nextState) checkedSet.delete(id);
            else checkedSet.add(id);
            drawList();
            showToast("체크 상태를 저장하지 못했습니다.");
          }
        });
        const bodyNode = element("div", "supply-body");
        const meta = element("div", "supply-meta");
        meta.append(time(entry.posted), element("span", "kind-badge", entry.source));
        bodyNode.append(meta, link(entry.text, "#item/" + entry.page_id, "supply-link"));
        row.append(box, bodyNode);
        nodes.push(row);
      }
    }
    container.replaceChildren(...nodes);
  }

  drawList();
  main.replaceChildren(container);
}

// ------------------------------------------------------------------ Integrated Calendar

function boardEntry(entry) {
  const node = element("article", "board-entry");
  if (entry.start && entry.start !== entry.end) {
    node.append(time(entry.start), document.createTextNode(" ~ "), time(entry.end));
  } else {
    node.append(time(entry.start || entry.posted));
  }
  node.append(element("p", "excerpt", entry.source), link(entry.text, "#item/" + entry.page_id));
  return node;
}

function dayRecordCard(rec) {
  const anchor = link("", "#item/" + rec.key, "day-record");
  if (rec.cover) {
    const img = element("img");
    img.src = mediaUrl(rec.key, rec.cover);
    img.alt = "";
    img.loading = "lazy";
    anchor.append(img);
  }
  const bodyNode = element("div", "day-record-body");
  const meta = element("div", "day-record-meta");
  meta.append(element("span", "kind-badge", labels[rec.kind] || rec.kind));
  if (rec.owner_name) meta.append(element("span", "", rec.owner_name));
  if (rec.date) meta.append(time(rec.date));
  bodyNode.append(meta, element("div", "day-record-title", rec.title || labels[rec.kind]));
  if (rec.excerpt && rec.excerpt !== rec.title) {
    bodyNode.append(element("div", "day-record-excerpt", rec.excerpt));
  }
  anchor.append(bodyNode);
  return anchor;
}

function isoDay(year, month, day) {
  return new Date(Date.UTC(year, month, day)).toISOString().slice(0, 10);
}

function pad2(n) {
  return String(n).padStart(2, "0");
}

async function renderCalendar(params, signal) {
  const initialMonth = params.get("month") || "";
  const ownerFilter = params.get("owner") || "";
  let selectedDay = params.get("day") || "";

  const data = await get("calendar", {
    ...(initialMonth ? { month: initialMonth } : {}),
    ...(ownerFilter ? { owner: ownerFilter } : {}),
  }, signal);
  if (signal.aborted) return;

  const [year, monthNum] = data.month.split("-").map(Number);
  const monthIndex = monthNum - 1;
  if (selectedDay && !selectedDay.startsWith(data.month)) selectedDay = "";

  function updateHash(nextMonth, nextDay, nextOwner) {
    const q = new URLSearchParams();
    if (nextMonth) q.set("month", nextMonth);
    if (nextDay) q.set("day", nextDay);
    if (nextOwner) q.set("owner", nextOwner);
    lastList = "calendar" + (q.size ? "?" + q : "");
    return "#" + lastList;
  }

  updateHash(data.month, selectedDay, ownerFilter);

  const controls = element("div", "calendar-controls");
  const legend = element("div", "calendar-legend");
  for (const [cls, text] of [
    ["dot-event", "일정"], ["dot-reports", "알림장"], ["dot-albums", "앨범"],
    ["dot-menus", "식단"], ["dot-notices", "공지"],
  ]) {
    const item = element("span", "legend-item");
    item.append(element("span", "dot " + cls), element("span", "", text));
    legend.append(item);
  }
  controls.append(legend);

  if (data.owners.length > 1) {
    const ownerSelect = element("select");
    ownerSelect.setAttribute("aria-label", "자녀 필터");
    fillSelect(ownerSelect, [
      { id: "", name: "모든 자녀" },
      ...data.owners.map(o => ({ id: o.id, name: o.name || ("자녀 ID " + o.id) })),
    ], ownerFilter);
    ownerSelect.addEventListener("change", () => {
      location.hash = updateHash(data.month, selectedDay, ownerSelect.value);
    });
    controls.append(ownerSelect);
  }

  const wrapper = element("div", "calendar-layout");
  const grid = element("section", "calendar");
  const panel = element("section", "day-panel");
  wrapper.append(grid, panel);

  function goMonthOffset(offset) {
    const next = new Date(Date.UTC(year, monthIndex + offset, 1));
    const ym = next.getUTCFullYear() + "-" + pad2(next.getUTCMonth() + 1);
    location.hash = updateHash(ym, "", ownerFilter);
  }

  function goToday() {
    location.hash = updateHash(data.today.slice(0, 7), data.today, ownerFilter);
  }

  function drawPanel() {
    const dayEvents = selectedDay
      ? data.events.filter(e => e.start <= selectedDay && e.end >= selectedDay)
      : data.events;
    const dayRecords = selectedDay
      ? data.records.filter(r => r.date === selectedDay)
      : data.records;

    const nodes = [element("h3", "day-panel-heading", selectedDay ? selectedDay + " 기록" : year + "년 " + monthNum + "월 전체 기록")];

    if (dayEvents.length) {
      const grp = element("div", "day-group");
      grp.append(element("h4", "day-group-title", "📅 일정 (" + dayEvents.length + ")"), ...dayEvents.map(boardEntry));
      nodes.push(grp);
    }

    const kindOrder = [
      ["schedules", "🗓 일정표"],
      ["reports", "📝 알림장"],
      ["albums", "📷 앨범"],
      ["menus", "🍱 식단"],
      ["notices", "📢 공지"],
      ["medications", "💊 투약의뢰서"],
      ["return_homes", "🏠 귀가동의서"],
    ];

    let anyRecord = false;
    for (const [k, labelText] of kindOrder) {
      const matching = dayRecords.filter(r => r.kind === k);
      if (!matching.length) continue;
      anyRecord = true;
      const grp = element("div", "day-group");
      const shown = selectedDay ? matching : matching.slice(0, 8);
      grp.append(element("h4", "day-group-title", labelText + " (" + matching.length + ")"), ...shown.map(dayRecordCard));
      if (!selectedDay && matching.length > shown.length) {
        grp.append(link(labelText + " 전체 보기 (" + matching.length + "건) →", "#" + k + "?month=" + data.month, "excerpt"));
      }
      nodes.push(grp);
    }

    if (!dayEvents.length && !anyRecord) {
      nodes.push(message(selectedDay ? "이 날짜에는 저장된 일정이나 기록이 없습니다." : "이번 달에 저장된 일정이나 기록이 없습니다."));
    }
    panel.replaceChildren(...nodes);
  }

  function drawGrid(focusDay) {
    const heading = element("div", "calendar-header");
    const prevBtn = button("‹ 이전 달", () => goMonthOffset(-1));
    const todayBtn = button("오늘", goToday);
    const nextBtn = button("다음 달 ›", () => goMonthOffset(1));
    const leftNav = element("div", "calendar-header-nav");
    leftNav.append(prevBtn);
    const rightNav = element("div", "calendar-header-nav");
    rightNav.append(todayBtn, nextBtn);
    heading.append(leftNav, element("h3", "", year + "년 " + monthNum + "월"), rightNav);

    const table = element("table", "calendar-table");
    table.setAttribute("aria-label", year + "년 " + monthNum + "월 달력");
    const header = table.createTHead().insertRow();
    for (const name of ["일", "월", "화", "수", "목", "금", "토"]) {
      const th = element("th", "", name);
      th.scope = "col";
      header.append(th);
    }

    const tbody = table.createTBody();
    const firstWeekday = new Date(Date.UTC(year, monthIndex, 1)).getUTCDay();
    const daysInMonth = new Date(Date.UTC(year, monthIndex + 1, 0)).getUTCDate();
    let activeBtn;

    for (let idx = 0; idx < firstWeekday + daysInMonth; idx += 1) {
      const row = idx % 7 === 0 ? tbody.insertRow() : tbody.rows[tbody.rows.length - 1];
      const cell = row.insertCell();
      const day = idx - firstWeekday + 1;
      if (day < 1) continue;
      const value = isoDay(year, monthIndex, day);

      const dayRecs = data.records.filter(r => r.date === value);
      const kindsPresent = new Set(dayRecs.map(r => r.kind));
      const hasEvent = data.events.some(e => e.start <= value && e.end >= value) || kindsPresent.has("schedules");
      const hasAny = hasEvent || dayRecs.length > 0;

      const dayButton = element("button", "calendar-day" + (hasAny ? " has-items" : "") + (hasEvent ? " has-event" : ""));
      dayButton.type = "button";
      dayButton.append(element("span", "", String(day)));

      const dots = element("span", "day-dots");
      dots.setAttribute("aria-hidden", "true");
      if (hasEvent) dots.append(element("span", "dot dot-event"));
      for (const k of ["reports", "albums", "menus", "notices"]) {
        if (kindsPresent.has(k)) dots.append(element("span", "dot dot-" + k));
      }
      dayButton.append(dots);

      const summaryParts = [];
      if (hasEvent) summaryParts.push("일정");
      for (const k of ["reports", "albums", "menus", "notices"]) {
        if (kindsPresent.has(k)) summaryParts.push(labels[k]);
      }
      dayButton.setAttribute("aria-label", value + (summaryParts.length ? ", " + summaryParts.join("·") + " 있음" : ", 기록 없음"));
      dayButton.setAttribute("aria-pressed", String(value === selectedDay));
      if (value === data.today) dayButton.setAttribute("aria-current", "date");

      dayButton.addEventListener("click", () => {
        selectedDay = selectedDay === value ? "" : value;
        updateHash(data.month, selectedDay, ownerFilter);
        history.replaceState(null, "", "#" + lastList);
        drawGrid(value);
        drawPanel();
      });

      if (value === focusDay) activeBtn = dayButton;
      cell.append(dayButton);
    }

    grid.replaceChildren(heading, table);
    if (activeBtn) activeBtn.focus();
  }

  drawGrid();
  drawPanel();
  main.replaceChildren(element("h2", "section-heading", labels.calendar), controls, wrapper);
}

// ------------------------------------------------------------------ Stats (통계분석)
//
// A place for parents to notice small changes in their child's day. Every
// indicator here is a gentle prompt to look again or ask the teacher, never a
// judgement about the teacher.

const STAT_PERIOD_LIST = [
  { id: "30", name: "최근 30일" }, { id: "90", name: "최근 90일" }, { id: "180", name: "최근 6개월" },
  { id: "365", name: "최근 1년" }, { id: "all", name: "전체" },
];
const FLAG_FILTERS = [
  { id: "", name: "전체" }, { id: "shift", name: "평소와 다른 시간" }, { id: "extended", name: "연장·통합보육" },
  { id: "after", name: "보육시간 이후" }, { id: "before", name: "보육 시작 전" }, { id: "late", name: "날짜보다 늦게" },
];
const STAT_GUIDE = [
  {
    title: "이 탭은 이렇게 써요",
    items: [
      "하원 후 집에서 살펴본 내용은 '하원 후 기록'에 남겨 두세요. 같은 날 알림장을 열면 함께 보여요.",
      "'한 번 더 살펴볼 알림장'은 올라온 시간만 보고 고른 목록이에요. 열어 보고 괜찮으면 '확인함'을 눌러 정리하세요.",
      "여기 보이는 숫자와 목록은 모두 참고 자료예요. 선생님을 평가하거나 어떤 일이 있었다고 판단하는 기준이 아니에요.",
      "하원 후 기록과 확인 표시는 이 Home Assistant 안에만 저장되고 다른 곳으로 보내지 않아요.",
    ],
  },
  {
    title: "등원할 때 살펴보기",
    items: [
      "얼굴·팔·다리에 상처가 있는지, 체온과 기분은 어떤지 가볍게 살펴봐 주세요. 이미 있던 상처는 사진으로 남겨 두면 좋아요.",
      "밤잠이나 아침 식사, 컨디션을 선생님께 짧게 전해 주시면 선생님이 하루를 돌보는 데도 도움이 돼요.",
      "등원할 때 유난히 오래 울거나 들어가기 싫어하는 날이 이어지는지 지켜봐 주세요.",
    ],
  },
  {
    title: "하원 후 집에서 살펴보기",
    items: [
      "목욕하거나 옷을 갈아입힐 때 등·엉덩이·팔 안쪽·머리처럼 잘 보이지 않는 곳도 함께 살펴봐 주세요.",
      "새로 생긴 상처가 있으면 날짜가 보이게 사진을 찍고, 다음 날 선생님께 어떻게 다쳤는지 여쭤봐 주세요.",
      "갑자기 낮잠이나 식사를 거부하는지, 쉽게 놀라는지, 놀이 속에서 낯선 행동을 반복하는지도 살펴봐 주세요.",
      "알림장 내용과 집에서 본 아이 모습이 자주 다르다면 기록해 두었다가 선생님과 이야기해 보세요.",
    ],
  },
  {
    title: "선생님과 이야기할 때",
    items: [
      "궁금한 점은 날짜와 상황을 구체적으로 적어 차분히 여쭤보세요. 대부분은 대화로 금방 풀려요.",
      "평소 고마운 마음을 자주 전하면 선생님도 아이의 작은 소식을 더 편하게 나눠 주세요.",
    ],
  },
  {
    title: "어린이집 CCTV 열람 방법",
    items: [
      "보관 기간: 어린이집 CCTV 영상은 60일 이상 보관해야 해요. 휴원일을 빼면 실제로 확인할 수 있는 기간이 더 짧을 수 있으니, 걱정되는 일이 있으면 미루지 말고 요청하세요.",
      "요청 방법: 어린이집에 '영상정보 열람 요청서'(또는 의사소견서)를 서면으로 내요. 서식은 어린이집이나 보건복지부 '보육사업안내'에서 받을 수 있어요.",
      "어린이집 회신: 요청을 받은 날부터 10일 안에 열람 장소와 일시를 알려줘야 해요. 열람을 거부한다면 그 사유도 10일 안에 서면으로 알려야 해요.",
      "열람하러 갈 때: 신분증과 가족관계증명서처럼 보호자임을 확인할 서류를 챙기세요. 비밀유지 서약서 작성을 요청받을 수 있어요.",
    ],
  },
  {
    title: "도움이 필요할 때",
    items: [
      "정당한 사유 없이 열람을 거부당하면 관할 시·군·구 보육 담당 부서에 상담할 수 있어요.",
      "아이가 위험하다고 느껴지면 망설이지 말고 112에 신고하세요. 신고한 사람의 신분은 보호돼요.",
      "법령은 바뀔 수 있어요. 정확한 내용은 국가법령정보센터의 '영유아보육법' 제15조의4·제15조의5와 시행규칙에서 확인하세요.",
    ],
  },
];

function statTile(label, value, hint, target) {
  const node = element("button", "stat-tile");
  node.type = "button";
  node.append(element("span", "stat-label", label), element("strong", "stat-value", value));
  if (hint) node.append(element("span", "stat-hint", hint));
  node.addEventListener("click", () => {
    const section = document.getElementById(target);
    if (!section) return;
    if (section.tagName === "DETAILS") section.open = true;
    section.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  return node;
}

function statCard(id, title, intro) {
  const card = element("section", "stat-card");
  card.id = id;
  card.append(element("h3", "", title));
  if (intro) card.append(element("p", "stat-hint", intro));
  return card;
}

function foldCard(id, title, summaryText) {
  const card = element("details", "stat-card stat-fold");
  card.id = id;
  const summary = element("summary");
  summary.append(element("span", "fold-title", title));
  if (summaryText) summary.append(element("span", "fold-sub", summaryText));
  card.append(summary);
  return card;
}

function guideList(title, items) {
  const box = element("div", "guide-block");
  box.append(element("h4", "", title));
  const list = element("ul");
  for (const text of items) list.append(element("li", "", text));
  box.append(list);
  return box;
}

function reportLink(entry, extra) {
  const row = element("div", "stat-row");
  const meta = element("div", "supply-meta");
  if (entry.date) meta.append(time(entry.date));
  if (extra) meta.append(extra);
  row.append(meta, link(entry.title || "알림장", "#item/" + entry.key, "supply-link"));
  return row;
}

function hourBand(hour) {
  const minute = hour * 60;
  if (minute < 450) return "before";
  if (minute < 540) return "morning";
  if (minute < 960) return "core";
  if (minute < 1170) return "extended";
  return "after";
}

function todayKst() {
  return new Date(Date.now() + 9 * 3600 * 1000).toISOString().slice(0, 10);
}

function barChart(className, columns, label) {
  const peak = Math.max(...columns.map(col => col.value), 1);
  const chart = element("div", className);
  chart.setAttribute("role", "img");
  chart.setAttribute("aria-label", label);
  for (const col of columns) {
    const node = element("div", "bar-col " + (col.className || ""));
    node.title = col.title;
    const track = element("span", "bar-track");
    const bar = element("span", "bar-fill");
    bar.style.height = (col.value / peak * 100) + "%";
    track.append(bar);
    node.append(element("span", "bar-count", col.value ? String(col.value) : ""), track,
      element("span", "bar-label", col.label));
    chart.append(node);
  }
  return chart;
}

function homeNotesSection(data, state) {
  const card = statCard("stat-notes", "🏠 하원 후 기록",
    "집에서 살펴본 아이 모습을 날짜별로 남겨 두세요. 같은 날 알림장을 열면 이 기록이 함께 보여요.");
  let notes = data.notes || [];
  let showAll = false;

  const form = element("form", "note-form");
  const dateInput = element("input");
  dateInput.type = "date";
  dateInput.max = todayKst();
  dateInput.value = todayKst();
  const flagBox = element("fieldset", "note-flags");
  flagBox.append(element("legend", "", "오늘 눈에 띈 점 (해당하는 것만)"));
  const boxes = new Map();
  for (const flag of data.home_flags || []) {
    const chip = element("label", "chip check-chip");
    const input = element("input");
    input.type = "checkbox";
    input.value = flag.id;
    chip.append(input, element("span", "", flag.label));
    boxes.set(flag.id, input);
    flagBox.append(chip);
  }
  const memo = element("textarea");
  memo.rows = 3;
  memo.maxLength = 1000;
  memo.placeholder = "예: 오늘은 낮잠을 짧게 잤대요. 저녁을 평소보다 잘 먹었어요.";
  const save = element("button", "primary-btn", "저장");
  save.type = "submit";
  // A second tap confirms; native confirm() dialogs are blocked in some app webviews.
  let removeArmed = null;
  const remove = button("이 날짜 기록 지우기", () => {
    if (removeArmed) {
      clearTimeout(removeArmed);
      removeArmed = null;
      submit(true);
      return;
    }
    remove.textContent = "한 번 더 누르면 지워져요";
    removeArmed = setTimeout(() => { removeArmed = null; remove.textContent = "이 날짜 기록 지우기"; }, 4000);
  }, "secondary-btn");
  const actions = element("div", "note-actions");
  actions.append(save, remove);
  form.append(field("날짜", dateInput), flagBox, field("메모", memo), actions);

  function load(day) {
    const note = notes.find(entry => entry.date === day);
    for (const [id, input] of boxes) input.checked = Boolean(note && note.flags.includes(id));
    memo.value = note ? note.memo : "";
    remove.hidden = !note;
    if (removeArmed) { clearTimeout(removeArmed); removeArmed = null; }
    remove.textContent = "이 날짜 기록 지우기";
    save.textContent = note ? "수정 저장" : "저장";
  }
  dateInput.addEventListener("change", () => load(dateInput.value));

  async function submit(clear) {
    if (!dateInput.value) return;
    const flags = [...boxes].filter(([, input]) => input.checked).map(([id]) => id);
    if (!clear && !flags.length && !memo.value.trim()) {
      showToast("눈에 띈 점을 고르거나 메모를 적어 주세요.");
      return;
    }
    save.disabled = true;
    try {
      const res = await post("home-notes", {
        owner: data.owner, date: dateInput.value,
        flags: clear ? [] : flags, memo: clear ? "" : memo.value,
      });
      if (!res.ok) throw new Error("save failed");
      notes = res.data.notes;
      state.onNotes(notes.length);
      showToast(clear ? "기록을 지웠어요." : "기록을 저장했어요.");
      load(dateInput.value);
      drawList();
    } catch {
      showToast("기록을 저장하지 못했어요. 잠시 후 다시 시도해 주세요.");
    } finally {
      save.disabled = false;
    }
  }
  form.addEventListener("submit", event => { event.preventDefault(); submit(false); });

  const list = element("div", "note-list");
  function drawList() {
    if (!notes.length) {
      list.replaceChildren(element("p", "stat-hint", "아직 남긴 기록이 없어요."));
      return;
    }
    const visible = showAll ? notes : notes.slice(0, 5);
    const rows = visible.map(note => {
      const row = element("div", "stat-row");
      const meta = element("div", "supply-meta");
      meta.append(time(note.date));
      for (const label of note.labels) meta.append(element("span", "kind-badge soft-badge", label));
      meta.append(button("수정", () => {
        dateInput.value = note.date;
        load(note.date);
        form.scrollIntoView({ behavior: "smooth", block: "center" });
        memo.focus({ preventScroll: true });
      }, "chip row-action"));
      row.append(meta);
      if (note.memo) row.append(element("p", "rep-text", note.memo));
      return row;
    });
    list.replaceChildren(element("h4", "", "최근 기록"), ...rows);
    if (notes.length > 5) {
      list.append(button(showAll ? "접기" : "기록 " + notes.length + "개 모두 보기", () => {
        showAll = !showAll;
        drawList();
      }, "secondary-btn more-btn"));
    }
  }
  load(dateInput.value);
  drawList();
  card.append(form, list);
  return card;
}

function lifeSection(life) {
  const card = statCard("stat-life", "🌡️ 아이 컨디션 변화",
    "알림장 생활기록에서 " + life.labels.join("·") + "이 평소와 달랐던 날을 주별로 모았어요. " +
    "평소보다 두 배 이상 많은 주는 색으로 표시해요. 그런 주에는 집에서도 피곤해하거나 아프지 않았는지 함께 살펴봐 주세요.");
  if (!life.recorded) {
    card.append(message("이 기간 알림장에는 생활기록(기분·식사·낮잠 등)이 없어요."));
    return card;
  }
  const labelStep = Math.ceil(life.weeks.length / 8);
  card.append(barChart("week-chart", life.weeks.map((week, index) => ({
    value: week.total,
    label: (life.weeks.length - 1 - index) % labelStep === 0 ? week.week.slice(5).replace("-", "/") : "",
    className: week.rising ? "is-rising" : "",
    title: week.week + " 주: " + (week.details.join(", ") || "특이사항 없음") + " (알림장 " + week.reports + "건)",
  })), "주별 컨디션 변화 횟수: " + life.weeks.map(week => week.week + " " + week.total + "회").join(", ")));
  if (life.rising.length) {
    card.append(element("h4", "", "평소보다 많았던 주"));
    for (const week of [...life.rising].reverse()) {
      const row = element("div", "stat-row");
      const meta = element("div", "supply-meta");
      meta.append(element("span", "kind-badge soft-badge", week.week + " 주"), element("span", "", "알림장 " + week.reports + "건"));
      row.append(meta, element("p", "rep-text", week.details.join(", ")));
      card.append(row);
    }
  } else {
    card.append(element("p", "stat-verdict", "이 기간에는 평소보다 눈에 띄게 많았던 주가 없어요."));
  }
  return card;
}

function timingSection(data, state) {
  const timing = data.timing;
  const card = statCard("stat-timing", "🔎 한 번 더 살펴볼 알림장",
    "선생님들은 수업·행사·상담 일정에 따라 알림장을 평소와 다른 시간에 쓰기도 해요. 대부분 자연스러운 일이니, " +
    "그날 아이 컨디션과 함께 가볍게 살펴보고 '확인함'을 눌러 정리해 주세요.");
  if (!timing.timed) {
    card.append(message("백업된 알림장에 올라온 시각 정보가 없어서 이 기능을 쓸 수 없어요."));
    return card;
  }
  if (timing.baseline) {
    card.append(element("p", "stat-verdict",
      "이 기간 알림장은 보통 " + timing.baseline.median + " 무렵에 올라왔어요."));
  } else {
    card.append(element("p", "stat-hint",
      "알림장이 8건 이상 쌓이면 평소 올라오는 시간을 계산해 비교해 드려요. 지금은 시간대만 보고 골랐어요."));
  }

  const reviewed = state.reviewed;
  const toolbar = element("div", "supplies-toolbar");
  const summary = element("span", "supplies-summary");
  const toggle = element("label", "toggle-label");
  const toggleBox = element("input");
  toggleBox.type = "checkbox";
  toggleBox.checked = showReviewedFlags;
  toggleBox.addEventListener("change", () => { showReviewedFlags = toggleBox.checked; draw(); });
  toggle.append(toggleBox, element("span", "", "확인한 알림장도 보기"));
  toolbar.append(summary, toggle);
  const chips = element("div", "chip-row");
  const list = element("div");

  function reviewBox(entry) {
    const label = element("label", "review-check");
    const box = element("input");
    box.type = "checkbox";
    box.checked = reviewed.has(entry.key);
    box.addEventListener("change", async () => {
      const next = box.checked;
      if (next) reviewed.add(entry.key); else reviewed.delete(entry.key);
      draw();
      state.onChange();
      try {
        const res = await post("stats/review", { key: entry.key, reviewed: next });
        if (!res.ok) throw new Error("save failed");
        if (next && !showReviewedFlags) showToast("확인했어요. '확인한 알림장도 보기'에서 다시 볼 수 있어요.");
      } catch {
        if (next) reviewed.delete(entry.key); else reviewed.add(entry.key);
        draw();
        state.onChange();
        showToast("확인 표시를 저장하지 못했어요.");
      }
    });
    label.append(box, element("span", "", "확인함"));
    return label;
  }

  function draw() {
    const open = timing.flagged.filter(f => !reviewed.has(f.key));
    summary.replaceChildren(document.createTextNode("확인 전 "), element("strong", "", open.length + "건"),
      document.createTextNode(" · 전체 " + timing.flagged.length + "건"));
    const pool = showReviewedFlags ? timing.flagged : open;
    if (!pool.some(f => !state.filter || f.tags.includes(state.filter))) state.filter = "";
    chips.replaceChildren(...FLAG_FILTERS.flatMap(option => {
      const count = option.id ? pool.filter(f => f.tags.includes(option.id)).length : pool.length;
      if (option.id && !count) return [];
      const chip = button(option.name + " " + count, () => {
        state.filter = option.id;
        state.saveHash();
        draw();
      }, "chip" + (state.filter === option.id ? " is-active" : ""));
      chip.setAttribute("aria-pressed", String(state.filter === option.id));
      return [chip];
    }));
    chips.hidden = pool.length === 0;
    const visible = pool.filter(f => !state.filter || f.tags.includes(state.filter));
    if (!visible.length) {
      list.replaceChildren(message(timing.flagged.length
        ? "모두 확인했어요. 고마워요! 🙂" : "이 기간에는 따로 살펴볼 알림장이 없어요."));
      return;
    }
    list.replaceChildren(...visible.map(entry => {
      const row = reportLink(entry, element("span", "kind-badge", entry.uploaded + " 올라옴"));
      row.classList.add("flag-row");
      if (reviewed.has(entry.key)) row.classList.add("is-reviewed");
      row.firstChild.append(reviewBox(entry));
      const reasons = element("ul", "reason-list");
      for (const reason of entry.reasons) reasons.append(element("li", "", reason));
      row.append(reasons);
      return row;
    }));
  }
  draw();
  card.append(toolbar, chips, list);
  if (timing.flagged_total > timing.flagged.length) {
    card.append(element("p", "stat-hint", "최근 " + timing.flagged.length + "건까지만 보여 드려요. 기간을 줄이면 더 오래된 알림장도 볼 수 있어요."));
  }

  const chartBox = element("details", "inner-fold");
  chartBox.append(element("summary", "", "시간대별로 언제 올라왔는지 보기"));
  chartBox.append(barChart("hour-chart", timing.hist.map((count, hour) => ({
    value: count,
    label: hour % 3 === 0 ? String(hour) : "",
    className: "band-" + hourBand(hour),
    title: hour + "시대 " + count + "건",
  })), "시간대별 알림장 수: " + timing.hist.map((count, hour) => count ? hour + "시 " + count + "건" : "").filter(Boolean).join(", ")));
  const legend = element("ul", "band-legend");
  for (const band of timing.bands) {
    const li = element("li", "band-" + band.id);
    li.append(element("span", "band-swatch"), document.createTextNode(band.label + " " + band.count + "건"));
    legend.append(li);
  }
  chartBox.append(legend, element("p", "stat-hint",
    "올라온 시각은 키즈노트에 기록된 작성 시각이에요. 보통 아이들이 낮잠 자는 시간에 몰아서 쓰는 경우가 많아요."));
  card.append(chartBox);
  return card;
}

function photoSection(photos) {
  const card = statCard("stat-photos", "📷 사진이 뜸했던 기간",
    "앨범이나 알림장에 사진이 평일 기준으로 평소보다 오래 올라오지 않은 기간이에요. " +
    "방학·휴원·결석이나 행사 준비로 자연스럽게 생기기도 해요.");
  if (!photos.days) {
    card.append(message("이 기간에는 사진이 올라온 날이 없어요."));
    return card;
  }
  card.append(element("p", "stat-hint", "사진이 올라온 날 " + photos.days + "일 · 마지막 사진 " + photos.last));
  if (!photos.gaps.length) {
    card.append(element("p", "stat-verdict", "사진이 오래 비었던 기간은 없어요."));
    return card;
  }
  for (const gap of photos.gaps) {
    const row = element("div", "stat-row");
    const meta = element("div", "supply-meta");
    meta.append(element("span", "kind-badge soft-badge", "평일 " + gap.weekdays + "일"),
      document.createTextNode(gap.after + " 이후 " + (gap.until ? gap.until + "까지" : "지금까지")));
    row.append(meta);
    card.append(row);
  }
  return card;
}

function lengthVariety(cv) {
  if (cv === null || cv === undefined) return "알림장이 더 쌓이면 보여 드려요";
  if (cv < 0.15) return "매번 비슷한 길이예요";
  if (cv < 0.35) return "적당히 달라요";
  return "날마다 다양해요";
}

function patternSection(data) {
  const rep = data.repetition;
  const card = foldCard("stat-pattern", "📝 알림장 문장 살펴보기", rep.verdict);
  const body = element("div", "fold-body");
  body.append(element("p", "stat-hint",
    "여러 아이의 알림장을 함께 쓰다 보면 인사말이나 공통 안내 문구가 반복되는 건 자연스러워요. " +
    "반복이 많다고 정성이 부족하다는 뜻은 아니에요. 사람이 썼는지 AI의 도움을 받았는지는 글만으로 가려낼 수 없어서, " +
    "여기서는 판단하지 않고 반복과 길이 변화만 보여 드려요. 아이 이름만 다른 문장은 같은 문장으로 셌어요."));
  const facts = element("dl", "facts");
  for (const [label, value] of [
    ["다른 알림장에도 있는 문장", Math.round(rep.average_ratio * 100) + "%"],
    ["내용이 거의 같은 알림장", rep.pairs_total + "쌍"],
    ["알림장 길이", lengthVariety(rep.length_cv)],
  ]) {
    const fact = element("div", "fact");
    fact.append(element("dt", "", label), element("dd", "", value));
    facts.append(fact);
  }
  body.append(facts);

  if (rep.sentences.length) {
    body.append(element("h4", "", "자주 쓰인 문장"));
    for (const entry of rep.sentences.slice(0, 15)) {
      const row = element("div", "stat-row");
      const meta = element("div", "supply-meta");
      meta.append(element("span", "kind-badge", entry.count + "번"));
      for (const ref of entry.reports.slice(0, 5)) meta.append(link(ref.date || "보기", "#item/" + ref.key, "date-link"));
      row.append(meta, element("p", "rep-text", entry.text));
      body.append(row);
    }
  }
  if (rep.pairs.length) {
    body.append(element("h4", "", "내용이 거의 같은 알림장"));
    for (const pair of rep.pairs.slice(0, 10)) {
      const row = element("div", "stat-row pair-row");
      row.append(element("span", "kind-badge", Math.round(pair.score * 100) + "% 비슷"),
        link((pair.a.date || "") + " " + (pair.a.title || ""), "#item/" + pair.a.key, "supply-link"),
        element("span", "pair-sep", "↔"),
        link((pair.b.date || "") + " " + (pair.b.title || ""), "#item/" + pair.b.key, "supply-link"));
      body.append(row);
    }
  }
  card.append(body);
  return card;
}

function guideSection() {
  const card = foldCard("stat-guide", "📖 이용 안내 · 아이 살피기 · CCTV 열람", "한 번만 읽어 두면 좋아요");
  const grid = element("div", "fold-body guide-grid");
  for (const block of STAT_GUIDE) grid.append(guideList(block.title, block.items));
  card.append(grid);
  return card;
}

async function renderStats(params, signal) {
  const owner = params.get("owner") || "";
  const days = params.get("days") || "90";
  const data = await get("stats", { ...(owner ? { owner } : {}), days }, signal);
  if (signal.aborted) return;

  const state = { filter: params.get("flag") || "", reviewed: new Set(data.reviewed || []) };
  function hashFor(nextOwner, nextDays, nextFlag) {
    const q = new URLSearchParams();
    if (nextOwner) q.set("owner", nextOwner);
    if (nextDays && nextDays !== "90") q.set("days", nextDays);
    if (nextFlag) q.set("flag", nextFlag);
    return "stats" + (q.size ? "?" + q : "");
  }
  state.saveHash = () => {
    lastList = hashFor(data.owner, data.days, state.filter);
    history.replaceState(null, "", "#" + lastList);
  };
  lastList = hashFor(data.owner, data.days, state.filter);

  const nodes = [element("h2", "section-heading", labels.stats)];
  nodes.push(element("p", "stat-intro",
    "알림장과 집에서 남긴 기록을 모아, 아이의 작은 변화를 놓치지 않도록 돕는 곳이에요. " +
    "여기 보이는 내용은 참고 자료일 뿐 선생님을 평가하는 기준이 아니에요. 궁금한 점이 생기면 선생님께 편하게 여쭤보세요."));

  const controls = element("div", "filters stat-filters");
  const ownerSelect = element("select");
  fillSelect(ownerSelect, data.owners, data.owner);
  const periodSelect = element("select");
  fillSelect(periodSelect, STAT_PERIOD_LIST, data.days);
  const go = () => { location.hash = hashFor(ownerSelect.value, periodSelect.value, ""); };
  ownerSelect.addEventListener("change", go);
  periodSelect.addEventListener("change", go);
  if (data.owners.length > 1) controls.append(field("아이", ownerSelect));
  controls.append(field("기간", periodSelect));
  nodes.push(controls);

  if (!data.owner) {
    nodes.push(message("아직 백업된 알림장이 없어요. 백업이 끝나면 이곳에서 볼 수 있어요."), guideSection());
    main.replaceChildren(...nodes);
    return;
  }

  const tiles = element("div", "stat-tiles");
  const timing = data.timing;
  const openCount = () => timing.flagged.filter(f => !state.reviewed.has(f.key)).length;
  const notesTile = statTile("하원 후 기록", (data.notes || []).length + "개", "집에서 남긴 기록", "stat-notes");
  const reviewTile = statTile("살펴볼 알림장", timing.timed ? openCount() + "건" : "—",
    timing.timed ? "확인 전 알림장" : "시각 정보 없음", "stat-timing");
  state.onChange = () => {
    if (timing.timed) reviewTile.querySelector(".stat-value").textContent = openCount() + "건";
  };
  state.onNotes = count => { notesTile.querySelector(".stat-value").textContent = count + "개"; };
  tiles.append(
    notesTile,
    statTile("컨디션 변화", data.life.rising.length + "주", "평소보다 많았던 주", "stat-life"),
    reviewTile,
    statTile("사진 공백", data.photos.gaps.length + "번", "사진이 뜸했던 기간", "stat-photos"),
  );
  nodes.push(tiles);

  nodes.push(homeNotesSection(data, state));
  if (!data.total) {
    nodes.push(message("이 기간에는 분석할 알림장이 없어요. 기간을 늘려 보세요."));
  } else {
    nodes.push(lifeSection(data.life), timingSection(data, state), photoSection(data.photos), patternSection(data));
  }
  nodes.push(guideSection());
  main.replaceChildren(...nodes);
}

// ------------------------------------------------------------------ Router

async function render(moveFocus = true) {
  const rawHash = location.hash.slice(1);
  if (rawHash === "main") return;
  const current = rawHash || currentSettings.default_tab || "reports";
  routeController?.abort();
  routeController = new AbortController();
  const signal = routeController.signal;
  const [path, query] = current.split("?", 2);
  const key = path.startsWith("item/") ? path.slice(5) : "";
  const choice = key ? key.split("/")[0] : path;
  const kind = Object.hasOwn(labels, choice) ? choice : "reports";

  for (const tab of tabs) {
    if (tab.hash === "#" + kind) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  }
  main.setAttribute("aria-busy", "true");
  try {
    if (key) await renderItem(key, signal);
    else if (catalogKinds.has(kind)) await renderList(kind, new URLSearchParams(query), signal);
    else if (kind === "supplies") await renderSupplies(signal);
    else if (kind === "stats") await renderStats(new URLSearchParams(query), signal);
    else await renderCalendar(new URLSearchParams(query), signal);
  } catch (error) {
    if (signal.aborted) return;
    main.replaceChildren(message("보관함을 불러오지 못했습니다."), button("다시 불러오기", () => render()));
  } finally {
    if (!signal.aborted) {
      main.setAttribute("aria-busy", "false");
      if (moveFocus) {
        main.focus({ preventScroll: true });
        window.scrollTo(0, 0);
      }
    }
  }
}

// ------------------------------------------------------------------ Bootstrap

loadCachedSettings();
initSettingsModal();
initLightbox();

document.querySelector("#backup-now").addEventListener("click", triggerManualBackup);
document.querySelector(".skip").addEventListener("click", event => {
  event.preventDefault();
  main.focus();
});
window.addEventListener("hashchange", () => render());

(async () => {
  try {
    const serverSettings = await get("settings", {});
    applySettings(serverSettings);
  } catch { /* use cached/default settings */ }
  pollBackupStatus();
  render(false);
})();
