# -*- coding: utf-8 -*-
"""Browse loadable families in the model, split into Model and Annotation
categories, preview the selected family, and place the selection using Revit's
native placement tool."""

__title__ = "Family\nBrowser"
__author__ = "Chulan Adasuriya"
__doc__ = ("Lists all Model and Annotation families in the current model "
           "(separated by type), previews the selected family, and lets you "
           "pick a category > family and place it. V.1.0.0")

import clr
for _asm in ("System.Drawing", "PresentationCore",
             "PresentationFramework", "WindowsBase"):
    try:
        clr.AddReference(_asm)
    except Exception:
        pass

from System.Drawing import Size as DrawingSize
from System.Drawing.Imaging import ImageFormat
from System.IO import MemoryStream, SeekOrigin
from System.Windows import Visibility
from System.Windows.Media.Imaging import BitmapImage, BitmapCacheOption

from pyrevit import revit, DB, forms

doc = revit.doc
uidoc = revit.uidoc

GROUP_MODEL = "Model"
GROUP_ANNOTATION = "Annotation"


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def get_name(element):
    """Safe name getter that avoids the ambiguous Element.Name overload."""
    try:
        return element.Name
    except Exception:
        try:
            return DB.Element.Name.GetValue(element)
        except Exception:
            return "<unnamed>"


def collect_families():
    """Return:

        { "Model":      { category: { family: [symbol_id, ...] } },
          "Annotation": { category: { family: [symbol_id, ...] } } }

    Symbol ids are sorted by type name (first id = first type, used for
    placement and preview). Only Model and Annotation loadable families with
    at least one type are included.
    """
    data = {GROUP_MODEL: {}, GROUP_ANNOTATION: {}}
    collector = DB.FilteredElementCollector(doc).OfClass(DB.Family)

    for fam in collector:
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

        symbol_ids = list(fam.GetFamilySymbolIds())
        if not symbol_ids:
            continue

        pairs = []
        for sid in symbol_ids:
            symbol = doc.GetElement(sid)
            if symbol is None:
                continue
            pairs.append((get_name(symbol), sid))
        if not pairs:
            continue
        pairs.sort(key=lambda p: p[0])

        cat_name = category.Name
        fam_name = get_name(fam)
        data[group].setdefault(cat_name, {})[fam_name] = [sid for _, sid in pairs]

    return data


def get_preview_source(symbol, width=320, height=220):
    """Return a WPF BitmapSource of the family type's stored preview image,
    or None if no preview is available."""
    if symbol is None:
        return None
    try:
        bmp = symbol.GetPreviewImage(DrawingSize(width, height))
    except Exception:
        bmp = None
    if bmp is None:
        return None
    try:
        stream = MemoryStream()
        bmp.Save(stream, ImageFormat.Png)
        stream.Seek(0, SeekOrigin.Begin)
        src = BitmapImage()
        src.BeginInit()
        src.CacheOption = BitmapCacheOption.OnLoad
        src.StreamSource = stream
        src.EndInit()
        src.Freeze()
        return src
    except Exception:
        return None


# ----------------------------------------------------------------------------
# WPF picker window
# ----------------------------------------------------------------------------
class FamilyBrowserWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name, data):
        self.data = data
        self._ready = False
        forms.WPFWindow.__init__(self, xaml_file_name)

        self._update_summary()

        self._ready = True
        if data.get(GROUP_MODEL):
            self.model_rb.IsChecked = True
        else:
            self.annot_rb.IsChecked = True

    # -- summary / reload ----------------------------------------------------
    def _update_summary(self):
        data = self.data
        total_fams = sum(len(f) for g in data.values() for f in g.values())
        total_types = sum(len(t) for g in data.values()
                          for f in g.values() for t in f.values())
        total_cats = sum(len(g) for g in data.values())
        self.summary_tb.Text = "{} categories  -  {} families  -  {} types".format(
            total_cats, total_fams, total_types
        )

    # -- state ---------------------------------------------------------------
    def _active_group(self):
        return GROUP_MODEL if self.model_rb.IsChecked else GROUP_ANNOTATION

    def _selected_symbol(self):
        category = self.category_cb.SelectedItem
        family = self.family_cb.SelectedItem
        if not (category and family):
            return None
        ids = self.data[self._active_group()].get(category, {}).get(family)
        if not ids:
            return None
        return doc.GetElement(ids[0])   # first type of the family

    # -- preview -------------------------------------------------------------
    def _show_preview(self, src):
        if src is None:
            self.preview_img.Source = None
            self.preview_hint.Visibility = Visibility.Visible
        else:
            self.preview_img.Source = src
            self.preview_hint.Visibility = Visibility.Collapsed

    def _update_preview(self):
        self._show_preview(get_preview_source(self._selected_symbol()))

    # -- Model / Annotation toggle ------------------------------------------
    def category_type_changed(self, sender, args):
        if not getattr(self, "_ready", False):
            return
        cats = sorted(self.data.get(self._active_group(), {}).keys())
        self.category_cb.ItemsSource = cats
        if self.category_cb.Items.Count:
            self.category_cb.SelectedIndex = 0
        else:
            self.family_cb.ItemsSource = None
            self._show_preview(None)

    # -- category -> family --------------------------------------------------
    def category_changed(self, sender, args):
        category = self.category_cb.SelectedItem
        if not category:
            self.family_cb.ItemsSource = None
            self._show_preview(None)
            return
        self.family_cb.ItemsSource = sorted(
            self.data[self._active_group()][category].keys())
        if self.family_cb.Items.Count:
            self.family_cb.SelectedIndex = 0

    # -- family selected -> refresh preview ----------------------------------
    def family_changed(self, sender, args):
        self._update_preview()

    # -- buttons -------------------------------------------------------------
    def place_click(self, sender, args):
        symbol = self._selected_symbol()
        if symbol is None:
            forms.alert("Please select a category and family first.",
                        title="Family Browser")
            return

        if not symbol.IsActive:
            with revit.Transaction("Activate Family Type"):
                symbol.Activate()
                doc.Regenerate()

        self.Close()
        uidoc.PostRequestForElementTypePlacement(symbol)


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------
def main():
    data = collect_families()
    if not (data[GROUP_MODEL] or data[GROUP_ANNOTATION]):
        forms.alert("No Model or Annotation families were found in this model.",
                    title="Family Browser")
        return
    FamilyBrowserWindow("FamilyBrowser.xaml", data).ShowDialog()


if __name__ == "__main__":
    main()
