(() => {
  'use strict';
  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const kindLabels = {book: 'كتاب', note: 'ملزمة', summary: 'ملخص', exam: 'امتحان'};
  const kindIcons = {book: '▤', note: '✎', summary: '≡', exam: '✓'};
  const categories = {all: null, books: 'book', notes: 'note', summaries: 'summary', exams: 'exam'};
  const categoryHeadings = {all: 'كل الملفات', books: 'الكتب', notes: 'الملازم', summaries: 'الملخصات', exams: 'الامتحانات'};
  let subjects = [];
  let allFiles = [];
  let selectedCategory = 'all';
  let selectedSubject = null;
  let savedOnly = false;
  let searchTimer = null;
  let toastTimer = null;
  let toastTimeout = null;
  const savedKey = 'rabaiyoon.savedFiles.v1';
  const planKey = 'rabaiyoon.studyPlan.v1';

  function readSaved() {
    try { const v = JSON.parse(localStorage.getItem(savedKey) || '[]'); return Array.isArray(v) ? v.map(Number).filter(Number.isFinite) : []; }
    catch { return []; }
  }
  function writeSaved(ids) { localStorage.setItem(savedKey, JSON.stringify([...new Set(ids)])); updateSavedCount(); }
  function updateSavedCount() { $('#savedCount').textContent = String(readSaved().length); }
  function toast(message) {
    const item = $('#toast'); item.textContent = message; item.classList.add('show');
    clearTimeout(toastTimeout); toastTimeout = setTimeout(() => item.classList.remove('show'), 2600);
  }
  function escapeHTML(value) {
    return String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }
  async function api(path, options = {}) {
    const headers = {'Accept': 'application/json', ...(options.body ? {'Content-Type': 'application/json'} : {}), ...(options.headers || {})};
    const response = await fetch(path, {...options, headers});
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || body.message || `خطأ ${response.status}`);
    return body;
  }
  function updateStats(stats) {
    $('#statSubjects').textContent = String(stats.subjects ?? subjects.length);
    $('#statFiles').textContent = String(stats.files ?? allFiles.length);
    $('#statDownloads').textContent = compactNumber(stats.downloads ?? 0);
  }
  function compactNumber(number) {
    const n = Number(number) || 0;
    return n.toLocaleString('en-US');
  }
  function renderSubjects() {
    $('#subjectCount').textContent = String(subjects.length);
    $('#subjectList').innerHTML = [
      `<button type="button" class="subject-button ${selectedSubject === null ? 'active' : ''}" data-subject="all"><span class="subject-emoji">⌂</span><span>كل المواد</span></button>`,
      ...subjects.map(s => `<button type="button" class="subject-button ${selectedSubject === s.id ? 'active' : ''}" data-subject="${s.id}"><span class="subject-emoji">${escapeHTML(s.emoji || '📚')}</span><span>${escapeHTML(s.name)}</span></button>`)
    ].join('');
    ['#studySubject','#requestSubject'].forEach(selector => {
      const el = $(selector); const prior = el.value;
      el.innerHTML = `<option value="">اختار المادة</option>` + subjects.map(s => `<option value="${escapeHTML(s.name)}">${escapeHTML(s.name)}</option>`).join('');
      if (subjects.some(s => s.name === prior)) el.value = prior;
    });
  }
  function currentSearch() { return $('#searchInput').value.trim(); }
  function updateTitle() {
    if (savedOnly) return 'الملفات المحفوظة';
    const subject = subjects.find(s => s.id === selectedSubject);
    if (subject) return subject.name;
    return categoryHeadings[selectedCategory] || 'كل الملفات';
  }
  function cardHTML(file) {
    const saved = readSaved().includes(Number(file.id));
    const preview = file.has_preview ? `<button type="button" class="preview-btn" data-preview="${file.id}" data-title="${escapeHTML(file.title)}">معاينة</button>` : '';
    const description = file.description || (file.teacher ? `الأستاذ: ${file.teacher}` : 'استلام الملف عبر بوت تيليجرام');
    return `<article class="file-card">
      <div class="file-card-top"><span class="file-type-icon">${kindIcons[file.kind] || '▤'}</span><span class="file-kind">${kindLabels[file.kind] || 'ملف'}</span></div>
      <h4 class="file-title">${escapeHTML(file.title)}</h4>
      <div class="file-subject"><span>${escapeHTML(file.subject_emoji || '📚')}</span><span>${escapeHTML(file.subject || '')}</span>${file.teacher ? `<span>· ${escapeHTML(file.teacher)}</span>` : ''}</div>
      <p class="file-desc">${escapeHTML(description)}</p>
      <div class="file-meta"><span>${file.year ? `الإصدار ${escapeHTML(file.year)}` : 'الرابع العلمي'}</span><span>${compactNumber(file.downloads)} استلام</span></div>
      <div class="file-card-actions"><a class="download-btn" href="${escapeHTML(file.download_url)}" target="_blank" rel="noopener noreferrer">استلام عبر تيليجرام <span>↗</span></a>${preview}<button type="button" class="save-btn ${saved ? 'saved' : ''}" data-save="${file.id}" aria-label="${saved ? 'إزالة من المحفوظات' : 'حفظ الملف'}" title="${saved ? 'إزالة من المحفوظات' : 'حفظ الملف'}">${saved ? '♥' : '♡'}</button></div>
    </article>`;
  }
  function renderFiles(files) {
    $('#emptyState').querySelector('strong').textContent = currentSearch() ? 'ما لكينا نتائج' : 'ماكو ملفات مضافة حالياً';
    $('#emptyState').querySelector('p').textContent = currentSearch() ? 'جرّب كلمة بحث ثانية أو غيّر القسم والمادة.' : 'الملفات الحقيقية راح تظهر هنا بعد إضافتها من لوحة البوت.';
    $('#resultsTitle').textContent = updateTitle();
    $('#resultCount').textContent = `${files.length} ملف`;
    $('#fileGrid').innerHTML = files.map(cardHTML).join('');
    $('#fileGrid').hidden = files.length === 0;
    $('#emptyState').hidden = files.length !== 0;
    $('#searchStatus').textContent = currentSearch() ? `نتائج البحث عن: ${currentSearch()}` : 'اكتب كلمة حتى تبحث داخل المكتبة.';
    $('#apiNotice').hidden = true;
  }
  async function loadFiles({keepSearch = true} = {}) {
    const params = new URLSearchParams();
    if (selectedCategory !== 'all' && categories[selectedCategory]) params.set('category', selectedCategory);
    if (selectedSubject !== null) params.set('subject_id', String(selectedSubject));
    if (keepSearch && currentSearch()) params.set('q', currentSearch());
    params.set('limit', '100');
    try {
      const results = await api(`/api/files?${params.toString()}`);
      allFiles = results;
      const display = savedOnly ? results.filter(f => readSaved().includes(Number(f.id))) : results;
      renderFiles(display);
      updateStats({files: Number($('#statFiles').textContent.replace(/,/g,'')), subjects: subjects.length, downloads: Number($('#statDownloads').textContent.replace(/,/g,''))});
    } catch (error) {
      $('#apiNotice').hidden = false;
      $('#apiNotice').textContent = error.message || 'تعذر الاتصال بالخادم.';
      $('#fileGrid').hidden = true; $('#emptyState').hidden = false; $('#resultCount').textContent = 'غير متاح';
      $('#emptyState').querySelector('strong').textContent = 'المكتبة غير متصلة';
      $('#emptyState').querySelector('p').textContent = 'تحقق من تشغيل الخادم ثم أعد تحميل الصفحة.';
    }
  }
  async function loadList(path, selector, emptyText) {
    try {
      const items = await api(path);
      $(selector).innerHTML = items.length ? items.slice(0, 4).map(item => `
        <a class="compact-item" href="${escapeHTML(item.download_url)}" target="_blank" rel="noopener noreferrer">
          <span class="compact-icon">${kindIcons[item.kind] || '▤'}</span>
          <span class="compact-copy"><strong>${escapeHTML(item.title)}</strong><small>${escapeHTML(item.subject)} · ${kindLabels[item.kind] || 'ملف'}${item.teacher ? ' · ' + escapeHTML(item.teacher) : ''}</small></span><span class="compact-arrow">←</span>
        </a>`).join('') : `<p class="muted">${emptyText}</p>`;
    } catch { $(selector).innerHTML = `<p class="muted">تعذر تحميل القائمة حالياً.</p>`; }
  }
  async function reloadDiscoveries() {
    await Promise.all([loadList('/api/files?sort=latest&limit=4', '#latestFiles', 'ماكو إضافات حالياً.'), loadList('/api/files?sort=popular&limit=4', '#popularFiles', 'ماكو ملفات مستلمة بعد.')]);
  }
  function openPreview(fileId, title) {
    const modal = $('#previewModal'); $('#previewImage').src = `/api/files/${encodeURIComponent(fileId)}/preview`;
    $('#previewImage').alt = title || 'صورة معاينة الامتحان'; $('#previewCaption').textContent = title || '';
    modal.hidden = false; document.body.style.overflow = 'hidden'; $('#closePreview').focus();
  }
  function closePreview() { $('#previewModal').hidden = true; $('#previewImage').src = ''; document.body.style.overflow = ''; }

  // Dark mode persists on this device.
  try { if (localStorage.getItem('rabaiyoon.theme') === 'dark') document.body.classList.add('dark'); } catch {}
  $('#themeToggle').addEventListener('click', () => {
    document.body.classList.toggle('dark');
    try { localStorage.setItem('rabaiyoon.theme', document.body.classList.contains('dark') ? 'dark' : 'light'); } catch {}
  });

  $$('.category-tab').forEach(button => button.addEventListener('click', async () => {
    selectedCategory = button.dataset.category || 'all'; savedOnly = false;
    $$('.category-tab').forEach(b => { const active = b === button; b.classList.toggle('active', active); b.setAttribute('aria-selected', String(active)); });
    await loadFiles();
  }));
  $('#subjectList').addEventListener('click', async event => {
    const button = event.target.closest('[data-subject]'); if (!button) return;
    selectedSubject = button.dataset.subject === 'all' ? null : Number(button.dataset.subject); savedOnly = false;
    $$('#subjectList .subject-button').forEach(b => b.classList.toggle('active', b === button));
    await loadFiles();
  });
  $('#clearFilters').addEventListener('click', async () => {
    selectedCategory = 'all'; selectedSubject = null; savedOnly = false; $('#searchInput').value = '';
    $$('.category-tab').forEach(b => {const active = b.dataset.category === 'all'; b.classList.toggle('active', active); b.setAttribute('aria-selected', String(active));});
    renderSubjects(); await loadFiles({keepSearch: false});
  });
  $('#resetSearch').addEventListener('click', async () => {
    selectedCategory = 'all'; selectedSubject = null; savedOnly = false; $('#searchInput').value = '';
    $$('.category-tab').forEach(b => {const active = b.dataset.category === 'all'; b.classList.toggle('active', active); b.setAttribute('aria-selected', String(active));});
    renderSubjects(); await loadFiles({keepSearch: false});
  });
  $('#searchInput').addEventListener('input', () => {
    savedOnly = false; clearTimeout(searchTimer); searchTimer = setTimeout(() => loadFiles(), 240);
  });
  document.addEventListener('keydown', event => {
    const tag = event.target && event.target.tagName;
    if (event.key === '/' && !event.ctrlKey && !event.metaKey && !['INPUT','TEXTAREA','SELECT'].includes(tag)) {event.preventDefault(); $('#searchInput').focus();}
    if (event.key === 'Escape' && !$('#previewModal').hidden) closePreview();
  });
  $('#showSaved').addEventListener('click', async () => { savedOnly = true; selectedCategory = 'all'; selectedSubject = null; await loadFiles({keepSearch: false}); renderSubjects(); $$('.category-tab').forEach(b => {const active = b.dataset.category === 'all'; b.classList.toggle('active', active); b.setAttribute('aria-selected', String(active));}); });
  $('#fileGrid').addEventListener('click', async event => {
    const saveButton = event.target.closest('[data-save]');
    if (saveButton) {
      const id = Number(saveButton.dataset.save); let saved = readSaved();
      saved = saved.includes(id) ? saved.filter(v => v !== id) : [...saved, id]; writeSaved(saved);
      if (savedOnly) await loadFiles(); else {
        saveButton.classList.toggle('saved', saved.includes(id));
        saveButton.textContent = saved.includes(id) ? '♥' : '♡';
        saveButton.title = saved.includes(id) ? 'إزالة من المحفوظات' : 'حفظ الملف';
        saveButton.setAttribute('aria-label', saveButton.title);
      }
      toast(saved.includes(id) ? 'انحفظ الملف بجهازك.' : 'انشال الملف من المحفوظات.'); return;
    }
    const previewButton = event.target.closest('[data-preview]');
    if (previewButton) openPreview(previewButton.dataset.preview, previewButton.dataset.title);
  });
  $('#closePreview').addEventListener('click', closePreview);
  $('#previewModal').addEventListener('click', event => {if (event.target === $('#previewModal')) closePreview();});

  // Study plan is intentionally local to the learner's device.
  function readPlan() { try { const v = JSON.parse(localStorage.getItem(planKey) || '[]'); return Array.isArray(v) ? v : []; } catch { return []; } }
  function savePlan(items) { localStorage.setItem(planKey, JSON.stringify(items)); }
  function renderPlan() {
    const tasks = readPlan().sort((a,b) => ['الأحد','الاثنين','الثلاثاء','الأربعاء','الخميس','الجمعة','السبت'].indexOf(a.day)-['الأحد','الاثنين','الثلاثاء','الأربعاء','الخميس','الجمعة','السبت'].indexOf(b.day) || a.time.localeCompare(b.time));
    if (!tasks.length) { $('#studyTasks').innerHTML = '<p class="muted">ما أضفت مهام لحد الآن.</p>'; return; }
    $('#studyTasks').innerHTML = tasks.map(task => `<div class="study-task ${task.done ? 'done' : ''}"><input type="checkbox" data-task-done="${task.id}" ${task.done ? 'checked' : ''} aria-label="إنجاز ${escapeHTML(task.topic)}"><span class="study-task-main"><strong>${escapeHTML(task.subject)} — ${escapeHTML(task.topic)}</strong><small>${escapeHTML(task.day)} · ${escapeHTML(task.time)}</small></span><button type="button" class="delete-task" data-task-delete="${task.id}" aria-label="حذف المهمة">×</button></div>`).join('');
  }
  $('#studyForm').addEventListener('submit', event => {
    event.preventDefault(); const subject = $('#studySubject').value; const topic = $('#studyTopic').value.trim(); const day = $('#studyDay').value; const time = $('#studyTime').value;
    if (!subject || !topic || !day || !time) return;
    const items = readPlan(); items.push({id: `${Date.now()}-${Math.random().toString(16).slice(2,7)}`, subject, topic, day, time, done: false}); savePlan(items);
    $('#studyTopic').value = ''; renderPlan(); toast('انضافت المهمة لجدولك.');
  });
  $('#studyTasks').addEventListener('change', event => {
    const id = event.target.dataset.taskDone; if (!id) return;
    savePlan(readPlan().map(task => task.id === id ? {...task, done: event.target.checked} : task)); renderPlan();
  });
  $('#studyTasks').addEventListener('click', event => {
    const id = event.target.closest('[data-task-delete]')?.dataset.taskDelete; if (!id) return;
    savePlan(readPlan().filter(task => task.id !== id)); renderPlan(); toast('انحذفت المهمة.');
  });

  $('#requestForm').addEventListener('submit', async event => {
    event.preventDefault(); const status = $('#requestStatus'); status.classList.remove('error'); status.textContent = 'جارٍ إرسال الطلب...';
    const submit = event.submitter; if (submit) submit.disabled = true;
    const payload = {subject: $('#requestSubject').value, details: $('#requestDetails').value.trim(), contact: $('#requestContact').value.trim()};
    try {
      const result = await api('/api/requests', {method: 'POST', body: JSON.stringify(payload)});
      status.textContent = result.message || 'وصل طلبك وانحفظ.'; $('#requestDetails').value = ''; $('#requestContact').value = ''; toast('انحفظ طلبك بنجاح.');
    } catch (error) { status.classList.add('error'); status.textContent = error.message || 'تعذر إرسال الطلب. حاول مرة ثانية.'; }
    finally {if (submit) submit.disabled = false;}
  });

  async function init() {
    updateSavedCount(); renderPlan();
    try {
      const [loadedSubjects, stats] = await Promise.all([api('/api/subjects'), api('/api/stats')]);
      subjects = loadedSubjects; renderSubjects(); updateStats(stats);
    } catch { $('#subjectList').innerHTML = '<p class="muted">تعذر تحميل المواد.</p>'; }
    await Promise.all([loadFiles({keepSearch: false}), reloadDiscoveries()]);
  }
  init();
})();
