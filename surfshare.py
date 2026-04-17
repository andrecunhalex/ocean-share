#!/usr/bin/env python3
"""
OceanShare 🌊 v5
App nativo macOS com janela própria (pywebview).
pip3 install pywebview
"""

import http.server
import socketserver
import os
import sys
import json
import socket
import urllib.parse
import subprocess
import threading
import signal
import time
import webview

PREFERRED_PORT = 8080
PORT_RANGE = (8080, 8100)
PORT = PREFERRED_PORT

if getattr(sys, 'frozen', False):
    if sys.platform == 'darwin':
        RESOURCE_DIR = os.path.normpath(os.path.join(os.path.dirname(sys.executable), '..', 'Resources'))
    else:
        RESOURCE_DIR = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
else:
    RESOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_PATH = os.path.join(RESOURCE_DIR, 'ocean-icon.jpg')

def find_free_port(preferred=PREFERRED_PORT, start=PORT_RANGE[0], end=PORT_RANGE[1]):
    """Tenta a porta preferida; se ocupada, procura a próxima livre no range."""
    candidates = [preferred] + [p for p in range(start, end + 1) if p != preferred]
    for p in candidates:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("", p))
                return p
            except OSError:
                continue
    # fallback: pede ao SO qualquer porta livre
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]
EXTENSIONS_VIDEO = {'.mp4','.mov','.avi','.mkv','.m4v','.wmv','.webm','.mpg','.mpeg','.3gp','.flv','.ts','.ogv'}
EXTENSIONS_IMG = {'.jpg','.jpeg','.png','.heic','.heif','.gif','.webp','.avif','.tiff','.tif','.bmp','.svg','.ico','.jxl',
                  '.dng','.raw','.raf','.cr2','.cr3','.nef','.arw','.orf','.rw2'}
EXTENSIONS_OTHER = {'.mp3','.wav','.flac','.aac','.ogg','.m4a','.opus','.wma',
                    '.pdf','.zip','.rar','.7z','.tar','.gz','.bz2','.xz','.iso','.dmg',
                    '.doc','.docx','.xls','.xlsx','.ppt','.pptx','.txt','.csv','.rtf','.json','.xml','.epub'}
EXTENSIONS = EXTENSIONS_VIDEO | EXTENSIONS_IMG | EXTENSIONS_OTHER

class State:
    def __init__(self):
        self.folder = None
        self.serving = False
        self.transfers = []
        self.received = []
        self._lock = threading.Lock()
        self.window = None

    def add_transfer(self, name, size, direction):
        with self._lock:
            entry = {"name": name, "size": size, "progress": 0, "direction": direction, "ts": time.time()}
            self.transfers.insert(0, entry)
            if len(self.transfers) > 50:
                self.transfers = self.transfers[:50]
            return entry

    def finish_transfer(self, entry):
        with self._lock:
            entry["progress"] = 100

    def add_received(self, name, size):
        with self._lock:
            self.received.insert(0, {"name": name, "size": size, "ts": time.time()})
            if len(self.received) > 50:
                self.received = self.received[:50]

state = State()

def _is_private_ipv4(ip):
    if not ip or ip.startswith('127.') or ip.startswith('169.254.'):
        return False
    if ip.startswith('10.') or ip.startswith('192.168.'):
        return True
    if ip.startswith('172.'):
        try:
            return 16 <= int(ip.split('.')[1]) <= 31
        except:
            return False
    return False

def _is_vpn_like(ip):
    # Tailscale / CGNAT range (100.64.0.0/10) — cliente não consegue alcançar
    if ip.startswith('100.'):
        try:
            return 64 <= int(ip.split('.')[1]) <= 127
        except:
            return False
    return False

def get_local_ips():
    """Retorna IPs IPv4 locais candidatos, com o da rota padrão primeiro."""
    ips = []
    default_ip = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        default_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    if default_ip and _is_private_ipv4(default_ip) and not _is_vpn_like(default_ip):
        ips.append(default_ip)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if _is_private_ipv4(ip) and not _is_vpn_like(ip) and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    if not ips and default_ip:
        ips.append(default_ip)
    if not ips:
        ips.append("127.0.0.1")
    return ips

def get_local_ip():
    return get_local_ips()[0]

def fmt_size(b):
    for u in ['B','KB','MB','GB']:
        if b < 1024: return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"

def choose_folder_dialog():
    if state.window:
        try:
            dlg_type = webview.FileDialog.FOLDER if hasattr(webview, 'FileDialog') else webview.FOLDER_DIALOG
            result = state.window.create_file_dialog(dialog_type=dlg_type)
            if result:
                if isinstance(result, (list, tuple)) and len(result) > 0:
                    return str(result[0])
                elif isinstance(result, str) and result:
                    return result
        except Exception:
            pass
    return None

def fmt_date(ts):
    import time
    t = time.localtime(ts)
    return f"{t.tm_mday:02d}/{t.tm_mon:02d}/{t.tm_year} {t.tm_hour:02d}:{t.tm_min:02d}"

def scan_dir(folder, rel_prefix=""):
    dirs, files = [], []
    if not folder or not os.path.isdir(folder): return dirs, files
    try:
        entries = sorted(os.listdir(folder))
    except:
        return dirs, files
    for f in entries:
        if f.startswith('.'): continue
        fp = os.path.join(folder, f)
        rel = os.path.join(rel_prefix, f) if rel_prefix else f
        if os.path.isdir(fp):
            count = _count_files_in(fp)
            if count > 0:
                dirs.append({"name": f, "rel": rel, "count": count})
        elif os.path.isfile(fp):
            ext = os.path.splitext(f)[1].lower()
            if ext in EXTENSIONS:
                ftype = "video" if ext in EXTENSIONS_VIDEO else "image" if ext in EXTENSIONS_IMG else "other"
                mtime = os.path.getmtime(fp)
                files.append({"name": f, "rel": rel, "size": fmt_size(os.path.getsize(fp)),
                              "bytes": os.path.getsize(fp), "type": ftype, "date": fmt_date(mtime)})
    return dirs, files

def _count_files_in(folder):
    count = 0
    try:
        for f in os.listdir(folder):
            if f.startswith('.'): continue
            fp = os.path.join(folder, f)
            if os.path.isdir(fp): count += _count_files_in(fp)
            elif os.path.isfile(fp) and os.path.splitext(f)[1].lower() in EXTENSIONS: count += 1
    except: pass
    return count

def scan_all_files(folder):
    files = []
    if not folder or not os.path.isdir(folder): return files
    try:
        for f in sorted(os.listdir(folder)):
            if f.startswith('.'): continue
            fp = os.path.join(folder, f)
            if os.path.isdir(fp):
                for sf in scan_all_files(fp):
                    sf_rel = os.path.join(f, sf["rel"]) if sf.get("_sub") else os.path.join(f, sf["name"])
                    files.append({**sf, "rel": sf_rel, "_sub": True})
            elif os.path.isfile(fp):
                ext = os.path.splitext(f)[1].lower()
                if ext in EXTENSIONS:
                    ftype = "video" if ext in EXTENSIONS_VIDEO else "image" if ext in EXTENSIONS_IMG else "other"
                    files.append({"name": f, "rel": f, "type": ftype})
    except: pass
    return files

def generate_thumbnail(fp, thumb_path):
    try:
        os.makedirs(os.path.dirname(thumb_path), exist_ok=True)
        subprocess.run(["qlmanage", "-t", "-s", "300", "-o", os.path.dirname(thumb_path), fp],
                       capture_output=True, text=True, timeout=10)
        base_gen = os.path.join(os.path.dirname(thumb_path), os.path.basename(fp) + ".png")
        if os.path.exists(base_gen):
            os.rename(base_gen, thumb_path); return True
        for c in os.listdir(os.path.dirname(thumb_path)):
            if c.endswith(".png") and c.startswith(os.path.splitext(os.path.basename(fp))[0]):
                src = os.path.join(os.path.dirname(thumb_path), c)
                if src != thumb_path: os.rename(src, thumb_path); return True
    except: pass
    return False

# ─── QR Code SVG ───

def _gf256():
    exp, log = [1]*512, [0]*256
    x = 1
    for i in range(1, 255):
        x <<= 1
        if x >= 256: x ^= 0x11d
        exp[i] = x; log[x] = i
    for i in range(255, 512): exp[i] = exp[i-255]
    return exp, log

GF_EXP, GF_LOG = _gf256()
def _gf_mul(a, b):
    if a == 0 or b == 0: return 0
    return GF_EXP[GF_LOG[a] + GF_LOG[b]]

def _rs_ecc(data, n):
    gen = [1]
    for i in range(n):
        ng = [0]*(len(gen)+1)
        for j in range(len(gen)):
            ng[j] ^= gen[j]; ng[j+1] ^= _gf_mul(gen[j], GF_EXP[i])
        gen = ng
    res = list(data)+[0]*n
    for i in range(len(data)):
        c = res[i]
        if c:
            for j in range(1, len(gen)): res[i+j] ^= _gf_mul(gen[j], c)
    return res[len(data):]

def make_qr_svg(text, box=6, border=2):
    data = text.encode('utf-8'); dl = len(data)
    if dl <= 32: ver, sz = 2, 25
    elif dl <= 53: ver, sz = 3, 29
    elif dl <= 78: ver, sz = 4, 33
    else: return None
    ecc_p = {2:(28,16), 3:(44,26), 4:(64,36)}
    data_cw, ecc_n = ecc_p[ver]
    bits = '0100' + format(dl,'08b')
    for b in data: bits += format(b,'08b')
    bits += '0000'
    while len(bits)%8: bits += '0'
    cws = [int(bits[i:i+8],2) for i in range(0,len(bits),8)]
    pad, pi = [0xEC,0x11], 0
    while len(cws)<data_cw: cws.append(pad[pi%2]); pi+=1
    ecc = _rs_ecc(cws[:data_cw], ecc_n)
    msg = cws[:data_cw] + ecc
    mx = [[None]*sz for _ in range(sz)]
    def finder(r,c):
        for dr in range(-1,8):
            for dc in range(-1,8):
                rr,cc=r+dr,c+dc
                if 0<=rr<sz and 0<=cc<sz:
                    if 0<=dr<=6 and 0<=dc<=6:
                        mx[rr][cc]=1 if(dr in(0,6)or dc in(0,6)or(2<=dr<=4 and 2<=dc<=4))else 0
                    else: mx[rr][cc]=0
    finder(0,0); finder(0,sz-7); finder(sz-7,0)
    for i in range(8,sz-8):
        mx[6][i]=1 if i%2==0 else 0; mx[i][6]=1 if i%2==0 else 0
    ap={2:[6,18],3:[6,22],4:[6,26]}[ver]
    for ar in ap:
        for ac in ap:
            if mx[ar][ac] is not None: continue
            for dr in range(-2,3):
                for dc in range(-2,3):
                    mx[ar+dr][ac+dc]=1 if(abs(dr)==2 or abs(dc)==2 or(dr==0 and dc==0))else 0
    mx[(4*ver)+9][8]=1
    is_fn=[[False]*sz for _ in range(sz)]
    for r in range(9):
        for c in range(9): is_fn[r][c]=True
        for c in range(sz-8,sz): is_fn[r][c]=True
    for r in range(sz-8,sz):
        for c in range(9): is_fn[r][c]=True
    for i in range(sz): is_fn[6][i]=True; is_fn[i][6]=True
    for ar in ap:
        for ac in ap:
            skip=(ar<=8 and ac<=8)or(ar<=8 and ac>=sz-8)or(ar>=sz-8 and ac<=8)
            if not skip:
                for dr in range(-2,3):
                    for dc in range(-2,3): is_fn[ar+dr][ac+dc]=True
    is_fn[(4*ver)+9][8]=True
    for i in range(9):
        if mx[8][i] is None: is_fn[8][i]=True; mx[8][i]=0
        if mx[i][8] is None: is_fn[i][8]=True; mx[i][8]=0
    for i in range(8):
        r,c=8,sz-1-i
        if mx[r][c] is None: is_fn[r][c]=True; mx[r][c]=0
        r2,c2=sz-1-i,8
        if mx[r2][c2] is None: is_fn[r2][c2]=True; mx[r2][c2]=0
    msgbits=''.join(format(cw,'08b') for cw in msg)
    bi=0; col=sz-1; up=True
    while col>=0:
        if col==6: col-=1; continue
        rows=range(sz-1,-1,-1) if up else range(sz)
        for row in rows:
            for dc in(0,-1):
                cc=col+dc
                if 0<=cc<sz and mx[row][cc] is None:
                    mx[row][cc]=int(msgbits[bi]) if bi<len(msgbits) else 0; bi+=1
        col-=2; up=not up
    for r in range(sz):
        for c in range(sz):
            if not is_fn[r][c] and(r+c)%2==0: mx[r][c]^=1
    fmt_bits="101010000010010"
    fph=[(8,0),(8,1),(8,2),(8,3),(8,4),(8,5),(8,7),(8,8),(7,8),(5,8),(4,8),(3,8),(2,8),(1,8),(0,8)]
    for i,(r,c) in enumerate(fph): mx[r][c]=int(fmt_bits[i])
    fv1=[(sz-1,8),(sz-2,8),(sz-3,8),(sz-4,8),(sz-5,8),(sz-6,8),(sz-7,8)]
    for i,(r,c) in enumerate(fv1): mx[r][c]=int(fmt_bits[i])
    fv2=[(8,sz-8),(8,sz-7),(8,sz-6),(8,sz-5),(8,sz-4),(8,sz-3),(8,sz-2),(8,sz-1)]
    for i,(r,c) in enumerate(fv2): mx[r][c]=int(fmt_bits[7+i])
    total=(sz+border*2)*box
    rects=[]
    for r in range(sz):
        for c in range(sz):
            if mx[r][c]:
                x,y=(c+border)*box,(r+border)*box
                rects.append(f'<rect x="{x}" y="{y}" width="{box}" height="{box}"/>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total} {total}" width="{total}" height="{total}"><rect width="{total}" height="{total}" fill="white"/>{"".join(rects)}</svg>'


# ─── Thumbs ───

THUMB_DIR = None
def ensure_thumb_dir():
    global THUMB_DIR
    if state.folder:
        THUMB_DIR = os.path.join(state.folder, ".oceanshare_thumbs")
        os.makedirs(THUMB_DIR, exist_ok=True)

def get_thumb_path(rel_path):
    if THUMB_DIR:
        safe = rel_path.replace(os.sep, "_").replace("/", "_")
        return os.path.join(THUMB_DIR, safe + "_thumb.png")
    return None

def _gen_all_thumbs():
    if not state.folder: return
    files = scan_all_files(state.folder)
    for fi in files:
        if fi["type"] in ("video", "image"):
            thumb = get_thumb_path(fi["rel"])
            if thumb and not os.path.exists(thumb):
                fp = os.path.join(state.folder, fi["rel"])
                generate_thumbnail(fp, thumb)


# ─── Página de download (celular) ───

def _build_download_script(files):
    """JavaScript separado pra não conflitar com f-strings"""
    # Lista de arquivos pra download all
    file_list_js = json.dumps([{"name": f["name"], "rel": f["rel"], "size": f.get("bytes",0)} for f in files])
    return """<script>
var FILE_LIST = """ + file_list_js + """;

async function uploadFiles(fls) {
  var prog = document.getElementById('upload-progress');
  var bar = document.getElementById('up-bar');
  var txt = document.getElementById('up-text');
  prog.style.display = 'block';
  for (var i = 0; i < fls.length; i++) {
    var f = fls[i];
    txt.textContent = 'Enviando ' + (i+1) + '/' + fls.length + ': ' + f.name;
    bar.style.width = '0%';
    await new Promise(function(resolve) {
      var xhr = new XMLHttpRequest();
      xhr.open('POST', '/upload');
      xhr.upload.onprogress = function(e) {
        if (e.lengthComputable) {
          var pct = Math.round(e.loaded / e.total * 100);
          bar.style.width = pct + '%';
          txt.textContent = 'Enviando ' + (i+1) + '/' + fls.length + ': ' + f.name + ' (' + pct + '%)';
        }
      };
      xhr.onload = function() { resolve(); };
      xhr.onerror = function() { txt.textContent = 'Erro ao enviar ' + f.name; resolve(); };
      var form = new FormData();
      form.append('file', f);
      xhr.send(form);
    });
  }
  bar.style.width = '100%';
  txt.textContent = '\\u2705 ' + fls.length + ' arquivo(s) enviado(s)!';
  setTimeout(function() { prog.style.display = 'none'; }, 4000);
}

var SELECTED = {};

function updateSelectionUI() {
  var n = 0;
  for (var k in SELECTED) if (SELECTED[k]) n++;
  var numEl = document.getElementById('sel-num');
  if (numEl) numEl.textContent = n;
  var btn = document.getElementById('btn-dl-sel');
  if (btn) btn.disabled = (n === 0);
  var all = document.getElementById('btn-all');
  if (all) all.textContent = (n === FILE_LIST.length && n > 0) ? 'Desmarcar tudo' : 'Marcar tudo';
}

function setCardSelected(rel, on) {
  var cards = document.querySelectorAll('.card');
  for (var i = 0; i < cards.length; i++) {
    if (cards[i].getAttribute('data-rel') === rel) {
      if (on) cards[i].classList.add('selected');
      else cards[i].classList.remove('selected');
    }
  }
}

function onItemToggle(inp) {
  var rel = inp.getAttribute('data-rel');
  SELECTED[rel] = inp.checked;
  setCardSelected(rel, inp.checked);
  updateSelectionUI();
}

function toggleAll() {
  var n = 0;
  for (var k in SELECTED) if (SELECTED[k]) n++;
  var markAll = !(n === FILE_LIST.length && n > 0);
  var chks = document.querySelectorAll('.file-chk');
  for (var i = 0; i < chks.length; i++) {
    chks[i].checked = markAll;
    var rel = chks[i].getAttribute('data-rel');
    SELECTED[rel] = markAll;
    setCardSelected(rel, markAll);
  }
  updateSelectionUI();
}

async function downloadSelected(e) {
  e.preventDefault();
  var picks = [];
  for (var i = 0; i < FILE_LIST.length; i++) {
    if (SELECTED[FILE_LIST[i].rel]) picks.push(FILE_LIST[i]);
  }
  if (picks.length === 0) return;
  var prog = document.getElementById('dl-progress');
  var bar = document.getElementById('dl-bar');
  var txt = document.getElementById('dl-text');
  prog.style.display = 'block';
  for (var i = 0; i < picks.length; i++) {
    var f = picks[i];
    txt.textContent = 'Baixando ' + (i+1) + '/' + picks.length + ': ' + f.name;
    bar.style.width = Math.round((i / picks.length) * 100) + '%';
    var a = document.createElement('a');
    a.href = '/dl/' + encodeURIComponent(f.rel);
    a.download = f.name;
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    await new Promise(function(r) { setTimeout(r, 800); });
  }
  bar.style.width = '100%';
  txt.textContent = '\\u2705 ' + picks.length + ' download(s) iniciado(s)!';
  setTimeout(function() { prog.style.display = 'none'; }, 4000);
}

for (var i = 0; i < FILE_LIST.length; i++) SELECTED[FILE_LIST[i].rel] = false;
updateSelectionUI();
</script></body></html>"""


def build_download_page(subpath=""):
    target_folder = os.path.join(state.folder, subpath) if subpath else state.folder
    target_folder = os.path.realpath(target_folder)
    if not target_folder.startswith(os.path.realpath(state.folder)):
        return "<h1>Acesso negado</h1>"
    dirs, files = scan_dir(target_folder, subpath)
    total = len(dirs) + len(files)
    cards = ""
    if subpath:
        parent = os.path.dirname(subpath)
        parent_enc = urllib.parse.quote(parent) if parent else ""
        parent_href = f"/browse/{parent_enc}" if parent else "/"
        cards += f'<a class="back-btn" href="{parent_href}"><span class="back-arrow">←</span> Voltar</a>'
    for d in dirs:
        enc = urllib.parse.quote(d["rel"])
        cards += f'''<a class="folder-card" href="/browse/{enc}">
            <span class="folder-icon">📁</span>
            <span class="folder-info"><span class="folder-name">{d["name"]}</span>
            <span class="folder-count">{d["count"]} arquivo{"s" if d["count"]!=1 else ""}</span></span>
            <span class="folder-arrow">›</span></a>'''
    for fi in files:
        enc = urllib.parse.quote(fi["rel"])
        rel_attr = fi["rel"].replace('"', '&quot;')
        icon = "🎬" if fi["type"]=="video" else "🖼️" if fi["type"]=="image" else "📄"
        if fi["type"] == "video":
            preview = f'''<div class="preview" id="p_{hash(fi["rel"])%99999}" onclick="var el=this;if(el.dataset.playing)return;el.dataset.playing='1';el.classList.add('playing');var v=document.createElement('video');v.src='/dl/{enc}';v.controls=true;v.autoplay=true;v.playsInline=true;v.setAttribute('playsinline','');v.setAttribute('webkit-playsinline','');v.style.cssText='width:100%;max-height:70vh;object-fit:contain;background:#000;border-radius:12px';el.innerHTML='';el.appendChild(v);el.onclick=null">
                <img src="/thumb/{enc}" onerror="this.parentElement.innerHTML='{icon}'" loading="lazy">
                <div class="play-overlay"><div class="play-circle">▶</div></div></div>'''
        elif fi["type"] == "image":
            preview = f'<div class="preview"><img src="/thumb/{enc}" onerror="this.parentElement.innerHTML=\'🖼️\'" loading="lazy"></div>'
        else:
            preview = f'<div class="preview no-img">{icon}</div>'
        cards += f'''<div class="card" data-rel="{rel_attr}">
            <label class="select-chk" onclick="event.stopPropagation()"><input type="checkbox" class="file-chk" data-rel="{rel_attr}" onchange="onItemToggle(this)"><span class="chk-box"></span></label>
            {preview}
            <a class="dl" href="/dl/{enc}" download>
            <span class="fname">{fi["name"]}</span>
            <span class="meta"><span class="fdate">{fi["date"]}</span><span class="fsize">{fi["size"]}</span>
            <span class="dl-btn">⬇ Baixar</span></span></a></div>'''
    title = os.path.basename(subpath) if subpath else "OceanShare"
    subtitle = f"📁 {subpath}" if subpath else "Toque pra assistir ou baixar"
    return f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#080c18">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>🌊 {title}</title><style>
*{{margin:0;padding:0;box-sizing:border-box}}
:root{{--accent:#38bdf8;--purple:#a78bfa;--green:#4ade80;--glass:rgba(255,255,255,0.05);--glass-b:rgba(255,255,255,0.08)}}
body{{font-family:-apple-system,BlinkMacSystemFont,system-ui,sans-serif;
background:#080c18;min-height:100vh;min-height:100dvh;color:#e2e8f0;
padding:env(safe-area-inset-top) 16px calc(env(safe-area-inset-bottom) + 90px) 16px;
overflow-x:hidden}}

/* background orbs */
body::before,body::after{{content:'';position:fixed;border-radius:50%;filter:blur(80px);opacity:.18;z-index:0;pointer-events:none}}
body::before{{width:340px;height:340px;background:radial-gradient(circle,#0ea5e9,#1e3a5f);top:-80px;left:-60px}}
body::after{{width:280px;height:280px;background:radial-gradient(circle,#7c3aed,#312e81);bottom:60px;right:-40px}}

.c{{max-width:500px;margin:0 auto;position:relative;z-index:1}}

/* header */
.header{{text-align:center;padding:20px 0 16px}}
.logo{{display:inline-flex;align-items:center;gap:10px;margin-bottom:6px}}
.logo-icon{{width:44px;height:44px;border-radius:13px;overflow:hidden;
box-shadow:0 4px 16px rgba(14,165,233,.3);border:1px solid rgba(255,255,255,.08)}}
.logo-icon img{{width:100%;height:100%;object-fit:cover;display:block}}
.logo h1{{font-size:1.5em;font-weight:700;letter-spacing:-.5px}}
.sub{{color:#64748b;font-size:.82em;margin-top:2px}}
.count-pill{{display:inline-flex;align-items:center;gap:6px;
margin-top:10px;padding:5px 14px;border-radius:100px;
background:rgba(56,189,248,.08);border:1px solid rgba(56,189,248,.15);
color:var(--accent);font-size:.8em;font-weight:600}}

/* back button */
.back-btn{{display:inline-flex;align-items:center;gap:6px;color:var(--accent);
text-decoration:none;padding:10px 0;font-size:.9em;font-weight:600;margin-bottom:8px}}
.back-btn:active{{opacity:.6}}
.back-arrow{{display:inline-flex;align-items:center;justify-content:center;
width:28px;height:28px;border-radius:9px;background:rgba(56,189,248,.1);font-size:.85em}}

/* folder cards */
.folder-card{{display:flex;align-items:center;background:var(--glass);
backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);
border-radius:16px;padding:16px;margin-bottom:10px;text-decoration:none;color:#e2e8f0;
border:1px solid var(--glass-b);gap:14px;transition:all .15s}}
.folder-card:active{{background:rgba(255,255,255,.1);transform:scale(.98)}}
.folder-icon{{font-size:2em;flex-shrink:0}}
.folder-info{{flex:1;display:flex;flex-direction:column;gap:2px}}
.folder-name{{font-size:1em;font-weight:600}}
.folder-count{{font-size:.8em;color:#94a3b8}}
.folder-arrow{{font-size:1.5em;color:#475569;flex-shrink:0}}

/* file cards */
.card{{position:relative;background:var(--glass);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);
border-radius:18px;margin-bottom:12px;overflow:hidden;border:1px solid var(--glass-b);
transition:all .15s}}
.card.selected{{border-color:rgba(56,189,248,.55);box-shadow:0 0 0 1px rgba(56,189,248,.35) inset}}

/* select checkbox on cards */
.select-chk{{position:absolute;top:10px;left:10px;z-index:5;cursor:pointer;
width:32px;height:32px;display:flex;align-items:center;justify-content:center}}
.select-chk input{{position:absolute;opacity:0;width:0;height:0;pointer-events:none}}
.chk-box{{width:26px;height:26px;border-radius:8px;background:rgba(8,12,24,.65);
backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);
border:1.5px solid rgba(255,255,255,.55);display:flex;align-items:center;justify-content:center;
transition:.15s}}
.chk-box::after{{content:"";width:12px;height:7px;border-left:2.2px solid #fff;border-bottom:2.2px solid #fff;
transform:rotate(-45deg) translateY(-2px);opacity:0;transition:.15s}}
.select-chk input:checked + .chk-box{{background:var(--accent);border-color:var(--accent);
box-shadow:0 2px 10px rgba(56,189,248,.4)}}
.select-chk input:checked + .chk-box::after{{opacity:1}}

/* selection bar */
.sel-bar{{display:flex;align-items:center;gap:10px;padding:12px 14px;margin-bottom:12px;
background:rgba(56,189,248,.06);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);
border:1px solid rgba(56,189,248,.18);border-radius:14px}}
.sel-count{{flex:1;font-size:.82em;font-weight:600;color:#e2e8f0}}
.sel-count span{{color:var(--accent)}}
.sel-btn{{background:rgba(255,255,255,.05);color:#e2e8f0;border:1px solid rgba(255,255,255,.12);
border-radius:10px;padding:8px 12px;font-size:.78em;font-weight:600;cursor:pointer;transition:.15s}}
.sel-btn:active{{transform:scale(.96)}}
.sel-btn.primary{{background:linear-gradient(135deg,#0ea5e9,#0284c7);border:none;color:#fff;
box-shadow:0 2px 10px rgba(14,165,233,.3)}}
.sel-btn:disabled{{opacity:.4;cursor:default}}

.preview{{position:relative;width:100%;aspect-ratio:16/9;background:#0c1022;
display:flex;align-items:center;justify-content:center;overflow:hidden;cursor:pointer;font-size:3em}}
.preview.playing{{aspect-ratio:auto;min-height:200px}}
.preview img{{width:100%;height:100%;object-fit:cover}}
.preview.no-img{{background:rgba(15,23,42,.8)}}
.play-overlay{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
background:linear-gradient(to top,rgba(0,0,0,.4),transparent);pointer-events:none}}
.play-circle{{width:52px;height:52px;border-radius:50%;
background:rgba(255,255,255,.18);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
border:1.5px solid rgba(255,255,255,.25);display:flex;align-items:center;justify-content:center;
font-size:.6em;color:#fff;padding-left:3px}}
.dl{{display:flex;flex-wrap:wrap;align-items:center;padding:14px 16px;text-decoration:none;color:#e2e8f0;gap:4px}}
.dl:active{{background:rgba(255,255,255,.04)}}
.fname{{width:100%;font-size:.88em;font-weight:500;word-break:break-all}}
.meta{{display:flex;width:100%;justify-content:space-between;align-items:center;margin-top:5px}}
.fsize{{color:#94a3b8;font-size:.78em}}
.fdate{{color:#64748b;font-size:.75em}}
.dl-btn{{background:linear-gradient(135deg,#0ea5e9,#0284c7);color:white;padding:7px 16px;
border-radius:10px;font-size:.78em;font-weight:600;box-shadow:0 2px 10px rgba(14,165,233,.25)}}
.empty{{text-align:center;color:#94a3b8;margin-top:50px;font-size:.9em}}

/* download all */
.dl-all{{display:flex;align-items:center;justify-content:center;gap:8px;
background:rgba(56,189,248,.06);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
color:var(--accent);padding:14px;border-radius:14px;font-size:.92em;font-weight:600;
text-decoration:none;border:1px solid rgba(56,189,248,.15);margin-bottom:14px;transition:.15s}}
.dl-all:active{{background:rgba(56,189,248,.12);transform:scale(.98)}}

/* progress bars */
.dl-progress,.upload-progress{{display:none;text-align:center;margin-top:8px}}
.dl-progress .prog-text,.upload-progress .prog-text{{font-size:.82em;margin-bottom:4px}}
.prog-bar{{width:100%;height:4px;background:rgba(255,255,255,.06);border-radius:2px;margin-top:6px;overflow:hidden}}
.prog-fill-dl{{height:100%;background:linear-gradient(90deg,var(--accent),#818cf8);border-radius:2px;transition:width .3s;width:0%}}
.prog-fill-up{{height:100%;background:linear-gradient(90deg,var(--purple),#c084fc);border-radius:2px;transition:width .3s;width:0%}}

/* upload bar */
.upload-bar{{position:fixed;bottom:0;left:0;right:0;
padding:12px 16px calc(env(safe-area-inset-bottom) + 12px);
background:rgba(8,12,24,.92);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);
border-top:1px solid rgba(255,255,255,.06);display:flex;flex-direction:column;align-items:center;z-index:100}}
.upload-btn{{background:linear-gradient(135deg,#8b5cf6,#7c3aed);color:white;
padding:14px 28px;border-radius:14px;font-size:.95em;font-weight:700;border:none;
cursor:pointer;width:100%;max-width:500px;box-shadow:0 4px 16px rgba(139,92,246,.3);transition:.15s}}
.upload-btn:active{{transform:scale(.97);box-shadow:0 2px 8px rgba(139,92,246,.4)}}
</style></head><body><div class="c">
<div class="header">
  <div class="logo"><div class="logo-icon"><img src="/app-icon" alt=""></div><h1>{title}</h1></div>
  <p class="sub">{subtitle}</p>
  <div class="count-pill">📂 {total} item{"s" if total!=1 else ""}</div>
</div>
{'''<div class="sel-bar"><div class="sel-count"><span id="sel-num">0</span> de ''' + str(len(files)) + ''' selecionado(s)</div><button class="sel-btn" id="btn-all" onclick="toggleAll()">Marcar tudo</button><button class="sel-btn primary" id="btn-dl-sel" onclick="downloadSelected(event)" disabled>⬇ Baixar</button></div><div class="dl-progress" id="dl-progress"><span class="prog-text" id="dl-text"></span><div class="prog-bar"><div class="prog-fill-dl" id="dl-bar"></div></div></div>''' if files else ''}
{cards if cards else '<p class="empty">Nenhum arquivo encontrado.</p>'}
</div>
<div class="upload-bar">
  <button class="upload-btn" onclick="document.getElementById('file-input').click()">📤 Enviar arquivo para o Mac</button>
  <input type="file" id="file-input" multiple accept="*/*" style="display:none" onchange="uploadFiles(this.files)">
  <div class="upload-progress" id="upload-progress">
    <span class="prog-text" id="up-text"></span>
    <div class="prog-bar"><div class="prog-fill-up" id="up-bar"></div></div>
  </div>
</div>
""" + _build_download_script(files)


# ─── Handler HTTP unificado ───

class UnifiedHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        client_ip = self.client_address[0]
        is_admin = client_ip in ('127.0.0.1', '::1')

        if self.path == '/app-icon':
            self._serve_app_icon(); return
        if is_admin and self.path == '/admin':
            self._send_html(build_admin_page())
        elif is_admin and self.path.startswith('/api/files'):
            self._api_files()
        elif is_admin and self.path == '/api/transfers':
            self._api_transfers()
        elif is_admin and self.path == '/api/received':
            self._api_received()
        elif is_admin and self.path.startswith('/api/'):
            pass  # POST only
        elif not is_admin or self.path.startswith(('/browse', '/dl/', '/thumb/')):
            if not state.serving and not is_admin:
                self._send_html("<h1>OceanShare ainda não está ligado</h1>"); return
            if self.path in ('/', ''):
                if is_admin:
                    self._send_html(build_admin_page())
                else:
                    self._send_html(build_download_page(""))
            elif self.path.startswith('/browse/'):
                subpath = urllib.parse.unquote(self.path[8:]).strip('/')
                self._send_html(build_download_page(subpath))
            elif self.path.startswith('/browse'):
                self._send_html(build_download_page(""))
            elif self.path.startswith('/thumb/'):
                self._serve_thumb(self.path[7:])
            elif self.path.startswith('/dl/'):
                self._serve_file(self.path[4:])
            elif self.path == '/favicon.ico':
                self.send_response(204); self.end_headers()
            else:
                self.send_error(404)
        elif self.path in ('/', ''):
            self._send_html(build_admin_page())
        elif self.path == '/favicon.ico':
            self.send_response(204); self.end_headers()
        else:
            self.send_error(404)

    def do_POST(self):
        path = self.path; res = {}
        if path == '/upload':
            self._handle_upload(); return
        if path == '/api/choose_folder':
            folder = choose_folder_dialog()
            if folder:
                state.folder = folder
                ensure_thumb_dir()
                threading.Thread(target=_gen_all_thumbs, daemon=True).start()
                all_files = scan_all_files(folder)
                res = {"folder": folder, "count": len(all_files)}
            else:
                res = {"folder": None}
        elif path == '/api/start':
            if state.folder:
                state.serving = True
                ensure_thumb_dir()
                ips = get_local_ips()
                url = f"http://{ips[0]}:{PORT}"
                alts = [{"url": f"http://{ip}:{PORT}", "qr": make_qr_svg(f"http://{ip}:{PORT}") or ""} for ip in ips[1:]]
                qr = make_qr_svg(url) or ""
                threading.Thread(target=_gen_all_thumbs, daemon=True).start()
                res = {"ok": True, "url": url, "qr": qr, "alts": alts}
            else:
                res = {"ok": False, "error": "Escolha uma pasta primeiro"}
        elif path == '/api/stop':
            state.serving = False
            res = {"ok": True}
        elif path == '/api/open_folder':
            cl = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(cl)) if cl > 0 else {}
            target = body.get("target", "main")
            if state.folder:
                if target == "received":
                    folder = os.path.join(state.folder, "_recebidos")
                    os.makedirs(folder, exist_ok=True)
                else:
                    folder = state.folder
                if sys.platform == 'darwin':
                    subprocess.Popen(["open", folder])
                elif sys.platform == 'win32':
                    os.startfile(folder)
                else:
                    subprocess.Popen(["xdg-open", folder])
                res = {"ok": True}
            else:
                res = {"ok": False}
        else:
            res = {"error": "not found"}
        d = json.dumps(res).encode()
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(d)))
        self.end_headers()
        self.wfile.write(d)

    def _send_html(self, html):
        d = html.encode() if isinstance(html, str) else html
        self.send_response(200)
        self.send_header('Content-Type','text/html;charset=utf-8')
        self.send_header('Content-Length',str(len(d)))
        self.end_headers()
        self.wfile.write(d)

    def _handle_upload(self):
        if not state.folder:
            self._send_json({"ok": False, "error": "Nenhuma pasta selecionada"})
            return
        content_type = self.headers.get('Content-Type', '')
        if 'multipart/form-data' not in content_type:
            self._send_json({"ok": False, "error": "Formato inválido"})
            return
        # Parse boundary
        boundary = content_type.split('boundary=')[1].strip()
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        # Parse multipart
        boundary_bytes = boundary.encode()
        parts = body.split(b'--' + boundary_bytes)
        upload_dir = os.path.join(state.folder, "_recebidos")
        os.makedirs(upload_dir, exist_ok=True)
        saved = []
        for part in parts:
            if b'filename="' not in part: continue
            # Extract filename
            header_end = part.find(b'\r\n\r\n')
            if header_end < 0: continue
            header = part[:header_end].decode('utf-8', errors='replace')
            file_data = part[header_end+4:]
            if file_data.endswith(b'\r\n'): file_data = file_data[:-2]
            # Get filename
            fn_start = header.find('filename="') + 10
            fn_end = header.find('"', fn_start)
            filename = header[fn_start:fn_end]
            if not filename: continue
            # Sanitize
            filename = os.path.basename(filename)
            filepath = os.path.join(upload_dir, filename)
            # Avoid overwrite
            base, ext = os.path.splitext(filename)
            counter = 1
            while os.path.exists(filepath):
                filepath = os.path.join(upload_dir, f"{base}_{counter}{ext}")
                counter += 1
            with open(filepath, 'wb') as f:
                f.write(file_data)
            saved.append(filename)
            state.add_received(filename, fmt_size(len(file_data)))
            t = state.add_transfer(filename, fmt_size(len(file_data)), "upload")
            state.finish_transfer(t)
        self._send_json({"ok": True, "files": saved, "count": len(saved)})

    def _send_json(self, obj):
        d = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(d)))
        self.end_headers()
        self.wfile.write(d)

    def _api_files(self):
        subpath = ""
        if '?' in self.path:
            qs = urllib.parse.parse_qs(self.path.split('?', 1)[1])
            subpath = qs.get('path', [''])[0]
        if not state.folder:
            self._send_json({"dirs": [], "files": []})
            return
        target = os.path.join(state.folder, subpath) if subpath else state.folder
        target = os.path.realpath(target)
        if not target.startswith(os.path.realpath(state.folder)):
            self._send_json({"dirs": [], "files": []})
            return
        dirs, files = scan_dir(target, subpath)
        self._send_json({"dirs": dirs, "files": files, "subpath": subpath})

    def _api_transfers(self):
        with state._lock:
            self._send_json({"transfers": list(state.transfers)})

    def _api_received(self):
        received = []
        if state.folder:
            rdir = os.path.join(state.folder, "_recebidos")
            if os.path.isdir(rdir):
                for f in sorted(os.listdir(rdir), key=lambda x: os.path.getmtime(os.path.join(rdir, x)), reverse=True):
                    if f.startswith('.'): continue
                    fp = os.path.join(rdir, f)
                    if os.path.isfile(fp):
                        received.append({"name": f, "size": fmt_size(os.path.getsize(fp)),
                                         "ts": os.path.getmtime(fp)})
        self._send_json({"received": received})

    def _serve_file(self, rel_encoded):
        rel = urllib.parse.unquote(rel_encoded)
        fp = os.path.realpath(os.path.join(state.folder, rel))
        if not fp.startswith(os.path.realpath(state.folder)): self.send_error(403); return
        if not os.path.isfile(fp): self.send_error(404); return
        sz = os.path.getsize(fp)
        ext = os.path.splitext(fp)[1].lower()
        ct = {
            # video
            '.mp4':'video/mp4','.mov':'video/quicktime','.avi':'video/x-msvideo',
            '.mkv':'video/x-matroska','.m4v':'video/x-m4v','.webm':'video/webm',
            '.wmv':'video/x-ms-wmv','.mpg':'video/mpeg','.mpeg':'video/mpeg',
            '.3gp':'video/3gpp','.flv':'video/x-flv','.ts':'video/mp2t','.ogv':'video/ogg',
            # image
            '.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png','.gif':'image/gif',
            '.heic':'image/heic','.heif':'image/heif','.webp':'image/webp','.avif':'image/avif',
            '.tiff':'image/tiff','.tif':'image/tiff','.bmp':'image/bmp','.svg':'image/svg+xml',
            '.ico':'image/x-icon','.jxl':'image/jxl',
            '.dng':'image/x-adobe-dng','.raw':'image/x-raw','.raf':'image/x-fuji-raf',
            '.cr2':'image/x-canon-cr2','.cr3':'image/x-canon-cr3','.nef':'image/x-nikon-nef',
            '.arw':'image/x-sony-arw','.orf':'image/x-olympus-orf','.rw2':'image/x-panasonic-rw2',
            # audio
            '.mp3':'audio/mpeg','.wav':'audio/wav','.flac':'audio/flac','.aac':'audio/aac',
            '.ogg':'audio/ogg','.m4a':'audio/mp4','.opus':'audio/opus','.wma':'audio/x-ms-wma',
            # docs / archives
            '.pdf':'application/pdf','.zip':'application/zip','.rar':'application/x-rar-compressed',
            '.7z':'application/x-7z-compressed','.tar':'application/x-tar','.gz':'application/gzip',
            '.bz2':'application/x-bzip2','.xz':'application/x-xz','.iso':'application/x-iso9660-image',
            '.dmg':'application/x-apple-diskimage',
            '.doc':'application/msword','.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.xls':'application/vnd.ms-excel','.xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            '.ppt':'application/vnd.ms-powerpoint','.pptx':'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            '.txt':'text/plain;charset=utf-8','.csv':'text/csv;charset=utf-8','.rtf':'application/rtf',
            '.json':'application/json','.xml':'application/xml','.epub':'application/epub+zip',
        }.get(ext, 'application/octet-stream')
        name = os.path.basename(fp)
        client_ip = self.client_address[0]
        is_admin = client_ip in ('127.0.0.1', '::1')
        transfer = None
        rh = self.headers.get('Range')
        if rh:
            try:
                rv = rh.replace('bytes=', '')
                ss, es = rv.split('-')
                start = int(ss); end = int(es) if es else sz-1; end = min(end, sz-1)
                length = end - start + 1
                self.send_response(206)
                self.send_header('Content-Type', ct)
                self.send_header('Content-Range', f'bytes {start}-{end}/{sz}')
                self.send_header('Content-Length', str(length))
                self.send_header('Accept-Ranges', 'bytes')
                self.end_headers()
                with open(fp, 'rb') as f:
                    f.seek(start); rem = length
                    while rem > 0:
                        chunk = f.read(min(1024*1024, rem))
                        if not chunk: break
                        try: self.wfile.write(chunk)
                        except: break
                        rem -= len(chunk)
                return
            except: pass
        self.send_response(200)
        self.send_header('Content-Type', ct)
        self.send_header('Content-Length', str(sz))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Disposition', f'attachment; filename="{name}"')
        self.end_headers()
        if not is_admin:
            transfer = state.add_transfer(name, fmt_size(sz), "download")
        sent = 0
        with open(fp, 'rb') as f:
            while True:
                chunk = f.read(1024*1024)
                if not chunk: break
                try: self.wfile.write(chunk)
                except: break
                sent += len(chunk)
                if transfer and sz > 0:
                    transfer["progress"] = min(100, int(sent * 100 / sz))
        if transfer:
            state.finish_transfer(transfer)

    def _serve_app_icon(self):
        if not os.path.isfile(ICON_PATH):
            self.send_error(404); return
        sz = os.path.getsize(ICON_PATH)
        self.send_response(200)
        self.send_header('Content-Type', 'image/jpeg')
        self.send_header('Content-Length', str(sz))
        self.send_header('Cache-Control', 'max-age=86400')
        self.end_headers()
        with open(ICON_PATH, 'rb') as f:
            self.wfile.write(f.read())

    def _serve_thumb(self, rel_encoded):
        rel = urllib.parse.unquote(rel_encoded)
        fp = os.path.realpath(os.path.join(state.folder, rel))
        if not fp.startswith(os.path.realpath(state.folder)): self.send_error(403); return
        if not os.path.isfile(fp): self.send_error(404); return
        thumb = get_thumb_path(rel)
        if not thumb: self.send_error(404); return
        if not os.path.exists(thumb): generate_thumbnail(fp, thumb)
        if os.path.exists(thumb):
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            tsz = os.path.getsize(thumb)
            self.send_header('Content-Length', str(tsz))
            self.send_header('Cache-Control', 'max-age=3600')
            self.end_headers()
            with open(thumb, 'rb') as f: self.wfile.write(f.read())
        else:
            self.send_error(404)


# ─── Página admin (dentro do pywebview) ───

def build_admin_page():
    return r"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OceanShare</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --glass-bg:rgba(255,255,255,0.04);
  --glass-border:rgba(255,255,255,0.08);
  --glass-hover:rgba(255,255,255,0.07);
  --text-1:#f1f5f9;--text-2:#94a3b8;--text-3:#64748b;
  --accent:#38bdf8;--green:#4ade80;--purple:#a78bfa;--red:#f87171;
}
body{font-family:-apple-system,BlinkMacSystemFont,'SF Pro Display',system-ui,sans-serif;
background:#080c18;color:var(--text-1);height:100vh;overflow:hidden;
-webkit-user-select:none;user-select:none}

.bg{position:fixed;inset:0;z-index:0;overflow:hidden;pointer-events:none}
.bg .orb{position:absolute;border-radius:50%;filter:blur(90px);opacity:.22;animation:drift 22s ease-in-out infinite}
.bg .orb:nth-child(1){width:420px;height:420px;background:radial-gradient(circle,#1e3a5f,#0ea5e9);top:-100px;left:-60px}
.bg .orb:nth-child(2){width:340px;height:340px;background:radial-gradient(circle,#312e81,#7c3aed);bottom:-60px;right:-40px;animation-delay:-11s}
.bg .orb:nth-child(3){width:260px;height:260px;background:radial-gradient(circle,#064e3b,#10b981);top:40%;left:50%;animation-delay:-6s}
@keyframes drift{0%,100%{transform:translate(0,0) scale(1)}33%{transform:translate(30px,-25px) scale(1.06)}66%{transform:translate(-25px,18px) scale(.94)}}

/* Layout: 3 columns */
.shell{position:relative;z-index:1;display:grid;grid-template-columns:260px 1fr 280px;
gap:14px;height:100vh;padding:16px}

.glass{background:var(--glass-bg);backdrop-filter:blur(28px) saturate(1.3);
-webkit-backdrop-filter:blur(28px) saturate(1.3);
border:1px solid var(--glass-border);border-radius:18px}

/* ── Left panel ── */
.left{display:flex;flex-direction:column;gap:10px;padding:18px 16px;overflow:hidden}
.brand{display:flex;align-items:center;gap:10px}
.brand-icon{width:42px;height:42px;border-radius:12px;overflow:hidden;flex-shrink:0;
box-shadow:0 4px 16px rgba(14,165,233,.28);border:1px solid rgba(255,255,255,.08)}
.brand-icon img{width:100%;height:100%;object-fit:cover;display:block}
.brand h1{font-size:1.1em;font-weight:700;letter-spacing:-.3px}
.brand .ver{font-size:.6em;color:var(--text-3);font-weight:400;margin-left:4px}

.pill{display:inline-flex;align-items:center;gap:7px;padding:5px 12px;
border-radius:100px;font-size:.72em;font-weight:600;width:fit-content;transition:all .35s}
.pill.off{background:rgba(100,116,139,.1);color:var(--text-3);border:1px solid rgba(100,116,139,.15)}
.pill.on{background:rgba(74,222,128,.08);color:var(--green);border:1px solid rgba(74,222,128,.2)}
.dot{width:6px;height:6px;border-radius:50%;background:currentColor;transition:.3s}
.pill.on .dot{box-shadow:0 0 6px rgba(74,222,128,.5);animation:pulse 2s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}

.folder-card{background:rgba(255,255,255,.025);border-radius:12px;padding:12px 14px;
border:1px solid rgba(255,255,255,.045);display:flex;flex-direction:column;gap:4px;
cursor:pointer;transition:all .18s}
.folder-card:hover{background:rgba(255,255,255,.05);border-color:rgba(255,255,255,.08)}
.folder-card:active{transform:scale(.98)}
.folder-card .lbl{font-size:.65em;text-transform:uppercase;letter-spacing:.5px;color:var(--text-3);font-weight:600}
.folder-card .fpath{font-size:.78em;color:var(--text-2);word-break:break-all;line-height:1.35}
.folder-card .fcount{display:inline-flex;align-items:center;gap:4px;margin-top:2px;
padding:2px 8px;border-radius:6px;background:rgba(56,189,248,.08);
color:var(--accent);font-size:.72em;font-weight:600;width:fit-content}
.folder-card .actions{display:flex;gap:8px;margin-top:4px}
.folder-card .actions a{font-size:.7em;color:var(--accent);cursor:pointer;text-decoration:none;opacity:.7;transition:.15s}
.folder-card .actions a:hover{opacity:1;text-decoration:underline}
.folder-card .choose-hint{font-size:.72em;color:var(--accent);margin-top:2px;opacity:.7}

.btn{display:flex;width:100%;padding:10px 14px;border:none;border-radius:12px;font-size:.82em;
font-weight:600;cursor:pointer;transition:all .18s;align-items:center;justify-content:center;gap:7px}
.btn:active{transform:scale(.97)}.btn:disabled{opacity:.3;cursor:default;transform:none}
.btn-folder{background:rgba(255,255,255,.05);color:var(--text-1);border:1px solid rgba(255,255,255,.08)}
.btn-folder:hover:not(:disabled){background:var(--glass-hover)}
.btn-go{background:linear-gradient(135deg,#22c55e,#16a34a);color:#fff;box-shadow:0 3px 12px rgba(34,197,94,.25)}
.btn-stop{background:linear-gradient(135deg,#ef4444,#dc2626);color:#fff;box-shadow:0 3px 12px rgba(239,68,68,.25)}
.btn-open{background:rgba(255,255,255,.04);color:var(--text-2);border:1px solid rgba(255,255,255,.06);font-size:.72em;padding:7px 10px}
.btn-open:hover{color:var(--text-1);background:rgba(255,255,255,.07)}

/* QR section in left */
.qr-section{display:none;flex-direction:column;align-items:center;gap:10px;animation:fadeUp .4s ease}
.qr-section.on{display:flex}
@keyframes fadeUp{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
.qr-wrap{background:#fff;padding:10px;border-radius:14px;box-shadow:0 6px 28px rgba(0,0,0,.3)}
.qr-wrap svg{display:block;width:130px;height:130px}
.url-display{font-size:.95em;font-weight:800;
background:linear-gradient(135deg,#38bdf8,#818cf8);-webkit-background-clip:text;
-webkit-text-fill-color:transparent;background-clip:text;letter-spacing:-.3px;
-webkit-user-select:text;user-select:text}
.qr-hint{color:var(--text-3);font-size:.68em;line-height:1.4;text-align:center}
.alt-ips{font-size:.62em;color:var(--text-3);text-align:center;line-height:1.5;margin-top:-4px}
.alt-ips b{color:var(--text-2);font-weight:600}
.alt-ips a{color:var(--accent);cursor:pointer;text-decoration:none}
.alt-ips a:hover{text-decoration:underline}
.tip-toggle{font-size:.65em;color:var(--accent);cursor:pointer;text-align:center;margin-top:2px;user-select:none}
.tip-toggle:hover{text-decoration:underline}
.tip-box{display:none;font-size:.62em;color:var(--text-2);background:rgba(255,255,255,.03);
border:1px solid rgba(255,255,255,.06);border-radius:10px;padding:8px 10px;line-height:1.5;text-align:left}
.tip-box.on{display:block;animation:fadeUp .25s ease}
.tip-box ol{margin:0;padding-left:16px}
.tip-box li{margin-bottom:4px}
.tip-box b{color:var(--text-1)}

/* steps (offline) */
.steps-mini{display:flex;flex-direction:column;gap:8px;margin-top:auto}
.step-m{display:flex;align-items:center;gap:10px;text-align:left}
.step-m .sn{width:24px;height:24px;border-radius:8px;flex-shrink:0;
background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.07);
display:flex;align-items:center;justify-content:center;font-size:.68em;font-weight:700;color:var(--accent)}
.step-m .st{font-size:.74em;color:var(--text-2);line-height:1.3}
.step-m .st strong{color:var(--text-1)}

.left-footer{margin-top:auto;text-align:center;color:var(--text-3);font-size:.65em;line-height:1.4}

/* ── Center panel (file browser) ── */
.center{display:flex;flex-direction:column;overflow:hidden;padding:0}

.center-header{display:flex;align-items:center;justify-content:space-between;
padding:14px 16px 10px;flex-shrink:0}
.center-header h2{font-size:.82em;font-weight:600;color:var(--text-2)}
.breadcrumb{display:flex;align-items:center;gap:4px;font-size:.72em;color:var(--text-3)}
.breadcrumb a{color:var(--accent);text-decoration:none;cursor:pointer}
.breadcrumb a:hover{text-decoration:underline}
.refresh-btn{color:var(--accent);cursor:pointer;font-size:1.1em;opacity:.6;transition:.2s;line-height:1;padding:2px 4px;border-radius:4px}
.refresh-btn:hover{opacity:1;background:rgba(255,255,255,.06)}

.file-grid{flex:1;min-height:0;overflow-y:auto;padding:0 12px 14px;display:flex;flex-direction:column}
.file-grid::-webkit-scrollbar{width:5px}
.file-grid::-webkit-scrollbar-track{background:transparent}
.file-grid::-webkit-scrollbar-thumb{background:rgba(255,255,255,.1);border-radius:3px}

.file-item{display:flex;align-items:center;gap:10px;padding:6px 10px;
border-radius:8px;cursor:default;transition:background .12s;border-bottom:1px solid rgba(255,255,255,.04)}
.file-item:hover{background:rgba(255,255,255,.05)}
.file-item.is-folder{cursor:pointer}
.file-thumb{width:32px;height:32px;border-radius:5px;background:#0c1022;display:flex;
align-items:center;justify-content:center;overflow:hidden;flex-shrink:0}
.file-thumb img{width:100%;height:100%;object-fit:cover}
.file-thumb .icon{font-size:1.1em}
.file-meta{flex:1;min-width:0;display:flex;align-items:center;gap:8px}
.file-meta .fn{font-size:.78em;font-weight:500;overflow:hidden;text-overflow:ellipsis;
white-space:nowrap;flex:1;min-width:0}
.file-meta .fd{font-size:.68em;color:var(--text-3);display:flex;gap:12px;flex-shrink:0}
.folder-badge{font-size:.65em;color:var(--text-3)}

.empty-state{display:flex;flex-direction:column;align-items:center;justify-content:center;
height:100%;width:100%;grid-column:1/-1;color:var(--text-3);gap:8px;text-align:center;padding:30px}
.empty-state .em-icon{font-size:2.5em;opacity:.5}
.empty-state .em-text{font-size:.82em}
.empty-state .em-sub{font-size:.72em;line-height:1.5}

/* ── Right panel (transfers + received) ── */
.right{display:flex;flex-direction:column;overflow:hidden;padding:0}
.tab-bar{display:flex;border-bottom:1px solid rgba(255,255,255,.06);flex-shrink:0}
.tab{flex:1;padding:12px 8px;text-align:center;font-size:.72em;font-weight:600;
color:var(--text-3);cursor:pointer;border-bottom:2px solid transparent;transition:.2s}
.tab.active{color:var(--accent);border-bottom-color:var(--accent)}
.tab:hover:not(.active){color:var(--text-2)}
.tab-badge{display:inline-flex;align-items:center;justify-content:center;
min-width:16px;height:16px;padding:0 5px;border-radius:8px;font-size:.65em;
font-weight:700;margin-left:4px}
.tab.active .tab-badge{background:rgba(56,189,248,.15);color:var(--accent)}
.tab:not(.active) .tab-badge{background:rgba(255,255,255,.06);color:var(--text-3)}

.tab-content{flex:1;overflow-y:auto;padding:10px 12px}
.tab-content::-webkit-scrollbar{width:5px}
.tab-content::-webkit-scrollbar-track{background:transparent}
.tab-content::-webkit-scrollbar-thumb{background:rgba(255,255,255,.1);border-radius:3px}

.transfer-item{display:flex;align-items:center;gap:10px;padding:9px 10px;
border-radius:10px;margin-bottom:6px;background:rgba(255,255,255,.02);
border:1px solid rgba(255,255,255,.04);transition:.15s}
.transfer-item:hover{background:rgba(255,255,255,.04)}
.t-icon{font-size:1.1em;flex-shrink:0}
.t-info{flex:1;min-width:0}
.t-name{font-size:.74em;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.t-detail{font-size:.62em;color:var(--text-3);display:flex;align-items:center;gap:6px;margin-top:2px}
.t-bar{width:50px;height:3px;background:rgba(255,255,255,.08);border-radius:2px;overflow:hidden}
.t-bar-fill{height:100%;border-radius:2px;transition:width .3s}
.t-bar-fill.dl{background:var(--accent)}
.t-bar-fill.ul{background:var(--purple)}
.t-status{font-size:.62em;font-weight:600;flex-shrink:0;padding:3px 8px;border-radius:6px}
.t-status.done{background:rgba(74,222,128,.08);color:var(--green)}
.t-status.active{background:rgba(56,189,248,.08);color:var(--accent)}

.recv-item{display:flex;align-items:center;gap:10px;padding:9px 10px;
border-radius:10px;margin-bottom:6px;background:rgba(255,255,255,.02);
border:1px solid rgba(255,255,255,.04);transition:.15s}
.recv-item:hover{background:rgba(255,255,255,.04)}
.r-icon{font-size:1.1em;flex-shrink:0}
.r-info{flex:1;min-width:0}
.r-name{font-size:.74em;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.r-detail{font-size:.62em;color:var(--text-3);margin-top:2px}

.right-footer{flex-shrink:0;padding:10px 12px;border-top:1px solid rgba(255,255,255,.05)}

.empty-list{text-align:center;color:var(--text-3);padding:30px 10px;font-size:.75em}
.empty-list .el-icon{font-size:1.8em;opacity:.4;margin-bottom:6px}
</style></head><body>
<div class="bg"><div class="orb"></div><div class="orb"></div><div class="orb"></div></div>
<div class="shell">
  <!-- Left -->
  <div class="left glass">
    <div class="brand">
      <div class="brand-icon"><img src="/app-icon" alt=""></div>
      <div><h1>OceanShare<span class="ver">v5</span></h1></div>
    </div>
    <div id="status" class="pill off"><span class="dot"></span><span id="stxt">Desligado</span></div>
    <div class="folder-card" id="folder-card" onclick="chooseFolder()">
      <span class="lbl">📂 Pasta selecionada</span>
      <div id="folder-info"><span class="fpath" style="color:var(--text-3)">Nenhuma pasta selecionada</span><div class="choose-hint">Clique para escolher</div></div>
    </div>
    <button class="btn btn-go" id="btn-start" onclick="startServer()" disabled>▶ Ligar</button>

    <div id="qr-area" class="qr-section">
      <div class="qr-wrap" id="qr-svg"></div>
      <div id="url" class="url-display"></div>
      <p class="qr-hint">Escaneie com a câmera do celular</p>
      <div id="alt-ips" class="alt-ips" style="display:none"></div>
      <div class="tip-toggle" onclick="document.getElementById('tip-box').classList.toggle('on')">📱 Não abre no iPhone?</div>
      <div id="tip-box" class="tip-box">
        <ol>
          <li>Desligue <b>"Limitar Rastreamento IP"</b>: Ajustes → Wi-Fi → (i) da rede.</li>
          <li>Desligue <b>Modo HTTPS</b>: Ajustes → Apps → Safari → Avançado.</li>
          <li>Confirme mesma rede (2.4 e 5GHz podem estar separadas).</li>
          <li>Se tiver rede de convidados, desligue "isolamento de clientes" no roteador.</li>
        </ol>
      </div>
    </div>

    <div id="steps-area" class="steps-mini">
      <div class="step-m"><div class="sn">1</div><div class="st"><strong>Pasta</strong> → escolha os arquivos</div></div>
      <div class="step-m"><div class="sn">2</div><div class="st"><strong>Ligar</strong> → inicia o servidor</div></div>
      <div class="step-m"><div class="sn">3</div><div class="st"><strong>QR Code</strong> → escaneie no celular</div></div>
    </div>

    <div class="left-footer">Mesma rede Wi-Fi · Sem internet · Sem instalar nada</div>
  </div>

  <!-- Center: file browser -->
  <div class="center glass">
    <div class="center-header">
      <h2>📁 Arquivos</h2>
      <div style="display:flex;align-items:center;gap:8px">
        <div class="breadcrumb" id="breadcrumb"><span style="color:var(--text-3)">raiz</span></div>
        <a class="refresh-btn" onclick="loadFiles('',true)" title="Atualizar">⟳</a>
      </div>
    </div>
    <div id="file-grid" class="file-grid">
      <div class="empty-state">
        <div class="em-icon">📂</div>
        <div class="em-text">Selecione uma pasta</div>
        <div class="em-sub">Escolha uma pasta no painel esquerdo para ver os arquivos aqui</div>
      </div>
    </div>
  </div>

  <!-- Right: transfers -->
  <div class="right glass">
    <div class="tab-bar">
      <div class="tab active">↕ Transferências<span class="tab-badge" id="badge-transfers">0</span></div>
    </div>
    <div class="tab-content" id="tab-content-transfers">
      <div class="empty-list" id="empty-transfers"><div class="el-icon">↕</div>As transferências aparecerão aqui</div>
    </div>
    <div class="right-footer">
      <button class="btn btn-open" onclick="openFolder('received')">📂 Abrir pasta de recebidos</button>
    </div>
  </div>
</div>
<script>
var currentPath='';
var currentTab='transfers';
var pollTimer=null;

async function api(a,body){
  var opts={method:'POST'};
  if(body)opts.body=JSON.stringify(body);
  var r=await fetch('/api/'+a,opts);return r.json();
}
async function apiGet(a){var r=await fetch('/api/'+a);return r.json();}


async function chooseFolder(){
  var fc=document.getElementById('folder-card');
  var oldHtml=document.getElementById('folder-info').innerHTML;
  document.getElementById('folder-info').innerHTML='<span class="fpath" style="color:var(--text-3)">⏳ Escolhendo...</span>';
  fc.style.pointerEvents='none';fc.style.opacity='.6';
  var d=await api('choose_folder');
  fc.style.pointerEvents='';fc.style.opacity='';
  if(d.folder){
    document.getElementById('folder-info').innerHTML=
      '<div class="fpath">'+d.folder+'</div>'+
      '<div class="fcount">📹 '+d.count+' arquivo'+(d.count!==1?'s':'')+'</div>'+
      '<div class="actions"><a onclick="event.stopPropagation();chooseFolder()">Trocar pasta</a><a onclick="event.stopPropagation();openFolder(\'main\')">Ver no Finder</a></div>';
    document.getElementById('btn-start').disabled=false;
    currentPath='';loadFiles('',true);
    setTimeout(retryFailedThumbs,2000);
    setTimeout(retryFailedThumbs,5000);
  }else{
    document.getElementById('folder-info').innerHTML=oldHtml;
  }
}
async function startServer(){
  var b=document.getElementById('btn-start');
  b.disabled=true;b.innerHTML='⏳ Iniciando...';
  var d=await api('start');
  b.disabled=false;
  if(d.ok){
    document.getElementById('status').className='pill on';
    document.getElementById('stxt').textContent='Compartilhando';
    b.className='btn btn-stop';b.innerHTML='⏹ Desligar';b.onclick=stopServer;
    document.getElementById('folder-card').style.pointerEvents='none';document.getElementById('folder-card').style.opacity='.5';
    document.getElementById('steps-area').style.display='none';
    document.getElementById('qr-area').classList.add('on');
    document.getElementById('qr-svg').innerHTML=d.qr;
    document.getElementById('url').textContent=d.url;
    window._allUrls=[{url:d.url,qr:d.qr}].concat(d.alts||[]);
    renderAltIps(0);
    startPolling();
  }else{
    b.innerHTML='▶ Ligar';alert('Erro: '+(d.error||'desconhecido'));
  }
}
async function stopServer(){
  await api('stop');
  stopPolling();
  var b=document.getElementById('btn-start');
  document.getElementById('status').className='pill off';
  document.getElementById('stxt').textContent='Desligado';
  b.className='btn btn-go';b.innerHTML='▶ Ligar';b.onclick=startServer;
  document.getElementById('folder-card').style.pointerEvents='';document.getElementById('folder-card').style.opacity='';
  document.getElementById('steps-area').style.display='';
  document.getElementById('qr-area').classList.remove('on');
}

function openFolder(target){
  api('open_folder',{target:target});
}

function renderAltIps(activeIdx){
  var box=document.getElementById('alt-ips');
  var urls=window._allUrls||[];
  if(urls.length<=1){box.style.display='none';return;}
  var html=urls.map(function(u,i){
    var host=u.url.replace('http://','');
    if(i===activeIdx)return '<b>'+host+'</b>';
    return '<a onclick="swapUrl('+i+')">'+host+'</a>';
  }).join(' · ');
  box.innerHTML='<b>IPs disponíveis:</b><br>'+html;
  box.style.display='block';
}

function swapUrl(idx){
  var urls=window._allUrls||[];
  if(!urls[idx])return;
  document.getElementById('qr-svg').innerHTML=urls[idx].qr;
  document.getElementById('url').textContent=urls[idx].url;
  renderAltIps(idx);
}

function retryFailedThumbs(){
  var imgs=document.querySelectorAll('.file-thumb img[style*="display: none"],.file-thumb img[style*="display:none"]');
  imgs.forEach(function(img){
    img.style.display='';
    img.src=img.src.split('?')[0]+'?t='+Date.now();
  });
}

/* File browser */
async function loadFiles(path,resetScroll){
  if(typeof path==='undefined')path=currentPath;
  var changed=path!==currentPath;
  currentPath=path;
  var url='/api/files'+(path?'?path='+encodeURIComponent(path):'');
  var d=await(await fetch(url)).json();
  var grid=document.getElementById('file-grid');
  var bc=document.getElementById('breadcrumb');

  // breadcrumb
  var parts=path?path.split('/'):[];
  var bcHtml='<a onclick="loadFiles(&quot;&quot;)">raiz</a>';
  var accumulated='';
  for(var i=0;i<parts.length;i++){
    accumulated+=(i>0?'/':'')+parts[i];
    bcHtml+=' › <a onclick="loadFiles(&quot;'+accumulated.replace(/"/g,'&quot;')+'&quot;)">'+parts[i]+'</a>';
  }
  bc.innerHTML=bcHtml;

  if(!d.dirs&&!d.files){grid.innerHTML='<div class="empty-state"><div class="em-icon">📂</div><div class="em-text">Selecione uma pasta</div></div>';return;}

  var html='';
  if(path){
    var parent=path.split('/').slice(0,-1).join('/');
    html+='<div class="file-item is-folder" onclick="loadFiles(&quot;'+parent.replace(/"/g,'&quot;')+'&quot;)"><div class="file-thumb"><span class="icon">⬅️</span></div><div class="file-meta"><div class="fn">← Voltar</div></div></div>';
  }
  if(d.dirs)for(var i=0;i<d.dirs.length;i++){
    var dir=d.dirs[i];
    html+='<div class="file-item is-folder" onclick="loadFiles(&quot;'+dir.rel.replace(/"/g,'&quot;')+'&quot;)"><div class="file-thumb"><span class="icon">📁</span></div><div class="file-meta"><div class="fn">'+dir.name+'</div><div class="fd"><span class="folder-badge">'+dir.count+' arquivo'+(dir.count!==1?'s':'')+'</span></div></div></div>';
  }
  if(d.files)for(var i=0;i<d.files.length;i++){
    var f=d.files[i];
    var thumbUrl=f.type==='other'?'':'/thumb/'+encodeURIComponent(f.rel);
    var icon=f.type==='video'?'🎬':f.type==='image'?'🖼️':'📄';
    var imgHtml=thumbUrl?'<img src="'+thumbUrl+'" onerror="this.style.display=&quot;none&quot;;this.nextElementSibling.style.display=&quot;flex&quot;" loading="lazy"><span class="icon" style="display:none">'+icon+'</span>':'<span class="icon">'+icon+'</span>';
    html+='<div class="file-item"><div class="file-thumb">'+imgHtml+'</div><div class="file-meta"><div class="fn" title="'+f.name+'">'+f.name+'</div><div class="fd"><span>'+f.date+'</span><span>'+f.size+'</span></div></div></div>';
  }
  if(!html)html='<div class="empty-state"><div class="em-icon">📭</div><div class="em-text">Pasta vazia</div></div>';
  grid.innerHTML=html;
  if(changed||resetScroll)grid.scrollTop=0;
}

/* Polling transfers */
function startPolling(){
  if(pollTimer)return;
  pollTimer=setInterval(async function(){
    try{
      var tr=await fetch('/api/transfers').then(function(r){return r.json()});
      renderTransfers(tr.transfers||[]);
    }catch(e){}
  },2000);
}
function stopPolling(){if(pollTimer){clearInterval(pollTimer);pollTimer=null;}}

function renderTransfers(list){
  document.getElementById('badge-transfers').textContent=list.length;
  var c=document.getElementById('tab-content-transfers');
  if(list.length===0){c.innerHTML='<div class="empty-list"><div class="el-icon">↕</div>As transferências aparecerão aqui</div>';return;}
  var h='';
  for(var i=0;i<list.length;i++){
    var t=list[i];
    var isUp=t.direction==='upload';
    var icon=isUp?'📥':'📤';
    var cls=isUp?'ul':'dl';
    var sDone=t.progress>=100;
    h+='<div class="transfer-item"><span class="t-icon">'+icon+'</span><div class="t-info"><div class="t-name">'+t.name+'</div><div class="t-detail"><div class="t-bar"><div class="t-bar-fill '+cls+'" style="width:'+t.progress+'%"></div></div><span>'+t.size+'</span></div></div><span class="t-status '+(sDone?'done':'active')+'">'+(sDone?'✓':''+t.progress+'%')+'</span></div>';
  }
  c.innerHTML=h;
}

</script></body></html>"""


# ─── Main ───

class QuietTCPServer(socketserver.ThreadingTCPServer):
    """Silencia desconexões normais do cliente (iPhone/Safari fechando conexões)."""
    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)

def main():
    global PORT
    PORT = find_free_port()

    # Inicia servidor HTTP em background
    socketserver.TCPServer.allow_reuse_address = True
    srv = QuietTCPServer(("", PORT), UnifiedHandler)
    srv_thread = threading.Thread(target=srv.serve_forever, daemon=True)
    srv_thread.start()

    print("🌊 OceanShare v5")
    print(f"   http://localhost:{PORT}")
    if PORT != PREFERRED_PORT:
        print(f"   (porta {PREFERRED_PORT} ocupada, usando {PORT})")

    # Abre janela nativa
    window = webview.create_window(
        "OceanShare 🌊",
        f"http://localhost:{PORT}/admin",
        width=1120,
        height=640,
        resizable=True,
        min_size=(900, 500),
    )
    state.window = window

    # Quando fecha a janela, mata tudo
    def on_closed():
        srv.shutdown()
        os._exit(0)

    window.events.closed += on_closed
    webview.start()
    os._exit(0)


if __name__ == "__main__":
    main()