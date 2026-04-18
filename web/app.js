// OceanShare Web — P2P file sharing via WebRTC
// Host shares a folder, guests connect with a code and download files

const CHUNK_SIZE = 16 * 1024;
const BUFFER_THRESHOLD = 1024 * 1024; // 1MB
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

function fileIcon(name) {
  const ext = fileExt(name);
  if (EXTENSIONS_VIDEO.includes(ext)) return '🎬';
  if (EXTENSIONS_IMG.includes(ext)) return '🖼️';
  if (EXTENSIONS_AUDIO.includes(ext)) return '🎵';
  if (ext === '.pdf') return '📄';
  if (['.zip','.rar','.7z','.tar','.gz'].includes(ext)) return '🗜️';
  if (['.doc','.docx','.odt','.txt','.rtf'].includes(ext)) return '📝';
  if (['.xls','.xlsx','.ods','.csv'].includes(ext)) return '📊';
  if (['.ppt','.pptx','.odp','.key'].includes(ext)) return '📽️';
  return '📎';
}

function isPreviewable(name) {
  const ext = fileExt(name);
  return PREVIEWABLE_IMG.includes(ext) || PREVIEWABLE_VIDEO.includes(ext);
}

// Generate random 4-digit code
function randomCode() {
  return String(Math.floor(1000 + Math.random() * 9000));
}

// Detect if peer connection is using LAN, same network (same public IP), or internet
async function detectConnectionType(peerConn) {
  try {
    const stats = await peerConn.getStats();
    const all = [];
    stats.forEach(s => all.push(s));
    const pair = all.find(s => s.type === 'candidate-pair' && s.state === 'succeeded' && (s.nominated || s.selected));
    if (!pair) return 'unknown';
    const local = all.find(s => s.id === pair.localCandidateId);
    const remote = all.find(s => s.id === pair.remoteCandidateId);
    if (!local || !remote) return 'unknown';

    // Direct LAN: both peers sent local addresses directly
    if (local.candidateType === 'host' && remote.candidateType === 'host') return 'lan';

    // If both share same public IP (srflx), they're behind same NAT = same network
    const localSrflx = all.find(s => (s.type === 'local-candidate' || s.type === 'candidate') && s.candidateType === 'srflx');
    const remoteSrflx = all.find(s => s.type === 'remote-candidate' && s.candidateType === 'srflx');
    if (localSrflx && remoteSrflx && localSrflx.address && localSrflx.address === remoteSrflx.address) {
      return 'same-nat';
    }
    return 'internet';
  } catch (e) {
    return 'unknown';
  }
}

// ================= HOST MODE =================

let hostState = {
  peer: null,
  code: null,
  folderHandle: null,
  files: new Map(), // path -> {name, size, handle}
  connections: new Set(),
};

async function startHost() {
  if (!window.showDirectoryPicker) {
    alert('Este navegador não suporta seleção de pasta. Use Chrome ou Edge.');
    return;
  }

  let dirHandle;
  try {
    dirHandle = await window.showDirectoryPicker({ mode: 'read' });
  } catch (e) {
    return; // user cancelled
  }

  hostState.folderHandle = dirHandle;
  $('hostFolderName').textContent = '📁 ' + dirHandle.name;

  const files = await scanDirectory(dirHandle);
  hostState.files = files;

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
  // Try to claim a short code. If taken, try another.
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
      $('hostStatus').textContent = 'Aguardando conexões... (código pronto)';
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
    peer.on('open', (id) => {
      if (done) return;
      done = true;
      hostState.peer = peer;
      setupHostPeer(peer);
      resolve(true);
    });
    peer.on('error', (err) => {
      if (done) return;
      done = true;
      peer.destroy();
      resolve(false);
    });
  });
}

function setupHostPeer(peer) {
  peer.on('connection', (conn) => {
    console.log('incoming connection', conn.peer);
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
  if (n === 0) {
    container.innerHTML = '';
    return;
  }
  container.innerHTML = `<div class="peer-item"><span>${n} dispositivo${n>1?'s':''} conectado${n>1?'s':''}</span><span>🟢</span></div>`;
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
    if (PREVIEWABLE_VIDEO.includes(ext)) {
      dataUrl = await videoFrameThumbnail(file);
    } else {
      dataUrl = await imageThumbnail(file);
    }
    conn.send({ type: 'preview_data', path, dataUrl });
  } catch (e) {
    console.warn('preview failed for', path, e);
    conn.send({ type: 'preview_data', path, dataUrl: null });
  }
}

async function imageThumbnail(file) {
  // try createImageBitmap first
  try {
    const bitmap = await createImageBitmap(file);
    const url = drawToDataUrl(bitmap, bitmap.width, bitmap.height);
    bitmap.close?.();
    return url;
  } catch (e1) {
    // fallback: <img> + blob URL (handles WebP/AVIF/HEIC if browser supports as <img>)
    return new Promise((resolve, reject) => {
      const blobUrl = URL.createObjectURL(file);
      const img = new Image();
      img.decoding = 'async';
      img.onload = () => {
        try {
          const url = drawToDataUrl(img, img.naturalWidth, img.naturalHeight);
          URL.revokeObjectURL(blobUrl);
          resolve(url);
        } catch (e) {
          URL.revokeObjectURL(blobUrl);
          reject(e);
        }
      };
      img.onerror = () => {
        URL.revokeObjectURL(blobUrl);
        reject(new Error('img decode failed'));
      };
      img.src = blobUrl;
    });
  }
}

async function videoFrameThumbnail(file) {
  return new Promise((resolve, reject) => {
    const blobUrl = URL.createObjectURL(file);
    const video = document.createElement('video');
    video.preload = 'metadata';
    video.muted = true;
    video.playsInline = true;
    video.crossOrigin = 'anonymous';
    let settled = false;
    const done = (fn, arg) => {
      if (settled) return;
      settled = true;
      URL.revokeObjectURL(blobUrl);
      fn(arg);
    };
    video.addEventListener('loadeddata', () => {
      try {
        video.currentTime = Math.min(0.5, video.duration > 0 ? video.duration / 4 : 0);
      } catch (e) { done(reject, e); }
    });
    video.addEventListener('seeked', () => {
      try {
        const url = drawToDataUrl(video, video.videoWidth, video.videoHeight);
        done(resolve, url);
      } catch (e) { done(reject, e); }
    });
    video.addEventListener('error', () => done(reject, new Error('video decode failed')));
    setTimeout(() => done(reject, new Error('video timeout')), 8000);
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
    console.warn('QR lib not loaded, falling back to external image');
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
    ctx.fillStyle = '#005a8f';
    for (let r = 0; r < modules; r++) {
      for (let c = 0; c < modules; c++) {
        if (qr.isDark(r, c)) ctx.fillRect(c * scale, r * scale, scale, scale);
      }
    }
  } catch (e) {
    console.error('QR render error:', e);
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
    const reader = file.stream().getReader();
    const dc = conn.dataChannel;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      // value is Uint8Array — split into 16KB chunks
      for (let i = 0; i < value.length; i += CHUNK_SIZE) {
        const chunk = value.subarray(i, Math.min(i + CHUNK_SIZE, value.length));
        while (dc && dc.bufferedAmount > BUFFER_THRESHOLD) {
          await new Promise(r => setTimeout(r, 20));
        }
        conn.send(chunk);
      }
    }
    conn.send({ type: 'end', path });
  } catch (e) {
    console.error('send error', e);
    conn.send({ type: 'error', path, message: e.message });
  }
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

let guestState = {
  peer: null,
  conn: null,
  hostCode: null,
  files: [],
  activeTransfer: null, // { path, name, size, chunks: [], received: 0 }
  previews: new Map(),
  previewsRequested: new Set(),
  previewsPending: 0,
};

function showJoin(prefillCode) {
  showView('join');
  $('joinStatus').textContent = '';
  $('joinStatus').className = 'status';
  if (prefillCode) {
    $('joinCode').value = prefillCode;
  }
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

      // detect connection type after a moment
      setTimeout(async () => {
        const type = await detectConnectionType(conn.peerConnection);
        const info = $('connInfo');
        if (type === 'lan') {
          info.textContent = '✓ Conectado pela rede local — transferência direta via LAN';
          info.className = 'conn-info lan';
        } else if (type === 'same-nat') {
          info.textContent = '✓ Mesma rede detectada — tráfego fica no roteador, não vai pra internet';
          info.className = 'conn-info same-nat';
        } else if (type === 'internet') {
          info.textContent = '🌐 Conectado via internet — P2P direto, sem servidor no meio';
          info.className = 'conn-info internet';
        } else {
          info.textContent = '🔗 Conectado';
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
    guestState.previews = new Map();
    $('filesTitle').textContent = `Arquivos em 📁 ${msg.folder}`;
    renderFiles();
    showView('files');
    requestPreviews();
  } else if (msg.type === 'preview_data') {
    if (msg.dataUrl) guestState.previews.set(msg.path, msg.dataUrl);
    else guestState.previews.set(msg.path, null);
    updateFileThumbnail(msg.path);
    guestState.previewsPending--;
    requestPreviews();
  } else if (msg.type === 'start') {
    guestState.activeTransfer = {
      path: msg.path, name: msg.name, size: msg.size,
      chunks: [], received: 0
    };
    renderTransfers();
  } else if (msg instanceof ArrayBuffer || msg instanceof Uint8Array) {
    if (!guestState.activeTransfer) return;
    const buf = msg instanceof Uint8Array ? msg : new Uint8Array(msg);
    guestState.activeTransfer.chunks.push(buf);
    guestState.activeTransfer.received += buf.length;
    renderTransfers();
  } else if (msg.type === 'end') {
    const t = guestState.activeTransfer;
    if (!t || t.path !== msg.path) return;
    const blob = new Blob(t.chunks);
    downloadBlob(blob, t.name);
    guestState.activeTransfer = null;
    renderTransfers();
  } else if (msg.type === 'error') {
    alert('Erro ao baixar: ' + msg.message);
    guestState.activeTransfer = null;
    renderTransfers();
  }
}

function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function renderFiles() {
  const list = $('fileList');
  if (guestState.files.length === 0) {
    list.innerHTML = '<div class="file-empty">Nenhum arquivo disponível</div>';
    return;
  }
  list.innerHTML = guestState.files.map((f, idx) => `
    <div class="file-item" data-idx="${idx}" data-path="${escapeHtml(f.path)}">
      <span class="file-slot">${fileIcon(f.name)}</span>
      <div class="file-meta">
        <div class="file-name">${escapeHtml(f.name)}</div>
        <div class="file-size">${fmtSize(f.size)}</div>
      </div>
      <span>⬇</span>
    </div>
  `).join('');
  list.querySelectorAll('.file-item').forEach(el => {
    el.addEventListener('click', () => {
      const idx = parseInt(el.dataset.idx);
      requestDownload(guestState.files[idx]);
    });
  });
}

function updateFileThumbnail(path) {
  const dataUrl = guestState.previews.get(path);
  if (!dataUrl) return;
  const item = $('fileList').querySelector(`.file-item[data-path="${CSS.escape(path)}"] .file-slot`);
  if (item) {
    item.innerHTML = `<img class="file-thumb" src="${dataUrl}" alt="">`;
  }
}

function requestPreviews() {
  if (!guestState.conn || !guestState.files) return;
  const MAX_PARALLEL = 3;
  guestState.previewsPending = guestState.previewsPending || 0;
  while (guestState.previewsPending < MAX_PARALLEL) {
    const next = guestState.files.find(f => isPreviewable(f.name) && !guestState.previews.has(f.path) && !guestState.previewsRequested?.has(f.path));
    if (!next) return;
    guestState.previewsRequested = guestState.previewsRequested || new Set();
    guestState.previewsRequested.add(next.path);
    guestState.previewsPending++;
    guestState.conn.send({ type: 'preview', path: next.path });
  }
}

function requestDownload(file) {
  if (guestState.activeTransfer) {
    alert('Aguarde a transferência atual terminar');
    return;
  }
  guestState.conn.send({ type: 'get', path: file.path });
}

function renderTransfers() {
  const container = $('transfers');
  const t = guestState.activeTransfer;
  if (!t) {
    container.innerHTML = '';
    return;
  }
  const pct = Math.min(100, (t.received / t.size) * 100);
  container.innerHTML = `
    <div class="transfer-item">
      <div>${escapeHtml(t.name)} — ${fmtSize(t.received)} / ${fmtSize(t.size)} (${pct.toFixed(0)}%)</div>
      <div class="transfer-bar"><div class="transfer-bar-fill" style="width:${pct}%"></div></div>
    </div>
  `;
}

function disconnectGuest() {
  if (guestState.conn) guestState.conn.close();
  if (guestState.peer) guestState.peer.destroy();
  guestState = { peer: null, conn: null, hostCode: null, files: [], activeTransfer: null, previews: new Map(), previewsRequested: new Set(), previewsPending: 0 };
  showView('home');
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// ================= INIT =================

$('btnHost').addEventListener('click', startHost);
$('btnJoin').addEventListener('click', () => showJoin());
$('btnStop').addEventListener('click', stopHost);
$('btnConnect').addEventListener('click', connectAsGuest);
$('btnJoinBack').addEventListener('click', () => showView('home'));
$('btnDisconnect').addEventListener('click', disconnectGuest);

$('joinCode').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') connectAsGuest();
});

$('hostLink').addEventListener('click', () => {
  navigator.clipboard.writeText($('hostLink').textContent);
  $('hostLink').textContent = '✓ Copiado!';
  setTimeout(() => {
    const url = new URL(location.href);
    url.hash = `#${hostState.code}`;
    $('hostLink').textContent = url.toString();
  }, 1500);
});

// If URL has #CODE, jump straight to join screen with code filled in
if (location.hash && /^#\d{4}$/.test(location.hash)) {
  showJoin(location.hash.slice(1));
}
