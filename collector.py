import os
import re
import ssl
import json
import asyncio
import sqlite3
import datetime
from typing import Optional, List, Dict, Any, Tuple
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
import websockets

DB_PATH = os.path.join(os.path.dirname(__file__), "soop_chat.db")

# Vercel 서버리스 환경 (/tmp 쓰기 가능 경로로 SQLite DB 복제)
if os.environ.get("VERCEL"):
    import shutil
    tmp_db = "/tmp/soop_chat.db"
    orig_db = os.path.join(os.path.dirname(__file__), "soop_chat.db")
    if not os.path.exists(tmp_db) and os.path.exists(orig_db):
        try:
            shutil.copy2(orig_db, tmp_db)
        except Exception:
            pass
    DB_PATH = tmp_db

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                streamer_id TEXT PRIMARY KEY,
                streamer_nick TEXT,
                broad_no TEXT,
                broad_title TEXT,
                status TEXT DEFAULT 'stopped',
                started_at TEXT,
                last_chat_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                streamer_id TEXT,
                broad_no TEXT,
                user_id TEXT,
                user_nick TEXT,
                message TEXT,
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS monitored_streamers (
                streamer_id TEXT PRIMARY KEY,
                streamer_nick TEXT,
                is_monitoring INTEGER DEFAULT 1,
                auto_record INTEGER DEFAULT 1,
                created_at TEXT,
                last_checked_at TEXT,
                last_status TEXT DEFAULT 'offline',
                broad_title TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vod_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title_no TEXT,
                broad_no TEXT,
                streamer_id TEXT,
                streamer_nick TEXT,
                broad_title TEXT,
                chat_count INTEGER DEFAULT 0,
                broad_start TEXT,
                last_chat_time TEXT,
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chats_streamer ON chats(streamer_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chats_user_id ON chats(user_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chats_user_nick ON chats(user_nick)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chats_created_at ON chats(created_at)
        """)
        try:
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_chats_unique 
                ON chats(streamer_id, broad_no, user_id, message, created_at)
            """)
        except Exception:
            pass
    conn.close()

init_db()

def extract_streamer_id(url_or_id: str) -> str:
    cleaned = url_or_id.strip()
    if "/" in cleaned:
        # e.g., https://play.sooplive.co.kr/khm11903/297663883 or https://ch.sooplive.co.kr/khm11903
        match = re.search(r'sooplive\.(?:co\.kr|com)/(?:play/|ch/)?([a-zA-Z0-9_-]+)', cleaned)
        if match:
            return match.group(1)
        # fallback simple slash split
        parts = cleaned.rstrip("/").split("/")
        for part in reversed(parts):
            if part and not part.isdigit() and "sooplive" not in part and "http" not in part:
                return part
    return cleaned

def extract_vod_title_no(url_or_no: str) -> Optional[int]:
    cleaned = url_or_no.strip()
    match = re.search(r'(?:player/|VIDEO/|^)(\d+)', cleaned)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None

def fetch_live_detail(streamer_id: str) -> Optional[Dict[str, Any]]:
    api_url = f"https://live.sooplive.co.kr/afreeca/player_live_api.php?bjid={streamer_id}"
    post_data = urllib.parse.urlencode({
        'bid': streamer_id,
        'type': 'live',
        'pwd': '',
        'player_type': 'html5',
        'stream_type': 'common',
        'quality': 'HD',
        'mode': 'landing',
        'from_api': '0',
        'is_revive': 'false'
    }).encode('utf-8')
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': f'https://play.sooplive.co.kr/{streamer_id}',
        'Origin': 'https://play.sooplive.co.kr'
    }
    req = urllib.request.Request(api_url, data=post_data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            res_json = json.loads(res.read().decode('utf-8'))
            channel = res_json.get('CHANNEL', {})
            if channel.get('RESULT') == 1:
                return channel
            return None
    except Exception as e:
        print(f"Error fetching live detail for {streamer_id}: {e}")
def fetch_station_info(streamer_id: str) -> Optional[Dict[str, Any]]:
    url = f"https://chapi.sooplive.co.kr/api/{streamer_id}/station"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=8) as res:
            data = json.loads(res.read().decode('utf-8'))
            return data.get('station', {})
    except Exception as e:
        print(f"Error fetching station info for {streamer_id}: {e}")
        return None

def fetch_vod_meta(title_no: int) -> Optional[Dict[str, Any]]:
    api_url = "https://api.m.sooplive.com/station/video/a/view"
    post_data = urllib.parse.urlencode({
        'nTitleNo': title_no,
        'nApiLevel': 11,
        'nPlaylistIdx': 0
    }).encode('utf-8')
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': f'https://vod.sooplive.com/player/{title_no}'
    }
    req = urllib.request.Request(api_url, data=post_data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=12) as res:
            res_json = json.loads(res.read().decode('utf-8'))
            return res_json.get('data')
    except Exception as e:
        print(f"Error fetching VOD meta for {title_no}: {e}")
        return None

def _fetch_single_chat_chunk(row_key: str, start_time: int) -> List[Tuple[str, str, str, float]]:
    url = f"https://videoimg.sooplive.com/php/ChatLoadSplit.php?rowKey={row_key}&startTime={start_time}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read().decode('utf-8')
            root = ET.fromstring(data)
            results = []
            for c in root.findall('chat'):
                msg = c.findtext('m', '')
                uid = c.findtext('u', '')
                unick = c.findtext('n', '')
                try:
                    t_val = float(c.findtext('t', '0'))
                except ValueError:
                    t_val = float(start_time)
                if msg or uid:
                    results.append((uid, unick, msg, t_val))
            return results
    except Exception:
        return []

async def import_vod_chat(title_no: int, max_chunks: Optional[int] = None) -> Dict[str, Any]:
    meta = fetch_vod_meta(title_no)
    if not meta:
        raise ValueError(f"다시보기(VOD 번호: {title_no}) 정보를 찾을 수 없습니다. 비공개이거나 삭제된 영상일 수 있습니다.")

    streamer_id = meta.get('bj_id') or "unknown"
    streamer_nick = meta.get('writer_nick') or streamer_id
    broad_title = meta.get('title') or "다시보기 방송"
    broad_no = str(meta.get('broad_no') or title_no)
    
    # Broadcast start time calculation
    broad_start_str = meta.get('broad_start')
    broad_start_dt = None
    if broad_start_str:
        try:
            broad_start_dt = datetime.datetime.strptime(broad_start_str, "%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    if not broad_start_dt:
        write_tm = meta.get('write_tm', '')
        if '~' in write_tm:
            try:
                broad_start_dt = datetime.datetime.strptime(write_tm.split('~')[0].strip(), "%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
    if not broad_start_dt:
        broad_start_dt = datetime.datetime.now()

    files = meta.get('files', [])
    if not files:
        raise ValueError("다시보기 내 재생 파일 및 채팅 정보를 찾을 수 없습니다.")

    all_chats_to_insert = []
    loop = asyncio.get_running_loop()

    for file_info in files:
        chat_url = file_info.get('chat', '')
        if not chat_url or 'rowKey=' not in chat_url:
            continue
        row_key_match = re.search(r'rowKey=([a-zA-Z0-9_-]+)', chat_url)
        if not row_key_match:
            continue
        row_key = row_key_match.group(1)

        duration_sec = int(file_info.get('duration', 0))
        if duration_sec <= 0:
            duration_sec = int(meta.get('total_file_duration', 0)) // 1000
        if duration_sec <= 0:
            duration_sec = 3600 * 3 # default 3 hours

        chunk_starts = list(range(0, duration_sec + 300, 300))
        if max_chunks and len(chunk_starts) > max_chunks:
            chunk_starts = chunk_starts[:max_chunks]

        # Parallel chunk fetching via ThreadPoolExecutor
        def fetch_all():
            with ThreadPoolExecutor(max_workers=8) as pool:
                res_list = pool.map(lambda st: _fetch_single_chat_chunk(row_key, st), chunk_starts)
                return [item for sublist in res_list for item in sublist]

        file_chats = await loop.run_in_executor(None, fetch_all)

        for uid, unick, msg, t_sec in file_chats:
            chat_dt = broad_start_dt + datetime.timedelta(seconds=t_sec)
            created_at_str = chat_dt.strftime('%Y-%m-%d %H:%M:%S')
            all_chats_to_insert.append((
                streamer_id,
                broad_no,
                uid,
                unick or uid,
                msg,
                created_at_str
            ))

    if not all_chats_to_insert:
        raise ValueError("해당 다시보기에서 불러올 수 있는 채팅 데이터가 없습니다.")

    # Batch insert into DB
    conn = get_db()
    with conn:
        conn.executemany("""
            INSERT OR IGNORE INTO chats (streamer_id, broad_no, user_id, user_nick, message, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, all_chats_to_insert)

        first_chat_time = all_chats_to_insert[0][5]
        last_chat_time = all_chats_to_insert[-1][5]

        conn.execute("""
            INSERT INTO vod_records (title_no, broad_no, streamer_id, streamer_nick, broad_title, chat_count, broad_start, last_chat_time, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
        """, (str(title_no), broad_no, streamer_id, streamer_nick, broad_title, len(all_chats_to_insert), broad_start_dt.strftime('%Y-%m-%d %H:%M:%S'), all_chats_to_insert[-1][5]))
        cur = conn.cursor()
        cur.execute("SELECT last_insert_rowid()")
        vod_id = cur.fetchone()[0]
    conn.close()

    return {
        "success": True,
        "vod_id": vod_id,
        "streamer_id": streamer_id,
        "streamer_nick": streamer_nick,
        "broad_title": broad_title,
        "broad_no": broad_no,
        "chats_imported": len(all_chats_to_insert),
        "broad_start": broad_start_dt.strftime('%Y-%m-%d %H:%M:%S'),
        "last_chat_time": all_chats_to_insert[-1][5]
    }

def parse_uploaded_chat_log(content_str: str, default_streamer_id: str = "imported", default_title: str = "업로드된 채팅 로그") -> Dict[str, Any]:
    lines = content_str.splitlines()
    parsed_chats = []
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Regex patterns:
    # 1. [2026-10-09 12:30:45] 닉네임(아이디): 메시지
    pattern_full = re.compile(r'^\[?(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\]?\s+([^(\[]+?)(?:\(([^)]+?)\))?:\s*(.*)$')
    # 2. [12:30:45] 닉네임(아이디): 메시지
    pattern_time = re.compile(r'^\[?(\d{2}:\d{2}:\d{2})\]?\s+([^(\[]+?)(?:\(([^)]+?)\))?:\s*(.*)$')
    # 3. CSV: timestamp,user_id,user_nick,message
    today_prefix = datetime.datetime.now().strftime('%Y-%m-%d')

    for line in lines:
        line = line.strip()
        if not line:
            continue

        m_full = pattern_full.match(line)
        if m_full:
            time_str, unick, uid, msg = m_full.groups()
            parsed_chats.append((default_streamer_id, "upload", uid or unick.strip(), unick.strip(), msg.strip(), time_str))
            continue

        m_time = pattern_time.match(line)
        if m_time:
            time_part, unick, uid, msg = m_time.groups()
            full_time = f"{today_prefix} {time_part}"
            parsed_chats.append((default_streamer_id, "upload", uid or unick.strip(), unick.strip(), msg.strip(), full_time))
            continue

        if "," in line:
            parts = [p.strip().strip('"').strip("'") for p in line.split(",")]
            if len(parts) >= 4:
                # time, uid, unick, msg
                parsed_chats.append((default_streamer_id, "upload", parts[1], parts[2], parts[3], parts[0]))
                continue

    if not parsed_chats:
        raise ValueError("채팅 형식에 맞는 로그를 찾지 못했습니다. [HH:MM:SS] 닉네임(아이디): 내용 형식을 권장합니다.")

    conn = get_db()
    with conn:
        conn.executemany("""
            INSERT OR IGNORE INTO chats (streamer_id, broad_no, user_id, user_nick, message, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, parsed_chats)

        conn.execute("""
            INSERT INTO vod_records (title_no, broad_no, streamer_id, streamer_nick, broad_title, chat_count, broad_start, last_chat_time, created_at)
            VALUES ('upload', 'upload', ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
        """, (default_streamer_id, default_streamer_id, default_title, len(parsed_chats), parsed_chats[0][5], parsed_chats[-1][5]))
        cur = conn.cursor()
        cur.execute("SELECT last_insert_rowid()")
        vod_id = cur.fetchone()[0]
    conn.close()

    return {
        "success": True,
        "vod_id": vod_id,
        "streamer_id": default_streamer_id,
        "streamer_nick": default_streamer_id,
        "broad_title": default_title,
        "broad_no": "upload",
        "chats_imported": len(parsed_chats),
        "first_chat": parsed_chats[0][5],
        "last_chat": parsed_chats[-1][5]
    }

class ChatCollector:
    def __init__(self):
        self.running_tasks: Dict[str, asyncio.Task] = {}
        self.stop_events: Dict[str, asyncio.Event] = {}

    def is_running(self, streamer_id: str) -> bool:
        task = self.running_tasks.get(streamer_id)
        return task is not None and not task.done()

    async def start(self, streamer_id: str):
        if self.is_running(streamer_id):
            return
        stop_event = asyncio.Event()
        self.stop_events[streamer_id] = stop_event
        task = asyncio.create_task(self._run_collector(streamer_id, stop_event))
        self.running_tasks[streamer_id] = task

    async def stop(self, streamer_id: str):
        if streamer_id in self.stop_events:
            self.stop_events[streamer_id].set()
        task = self.running_tasks.get(streamer_id)
        if task:
            try:
                task.cancel()
            except Exception:
                pass
            self.running_tasks.pop(streamer_id, None)
        self.stop_events.pop(streamer_id, None)

        conn = get_db()
        with conn:
            conn.execute("UPDATE channels SET status = 'stopped' WHERE streamer_id = ?", (streamer_id,))
        conn.close()

    async def _run_collector(self, streamer_id: str, stop_event: asyncio.Event):
        retry_delay = 5
        while not stop_event.is_set():
            detail = fetch_live_detail(streamer_id)
            if not detail:
                conn = get_db()
                with conn:
                    conn.execute("""
                        INSERT INTO channels (streamer_id, streamer_nick, broad_no, broad_title, status, started_at)
                        VALUES (?, ?, ?, ?, 'offline', datetime('now', 'localtime'))
                        ON CONFLICT(streamer_id) DO UPDATE SET status = 'offline'
                    """, (streamer_id, streamer_id, "", "방송 대기 중 (오프라인)"))
                conn.close()
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=15)
                    break
                except asyncio.TimeoutError:
                    continue

            streamer_nick = detail.get('BJNICK', streamer_id)
            broad_no = str(detail.get('BNO', ''))
            broad_title = detail.get('TITLE', '')
            chdomain = detail.get('CHDOMAIN', '').lower()
            chpt = int(detail.get('CHPT', '0')) + 1
            chatno = detail.get('CHATNO', '')

            ws_url = f"wss://{chdomain}:{chpt}/Websocket/{streamer_id}"

            conn = get_db()
            with conn:
                conn.execute("""
                    INSERT INTO channels (streamer_id, streamer_nick, broad_no, broad_title, status, started_at)
                    VALUES (?, ?, ?, ?, 'recording', datetime('now', 'localtime'))
                    ON CONFLICT(streamer_id) DO UPDATE SET
                        streamer_nick = excluded.streamer_nick,
                        broad_no = excluded.broad_no,
                        broad_title = excluded.broad_title,
                        status = 'recording'
                """, (streamer_id, streamer_nick, broad_no, broad_title))
            conn.close()

            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            try:
                async with websockets.connect(ws_url, subprotocols=['chat'], ssl=ssl_context) as ws:
                    sep = '\x0c'
                    starter = '\x1b\t'

                    # CONNECT handshake
                    connect_payload = f'{sep*3}16{sep}'
                    connect_packet = f'{starter}0001{len(connect_payload.encode()):06d}00{connect_payload}'
                    await ws.send(connect_packet)

                    last_ping = asyncio.get_event_loop().time()

                    while not stop_event.is_set():
                        now = asyncio.get_event_loop().time()
                        if now - last_ping > 55:
                            ping_packet = f'{starter}000000000100{sep}'
                            await ws.send(ping_packet)
                            last_ping = now

                        try:
                            msg_task = asyncio.create_task(ws.recv())
                            done, _ = await asyncio.wait([msg_task], timeout=2.0)
                            if not done:
                                msg_task.cancel()
                                continue
                            raw = msg_task.result()
                        except asyncio.CancelledError:
                            break
                        except Exception:
                            break

                        if isinstance(raw, bytes):
                            msg = raw.decode('utf-8', errors='ignore')
                        else:
                            msg = raw

                        if len(msg) < 6:
                            continue

                        opcode = msg[2:6]

                        if opcode == '0001': # CONNECT ACK
                            join_payload = f'{sep}{chatno}{sep*5}'
                            join_packet = f'{starter}0002{len(join_payload.encode()):06d}00{join_payload}'
                            await ws.send(join_packet)

                        elif opcode == '0005': # CHAT message
                            parts = msg.split(sep)
                            if len(parts) >= 7:
                                comment = parts[1]
                                user_id = parts[2]
                                user_nick = parts[6]
                                if comment and user_id:
                                    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                                    db = get_db()
                                    with db:
                                        db.execute("""
                                            INSERT OR IGNORE INTO chats (streamer_id, broad_no, user_id, user_nick, message, created_at)
                                            VALUES (?, ?, ?, ?, ?, ?)
                                        """, (streamer_id, broad_no, user_id, user_nick, comment, now_str))
                                        db.execute("""
                                            UPDATE channels SET last_chat_at = ? WHERE streamer_id = ?
                                        """, (now_str, streamer_id))
                                    db.close()
            except Exception as e:
                print(f"WebSocket error for {streamer_id}: {e}")
                if stop_event.is_set():
                    break
                await asyncio.sleep(retry_delay)

        conn = get_db()
        with conn:
            conn.execute("UPDATE channels SET status = 'stopped' WHERE streamer_id = ?", (streamer_id,))
        conn.close()

collector = ChatCollector()

class StreamMonitor:
    def __init__(self, chat_collector: ChatCollector, interval: int = 20):
        self.collector = chat_collector
        self.interval = interval
        self.running = False
        self.task: Optional[asyncio.Task] = None

    async def start(self):
        if self.running:
            return
        self.running = True
        self.task = asyncio.create_task(self._monitor_loop())
        print("[MONITOR] 24/7 스트리머 자동 감시 데몬 시작됨.")

    async def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        print("[MONITOR] 스트리머 자동 감시 데몬 중지됨.")

    async def check_streamer(self, streamer_id: str):
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM monitored_streamers WHERE streamer_id = ?", (streamer_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return
        
        is_monitoring = row["is_monitoring"]
        auto_record = row["auto_record"]
        conn.close()

        if not is_monitoring:
            return

        detail = fetch_live_detail(streamer_id)
        now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        conn = get_db()
        with conn:
            if detail:
                streamer_nick = detail.get('BJNICK', streamer_id)
                broad_title = detail.get('TITLE', '')
                conn.execute("""
                    UPDATE monitored_streamers 
                    SET last_status = 'live', streamer_nick = ?, broad_title = ?, last_checked_at = ?
                    WHERE streamer_id = ?
                """, (streamer_nick, broad_title, now_str, streamer_id))

                # If live and auto_record enabled, start recording if not already running
                if auto_record and not self.collector.is_running(streamer_id):
                    print(f"[MONITOR] 방송 시작 감지! '{streamer_nick}'({streamer_id}) 자동 채팅 수집 시작")
                    await self.collector.start(streamer_id)
            else:
                conn.execute("""
                    UPDATE monitored_streamers 
                    SET last_status = 'offline', last_checked_at = ?
                    WHERE streamer_id = ?
                """, (now_str, streamer_id))
        conn.close()

    async def check_all(self):
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT streamer_id FROM monitored_streamers WHERE is_monitoring = 1")
        rows = cursor.fetchall()
        conn.close()

        for row in rows:
            sid = row["streamer_id"]
            try:
                await self.check_streamer(sid)
            except Exception as e:
                print(f"[MONITOR] Error checking {sid}: {e}")

    async def _monitor_loop(self):
        while self.running:
            try:
                await self.check_all()
            except Exception as e:
                print(f"[MONITOR] Loop error: {e}")
            await asyncio.sleep(self.interval)

monitor = StreamMonitor(collector)
