"""Fail closed until authorizations have been reviewed in a separate change."""
import json
import os
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / "content" / "authorizations.json"

def require_authorization(production=False):
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    required = ["youtube_verified", "publication_approved"]
    switches = ["LRC_PUBLICATION_ENABLED"]
    if production:
        required += ["provider_apis_verified", "billing_limits_verified", "production_approved"]
        switches += ["LRC_PRODUCTION_ENABLED"]
    if any(config.get(key) is not True for key in required):
        raise RuntimeError("Autopilot bloqueado: autorizaciones pendientes de verificar")
    if any(os.environ.get(key) != "true" for key in switches):
        raise RuntimeError("Autopilot bloqueado: activación explícita pendiente")
