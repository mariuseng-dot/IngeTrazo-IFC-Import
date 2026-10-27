# SPDX-License-Identifier: GPL-3.0-or-later
"""Basic IFC import 0.2.0 for IngeTrazo v0.5.9 (extension API 2)."""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time

from PySide6.QtCore import QProcess, QSettings, QTimer, Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QProgressBar, QPushButton, QSpinBox, QVBoxLayout, QWidget, QTreeWidget, QTreeWidgetItem)

SUPPORT = Path(__file__).resolve().with_name('ifc_import_support')
VERSION = '0.2.0'


def _adapter():
    # Plugins are loaded by path; never change sys.path or import a top-level
    # helper named "adapter" which could collide with another extension.
    spec = importlib.util.spec_from_file_location(
        '_ingetrazo_ifc_basic_adapter', SUPPORT / 'adapter.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ImportDialog(QDialog):
    def __init__(self, app):
        super().__init__(app.window)
        self.app = app
        self.settings = QSettings('IngeTrazo-IFC-Import', 'BasicImporter')
        self.setWindowTitle(f'Basic IFC import {VERSION}')
        self.resize(760, 680)
        self.process = None
        self.temp = None
        self.reader = None
        self.generator = None
        self.groups = []
        self.summary = None
        self.busy = False
        self.scan_stamp = None
        self.category_rows = []
        self.stdout_buffer = b''
        self.stderr_tail = ''
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._build_tick)
        layout = QVBoxLayout(self)
        note = QLabel('Import reference geometry as separate element groups and storey layers.\n'
                      'Spaces and opening objects are excluded; openings are cut into their hosts.\n'
                      'Requires a separate Python environment with IfcOpenShell (see README).')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.options = QWidget()
        form = QFormLayout(self.options)
        self.file = QLineEdit()
        form.addRow('IFC file', self._browse_row(self.file, self._choose_ifc))
        default = (Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) /
                   'IngeTrazo-IFC-Import' / 'venv' / 'Scripts' / 'python.exe')
        if os.name != 'nt':
            default = Path.home() / '.local/share/ingetrazo-ifc-import/venv/bin/python'
        self.python = QLineEdit(str(self.settings.value('python', str(default))))
        form.addRow('Helper Python executable', self._browse_row(self.python, self._choose_python))
        self.scan_button = QPushButton('Scan categories')
        self.scan_button.clicked.connect(self._scan)
        form.addRow('Before importing', self.scan_button)
        controls = QWidget()
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        for title, preset in [('Structural review', 'review'), ('Select all', 'all'), ('Select none', 'none')]:
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, mode=preset: self._preset(mode))
            controls_layout.addWidget(button)
        form.addRow('Category selection', controls)
        self.categories = QTreeWidget()
        self.categories.setHeaderLabels(['IFC class', 'Subtype / use', 'Elements'])
        self.categories.setRootIsDecorated(False)
        self.categories.setMinimumHeight(160)
        self.categories.setToolTip('Subtypes come from the element or its assigned IFC type. '
                                   'NOTDEFINED means the exporter provided no subtype.')
        self.categories.itemChanged.connect(self._selection_changed)
        form.addRow(self.categories)
        self.selection_label = QLabel('Choose an IFC and scan its categories first.')
        self.selection_label.setWordWrap(True)
        form.addRow(self.selection_label)
        self.limit = QSpinBox()
        self.limit.setRange(1000, 5000000)
        self.limit.setSingleStep(50000)
        self.limit.setValue(2000000)
        form.addRow('Maximum triangles', self.limit)
        row = QWidget()
        offsets = QHBoxLayout(row)
        offsets.setContentsMargins(0, 0, 0, 0)
        self.offsets = []
        for axis in 'XYZ':
            offsets.addWidget(QLabel(axis))
            spin = QDoubleSpinBox()
            spin.setRange(-1e9, 1e9)
            spin.setDecimals(4)
            offsets.addWidget(spin)
            self.offsets.append(spin)
        form.addRow('Subtract offset (metres)', row)
        hint = QLabel('Leave offsets at 0 to preserve IFC placement. For survey coordinates,\n'
                      'use the same XYZ offset for every related IFC. No automatic centring.')
        hint.setWordWrap(True)
        form.addRow(hint)
        layout.addWidget(self.options)
        self.status = QLabel('Ready. Start with a small IFC.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        buttons = QDialogButtonBox()
        self.start = buttons.addButton('Import', QDialogButtonBox.ActionRole)
        self.start.clicked.connect(self._start)
        self.report_button = buttons.addButton('Save report…', QDialogButtonBox.ActionRole)
        self.report_button.setEnabled(False)
        self.report_button.clicked.connect(self._save_report)
        self.close_button = buttons.addButton('Close', QDialogButtonBox.RejectRole)
        self.close_button.clicked.connect(self.reject)
        layout.addWidget(buttons)
        self.start.setEnabled(False)
        self.file.textChanged.connect(self._invalidate_scan)

    def _browse_row(self, field, callback):
        row = QWidget()
        box = QHBoxLayout(row)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(field)
        button = QPushButton('Browse…')
        button.clicked.connect(callback)
        box.addWidget(button)
        return row

    def _choose_ifc(self):
        name, _ = QFileDialog.getOpenFileName(self, 'Choose IFC', '', 'IFC (*.ifc);;All files (*)')
        if name:
            self.file.setText(name)
            self._scan()

    def _choose_python(self):
        name, _ = QFileDialog.getOpenFileName(self, 'Choose helper Python executable')
        if name:
            self.python.setText(name)

    def _source_stamp(self):
        path = Path(self.file.text().strip()).resolve()
        stat = path.stat()
        return {'path': str(path), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}

    def _invalidate_scan(self, *args):
        self.scan_stamp = None
        self.category_rows = []
        self.categories.clear()
        self._selection_changed()

    def _selected_categories(self):
        return [{'class': row['class'], 'predefined_type': row['predefined_type']}
                for index, row in enumerate(self.category_rows)
                if not row['excluded'] and
                self.categories.topLevelItem(index).checkState(0) == Qt.Checked]

    def _selection_changed(self, *args):
        selected = self._selected_categories()
        selected_keys = {(row['class'], row['predefined_type']) for row in selected}
        count = sum(row['count'] for row in self.category_rows
                    if (row['class'], row['predefined_type']) in selected_keys)
        self.selection_label.setText(
            f'{len(selected)} categories selected, {count:,} elements. '
            'Members and plates are off in Structural review; enable them if needed.'
            if self.scan_stamp else 'Choose an IFC and scan its categories first.')
        self.start.setEnabled(bool(selected) and self.scan_stamp is not None and not self.busy)

    def _preset(self, mode):
        self.categories.blockSignals(True)
        for index, row in enumerate(self.category_rows):
            checked = not row['excluded'] and (mode == 'all' or
                                               (mode == 'review' and row['default_selected']))
            self.categories.topLevelItem(index).setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
        self.categories.blockSignals(False)
        self._selection_changed()

    def _show_categories(self, result):
        if result.get('protocol') != 1 or result.get('source_stamp') != self._source_stamp():
            raise ValueError('IFC changed during scanning. Scan its categories again.')
        self.scan_stamp = result['source_stamp']
        self.category_rows = result['categories']
        self.categories.blockSignals(True)
        self.categories.clear()
        for row in self.category_rows:
            item = QTreeWidgetItem([row['class'], row['predefined_type'], f"{row['count']:,}"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked if row['default_selected'] else Qt.Unchecked)
            if row['excluded']:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
                item.setToolTip(0, 'Always excluded as a separate object; host openings are still cut.')
            self.categories.addTopLevelItem(item)
        self.categories.blockSignals(False)
        for column in range(3):
            self.categories.resizeColumnToContents(column)

    def _scan(self):
        if self.busy:
            return
        self._invalidate_scan()
        self._launch('scan')

    def _start(self):
        if self.busy:
            return
        try:
            valid_scan = self.scan_stamp is not None and self.scan_stamp == self._source_stamp()
        except OSError:
            valid_scan = False
        if not valid_scan:
            self._invalidate_scan()
            QMessageBox.information(self, 'Scan categories first', 'Choose the IFC and click Scan categories first.')
            return
        if not self._selected_categories():
            QMessageBox.information(self, 'No categories selected', 'Select at least one category to import.')
            return
        self._launch('import')

    def _launch(self, action):
        if getattr(self.app.scene, 'edit_group', None) is not None:
            QMessageBox.information(self, 'Exit group first', 'Exit the open group, then import IFC.')
            return
        self.source = Path(self.file.text().strip())
        interpreter = Path(self.python.text().strip())
        if not self.source.is_file() or not interpreter.is_file():
            QMessageBox.warning(self, 'Missing file', 'Choose an existing IFC and helper Python executable.\n'
                                'Run setup_windows.bat first if the helper is not installed.')
            return
        if not (SUPPORT / 'worker.py').is_file():
            QMessageBox.warning(self, 'Missing support files', 'Reinstall the extension using install_windows.bat.')
            return
        self.action = action
        self.summary = None
        self.report_button.setEnabled(False)
        self.settings.setValue('python', str(interpreter))
        self.offset = [spin.value() for spin in self.offsets]
        self.started = time.monotonic()
        self.scene_at_start = self.app.scene
        self.stamp_at_start = self._source_stamp()
        self.busy = True
        self.options.setEnabled(False)
        self.start.setEnabled(False)
        self.close_button.setText('Cancel import')
        self.temp = tempfile.TemporaryDirectory(prefix='ingetrazo-ifc-')
        self.output = Path(self.temp.name) / 'geometry.jsonl'
        self.stdout_buffer = b''
        self.stderr_tail = ''
        self.groups = []
        self.skipped_triangles = 0
        self.empty_groups = 0
        self.worker_error = ''
        self.progress.setRange(0, 0)
        self.status.setText('Scanning categories without generating geometry…' if action == 'scan' else
                            'Reading IFC and generating selected geometry… Cancel remains available.')
        self.process = QProcess(self)
        # Sanitize PyInstaller/Qt environment for the EXTERNAL Python process.
        from PySide6.QtCore import QProcessEnvironment
        env = QProcessEnvironment.systemEnvironment()
        for key in ('PYTHONHOME', 'PYTHONPATH', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH'):
            env.remove(key)
        if env.contains('LD_LIBRARY_PATH_ORIG'):
            env.insert('LD_LIBRARY_PATH', env.value('LD_LIBRARY_PATH_ORIG'))
        else:
            env.remove('LD_LIBRARY_PATH')
        self.process.setProcessEnvironment(env)
        self.process.readyReadStandardOutput.connect(self._read_progress)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self._converted)
        self.process.errorOccurred.connect(self._process_error)
        arguments = ['-u', str(SUPPORT / 'worker.py'),
            '--input', str(self.source.resolve()), '--output', str(self.output),
            '--max-triangles', str(self.limit.value()), '--offset', *map(str, self.offset)]
        if action == 'scan':
            arguments.append('--scan')
        else:
            selection = Path(self.temp.name) / 'selection.json'
            selection.write_text(json.dumps(self._selected_categories()), encoding='utf-8')
            arguments += ['--selection', str(selection)]
        # PyInstaller uses a process-wide DLL directory on Windows. Children
        # inherit it; clear it only while spawning the unrelated Python.
        import sys
        if os.name == 'nt' and getattr(sys, 'frozen', False):
            import ctypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
            kernel.SetDllDirectoryW.restype = ctypes.c_int
            kernel.SetDllDirectoryW(None)
            try:
                self.process.start(str(interpreter), arguments)
            finally:
                kernel.SetDllDirectoryW(getattr(sys, '_MEIPASS', None))
        else:
            self.process.start(str(interpreter), arguments)

    def _read_progress(self):
        self.stdout_buffer += bytes(self.process.readAllStandardOutput())
        while b'\n' in self.stdout_buffer:
            line, self.stdout_buffer = self.stdout_buffer.split(b'\n', 1)
            try:
                message = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue  # Some geometry libraries write diagnostics on stdout.
            if not isinstance(message, dict):
                continue
            if message.get('type') == 'progress':
                total = int(message.get('total', 0))
                self.progress.setRange(0, max(total, 1))
                self.progress.setValue(int(message['done']))
                self.status.setText(f"Generating {message['done']}/{total}: {str(message.get('name', ''))[:160]}")
            elif message.get('type') == 'error':
                self.worker_error = str(message.get('message', 'Conversion failed.'))
        self.stdout_buffer = self.stdout_buffer[-16384:]

    def _read_stderr(self):
        self.stderr_tail = (self.stderr_tail + bytes(self.process.readAllStandardError()).decode('utf-8', 'replace'))[-16000:]

    def _process_error(self, error):
        if self.busy and error == QProcess.FailedToStart:
            self._fail('Could not start helper Python. Check its executable path.')

    def _converted(self, exit_code, exit_status):
        if not self.busy:
            return
        self._read_progress()
        self._read_stderr()
        if exit_code != 0 or exit_status != QProcess.NormalExit or not self.output.is_file():
            self._fail(self.worker_error or self.stderr_tail or 'IFC helper stopped without a completed output.')
            return
        try:
            if self.stamp_at_start != self._source_stamp():
                self._invalidate_scan()
                raise ValueError('IFC changed while processing. Scan it again before importing.')
            if self.action == 'scan':
                self._show_categories(json.loads(self.output.read_text(encoding='utf-8')))
                self._finish('Scan complete. Check the categories you want, then click Import.')
                return
            self.reader = self.output.open(encoding='utf-8')
            header = json.loads(next(self.reader))
            if header.get('type') != 'header' or header.get('protocol') != 1:
                raise ValueError('Unsupported worker output format.')
            self.adapter = _adapter()
            self.progress.setRange(0, 0)
            self.status.setText('Building element groups… Nothing has been added to the model yet.')
            self.timer.start(0)
        except Exception as exc:
            self._fail(str(exc))

    def _build_tick(self):
        try:
            # Each tick builds at most 400 triangles. All document mutations
            # occur later, on the GUI thread, in a single history command.
            if self.generator is not None:
                try:
                    next(self.generator)
                    return
                except StopIteration as result:
                    group, skipped = result.value
                    self.skipped_triangles += skipped
                    if group.mesh.faces:
                        self.groups.append(group)
                    else:
                        self.empty_groups += 1
                    self.generator = None
            line = next(self.reader, None)
            if line is None:
                raise ValueError('Worker output ended before its summary.')
            item = json.loads(line)
            if item.get('type') == 'element':
                self.generator = self.adapter.build_element(item, source=self.source, offset=self.offset)
                self.status.setText(f"Building element {len(self.groups)+1}: {item['name'][:160]}")
            elif item.get('type') == 'summary':
                self.timer.stop()
                if self.app.scene is not self.scene_at_start:
                    raise ValueError('Document changed during import. Nothing imported.')
                self.adapter.commit_groups(self.app.viewport, self.groups)
                self.summary = {**item, 'extension_version': VERSION,
                    'groups_added': len(self.groups), 'empty_groups': self.empty_groups,
                    'degenerate_triangles_skipped': self.skipped_triangles,
                    'total_elapsed_seconds': round(time.monotonic()-self.started, 3)}
                msg = (f"Imported {len(self.groups)} elements; {item['triangles']:,} source triangles. "
                       f"{len(item['failed'])} failed; {self.skipped_triangles} tiny triangles skipped. "
                       f"{self.summary['total_elapsed_seconds']:.1f} seconds. "
                       'Save the report for details. Use Zoom Extents to find the model.')
                if item.get('warnings'):
                    msg += '\n' + '\n'.join(item['warnings'])
                if item['failed'] or self.empty_groups or self.skipped_triangles:
                    msg += '\nImport is incomplete: check the report before using it as a reference.'
                self._finish(msg)
                self.report_button.setEnabled(True)
            else:
                raise ValueError('Unexpected worker output record.')
        except Exception as exc:
            self._fail(str(exc))

    def _cleanup(self):
        self.timer.stop()
        self.generator = None
        self.groups = []
        if self.reader:
            self.reader.close()
            self.reader = None
        if self.temp:
            self.temp.cleanup()
            self.temp = None

    def _finish(self, message):
        self.busy = False
        self._cleanup()
        self.status.setText(message)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.options.setEnabled(True)
        self._selection_changed()
        self.close_button.setText('Close')

    def _fail(self, message):
        added = any(group in self.app.scene.groups for group in self.groups)
        self._finish('Import could not finish. Geometry was added: check the model and use Undo.'
                     if added else 'Import failed. Nothing added to the model.')
        QMessageBox.warning(self, 'IFC import failed', str(message)[-4000:])

    def _save_report(self):
        name, _ = QFileDialog.getSaveFileName(self, 'Save import report',
                     str(self.source.with_suffix('.ifc-import-report.json')), 'JSON (*.json)')
        if name:
            try:
                Path(name).write_text(json.dumps(self.summary, indent=2, ensure_ascii=False), encoding='utf-8')
            except OSError as exc:
                QMessageBox.warning(self, 'Report not saved', str(exc))

    def reject(self):
        if not self.busy and self.process and self.process.state() != QProcess.NotRunning:
            return  # A previous cancel is still waiting for the helper to exit.
        if self.busy:
            self.busy = False  # Ignore the process's finished signal after kill.
            if self.process and self.process.state() != QProcess.NotRunning:
                self.process.kill()
                # Reap asynchronously; defer deleting temporary files until closed.
                self.process.finished.connect(self._cancel_finished)
                self.close_button.setEnabled(False)
                self.status.setText('Stopping IFC helper…')
                return
            self._cancel_finished()
            return
        super().reject()

    def _cancel_finished(self, *args):
        self._finish('Import cancelled. Nothing added to the model.')
        self.close_button.setEnabled(True)

    def closeEvent(self, event):
        if self.busy or (self.process and self.process.state() != QProcess.NotRunning):
            event.ignore()
            if self.busy:
                self.reject()
        else:
            super().closeEvent(event)


def setup(app):
    def open_dialog():
        dialog = ImportDialog(app)
        dialog.exec()
    app.add_menu_action('Import IFC (basic)…', open_dialog,
                        tip='Import IFC reference elements and storey layers with IfcOpenShell.')
