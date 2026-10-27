# SPDX-License-Identifier: GPL-3.0-or-later
"""External IFC -> JSONL converter. No IngeTrazo or Qt imports here."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import sys
import time

STRUCTURAL = ('IfcWall', 'IfcColumn', 'IfcBeam', 'IfcSlab', 'IfcFooting', 'IfcPile',
              'IfcMember', 'IfcPlate')
EXCLUDED = ('IfcSpace', 'IfcOpeningElement', 'IfcFeatureElementSubtraction',
            'IfcAnnotation')
REVIEW = ('IfcWall', 'IfcColumn', 'IfcBeam', 'IfcSlab', 'IfcFooting', 'IfcPile')
PROTOCOL = 1


def emit(value):
    print(json.dumps(value, ensure_ascii=True, allow_nan=False), flush=True)


def source_stamp(source):
    path = Path(source).resolve()
    stat = path.stat()
    return {'path': str(path), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}


def category_for(element):
    import ifcopenshell.util.element
    subtype = ifcopenshell.util.element.get_predefined_type(element) or 'NOTDEFINED'
    return (element.is_a(), str(subtype))


def represented_products(model):
    return (p for p in model.by_type('IfcProduct')
            if getattr(p, 'Representation', None))


def is_excluded(element):
    return any(element.is_a(c) for c in EXCLUDED)


def scan(source, destination, progress=emit):
    """Read categories and counts without tessellating any geometry."""
    import ifcopenshell
    stamp = source_stamp(source)
    progress({'type': 'scan_progress', 'message': 'Reading IFC categories…'})
    model = ifcopenshell.open(str(source))
    rows = {}
    for element in represented_products(model):
        key = category_for(element)
        if key not in rows:
            excluded = is_excluded(element)
            rows[key] = {'class': key[0], 'predefined_type': key[1], 'count': 0,
                         'excluded': excluded,
                         'default_selected': not excluded and any(element.is_a(c) for c in REVIEW)}
        rows[key]['count'] += 1
    if stamp != source_stamp(source):
        raise ValueError('IFC changed during scanning. Scan the file again.')
    result = {'protocol': PROTOCOL, 'source_stamp': stamp, 'schema': model.schema,
              'categories': [rows[key] for key in sorted(rows)]}
    Path(destination).write_text(json.dumps(result, ensure_ascii=True), encoding='utf-8')
    progress({'type': 'complete', 'categories': len(rows)})
    return result


def storey_for(element):
    # get_container follows aggregation (e.g. parts inside an assembly).
    import ifcopenshell.util.element
    container = ifcopenshell.util.element.get_container(element)
    seen = set()
    while container is not None and container.id() not in seen:
        seen.add(container.id())
        if container.is_a('IfcBuildingStorey'):
            return {'id': container.GlobalId, 'name': container.Name or 'Unnamed storey'}
        container = ifcopenshell.util.element.get_aggregate(container)
    return {'id': 'unassigned', 'name': 'Unassigned'}


def convert(source, destination, *, structural=True, offset=(0., 0., 0.),
            max_triangles=2000000, progress=emit, categories=None):
    import ifcopenshell
    import ifcopenshell.geom
    started = time.monotonic()
    offset = tuple(float(x) for x in offset)
    if len(offset) != 3 or not all(math.isfinite(x) for x in offset):
        raise ValueError('Offset must be three finite metre values.')
    if max_triangles < 1:
        raise ValueError('Triangle limit must be positive.')
    model = ifcopenshell.open(str(source))
    settings = ifcopenshell.geom.settings()
    # World coordinates include nested placements. IfcOpenShell outputs SI metres.
    # Map conversion / projected CRS is deliberately not applied.
    settings.set('use-world-coords', True)
    settings.set('convert-back-units', False)
    selected = None
    if categories is not None:
        if not isinstance(categories, list) or not categories:
            raise ValueError('Select at least one category before importing.')
        selected = set()
        for row in categories:
            if not isinstance(row, dict) or not all(isinstance(row.get(k), str) and row[k]
                                                   for k in ('class', 'predefined_type')):
                raise ValueError('Invalid category selection file.')
            selected.add((row['class'], row['predefined_type']))
    products = sorted((p for p in represented_products(model)
                       if not is_excluded(p)
                       and ((category_for(p) in selected) if selected is not None else
                            (not structural or any(p.is_a(c) for c in STRUCTURAL)))),
                      key=lambda p: p.id())
    counts = {}
    for product in products:
        key = category_for(product)
        counts.setdefault(key, {'class': key[0], 'predefined_type': key[1],
                                 'candidates': 0, 'imported': 0, 'failed': 0})['candidates'] += 1
    report = {'protocol': PROTOCOL, 'source': str(Path(source).resolve()),
              'schema': model.schema, 'ifcopenshell': ifcopenshell.version,
              'units': 'metres', 'offset_subtracted_m': offset,
              'mode': 'selected' if selected is not None else ('structural' if structural else 'physical'),
              'selected_categories': categories,
              'category_counts': [counts[k] for k in sorted(counts)],
              'candidates': len(products), 'imported': 0, 'triangles': 0,
              'failed': [], 'warnings': [], 'max_abs_coordinate_m': 0.0}
    destination = Path(destination)
    # A failed conversion must never leave an apparently complete stream.
    partial = destination.with_suffix(destination.suffix + '.partial')
    try:
        with partial.open('w', encoding='utf-8') as stream:
            stream.write(json.dumps({'type': 'header', 'protocol': PROTOCOL}) + '\n')
            for number, element in enumerate(products, 1):
                progress({'type': 'progress', 'done': number - 1,
                          'total': len(products), 'name': element.Name or element.is_a()})
                try:
                    shape = ifcopenshell.geom.create_shape(settings, element)
                    verts = list(shape.geometry.verts)
                    faces = list(shape.geometry.faces)
                    if not verts or not faces:
                        raise ValueError('No surface geometry returned.')
                    if len(verts) % 3 or len(faces) % 3:
                        raise ValueError('Invalid triangulation array.')
                    if not all(math.isfinite(v) for v in verts):
                        raise ValueError('Non-finite geometry coordinates.')
                    if any(i < 0 or i >= len(verts) // 3 for i in faces):
                        raise ValueError('Triangle index out of range.')
                    for i in range(len(verts)):
                        verts[i] -= offset[i % 3]
                    if not all(math.isfinite(v) for v in verts):
                        raise ValueError('Offset produced non-finite coordinates.')
                    materials = []
                    for material in shape.geometry.materials:
                        colour = [material.diffuse.r(), material.diffuse.g(), material.diffuse.b()]
                        if len(colour) != 3 or not all(math.isfinite(c) for c in colour):
                            colour = [0.75, 0.75, 0.75]
                        transparency = material.transparency
                        opacity = (1.0 - transparency
                                   if math.isfinite(transparency) else 1.0)
                        materials.append({'color': [max(0., min(1., c)) for c in colour],
                                          'opacity': max(0., min(1., opacity))})
                    item = {'type': 'element', 'id': element.id(),
                            'guid': element.GlobalId, 'class': element.is_a(),
                            'name': element.Name or element.is_a(),
                            'predefined_type': category_for(element)[1],
                            'storey': storey_for(element), 'vertices': verts,
                            'triangles': faces, 'materials': materials,
                            'material_ids': list(shape.geometry.material_ids)}
                except Exception as exc:
                    counts[category_for(element)]['failed'] += 1
                    report['failed'].append({'id': element.id(),
                                             'guid': element.GlobalId,
                                             'class': element.is_a(), 'error': str(exc)})
                    continue
                count = len(faces) // 3
                if report['triangles'] + count > max_triangles:
                    raise ValueError(f'Triangle limit ({max_triangles:,}) exceeded. '
                                     'Nothing imported. Filter the IFC or raise the limit.')
                report['max_abs_coordinate_m'] = max(report['max_abs_coordinate_m'],
                                                     max(abs(v) for v in verts))
                stream.write(json.dumps(item, ensure_ascii=True, allow_nan=False) + '\n')
                report['imported'] += 1
                counts[category_for(element)]['imported'] += 1
                report['triangles'] += count
            report['elapsed_seconds'] = round(time.monotonic() - started, 3)
            if report['max_abs_coordinate_m'] > 10000:
                report['warnings'].append('Coordinates exceed 10 km from origin. '
                    'Qt uses float32 positions: reimport with a common XYZ offset in metres.')
            stream.write(json.dumps({'type': 'summary', **report}, allow_nan=False) + '\n')
        partial.replace(destination)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    progress({'type': 'complete', 'imported': report['imported'],
              'triangles': report['triangles']})
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--scan', action='store_true', help='List categories only; do not generate geometry.')
    parser.add_argument('--selection', help='JSON file with selected class/predefined_type pairs.')
    parser.add_argument('--mode', choices=('structural', 'physical'), default='structural')
    parser.add_argument('--offset', nargs=3, type=float, default=(0., 0., 0.))
    parser.add_argument('--max-triangles', type=int, default=2000000)
    args = parser.parse_args(argv)
    try:
        if args.scan:
            scan(args.input, args.output)
        else:
            categories = (json.loads(Path(args.selection).read_text(encoding='utf-8'))
                          if args.selection else None)
            convert(args.input, args.output, structural=args.mode == 'structural',
                    offset=args.offset, max_triangles=args.max_triangles, categories=categories)
    except Exception as exc:
        emit({'type': 'error', 'message': str(exc)})
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
