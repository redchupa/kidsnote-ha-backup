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

// ------------------------------------------------------------------ Stats (이상징후 참고 지표)

const STAT_PERIOD_LIST = [
  { id: "30", name: "최근 30일" }, { id: "90", name: "최근 90일" }, { id: "180", name: "최근 6개월" },
  { id: "365", name: "최근 1년" }, { id: "all", name: "전체" },
];
const FLAG_FILTERS = [
  { id: "", name: "전체" }, { id: "shift", name: "평소와 다른 시간" }, { id: "extended", name: "연장·통합보육" },
  { id: "after", name: "보육시간 외" }, { id: "before", name: "보육 전" }, { id: "late", name: "늦은 업로드" },
];
const PARENT_GUIDE = [
  {
    title: "🌅 등원할 때 확인해 주세요",
    items: [
      "아이 몸 상태(얼굴·팔·다리 상처, 체온, 기분)를 한 번 살펴보고, 이미 있던 상처는 사진으로 남겨 두세요.",
      "등원 차량·현관에서 유독 심하게 울거나 특정 선생님을 피하지는 않는지 지켜봐 주세요.",
      "밤잠·아침 식사·컨디션을 선생님께 짧게 전달하면 이후 알림장 내용과 비교하기 쉬워요.",
    ],
  },
  {
    title: "🏠 하원 후 집에서 확인해 주세요",
    items: [
      "목욕이나 옷 갈아입을 때 등·엉덩이·팔 안쪽·머리처럼 잘 보이지 않는 곳에 멍이나 긁힌 자국이 없는지 확인해 주세요.",
      "새 상처가 있으면 날짜가 보이게 사진을 찍고, 다음 날 어린이집에 경위를 물어 기록해 두세요.",
      "갑자기 낮잠·식사를 거부하거나, 깜짝 놀라는 반응, 특정 놀이(때리기·재우기 흉내)를 반복하는지도 살펴봐 주세요.",
      "알림장 내용(잘 잤어요·잘 먹었어요)과 아이의 실제 컨디션이 자주 어긋나는지 함께 비교해 보세요.",
    ],
  },
];
const CCTV_GUIDE = [
  "보관 기간: 어린이집 CCTV 영상은 60일 이상 보관해야 해요. 휴원일을 빼면 실제로 볼 수 있는 기간이 더 짧을 수 있으니, 의심이 들면 바로 요청하세요.",
  "요청 방법: 어린이집에 「영상정보 열람 요청서」(또는 의사소견서)를 서면으로 내요. 서식은 어린이집이나 보건복지부 「보육사업안내」에서 받을 수 있어요.",
  "어린이집 회신: 요청을 받은 날부터 10일 안에 열람 장소·일시를 알려줘야 해요. 거부한다면 그 사유를 10일 안에 서면으로 알려야 해요.",
  "열람할 때: 신분증과 가족관계증명서처럼 보호자임을 확인할 서류를 챙기세요. 비밀유지 서약서를 요청받을 수 있어요.",
  "거부되면: 정당한 사유 없이 열람을 거부하면 과태료 대상이에요. 관할 시·군·구 보육 담당 부서에 상담·신고할 수 있어요.",
];
const REPORT_GUIDE = [
  "아동학대가 의심되면 112에 신고하세요. 신고자 신분은 보호돼요.",
  "법령은 바뀔 수 있으니 정확한 내용은 국가법령정보센터의 「영유아보육법」 제15조의4·제15조의5와 시행규칙에서 확인하세요.",
];

function statTile(label, value, hint) {
  const node = element("div", "stat-tile");
  node.append(element("span", "stat-label", label), element("strong", "stat-value", value));
  if (hint) node.append(element("span", "stat-hint", hint));
  return node;
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

async function renderStats(params, signal) {
  const owner = params.get("owner") || "";
  const days = params.get("days") || "90";
  let flagFilter = params.get("flag") || "";
  const data = await get("stats", { ...(owner ? { owner } : {}), days }, signal);
  if (signal.aborted) return;

  function hashFor(nextOwner, nextDays, nextFlag) {
    const q = new URLSearchParams();
    if (nextOwner) q.set("owner", nextOwner);
    if (nextDays && nextDays !== "90") q.set("days", nextDays);
    if (nextFlag) q.set("flag", nextFlag);
    return "stats" + (q.size ? "?" + q : "");
  }
  lastList = hashFor(data.owner, data.days, flagFilter);

  const nodes = [element("h2", "section-heading", labels.stats)];
  nodes.push(element("p", "stat-intro",
    "알림장이 올라온 시간과 문장 반복을 살펴, 다시 확인해 볼 만한 알림장을 골라 드려요. " +
    "학대 여부를 판정하는 기능은 아니에요. 평소와 다른 신호를 놓치지 않도록 돕는 참고 지표예요."));

  const controls = element("div", "filters");
  const ownerSelect = element("select");
  fillSelect(ownerSelect, data.owners, data.owner);
  const periodSelect = element("select");
  fillSelect(periodSelect, STAT_PERIOD_LIST, data.days);
  const go = () => { location.hash = hashFor(ownerSelect.value, periodSelect.value, flagFilter); };
  ownerSelect.addEventListener("change", go);
  periodSelect.addEventListener("change", go);
  if (data.owners.length > 1) controls.append(field("자녀", ownerSelect));
  controls.append(field("기간", periodSelect));
  nodes.push(controls);

  const guide = element("details", "stat-guide");
  guide.open = true;
  guide.append(element("summary", "", "👪 부모님 체크 안내 · CCTV 열람 방법"));
  const guideBody = element("div", "guide-grid");
  for (const block of PARENT_GUIDE) guideBody.append(guideList(block.title, block.items));
  guideBody.append(guideList("📹 CCTV 열람 신청 방법과 보관 기간", CCTV_GUIDE));
  guideBody.append(guideList("☎️ 신고·상담", REPORT_GUIDE));
  guide.append(guideBody);
  nodes.push(guide);

  if (!data.total) {
    nodes.push(message("이 기간에 분석할 알림장이 없습니다."));
    main.replaceChildren(...nodes);
    return;
  }

  const timing = data.timing;
  const rep = data.repetition;
  const tiles = element("div", "stat-tiles");
  tiles.append(
    statTile("분석한 알림장", data.total + "건", timing.untimed ? "업로드 시각 없음 " + timing.untimed + "건" : ""),
    statTile("평소 업로드 시각", timing.baseline ? timing.baseline.median : "—",
      timing.baseline ? "보통 ±" + timing.baseline.spread + "분" : "기준을 잡으려면 8건 이상 필요"),
    statTile("다시 볼 알림장", timing.flagged_total + "건", "시간대·늦은 업로드 기준"),
    statTile("반복 문장 비율", Math.round(rep.average_ratio * 100) + "%", "다른 알림장과 똑같은 문장"),
  );
  nodes.push(tiles);

  const histSection = element("section", "stat-card");
  histSection.append(element("h3", "", "⏰ 알림장 업로드 시간 분포"));
  if (!timing.timed) {
    histSection.append(message("백업된 알림장에 업로드 시각 정보가 없습니다."));
  } else {
    const peak = Math.max(...timing.hist, 1);
    const chart = element("div", "hour-chart");
    chart.setAttribute("role", "img");
    chart.setAttribute("aria-label", "시간대별 업로드 건수: " +
      timing.hist.map((count, hour) => count ? hour + "시 " + count + "건" : "").filter(Boolean).join(", "));
    timing.hist.forEach((count, hour) => {
      const col = element("div", "hour-col band-" + hourBand(hour));
      col.title = hour + "시대 " + count + "건";
      const track = element("span", "hour-track");
      const bar = element("span", "hour-bar");
      bar.style.height = (count / peak * 100) + "%";
      track.append(bar);
      col.append(element("span", "hour-count", count ? String(count) : ""), track,
        element("span", "hour-label", hour % 3 === 0 ? String(hour) : ""));
      chart.append(col);
    });
    histSection.append(chart);
    const legend = element("ul", "band-legend");
    for (const band of timing.bands) {
      const li = element("li", "band-" + band.id);
      li.append(element("span", "band-swatch"), document.createTextNode(band.label + " " + band.count + "건"));
      if (band.check && band.count) li.append(element("span", "kind-badge warn-badge", "확인"));
      legend.append(li);
    }
    histSection.append(legend);
    histSection.append(element("p", "stat-hint",
      "업로드 시각은 키즈노트 서버에 기록된 작성 시각이에요. 낮잠 시간(기본보육)에 몰아서 쓰는 경우가 많고, " +
      "연장·통합보육이나 보육시간 외에 올라온 알림장은 작성 경위를 한 번 더 살펴볼 만해요."));
  }
  nodes.push(histSection);

  const flagSection = element("section", "stat-card");
  flagSection.append(element("h3", "", "🔎 평소와 다른 시간대에 올라온 알림장 (" + timing.flagged_total + "건)"));
  const chips = element("div", "chip-row");
  const flagList = element("div");
  function drawFlags() {
    chips.replaceChildren(...FLAG_FILTERS.map(option => {
      const count = option.id ? timing.flagged.filter(f => f.tags.includes(option.id)).length : timing.flagged.length;
      const chip = button(option.name + " " + count, () => {
        flagFilter = option.id;
        lastList = hashFor(data.owner, data.days, flagFilter);
        history.replaceState(null, "", "#" + lastList);
        drawFlags();
      }, "chip" + (flagFilter === option.id ? " is-active" : ""));
      chip.setAttribute("aria-pressed", String(flagFilter === option.id));
      return chip;
    }));
    const visible = timing.flagged.filter(f => !flagFilter || f.tags.includes(flagFilter));
    if (!visible.length) {
      flagList.replaceChildren(message(timing.flagged.length ? "해당하는 알림장이 없습니다." : "평소와 다른 시간대에 올라온 알림장이 없어요."));
      return;
    }
    flagList.replaceChildren(...visible.map(entry => {
      const row = reportLink(entry, element("span", "kind-badge", "업로드 " + entry.uploaded));
      if (entry.author) row.firstChild.append(element("span", "", entry.author));
      const reasons = element("ul", "reason-list");
      for (const reason of entry.reasons) reasons.append(element("li", "", reason));
      row.append(reasons);
      return row;
    }));
  }
  drawFlags();
  flagSection.append(chips, flagList);
  if (timing.flagged_total > timing.flagged.length) {
    flagSection.append(element("p", "stat-hint", "최근 " + timing.flagged.length + "건만 표시했어요."));
  }
  nodes.push(flagSection);

  const repSection = element("section", "stat-card");
  repSection.append(element("h3", "", "📝 동일 문장·유사 알림장 검사"));
  repSection.append(element("p", "stat-verdict", rep.verdict));
  const repFacts = element("dl", "facts");
  for (const [label, value] of [
    ["반복 문장 비율 (평균)", Math.round(rep.average_ratio * 100) + "%"],
    ["거의 똑같은 알림장 쌍", rep.pairs_total + "쌍"],
    ["글 길이 들쭉날쭉함", rep.length_cv === null ? "—" : String(rep.length_cv)],
    ["문장 길이 들쭉날쭉함", rep.sentence_cv === null ? "—" : String(rep.sentence_cv)],
  ]) {
    const fact = element("div", "fact");
    fact.append(element("dt", "", label), element("dd", "", value));
    repFacts.append(fact);
  }
  repSection.append(repFacts);
  repSection.append(element("p", "stat-hint",
    "사람이 쓴 글인지 AI가 쓴 글인지 확실하게 가려낼 방법은 없어요. 대신 같은 문장이 반복되는지, " +
    "글·문장 길이가 지나치게 일정한지(들쭉날쭉함이 0에 가까울수록 일정함)를 보여 드려요. " +
    "아이 이름만 바꾼 문장도 같은 문장으로 세요."));

  if (rep.sentences.length) {
    const box = element("div", "rep-list");
    box.append(element("h4", "", "자주 반복된 문장"));
    for (const entry of rep.sentences) {
      const row = element("div", "stat-row");
      const meta = element("div", "supply-meta");
      meta.append(element("span", "kind-badge", entry.count + "회"));
      for (const ref of entry.reports.slice(0, 5)) meta.append(link(ref.date || "보기", "#item/" + ref.key, "date-link"));
      row.append(meta, element("p", "rep-text", entry.text));
      box.append(row);
    }
    repSection.append(box);
  }
  if (rep.pairs.length) {
    const box = element("div", "rep-list");
    box.append(element("h4", "", "내용이 거의 같은 알림장"));
    for (const pair of rep.pairs) {
      const row = element("div", "stat-row pair-row");
      row.append(element("span", "kind-badge", "유사도 " + Math.round(pair.score * 100) + "%"),
        link((pair.a.date || "") + " " + (pair.a.title || ""), "#item/" + pair.a.key, "supply-link"),
        element("span", "pair-sep", "↔"),
        link((pair.b.date || "") + " " + (pair.b.title || ""), "#item/" + pair.b.key, "supply-link"));
      box.append(row);
    }
    repSection.append(box);
  }
  if (rep.reports.length) {
    const box = element("div", "rep-list");
    box.append(element("h4", "", "반복 문장이 많은 알림장"));
    for (const entry of rep.reports) {
      box.append(reportLink(entry, element("span", "kind-badge",
        "반복 " + Math.round(entry.ratio * 100) + "% · " + entry.sentences + "문장")));
    }
    repSection.append(box);
  }
  nodes.push(repSection);

  if (data.authors.length) {
    const authSection = element("section", "stat-card");
    authSection.append(element("h3", "", "👩‍🏫 작성자별 알림장 수"));
    const list = element("ul", "author-list");
    for (const entry of data.authors) list.append(element("li", "", entry.name + " · " + entry.count + "건"));
    authSection.append(list);
    nodes.push(authSection);
  }

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
