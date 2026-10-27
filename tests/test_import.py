import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import ifcopenshell
import ifcopenshell.api
import numpy as np
import pytest

from ifc_import_support.worker import convert, scan, category_for


def api(operation, model=None, **kwargs):
    return ifcopenshell.api.run(operation, model, **kwargs)


@pytest.fixture
def sample(tmp_path):
    model = api('project.create_file', version='IFC4')
    project = api('root.create_entity', model, ifc_class='IfcProject', name='IFC test')
    api('unit.assign_unit', model, length={'is_metric': True, 'raw': 'MILLIMETERS'})
    context = api('context.add_context', model, context_type='Model')
    body = api('context.add_context', model, context_type='Model', context_identifier='Body',
               target_view='MODEL_VIEW', parent=context)
    site = api('root.create_entity', model, ifc_class='IfcSite')
    building = api('root.create_entity', model, ifc_class='IfcBuilding')
    floor1 = api('root.create_entity', model, ifc_class='IfcBuildingStorey', name='קומה א')
    floor2 = api('root.create_entity', model, ifc_class='IfcBuildingStorey', name='קומה א')
    api('aggregate.assign_object', model, products=[site], relating_object=project)
    api('aggregate.assign_object', model, products=[building], relating_object=site)
    api('aggregate.assign_object', model, products=[floor1, floor2], relating_object=building)

    def box(cls, name, length, thickness, height, matrix, floor=floor1):
        element = api('root.create_entity', model, ifc_class=cls, name=name)
        representation = api('geometry.add_wall_representation', model, context=body,
                             length=length, height=height, thickness=thickness)
        api('geometry.assign_representation', model, product=element, representation=representation)
        if floor:
            api('spatial.assign_container', model, products=[element], relating_structure=floor)
        api('geometry.edit_object_placement', model, product=element, matrix=matrix, is_si=True)
        return element

    placement = np.array([[0., -1., 0., 10.], [1., 0., 0., 20.],
                          [0., 0., 1., 3.], [0., 0., 0., 1.]])
    wall = box('IfcWall', 'קיר עם פתח', 4., .3, 3., placement)
    opening_placement = placement.copy()
    opening_placement[:3, 3] = (placement @ np.array([1., -.1, 0., 1.]))[:3]
    opening = box('IfcOpeningElement', 'opening', 1., .5, 2., opening_placement, floor=None)
    api('feature.add_feature', model, feature=opening, element=wall)
    column = box('IfcColumn', 'Column', .4, .4, 3., np.eye(4), floor=floor2)
    space = box('IfcSpace', 'excluded space', 5., 5., 3., np.eye(4), floor=None)
    api('aggregate.assign_object', model, products=[space], relating_object=floor1)
    box('IfcFurnishingElement', 'furniture', 1., 1., 1., np.eye(4))
    path = tmp_path / 'תיקייה' / 'דגם.ifc'
    path.parent.mkdir()
    model.write(str(path))
    return path, wall.GlobalId, column.GlobalId


def records(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def volume(item):
    points = np.array(item['vertices']).reshape(-1, 3)
    triangles = points[np.array(item['triangles']).reshape(-1, 3)]
    # Shift before volume evaluation for numerical stability at survey coordinates.
    triangles = triangles - points[0]
    return abs(np.einsum('ij,ij->i', triangles[:, 0],
                        np.cross(triangles[:, 1], triangles[:, 2])).sum() / 6)


def test_units_placement_opening_storeys_and_filter(sample, tmp_path):
    source, wall_guid, column_guid = sample
    output = tmp_path / 'out.jsonl'
    report = convert(source, output, progress=lambda _: None)
    assert report['imported'] == 2
    assert report['failed'] == []
    items = {r['guid']: r for r in records(output) if r['type'] == 'element'}
    wall, column = items[wall_guid], items[column_guid]
    points = np.array(wall['vertices']).reshape(-1, 3)
    np.testing.assert_allclose(points.min(axis=0), [9.7, 20., 3.], atol=1e-6)
    np.testing.assert_allclose(points.max(axis=0), [10., 24., 6.], atol=1e-6)
    assert volume(wall) == pytest.approx(3.0)  # 3.6 m3 minus 0.6 m3 door opening
    assert volume(column) == pytest.approx(.48)
    assert wall['storey']['name'] == 'קומה א'
    assert wall['storey']['id'] != column['storey']['id']
    assert wall['materials']


def test_physical_mode_excludes_spaces_and_opening_objects(sample, tmp_path):
    output = tmp_path / 'out.jsonl'
    report = convert(sample[0], output, structural=False, progress=lambda _: None)
    assert report['imported'] == 3
    classes = {r['class'] for r in records(output) if r['type'] == 'element'}
    assert classes == {'IfcWall', 'IfcColumn', 'IfcFurnishingElement'}


def test_explicit_offset_and_limit_are_atomic(sample, tmp_path):
    output = tmp_path / 'out.jsonl'
    report = convert(sample[0], output, offset=(10., 20., 3.), progress=lambda _: None)
    assert report['offset_subtracted_m'] == (10., 20., 3.)
    wall = next(r for r in records(output) if r.get('class') == 'IfcWall')
    points = np.array(wall['vertices']).reshape(-1, 3)
    np.testing.assert_allclose(points.min(axis=0), [-.3, 0., 0.], atol=1e-6)
    output.unlink()
    with pytest.raises(ValueError, match='Triangle limit'):
        convert(sample[0], output, max_triangles=1, progress=lambda _: None)
    assert not output.exists()
    assert not output.with_suffix('.jsonl.partial').exists()


def test_bad_element_is_reported(sample, tmp_path, monkeypatch):
    import ifcopenshell.geom
    real = ifcopenshell.geom.create_shape
    def fail_one(settings, element):
        if element.is_a('IfcColumn'):
            raise RuntimeError('deliberate geometry failure')
        return real(settings, element)
    monkeypatch.setattr(ifcopenshell.geom, 'create_shape', fail_one)
    report = convert(sample[0], tmp_path / 'out.jsonl', progress=lambda _: None)
    assert report['imported'] == 1
    assert report['failed'][0]['guid'] == sample[2]
    assert 'deliberate' in report['failed'][0]['error']


def test_worker_cli_nonzero_on_bad_input(tmp_path):
    worker = Path(__file__).resolve().parents[1] / 'ifc_import_support/worker.py'
    proc = subprocess.run([sys.executable, str(worker), '--input', str(tmp_path/'missing.ifc'),
                           '--output', str(tmp_path/'out.jsonl')], capture_output=True, text=True)
    assert proc.returncode == 1
    assert json.loads(proc.stdout.splitlines()[-1])['type'] == 'error'
    assert not (tmp_path/'out.jsonl').exists()


def adapter_module():
    from ifc_import_support import adapter
    return adapter


def drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as result:
            return result.value


def test_real_host_groups_layers_undo_redo_and_save(sample, tmp_path):
    from core.scene import Scene
    from core.history import History
    from formats.igz import save_scene, load_into
    adapter = adapter_module()
    output = tmp_path / 'out.jsonl'
    convert(sample[0], output, progress=lambda _: None)
    groups = [drain(adapter.build_element(item, source=sample[0], offset=(0,0,0)))[0]
              for item in records(output) if item['type'] == 'element']
    assert len(groups) == 2
    assert all(g.mesh.faces and g.ext['ifc_basic_import']['guid'] for g in groups)
    scene = Scene()
    history = History(scene)
    viewport = SimpleNamespace(scene=scene, history=history, notify_scene_changed=lambda: None)
    adapter.commit_groups(viewport, groups)
    assert history.last_error is None
    assert len(scene.groups) == 2 and len(scene.layers) == 3
    assert history.undo()
    assert not scene.groups and len(scene.layers) == 1
    assert history.redo()
    assert len(scene.groups) == 2 and len(scene.layers) == 3
    path = tmp_path / 'roundtrip.igz'
    save_scene(scene, path)
    loaded = Scene()
    load_into(loaded, path)
    assert {g.ext['ifc_basic_import']['guid'] for g in loaded.groups} == set(sample[1:])
    assert {g.layer for g in loaded.groups} == {g.layer for g in groups}
    assert sum(len(g.mesh.faces) for g in loaded.groups) == sum(len(g.mesh.faces) for g in groups)


def test_staging_cancel_never_mutates_host(sample, tmp_path):
    from core.scene import Scene
    adapter = adapter_module()
    output = tmp_path / 'out.jsonl'
    convert(sample[0], output, progress=lambda _: None)
    item = next(r for r in records(output) if r['type'] == 'element')
    # More than a tick of work, so we can abandon it before completion.
    item['triangles'] *= 100
    item['material_ids'] *= 100
    scene = Scene()
    gen = adapter.build_element(item, source=sample[0], offset=(0,0,0))
    next(gen)
    gen.close()
    assert not scene.groups and len(scene.layers) == 1


def test_reject_import_while_inside_group():
    adapter = adapter_module()
    viewport = SimpleNamespace(scene=SimpleNamespace(edit_group=object()))
    with pytest.raises(ValueError, match='Exit'):
        adapter.commit_groups(viewport, [object()])


@pytest.fixture
def gui_app(tmp_path):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    # Keep a strong reference for the whole test.
    app = QApplication.instance() or QApplication([])
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path/'settings'))
    return app


def run_until(qapp, condition, seconds=15):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    checker = QTimer()
    checker.setInterval(5)
    checker.timeout.connect(lambda: loop.quit() if condition() else None)
    checker.start()
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    deadline.start(seconds * 1000)
    loop.exec()
    checker.stop()
    deadline.stop()
    assert condition(), 'Timed out waiting for Qt import pipeline'


def make_dialog(gui_app, sample, monkeypatch):
    from PySide6.QtWidgets import QWidget, QMessageBox
    from core.scene import Scene
    from core.history import History
    import ifc_basic_import
    scene = Scene()
    host = SimpleNamespace(window=QWidget(), scene=scene)
    host.viewport = SimpleNamespace(scene=scene, history=History(scene), notify_scene_changed=lambda: None)
    errors = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: errors.append(args[2]))
    dialog = ifc_basic_import.ImportDialog(host)
    dialog.file.setText(str(sample[0]))
    dialog.python.setText(sys.executable)
    dialog._scan()
    run_until(gui_app, lambda: not dialog.busy)
    assert errors == []
    return dialog, host, errors


def test_qprocess_to_gui_import_and_report(gui_app, sample, monkeypatch):
    dialog, host, errors = make_dialog(gui_app, sample, monkeypatch)
    dialog._start()
    run_until(gui_app, lambda: not dialog.busy)
    assert errors == []
    assert dialog.summary['groups_added'] == 2
    assert len(host.scene.groups) == 2
    assert dialog.temp is None and dialog.reader is None
    assert host.viewport.history.undo()
    assert not host.scene.groups
    dialog.close()


def test_qprocess_cancel_leaves_no_geometry(gui_app, sample, monkeypatch):
    from PySide6.QtCore import QProcess
    dialog, host, errors = make_dialog(gui_app, sample, monkeypatch)
    dialog._start()
    dialog.reject()
    # Escape again while the kill is pending must not destroy a live QProcess.
    dialog.reject()
    run_until(gui_app, lambda: dialog.process.state() == QProcess.NotRunning and dialog.temp is None)
    assert errors == []
    assert not host.scene.groups and len(host.scene.layers) == 1
    assert not host.viewport.history.undo_stack
    dialog.close()


def test_plugin_discovery_and_menu_registration(gui_app):
    from core.extensions import discover_plugins
    root = Path(__file__).resolve().parents[1]
    plugins, errors = discover_plugins([root])
    assert errors == []
    assert len(plugins) == 1 and plugins[0].stem == 'ifc_basic_import'
    import ifc_basic_import
    actions = []
    ifc_basic_import.setup(SimpleNamespace(add_menu_action=lambda *a, **k: actions.append(a)))
    assert actions[0][0] == 'Import IFC (basic)…'


@pytest.fixture
def facade_sample(sample):
    path = sample[0]
    model = ifcopenshell.open(str(path))
    column = model.by_type('IfcColumn')[0]
    floor = ifcopenshell.util.element.get_container(column)
    for cls, subtype in [('IfcMember', 'MULLION'), ('IfcPlate', 'CURTAIN_PANEL')]:
        element = api('root.create_entity', model, ifc_class=cls, name=subtype, predefined_type=subtype)
        api('geometry.assign_representation', model, product=element,
            representation=column.Representation.Representations[0])
        api('spatial.assign_container', model, products=[element], relating_structure=floor)
        api('geometry.edit_object_placement', model, product=element, matrix=np.eye(4))
    model.write(str(path))
    return sample


def test_scan_counts_subtypes_without_tessellation(facade_sample, tmp_path, monkeypatch):
    import ifcopenshell.geom
    def forbidden(*args, **kwargs):
        raise AssertionError('Scan must not tessellate geometry')
    monkeypatch.setattr(ifcopenshell.geom, 'create_shape', forbidden)
    result = scan(facade_sample[0], tmp_path/'scan.json', progress=lambda _: None)
    rows = {(r['class'], r['predefined_type']): r for r in result['categories']}
    assert sum(r['count'] for r in rows.values()) == 7
    assert rows['IfcMember', 'MULLION']['count'] == 1
    assert rows['IfcPlate', 'CURTAIN_PANEL']['count'] == 1
    assert not rows['IfcMember', 'MULLION']['default_selected']
    assert not rows['IfcPlate', 'CURTAIN_PANEL']['default_selected']
    assert any(r['excluded'] for r in rows.values() if r['class'] == 'IfcSpace')
    assert any(r['default_selected'] for r in rows.values() if r['class'] == 'IfcWall')


def test_selected_categories_skip_facade_and_report_breakdown(facade_sample, tmp_path):
    result = scan(facade_sample[0], tmp_path/'scan.json', progress=lambda _: None)
    selection = [{'class': r['class'], 'predefined_type': r['predefined_type']}
                 for r in result['categories'] if r['default_selected']]
    output = tmp_path/'selected.jsonl'
    report = convert(facade_sample[0], output, categories=selection, progress=lambda _: None)
    assert report['mode'] == 'selected'
    assert report['imported'] == 2
    assert {r['class'] for r in records(output) if r['type'] == 'element'} == {'IfcWall', 'IfcColumn'}
    assert sum(r['imported'] for r in report['category_counts']) == 2
    # Users may explicitly opt into only the mullions.
    report = convert(facade_sample[0], output,
                     categories=[{'class': 'IfcMember', 'predefined_type': 'MULLION'}],
                     progress=lambda _: None)
    assert report['imported'] == 1
    assert next(r for r in records(output) if r['type'] == 'element')['predefined_type'] == 'MULLION'
    with pytest.raises(ValueError, match='Select at least one'):
        convert(facade_sample[0], output, categories=[], progress=lambda _: None)


def test_subtype_can_come_from_assigned_type(sample):
    model = ifcopenshell.open(str(sample[0]))
    element = api('root.create_entity', model, ifc_class='IfcMember')
    kind = api('root.create_entity', model, ifc_class='IfcMemberType', predefined_type='MULLION')
    api('type.assign_type', model, related_objects=[element], relating_type=kind)
    assert category_for(element) == ('IfcMember', 'MULLION')


def test_category_checkboxes_and_source_invalidation(gui_app, facade_sample, monkeypatch):
    from PySide6.QtCore import Qt
    dialog, host, errors = make_dialog(gui_app, facade_sample, monkeypatch)
    assert len(dialog._selected_categories()) == 2
    dialog._preset('none')
    assert not dialog.start.isEnabled()
    dialog._preset('all')
    assert len(dialog._selected_categories()) == 5  # physical only; spaces/openings disabled
    # Only the curtain panels: exact class+subtype selection, not whole IfcPlate hierarchy.
    dialog._preset('none')
    for index, row in enumerate(dialog.category_rows):
        if row['class'] == 'IfcPlate':
            dialog.categories.topLevelItem(index).setCheckState(0, Qt.Checked)
    dialog._start()
    run_until(gui_app, lambda: not dialog.busy)
    assert errors == []
    assert len(host.scene.groups) == 1
    assert host.scene.groups[0].ifc['class'] == 'IfcPlate'
    dialog.file.setText(str(facade_sample[0]) + '.different')
    assert dialog.scan_stamp is None and not dialog.start.isEnabled()
    assert dialog.categories.topLevelItemCount() == 0
    dialog.close()


def test_scan_cancel_does_not_enable_import(gui_app, sample, monkeypatch):
    from PySide6.QtCore import QProcess
    dialog, host, errors = make_dialog(gui_app, sample, monkeypatch)
    dialog._scan()
    dialog.reject()
    run_until(gui_app, lambda: dialog.process.state() == QProcess.NotRunning and dialog.temp is None)
    assert not dialog.start.isEnabled()
    assert dialog.scan_stamp is None
    assert not host.scene.groups
    assert errors == []
    dialog.close()


def test_changed_ifc_requires_rescan(gui_app, sample, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    dialog, host, errors = make_dialog(gui_app, sample, monkeypatch)
    notices = []
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: notices.append(args[2]))
    path = sample[0]
    path.write_text(path.read_text(encoding='utf-8') + '\n', encoding='utf-8')
    dialog._start()
    assert notices and not dialog.busy
    assert dialog.scan_stamp is None and not dialog.start.isEnabled()
    assert not host.scene.groups
    dialog.close()


def test_automatic_origin_reused_for_second_model(sample, tmp_path):
    first = tmp_path/'first.jsonl'
    report = convert(sample[0], first, auto_origin=True, progress=lambda _: None)
    offset = report['offset_subtracted_m']
    assert offset == pytest.approx((9.7, 20., 3.))
    second = tmp_path/'second.jsonl'
    convert(sample[0], second, offset=offset, progress=lambda _: None)
    a = [r for r in records(first) if r['type'] == 'element']
    b = [r for r in records(second) if r['type'] == 'element']
    assert [r['vertices'] for r in a] == [r['vertices'] for r in b]


def test_dialog_restores_origin_from_saved_groups(gui_app, sample, monkeypatch):
    import ifc_basic_import
    dialog, host, errors = make_dialog(gui_app, sample, monkeypatch)
    dialog._start()
    run_until(gui_app, lambda: not dialog.busy)
    assert not errors
    reopened = ifc_basic_import.ImportDialog(host)
    assert reopened.origin_mode.currentIndex() == 1
    assert [s.value() for s in reopened.offsets] == pytest.approx(dialog.summary['offset_subtracted_m'])
    reopened.close()
    dialog.close()


def test_startup_geometry_does_not_block_origin(gui_app, sample, monkeypatch):
    from core.group import Group
    from PySide6.QtGui import QVector3D
    dialog, host, errors = make_dialog(gui_app, sample, monkeypatch)
    figure = Group(name='Startup figure')
    figure.mesh.add_face([QVector3D(0,0,0), QVector3D(1,0,0), QVector3D(0,0,2)])
    host.scene.groups.append(figure)
    dialog._start()
    run_until(gui_app, lambda: not dialog.busy)
    assert errors == []
    assert dialog.summary['groups_added'] == 2
    assert figure in host.scene.groups
    assert dialog.summary['offset_subtracted_m'] == pytest.approx((9.7,20.,3.))
    dialog.close()
