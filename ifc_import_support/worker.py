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
PROTOCOL = 1


def emit(value):
    print(json.dumps(value, ensure_ascii=True, allow_nan=False), flush=True)


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
            max_triangles=200000, progress=emit):
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
    products = sorted((p for p in model.by_type('IfcProduct')
                       if getattr(p, 'Representation', None)
                       and not any(p.is_a(c) for c in EXCLUDED)
                       and (not structural or any(p.is_a(c) for c in STRUCTURAL))),
                      key=lambda p: p.id())
    report = {'protocol': PROTOCOL, 'source': str(Path(source).resolve()),
              'schema': model.schema, 'ifcopenshell': ifcopenshell.version,
              'units': 'metres', 'offset_subtracted_m': offset,
              'mode': 'structural' if structural else 'physical',
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
                            'storey': storey_for(element), 'vertices': verts,
                            'triangles': faces, 'materials': materials,
                            'material_ids': list(shape.geometry.material_ids)}
                except Exception as exc:
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
    parser.add_argument('--mode', choices=('structural', 'physical'), default='structural')
    parser.add_argument('--offset', nargs=3, type=float, default=(0., 0., 0.))
    parser.add_argument('--max-triangles', type=int, default=200000)
    args = parser.parse_args(argv)
    try:
        convert(args.input, args.output, structural=args.mode == 'structural',
                offset=args.offset, max_triangles=args.max_triangles)
    except Exception as exc:
        emit({'type': 'error', 'message': str(exc)})
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
