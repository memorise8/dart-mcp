import os
from typing import Final

from dotenv import load_dotenv

load_dotenv()

API_KEY: Final[str] = os.environ.get("DART_API_KEY", "") or os.environ.get("dart_api", "")
BASE_URL: Final[str] = "https://opendart.fss.or.kr/api"
