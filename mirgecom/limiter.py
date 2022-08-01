"""
:mod:`mirgecom.limiter` is for limiters and limiter-related constructs.

Field limiter functions
^^^^^^^^^^^^^^^^^^^^^^^

.. autofunction:: bound_preserving_limiter
.. autofunction:: cell_characteristic_size

"""

__copyright__ = """
Copyright (C) 2022 University of Illinois Board of Trustees
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

from grudge.discretization import DiscretizationCollection
import grudge.op as op


def cell_characteristic_size(actx, dcoll: DiscretizationCollection):
    r"""Evaluate cell area or volume."""
    zeros = dcoll.zeros(actx)
    return op.elementwise_integral(dcoll, zeros + 1.0)


def bound_preserving_limiter(dcoll: DiscretizationCollection, cell_size, field,
                                  mmin=0.0, mmax=None, modify_average=True):
    r"""Implement a slope limiter for bound-preserving properties.

    The implementation is summarized in [Zhang_2011]_, Sec. 2.3, Eq. 2.9,
    which uses a linear scaling factor of the high-order polynomials.

    By default, the average is also bounded to ensure that the solution is
    always positive. This option can be modified by an boolean argument.
    In case the average becomes negative, the limiter will drop to order zero
    and may not fix the negative values. This operation is not described in
    the original reference but greatly increased the robustness.

    An additional argument can be added to bound the upper limit.

    Parameters
    ----------
    dcoll: :class:`grudge.discretization.DiscretizationCollection`
        Grudge discretization with boundaries object
    cell_size: meshmode.dof_array.DOFArray or numpy.ndarray
        The cell area (2D) or volume (3D)
    field: meshmode.dof_array.DOFArray or numpy.ndarray
        A field to limit
    mmin: float
        Optional float with the target lower bound. Default to 0.0.
    mmax: float
        Optional float with the target upper bound. Default to None.
    modify_average: bool
        Flag to avoid modification the cell average. Defaults to True.

    Returns
    -------
    meshmode.dof_array.DOFArray or numpy.ndarray
        An array container containing the limited field(s).
    """
    actx = field.array_context

    # Compute cell averages of the state
    cell_avgs = 1.0/cell_size*op.elementwise_integral(dcoll, field)

    # Bound cell average in case it don't respect the boundaries
    if modify_average:
        cell_avgs = actx.np.where(actx.np.greater(cell_avgs, mmin), cell_avgs, mmin)

    # Compute elementwise max/mins of the field
    mmin_i = op.elementwise_min(dcoll, field)

    _theta = actx.np.minimum(
        1.0, actx.np.where(actx.np.less(mmin_i, mmin),
                           abs((mmin-cell_avgs)/(mmin_i-cell_avgs+1e-13)),
                           1.0)
        )

    if mmax is not None:
        cell_avgs = actx.np.where(actx.np.greater(cell_avgs, mmax), mmax, cell_avgs)

        mmax_i = op.elementwise_max(dcoll, field)

        _theta = actx.np.minimum(
            _theta, actx.np.where(actx.np.greater(mmax_i, mmax),
                                  abs((mmax-cell_avgs)/(mmax_i-cell_avgs+1e-13)),
                                  1.0)
        )

    return _theta*(field - cell_avgs) + cell_avgs
