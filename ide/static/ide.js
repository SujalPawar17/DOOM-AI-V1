/* ─────────────────────────────────────────────────────────────────────────────
   DOOM CYBER IDE — CLIENT-SIDE MONACO CONTROLLER & WORKSPACE ENGINE
───────────────────────────────────────────────────────────────────────────── */

let monacoEditor = null;
let currentWorkspace = { root: '', name: 'DOOM' };
let openTabs = []; // Array of { path, absPath, filename, content, savedContent, language, dirty }
let activeTabPath = null;
let selectedCodeSnippet = '';

// ─────────────────────────────────────────────────────────────────────────────
// 1. Monaco Editor Initialization
// ─────────────────────────────────────────────────────────────────────────────
function initMonaco() {
    require.config({ paths: { vs: 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs' } });

    require(['vs/editor/editor.main'], function () {
        // Define Custom DOOM Cyberpunk Theme
        monaco.editor.defineTheme('doom-cyber-dark', {
            base: 'vs-dark',
            inherit: true,
            rules: [
                { token: 'comment', foreground: '64748b', fontStyle: 'italic' },
                { token: 'keyword', foreground: '00f3ff', fontStyle: 'bold' },
                { token: 'string', foreground: '10b981' },
                { token: 'number', foreground: 'f59e0b' },
                { token: 'type', foreground: 'a855f7' },
                { token: 'function', foreground: '38bdf8' },
                { token: 'variable', foreground: 'f1f5f9' },
                { token: 'delimiter', foreground: '94a3b8' }
            ],
            colors: {
                'editor.background': '#07090e',
                'editor.foreground': '#f1f5f9',
                'editorCursor.foreground': '#00f3ff',
                'editor.lineHighlightBackground': '#111722',
                'editorLineNumber.foreground': '#334155',
                'editorLineNumber.activeForeground': '#00f3ff',
                'editor.selectionBackground': '#1e293b',
                'editor.inactiveSelectionBackground': '#0f172a',
                'editorIndentGuide.background': '#1e293b',
                'editorIndentGuide.activeBackground': '#334155'
            }
        });

        const target = document.getElementById('monaco-target');
        monacoEditor = monaco.editor.create(target, {
            value: '',
            language: 'python',
            theme: 'doom-cyber-dark',
            automaticLayout: true,
            fontFamily: "'JetBrains Mono', Consolas, 'Courier New', monospace",
            fontSize: 13,
            lineHeight: 20,
            minimap: { enabled: true },
            scrollBeyondLastLine: false,
            smoothScrolling: true,
            cursorBlinking: 'smooth',
            bracketPairColorization: { enabled: true }
        });

        // Editor Event Listeners
        monacoEditor.onDidChangeModelContent(() => {
            const currentTab = getActiveTab();
            if (currentTab) {
                const newContent = monacoEditor.getValue();
                if (newContent !== currentTab.savedContent) {
                    setTabDirty(currentTab.path, true);
                } else {
                    setTabDirty(currentTab.path, false);
                }
            }
        });

        monacoEditor.onDidChangeCursorPosition((e) => {
            document.getElementById('sb-line').textContent = e.position.lineNumber;
            document.getElementById('sb-col').textContent = e.position.column;
        });

        monacoEditor.onDidChangeCursorSelection((e) => {
            const selection = monacoEditor.getSelection();
            if (selection && !selection.isEmpty()) {
                selectedCodeSnippet = monacoEditor.getModel().getValueInRange(selection);
                document.getElementById('ai-selection-badge').style.display = 'inline-block';
            } else {
                selectedCodeSnippet = '';
                document.getElementById('ai-selection-badge').style.display = 'none';
            }
        });

        // Global Keyboard Shortcut: Ctrl + S to Save
        window.addEventListener('keydown', (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
                e.preventDefault();
                saveActiveFile();
            } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'l') {
                e.preventDefault();
                document.getElementById('ai-prompt-input').focus();
            } else if (e.key === 'F5') {
                e.preventDefault();
                runActiveCode();
            }
        });
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// 2. Workspace & File Tree Operations
// ─────────────────────────────────────────────────────────────────────────────
async function loadWorkspaceInfo() {
    try {
        const res = await fetch('/api/workspace/info');
        const data = await res.json();
        currentWorkspace = { root: data.workspace_root, name: data.workspace_name };
        document.getElementById('workspace-name-display').textContent = data.workspace_name;
        document.getElementById('breadcrumb-root').textContent = data.workspace_name;
        document.getElementById('sb-workspace-path').textContent = data.workspace_name;
        await refreshFileTree();
    } catch (e) {
        console.error('Failed to load workspace info:', e);
    }
}

async function refreshFileTree() {
    const treeContainer = document.getElementById('file-tree');
    treeContainer.innerHTML = '<div class="tree-loading">Refreshing structure...</div>';
    try {
        const res = await fetch('/api/fs/tree');
        const data = await res.json();
        treeContainer.innerHTML = '';
        if (data.tree && data.tree.length > 0) {
            renderTreeNodes(data.tree, treeContainer);
        } else {
            treeContainer.innerHTML = '<div class="tree-loading">Workspace is empty.</div>';
        }
    } catch (e) {
        treeContainer.innerHTML = '<div class="tree-loading" style="color:var(--neon-red);">Failed to load tree</div>';
    }
}

function getFileIcon(filename, language) {
    const ext = filename.split('.').pop().toLowerCase();
    switch (ext) {
        case 'py': return '🐍';
        case 'js': return '🟨';
        case 'ts': return '🔷';
        case 'jsx': case 'tsx': return '⚛️';
        case 'html': return '🌐';
        case 'css': case 'scss': return '🎨';
        case 'json': return '⚙️';
        case 'md': return '📝';
        case 'sql': return '🗄️';
        case 'sh': case 'bat': case 'ps1': return '⚡';
        case 'png': case 'jpg': case 'svg': return '🖼️';
        default: return '📄';
    }
}

function renderTreeNodes(nodes, container, level = 0) {
    nodes.forEach(node => {
        const nodeEl = document.createElement('div');
        nodeEl.className = 'tree-node';
        nodeEl.style.paddingLeft = `${12 + level * 14}px`;
        nodeEl.dataset.path = node.path;
        nodeEl.dataset.type = node.type;

        if (node.type === 'directory') {
            nodeEl.innerHTML = `
                <span class="tree-arrow">▶</span>
                <span class="tree-icon">📁</span>
                <span class="tree-name">${node.name}</span>
            `;

            const childrenContainer = document.createElement('div');
            childrenContainer.className = 'tree-children';

            nodeEl.addEventListener('click', (e) => {
                e.stopPropagation();
                const arrow = nodeEl.querySelector('.tree-arrow');
                const isOpen = childrenContainer.classList.toggle('open');
                arrow.classList.toggle('open', isOpen);
                nodeEl.querySelector('.tree-icon').textContent = isOpen ? '📂' : '📁';
            });

            container.appendChild(nodeEl);
            container.appendChild(childrenContainer);

            if (node.children && node.children.length > 0) {
                renderTreeNodes(node.children, childrenContainer, level + 1);
            }
        } else {
            const icon = getFileIcon(node.name, node.language);
            nodeEl.innerHTML = `
                <span style="width:12px;"></span>
                <span class="tree-icon">${icon}</span>
                <span class="tree-name">${node.name}</span>
            `;

            nodeEl.addEventListener('click', (e) => {
                e.stopPropagation();
                document.querySelectorAll('.tree-node').forEach(n => n.classList.remove('active'));
                nodeEl.classList.add('active');
                openFile(node.path);
            });

            container.appendChild(nodeEl);
        }
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// 3. File Open, Tab & Editor Management
// ─────────────────────────────────────────────────────────────────────────────
async function openFile(filePath) {
    const existing = openTabs.find(t => t.path === filePath);
    if (existing) {
        activateTab(filePath);
        return;
    }

    try {
        const res = await fetch(`/api/fs/read?path=${encodeURIComponent(filePath)}`);
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();

        const newTab = {
            path: data.path,
            absPath: data.abs_path,
            filename: data.filename,
            content: data.content,
            savedContent: data.content,
            language: data.language,
            dirty: false
        };

        openTabs.push(newTab);
        renderTabs();
        activateTab(filePath);
    } catch (e) {
        alert('Could not open file: ' + e.message);
    }
}

function renderTabs() {
    const tabsBar = document.getElementById('tabs-bar');
    tabsBar.innerHTML = '';

    openTabs.forEach(tab => {
        const tabEl = document.createElement('div');
        tabEl.className = `tab-item ${tab.path === activeTabPath ? 'active' : ''} ${tab.dirty ? 'dirty' : ''}`;
        tabEl.dataset.path = tab.path;

        const icon = getFileIcon(tab.filename, tab.language);
        tabEl.innerHTML = `
            <span>${icon}</span>
            <span>${tab.filename}</span>
            <span class="tab-dirty-dot"></span>
            <span class="tab-close-btn" title="Close">✕</span>
        `;

        tabEl.addEventListener('click', (e) => {
            if (e.target.classList.contains('tab-close-btn')) {
                e.stopPropagation();
                closeTab(tab.path);
            } else {
                activateTab(tab.path);
            }
        });

        tabsBar.appendChild(tabEl);
    });

    const welcome = document.getElementById('welcome-screen');
    const monacoDiv = document.getElementById('monaco-target');
    if (openTabs.length === 0) {
        welcome.style.display = 'flex';
        monacoDiv.style.display = 'none';
        document.getElementById('breadcrumb-active').textContent = 'Welcome';
        document.getElementById('ai-active-file-label').textContent = 'No file selected';
    } else {
        welcome.style.display = 'none';
        monacoDiv.style.display = 'block';
    }
}

function activateTab(filePath) {
    const tab = openTabs.find(t => t.path === filePath);
    if (!tab || !monacoEditor) return;

    activeTabPath = filePath;
    renderTabs();

    const currentModel = monaco.editor.createModel(tab.content, tab.language);
    monacoEditor.setModel(currentModel);

    document.getElementById('breadcrumb-active').textContent = tab.path;
    document.getElementById('sb-lang-name').textContent = tab.language.toUpperCase();
    document.getElementById('ai-active-file-label').textContent = tab.filename;
    updateSaveStatus(tab.dirty ? 'Unsaved Changes' : 'Saved');
}

function closeTab(filePath) {
    const idx = openTabs.findIndex(t => t.path === filePath);
    if (idx === -1) return;

    openTabs.splice(idx, 1);
    if (activeTabPath === filePath) {
        if (openTabs.length > 0) {
            const nextTab = openTabs[Math.max(0, idx - 1)];
            activateTab(nextTab.path);
        } else {
            activeTabPath = null;
            renderTabs();
        }
    } else {
        renderTabs();
    }
}

function getActiveTab() {
    return openTabs.find(t => t.path === activeTabPath);
}

function setTabDirty(filePath, dirty) {
    const tab = openTabs.find(t => t.path === filePath);
    if (tab) {
        tab.dirty = dirty;
        tab.content = monacoEditor.getValue();
        renderTabs();
        updateSaveStatus(dirty ? '● Unsaved Changes' : 'Saved');
    }
}

function updateSaveStatus(text) {
    const el = document.getElementById('save-status');
    el.textContent = text;
    el.style.color = text.includes('Unsaved') ? 'var(--neon-amber)' : 'var(--neon-green)';
}

async function saveActiveFile() {
    const tab = getActiveTab();
    if (!tab || !monacoEditor) return;

    const newContent = monacoEditor.getValue();
    try {
        const res = await fetch('/api/fs/write', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: tab.path, content: newContent })
        });
        if (!res.ok) throw new Error(await res.text());
        tab.savedContent = newContent;
        setTabDirty(tab.path, false);
        updateSaveStatus('Saved ' + new Date().toLocaleTimeString());
    } catch (e) {
        alert('Failed to save file: ' + e.message);
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// 4. Terminal & Code Execution
// ─────────────────────────────────────────────────────────────────────────────
async function runTerminalCommand(cmd) {
    if (!cmd.trim()) return;
    const feed = document.getElementById('terminal-feed');
    
    const cmdEl = document.createElement('div');
    cmdEl.className = 'terminal-line cmd';
    cmdEl.textContent = `PS> ${cmd}`;
    feed.appendChild(cmdEl);
    feed.scrollTop = feed.scrollHeight;

    try {
        const res = await fetch('/api/terminal/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command: cmd })
        });
        const data = await res.json();
        
        const outEl = document.createElement('div');
        outEl.className = `terminal-line ${data.exit_code === 0 ? 'out' : 'err'}`;
        outEl.textContent = data.output || '(Execution completed with no output)';
        feed.appendChild(outEl);
        feed.scrollTop = feed.scrollHeight;
    } catch (e) {
        const errEl = document.createElement('div');
        errEl.className = 'terminal-line err';
        errEl.textContent = 'Execution error: ' + e.message;
        feed.appendChild(errEl);
        feed.scrollTop = feed.scrollHeight;
    }
}

async function runActiveCode() {
    const tab = getActiveTab();
    if (!tab) {
        alert('Please open and select a code file to run.');
        return;
    }

    await saveActiveFile();

    document.querySelectorAll('.panel-tab-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('tab-output').classList.add('active');
    document.querySelectorAll('.panel-view').forEach(v => v.classList.remove('active'));
    document.getElementById('view-output').classList.add('active');

    const outBox = document.getElementById('run-output-log');
    outBox.textContent = `[DOOM RUNNER] Executing ${tab.filename}...\n`;

    let cmd = `python "${tab.path}"`;
    if (tab.language === 'javascript') {
        cmd = `node "${tab.path}"`;
    }

    try {
        const res = await fetch('/api/terminal/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command: cmd })
        });
        const data = await res.json();
        outBox.textContent += `--- Exit Code: ${data.exit_code} ---\n${data.output || '(No stdout)'}`;
    } catch (e) {
        outBox.textContent += `[ERROR] ${e.message}`;
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// 5. DOOM AI Copilot / Pair Programmer
// ─────────────────────────────────────────────────────────────────────────────
async function sendAIMessage(customPrompt = null) {
    const promptInput = document.getElementById('ai-prompt-input');
    const prompt = customPrompt || promptInput.value.trim();
    if (!prompt) return;

    if (!customPrompt) promptInput.value = '';

    const model = document.getElementById('ai-model-dropdown').value;
    const tab = getActiveTab();

    appendAIMessage('user', prompt);

    const loadingId = 'ai-loading-' + Date.now();
    appendAILoading(loadingId);

    try {
        const payload = {
            prompt: prompt,
            model: model,
            current_file_path: tab ? tab.path : null,
            current_file_content: tab && monacoEditor ? monacoEditor.getValue() : null,
            selected_code: selectedCodeSnippet || null
        };

        const res = await fetch('/api/ai/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const data = await res.json();
        removeAILoading(loadingId);
        appendAIAssistantResponse(data.response, data.code_blocks, data.latency_ms);
    } catch (e) {
        removeAILoading(loadingId);
        appendAIMessage('assistant', `⚠️ AI Error: ${e.message}`);
    }
}

function appendAIMessage(role, text) {
    const feed = document.getElementById('ai-chat-feed');
    const msgEl = document.createElement('div');
    msgEl.className = `ai-chat-msg msg-${role}`;
    msgEl.innerHTML = `
        <div class="msg-avatar">${role === 'user' ? 'S' : 'D'}</div>
        <div class="msg-bubble">
            <div class="msg-author">${role === 'user' ? 'SUJAL' : 'DOOM AI'}</div>
            <div class="msg-text">${escapeHtml(text)}</div>
        </div>
    `;
    feed.appendChild(msgEl);
    feed.scrollTop = feed.scrollHeight;
}

function appendAILoading(id) {
    const feed = document.getElementById('ai-chat-feed');
    const loadEl = document.createElement('div');
    loadEl.id = id;
    loadEl.className = 'ai-chat-msg msg-assistant';
    loadEl.innerHTML = `
        <div class="msg-avatar">D</div>
        <div class="msg-bubble">
            <div class="msg-author">DOOM AI</div>
            <div class="msg-text" style="color:var(--neon-purple);">Thinking & synthesizing code... ⏳</div>
        </div>
    `;
    feed.appendChild(loadEl);
    feed.scrollTop = feed.scrollHeight;
}

function removeAILoading(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}

function appendAIAssistantResponse(markdownText, codeBlocks, latencyMs) {
    const feed = document.getElementById('ai-chat-feed');
    const msgEl = document.createElement('div');
    msgEl.className = 'ai-chat-msg msg-assistant';

    let formattedHtml = escapeHtml(markdownText)
        .replace(/```(\w*)\n([\s\S]*?)```/g, (match, lang, code) => {
            const rawCode = unescapeHtml(code);
            return `
                <div class="ai-code-block">
                    <div class="ai-code-header">
                        <span>${lang || 'code'}</span>
                        <div class="ai-code-actions">
                            <button class="btn-code-action btn-apply" onclick="applyCodeToEditor(this)">Insert into Editor ⚡</button>
                            <button class="btn-code-action" onclick="copyCodeSnippet(this)">Copy 📋</button>
                        </div>
                    </div>
                    <pre class="ai-code-content">${escapeHtml(rawCode)}</pre>
                </div>
            `;
        })
        .replace(/\n/g, '<br>');

    msgEl.innerHTML = `
        <div class="msg-avatar">D</div>
        <div class="msg-bubble">
            <div class="msg-author">DOOM AI <span style="color:var(--text-dim); font-size:0.6rem;">(${latencyMs}ms)</span></div>
            <div class="msg-text">${formattedHtml}</div>
        </div>
    `;

    feed.appendChild(msgEl);
    feed.scrollTop = feed.scrollHeight;
}

window.applyCodeToEditor = function(btn) {
    const codeBlock = btn.closest('.ai-code-block').querySelector('.ai-code-content').textContent;
    if (!monacoEditor) {
        alert('Open a file first to insert code.');
        return;
    }

    const selection = monacoEditor.getSelection();
    if (selection && !selection.isEmpty()) {
        monacoEditor.executeEdits('ai-copilot', [{
            range: selection,
            text: codeBlock,
            forceMoveMarkers: true
        }]);
    } else {
        const position = monacoEditor.getPosition() || { lineNumber: 1, column: 1 };
        monacoEditor.executeEdits('ai-copilot', [{
            range: new monaco.Range(position.lineNumber, position.column, position.lineNumber, position.column),
            text: codeBlock,
            forceMoveMarkers: true
        }]);
    }
    btn.textContent = 'Applied ✓';
    btn.style.background = 'var(--neon-green)';
    setTimeout(() => { btn.textContent = 'Insert into Editor ⚡'; btn.style.background = ''; }, 2000);
};

window.copyCodeSnippet = function(btn) {
    const codeBlock = btn.closest('.ai-code-block').querySelector('.ai-code-content').textContent;
    navigator.clipboard.writeText(codeBlock);
    btn.textContent = 'Copied! ✓';
    setTimeout(() => { btn.textContent = 'Copy 📋'; }, 2000);
};

function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function unescapeHtml(str) {
    if (!str) return '';
    return str.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');
}

// ─────────────────────────────────────────────────────────────────────────────
// 6. DOM Setup & Event Listeners
// ─────────────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    initMonaco();
    loadWorkspaceInfo();

    const termForm = document.getElementById('terminal-form');
    termForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const input = document.getElementById('terminal-input');
        const cmd = input.value;
        input.value = '';
        runTerminalCommand(cmd);
    });

    document.getElementById('btn-clear-term').addEventListener('click', () => {
        document.getElementById('terminal-feed').innerHTML = '<div class="terminal-line banner">Terminal cleared.</div>';
    });

    document.getElementById('btn-run-file').addEventListener('click', runActiveCode);

    document.getElementById('btn-ai-send').addEventListener('click', () => sendAIMessage());
    document.getElementById('ai-prompt-input').addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
            e.preventDefault();
            sendAIMessage();
        }
    });

    document.querySelectorAll('.ai-chip').forEach(chip => {
        chip.addEventListener('click', () => {
            sendAIMessage(chip.dataset.prompt);
        });
    });

    const aiToggleBtn = document.getElementById('btn-toggle-ai-panel');
    const aiPanel = document.getElementById('ide-ai-panel');
    aiToggleBtn.addEventListener('click', () => {
        const isCollapsed = aiPanel.classList.toggle('collapsed');
        aiToggleBtn.classList.toggle('active', !isCollapsed);
    });
    document.getElementById('btn-ai-close').addEventListener('click', () => {
        aiPanel.classList.add('collapsed');
        aiToggleBtn.classList.remove('active');
    });

    document.querySelectorAll('.panel-tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.panel-tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.panel-view').forEach(v => v.classList.remove('active'));
            btn.classList.add('active');
            const targetView = btn.dataset.panel === 'terminal' ? 'view-terminal' : 'view-output';
            document.getElementById(targetView).classList.add('active');
        });
    });

    const folderModal = document.getElementById('folder-modal');
    const openFolderTrigger = () => { folderModal.style.display = 'flex'; };
    document.getElementById('workspace-pill').addEventListener('click', openFolderTrigger);
    document.getElementById('btn-open-folder').addEventListener('click', openFolderTrigger);
    document.getElementById('btn-welcome-open').addEventListener('click', openFolderTrigger);
    document.getElementById('btn-close-modal').addEventListener('click', () => { folderModal.style.display = 'none'; });
    document.getElementById('btn-modal-cancel').addEventListener('click', () => { folderModal.style.display = 'none'; });

    document.querySelectorAll('.qp-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.getElementById('folder-path-input').value = btn.dataset.path;
        });
    });

    document.getElementById('btn-modal-confirm').addEventListener('click', async () => {
        const path = document.getElementById('folder-path-input').value.trim();
        if (!path) return;
        try {
            const res = await fetch('/api/workspace/open', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: path })
            });
            if (!res.ok) throw new Error(await res.text());
            folderModal.style.display = 'none';
            openTabs = [];
            activeTabPath = null;
            renderTabs();
            await loadWorkspaceInfo();
        } catch (e) {
            alert('Could not open directory: ' + e.message);
        }
    });

    const createModal = document.getElementById('create-modal');
    let isCreatingDir = false;

    document.getElementById('btn-new-file').addEventListener('click', () => {
        isCreatingDir = false;
        document.getElementById('create-modal-title').textContent = '📄 Create New File';
        document.getElementById('create-name-input').value = '';
        createModal.style.display = 'flex';
    });

    document.getElementById('btn-welcome-new').addEventListener('click', () => {
        isCreatingDir = false;
        document.getElementById('create-modal-title').textContent = '📄 Create New File';
        document.getElementById('create-name-input').value = '';
        createModal.style.display = 'flex';
    });

    document.getElementById('btn-new-folder').addEventListener('click', () => {
        isCreatingDir = true;
        document.getElementById('create-modal-title').textContent = '📁 Create New Folder';
        document.getElementById('create-name-input').value = '';
        createModal.style.display = 'flex';
    });

    document.getElementById('btn-close-create-modal').addEventListener('click', () => { createModal.style.display = 'none'; });
    document.getElementById('btn-create-cancel').addEventListener('click', () => { createModal.style.display = 'none'; });

    document.getElementById('btn-create-confirm').addEventListener('click', async () => {
        const name = document.getElementById('create-name-input').value.trim();
        if (!name) return;
        try {
            await fetch('/api/fs/create', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: name, is_directory: isCreatingDir })
            });
            createModal.style.display = 'none';
            await refreshFileTree();
            if (!isCreatingDir) {
                openFile(name);
            }
        } catch (e) {
            alert('Failed to create: ' + e.message);
        }
    });

    const fileInput = document.getElementById('hidden-file-input');
    document.getElementById('btn-upload-files').addEventListener('click', () => {
        fileInput.click();
    });
    fileInput.addEventListener('change', async (e) => {
        if (fileInput.files.length === 0) return;
        const formData = new FormData();
        formData.append('target_dir', '');
        for (let i = 0; i < fileInput.files.length; i++) {
            formData.append('files', fileInput.files[i]);
        }
        try {
            await fetch('/api/fs/upload', { method: 'POST', body: formData });
            await refreshFileTree();
            alert('Uploaded ' + fileInput.files.length + ' file(s) successfully!');
        } catch (err) {
            alert('Upload failed: ' + err.message);
        }
    });

    document.getElementById('btn-refresh-tree').addEventListener('click', refreshFileTree);

    document.getElementById('ai-model-dropdown').addEventListener('change', (e) => {
        const modelName = e.target.options[e.target.selectedIndex].text.split('(')[0];
        document.getElementById('sb-model-name').textContent = modelName;
    });
});

