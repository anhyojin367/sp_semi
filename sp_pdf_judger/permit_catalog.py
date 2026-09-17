"""Product-scoped permit catalog and authoritative policy resolution."""
from __future__ import annotations
import hashlib, json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

@dataclass(frozen=True)
class PermitPolicy:
    policy_id: str
    authoritative: bool
    ignore_section_numbers: bool
    require_llm: bool
    ocr_mode: str

@dataclass(frozen=True)
class ResolvedPermit:
    policy: PermitPolicy | None
    paths: tuple[Path, ...]
    fingerprint: str
    errors: tuple[str, ...]
    catalog_valid: bool | None = None

def _normalize(value: str) -> str:
    return "".join(ch for ch in value.casefold() if ch.isalnum())

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def _fingerprint(entry: dict[str, Any] | None, paths: tuple[Path, ...], hashes: tuple[str, ...], catalog_valid: bool | None, catalog_hash: str | None) -> str:
    payload = {"entry": entry, "paths": [str(path) for path in paths], "hashes": list(hashes), "catalog_valid": catalog_valid, "catalog_hash": catalog_hash}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()

def resolve_permits(explicit_paths: Iterable[Path], company: str | None, product: str | None, package_dir: Path | None = None) -> ResolvedPermit:
    explicit: list[Path] = []
    seen: set[Path] = set()
    for raw_path in explicit_paths:
        path = Path(raw_path).resolve()
        if path.is_file() and path not in seen:
            explicit.append(path); seen.add(path)
    root = (Path(package_dir) if package_dir is not None else Path(__file__).parent).resolve()
    catalog_path = root / "permits" / "catalog.json"
    entry: dict[str, Any] | None = None
    policy: PermitPolicy | None = None
    errors: list[str] = []
    if catalog_path.is_file() and company is not None and product is not None:
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            company_key, product_key = _normalize(company), _normalize(product)
            for candidate in catalog.get("permits", []):
                if company_key in {_normalize(str(a)) for a in candidate.get("companies", [])} and product_key in {_normalize(str(a)) for a in candidate.get("products", [])}:
                    entry = candidate; break
        except (OSError, json.JSONDecodeError, AttributeError) as exc:
            errors.append(f"catalog error: {exc}")
    resolved = list(explicit)
    catalog_valid: bool | None = None
    catalog_hash: str | None = None
    if entry is not None:
        policy = PermitPolicy(str(entry["policy_id"]), bool(entry["authoritative"]), bool(entry["ignore_section_numbers"]), bool(entry["require_llm"]), str(entry["ocr_mode"]))
        catalog_file = (root / str(entry["path"])).resolve()
        catalog_valid = False
        if not catalog_file.is_file():
            errors.append(f"catalog file missing: {catalog_file}")
        else:
            actual_hash = _sha256(catalog_file)
            catalog_hash = actual_hash
            if actual_hash.casefold() != str(entry.get("sha256", "")).casefold():
                errors.append(f"catalog sha256 mismatch for {catalog_file}")
            else:
                catalog_valid = True
                if catalog_file not in seen:
                    resolved.append(catalog_file)
        if not catalog_valid:
            resolved = [path for path in resolved if path != catalog_file]
    hashes = tuple(_sha256(path) for path in resolved)
    return ResolvedPermit(policy, tuple(resolved), _fingerprint(entry, tuple(resolved), hashes, catalog_valid, catalog_hash), tuple(errors), catalog_valid)
