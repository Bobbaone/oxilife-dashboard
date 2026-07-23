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

async function loadFiltrationSpecial() {
  const info = $("specialmsg");
  try {
    const data = await api("/api/admin/filtration-special");
    const config = data.config || {};
    $("heatClima").checked = Boolean(config.heat_clima);
    $("heatTemperature").value = config.heat_temperature;
    $("smartFrost").checked = Boolean(config.smart_frost);
    $("smartMinTemperature").value = config.smart_min_temperature;
    $("smartMaxTemperature").value = config.smart_max_temperature;
    $("intelTemperature").value = config.intel_temperature;
    $("intelHours").value = config.intel_hours;
    $("intelSpeed").value = String(config.intel_speed);
    const missing = Object.entries(data.commands || {})
      .filter(([, configured]) => !configured)
      .map(([key]) => key);
    info.textContent = missing.length
      ? "Hinweis: Intelligent-Geschwindigkeit wird im Dashboard gespeichert; ein belastbares Oxilife-Register dafür ist noch nicht hinterlegt."
      : "";
  } catch (error) {
    info.textContent = error.message;
  }
}

async function saveFiltrationSpecial(silent = false) {
  const info = $("specialmsg");
  if (!silent) info.textContent = "Filtration-Spezialwerte werden gespeichert …";
  const body = {
    heat_clima: $("heatClima").checked,
    heat_temperature: Number($("heatTemperature").value),
    smart_frost: $("smartFrost").checked,
    smart_min_temperature: Number($("smartMinTemperature").value),
    smart_max_temperature: Number($("smartMaxTemperature").value),
    intel_temperature: Number($("intelTemperature").value),
    intel_hours: Number($("intelHours").value),
    intel_speed: Number($("intelSpeed").value)
  };
  const data = await api("/api/admin/filtration-special", {method: "PUT", body: JSON.stringify(body)});
  if (!silent) {
    const missing = Object.entries(data.sent || {})
      .filter(([, item]) => !item.configured)
      .map(([key]) => key);
    info.textContent = missing.length
      ? "Gespeichert. Temperatur/Smart-Werte wurden an Oxilife gesendet; Intelligent-Geschwindigkeit bleibt lokal gespeichert."
      : "Gespeichert und an Oxilife gesendet.";
  }
  return data;
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
