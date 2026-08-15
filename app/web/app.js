const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const state = {
  project: null,
  projects: [],
  sessionId: null,
  documents: [],
  jobs: [],
  citations: [],
  busy: false,
};

const api = {
  headers(json = true) {
    const headers = {};
    if (json) headers['Content-Type'] = 'application/json';
    return headers;
  },
  async request(path, options = {}) {
    const response = await fetch(`/api/v1${path}`, options);
    if (!response.ok) {
      let detail = `Request failed (${response.status})`;
      try { detail = (await response.json()).detail || detail; } catch {}
      throw new Error(detail);
    }
    return response.status === 204 ? null : response.json();
  },
};

function projectPath(suffix = '') {
  return `/projects/${encodeURIComponent(state.project.project_id)}${suffix}`;
}

function showView(name) {
  $('#chatView').classList.toggle('hidden', name !== 'chat');
  $('#documentsView').classList.toggle('hidden', name !== 'documents');
  $('#projectsView').classList.toggle('hidden', name !== 'projects');
  $$('[data-view]').forEach((button) => button.classList.toggle('active', button.dataset.view === name));
  if (name === 'documents') refreshDocuments();
  if (name === 'projects') refreshProjects();
}

function toast(message, error = false) {
  const element = document.createElement('div');
  element.className = `toast${error ? ' error' : ''}`;
  element.textContent = message;
  $('#toastRegion').append(element);
  setTimeout(() => element.remove(), 4200);
}

function escapeHtml(text = '') {
  return String(text).replace(/[&<>'"]/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  }[character]));
}

function formatDate(value) {
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' }).format(new Date(value));
}

function bytes(value) {
  if (value == null) return '';
  if (value < 1024) return `${value} B`;
  if (value < 1048576) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1048576).toFixed(1)} MB`;
}

function readinessLabel(project) {
  if (project.readiness === 'ready') return 'Ready to query';
  if (project.readiness === 'preparing') return 'Preparing sources';
  if (project.readiness === 'failed') return 'Setup failed';
  if (project.readiness === 'archived') return 'Archived';
  return 'Add documents to start';
}

async function refreshProjects(preferredId = null) {
  try {
    state.projects = await api.request('/projects', { headers: api.headers(false) });
    const savedId = preferredId || localStorage.getItem('citebot.selectedProjectId');
    const selected = state.projects.find((project) => project.project_id === savedId)
      || state.projects.find((project) => project.is_sample && project.readiness === 'ready')
      || state.projects.find((project) => project.status === 'active')
      || state.projects[0];
    if (selected) await switchProject(selected.project_id, false);
    renderProjects();
  } catch (error) {
    $('#libraryHint').textContent = 'Projects unavailable';
    toast(error.message, true);
  }
}

async function switchProject(projectId, announce = true) {
  const project = state.projects.find((candidate) => candidate.project_id === projectId);
  if (!project) return;
  state.project = project;
  state.sessionId = null;
  state.citations = [];
  state.documents = [];
  state.jobs = [];
  localStorage.setItem('citebot.selectedProjectId', project.project_id);
  $('.project-switcher-name').textContent = project.name;
  $('.project-switcher-state').textContent = readinessLabel(project);
  $('#documentsTitle').textContent = `${project.name} documents`;
  $('#chatEyebrow').textContent = project.is_sample ? 'Sample Project · private research desk' : `${project.name} · private research desk`;
  $('#queryInput').placeholder = project.readiness === 'ready' ? `Ask ${project.name}…` : 'Add a document before querying…';
  $('#sendButton').disabled = project.readiness !== 'ready';
  $('#messages').innerHTML = '';
  $('#emptyChat').classList.remove('hidden');
  await Promise.all([refreshDocuments(), refreshConversations()]);
  renderProjects();
  if (announce) toast(`Switched to ${project.name}`);
}

function renderProjects() {
  const list = $('#projectList');
  if (!list) return;
  list.innerHTML = state.projects.map((project) => `
    <article class="project-row ${state.project?.project_id === project.project_id ? 'selected' : ''}">
      <div class="project-row-main"><div class="project-row-kicker">${project.is_sample ? 'Sample' : 'Project'}</div><h2>${escapeHtml(project.name)}</h2><p>${escapeHtml(project.description || 'A focused source set for your team.')}</p></div>
      <div class="project-row-meta"><span class="project-readiness ${project.readiness}">${escapeHtml(readinessLabel(project))}</span><span>${project.ready_document_count} ready · ${project.document_count} total</span><button class="project-open" data-project-id="${escapeHtml(project.project_id)}">Open project →</button></div>
    </article>`).join('') || '<p class="project-empty">Create a project to start building a reusable source set.</p>';
  $$('.project-open').forEach((button) => button.onclick = () => { switchProject(button.dataset.projectId); showView('chat'); });
}

async function refreshSelectedProjectStatus() {
  if (!state.project) return;
  try {
    const project = await api.request(`/projects/${encodeURIComponent(state.project.project_id)}`, { headers: api.headers(false) });
    state.project = project;
    $('.project-switcher-name').textContent = project.name;
    $('.project-switcher-state').textContent = readinessLabel(project);
    $('#sendButton').disabled = project.readiness !== 'ready';
    $('#queryInput').placeholder = project.readiness === 'ready' ? `Ask ${project.name}…` : 'Add a document before querying…';
    renderProjects();
  } catch (error) { console.warn(error); }
}

async function refreshDocuments() {
  if (!state.project) return;
  try {
    const [documents, jobs] = await Promise.all([
      api.request(projectPath('/documents'), { headers: api.headers(false) }),
      api.request(projectPath('/documents/jobs'), { headers: api.headers(false) }),
    ]);
    state.documents = documents;
    state.jobs = jobs;
    renderDocuments();
    const project = state.projects.find((candidate) => candidate.project_id === state.project.project_id);
    if (project) { state.project = project; $('.project-switcher-state').textContent = readinessLabel(project); }
    $('#libraryHint').textContent = `${documents.length} source${documents.length === 1 ? '' : 's'} in ${state.project.name}`;
  } catch (error) {
    $('#libraryHint').textContent = 'Library unavailable';
    toast(error.message, true);
  }
}

function renderDocuments() {
  const query = $('#documentSearch').value.toLowerCase();
  const documents = state.documents.filter((document) => document.title.toLowerCase().includes(query));
  const pending = state.jobs.filter((job) => ['queued', 'running'].includes(job.status));
  $('#documentCount').textContent = `${state.documents.length + pending.length} document${state.documents.length + pending.length === 1 ? '' : 's'}`;
  $('#readyCount').textContent = `${state.documents.length} ready`;
  const pendingRows = pending.map((job) => documentRow({ title: job.source_path.split('/').pop(), source_uri: job.source_path, started_at: job.started_at, chunk_count: '—' }, 'processing', job.stage || job.status));
  const documentRows = documents.map((document) => documentRow(document, 'ready', 'Ready'));
  $('#documentList').innerHTML = [...pendingRows, ...documentRows].join('') || '<div class="document-row"><span>No documents in this project yet. Add your first source above.</span></div>';
}

function documentRow(document, status, label) {
  const extension = (document.title.split('.').pop() || 'DOC').slice(0, 4).toUpperCase();
  return `<div class="document-row" role="row"><span class="document-name"><i class="file-glyph">${escapeHtml(extension)}</i><span><strong>${escapeHtml(document.title)}</strong><small>${escapeHtml(bytes(document.size_bytes) || document.source_uri)}</small></span></span><span class="status ${status}">${escapeHtml(label)}</span><span>${formatDate(document.ingested_at || document.started_at)}</span><span>${document.chunk_count}</span></div>`;
}

async function uploadFiles(files) {
  if (!state.project || state.project.status === 'archived') return toast('Choose an active project before uploading', true);
  for (const file of files) {
    toast(`Uploading ${file.name}…`);
    try {
      const xhr = await new Promise((resolve, reject) => {
        const request = new XMLHttpRequest();
        request.open('POST', `/api/v1${projectPath(`/documents/uploads?filename=${encodeURIComponent(file.name)}`)}`);
        request.onload = () => request.status < 300 ? resolve(JSON.parse(request.responseText)) : reject(new Error(JSON.parse(request.responseText || '{}').detail || `Upload failed (${request.status})`));
        request.onerror = () => reject(new Error('Upload connection failed'));
        request.send(file);
      });
      toast(`${file.name} ${xhr.job.status === 'completed' ? 'is ready' : 'is processing'}`);
    } catch (error) { toast(`${file.name}: ${error.message}`, true); }
    await refreshDocuments();
  }
  await refreshSelectedProjectStatus();
}

async function refreshConversations() {
  if (!state.project) return;
  try {
    const rows = await api.request(projectPath('/conversations'), { headers: api.headers(false) });
    $('#conversationList').innerHTML = rows.map((row) => `<button class="conversation-item ${row.session_id === state.sessionId ? 'active' : ''}" data-session="${escapeHtml(row.session_id)}">${escapeHtml(row.title)}</button>`).join('');
    $$('.conversation-item').forEach((button) => button.onclick = () => loadConversation(button.dataset.session));
  } catch (error) { console.warn(error); }
}

async function loadConversation(sessionId) {
  try {
    const record = await api.request(projectPath(`/conversations/${encodeURIComponent(sessionId)}`), { headers: api.headers(false) });
    state.sessionId = sessionId;
    state.citations = [];
    $('#emptyChat').classList.add('hidden');
    $('#messages').innerHTML = '';
    record.turns.forEach((turn) => appendMessage(turn.role, turn.content));
    refreshConversations();
    showView('chat');
  } catch (error) { toast(error.message, true); }
}

function newChat() {
  state.sessionId = null;
  state.citations = [];
  $('#messages').innerHTML = '';
  $('#emptyChat').classList.remove('hidden');
  $('#sourcePanel').classList.remove('open');
  refreshConversations();
  $('#queryInput').focus();
}

function appendMessage(role, content, citations = [], verification = {}) {
  $('#emptyChat').classList.add('hidden');
  const element = document.createElement('article');
  element.className = `message ${role}`;
  if (role === 'assistant') {
    const metadata = citations.length ? `<div class="answer-meta"><span>${citations.length} source${citations.length === 1 ? '' : 's'}</span><span>Evidence checked</span><span>${escapeHtml((verification.answer_status || 'draft').replaceAll('_', ' '))}</span></div>` : '';
    element.innerHTML = `<div class="message-label">CiteBot · ${escapeHtml(state.project?.name || 'Project')}</div><p>${escapeHtml(content)} ${citations.map((citation, index) => `<button class="citation-button" data-citation="${index}">${index + 1}</button>`).join('')}</p>${metadata}`;
    element.querySelectorAll('.citation-button').forEach((button) => button.onclick = () => inspectCitation(citations[Number(button.dataset.citation)]));
  } else element.innerHTML = `<p>${escapeHtml(content)}</p>`;
  $('#messages').append(element);
  element.scrollIntoView({ behavior: 'smooth', block: 'end' });
  return element;
}

function inspectCitation(citation) {
  if (!citation) return;
  $('#sourceEmpty').classList.add('hidden');
  $('#sourceContent').classList.remove('hidden');
  $('#sourceContent').innerHTML = `<h2 class="source-document">${escapeHtml(citation.title)}</h2><div class="source-location">${escapeHtml(citation.location_marker || (citation.page ? `Page ${citation.page}` : 'Indexed source'))}</div><blockquote class="source-quote">${escapeHtml(citation.quoted_support)}</blockquote><p class="source-support">${escapeHtml(citation.support_span)}</p>`;
  $('#sourcePanel').classList.add('open');
}

async function submitQuestion(query) {
  if (state.busy || !state.project || state.project.readiness !== 'ready') return toast('This project is not ready to query yet', true);
  state.busy = true;
  $('#sendButton').disabled = true;
  appendMessage('user', query);
  const pending = appendMessage('assistant', 'Searching this project and checking sources…');
  pending.classList.add('pending');
  try {
    const response = await fetch(`/api/v1${projectPath('/research/query/stream')}`, { method: 'POST', headers: api.headers(), body: JSON.stringify({ session_id: state.sessionId, query, top_k: 6 }) });
    if (!response.ok) throw new Error(`Chat request failed (${response.status})`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let completed = null;
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const lines = buffer.split('\n');
      buffer = lines.pop();
      for (const line of lines) {
        if (!line) continue;
        const event = JSON.parse(line);
        if (event.event === 'start') state.sessionId = event.data.session_id;
        if (event.event === 'complete') completed = event.data;
        if (event.event === 'error') throw new Error(event.data.detail);
      }
      if (done) break;
    }
    pending.remove();
    if (!completed) throw new Error('The research request ended without an answer');
    state.citations = completed.answer.citations || [];
    appendMessage('assistant', completed.answer.direct_answer, state.citations, completed);
    await refreshConversations();
  } catch (error) {
    pending.remove();
    appendMessage('assistant', `I couldn't complete that request. ${error.message}`);
    toast(error.message, true);
  } finally {
    state.busy = false;
    $('#sendButton').disabled = state.project?.readiness !== 'ready';
  }
}

async function createProject(event) {
  event.preventDefault();
  const name = $('#newProjectName').value.trim();
  if (!name) return;
  try {
    const project = await api.request('/projects', { method: 'POST', headers: api.headers(), body: JSON.stringify({ name, description: $('#newProjectDescription').value.trim() || null }) });
    $('#newProjectDialog').close();
    $('#newProjectForm').reset();
    await refreshProjects(project.project_id);
    showView('chat');
    toast(`${project.name} is ready for uploads`);
  } catch (error) { toast(error.message, true); }
}

$$('[data-view]').forEach((button) => button.addEventListener('click', () => showView(button.dataset.view)));
$('#projectSwitcher').onclick = () => showView('projects');
$('#newChatButton').onclick = newChat;
$('#uploadButton').onclick = () => $('#fileInput').click();
$('#fileInput').onchange = (event) => uploadFiles(event.target.files);
$('#documentSearch').oninput = renderDocuments;
$('#newProjectButton').onclick = () => $('#newProjectDialog').showModal();
$('#newProjectForm').onsubmit = createProject;
$$('.suggestion').forEach((button) => button.onclick = () => { showView('chat'); $('#queryInput').value = button.textContent; submitQuestion(button.textContent); });
$('#chatForm').onsubmit = (event) => { event.preventDefault(); const query = $('#queryInput').value.trim(); if (query) { $('#queryInput').value = ''; submitQuestion(query); } };
$('#queryInput').onkeydown = (event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); $('#chatForm').requestSubmit(); } };
$('#queryInput').oninput = (event) => { event.target.style.height = 'auto'; event.target.style.height = `${event.target.scrollHeight}px`; };
$('#closeSources').onclick = () => $('#sourcePanel').classList.remove('open');

const dropZone = $('#dropZone');
['dragenter', 'dragover'].forEach((name) => dropZone.addEventListener(name, (event) => { event.preventDefault(); dropZone.classList.add('dragging'); }));
['dragleave', 'drop'].forEach((name) => dropZone.addEventListener(name, (event) => { event.preventDefault(); dropZone.classList.remove('dragging'); }));
dropZone.ondrop = (event) => uploadFiles(event.dataTransfer.files);
dropZone.onclick = () => $('#fileInput').click();

refreshProjects();
setInterval(() => { if (!$('#documentsView').classList.contains('hidden')) refreshDocuments(); }, 4000);
setInterval(refreshSelectedProjectStatus, 4000);
