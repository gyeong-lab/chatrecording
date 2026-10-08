import os
import json
import collections
import re
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import sqlite3
import urllib.parse
import datetime
import io
import csv

from collector import (
    get_db,
    collector,
    monitor,
    extract_streamer_id,
    extract_vod_title_no,
    fetch_live_detail,
    fetch_station_info,
    import_vod_chat,
    parse_uploaded_chat_log,
    DB_PATH
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 24/7 스트리머 감시 데몬 시작
    await monitor.start()
    yield
    # 종료 시 감시 및 수집 종료
    await monitor.stop()

app = FastAPI(title="SOOP Live Chat Archive", lifespan=lifespan)

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(CURRENT_DIR, "templates")
STATIC_DIR = os.path.join(CURRENT_DIR, "static")

templates = Jinja2Templates(directory=TEMPLATES_DIR)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Pydantic 모델들
class AddChannelRequest(BaseModel):
    url_or_id: str

class DeleteChannelRequest(BaseModel):
    streamer_id: str
    delete_chats: bool = True

class StartRecordRequest(BaseModel):
    url_or_id: str

class StopRecordRequest(BaseModel):
    streamer_id: str

class AddMonitorRequest(BaseModel):
    url_or_id: str
    auto_record: bool = True

class DeleteMonitorRequest(BaseModel):
    streamer_id: str

class ToggleMonitorRequest(BaseModel):
    streamer_id: str
    field: str # 'is_monitoring' or 'auto_record'

class ImportVodRequest(BaseModel):
    url_or_no: str
    max_chunks: Optional[int] = None

@app.get("/", response_class=HTMLResponse)
async def index_page(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

# ==================== [채널 및 실시간 수집 API] ====================

@app.get("/api/channels")
async def list_channels():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT c.*, 
               (SELECT COUNT(*) FROM chats WHERE chats.streamer_id = c.streamer_id) as total_chats,
               (SELECT COUNT(DISTINCT user_id) FROM chats WHERE chats.streamer_id = c.streamer_id) as total_users
        FROM channels c
        ORDER BY last_chat_at DESC, started_at DESC
    """)
    channels = [dict(row) for row in cursor.fetchall()]
    conn.close()

    for ch in channels:
        ch["is_active"] = collector.is_running(ch["streamer_id"])

    return {"channels": channels}

@app.post("/api/channels/add")
async def add_channel(req: AddChannelRequest):
    streamer_id = extract_streamer_id(req.url_or_id)
    if not streamer_id:
        raise HTTPException(status_code=400, detail="유효한 스트리머 ID 또는 채널 링크를 입력해주세요.")

    streamer_nick = streamer_id
    broad_title = ""
    detail = fetch_live_detail(streamer_id)
    if detail:
        streamer_nick = detail.get("BJNICK") or streamer_id
        broad_title = detail.get("TITLE") or ""
    else:
        st = fetch_station_info(streamer_id)
        if st:
            streamer_nick = st.get("user_nick") or streamer_id
            broad_title = st.get("station_title") or ""

    conn = get_db()
    with conn:
        conn.execute("""
            INSERT INTO channels (streamer_id, streamer_nick, broad_no, broad_title, status, started_at)
            VALUES (?, ?, '', ?, 'stopped', datetime('now', 'localtime'))
            ON CONFLICT(streamer_id) DO UPDATE SET
                streamer_nick = excluded.streamer_nick,
                broad_title = excluded.broad_title
        """, (streamer_id, streamer_nick, broad_title))
    conn.close()

    return {
        "status": "success",
        "streamer_id": streamer_id,
        "streamer_nick": streamer_nick,
        "message": f"'{streamer_nick}'({streamer_id}) 채널이 등록되었습니다."
    }

@app.post("/api/channels/delete")
async def delete_channel(req: DeleteChannelRequest):
    streamer_id = req.streamer_id.strip()
    if not streamer_id:
        raise HTTPException(status_code=400, detail="스트리머 ID를 입력해주세요.")

    if collector.is_running(streamer_id):
        await collector.stop(streamer_id)

    conn = get_db()
    deleted_chats = 0
    with conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM channels WHERE streamer_id = ?", (streamer_id,))
        if req.delete_chats:
            cursor.execute("DELETE FROM chats WHERE streamer_id = ?", (streamer_id,))
            deleted_chats = cursor.rowcount
    conn.close()

    return {
        "status": "success",
        "streamer_id": streamer_id,
        "deleted_chats": deleted_chats,
        "message": f"'{streamer_id}' 채널이 성공적으로 삭제되었습니다." + (f" (관련 채팅 {deleted_chats:,}건 삭제)" if req.delete_chats else "")
    }

@app.post("/api/record/start")
async def start_recording(req: StartRecordRequest):
    streamer_id = extract_streamer_id(req.url_or_id)
    if not streamer_id:
        raise HTTPException(status_code=400, detail="유효한 스트리머 ID 또는 방송 링크를 입력해주세요.")

    if collector.is_running(streamer_id):
        return {"status": "already_running", "streamer_id": streamer_id, "message": "이미 수집 중입니다."}

    detail = fetch_live_detail(streamer_id)
    if not detail:
        raise HTTPException(status_code=400, detail=f"스트리머 '{streamer_id}'의 방송 정보를 찾을 수 없거나 현재 방송 중이 아닙니다.")

    await collector.start(streamer_id)
    return {
        "status": "started",
        "streamer_id": streamer_id,
        "streamer_nick": detail.get("BJNICK", streamer_id),
        "broad_title": detail.get("TITLE", ""),
        "message": f"'{detail.get('BJNICK', streamer_id)}'님의 실시간 채팅 수집을 시작했습니다!"
    }

@app.post("/api/record/stop")
async def stop_recording(req: StopRecordRequest):
    streamer_id = req.streamer_id
    await collector.stop(streamer_id)
    return {"status": "stopped", "streamer_id": streamer_id, "message": "채팅 수집을 중지했습니다."}

# ==================== [24시간 자동 감시 스트리머 API] ====================

@app.get("/api/monitor")
async def list_monitored_streamers():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT m.*,
               (SELECT COUNT(*) FROM chats WHERE chats.streamer_id = m.streamer_id) as total_chats
        FROM monitored_streamers m
        ORDER BY m.last_status DESC, m.created_at DESC
    """)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    for r in rows:
        r["is_collecting"] = collector.is_running(r["streamer_id"])

    return {"streamers": rows}

@app.post("/api/monitor/add")
async def add_monitored_streamer(req: AddMonitorRequest):
    streamer_id = extract_streamer_id(req.url_or_id)
    if not streamer_id:
        raise HTTPException(status_code=400, detail="유효한 스트리머 ID 또는 링크를 입력해주세요.")

    detail = fetch_live_detail(streamer_id)
    streamer_nick = streamer_id
    broad_title = ""
    last_status = "offline"

    if detail:
        streamer_nick = detail.get("BJNICK", streamer_id)
        broad_title = detail.get("TITLE", "")
        last_status = "live"

    conn = get_db()
    with conn:
        conn.execute("""
            INSERT INTO monitored_streamers 
            (streamer_id, streamer_nick, is_monitoring, auto_record, created_at, last_checked_at, last_status, broad_title)
            VALUES (?, ?, 1, ?, datetime('now', 'localtime'), datetime('now', 'localtime'), ?, ?)
            ON CONFLICT(streamer_id) DO UPDATE SET
                is_monitoring = 1,
                auto_record = excluded.auto_record,
                streamer_nick = excluded.streamer_nick,
                broad_title = excluded.broad_title,
                last_status = excluded.last_status
        """, (streamer_id, streamer_nick, 1 if req.auto_record else 0, last_status, broad_title))
    conn.close()

    # 즉시 상태 체크 및 방송 중이면 자동 시작
    await monitor.check_streamer(streamer_id)

    return {
        "status": "success",
        "streamer_id": streamer_id,
        "streamer_nick": streamer_nick,
        "is_live": (last_status == "live"),
        "message": f"'{streamer_nick}'님을 24시간 자동 감시 목록에 등록했습니다."
    }

@app.post("/api/monitor/delete")
async def delete_monitored_streamer(req: DeleteMonitorRequest):
    conn = get_db()
    with conn:
        conn.execute("DELETE FROM monitored_streamers WHERE streamer_id = ?", (req.streamer_id,))
    conn.close()
    return {"status": "success", "message": f"'{req.streamer_id}' 감시를 해제했습니다."}

@app.post("/api/monitor/toggle")
async def toggle_monitored_streamer(req: ToggleMonitorRequest):
    if req.field not in ("is_monitoring", "auto_record"):
        raise HTTPException(status_code=400, detail="유효하지 않은 필드입니다.")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(f"SELECT {req.field} FROM monitored_streamers WHERE streamer_id = ?", (req.streamer_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="등록되지 않은 스트리머입니다.")

    current_val = row[0]
    new_val = 0 if current_val == 1 else 1
    with conn:
        conn.execute(f"UPDATE monitored_streamers SET {req.field} = ? WHERE streamer_id = ?", (new_val, req.streamer_id))
    conn.close()

    return {"status": "success", "field": req.field, "new_value": bool(new_val)}

# ==================== [과거 다시보기(VOD) 채팅 복원 API] ====================

@app.post("/api/vod/import")
async def import_vod_chats(req: ImportVodRequest):
    title_no = extract_vod_title_no(req.url_or_no)
    if not title_no:
        raise HTTPException(status_code=400, detail="유효한 다시보기(VOD) 링크 또는 번호를 입력해주세요. 예: https://vod.sooplive.com/player/209273485")

    try:
        result = await import_vod_chat(title_no, req.max_chunks)
        return {
            "status": "success",
            "message": f"'{result['streamer_nick']}'님의 다시보기 채팅 {result['chats_imported']:,}건을 성공적으로 불러왔습니다!",
            "data": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/vod/upload")
async def upload_chat_file(
    file: UploadFile = File(...),
    streamer_id: Optional[str] = Form(None),
    title: Optional[str] = Form(None)
):
    try:
        content_bytes = await file.read()
        try:
            content_str = content_bytes.decode('utf-8')
        except UnicodeDecodeError:
            content_str = content_bytes.decode('cp949', errors='ignore')

        sid = streamer_id.strip() if streamer_id else "imported_user"
        btitle = title.strip() if title else file.filename

        result = parse_uploaded_chat_log(content_str, sid, btitle)
        return {
            "status": "success",
            "message": f"파일에서 채팅 {result['chats_imported']:,}건을 성공적으로 가져왔습니다!",
            "data": result
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"파일 파싱 오류: {str(e)}")

@app.get("/api/vod/list")
async def list_vod_records():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT v.*,
               (SELECT COUNT(*) FROM chats WHERE chats.streamer_id = v.streamer_id AND (chats.broad_no = v.broad_no OR v.broad_no = 'upload')) as actual_chats
        FROM vod_records v
        ORDER BY v.id DESC
    """)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return {"vods": rows}

@app.get("/api/vod/chats")
async def get_vod_chats(
    vod_id: Optional[int] = None,
    streamer_id: Optional[str] = None,
    broad_no: Optional[str] = None,
    query: Optional[str] = None,
    user_search: Optional[str] = None,
    order: str = "asc",
    limit: int = 1000,
    offset: int = 0
):
    conn = get_db()
    cursor = conn.cursor()

    sid = streamer_id
    bno = broad_no
    vod_meta = None

    if vod_id:
        cursor.execute("SELECT * FROM vod_records WHERE id = ?", (vod_id,))
        row = cursor.fetchone()
        if row:
            vod_meta = dict(row)
            sid = vod_meta["streamer_id"]
            bno = vod_meta["broad_no"]

    conditions = []
    params = []

    if sid:
        conditions.append("streamer_id = ?")
        params.append(sid)

    if bno and bno != "upload":
        conditions.append("broad_no = ?")
        params.append(bno)

    if query:
        conditions.append("message LIKE ?")
        params.append(f"%{query}%")

    if user_search:
        conditions.append("(user_id LIKE ? OR user_nick LIKE ?)")
        params.extend([f"%{user_search}%", f"%{user_search}%"])

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    count_sql = f"SELECT COUNT(*) FROM chats {where_clause}"
    cursor.execute(count_sql, params)
    total_count = cursor.fetchone()[0]

    sort_direction = "ASC" if order.lower() == "asc" else "DESC"
    sql = f"""
        SELECT * FROM chats
        {where_clause}
        ORDER BY created_at {sort_direction}, id {sort_direction}
        LIMIT ? OFFSET ?
    """
    cursor.execute(sql, params + [limit, offset])
    chats = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return {
        "vod": vod_meta,
        "total": total_count,
        "limit": limit,
        "offset": offset,
        "chats": chats
    }

@app.get("/api/vod/export")
async def export_vod_chats(
    vod_id: Optional[int] = None,
    streamer_id: Optional[str] = None,
    broad_no: Optional[str] = None,
    format: str = "txt"
):
    conn = get_db()
    cursor = conn.cursor()

    sid = streamer_id
    bno = broad_no
    title = "SOOP_VOD_CHAT"
    nick = "스트리머"
    broad_start = ""

    if vod_id:
        cursor.execute("SELECT * FROM vod_records WHERE id = ?", (vod_id,))
        row = cursor.fetchone()
        if row:
            r = dict(row)
            sid = r["streamer_id"]
            bno = r["broad_no"]
            title = r["broad_title"] or title
            nick = r["streamer_nick"] or sid
            broad_start = r["broad_start"] or ""

    conditions = []
    params = []
    if sid:
        conditions.append("streamer_id = ?")
        params.append(sid)
    if bno and bno != "upload":
        conditions.append("broad_no = ?")
        params.append(bno)

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    sql = f"SELECT * FROM chats {where_clause} ORDER BY created_at ASC, id ASC"
    cursor.execute(sql, params)
    chats = [dict(row) for row in cursor.fetchall()]
    conn.close()

    safe_title = re.sub(r'[\\/*?:"<>|]', '_', title)[:30]
    safe_nick = re.sub(r'[\\/*?:"<>|]', '_', nick)[:20]
    safe_date = (broad_start.split(' ')[0] if broad_start else datetime.datetime.now().strftime('%Y%m%d')).replace('-', '')
    filename_base = f"SOOP_VOD_{safe_nick}_{safe_title}_{safe_date}"

    if format.lower() == "csv":
        output = io.StringIO()
        output.write('\ufeff')
        writer = csv.writer(output)
        writer.writerow(["시간", "스트리머ID", "방송번호", "시청자ID", "시청자닉네임", "채팅내용"])
        for c in chats:
            writer.writerow([c.get("created_at", ""), c.get("streamer_id", ""), c.get("broad_no", ""), c.get("user_id", ""), c.get("user_nick", ""), c.get("message", "")])

        filename = f"{filename_base}.csv"
        quoted_filename = urllib.parse.quote(filename)
        return Response(
            content=output.getvalue().encode('utf-8-sig'),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename=\"{quoted_filename}\"; filename*=UTF-8''{quoted_filename}"}
        )

    elif format.lower() == "json":
        data = {
            "title": title,
            "streamer_id": sid,
            "streamer_nick": nick,
            "broad_start": broad_start,
            "total_chats": len(chats),
            "chats": chats
        }
        filename = f"{filename_base}.json"
        quoted_filename = urllib.parse.quote(filename)
        return Response(
            content=json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'),
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename=\"{quoted_filename}\"; filename*=UTF-8''{quoted_filename}"}
        )

    else:
        lines = [
            "=" * 65,
            f"  SOOP VOD 채팅 복원 기록",
            f"  방송 제목 : {title}",
            f"  스트리머  : {nick} ({sid})",
            f"  방송 일시 : {broad_start}",
            f"  총 채팅 수: {len(chats):,}건",
            f"  출력 일시 : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 65,
            ""
        ]
        for c in chats:
            lines.append(f"[{c.get('created_at', '')}] {c.get('user_nick', '')}({c.get('user_id', '')}): {c.get('message', '')}")

        content_txt = "\n".join(lines)
        filename = f"{filename_base}.txt"
        quoted_filename = urllib.parse.quote(filename)
        return Response(
            content=content_txt.encode('utf-8'),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename=\"{quoted_filename}\"; filename*=UTF-8''{quoted_filename}"}
        )

@app.delete("/api/vod/delete")
async def delete_vod_record(vod_id: int):
    conn = get_db()
    with conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM vod_records WHERE id = ?", (vod_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail="해당 VOD 기록을 찾을 수 없습니다.")
        r = dict(row)
        cursor.execute("DELETE FROM vod_records WHERE id = ?", (vod_id,))
        if r["broad_no"] and r["broad_no"] != "upload":
            cursor.execute("DELETE FROM chats WHERE streamer_id = ? AND broad_no = ?", (r["streamer_id"], r["broad_no"]))
        elif r["broad_no"] == "upload":
            cursor.execute("DELETE FROM chats WHERE streamer_id = ? AND broad_no = 'upload'", (r["streamer_id"],))
    conn.close()
    return {"status": "success", "message": f"'{r['broad_title']}' VOD 내역이 삭제되었습니다."}

# ==================== [채팅 조회, 시청자 검색, 요약 API] ====================

@app.get("/api/chats")
async def get_chats(
    streamer_id: Optional[str] = None,
    query: Optional[str] = None,
    user_search: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
):
    conn = get_db()
    cursor = conn.cursor()

    conditions = []
    params = []

    if streamer_id:
        conditions.append("streamer_id = ?")
        params.append(streamer_id)

    if user_search:
        conditions.append("(user_id LIKE ? OR user_nick LIKE ?)")
        search_pattern = f"%{user_search}%"
        params.extend([search_pattern, search_pattern])

    if query:
        conditions.append("message LIKE ?")
        params.append(f"%{query}%")

    if start_time:
        conditions.append("created_at >= ?")
        params.append(start_time)

    if end_time:
        conditions.append("created_at <= ?")
        params.append(end_time)

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    count_sql = f"SELECT COUNT(*) FROM chats {where_clause}"
    cursor.execute(count_sql, params)
    total_count = cursor.fetchone()[0]

    sql = f"""
        SELECT * FROM chats 
        {where_clause}
        ORDER BY created_at DESC, id DESC
        LIMIT ? OFFSET ?
    """
    cursor.execute(sql, params + [limit, offset])
    chats = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return {
        "total": total_count,
        "limit": limit,
        "offset": offset,
        "chats": chats
    }

@app.get("/api/users/search")
async def search_user_chats(
    q: str = Query(..., min_length=1, description="검색할 시청자 ID 또는 닉네임"),
    streamer_id: Optional[str] = None,
    limit: int = 200,
    offset: int = 0
):
    conn = get_db()
    cursor = conn.cursor()

    conditions = ["(user_id LIKE ? OR user_nick LIKE ?)"]
    pattern = f"%{q}%"
    params = [pattern, pattern]

    if streamer_id:
        conditions.append("streamer_id = ?")
        params.append(streamer_id)

    where_clause = "WHERE " + " AND ".join(conditions)

    count_sql = f"SELECT COUNT(*) FROM chats {where_clause}"
    cursor.execute(count_sql, params)
    total_count = cursor.fetchone()[0]

    profiles_sql = f"""
        SELECT user_id, user_nick, COUNT(*) as chat_count, MAX(created_at) as last_seen
        FROM chats
        {where_clause}
        GROUP BY user_id, user_nick
        ORDER BY chat_count DESC
        LIMIT 20
    """
    cursor.execute(profiles_sql, params)
    profiles = [dict(row) for row in cursor.fetchall()]

    chats_sql = f"""
        SELECT c.*, ch.streamer_nick
        FROM chats c
        LEFT JOIN channels ch ON c.streamer_id = ch.streamer_id
        {where_clause}
        ORDER BY c.created_at DESC, c.id DESC
        LIMIT ? OFFSET ?
    """
    cursor.execute(chats_sql, params + [limit, offset])
    chats = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return {
        "total": total_count,
        "profiles": profiles,
        "chats": chats
    }

def build_chat_content_summary(conn, streamer_id: Optional[str] = None, start_time: Optional[str] = None, end_time: Optional[str] = None) -> Dict[str, Any]:
    cursor = conn.cursor()
    conditions = []
    params = []
    if streamer_id:
        conditions.append("streamer_id = ?")
        params.append(streamer_id)
    if start_time:
        conditions.append("created_at >= ?")
        params.append(start_time)
    if end_time:
        conditions.append("created_at <= ?")
        params.append(end_time)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    total = cursor.execute(f"SELECT COUNT(*) FROM chats {where}", params).fetchone()[0]
    if total == 0:
        return {
            "headline": "기록된 채팅 내역이 없습니다.",
            "narrative": "현재 분석 가능한 채팅 기록이 없습니다. 실시간 수집을 시작하거나 VOD 채팅을 불러와 주세요.",
            "highlights": [],
            "top_phrases": [],
            "mood_stats": {},
            "channel_distribution": []
        }

    users = cursor.execute(f"SELECT COUNT(DISTINCT user_id) FROM chats {where}", params).fetchone()[0]
    first_chat, last_chat = cursor.execute(f"SELECT MIN(created_at), MAX(created_at) FROM chats {where}", params).fetchone()

    # 채널 분포
    ch_dist = []
    if not streamer_id:
        ch_rows = cursor.execute("""
            SELECT c.streamer_id, c.streamer_nick, COUNT(ch.id) as cnt
            FROM channels c
            LEFT JOIN chats ch ON c.streamer_id = ch.streamer_id
            GROUP BY c.streamer_id
            ORDER BY cnt DESC
        """).fetchall()
        for r in ch_rows:
            cnt = r["cnt"]
            pct = round(cnt / total * 100, 1) if total > 0 else 0
            nick = r["streamer_nick"] or r["streamer_id"]
            ch_dist.append({"streamer_id": r["streamer_id"], "streamer_nick": nick, "count": cnt, "percent": pct})
    else:
        cursor.execute("SELECT streamer_nick FROM channels WHERE streamer_id = ?", (streamer_id,))
        s_row = cursor.fetchone()
        nick = s_row["streamer_nick"] if s_row else streamer_id
        ch_dist.append({"streamer_id": streamer_id, "streamer_nick": nick, "count": total, "percent": 100.0})

    # 샘플 분석 (최신 5,000건)
    cursor.execute(f"SELECT message FROM chats {where} ORDER BY id DESC LIMIT 5000", params)
    sample_msgs = [row[0] for row in cursor.fetchall() if row[0]]
    sample_len = len(sample_msgs)

    laughter = 0
    agreement = 0
    question = 0
    cheer = 0
    regret = 0
    phrase_counts = collections.Counter()
    stopwords = {'이', '그', '저', '것', '수', '등', '들', '및', '에서', '그리고', '진짜', '근데', '너무', '그냥', '오늘', '지금', '이거', '저거', '어제', '내일', '우리', '너희'}
    words = collections.Counter()

    for msg in sample_msgs:
        m = msg.strip()
        if len(m) >= 2 and not m.isdigit():
            phrase_counts[m] += 1
        if 'ㅋ' in m: laughter += 1
        if any(w in m for w in ['ㅇㅈ', 'ㄹㅇ', '맞아', '인정', '맞지', '그치']): agreement += 1
        if '?' in m: question += 1
        if any(w in m for w in ['나이스', '와', '대박', '오오', 'ㅅㅅ', '굿', '미쳤다', '레전드', '지렸다']): cheer += 1
        if any(w in m for w in ['ㅠㅠ', 'ㅜㅜ', ':(', '아쉽', '까비', '슬프', '망했']): regret += 1

        for w in re.findall(r'[가-힣a-zA-Z0-9]{2,}', m):
            if w not in stopwords and not w.startswith('http'):
                words[w] += 1

    top_phrases = [{"phrase": p, "count": c} for p, c in phrase_counts.most_common(5)]
    top_words = [w for w, _ in words.most_common(6)]

    # 화력 폭발 하이라이트 (분 단위 집계 상위 3개 구간)
    spike_rows = cursor.execute(f"""
        SELECT substr(created_at, 1, 16) as slot, COUNT(*) as cnt
        FROM chats {where}
        GROUP BY slot
        ORDER BY cnt DESC
        LIMIT 3
    """, params).fetchall()

    highlights = []
    for sp in spike_rows:
        slot = sp["slot"]
        cnt = sp["cnt"]
        cursor.execute(f"""
            SELECT message FROM chats 
            WHERE substr(created_at, 1, 16) = ? {("AND " + " AND ".join(conditions)) if conditions else ""}
            LIMIT 200
        """, [slot] + params)
        slot_msgs = [r[0] for r in cursor.fetchall() if r[0]]
        slot_words = collections.Counter()
        for sm in slot_msgs:
            for w in re.findall(r'[가-힣a-zA-Z0-9]{2,}', sm):
                if w not in stopwords:
                    slot_words[w] += 1
        slot_top_w = [w for w, _ in slot_words.most_common(3)]
        short_time = slot.split(" ")[1] if " " in slot else slot
        desc = f"{short_time}경에 분당 {cnt:,}건의 화력이 폭발했으며, 시청자들은 주로 '{', '.join(slot_top_w) if slot_top_w else '격한 반응'}'에 반응했습니다."
        highlights.append({
            "time_slot": slot,
            "short_time": short_time,
            "count": cnt,
            "top_words": slot_top_w,
            "description": desc
        })

    highlights.sort(key=lambda x: x["time_slot"])

    target_name = "전체 방송" if not streamer_id else f"'{ch_dist[0]['streamer_nick']}' 방송"
    laugh_pct = round(laughter / sample_len * 100, 1) if sample_len > 0 else 0
    agree_pct = round(agreement / sample_len * 100, 1) if sample_len > 0 else 0
    cheer_pct = round(cheer / sample_len * 100, 1) if sample_len > 0 else 0
    regret_pct = round(regret / sample_len * 100, 1) if sample_len > 0 else 0

    top_kw_str = ", ".join([f"#{w}" for w in top_words[:4]]) if top_words else "#채팅"
    headline = f"📢 {target_name} 총 {total:,}건 채팅 정밀 요약 브리핑"

    p1 = f"{first_chat} ~ {last_chat} 동안 기록된 {target_name} 채팅 {total:,}건(참여 시청자 {users:,}명)의 종합 브리핑입니다."
    if not streamer_id and len(ch_dist) > 1:
        top_ch = ch_dist[0]
        p1 += f" 채널 점유율은 **{top_ch['streamer_nick']}** 방송이 {top_ch['count']:,}건({top_ch['percent']}%)으로 가장 높은 비중을 차지했습니다."

    p2 = f"채팅 분위기는 **웃음(ㅋ) 화력 지수 {laugh_pct}%**로 매우 유쾌했으며, 시청자 간 공감 및 동의(ㅇㅈ/ㄹㅇ) 비율은 **{agree_pct}%**, 환호 및 리액션은 **{cheer_pct}%**로 나타났습니다."
    if regret_pct >= 3:
        p2 += f" 아쉬움/슬픔 반응({regret_pct}%)도 일부 감지되어 방송 내 극적인 상황들이 존재했음을 보여줍니다."

    p3 = f"가장 많이 언급된 주요 토픽 키워드는 **{top_kw_str}** 등이었으며, 시청자들이 가장 많이 연호한 단문 1위는 **'{top_phrases[0]['phrase'] if top_phrases else 'ㅋ'}'** ({top_phrases[0]['count'] if top_phrases else 0:,}회)였습니다."

    narrative = f"{p1}\n\n{p2}\n\n{p3}"

    mood_stats = {
        "laughter_count": laughter,
        "laughter_pct": laugh_pct,
        "agreement_count": agreement,
        "agreement_pct": agree_pct,
        "question_count": question,
        "question_pct": round(question / sample_len * 100, 1) if sample_len > 0 else 0,
        "cheer_count": cheer,
        "cheer_pct": cheer_pct,
        "regret_count": regret,
        "regret_pct": regret_pct
    }

    return {
        "headline": headline,
        "narrative": narrative,
        "highlights": highlights,
        "top_phrases": top_phrases,
        "mood_stats": mood_stats,
        "channel_distribution": ch_dist,
        "top_keywords": top_words,
        "total_chats": total,
        "total_users": users,
        "time_range": f"{first_chat} ~ {last_chat}"
    }

@app.get("/api/summary")
async def get_chat_summary(
    streamer_id: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None
):
    conn = get_db()
    cursor = conn.cursor()

    conditions = []
    params = []

    if streamer_id:
        conditions.append("streamer_id = ?")
        params.append(streamer_id)
    if start_time:
        conditions.append("created_at >= ?")
        params.append(start_time)
    if end_time:
        conditions.append("created_at <= ?")
        params.append(end_time)

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    stats_sql = f"""
        SELECT COUNT(*) as total_chats, 
               COUNT(DISTINCT user_id) as total_users,
               MIN(created_at) as first_chat,
               MAX(created_at) as last_chat
        FROM chats {where_clause}
    """
    cursor.execute(stats_sql, params)
    stats = dict(cursor.fetchone())

    timeline_sql = f"""
        SELECT substr(created_at, 1, 16) as time_slot, COUNT(*) as count
        FROM chats {where_clause}
        GROUP BY time_slot
        ORDER BY time_slot ASC
        LIMIT 100
    """
    cursor.execute(timeline_sql, params)
    timeline = [dict(row) for row in cursor.fetchall()]

    top_users_sql = f"""
        SELECT user_id, user_nick, COUNT(*) as chat_count
        FROM chats {where_clause}
        GROUP BY user_id, user_nick
        ORDER BY chat_count DESC
        LIMIT 10
    """
    cursor.execute(top_users_sql, params)
    top_users = [dict(row) for row in cursor.fetchall()]

    messages_sql = f"""
        SELECT message FROM chats {where_clause} ORDER BY id DESC LIMIT 2000
    """
    cursor.execute(messages_sql, params)
    messages = [row[0] for row in cursor.fetchall()]

    stopwords = {'이', '그', '저', '것', '수', '등', '들', '및', '에서', '그리고', '진짜', '근데', '너무', '그냥', '오늘', '지금'}
    word_counts = collections.Counter()
    laughter_count = 0
    question_count = 0

    for msg in messages:
        if not msg:
            continue
        if 'ㅋ' in msg:
            laughter_count += 1
        if '?' in msg:
            question_count += 1
        words = re.findall(r'[가-힣a-zA-Z0-9]{2,}', msg)
        for w in words:
            if w not in stopwords and not w.startswith('http'):
                word_counts[w] += 1

    top_keywords = [{"word": word, "count": count} for word, count in word_counts.most_common(20)]

    # 풍부한 방송 내용 요약 브리핑 생성
    content_summary = build_chat_content_summary(conn, streamer_id, start_time, end_time)
    conn.close()

    return {
        "stats": stats,
        "timeline": timeline,
        "top_users": top_users,
        "top_keywords": top_keywords,
        "laughter_count": laughter_count,
        "question_count": question_count,
        "sample_count": len(messages),
        "content_summary": content_summary
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
