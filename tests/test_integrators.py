import pytest
import numpy as np
import mici.integrators as integrators
import mici.systems as systems
import mici.matrices as matrices
import mici.solvers as solvers
from mici.states import ChainState
from mici.errors import IntegratorError

SEED = 3046987125
N_STEPS = {1, 5, 20}
N_STEPS_HAMILTONIAN = {200}
N_STATE = 5
N_METRIC = 5


@pytest.fixture
def rng():
    return np.random.default_rng(SEED)


@pytest.fixture(params={1, 2, 5})
def size(request):
    return request.param


@pytest.fixture(params={2, 5})
def size_more_than_one(request):
    return request.param


@pytest.fixture
def metric_list(rng, size):
    eigval = np.exp(0.1 * rng.standard_normal(size))
    eigvec = np.linalg.qr(rng.standard_normal((size, size)))[0]
    return [
        matrices.IdentityMatrix(),
        matrices.IdentityMatrix(size),
        matrices.PositiveDiagonalMatrix(eigval),
        matrices.DensePositiveDefiniteMatrix((eigvec * eigval) @ eigvec.T),
        matrices.EigendecomposedPositiveDefiniteMatrix(eigvec, eigval),
    ]


@pytest.fixture(params=range(N_METRIC))
def metric(metric_list, request):
    return metric_list[request.param]


@pytest.fixture
def init_state_list(rng, size):
    return [
        ChainState(pos=q, mom=p, dir=1)
        for q, p in rng.standard_normal((N_STATE, 2, size))
    ]


@pytest.fixture(params=range(N_STATE))
def init_state(init_state_list, request):
    return init_state_list[request.param]


def _integrate_with_reversal(integrator, init_state, n_step):
    state = init_state
    try:
        for s in range(n_step):
            state = integrator.step(state)
        state.dir *= -1
    except IntegratorError:
        state = init_state
    return state


class IntegratorTests:

    h_diff_tol = 5e-3

    @pytest.mark.parametrize("n_step", N_STEPS)
    def test_reversibility(self, integrator, init_state, n_step):
        state = _integrate_with_reversal(integrator, init_state, n_step)
        state = _integrate_with_reversal(integrator, state, n_step)
        assert np.allclose(state.pos, init_state.pos), (
            f"integrator not returning on reversal to initial position.\n"
            f"initial position = {init_state.pos},\n"
            f"final position   = {state.pos}."
        )
        assert np.allclose(state.mom, init_state.mom), (
            f"integrator not returning on reversal to initial momentum.\n"
            f"initial momentum = {init_state.mom},\n"
            f"final momentum   = {state.mom}."
        )
        assert (
            state.dir == init_state.dir
        ), "integrator not returning on reversal to initial direction."

    @pytest.mark.parametrize("n_step", N_STEPS_HAMILTONIAN)
    def test_approx_hamiltonian_conservation(self, integrator, init_state, n_step):
        h_vals = [integrator.system.h(init_state)]
        state = init_state
        try:
            for s in range(n_step):
                state = integrator.step(state)
                h_vals.append(integrator.system.h(state))
        except IntegratorError:
            return
        diff_h = np.mean(h_vals[: n_step // 2]) - np.mean(h_vals[n_step // 2 :])
        assert abs(diff_h) < self.h_diff_tol, (
            f"difference in average Hamiltonian over first and second halves "
            f"of trajectory ({diff_h}) is greater in magnitude than tolerance "
            f"({self.h_diff_tol})."
        )

    def test_state_mutation(self, integrator, init_state):
        init_pos = init_state.pos.copy()
        init_mom = init_state.mom.copy()
        init_dir = init_state.dir
        state = integrator.step(init_state)
        assert init_state is not state, "integrator not returning new state object"
        assert np.all(
            init_state.pos == init_pos
        ), "integrator modifiying passed state.pos attribute"
        assert np.all(
            init_state.mom == init_mom
        ), "integrator modifiying passed state.mom attribute"
        assert (
            init_state.dir == init_dir
        ), "integrator modifiying passed state.dir attribute"


class LinearSystemIntegratorTests(IntegratorTests):
    @pytest.mark.parametrize("n_step", N_STEPS)
    def test_volume_preservation(self, integrator, init_state_list, n_step):
        init_zs, final_zs = [], []
        for state in init_state_list:
            init_zs.append(np.concatenate((state.pos, state.mom)))
            for s in range(n_step):
                state = integrator.step(state)
            final_zs.append(np.concatenate((state.pos, state.mom)))
        init_zs = np.column_stack(init_zs)
        final_zs = np.column_stack(final_zs)
        assert np.allclose(
            np.linalg.det(init_zs @ init_zs.T), np.linalg.det(final_zs @ final_zs.T)
        ), "state space volume spanned by initial and final state differs"


class ConstrainedSystemIntegratorTests(IntegratorTests):
    @pytest.fixture
    def metric_list(self, rng, size_more_than_one):
        size = size_more_than_one
        eigval = np.exp(0.1 * rng.standard_normal(size))
        eigvec = np.linalg.qr(rng.standard_normal((size, size)))[0]
        return [
            matrices.IdentityMatrix(),
            matrices.IdentityMatrix(size),
            matrices.PositiveDiagonalMatrix(eigval),
            matrices.DensePositiveDefiniteMatrix((eigvec * eigval) @ eigvec.T),
            matrices.EigendecomposedPositiveDefiniteMatrix(eigvec, eigval),
        ]

    @pytest.mark.parametrize("n_step", N_STEPS)
    def test_position_constraint(self, integrator, init_state, n_step):
        init_error = np.max(np.abs(integrator.system.constr(init_state)))
        assert init_error < 1e-8, (
            "Position constraint not satisfied at initial state "
            f"(|c| = {init_error:.1e})"
        )
        final_state = _integrate_with_reversal(integrator, init_state, n_step)
        final_error = np.max(np.abs(integrator.system.constr(final_state)))
        assert final_error < 1e-8, (
            "Position constraint not satisfied at final state "
            f"(|c| = {final_error:.1e})"
        )

    @pytest.mark.parametrize("n_step", N_STEPS)
    def test_momentum_constraint(self, integrator, init_state, n_step):
        init_error = np.max(
            np.abs(
                integrator.system.jacob_constr(init_state)
                @ integrator.system.dh_dmom(init_state)
            )
        )
        assert init_error < 1e-8, (
            "Momentum constraint not satisfied at initial state "
            f"(|dc/dq @ dq/dt| = {init_error:.1e})"
        )
        final_state = _integrate_with_reversal(integrator, init_state, n_step)
        final_error = np.max(
            np.abs(
                integrator.system.jacob_constr(final_state)
                @ integrator.system.dh_dmom(final_state)
            )
        )
        assert final_error < 1e-8, (
            "Momentum constraint not satisfied at final state "
            f"(|dc/dq @ dq/dt| = {final_error:.1e})"
        )


class ConstrainedLinearSystemIntegratorTests(
    ConstrainedSystemIntegratorTests, LinearSystemIntegratorTests
):
    @pytest.fixture
    def init_state_list(self, rng, size_more_than_one, metric):
        return [
            ChainState(
                pos=np.concatenate([np.array([0.0]), q]),
                mom=metric @ np.concatenate([np.array([0.0]), p]),
                dir=1,
            )
            for q, p in rng.standard_normal((N_STATE, 2, size_more_than_one - 1))
        ]


class ConstrainedNonLinearSystemIntegratorTests(ConstrainedSystemIntegratorTests):
    @pytest.fixture
    def init_state_list(self, rng, size_more_than_one, system):
        init_state_list = [
            ChainState(
                pos=np.concatenate([np.array([np.cos(theta), np.sin(theta)]), q]),
                mom=None,
                dir=1,
            )
            for theta, q in zip(
                rng.uniform(size=N_STATE) * 2 * np.pi,
                rng.standard_normal((N_STATE, size_more_than_one - 2)),
            )
        ]
        for state in init_state_list:
            state.mom = system.sample_momentum(state, rng)
        return init_state_list


class TestLeapfrogIntegratorLinearSystem(LinearSystemIntegratorTests):

    h_diff_tol = 2e-3

    @pytest.fixture
    def integrator(self, metric):
        system = systems.EuclideanMetricSystem(
            neg_log_dens=lambda q: 0.5 * np.sum(q ** 2),
            metric=metric,
            grad_neg_log_dens=lambda q: q,
        )
        return integrators.LeapfrogIntegrator(system, 0.25)


class TestLeapfrogIntegratorNonLinearSystem(IntegratorTests):

    h_diff_tol = 1e-3

    @pytest.fixture
    def integrator(self, metric):
        system = systems.EuclideanMetricSystem(
            neg_log_dens=lambda q: 0.25 * np.sum(q ** 4),
            metric=metric,
            grad_neg_log_dens=lambda q: q ** 3,
        )
        return integrators.LeapfrogIntegrator(system, 0.05)


class TestLeapfrogIntegratorGaussianLinearSystem(LinearSystemIntegratorTests):

    h_diff_tol = 1e-10

    @pytest.fixture
    def integrator(self, metric):
        system = systems.GaussianEuclideanMetricSystem(
            neg_log_dens=lambda q: 0, metric=metric, grad_neg_log_dens=lambda q: 0 * q
        )
        return integrators.LeapfrogIntegrator(system, 0.5)


class TestLeapfrogIntegratorGaussianNonLinearSystem(IntegratorTests):

    h_diff_tol = 2e-3

    @pytest.fixture
    def integrator(self, metric):
        system = systems.GaussianEuclideanMetricSystem(
            neg_log_dens=lambda q: 0.125 * np.sum(q ** 4),
            metric=metric,
            grad_neg_log_dens=lambda q: 0.5 * q ** 3,
        )
        return integrators.LeapfrogIntegrator(system, 0.1)


class TestImplicitLeapfrogIntegratorLinearSystem(LinearSystemIntegratorTests):

    h_diff_tol = 5e-3

    @pytest.fixture
    def integrator(self):
        system = systems.DenseRiemannianMetricSystem(
            lambda q: 0.5 * np.sum(q ** 2),
            grad_neg_log_dens=lambda q: q,
            metric_func=lambda q: np.identity(q.shape[0]),
            vjp_metric_func=lambda q: lambda m: np.zeros_like(q),
        )
        return integrators.ImplicitLeapfrogIntegrator(system, 0.25)


class TestConstrainedLeapfrogIntegratorLinearSystem(
    ConstrainedLinearSystemIntegratorTests
):

    h_diff_tol = 1e-2

    @pytest.fixture
    def integrator(self, metric):
        system = systems.DenseConstrainedEuclideanMetricSystem(
            neg_log_dens=lambda q: 0.5 * np.sum(q ** 2),
            metric=metric,
            grad_neg_log_dens=lambda q: q,
            constr=lambda q: q[:1],
            jacob_constr=lambda q: np.eye(1, q.shape[0], 0),
        )
        return integrators.ConstrainedLeapfrogIntegrator(system, 0.1)


class TestConstrainedLeapfrogIntegratorNonLinearSystem(
    ConstrainedNonLinearSystemIntegratorTests
):

    h_diff_tol = 1e-2

    @pytest.fixture
    def system(self, metric):
        return systems.DenseConstrainedEuclideanMetricSystem(
            neg_log_dens=lambda q: 0.125 * np.sum(q ** 4),
            metric=metric,
            grad_neg_log_dens=lambda q: 0.5 * q ** 3,
            constr=lambda q: q[0:1] ** 2 + q[1:2] ** 2 - 1.0,
            jacob_constr=lambda q: np.concatenate(
                [2 * q[0:1], 2 * q[1:2], np.zeros(q.shape[0] - 2)]
            )[None],
        )

    @pytest.fixture(
        params=[
            solvers.solve_projection_onto_manifold_quasi_newton,
            solvers.solve_projection_onto_manifold_newton,
        ]
    )
    def integrator(self, system, request):
        return integrators.ConstrainedLeapfrogIntegrator(
            system, 0.1, projection_solver=request.param
        )


class TestConstrainedLeapfrogIntegratorGaussianLinearSystem(
    ConstrainedLinearSystemIntegratorTests
):

    h_diff_tol = 1e-4

    @pytest.fixture
    def integrator(self, metric):
        system = systems.GaussianDenseConstrainedEuclideanMetricSystem(
            lambda q: 0.0,
            grad_neg_log_dens=lambda q: 0.0 * q,
            metric=metric,
            constr=lambda q: q[:1],
            jacob_constr=lambda q: np.identity(q.shape[0])[:1],
            mhp_constr=lambda q: lambda m: np.zeros_like(q),
        )
        return integrators.ConstrainedLeapfrogIntegrator(system, 0.5)


class TestConstrainedLeapfrogIntegratorGaussianNonLinearSystem(
    ConstrainedNonLinearSystemIntegratorTests
):

    h_diff_tol = 5e-2

    @pytest.fixture
    def system(self, metric):
        return systems.GaussianDenseConstrainedEuclideanMetricSystem(
            neg_log_dens=lambda q: 0.125 * np.sum(q ** 4),
            metric=metric,
            grad_neg_log_dens=lambda q: 0.5 * q ** 3,
            constr=lambda q: q[0:1] ** 2 + q[1:2] ** 2 - 1.0,
            jacob_constr=lambda q: np.concatenate(
                [2 * q[0:1], 2 * q[1:2], np.zeros(q.shape[0] - 2)]
            )[None],
            mhp_constr=lambda q: lambda m: 0 * m[0],
        )

    @pytest.fixture(
        params=[
            solvers.solve_projection_onto_manifold_quasi_newton,
            solvers.solve_projection_onto_manifold_newton,
        ]
    )
    def integrator(self, system, request):
        return integrators.ConstrainedLeapfrogIntegrator(
            system, 0.05, projection_solver=request.param
        )
