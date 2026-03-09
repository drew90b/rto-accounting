import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:password@localhost:5432/rto_accounting"
)

STORAGE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "storage",
    "receipts"
)
