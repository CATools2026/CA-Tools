# bend_core.py  -  CoolOffset MEP Bend  -  pure ASCII
#
# WORKFLOW (no pre-splitting needed):
#  1. Popup: Offset height/distance, Angle, Direction (Up/Down/Left/Right), Gap length
#  2. Select the whole MEP run
#  3. Click one point = center of offset
#  4. Tool computes S = center - gap/2, E = center + gap/2 along run axis
#  5. Validates gap >= 2 * run_back
#  6. Deletes the original run segment
#  7. Creates up to 5 segments:
#       left_stub  : original start -> S   (if any)
#       seg_rise   : S -> A
#       seg_mid    : A -> B  (offset run)
#       seg_drop   : B -> E
#       right_stub : E -> original end  (if any)
#  8. Places 4 elbows: at S, A, B, E
#
#  Direction reference:
#     Up / Down    -> vertical offset   (moves along world Z)
#     Left / Right -> horizontal offset (moves sideways in plan,
#                     perpendicular to the run; if the run itself
#                     runs vertically, falls back to world Y)

import math
import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")

from Autodesk.Revit.DB import (
    Transaction, XYZ, ElementId,
    FilteredElementCollector, UnitUtils,
    LocationCurve, BuiltInCategory,
)
from Autodesk.Revit.DB.Plumbing   import Pipe
from Autodesk.Revit.DB.Mechanical import Duct
from Autodesk.Revit.DB.Electrical import Conduit, CableTray
from Autodesk.Revit.UI.Selection  import ISelectionFilter, ObjectType

import System.Windows.Forms as WF
import System.Drawing       as D

# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------
def mm_to_ft(mm):
    try:
        from Autodesk.Revit.DB import UnitTypeId
        return UnitUtils.ConvertToInternalUnits(float(mm), UnitTypeId.Millimeters)
    except Exception:
        from Autodesk.Revit.DB import DisplayUnitType
        return UnitUtils.ConvertToInternalUnits(float(mm), DisplayUnitType.DUT_MILLIMETERS)

def ft_to_mm(ft):
    try:
        from Autodesk.Revit.DB import UnitTypeId
        return UnitUtils.ConvertFromInternalUnits(float(ft), UnitTypeId.Millimeters)
    except Exception:
        from Autodesk.Revit.DB import DisplayUnitType
        return UnitUtils.ConvertFromInternalUnits(float(ft), DisplayUnitType.DUT_MILLIMETERS)

MEP_CLASSES = {
    "Pipe"      : Pipe,
    "Duct"      : Duct,
    "Conduit"   : Conduit,
    "CableTray" : CableTray,
}

FITTING_CATS = [
    BuiltInCategory.OST_PipeFitting,
    BuiltInCategory.OST_DuctFitting,
    BuiltInCategory.OST_ConduitFitting,
    BuiltInCategory.OST_CableTrayFitting,
]

class MEPFilter(ISelectionFilter):
    def __init__(self, cls):
        self._cls = cls
    def AllowElement(self, el):
        return isinstance(el, self._cls)
    def AllowReference(self, ref, pt):
        return False

# ---------------------------------------------------------------------------
# Theme  -  CA Tools dark-purple brand palette
#   #221A30 background  /  #7F77DD purple  /  #D85A30 orange  /  #F5F5F3 light
# ---------------------------------------------------------------------------
BG_DARK    = D.Color.FromArgb(34,  26,  48)    # #221A30  main background
BG_MID     = D.Color.FromArgb(45,  36,  62)    # slightly lighter purple panels
PURPLE     = D.Color.FromArgb(127, 119, 221)   # #7F77DD  accent
PURPLE_HOV = D.Color.FromArgb(150, 142, 240)   # accent hover
ORANGE     = D.Color.FromArgb(216, 90,  48)    # #D85A30  copyright / highlight
TEXT_WHT   = D.Color.FromArgb(245, 245, 243)   # #F5F5F3  light text
TEXT_GRY   = D.Color.FromArgb(160, 155, 180)   # muted purple-grey
BTN_CNCL   = D.Color.FromArgb(55,  46,  74)    # cancel button
INPUT_BG   = D.Color.FromArgb(45,  36,  62)    # input background
BORDER     = D.Color.FromArgb(90,  80,  130)   # borders

# ---------------------------------------------------------------------------
# Dialog  -  now includes Gap field
# ---------------------------------------------------------------------------
class BendDialog(WF.Form):
    def __init__(self, type_name):
        WF.Form.__init__(self)
        self.Text            = "CoolOffset  -  {} Bend".format(type_name)
        self.FormBorderStyle = WF.FormBorderStyle.FixedDialog
        self.StartPosition   = WF.FormStartPosition.CenterScreen
        self.MinimizeBox     = False
        self.MaximizeBox     = False
        self.BackColor       = BG_DARK

        # Unit state: values are shown/entered either in "mm" or "ft".
        # Internally run_bend always receives mm (see offset_mm / gap_mm).
        self.units      = "mm"
        self.unit_lbls  = []   # the little "mm"/"ft" labels beside each field

        FN  = D.Font("Segoe UI", 9)
        FNB = D.Font("Segoe UI", 9, D.FontStyle.Bold)
        FNS = D.Font("Segoe UI", 8)

        # Layout constants
        LX  = 16          # left margin for labels
        IX  = 140         # input box left edge
        IW  = 185         # input box width
        RW  = 410         # total usable row width (LX to right edge)
        y   = 18          # current vertical cursor

        def add_lbl(txt, top, x=LX, w=120, small=False):
            l = WF.Label()
            l.Text      = txt
            l.Left      = x
            l.Top       = top + 3
            l.Width     = w
            l.Height    = 20
            l.Font      = FNS if small else FN
            l.ForeColor = TEXT_GRY if small else TEXT_WHT
            l.BackColor = BG_DARK
            self.Controls.Add(l)
            return l

        def add_input(default, top, width=IW):
            tb = WF.TextBox()
            tb.Left        = IX
            tb.Top         = top
            tb.Width       = width
            tb.Font        = FNB
            tb.Text        = str(default)
            tb.BackColor   = INPUT_BG
            tb.ForeColor   = TEXT_WHT
            tb.BorderStyle = WF.BorderStyle.FixedSingle
            self.Controls.Add(tb)
            return tb

        def add_combo(opts, top, sel=0):
            cb = WF.ComboBox()
            cb.Left          = IX
            cb.Top           = top
            cb.Width         = IW
            cb.Font          = FNB
            cb.DropDownStyle = WF.ComboBoxStyle.DropDownList
            cb.BackColor     = INPUT_BG
            cb.ForeColor     = TEXT_WHT
            for o in opts:
                cb.Items.Add(o)
            cb.SelectedIndex = sel
            self.Controls.Add(cb)
            return cb

        def add_mm(top):
            l = add_lbl("mm", top, IX + IW + 6, 26, small=True)
            self.unit_lbls.append(l)
            return l

        # -- Offset Height / Distance -----------------------------------
        offset_top      = y
        self.lbl_offset = add_lbl("Offset Height", y)
        self.tb_offset  = add_input("500", y)
        add_mm(y)
        y += 36

        # -- Gap Length -----------------------------------------------
        gap_top = y
        add_lbl("Gap Length", y)
        self.tb_gap = add_input("1000", y)
        add_mm(y)
        y += 36

        # -- Units toggle (mm <-> ft) ----------------------------------
        # Tall button on the right, spanning the two numeric rows.
        self.btn_units             = WF.Button()
        self.btn_units.Text        = "mm"
        self.btn_units.Left        = IX + IW + 36        # right of the unit labels
        self.btn_units.Top         = offset_top
        self.btn_units.Width       = 46
        self.btn_units.Height      = (gap_top + 22) - offset_top
        self.btn_units.Font        = FNB
        self.btn_units.BackColor   = BG_MID
        self.btn_units.ForeColor   = PURPLE
        self.btn_units.FlatStyle   = WF.FlatStyle.Flat
        self.btn_units.FlatAppearance.BorderColor = PURPLE
        self.btn_units.TextAlign   = D.ContentAlignment.MiddleCenter
        try:
            tip = WF.ToolTip()
            tip.SetToolTip(self.btn_units, "Click to switch units (mm / feet)")
        except Exception:
            pass

        def _toggle_units(sender, args):
            # Convert the current field values to the other unit, then flip.
            def _read(tb):
                try:    return float(tb.Text)
                except Exception: return 0.0
            if self.units == "mm":                       # mm -> ft
                self.tb_offset.Text = "{:.4f}".format(mm_to_ft(_read(self.tb_offset)))
                self.tb_gap.Text    = "{:.4f}".format(mm_to_ft(_read(self.tb_gap)))
                self.units          = "ft"
            else:                                        # ft -> mm
                self.tb_offset.Text = "{:.0f}".format(ft_to_mm(_read(self.tb_offset)))
                self.tb_gap.Text    = "{:.0f}".format(ft_to_mm(_read(self.tb_gap)))
                self.units          = "mm"
            lbl = "ft" if self.units == "ft" else "mm"
            self.btn_units.Text = lbl
            for ul in self.unit_lbls:
                ul.Text = lbl

        try:
            self.btn_units.Click += _toggle_units
        except Exception:
            pass
        self.Controls.Add(self.btn_units)

        # -- Angle ----------------------------------------------------
        add_lbl("Angle", y)
        self.cb_angle = add_combo(["30", "45", "60", "90"], y, sel=3)
        y += 36

        # -- Bend Direction group ----------------------------------------
        # Row 1 = vertical directions  (UP / DOWN)      - unchanged
        # Row 2 = horizontal directions (LEFT / RIGHT)   - new, sits below row 1
        ROW1_TOP = 22
        ROW2_TOP = ROW1_TOP + 32

        self.grp2            = WF.GroupBox()
        self.grp2.Text       = "Bend Direction"
        self.grp2.Left       = LX
        self.grp2.Top        = y
        self.grp2.Width      = RW
        self.grp2.Height     = 90
        self.grp2.Font       = FN
        self.grp2.ForeColor  = PURPLE
        self.grp2.BackColor  = BG_MID
        self.Controls.Add(self.grp2)

        def add_dir_radio(text, left, top, width, checked=False):
            rb           = WF.RadioButton()
            rb.Text      = text
            rb.Left      = left
            rb.Top       = top
            rb.Width     = width
            rb.Font      = FNB
            rb.ForeColor = PURPLE
            rb.BackColor = BG_MID
            rb.Checked   = checked
            self.grp2.Controls.Add(rb)
            return rb

        # Row 1 - vertical (same positions as before)
        self.rb_a = add_dir_radio("UP",   14,  ROW1_TOP, 160, checked=True)
        self.rb_b = add_dir_radio("DOWN", 190, ROW1_TOP, 180)

        # Row 2 - horizontal (new, directly below row 1)
        self.rb_c = add_dir_radio("LEFT",  14,  ROW2_TOP, 160)
        self.rb_d = add_dir_radio("RIGHT", 190, ROW2_TOP, 180)

        # Keep the offset field's label in sync with the chosen direction
        # (vertical = "Offset Height", horizontal = "Offset Distance").
        # Wrapped in try/except so a label refresh never blocks the dialog.
        def _sync_offset_label(sender, args):
            if self.rb_c.Checked or self.rb_d.Checked:
                self.lbl_offset.Text = "Offset Distance"
            else:
                self.lbl_offset.Text = "Offset Height"

        try:
            self.rb_a.CheckedChanged += _sync_offset_label
            self.rb_b.CheckedChanged += _sync_offset_label
            self.rb_c.CheckedChanged += _sync_offset_label
            self.rb_d.CheckedChanged += _sync_offset_label
        except Exception:
            pass

        y += self.grp2.Height + 10

        # -- Buttons (Cancel left, Pick Center & Bend right, same row) --
        BTN_H    = 32
        BTN_OK_W = 158

        btn_c                            = WF.Button()
        btn_c.Text                       = "Cancel"
        btn_c.Left                       = LX
        btn_c.Top                        = y
        btn_c.Width                      = 90
        btn_c.Height                     = BTN_H
        btn_c.Font                       = FN
        btn_c.BackColor                  = BTN_CNCL
        btn_c.ForeColor                  = TEXT_WHT
        btn_c.FlatStyle                  = WF.FlatStyle.Flat
        btn_c.FlatAppearance.BorderColor = BORDER
        btn_c.DialogResult               = WF.DialogResult.Cancel
        self.Controls.Add(btn_c)

        btn_b                            = WF.Button()
        btn_b.Text                       = "Pick Center & Bend"
        btn_b.Top                        = y          # same row as Cancel
        btn_b.Width                      = BTN_OK_W
        btn_b.Height                     = BTN_H
        btn_b.Font                       = FNB
        btn_b.BackColor                  = PURPLE
        btn_b.ForeColor                  = TEXT_WHT
        btn_b.FlatStyle                  = WF.FlatStyle.Flat
        btn_b.FlatAppearance.BorderColor = PURPLE_HOV
        btn_b.DialogResult               = WF.DialogResult.OK
        self.Controls.Add(btn_b)

        self.AcceptButton = btn_b
        self.CancelButton = btn_c

        y += BTN_H + 12   # gap below buttons before footer

        # -- Footer ----------------------------------------------------
        FOOT_H = 22
        FOOT_Y = y

        auth            = WF.Label()
        auth.Text       = u"Copyright \u00A9 2026 Chulan Adasuriya"
        auth.Left       = LX
        auth.Top        = FOOT_Y + 2
        auth.Width      = 260
        auth.Height     = FOOT_H
        auth.Font       = D.Font("Segoe UI", 8, D.FontStyle.Bold)
        auth.ForeColor  = PURPLE
        auth.BackColor  = BG_DARK
        auth.TextAlign  = D.ContentAlignment.MiddleCenter
        self.Controls.Add(auth)

        ver             = WF.Label()
        ver.Text        = "v2.1.0"
        ver.Top         = FOOT_Y + 2
        ver.Height      = FOOT_H
        ver.Font        = D.Font("Segoe UI", 8)
        ver.ForeColor   = TEXT_GRY
        ver.BackColor   = BG_DARK
        self.Controls.Add(ver)

        # -- Final window sizing ---------------------------------------
        # Total client width  = LX + RW + LX  (16 + 390 + 16 = 422)
        # Total client height = FOOT_Y + FOOT_H + bottom-padding
        CLIENT_W = LX + RW + LX           # 422 px client width
        CLIENT_H = FOOT_Y + FOOT_H + 10   # breathing room at bottom

        self.ClientSize = D.Size(CLIENT_W, CLIENT_H)

        # Right-align OK button and ver label now that CLIENT_W is known
        btn_b.Left  = CLIENT_W - LX - BTN_OK_W
        ver.Left    = CLIENT_W - LX - 50
        ver.Width   = 50

        # Center the copyright across the footer width
        auth.Left   = LX
        auth.Width  = CLIENT_W - 2 * LX


    @property
    def offset_mm(self):
        try:
            v = float(self.tb_offset.Text)
            return ft_to_mm(v) if self.units == "ft" else v
        except Exception:
            return 500.0

    @property
    def gap_mm(self):
        try:
            v = float(self.tb_gap.Text)
            return ft_to_mm(v) if self.units == "ft" else v
        except Exception:
            return 1000.0

    @property
    def angle_deg(self):
        return int(str(self.cb_angle.SelectedItem))

    @property
    def direction(self):
        if self.rb_a.Checked: return "Up"
        if self.rb_b.Checked: return "Down"
        if self.rb_c.Checked: return "Left"
        return "Right"

# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
def _unit(v):
    L = math.sqrt(v.X*v.X + v.Y*v.Y + v.Z*v.Z)
    if L < 1e-9: return v
    return XYZ(v.X/L, v.Y/L, v.Z/L)

def _normal(run_dir, direction):
    """Unit vector perpendicular to the run, pointing the chosen way.
    Up / Down    -> vertical offset (world Z).
    Left / Right -> horizontal offset, perpendicular to the run in
                    plan. If the run itself is vertical (no plan
                    component), falls back to world Y as 'right'."""
    if direction == "Up":   return XYZ(0, 0, 1)
    if direction == "Down": return XYZ(0, 0, -1)
    p = run_dir.CrossProduct(XYZ(0, 0, 1))
    if p.GetLength() < 1e-6: p = XYZ(0, 1, 0)
    p = _unit(p)
    return p if direction == "Right" else XYZ(-p.X, -p.Y, -p.Z)

def _proj_onto_run(pt, run_start, run_dir, run_len):
    """Project pt onto run axis, clamped to [0, run_len]. Returns XYZ on axis."""
    vx = pt.X - run_start.X
    vy = pt.Y - run_start.Y
    vz = pt.Z - run_start.Z
    t  = vx*run_dir.X + vy*run_dir.Y + vz*run_dir.Z
    t  = max(0.0, min(t, run_len))
    return XYZ(run_start.X + run_dir.X*t,
               run_start.Y + run_dir.Y*t,
               run_start.Z + run_dir.Z*t), t

def _bend_points(S, E, rd, n, H, ang):
    rb = H / math.tan(math.radians(ang))
    A = XYZ(S.X + rd.X*rb + n.X*H,
             S.Y + rd.Y*rb + n.Y*H,
             S.Z + rd.Z*rb + n.Z*H)
    B = XYZ(E.X - rd.X*rb + n.X*H,
             E.Y - rd.Y*rb + n.Y*H,
             E.Z - rd.Z*rb + n.Z*H)
    return A, B

# ---------------------------------------------------------------------------
# ElementId helpers
# ---------------------------------------------------------------------------
def _eid_int(eid):
    try:    return int(eid.IntegerValue)
    except Exception:
        try: return int(eid.Value)
        except Exception: return -1

def _eid(v):
    return ElementId(int(v))

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
def _get_level_int(el):
    try:    return _eid_int(el.ReferenceLevel.Id)
    except Exception: pass
    try:    return _eid_int(el.LevelId)
    except Exception: pass
    from Autodesk.Revit.DB import Level
    lvls = list(FilteredElementCollector(el.Document).OfClass(Level).ToElements())
    return _eid_int(lvls[0].Id) if lvls else -1

def _bip_double(el, name):
    from Autodesk.Revit.DB import BuiltInParameter
    bip = getattr(BuiltInParameter, name, None)
    if bip is None: return None
    p = el.get_Parameter(bip)
    return float(p.AsDouble()) if p else None

def _cache(el, type_name):
    from Autodesk.Revit.DB import BuiltInParameter as BIP
    c = {}
    c["el_id"]    = _eid_int(el.Id)
    c["level_id"] = _get_level_int(el)
    c["type_id"]  = _eid_int(el.GetTypeId())

    if type_name == "Pipe":
        try:    c["type_id"] = _eid_int(el.PipeType.Id)
        except Exception: pass
        c["sys_id"] = -1
        try:
            p = el.get_Parameter(BIP.RBS_PIPING_SYSTEM_TYPE_PARAM)
            if p: c["sys_id"] = _eid_int(p.AsElementId())
        except Exception: pass
        c["diam"] = _bip_double(el, "RBS_PIPE_OUTER_DIAMETER")

    elif type_name == "Duct":
        try:    c["type_id"] = _eid_int(el.DuctType.Id)
        except Exception: pass
        c["sys_id"] = -1
        try:
            p = el.get_Parameter(BIP.RBS_DUCT_SYSTEM_TYPE_PARAM)
            if p: c["sys_id"] = _eid_int(p.AsElementId())
        except Exception: pass
        c["width"]  = _bip_double(el, "RBS_CURVE_WIDTH_PARAM")  or mm_to_ft(400)
        c["height"] = _bip_double(el, "RBS_CURVE_HEIGHT_PARAM") or mm_to_ft(400)

    elif type_name == "Conduit":
        c["diam"] = _bip_double(el, "CONDUIT_OUTER_DIAM_PARAM")

    elif type_name == "CableTray":
        c["width"]  = _bip_double(el, "RBS_CABLETRAY_WIDTH_PARAM")  or mm_to_ft(300)
        c["height"] = _bip_double(el, "RBS_CABLETRAY_HEIGHT_PARAM") or mm_to_ft(100)

    return c

def _set_p(el, name, val):
    from Autodesk.Revit.DB import BuiltInParameter
    bip = getattr(BuiltInParameter, name, None)
    if bip is None: return
    p = el.get_Parameter(bip)
    if p:
        try: p.Set(float(val))
        except Exception: pass

# ---------------------------------------------------------------------------
# Connectors
# ---------------------------------------------------------------------------
def _connectors(el):
    try:    return list(el.ConnectorManager.Connectors)
    except Exception: return []

def _near_conn(el, pt, free_only=False):
    best, bd = None, 1e18
    for c in _connectors(el):
        if free_only and c.IsConnected: continue
        d = c.Origin.DistanceTo(pt)
        if d < bd: best, bd = c, d
    return best

def _place_elbow(doc, sa, sb, pt):
    ca = _near_conn(sa, pt, free_only=True) or _near_conn(sa, pt)
    cb = _near_conn(sb, pt, free_only=True) or _near_conn(sb, pt)
    if ca and cb:
        try: doc.Create.NewElbowFitting(ca, cb); return True
        except Exception: pass
    return False

# ---------------------------------------------------------------------------
# Segment creation
# ---------------------------------------------------------------------------
def _create_one(doc, type_name, c, s, e):
    """Create a single MEP segment from s to e using cached props c."""
    lvl = _eid(c["level_id"])
    tid = _eid(c["type_id"])

    if type_name == "Pipe":
        from Autodesk.Revit.DB.Plumbing import Pipe as _P
        seg = _P.Create(doc, _eid(c["sys_id"]), tid, lvl, s, e)
        if c.get("diam"): _set_p(seg, "RBS_PIPE_OUTER_DIAMETER", c["diam"])

    elif type_name == "Duct":
        from Autodesk.Revit.DB.Mechanical import Duct as _D
        seg = _D.Create(doc, _eid(c["sys_id"]), tid, lvl, s, e)
        _set_p(seg, "RBS_CURVE_WIDTH_PARAM",  c["width"])
        _set_p(seg, "RBS_CURVE_HEIGHT_PARAM", c["height"])

    elif type_name == "Conduit":
        from Autodesk.Revit.DB.Electrical import Conduit as _C
        seg = _C.Create(doc, tid, s, e, lvl)
        if c.get("diam"): _set_p(seg, "CONDUIT_OUTER_DIAM_PARAM", c["diam"])

    elif type_name == "CableTray":
        from Autodesk.Revit.DB.Electrical import CableTray as _CT
        seg = _CT.Create(doc, tid, s, e, lvl)
        _set_p(seg, "RBS_CABLETRAY_WIDTH_PARAM",  c["width"])
        _set_p(seg, "RBS_CABLETRAY_HEIGHT_PARAM", c["height"])

    else:
        raise ValueError("Unknown type: {}".format(type_name))

    return seg

MIN_SEG = 0.05   # minimum segment length in feet (~15mm) to bother creating

# ---------------------------------------------------------------------------
# Find any union fittings near a point (to clean up after deletion)
# ---------------------------------------------------------------------------
def _union_ids_near(doc, pt, tol=0.15):
    ids = []
    for bcat in FITTING_CATS:
        try:
            from Autodesk.Revit.DB import ElementCategoryFilter, FilteredElementCollector as FEC
            filt = ElementCategoryFilter(bcat)
            for el in FEC(doc).WherePasses(filt).WhereElementIsNotElementType():
                conns = _connectors(el)
                if len(conns) != 2: continue
                near = [c for c in conns if c.Origin.DistanceTo(pt) < tol]
                if not near: continue
                # Check if collinear (union)
                try:
                    d0 = conns[0].CoordinateSystem.BasisX
                    d1 = conns[1].CoordinateSystem.BasisX
                    dot = abs(d0.X*d1.X + d0.Y*d1.Y + d0.Z*d1.Z)
                    if dot > 0.95:
                        ids.append(_eid_int(el.Id))
                except Exception:
                    ids.append(_eid_int(el.Id))
        except Exception:
            pass
    return ids

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_bend(uidoc, type_name):
    from pyrevit import forms as pf
    doc = uidoc.Document
    cls = MEP_CLASSES[type_name]

    # 1. Show dialog FIRST so user sets values before picking
    dlg = BendDialog(type_name)
    if dlg.ShowDialog() != WF.DialogResult.OK:
        return

    H       = mm_to_ft(dlg.offset_mm)
    gap_ft  = mm_to_ft(dlg.gap_mm)
    ang     = dlg.angle_deg
    drn     = dlg.direction

    # Validate: gap must be >= 2 * run_back
    rb      = H / math.tan(math.radians(ang))
    min_gap = rb * 2.0
    if gap_ft < min_gap:
        pf.alert(
            "Gap too small for this angle and offset height.\n\n"
            "  Gap entered  : {:.0f} mm\n"
            "  Minimum gap  : {:.0f} mm\n\n"
            "Increase Gap, reduce Offset Height, or increase Angle.".format(
                dlg.gap_mm, ft_to_mm(min_gap)
            ),
            title="CoolOffset - Gap Too Small"
        )
        return

    # 2. Select the whole run
    try:
        ref = uidoc.Selection.PickObject(
            ObjectType.Element,
            MEPFilter(cls),
            "Select the {} run".format(type_name)
        )
    except Exception:
        return

    el  = doc.GetElement(ref.ElementId)
    loc = el.Location
    if not isinstance(loc, LocationCurve):
        pf.alert("Selected element has no curve location.", title="CoolOffset")
        return

    crv       = loc.Curve
    run_start = crv.GetEndPoint(0)
    run_end   = crv.GetEndPoint(1)
    run_len   = run_start.DistanceTo(run_end)
    if run_len < 1e-6:
        pf.alert("Selected element has zero length.", title="CoolOffset")
        return

    rd = _unit(XYZ(run_end.X - run_start.X,
                   run_end.Y - run_start.Y,
                   run_end.Z - run_start.Z))

    # 3. Pick center point on the run
    try:
        raw_center = uidoc.Selection.PickPoint(
            "Click the CENTER of the offset on the {} run".format(type_name)
        )
    except Exception:
        return

    # Project clicked point onto run axis
    center_on_run, t_center = _proj_onto_run(raw_center, run_start, rd, run_len)

    # Compute S and E (split points) = center +/- gap/2 along run
    half_gap = gap_ft / 2.0
    t_S = t_center - half_gap
    t_E = t_center + half_gap

    # Clamp to run
    t_S = max(0.0, t_S)
    t_E = min(run_len, t_E)

    S = XYZ(run_start.X + rd.X*t_S,
             run_start.Y + rd.Y*t_S,
             run_start.Z + rd.Z*t_S)
    E = XYZ(run_start.X + rd.X*t_E,
             run_start.Y + rd.Y*t_E,
             run_start.Z + rd.Z*t_E)

    actual_gap = S.DistanceTo(E)
    if actual_gap < min_gap:
        pf.alert(
            "After clamping to run length, gap is {:.0f} mm (minimum {:.0f} mm).\n\n"
            "Click closer to the center of the run, or use a shorter gap.".format(
                ft_to_mm(actual_gap), ft_to_mm(min_gap)
            ),
            title="CoolOffset - Gap Too Small"
        )
        return

    # 4. Cache all element data before transaction
    cache  = _cache(el, type_name)
    el_id  = int(cache["el_id"])

    # Run endpoints (for left/right stubs)
    orig_start = XYZ(run_start.X, run_start.Y, run_start.Z)
    orig_end   = XYZ(run_end.X,   run_end.Y,   run_end.Z)

    # 5. Compute offset geometry
    n    = _normal(rd, drn)
    A, B = _bend_points(S, E, rd, n, H, ang)

    # 6. Transaction
    with Transaction(doc, "CoolOffset - {} Bend {} deg {}".format(type_name, ang, drn)) as t:
        t.Start()
        try:
            # Delete original run
            doc.Delete(ElementId(el_id))

            # Also delete any union fittings that existed at run endpoints
            for uid in _union_ids_near(doc, orig_start) + _union_ids_near(doc, orig_end):
                try: doc.Delete(ElementId(uid))
                except Exception: pass

            # --- Create segments ---
            # Left stub: orig_start -> S  (only if meaningful length)
            left_stub  = None
            right_stub = None

            if orig_start.DistanceTo(S) > MIN_SEG:
                left_stub = _create_one(doc, type_name, cache, orig_start, S)

            # Offset legs
            seg_rise = _create_one(doc, type_name, cache, S, A)
            seg_mid  = _create_one(doc, type_name, cache, A, B)
            seg_drop = _create_one(doc, type_name, cache, B, E)

            # Right stub: E -> orig_end
            if orig_end.DistanceTo(E) > MIN_SEG:
                right_stub = _create_one(doc, type_name, cache, E, orig_end)

            # --- Place elbows ---

            # Elbow at A  (seg_rise -> seg_mid)
            _place_elbow(doc, seg_rise, seg_mid, A)

            # Elbow at B  (seg_mid -> seg_drop)
            _place_elbow(doc, seg_mid, seg_drop, B)

            # Elbow at S  (left_stub or existing adjacent -> seg_rise)
            if left_stub:
                _place_elbow(doc, left_stub, seg_rise, S)
            else:
                # Find any adjacent existing segment at orig_start / S
                for adj_cls in [Pipe, Duct, Conduit, CableTray]:
                    for adj in FilteredElementCollector(doc).OfClass(adj_cls):
                        if _eid_int(adj.Id) in {_eid_int(seg_rise.Id),
                                                 _eid_int(seg_mid.Id),
                                                 _eid_int(seg_drop.Id)}:
                            continue
                        for ac in _connectors(adj):
                            if ac.Origin.DistanceTo(S) < 0.15:
                                ca = ac
                                cb = _near_conn(seg_rise, S, free_only=True) or _near_conn(seg_rise, S)
                                if ca and cb:
                                    try: doc.Create.NewElbowFitting(ca, cb)
                                    except Exception: pass
                                break

            # Elbow at E  (seg_drop -> right_stub or existing adjacent)
            if right_stub:
                _place_elbow(doc, seg_drop, right_stub, E)
            else:
                for adj_cls in [Pipe, Duct, Conduit, CableTray]:
                    for adj in FilteredElementCollector(doc).OfClass(adj_cls):
                        if _eid_int(adj.Id) in {_eid_int(seg_rise.Id),
                                                 _eid_int(seg_mid.Id),
                                                 _eid_int(seg_drop.Id)}:
                            continue
                        for ac in _connectors(adj):
                            if ac.Origin.DistanceTo(E) < 0.15:
                                ca = ac
                                cb = _near_conn(seg_drop, E, free_only=True) or _near_conn(seg_drop, E)
                                if ca and cb:
                                    try: doc.Create.NewElbowFitting(ca, cb)
                                    except Exception: pass
                                break

            t.Commit()
        except Exception as ex:
            t.RollBack()
            pf.alert("Failed:\n{}".format(str(ex)), title="CoolOffset Error")
            return

    pf.alert(
        "{} offset created!\n\n"
        "  Angle     : {} deg\n"
        "  Offset    : {:.0f} mm\n"
        "  Gap       : {:.0f} mm\n"
        "  Direction : {}".format(
            type_name, ang, dlg.offset_mm, ft_to_mm(actual_gap), drn
        ),
        title="CoolOffset - Done",
        warn_icon=False
    )
