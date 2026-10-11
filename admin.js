(() => {
  'use strict';
  const $ = s => document.querySelector(s);
  let key = '';
  let dashboard = {subjects: [], files: [], stats: {}, settings: {}};
  const kindLabels = {book:'كتاب', note:'ملزمة', summary:'ملخص', exam:'امتحان'};
  function esc(v) { return String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
  let toastTimer;
  function toast(text) { const el=$('#toast'); el.textContent=text; el.classList.add('show'); clearTimeout(toastTimer); toastTimer=setTimeout(()=>el.classList.remove('show'),2600); }
  function message(text, error=false) { const el=$('#adminMessage'); el.textContent=text; el.classList.toggle('error',error); }
  async function api(path, options={}) {
    const headers = {'Accept':'application/json','X-Admin-Key':key, ...(options.body ? {'Content-Type':'application/json'} : {}), ...(options.headers || {})};
    const response = await fetch(path, {...options, headers});
    const body = await response.json().catch(()=>({}));
    if (!response.ok) throw new Error(body.detail || body.message || `خطأ ${response.status}`);
    return body;
  }
  function renderStats() {
    const stats = dashboard.stats || {};
    const items = [['subjects','المواد'],['files','كل الملفات'],['visible_files','ملفات ظاهرة'],['users','مستخدمو البوت'],['downloads','الاستلامات']];
    $('#adminStats').innerHTML = items.map(([key,label])=>`<div class="admin-stat"><strong>${Number(stats[key]||0).toLocaleString('en-US')}</strong><span>${label}</span></div>`).join('');
  }
  function renderSubjectSelectors() {
    const visibleSubjects = dashboard.subjects.filter(s=>s.visible);
    $('#fileSubject').innerHTML = dashboard.subjects.map(s=>`<option value="${s.id}">${esc(s.emoji)} ${esc(s.name)}${s.visible?'':' (مخفية)'}</option>`).join('');
    if (!dashboard.subjects.length) $('#fileSubject').innerHTML='<option value="">أضف مادة أولاً</option>';
    $('#adminSubjects').innerHTML = dashboard.subjects.length ? dashboard.subjects.map(s=>`<div class="admin-subject-chip"><span>${esc(s.emoji)}</span><strong>${esc(s.name)}</strong><span class="status">${s.visible?'ظاهرة':'مخفية'}</span><button type="button" data-subject-edit="${s.id}">تعديل</button><button type="button" data-subject-toggle="${s.id}">${s.visible?'إخفاء':'إظهار'}</button><button type="button" data-subject-delete="${s.id}">حذف</button></div>`).join('') : '<p class="muted">ماكو مواد.</p>';
  }
  function renderFiles() {
    const q = $('#adminFileSearch').value.trim().toLowerCase();
    const kind = $('#adminFileKind').value; const visibility = $('#adminVisibility').value;
    const files = dashboard.files.filter(f=>{
      const hay = `${f.title} ${f.subject} ${f.teacher||''} ${f.description||''}`.toLowerCase();
      return (!q||hay.includes(q)) && (kind==='all'||f.kind===kind) && (visibility==='all'||(visibility==='visible'?f.visible:!f.visible));
    });
    $('#adminFiles').innerHTML = files.length ? files.map(f=>`<tr><td><strong>${esc(f.title)}</strong><span class="subline">#${f.id} · ${esc(f.subject)}${f.teacher?' · '+esc(f.teacher):''}</span></td><td>${kindLabels[f.kind]||esc(f.kind)}</td><td><span class="state-label ${f.visible?'':'hidden'}">${f.visible?'ظاهر':'مخفي'}</span></td><td>${Number(f.downloads||0).toLocaleString('en-US')}</td><td><div class="table-actions"><button type="button" data-file-edit="${f.id}">تعديل</button><button type="button" data-file-toggle="${f.id}">${f.visible?'إخفاء':'إظهار'}</button><button type="button" class="danger" data-file-delete="${f.id}">حذف</button></div></td></tr>`).join('') : '<tr><td colspan="5">ماكو ملفات مطابقة.</td></tr>';
  }
  function renderRequests(items) {
    $('#adminRequests').innerHTML = items.length ? items.map(r=>`<div class="request-admin-item"><div><strong>#${r.id} · ${esc(r.subject)} · ${r.status==='new'?'جديد':r.status==='done'?'مكتمل':'مرفوض'}</strong><p>${esc(r.details)}${r.contact?`\nالتواصل: ${esc(r.contact)}`:''}</p></div>${r.status==='new'?`<button type="button" data-request-done="${r.id}">اعتباره مكتملًا</button>`:''}</div>`).join('') : '<p class="muted">ماكو طلبات مسجلة.</p>';
  }
  function renderSettings() {
    const s = dashboard.settings || {};
    $('#settingChannel').value = s.mandatory_channel || '@Kii_8i';
    $('#settingSite').value = s.site_url || '';
    $('#settingWelcome').value = s.welcome || '';
    $('#settingSubscription').checked = Boolean(s.subscription_enabled);
  }
  async function refresh() {
    try {
      const data = await api('/api/admin/dashboard'); dashboard = data;
      const requests = await api('/api/admin/requests?limit=50');
      renderStats(); renderSubjectSelectors(); renderFiles(); renderRequests(requests); renderSettings();
      $('#adminDashboard').hidden = false; $('#logoutBtn').hidden = false; $('#adminKey').disabled = true;
      message('تم الاتصال. أي تغييرات محفوظة تظهر بالموقع والبوت عبر قاعدة البيانات المشتركة.');
      sessionStorage.setItem('rabaiyoon.adminKey', key);
    } catch (error) {
      $('#adminDashboard').hidden = true;
      message(error.message || 'تعذر الاتصال بلوحة الإدارة.', true);
      if ((error.message||'').includes('مفتاح الإدارة')) { sessionStorage.removeItem('rabaiyoon.adminKey'); key=''; }
      throw error;
    }
  }
  async function refreshSafe() { try { await refresh(); } catch {} }
  function resetFileForm() {
    $('#editFileId').value=''; $('#fileFormTitle').textContent='إضافة ملف للمكتبة'; $('#saveFileBtn').textContent='حفظ الملف';
    $('#fileTitle').value=''; $('#fileTeacher').value=''; $('#fileDescription').value=''; $('#fileYear').value='2026'; $('#telegramFileId').value=''; $('#previewFileId').value=''; $('#fileType').value='document'; $('#fileVisible').checked=true;
  }
  function fillFileForm(id) {
    const f = dashboard.files.find(item=>Number(item.id)===Number(id)); if(!f) return;
    $('#editFileId').value=f.id; $('#fileFormTitle').textContent=`تعديل الملف #${f.id}`; $('#saveFileBtn').textContent='حفظ التعديلات';
    $('#fileTitle').value=f.title; $('#fileSubject').value=String(f.subject_id); $('#fileKind').value=f.kind; $('#fileTeacher').value=f.teacher||''; $('#fileYear').value=f.year||'2026'; $('#fileType').value=f.file_type||'document'; $('#telegramFileId').value=f.telegram_file_id; $('#previewFileId').value=f.preview_file_id||''; $('#fileDescription').value=f.description||''; $('#fileVisible').checked=Boolean(f.visible);
    $('#fileFormTitle').scrollIntoView({behavior:'smooth',block:'center'});
  }

  $('#loginForm').addEventListener('submit', async event=>{
    event.preventDefault(); key=$('#adminKey').value.trim(); if(!key){message('أدخل مفتاح الإدارة.',true);return;}
    try { await refresh(); } catch {}
  });
  $('#logoutBtn').addEventListener('click',()=>{key='';sessionStorage.removeItem('rabaiyoon.adminKey');$('#adminKey').value='';$('#adminKey').disabled=false;$('#logoutBtn').hidden=true;$('#adminDashboard').hidden=true;message('تم تسجيل الخروج.');});
  $('#subjectForm').addEventListener('submit',async event=>{
    event.preventDefault();
    try { await api('/api/admin/subjects',{method:'POST',body:JSON.stringify({name:$('#newSubjectName').value.trim(),emoji:$('#newSubjectEmoji').value.trim()||'📚'})}); $('#newSubjectName').value=''; $('#newSubjectEmoji').value='📚'; await refresh(); toast('تمت إضافة المادة.'); }
    catch(error){toast(error.message||'تعذر إضافة المادة.');}
  });
  $('#adminSubjects').addEventListener('click',async event=>{
    const edit=event.target.closest('[data-subject-edit]'); const toggle=event.target.closest('[data-subject-toggle]'); const del=event.target.closest('[data-subject-delete]');
    try {
      if(edit){const subject=dashboard.subjects.find(s=>s.id===Number(edit.dataset.subjectEdit));if(!subject)return;const name=prompt('اسم المادة:',subject.name);if(name===null)return;const emoji=prompt('رمز المادة:',subject.emoji||'📚');if(emoji===null)return;await api(`/api/admin/subjects/${subject.id}`,{method:'PATCH',body:JSON.stringify({name:name.trim(),emoji:emoji.trim()||'📚'})});await refresh();toast('تم تعديل المادة.');}
      else if(toggle){const subject=dashboard.subjects.find(s=>s.id===Number(toggle.dataset.subjectToggle));if(!subject)return;await api(`/api/admin/subjects/${subject.id}`,{method:'PATCH',body:JSON.stringify({visible:!subject.visible})});await refresh();toast('تم تحديث ظهور المادة.');}
      else if(del){const subject=dashboard.subjects.find(s=>s.id===Number(del.dataset.subjectDelete));if(!subject)return;if(!confirm(`حذف مادة «${subject.name}» وكل الملفات المرتبطة بها؟ لا يمكن التراجع.`))return;await api(`/api/admin/subjects/${subject.id}`,{method:'DELETE'});await refresh();toast('تم حذف المادة والملفات التابعة.');}
    } catch(error){toast(error.message||'تعذر تنفيذ العملية.');}
  });
  $('#fileForm').addEventListener('submit',async event=>{
    event.preventDefault();
    const payload={subject_id:Number($('#fileSubject').value),kind:$('#fileKind').value,title:$('#fileTitle').value.trim(),teacher:$('#fileTeacher').value.trim(),description:$('#fileDescription').value.trim(),year:$('#fileYear').value.trim()||'2026',telegram_file_id:$('#telegramFileId').value.trim(),file_type:$('#fileType').value,preview_file_id:$('#previewFileId').value.trim()||null,visible:$('#fileVisible').checked};
    try { const id=$('#editFileId').value; await api(id?`/api/admin/files/${id}`:'/api/admin/files',{method:id?'PATCH':'POST',body:JSON.stringify(payload)});await refresh();resetFileForm();toast(id?'تم تحديث بيانات الملف.':'تمت إضافة الملف.'); }
    catch(error){toast(error.message||'تعذر حفظ الملف.');}
  });
  $('#resetFileForm').addEventListener('click',resetFileForm);
  $('#adminFiles').addEventListener('click',async event=>{
    const edit=event.target.closest('[data-file-edit]');const toggle=event.target.closest('[data-file-toggle]');const del=event.target.closest('[data-file-delete]');
    try {
      if(edit){fillFileForm(edit.dataset.fileEdit);return;}
      if(toggle){const f=dashboard.files.find(item=>item.id===Number(toggle.dataset.fileToggle));if(!f)return;await api(`/api/admin/files/${f.id}`,{method:'PATCH',body:JSON.stringify({visible:!f.visible})});await refresh();toast('تم تحديث ظهور الملف.');}
      else if(del){const f=dashboard.files.find(item=>item.id===Number(del.dataset.fileDelete));if(!f)return;if(!confirm(`حذف الملف «${f.title}» نهائيًا؟`))return;await api(`/api/admin/files/${f.id}`,{method:'DELETE'});await refresh();toast('تم حذف الملف.');}
    } catch(error){toast(error.message||'تعذر تنفيذ العملية.');}
  });
  $('#adminFileSearch').addEventListener('input',renderFiles);$('#adminFileKind').addEventListener('change',renderFiles);$('#adminVisibility').addEventListener('change',renderFiles);$('#refreshBtn').addEventListener('click',refreshSafe);
  $('#settingsForm').addEventListener('submit',async event=>{
    event.preventDefault();const payload={mandatory_channel:$('#settingChannel').value.trim(),site_url:$('#settingSite').value.trim(),welcome:$('#settingWelcome').value,subscription_enabled:$('#settingSubscription').checked};
    try{await api('/api/admin/settings',{method:'PATCH',body:JSON.stringify(payload)});await refresh();toast('تم حفظ الإعدادات.');}catch(error){toast(error.message||'تعذر حفظ الإعدادات.');}
  });
  $('#adminRequests').addEventListener('click',async event=>{
    const button=event.target.closest('[data-request-done]');if(!button)return;
    try{await api(`/api/admin/requests/${button.dataset.requestDone}`,{method:'PATCH',body:JSON.stringify({status:'done'})});await refresh();toast('تم تحديث حالة الطلب.');}catch(error){toast(error.message||'تعذر تحديث الطلب.');}
  });
  try { key=sessionStorage.getItem('rabaiyoon.adminKey')||''; if(key){$('#adminKey').value=key; refreshSafe();} } catch {}
})();
