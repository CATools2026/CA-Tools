# -*- coding: utf-8 -*-
"""Export loadable families from the current model to .rfa files on disk.

A popup lists the model's families split into Model categories (left) and
Annotation categories (right). Tick the families to export (with a per-side
Select all), choose a destination folder, and each selected family is saved
out as a .rfa file."""

__title__ = "Export\nFamilies"
__author__ = "Chulan Adasuriya"
__doc__ = ("Exports loadable families from the current model to .rfa files. "
           "Pick the families (Model and Annotation categories shown side by "
           "side), choose a folder, and each one is saved out. V.1.0.0")

import os
import re

import clr
for _asm in ("PresentationCore", "PresentationFramework", "WindowsBase"):
    try:
        clr.AddReference(_asm)
    except Exception:
        pass

from System.Windows import Thickness, FontWeights
from System.Windows.Controls import CheckBox, TextBlock

from pyrevit import revit, DB, forms

doc = revit.doc

GROUP_MODEL = "Model"
GROUP_ANNOTATION = "Annotation"


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def get_name(element):
    try:
        return element.Name
    except Exception:
        try:
            return DB.Element.Name.GetValue(element)
        except Exception:
            return "<unnamed>"


def safe_filename(name):
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", name).strip()
    cleaned = cleaned.rstrip(". ")
    return cleaned or "family"


def collect_families():
    """{ "Model": {category: [(fam_name, fam_id), ...]},
         "Annotation": {category: [...]} }  -- editable loadable families only."""
    data = {GROUP_MODEL: {}, GROUP_ANNOTATION: {}}
    for fam in DB.FilteredElementCollector(doc).OfClass(DB.Family):
        # only families that can actually be opened/saved out
        editable = getattr(fam, "IsEditable", True)
        if not editable:
            continue

        category = fam.FamilyCategory
        if category is None:
            continue

        cat_type = category.CategoryType
        if cat_type == DB.CategoryType.Model:
            group = GROUP_MODEL
        elif cat_type == DB.CategoryType.Annotation:
            group = GROUP_ANNOTATION
        else:
            continue

        data[group].setdefault(category.Name, []).append(
            (get_name(fam), fam.Id))

    return data


def export_families(selected, folder):
    """selected: list of (fam_id, fam_name). Returns (exported, failed_names)."""
    opts = DB.SaveAsOptions()
    opts.OverwriteExistingFile = True

    exported, failed = 0, []
    used_names = {}
    for fam_id, fam_name in selected:
        fam = doc.GetElement(fam_id)
        if fam is None:
            failed.append(fam_name)
            continue

        # avoid clobbering families whose sanitised names collide
        base = safe_filename(fam_name)
        name = base
        n = used_names.get(base, 0)
        if n:
            name = "{}_{}".format(base, n)
        used_names[base] = n + 1

        fam_doc = None
        try:
            fam_doc = doc.EditFamily(fam)
            path = os.path.join(folder, name + ".rfa")
            fam_doc.SaveAs(path, opts)
            exported += 1
        except Exception:
            failed.append(fam_name)
        finally:
            if fam_doc is not None:
                try:
                    fam_doc.Close(False)
                except Exception:
                    pass

    return exported, failed


# ----------------------------------------------------------------------------
# Window
# ----------------------------------------------------------------------------
class ExportFamiliesWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name, data):
        self.data = data
        self.result = None
        self.model_checks = []
        self.annot_checks = []
        self._ready = False
        forms.WPFWindow.__init__(self, xaml_file_name)

        total_fams = sum(len(f) for g in data.values() for f in g.values())
        total_cats = sum(len(g) for g in data.values())
        self.summary_tb.Text = \
            "{} exportable families across {} categories".format(
                total_fams, total_cats)

        self._text_brush = self.FindResource("TextBrush")
        self._accent_brush = self.FindResource("AccentBrush")

        self._populate()
        self._ready = True
        self._update_count()

    # -- build the checkbox lists -------------------------------------------
    def _populate(self):
        for group, panel, store in (
                (GROUP_MODEL, self.model_list, self.model_checks),
                (GROUP_ANNOTATION, self.annot_list, self.annot_checks)):
            cats = self.data.get(group, {})
            if not cats:
                tb = TextBlock()
                tb.Text = "No families in this group."
                tb.Foreground = self._accent_brush
                panel.Children.Add(tb)
                continue

            for cat in sorted(cats.keys(), key=lambda c: c.lower()):
                fams = sorted(cats[cat], key=lambda p: p[0].lower())
                header = TextBlock()
                header.Text = "{}  ({})".format(cat, len(fams))
                header.FontWeight = FontWeights.Bold
                header.Foreground = self._accent_brush
                header.Margin = Thickness(0, 8, 0, 2)
                panel.Children.Add(header)

                for fam_name, fam_id in fams:
                    cb = CheckBox()
                    cb.Content = fam_name
                    cb.Tag = fam_id
                    cb.Foreground = self._text_brush
                    cb.Margin = Thickness(14, 1, 0, 1)
                    cb.Click += self._on_toggle
                    panel.Children.Add(cb)
                    store.append(cb)

    # -- selection count -----------------------------------------------------
    def _all_checks(self):
        return self.model_checks + self.annot_checks

    def _update_count(self):
        n = sum(1 for cb in self._all_checks() if cb.IsChecked)
        self.selcount_tb.Text = "{} families selected".format(n)

    def _on_toggle(self, sender, args):
        self._update_count()

    # -- select all ----------------------------------------------------------
    def _set_all(self, checks, value):
        for cb in checks:
            cb.IsChecked = value
        self._update_count()

    def model_all_click(self, sender, args):
        if not self._ready:
            return
        self._set_all(self.model_checks, bool(self.model_all.IsChecked))

    def annot_all_click(self, sender, args):
        if not self._ready:
            return
        self._set_all(self.annot_checks, bool(self.annot_all.IsChecked))

    # -- folder --------------------------------------------------------------
    def browse_click(self, sender, args):
        folder = forms.pick_folder(title="Choose a folder to export families to")
        if folder:
            self.folder_tb.Text = folder

    # -- buttons -------------------------------------------------------------
    def cancel_click(self, sender, args):
        self.result = None
        self.Close()

    def export_click(self, sender, args):
        folder = (self.folder_tb.Text or "").strip()
        if not folder or not os.path.isdir(folder):
            forms.alert("Please choose a valid destination folder.",
                        title="Export Families")
            return

        selected = [(cb.Tag, cb.Content) for cb in self._all_checks()
                    if cb.IsChecked]
        if not selected:
            forms.alert("Please select at least one family to export.",
                        title="Export Families")
            return

        self.result = (selected, folder)
        self.Close()


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------
def main():
    data = collect_families()
    if not (data[GROUP_MODEL] or data[GROUP_ANNOTATION]):
        forms.alert("No exportable families were found in this model.",
                    title="Export Families")
        return

    window = ExportFamiliesWindow("ExportFamilies.xaml", data)
    window.ShowDialog()

    if not window.result:
        return

    selected, folder = window.result
    exported, failed = export_families(selected, folder)

    msg = "Exported {} of {} family file(s) to:\n{}".format(
        exported, len(selected), folder)
    if failed:
        shown = failed[:15]
        names = "\n".join("  - " + str(f) for f in shown)
        if len(failed) > len(shown):
            names += "\n  ... and {} more".format(len(failed) - len(shown))
        msg += "\n\nCould not export:\n" + names
    forms.alert(msg, title="Export Families")


if __name__ == "__main__":
    main()
