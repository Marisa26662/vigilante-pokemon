# Vigilante Pokémon 30 aniversario

Lee canales públicos de Telegram de avisos de stock y me manda un email cuando
aparece algo que me interesa. También actualiza un panel privado con contraseña.

## Qué me notifica

Hay tres tipos de aviso. Un mismo mensaje puede activar más de uno.

| Aviso | Cuándo salta |
|---|---|
| **⭐ Ultra Premium** | Cualquier mensaje que hable de la Ultra Premium, en cualquier tienda (Amazon incluida) y en cualquier idioma. |
| **Preventa 30 aniversario inglés** | El mensaje habla del 30 aniversario, es una preventa/reserva/pre-order y dice que es en inglés ("inglés", "English", "ENG" o "(EN)"). Cualquier tienda o web. |
| **Preventa 30 aniversario sin idioma** | Igual que la anterior, pero el mensaje no indica ningún idioma. |

**No me avisa de:**

- Preventas del 30 aniversario que indiquen otro idioma (español, japonés, chino, coreano, francés, alemán, italiano o portugués).
- Reposiciones o stock que no sean preventa (salvo si es la Ultra Premium).
- Productos que no sean del 30 aniversario (salvo la Ultra Premium).

Nota: "El Corte Inglés" no cuenta como "inglés"; el script quita el nombre de la tienda antes de comprobar el idioma.

## Canales que lee

- [@pokestock_es](https://t.me/s/pokestock_es)
- [@stockTCGpokemon](https://t.me/s/stockTCGpokemon)

Solo sirven canales públicos con vista web. Para comprobar uno nuevo, abre
`https://t.me/s/NOMBRE` en el navegador: si se ven los mensajes, vale. Los grupos
(con "miembros" o "temas") no se pueden leer.

## Cómo funciona

- **cron-job.org** lanza el workflow cada minuto llamando a la API de GitHub
  (las ejecuciones programadas de GitHub son muy irregulares, por eso no se usan).
  Usa un token *fine-grained* de GitHub con permiso **Actions: Read and write**
  solo para este repositorio. **Cuando caduque, crear otro y cambiarlo en cron-job.org.**
- El estado (por dónde va leyendo cada canal y los avisos) se guarda cifrado en la
  caché de GitHub Actions, no en el repositorio.
- Panel: https://marisa26662.github.io/vigilante-pokemon/ (contraseña = secreto `PANEL_PASSWORD`).

## Secretos (Settings → Secrets and variables → Actions)

| Secreto | Qué es |
|---|---|
| `EMAIL_USER` | Mi Gmail |
| `EMAIL_PASSWORD` | Contraseña de aplicación de Gmail (myaccount.google.com/apppasswords) |
| `EMAIL_TO` | Correo donde llegan los avisos |
| `PANEL_PASSWORD` | Contraseña del panel |

## Cambiar lo que se vigila

Todo está en la sección **CONFIGURACIÓN** al principio de `vigilante.py`:

- `CANALES`: canales de Telegram.
- `PATRON_ULTRA`, `PATRON_PREVENTA`, `PATRON_INGLES`, `PATRON_OTRO_IDIOMA`, `PATRON_30`: palabras que se buscan.
- `ULTRA_SOLO_INGLES`: ponerlo a `True` para que la Ultra Premium avise solo en inglés.
- `EXCLUIR`: palabras que hacen que un mensaje se ignore.

Al cambiar el filtro, los avisos antiguos del panel se borran solos.

## Probarlo

Actions → Vigilante Pokémon → **Run workflow** con la casilla de prueba marcada.
Llega un email "🧪 Prueba del vigilante de Telegram" con el estado de los canales.

## Recordatorios

- Antes de subir cambios: `git pull` (el vigilante también sube cambios al panel).
- Si en GitHub no aparece la pestaña **Settings** ni el botón **Run workflow**,
  estoy con la cuenta del trabajo: cambiar a **Marisa26662**.
- La carpeta del workflow tiene que ser exactamente `.github/workflows/`.
