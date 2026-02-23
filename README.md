# CubeVi C1 STL/3MF Viewer

A real-time lightfield viewer for the [CubeVi C1](https://cubevi.com) glasses-free 3D display. Load STL or 3MF models, rotate and inspect them, and see them in full parallax 3D on the C1's lenticular screen.

## How It Works

The viewer renders your 3D model from multiple camera angles simultaneously (8, 16, or 40 views), arranges them into a **quilt** (a tiled grid of views), then runs a **lenticular interlacing shader** that maps each physical LCD subpixel to the correct view. The C1's lenticular lens array then directs each view to a different angle, creating a glasses-free 3D image with smooth horizontal parallax.

```
STL/3MF Model
     |
     v
[Multi-view Quilt Render]  --  N cameras with off-axis projection
     |                         (no toe-in, asymmetric frustum shift)
     v
[Lenticular Interlacing]   --  Per-subpixel view selection (RGB separation)
     |                         using CubeVi's calibration parameters
     v
[1440x2560 Output]         --  Pixel-perfect fullscreen on C1 display
```

### Rendering Pipeline

1. **Pass 1 -- Quilt render**: For each of the N views, the mesh is rendered into a tile of the quilt framebuffer. Each camera is laterally offset along the view cone and uses an off-axis (asymmetric frustum) projection so all views converge at the focal plane. This matches CubeVi's official approach -- no toe-in rotation.

2. **Pass 2 -- Interlace**: A fullscreen shader samples the quilt texture per-output-pixel. Each RGB subpixel can come from a different view (subpixel interlacing with bias offsets 0, 1, 2), controlled by the device's calibration parameters (slope, interval, x0). Optional smoothstep blending and Catmull-Rom cubic interpolation reduce view-transition artifacts.

### Calibration

Each CubeVi C1 unit has unique calibration values stored in an encrypted `deviceConfig.json` file written by CubeStage. The viewer automatically locates and decrypts this file on startup. If CubeStage isn't installed, it falls back to reasonable defaults. You can also fine-tune calibration live via the Lenticular Calibration panel.

## Features

- **Drag-and-drop** STL and 3MF file loading
- **3MF vertex colors** -- per-face and per-vertex colors are extracted and rendered
- **Orbit camera** with Fusion 360-style controls (left-drag = orbit, right-drag = pan, scroll = zoom)
- **Smooth inertia** on rotation and pan with configurable friction
- **PBR-lite shading** -- 3-point studio lighting, Blinn-Phong specular, metallic/roughness, rim light
- **Fake ambient occlusion** -- cavity darkening without a separate AO pass
- **Fake environment reflection** -- sky-ground gradient reflection with Fresnel
- **Adjustable brightness** -- master light intensity slider
- **4 background modes** -- gradient, spotlight, studio floor, three-tone
- **8 color presets** and **5 material presets** (matte plastic, glossy, brushed metal, chrome, clay)
- **Live calibration tuning** -- slope, interval, x0 sliders
- **View count selection** -- 8, 16, or 40 views (fewer = smoother transitions, more = finer parallax)
- **View blending** -- smoothstep + optional 4-view Catmull-Rom at row boundaries
- **Auto C1 detection** -- finds the C1 display by resolution (1440x2560) and physical size
- **Settings persistence** -- all UI state saves to `cubevi_settings.json` on exit
- **Screenshot export** -- press S to save the interlaced output as PNG
- **Console-free launcher** -- `cubevi_viewer.pyw` runs without a terminal window
- **File logging** -- all debug output goes to `cubevi_viewer.log`

## Requirements

- **Python 3.8+**
- **Windows** (tested on Windows 10/11; CubeStage config paths are Windows-specific)
- **GPU with OpenGL 3.3+** support
- **CubeVi C1** display connected as a secondary monitor (optional -- works in windowed mode without it)

### Python Dependencies

```
pip install -r requirements.txt
```

| Package | Purpose |
|---------|---------|
| PyQt5 | UI framework, dual-window architecture |
| moderngl | OpenGL 3.3 rendering (standalone context, no window required) |
| numpy | Array math, vertex buffer construction |
| numpy-stl | STL file parsing |
| pyrr | Matrix/vector math (look-at, projection) |
| trimesh | 3MF file loading with vertex color extraction |
| lxml | XML parsing for 3MF (trimesh dependency) |
| pycryptodome | AES-256-CBC decryption of CubeVi device config |

## Usage

### Run with console output
```bash
python main.py
```

### Run without console (recommended for daily use)
```bash
pythonw cubevi_viewer.pyw
```
All output goes to `cubevi_viewer.log` in the project directory.

### Controls

| Input | Action |
|-------|--------|
| **Left drag** | Orbit (rotate around model) |
| **Right drag** | Pan (move the pivot point) |
| **Scroll wheel** | Zoom in/out |
| **Shift + drag** | Precision mode (slower movement) |
| **Middle-click** | Reset view (rotation + pan + zoom) |
| **R** | Reset view |
| **S** | Save interlaced output as `cubevi_output.png` |
| **F** | Toggle C1 fullscreen output |
| **Esc** | Quit (saves settings) |

### Loading Models

- **Drag and drop** an `.stl` or `.3mf` file onto the preview area
- Or click **Open File** in the title bar
- 3MF files with per-face or per-vertex colors will render in color automatically

## Project Structure

```
cubevi_stl_viewer/
  main.py               -- Application entry point, UI, mouse/keyboard handling
  renderer.py           -- ModernGL rendering pipeline (quilt + interlace)
  camera.py             -- Orbit camera system with off-axis projection
  device_config.py      -- CubeVi C1 calibration loader (AES decryption)
  settings.py           -- JSON settings persistence
  log.py                -- File logging setup
  cubevi_viewer.pyw     -- Console-free launcher
  requirements.txt      -- Python dependencies
  .gitignore
  shaders/
    mesh.vert           -- Vertex shader (position, normal, vertex color)
    mesh.frag           -- PBR-lite fragment shader (3-point light, AO, env reflect)
    bg.vert             -- Background quad vertex shader
    bg.frag             -- Multi-mode background (gradient, spotlight, studio, three-tone)
    interlace.vert      -- Interlace pass vertex shader (Y-flip for zero-cost readback)
    interlace.frag      -- CubeVi lenticular interlacing (subpixel view selection)
```

## Architecture Notes

### Dual-Window Design

The app uses two windows:
- **Control window** (primary monitor) -- all UI controls, scaled preview of the interlaced output, drag-and-drop
- **Output window** (C1 display) -- frameless, borderless, pixel-perfect 1440x2560 interlaced image

The output window auto-detects the C1 by matching screen resolution (1440x2560) and physical size (<200mm). If no C1 is found, it opens in a resizable window for development.

### Performance

The render path is optimized to maintain 60 FPS even at 40 views:

- **Pre-allocated GPU readback buffer** -- reuses a single `bytearray` for `texture.read_into()`, avoiding per-frame allocation of 14.7 MB
- **Y-flip in interlace vertex shader** -- flipping `gl_Position.y` produces top-down row order in the readback buffer, eliminating a CPU-side `np.flipud` on the 14.7 MB image
- **Camera position caching** -- view/projection matrices are only recalculated when orbit parameters actually change (keyed on distance, cone, views, pan, rotation)
- **Pre-converted matrix bytes** -- camera matrices are converted to `f4` bytes at cache time, not per-tile
- **No PIL in render path** -- raw `bytearray` goes directly to `QImage` via `Format_RGBA8888`
- **FastTransformation** for preview downscale (bilinear instead of bicubic)
- **Scissor test** for per-tile FBO clearing without clearing the entire quilt

### Interlacing Shader

The interlacing shader is based on CubeVi's official Swizzle shader from their Unity plugin. Key details:

- Each output pixel's RGB channels can sample from different views (subpixel separation with bias offsets 0, 1, 2)
- View selection uses the calibration formula: `(x + y * Slope) * 3 + bias`, modulo `Interval`, divided by `Interval`
- The shader uses **dual UV coordinates**: `lcdCoord` (top-down, for LCD pixel position mapping) and `fragTexCoord` (bottom-up, for quilt tile sampling). This was necessary because the Y-flip optimization reversed which physical row each fragment maps to.
- Optional smoothstep blending between adjacent views reduces harsh transitions
- Optional 4-view Catmull-Rom cubic interpolation at quilt row boundaries reduces the "bam" effect

### Off-Axis Projection

All views use the same forward direction (no toe-in). Each camera is translated laterally, and the projection frustum is shifted by an equal-and-opposite amount so all views converge at the focal plane (the pivot point). This is critical for lenticular displays -- toe-in causes incorrect depth plane alignment.

## CubeVi C1 Specs

| Parameter | Value |
|-----------|-------|
| Native LCD | 1440 x 2560 (portrait) |
| Views | 40 (8 x 5 quilt grid) |
| Tile size | 540 x 960 per view |
| Quilt size | 4320 x 4800 (at 40 views) |
| View cone | ~40 degrees horizontal |
| Lenticular type | Slanted barrier with RGB subpixel interlacing |

## Troubleshooting

### "CubeVi C1 not detected"
The app looks for a 1440x2560 screen. Make sure:
- The C1 is connected and recognized by Windows as a display
- It's set to its native 1440x2560 resolution
- Display scaling is set to 100% for the C1
- Press **F** to manually toggle the output window

### The preview looks like garbled stripes
This is normal. The preview shows the **interlaced** image, which only looks correct through the C1's lenticular lens. On a regular monitor, it will appear as colorful moire/stripes.

### Model looks dark
Use the **Brightness** slider in the Material section to increase the master light intensity. The default is 1.0; try 1.3-1.5 for a brighter look.

### 3MF colors not showing
Make sure `trimesh` and `lxml` are installed. The 3MF must contain per-face or per-vertex color data in its material properties. Not all 3MF exporters include color information.

### Calibration looks wrong
Each C1 unit has unique calibration values. If the 3D effect looks misaligned:
1. Install [CubeStage](https://cubevi.com) -- it writes the correct calibration for your unit
2. Or manually tune the **Slope**, **Interval**, and **X0** sliders in the Lenticular Calibration section until the views align cleanly

### No device config decryption
Install `pycryptodome`:
```bash
pip install pycryptodome
```
Without it, the app falls back to default calibration values which may not match your specific unit.

## License

This project is for personal/educational use with the CubeVi C1 display. The interlacing shader is derived from CubeVi's open-source Unity plugin.
