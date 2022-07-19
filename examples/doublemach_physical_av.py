"""Demonstrate double mach reflection."""

__copyright__ = """
Copyright (C) 2020 University of Illinois Board of Trustees
"""

__license__ = """
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""

import logging
import numpy as np
import pyopencl as cl
import pyopencl.tools as cl_tools
from functools import partial

from arraycontext import thaw
from meshmode.mesh import BTAG_ALL, BTAG_NONE  # noqa
from grudge.dof_desc import DTAG_BOUNDARY
from grudge.shortcuts import make_visualizer


from mirgecom.navierstokes import ns_operator
from mirgecom.artificial_viscosity import (
    av_laplacian_operator,
    smoothness_indicator
)
from mirgecom.io import make_init_message
from mirgecom.mpi import mpi_entry_point
from mirgecom.integrators import rk4_step, euler_step
from grudge.shortcuts import compiled_lsrk45_step
from mirgecom.steppers import advance_state
from mirgecom.boundary import (
    AdiabaticNoslipMovingBoundary,
    PrescribedFluidBoundary,
    OutflowBoundary,
    AdiabaticSlipBoundary,
    SymmetryBoundary
)
from mirgecom.initializers import DoubleMachReflection
from mirgecom.eos import IdealSingleGas

from mirgecom.transport import TransportModel
from mirgecom.fluid import ConservedVars
from meshmode.dof_array import DOFArray
from typing import Optional
from mirgecom.eos import GasEOS, GasDependentVars

from mirgecom.simutil import get_sim_timestep, force_evaluation

from logpyle import set_dt
from mirgecom.euler import extract_vars_for_logging, units_for_logging
from mirgecom.logging_quantities import (
    initialize_logmgr,
    logmgr_add_many_discretization_quantities,
    logmgr_add_cl_device_info,
    logmgr_add_device_memory_usage,
    set_sim_state
)

logger = logging.getLogger(__name__)


class ArtificialViscosityTransport(TransportModel):
    r"""Transport model to add artificial viscosity based on a mesh dependent indicator

    Inherits from (and implements) :class:`TransportModel`.

    .. automethod:: __init__
    .. automethod:: bulk_viscosity
    .. automethod:: viscosity
    .. automethod:: volume_viscosity
    .. automethod:: species_diffusivity
    .. automethod:: thermal_conductivity
    """

    def __init__(self,
                 av_alpha, av_prandtl,
                 bulk_viscosity=0, viscosity=0,
                 thermal_conductivity=0,
                 species_diffusivity=None):
        """Initialize uniform, constant transport properties."""
        if species_diffusivity is None:
            species_diffusivity = np.empty((0,), dtype=object)
        self._mu_bulk = bulk_viscosity
        self._mu = viscosity
        self._kappa = thermal_conductivity
        self._d_alpha = species_diffusivity
        self._av_alpha = av_alpha
        self._av_prandtl = av_prandtl

    def bulk_viscosity(self, cv: ConservedVars,
                       dv: Optional[GasDependentVars] = None) -> DOFArray:
        r"""Get the bulk viscosity for the gas, $\mu_{B}$."""
        return self._mu_bulk*(0*cv.mass + 1.0)

    def viscosity(self, cv: ConservedVars,
                  dv: Optional[GasDependentVars] = None) -> DOFArray:
        r"""Get the gas dynamic viscosity, $\mu$."""

        viscosity = self._mu*(0*cv.mass + 1.0)
        if dv.smoothness is not None:
            viscosity += dv.smoothness*self._av_alpha

        return viscosity

    def volume_viscosity(self, cv: ConservedVars,
                         dv: Optional[GasDependentVars] = None) -> DOFArray:
        r"""Get the 2nd viscosity coefficent, $\lambda$.

        In this transport model, the second coefficient of viscosity is defined as:

        $\lambda = \left(\mu_{B} - \frac{2\mu}{3}\right)$
        """
        viscosity = (self._mu_bulk - 2 * self._mu / 3)*(0*cv.mass + 1.0)
        if dv.smoothness is not None:
            viscosity += dv.smoothness*self._av_alpha

        return viscosity

    def thermal_conductivity(self, cv: ConservedVars,
                             dv: Optional[GasDependentVars] = None,
                             eos: Optional[GasEOS] = None) -> DOFArray:
        r"""Get the gas thermal_conductivity, $\kappa$."""
        kappa = self._kappa*(0*cv.mass + 1.0)

        if dv.smoothness is not None:
            # kappa_av = mu_av*cp/Pr
             kappa += (dv.smoothness*self._av_alpha*
                       eos.heat_capacity_cp(cv, dv.temperature)/self._av_prandtl)

        return kappa

    def species_diffusivity(self, cv: ConservedVars,
                            dv: Optional[GasDependentVars] = None,
                            eos: Optional[GasEOS] = None) -> DOFArray:
        r"""Get the vector of species diffusivities, ${d}_{\alpha}$."""
        return self._d_alpha*(0*cv.mass + 1.0)


class MyRuntimeError(RuntimeError):
    """Simple exception to kill the simulation."""

    pass


# setsize from 0.025 to 0.01

def get_doublemach_mesh():
    """Generate or import a grid using `gmsh`.

    Input required:
        doubleMach.msh (read existing mesh)

    This routine will generate a new grid if it does
    not find the grid file (doubleMach.msh).
    """
    from meshmode.mesh.io import (
        read_gmsh,
        generate_gmsh,
        ScriptSource,
    )
    import os
    meshfile = "doubleMach.msh"
    if not os.path.exists(meshfile):
        mesh = generate_gmsh(
            ScriptSource("""
                x0=1.0/6.0;
                setsize=0.025;
                Point(1) = {0, 0, 0, setsize};
                Point(2) = {x0,0, 0, setsize};
                Point(3) = {4, 0, 0, setsize};
                Point(4) = {4, 1, 0, setsize};
                Point(5) = {0, 1, 0, setsize};
                Line(1) = {1, 2};
                Line(2) = {2, 3};
                Line(5) = {3, 4};
                Line(6) = {4, 5};
                Line(7) = {5, 1};
                Line Loop(8) = {-5, -6, -7, -1, -2};
                Plane Surface(8) = {8};
                Physical Surface('domain') = {8};
                Physical Curve('flow') = {1, 6, 7};
                Physical Curve('wall') = {2};
                Physical Curve('out') = {5};
        """, "geo"), force_ambient_dim=2, dimensions=2, target_unit="M",
            output_file_name=meshfile)
    else:
        mesh = read_gmsh(meshfile, force_ambient_dim=2)

    return mesh


@mpi_entry_point
def main(ctx_factory=cl.create_some_context, use_logmgr=True,
         use_leap=False, use_profiling=False, use_overintegration=False,
         casename=None, rst_filename=None, actx_class=None, lazy=False):
    """Drive the example."""
    if actx_class is None:
        raise RuntimeError("Array context class missing.")

    cl_ctx = ctx_factory()

    if casename is None:
        casename = "mirgecom"

    from mpi4py import MPI
    comm = MPI.COMM_WORLD
    rank = comm.Get_rank()
    nparts = comm.Get_size()

    # logging and profiling
    log_path = "log_data/"
    logname = log_path + casename + ".sqlite"

    if rank == 0:
        import os
        log_dir = os.path.dirname(logname)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)
    comm.Barrier()

    logmgr = initialize_logmgr(use_logmgr,
        filename=logname, mode="wo", mpi_comm=comm)

    if use_profiling:
        queue = cl.CommandQueue(
            cl_ctx, properties=cl.command_queue_properties.PROFILING_ENABLE)
    else:
        queue = cl.CommandQueue(cl_ctx)

    if lazy:
        actx = actx_class(comm, queue, mpi_base_tag=12000)
    else:
        actx = actx_class(comm, queue,
                allocator=cl_tools.MemoryPool(cl_tools.ImmediateAllocator(queue)),
                force_device_scalars=True)

    # Timestepping control
    current_step = 0
    #timestepper = rk4_step
    timestepper = euler_step
    force_eval = True
    t_final = 0.1
    #t_final = 0.6
    current_cfl = 0.1
    #current_dt = 1.e-4
    current_dt = 5.e-5
    current_t = 0
    constant_cfl = False

    def _compiled_stepper_wrapper(state, t, dt, rhs):
        return compiled_lsrk45_step(actx, state, t, dt, rhs)

    #timestepper = _compiled_stepper_wrapper
    #force_eval = False

    # default health status bounds
    health_pres_min = 1.0e-4
    health_pres_max = 2.0e6
    health_temp_min = 1e-4
    health_temp_max = 100

    # Some i/o frequencies
    nviz = 100
    nrestart = 1000
    nhealth = 1
    nstatus = 1

    viz_path = "viz_data/"
    vizname = viz_path + casename
    rst_path = "restart_data/"
    rst_pattern = (
        rst_path + "{cname}-{step:04d}-{rank:04d}.pkl"
    )
    if rst_filename:  # read the grid from restart data
        rst_filename = f"{rst_filename}-{rank:04d}.pkl"

        from mirgecom.restart import read_restart_data
        restart_data = read_restart_data(actx, rst_filename)
        local_mesh = restart_data["local_mesh"]
        local_nelements = local_mesh.nelements
        global_nelements = restart_data["global_nelements"]
        assert restart_data["nparts"] == nparts
    else:  # generate the grid from scratch
        gen_grid = partial(get_doublemach_mesh)
        from mirgecom.simutil import generate_and_distribute_mesh
        local_mesh, global_nelements = generate_and_distribute_mesh(comm, gen_grid)
        local_nelements = local_mesh.nelements

    from mirgecom.simutil import global_reduce as _global_reduce
    global_reduce = partial(_global_reduce, comm=comm)

    from mirgecom.discretization import create_discretization_collection
    order = 3
    discr = create_discretization_collection(actx, local_mesh, order=order,
                                             mpi_communicator=comm)
    nodes = thaw(discr.nodes(), actx)

    from grudge.dof_desc import DISCR_TAG_QUAD
    if use_overintegration:
        quadrature_tag = DISCR_TAG_QUAD
    else:
        quadrature_tag = None  # noqa

    dim = 2
    if logmgr:
        logmgr_add_cl_device_info(logmgr, queue)
        logmgr_add_device_memory_usage(logmgr, queue)

        logmgr.add_watches([
            ("step.max", "step = {value}, "),
            ("t_sim.max", "sim time: {value:1.6e} s\n"),
            ("t_step.max", "------- step walltime: {value:6g} s, ")
        ])

    # Solution setup and initialization
    s0 = -6.0
    kappa = 1.0
    #s0 = np.log10(1.0e-4 / np.power(order, 4))
    #kappa = 0.5
    #alpha = 2.0e-2
    #alpha = 0.1
    alpha = 3.0e-2
    #alpha = 1.e-5
    # {{{ Initialize simple transport model
    kappa_t = 0.
    sigma_v = 0.
    # }}}

    initializer = DoubleMachReflection()

    from mirgecom.gas_model import GasModel, make_fluid_state
    transport_model = ArtificialViscosityTransport(av_alpha=alpha, av_prandtl=0.75,
                                                   viscosity=sigma_v,
                                                   thermal_conductivity=kappa_t)

    eos = IdealSingleGas()
    gas_model = GasModel(eos=eos, transport=transport_model)


    if rst_filename:
        current_t = restart_data["t"]
        current_step = restart_data["step"]
        current_cv = restart_data["cv"]
        if logmgr:
            from mirgecom.logging_quantities import logmgr_set_time
            logmgr_set_time(logmgr, current_step, current_t)
    else:
        # Set the current state from time 0
        current_cv = initializer(nodes)
    smoothness = smoothness_indicator(discr, current_cv.mass,
                                      kappa=kappa, s0=s0)
    current_state = make_fluid_state(cv=current_cv, gas_model=gas_model,
                                     smoothness=smoothness)

    from mirgecom.gas_model import project_fluid_state
    from grudge.dof_desc import DOFDesc, as_dofdesc
    dd_base_vol = DOFDesc("vol")

    initial_cv = initializer(x_vec=nodes, eos=gas_model.eos)
    initial_smoothness = smoothness_indicator(discr, initial_cv.mass,
                                              kappa=kappa, s0=s0)
    initial_state = make_fluid_state(cv=initial_cv, gas_model=gas_model,
                                    smoothness=initial_smoothness)

    def _boundary_state(discr, btag, gas_model, state_minus, **kwargs):
        actx = state_minus.array_context
        bnd_discr = discr.discr_from_dd(btag)
        nodes = thaw(bnd_discr.nodes(), actx)
        #return make_fluid_state(cv=initializer(x_vec=nodes, eos=gas_model.eos,
                                               #**kwargs),
                                #gas_model=gas_model)
        return make_fluid_state(cv=initializer(x_vec=nodes, eos=gas_model.eos,
                                               **kwargs),
                                gas_model=gas_model,
                                smoothness=state_minus.dv.smoothness)

    def get_target_state_on_boundary(btag):
        return project_fluid_state(
            discr, dd_base_vol,
            as_dofdesc(btag).with_discr_tag(quadrature_tag),
            initial_state, gas_model
        )

    flow_ref_state = \
        get_target_state_on_boundary(DTAG_BOUNDARY("flow"))

    flow_ref_state = force_evaluation(actx, flow_ref_state)

    def _target_flow_state_func(**kwargs):
        return flow_ref_state

    #flow_boundary = PrescribedFluidBoundary(
        #boundary_state_func=_target_flow_state_func)

    flow_boundary = PrescribedFluidBoundary(
        boundary_state_func=_boundary_state)

    boundaries = {
        DTAG_BOUNDARY("flow"): flow_boundary,
        DTAG_BOUNDARY("wall"): SymmetryBoundary(),
        DTAG_BOUNDARY("out"): OutflowBoundary(boundary_pressure=1.0),
    }


    visualizer = make_visualizer(discr, order)

    initname = initializer.__class__.__name__
    eosname = eos.__class__.__name__
    init_message = make_init_message(
        dim=dim,
        order=order,
        nelements=local_nelements,
        global_nelements=global_nelements,
        dt=current_dt,
        t_final=t_final,
        nstatus=nstatus,
        nviz=nviz,
        cfl=current_cfl,
        constant_cfl=constant_cfl,
        initname=initname,
        eosname=eosname,
        casename=casename,
    )
    if rank == 0:
        logger.info(init_message)

    from grudge.dt_utils import characteristic_lengthscales
    length_scales = characteristic_lengthscales(actx, discr)

    def vol_min(x):
        from grudge.op import nodal_min
        return actx.to_numpy(nodal_min(discr, "vol", x))[()]

    def vol_max(x):
        from grudge.op import nodal_max
        return actx.to_numpy(nodal_max(discr, "vol", x))[()]

    def my_write_status(cv, dv, dt, cfl):
        status_msg = f"-------- dt = {dt:1.3e}, cfl = {cfl:1.4f}"
        p_min = vol_min(dv.pressure)
        p_max = vol_max(dv.pressure)
        t_min = vol_min(dv.temperature)
        t_max = vol_max(dv.temperature)

        dv_status_msg = (
            f"\n-------- P (min, max) (Pa) = ({p_min:1.9e}, {p_max:1.9e})")
        dv_status_msg += (
            f"\n-------- T (min, max) (K)  = ({t_min:7g}, {t_max:7g})")

        status_msg += dv_status_msg
        status_msg += "\n"

        if rank == 0:
            logger.info(status_msg)

    from mirgecom.viscous import get_viscous_timestep, get_viscous_cfl

    def my_get_timestep(t, dt, state):
        t_remaining = max(0, t_final - t)
        if constant_cfl:
            ts_field = current_cfl * get_viscous_timestep(discr, state=state)
            from grudge.op import nodal_min
            dt = actx.to_numpy(nodal_min(discr, "vol", ts_field))
            cfl = current_cfl
        else:
            ts_field = get_viscous_cfl(discr, dt=dt, state=state)
            from grudge.op import nodal_max
            cfl = actx.to_numpy(nodal_max(discr, "vol", ts_field))

        return ts_field, cfl, min(t_remaining, dt)

    def my_write_viz(step, t, fluid_state):
        cv = fluid_state.cv
        dv = fluid_state.dv

        mu = fluid_state.viscosity

        viz_fields = [("cv", cv),
                      ("dv", dv),
                      ("mu", mu)]
        from mirgecom.simutil import write_visfile
        write_visfile(discr, viz_fields, visualizer, vizname=vizname,
                      step=step, t=t, overwrite=True)

    def my_write_restart(step, t, state):
        rst_fname = rst_pattern.format(cname=casename, step=step, rank=rank)
        if rst_fname != rst_filename:
            rst_data = {
                "local_mesh": local_mesh,
                "cv": state,
                "t": t,
                "step": step,
                "order": order,
                "global_nelements": global_nelements,
                "num_parts": nparts
            }
            from mirgecom.restart import write_restart_file
            write_restart_file(actx, rst_data, rst_fname, comm)

    def my_health_check(state, dv):
        # Note: This health check is tuned s.t. it is a test that
        #       the case gets the expected solution.  If dt,t_final or
        #       other run parameters are changed, this check should
        #       be changed accordingly.
        health_error = False
        from mirgecom.simutil import check_naninf_local, check_range_local
        if check_naninf_local(discr, "vol", dv.pressure):
            health_error = True
            logger.info(f"{rank=}: NANs/Infs in pressure data.")

        if global_reduce(check_range_local(discr, "vol", dv.pressure, health_pres_min, health_pres_max),
                         op="lor"):
            health_error = True
            from grudge.op import nodal_max, nodal_min
            p_min = actx.to_numpy(nodal_min(discr, "vol", dv.pressure))
            p_max = actx.to_numpy(nodal_max(discr, "vol", dv.pressure))
            logger.info(f"Pressure range violation ({p_min=}, {p_max=})")

        if check_naninf_local(discr, "vol", dv.temperature):
            health_error = True
            logger.info(f"{rank=}: NANs/INFs in temperature data.")

        if global_reduce(
                check_range_local(discr, "vol", dv.temperature, health_temp_min, health_temp_max),
                op="lor"):
            health_error = True
            from grudge.op import nodal_max, nodal_min
            t_min = actx.to_numpy(nodal_min(discr, "vol", dv.temperature))
            t_max = actx.to_numpy(nodal_max(discr, "vol", dv.temperature))
            logger.info(f"Temperature range violation ({t_min=}, {t_max=})")

        return health_error

    def my_pre_step(step, t, dt, state):
        try:

            if logmgr:
                logmgr.tick_before()

            from mirgecom.simutil import check_step
            do_viz = check_step(step=step, interval=nviz)
            do_restart = check_step(step=step, interval=nrestart)
            do_health = check_step(step=step, interval=nhealth)
            do_status = check_step(step=step, interval=nstatus)

            if any([do_viz, do_restart, do_health, do_status]):
                smoothness = smoothness_indicator(discr, state.mass,
                                                  kappa=kappa, s0=s0)
                fluid_state = make_fluid_state(cv=state, gas_model=gas_model,
                                               smoothness=smoothness)
                dv = fluid_state.dv
                # if the time integrator didn't force_eval, do so now
                if not force_eval:
                    fluid_state = force_evaluation(actx, fluid_state)
                dv = fluid_state.dv

                ts_field, cfl, dt = my_get_timestep(t, dt, fluid_state)

            if do_health:
                health_errors = \
                    global_reduce(my_health_check(state, dv), op="lor")

                if health_errors:
                    if rank == 0:
                        logger.info("Fluid solution failed health check.")
                    raise MyRuntimeError("Failed simulation health check.")

            if do_status:
                my_write_status(dt=dt, cfl=cfl, dv=dv, cv=state)

            if do_restart:
                my_write_restart(step=step, t=t, state=state)

            if do_viz:
                my_write_viz(step=step, t=t, fluid_state=fluid_state)

        except MyRuntimeError:
            if rank == 0:
                logger.info("Errors detected; attempting graceful exit.")
            my_write_viz(step=step, t=t, fluid_state=fluid_state)
            my_write_restart(step=step, t=t, state=state)
            raise

        dt = get_sim_timestep(discr, current_state, current_t, current_dt,
                              current_cfl, t_final, constant_cfl)

        return state, dt

    def my_post_step(step, t, dt, state):
        # Logmgr needs to know about EOS, dt, dim?
        # imo this is a design/scope flaw
        if logmgr:
            set_dt(logmgr, dt)
            logmgr.tick_after()
        return state, dt

    def my_rhs(t, state):

        # make a smoothness indicator
        # call make_fluid_state with a smoothness indicator 
        # smoothness indicator is an optional value in make_fluid_state
        smoothness = smoothness_indicator(discr, state.mass,
                                          kappa=kappa, s0=s0)
        fluid_state = make_fluid_state(state, gas_model, smoothness=smoothness)

        return (
            ns_operator(discr, state=fluid_state, time=t,
                        boundaries=boundaries,
                        gas_model=gas_model, quadrature_tag=quadrature_tag)
        )

    current_dt = get_sim_timestep(discr, current_state, current_t, current_dt,
                                  current_cfl, t_final, constant_cfl)

    current_step, current_t, current_cv = \
        advance_state(rhs=my_rhs, timestepper=timestepper,
                      pre_step_callback=my_pre_step,
                      post_step_callback=my_post_step, dt=current_dt,
                      state=current_state.cv, t=current_t, t_final=t_final)

    # Dump the final data
    if rank == 0:
        logger.info("Checkpointing final state ...")
    smoothness = smoothness_indicator(discr, current_cv.mass,
                                      kappa=kappa, s0=s0)
    current_state = make_fluid_state(current_cv, gas_model,
                                     smoothness=smoothness)
    final_dv = current_state.dv
    ts_field, cfl, dt = my_get_timestep(t=current_t, dt=current_dt,
                                        state=current_state)
    my_write_status(dt=dt, cfl=cfl, cv=current_cv, dv=final_dv)
    my_write_viz(step=current_step, t=current_t, fluid_state=current_state)
    my_write_restart(step=current_step, t=current_t, state=current_cv)

    if logmgr:
        logmgr.close()
    elif use_profiling:
        print(actx.tabulate_profiling_data())

    finish_tol = 1e-16
    assert np.abs(current_t - t_final) < finish_tol


if __name__ == "__main__":
    import argparse
    casename = "doublemach"
    parser = argparse.ArgumentParser(description=f"MIRGE-Com Example: {casename}")
    parser.add_argument("--overintegration", action="store_true",
        help="use overintegration in the RHS computations")
    parser.add_argument("--lazy", action="store_true",
        help="switch to a lazy computation mode")
    parser.add_argument("--profiling", action="store_true",
        help="turn on detailed performance profiling")
    parser.add_argument("--log", action="store_true", default=True,
        help="turn on logging")
    parser.add_argument("--leap", action="store_true",
        help="use leap timestepper")
    parser.add_argument("--restart_file", help="root name of restart file")
    parser.add_argument("--casename", help="casename to use for i/o")
    args = parser.parse_args()
    lazy = args.lazy
    if args.profiling:
        if lazy:
            raise ValueError("Can't use lazy and profiling together.")

    from grudge.array_context import get_reasonable_array_context_class
    actx_class = get_reasonable_array_context_class(lazy=lazy, distributed=True)

    logging.basicConfig(format="%(message)s", level=logging.INFO)
    if args.casename:
        casename = args.casename
    rst_filename = None
    if args.restart_file:
        rst_filename = args.restart_file

    main(use_logmgr=args.log, use_leap=args.leap, use_profiling=args.profiling,
         use_overintegration=args.overintegration, lazy=lazy,
         casename=casename, rst_filename=rst_filename, actx_class=actx_class)

# vim: foldmethod=marker
