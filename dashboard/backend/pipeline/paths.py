from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
STORAGE_ROOT = ROOT / "dashboard_storage"
RUNS_ROOT = STORAGE_ROOT / "runs"
RUNTIME_ROOT = ROOT / "runtime"
ENV_PATH = ROOT / ".env.dashboard"

DEFAULT_PRODUCT_MASTER = ROOT / "input" / "docs" / "product master doc.txt"
DEFAULT_IMAGE_SOURCES_FILE = ROOT / "input" / "image_sources.txt"
LEGACY_ACTIVE_IMAGES_FILE = ROOT / "input" / "activeimages.txt"
INPUT_IMAGES_DIR = ROOT / "input" / "images"
GENERATED_IMAGES_ROOT = ROOT / "generated_images"
CONVERT_916_TEMPLATE_PATH = ROOT / "input" / "prompt_916_from_45.txt"
PERSONA_SEEDS_PATH = ROOT / "persona_seeds.json"
COPY_ARCH_PATH = ROOT / "dashboard" / "backend" / "copy_architecture.json"
COPY_PROMPTS_PATH = ROOT / "dashboard" / "backend" / "copy_prompt_templates.json"
STARTING_PROMPT_PATH = ROOT / "input" / "startingprompt.txt"
