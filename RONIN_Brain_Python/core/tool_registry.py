from __future__ import annotations

import importlib
import inspect
import json
import types
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints


TOOLS_DIRECTORY = Path(__file__).resolve().parent.parent / "tools"


def _is_temporary(path: Path) -> bool:
    name = path.name
    stem = path.stem
    return (
        name.startswith(".")
        or stem.startswith(("tmp", "temp"))
        or name.endswith(".tmp.py")
        or name.endswith(".bak.py")
        or name.endswith("~")
    )


def _load_modules() -> list[ModuleType]:
    modules: list[ModuleType] = []
    if not TOOLS_DIRECTORY.is_dir():
        return modules

    for path in sorted(TOOLS_DIRECTORY.glob("*.py")):
        if path.name == "__init__.py" or _is_temporary(path):
            continue
        module_name = f"tools.{path.stem}"
        try:
            module = importlib.import_module(module_name)
            module = importlib.reload(module)
        except Exception:
            # A broken tool must not make the whole Brain unavailable.
            continue
        modules.append(module)
    return modules


def _json_type(annotation: Any) -> dict[str, Any]:
    if annotation in (inspect.Parameter.empty, Any):
        return {}
    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is Literal:
        values = list(args)
        if not values:
            return {}
        schema = _json_type(type(values[0]))
        schema["enum"] = values
        return schema

    if origin in (Union, types.UnionType):
        non_none = [item for item in args if item is not type(None)]
        if len(non_none) == 1:
            schema = _json_type(non_none[0])
            schema["nullable"] = True
            return schema
        return {"anyOf": [_json_type(item) for item in non_none]}

    if origin in (list, tuple, set, frozenset):
        return {"type": "array", "items": _json_type(args[0] if args else Any)}
    if origin is dict:
        return {"type": "object"}
    if annotation is str:
        return {"type": "string"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation in (dict,):
        return {"type": "object"}
    if annotation in (list, tuple, set, frozenset):
        return {"type": "array"}
    return {}


def _function_schema(function: Any) -> dict[str, Any] | None:
    try:
        signature = inspect.signature(function)
        try:
            hints = get_type_hints(function)
        except Exception:
            hints = {}
    except Exception:
        return None

    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, parameter in signature.parameters.items():
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            continue
        schema = _json_type(hints.get(name, parameter.annotation))
        properties[name] = schema
        if parameter.default is inspect.Parameter.empty:
            required.append(name)

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        parameters["required"] = required
    return {
        "type": "function",
        "function": {
            "name": function.__name__,
            "description": inspect.getdoc(function) or f"Execute {function.__name__}.",
            "parameters": parameters,
        },
    }


def _available_functions() -> dict[str, Any]:
    functions: dict[str, Any] = {}
    for module in _load_modules():
        for name, function in inspect.getmembers(module, inspect.isfunction):
            if name.startswith("_") or function.__module__ != module.__name__:
                continue
            functions.setdefault(name, function)
    return functions


def get_available_tools() -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for function in _available_functions().values():
        schema = _function_schema(function)
        if schema is not None:
            tools.append(schema)
    return tools


def execute_tool(function_name: str, arguments: Any) -> str:
    function = _available_functions().get(function_name)
    if function is None:
        return json.dumps({"error": f"Tool not found: {function_name}"}, ensure_ascii=False)

    try:
        if isinstance(arguments, str):
            arguments = json.loads(arguments) if arguments.strip() else {}
        if not isinstance(arguments, dict):
            return json.dumps({"error": "Tool arguments must be a JSON object."}, ensure_ascii=False)
        result = function(**arguments)
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as exc:
        return json.dumps({"error": f"Tool execution failed: {exc}"}, ensure_ascii=False)
