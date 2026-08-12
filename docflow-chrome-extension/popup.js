"use strict";

const statusTitle = document.querySelector("#statusTitle");
const statusMessage = document.querySelector("#statusMessage");
const pageLabel = document.querySelector("#pageLabel");
const progress = document.querySelector("#progress");
const statusDot = document.querySelector("#statusDot");
const stopButton = document.querySelector("#stopButton");

document.querySelector("#version").textContent = `v${chrome.runtime.getManifest().version}`;

function render(state) {
  const active = Boolean(state && state.active);
  const blocked = state && state.state === "blocked";
  statusTitle.textContent = active ? (blocked ? "等待人工处理" : "任务进行中") : "等待 DocFlow";
  statusMessage.textContent = state && state.message
    ? state.message
    : "在 DocFlow 的填写演示页中启动任务后，这里会显示实时状态。";
  pageLabel.textContent = state && state.pageLabel ? state.pageLabel : "未连接";
  progress.textContent = `${state && state.completedFields ? state.completedFields : 0} / ${state && state.totalFields ? state.totalFields : 0}`;
  statusDot.className = `status-dot${active ? " active" : ""}${blocked ? " blocked" : ""}`;
  stopButton.disabled = !active;
}

chrome.runtime.sendMessage({ type: "docflow.getState" }).then(render).catch(() => render(null));

stopButton.addEventListener("click", async () => {
  stopButton.disabled = true;
  await chrome.runtime.sendMessage({ type: "docflow.stopTask" });
  render(await chrome.runtime.sendMessage({ type: "docflow.getState" }));
});
