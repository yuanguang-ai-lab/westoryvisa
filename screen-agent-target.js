const fields = [...document.querySelectorAll(".mock-field")];
const counter = document.querySelector(".rail-boundary strong");
const jobLabel = document.querySelector("#jobLabel");
const jobId = new URLSearchParams(window.location.search).get("job") || "LOCAL SESSION";

jobLabel.textContent = jobId.slice(-12).toUpperCase();

function updateFieldState() {
  const completed = fields.filter((field) => {
    const input = field.querySelector("input");
    const filled = Boolean(input?.value.trim());
    field.classList.toggle("filled", filled);
    return filled;
  }).length;
  counter.textContent = `${completed} / ${fields.length}`;
}

fields.forEach((field) => {
  field.querySelector("input")?.addEventListener("input", updateFieldState);
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  document.activeElement?.blur();
  document.body.classList.add("agent-stopped");
  const ready = document.querySelector(".agent-ready");
  if (ready) ready.innerHTML = "<span></span> Agent stopped by operator";
});

updateFieldState();
