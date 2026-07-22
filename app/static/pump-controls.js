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
  loadBackwashSchedule();
  loadPumpProfile();
  setInterval(loadBackwashEvents, 30000);
});

async function loadPumpProfile() {
  const info = $("pumpProfileMsg");
  try {
    const data = await api("/api/admin/pump-profile");
    $("pumpModel").value = data.model;
    for (const speed of [1, 2, 3]) {
      $("pumpRpm" + speed).value = data.stages[speed].rpm;
      $("pumpWatts" + speed).value = data.stages[speed].watts;
    }
    info.textContent = "";
  } catch (error) {
    info.textContent = error.message;
  }
}

async function savePumpProfile() {
  const info = $("pumpProfileMsg");
  info.textContent = "Pumpenprofil wird gespeichert …";
  try {
    const stages = {};
    for (const speed of [1, 2, 3]) stages[speed] = {
      rpm: Number($("pumpRpm" + speed).value), watts: Number($("pumpWatts" + speed).value)};
    await api("/api/admin/pump-profile", {method: "PUT", body: JSON.stringify({model: $("pumpModel").value, stages})});
    info.textContent = "Pumpenprofil gespeichert. Statistiken und Berichte verwenden ab jetzt diese Werte.";
  } catch (error) {
    info.textContent = error.message;
  }
}

async function loadBackwashSchedule() {
  const info = $("backwashScheduleInfo");
  try {
    const data = await api("/api/admin/backwash-schedule");
    $("backwashAutomatic").checked = data.automatic;
    $("backwashWeekday").value = String(data.weekday);
    $("backwashStart").value = data.start;
    $("backwashRepeat").value = String(data.repeat_days);
    $("backwashDuration").value = data.duration_seconds;
    const next = data.next_label ? ` · nächste Durchführung ${data.next_label}` : "";
    info.textContent = data.available
      ? `Aktuell: ${data.automatic ? "Automatisch" : "Aus"} · alle ${data.repeat_days} Tag(e) · ${data.duration_seconds} Sekunden${next}${data.remaining_seconds ? ` · läuft noch ${data.remaining_seconds} Sekunden` : ""}`
      : "Die Besgo-Rückspülungsfunktion ist in Oxilife nicht eingerichtet.";
  } catch (error) {
    info.textContent = error.message;
  }
}

async function saveBackwashSchedule() {
  const info = $("backwashScheduleInfo");
  info.textContent = "Rückspülungszeitplan wird gespeichert und geprüft …";
  try {
    const body = {automatic: $("backwashAutomatic").checked,
      weekday: Number($("backwashWeekday").value), start: $("backwashStart").value,
      repeat_days: Number($("backwashRepeat").value),
      duration_seconds: Number($("backwashDuration").value)};
    await api("/api/admin/backwash-schedule", {method: "PUT", body: JSON.stringify(body)});
    info.textContent = "Zeitplan von Oxilife bestätigt.";
    await loadBackwashSchedule();
  } catch (error) {
    info.textContent = error.message;
  }
}

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
