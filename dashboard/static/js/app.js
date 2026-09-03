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
    const handsfreeIcon = document.getElementById("handsfree-icon");
    const handsfreeLabel = document.getElementById("handsfree-label");
    const btnScheduleBriefing = document.getElementById("btn-schedule-briefing");
    const scheduleTimeLabel = document.getElementById("schedule-time-label");
    const btnGestureToggle = document.getElementById("btn-gesture-toggle");

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

    const coreStateText = document.getElementById("core-state-text");
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

    // ─────────────────────────────────────────────────────────────────────────
    // 1. Digital Master Clock & Uptime
    // ─────────────────────────────────────────────────────────────────────────
    function updateClock() {
        const now = new Date();
        digitalClock.textContent = now.toTimeString().split(" ")[0];
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
            btnMusicPlay.classList.add("playing");
            musicPlayIcon.textContent = "⏸";
            musicEqualizer.classList.add("active");
        } else {
            btnMusicPlay.classList.remove("playing");
            musicPlayIcon.textContent = "▶";
            musicEqualizer.classList.remove("active");
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
                btnVoiceToggle.classList.add("active");
                voiceIcon.textContent = "🔊";
                voiceLabel.textContent = "AUDIO ON";
            } else {
                btnVoiceToggle.classList.remove("active");
                voiceIcon.textContent = "🔇";
                voiceLabel.textContent = "AUDIO OFF";
                if (currentAudio) currentAudio.pause();
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
            termStatusIndicator.textContent = "LISTENING (HANDS-FREE)";
            termStatusIndicator.style.color = "var(--neon-green)";
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
                termStatusIndicator.textContent = "READY";
                termStatusIndicator.style.color = "var(--neon-cyan)";
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

    const tabOrder = ["tab-audit", "tab-episodes", "tab-facts", "tab-api-tester", "tab-scaffolder", "tab-profile"];

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

                    if (msg.sentinel_alert && sentinelBanner) {
                        sentinelMsg.textContent = `SENTINEL ALERT [${msg.sentinel_alert.level}]: ${msg.sentinel_alert.msg}`;
                        sentinelBanner.style.display = "flex";
                    }
                } else if (msg.type === "scheduled_briefing") {
                    speakText(msg.response || "Morning protocol initiated, Boss Sujal.");
                } else if (msg.type === "command_executed" || msg.type === "mode_triggered") {
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
            uptimeDisplay.textContent = `UPTIME: ${data.uptime_formatted}`;
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
        termStatusIndicator.textContent = "PROCESSING...";
        termStatusIndicator.style.color = "var(--neon-green)";
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
            termStatusIndicator.textContent = "READY";
            termStatusIndicator.style.color = "var(--neon-cyan)";
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
            const res = await fetch("/api/logs?limit=15");
            const data = await res.json();
            const logs = data.logs || [];

            countLogs.textContent = logs.length;

            if (logs.length === 0) {
                auditTableBody.innerHTML = `<tr><td colspan="7" class="loading-cell">No command logs found in PostgreSQL.</td></tr>`;
                return;
            }

            auditTableBody.innerHTML = logs.map(l => `
                <tr>
                    <td>#${l.id}</td>
                    <td><strong>${escapeHtml(l.user_input)}</strong></td>
                    <td><span class="pill-green" style="padding:2px 6px; border-radius:4px; font-size:11px;">${l.model_used}</span></td>
                    <td><span style="font-family:var(--font-mono); font-size:11px; color:var(--text-secondary);">${l.tool_called}</span></td>
                    <td><span style="color:var(--neon-green); font-family:var(--font-mono);">${l.latency_ms ? l.latency_ms + 'ms' : '-'}</span></td>
                    <td><span class="${l.status === 'SUCCESS' ? 'text-green' : 'text-amber'}">${l.status}</span></td>
                    <td style="color:var(--text-muted); font-size:11px;">${l.created_at}</td>
                </tr>
            `).join("");
        } catch (e) {
            console.error("Logs fetch error:", e);
        }
    }

    async function refreshEpisodes() {
        try {
            const res = await fetch("/api/memory/episodes?limit=15");
            const data = await res.json();
            const episodes = data.episodes || [];

            countEpisodes.textContent = episodes.length;

            if (episodes.length === 0) {
                episodesFeedList.innerHTML = `<div class="empty-feed">No episodic action logs recorded yet.</div>`;
                return;
            }

            episodesFeedList.innerHTML = episodes.map(ep => `
                <div class="episode-item">
                    <div>
                        <div class="episode-title">${escapeHtml(ep.action || ep.episode_type || 'Action Episode')}</div>
                        <div style="font-size:12px; color:var(--text-secondary); margin-top:2px;">${escapeHtml(ep.result_summary || ep.context || '')}</div>
                    </div>
                    <div class="episode-meta">${ep.timestamp || ep.created_at || ''}</div>
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

            countFacts.textContent = facts.length;

            if (facts.length === 0) {
                factsGridList.innerHTML = `<div class="empty-feed">No semantic memory facts stored in PostgreSQL yet.</div>`;
                return;
            }

            factsGridList.innerHTML = facts.map(f => `
                <div class="fact-card">
                    <span class="fact-cat">[${f.category.toUpperCase()}]</span>
                    <span class="fact-key">${escapeHtml(f.key)}</span>
                    <span class="fact-val">${escapeHtml(f.value)}</span>
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

            if (tools.length > 0 && toolsPillsList) {
                toolsPillsList.innerHTML = tools.map(t => `
                    <span class="tool-pill" title="${escapeHtml(t.description)}">
                        <span class="tool-dot"></span>${t.name}
                    </span>
                `).join("");
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

    // Initial Data Loads
    refreshAuditLogs();
    refreshEpisodes();
    refreshFacts();
    loadToolsCatalogue();
    updateMusicUI();
});
