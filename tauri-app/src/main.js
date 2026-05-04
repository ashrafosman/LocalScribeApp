const tauriGlobal = window.__TAURI__;
const invoke = tauriGlobal?.invoke ?? tauriGlobal?.tauri?.invoke;
const listen = tauriGlobal?.event?.listen ?? tauriGlobal?.event?.listen;
const tauriAvailable = typeof invoke === "function";
const safeInvoke = tauriAvailable
  ? invoke
  : async () => {
      throw new Error("Tauri API not available. Run inside the Tauri app.");
    };
const safeListen = tauriAvailable ? listen : async () => {};
const dialogOpen = tauriGlobal?.dialog?.open;

const state = {
  recording: false,
  autoScroll: true,
  transcriptLines: [],
  recordingTimer: null,
  startTime: null,
  recordings: [],
  promptOptions: [],
  currentSummary: { keypoints: [], actions: [], issues: [] },
  currentMarkdown: {},
  settings: {},
  startupConsentShown: false,
};

const elements = {
  settingsPanel: document.getElementById("settings-panel"),
  toggleSettings: document.getElementById("toggle-settings"),
  closeSettings: document.getElementById("close-settings"),
  saveSettings: document.getElementById("save-settings"),
  downloadModel: document.getElementById("download-model"),
  statusPill: document.getElementById("status-pill"),
  meetingNameInput: document.getElementById("meeting-name"),
  meetingNameDisplay: document.getElementById("meeting-name-display"),
  elapsed: document.getElementById("elapsed"),
  startRecording: document.getElementById("start-recording"),
  stopRecording: document.getElementById("stop-recording"),
  refreshDevices: document.getElementById("refresh-devices"),
  deviceSelect: document.getElementById("device-select"),
  promptSelect: document.getElementById("prompt-select"),
  transcriptStream: document.getElementById("transcript-stream"),
  transcriptSource: document.getElementById("transcript-source"),
  toggleScroll: document.getElementById("toggle-scroll"),
  copyTranscript: document.getElementById("copy-transcript"),
  sessionHint: document.getElementById("session-hint"),
  summaryReady: document.getElementById("summary-ready"),
  keypointsList: document.getElementById("keypoints-list"),
  actionsList: document.getElementById("actions-list"),
  issuesList: document.getElementById("issues-list"),
  tabButtons: document.querySelectorAll(".tab"),
  summaryCards: document.querySelector(".summary-cards"),
  askPanel: document.querySelector(".ask-panel"),
  askInput: document.getElementById("ask-input"),
  askResponse: document.getElementById("ask-response"),
  askSubmit: document.getElementById("ask-submit"),
  askLast: document.getElementById("ask-last"),
  libraryList: document.getElementById("library-list"),
  summaryModal: document.getElementById("summary-modal"),
  summaryModalBody: document.getElementById("summary-modal-body"),
  summaryModalClose: document.getElementById("summary-close"),
  summaryModalCopy: document.getElementById("summary-copy"),
  summaryStatus: document.getElementById("summary-status"),
  outputPathInput: document.getElementById("output-path"),
  aboutModal: document.getElementById("about-modal"),
  aboutModalClose: document.getElementById("about-close"),
  aboutVersion: document.getElementById("about-version"),
  openAbout: document.getElementById("open-about"),
  modelDownload: document.getElementById("model-download"),
  modelDownloadFill: document.getElementById("model-download-fill"),
  modelDownloadMeta: document.getElementById("model-download-meta"),
  modelDownloadPercent: document.getElementById("model-download-percent"),
  consentModal: document.getElementById("consent-modal"),
  consentAccept: document.getElementById("consent-accept"),
  consentCancel: document.getElementById("consent-cancel"),
  consentCancelBottom: document.getElementById("consent-cancel-bottom"),
  consentCheckbox: document.getElementById("consent-checkbox"),
  retentionModal: document.getElementById("retention-modal"),
  retentionMessage: document.getElementById("retention-message"),
  retentionApprove: document.getElementById("retention-approve"),
};

const settingsMap = {
  calls_output_path: "output-path",
  summary_api_url: "summary-api-url",
  summary_api_model: "summary-model",
  summary_api_token: "summary-token",
};

const fallbackPrompts = [
  { id: "meeting", label: "Executive Meeting" },
  { id: "technical", label: "Technical Review" },
  { id: "sales", label: "Sales Call" },
  { id: "standup", label: "Daily Standup" },
  { id: "one_on_one", label: "1:1 Meeting" },
  { id: "staff", label: "Staff Meeting" },
];

const formatTime = (value) => {
  const total = Math.max(0, Math.floor(value));
  const hours = String(Math.floor(total / 3600)).padStart(2, "0");
  const minutes = String(Math.floor((total % 3600) / 60)).padStart(2, "0");
  const seconds = String(total % 60).padStart(2, "0");
  return `${hours}:${minutes}:${seconds}`;
};

const formatBytes = (value) => {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  return `${(value / 1024 / 1024 / 1024).toFixed(1)} GB`;
};

const showRetentionModal = (summary) => {
  if (!elements.retentionModal || !elements.retentionMessage) return;
  if (summary.count <= 0) return;
  const size = formatBytes(summary.total_bytes);
  elements.retentionMessage.textContent = `MyScribe will delete ${summary.count} file(s) older than 7 days (${size}). This action cannot be undone.`;
  elements.retentionModal.classList.add("open");
};

const closeRetentionModal = () => {
  elements.retentionModal?.classList.remove("open");
};

const setStatus = (status, label) => {
  elements.statusPill.dataset.status = status;
  elements.statusPill.textContent = label;
};

const setSummaryStatus = (label) => {
  if (elements.summaryStatus) {
    elements.summaryStatus.textContent = label;
  }
};

const setTranscriptSource = (label, ready = false) => {
  elements.transcriptSource.textContent = `Source: ${label}`;
  elements.transcriptSource.dataset.ready = ready ? "true" : "false";
};

const appendTranscriptLine = (text) => {
  if (!text) return;
  const line = document.createElement("div");
  line.className = "transcript-line";
  line.textContent = text;
  elements.transcriptStream.appendChild(line);
  if (state.autoScroll) {
    elements.transcriptStream.scrollTop = elements.transcriptStream.scrollHeight;
  }
};

const setModelDownload = ({ bytes = 0, total = null, percent = null, done = false }) => {
  if (!elements.modelDownload) return;
  if (done) {
    elements.modelDownload.dataset.state = "active";
    elements.modelDownloadFill.style.width = "100%";
    elements.modelDownloadPercent.textContent = "100%";
    elements.modelDownloadMeta.textContent = "Model ready.";
    if (elements.downloadModel) {
      elements.downloadModel.disabled = false;
    }
    window.setTimeout(() => {
      elements.modelDownload.dataset.state = "hidden";
    }, 1500);
    return;
  }

  const hasPercent = typeof percent === "number";
  elements.modelDownload.dataset.state = hasPercent ? "active" : "indeterminate";
  elements.modelDownloadPercent.textContent = hasPercent ? `${percent}%` : "…";
  elements.modelDownloadFill.style.width = hasPercent ? `${percent}%` : "30%";
  if (elements.downloadModel) {
    elements.downloadModel.disabled = true;
  }

  const parts = [];
  if (total) {
    parts.push(`${formatBytes(bytes)} of ${formatBytes(total)}`);
  } else if (bytes) {
    parts.push(`${formatBytes(bytes)} downloaded`);
  }
  if (hasPercent) {
    parts.push(`${percent}%`);
  }
  elements.modelDownloadMeta.textContent = parts.length ? parts.join(" • ") : "Downloading model...";
};

const renderSummary = () => {
  const fillList = (list, items) => {
    list.innerHTML = "";
    if (!items.length) {
      const li = document.createElement("li");
      li.textContent = "No summary yet.";
      list.appendChild(li);
      return;
    }
    items.forEach((item) => {
      const li = document.createElement("li");
      li.innerHTML = renderInlineMarkdown(item);
      list.appendChild(li);
    });
  };

  const keypointsMarkdown = state.currentMarkdown.keypoints;
  if (keypointsMarkdown) {
    elements.keypointsList.innerHTML = renderMarkdownBlock(keypointsMarkdown);
  } else {
    elements.keypointsList.innerHTML = renderMarkdownBlock(state.currentSummary.keypoints.join("\n"));
  }
  fillList(elements.actionsList, state.currentSummary.actions);
  const issuesMarkdown = state.currentMarkdown.issues;
  if (issuesMarkdown) {
    elements.issuesList.innerHTML = renderMarkdownBlock(issuesMarkdown);
  } else {
    elements.issuesList.innerHTML = renderMarkdownBlock(state.currentSummary.issues.join("\n"));
  }
};

const renderLibrary = () => {
  elements.libraryList.innerHTML = "";
  if (!state.recordings.length) {
    const empty = document.createElement("div");
    empty.className = "muted";
    empty.textContent = "No recordings yet. Start your first session.";
    elements.libraryList.appendChild(empty);
    return;
  }

  state.recordings.forEach((record) => {
    const card = document.createElement("div");
    card.className = "library-card";

    const title = document.createElement("h4");
    title.textContent = record.name;
    const meta = document.createElement("div");
    meta.className = "meta";
    const metaDetail = record.duration || record.size || "Unknown length";
    meta.textContent = `${record.date} • ${metaDetail}`;

    const buttons = document.createElement("div");
    buttons.className = "button-row";
    const actions = document.createElement("div");
    actions.className = "library-actions";
    const openTranscript = document.createElement("button");
    openTranscript.className = "btn ghost small";
    openTranscript.textContent = "Open Transcript";
    openTranscript.addEventListener("click", () => safeInvoke("open_path", { path: record.transcript_path }));

    const viewSummary = document.createElement("button");
    viewSummary.className = "btn ghost small";
    viewSummary.textContent = "View Summary";
    viewSummary.addEventListener("click", async () => {
      if (!record.summary_path) return;
      try {
        const markdown = await safeInvoke("read_summary_file", { path: record.summary_path });
        elements.summaryModalBody.innerHTML = renderMarkdownBlock(markdown || "No summary found.");
        elements.summaryModal.classList.add("open");
      } catch (error) {
        elements.sessionHint.textContent = `Failed to load summary: ${error}`;
      }
    });
    if (!record.summary_path) {
      viewSummary.disabled = true;
    }

    const promptSelect = document.createElement("select");
    promptSelect.className = "library-select";
    const options = state.promptOptions.length ? state.promptOptions : fallbackPrompts;
    options.forEach((prompt) => {
      const opt = document.createElement("option");
      opt.value = prompt.id;
      opt.textContent = prompt.label;
      promptSelect.appendChild(opt);
    });

    const redoSummary = document.createElement("button");
    redoSummary.className = "btn primary small";
    redoSummary.textContent = "Redo Summary";
    redoSummary.addEventListener("click", async () => {
      redoSummary.disabled = true;
      redoSummary.textContent = "Updating...";
      try {
        const summaryPath = await safeInvoke("re_summarize_recording", {
          transcriptPath: record.transcript_path,
          promptType: promptSelect.value,
        });
        record.summary_path = summaryPath;
        viewSummary.disabled = false;
        elements.sessionHint.textContent = "Summary updated.";
      } catch (error) {
        elements.sessionHint.textContent = `Failed to update summary: ${error}`;
      } finally {
        redoSummary.disabled = false;
        redoSummary.textContent = "Redo Summary";
      }
    });

    buttons.appendChild(openTranscript);
    buttons.appendChild(viewSummary);
    actions.appendChild(promptSelect);
    actions.appendChild(redoSummary);

    card.appendChild(title);
    card.appendChild(meta);
    card.appendChild(buttons);
    card.appendChild(actions);
    elements.libraryList.appendChild(card);
  });
};

const loadRecordings = async () => {
  try {
  const recordings = await safeInvoke("list_recordings");
    state.recordings = recordings ?? [];
  } catch (error) {
    state.recordings = [];
    elements.sessionHint.textContent = `Failed to load recordings: ${error}`;
  }
  renderLibrary();
};

const showTab = (tab) => {
  if (tab === "ask") {
    elements.askPanel.classList.add("active");
    elements.summaryCards.style.display = "none";
    return;
  } else {
    elements.askPanel.classList.remove("active");
    elements.summaryCards.style.display = "flex";
  }

  renderSummary();
  const cards = elements.summaryCards.querySelectorAll(".card");
  cards.forEach((card) => {
    const cardKey = card.dataset.card;
    card.style.display = cardKey === tab ? "block" : "none";
  });
};

const escapeHtml = (value) =>
  value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

const renderInlineMarkdown = (value) => {
  let safe = escapeHtml(value);
  safe = safe.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  safe = safe.replace(/\*(.+?)\*/g, "<em>$1</em>");
  safe = safe.replace(/`(.+?)`/g, "<code>$1</code>");
  return safe;
};

const renderMarkdownBlock = (markdown) => {
  if (!markdown || !markdown.trim()) {
    return "<p>No summary yet.</p>";
  }
  const lines = markdown.split("\n");
  let html = "";
  let listOpen = false;
  const closeList = () => {
    if (listOpen) {
      html += "</ul>";
      listOpen = false;
    }
  };

  lines.forEach((line) => {
    const trimmed = line.trim();
    if (!trimmed) {
      closeList();
      return;
    }
    const headingMatch = trimmed.match(/^(#{1,3})\s+(.*)$/);
    if (headingMatch) {
      closeList();
      const level = headingMatch[1].length;
      const tag = level === 1 ? "h4" : level === 2 ? "h5" : "h6";
      html += `<${tag}>${renderInlineMarkdown(headingMatch[2])}</${tag}>`;
      return;
    }
    const listMatch = trimmed.match(/^(?:[-*•]|\d+\.)\s+(.*)$/);
    if (listMatch) {
      if (!listOpen) {
        html += "<ul>";
        listOpen = true;
      }
      html += `<li>${renderInlineMarkdown(listMatch[1])}</li>`;
      return;
    }
    closeList();
    html += `<p>${renderInlineMarkdown(trimmed)}</p>`;
  });

  closeList();
  return html;
};

const parseMarkdownList = (markdown) => {
  const lines = markdown.split("\n");
  const items = [];
  lines.forEach((line) => {
    const match = line.match(/^\s*(?:[-*•]|\d+\.)\s+(.*)$/);
    if (match && match[1]) {
      items.push(match[1].trim());
      return;
    }
    if (line.trim().length) {
      items.push(line.trim());
    }
  });
  return items;
};

const generateSummarySection = async (section) => {
  if (!state.transcriptLines.length) {
    elements.summaryReady.textContent = "No transcript available yet.";
    return;
  }
  elements.summaryReady.textContent = "Generating summary...";
  try {
    const markdown = await safeInvoke("summarize_section", {
      transcriptText: state.transcriptLines.join("\n"),
      section,
    });
    const items = parseMarkdownList(markdown || "");
    state.currentSummary = {
      ...state.currentSummary,
      [section]: items.length ? items : [markdown || "No summary generated."],
    };
    state.currentMarkdown = {
      ...state.currentMarkdown,
      [section]: markdown || "",
    };
    renderSummary();
    elements.summaryReady.textContent = "Summary ready.";
  } catch (error) {
    elements.summaryReady.textContent = `Summary failed: ${error}`;
  }
};

const generateAskSuggestions = async () => {
  if (!state.transcriptLines.length) {
    elements.askResponse.textContent = "No transcript available yet.";
    return;
  }
  elements.askResponse.textContent = "Thinking...";
  try {
    const response = await safeInvoke("suggest_questions", {
      transcriptText: state.transcriptLines.join("\n"),
    });
    elements.askResponse.innerHTML = renderMarkdownBlock(response || "No suggestions yet.");
  } catch (error) {
    elements.askResponse.textContent = String(error);
  }
};

const startRecording = async () => {
  const meetingName = elements.meetingNameInput.value.trim();
  if (!meetingName) {
    elements.sessionHint.textContent = "Meeting name is required to begin.";
    setStatus("error", "Missing name");
    return;
  }
  try {
    state.settings = await safeInvoke("get_settings");
    await invoke("start_recording", {
      meetingName,
      deviceId: parseInt(elements.deviceSelect.value, 10),
      deviceName: elements.deviceSelect.options[elements.deviceSelect.selectedIndex]?.text ?? "",
      promptType: elements.promptSelect.value,
    });
    state.recording = true;
    state.startTime = Date.now();
    state.transcriptLines = [];
    elements.transcriptStream.innerHTML = "";
    elements.meetingNameDisplay.textContent = meetingName;
    elements.sessionHint.textContent = "Listening for audio. Live transcript flowing in.";
    setStatus("recording", "Recording");
    const mode = state.settings.whisper_mode === "api" ? "Remote" : "Local";
    setTranscriptSource(mode, true);
    elements.startRecording.disabled = true;
    elements.stopRecording.disabled = false;
    elements.summaryReady.textContent = "Listening for transcript...";
    state.currentSummary = { keypoints: [], actions: [], issues: [] };
    state.currentMarkdown = {};
    renderSummary();

    state.recordingTimer = setInterval(() => {
      const elapsed = (Date.now() - state.startTime) / 1000;
      elements.elapsed.textContent = formatTime(elapsed);
    }, 1000);
  } catch (error) {
    elements.sessionHint.textContent = error;
    setStatus("error", "Error");
  }
};

const openConsentModal = () => {
  if (state.startupConsentShown) return;
  if (state.recording) return;
  if (elements.retentionModal?.classList.contains("open")) return;
  if (!elements.consentModal) return;
  state.startupConsentShown = true;
  elements.consentCheckbox.checked = false;
  elements.consentAccept.disabled = true;
  elements.consentModal.classList.add("open");
};

const closeConsentModal = () => {
  elements.consentModal?.classList.remove("open");
};

const stopRecording = async () => {
  if (!state.recording) return;
  await safeInvoke("stop_recording");
  state.recording = false;
  elements.startRecording.disabled = false;
  elements.stopRecording.disabled = true;
  setStatus("processing", "Processing");
  elements.sessionHint.textContent = "Wrapping up audio and generating summaries.";
  clearInterval(state.recordingTimer);
};

const saveSettings = async () => {
  const settings = { ...state.settings };
  Object.entries(settingsMap).forEach(([key, id]) => {
    const input = document.getElementById(id);
    if (input) settings[key] = input.value;
  });
  try {
    await safeInvoke("save_settings", { settings });
    state.settings = settings;
    elements.sessionHint.textContent = "Settings saved.";
  } catch (error) {
    elements.sessionHint.textContent = `Failed to save settings: ${error}`;
  }
};

const loadSettings = async () => {
  try {
    const settings = await safeInvoke("get_settings");
    state.settings = settings;
    Object.entries(settingsMap).forEach(([key, id]) => {
      const input = document.getElementById(id);
      if (input && settings[key] !== undefined) {
        input.value = settings[key];
      }
    });
  } catch (error) {
    elements.sessionHint.textContent = `Failed to load settings: ${error}`;
  }
};

const populateSelect = (select, options) => {
  select.innerHTML = "";
  options.forEach((option) => {
    const opt = document.createElement("option");
    opt.value = option.id;
    opt.textContent = option.label;
    select.appendChild(opt);
  });
};

elements.toggleSettings.addEventListener("click", () => {
  elements.settingsPanel.classList.add("open");
  elements.settingsPanel.style.display = "block";
  elements.settingsPanel.scrollIntoView({ behavior: "smooth", block: "start" });
});

elements.closeSettings.addEventListener("click", () => {
  elements.settingsPanel.classList.remove("open");
  elements.settingsPanel.style.display = "none";
});

elements.openAbout.addEventListener("click", () => {
  elements.aboutModal.classList.add("open");
});

elements.aboutModalClose.addEventListener("click", () => {
  elements.aboutModal.classList.remove("open");
});

elements.aboutModal.addEventListener("click", (event) => {
  if (event.target === elements.aboutModal) {
    elements.aboutModal.classList.remove("open");
  }
});

elements.summaryModalClose.addEventListener("click", () => {
  elements.summaryModal.classList.remove("open");
});

elements.summaryModalCopy?.addEventListener("click", async () => {
  const text = elements.summaryModalBody?.textContent?.trim();
  if (!text) return;
  await navigator.clipboard?.writeText(text);
  elements.sessionHint.textContent = "Summary copied to clipboard.";
});

elements.summaryModal.addEventListener("click", (event) => {
  if (event.target === elements.summaryModal) {
    elements.summaryModal.classList.remove("open");
  }
});

elements.summaryStatus?.addEventListener("click", async () => {
  setSummaryStatus("LLM: checking...");
  try {
    await safeInvoke("check_summary_ready");
    setSummaryStatus("LLM: available");
    elements.summaryReady.textContent = "Summary model ready.";
  } catch (error) {
    setSummaryStatus("LLM: unavailable");
    elements.summaryReady.textContent = `Summary unavailable: ${error}`;
  }
});

elements.saveSettings.addEventListener("click", saveSettings);
elements.downloadModel?.addEventListener("click", async () => {
  elements.sessionHint.textContent = "Downloading Whisper model...";
  setModelDownload({ bytes: 0, total: null, percent: null, done: false });
  if (elements.downloadModel) {
    elements.downloadModel.disabled = true;
  }
  try {
    await safeInvoke("download_whisper_model");
  } catch (error) {
    elements.sessionHint.textContent = `Whisper download failed: ${error}`;
    if (elements.downloadModel) {
      elements.downloadModel.disabled = false;
    }
  }
});

elements.startRecording.addEventListener("click", startRecording);
elements.stopRecording.addEventListener("click", stopRecording);

elements.refreshDevices.addEventListener("click", async () => {
  await loadDevices();
  elements.sessionHint.textContent = "Device list refreshed.";
});

elements.toggleScroll.addEventListener("click", () => {
  state.autoScroll = !state.autoScroll;
  elements.toggleScroll.textContent = `Auto-scroll: ${state.autoScroll ? "On" : "Off"}`;
});

elements.copyTranscript.addEventListener("click", async () => {
  const text = state.transcriptLines.join("\n");
  if (!text) return;
  await navigator.clipboard?.writeText(text);
  elements.sessionHint.textContent = "Transcript copied to clipboard.";
});

elements.tabButtons.forEach((tab) => {
  tab.addEventListener("mouseenter", () => {
    elements.tabButtons.forEach((btn) => btn.classList.remove("active"));
    tab.classList.add("active");
    showTab(tab.dataset.tab);
  });

  tab.addEventListener("click", () => {
    tab.classList.add("clicked");
    window.setTimeout(() => tab.classList.remove("clicked"), 260);
    if (tab.dataset.tab !== "ask") {
      generateSummarySection(tab.dataset.tab);
    } else {
      generateAskSuggestions();
    }
  });
});

elements.askSubmit.addEventListener("click", async () => {
  await generateAskSuggestions();
});

elements.askLast.addEventListener("click", async () => {
  try {
    const response = await safeInvoke("ask_question", {
      transcriptText: state.transcriptLines.join("\n"),
      question: "What was the last topic discussed?",
    });
    elements.askResponse.innerHTML = renderMarkdownBlock(response || "No response yet.");
  } catch (error) {
    elements.askResponse.textContent = String(error);
  }
});

const submitAskQuestion = async () => {
  const question = elements.askInput.value.trim();
  if (!question) return;
  if (!state.transcriptLines.length) {
    elements.askResponse.textContent = "No transcript available yet.";
    return;
  }
  elements.askResponse.textContent = "Thinking...";
  try {
    const response = await safeInvoke("ask_question", {
      transcriptText: state.transcriptLines.join("\n"),
      question,
    });
    elements.askResponse.innerHTML = renderMarkdownBlock(response || "No response yet.");
  } catch (error) {
    elements.askResponse.textContent = String(error);
  }
};

elements.askInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    submitAskQuestion();
  }
});

elements.consentCheckbox?.addEventListener("change", () => {
  elements.consentAccept.disabled = !elements.consentCheckbox.checked;
});

elements.consentAccept?.addEventListener("click", async () => {
  closeConsentModal();
  elements.sessionHint.textContent = "Consent acknowledged.";
});

const cancelConsent = () => {
  closeConsentModal();
  elements.sessionHint.textContent = "Consent dialog closed.";
};

elements.consentCancel?.addEventListener("click", cancelConsent);
elements.consentCancelBottom?.addEventListener("click", cancelConsent);
elements.consentModal?.addEventListener("click", (event) => {
  if (event.target === elements.consentModal) {
    cancelConsent();
  }
});

const openOutputPathPicker = async () => {
  if (!dialogOpen || !elements.outputPathInput) return;
  try {
    const selection = await dialogOpen({
      directory: true,
      multiple: false,
      defaultPath: elements.outputPathInput.value || undefined,
    });
    if (typeof selection === "string") {
      elements.outputPathInput.value = selection;
    }
  } catch (error) {
    elements.sessionHint.textContent = `Failed to open folder picker: ${error}`;
  }
};

elements.outputPathInput?.addEventListener("click", openOutputPathPicker);

elements.retentionApprove?.addEventListener("click", async () => {
  elements.retentionApprove.disabled = true;
  try {
    const deleted = await safeInvoke("delete_expired_artifacts");
    elements.sessionHint.textContent = `Deleted ${deleted} old file(s).`;
  } catch (error) {
    elements.sessionHint.textContent = `Failed to delete old files: ${error}`;
  } finally {
    closeRetentionModal();
    elements.retentionApprove.disabled = false;
    await loadRecordings();
    openConsentModal();
  }
});

const loadDevices = async () => {
  try {
    const devices = await safeInvoke("list_audio_devices");
    const options = (devices ?? []).map((device) => ({ id: device.id, label: device.name }));
    if (!options.length) {
      options.push({ id: -1, label: "System Default" });
    }
    populateSelect(elements.deviceSelect, options);
  } catch (error) {
    populateSelect(elements.deviceSelect, [{ id: -1, label: "System Default" }]);
    elements.sessionHint.textContent = `Failed to load devices: ${error}`;
  }
};

const loadPrompts = async () => {
  try {
    const prompts = await safeInvoke("list_prompts");
    const options = (prompts ?? []).map((prompt) => ({ id: prompt.id, label: prompt.name }));
    if (!options.length) {
      options.push(...fallbackPrompts);
    }
    state.promptOptions = options;
    populateSelect(elements.promptSelect, options);
  } catch (error) {
    state.promptOptions = fallbackPrompts;
    populateSelect(elements.promptSelect, fallbackPrompts);
    elements.sessionHint.textContent = `Failed to load templates: ${error}`;
  }
};

const registerListeners = async () => {
  await safeListen("status", (event) => {
    const { status, message } = event.payload;
    setStatus(status, message || status);
    elements.sessionHint.textContent = message || status;
    if (status === "recording") {
      if (message && message.toLowerCase().includes("api")) {
        setTranscriptSource("Remote", true);
      } else {
        setTranscriptSource("Local", true);
      }
    }
    if (status === "complete") {
      state.recording = false;
      clearInterval(state.recordingTimer);
      elements.startRecording.disabled = false;
      elements.stopRecording.disabled = true;
      elements.meetingNameDisplay.textContent = "Not recording";
      elements.elapsed.textContent = "00:00:00";
      setTranscriptSource("Unknown", false);
      elements.summaryReady.textContent = "Summary ready. Explore the highlights.";
      loadRecordings();
    }
    if (status === "processing") {
      elements.summaryReady.textContent = "Processing and summarizing...";
    }
    if (status === "error") {
      state.recording = false;
      clearInterval(state.recordingTimer);
      elements.startRecording.disabled = false;
      elements.stopRecording.disabled = true;
      elements.summaryReady.textContent = message || "An error occurred.";
      if (message && message.toLowerCase().includes("whisper model") && elements.modelDownload) {
        elements.modelDownload.dataset.state = "active";
        elements.modelDownloadFill.style.width = "100%";
        elements.modelDownloadPercent.textContent = "!";
        elements.modelDownloadMeta.textContent = message;
      }
    }
  });

  await safeListen("transcription", (event) => {
    const { text } = event.payload;
    if (!text) return;
    state.transcriptLines.push(text);
    appendTranscriptLine(text);
  });

  await safeListen("summary", (event) => {
    state.currentSummary = event.payload;
    renderSummary();
  });

  await safeListen("model_download", (event) => {
    setModelDownload(event.payload ?? {});
  });
};

const init = async () => {
  if (!tauriAvailable) {
    elements.sessionHint.textContent = "Tauri API not available. Please run via npm run dev.";
    return;
  }
  const now = new Date();
  const version = `${now.getFullYear()}.${String(now.getMonth() + 1).padStart(2, "0")}.${String(
    now.getDate()
  ).padStart(2, "0")}`;
  if (elements.aboutVersion) {
    elements.aboutVersion.textContent = version;
  }
  populateSelect(elements.promptSelect, fallbackPrompts);
  await registerListeners();
  await loadSettings();
  await loadDevices();
  await loadPrompts();
  await loadRecordings();
  try {
    const summary = await safeInvoke("get_expired_artifacts_summary");
    showRetentionModal(summary);
  } catch (error) {
    elements.sessionHint.textContent = `Retention check failed: ${error}`;
  }
  try {
    await safeInvoke("ensure_whisper_ready");
  } catch (error) {
    elements.sessionHint.textContent = `Whisper init failed: ${error}`;
  }
  renderSummary();
  showTab("keypoints");
  try {
    await safeInvoke("check_summary_ready");
    elements.summaryReady.textContent = "Summary model ready.";
    setSummaryStatus("LLM: available");
  } catch (error) {
    elements.summaryReady.textContent = `Summary unavailable: ${error}`;
    setSummaryStatus("LLM: unavailable");
  }
  openConsentModal();
};

init();
