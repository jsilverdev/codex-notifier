# Codex Notifier

Notificador de finalización para Codex en Windows y Linux. Envía una Adaptive
Card a Microsoft Teams, un embed a Discord y puede reproducir un aviso por voz
cuando el turno supera los umbrales configurados.

No requiere paquetes de `pip`; usa únicamente Python 3 y herramientas del
sistema operativo.

## Comportamiento predeterminado

La configuración inicial se copia literalmente desde `config.example.json`:

- Teams desactivado hasta añadir un webhook y cambiar `enabled` a `true`.
- Teams a partir de 300 segundos.
- Los resúmenes largos conservan el inicio y los últimos 600 caracteres dentro
  de un máximo de 1.800 caracteres.
- Discord desactivado hasta añadir un webhook y cambiar `enabled` a `true`.
- Discord a partir de 300 segundos.
- Voz a partir de 30 segundos y también cuando no se puede determinar la
  duración.
- La voz añade el título del chat cuando Codex lo proporciona.
- Silencio de voz entre las 23:00 y las 07:00.
- Idioma de voz automático según el mensaje final.
- Voz, Teams y Discord se ejecutan de forma independiente; la voz espera a que
  el motor termine para evitar que Codex cierre el proceso antes de tiempo.
- Log diario de trazabilidad con siete días de retención.
- Nunca se guarda el prompt; el estado temporal contiene solo identificadores,
  directorio y hora de inicio.

## Requisitos

- Python 3.10 o posterior.
- Codex instalado y un `~/.codex/config.toml` accesible.
- Windows: SAPI, incluido normalmente con Windows.
- Linux, solo para voz: `spd-say` (Speech Dispatcher), `espeak-ng` o `espeak`.
  Teams y Discord funcionan aunque no haya motor de voz.

`mise` no es obligatorio. El instalador busca Python 3 en este orden:

1. `python3`.
2. En Windows, `py -3`.
3. `python` si realmente es Python 3.
4. `mise exec -- python` si no encontró un Python independiente.
5. El intérprete con el que se inició `install.py`.

## Instalación única

Hay un único instalador multiplataforma: `install.py`.

Windows:

```powershell
python install.py
```

También puedes iniciarlo con:

```powershell
py -3 install.py
mise exec -- python install.py
```

Linux:

```bash
python3 install.py
```

Si Python solo está administrado por mise:

```bash
mise exec -- python install.py
```

El instalador realiza las tres tareas:

1. Configura `notify` en `~/.codex/config.toml`.
2. Añade o actualiza el hook `UserPromptSubmit` en `~/.codex/hooks.json`, sin
   eliminar otros hooks.
3. Si todavía no existe, copia `config.example.json` a la ubicación local de
   configuración.

Los archivos de Codex existentes reciben una copia `.bak` antes de modificarse.
La configuración local existente nunca se sobrescribe automáticamente. Después
de cambiar el hook, abre un chat nuevo y usa `/hooks` para revisarlo y confiar
en él.

Opciones útiles:

```text
--codex-home RUTA          CODEX_HOME alternativo
--config-path RUTA         config.json alternativo
--python-executable RUTA   Python 3 específico para notify y el hook
--force-config             reemplaza config.json desde el ejemplo, con backup
```

Ten cuidado con `--force-config`: reemplaza el webhook y las preferencias
locales por los valores del ejemplo.

## Ubicación de la configuración

Windows:

```text
%LOCALAPPDATA%\CodexNotifier\config.json
```

Linux:

```text
$XDG_CONFIG_HOME/codex-notifier/config.json
```

Si `XDG_CONFIG_HOME` no existe:

```text
~/.config/codex-notifier/config.json
```

Se puede usar otra ruta definiendo `CODEX_NOTIFIER_CONFIG` o pasando
`--config-path` al instalar.

El instalador conserva una configuración local existente. Si actualizas desde
una versión anterior, agrega manualmente la sección `discord` y cambia las
opciones de `voice` mostradas abajo; `--force-config` reemplaza el archivo
completo y puede eliminar un webhook que ya tengas configurado.

## Configuración

```json
{
  "teams": {
    "enabled": false,
    "minimum_seconds": 300,
    "notify_when_duration_unknown": false,
    "summary_max_chars": 1800,
    "summary_tail_chars": 600,
    "webhook_url": ""
  },
  "discord": {
    "enabled": false,
    "minimum_seconds": 300,
    "notify_when_duration_unknown": false,
    "summary_max_chars": 1800,
    "webhook_url": ""
  },
  "voice": {
    "enabled": true,
    "minimum_seconds": 30,
    "notify_when_duration_unknown": true,
    "quiet_start": "23:00",
    "quiet_end": "07:00",
    "language": "auto",
    "spanish_voice": "",
    "english_voice": ""
  },
  "logging": {
    "enabled": true,
    "retention_days": 7
  }
}
```

Para activar Teams, completa ambos valores:

```json
"enabled": true,
"webhook_url": "https://tu-webhook"
```

Discord se activa de la misma manera dentro de su propia sección. Teams y
Discord comparten el transporte HTTP, los reintentos y los datos de contexto,
pero usan formatos distintos: Adaptive Card para Teams y embed para Discord.

Los webhooks son credenciales. El archivo local está fuera del repositorio y
no debe compartirse ni añadirse a Git.

`notify_when_duration_unknown` omite solamente el umbral cuando no existe un
marcador de inicio. El canal todavía debe estar habilitado, el webhook debe ser
válido y la voz continúa respetando el horario silencioso.

Cuando el mensaje final supera `summary_max_chars`, la card muestra el comienzo,
un aviso con el número de caracteres omitidos y los últimos
`summary_tail_chars`. El máximo configurable se mantiene entre 100 y 6.000
caracteres para dejar margen bajo el límite total de 28 KB de Teams. Si la
configuración local existente no incluye estas propiedades, se aplican los
valores predeterminados de 1.800 y 600.

En Discord, `summary_max_chars` se limita a 4.096 caracteres y el notifier
desactiva las menciones para que el resumen no genere avisos como `@everyone`.

`language` admite:

- `auto`: estima español o inglés usando el mensaje final.
- `es`: siempre español.
- `en`: siempre inglés.

En Windows, `spanish_voice` y `english_voice` pueden contener parte del nombre
de una voz SAPI instalada. Si están vacíos, se elige automáticamente una voz del
idioma. Linux utiliza el código de idioma con `spd-say` o `espeak`.

## Log de trazabilidad

El notifier escribe un archivo JSON Lines por día en el subdirectorio `logs`
de su directorio de estado. Cada entrada contiene únicamente fecha y hora, ID
del chat, duración y estado de Teams, Discord y voz. Los errores se recortan y
los webhooks se redactan; nunca se guardan el prompt, la respuesta, el proyecto
ni la URL del webhook.

Windows:

```text
%LOCALAPPDATA%\CodexNotifier\logs\notifier-AAAA-MM-DD.jsonl
```

Linux:

```text
$XDG_STATE_HOME/codex-notifier/logs/notifier-AAAA-MM-DD.jsonl
```

Si `XDG_STATE_HOME` no existe, se usa
`~/.local/state/codex-notifier/logs`. `retention_days` indica cuántos archivos
diarios se conservan, entre 1 y 365. La limpieza se realiza al escribir una
nueva entrada y puede desactivarse todo el log con `logging.enabled=false`.

## Probar la voz

Windows:

```powershell
python .\notifier.py voice-test es
python .\notifier.py voice-test en
```

Linux:

```bash
python3 ./notifier.py voice-test es
python3 ./notifier.py voice-test en
```

Estos comandos reproducen sonido. Las pruebas automatizadas no reproducen audio
ni llaman al webhook.

## Variables de entorno opcionales

El archivo JSON tiene prioridad. Si una propiedad no existe, se admiten estos
fallbacks:

| Variable | Predeterminado | Uso |
| --- | ---: | --- |
| `CODEX_TEAMS_WEBHOOK_URL` | vacío | Webhook alternativo |
| `CODEX_NOTIFIER_TEAMS_ENABLED` | según exista webhook | Activa Teams |
| `CODEX_NOTIFIER_TEAMS_MIN_SECONDS` | `300` | Umbral de Teams |
| `CODEX_NOTIFIER_TEAMS_NOTIFY_UNKNOWN_DURATION` | `0` | Teams sin duración conocida |
| `CODEX_DISCORD_WEBHOOK_URL` | vacío | Webhook alternativo de Discord |
| `CODEX_NOTIFIER_DISCORD_ENABLED` | según exista webhook | Activa Discord |
| `CODEX_NOTIFIER_DISCORD_MIN_SECONDS` | `300` | Umbral de Discord |
| `CODEX_NOTIFIER_DISCORD_NOTIFY_UNKNOWN_DURATION` | `0` | Discord sin duración conocida |
| `CODEX_NOTIFIER_VOICE_ENABLED` | `1` | Activa la voz |
| `CODEX_NOTIFIER_VOICE_MIN_SECONDS` | `30` | Umbral de voz |
| `CODEX_NOTIFIER_VOICE_NOTIFY_UNKNOWN_DURATION` | `1` | Voz sin duración conocida |
| `CODEX_NOTIFIER_QUIET_START` | `23:00` | Inicio del silencio |
| `CODEX_NOTIFIER_QUIET_END` | `07:00` | Fin del silencio |
| `CODEX_NOTIFIER_STATE_DIR` | dependiente del SO | Estado temporal |
| `CODEX_NOTIFIER_CONFIG` | dependiente del SO | Ruta de configuración |

## Pruebas

```powershell
python -m unittest discover -s tests -v
```

## Funcionamiento

`UserPromptSubmit` registra la hora de inicio y `notify` recibe el evento
`agent-turn-complete`. Ambos se correlacionan mediante `turn_id`; al terminar se
calcula la duración, se consume el marcador y se aplican los canales y el
horario configurados. Si el marcador no existe, cada canal aplica su opción
`notify_when_duration_unknown`.
