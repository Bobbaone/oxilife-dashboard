# Oxilife / Tasmota Pool Dashboard

## Start
1. In `docker-compose.yml` die IP bei `TASMOTA_BASE_URL` ändern.
2. Admin-Passwort und `SESSION_SECRET` ändern.
3. Starten:
   ```bash
   docker compose up -d --build
   ```
4. Öffnen: `http://SERVER-IP:8090`
5. Admin: `http://SERVER-IP:8090/admin`

## Wichtig
Die tatsächlichen Tasmota-Ausgaben und Oxilife-Kommandos sind noch nicht bekannt. Die Seite erkennt typische Feldnamen automatisch. Unter **Admin → Rohdaten** siehst du die komplette JSON-Antwort. Danach können die Feldzuordnung und die vier Steuerbefehle exakt angepasst werden.

Die voreingetragenen Filter- und Rückspülbefehle sind nur Platzhalter und dürfen nicht ungeprüft an einer echten Anlage verwendet werden.

## Datenspeicherung
Messwerte werden in `./data/oxilife.db` gespeichert. Der Verlauf ist bereits per API vorhanden (`/api/history`), die grafische Kurve kann nach dem ersten echten Datensatz ergänzt werden.
