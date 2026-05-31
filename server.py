#!/usr/bin/env python3
"""SAIF ROWING 账本 - 同步服务器（PostgreSQL 主存储 / JSON 本地回退）"""
import json, os, http.server, shutil, glob
from datetime import datetime
from urllib.parse import urlparse, parse_qs

BASE_DIR = os.path.dirname(__file__)
DATA_FILE = os.path.join(BASE_DIR, 'data.json')
BACKUP_DIR = os.path.join(BASE_DIR, 'backups')
MAX_BACKUPS = 50
PORT = int(os.environ.get('PORT', 3000))
DATABASE_URL = os.environ.get('DATABASE_URL', '') or os.environ.get('POSTGRESQL_URL', '')

# ── 存储后端选择 ──────────────────────────────
# 在 Render 上必须用 PostgreSQL，否则数据会丢失
RENDER = os.environ.get('RENDER', '')
FORCE_PG = os.environ.get('FORCE_PG', '') in ('1', 'true', 'yes')

# 如果部署在 Render 上但没有 DATABASE_URL，从已知数据库信息自动填充
from urllib.parse import quote
AUTO_DB_URL = 'postgresql://saif_rowing_db_user:aOEbn3gXaG6tJXdr0URy497uS7OdIsnC@dpg-d8am76v7f7vs73d9uen0-a.oregon-postgres.render.com/saif_rowing_db'

# 先看 DATABASE_URL 是否有效，有就用 PG
USE_PG = bool(DATABASE_URL) or bool(RENDER)

# 如果在 Render 上但没有 DATABASE_URL，自动填充
if RENDER and not DATABASE_URL:
    print("\n" + "="*55)
    print("  Render 上未检测到 DATABASE_URL，自动使用已知数据库")
    print("="*55)
    DATABASE_URL = AUTO_DB_URL
    USE_PG = True

if USE_PG:
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError as e:
        print(f"  ⚠️  psycopg2 导入失败: {e}，回退到 JSON 文件模式")
        USE_PG = False

if USE_PG:
    def get_pg_conn():
        return psycopg2.connect(DATABASE_URL, sslmode='require')

    def init_db():
        conn = get_pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS transactions (
                        id TEXT PRIMARY KEY,
                        type TEXT NOT NULL,
                        amount REAL NOT NULL DEFAULT 0,
                        date TEXT NOT NULL DEFAULT '',
                        note TEXT DEFAULT '',
                        comp_id TEXT DEFAULT '',
                        member_ids TEXT DEFAULT '[]',
                        orig_amount REAL DEFAULT NULL,
                        currency TEXT DEFAULT 'CNY',
                        rate REAL DEFAULT 1,
                        public_member_ids TEXT DEFAULT '[]',
                        expense_type TEXT DEFAULT ''
                    )
                ''')
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS competitions (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL DEFAULT '',
                        date TEXT NOT NULL DEFAULT '',
                        note TEXT DEFAULT '',
                        boat_details TEXT DEFAULT '{}'
                    )
                ''')
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS members (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL DEFAULT '',
                        note TEXT DEFAULT ''
                    )
                ''')
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS registrations (
                        id TEXT PRIMARY KEY,
                        comp_id TEXT DEFAULT '',
                        member_id TEXT DEFAULT '',
                        boat_name TEXT DEFAULT '',
                        race_num TEXT DEFAULT '',
                        result TEXT DEFAULT '',
                        race_time TEXT DEFAULT '',
                        paid REAL DEFAULT 0
                    )
                ''')
            conn.commit()
        finally:
            conn.close()

    def pg_load_data():
        conn = get_pg_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute('SELECT * FROM transactions ORDER BY date')
                txs = []
                for row in cur.fetchall():
                    t = dict(row)
                    # restore nested JSON fields
                    for fld in ('member_ids', 'public_member_ids'):
                        try:
                            t[fld] = json.loads(t.get(fld, '[]') or '[]')
                        except:
                            t[fld] = []
                    # rename columns back to camelCase for API
                    t['compId'] = t.pop('comp_id', '')
                    t['memberIds'] = t.pop('member_ids', [])
                    t['origAmount'] = t.pop('orig_amount', None)
                    t['publicMemberIds'] = t.pop('public_member_ids', [])
                    t['expenseType'] = t.pop('expense_type', '')
                    # drop nulls
                    t = {k: v for k, v in t.items() if v is not None}
                    txs.append(t)

                cur.execute('SELECT * FROM competitions ORDER BY date')
                comps = []
                for row in cur.fetchall():
                    c = dict(row)
                    try:
                        c['boatDetails'] = json.loads(c.get('boat_details', '{}'))
                    except:
                        c['boatDetails'] = {}
                    c['boatName'] = c.pop('boat_name', '')
                    c['compId'] = c.pop('comp_id', '')
                    c['memberId'] = c.pop('member_id', '')
                    c['boatName'] = c.pop('boat_name', '')
                    c['raceNum'] = c.pop('race_num', '')
                    c['raceTime'] = c.pop('race_time', '')
                    c['note'] = c.get('note', '')
                    c = {k: v for k, v in c.items() if k != 'boat_details'}
                    comps.append(c)

                cur.execute('SELECT * FROM members ORDER BY name')
                members = []
                for row in cur.fetchall():
                    m = dict(row)
                    m = {k: v for k, v in m.items() if v is not None}
                    members.append(m)

                cur.execute('SELECT * FROM registrations ORDER BY comp_id, member_id')
                regs = []
                for row in cur.fetchall():
                    r = dict(row)
                    r['compId'] = r.pop('comp_id', '')
                    r['memberId'] = r.pop('member_id', '')
                    r['boatName'] = r.pop('boat_name', '')
                    r['raceNum'] = r.pop('race_num', '')
                    r['raceTime'] = r.pop('race_time', '')
                    r = {k: v for k, v in r.items() if v is not None}
                    regs.append(r)
        finally:
            conn.close()
        return {'transactions': txs, 'competitions': comps, 'members': members, 'registrations': regs}

    def pg_save_data(data):
        conn = get_pg_conn()
        try:
            with conn.cursor() as cur:
                # ── transactions ──
                cur.execute('DELETE FROM transactions')
                for t in data.get('transactions', []):
                    mid = json.dumps(t.get('memberIds', []), ensure_ascii=False)
                    pmid = json.dumps(t.get('publicMemberIds', []), ensure_ascii=False)
                    cur.execute('''
                        INSERT INTO transactions
                            (id, type, amount, date, note, comp_id, member_ids,
                             orig_amount, currency, rate, public_member_ids, expense_type)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ''', (
                        t.get('id',''), t.get('type',''), t.get('amount',0),
                        t.get('date',''), t.get('note',''), t.get('compId',''), mid,
                        t.get('origAmount'), t.get('currency','CNY'), t.get('rate',1),
                        pmid, t.get('expenseType','')
                    ))

                # ── competitions ──
                cur.execute('DELETE FROM competitions')
                for c in data.get('competitions', []):
                    bd = json.dumps(c.get('boatDetails', {}), ensure_ascii=False)
                    cur.execute('''
                        INSERT INTO competitions (id, name, date, note, boat_details)
                        VALUES (%s,%s,%s,%s,%s)
                    ''', (c.get('id',''), c.get('name',''), c.get('date',''),
                          c.get('note',''), bd))

                # ── members ──
                cur.execute('DELETE FROM members')
                for m in data.get('members', []):
                    cur.execute('''
                        INSERT INTO members (id, name, note) VALUES (%s,%s,%s)
                    ''', (m.get('id',''), m.get('name',''), m.get('note','')))

                # ── registrations ──
                cur.execute('DELETE FROM registrations')
                for r in data.get('registrations', []):
                    # Support both boatName and event fields for backwards compatibility
                    boat = r.get('boatName', '') or r.get('boat', '') or r.get('event', '')
                    cur.execute('''
                        INSERT INTO registrations
                            (id, comp_id, member_id, boat_name, race_num, result, race_time, paid)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ''', (r.get('id',''), r.get('compId',''), r.get('memberId',''),
                          boat, r.get('raceNum',''), r.get('result',''),
                          r.get('raceTime',''), r.get('paid',0)))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def pg_list_backups():
        return []

    def load_data():
        return pg_load_data()

    def save_data(data):
        pg_save_data(data)

    def list_backups():
        return pg_list_backups()

else:
    # ── 回退到 JSON 文件模式（本地开发） ──
    os.makedirs(BACKUP_DIR, exist_ok=True)

    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'w') as f:
            json.dump({"transactions":[],"competitions":[],"members":[],"registrations":[]}, f)

    def load_data():
        with open(DATA_FILE, 'r') as f:
            return json.load(f)

    def save_data(data):
        if os.path.exists(DATA_FILE) and os.path.getsize(DATA_FILE) > 10:
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            shutil.copy2(DATA_FILE, os.path.join(BACKUP_DIR, f'data_{ts}.json'))
            backups = sorted(glob.glob(os.path.join(BACKUP_DIR, 'data_*.json')))
            while len(backups) > MAX_BACKUPS:
                os.remove(backups[0])
                backups = backups[1:]
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


# ═══════════════════════════════════════════════
#  HTTP Handler
# ═══════════════════════════════════════════════

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

    def do_HEAD(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == '/api/data':
            self.send_response(200)
            self.send_cors()
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
        else:
            self.send_response(200)
            self.send_cors()
            self.end_headers()

    def do_GET(self):
        try:
            self._do_GET_impl()
        except Exception as e:
            self.send_json({'error': str(e), 'hint': '服务器内部错误，请稍后重试'}, 500)

    def _do_GET_impl(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == '/api/diagnostics':
            self.send_json({
                'mode': 'postgresql' if USE_PG else 'json_file',
                'hasDatabaseUrl': bool(DATABASE_URL),
                'onRender': bool(os.environ.get('RENDER', '')),
                'transactionCount': len(load_data().get('transactions', [])),
                'backupCount': len(list_backups()),
                'serverTime': datetime.now().isoformat(),
                'hint': '如果 mode=json_file, 请在 Render Dashboard 手动将数据库链接到 web service',
            })
        elif path == '/api/data':
            self.send_json(load_data())
        elif path == '/api/export':
            self.send_response(200)
            self.send_cors()
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Disposition', 'attachment; filename="saif-rowing-backup.json"')
            self.end_headers()
            self.wfile.write(json.dumps(load_data(), ensure_ascii=False, indent=2).encode('utf-8'))
        elif path == '/api/backups':
            self.send_json(list_backups())
        elif path.startswith('/api/backup/'):
            fname = path.replace('/api/backup/', '')
            if USE_PG:
                self.send_json({"error": "PostgreSQL 模式不支持文件级备份恢复"}, 400)
            else:
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
        try:
            self._do_POST_impl()
        except Exception as e:
            self.send_json({'error': str(e), 'hint': '服务器内部错误，请稍后重试'}, 500)

    def _do_POST_impl(self):
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
            if USE_PG:
                self.send_json({"ok": False, "error": "PostgreSQL 模式不支持备份恢复，数据自动持久化"}, 400)
                return
            fname = query.get('file', [None])[0]
            if not fname:
                self.send_json({"ok": False, "error": "请指定备份文件"}, 400)
            fpath = os.path.join(BACKUP_DIR, fname)
            if not os.path.exists(fpath):
                self.send_json({"ok": False, "error": "备份文件不存在"}, 404)
            with open(fpath) as f:
                data = json.load(f)
            save_data(data)
            self.send_json({"ok": True, "message": f"已恢复到 {fname}"})
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # quiet


def migrate_json_to_pg():
    """将 data.json 中的数据迁移到 PostgreSQL"""
    if not os.path.exists(DATA_FILE):
        return 0
    with open(DATA_FILE) as f:
        try:
            json_data = json.load(f)
        except:
            return 0
    tx = json_data.get('transactions', [])
    if not tx:
        return 0
    pg_save_data(json_data)
    return len(tx)


if __name__ == '__main__':
    pg_ok = False
    if DATABASE_URL:
        try:
            init_db()
            data = load_data()
            tx_count = len(data.get('transactions', []))
            # 如果 PG 是空的，尝试从 data.json 迁移
            if tx_count == 0 and os.path.exists(DATA_FILE):
                migrated = migrate_json_to_pg()
                if migrated > 0:
                    tx_count = migrated
                    print(f"  📦 从 data.json 迁移了 {migrated} 条记录到 PostgreSQL")
                else:
                    # 重新加载迁移后的数据
                    data = load_data()
                    tx_count = len(data.get('transactions', []))
            pg_ok = True
            print(f"\n{'='*45}")
            print(f"  SAIF ROWING 账本 - 同步服务器")
            print(f"  📍 http://0.0.0.0:{PORT}")
            print(f"  🗄️  存储: PostgreSQL ({tx_count} 条交易)")
            print(f"  ✅ 数据跨重启持久化，永不丢失")
            print(f"{'='*45}\n")
        except Exception as e:
            print(f"\n{'!'*50}")
            print(f"  数据库连接失败: {e}")
            print(f"{'!'*50}\n")
    
    if not pg_ok:
        bk_count = len(list_backups())
        print(f"\n{'='*45}")
        print(f"  SAIF ROWING 账本 - 同步服务器")
        print(f"  📍 http://0.0.0.0:{PORT}")
        print(f"  💾 存储: data.json (JSON 文件模式)")
        print(f"  📦 自动备份: backups/ 目录 ({bk_count} 份)")
        print(f"{'='*45}\n")
    server = http.server.HTTPServer(('0.0.0.0', PORT), Handler)
    server.serve_forever()
