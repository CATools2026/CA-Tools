# -*- coding: utf-8 -*-
"""Shared engine for creating parallel MEP runs (cable tray / duct / pipe /
conduit). Each pushbutton calls ``run(discipline)``.

It picks the whole connected run from a selected element, offsets the path
sideways into N parallel copies, recreates the bends with elbow fittings, and
lets you set per-run size, service/system and spacing from a popup.
"""

import os
import clr

from pyrevit import revit, forms, script

from Autodesk.Revit.DB import (
    XYZ, BuiltInParameter, BuiltInCategory, ElementId, Element,
    FilteredElementCollector, UnitUtils, MEPSystemType
)
from Autodesk.Revit.DB.Electrical import CableTray, Conduit
from Autodesk.Revit.DB.Mechanical import Duct, MechanicalSystemType
from Autodesk.Revit.DB.Plumbing import Pipe, PipingSystemType
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.Exceptions import OperationCanceledException

clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
from System.Windows.Controls import StackPanel, TextBox, ComboBox, TextBlock
from System.Windows.Controls import Orientation
from System.Windows import Thickness, VerticalAlignment, Visibility
from System.Windows.Media import SolidColorBrush, Color


# --------------------------------------------------------------------------- #
# Theme brushes (explicit, so code-created controls are always readable on the
# dark-purple window regardless of implicit-style resolution in Revit's WPF)
# --------------------------------------------------------------------------- #
def _brush(r, g, b):
    return SolidColorBrush(Color.FromRgb(r, g, b))

CA_LIGHT = _brush(0xF5, 0xF5, 0xF3)   # light text on dark chrome
CA_DARK = _brush(0x22, 0x1A, 0x30)    # dark text on light input fields
CA_FIELD_BG = _brush(0xF5, 0xF5, 0xF3)  # light input-field background
CA_PURPLE = _brush(0x7F, 0x77, 0xDD)  # accent / borders

logger = script.get_logger()
XAML_FILE = os.path.join(os.path.dirname(__file__), 'ParallelMEPWindow.xaml')


# --------------------------------------------------------------------------- #
# Units
# --------------------------------------------------------------------------- #
def mm_to_ft(mm):
    try:
        from Autodesk.Revit.DB import UnitTypeId
        return UnitUtils.ConvertToInternalUnits(mm, UnitTypeId.Millimeters)
    except Exception:
        from Autodesk.Revit.DB import DisplayUnitType
        return UnitUtils.ConvertToInternalUnits(mm, DisplayUnitType.DUT_MILLIMETERS)


def ft_to_mm(ft):
    try:
        from Autodesk.Revit.DB import UnitTypeId
        return UnitUtils.ConvertFromInternalUnits(ft, UnitTypeId.Millimeters)
    except Exception:
        from Autodesk.Revit.DB import DisplayUnitType
        return UnitUtils.ConvertFromInternalUnits(ft, DisplayUnitType.DUT_MILLIMETERS)


def to_float(text, default):
    try:
        return float(str(text).strip())
    except Exception:
        return default


# --------------------------------------------------------------------------- #
# Discipline specification
# --------------------------------------------------------------------------- #
class Spec(object):
    pass


def get_spec(discipline, doc):
    s = Spec()
    s.kind = discipline

    if discipline == 'cabletray':
        s.cls = CableTray
        s.name = "Cable Tray"
        s.word = "tray"
        s.size_params = {'w': BuiltInParameter.RBS_CABLETRAY_WIDTH_PARAM,
                         'h': BuiltInParameter.RBS_CABLETRAY_HEIGHT_PARAM,
                         'd': None}
        s.service = 'string'
        s.service_label = "Service Type"
        s.system_class = None
        s.system_category = None
        s.create = (lambda type_id, level_id, sys_id, p0, p1:
                    CableTray.Create(doc, type_id, p0, p1, level_id))

    elif discipline == 'conduit':
        s.cls = Conduit
        s.name = "Conduit"
        s.word = "conduit"
        s.size_params = {'w': None, 'h': None,
                         'd': BuiltInParameter.RBS_CONDUIT_DIAMETER_PARAM}
        s.service = 'none'
        s.service_label = ""
        s.system_class = None
        s.system_category = None
        s.create = (lambda type_id, level_id, sys_id, p0, p1:
                    Conduit.Create(doc, type_id, p0, p1, level_id))

    elif discipline == 'pipe':
        s.cls = Pipe
        s.name = "Pipe"
        s.word = "pipe"
        s.size_params = {'w': None, 'h': None,
                         'd': BuiltInParameter.RBS_PIPE_DIAMETER_PARAM}
        s.service = 'system'
        s.service_label = "System Type"
        s.system_class = PipingSystemType
        s.system_category = BuiltInCategory.OST_PipingSystem
        s.create = (lambda type_id, level_id, sys_id, p0, p1:
                    Pipe.Create(doc, sys_id, type_id, level_id, p0, p1))

    elif discipline == 'duct':
        s.cls = Duct
        s.name = "Duct"
        s.word = "duct"
        s.size_params = {'w': BuiltInParameter.RBS_CURVE_WIDTH_PARAM,
                         'h': BuiltInParameter.RBS_CURVE_HEIGHT_PARAM,
                         'd': BuiltInParameter.RBS_CURVE_DIAMETER_PARAM}
        s.service = 'system'
        s.service_label = "System Type"
        s.system_class = MechanicalSystemType
        s.system_category = BuiltInCategory.OST_DuctSystem
        s.create = (lambda type_id, level_id, sys_id, p0, p1:
                    Duct.Create(doc, sys_id, type_id, level_id, p0, p1))

    else:
        raise ValueError("Unknown discipline: %s" % discipline)
    return s


def detect_size_mode(src, spec):
    """'wh' for rectangular (width+height) or 'd' for round (diameter)."""
    d_bip = spec.size_params.get('d')
    w_bip = spec.size_params.get('w')
    if d_bip is not None:
        dp = src.get_Parameter(d_bip)
        if dp is not None and dp.HasValue and dp.AsDouble() > 1e-9:
            if w_bip is not None:
                wp = src.get_Parameter(w_bip)
                if wp is not None and wp.HasValue and wp.AsDouble() > 1e-9:
                    return 'wh'
            return 'd'
    return 'wh' if w_bip is not None else 'd'


def read_size_mm(src, spec, mode):
    if mode == 'wh':
        wp = src.get_Parameter(spec.size_params['w'])
        hp = src.get_Parameter(spec.size_params['h'])
        w = ft_to_mm(wp.AsDouble()) if wp and wp.HasValue else 100.0
        h = ft_to_mm(hp.AsDouble()) if hp and hp.HasValue else 50.0
        return w, h
    dp = src.get_Parameter(spec.size_params['d'])
    d = ft_to_mm(dp.AsDouble()) if dp and dp.HasValue else 50.0
    return d, d


def set_size(elem, spec, mode, w_mm, h_mm):
    if mode == 'wh':
        wp = elem.get_Parameter(spec.size_params['w'])
        hp = elem.get_Parameter(spec.size_params['h'])
        if wp and not wp.IsReadOnly:
            wp.Set(mm_to_ft(w_mm))
        if hp and not hp.IsReadOnly:
            hp.Set(mm_to_ft(h_mm))
    else:
        dp = elem.get_Parameter(spec.size_params['d'])
        if dp and not dp.IsReadOnly:
            dp.Set(mm_to_ft(w_mm))


# --------------------------------------------------------------------------- #
# Service / system helpers
# --------------------------------------------------------------------------- #
def collect_service_strings(doc, spec):
    values = set()
    bip = getattr(BuiltInParameter, 'RBS_CTC_SERVICE_TYPE', None)
    for t in FilteredElementCollector(doc).OfClass(spec.cls):
        p = t.get_Parameter(bip) if bip is not None else None
        if p is None:
            p = t.LookupParameter("Service Type")
        if p is not None and p.HasValue:
            try:
                if p.AsString():
                    values.add(p.AsString())
            except Exception:
                pass
    return sorted(values)


def source_service_string(src):
    bip = getattr(BuiltInParameter, 'RBS_CTC_SERVICE_TYPE', None)
    p = src.get_Parameter(bip) if bip is not None else None
    if p is None:
        p = src.LookupParameter("Service Type")
    try:
        return p.AsString() if (p and p.HasValue) else ""
    except Exception:
        return ""


def set_service_string(elem, value):
    if not value:
        return
    bip = getattr(BuiltInParameter, 'RBS_CTC_SERVICE_TYPE', None)
    p = elem.get_Parameter(bip) if bip is not None else None
    if p is None:
        p = elem.LookupParameter("Service Type")
    if p is not None and not p.IsReadOnly:
        try:
            p.Set(value)
        except Exception:
            logger.debug("Could not set service type.")


def elem_name(el):
    """Robust element name: direct property, then reflection, then a param."""
    try:
        n = el.Name
        if n:
            return n
    except Exception:
        pass
    try:
        import clr as _clr
        pi = _clr.GetClrType(Element).GetProperty("Name")
        n = pi.GetValue(el, None)
        if n:
            return n
    except Exception:
        pass
    for bip in ('ALL_MODEL_TYPE_NAME', 'SYMBOL_NAME_PARAM',
                'RBS_SYSTEM_NAME_PARAM'):
        b = getattr(BuiltInParameter, bip, None)
        if b is None:
            continue
        try:
            p = el.get_Parameter(b)
            if p and p.HasValue and p.AsString():
                return p.AsString()
        except Exception:
            pass
    return "Id %d" % el.Id.IntegerValue


def collect_system_types(doc, spec):
    """Collect MEP system *types* for a discipline using several strategies,
    so it works regardless of Revit version / API quirks. Returns name->Id."""
    found = {}

    def add(collector):
        try:
            for el in collector:
                found[el.Id.IntegerValue] = el
        except Exception:
            pass

    if spec.system_class is not None:
        add(FilteredElementCollector(doc).OfClass(spec.system_class))
    if spec.system_category is not None:
        add(FilteredElementCollector(doc).OfCategory(spec.system_category)
            .WhereElementIsElementType())
        # base class + category filter (belt and suspenders)
        try:
            cat_int = int(spec.system_category)
            for el in FilteredElementCollector(doc).OfClass(MEPSystemType):
                c = getattr(el, 'Category', None)
                if c is not None and c.Id.IntegerValue == cat_int:
                    found[el.Id.IntegerValue] = el
        except Exception:
            pass

    name_to_id = {}
    for el in found.values():
        name_to_id[elem_name(el)] = el.Id
    return name_to_id


def source_system_id(src):
    """System type id of the source element, via its system or its connectors."""
    try:
        msys = src.MEPSystem
        if msys is not None:
            tid = msys.GetTypeId()
            if tid and tid != ElementId.InvalidElementId:
                return tid
    except Exception:
        pass
    for con in iter_connectors(src):
        try:
            msys = con.MEPSystem
            if msys is not None:
                tid = msys.GetTypeId()
                if tid and tid != ElementId.InvalidElementId:
                    return tid
        except Exception:
            pass
    return None


# --------------------------------------------------------------------------- #
# Selection + run traversal
# --------------------------------------------------------------------------- #
def make_filter(spec):
    cls = spec.cls

    class _F(ISelectionFilter):
        def AllowElement(self, e):
            return isinstance(e, cls)

        def AllowReference(self, r, p):
            return False
    return _F()


def pick_sources(uidoc, doc, spec):
    """Return a list of source elements of this discipline.

    If the user pre-selected one or more valid elements, all of them are used
    as seeds (so a run made of several selected segments, or several selected
    pieces that aren't fitting-joined, are all included). Otherwise the user is
    asked to pick one. The first item is treated as the reference element for
    type / size / level / service defaults.
    """
    sel = []
    for eid in uidoc.Selection.GetElementIds():
        el = doc.GetElement(eid)
        if isinstance(el, spec.cls):
            sel.append(el)
    if sel:
        return sel
    try:
        ref = uidoc.Selection.PickObject(
            ObjectType.Element, make_filter(spec),
            "Select any %s in the run to copy in parallel / stacked" % spec.word)
        return [doc.GetElement(ref)]
    except OperationCanceledException:
        return []


def iter_connectors(elem):
    try:
        return list(elem.ConnectorManager.Connectors)
    except Exception:
        try:
            return list(elem.MEPModel.ConnectorManager.Connectors)
        except Exception:
            return []


def neighbour_curves(connector, owner, spec):
    found = []
    try:
        refs = list(connector.AllRefs)
    except Exception:
        refs = []
    for ref in refs:
        other = getattr(ref, 'Owner', None)
        if other is None or other.Id == owner.Id:
            continue
        if isinstance(other, spec.cls):
            found.append(other)
        else:  # fitting - hop across it
            for c2 in iter_connectors(other):
                try:
                    refs2 = list(c2.AllRefs)
                except Exception:
                    refs2 = []
                for r2 in refs2:
                    o2 = getattr(r2, 'Owner', None)
                    if (o2 is not None and isinstance(o2, spec.cls)
                            and o2.Id != owner.Id):
                        found.append(o2)
    return found


def collect_run(start, spec):
    found = {}
    stack = [start]
    while stack:
        t = stack.pop()
        tid = t.Id.IntegerValue
        if tid in found:
            continue
        found[tid] = t
        for con in iter_connectors(t):
            for nb in neighbour_curves(con, t, spec):
                if nb.Id.IntegerValue not in found:
                    stack.append(nb)
    return found


def order_run(curves, spec):
    ids = list(curves.keys())
    adj = dict((i, set()) for i in ids)
    for i in ids:
        for con in iter_connectors(curves[i]):
            for nb in neighbour_curves(con, curves[i], spec):
                if nb.Id.IntegerValue in curves:
                    adj[i].add(nb.Id.IntegerValue)
    branched = any(len(adj[i]) > 2 for i in ids)
    ends = [i for i in ids if len(adj[i]) <= 1]
    start = ends[0] if ends else ids[0]
    ordered = [start]
    prev, cur = None, start
    while True:
        nxts = [n for n in adj[cur] if n != prev]
        if not nxts:
            break
        nxt = nxts[0]
        if nxt in ordered:
            break
        ordered.append(nxt)
        prev, cur = cur, nxt
        if len(ordered) >= len(ids):
            break
    return [curves[i] for i in ordered], branched, (len(ordered) < len(ids))


def order_component(elems):
    """Order the straight segments of ONE run into a continuous path, purely by
    geometry, so it behaves identically for every discipline (cable tray, duct,
    pipe, conduit) regardless of how each one's connectors/fittings report
    neighbours.

    Each segment has two endpoints. Inside a single run every endpoint's nearest
    endpoint on a *different* segment is its partner across the elbow. Two
    endpoints that are each other's nearest form a junction; the two endpoints
    with no mutual partner are the run's free ends. We start at a free end and
    walk junction-to-junction. Returns (ordered_elements, complete)."""
    segs = []
    for e in elems:
        loc = getattr(e, 'Location', None)
        curve = getattr(loc, 'Curve', None) if loc is not None else None
        if curve is None:
            continue
        segs.append((e, curve.GetEndPoint(0), curve.GetEndPoint(1)))
    n = len(segs)
    if n <= 1:
        return [s[0] for s in segs], True

    # flat endpoint list; endpoint index of (segment i, end e) == i*2 + e
    pts = []
    for (_e, p0, p1) in segs:
        pts.append(p0)
        pts.append(p1)

    partner = [None] * (2 * n)
    for k in range(2 * n):
        sk = k // 2
        best, best_d = None, None
        for m in range(2 * n):
            if m // 2 == sk:
                continue
            d = pts[k].DistanceTo(pts[m])
            if best_d is None or d < best_d:
                best_d = d
                best = m
        partner[k] = best

    mutual = [False] * (2 * n)
    for k in range(2 * n):
        p = partner[k]
        if p is not None and partner[p] == k:
            mutual[k] = True

    free = [k for k in range(2 * n) if not mutual[k]]
    start = free[0] if free else 0

    order = []
    used = set()
    cur = start
    while True:
        s = cur // 2
        if s in used:
            break
        used.add(s)
        order.append(segs[s][0])
        other = s * 2 + (1 - (cur % 2))        # opposite end of same segment
        if mutual[other] and partner[other] is not None and (partner[other] // 2) not in used:
            cur = partner[other]               # cross the elbow to next segment
        else:
            break

    # For a plain linear run this reaches every segment. If the run branches
    # (tee/cross) only the walked main chain is returned; the caller warns.
    return order, (len(order) == n)


def connected_components(curves, spec):
    """Split {id: element} into independent connected runs using connector
    adjacency. Several separate runs are therefore never merged into one path
    (which previously produced a scrambled zig-zag when many items were
    selected). Each component is copied on its own, giving clean parallel banks."""
    ids = list(curves.keys())
    adj = dict((i, set()) for i in ids)
    for i in ids:
        for con in iter_connectors(curves[i]):
            for nb in neighbour_curves(con, curves[i], spec):
                j = nb.Id.IntegerValue
                if j in curves:
                    adj[i].add(j)
                    adj[j].add(i)
    seen = set()
    comps = []
    for i in ids:
        if i in seen:
            continue
        stack = [i]
        comp = {}
        while stack:
            j = stack.pop()
            if j in seen:
                continue
            seen.add(j)
            comp[j] = curves[j]
            for k in adj[j]:
                if k not in seen:
                    stack.append(k)
        comps.append(comp)
    return comps


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #
def intersect_lines(p1, d1, p2, d2):
    cross = d1.CrossProduct(d2)
    denom = cross.GetLength()
    if denom < 1e-9:
        return None
    diff = p2.Subtract(p1)
    t = diff.CrossProduct(d2).DotProduct(cross) / (denom * denom)
    return p1.Add(d1.Multiply(t))


def horiz_perp(direction, ref):
    hp = direction.CrossProduct(XYZ.BasisZ)
    if hp.GetLength() < 1e-6:
        return ref
    return hp.Normalize()


def farther(a, b, ref):
    return a if a.DistanceTo(ref) >= b.DistanceTo(ref) else b


def connector_nearest(elem, point):
    best, best_d = None, None
    for c in iter_connectors(elem):
        try:
            d = c.Origin.DistanceTo(point)
        except Exception:
            continue
        if best_d is None or d < best_d:
            best, best_d = c, d
    return best


def prepare_path(ordered):
    """Turn an ordered list of curve elements into a reusable path description
    (straight segments, their sideways perpendiculars, the corner points, and
    the two true endpoints). Returns None if no centerline could be read."""
    segs = []
    for t in ordered:
        curve = getattr(getattr(t, 'Location', None), 'Curve', None)
        if curve is None:
            continue
        a = curve.GetEndPoint(0)
        b = curve.GetEndPoint(1)
        d = b.Subtract(a)
        if d.GetLength() < 1e-9:
            continue
        segs.append({'a': a, 'b': b, 'd': d.Normalize()})
    if not segs:
        return None

    ref = None
    for s in segs:
        hp = s['d'].CrossProduct(XYZ.BasisZ)
        if hp.GetLength() > 1e-6:
            ref = hp.Normalize()
            break
    if ref is None:
        ref = XYZ.BasisX
    for s in segs:
        s['hp'] = horiz_perp(s['d'], ref)

    corners = []
    for k in range(len(segs) - 1):
        pt = intersect_lines(segs[k]['a'], segs[k]['d'],
                             segs[k + 1]['a'], segs[k + 1]['d'])
        if pt is None:
            pt = segs[k]['b']
        corners.append(pt)

    if len(segs) == 1:
        v_start, v_end = segs[0]['a'], segs[0]['b']
    else:
        v_start = farther(segs[0]['a'], segs[0]['b'], corners[0])
        v_end = farther(segs[-1]['a'], segs[-1]['b'], corners[-1])

    return {'segs': segs, 'corners': corners, 'v_start': v_start, 'v_end': v_end}


def offset_path(path, d_ft, vertical=False):
    """Offset a prepared path by d_ft: sideways (parallel) or vertically
    (stack). Returns the list of vertices for the new run."""
    segs = path['segs']
    corners = path['corners']
    v_start = path['v_start']
    v_end = path['v_end']

    if vertical:
        # Stack layout: pure vertical translation. Every vertex moves up / down
        # by the same amount, so plan geometry and corner angles are preserved.
        shift = XYZ(0, 0, d_ft)
        verts = [v_start.Add(shift)]
        for k in range(len(segs) - 1):
            verts.append(corners[k].Add(shift))
        verts.append(v_end.Add(shift))
        return verts

    # Parallel layout: offset each segment sideways and recompute the corners.
    olines = [(s['a'].Add(s['hp'].Multiply(d_ft)), s['d']) for s in segs]
    verts = [v_start.Add(segs[0]['hp'].Multiply(d_ft))]
    for k in range(len(segs) - 1):
        pt = intersect_lines(olines[k][0], olines[k][1],
                             olines[k + 1][0], olines[k + 1][1])
        if pt is None:
            pt = corners[k].Add(segs[k]['hp'].Multiply(d_ft))
        verts.append(pt)
    verts.append(v_end.Add(segs[-1]['hp'].Multiply(d_ft)))
    return verts


# --------------------------------------------------------------------------- #
# Popup window
# --------------------------------------------------------------------------- #
class ParallelWindow(forms.WPFWindow):
    def __init__(self, cfg):
        forms.WPFWindow.__init__(self, XAML_FILE)
        self.cfg = cfg
        self.result = None
        self.row_controls = []

        self.Title = "Create Parallel / Stacked %ss" % cfg['name']

        if cfg['size_mode'] == 'wh':
            self.HdrSize1.Text = "Width (mm)"
            self.HdrSize2.Text = "Height (mm)"
            self.LblDefW.Text = "Width (mm):"
            self.LblDefH.Text = "Height (mm):"
        else:
            self.HdrSize1.Text = "Diameter (mm)"
            self.LblDefW.Text = "Diameter (mm):"
            self.HdrSize2.Visibility = Visibility.Collapsed
            self.LblDefH.Visibility = Visibility.Collapsed
            self.PART_DefHeight.Visibility = Visibility.Collapsed

        if cfg['service_enabled']:
            self.HdrService.Text = cfg['service_label']
        else:
            self.HdrService.Visibility = Visibility.Collapsed

        self.HdrRun.Text = cfg['name']
        self.PART_DefWidth.Text = "{0:.0f}".format(cfg['def_w'])
        self.PART_DefHeight.Text = "{0:.0f}".format(cfg['def_h'])

        # pluralise the run label used in the count row ("trays", "pipes"...)
        self._word_plural = cfg['word'] + "s"

        self.PART_Generate.Click += self.generate_rows
        self.PART_Create.Click += self.on_create
        self.PART_Cancel.Click += self.on_cancel
        self.PART_Layout.SelectionChanged += self.on_layout_changed

        # apply initial layout wording (Parallel by default)
        self.on_layout_changed(None, None)
        self.generate_rows(None, None)

    def is_stack(self):
        try:
            return self.PART_Layout.SelectedIndex == 1
        except Exception:
            return False

    def on_layout_changed(self, sender, args):
        """Toggle wording / controls between Parallel and Stack layouts."""
        stack = self.is_stack()
        word_pl = getattr(self, '_word_plural', 'runs')

        if stack:
            self.PART_CountLbl.Text = "Number of stacks:"
            self.LblDefSpacing.Text = "Vertical spacing (mm):"
            self.PART_Intro.Text = (
                "Set how many %s to stack vertically, choose the direction "
                "(up / down), then adjust each level's size and vertical "
                "spacing." % word_pl)
            self.PART_Flip.Visibility = Visibility.Collapsed
            self.PART_DirLbl.Visibility = Visibility.Visible
            self.PART_Direction.Visibility = Visibility.Visible
        else:
            self.PART_CountLbl.Text = "Number of runs:"
            self.LblDefSpacing.Text = "Default spacing (mm):"
            self.PART_Intro.Text = (
                "Set how many parallel %s to create, then adjust each run's "
                "size, %s and spacing." % (
                    word_pl,
                    self.cfg['service_label'].lower()
                    if self.cfg['service_enabled'] else "options"))
            self.PART_Flip.Visibility = Visibility.Visible
            self.PART_DirLbl.Visibility = Visibility.Collapsed
            self.PART_Direction.Visibility = Visibility.Collapsed

    def generate_rows(self, sender, args):
        cfg = self.cfg
        count = int(to_float(self.PART_Count.Text, 5))
        count = max(1, min(count, 100))
        def_w = to_float(self.PART_DefWidth.Text, 100)
        def_h = to_float(self.PART_DefHeight.Text, 50)
        def_s = to_float(self.PART_DefSpacing.Text, 300)

        self.PART_Rows.Children.Clear()
        self.row_controls = []

        for i in range(count):
            row = StackPanel()
            row.Orientation = Orientation.Horizontal
            row.Margin = Thickness(0, 1, 0, 1)

            lbl = TextBlock()
            lbl.Text = "{0} {1}".format(cfg['name'], i + 1)
            lbl.Width = 84
            lbl.Foreground = CA_LIGHT          # readable on dark purple
            lbl.VerticalAlignment = VerticalAlignment.Center
            row.Children.Add(lbl)

            w_box = TextBox(); w_box.Width = 88; w_box.Margin = Thickness(0, 0, 4, 0)
            w_box.Background = CA_FIELD_BG; w_box.Foreground = CA_DARK
            w_box.BorderBrush = CA_PURPLE
            w_box.Text = "{0:.0f}".format(def_w)
            row.Children.Add(w_box)

            h_box = TextBox(); h_box.Width = 88; h_box.Margin = Thickness(0, 0, 4, 0)
            h_box.Background = CA_FIELD_BG; h_box.Foreground = CA_DARK
            h_box.BorderBrush = CA_PURPLE
            h_box.Text = "{0:.0f}".format(def_h)
            if cfg['size_mode'] != 'wh':
                h_box.Visibility = Visibility.Collapsed
            row.Children.Add(h_box)

            s_cb = None
            if cfg['service_enabled']:
                s_cb = ComboBox(); s_cb.Width = 200; s_cb.Margin = Thickness(0, 0, 4, 0)
                s_cb.Background = CA_FIELD_BG; s_cb.Foreground = CA_DARK
                s_cb.BorderBrush = CA_PURPLE
                s_cb.IsEditable = (cfg['service_kind'] == 'string')
                for opt in cfg['service_options']:
                    s_cb.Items.Add(opt)
                if cfg['def_service']:
                    s_cb.Text = cfg['def_service']
                row.Children.Add(s_cb)

            sp_box = TextBox(); sp_box.Width = 88
            sp_box.Background = CA_FIELD_BG; sp_box.Foreground = CA_DARK
            sp_box.BorderBrush = CA_PURPLE
            sp_box.Text = "{0:.0f}".format(def_s)
            row.Children.Add(sp_box)

            self.PART_Rows.Children.Add(row)
            self.row_controls.append(
                {'w': w_box, 'h': h_box, 'service': s_cb, 'spacing': sp_box})

    def on_create(self, sender, args):
        cfg = self.cfg
        rows = []
        for rc in self.row_controls:
            w = to_float(rc['w'].Text, 100)
            h = to_float(rc['h'].Text, 50) if cfg['size_mode'] == 'wh' else w
            svc = rc['service'].Text if rc['service'] is not None else None
            rows.append({'width': w, 'height': h, 'service': svc,
                         'spacing': to_float(rc['spacing'].Text, 300)})
        self.result = {
            'rows': rows,
            'mode': 'center' if self.PART_Mode.SelectedIndex == 0 else 'gap',
            'flip': bool(self.PART_Flip.IsChecked),
            'layout': 'stack' if self.is_stack() else 'parallel',
            'direction': 'down' if self.PART_Direction.SelectedIndex == 1 else 'up',
        }
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def run(discipline):
    doc = revit.doc
    uidoc = revit.uidoc
    spec = get_spec(discipline, doc)

    src_list = pick_sources(uidoc, doc, spec)
    if not src_list:
        return
    src = src_list[0]

    # Gather the full source path: union of the connected run from every seed.
    # This makes the tool work when several segments (or several separate
    # pieces) are pre-selected, not just a single element.
    found = {}
    for seed in src_list:
        for e in collect_run(seed, spec).values():
            found[e.Id.IntegerValue] = e
    curves = found

    # Split the selection into independent connected runs, then order each one
    # geometrically (identical behaviour for tray / duct / pipe / conduit).
    components = connected_components(curves, spec)

    paths = []
    any_branched = False
    for comp in components:
        ordered, complete = order_component(list(comp.values()))
        if not complete:
            any_branched = True
        path = prepare_path(ordered)
        if path is not None:
            paths.append(path)

    if any_branched:
        forms.alert("A selected run branches (tee/cross). Only its main chain "
                    "is copied; branches are ignored.", title="Parallel " + spec.name)
    if not paths:
        forms.alert("Could not read a centerline from the selection.",
                    title="Parallel " + spec.name)
        return

    mode_size = detect_size_mode(src, spec)
    src_w, src_h = read_size_mm(src, spec, mode_size)
    type_id = src.GetTypeId()
    try:
        level_id = src.ReferenceLevel.Id
    except Exception:
        lp = src.get_Parameter(BuiltInParameter.RBS_START_LEVEL_PARAM)
        level_id = lp.AsElementId() if lp else ElementId.InvalidElementId

    # service / system options
    name_to_id = {}
    default_sys_id = None
    if spec.service == 'string':
        service_enabled = True
        service_kind = 'string'
        service_options = collect_service_strings(doc, spec)
        def_service = source_service_string(src)
    elif spec.service == 'system':
        service_enabled = True
        service_kind = 'system'
        name_to_id = collect_system_types(doc, spec)
        src_sys_id = source_system_id(src)

        # make sure the source's own system type is always an option/default
        if src_sys_id is not None:
            try:
                nm = elem_name(doc.GetElement(src_sys_id))
            except Exception:
                nm = "Source system"
            name_to_id[nm] = src_sys_id
            default_sys_id = src_sys_id
            def_service = nm
        elif name_to_id:
            def_service = sorted(name_to_id.keys())[0]
            default_sys_id = name_to_id[def_service]
        else:
            def_service = ""
            default_sys_id = None

        if not name_to_id and default_sys_id is None:
            forms.alert(
                "No %s system types were found in this model, and the selected "
                "%s has no system assigned, so new %ss cannot be created.\n\n"
                "Assign a system type to the source %s (or create one) and try "
                "again." % (spec.name.lower(), spec.word, spec.word, spec.word),
                title="Parallel " + spec.name)
            return
        service_options = sorted(name_to_id.keys())
    else:
        service_enabled = False
        service_kind = 'none'
        service_options = []
        def_service = ""

    cfg = {
        'name': spec.name, 'word': spec.word,
        'size_mode': mode_size, 'def_w': src_w, 'def_h': src_h,
        'service_enabled': service_enabled, 'service_kind': service_kind,
        'service_label': spec.service_label, 'service_options': service_options,
        'def_service': def_service,
    }

    win = ParallelWindow(cfg)
    win.ShowDialog()
    if not win.result:
        return

    rows = win.result['rows']
    spacing_mode = win.result['mode']
    layout = win.result.get('layout', 'parallel')
    vertical = (layout == 'stack')

    if vertical:
        # Up = +Z, Down = -Z. Clear-gap spacing uses the vertical size (height).
        sign = -1.0 if win.result.get('direction') == 'down' else 1.0
    else:
        sign = -1.0 if win.result['flip'] else 1.0

    created_runs = 0
    created_segs = 0

    tx_word = "Stacked" if vertical else "Parallel"
    with revit.Transaction("Create %s %ss" % (tx_word, spec.name)):
        for path in paths:
            # Each source run gets its own fresh set of offsets starting at 0.
            cumulative_mm = 0.0
            prev_half_mm = (src_h if vertical else src_w) / 2.0

            for r in rows:
                # dimension that matters for edge-to-edge spacing: width when
                # placing side-by-side, height when stacking vertically.
                gap_dim = r['height'] if vertical else r['width']
                if spacing_mode == 'center':
                    cumulative_mm += r['spacing']
                else:
                    cumulative_mm += prev_half_mm + r['spacing'] + (gap_dim / 2.0)
                    prev_half_mm = gap_dim / 2.0

                # resolve system id for this run (duct / pipe)
                sys_id = default_sys_id
                if spec.service == 'system':
                    sys_id = name_to_id.get(r['service'], default_sys_id)

                d_ft = mm_to_ft(cumulative_mm) * sign
                verts = offset_path(path, d_ft, vertical=vertical)

                seg_curves = []
                run_elems = []
                for i in range(len(verts) - 1):
                    a, b = verts[i], verts[i + 1]
                    if a.DistanceTo(b) < 1e-6:
                        continue
                    elem = spec.create(type_id, level_id, sys_id, a, b)
                    set_size(elem, spec, mode_size, r['width'], r['height'])
                    seg_curves.append((elem, verts[i + 1]))
                    run_elems.append(elem)
                    created_segs += 1

                for i in range(len(seg_curves) - 1):
                    vtx = seg_curves[i][1]
                    c1 = connector_nearest(seg_curves[i][0], vtx)
                    c2 = connector_nearest(seg_curves[i + 1][0], vtx)
                    if c1 is not None and c2 is not None:
                        try:
                            fitting = doc.Create.NewElbowFitting(c1, c2)
                            if fitting is not None:
                                run_elems.append(fitting)
                        except Exception:
                            logger.debug("Elbow fitting failed at a corner.")

                # Apply the chosen Service Type uniformly to the whole run AFTER
                # the elbows exist, so straights and bends stay on the same
                # service (Revit re-propagates service when a fitting joins the
                # segments into one network).
                if spec.service == 'string' and r['service']:
                    for e in run_elems:
                        set_service_string(e, r['service'])

                created_runs += 1

    verb = "stacked" if vertical else "parallel"
    forms.alert("Created {0} {1} {2} run(s) from {3} source run(s) / "
                "{4} segment(s)."
                .format(created_runs, verb, spec.word, len(paths), created_segs),
                title=("Stack " if vertical else "Parallel ") + spec.name)
