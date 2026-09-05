#!/usr/bin/env python3
"""Lee el XLSX orientativo, sin modificarlo; genera recorridos independientes de los PDF.

No descarga datos ni ejecuta fórmulas. Usa los valores guardados en el XLSX y
las demoras como duraciones estimadas. La hora de salida del Excel nunca se
publica como salida oficial. Requiere únicamente la biblioteca estándar.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
ALIASES = {
    "CARLOS PAZ": "VILLA CARLOS PAZ", "MONTE CRISTO": "MONTECRISTO",
    "CERRO AZUL": "VILLA CERRO AZUL", "LA BOLSA": "VILLA LA BOLSA",
    "LOS AROMOS": "VILLA LOS AROMOS", "MALVINAS": "MALVINAS ARGENTINAS",
    "SAN BARTOLOME": "COLONIA SAN BARTOLOME", "HUINCA": "HUINCA RENANCO",
    "HOLMBERG": "SANTA CATALINA HOLMBERG", "SAN MARCOS SIERRAS": "SAN MARCOS SIERRA",
    "SAN GERONIMO": "SAN JERONIMO", "LA TRAVESIA": "TRAVESIA",
    "PUNTA DE AGUA": "PUNTA DEL AGUA", "EL ESQUINAZO": "ESQUINAZO",
    "VILLA DE MARIA DE RIO SECO": "VILLA DE MARIA",
    "VILLA CURA CURA BROCHERO": "VILLA CURA BROCHERO",
}

# Equivalencias confirmadas donde el nombre oficial de la línea ya determina
# la variante, aunque el PDF semanal deje vacía la columna de ruta. Se exige
# corredor, CUIT, línea y variante exactos para no unir recorridos por parecido.
IMPLICIT_VARIANTS = {
    ("PUNILLA", "30707307818", "CORDOBA MALAGUENO CARLOS PAZ", "COLECTORA"),
}


def norm(value):
    value = unicodedata.normalize("NFD", str(value or ""))
    return re.sub(r"[^A-Z0-9]+", " ", "".join(c for c in value if unicodedata.category(c) != "Mn").upper()).strip()


def canonical(value):
    text = norm(value)
    return ALIASES.get(text, text)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def cuit(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if len(digits) == 11 else ""


def corridor(value):
    return "ESTE-SUDESTE" if norm(value) == "ESTE SUDESTE" else norm(value)


def signature(s):
    nodes = s["nodes"] if s["direction"] == "I" else list(reversed(s["nodes"]))
    return json.dumps([norm(s["corridor"]), cuit(s["cuit"]), norm(s["modality"]), norm(s["line"]), s["direction"], norm(s.get("route")), [canonical(n) for n in nodes]], ensure_ascii=False, separators=(",", ":"))


def read_xlsx(path):
    """Valores guardados, sin expandir formato a filas vacías ni ejecutar vínculos."""
    with zipfile.ZipFile(path) as z:
        strings = []
        if "xl/sharedStrings.xml" in z.namelist():
            strings = ["".join(t.text or "" for t in si.findall(".//s:t", NS)) for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("s:si", NS)]
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = {r.attrib["Id"]: r.attrib["Target"] for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
        sheets = []
        for sheet in wb.findall("s:sheets/s:sheet", NS):
            target = rels[sheet.attrib[f"{{{REL}}}id"]]
            target = target.lstrip("/") if target.startswith("/") else "xl/" + target
            root = ET.fromstring(z.read(target))
            rows = []
            for row in root.findall("s:sheetData/s:row", NS):
                number = int(row.attrib["r"])
                if number == 1:
                    continue
                values = [None] * 11
                for cell in row.findall("s:c", NS):
                    letters = re.match(r"[A-Z]+", cell.attrib["r"])[0]
                    column = 0
                    for letter in letters:
                        column = column * 26 + ord(letter) - 64
                    if column > 11:
                        continue
                    v = cell.find("s:v", NS)
                    if cell.attrib.get("t") == "inlineStr":
                        value = "".join(t.text or "" for t in cell.findall(".//s:t", NS))
                    elif v is None or v.text is None:
                        continue
                    elif cell.attrib.get("t") == "s":
                        value = strings[int(v.text)]
                    elif cell.attrib.get("t") in ("str", "e"):
                        value = v.text
                    else:
                        value = float(v.text)
                        if value.is_integer():
                            value = int(value)
                    values[column - 1] = value
                if any(v is not None and v != "" for v in values):
                    rows.append((number, values))
            sheets.append((sheet.attrib["name"], rows))
        core = ET.fromstring(z.read("docProps/core.xml"))
        modified = core.find("{http://purl.org/dc/terms/}modified")
        return sheets, modified.text if modified is not None else None


def minutes(value):
    return round(value * 1440) if isinstance(value, (float, int)) and value >= 0 else None


def make_data(xlsx, schedule, geography, georef_paths, existing_routes=None):
    sheets, modified = read_xlsx(xlsx)
    places, raw_profiles, skipped = {}, [], []
    for sheet, rows in sheets:
        group = None
        for row, v in rows:
            if v[5] not in ("I", "V") or not v[4] or not v[8]:
                skipped.append({"sheet": sheet, "row": row, "reason": "Falta sentido, línea o localidad"})
                if group:
                    group["issues"].append("Fila incompleta contigua: revisar cierre de recorrido")
                group = None
                continue
            source_id = str(v[9]) if isinstance(v[9], int) else None
            # ID 231 aparece asignado a La Estancia y Estancia de Guadalupe.
            # Se conservan separados; no se deduce que sean el mismo lugar.
            pid = "loc-" + (source_id if source_id and source_id != "231" else digest([corridor(sheet), canonical(v[8])]))
            p = places.setdefault(pid, {"id": pid, "name": str(v[8]).strip(), "source_id": source_id, "aliases": [], "corridors": [], "lat": None, "lon": None, "geo_source": None})
            if str(v[8]).strip() not in p["aliases"]:
                p["aliases"].append(str(v[8]).strip())
            if corridor(sheet) not in p["corridors"]:
                p["corridors"].append(corridor(sheet))
            key = [corridor(sheet), norm(v[1]), cuit(v[2]), norm(v[3]), norm(v[4]), v[5]]
            if not group or group["key"] != key or v[7] is None:
                group = {"key": key, "corridor": corridor(sheet), "company": v[1], "cuit": cuit(v[2]), "modality": v[3], "line": v[4].strip(), "direction": v[5], "source_sheet": sheet, "source_rows": [], "stops": [], "notes": [], "observations": [], "issues": []}
                if v[7] is not None:
                    group["issues"].append("Inicio sin demora vacía: anclaje de salida incierto")
                if not group["cuit"]:
                    group["issues"].append("Empresa/CUIT sin informar")
                raw_profiles.append(group)
            delay = 0 if not group["stops"] else minutes(v[7])
            if delay is None:
                group["issues"].append("Demora faltante o inválida")
            previous = group["stops"][-1]["departure_offset"] if group["stops"] else 0
            offset = previous + (delay or 0)
            stop = {"place_id": pid, "arrival_offset": offset, "departure_offset": offset, "rows": [row]}
            if group["stops"] and group["stops"][-1]["place_id"] == pid:
                # La hoja no etiqueta llegada/salida. No inferir una permanencia
                # ni elegir uno de dos horarios de la misma localidad.
                group["issues"].append("Localidad consecutiva repetida: revisar significado de ambos horarios")
            group["stops"].append(stop)
            group["source_rows"].append(row)
            if v[10] and str(v[10]).strip() not in group["notes"]:
                group["notes"].append(str(v[10]).strip())
            if v[10]:
                group["observations"].append({"text": str(v[10]).strip(), "place_id": pid, "row": row})
            if v[10] and "ALGUNOS HORARIOS" in norm(v[10]):
                group["issues"].append("Variante condicionada a horarios no identificados")

    # Coordenadas: reutilizar cabeceras o coincidencia única de nombre. No fuzzy matching.
    by_name_ids = defaultdict(set)
    for p in places.values():
        for name in p["aliases"]:
            by_name_ids[canonical(name)].add(p["id"])
    seed = {canonical(k): (k, v) for k, v in geography["locations"].items()}
    georef = defaultdict(list)
    for path in georef_paths:
        content = json.loads(path.read_text())
        for p in content.get("localidades", content.get("asentamientos", [])):
            key = canonical(p["nombre"])
            if not any(x["id"] == p["id"] for x in georef[key]):
                georef[key].append(p)
    for p in places.values():
        names = {canonical(n) for n in p["aliases"]}
        old = (existing_routes or {}).get("places", {}).get(p["id"])
        if old and old.get("lat") is not None and names.intersection(canonical(n) for n in old.get("aliases", []) + [old["name"]]):
            for field in ("name", "lat", "lon", "geo_source", "geo_id"):
                if field in old:
                    p[field] = old[field]
            continue
        # Homónimos con distintos IDs se dejan pendientes en lugar de ubicarlos mal.
        if any(len(by_name_ids[n]) > 1 for n in names):
            continue
        known = [seed[n] for n in sorted(names) if n in seed]
        if known:
            label, geo = known[0]
            p.update(name=label, lat=geo["lat"], lon=geo["lon"], geo_source=geo["source"])
            continue
        candidates = {q["id"]: q for n in names for q in georef[n]}
        if len(candidates) == 1:
            geo = next(iter(candidates.values()))
            p.update(name=geo["nombre"], lat=geo["centroide"]["lat"], lon=geo["centroide"]["lon"], geo_source="Georef Argentina · coincidencia única de nombre", geo_id=geo["id"])

    profiles, fingerprints = [], {}
    for p in raw_profiles:
        if len(p["stops"]) < 2:
            p["issues"].append("Menos de dos localidades")
        p["issues"] = sorted(set(p["issues"]))
        fingerprint = [p["key"], [[s["place_id"], s["arrival_offset"], s["departure_offset"]] for s in p["stops"]], [[o["text"], o["place_id"]] for o in p["observations"]], p["issues"]]
        p["id"] = "rec-" + digest(fingerprint)
        if p["id"] in fingerprints:
            fingerprints[p["id"]]["duplicate_source_rows"].append(p["source_rows"])
            continue
        p.pop("key")
        p["duplicate_source_rows"] = []
        profiles.append(p)
        fingerprints[p["id"]] = p

    # El enlace es por identidad de variante, nunca por hora ni ID del servicio.
    # Si un PDF futuro cambia empresa, ruta o línea, queda pendiente por diseño.
    bindings, pending, reasons = {}, [], Counter()
    signatures = defaultdict(list)
    for s in schedule["services"]:
        signatures[signature(s)].append(s)
    by_corridor = {c: {"services": 0, "linked": 0} for c in schedule["corridors"]}
    for key, services in sorted(signatures.items()):
        s = services[0]
        nodes = s["nodes"] if s["direction"] == "I" else list(reversed(s["nodes"]))
        def stop_matches(stop, name):
            place = places[stop["place_id"]]
            return canonical(name) in {canonical(n) for n in [place["name"]] + place["aliases"]}
        candidates = [p for p in profiles if not p["issues"] and p["cuit"] == cuit(s["cuit"]) and p["cuit"] and p["corridor"] == s["corridor"] and norm(p["modality"]) == norm(s["modality"]) and p["direction"] == s["direction"] and stop_matches(p["stops"][0], nodes[0]) and stop_matches(p["stops"][-1], nodes[-1])]
        # Cabeceras/intermedias expresas en el nombre PDF deben respetar el orden.
        candidates = [p for p in candidates if ordered_nodes(p, nodes, stop_matches)]
        candidates = [p for p in candidates if route_compatible(s, p, places)]
        hinted = [p for p in candidates if variant_hint(p["line"]) and hint_matches(variant_hint(p["line"]), s.get("route", ""))]
        if hinted:
            candidates = hinted
        exact = [p for p in candidates if norm(p["line"]) == norm(s["line"])]
        if exact and not hinted:
            candidates = exact
        # Perfiles con la misma secuencia/tiempos son equivalentes aunque difiera el rótulo.
        distinct = {}
        for p in candidates:
            k = json.dumps([[[v["place_id"], v["arrival_offset"], v["departure_offset"]] for v in p["stops"]], [[o["text"], o["place_id"]] for o in p["observations"]]])
            distinct.setdefault(k, p)
        candidates = list(distinct.values())
        if len(candidates) == 1:
            bindings[key] = candidates[0]["id"]
            by_corridor[s["corridor"]]["linked"] += len(services)
        else:
            reason = "Más de un recorrido compatible" if candidates else "Sin coincidencia suficiente de empresa, modalidad, sentido, cabeceras y variante"
            reasons[reason] += len(services)
            pending.append({"signature": key, "corridor": s["corridor"], "company": s["company"], "line": s["line"], "direction": s["direction"], "route": s.get("route", ""), "modality": s["modality"], "services": len(services), "reason": reason, "candidates": [p["id"] for p in candidates]})
        by_corridor[s["corridor"]]["services"] += len(services)
    linked_ids = set(bindings.values())
    linked_places = {stop["place_id"] for p in profiles if p["id"] in linked_ids for stop in p["stops"]}
    return {
        "schema_version": 1,
        "source": {"filename": xlsx.name, "sha256": hashlib.sha256(xlsx.read_bytes()).hexdigest(), "workbook_modified_at": modified, "validity": "Vigente según confirmación del administrador; base orientativa, no cronograma semanal", "time_kind": "estimated_segment_durations"},
        "name_aliases": ALIASES,
        "geography_sources": geography.get("sources", []),
        "places": places, "profiles": profiles, "bindings": bindings,
        "audit": {"incomplete_rows": skipped, "pending_bindings": pending},
        "stats": {"source_rows": sum(len(r) for _, r in sheets), "profiles": len(profiles), "places": len(places), "geolocated_places": sum(p["lat"] is not None for p in places.values()), "baseline_services": len(schedule["services"]), "linked_services": sum(v["linked"] for v in by_corridor.values()), "linked_profiles": len(linked_ids), "linked_places": len(linked_places), "linked_places_unlocated": sum(places[p]["lat"] is None for p in linked_places), "by_corridor": by_corridor},
    }


def report(data):
    stats = data["stats"]
    def cell(value):
        return str(value or "—").replace("|", "/").replace("\n", " ")
    lines = ["# Revisión de recorridos v5", "", "## Alcance", "",
        f"- Base: {data['source']['filename']}.",
        f"- Filas de origen: {stats['source_rows']}; perfiles orientativos: {stats['profiles']}.",
        f"- Servicios del cronograma de referencia: {stats['baseline_services']}.",
        f"- Con recorrido vinculado: {stats['linked_services']}; pendientes: {stats['baseline_services'] - stats['linked_services']}.",
        f"- Localidades/puntos vinculados: {stats['linked_places']}; sin coordenadas: {stats['linked_places_unlocated']}.",
        "- No se reemplazan las salidas de los PDF por las del Excel. Todas las horas intermedias son estimaciones.",
        "- IDs y homónimos ambiguos no se fusionan. No se inventan coordenadas ni se dibujan saltos sobre puntos faltantes.",
        "- Las anotaciones contradictorias se conservan para revisión, sin aplicar automáticamente prohibiciones de subida/bajada.",
        "- Filas consecutivas repetidas no se interpretan como llegada/salida sin confirmación. Su perfil queda pendiente.",
        "", "## Cobertura por corredor", "", "| Corredor | Servicios | Vinculados | Pendientes |", "|---|---:|---:|---:|"]
    for name, row in stats["by_corridor"].items():
        lines.append(f"| {name} | {row['services']} | {row['linked']} | {row['services'] - row['linked']} |")
    lines.extend(["", "## Caso que no debe unirse automáticamente", "",
        "Córdoba–El Talar de Intercórdoba figura por Padre Luchesse/Aeropuerto en los PDF usados. El perfil homónimo del Excel indica Donato Álvarez. No se enlazan entre sí por tener las mismas cabeceras; los horarios publicados siguen disponibles.",
        "", "## Variantes de cronograma pendientes de vinculación", "",
        "Estas filas son variantes, no servicios individuales. La columna Servicios indica cuántas salidas afecta cada una.", "",
        "| Corredor | Empresa | Línea | Sentido | Modalidad / ruta PDF | Servicios | Motivo |", "|---|---|---|---|---|---:|---|"])
    for p in data["audit"]["pending_bindings"]:
        lines.append("| " + " | ".join(cell(v) for v in [p["corridor"], p["company"], p["line"], p["direction"], p["modality"] + " / " + p["route"], p["services"], p["reason"]]) + " |")
    lines.extend(["", "## Perfiles con datos por revisar", "", "| Hoja | Filas | Empresa | Línea | Sentido | Observación de control |", "|---|---|---|---|---|---|"])
    for p in data["profiles"]:
        if p["issues"]:
            lines.append("| " + " | ".join(cell(v) for v in [p["source_sheet"], f"{p['source_rows'][0]}–{p['source_rows'][-1]}", p["company"], p["line"], p["direction"], "; ".join(p["issues"])]) + " |")
    lines.extend(["", "## Filas incompletas conservadas en auditoría", "", "| Hoja | Fila | Motivo |", "|---|---:|---|"])
    for row in data["audit"]["incomplete_rows"]:
        lines.append(f"| {row['sheet']} | {row['row']} | {row['reason']} |")
    linked_ids = set(data["bindings"].values())
    used = {s["place_id"] for p in data["profiles"] if p["id"] in linked_ids for s in p["stops"]}
    lines.extend(["", "## Localidades vinculadas sin coordenadas", "",
        "Sus nombres y tiempos siguen disponibles en la lista; los segmentos geográficos que las atraviesan no se dibujan.", "",
        "| ID en base | Localidad | Corredores |", "|---|---|---|"])
    for pid in sorted(used, key=lambda pid: data["places"][pid]["name"]):
        p = data["places"][pid]
        if p["lat"] is None:
            lines.append(f"| {cell(p['source_id'])} | {cell(p['name'])} | {cell(', '.join(p['corridors']))} |")
    return "\n".join(lines) + "\n"


def ordered_nodes(profile, nodes, matches):
    cursor = -1
    for node in nodes:
        cursor = next((i for i in range(cursor + 1, len(profile["stops"])) if matches(profile["stops"][i], node)), -1)
        if cursor == -1:
            return False
    return True


def variant_hint(line):
    split = re.split(r"\s+(?:X|POR|VIA)\s+", norm(line), maxsplit=1)
    return split[1] if len(split) > 1 else ""


def route_words(text):
    text = norm(text).replace("LUCHESSE", "LUCHESE").replace("C PAZ", "CARLOS PAZ")
    text = re.sub(r"\bGRAL\b", "GENERAL", text)
    text = re.sub(r"\b(?:RN|RP|RPE|RPA|RUTA|R)\s*(?=[0-9])", "", text)
    text = re.sub(r"\b(?:RP|RUTA)\s*(E|A)\s*([0-9]+)", r"\1\2", text)
    text = re.sub(r"\b([EA])\s+([0-9]+)\b", r"\1\2", text)
    return [t for t in text.split() if t not in {"POR", "VIA", "X", "EL", "LA", "LOS", "LAS", "DE", "DEL", "RUTA", "RN", "RP", "AV", "AVDA", "Y"}]


def hint_matches(hint, route):
    a, b = set(route_words(hint)), set(route_words(route))
    return bool(a) and a.issubset(b)


def implicit_variant_matches(service, profile, hint):
    """Reconoce solo variantes documentadas cuyo PDF omite la ruta.

    Una aclaración explícita del PDF, por ejemplo ``NO INGRESA``, nunca se
    reemplaza por esta equivalencia.
    """
    if norm(service.get("route")):
        return False
    profile_base = re.split(r"\s+(?:X|POR|VIA)\s+", norm(profile["line"]), maxsplit=1)[0]
    key = (norm(service["corridor"]), cuit(service["cuit"]), norm(service["line"]), norm(hint))
    return profile_base == norm(service["line"]) and key in IMPLICIT_VARIANTS


def route_compatible(service, profile, places):
    route = norm(service.get("route"))
    hint = variant_hint(profile["line"])
    if hint and not hint_matches(hint, route) and not implicit_variant_matches(service, profile, hint):
        return False
    if "REFUERZO DESDE" in route or "RESTRICCION" in route or "REST DE TRAFICO" in route:
        return False
    # Las exclusiones explícitas del PDF prevalecen sobre el catálogo orientativo.
    names = {canonical(n) for s in profile["stops"] for n in [places[s["place_id"]]["name"]] + places[s["place_id"]]["aliases"]}
    exclusions = []
    for match in re.finditer(r"(?:NO (?:ENTRA|INGRESA|TOCA)(?: A)?|NO)\s+(.+)", route):
        value = match[1].replace("C PAZ", "VILLA CARLOS PAZ").replace("GRAL PAZ", "GENERAL PAZ")
        exclusions.append(value)
    if any(n in text or text in n for text in exclusions for n in names):
        return False
    for phrase, place in (("ENTRA A RIO CEBALLOS", "RIO CEBALLOS"), ("TOCA C PAZ", "VILLA CARLOS PAZ"), ("INGRESAN A SAN MARTIN", "SAN MARTIN")):
        if phrase in route and "NO " + phrase not in route and not any(place in n for n in names):
            return False
    if "SOLO PARA EN ARROYITO" in route and any(n not in {"CORDOBA", "ARROYITO", "SAN FRANCISCO"} for n in names):
        return False
    # Una vía explícita por localidad debe estar representada en la secuencia.
    for token in ("CORRALITO", "ALMAFUERTE", "SAN AGUSTIN", "GENERAL CABRERA", "CHAZON", "JUSTINIANO POSSE", "MARCOS JUAREZ", "LA CARLOTA", "FALDA DEL CARMEN", "EL TALAR", "TALAR", "VILLA ALLENDE", "SANTA ELENA", "CAMINIAGA", "SAN NICOLAS"):
        if token in route and not any(token in e for e in exclusions):
            target = "EL TALAR" if token == "TALAR" else token
            if not any(target in n for n in names) and target not in norm(profile["line"]):
                return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xlsx", type=Path)
    parser.add_argument("--schedule", type=Path, required=True)
    parser.add_argument("--locations", type=Path, required=True)
    parser.add_argument("--georef", type=Path, action="append", default=[])
    parser.add_argument("--existing-routes", type=Path, help="Conserva coordenadas del catálogo anterior cuando coinciden ID y nombre")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, help="Informe Markdown de cobertura y casos pendientes")
    args = parser.parse_args()
    existing = json.loads(args.existing_routes.read_text()) if args.existing_routes else None
    data = make_data(args.xlsx, json.loads(args.schedule.read_text()), json.loads(args.locations.read_text()), args.georef, existing)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report(data), encoding="utf-8")
    print(json.dumps(data["stats"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
