# -*- coding: utf-8 -*-
"""Auto-dimension the selected grids, on the grid-bubble side.

Copyright (c) 2026 Chulan Adasuriya

Workflow:
  1. Select two or more straight grids in a plan / elevation / section.
  2. Run this tool.
  3. In the dark-purple popup pick a dimension style and the SIDE the
     dimensions should sit on (Top / Bottom / Left / Right), then press *Done*.

It creates:
  * a continuous "grid-to-grid" dimension string across all selected grids
    (one segment between each consecutive pair -> the 4@... look), and
  * an overall dimension from the first grid to the last grid.

Both strings are placed on the chosen side and sit just INSIDE the grid
bubbles - right next to them but clear of the circles - so they read like a
normal grid dimension string and never overlap the bubbles at any scale.

The popup defaults the side to whichever end the grid bubbles are currently
shown on, so the dimensions land on the bubble side automatically. If you flip
the bubbles to the other side and re-run the tool, the dimensions snap across
to that side too.
"""

from pyrevit import revit, DB, forms, script

doc = revit.doc
view = doc.ActiveView
logger = script.get_logger()

TITLE = "Auto Dimension Grids"
COPYRIGHT = u"Copyright \u00A9 2026 Chulan Adasuriya"

# The dimension strings sit just INSIDE the grid-bubble end - right next to the
# bubbles but clear of the circles - like a normal grid dimension string.
# Distances are measured on PAPER (in feet) and multiplied by the view scale so
# the gap looks the same at any drawing scale.
#   0.030 ft ~ 0.36"  -> first (grid-to-grid) string, inset from the bubble end
#   0.035 ft ~ 0.42"  -> extra step further in for the overall string
FIRST_STRING_PAPER_FT = 0.030
STRING_GAP_PAPER_FT = 0.035


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _try(getter):
    """Run a zero-arg callable, swallowing any exception and returning None."""
    try:
        return getter()
    except Exception:
        return None


def safe_name(element):
    """Read an element's name reliably across IronPython 2/3 and CPython."""
    name = _try(lambda: DB.Element.Name.GetValue(element))
    if name:
        return name
    name = _try(lambda: element.Name)
    if name:
        return name
    for bip in (DB.BuiltInParameter.SYMBOL_NAME_PARAM,
                DB.BuiltInParameter.ALL_MODEL_TYPE_NAME):
        param = _try(lambda b=bip: element.get_Parameter(b))
        if param is not None:
            value = _try(lambda p=param: p.AsString())
            if value:
                return value
    return None


def is_linear_style(dim_type):
    """True / False / None (None = could not determine)."""
    try:
        return dim_type.StyleType == DB.DimensionStyleType.Linear
    except Exception:
        return None


def collect_dimension_styles():
    """Collect every dimension style; linear styles listed first."""
    collector = list(DB.FilteredElementCollector(doc).OfClass(DB.DimensionType))
    raw_count = len(collector)

    label_to_type = {}
    linear_labels = []
    other_labels = []
    name_failures = 0

    for dt in collector:
        name = safe_name(dt)
        if not name:
            name_failures += 1
            continue
        label = name
        suffix = 2
        while label in label_to_type:
            label = "{0} ({1})".format(name, suffix)
            suffix += 1
        label_to_type[label] = dt
        if is_linear_style(dt) is False:
            other_labels.append(label)
        else:
            linear_labels.append(label)

    ordered = sorted(linear_labels) + sorted(other_labels)
    diagnostics = ("DimensionType elements found: {0}\n"
                   "Names that could not be read: {1}".format(
                       raw_count, name_failures))
    return ordered, label_to_type, diagnostics


def create_dimension(dim_view, dim_line, refs, dim_type):
    """Create a dimension, falling back to the default style if needed."""
    try:
        return doc.Create.NewDimension(dim_view, dim_line, refs, dim_type)
    except Exception as err:
        logger.debug("Typed NewDimension failed ({0}); "
                     "retrying with default style.".format(err))
        dim = doc.Create.NewDimension(dim_view, dim_line, refs)
        try:
            dim.DimensionType = dim_type
        except Exception as err2:
            logger.debug("Could not apply chosen style ({0}); "
                         "kept default.".format(err2))
        return dim


def detect_bubble_side(grid_list, active_view):
    """Return the screen side (Top/Bottom/Left/Right) where the grid bubbles
    are currently shown, so the dimensions can default to the bubble side."""
    up = active_view.UpDirection
    right = active_view.RightDirection
    tally = {"Top": 0, "Bottom": 0, "Left": 0, "Right": 0}

    for g in grid_list:
        crv = g.Curve
        if not isinstance(crv, DB.Line):
            continue
        p0 = crv.GetEndPoint(0)
        p1 = crv.GetEndPoint(1)
        mid = (p0 + p1).Multiply(0.5)
        for end, pt in ((DB.DatumEnds.End0, p0), (DB.DatumEnds.End1, p1)):
            visible = _try(lambda e=end: g.IsBubbleVisibleInView(e, active_view))
            if not visible:
                continue
            v = pt - mid
            if v.GetLength() < 1.0e-9:
                continue
            v = v.Normalize()
            du = v.DotProduct(up)
            dr = v.DotProduct(right)
            if abs(du) >= abs(dr):
                tally["Top" if du >= 0 else "Bottom"] += 1
            else:
                tally["Right" if dr >= 0 else "Left"] += 1

    best = max(tally, key=lambda k: tally[k])
    if tally[best] == 0:
        return "Top"
    return best


# --------------------------------------------------------------------------
# Dark-purple options popup (WPF)
# --------------------------------------------------------------------------
XAML = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Auto Dimension Grids"
        Width="400" SizeToContent="Height"
        WindowStartupLocation="CenterScreen"
        ResizeMode="NoResize" WindowStyle="None"
        AllowsTransparency="True" Background="Transparent"
        FontFamily="Segoe UI">
  <Window.Resources>
    <Style x:Key="SideButton" TargetType="RadioButton">
      <Setter Property="Margin" Value="3"/>
      <Setter Property="Foreground" Value="#EDE9FE"/>
      <Setter Property="FontSize" Value="13"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="RadioButton">
            <Border x:Name="bd" CornerRadius="7" Background="#2A1B3D"
                    BorderBrush="#4C3A6E" BorderThickness="1" Padding="0,9">
              <TextBlock Text="{TemplateBinding Content}"
                         Foreground="{TemplateBinding Foreground}"
                         HorizontalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsChecked" Value="True">
                <Setter TargetName="bd" Property="Background" Value="#7C3AED"/>
                <Setter TargetName="bd" Property="BorderBrush" Value="#C084FC"/>
              </Trigger>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="bd" Property="BorderBrush" Value="#A855F7"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
    <Style x:Key="PrimaryBtn" TargetType="Button">
      <Setter Property="Foreground" Value="White"/>
      <Setter Property="FontSize" Value="13"/>
      <Setter Property="FontWeight" Value="SemiBold"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="b" CornerRadius="7" Background="#7C3AED" Padding="20,9">
              <ContentPresenter HorizontalAlignment="Center"
                                VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="b" Property="Background" Value="#9333EA"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
    <Style x:Key="GhostBtn" TargetType="Button">
      <Setter Property="Foreground" Value="#C4B5FD"/>
      <Setter Property="FontSize" Value="13"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="b" CornerRadius="7" Background="Transparent"
                    BorderBrush="#4C3A6E" BorderThickness="1" Padding="20,9">
              <ContentPresenter HorizontalAlignment="Center"
                                VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="b" Property="Background" Value="#2A1B3D"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
  </Window.Resources>

  <Border CornerRadius="14" Background="#1E1030" BorderBrush="#3B2A5A"
          BorderThickness="1">
    <StackPanel Margin="22">
      <TextBlock Text="Auto Dimension Grids" Foreground="#FFFFFF"
                 FontSize="18" FontWeight="Bold"/>
      <TextBlock Text="Dimensions are placed on the grid-bubble side, clear of the bubbles."
                 Foreground="#A99BC7" FontSize="11" TextWrapping="Wrap"
                 Margin="0,4,0,16"/>

      <TextBlock Text="DIMENSION STYLE" Foreground="#8B7BB0" FontSize="10"
                 FontWeight="SemiBold" Margin="0,0,0,6"/>
      <Border CornerRadius="7" Background="#2A1B3D" BorderBrush="#4C3A6E"
              BorderThickness="1">
        <ComboBox x:Name="style_combo" Margin="4" BorderThickness="0"
                  Background="Transparent" Foreground="#1E1030" FontSize="13"/>
      </Border>

      <TextBlock Text="PLACE DIMENSIONS ON SIDE" Foreground="#8B7BB0"
                 FontSize="10" FontWeight="SemiBold" Margin="0,18,0,6"/>
      <UniformGrid Columns="4">
        <RadioButton x:Name="side_top" Content="Top" Style="{StaticResource SideButton}"/>
        <RadioButton x:Name="side_bottom" Content="Bottom" Style="{StaticResource SideButton}"/>
        <RadioButton x:Name="side_left" Content="Left" Style="{StaticResource SideButton}"/>
        <RadioButton x:Name="side_right" Content="Right" Style="{StaticResource SideButton}"/>
      </UniformGrid>
      <TextBlock x:Name="side_hint" Foreground="#7C6BA0" FontSize="10"
                 Margin="3,6,0,0"/>

      <StackPanel Orientation="Horizontal" HorizontalAlignment="Right"
                  Margin="0,20,0,0">
        <Button Content="Cancel" Style="{StaticResource GhostBtn}"
                Click="on_cancel" Margin="0,0,8,0"/>
        <Button Content="Done" Style="{StaticResource PrimaryBtn}"
                Click="on_done"/>
      </StackPanel>

      <Border Height="1" Background="#332248" Margin="0,18,0,0"/>
      <TextBlock x:Name="copyright_txt" Foreground="#6E5E92" FontSize="10"
                 HorizontalAlignment="Center" Margin="0,10,0,0"/>
    </StackPanel>
  </Border>
</Window>
"""


class AutoDimOptions(forms.WPFWindow):
    def __init__(self, style_labels, default_side):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        self.selected_style = None
        self.selected_side = None
        self.copyright_txt.Text = COPYRIGHT

        for lbl in style_labels:
            self.style_combo.Items.Add(lbl)
        if style_labels:
            self.style_combo.SelectedIndex = 0

        radio = getattr(self, "side_" + default_side.lower(), None)
        if radio is not None:
            radio.IsChecked = True
        self.side_hint.Text = (
            u"Defaulted to the current bubble side ({0}).".format(default_side))

        # allow dragging the borderless window
        self.MouseLeftButtonDown += self._drag

    def _drag(self, sender, args):
        try:
            self.DragMove()
        except Exception:
            pass

    def _current_side(self):
        for s in ("top", "bottom", "left", "right"):
            rb = getattr(self, "side_" + s)
            if bool(rb.IsChecked):
                return s.capitalize()
        return "Top"

    def on_done(self, sender, args):
        self.selected_style = self.style_combo.SelectedItem
        self.selected_side = self._current_side()
        self.Close()

    def on_cancel(self, sender, args):
        self.selected_style = None
        self.selected_side = None
        self.Close()


# --------------------------------------------------------------------------
# 1. Collect and validate the selected grids
# --------------------------------------------------------------------------
selection = revit.get_selection()
grids = [el for el in selection.elements if isinstance(el, DB.Grid)]

if len(grids) < 2:
    forms.alert("Select at least two grids first, then run the tool.",
                title=TITLE, exitscript=True)

line_grids = [g for g in grids if isinstance(g.Curve, DB.Line)]
if len(line_grids) < 2:
    forms.alert("Need at least two straight (line) grids. Curved grids "
                "are not supported.", title=TITLE, exitscript=True)
grids = line_grids


# --------------------------------------------------------------------------
# 2. Spacing direction (perpendicular to the grids) + sort the grids
# --------------------------------------------------------------------------
grid_dir = grids[0].Curve.Direction.Normalize()

perp = DB.XYZ(-grid_dir.Y, grid_dir.X, 0.0)
if perp.GetLength() < 1.0e-9:
    perp = DB.XYZ(1.0, 0.0, 0.0)
perp = perp.Normalize()


def along_spacing(grid):
    return grid.Curve.GetEndPoint(0).DotProduct(perp)


grids = sorted(grids, key=along_spacing)

tol = 1.0e-3
not_parallel = []
for g in grids[1:]:
    d = g.Curve.Direction.Normalize()
    if abs(abs(d.DotProduct(grid_dir)) - 1.0) > tol:
        not_parallel.append(g)
if not_parallel:
    if not forms.alert(
            "Some selected grids are not parallel to the first grid, so the "
            "dimension may look off. Continue anyway?",
            title=TITLE, yes=True, no=True):
        script.exit()


# --------------------------------------------------------------------------
# 3. Pop up: pick style + side (dark purple UI, defaults to bubble side)
# --------------------------------------------------------------------------
ordered_labels, label_to_type, diagnostics = collect_dimension_styles()

if not ordered_labels:
    forms.alert("No dimension styles could be read from this project.\n\n"
                + diagnostics, title=TITLE, exitscript=True)

default_side = detect_bubble_side(grids, view)

dlg = AutoDimOptions(ordered_labels, default_side)
dlg.ShowDialog()

if not dlg.selected_style or not dlg.selected_side:
    script.exit()

dim_type = label_to_type[dlg.selected_style]
chosen_side = dlg.selected_side


# --------------------------------------------------------------------------
# 4. Build the two dimension lines on the chosen side, clear of the bubbles
# --------------------------------------------------------------------------
first_pt = grids[0].Curve.GetEndPoint(0)
last_pt = grids[-1].Curve.GetEndPoint(0)
span = (last_pt - first_pt).DotProduct(perp)

if abs(span) < 1.0e-6:
    forms.alert("The selected grids appear to overlap (zero spacing). "
                "Nothing to dimension.", title=TITLE, exitscript=True)

# Screen direction for the chosen side, then the along-grid unit toward it.
side_screen = {
    "Top": view.UpDirection,
    "Bottom": view.UpDirection.Negate(),
    "Left": view.RightDirection.Negate(),
    "Right": view.RightDirection,
}[chosen_side]

along = grid_dir if grid_dir.DotProduct(side_screen) >= 0 else grid_dir.Negate()

# Bubble-side end of the grids. The bubbles stick OUT beyond this point, so we
# place the dimension strings just INSIDE it: next to the bubbles, not over them.
projections = []
for g in grids:
    for i in (0, 1):
        projections.append(g.Curve.GetEndPoint(i).DotProduct(along))
grid_end = max(projections)

scale = view.Scale if getattr(view, "Scale", 0) and view.Scale > 0 else 100
first_offset = FIRST_STRING_PAPER_FT * scale
second_offset = first_offset + STRING_GAP_PAPER_FT * scale

anchor = grids[0].Curve.GetEndPoint(0)
anchor_along = anchor.DotProduct(along)


def line_inset(offset):
    """Dimension line positioned `offset` INWARD from the bubble-side grid end,
    so it sits right beside the bubbles without overlapping them."""
    line_pos = grid_end - offset
    shift = line_pos - anchor_along
    a = anchor + along.Multiply(shift)
    b = a + perp.Multiply(span)
    return DB.Line.CreateBound(a, b)


# Match the standard convention seen everywhere: the OVERALL dimension hugs the
# bubble side (nearest the bubbles) and the grid-to-grid string steps one line
# further out. With only two grids there is a single string, placed nearest.
if len(grids) > 2:
    overall_line = line_inset(first_offset)      # nearest the bubbles
    continuous_line = line_inset(second_offset)  # stepped one line further out
else:
    continuous_line = line_inset(first_offset)   # single string, nearest bubbles
    overall_line = None


# --------------------------------------------------------------------------
# 5. Reference arrays
# --------------------------------------------------------------------------
continuous_refs = DB.ReferenceArray()
for g in grids:
    continuous_refs.Append(DB.Reference(g))

overall_refs = DB.ReferenceArray()
overall_refs.Append(DB.Reference(grids[0]))
overall_refs.Append(DB.Reference(grids[-1]))


# --------------------------------------------------------------------------
# 6. Create the dimensions
# --------------------------------------------------------------------------
created = 0
with revit.Transaction(TITLE):
    try:
        create_dimension(view, continuous_line, continuous_refs, dim_type)
        created += 1
    except Exception as err:
        logger.error("Continuous dimension failed: {0}".format(err))

    if len(grids) > 2:
        try:
            create_dimension(view, overall_line, overall_refs, dim_type)
            created += 1
        except Exception as err:
            logger.error("Overall dimension failed: {0}".format(err))

if created == 0:
    forms.alert("Could not create the dimensions. Make sure the grids are "
                "visible in the active view and the view is in the same plane "
                "as the grids.", title=TITLE)
else:
    forms.alert(
        "Done - dimensions placed on the {0} side.\n\n"
        "Continuous string: {1} segment(s) across {2} grids.{3}".format(
            chosen_side,
            len(grids) - 1,
            len(grids),
            "\nOverall dimension: 1 added." if len(grids) > 2 else ""),
        title=TITLE)
