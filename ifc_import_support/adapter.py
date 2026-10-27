# SPDX-License-Identifier: GPL-3.0-or-later
"""Build detached IngeTrazo groups; commit once, only after successful staging."""
import math
from pathlib import Path


def build_element(item, *, source, offset):
    """Generator: yield every 400 triangles, return (group, skipped_count).

    Coordinates are already world-space SI metres minus the supplied offset.
    Detached groups mean cancel/failure leaves the document unchanged.
    """
    from PySide6.QtGui import QVector3D
    from core.group import Group
    from core.mesh import Mesh
    mesh = Mesh()
    group = Group(mesh, name=f"{item['class']} | {item['name']} | {item['guid']}")
    group.component = False
    # Stable storey GUID distinguishes duplicate storey names / linked models.
    group.layer = f"IFC | {item['storey']['name']} | {item['storey']['id']}"
    group.ifc = {'class': item['class'], 'name': item['name']}
    group.ext = {'ifc_basic_import': {'guid': item['guid'], 'step_id': item['id'],
                 'source': str(Path(source).resolve()), 'storey': item['storey'],
                 'predefined_type': item.get('predefined_type', 'NOTDEFINED'),
                 'offset_subtracted_m': list(offset)}}
    raw = item['vertices']
    if len(raw) % 3 or not all(math.isfinite(v) for v in raw):
        raise ValueError('Invalid vertex array in worker output.')
    points = [QVector3D(*raw[i:i+3]) for i in range(0, len(raw), 3)]
    indices = item['triangles']
    if len(indices) % 3:
        raise ValueError('Invalid triangle array in worker output.')
    skipped = 0
    for n in range(0, len(indices), 3):
        tri = indices[n:n+3]
        if any(not isinstance(i, int) or i < 0 or i >= len(points) for i in tri):
            raise ValueError('Invalid vertex index in worker output.')
        pts = [points[i] for i in tri]
        # Match host weld grid so a tiny triangle cannot become a broken face.
        keys = [tuple(round(c * 10000) for c in p.toTuple()) for p in pts]
        if len(set(keys)) != 3 or QVector3D.crossProduct(pts[1]-pts[0], pts[2]-pts[0]).lengthSquared() < 1e-20:
            skipped += 1
        else:
            face = mesh.add_face(pts)
            mids = item.get('material_ids', [])
            mid = mids[n//3] if n//3 < len(mids) else -1
            materials = item.get('materials', [])
            if 0 <= mid < len(materials):
                face.attrs.update(materials[mid])
        if n // 3 % 400 == 399:
            yield
    # Collect connected planar regions before mutating topology. Use the seed
    # plane throughout: adjacent near-parallel facets must not flatten a curve.
    adjacency = {f: [] for f in mesh.faces}
    for index, edge in enumerate(mesh.edges):
        if len(edge.faces) == 2:
            a, b = edge.faces
            if a.attrs == b.attrs:
                adjacency[a].append(b)
                adjacency[b].append(a)
        if index % 400 == 399:
            yield
    remaining = set(mesh.faces)
    for index, seed in enumerate(list(mesh.faces)):
        if seed not in remaining:
            continue
        remaining.remove(seed)
        normal = seed.normal()
        origin = seed.loop[0].position
        region, pending = [seed], [seed]
        visited = 0
        while pending:
            current = pending.pop()
            for other in adjacency[current]:
                if other not in remaining:
                    continue
                if QVector3D.dotProduct(normal, other.normal()) < 0.99999:
                    continue
                if any(abs(QVector3D.dotProduct(normal, v.position-origin)) > 1e-5
                       for v in other.loop):
                    continue
                remaining.remove(other)
                region.append(other)
                pending.append(other)
            visited += 1
            if visited % 400 == 0:
                yield
        if len(region) > 1:
            # Host union preserves concave outlines and inner hole loops; on
            # ambiguous/non-manifold regions it leaves the triangles intact.
            mesh.dissolve_coplanar_region(region)
        if index % 400 == 399:
            yield
    return group, skipped


def commit_groups(viewport, groups):
    from core.history import SnapshotImport
    from core.layers import Layer
    if not groups:
        return
    # SnapshotImport operates on the active mesh/context; require root context.
    if getattr(viewport.scene, 'edit_group', None) is not None:
        raise ValueError('Exit the open group before importing IFC.')
    def mutate(scene):
        names = {layer.name for layer in scene.layers}
        for group in groups:
            if group.layer not in names:
                scene.layers.append(Layer(group.layer))
                names.add(group.layer)
        scene.groups.extend(groups)
    viewport.history.execute(SnapshotImport(mutate))
    error = viewport.history.last_error or viewport.history.last_rejected
    if error:
        raise RuntimeError(error)
    viewport.notify_scene_changed()
