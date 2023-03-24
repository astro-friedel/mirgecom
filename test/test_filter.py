"""Test filter-related functions and constructs."""

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

import pytest
import numpy as np
from functools import partial

from mirgecom.discretization import create_discretization_collection
from meshmode.array_context import (  # noqa
    pytest_generate_tests_for_pyopencl_array_context
    as pytest_generate_tests)
from pytools.obj_array import (
    make_obj_array
)
from mirgecom.filter import make_spectral_filter


@pytest.mark.parametrize("dim", [1, 2, 3])
@pytest.mark.parametrize("order", [2, 3, 4])
@pytest.mark.parametrize("filter_order", [1, 2, 3])
def test_filter_coeff(actx_factory, filter_order, order, dim):
    """
    Test the construction of filter coefficients.

    Tests that the filter coefficients have the right values
    at the imposed band limits of the filter.  Also tests that
    the created filter operator has the expected shape:
    (nummodes x nummodes) matrix, and the filter coefficients
    in the expected positions corresponding to mode ids.
    """
    actx = actx_factory()

    nel_1d = 16

    from meshmode.mesh.generation import generate_regular_rect_mesh

    mesh = generate_regular_rect_mesh(
        a=(-0.5,) * dim, b=(0.5,) * dim, nelements_per_axis=(nel_1d,) * dim
    )

    dcoll = create_discretization_collection(actx, mesh, order=order)
    vol_discr = dcoll.discr_from_dd("vol")

    eta = .5  # just filter half the modes

    from mirgecom.simutil import get_number_of_tetrahedron_nodes
    nmodes = get_number_of_tetrahedron_nodes(dim, order)
    print(f"{nmodes=}")

    cutoff = int(eta * order)
    print(f"{cutoff=}")

    # number of filtered modes
    nfilt = order - cutoff
    print(f"{nfilt=}")
    # alpha = f(machine eps)
    # Alpha value suggested by:
    # JSH/TW Nodal DG Methods, Section 5.3
    # DOI: 10.1007/978-0-387-72067-8
    alpha = -1.0*np.log(np.finfo(float).eps)

    # expected values @ filter band limits
    expected_high_coeff = np.exp(-1.0*alpha)
    expected_cutoff_coeff = 1.0
    if dim == 1:
        cutoff_indices = [cutoff]
        high_indices = [order]
    elif dim == 2:
        sk = 0
        cutoff_indices = []
        high_indices = []
        for i in range(order + 1):
            for j in range(order - i + 1):
                if (i + j) == cutoff:
                    cutoff_indices.append(sk)
                if (i + j) == order:
                    high_indices.append(sk)
                sk += 1
    elif dim == 3:
        sk = 0
        cutoff_indices = []
        high_indices = []
        for i in range(order + 1):
            for j in range(order - i + 1):
                for k in range(order - (i + j) + 1):
                    if (i + j + k) == cutoff:
                        cutoff_indices.append(sk)
                    if (i + j + k) == order:
                        high_indices.append(sk)
                    sk += 1

    if nfilt <= 0:
        expected_high_coeff = 1.0

    print(f"{expected_high_coeff=}")
    print(f"{expected_cutoff_coeff=}")

    from mirgecom.filter import exponential_mode_response_function as xmrfunc
    frfunc = partial(xmrfunc, alpha=alpha, filter_order=filter_order)

    print(f"{vol_discr.groups=}")

    for group in vol_discr.groups:
        mode_ids = group.mode_ids()
        print(f"{mode_ids=}")
        filter_coeff = actx.thaw(
            make_spectral_filter(
                actx, group, cutoff=cutoff,
                mode_response_function=frfunc
            )
        )
        print(f"{filter_coeff=}")
        for mode_index, mode_id in enumerate(mode_ids):
            mode = mode_id
            print(f"{mode_id=}")
            if dim > 1:
                mode = sum(mode_id)
            print(f"{mode=}")
            if mode == cutoff:
                assert filter_coeff[mode_index] == expected_cutoff_coeff
            if mode == order:
                assert filter_coeff[mode_index] == expected_high_coeff


@pytest.mark.parametrize("element_order", [2, 8, 10])
@pytest.mark.parametrize("dim", [1, 2, 3])
def test_spectral_filter(actx_factory, element_order, dim):
    """
    Test the stand-alone procedural interface to spectral filtering.

    Tests that filtered fields have expected attenuated higher modes.
    """
    actx = actx_factory()

    nel_1d = 1

    from meshmode.mesh.generation import generate_regular_rect_mesh
    periodic = (False,)*dim
    mesh = generate_regular_rect_mesh(
        a=(-1.0,) * dim, b=(1.0,) * dim, nelements_per_axis=(nel_1d,) * dim,
        periodic=periodic
    )
    numelem = mesh.nelements
    from mirgecom.simutil import get_number_of_tetrahedron_nodes
    nummodes = get_number_of_tetrahedron_nodes(dim, element_order)
    print(f"{nummodes=}")
    print(f"{numelem=}")
    print(f"{element_order=}")
    # low_cutoff = 0
    # hi_cutoff = int(element_order - 1)
    mid_cutoff = int(element_order/2)

    # Test fields which have components with the following max orders:
    test_field_orders = [0, 1, 2, 3, 8, 9, 10, 20]

    print(f"{test_field_orders=}")
    print(f"{mid_cutoff=}")

    dcoll = create_discretization_collection(actx, mesh, order=element_order)
    nodes = actx.thaw(dcoll.nodes())
    vol_discr = dcoll.discr_from_dd("vol")

    emodes_to_pmodes = np.array([0 for _ in range(nummodes)], dtype=np.uint32)
    for group in vol_discr.groups:
        mode_ids = group.mode_ids()
        print(f"{mode_ids=}")
        for modi, mode in enumerate(mode_ids):
            emodes_to_pmodes[modi] = sum(mode)
    print(f"{emodes_to_pmodes=}")

    from mirgecom.filter import exponential_mode_response_function as xmrfunc
    from mirgecom.filter import filter_modally
    from grudge.dof_desc import DD_VOLUME_ALL, DD_VOLUME_ALL_MODAL

    # make it sharp
    filter_order = 2
    alpha = -1000.0*np.log(np.finfo(float).eps)

    frfunc = partial(xmrfunc, alpha=alpha, filter_order=filter_order)

    modal_map = dcoll.connection_from_dds(DD_VOLUME_ALL, DD_VOLUME_ALL_MODAL)

    # construct polynomial field:
    # a0 + a1*x + a2*x*x + ....
    def poly_func(coeff):  # , x_vec):
        # r = actx.np.sqrt(np.dot(nodes, nodes))
        r = nodes[0]
        result = 0
        for n, a in enumerate(coeff):
            result = result + a * r ** n
        return make_obj_array([result])

    # ISO fields are for hand-testing, please don't remove
    # iso_fields = []  # f(x) = x**order
    # iso_cutoff = 2
    fields = []  # f(x) = a0 + a1*x + a2*x*x + ....
    numfields = len(test_field_orders)

    for field_order in test_field_orders:
        # iso_field_coeff = [1.0 if i == field_order else 0.0
        #                   for i in range(field_order+1)]
        field_coeff = [1.0 / (i + 1) for i in range(field_order+1)]
        fields.append(poly_func(field_coeff))
        # iso_fields.append(poly_func(iso_field_coeff))

    unfiltered_fields = make_obj_array(fields)
    # unfiltered_iso_fields = make_obj_array(iso_fields)
    unfiltered_spectra = modal_map(unfiltered_fields)
    spectra_numbers = actx.to_numpy(unfiltered_spectra)

    # print(f"{unfiltered_spectra=}")
    # print(f"{spectra_numbers.shape=}")
    # print(f"{spectra_numbers=}")

    element_spectra = make_obj_array([
        [np.array([0. for i in range(element_order+1)])
         for j in range(numfields)] for k in range(numelem)])

    total_power = [np.array([0. for _ in range(element_order+1)])
                   for e in range(numfields)]

    for fldi in range(numfields):
        field_spectra = spectra_numbers[fldi][0][0]
        for el in range(numelem):
            spectral_storage = element_spectra[el][fldi]
            el_spectrum = field_spectra[el]
            for i in range(nummodes):
                speci = emodes_to_pmodes[i]
                spec_val = np.abs(float(el_spectrum[i]))
                spectral_storage[speci] = spectral_storage[speci] + spec_val
                total_power[fldi][speci] = total_power[fldi][speci] + spec_val

    print("Unfiltered expansions:")
    print(f"{element_spectra=}")
    print(f"{total_power=}")

    # unfiltered_iso_spectra = modal_map(unfiltered_iso_fields)
    filtered_fields = filter_modally(dcoll, mid_cutoff, frfunc, unfiltered_fields)
    filtered_spectra = modal_map(filtered_fields)
    spectra_numbers = actx.to_numpy(filtered_spectra)

    # filtered_isofields = filter_modally(dcoll, iso_cutoff, frfunc,
    #                                    unfiltered_iso_fields)
    # filtered_isospectra = modal_map(filtered_isofields)

    element_spectra = make_obj_array([
        [np.array([0. for i in range(element_order+1)])
         for j in range(numfields)] for k in range(numelem)])

    tot_pow_filtered = [np.array([0. for _ in range(element_order+1)])
                        for e in range(numfields)]

    for fldi in range(numfields):
        field_spectra = spectra_numbers[fldi][0][0]
        for el in range(numelem):
            spectral_storage = element_spectra[el][fldi]
            el_spectrum = field_spectra[el]
            for i in range(nummodes):
                speci = emodes_to_pmodes[i]
                spec_val = np.abs(float(el_spectrum[i]))
                spectral_storage[speci] = spectral_storage[speci] + spec_val
                tot_pow_filtered[fldi][speci] = \
                    tot_pow_filtered[fldi][speci] + spec_val

    print("Filtered expansions:")
    print(f"{element_spectra=}")
    print(f"{tot_pow_filtered=}")
    nfilt = element_order - mid_cutoff
    ckfn = partial(xmrfunc, alpha=alpha, cutoff=mid_cutoff,
                   filter_order=filter_order, nfilt=nfilt)

    # This checks that the total power in each mode has been
    # either unaffected (n <= Nc) or squelched by the
    # correct amount.
    for i in range(numfields):
        for n in range(element_order+1):
            tp = total_power[i][n]
            tpf = tot_pow_filtered[i][n]
            tpdiff = np.abs(tp - tpf)
            err = tpdiff
            if n <= mid_cutoff:
                if tp > 1e-12:
                    err = err / tp
            else:
                exp_rat = 1. - ckfn(n)
                if tp > 1e-12:
                    err = np.abs(tpdiff/tp - exp_rat)
            assert err < 1e-8
