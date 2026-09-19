"use strict";

const statusTitle = document.querySelector("#statusTitle");
const statusMessage = document.querySelector("#statusMessage");
const pageLabel = document.querySelector("#pageLabel");
const progress = document.querySelector("#progress");
const statusDot = document.querySelector("#statusDot");
const stopButton = document.querySelector("#stopButton");
const continueButton = document.querySelector("#continueButton");

document.querySelector("#version").textContent = `v${chrome.runtime.getManifest().version}`;

function render(state) {
  const active = Boolean(state && state.active);
  const blocked = state && state.state === "blocked";
  const paused = state && state.state === "paused";
  statusTitle.textContent = active
    ? paused ? "任务已暂停" : blocked ? "等待人工处理" : "任务进行中"
    : "等待 DocFlow";
  statusMessage.textContent = state && state.message
    ? state.message
    : "在 DocFlow 的填写演示页中启动任务后，这里会显示实时状态。";
  pageLabel.textContent = state && state.pageLabel ? state.pageLabel : "未连接";
  progress.textContent = `${state && state.completedFields ? state.completedFields : 0} / ${state && state.totalFields ? state.totalFields : 0}`;
  statusDot.className = `status-dot${active ? " active" : ""}${blocked ? " blocked" : ""}`;
  stopButton.disabled = !active || paused;
  continueButton.disabled = !active || (state && state.state === "running");
  continueButton.textContent = paused ? "继续填写" : "我已进入表格，开始连续填写";
}

chrome.runtime.sendMessage({ type: "docflow.getState" }).then(render).catch(() => render(null));

continueButton.addEventListener("click", async () => {
  continueButton.disabled = true;
  try {
    const response = await chrome.runtime.sendMessage({ type: "docflow.resumeTask" });
    if (!response || response.ok === false) {
      throw new Error(response?.error || "无法识别当前页面");
    }
    render(await chrome.runtime.sendMessage({ type: "docflow.getState" }));
  } catch (error) {
    statusTitle.textContent = "启动失败";
    statusMessage.textContent = error.message || "扩展后台没有响应，请重新加载扩展";
    continueButton.disabled = false;
  }
});

stopButton.addEventListener("click", async () => {
  stopButton.disabled = true;
  try {
    const response = await chrome.runtime.sendMessage({ type: "docflow.pauseTask" });
    if (!response || response.ok === false) {
      throw new Error(response?.error || "暂停失败");
    }
    render(await chrome.runtime.sendMessage({ type: "docflow.getState" }));
  } catch (error) {
    statusTitle.textContent = "暂停失败";
    statusMessage.textContent = error.message || "扩展后台没有响应";
    stopButton.disabled = false;
  }
});
