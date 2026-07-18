# Oxilife Dashboard

Das Dashboard erkennt sämtliche Werte einer beliebig verschachtelten Tasmota-JSON-Antwort automatisch. Jeder Datenpunkt wird in SQLite registriert und kann im Adminbereich individuell konfiguriert werden.

## Mit Docker installieren und starten

Voraussetzung ist ein Rechner oder NAS mit Docker. Für die Compose-Variante wird zusätzlich Docker Compose benötigt, das bei aktuellen Docker-Installationen bereits als `docker compose` enthalten ist.

### Variante A: Repository herunterladen und selbst bauen

```bash
git clone https://github.com/bobbaone/oxilife-dashboard.git
cd oxilife-dashboard
```

Danach in `docker-compose.yml` mindestens diese Werte anpassen:

- `TASMOTA_BASE_URL`: Adresse des Tasmota-Geräts, zum Beispiel `http://192.168.1.50`
- `ADMIN_PASSWORD`: automatisch vergebenes Passwort für die erste Anmeldung (Standard: `wasserwerte`)
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
  -e ADMIN_PASSWORD=wasserwerte \
  -e SESSION_SECRET=EINE-LANGE-ZUFAELLIGE-ZEICHENFOLGE \
  ghcr.io/bobbaone/oxilife-dashboard:latest
```

`TASMOTA_BASE_URL` und `SESSION_SECRET` müssen vor dem Start angepasst werden. Die erste Anmeldung erfolgt mit `admin` und dem Initialpasswort `wasserwerte`. Danach erzwingt das Dashboard die Erstellung eines eigenen Benutzernamens und eines individuellen Passworts mit mindestens zehn Zeichen. Die neuen Zugangsdaten werden persistent gespeichert; das Passwort liegt ausschließlich als PBKDF2-SHA256-Hash in SQLite.

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

## Ampelfarben und Grenzwerte

Die öffentliche Seite verwendet folgende Bewertung:

- Grün – **Perfekt**: Wert liegt innerhalb der konfigurierten Warngrenzen.
- Gelb – **Kritisch**: Wert liegt außerhalb von `Warn min` oder `Warn max`, aber noch innerhalb von Minimum und Maximum.
- Rot – **Schlecht**: Wert liegt außerhalb von Minimum oder Maximum.

Beim Widget-Typ **Stufen** lauten die drei Zustände stattdessen **Langsam**, **Mittel** und **Schnell**. Ohne konfigurierte Grenzwerte bleibt die Anzeige neutral.

## E-Mail-Warnung bei niedrigem Wert

Für jeden numerischen Datenpunkt kann im Adminbereich **E-Mail niedrig** aktiviert werden. Fällt der skalierte Messwert unter `Warn min`, sendet das Dashboard beispielsweise:

```text
Achtung. Füllstand pH-Wert 6.4 niedrig.
```

SMTP und Empfänger werden über Docker-Umgebungsvariablen konfiguriert:

```yaml
SMTP_HOST: smtp.example.com
SMTP_PORT: 587
SMTP_USER: dashboard@example.com
SMTP_PASSWORD: eigenes-smtp-passwort
SMTP_FROM: dashboard@example.com
ALERT_EMAIL_TO: empfaenger@example.com
ALERT_COOLDOWN_SECONDS: 3600
```

Das Dashboard nutzt STARTTLS. Pro Datenpunkt wird während eines anhaltenden Niedrigstands höchstens eine Nachricht innerhalb der Sperrzeit versendet. Sobald sich der Wert erholt und später erneut absinkt, wird wieder gewarnt.

## Anlagensteuerung

Die vorhandenen Filterstufen- und Rückspülfunktionen bleiben erhalten. Die voreingetragenen Befehle sind Platzhalter und müssen vor dem Einsatz an einer echten Anlage geprüft werden.

## Datenspeicherung

SQLite liegt unter `/app/data/oxilife.db`. Docker Compose bindet dafür das benannte Volume `oxilife-dashboard-data` ein. Ein bestehendes bind-mount-basiertes `./data/oxilife.db` wird nicht automatisch in das neue Volume kopiert; bei einem Upgrade die Datei einmalig übernehmen oder den bisherigen Mount beibehalten.

Das benannte Volume bleibt erhalten, wenn der Container oder der lokale Projektordner gelöscht und das Repository neu geklont wird. Dadurch bleiben Datenpunkte, Historie, Einstellungen und Adminzugang bei Updates erhalten.

Für einen vollständigen Neustart mit leerer Datenbank müssen Container und Volume ausdrücklich gelöscht werden:

```bash
docker compose down -v
docker volume rm oxilife-dashboard-data 2>/dev/null || true
docker compose up -d --build
```

**Achtung:** Dabei werden sämtliche Messwerte, Einstellungen und selbst vergebenen Adminzugangsdaten unwiderruflich gelöscht. Die Ersteinrichtung beginnt anschließend wieder mit `admin` / `wasserwerte`.

## Container-Image

Pushes auf `main` und manuell gestartete Workflows bauen ein Multi-Arch-Image und veröffentlichen es als `ghcr.io/bobbaone/oxilife-dashboard:latest`. Für Pull Requests wird nur gebaut, nicht veröffentlicht.
