const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[char]);
const shortSubject = (name) => String(name || "").replace("（包括寄生蟲學）", "").replace("（包括細菌與黴菌）", "");
const uniqueId = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), (value) => value.toString(16).padStart(2,"0")).join("");
const state = { tab: "today", summary: null, categories: [], noteSummaries: [], queue: [], queueIndex: 0, mode: "module", card: null, selections: new Set(), submitted: false, queueLabel: "", previousFocus: null, attemptId: null, noteSaved: "", noteSavePromise: null, noteAttachments: [], noteUploading: false, knowledge: null, knowledgeOriginal: "", knowledgeFocus: null, knowledgeQuestionReturn: false, knowledgeCardReturn: false, knowledgeCardSnapshot: null };
const empty = (title, detail) => `<div class="empty"><strong>${esc(title)}</strong>${esc(detail)}</div>`;
const loading = (label) => `<div class="loading-state"><strong>正在讀取</strong>${esc(label)}</div>`;
const inlineError = (error) => `<div class="inline-error" role="alert">讀取失敗：${esc(error.message)}。請稍後重試。</div>`;
const formatKnowledgeText = (value) => esc(value)
  .replace(/`(11[1-5]-[12]-[^-\s`/]+-[0-9]{2})`/g, '<button type="button" class="question-reference" data-question-id="$1">$1</button>')
  .replace(/`([^`]+)`/g, "<code>$1</code>")
  .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
const inlineKnowledge = (value) => {
  const parts = [];
  let cursor = 0;
  for (const match of value.matchAll(/\[([^\]]+)\]\(([^)]+)\)/g)) {
    parts.push(formatKnowledgeText(value.slice(cursor, match.index)));
    const url = match[2];
    const question = url.match(/^\/api\/source\/(11[1-5]-[12]-[^-\s`/]+-[0-9]{2})/);
    if (question) parts.push(`<a class="question-reference" data-question-id="${question[1]}" href="${esc(url)}">${esc(match[1])}</a>`);
    else if (/^https?:\/\//.test(url)) parts.push(`<a href="${esc(url)}" target="_blank" rel="noopener">${esc(match[1])}</a>`);
    else parts.push(esc(match[1]));
    cursor = match.index + match[0].length;
  }
  parts.push(formatKnowledgeText(value.slice(cursor)));
  return parts.join("");
};
function renderKnowledgeMarkdown(markdown) {
  const output = [];
  let list = null;
  const closeList = () => { if (list) { output.push(`</${list}>`); list = null; } };
  for (const raw of String(markdown || "").split("\n")) {
    const line = raw.trim();
    if (!line) { closeList(); continue; }
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) { closeList(); const level = Math.min(4, heading[1].length + 1); output.push(`<h${level}>${inlineKnowledge(heading[2])}</h${level}>`); continue; }
    const bullet = line.match(/^[-*]\s+(.+)$/);
    const numbered = line.match(/^\d+[.)]\s+(.+)$/);
    if (bullet || numbered) {
      const wanted = bullet ? "ul" : "ol";
      if (list !== wanted) { closeList(); output.push(`<${wanted}>`); list = wanted; }
      output.push(`<li>${inlineKnowledge((bullet || numbered)[1])}</li>`);
      continue;
    }
    closeList();
    output.push(line.startsWith("> ") ? `<blockquote>${inlineKnowledge(line.slice(2))}</blockquote>` : `<p>${inlineKnowledge(line)}</p>`);
  }
  closeList();
  return output.join("");
}

const noteInline = (value) => esc(value)
  .replace(/!\[([^\]]*)\]\((\/api\/note-image\/[0-9a-f]{32})\)/g, '<img class="note-inline-image" src="$2" alt="$1" loading="lazy">')
  .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
  .replace(/`([^`]+)`/g, "<code>$1</code>")
  .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

function tableCells(line) {
  let value = line.trim();
  if (value.startsWith("|")) value = value.slice(1);
  if (value.endsWith("|")) value = value.slice(0, -1);
  return value.split("|").map((cell) => cell.trim());
}

function renderNoteMarkdown(markdown) {
  const lines = String(markdown || "").split("\n"), output = [];
  let list = null;
  const closeList = () => { if (list) { output.push(`</${list}>`); list = null; } };
  for (let index = 0; index < lines.length; index += 1) {
    const raw = lines[index], line = raw.trim();
    const next = (lines[index + 1] || "").trim();
    if (line.includes("|") && /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(next)) {
      closeList();
      const headers = tableCells(line), alignments = tableCells(next).map((cell) => cell.startsWith(":") && cell.endsWith(":") ? "center" : cell.endsWith(":") ? "right" : "left");
      output.push(`<div class="note-table-wrap"><table><thead><tr>${headers.map((cell, i) => `<th style="text-align:${alignments[i] || "left"}">${noteInline(cell)}</th>`).join("")}</tr></thead><tbody>`);
      index += 2;
      while (index < lines.length && lines[index].trim() && lines[index].includes("|")) {
        const cells = tableCells(lines[index]);
        output.push(`<tr>${headers.map((_, i) => `<td style="text-align:${alignments[i] || "left"}">${noteInline(cells[i] || "")}</td>`).join("")}</tr>`);
        index += 1;
      }
      output.push("</tbody></table></div>");
      index -= 1;
      continue;
    }
    if (!line) { closeList(); continue; }
    const image = line.match(/^!\[([^\]]*)\]\((\/api\/note-image\/[0-9a-f]{32})\)$/);
    if (image) { closeList(); output.push(`<figure class="note-figure"><img src="${image[2]}" alt="${esc(image[1])}" loading="lazy"><figcaption>${esc(image[1] || "筆記圖片")}</figcaption></figure>`); continue; }
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) { closeList(); output.push(`<h${Math.min(4, heading[1].length + 2)}>${noteInline(heading[2])}</h${Math.min(4, heading[1].length + 2)}>`); continue; }
    const bullet = line.match(/^[-*]\s+(.+)$/), numbered = line.match(/^\d+[.)]\s+(.+)$/);
    if (bullet || numbered) {
      const wanted = bullet ? "ul" : "ol";
      if (list !== wanted) { closeList(); output.push(`<${wanted}>`); list = wanted; }
      output.push(`<li>${noteInline((bullet || numbered)[1])}</li>`);
      continue;
    }
    closeList();
    output.push(line.startsWith("> ") ? `<blockquote>${noteInline(line.slice(2))}</blockquote>` : `<p>${noteInline(line)}</p>`);
  }
  closeList();
  return output.join("");
}
function showStatus(message, kind = "info") {
  const target = $("#app-status");
  target.textContent = message;
  target.className = `app-status ${kind}`;
  target.classList.toggle("hidden", !message);
}

async function api(path, options = {}) {
  let response;
  try { response = await fetch(path, { credentials: "same-origin", ...options }); }
  catch { throw new Error("無法連上本機題庫服務"); }
  let data;
  try { data = await response.json(); } catch { throw new Error("伺服器回應無法讀取"); }
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

async function post(path, payload) {
  return api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
}

function showApp(authenticated) {
  $("#setup").classList.add("hidden");
  $("#login").classList.toggle("hidden", authenticated);
  $("#app").classList.toggle("hidden", !authenticated);
  if (authenticated) $("#password").value = "";
}

function showSetup() {
  $("#setup").classList.remove("hidden");
  $("#login").classList.add("hidden");
  $("#app").classList.add("hidden");
  $("#setup-password").focus();
}

function setTab(name) {
  state.tab = name;
  showStatus("");
  document.querySelectorAll(".tabs button").forEach((button) => {
    const active = button.dataset.tab === name;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
  });
  document.querySelectorAll(".view").forEach((view) => view.classList.toggle("hidden", view.id !== `view-${name}`));
  if (name === "categories") renderCategories();
  if (name === "note-summary") loadNoteSummaries();
  if (name === "wrong") loadWrong();
  if (name === "history") loadHistory();
  if (name === "settings") loadAppSettings();
}

async function loadAppSettings() {
  try {
    const value = await api("/api/app-settings");
    $("#network-external").checked = value.network_mode === "external";
    $("#network-status").textContent = value.network_mode === "external"
      ? "目前允許外部裝置連線。" : "目前僅限這台電腦使用。";
  } catch (error) {
    $("#network-status").textContent = `讀取連線設定失敗：${error.message}`;
  }
}

async function saveAppSettings() {
  const button = $("#network-save");
  button.disabled = true;
  try {
    const value = await post("/api/app-settings", {network_mode: $("#network-external").checked ? "external" : "local"});
    $("#network-status").textContent = value.restart_required
      ? "設定已保存。請關閉並重新啟動題庫程式後生效。" : "設定已保存。";
  } catch (error) {
    $("#network-status").textContent = `儲存失敗：${error.message}`;
  } finally {
    button.disabled = false;
  }
}

async function refresh() {
  $("#subject-progress").innerHTML = loading("六科進度");
  try {
    const [summary, categories] = await Promise.all([api("/api/summary"), api("/api/categories")]);
    Object.assign(state, { summary, categories });
    renderToday();
    if (state.tab === "categories") renderCategories();
    if (state.tab === "note-summary") await loadNoteSummaries();
    if (state.tab === "wrong") await loadWrong();
    if (state.tab === "history") await loadHistory();
    showStatus("");
  } catch (error) {
    $("#subject-progress").innerHTML = inlineError(error);
    showStatus(`資料載入失敗：${error.message}`, "error");
    throw error;
  }
}

function moduleIdentity(item) {
  return [item.subject, item.topic, item.subtopic].join("\u001f");
}

async function openModule(item, start = false) {
  await post("/api/active-module", {subject:item.subject, topic:item.topic, subtopic:item.subtopic});
  await refresh();
  if (start) await startCurrentModule();
}

async function startCurrentModule() {
  try {
    const batch = await api("/api/module-batch");
    if (batch.ids.length) {
      await startQueue(batch.ids, "module", `${batch.module.topic} · ${batch.module.subtopic}`);
    } else if (batch.recommendation) {
      showStatus(`目前模塊已完成，已切換至推薦模塊：${batch.recommendation.topic} · ${batch.recommendation.subtopic}。`);
      await openModule(batch.recommendation, true);
    } else {
      showStatus("全部知識模塊都已完成。", "info");
    }
  } catch (error) { showStatus(`模塊載入失敗：${error.message}`, "error"); }
}

function bindModuleActions(root = document) {
  root.querySelectorAll(".module-select").forEach((button) => button.onclick = async () => {
    button.disabled = true;
    try { await openModule(button.dataset, false); }
    catch (error) { showStatus(`無法設定目前模塊：${error.message}`, "error"); }
    finally { button.disabled = false; }
  });
  root.querySelectorAll(".module-start").forEach((button) => button.onclick = async () => {
    button.disabled = true;
    try { await openModule(button.dataset, true); }
    catch (error) { showStatus(`模塊載入失敗：${error.message}`, "error"); }
    finally { button.disabled = false; }
  });
  root.querySelectorAll(".module-knowledge").forEach((button) => button.onclick = () => openKnowledgeNote(button));
}

function moduleCard(item) {
  const stateLabel = item.status === "completed" ? "已完成" : item.status === "in_progress" ? "進行中" : "未開始";
  const percent = item.total ? Math.round(item.completed / item.total * 100) : 0;
  const data = `data-subject="${esc(item.subject)}" data-topic="${esc(item.topic)}" data-subtopic="${esc(item.subtopic)}"`;
  return `<article class="module-card ${item.status} ${item.is_active ? "active" : ""}"><div class="module-card-head"><div><span class="module-state">${stateLabel}${item.is_active ? " · 目前模塊" : ""}</span><h4>${esc(item.subtopic)}</h4></div><strong>${percent}%</strong></div><div class="module-stats"><span>${item.completed}/${item.total} 題</span><span>近 ${item.recent_count || 0} 次 ${item.recent_count ? Math.round(item.recent_accuracy*100)+"%" : "—"}</span><span>${item.active_wrong} 題未熟練</span></div><div class="track"><span style="width:${percent}%"></span></div><div class="module-actions">${item.has_knowledge_note ? `<button class="secondary module-knowledge" ${data}>知識筆記</button>` : ""}${item.is_active ? `<button class="primary module-start" ${data}>${item.status === "completed" ? "查看完成狀態" : "繼續學習"}</button>` : `<button class="secondary module-select" ${data}>設為目前模塊</button>`}</div></article>`;
}

function renderToday() {
  const s = state.summary, active = s.active_module;
  const activeRow = state.categories.find((item) => item.is_active) || active;
  const percent = active.total ? Math.round(active.completed / active.total * 100) : 0;
  $("#today-date").textContent = "依六科、主題與子題自主選擇學習順序";
  $("#metric-completed").textContent = s.completed_modules;
  $("#metric-module-total").textContent = `／${s.module_total} 個知識模塊`;
  $("#metric-today").textContent = s.completed.toLocaleString();
  $("#metric-due").textContent = s.active_wrong;
  $("#metric-finish").textContent = `${percent}%`;
  $("#top-progress").textContent = `${s.completed_modules} / ${s.module_total} 模塊`;
  $("#due-preview").textContent = `${s.active_wrong} 題未熟練`;
  $("#new-preview").textContent = `${active.completed} / ${active.total} 題`;
  $("#today-subject").textContent = `${shortSubject(active.subject)} · ${active.topic} / ${active.subtopic}${active.completed >= active.total ? " · 已完成，按下後前往推薦模塊" : ""}`;
  $("#start-review").disabled = !s.active_wrong;
  $("#start-new").disabled = false;
  $("#start-new").innerHTML = active.completed >= active.total ? `前往推薦模塊 <span aria-hidden="true">→</span>` : `繼續學習（最多 20 題） <span aria-hidden="true">→</span>`;
  $("#backlog-note").textContent = `${s.completed_modules} / ${s.module_total} 個模塊完成`;
  $("#subject-progress").innerHTML = s.subjects.map((item) => `<article class="subject-row"><h3>${esc(shortSubject(item.subject))}</h3><span class="count">${item.completed_modules} / ${item.total_modules} 模塊 · ${item.completed_questions} / ${item.total_questions} 題</span><div class="track" role="progressbar" aria-label="${esc(shortSubject(item.subject))}完成進度" aria-valuenow="${item.completed_modules}" aria-valuemin="0" aria-valuemax="${item.total_modules}"><span style="width:${item.total_modules ? item.completed_modules/item.total_modules*100 : 0}%"></span></div></article>`).join("");
  const subjects = new Map();
  state.categories.forEach((item) => {
    if (!subjects.has(item.subject)) subjects.set(item.subject, new Map());
    const topics = subjects.get(item.subject);
    if (!topics.has(item.topic)) topics.set(item.topic, []);
    topics.get(item.topic).push(item);
  });
  $("#calendar").innerHTML = [...subjects].map(([subject, topics]) => `<details class="module-subject" ${[...topics.values()].flat().some((item)=>item.is_active) ? "open" : ""}><summary><strong>${esc(shortSubject(subject))}</strong><span>${[...topics.values()].flat().filter((item)=>item.status==="completed").length} / ${[...topics.values()].flat().length} 模塊</span></summary><div class="module-topics">${[...topics].map(([topic, items]) => `<details class="module-topic" ${items.some((item)=>item.is_active) ? "open" : ""}><summary><strong>${esc(topic)}</strong><span>${items.filter((item)=>item.status==="completed").length} / ${items.length}</span></summary><div class="module-grid">${items.map(moduleCard).join("")}</div></details>`).join("")}</div></details>`).join("");
  bindModuleActions($("#calendar"));
}

function setSubjectOptions() {
  const selected = $("#filter-subject").value;
  const subjects = [...new Set(state.categories.map((item) => item.subject))];
  $("#filter-subject").innerHTML = `<option value="">全部科目</option>${subjects.map((item) => `<option value="${esc(item)}">${esc(shortSubject(item))}</option>`).join("")}`;
  $("#filter-subject").value = subjects.includes(selected) ? selected : "";
  setTopicOptions();
}

function setTopicOptions() {
  const subject = $("#filter-subject").value;
  const selected = $("#filter-topic").value;
  const topics = [...new Set(state.categories.filter((item) => !subject || item.subject === subject).map((item) => item.topic))];
  $("#filter-topic").innerHTML = `<option value="">全部主題</option>${topics.map((item) => `<option value="${esc(item)}">${esc(item)}</option>`).join("")}`;
  $("#filter-topic").value = topics.includes(selected) ? selected : "";
}

function renderCategories() {
  if (!state.summary) { $("#category-list").innerHTML = loading("分類題庫"); return; }
  setSubjectOptions();
  const subject = $("#filter-subject").value, topic = $("#filter-topic").value;
  let rows = state.categories.filter((item) => (!subject || item.subject === subject) && (!topic || item.topic === topic));
  const status = $("#filter-status").value;
  rows = rows.filter((item) => status === "all" || (status === "unseen" && item.completed < item.total) || (status === "wrong" && item.ever_wrong > 0) || (status === "active_wrong" && item.active_wrong > 0));
  const sum = rows.reduce((acc,item)=>({total:acc.total+item.total,completed:acc.completed+item.completed,wrong:acc.wrong+item.active_wrong}),{total:0,completed:0,wrong:0});
  $("#category-summary").textContent = `${rows.length} 個子題 · ${sum.completed} / ${sum.total} 題已做 · ${sum.wrong} 題未熟練`;
  $("#category-list").innerHTML = rows.map((item) => {
    const data = `data-subject="${esc(item.subject)}" data-topic="${esc(item.topic)}" data-subtopic="${esc(item.subtopic)}"`;
    return `<div class="category-row"><div class="name"><span class="topic">${esc(shortSubject(item.subject))} · ${esc(item.topic)}${item.is_active ? " · 目前模塊" : ""}</span>${esc(item.subtopic)}</div><span class="stat"><b>${item.completed}/${item.total}</b>完成</span><span class="stat"><b>${item.ever_wrong}</b>曾答錯</span><span class="stat"><b>${item.recent_count ? Math.round(item.recent_accuracy*100)+"%" : "—"}</b>近 ${item.recent_count||0} 次正確率</span><span class="stat"><b>${item.active_wrong}</b>未熟練</span><div class="category-actions">${item.has_knowledge_note ? `<button class="secondary category-knowledge" ${data}>知識筆記</button>` : ""}<button class="secondary category-practice" ${data}>練習</button><button class="${item.is_active ? "primary" : "secondary"} module-select" ${data}>${item.is_active ? "目前模塊" : "設為目前模塊"}</button></div></div>`;
  }).join("") || empty("沒有符合的子題", "請調整科目、主題或狀態篩選。");
  document.querySelectorAll(".category-knowledge").forEach((button)=>button.onclick=()=>openKnowledgeNote(button));
  document.querySelectorAll(".category-practice").forEach((button)=>button.onclick=async()=>{
    button.disabled=true;
    try { const query=new URLSearchParams({subject:button.dataset.subject,topic:button.dataset.topic,subtopic:button.dataset.subtopic,status:$("#filter-status").value}); const cards=await api(`/api/list?${query}`); await startQueue(cards.map((item)=>item.id),"practice",`${button.dataset.topic} · ${button.dataset.subtopic}`); }
    catch(error){ showStatus(`題目載入失敗：${error.message}`,"error"); }
    finally{ button.disabled=false; }
  });
  bindModuleActions($("#category-list"));
}

function setNoteSummaryOptions() {
  const subjectSelect = $("#note-summary-subject"), topicSelect = $("#note-summary-topic"), subtopicSelect = $("#note-summary-subtopic");
  const oldSubject = subjectSelect.value, oldTopic = topicSelect.value, oldSubtopic = subtopicSelect.value;
  const subjects = [...new Set(state.noteSummaries.map((item) => item.subject))];
  subjectSelect.innerHTML = `<option value="">全部科目</option>${subjects.map((item) => `<option value="${esc(item)}">${esc(shortSubject(item))}</option>`).join("")}`;
  subjectSelect.value = subjects.includes(oldSubject) ? oldSubject : "";
  const topics = [...new Set(state.noteSummaries.filter((item) => !subjectSelect.value || item.subject === subjectSelect.value).map((item) => item.topic))];
  topicSelect.innerHTML = `<option value="">全部主題</option>${topics.map((item) => `<option value="${esc(item)}">${esc(item)}</option>`).join("")}`;
  topicSelect.value = topics.includes(oldTopic) ? oldTopic : "";
  const subtopics = [...new Set(state.noteSummaries.filter((item) => (!subjectSelect.value || item.subject === subjectSelect.value) && (!topicSelect.value || item.topic === topicSelect.value)).map((item) => item.subtopic))];
  subtopicSelect.innerHTML = `<option value="">全部子題</option>${subtopics.map((item) => `<option value="${esc(item)}">${esc(item)}</option>`).join("")}`;
  subtopicSelect.value = subtopics.includes(oldSubtopic) ? oldSubtopic : "";
}

function noteSummaryEntry(item) {
  const selected = item.selections.length ? item.selections.join("") : "未選";
  const status = item.is_credit ? "全給分" : item.is_correct ? "答對" : "答錯";
  const statusClass = item.is_credit ? "status-credit" : item.is_correct ? "status-good" : "status-bad";
  const date = item.answered_at.slice(0,16).replace("T", " ");
  return `<article class="note-summary-entry" data-question-id="${esc(item.question_id)}">
    <div class="note-summary-head"><div><button type="button" class="note-summary-open text-button">${item.year} 年第 ${item.sitting} 次 · 第 ${item.number} 題</button><div class="meta">作答日期 ${esc(date)} · 當時選擇 ${esc(selected)} · 正確答案 ${esc(item.answer_raw)}</div></div><span class="${statusClass}">${status}</span></div>
    <p class="note-summary-stem">${esc(item.stem)}</p>
    <div class="note-summary-note note-markdown">${renderNoteMarkdown(item.note)}</div>
    <div class="note-summary-actions"><button type="button" class="secondary note-summary-edit">修改筆記</button></div>
    <div class="note-summary-editor hidden"><label>目前的個人筆記<textarea maxlength="50000">${esc(item.note)}</textarea></label><p class="note-help">支援 Markdown 表格；清空並儲存會讓本題離開此總結頁。</p><div class="note-summary-editor-actions"><button type="button" class="primary note-summary-save">儲存筆記</button><button type="button" class="secondary note-summary-cancel">取消</button><span role="status" aria-live="polite"></span></div><div class="note-preview note-summary-preview hidden"></div></div>
  </article>`;
}

function renderNoteSummaries() {
  const subject = $("#note-summary-subject").value, topic = $("#note-summary-topic").value, subtopic = $("#note-summary-subtopic").value;
  const search = $("#note-summary-search").value.trim().toLocaleLowerCase("zh-Hant");
  const rows = state.noteSummaries.filter((item) => {
    if (subject && item.subject !== subject) return false;
    if (topic && item.topic !== topic) return false;
    if (subtopic && item.subtopic !== subtopic) return false;
    if (!search) return true;
    return [item.question_id, item.subject, item.topic, item.subtopic, item.stem, item.note, `${item.year}-${item.sitting}-${item.number}`].some((value) => String(value || "").toLocaleLowerCase("zh-Hant").includes(search));
  });
  $("#note-summary-count").textContent = `${rows.length} 題目前有個人筆記`;
  if (!rows.length) {
    $("#note-summary-list").innerHTML = empty("沒有符合的個人筆記", state.noteSummaries.length ? "請調整搜尋文字或分類篩選。" : "作答時填寫的錯題原因或個人筆記會整理在這裡。");
    return;
  }
  const subjects = new Map();
  rows.forEach((item) => {
    if (!subjects.has(item.subject)) subjects.set(item.subject, new Map());
    const topics = subjects.get(item.subject);
    if (!topics.has(item.topic)) topics.set(item.topic, new Map());
    const subtopics = topics.get(item.topic);
    if (!subtopics.has(item.subtopic)) subtopics.set(item.subtopic, []);
    subtopics.get(item.subtopic).push(item);
  });
  $("#note-summary-list").innerHTML = [...subjects].map(([subjectName, topics]) => {
    const subjectCount = [...topics.values()].reduce((sum, subtopics) => sum + [...subtopics.values()].reduce((subtotal, items) => subtotal + items.length, 0), 0);
    return `<section class="note-subject-group"><h2>${esc(shortSubject(subjectName))}<span>${subjectCount} 題</span></h2>${[...topics].map(([topicName, subtopics]) => `<section class="note-topic-group"><h3>${esc(topicName)}</h3>${[...subtopics].map(([subtopicName, items]) => `<section class="note-subtopic-group"><h4>${esc(subtopicName)}<span>${items.length} 題</span></h4><div class="note-summary-entries">${items.map(noteSummaryEntry).join("")}</div></section>`).join("")}</section>`).join("")}</section>`;
  }).join("");
}

async function loadNoteSummaries() {
  $("#note-summary-list").innerHTML = loading("筆記總結");
  try {
    state.noteSummaries = await api("/api/note-summaries");
    setNoteSummaryOptions();
    renderNoteSummaries();
  } catch (error) {
    $("#note-summary-list").innerHTML = inlineError(error);
    $("#note-summary-count").textContent = "";
  }
}

function updateNoteSummaryPreview(entry) {
  const textarea = entry.querySelector("textarea"), preview = entry.querySelector(".note-summary-preview");
  preview.classList.toggle("hidden", !textarea.value.trim());
  preview.innerHTML = textarea.value.trim() ? `<div class="note-preview-label">筆記預覽</div><div class="note-markdown">${renderNoteMarkdown(textarea.value)}</div>` : "";
}

$("#note-summary-list").addEventListener("click", async (event) => {
  const entry = event.target.closest(".note-summary-entry");
  if (!entry) return;
  if (event.target.closest(".note-summary-open")) {
    await startQueue([entry.dataset.questionId], "practice", "筆記總結 · 原題");
    return;
  }
  const editor = entry.querySelector(".note-summary-editor"), actions = entry.querySelector(".note-summary-actions"), textarea = entry.querySelector("textarea");
  if (event.target.closest(".note-summary-edit")) {
    editor.classList.remove("hidden"); actions.classList.add("hidden"); updateNoteSummaryPreview(entry); textarea.focus();
  } else if (event.target.closest(".note-summary-cancel")) {
    const item = state.noteSummaries.find((row) => row.question_id === entry.dataset.questionId);
    textarea.value = item?.note || ""; editor.classList.add("hidden"); actions.classList.remove("hidden");
  } else if (event.target.closest(".note-summary-save")) {
    const button = event.target.closest(".note-summary-save"), status = entry.querySelector('[role="status"]');
    button.disabled = true; status.textContent = "正在儲存…";
    try {
      await post("/api/note", {question_id:entry.dataset.questionId, note:textarea.value});
      const item = state.noteSummaries.find((row) => row.question_id === entry.dataset.questionId);
      if (item) item.note = textarea.value;
      if (!textarea.value.trim()) state.noteSummaries = state.noteSummaries.filter((row) => row.question_id !== entry.dataset.questionId);
      setNoteSummaryOptions(); renderNoteSummaries(); showStatus("個人筆記已儲存。", "info");
    } catch (error) { status.textContent = `儲存失敗：${error.message}`; button.disabled = false; }
  }
});

$("#note-summary-list").addEventListener("input", (event) => {
  const entry = event.target.closest(".note-summary-entry");
  if (entry && event.target.matches("textarea")) updateNoteSummaryPreview(entry);
});

function captureCardSnapshot() {
  return {
    queue:[...state.queue], queueIndex:state.queueIndex, mode:state.mode, queueLabel:state.queueLabel,
    cardId:state.card?.id, selections:[...state.selections], submitted:state.submitted,
    attemptId:state.attemptId, noteSaved:state.noteSaved, noteValue:$("#card-note")?.value ?? "",
    resultHtml:$("#card-result")?.innerHTML ?? "", noteStatus:$("#card-note-status")?.textContent ?? "",
    submitHidden:$("#card-submit").classList.contains("hidden"), nextHidden:$("#card-next").classList.contains("hidden"),
    nextText:$("#card-next").textContent, message:$("#card-message").textContent
  };
}

async function restoreCardSnapshot(snapshot) {
  state.queue = [...snapshot.queue]; state.queueIndex = snapshot.queueIndex; state.mode = snapshot.mode; state.queueLabel = snapshot.queueLabel;
  await showCard();
  state.selections = new Set(snapshot.selections); state.submitted = snapshot.submitted;
  state.attemptId = snapshot.attemptId; state.noteSaved = snapshot.noteSaved;
  document.querySelectorAll(".choice").forEach((choice) => {
    const active = state.selections.has(choice.dataset.choice);
    choice.classList.toggle("selected", active);
    choice.setAttribute("aria-pressed", String(active));
    choice.disabled = snapshot.submitted;
  });
  $("#card-note").value = snapshot.noteValue;
  $("#card-note-save").disabled = snapshot.noteValue === snapshot.noteSaved;
  $("#card-note-status").textContent = snapshot.noteStatus;
  renderCardNotePreview();
  $("#card-result").innerHTML = snapshot.resultHtml;
  $("#card-submit").classList.toggle("hidden", snapshot.submitHidden);
  $("#card-next").classList.toggle("hidden", snapshot.nextHidden);
  $("#card-next").textContent = snapshot.nextText;
  $("#card-message").textContent = snapshot.message;
}

async function openKnowledgeNote(button, fromCard = false) {
  const params = {subject:button.dataset.subject, topic:button.dataset.topic, subtopic:button.dataset.subtopic};
  state.knowledgeFocus = document.activeElement;
  state.knowledgeCardReturn = fromCard;
  state.knowledgeCardSnapshot = fromCard ? captureCardSnapshot() : null;
  button.disabled = true;
  try {
    const query = new URLSearchParams(params);
    const note = await api(`/api/knowledge-note?${query}`);
    state.knowledge = note;
    state.knowledgeOriginal = note.markdown;
    $("#knowledge-topic").textContent = `${shortSubject(note.subject)} · ${note.topic}`;
    $("#knowledge-title").textContent = note.subtopic;
    $("#knowledge-view").innerHTML = renderKnowledgeMarkdown(note.markdown);
    $("#knowledge-text").value = note.markdown;
    $("#knowledge-status").textContent = note.customized ? "目前顯示你保存的版本。" : "目前顯示匯入的原始筆記。";
    setKnowledgeEditing(false);
    $("#knowledge-overlay").classList.remove("hidden");
    $("#card-overlay").inert = fromCard;
    $("#app").inert = true;
    document.body.style.overflow = "hidden";
    $("#knowledge-close").textContent = fromCard ? "← 返回題目" : "← 返回分類";
    $("#knowledge-close").focus();
  } catch (error) {
    const row = button.closest(".category-row");
    if (row) {
      row.querySelector(".inline-error")?.remove();
      row.insertAdjacentHTML("beforeend", inlineError(error));
    } else {
      $("#card-message").textContent = `筆記載入失敗：${error.message}`;
    }
    state.knowledgeCardReturn = false;
    state.knowledgeCardSnapshot = null;
  } finally { button.disabled = false; }
}

function setKnowledgeEditing(editing) {
  $("#knowledge-view").classList.toggle("hidden", editing);
  $("#knowledge-editor").classList.toggle("hidden", !editing);
  $("#knowledge-save").classList.toggle("hidden", !editing);
  $("#knowledge-cancel").classList.toggle("hidden", !editing);
  $("#knowledge-edit").classList.toggle("hidden", editing);
  if (editing) $("#knowledge-text").focus();
}

async function closeKnowledgeNote(force = false) {
  if (!force && !$("#knowledge-editor").classList.contains("hidden") && $("#knowledge-text").value !== state.knowledgeOriginal) {
    $("#knowledge-status").textContent = "內容尚未儲存；請先儲存或取消修改。";
    $("#knowledge-save").focus();
    return;
  }
  $("#knowledge-overlay").classList.add("hidden");
  const returnToCard = state.knowledgeCardReturn && state.knowledgeCardSnapshot;
  if (returnToCard) {
    const snapshot = state.knowledgeCardSnapshot;
    $("#card-overlay").classList.remove("hidden");
    $("#card-overlay").inert = false;
    if (state.card?.id !== snapshot.cardId) {
      try { await restoreCardSnapshot(snapshot); }
      catch (error) { $("#card-message").textContent = `返回原題失敗：${error.message}`; }
    }
    $("#app").inert = true;
    document.body.style.overflow = "hidden";
    $("#card-knowledge")?.focus();
  } else {
    $("#card-overlay").inert = false;
    $("#app").inert = false;
    document.body.style.overflow = "";
    state.knowledgeFocus?.focus?.();
  }
  state.knowledge = null;
  state.knowledgeQuestionReturn = false;
  state.knowledgeCardReturn = false;
  state.knowledgeCardSnapshot = null;
}

async function saveKnowledgeNote() {
  if (!state.knowledge) return;
  const button = $("#knowledge-save");
  button.disabled = true;
  $("#knowledge-status").textContent = "正在儲存筆記…";
  try {
    const note = await post("/api/knowledge-note", {
      subject:state.knowledge.subject, topic:state.knowledge.topic, subtopic:state.knowledge.subtopic,
      markdown:$("#knowledge-text").value
    });
    state.knowledge = note;
    state.knowledgeOriginal = note.markdown;
    $("#knowledge-view").innerHTML = renderKnowledgeMarkdown(note.markdown);
    $("#knowledge-status").textContent = "個人修改已儲存。";
    setKnowledgeEditing(false);
  } catch (error) { $("#knowledge-status").textContent = `儲存失敗：${error.message}`; }
  finally { button.disabled = false; }
}

async function openKnowledgeQuestion(questionId) {
  if (!state.knowledge || !questionId) return;
  state.knowledgeQuestionReturn = true;
  $("#knowledge-overlay").classList.add("hidden");
  $("#knowledge-overlay").inert = true;
  $("#card-overlay").inert = false;
  await startQueue([questionId], "practice", `${state.knowledge.subtopic} · 原題`);
}

async function setMastered(questionId, button) {
  button.disabled = true;
  try {
    await post("/api/review-mastered", {question_id:questionId, mastered:true});
    showStatus("已標記為熟練；若日後再次答錯，會自動回到未熟練清單。", "info");
    await refresh();
  } catch (error) { showStatus(`更新失敗：${error.message}`, "error"); button.disabled = false; }
}

async function loadWrong() {
  $("#wrong-list").innerHTML = loading("錯題紀錄");
  try {
    const [active, wrong] = await Promise.all([api("/api/list?status=active_wrong"), api("/api/list?status=wrong")]);
    $("#wrong-due-count").textContent = `${active.length} 題未熟練；每次開啟網站都會提醒。`;
    $("#wrong-total-count").textContent = `${wrong.length} 題曾答錯，可隨時再次練習。`;
    $("#wrong-start-due").disabled = !active.length;
    $("#wrong-start-all").disabled = !wrong.length;
    $("#wrong-start-due").onclick = () => startQueue(active.map((item)=>item.id), "review", "未熟練錯題");
    $("#wrong-start-all").onclick = () => startQueue(wrong.map((item)=>item.id), "review", "曾答錯題");
    $("#wrong-list").innerHTML = active.map((item) => `<div class="list-row"><div><strong>${esc(item.topic)} · ${esc(item.subtopic)}</strong><div class="meta">${esc(shortSubject(item.subject))} · ${item.year} 年第 ${item.sitting} 次第 ${item.number} 題</div></div><div class="row-actions"><button class="secondary wrong-open" data-id="${esc(item.id)}">重做</button><button class="secondary wrong-master" data-id="${esc(item.id)}">標記已熟練</button></div></div>`).join("") || empty("目前沒有未熟練錯題", "答錯的題目會立即出現在這裡。");
    document.querySelectorAll(".wrong-open").forEach((button)=>button.onclick=()=>startQueue([button.dataset.id],"review","錯題重做"));
    document.querySelectorAll(".wrong-master").forEach((button)=>button.onclick=()=>setMastered(button.dataset.id,button));
  } catch (error) {
    $("#wrong-list").innerHTML = inlineError(error);
    $("#wrong-start-due").disabled = true; $("#wrong-start-all").disabled = true;
  }
}

async function loadHistory() {
  $("#history-list").innerHTML = loading("作答歷史");
  try {
    const attempts = await api("/api/history");
    $("#history-list").innerHTML = attempts.slice(0,100).map((item) => `<article class="history-entry"><div class="history-head"><div class="history-open" role="button" tabindex="0" title="點擊開啟此題" data-id="${esc(item.question_id)}"><strong>${esc(item.topic)} · ${esc(item.subtopic)}</strong><div class="meta">${esc(shortSubject(item.subject))} · ${item.year} 年第 ${item.sitting} 次第 ${item.number} 題 · ${esc(item.answered_at.slice(0,16).replace("T"," "))}</div><div class="meta">作答 ${esc(item.selections.join(""))} · 官方答案 ${esc(item.answer_raw)}</div></div><span class="${item.is_credit ? "status-credit" : item.is_correct ? "status-good" : "status-bad"}">${item.is_credit ? "全給分" : item.is_correct ? "答對" : "答錯"}</span></div>${item.note ? `<div class="history-note note-markdown"><div class="history-note-label">當次個人筆記</div>${renderNoteMarkdown(item.note)}</div>` : ""}</article>`).join("") || empty("尚無作答紀錄", "開始模塊題卡後，每次答案與筆記會完整保留在這裡。");
    document.querySelectorAll(".history-open").forEach((el) => {
      const open = () => startQueue([el.dataset.id], "review", "作答歷史");
      el.onclick = open;
      el.onkeydown = (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); } };
    });
  } catch (error) { $("#history-list").innerHTML = inlineError(error); }
}

async function startQueue(ids, mode, label) {
  if (!ids.length) { showStatus("目前沒有可作答題目，請選擇其他模塊或調整篩選。"); return; }
  state.queue = ids; state.queueIndex = 0; state.mode = mode; state.queueLabel = label; state.card = null;
  state.previousFocus = document.activeElement;
  $("#card-overlay").classList.remove("hidden");
  $("#app").inert = true;
  document.body.style.overflow = "hidden";
  $("#card-close").focus();
  try { await showCard(); }
  catch (error) { await closeCard(false, false); showStatus(`題卡載入失敗：${error.message}`, "error"); }
}

async function closeCard(reload = true, save = true) {
  if (save && !(await saveCardNote())) return;
  $("#card-overlay").classList.add("hidden");
  if (state.knowledgeQuestionReturn && state.knowledge) {
    state.knowledgeQuestionReturn = false;
    $("#card-overlay").inert = true;
    $("#knowledge-overlay").inert = false;
    $("#knowledge-overlay").classList.remove("hidden");
    $("#app").inert = true;
    document.body.style.overflow = "hidden";
    $("#knowledge-close").focus();
  } else {
    $("#app").inert = false;
    document.body.style.overflow = "";
    state.previousFocus?.focus?.();
  }
  if (reload) refresh().catch(() => {});
}

function renderCardNotePreview() {
  const preview = $("#card-note-preview"), field = $("#card-note");
  if (!preview || !field) return;
  const hasContent = Boolean(field.value.trim());
  preview.classList.toggle("hidden", !hasContent);
  preview.innerHTML = hasContent ? `<div class="note-preview-label">筆記預覽</div><div class="note-markdown">${renderNoteMarkdown(field.value)}</div>` : "";
}

function renderNoteAttachments() {
  const target = $("#card-note-attachments");
  if (!target) return;
  target.innerHTML = state.noteAttachments.map((item) => `<figure class="note-attachment"><img src="${esc(item.url)}" alt="${esc(item.name)}" loading="lazy"><figcaption><span>${esc(item.name)} · ${(item.size_bytes / 1024).toFixed(0)} KB</span><button type="button" class="text-button note-image-delete" data-id="${item.id}">刪除</button></figcaption></figure>`).join("");
  target.classList.toggle("hidden", !state.noteAttachments.length);
  target.querySelectorAll(".note-image-delete").forEach((button) => {
    button.onclick = async () => {
      if (button.dataset.confirm !== "yes") {
        button.dataset.confirm = "yes";
        button.textContent = "再次按下永久刪除";
        $("#card-note-status").textContent = "刪除圖片也會使舊作答歷史中的同一圖片無法顯示。";
        return;
      }
      button.disabled = true;
      try {
        await post("/api/note-image/delete", {attachment_id:button.dataset.id, confirm:"DELETE"});
        state.noteAttachments = state.noteAttachments.filter((item) => item.id !== button.dataset.id);
        const field = $("#card-note");
        field.value = field.value.replace(new RegExp(`!?\\[[^\\]]*\\]\\(\\/api\\/note-image\\/${button.dataset.id}\\)\\n?`, "g"), "");
        field.dispatchEvent(new Event("input"));
        renderNoteAttachments();
        await saveCardNote();
        $("#card-note-status").textContent = "圖片與筆記中的連結已刪除。";
      } catch (error) {
        button.disabled = false;
        button.dataset.confirm = "";
        button.textContent = "刪除";
        $("#card-note-status").textContent = `圖片刪除失敗：${error.message}`;
      }
    };
  });
}

function fileBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.onerror = () => reject(new Error("無法讀取圖片"));
    reader.readAsDataURL(file);
  });
}

function insertNoteText(value) {
  const field = $("#card-note");
  const start = field.selectionStart, end = field.selectionEnd;
  const prefix = start > 0 && field.value[start - 1] !== "\n" ? "\n" : "";
  const suffix = end < field.value.length && field.value[end] !== "\n" ? "\n" : "";
  field.setRangeText(`${prefix}${value}\n${suffix}`, start, end, "end");
  field.dispatchEvent(new Event("input"));
  field.focus();
}

async function uploadNoteImages(files) {
  if (!state.card || state.noteUploading) return;
  const accepted = ["image/jpeg", "image/png", "image/webp"];
  const images = [...files].filter((file) => accepted.includes(file.type));
  if (!images.length) { $("#card-note-status").textContent = "只接受 JPG、PNG 或 WebP 圖片。"; return; }
  if (state.noteAttachments.length + images.length > 20) { $("#card-note-status").textContent = "每題最多保存 20 張圖片。"; return; }
  state.noteUploading = true;
  $("#card-note-image-button").classList.add("disabled");
  try {
    for (const file of images) {
      if (file.size > 5 * 1024 * 1024) throw new Error(`${file.name} 超過 5 MB`);
      $("#card-note-status").textContent = `正在上傳 ${file.name}…`;
      const item = await post("/api/note-image", {question_id:state.card.id, name:file.name, mime_type:file.type, data_base64:await fileBase64(file)});
      state.noteAttachments.push(item);
      insertNoteText(item.markdown);
      renderNoteAttachments();
    }
    $("#card-note-status").textContent = "圖片已保存於本機；請儲存筆記以保留插入位置。";
  } catch (error) { $("#card-note-status").textContent = `圖片上傳失敗：${error.message}`; }
  finally {
    state.noteUploading = false;
    $("#card-note-image-button").classList.remove("disabled");
    $("#card-note-image").value = "";
  }
}

async function showCard() {
  const id = state.queue[state.queueIndex];
  if (!id) { closeCard(); return; }
  state.card = null;
  $("#card-content").innerHTML = loading("題卡與原卷影像");
  $("#card-submit").disabled = true;
  $("#card-message").textContent = "";
  const card = await api(`/api/question/${encodeURIComponent(id)}`);
  state.card = card; state.selections = new Set(); state.submitted = false;
  state.attemptId = null; state.noteSaved = card.note || ""; state.noteSavePromise = null; state.noteAttachments = card.note_attachments || [];
  $("#card-progress").textContent = `${state.queueIndex + 1} / ${state.queue.length}`;
  $("#card-mode").textContent = state.queueLabel;
  $("#card-submit").classList.remove("hidden");
  $("#card-submit").disabled = false;
  $("#card-next").classList.add("hidden");
  const urls = card.image_urls || (card.image_url ? [card.image_url] : []);
  const image = urls.map((url, index) => `<figure class="source-page"><figcaption>原卷影像 ${index + 1} · 點選可放大</figcaption><a href="${esc(url)}" target="_blank" rel="noopener"><img class="source-image" src="${esc(url)}" alt="原始試卷影像 ${index + 1}；包含本題圖表或原文" loading="lazy"></a></figure>`).join("");
  const warning = card.parse_warning ? `<div class="source-note">此題 PDF 的文字或版面較特殊，請以原卷影像為準；文字僅供輔助閱讀。</div>` : card.needs_image ? `<div class="source-note">本題包含圖表或影像，請搭配原卷影像作答。</div>` : "";
  const knowledgeButton = card.has_knowledge_note ? `<button id="card-knowledge" type="button" class="secondary card-knowledge" data-subject="${esc(card.subject)}" data-topic="${esc(card.topic)}" data-subtopic="${esc(card.subtopic)}">知識筆記</button>` : "";
  const sourceBody = `${warning}${image || `<p class="source-empty">此題可開啟官方原卷頁面核對。</p>`}<a class="source-link" href="${esc(card.source_url)}" target="_blank" rel="noopener">開啟官方原卷第 ${card.source_page} 頁 ↗</a>`;
  const sourcePanel = urls.length
    ? `<details class="source-column source-collapsible" aria-label="原始試卷影像"><summary><span><strong>原卷對照</strong><small>${urls.length} 頁圖片</small></span><span class="source-toggle"><span class="source-closed">展開原卷</span><span class="source-open">收合原卷</span><b aria-hidden="true">＋</b></span></summary><div class="source-collapsible-body">${sourceBody}</div></details>`
    : `<aside class="source-column" aria-label="原始試卷影像"><div class="source-head"><h3>原卷對照</h3><span>官方 PDF</span></div>${sourceBody}</aside>`;
  $("#card-content").innerHTML = `<div class="card-layout ${urls.length ? "" : "no-image"}"><div class="card-intro"><div class="card-heading-row"><div class="card-kicker">${esc(shortSubject(card.subject))} · ${esc(card.topic)} / ${esc(card.subtopic)}</div>${knowledgeButton}</div><h2>${card.year} 年第 ${card.sitting} 次 · 第 ${card.number} 題</h2><div class="q-stem">${esc(card.stem)}</div></div>${sourcePanel}<div class="answer-column"><div class="choices" role="group" aria-label="請選擇答案">${"ABCD".split("").map((letter) => `<button class="choice" data-choice="${letter}" aria-pressed="false"><span class="letter">${letter}</span><span>${esc(card.options[letter] || "請參照原卷影像")}</span></button>`).join("")}</div>${card.allows_multiple ? `<p class="muted">本題有官方更正答案，可選多個選項。</p>` : ""}<div id="card-result" aria-live="polite"></div><details id="card-supplement" class="question-supplement hidden"></details><label class="note-label" for="card-note">錯題原因或個人筆記（選填）</label><p class="note-help">支援 Markdown 表格；可直接貼上圖片，或從裝置選擇 JPG、PNG、WebP。</p><textarea id="card-note" class="note-box" maxlength="50000" placeholder="可輸入文字、Markdown 表格，或貼上圖片…">${esc(card.note)}</textarea><div class="note-tools"><label id="card-note-image-button" class="button secondary" for="card-note-image">上傳圖片</label><input id="card-note-image" class="visually-hidden" type="file" accept="image/jpeg,image/png,image/webp" multiple><span>每張最多 5 MB；每題最多 20 張</span></div><div id="card-note-attachments" class="note-attachments hidden"></div><div id="card-note-preview" class="note-preview hidden"></div><div class="note-actions"><button id="card-note-save" type="button" class="secondary" disabled>儲存筆記</button><span id="card-note-status" role="status" aria-live="polite"></span></div></div></div>`;
  document.querySelectorAll(".choice").forEach((button) => button.onclick = () => selectChoice(button.dataset.choice));
  $("#card-note").addEventListener("input", () => {
    const changed = $("#card-note").value !== state.noteSaved;
    $("#card-note-save").disabled = !changed;
    $("#card-note-status").textContent = changed ? "筆記尚未儲存；切換題目時會自動儲存。" : "";
    renderCardNotePreview();
  });
  $("#card-note").addEventListener("paste", (event) => {
    const images = [...(event.clipboardData?.items || [])].filter((item) => item.kind === "file" && item.type.startsWith("image/")).map((item) => item.getAsFile()).filter(Boolean);
    if (images.length) { event.preventDefault(); uploadNoteImages(images); }
  });
  $("#card-note-image").onchange = (event) => uploadNoteImages(event.target.files);
  $("#card-note-save").onclick = saveCardNote;
  if ($("#card-knowledge")) $("#card-knowledge").onclick = () => openKnowledgeNote($("#card-knowledge"), true);
  renderNoteAttachments();
  renderCardNotePreview();
  $("#card-content").scrollTop = 0;
}

async function saveCardNote() {
  const field = $("#card-note");
  if (!state.card || !field) return true;
  if (state.noteSavePromise) await state.noteSavePromise;
  const note = field.value;
  if (note === state.noteSaved) return true;
  const button = $("#card-note-save"), status = $("#card-note-status");
  button.disabled = true;
  status.textContent = "正在儲存筆記…";
  const pending = post("/api/note", { question_id: state.card.id, attempt_id: state.attemptId, note })
    .then(() => {
      state.noteSaved = note;
      state.card.note = note;
      status.textContent = field.value === note ? "筆記已儲存。" : "筆記仍有尚未儲存的修改。";
      return true;
    })
    .catch((error) => { status.textContent = `筆記儲存失敗：${error.message}`; return false; });
  state.noteSavePromise = pending;
  const saved = await pending;
  if (state.noteSavePromise === pending) state.noteSavePromise = null;
  button.disabled = field.value === state.noteSaved;
  return saved;
}

function selectChoice(letter) {
  if (state.submitted) return;
  if (state.card.allows_multiple) {
    if (state.selections.has(letter)) state.selections.delete(letter); else state.selections.add(letter);
  } else {
    state.selections = new Set([letter]);
  }
  document.querySelectorAll(".choice").forEach((button) => {
    const active = state.selections.has(button.dataset.choice);
    button.classList.toggle("selected", active);
    button.setAttribute("aria-pressed", String(active));
  });
  $("#card-message").textContent = "";
}

async function submitCard() {
  if (state.submitted) return;
  if (!state.selections.size) { $("#card-message").textContent = "請先選擇答案。"; document.querySelector(".choice")?.focus(); return; }
  const button = $("#card-submit"); button.disabled = true;
  const noteField = $("#card-note"), noteButton = $("#card-note-save");
  try {
    if (state.noteSavePromise) await state.noteSavePromise;
    const note = noteField.value;
    noteField.disabled = true;
    noteButton.disabled = true;
    const result = await post("/api/attempt", {
      client_id: uniqueId(), question_id: state.card.id,
      selections: [...state.selections], mode: state.mode, note
    });
    state.submitted = true;
    state.attemptId = result.attempt_id;
    state.noteSaved = note;
    document.querySelectorAll(".choice").forEach((choice) => { choice.disabled = true; });
    $("#card-note-status").textContent = "送出後仍可補寫錯題原因；修改後按儲存筆記。";
    const tone = result.correct ? "good" : "bad";
    const headline = result.credit ? "本題官方全給分" : result.correct ? "答對了" : "答錯了";
    $("#card-result").innerHTML = `<div class="result-box ${tone}"><strong>${headline}</strong><br>你的答案：${esc([...state.selections].sort().join(""))} · 官方最終答案：${esc(result.question.answer)}${result.question.answer_note ? `<br>更正註記：${esc(result.question.answer_note)}` : ""}${result.correct && result.active_wrong ? `<div class="result-actions"><button id="card-mastered" type="button" class="secondary">標記已熟練</button></div>` : ""}</div>`;
    if ($("#card-mastered")) $("#card-mastered").onclick = () => setMastered(state.card.id, $("#card-mastered"));
    const supplement = result.supplement;
    const supplementBox = $("#card-supplement");
    if (supplement && [supplement.background, supplement.steps, supplement.traps, supplement.extension].every(Boolean)) {
      const section = (title, value) => `<section><h3>${title}</h3><p>${esc(value).replace(/\n/g, "<br>")}</p></section>`;
      supplementBox.innerHTML = `<summary><span>本題知識補充</span><small><span class="supplement-closed">展開閱讀</span><span class="supplement-open">收合內容</span><b aria-hidden="true">＋</b></small></summary><div class="supplement-content">${section("背景知識補充", supplement.background)}${section("判讀步驟", supplement.steps)}${section("常見陷阱", supplement.traps)}${section("延伸知識", supplement.extension)}</div>`;
      supplementBox.open = false;
      supplementBox.classList.remove("hidden");
    }
    button.classList.add("hidden");
    $("#card-next").classList.remove("hidden");
    $("#card-next").textContent = state.queueIndex + 1 < state.queue.length ? "下一題 →" : "完成並返回";
    $("#card-next").focus();
  } catch (error) { $("#card-message").textContent = `送出失敗：${error.message}`; }
  finally { noteField.disabled = false; noteButton.disabled = noteField.value === state.noteSaved; button.disabled = false; }
}

async function restoreBackup() {
  const file = $("#restore-file").files[0];
  if (!file) { $("#restore-result").textContent = "請先選擇備份 JSON。"; $("#restore-file").focus(); return; }
  $("#restore-result").textContent = "";
  $("#restore-confirm").classList.remove("hidden");
  $("#restore-approve").focus();
}

async function approveRestore() {
  const file = $("#restore-file").files[0];
  if (!file) { $("#restore-confirm").classList.add("hidden"); $("#restore-result").textContent = "請先選擇備份 JSON。"; return; }
  const button = $("#restore-approve");
  button.disabled = true;
  $("#restore-result").textContent = "正在還原備份…";
  try {
    let result;
    if (file.name.toLowerCase().endsWith(".zip") || file.type === "application/zip") {
      const response = await fetch("/api/restore-full", {method:"POST", credentials:"same-origin", headers:{"Content-Type":"application/zip", "X-Restore-Confirm":"RESTORE"}, body:file});
      let data;
      try { data = await response.json(); } catch { throw new Error("伺服器回應無法讀取"); }
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      result = data;
    } else {
      const backup = JSON.parse(await file.text());
      result = await post("/api/restore", {confirm:"RESTORE",backup});
    }
    $("#restore-result").textContent = `已還原 ${result.attempts} 筆作答${Number.isInteger(result.attachments) ? `及 ${result.attachments} 張筆記圖片` : ""}。`;
    $("#restore-confirm").classList.add("hidden");
    await refresh();
  } catch (error) { $("#restore-result").textContent = `還原失敗：${error.message}`; }
  finally { button.disabled = false; }
}

async function boot() {
  try {
    const status = await api("/api/status");
    if (status.setup_required) { showSetup(); return; }
    showApp(status.authenticated);
    if (status.authenticated) await refresh().catch(() => {});
  } catch (error) { $("#login").classList.remove("hidden"); $("#login-error").textContent = error.message; }
}

$("#setup-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const password = $("#setup-password").value;
  $("#setup-error").textContent = "";
  if (password !== $("#setup-confirm").value) {
    $("#setup-error").textContent = "兩次輸入的密碼不同。";
    return;
  }
  try {
    await post("/api/setup", {password});
    $("#password").value = password;
    $("#setup-password").value = "";
    $("#setup-confirm").value = "";
    $("#setup").classList.add("hidden");
    $("#login").classList.remove("hidden");
    $("#login-form").requestSubmit();
  } catch (error) {
    $("#setup-error").textContent = error.message;
  }
});

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#login-error").textContent = "";
  try { await post("/api/login", {password: $("#password").value}); showApp(true); await refresh().catch(() => {}); }
  catch (error) { $("#login-error").textContent = error.message; }
});
$("#logout").onclick = async () => {
  try { await post("/api/logout", {}); showApp(false); $("#password").focus(); }
  catch (error) { showStatus(`登出失敗：${error.message}`, "error"); }
};
document.querySelectorAll(".tabs button").forEach((button) => button.onclick = () => setTab(button.dataset.tab));
$("#start-new").onclick = startCurrentModule;
$("#start-review").onclick = async () => { const rows = await api("/api/list?status=active_wrong"); startQueue(rows.map((item)=>item.id), "review", "未熟練錯題"); };
$("#filter-subject").onchange = () => { setTopicOptions(); renderCategories(); };
$("#filter-topic").onchange = renderCategories;
$("#filter-status").onchange = renderCategories;
$("#note-summary-search").oninput = renderNoteSummaries;
$("#note-summary-subject").onchange = () => { setNoteSummaryOptions(); renderNoteSummaries(); };
$("#note-summary-topic").onchange = () => { setNoteSummaryOptions(); renderNoteSummaries(); };
$("#note-summary-subtopic").onchange = renderNoteSummaries;
$("#card-close").onclick = () => closeCard();
$("#card-submit").onclick = submitCard;
$("#knowledge-close").onclick = () => closeKnowledgeNote();
$("#knowledge-edit").onclick = () => {
  $("#knowledge-text").value = state.knowledgeOriginal;
  $("#knowledge-status").textContent = "編輯後請按儲存筆記。";
  setKnowledgeEditing(true);
};
$("#knowledge-cancel").onclick = () => {
  $("#knowledge-text").value = state.knowledgeOriginal;
  $("#knowledge-status").textContent = state.knowledge?.customized ? "已取消修改；仍顯示你先前保存的版本。" : "已取消修改。";
  setKnowledgeEditing(false);
};
$("#knowledge-save").onclick = saveKnowledgeNote;
$("#knowledge-view").addEventListener("click", (event) => {
  const reference = event.target.closest("[data-question-id]");
  if (!reference) return;
  event.preventDefault();
  openKnowledgeQuestion(reference.dataset.questionId);
});
$("#card-next").onclick = async () => {
  if (!(await saveCardNote())) return;
  const previousIndex = state.queueIndex;
  state.queueIndex++;
  try {
    await showCard();
    if (!$("#card-overlay").classList.contains("hidden")) $("#card-close").focus();
  } catch (error) {
    state.queueIndex = previousIndex;
    $("#card-message").textContent = `下一題載入失敗：${error.message}`;
    $("#card-next").classList.remove("hidden");
  }
};
$("#restore-button").onclick = restoreBackup;
$("#restore-approve").onclick = approveRestore;
$("#restore-cancel").onclick = () => { $("#restore-confirm").classList.add("hidden"); $("#restore-result").textContent = "已取消還原。"; $("#restore-button").focus(); };
$("#restore-file").onchange = () => { $("#restore-confirm").classList.add("hidden"); $("#restore-result").textContent = ""; };
$("#network-save").onclick = saveAppSettings;
document.addEventListener("keydown", (event) => {
  if (!$("#knowledge-overlay").classList.contains("hidden")) {
    if (event.key === "Escape") { event.preventDefault(); closeKnowledgeNote(); return; }
    if (event.key === "Tab") {
      const focusable = [...$("#knowledge-overlay").querySelectorAll('button:not([disabled]):not(.hidden), a[href], textarea:not([disabled])')].filter((element) => element.getClientRects().length);
      if (!focusable.length) return;
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
    return;
  }
  if ($("#card-overlay").classList.contains("hidden")) return;
  if (event.key === "Escape") { event.preventDefault(); closeCard(); return; }
  if (event.key === "Tab") {
    const focusable = [...$("#card-overlay").querySelectorAll('button:not([disabled]):not(.hidden), a[href], textarea:not([disabled])')].filter((element) => element.getClientRects().length);
    if (!focusable.length) return;
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    return;
  }
  if (event.altKey || event.ctrlKey || event.metaKey || ["INPUT","TEXTAREA","SELECT"].includes(document.activeElement?.tagName)) return;
  const letter = event.key.toUpperCase();
  if ("ABCD".includes(letter) && letter.length === 1) selectChoice(letter);
});
boot();
