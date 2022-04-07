import MDSplus as mds
import numpy as np
import shot_details


def get_major_radius_coordinates(shot_number: int):
    """
    Extracts the radial and poloidal positions of the pixels in major radius coordinates for shots.

    Args:
        shot_number: Shot number(s) of interest.

    Returns:
        R: Major radius coordinates in centimetres.
        Z: Height array (above the machine midplane) in centimetres.
    """

    c = mds.Connection("alcdata")
    c.openTree("spectroscopy", shot_number)

    R_array_path = "GPI.APD_ARRAY.R_ARR"
    Z_array_path = "GPI.APD_ARRAY.Z_ARR"

    # extract frames, time, radial array and poloidal array
    R = c.get(R_array_path).data()
    Z = c.get(Z_array_path).data()

    return R, Z


def get_apd_frames(shot_number: int):
    """
    Extracts the frames and time array for a shot.

    Args:
        shot_number: Shot number(s) of interest.

    Returns:
        times: Time array in 100 nanosecond units.
        frames: Raw frames extracted for all pixels. These require further processing before analysis.
    """

    c = mds.Connection("alcdata")
    c.openTree("spectroscopy", shot_number)
    frames_path = "GPI.APD_ARRAY.FRAMES"

    frames = c.get(frames_path).data()
    time = c.get("dim_of(" + frames_path + ")").data()

    # Sometimes, the time dimension is not the same as the number of frames.
    # need to force them to be the same by adding time points if the time dimension is smaller than the number of frames
    # or cutting the time values off the end of the time array if the time dimension is greater than the number of frames.

    time_array_length = len(time)
    frame_array_length = len(frames)

    if time_array_length < frame_array_length:
        time_step = (time[99] - time[0]) / 99.0
        num = frame_array_length - time_array_length + 1
        new_times = np.arange(
            time[time_array_length - 1] + time_step,
            time[time_array_length - 1] + num * time_step,
            time_step,
        )
        time = np.append(time, new_times)
    elif time_array_length > frame_array_length:
        time = time[0:frame_array_length]

    return time, frames


def get_outergap_efit(shot_number, tree="ANALYSIS"):
    """
    Extracts the data on the outergap determined by EFIT.
    The outergap is the distance of limiter surface relative to the separatrix.
    More details about EFIT are here: https://omfit.io/modules/mod_EFIT.html

    Args:
        shot_number: Shot number(s) of interest.
        tree: Which EFIT data you want.
              By default, this is set to "ANALYSIS" which is a lower resolution EFIT.
              The other option is "EFIT19" which is a higher resolution EFIT.
              This is case sensisitve, so use capitals.

    Returns:
        time_outergap_EFIT: Time data of the EFIT data in seconds.
        outergap_EFIT: Outergap determined by EFIT in millimetres.

    """

    c = mds.Connection("alcdata")

    if tree == "EFIT19":
        c.openTree('efit19', shot_number)
        tree_path = "\EFIT19::TOP.RESULTS.A_EQDSK:ORIGHT"
    else:
        c.openTree('analysis', shot_number)
        tree_path = "\ANALYSIS::EFIT_AEQDSK:ORIGHT"

    outergap_efit = c.get(tree_path)
    time_outergap_efit = c.get("dim_of(" + tree_path + ")").data()

    return time_outergap_efit, outergap_efit


def generate_raw_apd_dataset(shot_number: int, time_start=None, time_end=None):
    """
    Generates an xarray dataset containing raw APD data for a shot

    Args:
        shot_number: Shot number(s) of interest.
        time_start: The beginning of the time window in seconds. Set to None by default.
        time_end: The end of the time window in seconds. Set to None by default.

    Returns:
        dataset: An xarray dataset containing raw APD data for all pixels:
            time: Time array in seconds.
            frames: Raw frames extracted for all pixels. These require further processing before analysis.
            R: Major radius coordinates in centimetres.
            Z: Height array (above the machine midplane) in centimetres.
    """

    time, frames = get_apd_frames(shot_number)

    # Convert time from 100 nanosecond units to seconds
    time = time * 1e-7

    R, Z = get_major_radius_coordinates(shot_number)

    apd_pixel_list = np.zeros((90, 2))
    for i in range(90):
        apd_pixel_list[i] = (i % 10, int(i / 10))

    apd_pixel_list = apd_pixel_list.astype(int).tolist()
    pixel_array_length = len(apd_pixel_list)

    apd_signal_array = np.zeros(
        (
            pixel_array_length,
            frames[:, 0, 0].size,
        )
    )

    for i in range(len(apd_pixel_list)):
        raw_time_series = frames[:, apd_pixel_list[i][0], apd_pixel_list[i][1]]

        # Criterion to find dead pixels
        if raw_time_series.std() < 0.01:
            raw_time_series[:] = np.nan
        else:
            # Take into account the offset
            offset = np.mean(raw_time_series[:200])
            raw_time_series = offset - raw_time_series[:]

        apd_signal_array[i, :] = raw_time_series[:]

    import xarray as xr

    frames = np.swapaxes(np.reshape(apd_signal_array, (9, 10, len(time))), 0, 1)

    if (time_start is not None) & (time_end is not None):
        time_interval = (time > time_start) & (time < time_end)
        frames = frames[:, :, time_interval]
        time = time[time_interval]

    dataset = xr.Dataset(
        {"frames": (["y", "x", "time"], frames)},
        coords={"R": (["y", "x"], R), "Z": (["y", "x"], Z), "time": (["time"], time)},
    )

    return dataset


def efit_major_radius_to_rho(R, Z, time_array, shot_number, tree):
    """
    Converts radial and poloidal coordinates to flux surface coordinates, rho.

    Args:
        R: Major radius (in cm) array for the coordinate pair to be mapped to rho.
        Z: Height (above the machine midplane) (in cm) array for the coordinate pair to be mapped to rho.
        time_array: Time array in seconds for the times for which the mapping is desired.
        shot_number: Shot number of interest.
        tree: Set this equal to the string of the tree name (e.g. 'efit19') which will be used
                for the flux surface mapping. The default is 'analysis'

    Returns:
        rho: 2D array (with the 1st dimension having the same number of
            elements as inputs R and Z and the 2nd dimension having the
            same number of elements as the input time_array).
            The 1st dimension specifies the element of the coordinate pair (R, Z) for the
            flux surface mapping from the coordinate (R, Z) at time time_array(i)
            to the distance beyond the separatrix in the horizontal plane
            that is defined by the Z of the magnetic axis (ZMAGX) at
            time time_array(i). Rho is returned in cm, with positive sign being outside
            the separatrix and minus sign being inside the separatrix.

    """

    import eqtools as eq
    import numpy as np

    cmod_efit = eq.CModEFITTree(shot_number, tree)

    # Major radii (in m) of the R,Z coordinate arrays
    # flux-surface-mapped to the height of the magnetic axis at each
    # time of the time_array. NOTE: that the time index is the 1st one, the space index is the 2nd!
    rmid = cmod_efit.rz2rmid(R / 100.0, Z / 100.0, time_array)

    # rmidout is the major radius of the outboard side of the separatrix at the
    # height of the magnetic axis (m)
    rmidout = cmod_efit.getRmidOut()
    efit_time = cmod_efit.getTimeBase()

    rho = np.zeros((len(R), len(time_array)))

    for i in range(len(time_array)):

        # Rhos is now the array of distances between the R,Z coordinate array
        # points that have been flux-surface-mapped to the height of the
        # magnetic axis MINUS the major radius of the outboard side of the
        # separatrix at the height of the magnetic axis (in cm)

        conditional = np.where(
            np.abs(time_array[i] - efit_time)
            == np.amin(np.abs(time_array[i] - efit_time))
        )
        rho[:, i] = (rmid[i, :] - rmidout[conditional]) * 100.0

    return rho


def major_radius_to_average_rho(
    shot_number, time_start=None, time_end=None, tree="EFIT19"
):
    """
    Given the pixel locations and the time slice, this function converts radial and poloidal coordinates to flux surface coordinates, rho.

    Args:
        shot_number: Shot number of interest.
        time_start: The beginning of the time window in seconds. Set to None by default.
        time_end: The end of the time window in seconds. Set to None by default.
        tree: Set this equal to the string of the tree name (e.g. 'efit19') which will be used
                for the flux surface mapping. The default is 'analysis'

    Returns:
        R: Array of radial coordinates in centimetres.
        Z: Array of the height in centimetres.
        rho_time_averaged: The time-averaged flux mapped coordinate in centimetres based on the time window specified.

    """

    R, Z = get_major_radius_coordinates(shot_number)

    if (time_start is None) & (time_end is None):
        time_array, _ = get_apd_frames(shot_number)
        time_start, time_end = time_array.amin(), time_array.amax()

    time = np.arange(time_start, time_end, 0.001)

    apd_pixel_list = np.zeros((90, 2))
    for i in range(90):
        apd_pixel_list[i] = (i % 10, int(i / 10))

    apd_pixel_list = apd_pixel_list.astype(int).tolist()
    pixel_array_length = len(apd_pixel_list)

    # Make a list of the R, Z coordinates
    major_radius_position = []

    for i in range(pixel_array_length):

        R_position = R[apd_pixel_list[i][0], apd_pixel_list[i][1]]
        Z_position = Z[apd_pixel_list[i][0], apd_pixel_list[i][1]]

        RZ_pair = [R_position, Z_position]
        major_radius_position.append(RZ_pair)

    major_radius_R = np.array(major_radius_position)[:, 0]
    major_radius_Z = np.array(major_radius_position)[:, 1]

    rho_array = efit_major_radius_to_rho(
        R=major_radius_R,
        Z=major_radius_Z,
        time_array=time,
        shot_number=shot_number,
        tree=tree,
    )

    rho_mean = np.mean(rho_array, axis=1)
    rho_time_averaged = np.swapaxes(np.reshape(rho_mean, (9, 10)), 0, 1)

    return R, Z, rho_time_averaged
