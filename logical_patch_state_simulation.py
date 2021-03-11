import random

from patches import *

import qiskit.aqua.operators as qk
import qiskit.quantum_info as qkinfo
import qiskit.exceptions as qkexcept
import numpy.linalg

from typing import *
import math
import cmath
import uuid


class ConvertersToQiskit:
    @staticmethod
    def pauli_op(op: PauliOperator) -> Optional[qiskit.aqua.operators.PrimitiveOp]:
        known_map: Dict[PauliOperator, qiskit.aqua.operators.PrimitiveOp] = {
            PauliOperator.I: qiskit.aqua.operators.I,
            PauliOperator.X: qiskit.aqua.operators.X,
            PauliOperator.Y: qiskit.aqua.operators.Y,
            PauliOperator.Z: qiskit.aqua.operators.Z
        }
        return known_map[op]


    @staticmethod
    def symbolic_state(s:SymbolicState)->qk.StateFn:
        if s == InitializeableState.Zero:
            return qk.Zero
        elif s == InitializeableState.Plus:
            return qk.One
        elif s == InitializeableState.YEigenState:
            return (qk.Zero - 1j*qk.One)/math.sqrt(2)
        elif s == InitializeableState.Magic:
            return (qk.Zero + cmath.exp(1j*math.pi/4)*qk.One)/math.sqrt(2)
        else:
            raise Exception("State cannot be converted to qiskit: "+repr(s))


def circuit_add_op_to_qubit(circ : qk.CircuitOp, op: qk.PrimitiveOp, idx: int) -> qk.CircuitOp:
    """"Take a local operator (applied to a single qubit) and apply it to the given circuit."""
    new_op = op
    if idx > 0:
        new_op = (qk.I ^ idx ) ^ new_op
    if circ.num_qubits-idx-1 > 0:
        new_op = new_op ^ (qk.I ^ (circ.num_qubits - idx - 1))
    return new_op @ circ

class ProjectiveMeasurement:

        class BinaryMeasurementOutcome:
            def __init__(self, resulting_state: qk.DictStateFn, corresponding_eigenvalue: int):
                assert corresponding_eigenvalue in {-1,1}
                self.resulting_state = resulting_state
                self.corresponding_eigenvalue = corresponding_eigenvalue

        @staticmethod
        def borns_rule(projector: qk.PrimitiveOp, state: qk.OperatorBase) -> float:
            # https://qiskit.org/documentation/tutorials/operators/01_operator_flow.html#listop
            compute_states = lambda s: s.to_matrix_op().eval()

            return qk.StateFn(projector).adjoint().eval(compute_states(state))

        @staticmethod
        def compute_outcome_state(projector: qk.PrimitiveOp, state: qk.OperatorBase) -> Tuple[qk.OperatorBase, float]:
            prob = ProjectiveMeasurement.borns_rule(projector, state)
            assert prob.imag < 10 ** (-8)
            prob = prob.real
            state = (projector @ state) / math.sqrt(prob)
            return state, prob

        @staticmethod
        def get_projectors_from_pauli_observable(pauli_observable: qk.OperatorBase) -> Tuple[
            qk.PrimitiveOp, qk.PrimitiveOp]:
            eye = qk.I ^ pauli_observable.num_qubits
            return (eye + pauli_observable) / 2, (eye - pauli_observable) / 2


        @staticmethod
        def pauli_product_measurement_distribution(pauli_observable: qk.OperatorBase, state: qk.OperatorBase) \
                -> Iterable[Tuple[BinaryMeasurementOutcome, float]] :
            p_plus, p_minus = ProjectiveMeasurement.get_projectors_from_pauli_observable(pauli_observable)

            out = []
            for proj, eigenv in [[p_plus, +1], [p_minus, -1]]:
                out_state, prob = ProjectiveMeasurement.compute_outcome_state(proj, state)
                numerical_out_state = out_state.eval()
                if not isinstance(numerical_out_state,qk.DictStateFn):
                    raise Exception("Composed ops do not eval to single state, but to "+str(numerical_out_state))
                out.append((ProjectiveMeasurement.BinaryMeasurementOutcome(numerical_out_state, eigenv), prob))
            return out


T = TypeVar('T')
def proportional_choice(assoc_data_prob : List[Tuple[T, float]]) -> T:
    return random.choices([val for val, prob in assoc_data_prob],
                          weights=[prob for val, prob in assoc_data_prob],
                          k=1)[0]

class PatchToQubitMapper:
    def __init__(self, logical_computation: LogicalLatticeComputation):
        self.patch_location_to_logical_idx: Dict[uuid.UUID, int] = dict()
        for p in PatchToQubitMapper._get_all_operating_patches(logical_computation.ops):
            if self.patch_location_to_logical_idx.get(p) is None:
                self.patch_location_to_logical_idx[p] = self.max_num_patches()

    def get_idx(self, patch: uuid.UUID) -> int:
        return self.patch_location_to_logical_idx[patch]

    def max_num_patches(self) ->int:
        return len(self.patch_location_to_logical_idx)

    def get_uuid(self, target_idx: int) -> uuid.UUID:
        for quiid, idx in self.patch_location_to_logical_idx.items():
            if idx==target_idx:
                return quiid

    def enumerate_patches_by_index(self) -> Iterable[Tuple[int, uuid.UUID]]:
        for idx in range(self.max_num_patches()):
            yield (idx, self.get_uuid(idx))



    @staticmethod
    def _get_all_operating_patches(logical_ops: List[LogicalLatticeOperation]) -> List[uuid.UUID]:
        patch_set = set()
        for op in logical_ops:
            patch_set = patch_set.union(op.get_operating_patches())
        return list(patch_set)



def tensor_list(l):
    t = l[0]
    for s in l[1:]:
        t = t ^ s
    return t


class PatchSimulator:
    def __init__(self, logical_computation : LogicalLatticeComputation):
        self.logical_computation = logical_computation
        self.mapper = PatchToQubitMapper(logical_computation)
        self.logical_state : qk.DictStateFn = self._make_initial_logical_state()


    def _make_initial_logical_state(self) -> qk.DictStateFn:
        """Every patch, when initialized, is considered a new logical qubit.
        So all patch initializations and magic state requests are handled ahead of time"""
        initial_ancilla_states:Dict[uuid.UUID,SymbolicState] = dict()

        def add_initial_ancilla_state(quuid, symbolic_state):
            if initial_ancilla_states.get(quuid) is not None:
                raise Exception("Initializing patch "+str(quuid)+" twice")
            initial_ancilla_states[quuid] = symbolic_state

        def get_init_state(quuid:uuid.UUID) -> qk.StateFn:
            return ConvertersToQiskit.symbolic_state(initial_ancilla_states.get(quuid, InitializeableState.Zero))

        for op in self.logical_computation.ops:
                if isinstance(op, AncillaQubitPatchInitialization):
                    add_initial_ancilla_state(op.qubit_uuid,op.qubit_state)
                elif isinstance(op, MagicStateRequest):
                    add_initial_ancilla_state(op.qubit_uuid,InitializeableState.Magic)

        all_init_states = [get_init_state(quuid) for idx, quuid in self.mapper.enumerate_patches_by_index()]
        return tensor_list(all_init_states)

    def apply_logical_operation(self, logical_op : LogicalLatticeOperation):
        """Update the logical state"""

        if not logical_op.does_evaluate():
            raise Exception("apply_logical_operation called with non evaluating operation :"+repr(logical_op))

        if isinstance(logical_op, SinglePatchMeasurement):
            measure_idx = self.mapper.get_idx(logical_op.qubit_uuid)
            local_observable = ConvertersToQiskit.pauli_op(logical_op.op)
            global_observable = (qk.I ^ measure_idx) ^ local_observable ^ (
                        qk.I ^ (self.mapper.max_num_patches() - measure_idx - 1))
            distribution = ProjectiveMeasurement.pauli_product_measurement_distribution(global_observable,
                                                                                            self.logical_state)
            outcome = proportional_choice(distribution)
            self.logical_state = outcome.resulting_state
            logical_op.set_outcome(outcome.corresponding_eigenvalue)

        elif isinstance(logical_op, PauliOperator):
            for patch, op in logical_op.patch_pauli_operator_map.items():
                symbolic_state = circuit_add_op_to_qubit(self.logical_state, ConvertersToQiskit.pauli_op(op),
                                                        self.mapper.get_idx(patch))
                self.logical_state = symbolic_state.eval()  # Convert to DictStateFn

        elif isinstance(logical_op, MultiBodyMeasurement):
            pauli_op_list : List[qk.PrimitiveOp] = []

            for j,quuid in self.mapper.enumerate_patches_by_index():
                pauli_op_list.append(logical_op.patch_pauli_operator_map.get(quuid, PauliOperator.I))
            global_observable = tensor_list(list(map(ConvertersToQiskit.pauli_op,pauli_op_list)))
            distribution = list(ProjectiveMeasurement.pauli_product_measurement_distribution(global_observable,
                                                                                         self.logical_state))
            outcome = proportional_choice(distribution)
            self.logical_state = outcome.resulting_state
            logical_op.set_outcome(outcome.corresponding_eigenvalue)




class StateSeparator:

    @staticmethod
    def trace_dict_state(state: qk.DictStateFn, trace_over: List[int]) -> qk.DictStateFn:
        """Assumes state is separable as a DictStateFn can only represent pure states"""
        input_statevector = qkinfo.Statevector(state.to_matrix())
        traced_statevector = qkinfo.partial_trace(input_statevector, trace_over).to_statevector()
        return qk.DictStateFn(traced_statevector.to_dict())

    @staticmethod
    def trace_to_density_op(state: qk.DictStateFn, trace_over: List[int]) -> qkinfo.DensityMatrix:
        input_statevector = qkinfo.Statevector(state.to_matrix())
        return qkinfo.partial_trace(input_statevector, trace_over)

    @staticmethod
    def separate(qnum:int, dict_state : qk.DictStateFn) -> Optional[qk.DictStateFn]:
        """The closer to zero the closer to being separable"""

        # Qubits are indexed left to right in the dict state so we need to swap
        remaing_qubits = list(range(dict_state.num_qubits))
        remaing_qubits.remove(qnum)

        selected_qubit_maybe_mixed_state = StateSeparator.trace_to_density_op(dict_state, remaing_qubits)

        try:
            selected_qubit_pure_state = selected_qubit_maybe_mixed_state.to_statevector(rtol=10**(-10))
            return qk.DictStateFn(selected_qubit_pure_state.to_dict())

        except qkexcept.QiskitError as e:
            if e.message != 'Density matrix is not a pure state':  raise e
            return None


    @staticmethod
    def get_separable_qubits(dict_state : qk.DictStateFn) -> Dict[int,qk.DictStateFn]:
        out = {}
        for i in range(dict_state.num_qubits):
            maybe_state = StateSeparator.separate(i, dict_state)
            if maybe_state is not None:
                out[i] = maybe_state
        return out

