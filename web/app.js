// OceanShare Web — P2P file sharing via WebRTC
// Host shares a folder, guests connect with a code and download files

const CHUNK_SIZE = 16 * 1024;
const BUFFER_THRESHOLD = 1024 * 1024; // 1MB
const EXTENSIONS_VIDEO = ['.mp4','.mov','.avi','.mkv','.m4v','.wmv','.webm'];
const EXTENSIONS_IMG = ['.jpg','.jpeg','.png','.heic','.gif'];
const EXTENSIONS_OTHER = ['.mp3','.pdf','.zip','.rar'];
const EXTENSIONS = [...EXTENSIONS_VIDEO, ...EXTENSIONS_IMG, ...EXTENSIONS_OTHER];

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

function fileIcon(name) {
  const ext = '.' + name.split('.').pop().toLowerCase();
  if (EXTENSIONS_VIDEO.includes(ext)) return '🎬';
  if (EXTENSIONS_IMG.includes(ext)) return '🖼️';
  if (ext === '.mp3') return '🎵';
  if (ext === '.pdf') return '📄';
  if (ext === '.zip' || ext === '.rar') return '🗜️';
  return '📎';
}

// Generate random 4-digit code
function randomCode() {
  return String(Math.floor(1000 + Math.random() * 9000));
}

// Detect if peer connection is using LAN or internet
async function detectConnectionType(peerConn) {
  try {
    const stats = await peerConn.getStats();
    let localType = '', remoteType = '';
    stats.forEach(s => {
      if (s.type === 'candidate-pair' && s.state === 'succeeded' && s.nominated) {
        stats.forEach(c => {
          if (c.id === s.localCandidateId) localType = c.candidateType;
          if (c.id === s.remoteCandidateId) remoteType = c.candidateType;
        });
      }
    });
    // 'host' = direct LAN, 'srflx' = via STUN (internet), 'relay' = via TURN
    if (localType === 'host' && remoteType === 'host') return 'lan';
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
      const ext = '.' + entry.name.split('.').pop().toLowerCase();
      if (!EXTENSIONS.includes(ext)) continue;
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
      $('hostLink').textContent = url.toString();
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
          info.textContent = '✓ Conectado pela rede local — transferência não usa internet';
          info.className = 'conn-info lan';
        } else if (type === 'internet') {
          info.textContent = '🌐 Conectado via internet — transferência P2P direta';
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
    $('filesTitle').textContent = `Arquivos em 📁 ${msg.folder}`;
    renderFiles();
    showView('files');
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
    <div class="file-item" data-idx="${idx}">
      <span class="file-icon">${fileIcon(f.name)}</span>
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
  guestState = { peer: null, conn: null, hostCode: null, files: [], activeTransfer: null };
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
