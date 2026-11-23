"""Convert Pydantic models into Groq strict structured-output schemas.

Reasoning models such as `openai/gpt-oss-120b` interleave a reasoning trace
with their answer, which makes plain `{"type": "json_object"}` mode return
`json_validate_failed`. Strict `json_schema` mode instead uses constrained
decoding, so the model *cannot* emit anything that violates the schema.

Strict mode imposes rules Pydantic does not follow by default:

* every object must set ``additionalProperties: false``
* every property must appear in ``required`` -- optionality is expressed by
  allowing ``null`` in the type instead
* keywords like ``default`` are not accepted
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

# Validation keywords Groq's strict parser rejects or ignores.
_STRIP = {"default", "examples", "$comment", "deprecated", "readOnly", "writeOnly"}

# Constraints that are meaningful to us but unsupported in strict mode; the
# Pydantic model still enforces them after parsing, so dropping them is safe.
_STRIP_CONSTRAINTS = {
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "minLength", "maxLength", "pattern", "minItems", "maxItems",
    "multipleOf", "uniqueItems",
}


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    """Allow null for a field that strict mode forces into `required`."""
    if "anyOf" in schema:
        if not any(sub.get("type") == "null" for sub in schema["anyOf"]):
            schema["anyOf"] = [*schema["anyOf"], {"type": "null"}]
        return schema

    kind = schema.get("type")
    if isinstance(kind, str) and kind != "null":
        schema["type"] = [kind, "null"]
    elif isinstance(kind, list) and "null" not in kind:
        schema["type"] = [*kind, "null"]
    elif kind is None and ("$ref" in schema or "enum" in schema):
        # A bare $ref or enum cannot carry a null type; wrap it.
        inner = {k: v for k, v in schema.items() if k not in ("description",)}
        description = schema.get("description")
        schema.clear()
        schema["anyOf"] = [inner, {"type": "null"}]
        if description:
            schema["description"] = description
    return schema


def _walk(node: Any, *, required_originally: set[str] | None = None) -> Any:
    if isinstance(node, list):
        return [_walk(item) for item in node]
    if not isinstance(node, dict):
        return node

    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _STRIP or key in _STRIP_CONSTRAINTS:
            continue
        out[key] = value

    # $defs must be walked whatever else this node is: Groq validates every
    # definition, and a nested model reached only through a $ref would
    # otherwise keep Pydantic's permissive defaults.
    if isinstance(out.get("$defs"), dict):
        out["$defs"] = {k: _walk(v) for k, v in out["$defs"].items()}

    for key in ("items", "not", "contains", "propertyNames"):
        if isinstance(out.get(key), dict):
            out[key] = _walk(out[key])
    for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
        if isinstance(out.get(key), list):
            out[key] = [_walk(sub) for sub in out[key]]

    properties = out.get("properties")
    if isinstance(properties, dict):
        was_required = set(out.get("required") or [])
        new_props: dict[str, Any] = {}
        for name, sub in properties.items():
            walked = _walk(sub)
            if name not in was_required:
                walked = _nullable(walked)
            new_props[name] = walked
        out["properties"] = new_props
        # Strict mode: every property must be listed as required.
        out["required"] = list(new_props)
        out["additionalProperties"] = False
        out.setdefault("type", "object")
    else:
        # A dict-typed field (e.g. dict[str, Model]) has open keys. Walk the
        # value schema, but leave the object itself permissive.
        if isinstance(out.get("additionalProperties"), dict):
            out["additionalProperties"] = _walk(out["additionalProperties"])
        elif out.get("type") == "object":
            out.setdefault("additionalProperties", True)

    return out


def strict_json_schema(model_cls: type[BaseModel]) -> dict[str, Any]:
    """Groq `json_schema` payload for `model_cls`, in strict form."""
    schema = _walk(model_cls.model_json_schema())
    return {
        "name": model_cls.__name__,
        "schema": schema,
        "strict": True,
    }


def supports_strict_dicts(model_cls: type[BaseModel]) -> bool:
    """False when the model has an open-ended dict that strict mode rejects.

    `BatchFileReviewOut.reviews` is a `dict[str, FileReviewOut]` with arbitrary
    keys; strict constrained decoding cannot express that, so those calls fall
    back to plain JSON-object mode.
    """
    schema = model_cls.model_json_schema()
    for prop in (schema.get("properties") or {}).values():
        if prop.get("type") == "object" and "properties" not in prop:
            return False
        if isinstance(prop.get("additionalProperties"), dict):
            return False
    return True
