document.addEventListener("DOMContentLoaded", () => {
  labels[0] = "Aus";

  loadStatus = async function loadPumpStatus() {
    const data = await api("/api/status");
    const mode = data.datapoints.find(point => point.semantic === "filtration_mode");
    const speed = data.datapoints.find(point => point.semantic === "filtration_speed");

    $("systemmsg").textContent = (data.online ? "Online · " : "Offline · ") +
      (data.updated_at ? new Date(data.updated_at * 1000).toLocaleString("de-DE") : "keine Daten");

    if (mode) showMode(Number(mode.value));
    if (Number(data.filtration?.state) === 0) showSpeed(0);
    else if (speed) showSpeed(Number(speed.value));
  };

  loadStatus().catch(error => {
    $("systemmsg").textContent = error.message;
  });

  loadBackwashEvents();
  setInterval(loadBackwashEvents, 30000);
});

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return "läuft";
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return minutes ? `${minutes} Min. ${rest} Sek.` : `${rest} Sek.`;
}

async function loadBackwashEvents() {
  try {
    const data = await api("/api/admin/backwash-events?limit=50");
    $("backwashSummary").textContent = `Gesamt: ${data.total} · Dieses Jahr: ${data.year}`;
    $("backwashHistory").innerHTML = data.events.length
      ? data.events.map(event => {
          const started = new Date(event.started_at * 1000).toLocaleString("de-DE");
          const status = event.ended_at ? formatDuration(event.duration_seconds) : "läuft derzeit";
          return `<p><b>${started}</b> · ${status}<br><span class="muted">${event.source}</span></p>`;
        }).join("")
      : "Noch keine Rückspülung erkannt.";
  } catch (error) {
    $("backwashHistory").textContent = error.message;
  }
}
