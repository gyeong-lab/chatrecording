// 전역 상태 변수
let currentStreamerId = "";
let currentActiveStreamer = null;
let timelineChart = null;
let pollInterval = null;

// DOM 로드 완료 후 실행
document.addEventListener("DOMContentLoaded", () => {
    loadChannels();
    loadChats();
    loadMonitoredStreamers();
    startPolling();
});

// 탭 전환
function switchTab(tabName) {
    document.querySelectorAll(".tab-btn").forEach(btn => {
        btn.classList.remove("text-white", "bg-blue-600", "shadow-sm");
        btn.classList.add("text-slate-400");
    });
    const activeBtn = document.getElementById(`tab-${tabName}`);
    if (activeBtn) {
        activeBtn.classList.remove("text-slate-400");
        activeBtn.classList.add("text-white", "bg-blue-600", "shadow-sm");
    }

    document.querySelectorAll(".tab-content").forEach(content => {
        content.classList.add("hidden");
    });
    const activeSection = document.getElementById(`section-${tabName}`);
    if (activeSection) {
        activeSection.classList.remove("hidden");
    }

    if (tabName === "summary") {
        loadSummary();
    } else if (tabName === "monitor") {
        loadMonitoredStreamers();
    }
}

// 주기적 자동 폴링 (2초)
function startPolling() {
    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(() => {
        const autoRefresh = document.getElementById("autoRefreshToggle").checked;
        const realtimeTab = document.getElementById("section-realtime");
        if (autoRefresh && !realtimeTab.classList.contains("hidden")) {
            loadChats(false);
            loadChannels();
        }
    }, 2000);
}

// 채널 추가 모달 열기/닫기
function openAddChannelModal() {
    const modal = document.getElementById("addChannelModal");
    if (modal) {
        modal.classList.remove("hidden");
        const input = document.getElementById("addChannelInput");
        if (input) {
            input.value = "";
            input.focus();
        }
    }
}

function closeAddChannelModal() {
    const modal = document.getElementById("addChannelModal");
    if (modal) modal.classList.add("hidden");
}

function handleAddChannelEnter(e) {
    if (e.key === "Enter") {
        submitAddChannel();
    }
}

async function submitAddChannel() {
    const input = document.getElementById("addChannelInput");
    const val = input.value.trim();
    if (!val) {
        alert("스트리머 ID 또는 채널 링크를 입력해주세요.");
        return;
    }

    try {
        const res = await fetch("/api/channels/add", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url_or_id: val })
        });
        const data = await res.json();
        if (!res.ok) {
            alert(data.detail || "채널 등록 실패");
            return;
        }
        alert(data.message);
        closeAddChannelModal();
        loadChannels();
        loadSummary();
    } catch (e) {
        alert("채널 등록 중 오류가 발생했습니다.");
    }
}

async function deleteChannel(e, streamerId, streamerNick) {
    if (e) {
        e.preventDefault();
        e.stopPropagation();
    }
    if (!confirm(`'${streamerNick}' 채널을 삭제하시겠습니까?\n(수집된 모든 채팅 내역도 함께 삭제됩니다)`)) {
        return;
    }

    try {
        const res = await fetch("/api/channels/delete", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ streamer_id: streamerId, delete_chats: true })
        });
        const data = await res.json();
        if (!res.ok) {
            alert(data.detail || "삭제 실패");
            return;
        }
        alert(data.message);
        if (currentStreamerId === streamerId) {
            currentStreamerId = "";
            document.getElementById("activeChannelBadge").innerText = "채널: 전체";
        }
        loadChannels();
        loadChats(true);
        loadSummary();
    } catch (err) {
        alert("채널 삭제 요청 중 오류가 발생했습니다.");
    }
}

// 채널 목록 가져오기 & 상단 상태 표시
async function loadChannels() {
    try {
        const res = await fetch("/api/channels");
        const data = await res.json();
        const channels = data.channels || [];

        const badgeContainer = document.getElementById("recordedChannelsList");
        let html = `
            <div class="flex items-center gap-1.5 shrink-0 mr-1">
                <span class="text-xs text-slate-500 font-medium">기록된 채널:</span>
                <button onclick="openAddChannelModal()" class="px-2 py-0.5 rounded-md text-[11px] font-semibold bg-blue-600/20 text-blue-400 hover:bg-blue-600/30 border border-blue-500/30 flex items-center gap-1 transition-all" title="새로운 채널 등록">
                    <i data-lucide="plus" class="w-3 h-3"></i> 채널 추가
                </button>
            </div>
        `;

        let activeFound = false;

        channels.forEach(ch => {
            const isRecording = ch.is_active;
            if (isRecording) {
                activeFound = true;
                currentActiveStreamer = ch;
            }

            const isSelected = currentStreamerId === ch.streamer_id;
            html += `
                <div class="group/badge inline-flex items-center rounded-lg text-xs font-medium transition-all shrink-0 
                    ${isSelected ? 'bg-blue-600 text-white font-bold shadow-md shadow-blue-500/20' : 'bg-slate-800 text-slate-300 hover:bg-slate-700'}">
                    <button onclick="selectChannel('${ch.streamer_id}')" class="px-2.5 py-1 flex items-center gap-1.5">
                        <span class="w-2 h-2 rounded-full ${isRecording ? 'bg-emerald-400 animate-ping' : 'bg-slate-500'}"></span>
                        <span>${escapeHtml(ch.streamer_nick || ch.streamer_id)}</span>
                        <span class="text-[10px] opacity-75">(${ch.total_chats || 0}건)</span>
                    </button>
                    <button onclick="deleteChannel(event, '${ch.streamer_id}', '${escapeHtml(ch.streamer_nick || ch.streamer_id)}')" 
                        title="채널 삭제" 
                        class="pr-2 pl-0.5 py-1 text-slate-400 hover:text-rose-400 opacity-60 hover:opacity-100 transition-all">
                        <i data-lucide="trash-2" class="w-3 h-3"></i>
                    </button>
                </div>
            `;
        });

        if (channels.length === 0) {
            html += '<span class="text-xs text-slate-600">등록된 채널이 없습니다. (+ 채널 추가 버튼으로 등록)</span>';
        }

        badgeContainer.innerHTML = html;
        lucide.createIcons();

        // 상태창 업데이트
        const statusDot = document.getElementById("statusDot");
        const statusText = document.getElementById("currentStreamerText");
        const btnStop = document.getElementById("btnStopRecord");

        if (activeFound && currentActiveStreamer) {
            statusDot.className = "w-3 h-3 rounded-full bg-emerald-500 animate-pulse";
            statusText.innerHTML = `<span class="text-emerald-400 font-bold">${escapeHtml(currentActiveStreamer.streamer_nick)}</span> 실시간 수집 중`;
            btnStop.style.display = "flex";
        } else {
            statusDot.className = "w-3 h-3 rounded-full bg-slate-600";
            statusText.innerText = "수집 대기 중";
            btnStop.style.display = "none";
        }

    } catch (e) {
        console.error("채널 로드 실패:", e);
    }
}

// 채널 선택 필터
function selectChannel(streamerId) {
    if (currentStreamerId === streamerId) {
        currentStreamerId = ""; // 토글 해제
    } else {
        currentStreamerId = streamerId;
    }
    document.getElementById("activeChannelBadge").innerText = currentStreamerId ? `채널: ${currentStreamerId}` : "채널: 전체";
    loadChannels();
    loadChats(true);
}

// 수집 시작
async function startRecording() {
    const input = document.getElementById("streamerInput");
    const val = input.value.trim();
    if (!val) {
        alert("방송 링크 또는 스트리머 ID를 입력해주세요!");
        return;
    }

    try {
        const res = await fetch("/api/record/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url_or_id: val })
        });
        const data = await res.json();

        if (!res.ok) {
            alert(data.detail || "수집 시작 실패");
            return;
        }

        alert(data.message);
        currentStreamerId = data.streamer_id;
        input.value = "";
        loadChannels();
        loadChats(true);
    } catch (e) {
        alert("네트워크 통신 중 오류가 발생했습니다.");
    }
}

// 수집 중지
async function stopRecording() {
    if (!currentActiveStreamer) return;
    if (!confirm(`'${currentActiveStreamer.streamer_nick}' 채널 수집을 중지하시겠습니까?`)) return;

    try {
        const res = await fetch("/api/record/stop", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ streamer_id: currentActiveStreamer.streamer_id })
        });
        const data = await res.json();
        alert(data.message);
        loadChannels();
    } catch (e) {
        alert("중지 요청 중 오류 발생");
    }
}

// 실시간 채팅 로드
async function loadChats(scrollToBottom = false) {
    const keyword = document.getElementById("chatSearchKeyword").value.trim();
    const userSearch = document.getElementById("chatSearchUser").value.trim();
    const startTime = document.getElementById("filterStartTime").value;
    const endTime = document.getElementById("filterEndTime").value;

    let url = `/api/chats?limit=150`;
    if (currentStreamerId) url += `&streamer_id=${encodeURIComponent(currentStreamerId)}`;
    if (keyword) url += `&query=${encodeURIComponent(keyword)}`;
    if (userSearch) url += `&user_search=${encodeURIComponent(userSearch)}`;
    if (startTime) url += `&start_time=${encodeURIComponent(startTime.replace('T', ' '))}`;
    if (endTime) url += `&end_time=${encodeURIComponent(endTime.replace('T', ' '))}`;

    try {
        const res = await fetch(url);
        const data = await res.json();
        const chats = data.chats || [];

        document.getElementById("chatTotalCounter").innerText = `총 ${data.total.toLocaleString()}개`;

        const container = document.getElementById("chatMessageContainer");
        if (chats.length === 0) {
            container.innerHTML = `
                <div class="h-full flex flex-col items-center justify-center text-slate-500 space-y-2">
                    <i data-lucide="inbox" class="w-10 h-10 text-slate-600"></i>
                    <p class="text-sm">기록된 채팅이 없습니다.</p>
                </div>
            `;
            lucide.createIcons();
            return;
        }

        // 최신 메시지 렌더링
        let html = "";
        chats.forEach(chat => {
            const timeStr = chat.created_at.split(" ")[1] || chat.created_at;
            html += `
                <div class="group flex items-start gap-3 p-2 rounded-xl hover:bg-slate-800/60 transition-all border border-transparent hover:border-slate-800">
                    <span class="text-[11px] font-mono text-slate-500 shrink-0 mt-0.5">${timeStr}</span>
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2 mb-0.5">
                            <button onclick="quickSearchUser('${chat.user_id}')" class="text-xs font-bold text-blue-400 hover:text-blue-300 hover:underline">
                                ${escapeHtml(chat.user_nick)}
                            </button>
                            <span class="text-[10px] text-slate-500">(${escapeHtml(chat.user_id)})</span>
                        </div>
                        <div class="text-sm text-slate-200 break-words leading-relaxed select-text">${escapeHtml(chat.message)}</div>
                    </div>
                </div>
            `;
        });

        container.innerHTML = html;

    } catch (e) {
        console.error("채팅 로드 오류:", e);
    }
}

// 시청자 닉네임 클릭 시 바로 검색 탭으로 이동
function quickSearchUser(userId) {
    switchTab('userSearch');
    document.getElementById("targetUserQuery").value = userId;
    searchTargetUser();
}

// 실시간 검색 엔터 처리
function handleRealtimeSearch(e) {
    if (e.key === "Enter") {
        loadChats(true);
    }
}

// 필터 초기화
function resetFilters() {
    document.getElementById("chatSearchKeyword").value = "";
    document.getElementById("chatSearchUser").value = "";
    document.getElementById("filterStartTime").value = "";
    document.getElementById("filterEndTime").value = "";
    loadChats(true);
}

// ==================== [24시간 자동 감시 스트리머 기능 (방법 1)] ====================

// 상단 입력바에서 바로 자동 감시 등록
async function quickAddMonitorFromBar() {
    const input = document.getElementById("streamerInput");
    const val = input.value.trim();
    if (!val) {
        alert("방송 링크 또는 스트리머 ID를 입력해주세요!");
        return;
    }
    await addMonitoredStreamerExplicit(val, true);
    input.value = "";
}

// 감시 대상 등록
async function addMonitoredStreamer() {
    const input = document.getElementById("monitorInput");
    const val = input.value.trim();
    const autoRec = document.getElementById("monitorAutoRecordCheck").checked;
    if (!val) {
        alert("감시할 스트리머 ID 또는 채널 링크를 입력하세요!");
        return;
    }
    await addMonitoredStreamerExplicit(val, autoRec);
    input.value = "";
}

async function addMonitoredStreamerExplicit(urlOrId, autoRecord) {
    try {
        const res = await fetch("/api/monitor/add", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url_or_id: urlOrId, auto_record: autoRecord })
        });
        const data = await res.json();
        if (!res.ok) {
            alert(data.detail || "등록 실패");
            return;
        }
        alert(data.message);
        loadMonitoredStreamers();
        loadChannels();
    } catch (e) {
        alert("등록 중 네트워크 오류 발생");
    }
}

// 감시 대상 목록 로드
async function loadMonitoredStreamers() {
    try {
        const res = await fetch("/api/monitor");
        const data = await res.json();
        const streamers = data.streamers || [];

        const container = document.getElementById("monitoredListContainer");
        if (streamers.length === 0) {
            container.innerHTML = `<div class="col-span-full py-12 text-center text-slate-500 text-sm">등록된 감시 스트리머가 없습니다. 위에서 스트리머를 등록해보세요!</div>`;
            return;
        }

        let html = "";
        streamers.forEach(s => {
            const isLive = (s.last_status === "live");
            const isCollecting = s.is_collecting;
            const isMonitoring = Boolean(s.is_monitoring);
            const autoRecord = Boolean(s.auto_record);

            html += `
                <div class="bg-slate-950 border border-slate-800 rounded-xl p-4 flex flex-col justify-between hover:border-slate-700 transition-all">
                    <div>
                        <div class="flex items-center justify-between mb-2">
                            <span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-bold 
                                ${isLive ? 'bg-rose-500/20 text-rose-400 border border-rose-500/30' : 'bg-slate-800 text-slate-400'}">
                                <span class="w-2 h-2 rounded-full ${isLive ? 'bg-rose-500 animate-ping' : 'bg-slate-600'}"></span>
                                ${isLive ? '생방송 중 (ON-AIR)' : '오프라인 (대기 중)'}
                            </span>
                            <span class="text-xs text-slate-500">${s.last_checked_at ? s.last_checked_at.split(' ')[1] : ''} 확인</span>
                        </div>

                        <div class="font-bold text-base text-white flex items-center gap-1.5">
                            ${escapeHtml(s.streamer_nick || s.streamer_id)}
                            <span class="text-xs text-slate-500 font-normal">(${escapeHtml(s.streamer_id)})</span>
                        </div>
                        <div class="text-xs text-slate-400 truncate mt-0.5 mb-3">${escapeHtml(s.broad_title || '방송 제목 없음')}</div>

                        <div class="flex items-center gap-2 mb-3 text-xs text-slate-400">
                            <span>기록된 채팅: <strong class="text-blue-400 font-bold">${(s.total_chats || 0).toLocaleString()}개</strong></span>
                            ${isCollecting ? '<span class="px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-400 text-[10px] font-bold">수집 작동 중</span>' : ''}
                        </div>
                    </div>

                    <div class="pt-3 border-t border-slate-800/80 flex items-center justify-between">
                        <div class="flex items-center gap-3 text-xs">
                            <label class="flex items-center gap-1 cursor-pointer text-slate-400 hover:text-white" title="방송 시작 감지 켜기/끄기">
                                <input type="checkbox" ${isMonitoring ? 'checked' : ''} onchange="toggleMonitorField('${s.streamer_id}', 'is_monitoring')" class="rounded bg-slate-800 text-emerald-500 focus:ring-0">
                                감시
                            </label>
                            <label class="flex items-center gap-1 cursor-pointer text-slate-400 hover:text-white" title="방송 시 자동 수집 시작 여부">
                                <input type="checkbox" ${autoRecord ? 'checked' : ''} onchange="toggleMonitorField('${s.streamer_id}', 'auto_record')" class="rounded bg-slate-800 text-emerald-500 focus:ring-0">
                                자동수집
                            </label>
                        </div>
                        <button onclick="deleteMonitoredStreamer('${s.streamer_id}')" class="text-xs text-rose-400 hover:text-rose-300 px-2 py-1 hover:bg-rose-500/10 rounded transition-all">
                            해제
                        </button>
                    </div>
                </div>
            `;
        });

        container.innerHTML = html;
        lucide.createIcons();

    } catch (e) {
        console.error("감시 스트리머 목록 로드 실패:", e);
    }
}

// 감시 설정 토글
async function toggleMonitorField(streamerId, field) {
    try {
        const res = await fetch("/api/monitor/toggle", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ streamer_id: streamerId, field: field })
        });
        if (!res.ok) {
            alert("설정 변경 실패");
            loadMonitoredStreamers();
        }
    } catch (e) {
        alert("네트워크 오류");
    }
}

// 감시 대상 삭제
async function deleteMonitoredStreamer(streamerId) {
    if (!confirm(`'${streamerId}' 감시를 해제하시겠습니까?`)) return;
    try {
        const res = await fetch("/api/monitor/delete", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ streamer_id: streamerId })
        });
        const data = await res.json();
        alert(data.message);
        loadMonitoredStreamers();
    } catch (e) {
        alert("해제 중 오류 발생");
    }
}

// ==================== [과거 다시보기(VOD) 채팅 복원 기능 (방법 2)] ====================

// VOD URL로 과거 채팅 복원
async function importVodChat() {
    const input = document.getElementById("vodUrlInput");
    const val = input.value.trim();
    if (!val) {
        alert("다시보기(VOD) 링크 또는 번호를 입력해주세요!");
        return;
    }

    const btn = document.getElementById("btnImportVod");
    const originalText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span class="animate-spin mr-2">⏳</span> 과거 채팅 복원 중... (수만 건 처리)`;

    try {
        const res = await fetch("/api/vod/import", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url_or_no: val })
        });
        const result = await res.json();

        if (!res.ok) {
            alert(result.detail || "VOD 채팅 복원에 실패했습니다.");
            return;
        }

        const data = result.data;
        const resultBox = document.getElementById("vodResultBox");
        resultBox.classList.remove("hidden");
        document.getElementById("vodResultTitle").innerText = `복원 성공! 총 ${data.chats_imported.toLocaleString()}건 저장 완료`;
        document.getElementById("vodResultDesc").innerText = 
            `스트리머: ${data.streamer_nick}(${data.streamer_id}) | 제목: ${data.broad_title} | 방송시간: ${data.broad_start}`;

        alert(result.message);
        input.value = "";
        currentStreamerId = data.streamer_id;
        loadChannels();
        loadChats(true);

    } catch (e) {
        alert("복원 요청 중 오류가 발생했습니다.");
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}

// 채팅 로그 파일 직접 업로드
async function uploadChatLogFile() {
    const fileInput = document.getElementById("chatFileInput");
    if (!fileInput.files || fileInput.files.length === 0) {
        alert("업로드할 .txt 또는 .csv 채팅 파일을 선택해주세요!");
        return;
    }

    const file = fileInput.files[0];
    const sid = document.getElementById("chatFileStreamerId").value.trim();

    const formData = new FormData();
    formData.append("file", file);
    if (sid) formData.append("streamer_id", sid);

    try {
        const res = await fetch("/api/vod/upload", {
            method: "POST",
            body: formData
        });
        const result = await res.json();
        if (!res.ok) {
            alert(result.detail || "파일 처리 실패");
            return;
        }
        alert(result.message);
        fileInput.value = "";
        loadChannels();
        loadChats(true);
    } catch (e) {
        alert("파일 업로드 중 오류 발생");
    }
}

// ==================== [시청자 검색 기능] ====================

async function searchTargetUser() {
    const q = document.getElementById("targetUserQuery").value.trim();
    if (!q) {
        alert("검색할 닉네임 또는 아이디를 입력하세요!");
        return;
    }

    try {
        const res = await fetch(`/api/users/search?q=${encodeURIComponent(q)}&limit=200`);
        const data = await res.json();

        const resultsArea = document.getElementById("userSearchResultsArea");
        resultsArea.style.display = "block";

        const profilesContainer = document.getElementById("userProfilesCardList");
        const historyContainer = document.getElementById("userChatHistoryContainer");

        document.getElementById("userChatCountBadge").innerText = `총 ${data.total.toLocaleString()}건`;

        // 1. 프로필 카드 렌더링
        let profHtml = "";
        data.profiles.forEach(p => {
            profHtml += `
                <div class="bg-slate-950 border border-slate-800 rounded-xl p-4 flex items-center justify-between">
                    <div>
                        <div class="text-sm font-bold text-white flex items-center gap-1.5">
                            <i data-lucide="user" class="w-3.5 h-3.5 text-indigo-400"></i>
                            ${escapeHtml(p.user_nick)}
                        </div>
                        <div class="text-xs text-slate-400">ID: ${escapeHtml(p.user_id)}</div>
                        <div class="text-[11px] text-slate-500 mt-1">최근 활동: ${p.last_seen}</div>
                    </div>
                    <div class="text-right">
                        <span class="text-lg font-extrabold text-indigo-400">${p.chat_count}</span>
                        <span class="text-xs text-slate-500 block">채팅 수</span>
                    </div>
                </div>
            `;
        });
        profilesContainer.innerHTML = profHtml;

        // 2. 채팅 내역 렌더링
        let chatHtml = "";
        if (data.chats.length === 0) {
            chatHtml = `<div class="p-6 text-center text-sm text-slate-500">작성한 채팅 내역이 없습니다.</div>`;
        } else {
            data.chats.forEach(c => {
                chatHtml += `
                    <div class="bg-slate-950/60 border border-slate-800/80 rounded-xl p-3 flex items-start justify-between gap-4">
                        <div class="flex-1 min-w-0">
                            <div class="flex items-center gap-2 mb-1">
                                <span class="text-xs font-bold text-indigo-300">${escapeHtml(c.user_nick)}</span>
                                <span class="text-[10px] text-slate-500">(${escapeHtml(c.user_id)})</span>
                                <span class="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400">${escapeHtml(c.streamer_nick || c.streamer_id)} 방송</span>
                            </div>
                            <div class="text-sm text-slate-200 break-words leading-relaxed">${escapeHtml(c.message)}</div>
                        </div>
                        <span class="text-xs text-slate-500 font-mono shrink-0">${c.created_at}</span>
                    </div>
                `;
            });
        }
        historyContainer.innerHTML = chatHtml;
        lucide.createIcons();

    } catch (e) {
        alert("검색 중 오류 발생");
    }
}

// ==================== [분석 & 요약 탭 데이터 로드] ====================

let currentSummaryData = null;

// ==================== [분석 & 요약 탭 데이터 로드] ====================

async function loadSummary() {
    let url = "/api/summary";
    if (currentStreamerId) url += `?streamer_id=${encodeURIComponent(currentStreamerId)}`;

    try {
        const res = await fetch(url);
        const data = await res.json();
        currentSummaryData = data;

        // 0. 헤더 뱃지 업데이트
        const badgeEl = document.getElementById("summaryChannelBadge");
        if (badgeEl) {
            badgeEl.innerText = currentStreamerId ? (currentActiveStreamer ? currentActiveStreamer.streamer_nick : currentStreamerId) + " 방송" : "전체 방송 종합";
        }

        // 1. 통계 카드 채우기
        const stats = data.stats || {};
        document.getElementById("statTotalChats").innerText = `${(stats.total_chats || 0).toLocaleString()}개`;
        document.getElementById("statTotalUsers").innerText = `${(stats.total_users || 0).toLocaleString()}명`;
        document.getElementById("statLaughterCount").innerText = `${(data.laughter_count || 0).toLocaleString()}회`;
        document.getElementById("statQuestionCount").innerText = `${(data.question_count || 0).toLocaleString()}회`;

        if (stats.first_chat && stats.last_chat) {
            document.getElementById("statTimeRange").innerText = `${stats.first_chat.split(' ')[1] || stats.first_chat} ~ ${stats.last_chat.split(' ')[1] || stats.last_chat}`;
        }

        // 2. 스마트 내용 종합 브리핑 채우기
        const cs = data.content_summary || {};
        const narrativeEl = document.getElementById("summaryNarrativeText");
        if (narrativeEl && cs.narrative) {
            let formatted = escapeHtml(cs.narrative);
            formatted = formatted.replace(/\*\*(.*?)\*\*/g, '<strong class="text-white font-bold">$1</strong>');
            narrativeEl.innerHTML = formatted;
        }

        // 3. 감정 및 분위기 지수 렌더링
        const moodContainer = document.getElementById("moodStatsContainer");
        if (moodContainer && cs.mood_stats) {
            const ms = cs.mood_stats;
            moodContainer.innerHTML = `
                <div>
                    <div class="flex justify-between text-xs mb-1">
                        <span class="text-slate-300 font-medium">😄 웃음 화력 (ㅋ)</span>
                        <span class="text-amber-400 font-bold">${ms.laughter_pct}% (${(ms.laughter_count || 0).toLocaleString()}회)</span>
                    </div>
                    <div class="w-full bg-slate-800 rounded-full h-2 overflow-hidden">
                        <div class="bg-amber-400 h-2 rounded-full transition-all duration-500" style="width: ${Math.min(100, ms.laughter_pct)}%"></div>
                    </div>
                </div>
                <div>
                    <div class="flex justify-between text-xs mb-1">
                        <span class="text-slate-300 font-medium">🤝 공감 및 인정 (ㅇㅈ / ㄹㅇ)</span>
                        <span class="text-blue-400 font-bold">${ms.agreement_pct}% (${(ms.agreement_count || 0).toLocaleString()}회)</span>
                    </div>
                    <div class="w-full bg-slate-800 rounded-full h-2 overflow-hidden">
                        <div class="bg-blue-400 h-2 rounded-full transition-all duration-500" style="width: ${Math.min(100, ms.agreement_pct)}%"></div>
                    </div>
                </div>
                <div>
                    <div class="flex justify-between text-xs mb-1">
                        <span class="text-slate-300 font-medium">🔥 열띤 환호/응원 (나이스 / 와 / 대박)</span>
                        <span class="text-rose-400 font-bold">${ms.cheer_pct}% (${(ms.cheer_count || 0).toLocaleString()}회)</span>
                    </div>
                    <div class="w-full bg-slate-800 rounded-full h-2 overflow-hidden">
                        <div class="bg-rose-400 h-2 rounded-full transition-all duration-500" style="width: ${Math.min(100, ms.cheer_pct)}%"></div>
                    </div>
                </div>
                <div>
                    <div class="flex justify-between text-xs mb-1">
                        <span class="text-slate-300 font-medium">❓ 질문 및 소통 반응 (?)</span>
                        <span class="text-purple-400 font-bold">${ms.question_pct}% (${(ms.question_count || 0).toLocaleString()}회)</span>
                    </div>
                    <div class="w-full bg-slate-800 rounded-full h-2 overflow-hidden">
                        <div class="bg-purple-400 h-2 rounded-full transition-all duration-500" style="width: ${Math.min(100, ms.question_pct)}%"></div>
                    </div>
                </div>
            `;
        }

        // 4. 최다 빈출 한마디 TOP 5 렌더링
        const phrasesContainer = document.getElementById("topPhrasesContainer");
        if (phrasesContainer && cs.top_phrases) {
            let prHtml = "";
            cs.top_phrases.forEach((tp, idx) => {
                prHtml += `
                    <div class="flex items-center justify-between p-2 rounded-lg bg-slate-900/80 border border-slate-800/80">
                        <div class="flex items-center gap-2">
                            <span class="text-[11px] font-bold px-1.5 py-0.2 rounded ${idx === 0 ? 'bg-indigo-500/20 text-indigo-300 border border-indigo-500/30' : 'bg-slate-800 text-slate-400'}">#${idx + 1}</span>
                            <span class="text-xs text-slate-200 font-medium truncate max-w-[220px]">"${escapeHtml(tp.phrase)}"</span>
                        </div>
                        <span class="text-xs font-semibold text-indigo-400 font-mono">${tp.count.toLocaleString()}회</span>
                    </div>
                `;
            });
            if (cs.top_phrases.length === 0) {
                prHtml = '<div class="text-xs text-slate-500 p-2 text-center">집계된 한마디가 없습니다.</div>';
            }
            phrasesContainer.innerHTML = prHtml;
        }

        // 5. 화력 폭발 하이라이트 타임라인 그리드 렌더링
        const highlightsGrid = document.getElementById("highlightsTimelineGrid");
        if (highlightsGrid && cs.highlights) {
            let hlHtml = "";
            cs.highlights.forEach((hl, idx) => {
                const wordsBadges = (hl.top_words || []).map(w => `<span class="px-1.5 py-0.5 rounded bg-rose-500/10 text-rose-300 text-[10px] border border-rose-500/20">#${escapeHtml(w)}</span>`).join(" ");
                hlHtml += `
                    <div class="bg-slate-950/70 border border-slate-800 rounded-xl p-3.5 flex flex-col justify-between space-y-2 hover:border-slate-700 transition-colors">
                        <div class="flex items-center justify-between">
                            <span class="text-xs font-mono font-bold text-blue-400 flex items-center gap-1">
                                <i data-lucide="clock" class="w-3.5 h-3.5 text-blue-400"></i> ${hl.short_time}
                            </span>
                            <span class="text-xs font-bold px-2 py-0.5 rounded-full bg-rose-500/20 text-rose-400 border border-rose-500/30">
                                분당 ${hl.count.toLocaleString()}건 🔥
                            </span>
                        </div>
                        <p class="text-xs text-slate-300 leading-snug">${escapeHtml(hl.description)}</p>
                        <div class="flex flex-wrap gap-1 pt-1 border-t border-slate-800/60">
                            ${wordsBadges}
                        </div>
                    </div>
                `;
            });
            if (cs.highlights.length === 0) {
                hlHtml = '<div class="col-span-full p-4 text-center text-xs text-slate-500">감지된 화력 집중 구간이 없습니다.</div>';
            }
            highlightsGrid.innerHTML = hlHtml;
        }

        // 6. 타임라인 그래프 업데이트
        renderTimelineChart(data.timeline || []);

        // 7. 인기 키워드 TOP 20
        const keywordContainer = document.getElementById("keywordListContainer");
        let kwHtml = "";
        data.top_keywords.forEach((k, idx) => {
            kwHtml += `
                <div class="flex items-center justify-between p-2 rounded-lg bg-slate-950/60 border border-slate-800/80">
                    <div class="flex items-center gap-2">
                        <span class="text-xs font-bold w-5 text-center ${idx < 3 ? 'text-rose-400' : 'text-slate-500'}">${idx + 1}</span>
                        <span class="text-xs font-medium text-slate-200">${escapeHtml(k.word)}</span>
                    </div>
                    <span class="text-xs font-semibold px-2 py-0.5 rounded-full bg-slate-800 text-slate-400">${k.count}회</span>
                </div>
            `;
        });
        if (data.top_keywords.length === 0) {
            kwHtml = `<div class="p-4 text-center text-xs text-slate-500">수집된 키워드 데이터가 없습니다.</div>`;
        }
        keywordContainer.innerHTML = kwHtml;

        // 8. 채팅왕 TOP 10
        const topGrid = document.getElementById("topChattersGrid");
        let topHtml = "";
        data.top_users.forEach((u, idx) => {
            topHtml += `
                <div class="bg-slate-950 border border-slate-800 rounded-xl p-3 flex flex-col justify-between">
                    <div class="flex items-center justify-between mb-2">
                        <span class="text-xs font-bold px-1.5 py-0.5 rounded ${idx === 0 ? 'bg-amber-500/20 text-amber-400 border border-amber-500/30' : 'bg-slate-800 text-slate-400'}">TOP ${idx + 1}</span>
                        <span class="text-xs font-extrabold text-blue-400">${u.chat_count}회</span>
                    </div>
                    <div>
                        <div class="text-xs font-bold text-white truncate">${escapeHtml(u.user_nick)}</div>
                        <div class="text-[10px] text-slate-500 truncate">ID: ${escapeHtml(u.user_id)}</div>
                    </div>
                </div>
            `;
        });
        if (data.top_users.length === 0) {
            topHtml = `<div class="col-span-full p-4 text-center text-xs text-slate-500">데이터가 없습니다.</div>`;
        }
        topGrid.innerHTML = topHtml;

        lucide.createIcons();

    } catch (e) {
        console.error("요약 데이터 로드 실패:", e);
    }
}

// 요약문 클립보드 복사
function copySummaryText() {
    if (!currentSummaryData || !currentSummaryData.content_summary) {
        alert("복사할 요약 데이터가 없습니다.");
        return;
    }
    const cs = currentSummaryData.content_summary;
    let text = `[방송 채팅 내용 정밀 요약 리포트]\n\n${cs.headline || ''}\n\n${cs.narrative || ''}\n\n`;
    if (cs.highlights && cs.highlights.length > 0) {
        text += `[주요 장면 화력 폭발 하이라이트]\n`;
        cs.highlights.forEach(h => {
            text += `- ${h.short_time}: ${h.description}\n`;
        });
        text += `\n`;
    }
    if (cs.top_phrases && cs.top_phrases.length > 0) {
        text += `[시청자 최다 빈출 한마디 TOP 5]\n`;
        cs.top_phrases.forEach((p, idx) => {
            text += `${idx + 1}. "${p.phrase}" (${p.count}회)\n`;
        });
    }

    navigator.clipboard.writeText(text).then(() => {
        alert("요약 리포트가 클립보드에 복사되었습니다! 📋");
    }).catch(() => {
        alert("클립보드 복사에 실패했습니다.");
    });
}

// Chart.js 타임라인 차트 렌더링
function renderTimelineChart(timeline) {
    const ctx = document.getElementById("timelineChart").getContext("2d");
    const labels = timeline.map(t => t.time_slot.split(" ")[1] || t.time_slot);
    const counts = timeline.map(t => t.count);

    if (timelineChart) {
        timelineChart.destroy();
    }

    timelineChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: '채팅 발생량',
                data: counts,
                borderColor: '#3b82f6',
                backgroundColor: 'rgba(59, 130, 246, 0.1)',
                borderWidth: 2,
                fill: true,
                tension: 0.35,
                pointRadius: 3,
                pointHoverRadius: 6
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: {
                    grid: { color: '#1e293b' },
                    ticks: { color: '#64748b', font: { size: 10 } }
                },
                y: {
                    grid: { color: '#1e293b' },
                    ticks: { color: '#64748b', font: { size: 10 } },
                    beginAtZero: true
                }
            },
            plugins: {
                legend: { display: false }
            }
        }
    });
}

// XSS 방지 유틸
function escapeHtml(str) {
    if (!str) return "";
    return String(str).replace(/[&<>"']/g, function(m) {
        return {
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#039;'
        }[m];
    });
}
