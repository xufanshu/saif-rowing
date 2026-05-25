#!/usr/bin/env python3
"""SAIF ROWING 账本 - 同步服务器（含自动备份）"""
import json, os, http.server, shutil, glob
from datetime import datetime
from urllib.parse import urlparse, parse_qs

BASE_DIR = os.path.dirname(__file__)
DATA_FILE = os.path.join(BASE_DIR, 'data.json')
BACKUP_DIR = os.path.join(BASE_DIR, 'backups')
MAX_BACKUPS = 50  # keep last 50
PORT = 3000

os.makedirs(BACKUP_DIR, exist_ok=True)

# Initialize empty data file
if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, 'w') as f:
        json.dump({"transactions":[],"competitions":[],"members":[],"registrations":[]}, f)

def load_data():
    with open(DATA_FILE, 'r') as f:
        return json.load(f)

def save_data(data):
    # Backup current data before overwriting
    if os.path.exists(DATA_FILE) and os.path.getsize(DATA_FILE) > 10:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_name = f'data_{ts}.json'
        backup_path = os.path.join(BACKUP_DIR, backup_name)
        shutil.copy2(DATA_FILE, backup_path)
        # Limit backups
        backups = sorted(glob.glob(os.path.join(BACKUP_DIR, 'data_*.json')))
        while len(backups) > MAX_BACKUPS:
            os.remove(backups[0])
            backups = backups[1:]
    # Write new data
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def list_backups():
    backups = sorted(glob.glob(os.path.join(BACKUP_DIR, 'data_*.json')), reverse=True)
    result = []
    for b in backups:
        name = os.path.basename(b)
        size = os.path.getsize(b)
        ts = name.replace('data_', '').replace('.json', '')
        try:
            dt = datetime.strptime(ts, '%Y%m%d_%H%M%S')
            display = dt.strftime('%Y-%m-%d %H:%M:%S')
        except:
            display = ts
        result.append({'file': name, 'time': display, 'size': size})
    return result

class Handler(http.server.SimpleHTTPRequestHandler):
    directory = BASE_DIR

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors()
        self.end_headers()

    def send_cache_headers(self):
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')

    def send_response(self, *args, **kwargs):
        super().send_response(*args, **kwargs)
        self.send_cache_headers()

    def send_cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_cache_headers()

    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_cors()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def do_GET(self):
        if self.path == '/api/data':
            self.send_json(load_data())
        elif self.path == '/api/export':
            self.send_response(200)
            self.send_cors()
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Disposition', 'attachment; filename="saif-rowing-backup.json"')
            self.end_headers()
            self.wfile.write(json.dumps(load_data(), ensure_ascii=False, indent=2).encode('utf-8'))
        elif self.path == '/api/backups':
            self.send_json(list_backups())
        elif self.path.startswith('/api/backup/'):
            # GET /api/backup/data_20260524_220000.json
            fname = self.path.replace('/api/backup/', '')
            fpath = os.path.join(BACKUP_DIR, fname)
            if os.path.exists(fpath):
                self.send_response(200)
                self.send_cors()
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                with open(fpath) as f:
                    self.wfile.write(f.read().encode('utf-8'))
            else:
                self.send_json({"error": "备份文件不存在"}, 404)
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == '/api/data':
            content_len = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_len)
            try:
                new_data = json.loads(body)
                save_data(new_data)
                self.send_json({"ok": True, "backupCount": len(list_backups())})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 400)
        elif path == '/api/import':
            content_len = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_len)
            try:
                new_data = json.loads(body)
                save_data(new_data)
                bks = list_backups()
                self.send_json({"ok": True, "message": f"已导入 {len(new_data.get('transactions',[]))} 条支出, {len(new_data.get('members',[]))} 个会员, {len(new_data.get('competitions',[]))} 个比赛", "backupCount": len(bks)})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 400)
        elif path == '/api/restore':
            fname = query.get('file', [None])[0]
            if not fname:
                self.send_json({"ok": False, "error": "请指定备份文件"}, 400)
            fpath = os.path.join(BACKUP_DIR, fname)
            if not os.path.exists(fpath):
                self.send_json({"ok": False, "error": "备份文件不存在"}, 404)
            with open(fpath) as f:
                data = json.load(f)
            save_data(data)  # this creates another backup of current state
            self.send_json({"ok": True, "message": f"已恢复到 {fname}"})
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # quiet

if __name__ == '__main__':
    bk_count = len(list_backups())
    print(f"\n{'='*45}")
    print(f"  SAIF ROWING 账本 - 同步服务器")
    print(f"  📍 http://localhost:{PORT}")
    print(f"  📱 手机同WiFi: http://<本机IP>:{PORT}")
    print(f"  💾 数据文件: data.json")
    print(f"  📦 自动备份: backups/ 目录 ({bk_count} 份)")
    print(f"{'='*45}\n")
    server = http.server.HTTPServer(('0.0.0.0', PORT), Handler)
    server.serve_forever()
