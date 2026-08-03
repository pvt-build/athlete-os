# 🔱 Athlete OS — dashboard live

Dashboard de performance físico de Patricio (Private Build), servido por GitHub Pages
y actualizado automáticamente **cada sábado AM** desde Notion.

- **Live:** https://pvt-build.github.io/athlete-os/
- **Datos:** las 4 bases del Athlete OS en Notion (Check-in, Comidas, Entreno, Running).
- **Cómo funciona:** `index.html` lee un bloque `<script id="athlete-data">`.
  `.privatebuild/sync.py` consulta Notion, recalcula todo (KPIs, pilares, macros,
  fuerza, volumen semanal, radar, hábitos) y reescribe ese bloque.
  El workflow `.github/workflows/notion-sync.yml` lo corre los sábados y commitea.

## ⚙️ Setup (una sola vez) — para que el sync automático funcione

El sitio ya se ve live con los datos de la carga inicial. Para que el job semanal
traiga datos frescos de Notion faltan 2 pasos manuales:

1. **Crear una integración interna en Notion** → https://www.notion.so/my-integrations
   - "New integration", tipo *Internal*, workspace de Patricio.
   - Copiar el *Internal Integration Secret* (empieza con `ntn_` o `secret_`).
2. **Compartir las 4 bases con la integración**: en Notion, abrir cada base
   (Check-in Diario, Comidas, Entreno, Running) → `•••` → *Connections* →
   agregar la integración creada.
3. **Guardar el token como secret del repo**:
   ```bash
   gh secret set NOTION_TOKEN -R pvt-build/athlete-os
   ```
   (pegar el secret cuando lo pida).

Luego, en la pestaña **Actions** del repo, correr el workflow a mano una vez
("Run workflow") para validar. Desde ahí corre solo cada sábado.

## Correr el sync localmente

```bash
NOTION_TOKEN=ntn_xxx python .privatebuild/sync.py
```

## Editar

- **Diseño / textos fijos / hipótesis:** editar `index.html` directo y hacer push.
- **Datos:** no se tocan a mano — los regenera `sync.py`.
