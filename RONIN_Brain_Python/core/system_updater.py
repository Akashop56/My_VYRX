from __future__ import annotations
import importlib, shutil, sys
from pathlib import Path

def safe_hot_reload(module_name: str, file_path: str, new_code: str) -> dict:
    target = Path(file_path).resolve()
    if (target.exists() and not target.is_file()) or target.suffix != ".py" or not new_code.strip():
        return {"success": False, "error": "Target must be a Python source file with non-empty code."}
    try: compile(new_code, str(target), "exec")
    except SyntaxError as exc: return {"success": False, "error": f"Syntax validation failed: {exc}"}
    backup = target.with_suffix(target.suffix + ".bak")
    existed = target.exists()
    if existed:
        shutil.copy2(target, backup)
    try:
        target.write_text(new_code, encoding="utf-8")
        importlib.invalidate_caches()
        module = importlib.import_module(module_name) if module_name not in sys.modules else importlib.reload(sys.modules[module_name])
        return {"success": True, "module": module.__name__, "backup": str(backup) if existed else None}
    except Exception as exc:
        if existed:
            shutil.copy2(backup, target)
        else:
            target.unlink(missing_ok=True)
        importlib.invalidate_caches()
        try:
            if module_name in sys.modules: importlib.reload(sys.modules[module_name])
        except Exception: pass
        return {"success": False, "error": str(exc), "restored": True, "backup": str(backup) if existed else None}
