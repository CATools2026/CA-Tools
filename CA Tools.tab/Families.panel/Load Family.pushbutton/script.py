# -*- coding: utf-8 -*-
"""Load one or more family (.rfa) files from the PC into the current model."""

__title__ = "Load\nFamily"
__author__ = "Chulan Adasuriya"
__doc__ = ("Pick one or more family (.rfa) files from your PC and load them "
           "into the current model. Existing families are overwritten and "
           "their parameter values kept. V.1.0.0")

from pyrevit import revit, DB, forms

doc = revit.doc


# ----------------------------------------------------------------------------
# Family load options (overwrite existing, keep parameter values)
# ----------------------------------------------------------------------------
class FamilyLoadOptions(DB.IFamilyLoadOptions):
    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        overwriteParameterValues.Value = True
        return True

    def OnSharedFamilyFound(self, sharedFamily, familyInUse,
                            source, overwriteParameterValues):
        source.Value = DB.FamilySource.Family
        overwriteParameterValues.Value = True
        return True


def main():
    paths = forms.pick_file(file_ext="rfa", multi_file=True,
                            title="Select family files to load")
    if not paths:
        return
    if not isinstance(paths, list):
        paths = [paths]

    opts = FamilyLoadOptions()
    loaded, failed = 0, []
    with revit.Transaction("Load Families"):
        for path in paths:
            try:
                if doc.LoadFamily(path, opts):
                    loaded += 1
                else:
                    failed.append(path)
            except Exception:
                failed.append(path)

    msg = "Loaded {} of {} family file(s).".format(loaded, len(paths))
    if failed:
        names = "\n".join("  - " + str(p).split("\\")[-1] for p in failed)
        msg += "\n\nNot loaded (already present or invalid):\n" + names
    forms.alert(msg, title="Load Family")


if __name__ == "__main__":
    main()
