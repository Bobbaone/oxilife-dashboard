# PoolMonitor

PoolMonitor ist ein lokales Dashboard für Oxilife-/NeoPool-Poolanlagen. Es erkennt sämtliche Werte einer beliebig verschachtelten Tasmota-JSON-Antwort automatisch. Jeder Datenpunkt wird in SQLite registriert und kann im Adminbereich individuell konfiguriert werden.

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
- `SESSION_HTTPS_ONLY`: bei ausschließlichem HTTPS-Betrieb hinter einem Reverse Proxy auf `true` setzen
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

`TASMOTA_BASE_URL` und `SESSION_SECRET` müssen vor dem Start angepasst werden. Die erste Anmeldung erfolgt mit `admin` und dem Initialpasswort `wasserwerte`. Danach erzwingt das Dashboard ein individuelles Passwort mit mindestens zehn Zeichen. Der Benutzername `admin` kann beibehalten oder optional geändert werden. Die Zugangsdaten werden persistent gespeichert; das Passwort liegt ausschließlich als PBKDF2-SHA256-Hash in SQLite.

Bei einem unsicheren oder zu kurzen `SESSION_SECRET` schreibt die Anwendung eine deutliche Warnung ins Container-Log. Für öffentlich erreichbare Installationen sollte `SESSION_HTTPS_ONLY=true` gesetzt werden. Dann wird das Session-Cookie ausschließlich über HTTPS übertragen; eine Anmeldung über eine direkte unverschlüsselte LAN-Adresse ist in diesem Modus absichtlich nicht möglich. Port `8090` darf nicht direkt aus dem Internet erreichbar sein – vorgeschaltet werden sollte ein TLS-terminierender Reverse Proxy oder Cloudflare Tunnel.

Das Login akzeptiert höchstens fünf Fehlversuche innerhalb von 15 Minuten pro Client und Benutzerkonto. Weitere Versuche werden vorübergehend mit HTTP `429` und einem `Retry-After`-Header abgewiesen.

Der eigentliche Dashboard-Prozess läuft im Container als unprivilegierter Benutzer `poolmonitor` (UID/GID 10001). Beim Start korrigiert ein kurzer Entrypoint mit Root-Rechten ausschließlich die Besitzrechte des persistenten Verzeichnisses `/app/data` und gibt anschließend alle Privilegien dauerhaft ab. Dadurch bleiben auch bereits vorhandene SQLite-Volumes nach dem Update beschreibbar.

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

Auf das neueste veröffentlichte GHCR-Image aktualisieren:

```bash
docker compose down
docker compose pull
docker compose up -d --force-recreate
```

Für einen lokalen Build direkt aus dem ausgecheckten Quellcode stattdessen `docker compose up -d --build` verwenden.

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

## Getrennter Verbindungsstatus

Die Startseite zeigt das Modbus/Tasmota-Gateway und Oxilife getrennt an. Der Gateway-Name kann mit `TASMOTA_DISPLAY_NAME` vorbelegt und später im Adminbereich geändert werden. Das Gateway gilt als online, wenn die HTTP-Abfrage erfolgreich ist.

Für Oxilife wird im Adminbereich unter **Verbindungsstatus** ein regelmäßig aktualisierter Oxilife-Datenpunkt als Lebenszeichen ausgewählt. Wird er innerhalb des konfigurierten Zeitfensters nicht mehr empfangen, zeigt die Startseite beispielsweise:

```text
Modbus: Online · Oxilife: Offline
```

Ohne ausgewählten Lebenszeichen-Datenpunkt steht Oxilife auf **Nicht konfiguriert**. Das verhindert eine falsche Online-Anzeige, bevor die reale JSON-Struktur der Anlage bekannt ist.

## Ampelfarben und Grenzwerte

Die öffentliche Seite verwendet folgende Bewertung:

- Grün – **Perfekt**: Wert liegt innerhalb der konfigurierten Warngrenzen.
- Gelb – **Kritisch**: Wert liegt außerhalb von `Warn min` oder `Warn max`, aber noch innerhalb von Minimum und Maximum.
- Rot – **Schlecht**: Wert liegt außerhalb von Minimum oder Maximum.

Beim Widget-Typ **Stufen** lauten die drei Zustände stattdessen **Langsam**, **Mittel** und **Schnell**. Ohne konfigurierte Grenzwerte bleibt die Anzeige neutral.

Kritische oder schlechte freigegebene Messwerte werden zusätzlich in einer gut sichtbaren Warnbox unten auf der Startseite zusammengefasst. Damit kann beispielsweise ein niedriger pH-Tankfüllstand sofort auffallen. Pumpen-Datenpunkte mit dem Widget-Typ **Stufen** lösen diese Warnbox nicht aus.

## Wetter-Widget

Im Adminbereich kann unter **Wetter** eine fünfstellige deutsche Postleitzahl gespeichert werden. Das Dashboard ermittelt den zugehörigen Ort und zeigt unten mittig auf der öffentlichen Startseite aktuelle Temperatur, Wetterlage, Luftfeuchtigkeit und Wind an. Die Daten werden serverseitig zwischengespeichert und standardmäßig höchstens alle 15 Minuten neu abgerufen. Das Intervall kann mit `WEATHER_REFRESH_SECONDS` angepasst werden.

Erfolgreiche Wetterabrufe werden dauerhaft in SQLite aufgezeichnet – auch wenn gerade niemand die Webseite geöffnet hat. Unter **Statistiken → Wetterverlauf** stehen Temperaturkurve, Minimum, Durchschnitt, Maximum sowie eine Tagesübersicht mit Wetterlage, Luftfeuchtigkeit und Wind bereit. Die Wetterhistorie liegt zusammen mit den übrigen Daten im persistenten Docker-Volume.

Optional kann die PLZ bereits in `docker-compose.yml` vorbelegt werden:

```yaml
WEATHER_POSTAL_CODE: "75015"
WEATHER_REFRESH_SECONDS: 900
```

`WEATHER_POSTAL_CODE` dient nur als Erstkonfiguration, solange noch kein Wetterort in SQLite gespeichert wurde. Eine später im Adminbereich gespeicherte PLZ hat Vorrang und bleibt im Docker-Volume erhalten.

Für die Ortsauflösung wird [OpenStreetMap Nominatim](https://nominatim.openstreetmap.org/) verwendet; die Wetterdaten stammen von [Open-Meteo](https://open-meteo.com/). Nichtkommerzielle Nutzung benötigt keinen API-Schlüssel. Der Docker-Container benötigt dafür ausgehenden Internetzugriff. Bei einem vorübergehenden API-Ausfall werden nach Möglichkeit die zuletzt bekannten Daten angezeigt; das übrige Dashboard bleibt funktionsfähig.

## Als App auf dem Home-Bildschirm

Das öffentliche Dashboard ist als Progressive Web App (PWA) vorbereitet. Auf iPhone und iPad die Dashboard-Adresse in Safari öffnen und **Teilen → Zum Home-Bildschirm** auswählen. Auf Android kann im Browser **App installieren** oder **Zum Startbildschirm hinzufügen** verwendet werden.

Die installierte PWA startet als **PoolMonitor** im eigenständigen App-Fenster. Ist der Dashboard-Server nicht erreichbar, erscheint eine eindeutige Offline-Seite. Messwerte und Steuerbefehle werden absichtlich nicht offline zwischengespeichert, damit keine veralteten Anlagenzustände als aktuell erscheinen.

Für Installation und Service Worker muss das Dashboard per HTTPS oder lokal über `localhost` aufgerufen werden. Bei Änderungen aktualisiert sich die PWA automatisch mit der veröffentlichten Webseite.

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

### Individuelles Pumpenprofil

Unter **Pumpe → Pumpenprofil** können Pumpenmodell, Drehzahl und Leistungsaufnahme in Watt für die Stufen **Langsam**, **Mittel** und **Schnell** hinterlegt werden. Das Dashboard verwendet diese individuellen Werte für die Stromverbrauchsdiagramme, die kWh-Zusammenfassungen und die PDF-Wochenberichte. Die Konfiguration wird persistent in SQLite gespeichert und bleibt bei Container-Updates erhalten.

### Shelly Plug M Stromtest

Ein Shelly Plug M kann lokal über die Gen2/Gen3-RPC-API abgefragt werden. Die voreingestellte IP ist `192.168.5.233` und kann per `SHELLY_PLUG_IP` in `.env` oder Docker Compose geändert werden. Die Testansicht ist unter `/shelly-preview` erreichbar und zeigt aktuell gemessene Leistung, Gesamtverbrauch, Spannung, Strom und Rohdaten. Die Abfrage ist nur lesend und nutzt `Switch.GetStatus?id=0`.

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
