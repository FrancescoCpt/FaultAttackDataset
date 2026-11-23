#!/usr/bin/env python3
"""Read-only generic HDF5 browser (Tkinter + Matplotlib).

  python h5_viewer.py                        # choose a file in the GUI
  python h5_viewer.py Dataset/dataset.h5
  python h5_viewer.py file.h5 --inspect /     # text inspection, no GUI
  python h5_viewer.py file.h5 --inspect /data --axis 1 --indices 0

Select a dataset, choose the axis to vary and one fixed index for every other
axis (in axis order), then choose start/stop/step. At most --max-points values
are read using an HDF5 strided selection. Sampling is for display only and
may miss narrow spikes. Data on disk is never changed. Compound datasets can
be inspected one field at a time. Complex signals plot real/imaginary parts.
Soft/external links are shown but not followed automatically. Numeric datasets
of any rank are viewed as 1-D slices; the viewer does not infer physical axes.
Requires numpy, h5py, matplotlib, and Python with Tk support for the GUI.
Variable-length HDF5 elements can individually be large even in a short slice.
"""
import argparse
from itertools import islice
import math
from pathlib import Path

import h5py
import numpy as np

PAGE_SIZE = 200
PREVIEW_SIZE = 200


def short(value, limit=1500):
    if isinstance(value, bytes):
        return value[:limit].decode('utf-8', errors='replace') + (' …' if len(value) > limit else '')
    if isinstance(value, np.ndarray):
        with np.printoptions(threshold=20, edgeitems=3):
            text = str(value)
    else:
        text = str(value)
    return text[:limit] + (' …' if len(text) > limit else '')


def describe(obj):
    lines = [f'Percorso: {obj.name}', f'Tipo: {type(obj).__name__}']
    if isinstance(obj, h5py.Dataset):
        lines += [f'Shape: {obj.shape}', f'Dtype: {obj.dtype}',
                  f'Chunk: {obj.chunks}', f'Compressione: {obj.compression}',
                  f'Spazio allocato: {obj.id.get_storage_size():,} byte']
    elif isinstance(obj, h5py.Group):
        lines.append(f'Elementi: {len(obj)}')
    elif isinstance(obj, h5py.Datatype):
        lines.append(f'Dtype: {obj.dtype}')
    lines.append('\nAttributi:')
    for name in islice(obj.attrs, 100):
        aid = obj.attrs.get_id(name)
        if aid.get_storage_size() > 65536:
            lines.append(f'  {name}: <attributo grande, anteprima omessa>')
        else:
            lines.append(f'  {name}: {short(obj.attrs[name])}')
    if len(obj.attrs) > 100:
        lines.append('  … altri attributi omessi')
    return '\n'.join(lines)


def read_slice(ds, axis=-1, indices='', start=0, stop=None, step=1, max_points=5000, field=''):
    """Return x indices, values, effective selection; never fetch a whole matrix."""
    if max_points < 1 or step < 1:
        raise ValueError('Passo e limite punti devono essere positivi')
    if ds.shape is None:
        raise ValueError('Dataset HDF5 nullo: nessun dato')
    source = ds.fields(field) if field else ds
    if ds.ndim == 0:
        return np.array([0]), np.asarray(source[()]).reshape(1), 'scalare'
    if not -ds.ndim <= axis < ds.ndim:
        raise ValueError('Asse fuori intervallo')
    axis %= ds.ndim
    fixed = [int(s.strip()) for s in indices.split(',') if s.strip()]
    if not fixed:
        fixed = [0] * (ds.ndim - 1)
    if len(fixed) != ds.ndim - 1:
        raise ValueError(f'Servono {ds.ndim - 1} indici fissi, separati da virgole')
    length = ds.shape[axis]
    stop = length if stop is None else stop
    if not 0 <= start <= stop <= length:
        raise ValueError(f'Intervallo richiesto non valido: usare 0 ≤ inizio ≤ fine ≤ {length}')
    count = len(range(start, stop, step))
    stride = step * max(1, math.ceil(count / max_points))
    selection, pos = [], 0
    for dim, size in enumerate(ds.shape):
        if dim == axis:
            selection.append(slice(start, stop, stride))
        else:
            index = fixed[pos]
            pos += 1
            if not 0 <= index < size:
                raise ValueError(f'Indice {index} non valido per asse {dim} (dimensione {size})')
            selection.append(index)
    return np.arange(start, stop, stride), np.asarray(source[tuple(selection)]), str(tuple(selection))


def preview(x, values):
    lines = [f'Valori letti: {len(x)}; anteprima dei primi {PREVIEW_SIZE}.', 'Indice\tValore']
    lines.extend(f'{i}\t{short(v, 500)}' for i, v in zip(x[:PREVIEW_SIZE], values[:PREVIEW_SIZE]))
    return '\n'.join(lines)


class Viewer:
    def __init__(self, root, max_points):
        import tkinter as tk
        from tkinter import ttk
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
        self.tk, self.ttk, self.root = tk, ttk, root
        self.file, self.selected = None, None
        self.nodes = {}
        self.max_points = max_points
        root.title('HDF5 Viewer — sola lettura')
        root.geometry('1200x800')
        root.protocol('WM_DELETE_WINDOW', self.close)
        bar = ttk.Frame(root, padding=6)
        bar.pack(fill='x')
        ttk.Button(bar, text='Apri file…', command=self.choose).pack(side='left')
        ttk.Button(bar, text='Riapri / aggiorna', command=self.reload).pack(side='left', padx=6)
        self.filename = tk.StringVar(value='Nessun file aperto')
        ttk.Label(bar, textvariable=self.filename).pack(side='left')
        panes = ttk.Panedwindow(root, orient='horizontal')
        panes.pack(fill='both', expand=True)
        left = ttk.Frame(panes)
        panes.add(left, weight=1)
        self.tree = ttk.Treeview(left, show='tree', selectmode='browse')
        scroll = ttk.Scrollbar(left, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.tree.pack(fill='both', expand=True)
        self.tree.bind('<<TreeviewOpen>>', self.expand)
        self.tree.bind('<<TreeviewSelect>>', self.select)
        right = ttk.Frame(panes)
        panes.add(right, weight=3)
        controls = ttk.Frame(right, padding=6)
        controls.pack(fill='x')
        self.vars = {}
        for col, (key, label, default, width) in enumerate([
            ('axis', 'Asse variabile', '-1', 5), ('indices', 'Altri indici', '', 12),
            ('start', 'Inizio', '0', 8), ('stop', 'Fine esclusa', '', 10),
            ('step', 'Passo', '1', 7)]):
            ttk.Label(controls, text=label).grid(row=0, column=col, sticky='w', padx=3)
            self.vars[key] = tk.StringVar(value=default)
            ttk.Entry(controls, textvariable=self.vars[key], width=width).grid(row=1, column=col, padx=3)
        ttk.Label(controls, text='Campo composto').grid(row=0, column=5)
        self.field = ttk.Combobox(controls, state='readonly', width=18, values=[''])
        self.field.set('')
        self.field.grid(row=1, column=5, padx=3)
        ttk.Button(controls, text='Visualizza', command=self.render).grid(row=1, column=6, padx=6)
        ttk.Label(right, text='Asse -1 = ultimo asse. Indici fissi separati da virgole; vuoto = tutti zero.').pack(anchor='w', padx=8)
        self.tabs = ttk.Notebook(right)
        self.tabs.pack(fill='both', expand=True)
        self.info = self.text_tab('Struttura e attributi')
        self.data = self.text_tab('Dati')
        plot = ttk.Frame(self.tabs)
        self.tabs.add(plot, text='Grafico')
        self.figure = Figure(figsize=(7, 4), dpi=100)
        self.ax = self.figure.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.figure, master=plot)
        NavigationToolbar2Tk(self.canvas, plot)
        self.canvas.get_tk_widget().pack(fill='both', expand=True)
        self.status = tk.StringVar(value=f'Limite visualizzazione: {max_points} punti; nessuna modifica ai file.')
        ttk.Label(root, textvariable=self.status, padding=6, wraplength=1150).pack(fill='x')

    def text_tab(self, title):
        from tkinter.scrolledtext import ScrolledText
        frame = self.ttk.Frame(self.tabs)
        self.tabs.add(frame, text=title)
        text = ScrolledText(frame, wrap='word', font=('Courier', 11), state='disabled')
        text.pack(fill='both', expand=True)
        return text

    def set_text(self, widget, text):
        widget.configure(state='normal')
        widget.delete('1.0', 'end')
        widget.insert('1.0', text)
        widget.configure(state='disabled')

    def error(self, exc):
        from tkinter import messagebox
        self.status.set(str(exc))
        messagebox.showerror('HDF5 Viewer', str(exc), parent=self.root)

    def choose(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(filetypes=[('HDF5', '*.h5 *.hdf5 *.hdf *.he5'), ('Tutti i file', '*')])
        if path:
            self.open(path)

    def open(self, path):
        try:
            new_file = h5py.File(path, 'r')
        except Exception as exc:
            self.error(exc)
            return
        if self.file is not None:
            self.file.close()
        self.file = new_file
        self.selected = None
        self.filename.set(str(Path(path).resolve()))
        self.tree.delete(*self.tree.get_children())
        self.nodes.clear()
        self.set_text(self.data, '')
        self.ax.clear()
        self.canvas.draw_idle()
        item = self.tree.insert('', 'end', text='/', open=True)
        self.nodes[item] = ('group', '/', 0)
        self.populate(item, '/', 0)
        self.tree.selection_set(item)
        self.status.set('File aperto in sola lettura')

    def reload(self):
        if self.file is not None:
            self.open(self.file.filename)

    def populate(self, parent, path, offset):
        group = self.file[path]
        for name in islice(group.keys(), offset, offset + PAGE_SIZE):
            full = path.rstrip('/') + '/' + name
            link = group.get(name, getlink=True)
            if isinstance(link, (h5py.SoftLink, h5py.ExternalLink)):
                item = self.tree.insert(parent, 'end', text=f'{name} → {link}')
                self.nodes[item] = ('link', full, 0)
                continue
            try:
                obj = group[name]
                kind = 'group' if isinstance(obj, h5py.Group) else 'dataset'
                suffix = '' if kind == 'group' else (
                    f'  {obj.shape} {obj.dtype}' if isinstance(obj, h5py.Dataset) else f'  tipo: {obj.dtype}')
                item = self.tree.insert(parent, 'end', text=name + suffix)
                self.nodes[item] = (kind, full, 0)
                if kind == 'group' and len(obj):
                    self.tree.insert(item, 'end', text='…')
            except Exception as exc:
                self.tree.insert(parent, 'end', text=f'{name}: {exc}')
        if len(group) > offset + PAGE_SIZE:
            item = self.tree.insert(parent, 'end', text='Mostra altri…')
            self.nodes[item] = ('more', path, offset + PAGE_SIZE)

    def expand(self, event=None):
        item = self.tree.focus()
        node = self.nodes.get(item)
        if node and node[0] == 'group':
            children = self.tree.get_children(item)
            if len(children) == 1 and children[0] not in self.nodes:
                self.tree.delete(children[0])
                try:
                    self.populate(item, node[1], 0)
                except Exception as exc:
                    self.error(exc)

    def select(self, event=None):
        selected = self.tree.selection()
        if not selected:
            return
        item = selected[0]
        node = self.nodes.get(item)
        if not node:
            return
        kind, path, offset = node
        self.selected = None
        try:
            if kind == 'more':
                parent = self.tree.parent(item)
                self.tree.delete(item)
                self.nodes.pop(item)
                self.populate(parent, path, offset)
                return
            if kind == 'link':
                self.set_text(self.info, f'{path}\nLink simbolico/esterno: non seguito automaticamente.')
                self.tabs.select(0)
                return
            obj = self.file[path]
            self.set_text(self.info, describe(obj))
            self.tabs.select(0)
            if isinstance(obj, h5py.Dataset):
                self.selected = path
                for key, value in dict(axis='-1', indices='', start='0', stop='', step='1').items():
                    self.vars[key].set(value)
                self.field.configure(values=[''] + list(obj.dtype.names or ()))
                self.field.set('')
            self.set_text(self.data, '')
            self.ax.clear()
            self.canvas.draw_idle()
            self.status.set('Seleziona intervallo e premi Visualizza' if self.selected else path)
        except Exception as exc:
            self.error(exc)

    def render(self):
        if self.selected is None:
            return
        try:
            ds = self.file[self.selected]
            args = {key: var.get().strip() for key, var in self.vars.items()}
            x, values, selection = read_slice(ds, axis=int(args['axis']), indices=args['indices'],
                start=int(args['start']), stop=int(args['stop']) if args['stop'] else None,
                step=int(args['step']), max_points=self.max_points, field=self.field.get())
            self.set_text(self.data, preview(x, values))
            self.ax.clear()
            if values.ndim == 1 and values.dtype.kind in 'biufc' and len(x):
                if np.iscomplexobj(values):
                    self.ax.plot(x, values.real, label='Reale')
                    self.ax.plot(x, values.imag, label='Immaginaria')
                    self.ax.legend()
                else:
                    self.ax.plot(x, values, linewidth=0.8, marker='.' if len(x) < 100 else None)
                self.ax.set_xlabel('Indice lungo asse selezionato')
                self.ax.set_ylabel('Valore')
                self.ax.set_title(self.selected.rsplit('/', 1)[-1])
                self.ax.grid(True, alpha=0.3)
                self.figure.tight_layout()
                self.tabs.select(2)
            else:
                self.tabs.select(1)
            self.canvas.draw_idle()
            self.status.set(f'{len(x)} punti — selezione {selection}. Campionamento limitato a {self.max_points} punti: picchi stretti possono non apparire.')
        except Exception as exc:
            self.error(exc)

    def close(self):
        if self.file is not None:
            self.file.close()
        self.root.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('file', nargs='?', type=Path)
    parser.add_argument('--max-points', type=int, default=5000)
    parser.add_argument('--inspect', metavar='HDF5_PATH', help='Ispezione da terminale, senza GUI')
    parser.add_argument('--axis', type=int, default=-1)
    parser.add_argument('--indices', default='')
    args = parser.parse_args(argv)
    if args.max_points < 1:
        parser.error('--max-points deve essere positivo')
    if args.inspect is not None:
        if args.file is None:
            parser.error('--inspect richiede un file')
        with h5py.File(args.file, 'r') as f:
            obj = f[args.inspect]
            print(describe(obj))
            if isinstance(obj, h5py.Dataset) and obj.shape is not None:
                x, values, selection = read_slice(obj, args.axis, args.indices, max_points=args.max_points)
                print(f'\nSelezione: {selection}\n{preview(x, values)}')
            elif isinstance(obj, h5py.Group):
                print('\nElementi (primi 200):\n' + '\n'.join(islice(obj.keys(), PAGE_SIZE)))
        return 0
    try:
        import tkinter as tk
    except ImportError as exc:
        parser.exit(1, f'GUI non disponibile: {exc}. Usare Python con Tk oppure --inspect.\n')
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        parser.exit(1, f'Impossibile aprire la GUI: {exc}. Usare --inspect senza display.\n')
    viewer = Viewer(root, args.max_points)
    if args.file:
        viewer.open(args.file)
    root.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
