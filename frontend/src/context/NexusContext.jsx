import React, { createContext, useContext, useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { safeStr } from '../utils/helpers';

// Create the Context
const NexusContext = createContext();

// Custom hook so any file can easily access the global state
export function useNexus() {
  return useContext(NexusContext);
}

// The Provider Component
export function NexusProvider({ children }) {
  const BACKEND_URL = `http://${window.location.hostname}:8000`;

  // --- AUTH & NAVIGATION STATE ---
  // On load, try to restore session from sessionStorage
  const [currentUser, setCurrentUser] = useState(() => {
    try {
      const saved = sessionStorage.getItem('nexus_user');
      return saved ? JSON.parse(saved) : null;
    } catch { return null; }
  });

  // Token lives in memory only (more secure than localStorage)
  // Refresh token lives in sessionStorage to survive page refresh
  const accessTokenRef = useRef(sessionStorage.getItem('nexus_access_token') || null);

  const [activeTab, setActiveTab] = useState(() => {
    if (typeof window !== 'undefined') {
      const savedTab = sessionStorage.getItem('nexus_tab');
      if (savedTab) return savedTab;
      if (currentUser) return currentUser.role === 'Manager' ? 'dashboard' : 'directives';
    }
    return 'dashboard';
  });

  // --- GLOBAL DATA STATE ---
  const [tasks, setTasks] = useState([]);
  const [employees, setEmployees] = useState([]);
  const [meetings, setMeetings] = useState([]);
  const [notifications, setNotifications] = useState([]);
  const [unreadCount, setUnreadCount]     = useState(0);
  const [isSyncing, setIsSyncing] = useState(false);

  const [manualCommand, setManualCommand] = useState('');

  // --- UI SELECTION STATE ---
  const [selectedTask, setSelectedTask] = useState(null);
  const [selectedTeam, setSelectedTeam] = useState(() => {
    if (typeof window !== 'undefined') {
      return sessionStorage.getItem('nexus_selected_team') || null;
    }
    return null;
  });

  // --- AI & VOICE STATE ---
  const [transcript, setTranscript] = useState('');
  const [aiResponse, setAiResponse] = useState('System standing by.');
  const [isListening, setIsListening] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const recognitionRef = useRef(null);
  const currentAudioRef = useRef(null);

  // --- NEW GLASS BRAIN STATES ---
  const [thoughts, setThoughts] = useState([]); 
  const [isThinking, setIsThinking] = useState(false);

  const fetchNotifications = async () => {
    if (!currentUser?.dbId || currentUser?.role === 'Manager') return;
    try {
      const headers = { Authorization: `Bearer ${accessTokenRef.current}` };
      const res = await axios.get(
        `${BACKEND_URL}/api/v1/notifications/${currentUser.dbId}`,
        { headers }
      );
      setNotifications(res.data?.notifications || []);
      setUnreadCount(res.data?.unread_count || 0);
    } catch (e) { console.error('Notification fetch error', e); }
  };

  const markNotificationRead = async (notifId) => {
    try {
      const headers = { Authorization: `Bearer ${accessTokenRef.current}` };
      await axios.post(
        `${BACKEND_URL}/api/v1/notifications/read/${notifId}`,
        {},
        { headers }
      );
      setNotifications(prev =>
        prev.map(n => n.id === notifId ? { ...n, is_read: true } : n)
      );
      setUnreadCount(prev => Math.max(0, prev - 1));
    } catch (e) { console.error('Mark read error', e); }
  };

  const markAllNotificationsRead = async () => {
    if (!currentUser?.dbId) return;
    try {
      const headers = { Authorization: `Bearer ${accessTokenRef.current}` };
      await axios.post(
        `${BACKEND_URL}/api/v1/notifications/read-all/${currentUser.dbId}`,
        {},
        { headers }
      );
      setNotifications(prev => prev.map(n => ({ ...n, is_read: true })));
      setUnreadCount(0);
    } catch (e) { console.error('Mark all read error', e); }
  };

  // --- DATA FETCHING ---
  const fetchDashboardData = async () => {
    try {
      setIsSyncing(true);
      const headers = { Authorization: `Bearer ${accessTokenRef.current}` };
      const [taskRes, empRes, meetRes] = await Promise.all([
        axios.get(`${BACKEND_URL}/api/v1/tasks/`,     { headers }).catch(() => ({ data: { tasks: [] } })),
        axios.get(`${BACKEND_URL}/api/v1/employees/`, { headers }).catch(() => ({ data: { employees: [] } })),
        axios.get(`${BACKEND_URL}/api/v1/meetings/`,  { headers }).catch(() => ({ data: { meetings: [] } })),
      ]);

      const newTasks = Array.isArray(taskRes.data?.tasks) ? taskRes.data.tasks : [];
      const newEmps = Array.isArray(empRes.data?.employees) ? empRes.data.employees : [];
      const newMeets = Array.isArray(meetRes.data?.meetings) ? meetRes.data.meetings : [];

      setTasks(prev => JSON.stringify(prev) === JSON.stringify(newTasks) ? prev : newTasks);
      setEmployees(prev => JSON.stringify(prev) === JSON.stringify(newEmps) ? prev : newEmps);
      setMeetings(prev => JSON.stringify(prev) === JSON.stringify(newMeets) ? prev : newMeets);
      // Refresh notifications every time data syncs
      await fetchNotifications();
    } catch (error) { console.error('Sync error', error); }
    finally { setIsSyncing(false); }
  };

  // Ref to always have fresh currentUser inside the WebSocket callback
  const currentUserRef = useRef(currentUser);
  useEffect(() => { currentUserRef.current = currentUser; }, [currentUser]);

  // --- LIFECYCLE EFFECTS (WEBSOCKET UPGRADE) ---
  useEffect(() => {
    // 1. Fetch initial data on load
    fetchDashboardData();

    // 2. Open the WebSocket Bridge
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.hostname}:8000/api/v1/ws`;
    const ws = new WebSocket(wsUrl);

    ws.onmessage = (event) => {
      // 1. Handle Automatic Syncing
      if (event.data === "SYNC_REQUIRED") {
        console.log("⚡ WebSocket Ping: Database updated! Refreshing UI...");
        fetchDashboardData();
        return;
      }

      // 2. Handle AI "Thoughts" and System Audits
      if (event.data.startsWith("THOUGHT:")) {
        const rawPayload = event.data.replace("THOUGHT:", "").trim();

        // Messages now use full agent_id as prefix: "Manager_1|..." or "Employee_3|..."
        // This prevents Employee_2's thoughts from showing on Employee_3's screen
        if (rawPayload.includes("|")) {
          const [targetAgentId, ...messageParts] = rawPayload.split("|");
          const thought = messageParts.join("|");

          // Build the current user's agent_id to match against
          const user = currentUserRef.current;
          const myAgentId = user?.role === 'Manager'
            ? 'Manager_1'
            : `Employee_${user?.dbId}`;

          const isMatch = targetAgentId.trim().toLowerCase() === myAgentId.toLowerCase();

          if (isMatch) {
            if (thought.includes("[GLASS BRAIN]")) {
              const cleanThought = thought.split("[GLASS BRAIN]")[1].trim();
              setThoughts(prev => [...prev, cleanThought]);
              setIsThinking(true);
            } else {
              setIsThinking(false);
              setAiResponse(`[SYSTEM AUDIT]: ${thought}`);
              if (typeof speakText === 'function') {
                speakText(thought);
              }
            }
          }
          // Silently ignore messages meant for other agents — no log spam
        } else {
          // Fallback if no pipe character exists
          setIsThinking(false);
          setAiResponse(`[AI THOUGHT]: ${rawPayload}`);
        }
      }
    };
    
    ws.onopen = () => console.log("🌐 Nexus Core WebSocket Link Established.");
    ws.onclose = () => console.log("🌐 Nexus Core WebSocket Disconnected.");
    // Initialize Speech Engine
    try {
      if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
        window.speechSynthesis.onvoiceschanged = () => window.speechSynthesis.getVoices();
        window.speechSynthesis.getVoices();
      }
    } catch (e) { console.error("Speech init error", e); }

    // 4. Cleanup function to close the bridge if component unmounts
    return () => {
      ws.close();
    };
  }, []); // Empty dependency array means this only runs once on boot!

  useEffect(() => {
    if (selectedTask) {
      const updated = tasks.find(t => t.id === selectedTask.id);
      if (updated) setSelectedTask(updated);
    }
  }, [tasks, selectedTask]);

  // --- TAB & TEAM STATE MANAGEMENT ---
  useEffect(() => {
    sessionStorage.setItem('nexus_tab', activeTab);
    if (activeTab !== 'team') {
      setSelectedTeam(null);
      sessionStorage.removeItem('nexus_selected_team');
    }
  }, [activeTab]);

  useEffect(() => {
    if (selectedTeam) {
      sessionStorage.setItem('nexus_selected_team', selectedTeam);
    } else {
      sessionStorage.removeItem('nexus_selected_team');
    }
  }, [selectedTeam]);

  // --- AI ENGINE & VOICE FUNCTIONS ---
 // --- THE KILL SWITCH ---
 const stopSpeaking = () => {
  // 1. Stop the MP3 if it's playing
  if (currentAudioRef.current) {
    currentAudioRef.current.pause();
    currentAudioRef.current.currentTime = 0;
    currentAudioRef.current = null;
  }
  // 2. Stop the browser fallback if it was used
  if ('speechSynthesis' in window) {
    window.speechSynthesis.cancel();
  }
  // 3. Turn off the orb animation
  setIsSpeaking(false);
};

const speakText = async (text) => {
  stopSpeaking(); 
  
  // FIX: Do NOT set isSpeaking(true) here! We must wait for the audio to load first!
  
  try {
    const response = await axios.post(`${BACKEND_URL}/api/v1/manager/speak`, 
      { text: text }, 
      { responseType: 'blob' }
    );

    const audioUrl = window.URL.createObjectURL(new Blob([response.data]));
    const audio = new Audio(audioUrl);
    
    currentAudioRef.current = audio; 

    // FIX: The absolute millisecond the audio starts playing, turn on the Orb animation!
    audio.onplay = () => {
      setIsSpeaking(true);
    };

    audio.onended = () => {
      setIsSpeaking(false);
    };
    
    audio.onerror = () => {
      setIsSpeaking(false);
    };

    await audio.play();

  } catch (error) {
    console.error("Neural Voice Engine failed to load.", error);
    
    const fallback = new SpeechSynthesisUtterance(text.replace(/[*#_`~\[\]]/g, ''));
    fallback.onstart = () => setIsSpeaking(true);
    fallback.onend = () => setIsSpeaking(false); 
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
    if (!SpeechRecognition) return alert('Please use Chrome or Edge browser for voice features.');
    try {
      const recognition = new SpeechRecognition();
      recognitionRef.current = recognition; recognition.continuous = true; recognition.interimResults = true;
      recognition.onstart = () => { setIsListening(true); setTranscript(''); }
      recognition.onresult = (event) => {
        let currentText = '';
        for (let i = 0; i < event.results.length; i += 1) currentText += event.results[i][0].transcript;
        setTranscript(currentText);
      }
      recognition.onerror = () => { setAiResponse('Microphone error.'); setIsListening(false); }
      recognition.onend = () => setIsListening(false);
      recognition.start();
    } catch (e) { console.error("Mic error", e); }
  };
  const sendCommandToNexus = async (text) => {
    const trimmed = safeStr(text).trim();
    if (!trimmed || !currentUser) return;
    
    // --- RESET AND START THE BRAIN ---
    setThoughts([]); // Clear old telemetry logs
    setIsThinking(true); // Start the Gemini spinner
    setAiResponse('Processing directive...'); 
    setTranscript(`[SENT]: "${trimmed}"`);
    
    try {
      const dynamicManagerId = currentUser.role === 'Manager' ? 'Manager_1' : `Employee_${currentUser.dbId}`;
      const res = await axios.post(`${BACKEND_URL}/api/v1/manager/command`, {
        manager_id: dynamicManagerId, command_text: trimmed, input_method: isListening ? 'voice' : 'manual',
      });
      
      // --- STOP THE SPINNER ---
      setIsThinking(false); // <--- THE FIX!
      
      const finalResponse = safeStr(res.data?.ai_response || 'Directive complete.');
      setAiResponse(finalResponse); 
      
      // Safety check for the voice engine
      if (typeof speakText === 'function') {
        speakText(finalResponse); 
      }
    } catch (error) {
      // Stop the spinner even if it crashes!
      setIsThinking(false); 
      setAiResponse('Backend connection error.'); 
      
      if (typeof speakText === 'function') {
        speakText("Error connecting to mainframe.");
      }
    }
  };

  const handleDbAction = (commandString) => {
      setActiveTab('commands');
      sendCommandToNexus(commandString);
  };
  
  const handleCompleteSubtask = async (subtaskId) => { await sendCommandToNexus(`Complete subtask ID ${subtaskId}`); }
  const handleQuickComplete = async (taskId) => { await sendCommandToNexus(`Mark task ID ${taskId} as complete`); setSelectedTask(null); }
  
  const handlePeerRequestAction = async (reqId, action) => {
    try {
      await axios.post(`${BACKEND_URL}/api/v1/peer-requests/${reqId}/respond`, { action });
      // Force the UI to refresh with the new data instantly
      await fetchDashboardData(); 
    } catch (error) { console.error("Failed to update peer request", error); }
  }

  // --- AUTH FUNCTIONS ---
  const handleLogin = async (name, password) => {
    try {
      const res = await axios.post(`${BACKEND_URL}/api/v1/auth/login`, { name, password });
      const { access_token, user } = res.data;

      // Build the user object matching existing structure
      const userObj = {
        role: user.role === 'manager' ? 'Manager' : 'Employee',
        dbId: user.id,
        name: user.name,
        team: user.team,
      };

      // Store token in ref (memory) and sessionStorage
      accessTokenRef.current = access_token;
      sessionStorage.setItem('nexus_access_token', access_token);
      sessionStorage.setItem('nexus_user', JSON.stringify(userObj));

      setCurrentUser(userObj);
      setActiveTab(user.role === 'manager' ? 'dashboard' : 'directives');

      return null; // no error
    } catch (err) {
      const msg = err.response?.data?.detail || 'Login failed. Check your credentials.';
      return msg; // return error string to Login.jsx
    }
  };

  // Axios helper that automatically adds the JWT token to every request
  const authAxios = {
    get: (url) => axios.get(url, {
      headers: { Authorization: `Bearer ${accessTokenRef.current}` }
    }),
    post: (url, data) => axios.post(url, data, {
      headers: { Authorization: `Bearer ${accessTokenRef.current}` }
    }),
  };

  const handleDisconnect = () => {
    // Tell backend to invalidate the token
    if (accessTokenRef.current) {
      axios.post(`${BACKEND_URL}/api/v1/auth/logout`, {}, {
        headers: { Authorization: `Bearer ${accessTokenRef.current}` }
      }).catch(() => {}); // silent fail — still clear local state
    }
    accessTokenRef.current = null;
    setCurrentUser(null);
    sessionStorage.removeItem('nexus_user');
    sessionStorage.removeItem('nexus_access_token');
    sessionStorage.removeItem('nexus_tab');
    sessionStorage.removeItem('nexus_selected_team');
    stopSpeaking();
  };

  // Expose everything to the rest of the app
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
    BACKEND_URL
  };

  return (
    <NexusContext.Provider value={value}>
      {children}
    </NexusContext.Provider>
  );
}