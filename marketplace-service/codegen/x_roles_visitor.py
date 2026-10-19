"""fastapi-codegen visitor: exposes the `x-roles` extension of every operation to templates."""
from pathlib import Path
from typing import Dict

from fastapi_code_generator.parser import OpenAPIParser


def visit(parser: OpenAPIParser, model_path: Path) -> Dict[str, object]:
    roles: Dict[str, list] = {}
    for path_item in (parser.raw_obj.get("paths") or {}).values():
        for operation in path_item.values():
            if isinstance(operation, dict) and "x-roles" in operation:
                roles[operation["operationId"]] = list(operation["x-roles"])
    return {"x_roles": roles}
