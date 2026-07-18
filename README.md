# Generisches Oxilife / Tasmota Dashboard

Das Dashboard erkennt sämtliche Blattwerte einer beliebig verschachtelten Tasmota-JSON-Antwort automatisch. Jeder Datenpunkt wird in SQLite registriert und kann im Adminbereich individuell konfiguriert werden.

## Mit Docker installieren und starten

Voraussetzung ist ein Rechner oder NAS mit Docker. Für die Compose-Variante wird zusätzlich Docker Compose benötigt, das bei aktuellen Docker-Installationen bereits als `docker compose` enthalten ist.

### Variante A: Repository herunterladen und selbst bauen

```bash
git clone https://github.com/bobbaone/oxilife-dashboard.git
cd oxilife-dashboard
```

Danach in `docker-compose.yml` mindestens diese Werte anpassen:

- `TASMOTA_BASE_URL`: Adresse des Tasmota-Geräts, zum Beispiel `http://192.168.1.50`
- `ADMIN_PASSWORD`: eigenes, sicheres Admin-Passwort
- `SESSION_SECRET`: lange, zufällige Zeichenfolge
- bei Bedarf `TASMOTA_STATUS_PATH`, Abfrageintervall und Steuerbefehle

Container bauen und im Hintergrund starten:

```bash
docker compose up -d --build
```

### Variante B: Fertiges Image von GHCR verwenden

Image herunterladen:

```bash
docker pull ghcr.io/bobbaone/oxilife-dashboard:latest
```

Persistentes Volume anlegen und Container starten:

```bash
docker volume create oxilife-dashboard-data

docker run -d \
  --name oxilife-dashboard \
  --restart unless-stopped \
  -p 8090:8000 \
  -v oxilife-dashboard-data:/app/data \
  -e TZ=Europe/Berlin \
  -e TASMOTA_BASE_URL=http://192.168.1.50 \
  -e 'TASMOTA_STATUS_PATH=/cm?cmnd=Status%2010' \
  -e POLL_SECONDS=10 \
  -e ADMIN_USER=admin \
  -e ADMIN_PASSWORD=EIN-SICHERES-PASSWORT \
  -e SESSION_SECRET=EINE-LANGE-ZUFAELLIGE-ZEICHENFOLGE \
  ghcr.io/bobbaone/oxilife-dashboard:latest
```

`TASMOTA_BASE_URL`, `ADMIN_PASSWORD` und `SESSION_SECRET` müssen vor dem Start angepasst werden.

### Dashboard öffnen

- Öffentliche Übersicht: `http://SERVER-IP:8090`
- Adminbereich: `http://SERVER-IP:8090/admin`

`SERVER-IP` durch die IP-Adresse des Docker-Hosts ersetzen. Auf demselben Rechner kann `http://localhost:8090` verwendet werden.

### Betrieb und Updates

Status und Logs der Compose-Installation anzeigen:

```bash
docker compose ps
docker compose logs -f
```

Compose-Installation stoppen beziehungsweise erneut starten:

```bash
docker compose down
docker compose up -d
```

Eine Installation mit dem fertigen Image aktualisieren:

```bash
docker pull ghcr.io/bobbaone/oxilife-dashboard:latest
docker stop oxilife-dashboard
docker rm oxilife-dashboard
```

Danach den oben gezeigten `docker run`-Befehl erneut ausführen. Die Einstellungen und historischen Messwerte bleiben im Volume `oxilife-dashboard-data` erhalten.

## Datenpunkte

Objekte werden mit Punktpfaden und Arrays mit Indexpfaden erfasst, zum Beispiel `StatusSNS.DS18B20.Temperature` oder `sensors[0].value`. Neue Pfade erscheinen automatisch im Adminbereich. Dort lassen sich Name, Einheit, öffentliche Sichtbarkeit, Reihenfolge, Logging, Diagramm, Widget-Typ, Skalierung, Nachkommastellen sowie Anzeige- und Warngrenzen einstellen.

Nur explizit freigegebene Datenpunkte erscheinen auf der öffentlichen Startseite. Die Historie beginnt für einen Datenpunkt, solange **Logging** aktiv ist. Deaktivieren stoppt neue Einträge, löscht aber keine vorhandenen Daten.

## Anlagensteuerung

Die vorhandenen Filterstufen- und Rückspülfunktionen bleiben erhalten. Die voreingetragenen Befehle sind Platzhalter und müssen vor dem Einsatz an einer echten Anlage geprüft werden.

## Datenspeicherung

SQLite liegt unter `/app/data/oxilife.db`. Docker Compose bindet dafür das benannte Volume `oxilife-dashboard-data` ein. Ein bestehendes bind-mount-basiertes `./data/oxilife.db` wird nicht automatisch in das neue Volume kopiert; bei einem Upgrade die Datei einmalig übernehmen oder den bisherigen Mount beibehalten.

## Container-Image

Pushes auf `main` und manuell gestartete Workflows bauen ein Multi-Arch-Image und veröffentlichen es als `ghcr.io/bobbaone/oxilife-dashboard:latest`. Für Pull Requests wird nur gebaut, nicht veröffentlicht.
