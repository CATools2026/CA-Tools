# -*- coding: utf-8 -*-
"""Batch show / hide grid bubbles on one side.

Copyright (c) 2026 Chulan Adasuriya

Select several grids, run the tool, choose a side (Top / Bottom / Left /
Right) and whether to show or hide the bubble there. The change is applied to
every selected grid at once, in the active view only.

The side is judged against what you see on screen (the view's up / right
directions), so it works in plans, elevations and sections, and even for
diagonal grids: for each grid the end that points most toward the chosen side
is the one that gets toggled.

Tip: after moving bubbles to a new side, run *Auto Dim Grids* again to snap the
dimensions across to the same side.
"""

from pyrevit import revit, DB, forms, script

doc = revit.doc
view = doc.ActiveView
logger = script.get_logger()

TITLE = "Grid Bubbles"
COPYRIGHT = u"Copyright \u00A9 2026 Chulan Adasuriya"


# --------------------------------------------------------------------------
# 1. Collect selected grids
# --------------------------------------------------------------------------
selection = revit.get_selection()
grids = [el for el in selection.elements if isinstance(el, DB.Grid)]

if not grids:
    forms.alert("Select one or more grids first, then run the tool.",
                title=TITLE, exitscript=True)


# --------------------------------------------------------------------------
# 2. Dark-purple popup: side + show/hide
# --------------------------------------------------------------------------
XAML = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Grid Bubbles"
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
      <TextBlock Text="Grid Bubbles" Foreground="#FFFFFF"
                 FontSize="18" FontWeight="Bold"/>
      <TextBlock Text="Show or hide grid bubbles on one side for every selected grid."
                 Foreground="#A99BC7" FontSize="11" TextWrapping="Wrap"
                 Margin="0,4,0,16"/>

      <TextBlock Text="SIDE" Foreground="#8B7BB0" FontSize="10"
                 FontWeight="SemiBold" Margin="0,0,0,6"/>
      <UniformGrid Columns="4">
        <RadioButton x:Name="side_top" Content="Top" Style="{StaticResource SideButton}"/>
        <RadioButton x:Name="side_bottom" Content="Bottom" Style="{StaticResource SideButton}"/>
        <RadioButton x:Name="side_left" Content="Left" Style="{StaticResource SideButton}"/>
        <RadioButton x:Name="side_right" Content="Right" Style="{StaticResource SideButton}"/>
      </UniformGrid>

      <TextBlock Text="ACTION" Foreground="#8B7BB0" FontSize="10"
                 FontWeight="SemiBold" Margin="0,18,0,6"/>
      <UniformGrid Columns="2">
        <RadioButton x:Name="act_show" Content="Show bubble"
                     Style="{StaticResource SideButton}"/>
        <RadioButton x:Name="act_hide" Content="Hide bubble"
                     Style="{StaticResource SideButton}"/>
      </UniformGrid>

      <StackPanel Orientation="Horizontal" HorizontalAlignment="Right"
                  Margin="0,20,0,0">
        <Button Content="Cancel" Style="{StaticResource GhostBtn}"
                Click="on_cancel" Margin="0,0,8,0"/>
        <Button Content="Apply" Style="{StaticResource PrimaryBtn}"
                Click="on_done"/>
      </StackPanel>

      <Border Height="1" Background="#332248" Margin="0,18,0,0"/>
      <TextBlock x:Name="copyright_txt" Foreground="#6E5E92" FontSize="10"
                 HorizontalAlignment="Center" Margin="0,10,0,0"/>
    </StackPanel>
  </Border>
</Window>
"""


class BubbleOptions(forms.WPFWindow):
    def __init__(self):
        forms.WPFWindow.__init__(self, XAML, literal_string=True)
        self.selected_side = None
        self.show_bubble = None
        self.copyright_txt.Text = COPYRIGHT
        self.side_top.IsChecked = True
        self.act_show.IsChecked = True
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
        self.selected_side = self._current_side()
        self.show_bubble = bool(self.act_show.IsChecked)
        self.Close()

    def on_cancel(self, sender, args):
        self.selected_side = None
        self.show_bubble = None
        self.Close()


dlg = BubbleOptions()
dlg.ShowDialog()

selected_side = dlg.selected_side
if not selected_side:
    script.exit()
show_bubble = bool(dlg.show_bubble)


# --------------------------------------------------------------------------
# 3. Build the screen-space direction for the chosen side
# --------------------------------------------------------------------------
up = view.UpDirection
right = view.RightDirection

side_vectors = {
    "Top": up,
    "Bottom": up.Negate(),
    "Left": right.Negate(),
    "Right": right,
}
direction = side_vectors[selected_side]

ALIGN_TOL = 0.5


def runs_along_side(grid):
    gdir = grid.Curve.Direction.Normalize()
    return abs(gdir.DotProduct(direction)) >= ALIGN_TOL


def end_toward_side(grid):
    crv = grid.Curve
    p0 = crv.GetEndPoint(0)
    p1 = crv.GetEndPoint(1)
    if p1.DotProduct(direction) >= p0.DotProduct(direction):
        return DB.DatumEnds.End1
    return DB.DatumEnds.End0


# --------------------------------------------------------------------------
# 4. Apply to every selected grid that has a bubble on the chosen side
# --------------------------------------------------------------------------
changed = 0
skipped = 0
failed = 0
action = "Show" if show_bubble else "Hide"

with revit.Transaction("{0} grid bubbles ({1})".format(action, selected_side)):
    for grid in grids:
        try:
            if not runs_along_side(grid):
                skipped += 1
                continue
            end = end_toward_side(grid)
            if show_bubble:
                grid.ShowBubbleInView(end, view)
            else:
                grid.HideBubbleInView(end, view)
            changed += 1
        except Exception as err:
            failed += 1
            logger.debug("Grid {0} failed: {1}".format(grid.Id, err))

msg = "{0} bubble on the {1} side for {2} grid(s).".format(
    action, selected_side, changed)
if skipped:
    msg += ("\n{0} grid(s) skipped (they don't run toward the {1} side)."
            .format(skipped, selected_side))
if failed:
    msg += "\n{0} grid(s) could not be changed in this view.".format(failed)

forms.alert(msg, title=TITLE)
