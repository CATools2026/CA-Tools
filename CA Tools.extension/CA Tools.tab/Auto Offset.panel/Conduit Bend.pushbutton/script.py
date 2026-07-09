# GreaterBIM - Conduit Bend
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'lib'))
from pyrevit import revit
import bend_core
bend_core.run_bend(revit.uidoc, "Conduit")
