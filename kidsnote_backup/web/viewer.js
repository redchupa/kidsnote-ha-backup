"use strict";

const main = document.querySelector("main");
const tabs = [...document.querySelectorAll(".tabs a")];
const labels = { reports: "알림장", albums: "앨범", notices: "공지", menus: "식단", supplies: "준비물", calendar: "달력" };
const catalogKinds = new Set(["reports", "albums", "notices", "menus"]);
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

function setStatus(status, running = false) {
  const stamp = status?.last_success_at;
  const state = running ? "백업 진행 중..." : {
    failed: "최근 백업 실패", partial: "일부 백업 실패",
    stopped: "백업 중지됨", deferred: "키즈노트 응답으로 백업 일시 중지",
  }[status?.state];
  const formatted = stamp ? new Intl.DateTimeFormat("ko", {
    dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Seoul",
  }).format(new Date(stamp)) : "";
  document.querySelector("#backup-status").textContent =
    [formatted ? "마지막 완료 " + formatted : "", state].filter(Boolean).join(" · ");
}

function updateBackupButton(info) {
  const btn = document.querySelector("#backup-now");
  if (!btn) return;
  if (!info.can_trigger) {
    btn.hidden = true;
    return;
  }
  btn.hidden = false;
  const inCooldown = info.cooldown_until && info.cooldown_until * 1000 > Date.now();
  if (info.running) {
    btn.disabled = true;
    btn.classList.add("is-busy");
    btn.textContent = "백업 중...";
  } else if (inCooldown) {
    btn.disabled = true;
    btn.classList.remove("is-busy");
    btn.textContent = "대기 중";
  } else {
    btn.disabled = false;
    btn.classList.remove("is-busy");
    btn.textContent = "지금 백업";
  }
  setStatus(info.status, info.running);
}

async function pollBackupStatus() {
  try {
    const info = await get("backup/status", {});
    updateBackupButton(info);
    if (info.running) {
      clearTimeout(backupPollTimer);
      backupPollTimer = setTimeout(pollBackupStatus, 3000);
    } else if (backupPollTimer !== null) {
      clearTimeout(backupPollTimer);
      backupPollTimer = null;
      showToast("백업이 완료되었습니다.");
      render(false);
    }
  } catch { /* ignore transient status errors */ }
}

async function triggerManualBackup() {
  const btn = document.querySelector("#backup-now");
  btn.disabled = true;
  btn.textContent = "요청 중...";
  try {
    const result = await post("backup/trigger", {});
    showToast(result.data.message || (result.ok ? "백업을 시작합니다." : "백업을 시작할 수 없습니다."));
    if (result.ok) {
      backupPollTimer = setTimeout(pollBackupStatus, 1500);
      btn.textContent = "백업 중...";
      btn.classList.add("is-busy");
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
  const ownerLabel = ["reports", "albums"].includes(kind) ? "자녀" : "기관";
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
  if (kind !== "menus") {
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
    fragment.append(message(data.status.last_success_at || data.total || Object.values(filters).some(Boolean)
      ? "조건에 맞는 기록이 없습니다."
      : "저장된 기록이 없습니다. 첫 백업이 끝나면 여기에 표시됩니다."));
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
  const listing = prior === kind || ["calendar", "supplies"].includes(prior) ? lastList : kind;
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
    article.append(section(kind === "menus" ? "하루 식단" : "생활 기록", fields));
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
      ["reports", "📝 알림장"],
      ["albums", "📷 앨범"],
      ["menus", "🍱 식단"],
      ["notices", "📢 공지"],
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

      const hasEvent = data.events.some(e => e.start <= value && e.end >= value);
      const dayRecs = data.records.filter(r => r.date === value);
      const kindsPresent = new Set(dayRecs.map(r => r.kind));
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
