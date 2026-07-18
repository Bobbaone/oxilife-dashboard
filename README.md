# Generisches Oxilife / Tasmota Dashboard

Das Dashboard erkennt sämtliche Blattwerte einer beliebig verschachtelten Tasmota-JSON-Antwort automatisch. Jeder Datenpunkt wird in SQLite registriert und kann im Adminbereich individuell konfiguriert werden.

## Start
1. In `docker-compose.yml` die Adresse bei `TASMOTA_BASE_URL` ändern.
2. Admin-Passwort und `SESSION_SECRET` ändern.
3. Starten:
   ```bash
   docker compose up -d --build
   ```
4. Öffnen: `http://SERVER-IP:8090`
5. Admin: `http://SERVER-IP:8090/admin`

## Datenpunkte

Objekte werden mit Punktpfaden und Arrays mit Indexpfaden erfasst, zum Beispiel `StatusSNS.DS18B20.Temperature` oder `sensors[0].value`. Neue Pfade erscheinen automatisch im Adminbereich. Dort lassen sich Name, Einheit, öffentliche Sichtbarkeit, Reihenfolge, Logging, Diagramm, Widget-Typ, Skalierung, Nachkommastellen sowie Anzeige- und Warngrenzen einstellen.

Nur explizit freigegebene Datenpunkte erscheinen auf der öffentlichen Startseite. Die Historie beginnt für einen Datenpunkt, solange **Logging** aktiv ist. Deaktivieren stoppt neue Einträge, löscht aber keine vorhandenen Daten.

## Anlagensteuerung

Die vorhandenen Filterstufen- und Rückspülfunktionen bleiben erhalten. Die voreingetragenen Befehle sind Platzhalter und müssen vor dem Einsatz an einer echten Anlage geprüft werden.

Die voreingetragenen Filter- und Rückspülbefehle sind nur Platzhalter und dürfen nicht ungeprüft an einer echten Anlage verwendet werden.

## Datenspeicherung
SQLite liegt unter `/app/data/oxilife.db`. Docker Compose bindet dafür das benannte Volume `oxilife-dashboard-data` ein. Ein bestehendes bind-mount-basiertes `./data/oxilife.db` wird nicht automatisch in das neue Volume kopiert; bei einem Upgrade die Datei einmalig übernehmen oder den bisherigen Mount beibehalten.

## Container-Image

Pushes auf `main` und manuell gestartete Workflows bauen ein Multi-Arch-Image und veröffentlichen es als `ghcr.io/bobbaone/oxilife-dashboard:latest`. Für Pull Requests wird nur gebaut, nicht veröffentlicht.
