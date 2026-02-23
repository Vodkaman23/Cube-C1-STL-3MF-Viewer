"""
ModernGL renderer for CubeVi C1 lightfield display
Handles STL loading and 40-view quilt rendering
"""

import moderngl
import numpy as np
from stl import mesh as stl_mesh
import pyrr

try:
    import trimesh
    HAS_TRIMESH = True
except ImportError:
    HAS_TRIMESH = False
from camera import (
    calculate_camera_positions,
    create_projection_matrix,
    get_quilt_tile_position,
    quilt_grid_for_views,
)
from device_config import load_device_config


class CubeViRenderer:
    """
    Renders STL models as N-view quilts for CubeVi C1 display (N=8, 16, or 40),
    then interlaces the quilt into a lenticular-ready image.
    """

    # Per-view tile size (C1 spec)
    TILE_WIDTH = 540
    TILE_HEIGHT = 960

    # C1 native LCD resolution (portrait orientation)
    OUTPUT_WIDTH = 1440
    OUTPUT_HEIGHT = 2560

    # Default CubeVi C1 calibration values
    # From CubeVi-Swizzle-Unity BatchCameraManager defaults
    # These can be overridden per-device from deviceConfig.json
    DEFAULT_SLOPE = 0.1021
    DEFAULT_INTERVAL = 19.6169
    DEFAULT_X0 = 3.59

    def __init__(self, ctx, debug=True):
        """
        Initialize the renderer

        Args:
            ctx: ModernGL context
            debug: Enable debug output
        """
        self.ctx = ctx
        self.debug = debug

        # Rendering state
        self.mesh_vao = None
        self.mesh_vbo = None
        self.vertex_count = 0
        self.model_bounds = None

        # Transformation state
        self.model_rotation = [0.0, 0.0, 0.0]  # Pitch, Yaw, Roll
        self.model_scale = 1.0
        self.camera_distance = 1.77
        self._first_render = True  # Only print debug on first render
        self.view_blend = 0.7  # 0 = discrete views, 1 = smooth blend between adjacent views
        self.cubic_blend = 0.0  # 0 = 2-view smoothstep, 1 = 4-view Catmull-Rom at row boundaries
        self.view_cone_degrees = 40.0  # horizontal view cone (C1 hardware spec)
        self.gamma = 1.0  # output gamma (CubeVi _Gamma)
        self.num_views = 16  # 8, 16, or 40. Fewer = smoother transitions, less parallax.

        # Material properties
        self.model_color = (0.85, 0.85, 0.88)  # near-white with slight cool tint
        self.metallic = 0.0      # 0 = plastic/matte, 1 = metal
        self.roughness = 0.55    # moderate roughness — some specular
        self.rim_strength = 0.6  # rim/edge glow intensity

        # Background
        self.bg_top = (0.18, 0.20, 0.24)
        self.bg_bottom = (0.06, 0.06, 0.08)
        self.bg_accent = (0.25, 0.25, 0.30)  # third color for radial/studio/three-tone
        self.bg_mode = 0  # 0=gradient, 1=radial, 2=studio, 3=three-tone

        # Effects
        self.ao_strength = 0.4    # cavity darkening
        self.env_reflect = 0.15   # environment reflection
        self.light_intensity = 1.0  # master light brightness multiplier

        # Pan offset (right-click drag)
        self.pan_offset = np.array([0.0, 0.0, 0.0])

        # Pre-allocated GPU readback buffer (avoids alloc per frame)
        self._readback_buf = None

        # Camera cache (invalidated when distance/cone/num_views change)
        self._cached_cameras = None
        self._cam_cache_key = None

        # Identity model matrix (pre-computed bytes — camera orbit handles all transforms)
        self._model_identity_bytes = np.eye(4, dtype='f4').tobytes()

        # Quilt dimensions (depend on num_views)
        self._update_quilt_dims()

        # Framebuffers
        self.quilt_fbo = None
        self.quilt_texture = None
        self.interlace_fbo = None
        self.interlace_texture = None

        # Shader programs
        self.program = None
        self.bg_program = None
        self.bg_vao = None
        self.interlace_program = None
        self.fullscreen_vao = None

        # Load device-specific calibration from CubeStage config
        if self.debug:
            print("\n=== Loading device calibration ===")
        device_cfg = load_device_config(debug=self.debug)
        self.slope = device_cfg['slope']
        self.interval = device_cfg['interval']
        self.x0 = device_cfg['x0']

        self._setup_rendering()

        if self.debug:
            print(f"\n=== CubeVi Renderer Initialized ===")
            print(f"Views: {self.num_views} (quilt {self.quilt_cols}x{self.quilt_rows})")
            print(f"Quilt size: {self.quilt_width}x{self.quilt_height}")
            print(f"Tile size: {self.TILE_WIDTH}x{self.TILE_HEIGHT}")
            print(f"Output size: {self.OUTPUT_WIDTH}x{self.OUTPUT_HEIGHT}")
            print(f"Calibration: slope={self.slope}, interval={self.interval}, x0={self.x0}")

    def _update_quilt_dims(self):
        """Set quilt_cols, quilt_rows, quilt_width, quilt_height from num_views."""
        self.quilt_cols, self.quilt_rows = quilt_grid_for_views(self.num_views)
        self.quilt_width = self.TILE_WIDTH * self.quilt_cols
        self.quilt_height = self.TILE_HEIGHT * self.quilt_rows

    def _apply_material_uniforms(self):
        """Push current material/lighting state to the mesh shader."""
        p = self.program
        intensity = self.light_intensity

        # Material
        p['model_color'].value = self.model_color
        p['metallic'].value = self.metallic
        p['roughness'].value = self.roughness
        p['rim_strength'].value = self.rim_strength

        # Key light: bright, warm, top-right-front (main shadow caster)
        p['light_dir_key'].value = tuple(
            pyrr.vector3.normalize(np.array([1.0, 1.2, 1.0]))
        )
        p['light_color_key'].value = (1.0 * intensity, 0.95 * intensity, 0.9 * intensity)

        # Fill light: cooler, from the left (softens shadows) — brighter default
        p['light_dir_fill'].value = tuple(
            pyrr.vector3.normalize(np.array([-0.8, 0.4, 0.6]))
        )
        p['light_color_fill'].value = (0.45 * intensity, 0.48 * intensity, 0.55 * intensity)

        # Back/rim light: from behind, adds edge definition
        p['light_dir_back'].value = tuple(
            pyrr.vector3.normalize(np.array([-0.2, 0.6, -1.0]))
        )
        p['light_color_back'].value = (0.4 * intensity, 0.4 * intensity, 0.5 * intensity)

        # Ambient — brighter default
        p['ambient_color'].value = (0.22 * intensity, 0.22 * intensity, 0.25 * intensity)

        # Effects
        p['ao_strength'].value = self.ao_strength
        p['env_reflect'].value = self.env_reflect

    def _setup_rendering(self):
        """Setup shaders, framebuffers, and interlacing pipeline"""
        if self.debug:
            print("\n=== Setting up rendering ===")

        import os
        shader_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'shaders')

        # Load mesh shaders
        with open(os.path.join(shader_dir, 'mesh.vert'), 'r') as f:
            vertex_shader = f.read()
        with open(os.path.join(shader_dir, 'mesh.frag'), 'r') as f:
            fragment_shader = f.read()

        self.program = self.ctx.program(
            vertex_shader=vertex_shader,
            fragment_shader=fragment_shader
        )

        if self.debug:
            print("  Mesh shaders compiled")

        # Load background gradient shaders
        with open(os.path.join(shader_dir, 'bg.vert'), 'r') as f:
            bg_vs = f.read()
        with open(os.path.join(shader_dir, 'bg.frag'), 'r') as f:
            bg_fs = f.read()

        self.bg_program = self.ctx.program(
            vertex_shader=bg_vs,
            fragment_shader=bg_fs
        )

        # Fullscreen quad for background (just positions, no texcoords)
        bg_quad = np.array([
            -1.0, -1.0,
             1.0, -1.0,
            -1.0,  1.0,
             1.0,  1.0,
        ], dtype='f4')
        bg_vbo = self.ctx.buffer(bg_quad)
        self.bg_vao = self.ctx.vertex_array(
            self.bg_program,
            [(bg_vbo, '2f', 'in_position')]
        )

        if self.debug:
            print("  Background shaders compiled")

        # Load interlacing shaders
        with open(os.path.join(shader_dir, 'interlace.vert'), 'r') as f:
            interlace_vs = f.read()
        with open(os.path.join(shader_dir, 'interlace.frag'), 'r') as f:
            interlace_fs = f.read()

        self.interlace_program = self.ctx.program(
            vertex_shader=interlace_vs,
            fragment_shader=interlace_fs
        )

        if self.debug:
            print("  Interlace shaders compiled")

        # Create fullscreen quad for interlacing pass
        quad_vertices = np.array([
            # position   texcoord
            -1.0, -1.0,  0.0, 0.0,
             1.0, -1.0,  1.0, 0.0,
            -1.0,  1.0,  0.0, 1.0,
             1.0,  1.0,  1.0, 1.0,
        ], dtype='f4')
        quad_vbo = self.ctx.buffer(quad_vertices)
        self.fullscreen_vao = self.ctx.vertex_array(
            self.interlace_program,
            [(quad_vbo, '2f 2f', 'in_position', 'in_texcoord')]
        )

        # Create quilt framebuffer (cols x rows of 540x960 tiles)
        self.quilt_texture = self.ctx.texture(
            (self.quilt_width, self.quilt_height),
            4  # RGBA
        )
        self.quilt_texture.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.quilt_fbo = self.ctx.framebuffer(
            color_attachments=[self.quilt_texture],
            depth_attachment=self.ctx.depth_renderbuffer(
                (self.quilt_width, self.quilt_height)
            )
        )

        # Create interlaced output framebuffer (1440x2560 = C1 native LCD)
        self.interlace_texture = self.ctx.texture(
            (self.OUTPUT_WIDTH, self.OUTPUT_HEIGHT),
            4  # RGBA
        )
        self.interlace_fbo = self.ctx.framebuffer(
            color_attachments=[self.interlace_texture]
        )

        if self.debug:
            print(f"  Quilt FBO: {self.quilt_width}x{self.quilt_height}")
            print(f"  Interlace FBO: {self.OUTPUT_WIDTH}x{self.OUTPUT_HEIGHT}")

        # Enable depth testing
        self.ctx.enable(moderngl.DEPTH_TEST)

        # Set mesh shader uniforms — 3-point studio lighting
        self._apply_material_uniforms()
        self.program['has_vertex_colors'].value = 0.0

        # Set interlace shader uniforms
        self.interlace_program['Slope'].value = self.slope
        self.interlace_program['Interval'].value = self.interval
        self.interlace_program['X0'].value = self.x0
        self.interlace_program['ImgsCountX'].value = float(self.quilt_cols)
        self.interlace_program['ImgsCountY'].value = float(self.quilt_rows)
        self.interlace_program['ImgsCountAll'].value = float(self.num_views)
        self.interlace_program['OutputSizeX'].value = float(self.OUTPUT_WIDTH)
        self.interlace_program['OutputSizeY'].value = float(self.OUTPUT_HEIGHT)
        self.interlace_program['ViewBlend'].value = float(self.view_blend)
        self.interlace_program['CubicBlend'].value = float(self.cubic_blend)
        self.interlace_program['Gamma'].value = float(self.gamma)

        if self.debug:
            print("  All rendering setup complete")
    
    def load_stl(self, filepath):
        """
        Load STL or 3MF file and prepare for rendering.

        Args:
            filepath: Path to STL or 3MF file
        """
        ext = filepath.lower().split('.')[-1] if '.' in filepath else ''
        if self.debug:
            print(f"\n=== Loading {filepath} ===")

        try:
            colors = None
            if ext == '3mf':
                vertices, normals, colors = self._load_3mf(filepath)
            else:
                vertices, normals = self._load_stl(filepath)

            if vertices is None:
                return False

            self.vertex_count = len(vertices)

            if self.debug:
                print(f"✓ Extracted {self.vertex_count} vertices")

            # Calculate bounds for auto-centering and scaling
            min_bounds = vertices.min(axis=0)
            max_bounds = vertices.max(axis=0)
            self.model_bounds = {
                'min': min_bounds,
                'max': max_bounds,
                'center': (min_bounds + max_bounds) / 2.0,
                'size': max_bounds - min_bounds
            }

            max_dimension = self.model_bounds['size'].max()

            if self.debug:
                print(f"✓ Model bounds:")
                print(f"  Center: {self.model_bounds['center']}")
                print(f"  Size: {self.model_bounds['size']}")
                print(f"  Max dimension: {max_dimension:.3f}")

            # Auto-scale to fit in [-1, 1] range
            auto_scale = 1.8 / max_dimension  # Leave some margin
            vertices = (vertices - self.model_bounds['center']) * auto_scale

            self.model_scale = auto_scale

            if self.debug:
                print(f"✓ Auto-scaled by {auto_scale:.3f}")

            # Interleave vertices, normals, and colors for VBO
            # Format: [vx, vy, vz, nx, ny, nz, cr, cg, cb, ...]
            has_colors = colors is not None
            vertex_data = np.zeros((self.vertex_count, 9), dtype='f4')
            vertex_data[:, 0:3] = vertices
            vertex_data[:, 3:6] = normals
            if has_colors:
                vertex_data[:, 6:9] = colors
            else:
                vertex_data[:, 6:9] = 1.0  # white default

            # Create VBO
            self.mesh_vbo = self.ctx.buffer(vertex_data.tobytes())

            # Create VAO with color attribute
            self.mesh_vao = self.ctx.vertex_array(
                self.program,
                [
                    (self.mesh_vbo, '3f 3f 3f', 'in_position', 'in_normal', 'in_color')
                ]
            )

            # Tell the shader whether to use vertex colors or uniform color
            self.program['has_vertex_colors'].value = 1.0 if has_colors else 0.0

            if self.debug:
                col_str = "with vertex colors" if has_colors else "uniform color"
                print(f"✓ GPU buffers created ({col_str})")
                print(f"✓ Model ready for rendering")

            return True

        except Exception as e:
            print(f"✗ Error loading: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _load_stl(self, filepath):
        """Load STL file, return (vertices, normals) or (None, None) on error."""
        stl_data = stl_mesh.Mesh.from_file(filepath)
        if self.debug:
            print(f"✓ STL loaded: {len(stl_data.vectors)} triangles")
        vertices = stl_data.vectors.reshape(-1, 3)
        normals = np.repeat(stl_data.normals, 3, axis=0)
        return vertices, normals

    def _load_3mf(self, filepath):
        """Load 3MF file via trimesh, return (vertices, normals, colors) or (None, None, None) on error.
        colors is None if no per-face/vertex colors exist, otherwise Nx3 float32 (0-1 range)."""
        if not HAS_TRIMESH:
            print("✗ 3MF requires trimesh: pip install trimesh lxml")
            return None, None, None
        loaded = trimesh.load(filepath, force='mesh')
        if isinstance(loaded, trimesh.Scene):
            meshes = [g for g in loaded.geometry.values() if isinstance(g, trimesh.Trimesh)]
            if not meshes:
                print("✗ 3MF contains no mesh geometry")
                return None, None, None
            mesh = trimesh.util.concatenate(meshes) if len(meshes) > 1 else meshes[0]
        elif isinstance(loaded, trimesh.Trimesh):
            mesh = loaded
        else:
            print(f"✗ 3MF loaded as unsupported type: {type(loaded)}")
            return None, None, None

        # Fix degenerate/zero-area faces that produce NaN normals
        mesh.update_faces(mesh.nondegenerate_faces())
        mesh.fix_normals()

        # Expand to triangle list (match STL format: 3 vertices per triangle)
        vertices = mesh.vertices[mesh.faces].reshape(-1, 3).astype(np.float32)

        # Use face normals (one per triangle, repeated 3x per vertex) —
        # matches STL convention and avoids the smooth-averaged vertex_normals
        # which can produce wrong lighting on hard-edge / mechanical parts.
        normals = np.repeat(mesh.face_normals, 3, axis=0).astype(np.float32)

        # Extract per-face or per-vertex colors if available
        colors = None
        if hasattr(mesh.visual, 'kind'):
            if mesh.visual.kind == 'face' and mesh.visual.face_colors is not None:
                # face_colors is (N, 4) RGBA uint8 — take RGB, normalize, repeat 3x per vertex
                fc = mesh.visual.face_colors[:, :3].astype(np.float32) / 255.0
                colors = np.repeat(fc, 3, axis=0)
                if self.debug:
                    print(f"✓ 3MF has per-face colors ({len(fc)} faces)")
            elif mesh.visual.kind == 'vertex' and mesh.visual.vertex_colors is not None:
                # vertex_colors is (V, 4) RGBA uint8 — expand via faces
                vc = mesh.visual.vertex_colors[:, :3].astype(np.float32) / 255.0
                colors = vc[mesh.faces].reshape(-1, 3)
                if self.debug:
                    print(f"✓ 3MF has per-vertex colors ({len(vc)} vertices)")

        if self.debug:
            has_col = "yes" if colors is not None else "no"
            print(f"✓ 3MF loaded: {len(mesh.faces)} triangles, {len(vertices)} vertices, colors={has_col}")
        return vertices, normals, colors
    
    def render_quilt(self):
        """
        Render the complete N-view quilt and interlace for C1 output.

        Returns:
            bytearray of RGBA pixels (1440x2560x4, top-down row order)
        """
        if self.mesh_vao is None:
            if self.debug:
                print("✗ No mesh loaded")
            return None
        
        debug_this_frame = self.debug and self._first_render
        if debug_this_frame:
            print(f"\n=== Rendering quilt ===")
            print(f"Rotation: pitch={self.model_rotation[0]:.1f}° yaw={self.model_rotation[1]:.1f}° roll={self.model_rotation[2]:.1f}°")
        
        # Use quilt framebuffer
        self.quilt_fbo.use()

        # Generate camera data (cached — recalculated when any orbit param changes)
        pan_key = tuple(self.pan_offset)
        rot_key = tuple(self.model_rotation)
        cache_key = (self.camera_distance, self.view_cone_degrees, self.num_views, pan_key, rot_key)
        if self._cam_cache_key != cache_key:
            pivot = self.pan_offset.copy()
            cameras = calculate_camera_positions(
                pivot,
                self.camera_distance,
                yaw_deg=self.model_rotation[1],    # yaw from left-drag
                pitch_deg=self.model_rotation[0],   # pitch from left-drag
                debug=debug_this_frame,
                view_cone_degrees=self.view_cone_degrees,
                num_views=self.num_views,
            )
            # Pre-convert to render-ready tuples: (tile_pos, view_bytes, proj_bytes)
            self._cached_cam_data = []
            for cam in cameras:
                self._cached_cam_data.append((
                    get_quilt_tile_position(cam['index'], self.TILE_WIDTH, self.TILE_HEIGHT, self.quilt_cols),
                    cam['view_matrix'].astype('f4').tobytes(),
                    cam['proj_matrix'].astype('f4').tobytes(),
                ))
            self._cam_cache_key = cache_key

        # Model matrix is identity (camera orbit handles all transforms)
        self.program['model'].write(self._model_identity_bytes)

        # Enable scissor test for per-tile clearing and rendering isolation
        self.ctx.enable(moderngl.DEPTH_TEST)
        self.ctx.enable_direct(0x0C11)  # GL_SCISSOR_TEST

        # Set bg uniforms once (same for all tiles)
        self.bg_program['bg_top'].value = self.bg_top
        self.bg_program['bg_bottom'].value = self.bg_bottom
        self.bg_program['bg_accent'].value = self.bg_accent
        self.bg_program['bg_mode'].value = self.bg_mode

        # Render each view with its own view + projection matrix
        for (tile_x, tile_y), view_bytes, proj_bytes in self._cached_cam_data:
            # Set viewport AND scissor to this tile
            self.ctx.viewport = (tile_x, tile_y, self.TILE_WIDTH, self.TILE_HEIGHT)
            self.ctx.scissor = (tile_x, tile_y, self.TILE_WIDTH, self.TILE_HEIGHT)

            # Clear depth and draw gradient background
            self.quilt_fbo.clear(0.0, 0.0, 0.0, 1.0)

            # Draw gradient background (no depth write)
            self.ctx.disable(moderngl.DEPTH_TEST)
            self.bg_vao.render(moderngl.TRIANGLE_STRIP)
            self.ctx.enable(moderngl.DEPTH_TEST)

            # Set per-camera view and off-axis projection matrices
            self.program['view'].write(view_bytes)
            self.program['projection'].write(proj_bytes)

            # Render the mesh
            self.mesh_vao.render(moderngl.TRIANGLES)

        # Disable scissor test and reset
        self.ctx.disable_direct(0x0C11)  # GL_SCISSOR_TEST
        self.ctx.scissor = None
        
        if debug_this_frame:
            print(f"  Rendered {self.num_views} views into quilt")

        # === Pass 2: Interlace quilt into lenticular output ===
        self.interlace_fbo.use()
        self.ctx.viewport = (0, 0, self.OUTPUT_WIDTH, self.OUTPUT_HEIGHT)
        self.ctx.disable(moderngl.DEPTH_TEST)
        self.interlace_fbo.clear(0.0, 0.0, 0.0, 1.0)

        # Bind quilt texture for the interlace shader to sample
        self.quilt_texture.use(0)
        self.interlace_program['quiltTex'].value = 0

        # Draw fullscreen quad to run interlacing shader
        self.fullscreen_vao.render(moderngl.TRIANGLE_STRIP)

        # Re-enable depth test for next frame's mesh rendering
        self.ctx.enable(moderngl.DEPTH_TEST)

        if debug_this_frame:
            print(f"  Interlaced to {self.OUTPUT_WIDTH}x{self.OUTPUT_HEIGHT}")
            self._first_render = False

        # Read interlaced output directly into pre-allocated buffer
        # Y-flip is handled in the interlace vertex shader, so readback is already top-down
        if self._readback_buf is None:
            self._readback_buf = bytearray(self.OUTPUT_WIDTH * self.OUTPUT_HEIGHT * 4)
        self.interlace_texture.read_into(self._readback_buf)
        return self._readback_buf
    
    def set_rotation(self, pitch, yaw, roll):
        """Set model rotation in degrees"""
        self.model_rotation = [pitch, yaw, roll]
    
    def rotate(self, delta_pitch, delta_yaw, delta_roll=0):
        """Add to current rotation (orbit angles in degrees)"""
        self.model_rotation[0] = max(-89.0, min(89.0, self.model_rotation[0] + delta_pitch))
        self.model_rotation[1] += delta_yaw
        self.model_rotation[2] += delta_roll
    
    def set_camera_distance(self, distance):
        """Set camera distance from model"""
        self.camera_distance = max(1.0, min(10.0, distance))

    def set_calibration(self, slope, interval, x0):
        """Update lenticular calibration values (per-device)"""
        self.slope = slope
        self.interval = interval
        self.x0 = x0
        self.interlace_program['Slope'].value = slope
        self.interlace_program['Interval'].value = interval
        self.interlace_program['X0'].value = x0
        if self.debug:
            print(f"Calibration updated: slope={slope}, interval={interval}, x0={x0}")

    def set_view_blend(self, value):
        """Set view transition smoothing: 0 = discrete steps, 1 = full blend between adjacent views"""
        self.view_blend = max(0.0, min(1.0, float(value)))
        if self.interlace_program is not None:
            self.interlace_program['ViewBlend'].value = self.view_blend
        if self.debug:
            print(f"View blend: {self.view_blend:.2f}")

    def set_cubic_blend(self, value):
        """Set 4-view Catmull-Rom: 0 = 2-view smoothstep only, 1 = cubic at row boundaries (smoother 'bam')"""
        self.cubic_blend = max(0.0, min(1.0, float(value)))
        if self.interlace_program is not None:
            self.interlace_program['CubicBlend'].value = self.cubic_blend
        if self.debug:
            print(f"Cubic blend: {self.cubic_blend:.2f}")

    def set_view_cone(self, degrees):
        """Set horizontal view cone in degrees (CubeVi theta). Narrower = smoother steps, wider = more parallax."""
        self.view_cone_degrees = max(20.0, min(60.0, float(degrees)))
        if self.debug:
            print(f"View cone: {self.view_cone_degrees}°")

    def set_gamma(self, value):
        """Set output gamma (CubeVi _Gamma). 1.0 = linear."""
        self.gamma = max(0.1, min(3.0, float(value)))
        if self.interlace_program is not None:
            self.interlace_program['Gamma'].value = self.gamma
        if self.debug:
            print(f"Gamma: {self.gamma:.2f}")

    def set_model_color(self, r, g, b):
        """Set model albedo color (0-1 per channel)."""
        self.model_color = (float(r), float(g), float(b))
        if self.program is not None:
            self.program['model_color'].value = self.model_color

    def set_metallic(self, value):
        """Set metallic factor: 0 = plastic, 1 = metal."""
        self.metallic = max(0.0, min(1.0, float(value)))
        if self.program is not None:
            self.program['metallic'].value = self.metallic

    def set_roughness(self, value):
        """Set roughness: 0 = mirror, 1 = matte."""
        self.roughness = max(0.0, min(1.0, float(value)))
        if self.program is not None:
            self.program['roughness'].value = self.roughness

    def set_rim_strength(self, value):
        """Set rim/edge light intensity."""
        self.rim_strength = max(0.0, min(2.0, float(value)))
        if self.program is not None:
            self.program['rim_strength'].value = self.rim_strength

    def set_bg_gradient(self, top, bottom):
        """Set background gradient colors. Each is (r, g, b) 0-1."""
        self.bg_top = tuple(float(c) for c in top)
        self.bg_bottom = tuple(float(c) for c in bottom)

    def set_bg_accent(self, r, g, b):
        """Set the third background color (radial center / studio glow / mid band)."""
        self.bg_accent = (float(r), float(g), float(b))

    def set_bg_mode(self, mode):
        """Set background mode: 0=gradient, 1=radial, 2=studio, 3=three-tone."""
        self.bg_mode = int(mode)

    def set_ao_strength(self, value):
        """Set ambient occlusion (cavity darkening): 0 = off, 1 = full."""
        self.ao_strength = max(0.0, min(1.0, float(value)))
        if self.program is not None:
            self.program['ao_strength'].value = self.ao_strength

    def set_env_reflect(self, value):
        """Set environment reflection: 0 = off, 1 = full."""
        self.env_reflect = max(0.0, min(1.0, float(value)))
        if self.program is not None:
            self.program['env_reflect'].value = self.env_reflect

    def set_light_intensity(self, value):
        """Set master light brightness: 0.5 = dim, 1.0 = default, 2.0 = bright."""
        self.light_intensity = max(0.2, min(2.5, float(value)))
        if self.program is not None:
            self._apply_material_uniforms()

    def set_pan_offset(self, x, y):
        """Set horizontal/vertical pan offset (right-click drag)."""
        self.pan_offset = np.array([float(x), float(y), 0.0])

    def pan(self, dx, dy):
        """Add to current pan offset (world X/Y only)."""
        self.pan_offset[0] += dx
        self.pan_offset[1] += dy

    def pan_3d(self, dx, dy, dz):
        """Add to current pan offset in full 3D world space."""
        self.pan_offset[0] += dx
        self.pan_offset[1] += dy
        self.pan_offset[2] += dz

    def reset_pan(self):
        """Reset pan to center."""
        self.pan_offset = np.array([0.0, 0.0, 0.0])

    def set_calibration_slope(self, value):
        """Set lenticular slope (obliquity) from device calibration."""
        self.slope = float(value)
        if self.interlace_program is not None:
            self.interlace_program['Slope'].value = self.slope

    def set_calibration_interval(self, value):
        """Set lenticular interval (line spacing in subpixels)."""
        self.interval = float(value)
        if self.interlace_program is not None:
            self.interlace_program['Interval'].value = self.interval

    def set_calibration_x0(self, value):
        """Set lenticular X0 (phase offset / deviation). CubeVi shader default 15.4."""
        self.x0 = float(value)
        if self.interlace_program is not None:
            self.interlace_program['X0'].value = self.x0

    def set_num_views(self, n):
        """Set number of views: 8, 16, or 40. Fewer = smoother, fewer row boundaries; more = finer parallax."""
        n = int(n)
        if n == self.num_views:
            return
        if n not in (8, 16, 40):
            n = max(8, min(40, n))
            # snap to 8, 16, or 40
            if n <= 12:
                n = 8
            elif n <= 28:
                n = 16
            else:
                n = 40
        self.num_views = n
        self._update_quilt_dims()
        # Recreate quilt FBO for new size
        self.quilt_texture = self.ctx.texture(
            (self.quilt_width, self.quilt_height), 4
        )
        self.quilt_texture.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.quilt_fbo = self.ctx.framebuffer(
            color_attachments=[self.quilt_texture],
            depth_attachment=self.ctx.depth_renderbuffer(
                (self.quilt_width, self.quilt_height)
            )
        )
        if self.interlace_program is not None:
            self.interlace_program['ImgsCountX'].value = float(self.quilt_cols)
            self.interlace_program['ImgsCountY'].value = float(self.quilt_rows)
            self.interlace_program['ImgsCountAll'].value = float(self.num_views)
        if self.debug:
            print(f"View count: {self.num_views} (quilt {self.quilt_cols}x{self.quilt_rows})")
