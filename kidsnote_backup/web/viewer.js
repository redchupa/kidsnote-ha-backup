"use strict";

const main = document.querySelector("main");
const tabs = [...document.querySelectorAll(".tabs a")];
const labels = { reports: "알림장", albums: "앨범", notices: "공지", menus: "식단", supplies: "준비물", calendar: "달력" };
const catalogKinds = new Set(["reports", "albums", "notices", "menus"]);
const pageSize = 12;
let routeController;
let lastList = "reports";

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function link(text, href, className) {
  const node = element("a", className, text);
  node.href = href;
  return node;
}

function mediaUrl(key, file, download = false) {
  return "./media/" + key.split("/").map(encodeURIComponent).join("/") + "/" +
    encodeURIComponent(file) + (download ? "?download=1" : "");
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

async function get(endpoint, query, signal) {
  const response = await fetch("./api/" + endpoint + "?" + new URLSearchParams(query), {signal});
  if (!response.ok) throw new Error("Could not load " + endpoint);
  return response.json();
}

function setStatus(status) {
  const stamp = status.last_success_at;
  const state = {
    failed: "최근 백업 실패", partial: "일부 백업 실패",
    stopped: "백업 중지됨", deferred: "키즈노트 응답으로 백업 일시 중지",
  }[status.state];
  const formatted = stamp ? new Intl.DateTimeFormat("ko", {
    dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Seoul",
  }).format(new Date(stamp)) : "";
  document.querySelector("#backup-status").textContent =
    [formatted ? "마지막 완료 " + formatted : "", state].filter(Boolean).join(" · ");
}

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
  if (item.date) text.append(time(item.date));
  text.append(element("h3", "", item.title || labels[kind]));
  if (item.excerpt && item.excerpt !== item.title) text.append(element("p", "", item.excerpt));
  anchor.append(text);
  return anchor;
}

function button(text, action) {
  const node = element("button", "", text);
  node.type = "button";
  node.addEventListener("click", action);
  return node;
}

async function renderList(kind, params, signal) {
  const filters = {q: params.get("q") || "", owner: params.get("owner") || "", month: params.get("month") || ""};
  const page = Math.max(Number(params.get("page")) || 1, 1);
  const data = await get("catalog", {kind, ...filters, page}, signal);
  if (signal.aborted) return;
  setStatus(data.status);
  lastList = location.hash.slice(1) || kind;
  const search = element("input");
  search.type = "search";
  search.value = filters.q;
  search.maxLength = 200;
  search.placeholder = "제목, 본문, 댓글";
  const owner = element("select");
  const ownerLabel = ["reports", "albums"].includes(kind) ? "자녀" : "기관";
  fillSelect(owner, [
    {id: "", name: "모든 " + ownerLabel},
    ...data.owners.map(group => ({
      id: group.id,
      name: group.name ? group.name + " (ID " + group.id + ")" : ownerLabel + " ID " + group.id,
    })),
  ], filters.owner);
  const month = element("select");
  fillSelect(month, [{id: "", name: "모든 달"}, ...data.months.map(day => ({id: day, name: day}))], filters.month);
  const refine = () => goList(kind, {q: search.value.trim(), owner: owner.value, month: month.value});
  const row = element("div", "filters");
  search.addEventListener("keydown", event => {
    if (event.key === "Enter") {
      event.preventDefault();
      refine();
    }
  });
  const apply = button("검색", refine);
  apply.className = "search-button";
  owner.addEventListener("change", refine);
  month.addEventListener("change", refine);
  row.append(field("검색", search), apply, field(ownerLabel, owner), field("날짜", month));

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
    const prev = button("이전", () => goList(kind, {...filters, page: page - 1}));
    prev.disabled = page <= 1;
    const next = button("다음", () => goList(kind, {...filters, page: page + 1}));
    next.disabled = page >= pages;
    bar.append(prev, element("span", "", page + " / " + pages), next);
    fragment.append(bar);
  }
  main.replaceChildren(fragment);
}

function mediaBlock(assets, key) {
  const gallery = element("div", "gallery");
  for (const asset of assets) {
    const source = mediaUrl(key, asset.file);
    if (asset.type === "image") {
      const img = element("img");
      img.loading = "lazy";
      img.alt = asset.name === asset.file ? "첨부 사진" : asset.name;
      img.src = source;
      const full = link("", source);
      full.target = "_blank";
      full.rel = "noopener";
      full.append(img);
      gallery.append(full);
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
  const data = await get("item", {key}, signal);
  if (signal.aborted) return;
  const kind = key.split("/")[0];
  const prior = lastList.split("?")[0];
  const listing = prior === kind || ["calendar", "supplies"].includes(prior) ? lastList : kind;
  const article = element("article", "entry");
  article.append(link(labels[listing.split("?")[0]] + " 목록", "#" + listing, "back"));
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
  if (data.attachments.length) article.append(section("사진·영상·파일", mediaBlock(data.attachments, key)));
  if (data.comments.length) {
    const list = element("div");
    for (const comment of data.comments) {
      const box = element("article", "comment");
      box.append(element("strong", "", comment.name || "작성자"));
      if (comment.date) box.append(time(comment.date));
      if (comment.body) box.append(element("p", "prose", comment.body));
      if (comment.attachments.length) box.append(mediaBlock(comment.attachments, key));
      list.append(box);
    }
    article.append(section("댓글 " + data.comments.length + "개", list));
  }
  main.replaceChildren(article);
}

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

function isoDay(year, month, day) {
  return new Date(Date.UTC(year, month, day)).toISOString().slice(0, 10);
}

function calendar(board) {
  const wrapper = element("div", "calendar-layout");
  const grid = element("section", "calendar");
  const events = element("section");
  wrapper.append(grid, events);
  let [year, month] = board.today.split("-").map(Number);
  month -= 1;
  let selected = "";

  function changeMonth(offset) {
    const next = new Date(Date.UTC(year, month + offset, 1));
    year = next.getUTCFullYear();
    month = next.getUTCMonth();
    selected = "";
    draw(offset < 0 ? "prev" : "next");
  }

  function draw(focus) {
    const heading = element("div", "calendar-header");
    const previous = button("이전 달", () => changeMonth(-1));
    const next = button("다음 달", () => changeMonth(1));
    heading.append(previous, element("h3", "", year + "년 " + (month + 1) + "월"), next);
    const table = element("table", "calendar-table");
    table.setAttribute("aria-label", year + "년 " + (month + 1) + "월 일정");
    const header = table.createTHead().insertRow();
    for (const name of ["일", "월", "화", "수", "목", "금", "토"]) {
      const day = element("th", "", name);
      day.scope = "col";
      header.append(day);
    }
    const cells = table.createTBody();
    const firstWeekday = new Date(Date.UTC(year, month, 1)).getUTCDay();
    const days = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
    const monthStart = isoDay(year, month, 1);
    const monthEnd = isoDay(year, month, days);
    const upcoming = board.events.filter(entry => entry.start <= monthEnd && entry.end >= monthStart);
    let active;
    for (let index = 0; index < firstWeekday + days; index += 1) {
      const row = index % 7 === 0 ? cells.insertRow() : cells.rows[cells.rows.length - 1];
      const cell = row.insertCell();
      const day = index - firstWeekday + 1;
      if (day < 1) continue;
      const value = isoDay(year, month, day);
      const count = upcoming.filter(entry => entry.start <= value && entry.end >= value).length;
      const dayButton = button(String(day), () => {
        selected = selected === value ? "" : value;
        draw(value);
      });
      dayButton.className = "calendar-day" + (count ? " has-event" : "");
      dayButton.setAttribute("aria-label", value + (count ? ", 일정 " + count + "건" : ", 일정 없음"));
      dayButton.setAttribute("aria-pressed", String(value === selected));
      if (value === board.today) dayButton.setAttribute("aria-current", "date");
      if (value === focus) active = dayButton;
      cell.append(dayButton);
    }
    const shown = selected ? upcoming.filter(entry => entry.start <= selected && entry.end >= selected) : upcoming;
    events.replaceChildren(element("h3", "", selected ? selected + " 일정" : "이번 달 일정"),
      ...(shown.length ? shown.map(boardEntry) : [message("표시할 일정이 없습니다.")]));
    grid.replaceChildren(heading, table);
    if (focus) (active || (focus === "prev" ? previous : next)).focus();
  }

  draw();
  return wrapper;
}

async function renderBoard(kind, signal) {
  const data = await get("board", {}, signal);
  if (signal.aborted) return;
  lastList = kind;
  const content = kind === "calendar" ? [calendar(data)] :
    data.requests.length ? data.requests.map(boardEntry) : [message("최근 30일간 확인된 준비물 요청이 없습니다.")];
  main.replaceChildren(element("h2", "section-heading", labels[kind]), ...content);
}

async function render(moveFocus = true) {
  const current = location.hash.slice(1) || "reports";
  if (current === "main") return;
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
    else await renderBoard(kind, signal);
  } catch (error) {
    if (signal.aborted) return;
    main.replaceChildren(message("보관함을 불러오지 못했습니다."), button("다시 불러오기", () => render()));
  } finally {
    if (!signal.aborted) {
      main.setAttribute("aria-busy", "false");
      if (moveFocus) {
        main.focus({preventScroll: true});
        window.scrollTo(0, 0);
      }
    }
  }
}

document.querySelector(".skip").addEventListener("click", event => {
  event.preventDefault();
  main.focus();
});
window.addEventListener("hashchange", () => render());
render(false);
