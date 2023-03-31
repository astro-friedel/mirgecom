""":mod:`mirgecom.boundary` provides methods and constructs for boundary treatments.

Boundary Treatment Interfaces
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. autoclass:: FluidBoundary

Boundary Conditions Base Classes
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. autoclass:: PrescribedFluidBoundary

Boundary Conditions
^^^^^^^^^^^^^^^^^^^

.. autoclass:: DummyBoundary
.. autoclass:: AdiabaticSlipBoundary
.. autoclass:: AdiabaticNoslipMovingBoundary
.. autoclass:: IsothermalWallBoundary
"""

__copyright__ = """
Copyright (C) 2021 University of Illinois Board of Trustees
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

import numpy as np
from meshmode.mesh import BTAG_ALL, BTAG_NONE  # noqa
from meshmode.discretization.connection import FACE_RESTR_ALL
from grudge.dof_desc import as_dofdesc
from mirgecom.fluid import make_conserved
from grudge.trace_pair import TracePair
import grudge.op as op
from mirgecom.viscous import viscous_facial_flux_central
from mirgecom.flux import num_flux_central
from mirgecom.gas_model import make_fluid_state

from mirgecom.inviscid import inviscid_facial_flux_rusanov

from abc import ABCMeta, abstractmethod


class FluidBoundary(metaclass=ABCMeta):
    r"""Abstract interface to fluid boundary treatment.

    .. automethod:: inviscid_divergence_flux
    .. automethod:: viscous_divergence_flux
    .. automethod:: cv_gradient_flux
    .. automethod:: temperature_gradient_flux
    """

    @abstractmethod
    def inviscid_divergence_flux(self, dcoll, dd_bdry, gas_model, state_minus,
                                 numerical_flux_func, **kwargs):
        """Get the inviscid boundary flux for the divergence operator.

        This routine returns the facial flux used in the divergence
        of the inviscid fluid transport flux.

        Parameters
        ----------
        dcoll: :class:`~grudge.discretization.DiscretizationCollection`

            A discretization collection encapsulating the DG elements

        state_minus: :class:`~mirgecom.gas_model.FluidState`

            Fluid state object with the conserved state, and dependent
            quantities for the (-) side of the boundary specified by
            *dd_bdry*.

        dd_bdry:

            Boundary DOF descriptor (or object convertible to one) indicating which
            domain boundary to process

        gas_model: :class:`~mirgecom.gas_model.GasModel`

            Physical gas model including equation of state, transport,
            and kinetic properties as required by fluid state

        numerical_flux_func:

            Function should return the numerical flux corresponding to
            the divergence of the inviscid transport flux. This function
            is typically backed by an approximate Riemann solver, such as
            :func:`~mirgecom.inviscid.inviscid_facial_flux_rusanov`.

        Returns
        -------
        :class:`mirgecom.fluid.ConservedVars`
        """

    @abstractmethod
    def viscous_divergence_flux(self, dcoll, dd_bdry, gas_model, state_minus,
                                grad_cv_minus, grad_t_minus,
                                numerical_flux_func, **kwargs):
        """Get the viscous boundary flux for the divergence operator.

        This routine returns the facial flux used in the divergence
        of the viscous fluid transport flux.

        Parameters
        ----------
        dcoll: :class:`~grudge.discretization.DiscretizationCollection`

            A discretization collection encapsulating the DG elements

        dd_bdry:

            Boundary DOF descriptor (or object convertible to one) indicating which
            domain boundary to process

        state_minus: :class:`~mirgecom.gas_model.FluidState`

            Fluid state object with the conserved state, and dependent
            quantities for the (-) side of the boundary specified
            by *dd_bdry*.

        grad_cv_minus: :class:`~mirgecom.fluid.ConservedVars`

            The gradient of the conserved quantities on the (-) side
            of the boundary specified by *dd_bdry*.

        grad_t_minus: numpy.ndarray

            The gradient of the fluid temperature on the (-) side
            of the boundary specified by *dd_bdry*.

        gas_model: :class:`~mirgecom.gas_model.GasModel`

            Physical gas model including equation of state, transport,
            and kinetic properties as required by fluid state

        numerical_flux_func:

            Function should return the numerical flux corresponding to
            the divergence of the viscous transport flux. This function
            is typically backed by a helper, such as
            :func:`~mirgecom.viscous.viscous_facial_flux_central`.

        Returns
        -------
        :class:`mirgecom.fluid.ConservedVars`
        """

    @abstractmethod
    def cv_gradient_flux(self, dcoll, dd_bdry, gas_model, state_minus, **kwargs):
        """Get the boundary flux for the gradient of the fluid conserved variables.

        This routine returns the facial flux used by the gradient operator to
        compute the gradient of the fluid solution on a domain boundary.

        Parameters
        ----------
        dcoll: :class:`~grudge.discretization.DiscretizationCollection`

            A discretization collection encapsulating the DG elements

        dd_bdry:

            Boundary DOF descriptor (or object convertible to one) indicating which
            domain boundary to process

        state_minus: :class:`~mirgecom.gas_model.FluidState`

            Fluid state object with the conserved state, and dependent
            quantities for the (-) side of the boundary specified by
            *dd_bdry*.

        gas_model: :class:`~mirgecom.gas_model.GasModel`

            Physical gas model including equation of state, transport,
            and kinetic properties as required by fluid state

        Returns
        -------
        :class:`mirgecom.fluid.ConservedVars`
        """

    @abstractmethod
    def temperature_gradient_flux(self, dcoll, dd_bdry, gas_model, state_minus,
                                  **kwargs):
        """Get the boundary flux for the gradient of the fluid temperature.

        This method returns the boundary flux to be used by the gradient
        operator when computing the gradient of the fluid temperature at a
        domain boundary.

        Parameters
        ----------
        dcoll: :class:`~grudge.discretization.DiscretizationCollection`

            A discretization collection encapsulating the DG elements

        dd_bdry:

            Boundary DOF descriptor (or object convertible to one) indicating which
            domain boundary to process

        state_minus: :class:`~mirgecom.gas_model.FluidState`

            Fluid state object with the conserved state, and dependent
            quantities for the (-) side of the boundary specified by
            *dd_bdry*.

        gas_model: :class:`~mirgecom.gas_model.GasModel`

            Physical gas model including equation of state, transport,
            and kinetic properties as required by fluid state

        Returns
        -------
        numpy.ndarray
        """


# This class is a FluidBoundary that provides default implementations of
# the abstract methods in FluidBoundary. This class will be eliminated
# by resolution of https://github.com/illinois-ceesd/mirgecom/issues/576.
# TODO: Don't do this. Make every boundary condition implement its own
# version of the FluidBoundary methods.
class PrescribedFluidBoundary(FluidBoundary):
    r"""Abstract interface to a prescribed fluid boundary treatment.

    .. automethod:: __init__
    .. automethod:: inviscid_divergence_flux
    .. automethod:: viscous_divergence_flux
    .. automethod:: cv_gradient_flux
    .. automethod:: temperature_gradient_flux
    .. automethod:: av_flux
    """

    def __init__(self,
                 # returns the flux to be used in div op (prescribed flux)
                 inviscid_flux_func=None,
                 # returns CV+, to be used in num flux func (prescribed soln)
                 boundary_state_func=None,
                 # Flux to be used in grad(Temperature) op
                 temperature_gradient_flux_func=None,
                 # Function returns boundary temperature_plus
                 boundary_temperature_func=None,
                 # Function returns the flux to be used in grad(cv)
                 cv_gradient_flux_func=None,
                 # Function computes the numerical flux for a gradient
                 gradient_numerical_flux_func=None,
                 # Function computes the flux to be used in the div op
                 viscous_flux_func=None,
                 # Returns the boundary value for grad(cv)
                 boundary_gradient_cv_func=None,
                 # Returns the boundary value for grad(temperature)
                 boundary_gradient_temperature_func=None,
                 # For artificial viscosity - grad fluid soln on boundary
                 boundary_grad_av_func=None,
                 ):
        """Initialize the PrescribedFluidBoundary and methods."""
        self._bnd_state_func = boundary_state_func
        self._temperature_grad_flux_func = temperature_gradient_flux_func
        self._inviscid_flux_func = inviscid_flux_func
        self._bnd_temperature_func = boundary_temperature_func
        self._grad_num_flux_func = gradient_numerical_flux_func
        self._cv_gradient_flux_func = cv_gradient_flux_func
        self._viscous_flux_func = viscous_flux_func
        self._bnd_grad_cv_func = boundary_gradient_cv_func
        self._bnd_grad_temperature_func = boundary_gradient_temperature_func
        self._av_num_flux_func = num_flux_central
        self._bnd_grad_av_func = boundary_grad_av_func

        if not self._bnd_grad_av_func:
            self._bnd_grad_av_func = self._identical_grad_av

        if not self._inviscid_flux_func and not self._bnd_state_func:
            from warnings import warn
            warn("Using dummy boundary: copies interior solution.", stacklevel=2)

        if not self._inviscid_flux_func:
            self._inviscid_flux_func = self._inviscid_flux_for_prescribed_state

        if not self._bnd_state_func:
            self._bnd_state_func = self._identical_state

        if not self._bnd_temperature_func:
            self._bnd_temperature_func = self._temperature_for_prescribed_state
        if not self._grad_num_flux_func:
            self._grad_num_flux_func = num_flux_central

        if not self._cv_gradient_flux_func:
            self._cv_gradient_flux_func = self._gradient_flux_for_prescribed_cv
        if not self._temperature_grad_flux_func:
            self._temperature_grad_flux_func = \
                self._gradient_flux_for_prescribed_temperature

        if not self._viscous_flux_func:
            self._viscous_flux_func = self._viscous_flux_for_prescribed_state
        if not self._bnd_grad_cv_func:
            self._bnd_grad_cv_func = self._identical_grad_cv
        if not self._bnd_grad_temperature_func:
            self._bnd_grad_temperature_func = self._identical_grad_temperature

    def _boundary_quantity(self, dcoll, dd_bdry, quantity, local=False, **kwargs):
        """Get a boundary quantity on local boundary, or projected to "all_faces"."""
        dd_allfaces = dd_bdry.with_boundary_tag(FACE_RESTR_ALL)
        return quantity if local else op.project(dcoll,
            dd_bdry, dd_allfaces, quantity)

    def _boundary_state_pair(self, dcoll, dd_bdry, gas_model, state_minus, **kwargs):
        return TracePair(dd_bdry,
                         interior=state_minus,
                         exterior=self._bnd_state_func(dcoll=dcoll, dd_bdry=dd_bdry,
                                                       gas_model=gas_model,
                                                       state_minus=state_minus,
                                                       **kwargs))
    # The following methods provide default implementations of the fluid
    # boundary functions and helpers in an effort to eliminate much
    # repeated code. They will be eliminated by the resolution of
    # https://github.com/illinois-ceesd/mirgecom/issues/576.

    # {{{ Default boundary helpers

    # Returns temperature(+) for boundaries that prescribe CV(+)
    def _temperature_for_prescribed_state(self, dcoll, dd_bdry,
                                          gas_model, state_minus, **kwargs):
        boundary_state = self._bnd_state_func(dcoll=dcoll, dd_bdry=dd_bdry,
                                              gas_model=gas_model,
                                              state_minus=state_minus,
                                              **kwargs)
        return boundary_state.temperature

    def _identical_state(self, state_minus, **kwargs):
        return state_minus

    def _identical_grad_cv(self, grad_cv_minus, **kwargs):
        return grad_cv_minus

    def _identical_grad_temperature(self, dcoll, dd_bdry, grad_t_minus, **kwargs):
        return grad_t_minus

    # Returns the flux to be used by the gradient operator when computing the
    # gradient of the fluid solution on boundaries that prescribe CV(+).
    def _gradient_flux_for_prescribed_cv(self, dcoll, dd_bdry, gas_model,
                                         state_minus, **kwargs):
        # Use prescribed external state and gradient numerical flux function
        boundary_state = self._bnd_state_func(dcoll=dcoll, dd_bdry=dd_bdry,
                                              gas_model=gas_model,
                                              state_minus=state_minus,
                                              **kwargs)
        cv_pair = TracePair(dd_bdry,
                            interior=state_minus.cv,
                            exterior=boundary_state.cv)

        actx = state_minus.array_context
        nhat = actx.thaw(dcoll.normal(dd_bdry))
        from arraycontext import outer
        return outer(self._grad_num_flux_func(cv_pair.int, cv_pair.ext), nhat)

    # Returns the flux to be used by the gradient operator when computing the
    # gradient of fluid temperature using prescribed fluid temperature(+).
    def _gradient_flux_for_prescribed_temperature(self, dcoll, dd_bdry, gas_model,
                                                  state_minus, **kwargs):
        # Feed a boundary temperature to numerical flux for grad op
        actx = state_minus.array_context
        nhat = actx.thaw(dcoll.normal(dd_bdry))
        bnd_tpair = TracePair(dd_bdry,
                              interior=state_minus.temperature,
                              exterior=self._bnd_temperature_func(
                                  dcoll=dcoll, dd_bdry=dd_bdry, gas_model=gas_model,
                                  state_minus=state_minus, **kwargs))
        from arraycontext import outer
        return outer(self._grad_num_flux_func(bnd_tpair.int, bnd_tpair.ext), nhat)

    # Returns the flux to be used by the divergence operator when computing the
    # divergence of inviscid fluid transport flux using the boundary's
    # prescribed CV(+).
    def _inviscid_flux_for_prescribed_state(
            self, dcoll, dd_bdry, gas_model, state_minus,
            numerical_flux_func=inviscid_facial_flux_rusanov, **kwargs):
        # Use a prescribed boundary state and the numerical flux function
        boundary_state_pair = self._boundary_state_pair(dcoll=dcoll, dd_bdry=dd_bdry,
                                                        gas_model=gas_model,
                                                        state_minus=state_minus,
                                                        **kwargs)
        normal = state_minus.array_context.thaw(dcoll.normal(dd_bdry))
        return numerical_flux_func(boundary_state_pair, gas_model, normal)

    # Returns the flux to be used by the divergence operator when computing the
    # divergence of viscous fluid transport flux using the boundary's
    # prescribed CV(+).
    def _viscous_flux_for_prescribed_state(
            self, dcoll, dd_bdry, gas_model, state_minus, grad_cv_minus,
            grad_t_minus, numerical_flux_func=viscous_facial_flux_central, **kwargs):

        state_pair = self._boundary_state_pair(
            dcoll=dcoll, dd_bdry=dd_bdry, gas_model=gas_model,
            state_minus=state_minus, **kwargs)

        grad_cv_pair = \
            TracePair(dd_bdry, interior=grad_cv_minus,
                      exterior=self._bnd_grad_cv_func(
                          dcoll=dcoll, dd_bdry=dd_bdry, gas_model=gas_model,
                          state_minus=state_minus, grad_cv_minus=grad_cv_minus,
                          grad_t_minus=grad_t_minus))

        grad_t_pair = \
            TracePair(
                dd_bdry, interior=grad_t_minus,
                exterior=self._bnd_grad_temperature_func(
                    dcoll=dcoll, dd_bdry=dd_bdry, gas_model=gas_model,
                    state_minus=state_minus, grad_cv_minus=grad_cv_minus,
                    grad_t_minus=grad_t_minus))

        return numerical_flux_func(
            dcoll=dcoll, gas_model=gas_model, state_pair=state_pair,
            grad_cv_pair=grad_cv_pair, grad_t_pair=grad_t_pair)

    # }}} Default boundary helpers

    def inviscid_divergence_flux(self, dcoll, dd_bdry, gas_model, state_minus,
                                 numerical_flux_func=inviscid_facial_flux_rusanov,
                                 **kwargs):
        """Get the inviscid boundary flux for the divergence operator."""
        dd_bdry = as_dofdesc(dd_bdry)
        return self._inviscid_flux_func(dcoll, dd_bdry, gas_model, state_minus,
                                        numerical_flux_func=numerical_flux_func,
                                        **kwargs)

    def cv_gradient_flux(self, dcoll, dd_bdry, gas_model, state_minus, **kwargs):
        """Get the flux for *dd_bdry* for use in grad(CV)."""
        dd_bdry = as_dofdesc(dd_bdry)
        return self._cv_gradient_flux_func(
            dcoll=dcoll, dd_bdry=dd_bdry, gas_model=gas_model,
            state_minus=state_minus, **kwargs)

    def temperature_gradient_flux(self, dcoll, dd_bdry, gas_model, state_minus,
                                  **kwargs):
        """Get the flux for *dd_bdry* for use in grad(T)."""
        dd_bdry = as_dofdesc(dd_bdry)
        return self._temperature_grad_flux_func(dcoll, dd_bdry, gas_model,
                                                state_minus, **kwargs)

    def viscous_divergence_flux(self, dcoll, dd_bdry, gas_model, state_minus,
                                grad_cv_minus, grad_t_minus,
                                numerical_flux_func=viscous_facial_flux_central,
                                **kwargs):
        """Get the viscous flux for *dd_bdry* for use in the divergence operator."""
        dd_bdry = as_dofdesc(dd_bdry)
        return self._viscous_flux_func(dcoll=dcoll, dd_bdry=dd_bdry,
                                       gas_model=gas_model,
                                       state_minus=state_minus,
                                       grad_cv_minus=grad_cv_minus,
                                       grad_t_minus=grad_t_minus,
                                       numerical_flux_func=numerical_flux_func,
                                       **kwargs)

    # {{{ Boundary interface for artificial viscosity

    def _identical_grad_av(self, grad_av_minus, **kwargs):
        return grad_av_minus

    def av_flux(self, dcoll, dd_bdry, diffusion, **kwargs):
        """Get the diffusive fluxes for the AV operator API."""
        dd_bdry = as_dofdesc(dd_bdry)
        grad_av_minus = op.project(dcoll, dd_bdry.untrace(), dd_bdry, diffusion)
        actx = grad_av_minus.mass[0].array_context
        nhat = actx.thaw(dcoll.normal(dd_bdry))
        grad_av_plus = self._bnd_grad_av_func(
            dcoll=dcoll, dd_bdry=dd_bdry, grad_av_minus=grad_av_minus, **kwargs)
        bnd_grad_pair = TracePair(dd_bdry, interior=grad_av_minus,
                                  exterior=grad_av_plus)
        num_flux = self._av_num_flux_func(bnd_grad_pair.int, bnd_grad_pair.ext)@nhat
        return self._boundary_quantity(dcoll, dd_bdry, num_flux, **kwargs)

    # }}}


class DummyBoundary(PrescribedFluidBoundary):
    """Boundary type that assigns boundary-adjacent soln as the boundary solution."""

    def __init__(self):
        """Initialize the DummyBoundary boundary type."""
        PrescribedFluidBoundary.__init__(self)


class AdiabaticSlipBoundary(PrescribedFluidBoundary):
    r"""Boundary condition implementing inviscid slip boundary.

    a.k.a. Reflective inviscid wall boundary

    This class implements an adiabatic reflective slip boundary given
    by
    $\mathbf{q^{+}} = [\rho^{-}, (\rho{E})^{-}, (\rho\vec{V})^{-}
    - 2((\rho\vec{V})^{-}\cdot\hat{\mathbf{n}}) \hat{\mathbf{n}}]$
    wherein the normal component of velocity at the wall is 0, and
    tangential components are preserved. These perfectly reflecting
    conditions are used by the forward-facing step case in
    [Hesthaven_2008]_, Section 6.6, and correspond to the characteristic
    boundary conditions described in detail in [Poinsot_1992]_.

    .. automethod:: adiabatic_slip_state
    .. automethod:: adiabatic_slip_grad_av
    """

    def __init__(self):
        """Initialize AdiabaticSlipBoundary."""
        PrescribedFluidBoundary.__init__(
            self, boundary_state_func=self.adiabatic_slip_state,
            boundary_grad_av_func=self.adiabatic_slip_grad_av
        )

    def adiabatic_slip_state(self, dcoll, dd_bdry, gas_model, state_minus, **kwargs):
        """Get the exterior solution on the boundary.

        The exterior solution is set such that there will be vanishing
        flux through the boundary, preserving mass, momentum (magnitude) and
        energy.
        rho_plus = rho_minus
        v_plus = v_minus - 2 * (v_minus . n_hat) * n_hat
        mom_plus = rho_plus * v_plus
        E_plus = E_minus
        """
        # Grab some boundary-relevant data
        dim = state_minus.dim
        actx = state_minus.array_context

        # Grab a unit normal to the boundary
        nhat = actx.thaw(dcoll.normal(dd_bdry))

        # Subtract out the 2*wall-normal component
        # of velocity from the velocity at the wall to
        # induce an equal but opposite wall-normal (reflected) wave
        # preserving the tangential component
        cv_minus = state_minus.cv
        ext_mom = (cv_minus.momentum
                   - 2.0*np.dot(cv_minus.momentum, nhat)*nhat)
        # Form the external boundary solution with the new momentum
        ext_cv = make_conserved(dim=dim, mass=cv_minus.mass, energy=cv_minus.energy,
                                momentum=ext_mom, species_mass=cv_minus.species_mass)
        t_seed = state_minus.temperature if state_minus.is_mixture else None

        return make_fluid_state(cv=ext_cv, gas_model=gas_model,
                                temperature_seed=t_seed)

    def adiabatic_slip_grad_av(self, dcoll, dd_bdry, grad_av_minus, **kwargs):
        """Get the exterior grad(Q) on the boundary."""
        # Grab some boundary-relevant data
        dim, = grad_av_minus.mass.shape
        actx = grad_av_minus.mass[0].array_context
        nhat = actx.thaw(dcoll.normal(dd_bdry))

        # Subtract 2*wall-normal component of q
        # to enforce q=0 on the wall
        s_mom_normcomp = np.outer(nhat,
                                  np.dot(grad_av_minus.momentum, nhat))
        s_mom_flux = grad_av_minus.momentum - 2*s_mom_normcomp

        # flip components to set a neumann condition
        return make_conserved(dim, mass=-grad_av_minus.mass,
                              energy=-grad_av_minus.energy,
                              momentum=-s_mom_flux,
                              species_mass=-grad_av_minus.species_mass)


class AdiabaticNoslipMovingBoundary(PrescribedFluidBoundary):
    r"""Boundary condition implementing a noslip moving boundary.

    .. automethod:: adiabatic_noslip_state
    .. automethod:: adiabatic_noslip_grad_av
    """

    def __init__(self, wall_velocity=None, dim=2):
        """Initialize boundary device."""
        PrescribedFluidBoundary.__init__(
            self, boundary_state_func=self.adiabatic_noslip_state,
            boundary_grad_av_func=self.adiabatic_noslip_grad_av,
        )
        # Check wall_velocity (assumes dim is correct)
        if wall_velocity is None:
            wall_velocity = np.zeros(shape=(dim,))
        if len(wall_velocity) != dim:
            raise ValueError(f"Specified wall velocity must be {dim}-vector.")
        self._wall_velocity = wall_velocity

    def adiabatic_noslip_state(
            self, dcoll, dd_bdry, gas_model, state_minus, **kwargs):
        """Get the exterior solution on the boundary.

        Sets the external state s.t. $v^+ = -v^-$, giving vanishing contact velocity
        in the approximate Riemann solver used to compute the inviscid flux.
        """
        wall_pen = 2.0 * self._wall_velocity * state_minus.mass_density
        ext_mom = wall_pen - state_minus.momentum_density  # no-slip

        # Form the external boundary solution with the new momentum
        cv = make_conserved(dim=state_minus.dim, mass=state_minus.mass_density,
                            energy=state_minus.energy_density,
                            momentum=ext_mom,
                            species_mass=state_minus.species_mass_density)
        tseed = state_minus.temperature if state_minus.is_mixture else None
        return make_fluid_state(cv=cv, gas_model=gas_model, temperature_seed=tseed)

    def adiabatic_noslip_grad_av(self, grad_av_minus, **kwargs):
        """Get the exterior solution on the boundary."""
        return -grad_av_minus


class IsothermalWallBoundary(FluidBoundary):
    r"""Isothermal viscous wall boundary.

    This class implements an isothermal no-slip wall consistent with the prescription
    by [Mengaldo_2014]_.

    .. automethod:: __init__
    .. automethod:: inviscid_divergence_flux
    .. automethod:: viscous_divergence_flux
    .. automethod:: cv_gradient_flux
    .. automethod:: temperature_gradient_flux

    .. automethod:: state_bc
    .. automethod:: temperature_bc
    .. automethod:: inviscid_state_plus
    .. automethod:: viscous_state_plus
    .. automethod:: isothermal_wall_state
    """

    def __init__(self, wall_temperature=300):
        """Initialize the boundary condition object."""
        self._wall_temp = wall_temperature

    def temperature_bc(self, state_minus, **kwargs):
        """Get temperature value used in grad(T)."""
        return 0.*state_minus.temperature + self._wall_temp

    def state_bc(self, dcoll, dd_bdry, gas_model, state_minus, **kwargs):
        """Return BC fluid state."""
        # Mengaldo Eqn (48)
        temperature_bc = self.temperature_bc(state_minus, **kwargs)
        mom_bc = 0*state_minus.momentum_density
        mass_frac_bc = state_minus.species_mass_fractions
        rho_bc = state_minus.mass_density
        ie_bc = gas_model.eos.get_internal_energy(
            temperature=temperature_bc, species_mass_fractions=mass_frac_bc)
        energy_bc = rho_bc * ie_bc
        cv_bc = make_conserved(
            state_minus.dim, mass=rho_bc, energy=energy_bc, momentum=mom_bc,
            species_mass=rho_bc*mass_frac_bc)

        return make_fluid_state(cv=cv_bc, gas_model=gas_model,
                                temperature_seed=temperature_bc)

    def grad_cv_bc(self, state_minus, grad_cv_minus, normal, **kwargs):
        """Return grad(CV) to be used in the boundary calculation of viscous flux."""
        grad_species_mass_plus = 1.*grad_cv_minus.species_mass
        if state_minus.nspecies > 0:
            from mirgecom.fluid import species_mass_fraction_gradient
            grad_y_minus = species_mass_fraction_gradient(state_minus.cv,
                                                          grad_cv_minus)
            grad_y_plus = grad_y_minus - np.outer(grad_y_minus@normal, normal)
            grad_species_mass_plus = 0.*grad_y_plus

            for i in range(state_minus.nspecies):
                grad_species_mass_plus[i] = \
                    (state_minus.mass_density*grad_y_plus[i]
                     + state_minus.species_mass_fractions[i]*grad_cv_minus.mass)

        return make_conserved(grad_cv_minus.dim,
                              mass=grad_cv_minus.mass,
                              energy=grad_cv_minus.energy,
                              momentum=grad_cv_minus.momentum,
                              species_mass=grad_species_mass_plus)

    def grad_temperature_bc(self, dcoll, dd_bdry, gas_model, state_minus,
                            grad_cv_minus, grad_t_minus):
        """Return BC on grad(temperature)."""
        # Mengaldo Eqns (50-51)
        return grad_t_minus

    def inviscid_state_plus(self, dcoll, dd_bdry, gas_model, state_minus, **kwargs):
        """Return fluid state to use in calculation of inviscid flux."""
        # Mengaldo Eqn (45)
        rho_plus = state_minus.mass_density
        mom_plus = -state_minus.momentum_density
        ener_plus = state_minus.energy_density
        rhoy_plus = state_minus.species_mass_density
        cv_plus = make_conserved(state_minus.dim, mass=rho_plus, momentum=mom_plus,
                                 energy=ener_plus, species_mass=rhoy_plus)
        return make_fluid_state(cv=cv_plus, gas_model=gas_model,
                                temperature_seed=state_minus.temperature)

    def viscous_state_plus(self, dcoll, dd_bdry, gas_model, state_minus, **kwargs):
        """Return fluid state to use in calculation of viscous flux."""
        # Mengaldo Eqn (50)
        return self.state_bc(dcoll, dd_bdry, gas_model, state_minus, **kwargs)

    def inviscid_divergence_flux(self, dcoll, dd_bdry, gas_model, state_minus,
            numerical_flux_func=inviscid_facial_flux_rusanov, **kwargs):
        """Return Riemann flux using state with mom opposite of interior state."""
        # Mengaldo Eqn (46)
        dd_bdry = as_dofdesc(dd_bdry)
        state_plus = self.inviscid_state_plus(
            dcoll, dd_bdry, gas_model, state_minus, **kwargs)
        state_pair = TracePair(dd_bdry, interior=state_minus, exterior=state_plus)
        normal = state_minus.array_context.thaw(dcoll.normal(dd_bdry))
        return numerical_flux_func(state_pair, gas_model, normal)

    def viscous_divergence_flux(self, dcoll, dd_bdry, gas_model, state_minus,
                                grad_cv_minus, grad_t_minus,
                                numerical_flux_func=viscous_facial_flux_central,
                                **kwargs):
        """Return the boundary flux for the divergence of the viscous flux."""
        # Mengaldo Eqn (51)
        dd_bdry = as_dofdesc(dd_bdry)
        from mirgecom.viscous import viscous_flux
        actx = state_minus.array_context
        normal = actx.thaw(dcoll.normal(dd_bdry))
        state_bc = self.state_bc(dcoll, dd_bdry, gas_model, state_minus, **kwargs)
        grad_cv_bc = self.grad_cv_bc(state_minus=state_minus,
                                     grad_cv_minus=grad_cv_minus,
                                     normal=normal, **kwargs)

        grad_t_bc = self.grad_temperature_bc(
            dcoll=dcoll, dd_bdry=dd_bdry, gas_model=gas_model,
            state_minus=state_minus, grad_cv_minus=grad_cv_minus,
            grad_t_minus=grad_t_minus)

        # Note that [Mengaldo_2014]_ uses F_v(Q_bc, dQ_bc) here and
        # *not* the numerical viscous flux as advised by [Bassi_1997]_.
        f_ext = viscous_flux(state=state_bc, grad_cv=grad_cv_bc,
                             grad_t=grad_t_bc)
        return f_ext@normal

    def cv_gradient_flux(self, dcoll, dd_bdry, gas_model, state_minus, **kwargs):
        """Return flux to be used in grad operator when calculating grad(CV)"""
        # Mengaldo Eqn (50)+
        state_bc = self.state_bc(dcoll, dd_bdry, gas_model, state_minus, **kwargs)
        actx = state_minus.array_context
        nhat = actx.thaw(dcoll.normal(dd_bdry))
        from arraycontext import outer
        return outer(state_bc.cv, nhat)

    def temperature_gradient_flux(self, dcoll, dd_bdry, gas_model, state_minus,
                                  **kwargs):
        """Return flux to be used in grad operator when calculating grad(CV)"""
        # Mengaldo Eqn (50)+
        temperature_bc = self.temperature_bc(state_minus, **kwargs)
        actx = state_minus.array_context
        nhat = actx.thaw(dcoll.normal(dd_bdry))
        from arraycontext import outer
        return outer(temperature_bc, nhat)
