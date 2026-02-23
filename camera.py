"""
Camera position calculations for CubeVi C1 lightfield display.

Uses off-axis (lens shift) projection to match CubeVi's official approach:
  - All N cameras face the SAME direction (no toe-in rotation)
  - Each camera is translated sideways (horizontal parallax only)
  - The projection frustum is shifted per camera to converge at the focal plane
  - This eliminates view-jump artifacts at view boundaries

Orbit model:
  - The camera orbits around a pivot point (set by right-click pan)
  - Yaw/pitch control the orbit angle (set by left-click drag)
  - Off-axis views spread laterally from the orbital position
  - All views share the same forward direction (critical for lenticular)
"""

import numpy as np
import pyrr

# CubeVi C1 specifications
QUILT_COLS = 8
QUILT_ROWS = 5
TOTAL_VIEWS = QUILT_COLS * QUILT_ROWS  # 40 views (default)
VIEW_CONE_DEGREES = 40.0  # C1's viewing angle


def quilt_grid_for_views(num_views):
    """Return (cols, rows) for a given view count. Always 8 columns; rows = ceil(num_views/8)."""
    cols = 8
    rows = (num_views + 7) // 8
    return cols, rows


def calculate_camera_positions(pivot, distance, yaw_deg=0.0, pitch_deg=0.0,
                               debug=True, view_cone_degrees=None,
                               num_views=40):
    """
    Generate N camera positions orbiting around a pivot point.

    The camera orbits at (yaw, pitch) around the pivot, then off-axis
    views spread laterally along the camera's local X axis.  All views
    share the same forward direction (required for lenticular interlacing).

    Args:
        pivot: np.array([x, y, z]) — point to orbit around (pan offset)
        distance: float — orbital radius (camera distance from pivot)
        yaw_deg: float — horizontal orbit angle in degrees
        pitch_deg: float — vertical orbit angle in degrees
        debug: bool — print debug information
        view_cone_degrees: float or None — horizontal view cone in degrees
        num_views: int — number of views (8, 16, or 40)

    Returns:
        list of dicts with camera info including view_matrix and proj_matrix
    """
    cameras = []
    cone_deg = view_cone_degrees if view_cone_degrees is not None else VIEW_CONE_DEGREES
    n = max(2, min(40, int(num_views)))
    cols, rows = quilt_grid_for_views(n)

    yaw_rad = np.radians(yaw_deg)
    pitch_rad = np.radians(pitch_deg)

    # Compute the camera's forward direction from orbit angles
    # Forward = direction FROM camera TO pivot
    # Standard spherical: camera sits at (r*sin(yaw)*cos(pitch), r*sin(pitch), r*cos(yaw)*cos(pitch))
    # relative to pivot, looking inward
    cos_pitch = np.cos(pitch_rad)
    cam_offset = np.array([
        np.sin(yaw_rad) * cos_pitch,
        np.sin(pitch_rad),
        np.cos(yaw_rad) * cos_pitch,
    ]) * distance

    # Camera basis vectors (local axes)
    forward = -cam_offset / distance  # normalized, points from camera toward pivot
    world_up = np.array([0.0, 1.0, 0.0])
    right = np.cross(forward, world_up)
    right_len = np.linalg.norm(right)
    if right_len < 1e-6:
        # Looking straight up or down — use Z as fallback up
        right = np.cross(forward, np.array([0.0, 0.0, 1.0]))
        right_len = np.linalg.norm(right)
    right = right / right_len
    up = np.cross(right, forward)

    # Off-axis: views spread along the camera's local right axis
    half_cone_rad = np.radians(cone_deg / 2.0)
    x_extent = distance * np.tan(half_cone_rad)

    aspect = 540.0 / 960.0
    fov_degrees = 45.0
    fov_rad = np.radians(fov_degrees)
    half_fov_tan = np.tan(fov_rad / 2.0)
    near = 0.1
    far = 100.0

    if debug:
        print(f"\n=== Camera Setup (orbit + off-axis) ===")
        print(f"Total views: {n} ({cols}x{rows} grid)")
        print(f"View cone: {cone_deg}° (horizontal only)")
        print(f"Orbit: yaw={yaw_deg:.1f}° pitch={pitch_deg:.1f}°")
        print(f"Pivot: {pivot}")
        print(f"Focal distance: {distance:.2f}")
        print(f"Lateral range: ±{x_extent:.3f}")

    # Base camera position (center of the view array)
    base_cam_pos = pivot + cam_offset

    for view_index in range(n):
        t = view_index / (n - 1) if n > 1 else 0.0
        lateral_offset = -x_extent + 2.0 * x_extent * t

        # Camera position: base + lateral shift along local right axis
        cam_pos = base_cam_pos + right * lateral_offset

        # Look target: same lateral shift (off-axis = no toe-in)
        look_target = pivot + right * lateral_offset

        view_matrix = pyrr.matrix44.create_look_at(
            cam_pos, look_target, up
        )

        # Off-axis frustum shift
        shift = -lateral_offset / distance
        top = near * half_fov_tan
        bottom = -top
        r = top * aspect
        l = -r
        shift_near = shift * near
        l += shift_near
        r += shift_near
        proj_matrix = _create_offaxis_projection(l, r, bottom, top, near, far)

        col = view_index % cols
        row = view_index // cols

        cameras.append({
            'index': view_index,
            'col': col,
            'row': row,
            'position': cam_pos,
            'look_at': look_target,
            'h_angle': np.degrees(np.arctan2(lateral_offset, distance)),
            'x_offset': lateral_offset,
            'v_angle': 0.0,
            'view_matrix': view_matrix,
            'proj_matrix': proj_matrix,
        })

    if debug and len(cameras) >= 2:
        print(f"  View 0:   offset={cameras[0]['x_offset']:+.3f}  angle={cameras[0]['h_angle']:.2f}°")
        if n > 10:
            mid = n // 2
            print(f"  View {mid}: offset={cameras[mid]['x_offset']:+.3f}  angle={cameras[mid]['h_angle']:.2f}°")
        print(f"  View {n-1}: offset={cameras[-1]['x_offset']:+.3f}  angle={cameras[-1]['h_angle']:.2f}°")

    return cameras


def _create_offaxis_projection(left, right, bottom, top, near, far):
    """
    Create an off-axis (asymmetric) perspective projection matrix.
    This is the OpenGL glFrustum equivalent.

    Returns a 4x4 matrix in the same layout as pyrr (row-major,
    compatible with ModernGL's .write()).
    """
    m = np.zeros((4, 4), dtype=np.float32)

    m[0, 0] = (2.0 * near) / (right - left)
    m[1, 1] = (2.0 * near) / (top - bottom)
    m[2, 0] = (right + left) / (right - left)
    m[2, 1] = (top + bottom) / (top - bottom)
    m[2, 2] = -(far + near) / (far - near)
    m[2, 3] = -1.0
    m[3, 2] = -(2.0 * far * near) / (far - near)

    return m


def create_projection_matrix(fov=45.0, aspect=0.5625, near=0.1, far=100.0):
    """
    Create a standard perspective projection matrix (used as fallback).
    For lenticular rendering, use the per-camera proj_matrix from
    calculate_camera_positions() instead.
    """
    return pyrr.matrix44.create_perspective_projection_matrix(
        fov, aspect, near, far
    )


def get_quilt_tile_position(view_index, tile_width, tile_height, quilt_cols=8):
    """
    Get the pixel position of a tile in the quilt.

    Args:
        view_index: 0 to (num_views - 1)
        tile_width: width of each tile in pixels
        tile_height: height of each tile in pixels
        quilt_cols: number of columns (always 8 for C1)

    Returns:
        (x, y) bottom-left corner of tile in quilt (OpenGL coordinates)
    """
    col = view_index % quilt_cols
    row = view_index // quilt_cols
    x = col * tile_width
    y = row * tile_height
    return (x, y)
