
import patches
import lattice_view
from lattice_surgery_computation_composer import LatticeSurgeryComputationComposer,PatchInitializer


# Example
# Construct the device layout
tac = LatticeSurgeryComputationComposer(
    PatchInitializer.simpleLayout(5))


# tac.measureMultiPatch({
#     (0,0):patches.PauliMatrix.X,
#     (4,0):patches.PauliMatrix.Z,
# })


tac.measureMultiPatch({
    (0,0):patches.PauliMatrix.Y,
    (4,0):patches.PauliMatrix.Z,
})


lattice_view.to_file(tac.getSlices(),"index.html")