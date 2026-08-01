import React, { createContext, useContext, useState, useEffect, useRef, useCallback } from 'react';
import axios from 'axios';
import { safeStr } from '../utils/helpers';
import { BACKEND_URL, WS_BASE } from '../config';

const NexusContext = createContext();

// Registered at IMPORT time, not in an effect: React runs child effects before
// parent effects, so a page fetching on mount would otherwise race the
// provider and go out with no Authorization header (observed as a 401 on the
// dashboard's attention panel after every reload). Reads sessionStorage
// directly so it always sees the freshest token, including after a refresh.
axios.interceptors.request.use((config) => {
  const token = sessionStorage.getItem('nexus_access_token');
  if (token && !config.headers.Authorization) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

export function useNexus() {
  return useContext(NexusContext);
}

export function NexusProvider({ children }) {

  // ── Auth & Navigation ───────────────────────────────────────
  const [currentUser, setCurrentUser] = useState(() => {
    try {
      const saved = sessionStorage.getItem('nexus_user');
      return saved ? JSON.parse(saved) : null;
    } catch { return null; }
  });

  const accessTokenRef = useRef(sessionStorage.getItem('nexus_access_token') || null);
  // FIX (Bug 2): Refresh token now stored — previously was discarded after login
  const refreshTokenRef = useRef(sessionStorage.getItem('nexus_refresh_token') || null);
  // FIX (Bug 7): Polling fallback ref — fires every 60s as safety net when WS is down
  const pollIntervalRef = useRef(null);
  // FIX (Bug 9): Boot race guard — only connect WS after auth is confirmed
  const authReadyRef    = useRef(!!sessionStorage.getItem('nexus_access_token'));

  const [activeTab, setActiveTab] = useState(() => {
    if (typeof window !== 'undefined') {
      const savedTab = sessionStorage.getItem('nexus_tab');
      if (savedTab) return savedTab;
      try {
        const saved = sessionStorage.getItem('nexus_user');
        const user  = saved ? JSON.parse(saved) : null;
        if (user) return user.role === 'Manager' ? 'dashboard' : 'directives';
      } catch {}
    }
    return 'dashboard';
  });

  // ── Global Data ─────────────────────────────────────────────
  const [tasks,         setTasks]         = useState([]);
  const [employees,     setEmployees]     = useState([]);
  const [meetings,      setMeetings]      = useState([]);
  const [notifications, setNotifications] = useState([]);
  const [unreadCount,   setUnreadCount]   = useState(0);
  const [isSyncing,     setIsSyncing]     = useState(false);
  const [manualCommand, setManualCommand] = useState('');

  // ── UI Selection ─────────────────────────────────────────────
  const [selectedTask, setSelectedTask] = useState(null);
  const [selectedTeam, setSelectedTeam] = useState(() => {
    if (typeof window !== 'undefined') {
      return sessionStorage.getItem('nexus_selected_team') || null;
    }
    return null;
  });

  // ── AI & Voice ───────────────────────────────────────────────
  const [transcript,  setTranscript]  = useState('');
  const [aiResponse,  setAiResponse]  = useState('');
  const [isListening, setIsListening] = useState(false);
  const [isSpeaking,  setIsSpeaking]  = useState(false);
  const [thoughts,    setThoughts]    = useState([]);
  const [isThinking,  setIsThinking]  = useState(false);

  const recognitionRef  = useRef(null);
  const currentAudioRef = useRef(null);

  // ── WebSocket refs ────────────────────────────────────────────
  // FIX: Added wsRef and wsReconnectDelay for auto-reconnect.
  // Previous version never reconnected after disconnect — real-time
  // updates silently stopped on any network hiccup.
  const wsRef            = useRef(null);
  const wsReconnectDelay = useRef(1000); // starts 1s, doubles up to 30s
  // FIX (#22): track the pending reconnect timer so we never stack parallel
  // reconnect chains (onerror→close plus a server close both firing) into
  // duplicate sockets — the untracked setTimeout could spawn several.
  const wsReconnectTimer = useRef(null);

  // Stable ref so WS onmessage always sees the current user
  const currentUserRef = useRef(currentUser);
  useEffect(() => { currentUserRef.current = currentUser; }, [currentUser]);

  // Mirror of notifications for stale-free reads inside async handlers.
  const notificationsRef = useRef(notifications);
  useEffect(() => { notificationsRef.current = notifications; }, [notifications]);

  // Live-typing stream for the in-flight AI command: {id, buf}. Deltas arrive
  // over the WS as STREAM:{id}|{delta}; the POST response stays authoritative.
  const streamRef = useRef(null);

  // ── Shared, de-duplicated access-token refresh ────────────────
  // A single in-flight refresh is shared by ALL callers (a burst of 401s, or a
  // WS auth-close racing an HTTP 401). Without this, each caller would POST its
  // own /auth/refresh, rotating the refresh token N times so all but one become
  // invalid — silently logging the user out mid-session. Returns the new access
  // token, or null on failure (caller decides whether to log out / retry).
  const refreshPromiseRef = useRef(null);
  const refreshAccessToken = useCallback(async () => {
    if (!refreshTokenRef.current) return null;
    if (refreshPromiseRef.current) return refreshPromiseRef.current;

    refreshPromiseRef.current = (async () => {
      try {
        const res = await axios.post(`${BACKEND_URL}/api/v1/auth/refresh`, {
          refresh_token: refreshTokenRef.current,
        });
        const newToken = res.data?.access_token;
        if (!newToken) return null;
        accessTokenRef.current = newToken;
        sessionStorage.setItem('nexus_access_token', newToken);
        // Honor refresh-token rotation if the server issues a new one.
        if (res.data?.refresh_token) {
          refreshTokenRef.current = res.data.refresh_token;
          sessionStorage.setItem('nexus_refresh_token', res.data.refresh_token);
        }
        return newToken;
      } catch {
        return null;
      } finally {
        refreshPromiseRef.current = null;
      }
    })();

    return refreshPromiseRef.current;
  }, [BACKEND_URL]);


  // ── FIX (Bug 2): Auto-refresh access token on 401 ──────────────
  // Previously the refresh token returned by /login was discarded and
  // there was no 401 handler. After 8 hours every request failed silently.
  // This interceptor catches one 401, tries to refresh, retries the
  // original request. If refresh fails, logs the user out cleanly.
  useEffect(() => {
    const responseInterceptor = axios.interceptors.response.use(
      (response) => response,
      async (error) => {
        const originalRequest = error.config;
        // Only try refresh once per request, only on 401, and only if we have a refresh token
        if (
          error.response?.status === 401 &&
          !originalRequest._retry &&
          refreshTokenRef.current &&
          !originalRequest.url?.includes('/auth/refresh')
        ) {
          originalRequest._retry = true;
          // Shared refresh — a burst of parallel 401s triggers ONE /auth/refresh
          // (see refreshAccessToken) instead of N competing rotations.
          const newToken = await refreshAccessToken();
          if (newToken) {
            originalRequest.headers.Authorization = `Bearer ${newToken}`;
            return axios(originalRequest);
          }
          // Refresh failed — log out cleanly
          handleDisconnectInternal();
          return Promise.reject(error);
        }
        return Promise.reject(error);
      }
    );

    return () => {
      axios.interceptors.response.eject(responseInterceptor);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Internal disconnect (used by interceptor before handleDisconnect is defined)
  const handleDisconnectInternal = () => {
    accessTokenRef.current  = null;
    refreshTokenRef.current = null;
    authReadyRef.current    = false;
    if (wsReconnectTimer.current) {
      clearTimeout(wsReconnectTimer.current);
      wsReconnectTimer.current = null;
    }
    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.close();
    }
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
    setCurrentUser(null);
    sessionStorage.removeItem('nexus_user');
    sessionStorage.removeItem('nexus_access_token');
    sessionStorage.removeItem('nexus_refresh_token');
    sessionStorage.removeItem('nexus_tab');
    sessionStorage.removeItem('nexus_selected_team');
  };


  // ── Notifications ────────────────────────────────────────────
  const fetchNotifications = useCallback(async () => {
    const user = currentUserRef.current;
    if (!user?.dbId) return;  // managers now receive proactive alerts too
    try {
      const headers = { Authorization: `Bearer ${accessTokenRef.current}` };
      const res = await axios.get(
        `${BACKEND_URL}/api/v1/notifications/${user.dbId}`,
        { headers }
      );
      setNotifications(res.data?.notifications || []);
      setUnreadCount(res.data?.unread_count    || 0);
    } catch (e) { console.error('Notification fetch error', e); }
  }, [BACKEND_URL]);

  const markNotificationRead = async (notifId) => {
    try {
      const headers = { Authorization: `Bearer ${accessTokenRef.current}` };
      await axios.post(`${BACKEND_URL}/api/v1/notifications/read/${notifId}`, {}, { headers });
      // Only decrement the badge if this notification was actually unread —
      // re-marking an already-read one must not drive the count negative or
      // desync it from the true unread total. (#24)
      const target = notificationsRef.current.find(n => n.id === notifId);
      const wasUnread = target ? !target.is_read : false;
      setNotifications(prev => prev.map(n => n.id === notifId ? { ...n, is_read: true } : n));
      if (wasUnread) setUnreadCount(prev => Math.max(0, prev - 1));
    } catch (e) { console.error('Mark read error', e); }
  };

  const markAllNotificationsRead = async () => {
    const user = currentUserRef.current;
    if (!user?.dbId) return;
    try {
      const headers = { Authorization: `Bearer ${accessTokenRef.current}` };
      await axios.post(`${BACKEND_URL}/api/v1/notifications/read-all/${user.dbId}`, {}, { headers });
      setNotifications(prev => prev.map(n => ({ ...n, is_read: true })));
      setUnreadCount(0);
    } catch (e) { console.error('Mark all read error', e); }
  };


  // ── Data Fetching ────────────────────────────────────────────
  const fetchDashboardData = useCallback(async () => {
    try {
      setIsSyncing(true);
      const headers = { Authorization: `Bearer ${accessTokenRef.current}` };
      const [taskRes, empRes, meetRes] = await Promise.all([
        axios.get(`${BACKEND_URL}/api/v1/tasks/`,     { headers }).catch(() => ({ data: { tasks:     [] } })),
        axios.get(`${BACKEND_URL}/api/v1/employees/`, { headers }).catch(() => ({ data: { employees: [] } })),
        axios.get(`${BACKEND_URL}/api/v1/meetings/`,  { headers }).catch(() => ({ data: { meetings:  [] } })),
      ]);

      const newTasks = Array.isArray(taskRes.data?.tasks)    ? taskRes.data.tasks     : [];
      const newEmps  = Array.isArray(empRes.data?.employees) ? empRes.data.employees  : [];
      const newMeets = Array.isArray(meetRes.data?.meetings) ? meetRes.data.meetings  : [];

      setTasks(prev     => JSON.stringify(prev) === JSON.stringify(newTasks) ? prev : newTasks);
      setEmployees(prev => JSON.stringify(prev) === JSON.stringify(newEmps)  ? prev : newEmps);
      setMeetings(prev  => JSON.stringify(prev) === JSON.stringify(newMeets) ? prev : newMeets);
      await fetchNotifications();
    } catch (error) {
      console.error('Sync error', error);
    } finally {
      setIsSyncing(false);
    }
  }, [BACKEND_URL, fetchNotifications]);


  // ── WebSocket with Auto-Reconnect ────────────────────────────
  const connectWebSocket = useCallback(() => {
    if (!currentUserRef.current) return;

    // Cancel any pending reconnect — we're connecting now, so a queued retry
    // would only create a duplicate socket.
    if (wsReconnectTimer.current) {
      clearTimeout(wsReconnectTimer.current);
      wsReconnectTimer.current = null;
    }

    // Clean up any existing dead connection
    if (wsRef.current) {
      const state = wsRef.current.readyState;
      if (state === WebSocket.OPEN || state === WebSocket.CONNECTING) {
        wsRef.current.onclose = null; // Prevent reconnect loop on manual close
        wsRef.current.close();
      }
    }

    // Room-based WS — server needs employee_id to route messages correctly
    const employeeId = currentUserRef.current.dbId;
    // Auth: the WS now requires a valid token whose subject matches employeeId
    const wsToken    = accessTokenRef.current || '';
    // The token rides in Sec-WebSocket-Protocol, not the URL: query strings are
    // written to proxy access logs and browser history, and this is an 8-hour
    // credential. Browsers can't set arbitrary headers on a WS handshake, but
    // the subprotocol list is one they will send. The server echoes back
    // 'nexus-auth' to complete the handshake.
    const wsUrl      = `${WS_BASE}/api/v1/ws/${employeeId}`;

    try {
      // Per-socket flag: a failed HANDSHAKE (expired token → server closes
      // before accept) never fires onopen, and the browser reports a generic
      // 1006 instead of our 1008 — so "never opened" is also treated as auth
      // failure below and triggers a token refresh before reconnecting.
      let wsEverOpened = false;
      const ws = new WebSocket(wsUrl, ['nexus-auth', wsToken]);
      wsRef.current = ws;

      ws.onopen = () => {
        console.log('🌐 Nexus WS connected');
        wsEverOpened = true;
        wsReconnectDelay.current = 1000; // Reset backoff on successful connect
      };

      ws.onmessage = (event) => {
        const data = event.data;

        if (data === 'SYNC_REQUIRED') {
          fetchDashboardData();
          return;
        }

        // ── STREAM: live-typing for the current AI command ────────
        if (data.startsWith('STREAM_RESET:')) {
          if (streamRef.current && data.slice(13) === streamRef.current.id) streamRef.current.buf = '';
          return;
        }
        if (data.startsWith('STREAM_END:')) return;   // POST response is authoritative
        if (data.startsWith('STREAM:')) {
          const rest = data.slice(7);
          const sep = rest.indexOf('|');
          if (sep > 0 && streamRef.current && rest.slice(0, sep) === streamRef.current.id) {
            streamRef.current.buf += rest.slice(sep + 1);
            setIsThinking(false);
            setAiResponse(streamRef.current.buf);
          }
          return;
        }

        // ── NOTIF: real-time notification push ─────────────────────
        // FIX (Wiring Gap): Server sends NOTIF: messages but frontend
        // had no handler. Real-time notification badge now updates
        // without waiting for a SYNC_REQUIRED.
        if (data.startsWith('NOTIF:')) {
          try {
            const notif = JSON.parse(data.slice(6));
            setNotifications(prev => [notif, ...prev]);
            setUnreadCount(prev => prev + 1);
          } catch (e) {
            console.error('NOTIF parse error', e);
          }
          return;
        }

        // ── CHAT: new chat message in a channel ────────────────────
        // FIX (Wiring Gap): Server sends CHAT:{channel_id}|{json} but
        // frontend had no handler. Stub for now — chat UI is Phase 3.
        // We dispatch a custom event so the chat page (when built) can listen.
        if (data.startsWith('CHAT:')) {
          try {
            const pipeIdx = data.indexOf('|');
            const channelId = parseInt(data.slice(5, pipeIdx), 10);
            const message   = JSON.parse(data.slice(pipeIdx + 1));
            window.dispatchEvent(new CustomEvent('nexus:chat', {
              detail: { channelId, message },
            }));
          } catch (e) {
            console.error('CHAT parse error', e);
          }
          return;
        }

        // ── MEETING: meeting event (participant joined, ended, etc) ──
        // FIX (Wiring Gap): Stub for now — meeting UI is Phase 6.
        if (data.startsWith('MEETING:')) {
          try {
            const pipeIdx   = data.indexOf('|');
            const meetingId = parseInt(data.slice(8, pipeIdx), 10);
            const eventData = JSON.parse(data.slice(pipeIdx + 1));
            window.dispatchEvent(new CustomEvent('nexus:meeting', {
              detail: { meetingId, event: eventData },
            }));
          } catch (e) {
            console.error('MEETING parse error', e);
          }
          return;
        }

        // ── THOUGHT: Glass Brain / system messages ─────────────────
        if (data.startsWith('THOUGHT:')) {
          const rawPayload = data.replace('THOUGHT:', '').trim();

          if (!rawPayload.includes('|')) {
            setIsThinking(false);
            setAiResponse(`[AI THOUGHT]: ${rawPayload}`);
            return;
          }

          const pipeIdx       = rawPayload.indexOf('|');
          const targetAgentId = rawPayload.slice(0, pipeIdx).trim();
          const thought       = rawPayload.slice(pipeIdx + 1).trim();

          const user = currentUserRef.current;
          const myAgentId = user?.role === 'Manager'
            ? 'Manager_1'
            : `Employee_${user?.dbId}`;

          if (targetAgentId.toLowerCase() !== myAgentId.toLowerCase()) return;

          if (thought.includes('[GLASS BRAIN]')) {
            const cleanThought = thought.split('[GLASS BRAIN]')[1].trim();
            setThoughts(prev => [...prev, cleanThought]);
            setIsThinking(true);
          } else {
            setIsThinking(false);
            setAiResponse(`[SYSTEM AUDIT]: ${thought}`);
          }
        }
      };

      ws.onerror = () => {
        // Close triggers onclose which handles reconnect
        ws.close();
      };

      ws.onclose = (event) => {
        if (!currentUserRef.current) return;          // User logged out — don't reconnect
        if (wsRef.current && wsRef.current !== ws) return; // Superseded by a newer socket
        if (wsReconnectTimer.current) return;         // A reconnect is already queued (#22)

        const delay = wsReconnectDelay.current;
        wsReconnectDelay.current = Math.min(delay * 2, 30000); // Cap at 30s

        // FIX (#6 HIGH): if the socket was rejected for auth (expired access
        // token in the handshake — code 1008, or a failed handshake that never
        // opened → 1006), refresh the token BEFORE reconnecting. Otherwise we'd
        // loop forever re-presenting the same dead token and real-time silently
        // dies once the access token's ~8h lifetime expires.
        const authFailure = event?.code === 1008 || !wsEverOpened;
        console.log(`🌐 Nexus WS disconnected. Reconnecting in ${delay}ms...`);

        wsReconnectTimer.current = setTimeout(async () => {
          wsReconnectTimer.current = null;
          if (!currentUserRef.current) return;
          if (authFailure) {
            await refreshAccessToken(); // updates accessTokenRef → reconnect uses fresh token
          }
          connectWebSocket();
        }, delay);
      };
    } catch (e) {
      console.error('WS connection error:', e);
    }
  }, [fetchDashboardData, refreshAccessToken]);


  // ── Boot: initial data fetch + WS ───────────────────────────
  // FIX (Bug 9): Only connect WS if we have a valid auth token.
  // Previously WS connected on mount even if session was expired.
  // FIX (Bug 7): Polling fallback — refreshes data every 60s as a
  // safety net in case WS drops and reconnect backoff is high.
  useEffect(() => {
    if (authReadyRef.current && currentUser) {
      fetchDashboardData();
      connectWebSocket();

      // Sessions created before sysRole existed: backfill it once from /auth/me
      // (team leads logged in with an old session would otherwise miss My Team).
      if (currentUser.sysRole === undefined) {
        axios.get(`${BACKEND_URL}/api/v1/auth/me`).then(r => {
          setCurrentUser(prev => {
            if (!prev) return prev;
            const upd = { ...prev, sysRole: r.data?.role };
            sessionStorage.setItem('nexus_user', JSON.stringify(upd));
            return upd;
          });
        }).catch(() => {});
      }

      // Polling fallback: fires every 60s — only triggers a refresh if WS isn't connected
      pollIntervalRef.current = setInterval(() => {
        const wsOpen = wsRef.current?.readyState === WebSocket.OPEN;
        if (!wsOpen) {
          fetchDashboardData();
        }
      }, 60000);
    }

    try {
      if ('speechSynthesis' in window) {
        window.speechSynthesis.onvoiceschanged = () => window.speechSynthesis.getVoices();
        window.speechSynthesis.getVoices();
      }
    } catch (e) { console.error('Speech init error', e); }

    return () => {
      // On unmount, close WS cleanly without triggering reconnect
      if (wsReconnectTimer.current) {
        clearTimeout(wsReconnectTimer.current);
        wsReconnectTimer.current = null;
      }
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
      }
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
        pollIntervalRef.current = null;
      }
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps


  // ── selectedTask live sync ────────────────────────────────────
  // FIX: Previous version had [tasks, selectedTask] as dependencies.
  // When tasks synced → setSelectedTask called → selectedTask changed
  // → effect ran again → infinite loop whenever the task modal was open.
  //
  // Fix: use functional setState (receives prev, no dependency on selectedTask)
  // and deep-compare to avoid unnecessary re-renders.
  useEffect(() => {
    setSelectedTask(prev => {
      if (!prev) return prev;
      const updated = tasks.find(t => t.id === prev.id);
      if (!updated) return prev;
      return JSON.stringify(updated) !== JSON.stringify(prev) ? updated : prev;
    });
  }, [tasks]);


  // ── Tab & team persistence ───────────────────────────────────
  useEffect(() => {
    sessionStorage.setItem('nexus_tab', activeTab);
    if (activeTab !== 'team') {
      setSelectedTeam(null);
      sessionStorage.removeItem('nexus_selected_team');
    }
  }, [activeTab]);

  useEffect(() => {
    if (selectedTeam) sessionStorage.setItem('nexus_selected_team', selectedTeam);
    else              sessionStorage.removeItem('nexus_selected_team');
  }, [selectedTeam]);


  // ── Voice Engine ─────────────────────────────────────────────
  const stopSpeaking = () => {
    if (currentAudioRef.current) {
      currentAudioRef.current.pause();
      currentAudioRef.current.currentTime = 0;
      currentAudioRef.current = null;
    }
    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    setIsSpeaking(false);
  };

  // Strip emojis, markdown, and decorative characters before speaking
  const cleanForSpeech = (text) => {
    if (!text) return '';
    return text
      // Remove emojis (full unicode emoji range)
      .replace(/[\u{1F300}-\u{1F9FF}]|[\u{2600}-\u{27BF}]|[\u{1F000}-\u{1F02F}]|[\u{1F0A0}-\u{1F0FF}]|[\u{1F100}-\u{1F1FF}]|[\u{1F200}-\u{1F2FF}]|[\u{1FA00}-\u{1FA6F}]|[\u{1FA70}-\u{1FAFF}]|[\u{2300}-\u{23FF}]|[\u{2B00}-\u{2BFF}]|[\u{1F680}-\u{1F6FF}]/gu, '')
      // Remove markdown: ** _ ` # > | etc
      .replace(/\*\*/g, '')
      .replace(/[*_`~]/g, '')
      .replace(/^#+\s+/gm, '')
      .replace(/^\s*[->•]\s+/gm, '')
      // Strip checkbox-style markers from task lists
      .replace(/\[\s*[X ]?\s*\]/g, '')
      // Pipes and dashes used in tables/IDs
      .replace(/\s*\|\s*/g, '. ')
      // Multi-newline → single sentence break
      .replace(/\n{2,}/g, '. ')
      .replace(/\n/g, ' ')
      // Cleanup multiple spaces and odd punctuation runs
      .replace(/\s+/g, ' ')
      .replace(/\.\s*\.+/g, '.')
      .trim();
  };

  const speakText = async (text) => {
    stopSpeaking();
    const cleaned = cleanForSpeech(text);
    if (!cleaned) return;

    try {
      const response = await axios.post(
        `${BACKEND_URL}/api/v1/manager/speak`,
        { text: cleaned },
        { responseType: 'blob' }
      );
      const audioUrl = window.URL.createObjectURL(new Blob([response.data]));
      const audio    = new Audio(audioUrl);
      currentAudioRef.current = audio;
      audio.onplay  = () => setIsSpeaking(true);
      audio.onended = () => setIsSpeaking(false);
      audio.onerror = () => setIsSpeaking(false);
      await audio.play();
    } catch {
      const fallback = new SpeechSynthesisUtterance(cleaned);
      fallback.onstart = () => setIsSpeaking(true);
      fallback.onend   = () => setIsSpeaking(false);
      window.speechSynthesis.speak(fallback);
    }
  };

  const toggleListening = () => {
    if (isListening) {
      if (recognitionRef.current) recognitionRef.current.stop();
      setIsListening(false);
      if (transcript.trim() !== '') sendCommandToNexus(transcript);
      return;
    }
    stopSpeaking();
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) return alert('Please use Chrome or Edge for voice features.');
    try {
      const recognition = new SpeechRecognition();
      recognitionRef.current = recognition;
      recognition.continuous      = true;
      recognition.interimResults  = true;
      recognition.onstart  = () => { setIsListening(true); setTranscript(''); };
      recognition.onresult = (event) => {
        let currentText = '';
        for (let i = 0; i < event.results.length; i++) currentText += event.results[i][0].transcript;
        setTranscript(currentText);
      };
      recognition.onerror = () => { setAiResponse('Microphone error.'); setIsListening(false); };
      recognition.onend   = () => setIsListening(false);
      recognition.start();
    } catch (e) { console.error('Mic error', e); }
  };


  // ── AI Command Sender ────────────────────────────────────────
  const sendCommandToNexus = async (text) => {
    const trimmed = safeStr(text).trim();
    if (!trimmed || !currentUser) return;

    setThoughts([]);
    setIsThinking(true);
    setAiResponse('Processing directive...');
    setTranscript(`[SENT]: "${trimmed}"`);

    try {
      const dynamicManagerId = currentUser.role === 'Manager'
        ? 'Manager_1'
        : `Employee_${currentUser.dbId}`;

      const streamId = (window.crypto?.randomUUID?.() || `s${Date.now()}`).replace(/[^A-Za-z0-9_-]/g, '');
      streamRef.current = { id: streamId, buf: '' };

      const res = await axios.post(`${BACKEND_URL}/api/v1/manager/command`, {
        manager_id:   dynamicManagerId,
        command_text: trimmed,
        input_method: isListening ? 'voice' : 'manual',
        stream_id:    streamId,
      });
      streamRef.current = null;

      setIsThinking(false);
      const finalResponse = safeStr(res.data?.ai_response || 'Directive complete.');
      setAiResponse(finalResponse);
      speakText(finalResponse);
    } catch (error) {
      setIsThinking(false);
      setAiResponse('Backend connection error.');
      speakText('Error connecting to mainframe.');
    }
  };

  const handleDbAction = (commandString) => {
    setActiveTab('commands');
    sendCommandToNexus(commandString);
  };

  const handleCompleteSubtask = async (subtaskId) => {
    await sendCommandToNexus(`Complete subtask ID ${subtaskId}`);
  };

  const handleQuickComplete = async (taskId) => {
    await sendCommandToNexus(`Mark task ID ${taskId} as complete`);
    setSelectedTask(null);
  };

  const handlePeerRequestAction = async (reqId, action) => {
    try {
      await axios.post(
        `${BACKEND_URL}/api/v1/peer-requests/${reqId}/respond`,
        { action }
      );
      await fetchDashboardData();
    } catch (error) { console.error('Peer request update failed', error); }
  };


  // ── Auth ─────────────────────────────────────────────────────
  const handleLogin = async (name, password) => {
    try {
      const res = await axios.post(`${BACKEND_URL}/api/v1/auth/login`, { name, password });
      // FIX (Bug 2): refresh_token was previously discarded — now stored
      const { access_token, refresh_token, user } = res.data;

      const userObj = {
        role: user.role === 'manager' ? 'Manager' : 'Employee',
        sysRole: user.role,   // raw system_role: manager | team_lead | employee
        dbId: user.id,
        name: user.name,
        team: user.team,
      };

      accessTokenRef.current  = access_token;
      refreshTokenRef.current = refresh_token;
      authReadyRef.current    = true;
      sessionStorage.setItem('nexus_access_token',  access_token);
      sessionStorage.setItem('nexus_refresh_token', refresh_token);
      sessionStorage.setItem('nexus_user', JSON.stringify(userObj));

      setCurrentUser(userObj);
      setActiveTab(user.role === 'manager' ? 'dashboard' : 'directives');

      // Load the dashboard immediately — the poll below only fires when the
      // WebSocket is DOWN, so without this the app showed zeros after every
      // fresh login until some unrelated sync happened to arrive.
      fetchDashboardData();

      // Open WebSocket now that user is logged in
      wsReconnectDelay.current = 1000;
      connectWebSocket();

      // Start polling fallback
      if (!pollIntervalRef.current) {
        pollIntervalRef.current = setInterval(() => {
          const wsOpen = wsRef.current?.readyState === WebSocket.OPEN;
          if (!wsOpen) fetchDashboardData();
        }, 60000);
      }

      return null;
    } catch (err) {
      return err.response?.data?.detail || 'Login failed. Check your credentials.';
    }
  };

  const handleDisconnect = () => {
    if (accessTokenRef.current) {
      axios.post(
        `${BACKEND_URL}/api/v1/auth/logout`,
        {},
        { headers: { Authorization: `Bearer ${accessTokenRef.current}` } }
      ).catch(() => {});
    }
    handleDisconnectInternal();
    stopSpeaking();
  };


  // ── Context Value ────────────────────────────────────────────
  const value = {
    currentUser, setCurrentUser,
    handleLogin, handleDisconnect,
    activeTab, setActiveTab,
    tasks, employees, meetings,
    notifications, unreadCount,
    markNotificationRead, markAllNotificationsRead,
    isSyncing, fetchDashboardData,
    selectedTask, setSelectedTask,
    selectedTeam, setSelectedTeam,
    manualCommand, setManualCommand,
    transcript, setTranscript,
    aiResponse, setAiResponse,
    isListening, isSpeaking,
    toggleListening, sendCommandToNexus,
    handleDbAction,
    handleCompleteSubtask, handleQuickComplete, handlePeerRequestAction,
    thoughts, setThoughts,
    isThinking, setIsThinking,
    stopSpeaking,
    BACKEND_URL,
  };

  return (
    <NexusContext.Provider value={value}>
      {children}
    </NexusContext.Provider>
  );
}