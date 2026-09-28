"""Memory-efficient TM near fields from the same MEENT modes as the power solver.

Layer positions follow MEENT's stack: positive depth points from glass into air.
`reconstruct_tm` returns native (Hy, Ex, Ez). MEENT's native vector z axis points
opposite to stack depth. `sample_fields` rotates y/z axes so positive physical
height points from glass into air; its Ez therefore changes sign. MEENT uses
unit incident Hy and incident Ex = -1/n_incident. Forward power divided by
incident power in native components is -n_incident*Re(Ex*conj(Hy)).
"""
import meent
import numpy as np

from .physics import silicon_index, validate_design


def padded_solver(config, design, glass_nm=100.0, air_nm=1400.0):
    """Add homogeneous observation layers without changing the physical device."""
    design = validate_design(design, config.n_cells)
    if glass_nm <= 0 or air_nm <= 0:
        raise ValueError("Observation layers must have positive thickness")
    indices = np.where(design, silicon_index(config), config.n_exit)
    solver = meent.call_mee(
        backend=0, n_top=config.n_incident, n_bot=config.n_exit,
        theta=0, phi=None, pol=1, period=[config.period_nm],
        wavelength=config.wavelength_nm,
        thickness=[glass_nm, config.thickness_nm, air_nm],
        fto=[config.fourier_order, 0], type_complex=np.complex128, fourier_type=1,
        ucell=np.stack([np.full(config.n_cells, config.n_incident), indices,
                        np.full(config.n_cells, config.n_exit)])[:, None, :].astype(np.complex128),
    )
    result = solver.conv_solve()
    return solver, result


def reconstruct_tm(solver, x_nm, depths_by_layer):
    """Evaluate solved layer modes without allocating stacks of diagonal matrices.

    Each layer's depths are local, in [0, thickness]. Layers and returned arrays
    run from incident to exit medium. Arbitrary x positions permit cell-centred
    grids and exact periodic quadrature without double-counting the endpoint.
    This implements MEENT's native direct Fourier field reconstruction; it does
    not remove the Gibbs oscillations of discontinuous normal electric fields.
    """
    if solver.pol != 1 or solver.phi is not None:
        raise ValueError("Only non-conical one-dimensional TM fields are supported")
    layers = list(reversed(solver.layer_info_list))
    if len(layers) != len(depths_by_layer):
        raise ValueError("Provide local depths for every observation layer")
    k0 = 2 * np.pi / solver.wavelength
    kx, _ = solver.get_kx_ky_vector(solver.wavelength)
    inverse = np.exp(-1j * k0 * np.asarray(x_nm)[:, None] * kx[None, :])
    transmitted = solver.T1.copy()
    output = []
    for info, depths in zip(layers, depths_by_layer):
        epz_inverse, W, V, q, thickness, A_inverse, B = info
        depths = np.asarray(depths, dtype=float)
        if depths.ndim != 1 or np.any(depths < 0) or np.any(depths > thickness):
            raise ValueError("Depths must lie inside their layer")
        propagated = np.exp(-k0 * q * thickness) * transmitted
        reflected = B @ (A_inverse @ propagated)
        forward = np.exp(-k0 * depths[:, None] * q) * transmitted
        backward = np.exp(k0 * (depths[:, None] - thickness) * q) * reflected
        hy_coeff = (forward + backward) @ W.T
        ex_coeff = -1j * (-forward + backward) @ V.T
        ez_coeff = -(hy_coeff * kx) @ epz_inverse.T
        output.append(np.stack([hy_coeff @ inverse.T, ex_coeff @ inverse.T,
                                ez_coeff @ inverse.T], axis=-1))
        transmitted = A_inverse @ propagated
    return output


def sample_fields(config, design, nx=512, nz_pattern=160, air_nm=1400.0, glass_nm=100.0):
    """Field arrays and physical axes for a Figure-5-style three-panel plot."""
    solver, result = padded_solver(config, design, glass_nm, air_nm)
    dz = config.thickness_nm / nz_pattern
    counts = [max(2, round(glass_nm / dz)), nz_pattern, max(2, round(air_nm / dz))]
    thicknesses = [glass_nm, config.thickness_nm, air_nm]
    x = (np.arange(nx) + 0.5) * config.period_nm / nx
    depths = [(np.arange(count) + .5) * thickness / count
              for count, thickness in zip(counts, thicknesses)]
    fields = np.concatenate(reconstruct_tm(solver, x, depths))
    # Plotting coordinate: glass below zero, Si/air at 0..325 nm, air above it.
    height = np.concatenate([depths[0] - glass_nm, depths[1],
                             depths[2] + config.thickness_nm])
    # Reference phase to the incident field at the glass/grating interface.
    phase = np.exp(1j * 2 * np.pi / config.wavelength_nm * config.n_incident * glass_nm)
    ex = -config.n_incident * fields[..., 1] * phase
    # Rotate the native z component into the plotted positive-height axis.
    # This makes kx*Ex + kz*Ez = 0 for each outgoing homogeneous-medium mode.
    ez = config.n_incident * fields[..., 2] * phase
    transmitted = np.asarray(result.de_ti).reshape(-1)
    reflected = np.asarray(result.de_ri).reshape(-1)
    flux = -config.n_incident * np.real(fields[..., 1] * fields[..., 0].conj()).mean(axis=1)
    return dict(x_nm=x, height_nm=height, ex=ex, ez=ez,
                intensity=np.abs(ex)**2 + np.abs(ez)**2,
                efficiency=float(transmitted[config.fourier_order + 1]),
                transmittance=float(transmitted.sum()), reflectance=float(reflected.sum()),
                # The homogeneous air rows offer a spatial power check independent
                # of the diffraction-order efficiency calculation.
                exit_flux=float(flux[-1]), flux_by_height=flux,
                fourier_order=config.fourier_order, field_schema=2)
