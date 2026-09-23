# Codex Notifier

Notificador pequeño para Codex en Windows. Envía una tarjeta a Microsoft Teams
solo cuando un turno supera un tiempo mínimo y puede reproducir un aviso por voz
con la voz integrada de Windows.

No requiere paquetes de `pip`:

- Python 3 y su biblioteca estándar.
- Windows PowerShell/SAPI para la voz.
- `mise` solo en los comandos de instalación incluidos, porque es el gestor de
  Python usado en esta máquina. El script no depende de `mise`.

## Comportamiento predeterminado

- Menos de 2 minutos: no notifica.
- Desde 2 minutos: envía Teams si hay un webhook en el archivo local o en
  `CODEX_TEAMS_WEBHOOK_URL`.
- Desde 5 minutos: además reproduce un mensaje por voz.
- Entre las 22:00 y las 08:00: silencia únicamente la voz.
- Selecciona español o inglés según el mensaje final y usa una voz instalada de
  ese idioma.
- Guarda solo el identificador del turno, la hora, el identificador de sesión y
  el directorio. Nunca guarda el prompt.
- El marcador se consume al finalizar, evitando duplicar el mismo turno.

## Funcionamiento

El hook `UserPromptSubmit` ejecuta:

```powershell
mise exec -- python .\notifier.py record-start
```

Codex entrega el JSON del hook por `stdin`. Al completar el turno, `notify`
ejecuta:

```powershell
mise exec -- python .\notifier.py notify '<json-de-codex>'
```

El script correlaciona ambos eventos mediante `turn_id`, calcula la duración y
decide qué canales usar.

## Instalación

Desde PowerShell:

```powershell
.\install.ps1
```

El instalador:

1. Crea una copia con fecha de `~/.codex/config.toml`.
2. Actualiza la propiedad `notify` para apuntar a este repositorio.
3. Crea `~/.codex/hooks.json` con el hook `UserPromptSubmit`.

Por seguridad, se detiene si ya existe `hooks.json`, para no sobrescribir hooks
ajenos. Después de instalar, abre un nuevo chat y usa `/hooks` para revisar y
confiar en el hook. Codex no ejecuta hooks locales nuevos hasta que se aprueban.

Genera el archivo local de configuración con:

```powershell
.\configure.ps1
```

El comando migra automáticamente `CODEX_TEAMS_WEBHOOK_URL`, si está definida, a
`%LOCALAPPDATA%\CodexNotifier\config.json`. Ese archivo está fuera del
repositorio y no se versiona. Puedes editarlo directamente para cambiar el
webhook, los tiempos, el horario o las voces. `config.example.json` contiene la
estructura completa sin credenciales.

El webhook es una credencial. No compartas ni añadas el archivo local a Git.

## Configuración

Archivo predeterminado:

```text
%LOCALAPPDATA%\CodexNotifier\config.json
```

Ejemplo:

```json
{
  "teams": {
    "enabled": true,
    "minimum_seconds": 120,
    "webhook_url": "https://tu-webhook"
  },
  "voice": {
    "enabled": true,
    "minimum_seconds": 300,
    "quiet_start": "22:00",
    "quiet_end": "08:00",
    "language": "auto",
    "spanish_voice": "Microsoft Helena Desktop",
    "english_voice": "Microsoft Zira Desktop"
  }
}
```

`language` admite `auto`, `es` o `en`. En `auto`, el idioma se estima usando el
mensaje final. Si la voz indicada no existe, se elige otra voz instalada del
mismo idioma y finalmente la voz predeterminada de Windows.

Las variables de entorno anteriores siguen disponibles como fallback cuando un
valor no aparece en el archivo:

| Variable | Predeterminado | Uso |
| --- | ---: | --- |
| `CODEX_TEAMS_WEBHOOK_URL` | vacío | Fallback del webhook |
| `CODEX_NOTIFIER_TEAMS_ENABLED` | `1` | `0` desactiva Teams |
| `CODEX_NOTIFIER_TEAMS_MIN_SECONDS` | `120` | Umbral de Teams |
| `CODEX_NOTIFIER_VOICE_ENABLED` | `1` | `0` desactiva voz |
| `CODEX_NOTIFIER_VOICE_MIN_SECONDS` | `300` | Umbral de voz |
| `CODEX_NOTIFIER_QUIET_START` | `22:00` | Inicio del silencio |
| `CODEX_NOTIFIER_QUIET_END` | `08:00` | Fin del silencio |
| `CODEX_NOTIFIER_STATE_DIR` | `%LOCALAPPDATA%\CodexNotifier` | Estado temporal |
| `CODEX_NOTIFIER_CONFIG` | `%LOCALAPPDATA%\CodexNotifier\config.json` | Ruta alternativa del archivo |

Los valores se pueden definir para el usuario con
`[Environment]::SetEnvironmentVariable(...)`. Reinicia Codex después de cambiar
variables de entorno persistentes.

Para probar exclusivamente cada voz, de forma explícita:

```powershell
mise exec -- python .\notifier.py voice-test es
mise exec -- python .\notifier.py voice-test en
```

Este comando sí reproduce sonido. Las pruebas automatizadas nunca llaman al
webhook ni reproducen audio.

## Pruebas

```powershell
mise exec -- python -m unittest discover -s tests -v
```

## Limitaciones intencionales

No se intenta interpretar si el texto final representa éxito o error, porque el
payload de `notify` no proporciona un estado estructurado. Tampoco se inspecciona
la ventana en primer plano: el filtro temporal es más predecible y mantiene el
proyecto pequeño y replicable.
