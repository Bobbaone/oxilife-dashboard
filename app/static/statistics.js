const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));

async function api(url) {
  const response = await fetch(url), data = await response.json();
  if (response.status === 401) { location.href = "/admin"; throw Error("Anmeldung erforderlich"); }
  if (!response.ok) throw Error(data.detail || "Fehler");
  return data;
}

function duration(seconds) {
  const hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds % 3600 / 60);
  return hours ? `${hours} Std. ${minutes} Min.` : `${minutes} Min.`;
}

function kwh(value) {
  return Number(value).toLocaleString("de-DE", {minimumFractionDigits: 2, maximumFractionDigits: 2}) + " kWh";
}

async function loadRuntime() {
  const data = await api("/api/admin/filter-runtime");
  const items = [["Heute",data.summary.today],["Diese Woche",data.summary.week],["Dieser Monat",data.summary.month],["Dieses Jahr",data.summary.year],["Gesamt",data.summary.total]];
  $("runtimeSummary").innerHTML = items.map(item => `<div class="runtime-card"><span class="muted">${item[0]}</span><b>${duration(item[1])}</b></div>`).join("");
  $("energySummary").innerHTML = [["Energie heute",data.energy_kwh.today],["Energie dieses Jahr",data.energy_kwh.year],["Energie gesamt",data.energy_kwh.total]].map(item => `<div class="runtime-card"><span class="muted">${item[0]}</span><b>${kwh(item[1])}</b></div>`).join("");
  const maximum = Math.max(1, ...data.monthly.map(item => item.seconds));
  $("runtimeMonths").innerHTML = data.monthly.map(item => `<div class="runtime-month"><span class="muted">${item.label}</span><b>${duration(item.seconds)}</b><div>${kwh(item.kwh)}</div><div class="runtime-bar"><i style="width:${item.seconds / maximum * 100}%"></i></div></div>`).join("");
  $("powerProfile").innerHTML = data.energy_by_speed.map(item => `<div class="power-card"><span class="muted">Stufe ${item.speed} · ${Number(item.rpm).toLocaleString("de-DE")} U/min</span><b>${item.watts} W</b><div>${kwh(item.kwh)} erfasst · individuell hinterlegt</div></div>`).join("");
  const modes = ["Manuell","Automatik","Heizung","Smart","Intelligent"];
  $("runtimeRuns").innerHTML = data.recent.map(item => `<div class="report"><div><b>${new Date(item.started_at * 1000).toLocaleString("de-DE")} – ${item.active ? "läuft" : new Date(item.ended_at * 1000).toLocaleString("de-DE")}</b><div class="muted">${duration(item.duration_seconds)} · ${modes[item.mode] ?? "Modus " + item.mode} · Stufe ${item.speed ?? "–"}</div></div></div>`).join("") || '<p class="muted">Noch keine Pumpenlaufzeit erfasst.</p>';
}

function draw(canvas, values) {
  const context = canvas.getContext("2d"), width = canvas.clientWidth, height = canvas.clientHeight, ratio = devicePixelRatio || 1;
  canvas.width = width * ratio; canvas.height = height * ratio; context.scale(ratio, ratio);
  const points = values.filter(item => Number.isFinite(item.value_num));
  context.fillStyle = "#8eabb7"; context.font = "12px system-ui";
  if (points.length < 2) { context.fillText("Nicht genügend numerische Daten", 12, 25); return; }
  let low = Math.min(...points.map(item => item.value_num)), high = Math.max(...points.map(item => item.value_num));
  if (low === high) { low--; high++; }
  const left = 45, right = 10, top = 10, bottom = 25;
  context.strokeStyle = "#1d3a46";
  for (let index = 0; index < 5; index++) { const y = top + (height-top-bottom)*index/4; context.beginPath(); context.moveTo(left,y); context.lineTo(width-right,y); context.stroke(); context.fillText((high-(high-low)*index/4).toFixed(1),2,y+4); }
  context.strokeStyle = "#42c8f5"; context.lineWidth = 2; context.beginPath();
  points.forEach((point,index) => { const x=left+(width-left-right)*index/(points.length-1), y=top+(height-top-bottom)*(high-point.value_num)/(high-low); index ? context.lineTo(x,y) : context.moveTo(x,y); });
  context.stroke();
}

function meaningful(series) {
  const point=series.datapoint, path=String(point.path ?? "").toLowerCase();
  if (point.data_type !== "number" || ["status","text","levels"].includes(point.widget_type) || path.includes(".modules.")) return false;
  return series.values.some(value => Number.isFinite(value.value_num) && value.value_num !== 0);
}

async function loadHistory() {
  const hours=$("range").value;
  const [data,energy]=await Promise.all([api("/api/admin/history?hours=" + hours),api("/api/admin/filter-energy-history?hours=" + hours)]), series=data.series.filter(meaningful);
  const energyCard=`<article class="chartbox"><b>Stromverbrauch Pumpe</b><div class="muted">Gesamt ${kwh(energy.total)} · Verbrauch je ${energy.bucket_seconds < 3600 ? energy.bucket_seconds / 60 + " Minuten" : energy.bucket_seconds / 3600 + " Stunden"}</div><canvas class="chart" id="energyChart"></canvas></article>`;
  $("charts").innerHTML=energyCard+series.map((item,index)=>`<article class="chartbox"><b>${esc(item.datapoint.name)}</b><div class="muted">Min ${item.stats.min ?? "–"} · Ø ${item.stats.avg == null ? "–" : Number(item.stats.avg).toFixed(item.datapoint.decimals)} · Max ${item.stats.max ?? "–"} · ${item.stats.samples} Werte</div><canvas class="chart" id="chart${index}"></canvas></article>`).join("");
  draw($("energyChart"),energy.values);
  series.forEach((item,index)=>draw($("chart"+index),item.values));
}

async function loadWeatherHistory() {
  const data=await api("/api/admin/weather-history?hours="+$('range').value), stats=data.stats;
  const temp=value=>value == null ? "–" : Number(value).toLocaleString("de-DE",{minimumFractionDigits:1,maximumFractionDigits:1})+" °C";
  $("weatherSummary").innerHTML=[["Minimum",temp(stats.minimum)],["Durchschnitt",temp(stats.average)],["Maximum",temp(stats.maximum)],["Aufzeichnungen",stats.samples]].map(item=>`<div class="runtime-card"><span class="muted">${item[0]}</span><b>${item[1]}</b></div>`).join("");
  $("weatherChartMeta").textContent=`${stats.samples} Wetterwerte · Intervall ${data.bucket_seconds/3600 < 1 ? data.bucket_seconds/60+" Minuten" : data.bucket_seconds/3600+" Stunden"}`;
  draw($("weatherChart"),data.values);
  $("weatherDays").innerHTML=data.days.map(day=>`<div class="weather-day"><span class="muted">${new Date(day.date+"T12:00:00").toLocaleDateString("de-DE",{weekday:"short",day:"2-digit",month:"2-digit"})}</span><b>${esc(day.condition)}</b><span>${temp(day.minimum)} bis ${temp(day.maximum)}</span><span class="muted">Ø Luftfeuchte ${day.humidity == null ? "–" : Math.round(day.humidity)+" %"} · Wind max. ${day.wind_max == null ? "–" : Number(day.wind_max).toLocaleString("de-DE",{maximumFractionDigits:1})+" km/h"}</span></div>`).join("") || '<p class="muted">Noch keine Wetterhistorie vorhanden. Der erste Wert wird beim nächsten Wetterabruf gespeichert.</p>';
}

async function loadReports() {
  const reports=await api("/api/admin/reports");
  $("reports").innerHTML=reports.map(report=>`<div class="report"><div><b>${esc(report.filename.replace(".pdf","").replaceAll("_"," "))}</b><div class="muted">${new Date(report.created_at*1000).toLocaleString("de-DE")} · ${(report.size/1024).toFixed(0)} KB</div></div><a href="/api/admin/reports/${encodeURIComponent(report.filename)}">PDF herunterladen</a></div>`).join("") || '<p class="muted">Noch kein abgeschlossener Wochenbericht vorhanden.</p>';
}

async function loadSystemStatus() {
  const data=await api("/api/status");
  $("systemmsg").textContent=(data.online ? "Online · " : "Offline · ") + (data.updated_at ? new Date(data.updated_at*1000).toLocaleString("de-DE") : "keine Daten");
}

Promise.all([loadRuntime(),loadHistory(),loadWeatherHistory(),loadReports(),loadSystemStatus()]).catch(error => { $("reports").innerHTML='<p class="muted">'+esc(error.message)+"</p>"; });
