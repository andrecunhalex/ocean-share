// OceanShare Web — P2P file sharing via WebRTC
// Host shares a folder, guests connect with a code and download files

const CHUNK_SIZE = 64 * 1024;       // 64KB — modern WebRTC handles this cleanly
const BUFFER_HIGH = 8 * 1024 * 1024; // 8MB: pause when buffer exceeds
const BUFFER_LOW = 2 * 1024 * 1024;  // 2MB: resume when buffer drops below
const THUMB_SIZE = 120;
const EXTENSIONS_VIDEO = ['.mp4','.mov','.avi','.mkv','.m4v','.wmv','.webm','.flv','.mpg','.mpeg','.3gp'];
const EXTENSIONS_IMG = ['.jpg','.jpeg','.png','.gif','.webp','.bmp','.svg','.heic','.heif','.avif','.tiff','.tif'];
const EXTENSIONS_AUDIO = ['.mp3','.wav','.ogg','.flac','.m4a','.aac','.opus'];
const PREVIEWABLE_IMG = ['.jpg','.jpeg','.png','.gif','.webp','.bmp','.avif'];
const PREVIEWABLE_VIDEO = ['.mp4','.mov','.webm','.m4v'];

const $ = (id) => document.getElementById(id);
const views = ['home','host','join','files'];
function showView(name) {
  views.forEach(v => $(v).classList.toggle('hidden', v !== name));
}

function fmtSize(b) {
  const units = ['B','KB','MB','GB'];
  let i = 0;
  while (b >= 1024 && i < units.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(1)} ${units[i]}`;
}

function fileExt(name) {
  const i = name.lastIndexOf('.');
  return i >= 0 ? name.slice(i).toLowerCase() : '';
}

function fileIconName(name) {
  const ext = fileExt(name);
  if (EXTENSIONS_VIDEO.includes(ext)) return 'mdi:video';
  if (EXTENSIONS_IMG.includes(ext)) return 'mdi:image';
  if (EXTENSIONS_AUDIO.includes(ext)) return 'mdi:music-note';
  if (ext === '.pdf') return 'mdi:file-pdf-box';
  if (['.zip','.rar','.7z','.tar','.gz'].includes(ext)) return 'mdi:folder-zip';
  if (['.doc','.docx','.odt','.txt','.rtf'].includes(ext)) return 'mdi:file-document';
  if (['.xls','.xlsx','.ods','.csv'].includes(ext)) return 'mdi:file-excel';
  if (['.ppt','.pptx','.odp','.key'].includes(ext)) return 'mdi:file-powerpoint';
  return 'mdi:file-outline';
}

function isImage(name) { return EXTENSIONS_IMG.includes(fileExt(name)); }
function isVideo(name) { return EXTENSIONS_VIDEO.includes(fileExt(name)); }
function isAudio(name) { return EXTENSIONS_AUDIO.includes(fileExt(name)); }
function isPreviewable(name) {
  const ext = fileExt(name);
  return PREVIEWABLE_IMG.includes(ext) || PREVIEWABLE_VIDEO.includes(ext);
}

function mimeFor(name) {
  const ext = fileExt(name);
  const map = {
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
    '.gif': 'image/gif', '.webp': 'image/webp', '.avif': 'image/avif',
    '.bmp': 'image/bmp', '.svg': 'image/svg+xml',
    '.mp4': 'video/mp4', '.mov': 'video/quicktime', '.webm': 'video/webm',
    '.m4v': 'video/mp4', '.mkv': 'video/x-matroska',
    '.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.ogg': 'audio/ogg',
    '.flac': 'audio/flac', '.m4a': 'audio/mp4', '.aac': 'audio/aac',
    '.pdf': 'application/pdf',
  };
  return map[ext] || 'application/octet-stream';
}

function randomCode() {
  return String(Math.floor(1000 + Math.random() * 9000));
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// Detect if peer connection is LAN, same-nat, or internet
async function detectConnectionType(peerConn) {
  try {
    const stats = await peerConn.getStats();
    const all = [];
    stats.forEach(s => all.push(s));
    const addr = (c) => c?.address || c?.ip || null;

    const pair = all.find(s => s.type === 'candidate-pair' && s.state === 'succeeded' && (s.nominated || s.selected));
    const local = pair ? all.find(s => s.id === pair.localCandidateId) : null;
    const remote = pair ? all.find(s => s.id === pair.remoteCandidateId) : null;

    if (local?.candidateType === 'host' && remote?.candidateType === 'host') return 'lan';

    const ourPublics = all
      .filter(s => (s.type === 'local-candidate' || s.type === 'candidate') && (s.candidateType === 'srflx' || s.candidateType === 'prflx'))
      .map(addr).filter(Boolean);
    const theirAddrs = all
      .filter(s => s.type === 'remote-candidate')
      .map(addr).filter(Boolean);

    if (ourPublics.some(ip => theirAddrs.includes(ip))) return 'same-nat';
    return 'internet';
  } catch (e) { return 'unknown'; }
}

// ================= HOST MODE =================

let hostState = {
  peer: null, code: null, folderHandle: null,
  files: new Map(), connections: new Set(),
};

async function startHost() {
  if (!window.showDirectoryPicker) {
    alert('Este navegador não suporta seleção de pasta. Use Chrome ou Edge.');
    return;
  }
  let dirHandle;
  try { dirHandle = await window.showDirectoryPicker({ mode: 'read' }); }
  catch (e) { return; }

  hostState.folderHandle = dirHandle;
  $('hostFolderName').textContent = dirHandle.name;
  hostState.files = await scanDirectory(dirHandle);

  showView('host');
  $('hostStatus').textContent = 'Iniciando...';
  $('hostStatus').className = 'status';
  await initHostPeer();
}

async function scanDirectory(dirHandle, prefix = '') {
  const files = new Map();
  for await (const entry of dirHandle.values()) {
    if (entry.name.startsWith('.')) continue;
    const relPath = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.kind === 'file') {
      try {
        const file = await entry.getFile();
        files.set(relPath, { name: entry.name, path: relPath, size: file.size, handle: entry });
      } catch (e) {}
    } else if (entry.kind === 'directory') {
      const sub = await scanDirectory(entry, relPath);
      sub.forEach((v, k) => files.set(k, v));
    }
  }
  return files;
}

async function initHostPeer() {
  for (let attempt = 0; attempt < 10; attempt++) {
    const candidate = randomCode();
    const peerId = `ocean-${candidate}`;
    const success = await tryClaimId(peerId);
    if (success) {
      hostState.code = candidate;
      $('hostCode').textContent = candidate;
      const url = new URL(location.href);
      url.hash = `#${candidate}`;
      const urlStr = url.toString();
      $('hostLink').textContent = urlStr;
      renderQR(urlStr);
      $('hostStatus').textContent = 'Pronto. Aguardando conexões...';
      $('hostStatus').className = 'status ok';
      return;
    }
  }
  $('hostStatus').textContent = 'Erro: não foi possível reservar um código. Tente de novo.';
  $('hostStatus').className = 'status err';
}

function tryClaimId(peerId) {
  return new Promise((resolve) => {
    const peer = new Peer(peerId, { debug: 1 });
    let done = false;
    peer.on('open', () => {
      if (done) return;
      done = true;
      hostState.peer = peer;
      setupHostPeer(peer);
      resolve(true);
    });
    peer.on('error', () => {
      if (done) return;
      done = true;
      peer.destroy();
      resolve(false);
    });
  });
}

function setupHostPeer(peer) {
  peer.on('connection', (conn) => {
    hostState.connections.add(conn);
    conn.on('open', () => {
      renderPeers();
      conn.on('data', (msg) => handleHostMessage(conn, msg));
      conn.on('close', () => {
        hostState.connections.delete(conn);
        renderPeers();
      });
    });
  });
  peer.on('error', (err) => {
    console.error('peer error', err);
    $('hostStatus').textContent = 'Erro: ' + err.type;
    $('hostStatus').className = 'status err';
  });
}

function renderPeers() {
  const container = $('hostPeers');
  const n = hostState.connections.size;
  container.innerHTML = n === 0 ? '' :
    `<div class="peer-item"><span>🟢 ${n} dispositivo${n>1?'s':''} conectado${n>1?'s':''}</span></div>`;
}

async function handleHostMessage(conn, msg) {
  if (msg.type === 'list') {
    const arr = Array.from(hostState.files.values()).map(f => ({
      name: f.name, path: f.path, size: f.size
    }));
    conn.send({ type: 'files', folder: hostState.folderHandle.name, files: arr });
  } else if (msg.type === 'get') {
    await sendFileToGuest(conn, msg.path);
  } else if (msg.type === 'preview') {
    await sendPreviewToGuest(conn, msg.path);
  }
}

async function sendFileToGuest(conn, path) {
  const entry = hostState.files.get(path);
  if (!entry) {
    conn.send({ type: 'error', path, message: 'Arquivo não encontrado' });
    return;
  }
  try {
    const file = await entry.handle.getFile();
    conn.send({ type: 'start', path, name: file.name, size: file.size });

    const dc = conn.dataChannel;
    if (dc) dc.bufferedAmountLowThreshold = BUFFER_LOW;

    const reader = file.stream().getReader();
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      for (let i = 0; i < value.length; i += CHUNK_SIZE) {
        const chunk = value.subarray(i, Math.min(i + CHUNK_SIZE, value.length));
        if (dc && dc.bufferedAmount > BUFFER_HIGH) await waitForDrain(dc);
        conn.send(chunk);
      }
    }
    conn.send({ type: 'end', path });
  } catch (e) {
    console.error('send error', e);
    conn.send({ type: 'error', path, message: e.message });
  }
}

function waitForDrain(dc) {
  return new Promise((resolve) => {
    const handler = () => { dc.removeEventListener('bufferedamountlow', handler); resolve(); };
    dc.addEventListener('bufferedamountlow', handler);
  });
}

async function sendPreviewToGuest(conn, path) {
  const entry = hostState.files.get(path);
  if (!entry || !isPreviewable(entry.name)) {
    conn.send({ type: 'preview_data', path, dataUrl: null });
    return;
  }
  try {
    const file = await entry.handle.getFile();
    const ext = fileExt(entry.name);
    let dataUrl = null;
    if (PREVIEWABLE_VIDEO.includes(ext)) dataUrl = await videoFrameThumbnail(file);
    else dataUrl = await imageThumbnail(file);
    conn.send({ type: 'preview_data', path, dataUrl });
  } catch (e) {
    console.warn('preview failed for', path, e);
    conn.send({ type: 'preview_data', path, dataUrl: null });
  }
}

async function imageThumbnail(file) {
  try {
    const bitmap = await createImageBitmap(file);
    const url = drawToDataUrl(bitmap, bitmap.width, bitmap.height);
    bitmap.close?.();
    return url;
  } catch (e1) {
    return new Promise((resolve, reject) => {
      const blobUrl = URL.createObjectURL(file);
      const img = new Image();
      img.decoding = 'async';
      img.onload = () => {
        try {
          const url = drawToDataUrl(img, img.naturalWidth, img.naturalHeight);
          URL.revokeObjectURL(blobUrl);
          resolve(url);
        } catch (e) { URL.revokeObjectURL(blobUrl); reject(e); }
      };
      img.onerror = () => { URL.revokeObjectURL(blobUrl); reject(new Error('img decode failed')); };
      img.src = blobUrl;
    });
  }
}

async function videoFrameThumbnail(file) {
  return new Promise((resolve, reject) => {
    const blobUrl = URL.createObjectURL(file);
    const video = document.createElement('video');
    video.preload = 'auto';
    video.muted = true;
    video.playsInline = true;
    video.crossOrigin = 'anonymous';
    let settled = false;
    const done = (fn, arg) => {
      if (settled) return;
      settled = true;
      URL.revokeObjectURL(blobUrl);
      video.removeAttribute('src');
      video.load();
      fn(arg);
    };
    video.addEventListener('loadedmetadata', () => {
      try {
        const seekTo = video.duration && video.duration > 0
          ? Math.min(1, video.duration / 4)
          : 0;
        video.currentTime = seekTo;
      } catch (e) { done(reject, e); }
    });
    video.addEventListener('seeked', () => {
      try {
        if (!video.videoWidth || !video.videoHeight) {
          return done(reject, new Error('no video dimensions'));
        }
        done(resolve, drawToDataUrl(video, video.videoWidth, video.videoHeight));
      } catch (e) { done(reject, e); }
    });
    video.addEventListener('error', (e) => {
      console.warn('video thumbnail error', file.name, video.error);
      done(reject, new Error('video decode failed'));
    });
    setTimeout(() => done(reject, new Error('video timeout')), 20000);
    video.src = blobUrl;
  });
}

function drawToDataUrl(source, srcW, srcH) {
  const ratio = Math.min(THUMB_SIZE / srcW, THUMB_SIZE / srcH, 1);
  const w = Math.max(1, Math.round(srcW * ratio));
  const h = Math.max(1, Math.round(srcH * ratio));
  const canvas = document.createElement('canvas');
  canvas.width = w; canvas.height = h;
  canvas.getContext('2d').drawImage(source, 0, 0, w, h);
  return canvas.toDataURL('image/jpeg', 0.7);
}

function renderQR(url) {
  const canvas = $('hostQr');
  if (!canvas) return;
  if (typeof qrcode === 'undefined') {
    const img = document.createElement('img');
    img.src = `https://api.qrserver.com/v1/create-qr-code/?size=220x220&data=${encodeURIComponent(url)}`;
    img.alt = 'QR code';
    img.style.maxWidth = '220px';
    canvas.replaceWith(img);
    img.id = 'hostQr';
    return;
  }
  try {
    const qr = qrcode(0, 'M');
    qr.addData(url);
    qr.make();
    const modules = qr.getModuleCount();
    const scale = 6;
    const size = modules * scale;
    canvas.width = size;
    canvas.height = size;
    canvas.style.width = Math.min(220, size) + 'px';
    canvas.style.height = Math.min(220, size) + 'px';
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, size, size);
    ctx.fillStyle = '#0f172a';
    for (let r = 0; r < modules; r++) {
      for (let c = 0; c < modules; c++) {
        if (qr.isDark(r, c)) ctx.fillRect(c * scale, r * scale, scale, scale);
      }
    }
  } catch (e) { console.error('QR error:', e); }
}

function stopHost() {
  if (hostState.peer) {
    hostState.connections.forEach(c => c.close());
    hostState.peer.destroy();
  }
  hostState = { peer: null, code: null, folderHandle: null, files: new Map(), connections: new Set() };
  showView('home');
}

// ================= GUEST MODE =================

const guestState = {
  peer: null, conn: null, hostCode: null, folder: '',
  files: [],
  currentPath: '',
  previews: new Map(),
  previewsRequested: new Set(),
  previewsPending: 0,
  downloaded: new Set(),
  selected: new Set(),
  queue: [], // [{path, mode: 'save'|'preview'}]
  activeTransfer: null, // {path, mode, name, size, chunks, received}
};

function resetGuest() {
  guestState.peer = null; guestState.conn = null;
  guestState.hostCode = null; guestState.folder = '';
  guestState.files = [];
  guestState.currentPath = '';
  guestState.previews = new Map();
  guestState.previewsRequested = new Set();
  guestState.previewsPending = 0;
  guestState.downloaded = new Set();
  guestState.selected = new Set();
  guestState.queue = [];
  guestState.activeTransfer = null;
}

function showJoin(prefillCode) {
  showView('join');
  $('joinStatus').textContent = '';
  $('joinStatus').className = 'status';
  if (prefillCode) $('joinCode').value = prefillCode;
  $('joinCode').focus();
}

async function connectAsGuest() {
  const code = $('joinCode').value.trim();
  if (!/^\d{4}$/.test(code)) {
    $('joinStatus').textContent = 'Digite um código de 4 dígitos';
    $('joinStatus').className = 'status err';
    return;
  }
  $('joinStatus').textContent = 'Conectando...';
  $('joinStatus').className = 'status';

  guestState.hostCode = code;
  const peer = new Peer({ debug: 1 });
  guestState.peer = peer;

  peer.on('open', () => {
    const conn = peer.connect(`ocean-${code}`, { reliable: true });
    guestState.conn = conn;
    conn.on('open', async () => {
      $('joinStatus').textContent = 'Conectado! Carregando lista...';
      $('joinStatus').className = 'status ok';
      conn.send({ type: 'list' });
      setTimeout(async () => {
        const type = await detectConnectionType(conn.peerConnection);
        const info = $('connInfo');
        if (type === 'lan') {
          info.innerHTML = '<iconify-icon icon="mdi:wifi-check"></iconify-icon> <span>Conectado pela rede local — transferência direta</span>';
          info.className = 'conn-info lan';
        } else if (type === 'same-nat') {
          info.innerHTML = '<iconify-icon icon="mdi:wifi-check"></iconify-icon> <span>Mesma rede detectada — tráfego não vai pra internet</span>';
          info.className = 'conn-info same-nat';
        } else if (type === 'internet') {
          info.innerHTML = '<iconify-icon icon="mdi:earth"></iconify-icon> <span>Conectado via internet — P2P direto</span>';
          info.className = 'conn-info internet';
        } else {
          info.innerHTML = '<iconify-icon icon="mdi:link-variant"></iconify-icon> <span>Conectado</span>';
          info.className = 'conn-info';
        }
      }, 1500);
    });
    conn.on('data', handleGuestMessage);
    conn.on('close', () => {
      if (guestState.conn) {
        $('joinStatus').textContent = 'Conexão encerrada';
        $('joinStatus').className = 'status err';
        showView('join');
      }
    });
    conn.on('error', (err) => {
      console.error('conn error', err);
      $('joinStatus').textContent = 'Erro na conexão: ' + (err.message || err.type || 'desconhecido');
      $('joinStatus').className = 'status err';
    });
  });

  peer.on('error', (err) => {
    if (err.type === 'peer-unavailable') {
      $('joinStatus').textContent = 'Código não encontrado. Confirme se a pessoa está compartilhando.';
    } else {
      $('joinStatus').textContent = 'Erro: ' + err.type;
    }
    $('joinStatus').className = 'status err';
  });
}

function handleGuestMessage(msg) {
  if (msg.type === 'files') {
    guestState.files = msg.files;
    guestState.folder = msg.folder;
    guestState.currentPath = '';
    $('filesTitle').textContent = `Arquivos em “${msg.folder}”`;
    renderBreadcrumb();
    renderFiles();
    showView('files');
    requestPreviews();
  } else if (msg.type === 'preview_data') {
    guestState.previews.set(msg.path, msg.dataUrl || null);
    updateFileThumbnail(msg.path);
    guestState.previewsPending--;
    requestPreviews();
  } else if (msg.type === 'start') {
    const t = guestState.activeTransfer;
    if (!t || t.path !== msg.path) return;
    t.name = msg.name; t.size = msg.size;
    renderTransfers();
    updateModalProgress();
  } else if (msg instanceof ArrayBuffer || msg instanceof Uint8Array) {
    const t = guestState.activeTransfer;
    if (!t) return;
    const buf = msg instanceof Uint8Array ? msg : new Uint8Array(msg);
    t.chunks.push(buf);
    t.received += buf.length;
    renderTransfers();
    updateModalProgress();
  } else if (msg.type === 'end') {
    const t = guestState.activeTransfer;
    if (!t || t.path !== msg.path) return;
    const blob = new Blob(t.chunks, { type: mimeFor(t.name) });
    finishTransfer(t, blob);
  } else if (msg.type === 'error') {
    alert('Erro: ' + msg.message);
    guestState.activeTransfer = null;
    renderTransfers();
    processQueue();
  }
}

function finishTransfer(t, blob) {
  if (t.mode === 'save') {
    downloadBlob(blob, t.name);
    guestState.downloaded.add(t.path);
    renderFiles();
  } else if (t.mode === 'preview') {
    showPreviewBlob(t, blob);
  }
  guestState.activeTransfer = null;
  renderTransfers();
  processQueue();
}

function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function enqueue(path, mode) {
  // avoid double queuing same path in same mode
  if (guestState.queue.some(q => q.path === path && q.mode === mode)) return;
  if (guestState.activeTransfer?.path === path && guestState.activeTransfer.mode === mode) return;
  guestState.queue.push({ path, mode });
  processQueue();
}

function processQueue() {
  if (guestState.activeTransfer) return;
  const next = guestState.queue.shift();
  if (!next) return;
  const fileMeta = guestState.files.find(f => f.path === next.path);
  if (!fileMeta) { processQueue(); return; }
  guestState.activeTransfer = {
    path: next.path, mode: next.mode,
    name: fileMeta.name, size: fileMeta.size,
    chunks: [], received: 0
  };
  guestState.conn.send({ type: 'get', path: next.path });
  renderTransfers();
  if (next.mode === 'preview') renderModalLoading(fileMeta);
}

// Files rendering

function getViewItems() {
  const cur = guestState.currentPath;
  const prefix = cur ? cur + '/' : '';
  const folderCounts = new Map();
  const files = [];
  for (const f of guestState.files) {
    if (prefix && !f.path.startsWith(prefix)) continue;
    const rest = f.path.slice(prefix.length);
    const slash = rest.indexOf('/');
    if (slash >= 0) {
      const folderName = rest.slice(0, slash);
      folderCounts.set(folderName, (folderCounts.get(folderName) || 0) + 1);
    } else {
      files.push(f);
    }
  }
  const folders = Array.from(folderCounts.entries())
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => a.name.localeCompare(b.name));
  files.sort((a, b) => a.name.localeCompare(b.name));
  return { folders, files };
}

function renderBreadcrumb() {
  const bc = $('breadcrumb');
  if (!guestState.folder) { bc.innerHTML = ''; return; }
  const parts = guestState.currentPath ? guestState.currentPath.split('/') : [];
  let html = `<a data-path="" class="${parts.length === 0 ? 'current' : ''}">
    <iconify-icon icon="mdi:folder-home"></iconify-icon>
    ${escapeHtml(guestState.folder)}
  </a>`;
  let accum = '';
  parts.forEach((p, i) => {
    accum = accum ? accum + '/' + p : p;
    const isLast = i === parts.length - 1;
    html += `<iconify-icon icon="mdi:chevron-right"></iconify-icon>
      <a data-path="${escapeHtml(accum)}" class="${isLast ? 'current' : ''}">${escapeHtml(p)}</a>`;
  });
  bc.innerHTML = html;
  bc.querySelectorAll('a').forEach(el => {
    el.addEventListener('click', () => goToPath(el.dataset.path));
  });
}

function goToPath(path) {
  guestState.currentPath = path;
  renderBreadcrumb();
  renderFiles();
}

function enterFolder(name) {
  const newPath = guestState.currentPath ? guestState.currentPath + '/' + name : name;
  goToPath(newPath);
}

function renderFiles() {
  const list = $('fileList');
  const { folders, files } = getViewItems();
  if (folders.length === 0 && files.length === 0) {
    list.innerHTML = '<div class="file-empty">Pasta vazia</div>';
    updateSelectBar();
    return;
  }
  let html = '';
  folders.forEach(fol => {
    html += `
      <div class="file-item folder-entry" data-folder="${escapeHtml(fol.name)}">
        <div class="file-slot">
          <iconify-icon icon="mdi:folder"></iconify-icon>
        </div>
        <div class="file-meta">
          <div class="file-name">${escapeHtml(fol.name)}</div>
          <div class="folder-count">${fol.count} arquivo${fol.count > 1 ? 's' : ''}</div>
        </div>
        <iconify-icon icon="mdi:chevron-right" style="color:var(--text-3);font-size:20px"></iconify-icon>
      </div>
    `;
  });
  files.forEach((f) => {
    const isSel = guestState.selected.has(f.path);
    const isDl = guestState.downloaded.has(f.path);
    const previewable = isPreviewable(f.name) || isAudio(f.name);
    html += `
      <div class="file-item ${isSel ? 'selected' : ''} ${isDl ? 'downloaded' : ''}" data-path="${escapeHtml(f.path)}">
        <div class="file-chk" data-act="select">
          <iconify-icon icon="mdi:check"></iconify-icon>
        </div>
        <div class="file-slot" data-act="${previewable ? 'preview' : 'download'}">
          ${renderThumb(f)}
        </div>
        <div class="file-meta" data-act="${previewable ? 'preview' : 'download'}">
          <div class="file-name">${escapeHtml(f.name)}</div>
          <div class="file-size">${fmtSize(f.size)}</div>
        </div>
        <div class="file-actions" data-act="download">
          ${isDl
            ? '<iconify-icon icon="mdi:check-circle" class="ico-done"></iconify-icon>'
            : '<iconify-icon icon="mdi:download"></iconify-icon>'}
        </div>
      </div>
    `;
  });
  list.innerHTML = html;

  list.querySelectorAll('.file-item.folder-entry').forEach(el => {
    el.addEventListener('click', () => enterFolder(el.dataset.folder));
  });
  list.querySelectorAll('.file-item:not(.folder-entry)').forEach(el => {
    el.addEventListener('click', (e) => {
      const target = e.target.closest('[data-act]');
      const act = target?.dataset.act;
      const path = el.dataset.path;
      const file = guestState.files.find(f => f.path === path);
      if (!file) return;
      if (act === 'select') toggleSelect(path);
      else if (act === 'download') enqueue(path, 'save');
      else if (act === 'preview') {
        if (isPreviewable(file.name) || isAudio(file.name)) openPreview(file);
        else enqueue(path, 'save');
      }
    });
  });
  updateSelectBar();
}

function renderThumb(f) {
  const dataUrl = guestState.previews.get(f.path);
  if (dataUrl) return `<img class="file-thumb" src="${dataUrl}" alt="">`;
  return `<iconify-icon icon="${fileIconName(f.name)}"></iconify-icon>`;
}

function updateFileThumbnail(path) {
  const slot = $('fileList').querySelector(`.file-item[data-path="${CSS.escape(path)}"] .file-slot`);
  if (!slot) return;
  const f = guestState.files.find(x => x.path === path);
  if (!f) return;
  slot.innerHTML = renderThumb(f);
}

function requestPreviews() {
  if (!guestState.conn || !guestState.files) return;
  const MAX_PARALLEL = 3;
  while (guestState.previewsPending < MAX_PARALLEL) {
    const next = guestState.files.find(f =>
      isPreviewable(f.name) &&
      !guestState.previews.has(f.path) &&
      !guestState.previewsRequested.has(f.path)
    );
    if (!next) return;
    guestState.previewsRequested.add(next.path);
    guestState.previewsPending++;
    guestState.conn.send({ type: 'preview', path: next.path });
  }
}

// Selection

function toggleSelect(path) {
  if (guestState.selected.has(path)) guestState.selected.delete(path);
  else guestState.selected.add(path);
  renderFiles();
}

function clearSelection() {
  guestState.selected.clear();
  renderFiles();
}

function selectAll() {
  const { files } = getViewItems();
  files.forEach(f => guestState.selected.add(f.path));
  renderFiles();
}

function downloadSelected() {
  const paths = Array.from(guestState.selected);
  paths.forEach(p => enqueue(p, 'save'));
  clearSelection();
}

function updateSelectBar() {
  const bar = $('selectBar');
  const n = guestState.selected.size;
  $('selCount').textContent = n;
  bar.classList.toggle('hidden', n === 0);
}

// Transfers UI

function renderTransfers() {
  const container = $('transfers');
  const t = guestState.activeTransfer;
  const qLen = guestState.queue.length;
  if (!t && qLen === 0) { container.innerHTML = ''; return; }
  let html = '';
  if (t) {
    const pct = t.size ? Math.min(100, (t.received / t.size) * 100) : 0;
    const label = t.mode === 'preview' ? 'Carregando preview' : 'Baixando';
    html += `
      <div class="transfer-item">
        <div class="transfer-name">${label}: ${escapeHtml(t.name)}</div>
        <div class="transfer-meta">${fmtSize(t.received)} / ${fmtSize(t.size || 0)} (${pct.toFixed(0)}%)</div>
        <div class="transfer-bar"><div class="transfer-bar-fill" style="width:${pct}%"></div></div>
      </div>`;
  }
  if (qLen > 0) {
    html += `<div class="transfer-item"><div class="transfer-meta">${qLen} em fila</div></div>`;
  }
  container.innerHTML = html;
}

// Modal preview

const modalState = { blob: null, name: null, path: null };

function openPreview(file) {
  modalState.blob = null;
  modalState.name = file.name;
  modalState.path = file.path;
  $('modal').classList.remove('hidden');
  renderModalLoading(file);
  enqueue(file.path, 'preview');
}

function closeModal() {
  $('modal').classList.add('hidden');
  $('modalBody').innerHTML = '';
  modalState.blob = null; modalState.name = null; modalState.path = null;
}

function renderModalLoading(file) {
  $('modalFileInfo').innerHTML = `
    <div class="n">${escapeHtml(file.name)}</div>
    <div class="s">${fmtSize(file.size)}</div>
  `;
  $('modalBody').innerHTML = `
    <div class="modal-loading">
      <iconify-icon icon="mdi:progress-download" style="font-size:48px;color:var(--accent)"></iconify-icon>
      <div style="margin-top:12px">Carregando <span id="modalPct">0</span>%</div>
      <div class="transfer-bar"><div class="transfer-bar-fill" id="modalBarFill" style="width:0%"></div></div>
    </div>`;
}

function updateModalProgress() {
  const t = guestState.activeTransfer;
  if (!t || t.mode !== 'preview') return;
  const pct = t.size ? Math.min(100, (t.received / t.size) * 100) : 0;
  const pctEl = $('modalPct'); const barEl = $('modalBarFill');
  if (pctEl) pctEl.textContent = pct.toFixed(0);
  if (barEl) barEl.style.width = pct + '%';
}

function showPreviewBlob(t, blob) {
  modalState.blob = blob;
  const url = URL.createObjectURL(blob);
  const name = t.name;
  let html;
  if (isImage(name)) {
    html = `<img src="${url}" alt="${escapeHtml(name)}">`;
  } else if (isVideo(name)) {
    html = `<video src="${url}" controls autoplay playsinline></video>`;
  } else if (isAudio(name)) {
    html = `<div style="padding:20px"><audio src="${url}" controls autoplay></audio></div>`;
  } else {
    html = `<div class="modal-generic">
      <iconify-icon icon="${fileIconName(name)}"></iconify-icon>
      <p>Preview não disponível para este tipo. Salve no dispositivo para abrir.</p>
    </div>`;
  }
  $('modalBody').innerHTML = html;
}

function saveFromModal() {
  if (!modalState.blob || !modalState.name) return;
  downloadBlob(modalState.blob, modalState.name);
  guestState.downloaded.add(modalState.path);
  renderFiles();
}

function disconnectGuest() {
  if (guestState.conn) guestState.conn.close();
  if (guestState.peer) guestState.peer.destroy();
  resetGuest();
  showView('home');
}

// ================= INIT =================

$('btnHost').addEventListener('click', startHost);
$('btnJoin').addEventListener('click', () => showJoin());
$('btnStop').addEventListener('click', stopHost);
$('btnConnect').addEventListener('click', connectAsGuest);
$('btnJoinBack').addEventListener('click', () => showView('home'));
$('btnDisconnect').addEventListener('click', disconnectGuest);
$('btnSelClear').addEventListener('click', clearSelection);
$('btnSelAll').addEventListener('click', selectAll);
$('btnSelDownload').addEventListener('click', downloadSelected);

$('modalClose').addEventListener('click', closeModal);
$('modalBackdrop').addEventListener('click', closeModal);
$('modalSave').addEventListener('click', saveFromModal);

$('joinCode').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') connectAsGuest();
});

$('hostLink').addEventListener('click', () => {
  navigator.clipboard.writeText($('hostLink').textContent);
  const orig = $('hostLink').textContent;
  $('hostLink').textContent = '✓ Copiado!';
  setTimeout(() => { $('hostLink').textContent = orig; }, 1500);
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !$('modal').classList.contains('hidden')) closeModal();
});

// Deep link: #CODE opens join view
if (location.hash && /^#\d{4}$/.test(location.hash)) {
  showJoin(location.hash.slice(1));
}
