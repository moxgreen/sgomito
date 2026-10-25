# Provenienza geografica dei giocatori dall'IP

Stato: piano, non ancora implementato (ottobre 2026).

## Contesto

Per il playtest vogliamo sapere da dove giocano le persone. Si fa facilmente: si cerca
l'IP in un database GeoIP **locale** (nessun servizio esterno, nessuna chiamata di rete
per ogni giocatore) e si salvano **paese, regione e città** sul `Player`.
**L'IP non si salva**: si usa per la ricerca e poi si scarta, così si conservano meno dati
personali (GDPR). Il server andrà dietro nginx/Caddy, quindi l'IP vero arriva in
`X-Forwarded-For`.

Precisione: il paese è giusto per il ~99% degli IP, la città per il 50-80%; sui
telefoni spesso indica la sede dell'operatore. La città va letta come indicativa.

## Scelte

- **Database**: DB-IP "IP to City Lite" in formato `.mmdb` (licenza CC BY 4.0, senza
  registrazione, aggiornato ogni mese, ~130 MB). Ha lo stesso schema di MaxMind
  GeoLite2-City, quindi il codice funziona anche con quello se un giorno servisse.
  La licenza chiede l'attribuzione ("IP Geolocation by DB-IP") sul sito.
- **Lettore**: pacchetto `maxminddb` in `web/requirements.txt` (solo per il sito; il
  motore `hexpush/` resta solo stdlib come da CLAUDE.md).
- **Senza file `.mmdb`** (test, sviluppo locale) la geolocalizzazione è spenta in
  silenzio: luogo vuoto, nessun errore.
- **Proxy**: `HEXPUSH_PROXY=1` fa leggere l'IP dalla voce **più a destra** di
  `X-Forwarded-For`, cioè quella aggiunta dal nostro proxy (sia nginx con
  `$proxy_add_x_forwarded_for` sia Caddy aggiungono in coda). Le voci più a sinistra le
  può scrivere chiunque e non si guardano. Senza la variabile si usa `REMOTE_ADDR`
  (lo stesso comportamento di oggi in locale).

## Modifiche

1. **`web/play/geo.py`** (nuovo)
   - `client_ip(request)`: `REMOTE_ADDR`, oppure la voce più a destra di
     `HTTP_X_FORWARDED_FOR` se `settings.HEXPUSH_PROXY`.
   - `locate(ip) -> {"country": "IT", "region": "Piedmont", "city": "Turin"}`, oppure
     `{}` per IP privati/loopback (`ipaddress.ip_address(ip).is_global`), IP non trovati,
     IP malformati o file assente. Lettore `maxminddb` aperto alla prima chiamata e tenuto
     in memoria (`functools.cache`). I nomi si prendono da `names["en"]`, la regione da
     `subdivisions[0]`.

2. **`web/hexweb/settings.py`**: `HEXPUSH_GEOIP` (percorso del `.mmdb`, default
   `web/geoip/dbip-city-lite.mmdb`) e `HEXPUSH_PROXY` (default spento), letti
   dall'ambiente come le altre variabili `HEXPUSH_*`.

3. **`web/play/models.py`**: sul `Player`, `country` (CharField 2, codice ISO),
   `region`, `city` (CharField blank) e `located` (DateTimeField null): luogo
   dell'ultima visita. Una migrazione nuova.
   **Attenzione**: le modifiche non ancora committate (`Nickname`, `account`,
   `name_a/b`, ...) non hanno ancora una migrazione (`0001_initial.py` non le contiene).
   Prima si genera quella (0002), poi quella della geolocalizzazione (0003), per
   tenerle separate.

4. **`web/play/records.py`**: `update_place(player, place)`, che salva solo se paese,
   regione o città sono cambiati (niente scritture a ogni pagina), e `set_player_name(pid,
   name, password="", place=None)`, che mette il luogo nei `defaults` di
   `update_or_create`.

5. **`web/play/views.py`**: in `create` e nel POST di `match_page` si passa
   `place=geo.locate(geo.client_ip(request))` a `set_player_name`; in `home` e nel GET
   di `match_page`, se il giocatore esiste già, `records.update_place`. Il consumer
   WebSocket non cambia: chi si siede è già passato dalle viste.

6. **Registro** (`views.py`, `registry.html`, `management/commands/registry.py` se scrive
   le stesse colonne):
   - `partite.csv`: `country_A`, `region_A`, `city_A`, `country_B`, `region_B`,
     `city_B` (dal `Player` di ciascun posto, `select_related`);
   - `giudizi.csv`: `country`, `region`, `city`;
   - pagina `/registro/`: una tabellina "giocatori per paese" (conteggio dei `Player`).

7. **Avviso e attribuzione** in fondo alla home (in inglese come il resto del sito):
   "We record your approximate location (country, region, city) from your IP address;
   the address itself is not stored. IP geolocation by DB-IP." con il link a db-ip.com.

8. **Download del database**: target `make geoip` nel `Makefile` che scarica
   `https://download.db-ip.com/free/dbip-city-lite-YYYY-MM.mmdb.gz` del mese corrente e
   lo decomprime in `web/geoip/`. `web/geoip/` va in `.gitignore`. Da rilanciare ogni
   tanto (anche da cron sul server).

9. **`web/README.md`**: le variabili `HEXPUSH_GEOIP` e `HEXPUSH_PROXY`, `make geoip`, e
   il blocco nginx/Caddy minimo (`proxy_set_header X-Forwarded-For
   $proxy_add_x_forwarded_for;` più gli header di upgrade per il WebSocket).
   Nota per chi legge i dati: il luogo è quello dell'ultima visita del browser, non
   della singola partita.

## Test (`web/play/tests.py`)

- `client_ip`: con e senza `HEXPUSH_PROXY` (`override_settings`); con il proxy, un
  `X-Forwarded-For: 6.6.6.6, 1.2.3.4` falsificato dà `1.2.3.4`.
- `locate`: lettore finto al posto di quello vero (un oggetto con `.get(ip)` che
  restituisce un record con lo schema DB-IP); IP privato → `{}`; IP malformato → `{}`;
  file assente → `{}`.
- Vista: con `locate` sostituito, `POST /create` salva `country/region/city` sul
  `Player`; una seconda visita dallo stesso paese non riscrive la riga.
- CSV: le colonne nuove ci sono e sono piene.

## Verifica

1. `.venv/bin/pip install -r web/requirements.txt`, `make geoip`,
   `.venv/bin/python web/manage.py migrate`.
2. `make test` (`manage.py test play` e `python3 -m unittest`).
3. Controllo a mano del database: `python -c` con `geo.locate("130.192.1.1")` (rete
   dell'Università di Torino) → `IT`, Piemonte, Torino (o simile).
4. Prova del proxy in locale: `HEXPUSH_PROXY=1 make run` e
   `curl -H 'X-Forwarded-For: 8.8.8.8' -c jar http://localhost:8000/` seguito da una
   creazione partita; in `/registro/partite.csv` deve comparire `US`.
