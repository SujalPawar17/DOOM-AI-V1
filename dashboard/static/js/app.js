/**
 * DOOM V2 — HOLOGRAPHIC CONTROL HUD CONTROLLER (VANILLA JS)
 * High performance, 60fps canvas visualizers, WebSockets, Neural Voice, Ambient Music DJ & Gesture Engine
 */

document.addEventListener("DOMContentLoaded", () => {
    // ─────────────────────────────────────────────────────────────────────────
    // State Store
    // ─────────────────────────────────────────────────────────────────────────
    const state = {
        telemetryHistory: {
            cpu: Array(30).fill(0),
            ram: Array(30).fill(0)
        },
        coreState: "IDLE", // IDLE, EXECUTING, SPEAKING
        voiceEnabled: true,
        handsFree: true,
        isRecording: false,
        activeTab: "tab-audit",
        wsConnected: false,
        // Music state
        musicPlaying: false,
        currentStationIdx: 0,
        musicVolume: 0.7,
        musicStations: [
            { title: "NEON OVERDRIVE // CYBERPUNK SYNTHWAVE", tag: "FOCUS HIGH", url: "https://ice1.somafm.com/vaporwaves-128-mp3" },
            { title: "CYBER CHILL // LO-FI CODING BEATS", tag: "FLOW STATE", url: "https://ice2.somafm.com/groovesalad-128-mp3" },
            { title: "NIGHT CITY PROTOCOL // INDUSTRIAL DARKWAVE", tag: "INTENSE CODE", url: "https://ice4.somafm.com/defcon-128-mp3" },
            { title: "DEEP SPACE ORBIT // AMBIENT SOUNDSCAPES", tag: "CALM", url: "https://ice2.somafm.com/dronezone-128-mp3" }
        ],
        // Gesture sensor state
        gestureActive: false
    };

    // ─────────────────────────────────────────────────────────────────────────
    // DOM Elements
    // ─────────────────────────────────────────────────────────────────────────
    const digitalClock = document.getElementById("digital-clock");
    const uptimeDisplay = document.getElementById("uptime-display");
    const cpuVal = document.getElementById("cpu-val");
    const ramVal = document.getElementById("ram-val");
    const diskVal = document.getElementById("disk-val");
    const cpuGaugeBar = document.getElementById("cpu-gauge-bar");
    const ramGaugeBar = document.getElementById("ram-gauge-bar");
    const diskGaugeBar = document.getElementById("disk-gauge-bar");
    
    const metricRamUsed = document.getElementById("metric-ram-used");
    const metricDiskFree = document.getElementById("metric-disk-free");
    const metricProcesses = document.getElementById("metric-processes");
    const metricNetwork = document.getElementById("metric-network");

    const commandForm = document.getElementById("command-form");
    const commandInput = document.getElementById("command-input");
    const btnExecute = document.getElementById("btn-execute");
    const btnMic = document.getElementById("btn-mic");
    const btnVoiceToggle = document.getElementById("btn-voice-toggle");
    const voiceIcon = document.getElementById("voice-icon");
    const voiceLabel = document.getElementById("voice-label");
    const btnHandsfreeToggle = document.getElementById("btn-handsfree-toggle");
    const handsfreeIcon = document.getElementById("handsfree-icon") || { textContent: '' };
    const handsfreeLabel = document.getElementById("handsfree-label") || { textContent: '' };
    const btnScheduleBriefing = document.getElementById("btn-schedule-briefing");
    const scheduleTimeLabel = document.getElementById("schedule-time-label") || { textContent: '' };
    const btnGestureToggle = document.getElementById("btn-gesture-toggle");
    const btnClapToggle = document.getElementById("btn-clap-toggle");
    const clapIcon = document.getElementById("clap-icon") || { textContent: '' };
    const clapLabel = document.getElementById("clap-label") || { textContent: '' };


    // Music Elements
    const btnMusicPlay = document.getElementById("btn-music-play");
    const musicPlayIcon = document.getElementById("music-play-icon");
    const musicEqualizer = document.getElementById("music-equalizer");
    const musicTrackTitle = document.getElementById("music-track-title");
    const musicStationTag = document.getElementById("music-station-tag");
    const btnMusicPrev = document.getElementById("btn-music-prev");
    const btnMusicNext = document.getElementById("btn-music-next");
    const musicVolumeSlider = document.getElementById("music-volume");

    // Gesture Elements
    const gestureHudOverlay = document.getElementById("gesture-hud-overlay");
    const btnCloseGesture = document.getElementById("btn-close-gesture");
    const gestureVideo = document.getElementById("gestureVideo");
    const gestureCanvas = document.getElementById("gestureCanvas");
    const gestureStatusPill = document.getElementById("gesture-status-pill");

    const coreStateText = document.getElementById("core-state-text") || { textContent: '' };
    const termStatusIndicator = document.getElementById("term-status-indicator");
    const responseContent = document.getElementById("response-content");
    const responseMeta = document.getElementById("response-meta");

    const sentinelBanner = document.getElementById("sentinel-banner");
    const sentinelMsg = document.getElementById("sentinel-msg");
    const btnDismissAlert = document.getElementById("btn-dismiss-alert");

    const auditTableBody = document.getElementById("audit-table-body");
    const episodesFeedList = document.getElementById("episodes-feed-list");
    const factsGridList = document.getElementById("facts-grid-list");
    const toolsPillsList = document.getElementById("tools-pills-list");

    const countLogs = document.getElementById("count-logs");
    const countEpisodes = document.getElementById("count-episodes");
    const countFacts = document.getElementById("count-facts");

    // Null-safe helper to update text content
    function safeText(el, text) { if (el) el.textContent = text; }
    // ─────────────────────────────────────────────────────────────────────────
    // 1. Digital Master Clock & Uptime
    // ─────────────────────────────────────────────────────────────────────────
    function updateClock() {
        const now = new Date();
        if (digitalClock) digitalClock.textContent = now.toTimeString().split(" ")[0];
    }
    setInterval(updateClock, 1000);
    updateClock();

    // ─────────────────────────────────────────────────────────────────────────
    // 2. Circular Gauge Meter Animator
    // ─────────────────────────────────────────────────────────────────────────
    const GAUGE_CIRCUMFERENCE = 251.2;
    function updateGauge(circleElement, valueElement, percent) {
        if (!circleElement || !valueElement) return;
        const safePercent = Math.min(Math.max(percent || 0, 0), 100);
        valueElement.textContent = Math.round(safePercent);
        const offset = GAUGE_CIRCUMFERENCE - (GAUGE_CIRCUMFERENCE * safePercent) / 100;
        circleElement.style.strokeDashoffset = offset;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 3. Real-Time Rolling Telemetry Chart (60fps Canvas)
    // ─────────────────────────────────────────────────────────────────────────
    const chartCanvas = document.getElementById("telemetryCanvas");
    const chartCtx = chartCanvas ? chartCanvas.getContext("2d") : null;

    function renderTelemetryChart() {
        if (!chartCtx || !chartCanvas) return;
        const w = chartCanvas.width;
        const h = chartCanvas.height;

        chartCtx.clearRect(0, 0, w, h);

        chartCtx.strokeStyle = "rgba(255, 255, 255, 0.05)";
        chartCtx.lineWidth = 1;
        for (let y = 0; y < h; y += 30) {
            chartCtx.beginPath();
            chartCtx.moveTo(0, y);
            chartCtx.lineTo(w, y);
            chartCtx.stroke();
        }

        const step = w / (state.telemetryHistory.cpu.length - 1);

        drawLine(state.telemetryHistory.cpu, "#00f0ff", "rgba(0, 240, 255, 0.15)");
        drawLine(state.telemetryHistory.ram, "#9d4edd", "rgba(157, 78, 221, 0.15)");

        function drawLine(data, strokeColor, fillColor) {
            chartCtx.beginPath();
            chartCtx.strokeStyle = strokeColor;
            chartCtx.lineWidth = 2;

            data.forEach((val, i) => {
                const x = i * step;
                const y = h - (val / 100) * (h - 10) - 5;
                if (i === 0) chartCtx.moveTo(x, y);
                else chartCtx.lineTo(x, y);
            });
            chartCtx.stroke();

            chartCtx.lineTo((data.length - 1) * step, h);
            chartCtx.lineTo(0, h);
            chartCtx.closePath();
            chartCtx.fillStyle = fillColor;
            chartCtx.fill();
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 4. Holographic Arc Reactor Visualizer (60fps Canvas with Voice Reactivity)
    // ─────────────────────────────────────────────────────────────────────────
    const coreCanvas = document.getElementById("coreCanvas");
    const coreCtx = coreCanvas ? coreCanvas.getContext("2d") : null;
    let coreAngle = 0;

    const particles = Array.from({ length: 42 }, () => ({
        dist: 45 + Math.random() * 55,
        speed: (Math.random() * 0.02 + 0.005) * (Math.random() > 0.5 ? 1 : -1),
        size: Math.random() * 2.5 + 1,
        angle: Math.random() * Math.PI * 2,
        color: Math.random() > 0.4 ? "#00f0ff" : "#9d4edd"
    }));

    function animateCoreReactor() {
        if (!coreCtx || !coreCanvas) return;
        const w = coreCanvas.width;
        const h = coreCanvas.height;
        const cx = w / 2;
        const cy = h / 2;

        coreCtx.clearRect(0, 0, w, h);
        coreAngle += 0.015;

        let pulseSpeed = 3;
        let pulseAmp = 6;
        if (state.coreState === "SPEAKING") {
            pulseSpeed = 8;
            pulseAmp = 18;
        } else if (state.coreState === "EXECUTING") {
            pulseSpeed = 6;
            pulseAmp = 12;
        }

        const pulse = Math.sin(coreAngle * pulseSpeed) * pulseAmp;
        const radGrad = coreCtx.createRadialGradient(cx, cy, 5, cx, cy, 45 + pulse);
        
        if (state.coreState === "SPEAKING") {
            radGrad.addColorStop(0, "rgba(0, 240, 255, 0.95)");
            radGrad.addColorStop(0.5, "rgba(0, 255, 157, 0.5)");
            radGrad.addColorStop(1, "rgba(0, 240, 255, 0)");
        } else if (state.coreState === "EXECUTING") {
            radGrad.addColorStop(0, "rgba(0, 255, 157, 0.9)");
            radGrad.addColorStop(0.5, "rgba(0, 255, 157, 0.4)");
            radGrad.addColorStop(1, "rgba(0, 255, 157, 0)");
        } else {
            radGrad.addColorStop(0, "rgba(0, 240, 255, 0.85)");
            radGrad.addColorStop(0.5, "rgba(157, 78, 221, 0.35)");
            radGrad.addColorStop(1, "rgba(0, 240, 255, 0)");
        }
        
        coreCtx.fillStyle = radGrad;
        coreCtx.beginPath();
        coreCtx.arc(cx, cy, 50 + pulse, 0, Math.PI * 2);
        coreCtx.fill();

        particles.forEach(p => {
            p.angle += p.speed * (state.coreState === "SPEAKING" ? 2.5 : 1);
            const px = cx + Math.cos(p.angle) * p.dist;
            const py = cy + Math.sin(p.angle) * p.dist;

            coreCtx.fillStyle = p.color;
            coreCtx.shadowColor = p.color;
            coreCtx.shadowBlur = 6;
            coreCtx.beginPath();
            coreCtx.arc(px, py, p.size, 0, Math.PI * 2);
            coreCtx.fill();
        });
        coreCtx.shadowBlur = 0;

        requestAnimationFrame(animateCoreReactor);
    }
    animateCoreReactor();

    // ─────────────────────────────────────────────────────────────────────────
    // 5. Ambient Cyberpunk Music DJ (Dual Engine: Streaming + Web Audio Synth)
    // ─────────────────────────────────────────────────────────────────────────
    let musicAudio = new Audio();
    let synthContext = null;
    let synthInterval = null;
    let synthGain = null;
    let isSynthRunning = false;

    function startProceduralSynthwave() {
        if (isSynthRunning) return;
        try {
            const AudioContext = window.AudioContext || window.webkitAudioContext;
            if (!synthContext) {
                synthContext = new AudioContext();
            }
            if (synthContext.state === "suspended") {
                synthContext.resume();
            }

            synthGain = synthContext.createGain();
            synthGain.gain.setValueAtTime(state.musicVolume * 0.25, synthContext.currentTime);
            synthGain.connect(synthContext.destination);

            const chordNotes = [
                [130.81, 164.81, 196.00], // C3 minor
                [116.54, 146.83, 174.61], // Bb2
                [103.83, 130.81, 155.56], // Ab2
                [116.54, 146.83, 174.61]  // Bb2
            ];
            let chordIdx = 0;
            let step = 0;

            function playNote(freq, type = "sawtooth", dur = 0.22, vol = 0.18) {
                if (!synthContext || !isSynthRunning) return;
                const osc = synthContext.createOscillator();
                const noteGain = synthContext.createGain();
                const filter = synthContext.createBiquadFilter();

                filter.type = "lowpass";
                filter.frequency.setValueAtTime(800 + Math.sin(step * 0.2) * 400, synthContext.currentTime);

                osc.type = type;
                osc.frequency.setValueAtTime(freq, synthContext.currentTime);

                noteGain.gain.setValueAtTime(vol, synthContext.currentTime);
                noteGain.gain.exponentialRampToValueAtTime(0.001, synthContext.currentTime + dur);

                osc.connect(filter);
                filter.connect(noteGain);
                noteGain.connect(synthGain);

                osc.start();
                osc.stop(synthContext.currentTime + dur);
            }

            isSynthRunning = true;
            synthInterval = setInterval(() => {
                const currentChord = chordNotes[chordIdx];
                const note = currentChord[step % currentChord.length];
                
                // Arpeggiated bass / lead
                playNote(note * (step % 2 === 0 ? 1 : 2), "sawtooth", 0.24, 0.16);
                
                // Sub Bass
                if (step % 4 === 0) {
                    playNote(currentChord[0] * 0.5, "sine", 0.5, 0.25);
                }

                step++;
                if (step >= 16) {
                    step = 0;
                    chordIdx = (chordIdx + 1) % chordNotes.length;
                }
            }, 180);

            state.musicPlaying = true;
            updateMusicUI();
        } catch (e) {
            console.warn("Synthwave engine error:", e);
        }
    }

    function stopProceduralSynthwave() {
        isSynthRunning = false;
        if (synthInterval) {
            clearInterval(synthInterval);
            synthInterval = null;
        }
    }

    function updateMusicUI() {
        const station = state.musicStations[state.currentStationIdx];
        if (musicTrackTitle) musicTrackTitle.textContent = station.title;
        if (musicStationTag) musicStationTag.textContent = `${station.tag} (AUTO-DUCKING ENABLED)`;

        if (state.musicPlaying) {
            if (btnMusicPlay) btnMusicPlay.classList.add("playing");
            if (musicPlayIcon) musicPlayIcon.textContent = "⏸";
            if (musicEqualizer) musicEqualizer.classList.add("active");
            // New HTML music player
            const eqEl = document.getElementById("music-eq");
            if (eqEl) eqEl.classList.add("active");
            const playBtn = document.getElementById("btn-music-play");
            if (playBtn) { const ico = playBtn.querySelector("svg"); if (ico) ico.style.opacity = "0.7"; }
        } else {
            if (btnMusicPlay) btnMusicPlay.classList.remove("playing");
            if (musicPlayIcon) musicPlayIcon.textContent = "▶";
            if (musicEqualizer) musicEqualizer.classList.remove("active");
            const eqEl = document.getElementById("music-eq");
            if (eqEl) eqEl.classList.remove("active");
        }
    }

    function playMusicStation(index) {
        state.currentStationIdx = (index + state.musicStations.length) % state.musicStations.length;
        const station = state.musicStations[state.currentStationIdx];
        
        stopProceduralSynthwave();
        musicAudio.src = station.url;
        musicAudio.volume = state.musicVolume;
        
        musicAudio.play().then(() => {
            state.musicPlaying = true;
            updateMusicUI();
        }).catch(() => {
            console.log("[DOOM MUSIC] Streaming buffer delayed. Engaging Procedural Cyber Synthwave...");
            startProceduralSynthwave();
        });
    }

    function toggleMusic() {
        if (state.musicPlaying) {
            musicAudio.pause();
            stopProceduralSynthwave();
            state.musicPlaying = false;
            updateMusicUI();
        } else {
            playMusicStation(state.currentStationIdx);
        }
    }

    if (btnMusicPlay) btnMusicPlay.addEventListener("click", toggleMusic);
    if (btnMusicNext) btnMusicNext.addEventListener("click", () => playMusicStation(state.currentStationIdx + 1));
    if (btnMusicPrev) btnMusicPrev.addEventListener("click", () => playMusicStation(state.currentStationIdx - 1));
    if (musicVolumeSlider) {
        musicVolumeSlider.addEventListener("input", (e) => {
            state.musicVolume = parseFloat(e.target.value);
            musicAudio.volume = state.musicVolume;
            if (synthGain && synthContext) {
                synthGain.gain.setValueAtTime(state.musicVolume * 0.25, synthContext.currentTime);
            }
        });
    }

    // YouTube Stream Form Handler
    const ytSearchForm = document.getElementById("yt-search-form");
    const ytSearchInput = document.getElementById("yt-search-input");
    if (ytSearchForm && ytSearchInput) {
        ytSearchForm.addEventListener("submit", (e) => {
            e.preventDefault();
            const query = ytSearchInput.value.trim();
            if (query) {
                executeGoal(`play ${query} on youtube`);
                ytSearchInput.value = "";
            }
        });
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 6. In-Browser Neural Voice: Single-Invocation Lock & Deduplication
    // ─────────────────────────────────────────────────────────────────────────
    let currentSpeechAudio = null;
    let lastSpokenText = "";
    let lastSpokenTime = 0;

    function speakText(text) {
        if (!state.voiceEnabled || !text) return;

        const clean = text.replace(/[*#_`~]/g, "").slice(0, 300).trim();
        const now = Date.now();

        // Deduplication: prevent speaking the exact same message within 6 seconds
        if (lastSpokenText === clean && now - lastSpokenTime < 6000) {
            console.log("[DOOM VOICE] Discarding duplicate speech request within 6s.");
            return;
        }

        lastSpokenText = clean;
        lastSpokenTime = now;

        // Cancel any browser synthesis if somehow triggered
        if ("speechSynthesis" in window) {
            window.speechSynthesis.cancel();
        }

        // Stop any currently playing speech immediately
        if (currentSpeechAudio) {
            currentSpeechAudio.pause();
            currentSpeechAudio = null;
        }

        // Auto-Duck Music Volume while DOOM speaks
        if (state.musicPlaying) {
            musicAudio.volume = Math.min(state.musicVolume * 0.15, 0.15);
            if (synthGain && synthContext) {
                synthGain.gain.setValueAtTime(state.musicVolume * 0.05, synthContext.currentTime);
            }
        }

        state.coreState = "SPEAKING";
        coreStateText.textContent = "DOOM SPEAKING";

        const ttsUrl = `/api/tts?text=${encodeURIComponent(clean)}`;
        const audio = new Audio(ttsUrl);
        currentSpeechAudio = audio;

        const onSpeechFinish = () => {
            state.coreState = "IDLE";
            coreStateText.textContent = "DOOM ONLINE";
            currentSpeechAudio = null;
            // Restore Music Volume
            if (state.musicPlaying) {
                musicAudio.volume = state.musicVolume;
                if (synthGain && synthContext) {
                    synthGain.gain.setValueAtTime(state.musicVolume * 0.25, synthContext.currentTime);
                }
            }
        };

        audio.onended = onSpeechFinish;
        audio.onerror = onSpeechFinish;

        audio.play().catch((e) => {
            console.warn("Audio play blocked by browser policy until interaction:", e);
            onSpeechFinish();
        });
    }

    if (btnVoiceToggle) {
        btnVoiceToggle.addEventListener("click", () => {
            state.voiceEnabled = !state.voiceEnabled;
            if (state.voiceEnabled) {
                btnVoiceToggle.classList.remove("muted");
                btnVoiceToggle.title = "Voice output ON";
            } else {
                btnVoiceToggle.classList.add("muted");
                btnVoiceToggle.title = "Voice output OFF";
                if (currentSpeechAudio) { currentSpeechAudio.pause(); currentSpeechAudio = null; }
                if ("speechSynthesis" in window) window.speechSynthesis.cancel();
            }
        });
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 7. Speech-to-Text: Continuous Hands-Free Listening & Music Voice Commands
    // ─────────────────────────────────────────────────────────────────────────
    let recognition = null;
    if ("webkitSpeechRecognition" in window || "SpeechRecognition" in window) {
        const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
        recognition = new SpeechRec();
        recognition.continuous = true;
        recognition.interimResults = false;
        recognition.lang = "en-US";

        recognition.onstart = () => {
            state.isRecording = true;
            btnMic.classList.add("recording");
            if (termStatusIndicator) {
                termStatusIndicator.textContent = "";
                termStatusIndicator.classList.add("active");
            }
            if (responseMeta) responseMeta.textContent = "Listening (hands-free)...";
        };

        recognition.onresult = (event) => {
            const lastResultIndex = event.results.length - 1;
            const transcript = event.results[lastResultIndex][0].transcript.trim();
            console.log("[DOOM VOICE] Heard:", transcript);

            if (!transcript) return;

            const lower = transcript.toLowerCase();

            // Music Voice Controls
            if (lower.includes("play music") || lower.includes("start music") || lower.includes("play synthwave")) {
                playMusicStation(0);
                speakText("Engaging Cyberpunk Synthwave focus stream, Boss Sujal.");
                return;
            } else if (lower.includes("play lofi") || lower.includes("play lo-fi") || lower.includes("chill music")) {
                playMusicStation(1);
                speakText("Switching to Lo-Fi coding stream.");
                return;
            } else if (lower.includes("pause music") || lower.includes("stop music") || lower.includes("mute music")) {
                if (state.musicPlaying) toggleMusic();
                speakText("Music paused, standing by.");
                return;
            } else if (lower.includes("next music") || lower.includes("next song") || lower.includes("next track") || lower.includes("next station")) {
                playMusicStation(state.currentStationIdx + 1);
                return;
            }

            // Workstation Macros
            if (lower.includes("code mode") || lower === "code") {
                triggerWorkstationMode("code");
            } else if (lower.includes("daily briefing") || lower.includes("morning briefing") || lower.includes("briefing")) {
                triggerWorkstationMode("briefing");
            } else if (lower.includes("standup") || lower.includes("stand up") || lower.includes("status report")) {
                triggerWorkstationMode("standup");
            } else if (lower.includes("lockdown") || lower.includes("lock workstation") || lower.includes("lock screen")) {
                triggerWorkstationMode("lockdown");
            } else if (lower.includes("screen eye") || lower.includes("look at screen") || lower.includes("analyze screen")) {
                triggerWorkstationMode("vision");
            } else {
                let cleanPrompt = transcript;
                for (const w of ["hey doom", "hello doom", "ok doom", "doom"]) {
                    if (lower.startsWith(w)) {
                        cleanPrompt = transcript.slice(w.length).trim().replace(/^[,:]\s*/, "");
                        break;
                    }
                }
                if (cleanPrompt) {
                    commandInput.value = cleanPrompt;
                    executeGoal(cleanPrompt);
                }
            }
        };

        recognition.onerror = (e) => {
            console.warn("Speech recognition error:", e.error);
        };

        recognition.onend = () => {
            if (state.handsFree && state.coreState !== "SPEAKING") {
                try { recognition.start(); } catch (err) {}
            } else {
                state.isRecording = false;
                btnMic.classList.remove("recording");
                if (termStatusIndicator) {
                    termStatusIndicator.textContent = "";
                    termStatusIndicator.classList.remove("active");
                }
                if (responseMeta) responseMeta.textContent = "Ready";
            }
        };

        const startHandsFree = () => {
            if (state.handsFree && !state.isRecording) {
                try { recognition.start(); } catch (e) {}
            }
            window.removeEventListener("click", startHandsFree);
            window.removeEventListener("keydown", startHandsFree);
        };
        window.addEventListener("click", startHandsFree);
        window.addEventListener("keydown", startHandsFree);
    }

    if (btnHandsfreeToggle) {
        btnHandsfreeToggle.addEventListener("click", () => {
            state.handsFree = !state.handsFree;
            if (state.handsFree) {
                btnHandsfreeToggle.classList.add("active");
                handsfreeIcon.textContent = "🎙️";
                handsfreeLabel.textContent = "HANDS-FREE ON";
                try { recognition.start(); } catch (e) {}
            } else {
                btnHandsfreeToggle.classList.remove("active");
                handsfreeIcon.textContent = "🔇";
                handsfreeLabel.textContent = "HANDS-FREE OFF";
                try { recognition.stop(); } catch (e) {}
            }
        });
    }

    if (btnMic) {
        btnMic.addEventListener("click", () => {
            if (!recognition) {
                alert("Speech recognition is not supported in this browser. Please use Chrome or Edge.");
                return;
            }
            if (state.isRecording) {
                recognition.stop();
            } else {
                recognition.start();
            }
        });
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 8. Workstation Modes (Debounced Click & Voice Handler)
    // ─────────────────────────────────────────────────────────────────────────
    let isModeExecuting = false;

    async function triggerWorkstationMode(mode) {
        if (!mode || isModeExecuting || state.coreState === "SPEAKING") return;

        isModeExecuting = true;
        state.coreState = "EXECUTING";
        coreStateText.textContent = `MODE: ${mode.toUpperCase()}`;
        responseMeta.textContent = `Executing ${mode.toUpperCase()} mode...`;
        responseContent.textContent = "⚙️ Executing workstation commands and logging to PostgreSQL...";

        try {
            const res = await fetch(`/api/modes/${mode}`, { method: "POST" });
            const data = await res.json();

            responseContent.textContent = data.response;
            responseMeta.textContent = `Mode ${mode.toUpperCase()} active`;

            speakText(data.response);
            refreshAuditLogs();
            refreshEpisodes();
        } catch (err) {
            responseContent.textContent = `Mode error: ${err.message}`;
        } finally {
            setTimeout(() => {
                isModeExecuting = false;
            }, 2500);
            state.coreState = "IDLE";
            coreStateText.textContent = "DOOM ONLINE";
        }
    }

    document.querySelectorAll(".mode-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            const mode = btn.getAttribute("data-mode");
            triggerWorkstationMode(mode);
        });
    });

    // ─────────────────────────────────────────────────────────────────────────
    // 9. Morning Routine Scheduler
    // ─────────────────────────────────────────────────────────────────────────
    if (btnScheduleBriefing) {
        btnScheduleBriefing.addEventListener("click", async () => {
            const current = scheduleTimeLabel.textContent.replace(" AM", "").replace(" PM", "");
            const newTime = prompt("Set Daily Morning Briefing Alarm (24h format HH:MM, e.g. 09:00):", current || "09:00");
            if (newTime && /^\d{1,2}:\d{2}$/.test(newTime.trim())) {
                try {
                    await fetch("/api/settings/briefing_time", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ time_str: newTime.trim() })
                    });
                    scheduleTimeLabel.textContent = `${newTime.trim()} ALARM`;
                    alert(`Daily Briefing Alarm configured for ${newTime.trim()}! DOOM will awaken and brief you automatically.`);
                } catch (e) {
                    console.error("Scheduler update error:", e);
                }
            }
        });
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 10. Iron Man Webcam Hand Gesture Vision Sensor (Speech-Aware)
    // ─────────────────────────────────────────────────────────────────────────
    let videoStream = null;
    let gestureInterval = null;
    let lastFrameData = null;
    let lastGestureTime = 0;

    const tabOrder = ["tab-audit", "tab-episodes", "tab-facts", "tab-agent-studio", "tab-profile"];

    function cycleTab(direction = 1) {
        const activeIdx = tabOrder.indexOf(state.activeTab);
        const nextIdx = (activeIdx + direction + tabOrder.length) % tabOrder.length;
        const nextTabId = tabOrder[nextIdx];
        const tabBtn = document.querySelector(`.tab-btn[data-tab="${nextTabId}"]`);
        if (tabBtn) tabBtn.click();
    }

    async function toggleGestureSensor() {
        state.gestureActive = !state.gestureActive;

        if (state.gestureActive) {
            gestureHudOverlay.style.display = "flex";
            btnGestureToggle.classList.add("active");

            try {
                videoStream = await navigator.mediaDevices.getUserMedia({ video: { width: 320, height: 240 } });
                gestureVideo.srcObject = videoStream;
                gestureStatusPill.textContent = "VISION SENSOR ACTIVE";
                gestureStatusPill.style.color = "var(--neon-green)";

                const ctx = gestureCanvas.getContext("2d");

                gestureInterval = setInterval(() => {
                    // Ignore gestures completely while DOOM is speaking or executing
                    if (!gestureVideo.videoWidth || state.coreState === "SPEAKING" || state.coreState === "EXECUTING" || isModeExecuting) {
                        return;
                    }

                    ctx.drawImage(gestureVideo, 0, 0, 320, 240);
                    const frame = ctx.getImageData(0, 0, 320, 240);

                    if (lastFrameData) {
                        let leftDiff = 0;
                        let rightDiff = 0;
                        let centerDiff = 0;

                        for (let i = 0; i < frame.data.length; i += 16) {
                            const diff = Math.abs(frame.data[i] - lastFrameData.data[i]);
                            if (diff > 45) {
                                const pixelIdx = i / 4;
                                const x = pixelIdx % 320;
                                if (x < 100) leftDiff++;
                                else if (x > 220) rightDiff++;
                                else centerDiff++;
                            }
                        }

                        const now = Date.now();
                        // 3.5 second cooldown between gesture triggers
                        if (now - lastGestureTime > 3500) {
                            if (leftDiff > 65 && rightDiff < 25) {
                                gestureStatusPill.textContent = "🖐️ GESTURE: SWIPE LEFT";
                                cycleTab(-1);
                                lastGestureTime = now;
                            } else if (rightDiff > 65 && leftDiff < 25) {
                                gestureStatusPill.textContent = "🖐️ GESTURE: SWIPE RIGHT";
                                cycleTab(1);
                                lastGestureTime = now;
                            } else if (centerDiff > 180) {
                                gestureStatusPill.textContent = "🖐️ GESTURE: PALM DETECTED";
                                triggerWorkstationMode("briefing");
                                lastGestureTime = now;
                            }
                        }
                    }
                    lastFrameData = frame;
                }, 120);

            } catch (err) {
                alert(`Camera access denied or unavailable: ${err.message}`);
                state.gestureActive = false;
                gestureHudOverlay.style.display = "none";
                btnGestureToggle.classList.remove("active");
            }
        } else {
            gestureHudOverlay.style.display = "none";
            btnGestureToggle.classList.remove("active");
            if (gestureInterval) {
                clearInterval(gestureInterval);
                gestureInterval = null;
            }
            if (videoStream) {
                videoStream.getTracks().forEach(t => t.stop());
                videoStream = null;
            }
        }
    }

    if (btnGestureToggle) btnGestureToggle.addEventListener("click", toggleGestureSensor);
    if (btnCloseGesture) btnCloseGesture.addEventListener("click", toggleGestureSensor);

    // ─────────────────────────────────────────────────────────────────────────
    // 11. WebSocket Client: Real-Time Telemetry, Sentinel & Morning Routine
    // ─────────────────────────────────────────────────────────────────────────
    function initWebSocket() {
        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const wsUrl = `${protocol}//${window.location.host}/ws/telemetry`;
        const ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            state.wsConnected = true;
            console.log("[DOOM HUD] WebSocket Connected to Telemetry Stream");
        };

        ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                if (msg.type === "telemetry_update" && msg.data) {
                    handleTelemetryUpdate(msg.data);

                    // Update Home minimal health indicator
                    const healthText = document.getElementById("home-health-text");
                    if (healthText) {
                        healthText.textContent = `SYSTEM HEALTHY // CPU: ${Math.round(msg.data.cpu_percent || 0)}% • RAM: ${Math.round(msg.data.memory_percent || 0)}%`;
                    }

                    // Handle unified DOOM State Machine
                    if (msg.doom_state) {
                        state.coreState = msg.doom_state.state;
                        const stateText = document.getElementById("core-state-text");
                        if (stateText) stateText.textContent = `DOOM ${msg.doom_state.state}`;
                        const subText = document.getElementById("core-sub-text");
                        if (subText) subText.textContent = msg.doom_state.message || "AWAITING COMMAND";
                    }

                    // Handle Active Autonomous Task updates
                    const taskCard = document.getElementById("home-active-task-card");
                    if (msg.active_task) {
                        if (taskCard) {
                            taskCard.style.display = "block";
                            const pctEl = document.getElementById("home-task-pct");
                            const fillEl = document.getElementById("home-task-progress-fill");
                            const goalEl = document.getElementById("home-task-goal");
                            const listEl = document.getElementById("home-task-steps-checklist");

                            if (pctEl) pctEl.textContent = `${msg.active_task.progress}%`;
                            if (fillEl) fillEl.style.width = `${msg.active_task.progress}%`;
                            if (goalEl) goalEl.textContent = msg.active_task.goal;
                            if (listEl && msg.active_task.steps) {
                                listEl.innerHTML = msg.active_task.steps.map(s => {
                                    const icon = s.status === "completed" ? "✓" : (s.status === "active" ? "◉" : "○");
                                    return `<li class="task-step-item ${s.status}"><span>${icon}</span> <span>${s.description}</span></li>`;
                                }).join("");
                            }
                        }
                    } else if (taskCard) {
                        taskCard.style.display = "none";
                    }

                    if (msg.sentinel_alert && sentinelBanner) {
                        sentinelMsg.textContent = `SENTINEL ALERT [${msg.sentinel_alert.level}]: ${msg.sentinel_alert.msg}`;
                        sentinelBanner.style.display = "flex";
                    }
                } else if (msg.type === "scheduled_briefing") {
                    speakText(msg.response || "Morning protocol initiated, Boss Sujal.");
                } else if (msg.type === "clap_detected") {
                    console.log("[DOOM HUD] 👏 Acoustic clap detected!");
                    state.coreState = "EXECUTING";
                    coreStateText.textContent = "DOOM ONLINE";
                    if (termStatusIndicator) {
                        termStatusIndicator.textContent = "";
                        termStatusIndicator.classList.add("active");
                    }
                    responseMeta.textContent = `Online // ${msg.timestamp}`;
                    responseContent.textContent = "DOOM is online. At your service, Boss. Listening for command...";
                    if (btnClapToggle) btnClapToggle.classList.add("recording");
                } else if (msg.type === "clap_command") {
                    console.log("[DOOM HUD] Command received:", msg.command);
                    commandInput.value = msg.command;
                    responseMeta.textContent = `Command Recognized: "${msg.command}"`;
                    responseContent.textContent = `Processing: "${msg.command}"...`;
                } else if (msg.type === "clap_standby") {
                    if (btnClapToggle) btnClapToggle.classList.remove("recording");
                    state.coreState = "IDLE";
                    coreStateText.textContent = "DOOM ONLINE";
                    if (termStatusIndicator) {
                        termStatusIndicator.textContent = "";
                        termStatusIndicator.classList.remove("active");
                    }
                    responseMeta.textContent = `Standby // ${msg.timestamp}`;
                    responseContent.textContent = "Standing by, Boss.";
                } else if (msg.type === "command_executed" || msg.type === "mode_triggered") {

                    if (btnClapToggle) btnClapToggle.classList.remove("recording");
                    if (msg.response) {
                        responseContent.textContent = msg.response;
                        responseMeta.textContent = `Completed // ${msg.timestamp}`;
                    }
                    refreshAuditLogs();
                    refreshEpisodes();
                }

            } catch (e) {
                console.error("[DOOM HUD] WS parse error:", e);
            }
        };

        ws.onclose = () => {
            state.wsConnected = false;
            setTimeout(initWebSocket, 3000);
        };
    }
    initWebSocket();

    // Clap Sensor Calibration button handler (Optional manual tune)
    if (btnClapToggle) {
        btnClapToggle.addEventListener("click", async () => {
            clapLabel.textContent = "CALIBRATING...";
            try {
                const res = await fetch("/api/clap/calibrate", { method: "POST" });
                const data = await res.json();
                clapLabel.textContent = `CLAP ON (${data.threshold})`;
                responseMeta.textContent = `Calibrated // Threshold: ${data.threshold}`;
                responseContent.textContent = `Acoustic sensor calibrated with current room noise (Threshold = ${data.threshold}). Double-clap anytime to awaken DOOM.`;
            } catch (err) {
                clapLabel.textContent = "CLAP SENSOR ON";
                console.error("Clap calibration error:", err);
            }
        });
    }


    if (btnDismissAlert) {
        btnDismissAlert.addEventListener("click", () => {
            sentinelBanner.style.display = "none";
        });
    }

    function handleTelemetryUpdate(data) {
        updateGauge(cpuGaugeBar, cpuVal, data.cpu_percent);
        updateGauge(ramGaugeBar, ramVal, data.memory_percent);
        updateGauge(diskGaugeBar, diskVal, data.disk_percent);

        metricRamUsed.textContent = `${data.memory_used_gb} GB / ${data.memory_total_gb} GB`;
        metricDiskFree.textContent = `${data.disk_free_gb} GB Free`;
        metricProcesses.textContent = `${data.processes} ACTIVE`;
        metricNetwork.textContent = `${data.bytes_recv_mb} / ${data.bytes_sent_mb} MB`;

        if (data.uptime_formatted) {
            if (uptimeDisplay) uptimeDisplay.textContent = `Up ${data.uptime_formatted}`;
        }

        state.telemetryHistory.cpu.push(data.cpu_percent || 0);
        state.telemetryHistory.cpu.shift();
        state.telemetryHistory.ram.push(data.memory_percent || 0);
        state.telemetryHistory.ram.shift();

        renderTelemetryChart();
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 12. Interactive Command Execution
    // ─────────────────────────────────────────────────────────────────────────
    async function executeGoal(goalText) {
        if (!goalText || !goalText.trim()) return;

        state.coreState = "EXECUTING";
        coreStateText.textContent = "DOOM EXECUTING";
        if (termStatusIndicator) {
            termStatusIndicator.textContent = "";
            termStatusIndicator.classList.add("active");
        }
        btnExecute.disabled = true;

        responseMeta.textContent = "Orchestrating agent goal...";
        responseContent.textContent = "Routing model, reasoning task intent, executing tools...";

        try {
            const res = await fetch("/api/command", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ goal: goalText })
            });
            const data = await res.json();

            responseContent.textContent = data.response || "No response received.";
            responseMeta.textContent = `Completed in ${data.latency_ms}ms // ${data.timestamp}`;

            speakText(data.response);
            refreshAuditLogs();
            refreshEpisodes();
        } catch (err) {
            responseContent.textContent = `Error executing command: ${err.message}`;
            responseMeta.textContent = "Execution Failed";
        } finally {
            state.coreState = "IDLE";
            coreStateText.textContent = "DOOM ONLINE";
            if (termStatusIndicator) {
                termStatusIndicator.textContent = "";
                termStatusIndicator.classList.remove("active");
            }
            btnExecute.disabled = false;
            commandInput.value = "";
        }
    }

    if (commandForm) {
        commandForm.addEventListener("submit", (e) => {
            e.preventDefault();
            executeGoal(commandInput.value);
        });
    }

    document.querySelectorAll(".quick-chip").forEach(chip => {
        chip.addEventListener("click", () => {
            const cmd = chip.getAttribute("data-cmd");
            commandInput.value = cmd;
            executeGoal(cmd);
        });
    });

    window.addEventListener("keydown", (e) => {
        if ((e.ctrlKey && e.key === "k") || (e.key === "/" && document.activeElement !== commandInput)) {
            e.preventDefault();
            commandInput.focus();
        } else if (e.ctrlKey && e.key === "m") {
            e.preventDefault();
            btnMic.click();
        }
    });

    // ─────────────────────────────────────────────────────────────────────────
    // 13. REST Data Fetchers (PostgreSQL Feeds & Tools)
    // ─────────────────────────────────────────────────────────────────────────
    async function refreshAuditLogs() {
        try {
            const res = await fetch("/api/logs?limit=20");
            const data = await res.json();
            const logs = data.logs || [];

            if (countLogs) countLogs.textContent = logs.length;

            if (!auditTableBody) return;

            if (logs.length === 0) {
                auditTableBody.innerHTML = `<div class="empty-feed">No command logs found. Execute a goal to start logging.</div>`;
                return;
            }

            auditTableBody.innerHTML = logs.map(l => {
                const isSuccess = (l.status || '').toUpperCase() === 'SUCCESS';
                const statusClass = isSuccess ? 'success' : 'error';
                const dotColor = isSuccess ? 'var(--green)' : 'var(--red)';
                return `
                <div class="activity-item">
                    <div class="activity-timeline-dot ${isSuccess ? 'activity-dot-success' : 'activity-dot-error'}">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="${dotColor}" stroke-width="2">
                            ${isSuccess
                                ? '<polyline points="20 6 9 17 4 12"/>'
                                : '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>'
                            }
                        </svg>
                    </div>
                    <div class="activity-content">
                        <div class="activity-content-header">
                            <span class="activity-goal">${escapeHtml(l.user_input || 'Command')}</span>
                            <span class="activity-time">${l.created_at || ''}</span>
                        </div>
                        <div class="activity-meta">
                            <span class="activity-meta-pill">${escapeHtml(l.model_used || 'auto')}</span>
                            <span class="activity-meta-pill">${escapeHtml(l.tool_called || 'direct')}</span>
                            <span class="activity-meta-pill">${l.latency_ms ? l.latency_ms + 'ms' : '-'}</span>
                            <span class="activity-meta-pill ${statusClass}">${l.status || 'UNKNOWN'}</span>
                        </div>
                    </div>
                </div>`;
            }).join("");
        } catch (e) {
            console.error("Logs fetch error:", e);
        }
    }

    async function refreshEpisodes() {
        try {
            const res = await fetch("/api/memory/episodes?limit=15");
            const data = await res.json();
            const episodes = data.episodes || [];

            if (countEpisodes) countEpisodes.textContent = episodes.length;

            if (!episodesFeedList) return;

            if (episodes.length === 0) {
                episodesFeedList.innerHTML = `<div class="empty-feed">No episodic memories recorded yet.</div>`;
                return;
            }

            episodesFeedList.innerHTML = episodes.map(ep => `
                <div class="memory-episode-item">
                    <span class="episode-time">${(ep.timestamp || ep.created_at || '').slice(11, 16) || 'recent'}</span>
                    <span class="episode-text">${escapeHtml(ep.action || ep.episode_type || 'Action')} — ${escapeHtml(ep.result_summary || ep.context || '')}</span>
                </div>
            `).join("");
        } catch (e) {
            console.error("Episodes fetch error:", e);
        }
    }

    async function refreshFacts() {
        try {
            const res = await fetch("/api/memory/facts");
            const data = await res.json();
            const facts = data.facts || [];

            if (countFacts) countFacts.textContent = facts.length;

            if (!factsGridList) return;

            if (facts.length === 0) {
                factsGridList.innerHTML = `<div class="empty-feed">No knowledge base facts recorded yet.</div>`;
                return;
            }

            factsGridList.innerHTML = facts.map(f => `
                <div class="memory-fact-card">
                    <div class="memory-fact-key">${escapeHtml(f.key)}</div>
                    <div class="memory-fact-val">${escapeHtml(f.value)}</div>
                    <span class="memory-fact-cat">${(f.category || 'general').toLowerCase()}</span>
                </div>
            `).join("");
        } catch (e) {
            console.error("Facts fetch error:", e);
        }
    }

    async function loadToolsCatalogue() {
        try {
            const res = await fetch("/api/tools");
            const data = await res.json();
            const tools = data.tools || [];
            // For home page quick ref (legacy)
            const toolsPillsList = document.getElementById("tools-pills-list");
            if (tools.length > 0 && toolsPillsList) {
                toolsPillsList.innerHTML = tools.map(t => `<span class="tool-pill" title="${escapeHtml(t.description)}"><span class="tool-dot"></span>${t.name}</span>`).join("");
            }
        } catch (e) {
            console.error("Tools fetch error:", e);
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 14. Tab Navigation Controller
    // ─────────────────────────────────────────────────────────────────────────
    document.querySelectorAll(".tab-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
            document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));

            btn.classList.add("active");
            const targetId = btn.getAttribute("data-tab");
            state.activeTab = targetId;
            const targetPanel = document.getElementById(targetId);
            if (targetPanel) {
                targetPanel.classList.add("active");
            }
        });
    });

    const btnRefreshTabs = document.getElementById("btn-refresh-tabs");
    if (btnRefreshTabs) {
        btnRefreshTabs.addEventListener("click", () => {
            refreshAuditLogs();
            refreshEpisodes();
            refreshFacts();
        });
    }

    function escapeHtml(text) {
        if (!text) return "";
        return String(text)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 15. Developer Arsenal: In-HUD API Tester & Latency Benchmarker
    // ─────────────────────────────────────────────────────────────────────────
    const apiMethod = document.getElementById("api-method");
    const apiUrl = document.getElementById("api-url");
    const apiBodyWrap = document.getElementById("api-body-wrap");
    const apiRequestBody = document.getElementById("api-request-body");
    const btnApiSend = document.getElementById("btn-api-send");
    const apiStatusPill = document.getElementById("api-status-pill");
    const apiLatencyPill = document.getElementById("api-latency-pill");
    const apiTypePill = document.getElementById("api-type-pill");
    const apiResponseJson = document.getElementById("api-response-json");
    const btnGenTypescript = document.getElementById("btn-gen-typescript");
    const btnGenPydantic = document.getElementById("btn-gen-pydantic");
    const btnCopyJson = document.getElementById("btn-copy-json");

    let lastAPIResponseData = null;

    if (apiMethod) {
        apiMethod.addEventListener("change", () => {
            const m = apiMethod.value;
            if (["POST", "PUT", "PATCH"].includes(m)) {
                apiBodyWrap.style.display = "flex";
            } else {
                apiBodyWrap.style.display = "none";
            }
        });
    }

    if (btnApiSend) {
        btnApiSend.addEventListener("click", async () => {
            const url = apiUrl.value.trim();
            const method = apiMethod.value;
            if (!url) return;

            let body = null;
            if (["POST", "PUT", "PATCH"].includes(method) && apiRequestBody.value.trim()) {
                try {
                    body = JSON.parse(apiRequestBody.value.trim());
                } catch (e) {
                    alert("Invalid JSON in Request Body!");
                    return;
                }
            }

            btnApiSend.disabled = true;
            btnApiSend.textContent = "SENDING...";
            apiStatusPill.textContent = "PROBING...";
            apiStatusPill.className = "text-amber";
            apiResponseJson.textContent = `// Dispatching ${method} request to ${url}...`;

            try {
                const res = await fetch("/api/dev/test_endpoint", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ url, method, body })
                });
                const data = await res.json();
                lastAPIResponseData = data.response;

                if (data.status_code) {
                    apiStatusPill.textContent = `${data.status_code} ${data.reason || 'OK'}`;
                    apiStatusPill.className = data.status_code < 400 ? "pill-green" : "text-amber";
                }
                apiLatencyPill.textContent = `${data.latency_ms || 0} ms`;
                apiTypePill.textContent = (data.headers && data.headers["content-type"]) ? data.headers["content-type"].split(";")[0] : "application/json";

                apiResponseJson.textContent = typeof data.response === "object"
                    ? JSON.stringify(data.response, null, 2)
                    : (data.response || data.output || "No payload returned.");

            } catch (err) {
                apiStatusPill.textContent = "FAIL";
                apiStatusPill.className = "text-amber";
                apiResponseJson.textContent = `Error testing endpoint: ${err.message}`;
            } finally {
                btnApiSend.disabled = false;
                btnApiSend.textContent = "SEND REQUEST ⚡";
            }
        });
    }

    async function generateTypesFromAPI(targetLang) {
        if (!lastAPIResponseData) {
            alert("Please send a request first to get a JSON response!");
            return;
        }
        apiResponseJson.textContent = `// Generating ${targetLang.toUpperCase()} models via Groq 500 T/S...`;
        try {
            const res = await fetch("/api/dev/generate_types", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ json_data: lastAPIResponseData, target_lang: targetLang })
            });
            const data = await res.json();
            apiResponseJson.textContent = data.code || "// Failed to generate types.";
        } catch (e) {
            apiResponseJson.textContent = `// Type generation error: ${e.message}`;
        }
    }

    if (btnGenTypescript) btnGenTypescript.addEventListener("click", () => generateTypesFromAPI("typescript"));
    if (btnGenPydantic) btnGenPydantic.addEventListener("click", () => generateTypesFromAPI("pydantic"));
    if (btnCopyJson) {
        btnCopyJson.addEventListener("click", () => {
            navigator.clipboard.writeText(apiResponseJson.textContent);
            btnCopyJson.textContent = "✓ COPIED!";
            setTimeout(() => { btnCopyJson.textContent = "📋 Copy JSON"; }, 1500);
        });
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 16. Developer Arsenal: Codebase Scaffolder Client
    // ─────────────────────────────────────────────────────────────────────────
    const scaffoldProjectName = document.getElementById("scaffold-project-name");
    const scaffoldDescription = document.getElementById("scaffold-description");
    const scaffoldTargetDir = document.getElementById("scaffold-target-dir");
    const btnScaffoldGenerate = document.getElementById("btn-scaffold-generate");
    const scaffoldLogBox = document.getElementById("scaffold-log-box");
    const btnModeScaffold = document.getElementById("btn-mode-scaffold");

    if (btnModeScaffold) {
        btnModeScaffold.addEventListener("click", () => {
            const tabBtn = document.getElementById("btn-tab-scaffolder");
            if (tabBtn) tabBtn.click();
        });
    }

    document.querySelectorAll(".template-card").forEach(card => {
        card.addEventListener("click", () => {
            document.querySelectorAll(".template-card").forEach(c => c.classList.remove("active"));
            card.classList.add("active");
            const radio = card.querySelector("input[type='radio']");
            if (radio) radio.checked = true;
        });
    });

    if (btnScaffoldGenerate) {
        btnScaffoldGenerate.addEventListener("click", async () => {
            const projectName = scaffoldProjectName.value.trim() || "DevProject";
            const description = scaffoldDescription ? scaffoldDescription.value.trim() : "";
            const checkedRadio = document.querySelector("input[name='scaffold-template']:checked");
            const template = checkedRadio ? checkedRadio.value : "fastapi_postgres";
            const targetDir = scaffoldTargetDir.value.trim() || null;

            btnScaffoldGenerate.disabled = true;
            btnScaffoldGenerate.textContent = "GENERATING CODEBASE...";
            scaffoldLogBox.textContent = `[DOOM SCAFFOLDER] Assembling ${template} architecture for '${projectName}'...${description ? '\n[AI CUSTOMIZER] Synthesizing domain logic: ' + description : ''}`;

            try {
                const res = await fetch("/api/dev/scaffold", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ project_name: projectName, template, description, target_dir: targetDir })
                });
                const data = await res.json();

                if (data.success) {
                    scaffoldLogBox.textContent = `[SUCCESS] ${data.output}\n\n📁 Project Directory: ${data.data.path}\n📄 Files Generated (${data.data.files.length}):\n${data.data.files.map(f => '  ✓ ' + f).join('\n')}`;
                    speakText(`Codebase ${projectName} generated successfully on your workstation, Boss Sujal.`);
                    refreshFacts();
                } else {
                    scaffoldLogBox.textContent = `[FAILED] ${data.output}`;
                }
            } catch (err) {
                scaffoldLogBox.textContent = `[ERROR] Scaffolding failed: ${err.message}`;
            } finally {
                btnScaffoldGenerate.disabled = false;
                btnScaffoldGenerate.textContent = "🚀 GENERATE COMPLETE CODEBASE NOW";
            }
        });
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 17. AI Agent Studio V2 — Antigravity-Style Intelligence Panel
    // ─────────────────────────────────────────────────────────────────────────
    const agentChatFeed     = document.getElementById("agent-chat-feed");
    const agentPromptInput  = document.getElementById("agent-prompt-input");
    const btnAgentSend      = document.getElementById("btn-agent-send");
    const agentFilePath     = document.getElementById("agent-file-path");
    const agentTypingIndicator = document.getElementById("agent-typing-indicator");
    const typingLabelText   = document.getElementById("typing-label-text");
    const btnClearAgentChatV2 = document.getElementById("btn-clear-agent-chat-v2");
    const btnAgentNewChat   = document.getElementById("btn-agent-new-chat");
    const statMessages      = document.getElementById("stat-messages");
    const statTokens        = document.getElementById("stat-tokens");
    const statModelDisplay  = document.getElementById("stat-model-display");
    const statModeDisplay   = document.getElementById("stat-mode-display");
    let activeAgentMode     = "pair_programmer";
    let activeAgentModel    = "groq";
    let agentConversation   = [];
    let agentMsgCount       = 0;
    let agentTokenEst       = 0;

    // Set welcome timestamp
    const welcomeTs = document.getElementById("welcome-ts");
    if (welcomeTs) welcomeTs.textContent = new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});

    /* ── Model option click ── */
    const modelLabels = { groq:"GROQ", bedrock_claude:"CLAUDE", bedrock_nova:"NOVA", gemini:"GEMINI", auto:"AUTO" };
    document.querySelectorAll(".agent-model-option").forEach(opt => {
        opt.addEventListener("click", () => {
            document.querySelectorAll(".agent-model-option").forEach(o => o.classList.remove("active"));
            opt.classList.add("active");
            activeAgentModel = opt.dataset.model;
            if (statModelDisplay) statModelDisplay.textContent = modelLabels[activeAgentModel] || activeAgentModel.toUpperCase();
        });
    });

    /* ── Mode option click ── */
    const modeShortLabels = { pair_programmer:"PAIR", architect:"ARCH", debugger:"BUG", reviewer:"REV" };
    document.querySelectorAll(".agent-mode-option").forEach(opt => {
        opt.addEventListener("click", () => {
            document.querySelectorAll(".agent-mode-option").forEach(o => o.classList.remove("active"));
            opt.classList.add("active");
            activeAgentMode = opt.dataset.agentMode;
            if (statModeDisplay) statModeDisplay.textContent = modeShortLabels[activeAgentMode] || "PAIR";
        });
    });

    /* ── Quick chips ── */
    document.querySelectorAll(".agent-chip").forEach(chip => {
        chip.addEventListener("click", () => {
            if (agentPromptInput) {
                agentPromptInput.value = chip.dataset.quick;
                agentPromptInput.focus();
            }
        });
    });

    /* ── Clear / New Chat ── */
    function clearAgentChat() {
        agentConversation = [];
        agentMsgCount = 0;
        agentTokenEst = 0;
        if (statMessages) statMessages.textContent = "0";
        if (statTokens) statTokens.textContent = "0";
        const ts = new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
        agentChatFeed.innerHTML = `
            <div class="agent-msg-v2 agent-msg-ai">
                <div class="agent-msg-avatar"><div class="avatar-ai-dot"></div><span>D</span></div>
                <div class="agent-msg-content-wrap">
                    <div class="agent-msg-meta-row">
                        <span class="agent-msg-sender">DOOM AI</span>
                        <span class="agent-msg-timestamp">${ts}</span>
                    </div>
                    <div class="agent-msg-text"><p>New session started. Standing by, <strong>Boss Sujal</strong>.</p></div>
                </div>
            </div>`;
    }
    if (btnClearAgentChatV2) btnClearAgentChatV2.addEventListener("click", clearAgentChat);
    if (btnAgentNewChat)     btnAgentNewChat.addEventListener("click", clearAgentChat);

    /* ── Ctrl+Enter to send ── */
    if (agentPromptInput) {
        agentPromptInput.addEventListener("keydown", e => {
            if (e.ctrlKey && e.key === "Enter") { e.preventDefault(); sendAgentMessage(); }
        });
        // Auto-resize
        agentPromptInput.addEventListener("input", () => {
            agentPromptInput.style.height = "auto";
            agentPromptInput.style.height = Math.min(agentPromptInput.scrollHeight, 160) + "px";
        });
    }
    if (btnAgentSend) btnAgentSend.addEventListener("click", sendAgentMessage);

    /* ── Voice mic for agent ── */
    const btnAgentMic = document.getElementById("btn-agent-mic");
    if (btnAgentMic && 'webkitSpeechRecognition' in window) {
        btnAgentMic.addEventListener("click", () => {
            const rec = new webkitSpeechRecognition();
            rec.lang = "en-US"; rec.interimResults = false;
            rec.onresult = e => { if (agentPromptInput) agentPromptInput.value = e.results[0][0].transcript; };
            rec.start();
        });
    }

    /* ── Append V2 user bubble ── */
    function appendUserBubbleV2(text) {
        const ts = new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
        const div = document.createElement("div");
        div.className = "agent-msg-v2 agent-msg-user";
        div.innerHTML = `
            <div class="agent-msg-avatar"><span>S</span></div>
            <div class="agent-msg-content-wrap">
                <div class="agent-msg-meta-row">
                    <span class="agent-msg-sender">Sujal</span>
                    <span class="agent-msg-timestamp">${ts}</span>
                </div>
                <div class="agent-msg-text"><p>${escapeHtml(text)}</p></div>
            </div>`;
        agentChatFeed.appendChild(div);
        agentChatFeed.scrollTop = agentChatFeed.scrollHeight;
    }

    /* ── Show/hide typing indicator ── */
    function showTyping(model) {
        if (agentTypingIndicator) {
            agentTypingIndicator.style.display = "flex";
            if (typingLabelText) typingLabelText.textContent = `DOOM (${modelLabels[model] || model}) is thinking`;
            agentChatFeed.scrollTop = agentChatFeed.scrollHeight;
        }
    }
    function hideTyping() {
        if (agentTypingIndicator) agentTypingIndicator.style.display = "none";
    }

    /* ── Append V2 AI response bubble ── */
    function appendAIBubbleV2(data) {
        const ts = new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
        const modeLabels2 = { pair_programmer:"Pair Programmer", architect:"Architect", debugger:"Bug Hunter", reviewer:"Code Reviewer" };

        // Plain text (strip code blocks)
        const plainText = data.response.replace(/```[\s\S]*?```/g, "").trim();

        // Code blocks
        let codeHtml = "";
        if (data.code_blocks && data.code_blocks.length) {
            codeHtml = data.code_blocks.map((cb, idx) => `
                <div class="agent-code-box" id="cbox-${idx}-${Date.now()}">
                    <div class="code-box-header">
                        <span class="code-lang-tag">📄 ${cb.language || "code"}</span>
                        <div class="code-box-actions">
                            <button class="btn-code-action" onclick="agentCopyCode(this,${JSON.stringify(cb.code)})">📋 Copy</button>
                            <button class="btn-code-action" onclick="agentSaveFile(this,${JSON.stringify(cb.suggested_file||'solution.txt')},${JSON.stringify(cb.code)})">💾 Save to File</button>
                            <button class="btn-code-action" onclick="agentRunTerminal(this,${JSON.stringify(cb.code)})">⚡ Run</button>
                        </div>
                    </div>
                    <pre style="margin:0;padding:0.65rem;font-size:0.74rem;font-family:var(--font-mono);overflow-x:auto;background:rgba(0,0,0,0.45);color:#a5f3fc;white-space:pre-wrap;">${escapeHtml(cb.code)}</pre>
                </div>`).join("");
        }

        const div = document.createElement("div");
        div.className = "agent-msg-v2 agent-msg-ai";
        div.innerHTML = `
            <div class="agent-msg-avatar"><div class="avatar-ai-dot"></div><span>D</span></div>
            <div class="agent-msg-content-wrap">
                <div class="agent-msg-meta-row">
                    <span class="agent-msg-sender">DOOM AI</span>
                    <span class="agent-msg-timestamp">${ts} · ${data.latency_ms||0}ms · ${(modelLabels[data.model]||data.model||"").toUpperCase()}</span>
                </div>
                <div class="agent-msg-text" style="white-space:pre-wrap;">${escapeHtml(plainText)}${codeHtml}</div>
            </div>`;
        agentChatFeed.appendChild(div);
        agentChatFeed.scrollTop = agentChatFeed.scrollHeight;
    }

    /* ── Render a "thinking" placeholder bubble ── */
    function appendThinkingBubble(model, mode) {
        const modeLabels = { pair_programmer: "PAIR PROGRAMMER", architect: "ARCHITECT", debugger: "BUG HUNTER" };
        const div = document.createElement("div");
        div.className = "agent-msg-bubble agent-bot";
        div.id = "agent-thinking-bubble";
        div.innerHTML = `
            <div class="msg-author-header">
                <span class="author-icon">🤖</span>
                <span class="author-name">DOOM AI AGENT</span>
                <span class="author-tag">${modeLabels[mode] || "AGENT"}</span>
            </div>
            <div class="step-card plan"><span>⚙ PROCESSING — ${model.toUpperCase()} @ ${new Date().toLocaleTimeString()}</span></div>
            <div class="msg-body" id="agent-thinking-text" style="color: var(--text-muted); font-style: italic;">Reasoning... Please wait.</div>`;
        agentChatFeed.appendChild(div);
        agentChatFeed.scrollTop = agentChatFeed.scrollHeight;
        return div;
    }

    /* ── Build the full bot response bubble from API data ── */
    function buildBotResponseBubble(data) {
        const modeLabels = { pair_programmer: "PAIR PROGRAMMER", architect: "ARCHITECT", debugger: "BUG HUNTER" };
        const modeTag = modeLabels[data.mode] || "AGENT";

        let stepsHtml = "";
        if (data.steps && data.steps.length) {
            stepsHtml = data.steps.map(s => `
                <div class="step-card ${s.type}">
                    <span>${s.type === "plan" ? "📋" : "🔧"} [${s.type.toUpperCase()}] ${escapeHtml(s.title)}: <em>${escapeHtml(s.desc)}</em></span>
                </div>`).join("");
        }

        // Render the raw text (strip code blocks — shown separately below)
        const plainText = data.response.replace(/```[\s\S]*?```/g, "").trim();

        let codeBlocksHtml = "";
        if (data.code_blocks && data.code_blocks.length) {
            codeBlocksHtml = data.code_blocks.map((cb, idx) => `
                <div class="agent-code-box" id="code-box-${idx}-${Date.now()}">
                    <div class="code-box-header">
                        <span class="code-lang-tag">📄 ${cb.language || "code"}</span>
                        <div class="code-box-actions">
                            <button class="btn-code-action" onclick="agentCopyCode(this, ${JSON.stringify(cb.code)})">📋 Copy</button>
                            <button class="btn-code-action btn-save-file" onclick="agentSaveFile(this, ${JSON.stringify(cb.suggested_file || "solution.txt")}, ${JSON.stringify(cb.code)})">💾 Save to File</button>
                            <button class="btn-code-action btn-run-term" onclick="agentRunTerminal(this, ${JSON.stringify(cb.code)})">⚡ Run</button>
                        </div>
                    </div>
                    <pre style="margin:0; padding:0.65rem; font-size:0.74rem; font-family:var(--font-mono); overflow-x:auto; background:rgba(0,0,0,0.45); color:#a5f3fc; white-space:pre-wrap;">${escapeHtml(cb.code)}</pre>
                </div>`).join("");
        }

        const div = document.createElement("div");
        div.className = "agent-msg-bubble agent-bot";
        div.innerHTML = `
            <div class="msg-author-header">
                <span class="author-icon">🤖</span>
                <span class="author-name">DOOM AI AGENT</span>
                <span class="author-tag">${modeTag}</span>
                <span style="margin-left:auto; font-size:0.6rem; color:var(--text-muted);">${data.latency_ms}ms · ${data.timestamp}</span>
            </div>
            ${stepsHtml}
            <div class="msg-body" style="white-space:pre-wrap;">${escapeHtml(plainText)}</div>
            ${codeBlocksHtml}`;
        return div;
    }

    /* ── Helper: escape HTML ── */
    function escapeHtml(str) {
        return String(str || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
    }

    /* ── Main: Send agent message ── */
    async function sendAgentMessage() {
        const prompt = agentPromptInput ? agentPromptInput.value.trim() : "";
        if (!prompt) return;

        const selectedModel = activeAgentModel || "groq";
        const filePath = agentFilePath ? agentFilePath.value.trim() : "";

        agentConversation.push({ role: "user", content: prompt });
        appendUserBubbleV2(prompt);
        agentPromptInput.value = "";
        agentPromptInput.style.height = "auto";

        agentMsgCount++;
        agentTokenEst += Math.ceil(prompt.length / 4);
        if (statMessages) statMessages.textContent = agentMsgCount;
        if (statTokens)   statTokens.textContent   = agentTokenEst;

        if (btnAgentSend) { btnAgentSend.disabled = true; const lbl = document.getElementById("agent-send-label"); if(lbl) lbl.textContent = "..."; }
        showTyping(selectedModel);

        let fileContent = null;
        if (filePath) {
            try {
                const fc = await fetch(`/api/agent/read_file?path=${encodeURIComponent(filePath)}`);
                if (fc.ok) fileContent = (await fc.json()).content;
            } catch {}
        }

        try {
            const res = await fetch("/api/agent/chat", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    prompt,
                    model: selectedModel,
                    mode: activeAgentMode,
                    file_path: filePath || null,
                    file_content: fileContent
                })
            });

            const data = await res.json();
            agentConversation.push({ role: "assistant", content: data.response });

            hideTyping();
            appendAIBubbleV2(data);

            // Update token estimate with response
            agentTokenEst += Math.ceil((data.response || "").length / 4);
            agentMsgCount++;
            if (statMessages) statMessages.textContent = agentMsgCount;
            if (statTokens)   statTokens.textContent   = agentTokenEst;

            const shortSpeech = data.response.replace(/```[\s\S]*?```/g, "").replace(/[#*`_]/g, "").trim().split("\n")[0].slice(0, 200);
            speakText(shortSpeech);

        } catch (err) {
            hideTyping();
            const errDiv = document.createElement("div");
            errDiv.className = "agent-msg-v2 agent-msg-ai";
            errDiv.innerHTML = `
                <div class="agent-msg-avatar"><span>D</span></div>
                <div class="agent-msg-content-wrap">
                    <div class="agent-msg-meta-row"><span class="agent-msg-sender">DOOM AI</span></div>
                    <div class="agent-msg-text" style="color:var(--neon-amber);">⚠ Agent error: ${escapeHtml(err.message)}</div>
                </div>`;
            agentChatFeed.appendChild(errDiv);
        } finally {
            if (btnAgentSend) { btnAgentSend.disabled = false; const lbl = document.getElementById("agent-send-label"); if(lbl) lbl.textContent = "Send"; }
            agentChatFeed.scrollTop = agentChatFeed.scrollHeight;
        }
    }

    /* ── Global: Copy code block ── */
    window.agentCopyCode = function(btn, code) {
        navigator.clipboard.writeText(code);
        btn.textContent = "✓ Copied!";
        setTimeout(() => { btn.textContent = "📋 Copy"; }, 1500);
    };

    /* ── Global: Save generated code directly to disk ── */
    window.agentSaveFile = async function(btn, suggestedPath, code) {
        const targetPath = window.prompt("💾 Save to file path:", suggestedPath);
        if (!targetPath) return;
        btn.textContent = "SAVING...";
        try {
            const res = await fetch("/api/agent/write_code_file", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ file_path: targetPath, content: code })
            });
            const data = await res.json();
            if (data.success) {
                btn.textContent = "✓ Saved!";
                speakText(`File saved to ${targetPath}, Boss Sujal.`);
            } else {
                btn.textContent = "⚠ Failed";
            }
        } catch { btn.textContent = "⚠ Error"; }
        setTimeout(() => { btn.textContent = "💾 Save to File"; }, 2500);
    };

    /* ── Global: Run code in terminal ── */
    window.agentRunTerminal = async function(btn, code) {
        const command = window.prompt("⚡ Execute command:", code.split("\n")[0]);
        if (!command) return;
        btn.textContent = "RUNNING...";
        try {
            const res = await fetch("/api/agent/run_code_terminal", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ command })
            });
            const data = await res.json();
            btn.textContent = data.success ? "✓ Done!" : "⚠ Error";
            if (data.output) {
                const outDiv = document.createElement("div");
                outDiv.style.cssText = "font-family:var(--font-mono); font-size:0.72rem; color:#a5f3fc; background:rgba(0,0,0,0.4); padding:0.4rem 0.65rem; border-radius:4px; margin-top:0.4rem; white-space:pre-wrap;";
                outDiv.textContent = data.output;
                btn.closest(".agent-code-box").appendChild(outDiv);
            }
            speakText(`Command executed, Boss Sujal.`);
        } catch { btn.textContent = "⚠ Error"; }
        setTimeout(() => { btn.textContent = "⚡ Run"; }, 2500);
    };

    // ─────────────────────────────────────────────────────────────────────────
    // DOOM V3: PRIMARY NAVIGATION CONTROLLER & VIEW LOADERS
    // ─────────────────────────────────────────────────────────────────────────
    function switchDOOMView(targetView) {
        if (!targetView) return;
        const navBtns = document.querySelectorAll(".nav-btn, .v3-nav-btn, [data-view]");
        navBtns.forEach(b => {
            if (b.dataset.view === targetView) {
                b.classList.add("active");
            } else if (b.classList.contains("nav-btn") || b.classList.contains("v3-nav-btn")) {
                b.classList.remove("active");
            }
        });

        document.querySelectorAll(".view-panel").forEach(p => p.classList.remove("active"));
        const panel = document.getElementById(`view-${targetView}`);
        if (panel) panel.classList.add("active");

        if (targetView === "tasks") loadTasksView();
        if (targetView === "system") loadSystemView();
        if (targetView === "activity") refreshAuditLogs();
        if (targetView === "memory") {
            refreshFacts();
            refreshEpisodes();
            if (typeof loadMemoryProfile === "function") loadMemoryProfile();
        }
        if (targetView === "home") updateGreeting();
    }
    window.switchDOOMView = switchDOOMView;

    function setupDOOMV3Nav() {
        const navBtns = document.querySelectorAll(".nav-btn, .v3-nav-btn, [data-view]");
        navBtns.forEach(btn => {
            btn.addEventListener("click", (e) => {
                const targetView = btn.dataset.view;
                if (targetView) {
                    switchDOOMView(targetView);
                }
            });
        });

        // Brand logo click navigates to Home
        const brand = document.querySelector(".header-brand");
        if (brand) {
            brand.style.cursor = "pointer";
            brand.addEventListener("click", () => switchDOOMView("home"));
        }
    }

    function setupControlCenterSubnav() {
        // Support both old .cc-subnav-btn and new .subnav-btn selectors
        const ccBtns = document.querySelectorAll(".subnav-btn, .cc-subnav-btn");
        ccBtns.forEach(btn => {
            btn.addEventListener("click", () => {
                const targetCC = btn.dataset.cc;
                ccBtns.forEach(b => b.classList.remove("active"));
                btn.classList.add("active");

                // Support both old .cc-section-panel and new .system-panel selectors
                document.querySelectorAll(".system-panel, .cc-section-panel").forEach(p => p.classList.remove("active"));
                // Try new panel naming first (cc-panel-hardware for the hardware tab)
                let panel = document.getElementById(`cc-panel-${targetCC}`);
                if (!panel) panel = document.getElementById(`cc-panel-telemetry`);
                if (panel) panel.classList.add("active");
            });
        });
    }

    function updateGreeting() {
        const hour = new Date().getHours();
        const greetingEl = document.getElementById("home-greeting-text");
        if (!greetingEl) return;
        if (hour < 12) greetingEl.textContent = "Good morning, Boss.";
        else if (hour < 17) greetingEl.textContent = "Good afternoon, Boss.";
        else greetingEl.textContent = "Good evening, Boss.";
    }

    async function loadTasksView() {
        try {
            const res = await fetch("/api/tasks");
            const data = await res.json();
            const container = document.getElementById("tasks-active-container");
            const historyGrid = document.getElementById("tasks-history-grid");

            if (data.active_task && container) {
                const t = data.active_task;
                container.innerHTML = `
                    <div class="task-card-v3" style="border-color: var(--neon-cyan);">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <span class="active-task-tag">◉ RUNNING // ${t.task_id}</span>
                            <span class="active-task-pct">${t.progress}%</span>
                        </div>
                        <h3 style="color:#ffffff; font-size:1.1rem; margin:0.4rem 0;">${t.goal}</h3>
                        <div class="task-progress-bar-wrap">
                            <div class="task-progress-fill" style="width: ${t.progress}%;"></div>
                        </div>
                        <div style="font-size:0.8rem; color:var(--text-secondary); margin-bottom:0.6rem;">Current: <strong>${t.current_step}</strong></div>
                        <ul class="task-steps-checklist">
                            ${(t.steps || []).map(s => {
                                const icon = s.status === 'completed' ? '✓' : (s.status === 'active' ? '◉' : '○');
                                return `<li class="task-step-item ${s.status}"><span>${icon}</span> <span>${s.description}</span></li>`;
                            }).join("")}
                        </ul>
                    </div>
                `;
            } else if (container) {
                container.innerHTML = `<div class="task-card-v3"><span style="color:var(--text-muted); font-size:0.85rem;">No active task running. Initiate a goal from Home or Chat to watch the execution loop.</span></div>`;
            }

            if (historyGrid && data.history) {
                if (data.history.length === 0) {
                    historyGrid.innerHTML = `<span style="color:var(--text-muted); font-size:0.8rem;">No previous tasks recorded yet.</span>`;
                } else {
                    historyGrid.innerHTML = data.history.map(t => `
                        <div class="task-card-v3">
                            <div style="display:flex; justify-content:space-between; align-items:center;">
                                <span style="font-size:0.7rem; font-family:var(--font-mono); color:var(--text-muted);">${t.created_at || ''}</span>
                                <span class="status-${t.status === 'COMPLETED' ? 'live' : 'ready'}" style="font-size:0.65rem; padding:2px 6px; border-radius:3px;">${t.status}</span>
                            </div>
                            <strong style="color:#ffffff; font-size:0.9rem;">${t.goal}</strong>
                            <div style="font-size:0.75rem; color:var(--text-secondary);">${t.result ? t.result.slice(0, 140) + '...' : 'In progress...'}</div>
                            <div style="font-size:0.7rem; font-family:var(--font-mono); color:var(--neon-cyan); margin-top:0.3rem;">
                                Tools: ${(t.tools_used || []).join(', ') || 'Direct'} • Duration: ${t.duration_ms || 0}ms
                            </div>
                        </div>
                    `).join("");
                }
            }
        } catch (e) {
            console.error("Tasks fetch error:", e);
        }
    }

    async function loadSystemView() {
        try {
            // Load Intelligence Providers Matrix
            const intelRes = await fetch("/api/system/intelligence");
            const intelData = await intelRes.json();
            const intelList = document.getElementById("cc-intelligence-list");
            if (intelList && intelData.providers) {
                intelList.innerHTML = intelData.providers.map(p => {
                    const dotColors = { groq: 'var(--cyan)', openai: 'var(--green)', bedrock_claude: 'var(--purple)', gemini: 'var(--amber)', ollama: '#94a3b8', fallback: 'var(--text-muted)' };
                    const dotColor = dotColors[p.provider_id] || 'var(--text-muted)';
                    return `<div class="intelligence-card ${p.is_available ? 'active-provider' : ''}">
                        <span class="intel-dot" style="background:${dotColor}"></span>
                        <span class="intel-name">${p.name}</span>
                        <span class="intel-detail">${p.model} · ${p.role}</span>
                        <span class="intel-status ${p.is_available ? 'active' : 'unavailable'}">${p.is_available ? 'Online' : 'Offline'}</span>
                    </div>`;
                }).join("");
            }

            // Load Tools Registry with Risk Levels — grouped by category
            const toolsRes = await fetch("/api/system/tools");
            const toolsData = await toolsRes.json();
            const toolsBadgeCount = document.getElementById("tools-badge-count");
            const toolsCatContainer = document.getElementById("tools-category-container");
            if (toolsBadgeCount && toolsData.count) toolsBadgeCount.textContent = `${toolsData.count} Tools`;

            if (toolsCatContainer && toolsData.tools) {
                // Group tools by category
                const toolCategories = {};
                const defaultCats = { system: 'System & OS', file: 'Files & Storage', browser: 'Browser & Web', code: 'Code & Dev', media: 'Media & Audio', ai: 'AI & Models', db: 'Database', network: 'Network', other: 'Other' };
                toolsData.tools.forEach(t => {
                    const catKey = (t.category || 'other').toLowerCase();
                    if (!toolCategories[catKey]) toolCategories[catKey] = [];
                    toolCategories[catKey].push(t);
                });

                // If no categories from API, group by name prefix
                if (Object.keys(toolCategories).length <= 1 && toolCategories.other) {
                    const tools = toolCategories.other;
                    delete toolCategories.other;
                    const nameCategories = { 'screenshot': 'system', 'cpu': 'system', 'ram': 'system', 'disk': 'system', 'process': 'system', 'file': 'file', 'read': 'file', 'write': 'file', 'browse': 'browser', 'web': 'browser', 'search': 'browser', 'code': 'code', 'python': 'code', 'run': 'code', 'music': 'media', 'audio': 'media', 'play': 'media', 'groq': 'ai', 'llm': 'ai', 'model': 'ai', 'postgres': 'db', 'db': 'db', 'sql': 'db', 'email': 'network', 'http': 'network', 'api': 'network' };
                    tools.forEach(t => {
                        const name = t.name.toLowerCase();
                        let assigned = 'other';
                        for (const [kw, cat] of Object.entries(nameCategories)) {
                            if (name.includes(kw)) { assigned = cat; break; }
                        }
                        if (!toolCategories[assigned]) toolCategories[assigned] = [];
                        toolCategories[assigned].push(t);
                    });
                }

                const riskDotClass = { 'SAFE': 'tool-item-safe', 'LOW': 'tool-item-low', 'MEDIUM': 'tool-item-medium', 'HIGH': 'tool-item-high', 'CRITICAL': 'tool-item-critical' };

                toolsCatContainer.innerHTML = Object.entries(toolCategories).map(([catKey, tools]) => {
                    const catName = defaultCats[catKey] || (catKey.charAt(0).toUpperCase() + catKey.slice(1));
                    const toolItems = tools.map(t => `
                        <div class="tool-item">
                            <span class="tool-item-dot ${riskDotClass[t.risk_level] || 'tool-item-low'}"></span>
                            <div>
                                <div class="tool-item-name">${t.name}</div>
                                <div class="tool-item-desc">${(t.description || '').slice(0, 60)}${t.description && t.description.length > 60 ? '...' : ''}</div>
                            </div>
                        </div>
                    `).join('');
                    return `
                        <div class="tool-category" id="cat-${catKey}">
                            <div class="tool-category-header" onclick="this.parentElement.classList.toggle('expanded')">
                                <div class="tool-cat-left">
                                    <span class="tool-cat-name">${catName}</span>
                                    <span class="tool-cat-count">${tools.length}</span>
                                </div>
                                <svg class="tool-cat-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
                            </div>
                            <div class="tool-items">${toolItems}</div>
                        </div>
                    `;
                }).join('');
            }
        } catch (e) {
            console.error("System view load error:", e);
        }
    }

    async function loadMemoryProfile() {
        try {
            const res = await fetch("/api/memory/profile");
            if (!res.ok) return;
            const p = await res.json();

            const profileBlock = document.getElementById("memory-profile-block");
            if (profileBlock) {
                profileBlock.innerHTML = `
                    <div class="memory-profile-name">${p.name || 'Sujal'}</div>
                    <div class="memory-profile-role">${p.title || 'Creator, Boss & Lead AI Engineer'}</div>
                    <span class="memory-profile-tag">${p.access_level || 'Root / Level 10'}</span>
                `;
            }

            const prefsBlock = document.getElementById("memory-prefs-block");
            if (prefsBlock) {
                const prefs = p.preferences || {};
                prefsBlock.innerHTML = Object.entries(prefs).map(([k, v]) =>
                    `<div class="memory-kv-item"><span class="memory-kv-key">${k}</span><span class="memory-kv-val">${v}</span></div>`
                ).join('') || '<span class="empty-feed">No preferences stored yet.</span>';
            }

            const projBlock = document.getElementById("memory-projects-block");
            if (projBlock) {
                const projects = p.projects || [];
                projBlock.innerHTML = projects.length
                    ? projects.map(pr => `<div class="memory-kv-item"><span class="memory-kv-val">${pr}</span></div>`).join('')
                    : '<span style="font-size:0.8rem;color:var(--text-secondary)">DOOM V3 — Personal AI OS</span>';
            }
        } catch (e) {
            console.log("Memory profile not loaded:", e.message);
        }
    }

    // Double-clap calibration button in System / Sensors
    const btnClapCalibrate = document.getElementById("btn-clap-calibrate");
    if (btnClapCalibrate) {
        btnClapCalibrate.addEventListener("click", async () => {
            btnClapCalibrate.textContent = "Calibrating (be quiet)...";
            try {
                const res = await fetch("/api/clap/calibrate", { method: "POST" });
                const data = await res.json();
                btnClapCalibrate.textContent = `Calibrated (${data.threshold} RMS)`;
                setTimeout(() => { btnClapCalibrate.textContent = "Calibrate Threshold"; }, 3000);
            } catch {
                btnClapCalibrate.textContent = "Calibration Failed";
                setTimeout(() => { btnClapCalibrate.textContent = "Calibrate Threshold"; }, 2500);
            }
        });
    }

    // Initial Data Loads & DOOM V3 Navigation Setup
    setupDOOMV3Nav();
    setupControlCenterSubnav();
    updateGreeting();
    refreshAuditLogs();
    refreshEpisodes();
    refreshFacts();
    loadToolsCatalogue();
    updateMusicUI();

    // Home activity feed update on command completion
    const origExecuteGoalRef = window._origExecuteGoal;
    function addHomeActivity(text) {
        const feed = document.getElementById("home-recent-activity");
        if (!feed) return;
        const item = document.createElement("div");
        item.className = "home-activity-item";
        const now = new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
        item.innerHTML = `<span class="hai-dot"></span><span class="hai-text">${escapeHtml(text.slice(0, 80))}</span><span class="hai-time">${now}</span>`;
        feed.insertBefore(item, feed.firstChild);
        // Keep only 5 items
        while (feed.children.length > 5) feed.removeChild(feed.lastChild);
    }

    // Patch executeGoal to update home activity feed
    const origExecuteGoal = executeGoal;
    async function executeGoalPatched(goalText) {
        await origExecuteGoal(goalText);
        addHomeActivity(goalText);
    }
    // Wire up command form with patched executor
    if (commandForm) {
        commandForm.removeEventListener('submit', commandForm._handler);
    }

    // Orb container state class manager (maps DoomState to CSS)
    function setOrbState(stateName) {
        const orb = document.getElementById("orb-container");
        if (!orb) return;
        orb.className = 'orb-container'; // reset
        if (stateName) orb.classList.add(`state-${stateName.toUpperCase()}`);
    }

    // Patch the WS message handler to update orb state class
    const origHandleTelemetry = handleTelemetryUpdate;
    function handleTelemetryUpdatePatched(data) {
        origHandleTelemetry(data);
        // Update home health pill
        const healthText = document.getElementById("home-health-text");
        if (healthText) healthText.textContent = `System healthy — CPU: ${Math.round(data.cpu_percent || 0)}% · RAM: ${Math.round(data.memory_percent || 0)}%`;
    }
});

