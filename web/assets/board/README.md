# Web board artwork

These assets are bundled with the project so the browser client never fetches
artwork from a third-party host at runtime.

- `terrain-wood.webp`, `terrain-sheep.webp`, `terrain-wheat.webp`,
  `terrain-brick.webp`, and `terrain-ore.webp` are Web-optimized crops of the
  project-owned generated atlas at
  `material/ChatGPT Image 2026年3月21日 16_16_06.png`.
- `terrain-desert.webp` and `ocean.webp` were generated for this project with
  OpenAI's built-in image generation tool on 2026-07-15. The prompts requested
  original hand-painted board-game artwork with no text, logo, trademark,
  watermark, or resemblance to branded board-game art.

The original generated files remain outside this folder. These WebP files are
the optimized runtime assets and should be referenced only through the exact
static routes declared in `python/game/web_server.py`.
