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
});
