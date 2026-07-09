# -*- coding: utf-8 -*-
"""Bulk Export - PDF and DWG exporter for sheets and views.

Part of the "Tools By Chulan" suite. Lets you pick sheets or views,
build a custom filename from sheet/project parameters, choose PDF and/or
DWG output with print options, and batch export to a folder.
"""

__title__ = "Bulk\nExp"
__author__ = "Chulan"
__doc__ = "Bulk export sheets / views to PDF and DWG with a custom filename builder."

import os
import os.path as op
import json
import traceback

import clr

# --- assembly references (safe / idempotent) -------------------------------
# NOTE: "System.Core" must be referenced explicitly - it's where
# System.Dynamic.ExpandoObject lives. Without it, IronPython raises
# "ImportError: No module named Dynamic" on the import below.
for _asm in ("System.Core", "System.Windows.Forms", "PresentationFramework",
             "PresentationCore", "WindowsBase", "System.Xaml",
             "System.Diagnostics.Process", "System"):
    try:
        clr.AddReference(_asm)
    except Exception:
        pass

from System import DateTime
from System.Collections.Generic import List
from System.Collections.ObjectModel import ObservableCollection
from System.Dynamic import ExpandoObject
# System.Diagnostics.Process – not available in all IronPython environments;
# fall back gracefully so the rest of the script still loads.
try:
    from System.Diagnostics import Process as _SysProcess
    _HAS_PROCESS = True
except Exception:
    try:
        import clr as _clr2
        _clr2.AddReference("System")
        from System.Diagnostics import Process as _SysProcess
        _HAS_PROCESS = True
    except Exception:
        _SysProcess = None
        _HAS_PROCESS = False


class Process(object):
    """Thin shim so call-sites work whether or not the real Process loaded."""
    @staticmethod
    def Start(cmd, args=""):
        if _HAS_PROCESS:
            try:
                _SysProcess.Start(cmd, args)
                return
            except Exception:
                pass
        # last-resort: use subprocess
        try:
            import subprocess
            subprocess.Popen([cmd] + ([args] if args else []), shell=True)
        except Exception:
            pass
from System.Windows.Controls import TabItem
from System.Windows.Threading import (DispatcherFrame, DispatcherPriority,
                                       DispatcherOperationCallback)
from System.Windows import Visibility, SystemParameters
import System.Windows.Threading as _thr

from pyrevit import forms, revit, script

# Friendly display labels for Revit's internal ViewType enum names, used in
# the view-type filter combo (e.g. ViewType.ThreeD -> "3D").
_VIEWTYPE_LABELS = {"ThreeD": "3D"}
_VIEWTYPE_LABELS_REV = dict((v, k) for k, v in _VIEWTYPE_LABELS.items())


def _view_type_label(t):
    """Raw Revit ViewType name -> friendly display label."""
    return _VIEWTYPE_LABELS.get(t, t)


def _view_type_from_label(lbl):
    """Friendly display label -> raw Revit ViewType name."""
    return _VIEWTYPE_LABELS_REV.get(lbl, lbl)


import Autodesk.Revit.DB as DB
from Autodesk.Revit.DB import (FilteredElementCollector, ViewSheet, View,
                               ViewSheetSet, ElementId, BuiltInCategory,
                               BuiltInParameter, StorageType, ViewType,
                               DWGExportOptions, ViewSet, PrintRange)

# --- optional PDF export (Revit 2022+) -------------------------------------
HAS_PDF = False
try:
    from Autodesk.Revit.DB import PDFExportOptions
    HAS_PDF = True
except Exception:
    PDFExportOptions = None

try:
    from Autodesk.Revit.DB import ColorDepthType
except Exception:
    ColorDepthType = None
try:
    from Autodesk.Revit.DB import RasterQualityType
except Exception:
    RasterQualityType = None
try:
    from Autodesk.Revit.DB import ZoomFitType
except Exception:
    ZoomFitType = None


# ===========================================================================
#  ELEMENT-ID HELPER  (Revit 2025+ replaced ElementId.IntegerValue with .Value)
# ===========================================================================
def _id_int(eid):
    """Return an ElementId's integer key on any Revit version.

    Revit 2025/2026 removed ``ElementId.IntegerValue`` in favour of the
    ``Value`` (Int64) property. Older releases (2021-2024) only expose
    ``IntegerValue``. This helper works on both so sheet-set matching keeps
    working across every supported year.
    """
    if eid is None:
        return None
    try:
        return eid.Value          # Revit 2025+
    except Exception:
        pass
    try:
        return eid.IntegerValue   # Revit 2021-2024
    except Exception:
        return None


# ===========================================================================
#  PATHS
# ===========================================================================
def _bundle(name):
    return op.join(op.dirname(__file__), name)


# ===========================================================================
#  EXPANDOOBJECT HELPERS  (two-way bindable rows for the DataGrids)
# ===========================================================================
def expando(**kw):
    e = ExpandoObject()
    for k in kw:
        setattr(e, k, kw[k])
    return e


def eset(e, key, value):
    setattr(e, key, value)


def eget(e, key):
    try:
        return getattr(e, key)
    except Exception:
        return None


# ===========================================================================
#  REVIT HELPERS
# ===========================================================================
_NAME_PROP = clr.GetClrType(DB.Element).GetProperty("Name")


def elem_name(el):
    """Safe element/type name access (handles overridden Name properties)."""
    if el is None:
        return ""
    try:
        v = _NAME_PROP.GetValue(el, None)
        if v is not None:
            return v
    except Exception:
        pass
    try:
        return el.Name
    except Exception:
        return ""


def sanitize(name):
    if name is None:
        return ""
    bad = '\\/:*?"<>|\r\n\t'
    out = []
    for ch in name:
        out.append("_" if ch in bad else ch)
    return "".join(out).strip()


def param_to_str(p):
    try:
        st = p.StorageType
        if st == StorageType.String:
            return p.AsString() or ""
        if st == StorageType.Integer:
            v = p.AsValueString()
            return v if v else str(p.AsInteger())
        if st == StorageType.Double:
            v = p.AsValueString()
            return v if v else str(p.AsDouble())
        if st == StorageType.ElementId:
            v = p.AsValueString()
            if v:
                return v
            eid = p.AsElementId()
            return str(_id_int(eid)) if eid is not None else ""
        return p.AsValueString() or ""
    except Exception:
        return ""


def param_str_by_name(el, name):
    try:
        p = el.LookupParameter(name)
        if p is not None:
            return param_to_str(p)
    except Exception:
        pass
    return ""


def current_rev(el):
    try:
        p = el.get_Parameter(BuiltInParameter.SHEET_CURRENT_REVISION)
        if p is not None:
            s = p.AsString()
            if s:
                return s
    except Exception:
        pass
    return ""


# --- title-block cache (perf) ----------------------------------------------
# Reading the title block per sheet with a per-sheet FilteredElementCollector
# is the main reason the dialog is slow to open on big models. Instead we
# collect every title-block instance once and map it by the sheet it sits on
# (OwnerViewId), turning per-sheet lookups into dictionary hits.
_TB_MAP = {"doc": None, "map": {}}
_TB_TYPE_NAME = {}


def build_titleblock_cache(doc):
    """(Re)build the sheet-id -> title-block map for this document."""
    m = {}
    try:
        col = FilteredElementCollector(doc) \
            .OfCategory(BuiltInCategory.OST_TitleBlocks) \
            .WhereElementIsNotElementType().ToElements()
        for tb in col:
            try:
                oid = _id_int(tb.OwnerViewId)
                if oid is not None and oid not in m:
                    m[oid] = tb
            except Exception:
                pass
    except Exception:
        pass
    _TB_MAP["doc"] = doc
    _TB_MAP["map"] = m
    _TB_TYPE_NAME.clear()
    return m


def _titleblock(doc, sheet):
    # Fast path: cached map keyed by the owner sheet id.
    try:
        if _TB_MAP.get("doc") is not doc:
            build_titleblock_cache(doc)
        tb = _TB_MAP["map"].get(_id_int(sheet.Id))
        if tb is not None:
            return tb
        # Sheet is in the map's doc but has no title block -> genuinely none.
        if _TB_MAP.get("doc") is doc:
            return None
    except Exception:
        pass
    # Fallback: original per-sheet collector (only if cache unavailable).
    try:
        col = FilteredElementCollector(doc, sheet.Id) \
            .OfCategory(BuiltInCategory.OST_TitleBlocks) \
            .WhereElementIsNotElementType().ToElements()
        for tb in col:
            return tb
    except Exception:
        pass
    return None


def sheet_size(doc, sheet):
    tb = _titleblock(doc, sheet)
    if tb is None:
        return ""
    try:
        tid = _id_int(tb.GetTypeId())
        if tid in _TB_TYPE_NAME:
            return _TB_TYPE_NAME[tid]
        sym = doc.GetElement(tb.GetTypeId())
        n = elem_name(sym) or ""
        _TB_TYPE_NAME[tid] = n
        return n
    except Exception:
        pass
    return ""



def _sheet_dims(doc, sheet):
    tb = _titleblock(doc, sheet)
    if tb is None:
        return (0.0, 0.0)
    w = 0.0
    h = 0.0
    try:
        pw = tb.get_Parameter(BuiltInParameter.SHEET_WIDTH)
        ph = tb.get_Parameter(BuiltInParameter.SHEET_HEIGHT)
        if pw is not None:
            w = pw.AsDouble()
        if ph is not None:
            h = ph.AsDouble()
    except Exception:
        pass
    return (w, h)


def orient(doc, sheet):
    w, h = _sheet_dims(doc, sheet)
    if w > 0 and h > 0:
        return "Landscape" if w >= h else "Portrait"
    return "-"


def default_name(doc, el):
    try:
        if isinstance(el, ViewSheet):
            return sanitize("{0} - {1}".format(el.SheetNumber, elem_name(el)))
    except Exception:
        pass
    return sanitize(elem_name(el)) or "Unnamed"


def natural_key(s):
    s = s or ""
    out = []
    num = ""
    for ch in s:
        if ch.isdigit():
            num += ch
        else:
            if num:
                out.append((1, int(num), ""))
                num = ""
            out.append((0, 0, ch.lower()))
    if num:
        out.append((1, int(num), ""))
    return out


# --- filename token model ---------------------------------------------------
BASE_FIELDS = [
    "Sheet Number", "Sheet Name", "Approved By", "Checked By",
    "Current Revision", "Current Revision Date", "Current Revision Description",
    "Current Revision Issued By", "Current Revision Issued To", "Dependency",
    "Designed By", "Drawn By", "Referencing Sheet", "Scale",
    "Sheet Issue Date", "View Template",
    "Year (current)", "Month (current)", "Day (current)",
    "Hour (current)", "Minute (current)", "Second (current)",
]


def resolve_field(doc, el, field):
    now = DateTime.Now
    date_tokens = {
        "Year (current)": str(now.Year),
        "Month (current)": "{0:02d}".format(now.Month),
        "Day (current)": "{0:02d}".format(now.Day),
        "Hour (current)": "{0:02d}".format(now.Hour),
        "Minute (current)": "{0:02d}".format(now.Minute),
        "Second (current)": "{0:02d}".format(now.Second),
    }
    if field in date_tokens:
        return date_tokens[field]
    if field == "Sheet Number":
        try:
            return el.SheetNumber
        except Exception:
            return ""
    if field == "Sheet Name":
        return elem_name(el)
    if field == "Current Revision":
        return current_rev(el)
    val = param_str_by_name(el, field)
    if val:
        return val
    try:
        val = param_str_by_name(doc.ProjectInformation, field)
    except Exception:
        val = ""
    return val or ""


def resolve_tokens(doc, el, tokens, field_sep):
    if not tokens:
        return default_name(doc, el)
    parts = []
    prev_value = False
    for t in tokens:
        kind = t.get("kind")
        value = t.get("value", "")
        if kind == "sep":
            parts.append(value)
            prev_value = False
            continue
        if kind == "custom":
            resolved = value
        else:
            resolved = resolve_field(doc, el, value)
        if field_sep and prev_value:
            parts.append(field_sep)
        parts.append(resolved if resolved is not None else "")
        prev_value = True
    name = sanitize("".join(parts))
    return name or default_name(doc, el)


def expand_env(path):
    if not path:
        return path
    try:
        p = os.path.expandvars(path)
    except Exception:
        p = path
    now = DateTime.Now
    reps = [
        ("%yy", "{0:02d}".format(now.Year % 100)),
        ("%mm", "{0:02d}".format(now.Month)),
        ("%dd", "{0:02d}".format(now.Day)),
        ("%Y", str(now.Year)),
        ("%m", str(now.Month)),
        ("%d", str(now.Day)),
        ("%H", "{0:02d}".format(now.Hour)),
        ("%M", "{0:02d}".format(now.Minute)),
        ("%S", "{0:02d}".format(now.Second)),
    ]
    for k, v in reps:
        p = p.replace(k, v)
    return p


# --- WPF DoEvents -----------------------------------------------------------
def _exit_frame(frame):
    frame.Continue = False
    return None


def do_events():
    frame = DispatcherFrame()
    try:
        _thr.Dispatcher.CurrentDispatcher.BeginInvoke(
            DispatcherPriority.Background,
            DispatcherOperationCallback(_exit_frame),
            frame)
        _thr.Dispatcher.PushFrame(frame)
    except Exception:
        pass


# --- collectors -------------------------------------------------------------
def collect_sheets(doc):
    res = []
    try:
        col = FilteredElementCollector(doc).OfClass(ViewSheet).ToElements()
        for s in col:
            try:
                if s.IsPlaceholder:
                    continue
            except Exception:
                pass
            res.append(s)
    except Exception:
        pass
    res.sort(key=lambda x: natural_key(_safe_sheet_no(x)))
    return res


def _safe_sheet_no(s):
    try:
        return s.SheetNumber
    except Exception:
        return ""


def sheet_printable(s):
    """True if the sheet can be printed / appears in the sheet list.

    Falls back to True when the API doesn't expose the flag."""
    try:
        v = s.CanBePrinted
        if v is not None:
            return bool(v)
    except Exception:
        pass
    try:
        p = s.get_Parameter(BuiltInParameter.SHEET_SCHEDULED)
        if p is not None:
            return bool(p.AsInteger())
    except Exception:
        pass
    return True


def sheet_collection(doc, s):
    """Best-effort 'Sheet Collection' / browser grouping label for a sheet."""
    for nm in ("Sheet Collection", "Sheet Group", "Sheet Series",
               "Discipline", "Sub-Discipline"):
        try:
            v = param_str_by_name(s, nm)
            if v:
                return v
        except Exception:
            pass
    return ""


def collect_views(doc):
    res = []
    try:
        col = FilteredElementCollector(doc).OfClass(View).ToElements()
        for v in col:
            try:
                if v.IsTemplate:
                    continue
                if isinstance(v, ViewSheet):
                    continue
                if not v.CanBePrinted:
                    continue
            except Exception:
                continue
            res.append(v)
    except Exception:
        pass
    res.sort(key=lambda x: natural_key(elem_name(x)))
    return res


def list_dwg_setup_names(doc):
    """Named DWG/DXF export setups saved in this project (Modify DWG/DXF
    Export Setup dialog). Returns [] on older API versions that don't
    expose ExportDWGSettings as a queryable element class."""
    names = []
    try:
        cls = getattr(DB, "ExportDWGSettings", None)
        if cls is None:
            return names
        col = FilteredElementCollector(doc).OfClass(cls).ToElements()
        for s in col:
            nm = elem_name(s)
            if nm and nm not in names:
                names.append(nm)
    except Exception:
        pass
    names.sort(key=natural_key)
    return names


def sheet_shared_param_names(doc):
    """Names of *shared* parameters bound to sheets in this project.

    Shared parameters bound to the Sheets category appear on every sheet, so
    sampling one real sheet and keeping those where ``Parameter.IsShared`` is
    true gives the full list quickly and reliably (no ParameterBindings guess-
    work about which definitions are shared)."""
    names = []
    seen = set()
    try:
        sheets = FilteredElementCollector(doc).OfClass(ViewSheet) \
            .WhereElementIsNotElementType().ToElements()
    except Exception:
        sheets = []
    for s in sheets:
        try:
            if s.IsPlaceholder:
                continue
        except Exception:
            pass
        try:
            for p in s.Parameters:
                try:
                    if not p.IsShared:
                        continue
                    nm = p.Definition.Name
                    if nm and nm not in seen:
                        seen.add(nm)
                        names.append(nm)
                except Exception:
                    pass
        except Exception:
            pass
        break  # one non-placeholder sheet is representative (category binding)
    names.sort(key=natural_key)
    return names


# ===========================================================================
#  SAVED NAMING FORMATS  (persisted to %APPDATA%\CA Tools\...)
# ===========================================================================
def _ca_config_dir():
    """Return (creating if needed) the CA Tools config folder."""
    base = os.environ.get("APPDATA") or op.expanduser("~")
    path = op.join(base, "CA Tools")
    try:
        if not op.isdir(path):
            os.makedirs(path)
    except Exception:
        pass
    return path


def _formats_path():
    return op.join(_ca_config_dir(), "bulk_export_formats.json")


def load_naming_formats():
    """Return the list of saved naming formats (never raises)."""
    try:
        p = _formats_path()
        if not op.isfile(p):
            return []
        with open(p, "r") as fh:
            data = json.load(fh)
        fmts = data.get("formats", []) if isinstance(data, dict) else []
        out = []
        for f in fmts:
            try:
                name = f.get("name")
                if not name:
                    continue
                out.append({
                    "name": name,
                    "tokens": list(f.get("tokens", []) or []),
                    "field_sep": f.get("field_sep", None),
                })
            except Exception:
                pass
        return out
    except Exception:
        return []


def save_naming_formats(formats):
    """Persist the list of naming formats. Returns True on success."""
    try:
        with open(_formats_path(), "w") as fh:
            json.dump({"formats": formats}, fh, indent=2)
        return True
    except Exception:
        return False


def _safe_int(s, default):
    try:
        return int(float(str(s).strip()))
    except Exception:
        return default


# ===========================================================================
#  CUSTOM FILENAME DIALOG
# ===========================================================================
class ParamDialog(forms.WPFWindow):

    def __init__(self, doc, tokens, field_sep):
        self._ready = False
        forms.WPFWindow.__init__(self, _bundle("ParamDialog.xaml"))
        self.doc = doc
        self.ok = False
        self.result_tokens = list(tokens) if tokens else []
        self.field_sep = field_sep
        self.sel = list(tokens) if tokens else []

        self._fill_available()

        if field_sep:
            self.chk_field_sep.IsChecked = True
            self.txt_field_sep.Text = field_sep
        else:
            self.chk_field_sep.IsChecked = False

        self._rebuild_selected()
        self._refresh_saved_combo()
        self._ready = True
        self._update_preview()

    # -- available list --
    def _fill_available(self):
        self.lst_available.Items.Clear()
        fields = list(BASE_FIELDS)
        if self.chk_include_proj.IsChecked:
            try:
                pinfo = self.doc.ProjectInformation
                for p in pinfo.Parameters:
                    nm = p.Definition.Name
                    if nm and nm not in fields:
                        fields.append(nm)
            except Exception:
                pass
        if self.chk_include_shared.IsChecked:
            try:
                for nm in sheet_shared_param_names(self.doc):
                    if nm and nm not in fields:
                        fields.append(nm)
            except Exception:
                pass
        for f in fields:
            self.lst_available.Items.Add(f)

    def on_include_proj(self, sender, args):
        if not getattr(self, "_ready", False):
            return
        self._fill_available()

    def on_include_shared(self, sender, args):
        if not getattr(self, "_ready", False):
            return
        self._fill_available()

    # -- selected list --
    def _tok_text(self, t):
        kind = t.get("kind")
        value = t.get("value", "")
        if kind == "field":
            return value
        if kind == "custom":
            return '"{0}"'.format(value)
        return "[ {0} ]".format(value)

    def _rebuild_selected(self):
        self.lst_selected.Items.Clear()
        for t in self.sel:
            self.lst_selected.Items.Add(self._tok_text(t))
        self._update_preview()

    def _update_preview(self):
        fs = self.txt_field_sep.Text if self.chk_field_sep.IsChecked else None
        parts = []
        prev = False
        for t in self.sel:
            if t.get("kind") == "sep":
                parts.append(t.get("value", ""))
                prev = False
                continue
            if fs and prev:
                parts.append(fs)
            if t.get("kind") == "custom":
                parts.append(t.get("value", ""))
            else:
                parts.append("<{0}>".format(t.get("value", "")))
            prev = True
        self.lbl_preview.Text = "".join(parts) if parts else "(default: sheet number)"

    # -- buttons --
    def on_add(self, sender, args):
        items = list(self.lst_available.SelectedItems)
        if not items:
            return
        for it in items:
            self.sel.append({"kind": "field", "value": str(it)})
        self._rebuild_selected()

    def on_remove(self, sender, args):
        idxs = sorted([self.lst_selected.Items.IndexOf(it)
                       for it in self.lst_selected.SelectedItems], reverse=True)
        for i in idxs:
            if 0 <= i < len(self.sel):
                del self.sel[i]
        self._rebuild_selected()

    def on_add_custom_field(self, sender, args):
        txt = self.txt_custom_field.Text
        if txt and txt.strip():
            self.sel.append({"kind": "custom", "value": txt.strip()})
            self.txt_custom_field.Text = ""
            self._rebuild_selected()

    def on_add_custom_sep(self, sender, args):
        txt = self.txt_custom_sep.Text
        if txt is not None and len(txt) > 0:
            self.sel.append({"kind": "sep", "value": txt})
            self.txt_custom_sep.Text = ""
            self._rebuild_selected()

    def _sel_index(self):
        return self.lst_selected.SelectedIndex

    def on_move_top(self, sender, args):
        i = self._sel_index()
        if i > 0:
            self.sel.insert(0, self.sel.pop(i))
            self._rebuild_selected()
            self.lst_selected.SelectedIndex = 0

    def on_move_up(self, sender, args):
        i = self._sel_index()
        if i > 0:
            self.sel[i - 1], self.sel[i] = self.sel[i], self.sel[i - 1]
            self._rebuild_selected()
            self.lst_selected.SelectedIndex = i - 1

    def on_move_down(self, sender, args):
        i = self._sel_index()
        if 0 <= i < len(self.sel) - 1:
            self.sel[i + 1], self.sel[i] = self.sel[i], self.sel[i + 1]
            self._rebuild_selected()
            self.lst_selected.SelectedIndex = i + 1

    def on_move_bottom(self, sender, args):
        i = self._sel_index()
        if 0 <= i < len(self.sel) - 1:
            self.sel.append(self.sel.pop(i))
            self._rebuild_selected()
            self.lst_selected.SelectedIndex = len(self.sel) - 1

    def on_reset(self, sender, args):
        self.sel = []
        self._rebuild_selected()

    def on_field_sep_toggle(self, sender, args):
        if not getattr(self, "_ready", False):
            return
        self._update_preview()

    def on_field_sep_text(self, sender, args):
        if not getattr(self, "_ready", False):
            return
        self._update_preview()

    def on_ok(self, sender, args):
        self.field_sep = self.txt_field_sep.Text if self.chk_field_sep.IsChecked else None
        self.result_tokens = list(self.sel)
        self.ok = True
        self.Close()

    def on_cancel(self, sender, args):
        self.ok = False
        self.Close()

    # -- saved naming formats --
    def _refresh_saved_combo(self, select_name=None):
        """Reload the saved-format names into the dropdown."""
        self._formats = load_naming_formats()
        try:
            self.cmb_saved.Items.Clear()
            for f in self._formats:
                self.cmb_saved.Items.Add(f["name"])
            if select_name:
                for i, f in enumerate(self._formats):
                    if f["name"] == select_name:
                        self.cmb_saved.SelectedIndex = i
                        break
            elif self.cmb_saved.Items.Count > 0:
                self.cmb_saved.SelectedIndex = 0
        except Exception:
            pass

    def _selected_format(self):
        try:
            i = self.cmb_saved.SelectedIndex
            if 0 <= i < len(getattr(self, "_formats", [])):
                return self._formats[i]
        except Exception:
            pass
        return None

    def on_save_format(self, sender, args):
        if not getattr(self, "_ready", False):
            return
        if not self.sel:
            forms.alert("Add at least one parameter to the "
                        "'Selected Parameters' list before saving a format.",
                        title="Save Naming Format")
            return
        name = forms.ask_for_string(
            default="",
            prompt="Enter a name for this naming format:",
            title="Save Naming Format")
        if not name:
            return
        name = name.strip()
        if not name:
            return
        field_sep = self.txt_field_sep.Text if self.chk_field_sep.IsChecked else None
        formats = load_naming_formats()
        entry = {"name": name,
                 "tokens": list(self.sel),
                 "field_sep": field_sep}
        # Overwrite a format with the same name, otherwise append.
        replaced = False
        for i, f in enumerate(formats):
            if f.get("name") == name:
                if not forms.alert(
                        "A format named '{0}' already exists. "
                        "Overwrite it?".format(name),
                        title="Save Naming Format",
                        yes=True, no=True):
                    return
                formats[i] = entry
                replaced = True
                break
        if not replaced:
            formats.append(entry)
        if save_naming_formats(formats):
            self._refresh_saved_combo(select_name=name)
        else:
            forms.alert("Could not save the naming format to disk.",
                        title="Save Naming Format")

    def on_load_format(self, sender, args):
        if not getattr(self, "_ready", False):
            return
        fmt = self._selected_format()
        if not fmt:
            forms.alert("Pick a saved format from the list first.",
                        title="Load Naming Format")
            return
        # Apply tokens.
        self.sel = [dict(t) for t in fmt.get("tokens", []) if isinstance(t, dict)]
        # Apply the field separator.
        fs = fmt.get("field_sep", None)
        try:
            if fs:
                self.chk_field_sep.IsChecked = True
                self.txt_field_sep.Text = fs
            else:
                self.chk_field_sep.IsChecked = False
        except Exception:
            pass
        self._rebuild_selected()

    def on_delete_format(self, sender, args):
        if not getattr(self, "_ready", False):
            return
        fmt = self._selected_format()
        if not fmt:
            forms.alert("Pick a saved format from the list first.",
                        title="Delete Naming Format")
            return
        name = fmt.get("name", "")
        if not forms.alert("Delete the saved format '{0}'?".format(name),
                           title="Delete Naming Format", yes=True, no=True):
            return
        formats = [f for f in load_naming_formats() if f.get("name") != name]
        if save_naming_formats(formats):
            self._refresh_saved_combo()
        else:
            forms.alert("Could not update the saved formats on disk.",
                        title="Delete Naming Format")


# ===========================================================================
#  EXPORT-COMPLETE TOAST  (small popup with an "Open Folder" shortcut)
# ===========================================================================
class ExportToast(forms.WPFWindow):

    def __init__(self, folder, ok_count, total, report_path=None):
        forms.WPFWindow.__init__(self, _bundle("ExportToast.xaml"))
        self.folder = folder
        try:
            self.lbl_sub.Text = "{0} of {1} file(s) exported.".format(ok_count, total)
        except Exception:
            pass
        # WindowStartupLocation="CenterScreen" handles positioning in XAML

    def on_open_folder(self, sender, args):
        opened = False
        try:
            Process.Start("explorer.exe", '"{0}"'.format(self.folder))
            opened = True
        except Exception:
            pass
        if not opened:
            try:
                os.startfile(self.folder)
                opened = True
            except Exception:
                pass
        if not opened:
            try:
                forms.alert("Could not open the folder:\n{0}".format(self.folder),
                            title="Bulk Export")
            except Exception:
                pass
        self.Close()

    def on_close(self, sender, args):
        self.Close()


# ===========================================================================
#  MAIN WINDOW
# ===========================================================================

# ===========================================================================
#  HELPERS
# ===========================================================================
def _do_events():
    """Pump the WPF dispatcher so pending renders flush (like Application.DoEvents)."""
    try:
        frame = DispatcherFrame()
        def _exit(op):
            frame.Continue = False
            return None
        _thr.Dispatcher.CurrentDispatcher.BeginInvoke(
            DispatcherPriority.Background,
            DispatcherOperationCallback(_exit),
            None)
        _thr.Dispatcher.PushFrame(frame)
    except Exception:
        pass


# ===========================================================================
#  LOADING SPLASH
# ===========================================================================
class LoadingWindow(forms.WPFWindow):
    """Lightweight splash shown while BulkExportWindow initialises."""

    _STEPS = [
        "[1/2] Reading Sheets from document...",
        "[2/2] Reading Views from document...",
    ]

    def __init__(self):
        forms.WPFWindow.__init__(self, _bundle("LoadingWindow.xaml"))
        self._step  = 0
        self._pct   = 0
        self._timer = None
        self._start_timer()

    # ── timer ──────────────────────────────────────────────────────────────
    def _start_timer(self):
        try:
            from System.Windows.Threading import DispatcherTimer
            from System import TimeSpan
            self._timer = DispatcherTimer()
            self._timer.Interval = TimeSpan.FromMilliseconds(420)
            self._timer.Tick    += self._on_tick
            self._timer.Start()
        except Exception:
            pass

    def _on_tick(self, sender, args):
        try:
            self._pct = min(self._pct + 8, 95)           # creep toward 95 %
            if self._pct > 50 and self._step == 0:
                self._step = 1
            self.lbl_status.Text = self._STEPS[self._step]
            self.lbl_dots.Text   = "Completed {0}%".format(self._pct)
        except Exception:
            pass

    def close_splash(self):
        try:
            if self._timer is not None:
                self._timer.Stop()
        except Exception:
            pass
        try:
            self.lbl_status.Text = "[2/2] Reading Views from document..."
            self.lbl_dots.Text   = "Completed 100%"
        except Exception:
            pass
        try:
            self.Close()
        except Exception:
            pass


class PrintOrderDialog(forms.WPFWindow):
    """Edit Print Order in All Sheets - matches the Revit print-order dialog.

    Lets the user choose Browser organization / Sheet Number (Ascending) /
    Manual order and, in manual mode, reorder rows with the move buttons."""

    def __init__(self, doc, sheet_rows, mode, manual_ids, only_selected=False):
        self._building = True
        forms.WPFWindow.__init__(self, _bundle("PrintOrderDialog.xaml"))
        self.doc = doc
        self.ok = False
        self.mode = mode or "number"
        self.ordered_ids = list(manual_ids or [])

        # build a working list of display rows (keep element handles)
        self._src = []
        for r in sheet_rows:
            el = eget(r, "_elem")
            try:
                eid = _id_int(el.Id)
            except Exception:
                eid = 0
            d = expando(
                PrintOrder="",
                Type="Sheet",
                Collection=eget(r, "_collection") or "",
                Number=eget(r, "SheetNumber") or "",
                Revision=eget(r, "Revision") or "",
                Name=eget(r, "SheetName") or "",
            )
            eset(d, "_eid", eid)
            self._src.append(d)

        self._coll = ObservableCollection[object]()
        self.dg_order.ItemsSource = self._coll

        # initialise radio to the incoming mode
        try:
            if self.mode == "browser":
                self.rb_browser.IsChecked = True
            elif self.mode == "manual":
                self.rb_manual.IsChecked = True
            else:
                self.rb_number.IsChecked = True
        except Exception:
            pass

        self._building = False
        self._resort()

        # Reflect whether we are ordering just the selected sheets or all.
        try:
            n = len(self._src)
            if only_selected:
                self.Title = "Edit Print Order in Selected Sheets ({0})".format(n)
            else:
                self.Title = "Edit Print Order in All Sheets ({0})".format(n)
        except Exception:
            pass

    # ---------------------------------------------------------------- sorting
    def _current_mode(self):
        try:
            if self.rb_browser.IsChecked:
                return "browser"
            if self.rb_manual.IsChecked:
                return "manual"
        except Exception:
            pass
        return "number"

    def _resort(self):
        mode = self._current_mode()
        rows = list(self._src)
        if mode == "manual" and self.ordered_ids:
            order = {}
            for i, eid in enumerate(self.ordered_ids):
                order[eid] = i
            big = len(self.ordered_ids) + 1
            rows.sort(key=lambda d: order.get(eget(d, "_eid"), big))
        elif mode == "browser":
            rows.sort(key=lambda d: [(0, 0, (eget(d, "Collection") or "").lower())]
                      + natural_key(eget(d, "Number") or ""))
        elif mode == "number":
            rows.sort(key=lambda d: natural_key(eget(d, "Number") or ""))
        # manual with no stored order -> keep current source order
        self._src = rows
        self._rebuild()

    def _rebuild(self):
        self._coll.Clear()
        for i, d in enumerate(self._src):
            eset(d, "PrintOrder", str(i + 1))
            self._coll.Add(d)
        try:
            self.lbl_count.Text = "{0} sheet(s)".format(len(self._src))
            self.pnl_manual.IsEnabled = (self._current_mode() == "manual")
        except Exception:
            pass

    def on_sort_changed(self, sender, args):
        if getattr(self, "_building", True):
            return
        self._resort()

    # ------------------------------------------------------------- move (manual)
    def _switch_to_manual(self):
        try:
            if not self.rb_manual.IsChecked:
                self._building = True
                self.rb_manual.IsChecked = True
                self._building = False
        except Exception:
            pass

    def _sel_indices(self):
        idxs = []
        try:
            for it in self.dg_order.SelectedItems:
                i = self._src.index(it)
                if i >= 0:
                    idxs.append(i)
        except Exception:
            pass
        return sorted(idxs)

    def _commit_manual(self):
        self.ordered_ids = [eget(d, "_eid") for d in self._src]

    def on_move_up(self, sender, args):
        self._switch_to_manual()
        for i in self._sel_indices():
            if i > 0:
                self._src[i - 1], self._src[i] = self._src[i], self._src[i - 1]
        self._commit_manual()
        self._rebuild()

    def on_move_down(self, sender, args):
        self._switch_to_manual()
        for i in reversed(self._sel_indices()):
            if i < len(self._src) - 1:
                self._src[i + 1], self._src[i] = self._src[i], self._src[i + 1]
        self._commit_manual()
        self._rebuild()

    def on_move_top(self, sender, args):
        self._switch_to_manual()
        sel = self._sel_indices()
        picked = [self._src[i] for i in sel]
        rest = [d for j, d in enumerate(self._src) if j not in sel]
        self._src = picked + rest
        self._commit_manual()
        self._rebuild()

    def on_move_bottom(self, sender, args):
        self._switch_to_manual()
        sel = self._sel_indices()
        picked = [self._src[i] for i in sel]
        rest = [d for j, d in enumerate(self._src) if j not in sel]
        self._src = rest + picked
        self._commit_manual()
        self._rebuild()

    # ---------------------------------------------------------------- ok/cancel
    def on_ok(self, sender, args):
        self.mode = self._current_mode()
        self.ordered_ids = [eget(d, "_eid") for d in self._src]
        self.ok = True
        self.Close()

    def on_cancel(self, sender, args):
        self.ok = False
        self.Close()


class BulkExportWindow(forms.WPFWindow):

    def __init__(self):
        self._ready = False
        forms.WPFWindow.__init__(self, _bundle("BulkExportWindow.xaml"))
        self.doc = revit.doc
        self._tokens = []
        self._field_sep = None
        self._exporting = False

        self._sheet_rows = []
        self._view_rows = []
        self._create_rows = []
        self._sel_items = ObservableCollection[object]()

        # ---- sheet-ordering state -------------------------------------
        self._print_order_mode = "number"   # number | browser | manual
        self._manual_ids = []               # ordered element-id ints
        self._drawing_list = None           # ordered list of sheet numbers
        self._drawing_list_path = None

        self._populate_combos()
        self._build_sheet_rows()
        self._build_view_rows()

        self.dg_selection.ItemsSource = self._sel_items
        self._apply_mode()
        self._sync_nav(0)

        if not HAS_PDF:
            try:
                self.chk_pdf.IsChecked = False
                self.chk_pdf.IsEnabled = False
                self.chk_dwg.IsChecked = True
            except Exception:
                pass

        self._ready = True
        # Apply initial panel visibility to match the default checkbox states.
        self.on_format_toggle(None, None)
        self._update_status()

    # ------------------------------------------------------------------ setup
    def _populate_combos(self):
        # view-type filter
        self.cmb_view_filter.Items.Clear()
        self.cmb_view_filter.Items.Add("All Views")
        self.cmb_view_filter.SelectedIndex = 0

        # saved view/sheet sets
        self._refresh_vs_sets()

        # DWG/DXF export setups saved in this Revit model (Modify DWG/DXF
        # Export Setup dialog) - picking one here applies its layer/color
        # mapping so exported DWGs come out with the correct colors.
        self.cmb_dwg_setup.Items.Clear()
        self.cmb_dwg_setup.Items.Add("Revit Default (no named setup)")
        self._dwg_setups = list_dwg_setup_names(self.doc)
        for nm in self._dwg_setups:
            self.cmb_dwg_setup.Items.Add(nm)
        if len(self._dwg_setups) == 1:
            self.cmb_dwg_setup.SelectedIndex = 1
        else:
            self.cmb_dwg_setup.SelectedIndex = 0

        # index digits 1..6, default 4 (index 3)
        self._fill_combo(self.cmb_index_digits, ["1", "2", "3", "4", "5", "6"], 3)

        self._fill_combo(self.cmb_margin, ["No Margin", "Printer Limit"], 0)
        self._fill_combo(self.cmb_raster, ["Low", "Medium", "High", "Presentation"], 1)
        self._fill_combo(self.cmb_colors, ["Color", "Grayscale", "Black Line"], 0)
        self._fill_combo(self.cmb_report,
                         ["Don't Save Report", "Save Report (TXT)", "Save Report (CSV)"], 0)
        self._fill_combo(self.cmb_paper_orient,
                         ["Automatic (per sheet)", "Landscape", "Portrait"], 0)

    def _fill_combo(self, combo, items, idx):
        combo.Items.Clear()
        for it in items:
            combo.Items.Add(it)
        if combo.Items.Count > 0:
            combo.SelectedIndex = idx

    def _refresh_view_filter(self):
        """Populate the view-type filter from the collected views."""
        types = []
        for r in self._view_rows:
            t = eget(r, "_vtype")
            if t and t not in types:
                types.append(t)
        types.sort()
        self.cmb_view_filter.Items.Clear()
        self.cmb_view_filter.Items.Add("All Views")
        for t in types:
            self.cmb_view_filter.Items.Add(_view_type_label(t))
        self.cmb_view_filter.SelectedIndex = 0

    def _build_sheet_rows(self):
        self._sheet_rows = []
        build_titleblock_cache(self.doc)   # one pass, then O(1) size lookups
        for s in collect_sheets(self.doc):
            fname = resolve_tokens(self.doc, s, self._tokens, self._field_sep)
            printable = sheet_printable(s)
            row = expando(
                IsSelected=False,
                Index="",
                SheetNumber=_safe_sheet_no(s),
                SheetName=elem_name(s),
                Revision=current_rev(s),
                Size=sheet_size(self.doc, s),
                CustomFilename=fname,
                IsPrintable=printable,
            )
            eset(row, "_elem", s)
            eset(row, "_isheet", True)
            eset(row, "_printable", printable)
            eset(row, "_collection", sheet_collection(self.doc, s))
            self._sheet_rows.append(row)
        self._apply_ordering()

    def _build_view_rows(self):
        self._view_rows = []
        for v in collect_views(self.doc):
            fname = resolve_tokens(self.doc, v, self._tokens, self._field_sep)
            try:
                vt = str(v.ViewType)
            except Exception:
                vt = ""
            row = expando(
                IsSelected=False,
                Index="",
                SheetNumber="",
                SheetName=elem_name(v),
                Revision="",
                Size="-",
                CustomFilename=fname,
                IsPrintable=True,
            )
            eset(row, "_elem", v)
            eset(row, "_isheet", False)
            eset(row, "_vtype", vt)
            eset(row, "_printable", True)
            self._view_rows.append(row)
        self._refresh_view_filter()

    # -------------------------------------------------------------- ordering
    def _order_key_number(self, r):
        return natural_key(eget(r, "SheetNumber") or "")

    def _order_key_browser(self, r):
        coll = (eget(r, "_collection") or "").lower()
        return [(0, 0, coll)] + natural_key(eget(r, "SheetNumber") or "")

    def _reorder_sheet_rows(self):
        """Sort self._sheet_rows in place per the active mode / drawing list."""
        rows = list(self._sheet_rows)

        if self._drawing_list:
            pos = {}
            for i, num in enumerate(self._drawing_list):
                pos[str(num).strip().lower()] = i
            big = len(self._drawing_list) + 1

            def dl_key(r):
                num = (eget(r, "SheetNumber") or "").strip().lower()
                return (pos.get(num, big),)
            rows.sort(key=dl_key)
        elif self._print_order_mode == "manual" and self._manual_ids:
            order = {}
            for i, eid in enumerate(self._manual_ids):
                order[eid] = i
            big = len(self._manual_ids) + 1

            def man_key(r):
                el = eget(r, "_elem")
                try:
                    return (order.get(_id_int(el.Id), big),)
                except Exception:
                    return (big,)
            rows.sort(key=man_key)
        elif self._print_order_mode == "browser":
            rows.sort(key=self._order_key_browser)
        else:  # number (default)
            rows.sort(key=self._order_key_number)

        try:
            if bool(self.chk_reverse.IsChecked):
                rows.reverse()
        except Exception:
            pass

        self._sheet_rows = rows

    def _compute_index(self):
        """Assign a zero-padded running Index to each sheet row."""
        try:
            digits = int(str(self.cmb_index_digits.SelectedItem or "4"))
        except Exception:
            digits = 4
        start = _safe_int(getattr(self.txt_index_start, "Text", "0"), 0)
        # Non-printable sheets are never indexed (toggle removed from the UI).
        index_nonprint = False

        n = start
        for r in self._sheet_rows:
            printable = eget(r, "_printable")
            if printable or index_nonprint:
                eset(r, "Index", str(n).zfill(digits))
                n += 1
            else:
                eset(r, "Index", "")

    def _apply_ordering(self):
        self._reorder_sheet_rows()
        self._compute_index()
        if getattr(self, "_ready", False):
            self._apply_mode()

    # ------------------------------------------------------------- selection
    def _base_rows(self):
        return self._sheet_rows if self.rb_sheets.IsChecked else self._view_rows

    def _apply_mode(self):
        """Rebuild the filtered ObservableCollection bound to dg_selection."""
        is_sheets = self.rb_sheets.IsChecked
        try:
            self.cmb_view_filter.IsEnabled = (not is_sheets)
        except Exception:
            pass

        search = ""
        try:
            search = (self.txt_search.Text or "").strip().lower()
        except Exception:
            pass

        vfilter = "All Views"
        try:
            if self.cmb_view_filter.SelectedItem is not None:
                vfilter = _view_type_from_label(str(self.cmb_view_filter.SelectedItem))
        except Exception:
            pass

        # Non-printable sheets are always shown in the list (toggle removed
        # from the UI); they simply receive no index number.
        show_nonprint = True

        # "Show only sheets in set" filter -> restrict the visible list to the
        # members of the currently selected sheet set.
        only_set = False
        try:
            only_set = bool(self.chk_only_set.IsChecked)
        except Exception:
            pass
        active_ids = getattr(self, "_active_set_ids", None) or set()
        set_filter_on = only_set and bool(active_ids)

        self._sel_items.Clear()
        for r in self._base_rows():
            if set_filter_on:
                el = eget(r, "_elem")
                try:
                    if _id_int(el.Id) not in active_ids:
                        continue
                except Exception:
                    continue
            if is_sheets and (not show_nonprint) and (not eget(r, "_printable")):
                continue
            if (not is_sheets) and vfilter != "All Views":
                if eget(r, "_vtype") != vfilter:
                    continue
            if search:
                num = (eget(r, "SheetNumber") or "").lower()
                nam = (eget(r, "SheetName") or "").lower()
                if search not in num and search not in nam:
                    continue
            self._sel_items.Add(r)

        self._sync_check_all()

    def _sync_check_all(self):
        items = list(self._sel_items)
        if not items:
            self.chk_all.IsChecked = False
            return
        all_on = True
        any_on = False
        for r in items:
            if eget(r, "IsSelected"):
                any_on = True
            else:
                all_on = False
        if all_on:
            self.chk_all.IsChecked = True
        elif any_on:
            self.chk_all.IsChecked = None
        else:
            self.chk_all.IsChecked = False

    def _update_status(self):
        ns = 0
        nv = 0
        for r in self._sheet_rows:
            if eget(r, "IsSelected"):
                ns += 1
        for r in self._view_rows:
            if eget(r, "IsSelected"):
                nv += 1
        self.lbl_status.Text = \
            "{0} sheets and {1} views selected. Total: {2}".format(ns, nv, ns + nv)

    def _refresh_filenames(self):
        for r in self._sheet_rows:
            el = eget(r, "_elem")
            eset(r, "CustomFilename",
                 resolve_tokens(self.doc, el, self._tokens, self._field_sep))
        for r in self._view_rows:
            el = eget(r, "_elem")
            eset(r, "CustomFilename",
                 resolve_tokens(self.doc, el, self._tokens, self._field_sep))

    # ------------------------------------------------------------- events
    def on_mode_changed(self, sender, args):
        if not getattr(self, "_ready", False):
            return
        self._apply_mode()
        self._update_status()

    def on_search_changed(self, sender, args):
        if not getattr(self, "_ready", False):
            return
        try:
            self.ph_search.Visibility = \
                Visibility.Collapsed if (self.txt_search.Text or "") else Visibility.Visible
        except Exception:
            pass
        self._apply_mode()

    def on_view_filter_changed(self, sender, args):
        if not getattr(self, "_ready", False):
            return
        self._apply_mode()

    def on_vs_set_changed(self, sender, args):
        """A sheet set was picked in the dropdown -> tick exactly the sheets
        and views that belong to that set. Sets created here or directly in
        Revit both work; the grid is switched to the set's content type and
        any active search is cleared so the ticked members are visible."""
        if not getattr(self, "_ready", False):
            return
        if getattr(self, "_loading_sets", False):
            return
        try:
            name = str(self.cmb_vs_set.SelectedItem) if self.cmb_vs_set.SelectedItem else ""
        except Exception:
            name = ""

        # Placeholder ("Load saved set...") -> leave the current ticks alone
        # and drop the set filter so the full list is shown again.
        if not name or name == self._VS_PLACEHOLDER:
            self._active_set_ids = set()
            self._apply_mode()
            return

        vset = self._vs_sets.get(name)
        if vset is None:
            return

        # Collect the member element ids (sheets and/or views) of the set.
        ids = set()
        try:
            for v in vset.Views:
                key = _id_int(v.Id)
                if key is not None:
                    ids.add(key)
        except Exception:
            ids = set()

        # Remember them so the "Show only sheets in set" filter can use them.
        self._active_set_ids = set(ids)

        # Tick members, untick everything else, across both lists.
        n_sheets = 0
        n_views = 0
        for r in self._sheet_rows:
            el = eget(r, "_elem")
            on = False
            try:
                on = _id_int(el.Id) in ids
            except Exception:
                on = False
            eset(r, "IsSelected", on)
            if on:
                n_sheets += 1
        for r in self._view_rows:
            el = eget(r, "_elem")
            on = False
            try:
                on = _id_int(el.Id) in ids
            except Exception:
                on = False
            eset(r, "IsSelected", on)
            if on:
                n_views += 1

        # Make the ticked members visible: clear any search filter and switch
        # to whichever list actually holds the set's members.
        try:
            if (self.txt_search.Text or "").strip():
                self.txt_search.Text = ""
        except Exception:
            pass
        try:
            if n_sheets and not n_views and not bool(self.rb_sheets.IsChecked):
                self.rb_sheets.IsChecked = True      # fires on_mode_changed -> _apply_mode
            elif n_views and not n_sheets and not bool(self.rb_views.IsChecked):
                self.rb_views.IsChecked = True
            else:
                self._apply_mode()
        except Exception:
            self._apply_mode()

        self._update_status()

    def on_only_set_changed(self, sender, args):
        """Toggle the 'Show only sheets in set' filter. Ticked -> the list
        shows just the members of the selected set; unticked -> shows all."""
        if not getattr(self, "_ready", False):
            return
        # If the filter is switched on while no real set is selected, there is
        # nothing to filter to -> gently untick and show everything.
        try:
            if bool(self.chk_only_set.IsChecked) and not (getattr(self, "_active_set_ids", None)):
                self.chk_only_set.IsChecked = False   # re-fires here, no-op branch
                forms.alert("Pick a sheet set first, then tick "
                            "'Show only sheets in set'.",
                            title="Bulk Export")
                return
        except Exception:
            pass
        self._apply_mode()
        self._update_status()

    # ------------------------------------------------------- sheet sets
    _VS_PLACEHOLDER = "Load saved set..."

    def _refresh_vs_sets(self, select_name=None):
        """Rebuild the saved-set combo from the model's ViewSheetSets."""
        self._loading_sets = True
        try:
            self.cmb_vs_set.Items.Clear()
            self.cmb_vs_set.Items.Add(self._VS_PLACEHOLDER)
            self._vs_sets = {}
            if not hasattr(self, "_active_set_ids"):
                self._active_set_ids = set()
            try:
                sets = FilteredElementCollector(self.doc) \
                    .OfClass(ViewSheetSet).ToElements()
                names = []
                for s in sets:
                    nm = elem_name(s)
                    if nm:
                        names.append(nm)
                        self._vs_sets[nm] = s
                names.sort(key=natural_key)
                for nm in names:
                    self.cmb_vs_set.Items.Add(nm)
            except Exception:
                pass
            idx = 0
            if select_name and select_name in self._vs_sets:
                try:
                    idx = list(self.cmb_vs_set.Items).index(select_name)
                except Exception:
                    idx = 0
            self.cmb_vs_set.SelectedIndex = idx
        finally:
            self._loading_sets = False

    def _ticked_elements(self):
        """Every ticked row across both sheet and view lists."""
        out = []
        for r in (self._sheet_rows + self._view_rows):
            if eget(r, "IsSelected"):
                el = eget(r, "_elem")
                if el is not None:
                    out.append(el)
        return out

    def _ticked_sheets(self):
        out = []
        for r in self._sheet_rows:
            if eget(r, "IsSelected"):
                el = eget(r, "_elem")
                if el is not None:
                    out.append(el)
        return out

    def _save_view_sheet_set(self, name, elements):
        """Create or overwrite a ViewSheetSet. Returns True on success."""
        d = self.doc
        existing = {}
        try:
            for s in FilteredElementCollector(d).OfClass(ViewSheetSet):
                existing[elem_name(s)] = s
        except Exception:
            pass
        overwrite = name in existing
        if overwrite:
            if not forms.alert(
                    "A sheet set named '{0}' already exists.\n"
                    "Overwrite it?".format(name),
                    yes=True, no=True):
                return False
        try:
            pm = d.PrintManager
            pm.PrintRange = PrintRange.Select
            vss = pm.ViewSheetSetting
            vset = ViewSet()
            for el in elements:
                vset.Insert(el)
            # Saving a ViewSheetSet modifies the document, so it must run
            # inside a transaction (Revit 2025+ is strict about this).
            with revit.Transaction("Create Sheet Set", doc=d):
                if overwrite:
                    vss.CurrentViewSheetSet = existing[name]
                vss.CurrentViewSheetSet.Views = vset
                if overwrite:
                    vss.Save()
                else:
                    vss.SaveAs(name)
        except Exception as ex:
            forms.alert("Could not save the sheet set:\n{0}\n\n"
                        "Tip: close Revit's Print dialog if it is open, then "
                        "try again.".format(ex), title="Bulk Export")
            return False
        return True

    def on_create_set(self, sender, args):
        if not getattr(self, "_ready", False):
            return
        elements = self._ticked_elements()
        if not elements:
            forms.alert("Tick the sheets (or views) you want in the set first, "
                        "then click Create Set.", title="Create Sheet Set")
            return
        n_sheets = len(self._ticked_sheets())
        n_views = len(elements) - n_sheets
        default = "Bulk Export Set"
        name = forms.ask_for_string(
            default=default, prompt="Name for the new sheet set:",
            title="Create Sheet Set")
        if not name:
            return
        if self._save_view_sheet_set(name, elements):
            self._refresh_vs_sets(select_name=name)
            forms.alert(
                "Sheet set '{0}' saved ({1} sheet(s), {2} view(s)).\n\n"
                "It now appears in the Sheet set dropdown here and in Revit's "
                "Print / Export 'Select Views/Sheets' dialog."
                .format(name, n_sheets, n_views), title="Create Sheet Set")

    def on_delete_set(self, sender, args):
        if not getattr(self, "_ready", False):
            return
        try:
            sets = list(FilteredElementCollector(self.doc)
                        .OfClass(ViewSheetSet).ToElements())
        except Exception:
            sets = []
        if not sets:
            forms.alert("There are no sheet sets in this model.",
                        title="Delete Sheet Set")
            return

        class _SetOption(object):
            def __init__(self, vss):
                self.vss = vss
                self.name = elem_name(vss)

            def __str__(self):
                return self.name

        options = sorted([_SetOption(s) for s in sets],
                         key=lambda o: (o.name or "").lower())
        picked = forms.SelectFromList.show(
            options, title="Select sheet set(s) to delete",
            multiselect=True, name_attr="name", button_name="Delete")
        if not picked:
            return
        names = "\n  ".join(o.name for o in picked)
        if not forms.alert(
                "Delete {0} sheet set(s)?\n\n  {1}\n\n"
                "This removes only the saved print/export sets, not the "
                "sheets themselves.".format(len(picked), names),
                yes=True, no=True):
            return
        ids = List[ElementId]([o.vss.Id for o in picked])
        try:
            with revit.Transaction("Delete Sheet Set(s)", doc=self.doc):
                self.doc.Delete(ids)
        except Exception as ex:
            forms.alert("Could not delete the sheet set(s):\n{0}".format(ex),
                        title="Delete Sheet Set")
            return
        self._refresh_vs_sets()
        forms.alert("Deleted {0} sheet set(s).".format(len(picked)),
                    title="Delete Sheet Set")

    def on_export_list(self, sender, args):
        """Write the current mode's list (as shown / filtered) to CSV."""
        if not getattr(self, "_ready", False):
            return
        rows = list(self._sel_items)
        if not rows:
            forms.alert("There is nothing in the current list to export.",
                        title="Export List")
            return
        is_sheets = bool(self.rb_sheets.IsChecked)
        try:
            default = revit.doc.Title.replace(".rvt", "")
        except Exception:
            default = "Model"
        default += "_SheetList" if is_sheets else "_ViewList"
        try:
            path = forms.save_file(file_ext="csv", default_name=default,
                                   title="Export current list as CSV")
        except Exception:
            path = None
        if not path:
            return
        header = ["Selected", "Sheet Number", "Name", "Revision",
                  "Size", "Custom Filename"]
        data = []
        for r in rows:
            data.append([
                "Yes" if eget(r, "IsSelected") else "No",
                eget(r, "SheetNumber") or "",
                eget(r, "SheetName") or "",
                eget(r, "Revision") or "",
                eget(r, "Size") or "",
                eget(r, "CustomFilename") or "",
            ])
        try:
            self._write_csv(path, header, data)
        except Exception as ex:
            forms.alert("Could not write the CSV:\n{0}".format(ex),
                        title="Export List")
            return
        forms.alert("Exported {0} row(s) to:\n{1}".format(len(data), path),
                    title="Export List")

    @staticmethod
    def _write_csv(path, header, data_rows):
        """CSV writer that works on both IronPython 2 and CPython 3."""
        import csv
        try:
            f = open(path, "w", newline="")     # CPython 3
        except TypeError:
            f = open(path, "wb")                # IronPython 2 / Py2
        try:
            writer = csv.writer(f)
            writer.writerow(header)
            for row in data_rows:
                writer.writerow(["" if c is None else c for c in row])
        finally:
            f.close()

    def on_check_all(self, sender, args):
        if not getattr(self, "_ready", False):
            return
        val = self.chk_all.IsChecked
        if val is None:
            return
        for r in list(self._sel_items):
            eset(r, "IsSelected", bool(val))
        self._update_status()

    def on_row_check(self, sender, args):
        if not getattr(self, "_ready", False):
            return
        self._sync_check_all()
        self._update_status()

    def on_format_toggle(self, sender, args):
        if not getattr(self, "_ready", False):
            return
        # Show/hide PDF options panel and DWG setup row based on checkboxes.
        pdf_on = bool(self.chk_pdf.IsChecked)
        dwg_on = bool(self.chk_dwg.IsChecked)
        try:
            self.pnl_pdf_options.Visibility = (
                Visibility.Visible if pdf_on else Visibility.Collapsed
            )
        except Exception:
            pass
        try:
            self.pnl_dwg_setup.Visibility = (
                Visibility.Visible if dwg_on else Visibility.Collapsed
            )
        except Exception:
            pass

    # -------------------------------------------------------- sheet ordering
    def on_ordering_changed(self, sender, args):
        if not getattr(self, "_ready", False):
            return
        self._apply_ordering()

    def on_index_reset(self, sender, args):
        if not getattr(self, "_ready", False):
            return
        try:
            self.txt_index_start.Text = "0"
        except Exception:
            pass
        try:
            self.cmb_index_digits.SelectedItem = "4"
        except Exception:
            pass
        self._apply_ordering()

    def on_print_order(self, sender, args):
        if not getattr(self, "_ready", False):
            return
        # Feed only the sheets currently selected / assigned to the chosen
        # sheet set into the Print Order window (in their current order).
        # Changing the sheet set re-ticks its members, so this window always
        # reflects the active set. Fall back to every sheet when nothing is
        # ticked, so the button still works with no selection.
        rows = [r for r in self._sheet_rows if eget(r, "IsSelected")]
        only_selected = bool(rows)
        if not rows:
            rows = self._sheet_rows
        dlg = PrintOrderDialog(self.doc, rows,
                               self._print_order_mode, self._manual_ids,
                               only_selected=only_selected)
        dlg.ShowDialog()
        if getattr(dlg, "ok", False):
            self._print_order_mode = dlg.mode
            self._manual_ids = dlg.ordered_ids
            # A manual print order overrides any loaded drawing list.
            if self._print_order_mode == "manual":
                self._drawing_list = None
                self._drawing_list_path = None
                self._update_list_status()
            self._apply_ordering()

    # -------------------------------------------------------- drawing list
    def _update_list_status(self):
        try:
            if self._drawing_list:
                self.lbl_list_status.Text = "{0} sheet(s) loaded from: {1}".format(
                    len(self._drawing_list),
                    op.basename(self._drawing_list_path or ""))
            else:
                self.lbl_list_status.Text = \
                    "(no list loaded \u2014 using schedule order)"
        except Exception:
            pass

    def on_load_list(self, sender, args):
        if not getattr(self, "_ready", False):
            return
        path = None
        try:
            path = forms.pick_file(
                files_filter="Drawing list (*.csv;*.xlsx;*.xls;*.txt)|"
                             "*.csv;*.xlsx;*.xls;*.txt|All files (*.*)|*.*",
                title="Load drawing list")
        except Exception:
            try:
                path = forms.pick_file(file_ext="csv")
            except Exception:
                path = None
        if not path:
            return
        try:
            numbers = self._read_drawing_list(path)
        except Exception as ex:
            forms.alert("Could not read the list:\n{0}".format(ex),
                        title="Load drawing list")
            return
        if not numbers:
            forms.alert("No sheet numbers were found in that file.",
                        title="Load drawing list")
            return
        self._drawing_list = numbers
        self._drawing_list_path = path
        self._print_order_mode = "number"   # drawing list drives order now
        self._manual_ids = []
        self._update_list_status()
        self._apply_ordering()
        forms.alert("Loaded {0} sheet number(s). The list order is now "
                    "used as the print order.".format(len(numbers)),
                    title="Load drawing list")

    def _read_drawing_list(self, path):
        """Return an ordered list of sheet-number strings from CSV/TXT/XLSX.

        Uses the first column of each row. A header row whose first cell
        looks like 'sheet' / 'number' is skipped."""
        ext = op.splitext(path)[1].lower()
        rows = []
        if ext in (".xlsx", ".xls"):
            rows = self._read_xlsx_first_col(path)
        else:
            import csv
            try:
                f = open(path, "r")
            except TypeError:
                f = open(path, "rb")
            try:
                for parts in csv.reader(f):
                    if parts:
                        rows.append(parts[0])
            finally:
                f.close()
        out = []
        for i, cell in enumerate(rows):
            val = (cell or "").strip()
            if not val:
                continue
            if i == 0:
                low = val.lower()
                if "sheet" in low or "number" in low or low in ("no", "no."):
                    continue
            out.append(val)
        return out

    @staticmethod
    def _read_xlsx_first_col(path):
        """Read the first column of the first worksheet of an .xlsx file
        without external libraries (xlsx = zip of XML)."""
        import zipfile
        import re as _re
        vals = []
        z = zipfile.ZipFile(path)
        try:
            shared = []
            if "xl/sharedStrings.xml" in z.namelist():
                sx = z.read("xl/sharedStrings.xml").decode("utf-8", "ignore")
                for m in _re.findall(r"<t[^>]*>(.*?)</t>", sx, _re.DOTALL):
                    shared.append(_re.sub(r"<[^>]+>", "", m))
            sheet_name = "xl/worksheets/sheet1.xml"
            names = z.namelist()
            if sheet_name not in names:
                cand = [n for n in names
                        if n.startswith("xl/worksheets/") and n.endswith(".xml")]
                if not cand:
                    return vals
                sheet_name = sorted(cand)[0]
            data = z.read(sheet_name).decode("utf-8", "ignore")
            for row_xml in _re.findall(r"<row[^>]*>(.*?)</row>", data, _re.DOTALL):
                m = _re.search(r'<c\s+r="A\d+"[^>]*?(?:\st="(\w+)")?[^>]*>'
                               r'(?:<v>(.*?)</v>|<is><t[^>]*>(.*?)</t></is>)',
                               row_xml, _re.DOTALL)
                if not m:
                    continue
                typ, v1, v2 = m.group(1), m.group(2), m.group(3)
                if v2 is not None:
                    vals.append(v2)
                elif typ == "s" and v1 is not None:
                    try:
                        vals.append(shared[int(v1)])
                    except Exception:
                        vals.append("")
                else:
                    vals.append(v1 or "")
        finally:
            z.close()
        return vals

    def on_export_drawing_list(self, sender, args):
        if not getattr(self, "_ready", False):
            return
        rows = [r for r in self._sheet_rows]
        if not rows:
            forms.alert("There are no sheets to export.",
                        title="Export drawing list")
            return
        try:
            default = revit.doc.Title.replace(".rvt", "")
        except Exception:
            default = "Model"
        default += "_DrawingList"
        try:
            path = forms.save_file(file_ext="csv", default_name=default,
                                   title="Export drawing list as CSV")
        except Exception:
            path = None
        if not path:
            return
        header = ["Index", "Sheet Number", "Sheet Name",
                  "Current Revision", "Printable"]
        data = []
        for r in rows:
            data.append([
                eget(r, "Index") or "",
                eget(r, "SheetNumber") or "",
                eget(r, "SheetName") or "",
                eget(r, "Revision") or "",
                "Yes" if eget(r, "_printable") else "No",
            ])
        try:
            self._write_csv(path, header, data)
        except Exception as ex:
            forms.alert("Could not write the CSV:\n{0}".format(ex),
                        title="Export drawing list")
            return
        forms.alert("Exported {0} sheet(s) to:\n{1}".format(len(data), path),
                    title="Export drawing list")

    def on_clear_list(self, sender, args):
        if not getattr(self, "_ready", False):
            return
        self._drawing_list = None
        self._drawing_list_path = None
        self._update_list_status()
        self._apply_ordering()

    def on_paper_orient_changed(self, sender, args):
        if not getattr(self, "_ready", False):
            return

    def on_edit_filename(self, sender, args):
        if not getattr(self, "_ready", False):
            return
        dlg = ParamDialog(self.doc, self._tokens, self._field_sep)
        dlg.ShowDialog()
        if getattr(dlg, "ok", False):
            self._tokens = dlg.result_tokens
            self._field_sep = dlg.field_sep
            self._refresh_filenames()

    def on_browse(self, sender, args):
        if not getattr(self, "_ready", False):
            return
        try:
            from System.Windows.Forms import FolderBrowserDialog, DialogResult
            fbd = FolderBrowserDialog()
            fbd.Description = "Choose the export folder"
            if fbd.ShowDialog() == DialogResult.OK:
                self.txt_folder.Text = fbd.SelectedPath
        except Exception as e:
            forms.alert("Could not open folder dialog: {0}".format(e),
                        title="Bulk Export")

    # ------------------------------------------------------------- navigation
    def _sync_nav(self, idx):
        self.btn_back.IsEnabled = (idx > 0)
        if idx >= 2:
            self.btn_next.Visibility = Visibility.Collapsed
            self.btn_create.Visibility = Visibility.Visible
        else:
            self.btn_next.Visibility = Visibility.Visible
            self.btn_create.Visibility = Visibility.Collapsed

    def on_tab_changed(self, sender, args):
        if not getattr(self, "_ready", False):
            return
        try:
            if args.AddedItems is None or args.AddedItems.Count == 0:
                return
            if not isinstance(args.AddedItems[0], TabItem):
                return  # bubbled event from an inner ComboBox / Selector
        except Exception:
            return
        idx = self.tabs.SelectedIndex
        if idx == 2:
            self._build_create_rows()
        self._sync_nav(idx)

    def on_next(self, sender, args):
        idx = self.tabs.SelectedIndex
        if idx < 2:
            self.tabs.SelectedIndex = idx + 1

    def on_back(self, sender, args):
        idx = self.tabs.SelectedIndex
        if idx > 0:
            self.tabs.SelectedIndex = idx - 1

    # ------------------------------------------------------------- create tab
    def _selected_elements(self):
        out = []
        for r in self._base_rows():
            if eget(r, "IsSelected"):
                out.append(r)
        return out

    def _formats(self):
        fmts = []
        try:
            if self.chk_pdf.IsChecked:
                fmts.append("PDF")
        except Exception:
            pass
        try:
            if self.chk_dwg.IsChecked:
                fmts.append("DWG")
        except Exception:
            pass
        return fmts

    def _build_create_rows(self):
        self._create_rows = []
        coll = ObservableCollection[object]()
        fmts = self._formats()
        for r in self._selected_elements():
            el = eget(r, "_elem")
            is_sheet = eget(r, "_isheet")
            fname = eget(r, "CustomFilename")
            num = eget(r, "SheetNumber") or ""
            nam = eget(r, "SheetName") or ""
            size = eget(r, "Size") or "-"
            ori = orient(self.doc, el) if is_sheet else "-"
            for fmt in fmts:
                row = expando(
                    IsSelected=True,
                    Number=num,
                    Name=nam,
                    Format=fmt,
                    Size=(size if fmt == "PDF" else "DWG"),
                    Orientation=ori,
                    Progress="",
                )
                eset(row, "_elem", el)
                eset(row, "_fname", fname)
                self._create_rows.append(row)
                coll.Add(row)
        self.dg_create.ItemsSource = coll

    def on_check_all_create(self, sender, args):
        if not getattr(self, "_ready", False):
            return
        val = self.chk_all_create.IsChecked
        if val is None:
            return
        for r in self._create_rows:
            eset(r, "IsSelected", bool(val))

    # ------------------------------------------------------------- export
    def set_progress(self, pct):
        try:
            self.pbar.Value = pct
            self.lbl_progress.Text = "Completed {0}%".format(int(pct))
        except Exception:
            pass

    def _dest(self, folder, fmt):
        if self.rb_save_split.IsChecked:
            sub = op.join(folder, fmt)
            try:
                if not op.isdir(sub):
                    os.makedirs(sub)
            except Exception:
                return folder
            return sub
        return folder

    def make_pdf_options(self):
        opts = PDFExportOptions()

        def trySet(fn):
            try:
                fn()
            except Exception:
                pass

        trySet(lambda: setattr(opts, "Combine", True))
        trySet(lambda: setattr(opts, "HideReferencePlane", bool(self.chk_hideref.IsChecked)))
        trySet(lambda: setattr(opts, "HideUnreferencedViewTags", bool(self.chk_hidetags.IsChecked)))
        trySet(lambda: setattr(opts, "HideScopeBoxes", bool(self.chk_hidescope.IsChecked)))
        trySet(lambda: setattr(opts, "HideCropBoundaries", bool(self.chk_hidecrop.IsChecked)))
        trySet(lambda: setattr(opts, "ReplaceHalftoneWithThinLines", bool(self.chk_halftone.IsChecked)))
        trySet(lambda: setattr(opts, "MaskCoincidentLines", bool(self.chk_maskcoin.IsChecked)))
        trySet(lambda: setattr(opts, "ViewLinksInBlue", bool(self.chk_viewlinks.IsChecked)))

        if ColorDepthType is not None:
            ci = self.cmb_colors.SelectedIndex
            if ci == 1:
                trySet(lambda: setattr(opts, "ColorDepth", ColorDepthType.GrayScale))
            elif ci == 2:
                trySet(lambda: setattr(opts, "ColorDepth", ColorDepthType.BlackLine))
            else:
                trySet(lambda: setattr(opts, "ColorDepth", ColorDepthType.Color))

        if RasterQualityType is not None:
            ri = self.cmb_raster.SelectedIndex
            rmap = {0: "Low", 1: "Medium", 2: "High", 3: "Presentation"}
            qn = rmap.get(ri, "Medium")
            try:
                setattr(opts, "RasterQuality", getattr(RasterQualityType, qn))
            except Exception:
                pass

        if ZoomFitType is not None:
            if self.rb_fit.IsChecked:
                trySet(lambda: setattr(opts, "ZoomType", ZoomFitType.FitToPage))
            else:
                trySet(lambda: setattr(opts, "ZoomType", ZoomFitType.Zoom))
                trySet(lambda: setattr(opts, "ZoomPercentage",
                                       _safe_int(self.txt_zoom.Text, 100)))
        return opts

    def make_dwg_options(self):
        setup_name = None
        try:
            sel = self.cmb_dwg_setup.SelectedItem
            if sel is not None and str(sel) in getattr(self, "_dwg_setups", []):
                setup_name = str(sel)
        except Exception:
            setup_name = None

        opts = None
        if setup_name:
            try:
                opts = DWGExportOptions.GetPredefinedOptions(self.doc, setup_name)
            except Exception:
                opts = None
        if opts is None:
            opts = DWGExportOptions()

        try:
            opts.MergedViews = True
        except Exception:
            pass
        return opts

    def export_pdf(self, ids, name, folder):
        if not HAS_PDF:
            return False, "PDF export requires Revit 2022 or newer"
        try:
            col = List[ElementId]()
            for i in ids:
                col.Add(i)
            opts = self.make_pdf_options()
            try:
                opts.FileName = name
            except Exception:
                pass
            try:
                self.doc.Export(folder, col, opts)
            except Exception as e:
                return False, str(e)
            path = op.join(folder, name + ".pdf")
            return True, path
        except Exception as e:
            return False, str(e)

    def export_dwg(self, el, name, folder):
        try:
            col = List[ElementId]()
            col.Add(el.Id)
            opts = self.make_dwg_options()
            try:
                self.doc.Export(folder, name, col, opts)
            except Exception as e:
                return False, str(e)
            path = op.join(folder, name + ".dwg")
            return True, path
        except Exception as e:
            return False, str(e)

    def on_create(self, sender, args):
        if self._exporting:
            return
        if not getattr(self, "_ready", False):
            return

        raw = ""
        try:
            raw = (self.txt_folder.Text or "").strip()
        except Exception:
            pass
        folder = expand_env(raw)
        if not folder:
            forms.alert("Please choose an output folder.", title="Bulk Export")
            return
        if not op.isdir(folder):
            try:
                os.makedirs(folder)
            except Exception:
                forms.alert("Output folder does not exist and could not be "
                            "created:\n{0}".format(folder), title="Bulk Export")
                return

        fmts = self._formats()
        if not fmts:
            forms.alert("Select at least one format (PDF or DWG) on the Format tab.",
                        title="Bulk Export")
            return

        rows = [r for r in self._create_rows if eget(r, "IsSelected")]
        if not rows:
            forms.alert("Nothing is selected to export.", title="Bulk Export")
            return

        self._exporting = True
        try:
            self.btn_create.IsEnabled = False
            self.btn_back.IsEnabled = False
        except Exception:
            pass

        total = len(rows)
        done = 0
        ok_count = 0
        log = []
        self.set_progress(0)

        combine_pdf = bool(self.rb_combine.IsChecked)

        # ---- combined single PDF path ----
        handled = set()
        if combine_pdf and HAS_PDF:
            pdf_rows = [r for r in rows if eget(r, "Format") == "PDF"]
            if pdf_rows:
                ids = [eget(r, "_elem").Id for r in pdf_rows]
                cname = ""
                try:
                    cname = sanitize((self.txt_filename.Text or "").strip())
                except Exception:
                    cname = ""
                if not cname:
                    cname = "Combined"
                for r in pdf_rows:
                    eset(r, "Progress", "Exporting...")
                do_events()
                ok, msg = self.export_pdf(ids, cname, self._dest(folder, "PDF"))
                for r in pdf_rows:
                    eset(r, "Progress", "Done" if ok else "Failed")
                    done += 1
                    if ok:
                        ok_count += 1
                    handled.add(id(r))
                    log.append((eget(r, "Name"), "PDF",
                                "OK" if ok else "FAIL", msg))
                self.set_progress(int(done * 100.0 / total))
                do_events()

        # ---- per-item path ----
        for r in rows:
            if id(r) in handled:
                continue
            el = eget(r, "_elem")
            fmt = eget(r, "Format")
            name = eget(r, "_fname") or default_name(self.doc, el)
            eset(r, "Progress", "Exporting...")
            do_events()

            if fmt == "PDF":
                ok, msg = self.export_pdf([el.Id], name, self._dest(folder, "PDF"))
            else:
                ok, msg = self.export_dwg(el, name, self._dest(folder, "DWG"))

            eset(r, "Progress", "Done" if ok else ("Failed: " + str(msg)[:40]))
            log.append((eget(r, "Name"), fmt, "OK" if ok else "FAIL", msg))
            done += 1
            if ok:
                ok_count += 1
            self.set_progress(int(done * 100.0 / total))
            do_events()

        self.set_progress(100)
        self._exporting = False
        try:
            self.btn_create.IsEnabled = True
            self.btn_back.IsEnabled = True
        except Exception:
            pass

        report_path = self._save_report(folder, log)
        try:
            toast = ExportToast(folder, ok_count, total, report_path)
            toast.ShowDialog()
        except Exception:
            msg = "Export complete.\n\n{0} of {1} item(s) exported to:\n{2}".format(
                ok_count, total, folder)
            if report_path:
                msg += "\n\nReport: {0}".format(report_path)
            forms.alert(msg, title="Bulk Export")

    def _save_report(self, folder, log):
        try:
            idx = self.cmb_report.SelectedIndex
        except Exception:
            idx = 0
        if idx <= 0 or not log:
            return None
        stamp = DateTime.Now.ToString("yyyyMMdd_HHmmss")
        try:
            if idx == 2:
                path = op.join(folder, "BulkExport_{0}.csv".format(stamp))
                with open(path, "w") as f:
                    f.write("Name,Format,Status,Detail\n")
                    for name, fmt, status, detail in log:
                        d = str(detail).replace('"', "'")
                        f.write('"{0}","{1}","{2}","{3}"\n'.format(
                            name, fmt, status, d))
            else:
                path = op.join(folder, "BulkExport_{0}.txt".format(stamp))
                with open(path, "w") as f:
                    f.write("Bulk Export Report - {0}\n".format(stamp))
                    f.write("=" * 50 + "\n")
                    for name, fmt, status, detail in log:
                        f.write("[{0}] {1:<5} {2}\n".format(status, fmt, name))
                        f.write("       {0}\n".format(detail))
            return path
        except Exception:
            return None


# ===========================================================================
#  ENTRY
# ===========================================================================
def main():
    splash = None
    try:
        # Show the loading splash immediately so the user sees feedback.
        splash = LoadingWindow()
        splash.Show()
        # Pump the dispatcher so the splash paints before heavy __init__ runs.
        _do_events()
        # Build the main window (heavy: reads sheets, views, combos).
        win = BulkExportWindow()
        # Close the splash now that the main window is ready.
        if splash is not None:
            splash.close_splash()
            splash = None
        win.show_dialog()
    except Exception:
        if splash is not None:
            try:
                splash.close_splash()
            except Exception:
                pass
        forms.alert("Bulk Export failed to start:\n\n{0}".format(
            traceback.format_exc()), title="Bulk Export - Error")


if __name__ == "__main__":
    main()
