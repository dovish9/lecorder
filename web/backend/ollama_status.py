"""Availability checks shared by transcription and note jobs."""
import requests
from .config import OLLAMA_URL, DEFAULT_LLM_MODEL

def ollama_ready(model: str) -> tuple[bool, bool]:
    try:
        response = requests.get(OLLAMA_URL.rsplit("/", 1)[0] + "/tags", timeout=(1, 3))
        response.raise_for_status()
        installed = {str(item.get("name") or item.get("model") or "") for item in response.json().get("models", [])}
        return True, model in installed
    except (requests.RequestException, ValueError, AttributeError):
        return False, False


def require_ollama(model=DEFAULT_LLM_MODEL):
    online, installed = ollama_ready(model)
    if not online:
        raise RuntimeError("Ollama 서버에 연결할 수 없어 작업을 실패 처리했습니다. 서버를 켠 뒤 다시 실행하세요.")
    if not installed:
        raise RuntimeError(f"Ollama에 {model} 모델이 없습니다. 모델을 설치한 뒤 다시 실행하세요.")
