from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
STORAGE_ROOT = ROOT / "dashboard_storage"
RUNS_ROOT = STORAGE_ROOT / "runs"
RUNTIME_ROOT = ROOT / "runtime"
ENV_PATH = ROOT / ".env.dashboard"
DEFAULTS_DIR = ROOT / "dashboard" / "backend" / "defaults"

DEFAULT_PRODUCT_MASTER = DEFAULTS_DIR / "product_master_doc.txt"
DEFAULT_IMAGE_SOURCES_FILE = RUNTIME_ROOT / "image_sources.txt"
LEGACY_ACTIVE_IMAGES_FILE = RUNTIME_ROOT / "activeimages.txt"
INPUT_IMAGES_DIR = RUNTIME_ROOT / "input_images"
GENERATED_IMAGES_ROOT = ROOT / "generated_images"
CONVERT_916_TEMPLATE_PATH = DEFAULTS_DIR / "conversion_916_prompt.txt"
PERSONA_SEEDS_PATH = ROOT / "persona_seeds.json"
COPY_ARCH_PATH = ROOT / "dashboard" / "backend" / "copy_architecture.json"
COPY_PROMPTS_PATH = ROOT / "dashboard" / "backend" / "copy_prompt_templates.json"
STARTING_PROMPT_PATH = DEFAULTS_DIR / "starting_prompt.txt"
REFERENCE_STARTING_PROMPT_PATH = DEFAULTS_DIR / "reference_starting_prompt.txt"

FORMATS = ["HERO", "BA", "TEST", "FEAT", "UGC"]
DEFAULT_GOOGLE_API_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GOOGLE_MODEL = "gemini-2.0-flash"
OPENCODE_ADS_PER_SESSION_SCHEDULE = [25, 15, 10, 5, 2, 1]
OPENCODE_AD_TIMEOUT_SECONDS = 600
OPENCODE_MAX_CONCURRENT = 5
OPENCODE_QUEUE_DIR = RUNTIME_ROOT / "opencode_queue"
LLM_TRACES_DIR = RUNTIME_ROOT / "llm_traces"
INPUT_ROOT = DEFAULTS_DIR
