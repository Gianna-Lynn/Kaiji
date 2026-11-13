"use strict";

const SESSION_STORAGE_KEY = "kaiji.session_id";
const SESSION_ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;
const chatLog = document.querySelector("#chat-log");
const chatForm = document.querySelector("#chat-form");
const messageInput = document.querySelector("#message-input");
const sendButton = document.querySelector("#send-button");
const resetButton = document.querySelector("#reset-button");
const statusLine = document.querySelector("#status-line");

function getSessionId() {
  let sessionId = localStorage.getItem(SESSION_STORAGE_KEY);
  if (!sessionId || !SESSION_ID_PATTERN.test(sessionId)) {
    sessionId = crypto.randomUUID().replaceAll("-", "");
    localStorage.setItem(SESSION_STORAGE_KEY, sessionId);
  }
  return sessionId;
}

const sessionId = getSessionId();

function setStatus(message, kind = "") {
  statusLine.textContent = message;
  statusLine.className = `status-line ${kind}`.trim();
}

function addMessage(role, content) {
  const row = document.createElement("article");
  row.className = `message-row ${role}`;
  const label = document.createElement("span");
  label.className = "message-label";
  label.textContent = role === "user" ? "你" : "Kaiji";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = content;
  row.append(label, bubble);
  chatLog.append(row);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function showEmptyState() {
  const empty = document.createElement("p");
  empty.className = "empty-state";
  empty.textContent = "从一个问题开始吧。成功完成的对话会在本次运行期间保留。";
  chatLog.append(empty);
}

async function loadHistory() {
  chatLog.replaceChildren();
  const response = await fetch(`/api/history?session_id=${encodeURIComponent(sessionId)}`);
  if (!response.ok) throw new Error("history_unavailable");
  const data = await response.json();
  for (const message of data.messages) addMessage(message.role, message.content);
  if (data.messages.length === 0) showEmptyState();
}

function formatSummary(data) {
  const tokens = data.total_tokens === null ? "用量未知" : `${data.total_tokens} tokens`;
  const cost = data.estimated_peak_cost_cny === null
    ? "费用未知"
    : `峰值估算 ¥${data.estimated_peak_cost_cny}`;
  const tools = data.tool_names.length ? ` · 工具 ${data.tool_names.join("、")}` : "";
  const result = `${data.stop_reason} · ${Math.round(data.latency_ms)} ms · ${tokens} · ${cost}${tools}`;
  if (data.logging_warning === "logging_failed") {
    const notice = data.answer
      ? "回答已生成，但审计日志未保存。"
      : `本轮未完成（${data.failure_code || data.stop_reason}），审计日志也未保存。`;
    setStatus(`${result}。${notice}`, "warning");
  } else if (data.stop_reason !== "completed") {
    setStatus(`${result}。本轮未完成（${data.failure_code || data.stop_reason}）。`, "error");
  } else {
    setStatus(result);
  }
}

function isApiErrorPayload(data) {
  return data !== null
    && typeof data === "object"
    && !Array.isArray(data)
    && typeof data.error === "string";
}

function isChatResponsePayload(data) {
  return data !== null
    && typeof data === "object"
    && typeof data.stop_reason === "string"
    && (data.answer === null || typeof data.answer === "string")
    && typeof data.latency_ms === "number"
    && typeof data.model === "string"
    && (data.input_tokens === null || Number.isInteger(data.input_tokens))
    && (data.output_tokens === null || Number.isInteger(data.output_tokens))
    && (data.total_tokens === null || Number.isInteger(data.total_tokens))
    && (data.estimated_peak_cost_cny === null || typeof data.estimated_peak_cost_cny === "string")
    && Array.isArray(data.tool_names)
    && data.tool_names.every((name) => typeof name === "string")
    && (typeof data.tool_success === "boolean" || data.tool_success === null)
    && typeof data.log_persisted === "boolean"
    && (data.logging_warning === null || typeof data.logging_warning === "string")
    && (data.failure_code === null || typeof data.failure_code === "string");
}

function showApiError(data, statusCode) {
  if (data.error === "invalid_request" || statusCode === 422) {
    setStatus("请求失败。请检查消息格式后重试。", "error");
  } else {
    setStatus("服务异常，请稍后重试。", "error");
  }
}

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = messageInput.value;
  if (!message.trim() || sendButton.disabled) return;
  sendButton.disabled = true;
  resetButton.disabled = true;
  messageInput.disabled = true;
  setStatus("Kaiji 正在思考…");
  try {
    let response;
    let data;
    try {
      response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, message }),
      });
      data = await response.json();
    } catch {
      setStatus("连接失败。请确认本地服务仍在运行后重试。", "error");
      return;
    }
    if (isApiErrorPayload(data)) {
      showApiError(data, response.status);
      return;
    }
    if (!isChatResponsePayload(data)) {
      setStatus("服务异常：响应格式无效，请稍后重试。", "error");
      return;
    }
    if (data.answer) addMessage("user", message);
    if (data.answer) addMessage("assistant", data.answer);
    formatSummary(data);
    if (!response.ok && data.answer === null) {
      try {
        await loadHistory();
      } catch {
        setStatus("本轮未完成，且无法刷新历史。请检查本地服务。", "error");
      }
    }
    if (response.ok) messageInput.value = "";
  } finally {
    sendButton.disabled = false;
    resetButton.disabled = false;
    messageInput.disabled = false;
    messageInput.focus();
  }
});

messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !sendButton.disabled) {
    event.preventDefault();
    chatForm.requestSubmit();
  }
});

resetButton.addEventListener("click", async () => {
  resetButton.disabled = true;
  sendButton.disabled = true;
  try {
    const response = await fetch("/api/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    });
    if (!response.ok) throw new Error("reset_failed");
    chatLog.replaceChildren();
    showEmptyState();
    setStatus("当前会话已清空。");
  } catch {
    setStatus("清空失败。请稍后重试。", "error");
  } finally {
    resetButton.disabled = false;
    sendButton.disabled = false;
  }
});

loadHistory().then(() => {
  setStatus("会话已就绪。");
}).catch(() => {
  setStatus("无法读取当前会话，请检查本地服务。", "error");
});
