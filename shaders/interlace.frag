#version 330

// CubeVi C1 lenticular interlacing shader
// Converts an NxM quilt texture into a lenticular-interlaced image
// Direct port of CubeVi Swizzle shader (MultiView.shader)
//
// The shader maps each physical LCD pixel to the correct view from the quilt.
// RGB subpixels are sampled from (potentially) different views for smooth parallax.

in vec2 fragTexCoord;  // OpenGL UV for quilt tile sampling (bottom-up)
in vec2 lcdCoord;      // Top-down UV for LCD pixel position mapping
out vec4 FragColor;

uniform sampler2D quiltTex;

// Device calibration parameters (from deviceConfig.json or defaults)
uniform float Slope;        // Lenticular obliquity (line angle)
uniform float Interval;     // Lenticular line spacing in subpixels
uniform float X0;           // Phase offset (deviation)

// Quilt layout
uniform float ImgsCountX;   // Columns in quilt (8)
uniform float ImgsCountY;   // Rows in quilt (5)
uniform float ImgsCountAll; // Total views (40)

// Output display resolution
uniform float OutputSizeX;  // C1 LCD width (1440)
uniform float OutputSizeY;  // C1 LCD height (2560)

// View transition smoothing: 0 = discrete views (sharp steps), 1 = blend between adjacent views
uniform float ViewBlend;

// Gamma correction (CubeVi MultiView.shader: _Gamma). 1.0 = linear, 1.8 = typical display
uniform float Gamma;

// 1 = use 4-view Catmull-Rom at row boundaries (smoother derivative, less "bam")
uniform float CubicBlend;

// Determine which view to sample for this physical LCD pixel position.
// lcdPos is top-down (row 0 = top of LCD), matching CubeVi's Unity convention.
float get_choice_float(vec2 lcdPos, float bias) {
    float x = lcdPos.x * OutputSizeX + 0.5;
    float y = lcdPos.y * OutputSizeY + 0.5;
    float x1 = (x + y * Slope) * 3.0 + bias;
    float x_local = mod(x1 + X0, Interval);
    return x_local / Interval;
}

// Map from normalized view choice [0..1] to quilt UV coordinates.
// quiltPos is the within-tile UV (bottom-up, standard OpenGL).
// The column reversal (ImgsCountX - ... - 1) matches CubeVi's quilt convention
// where rightmost column in a row = lowest view index for that row.
vec2 get_uv_from_choice(vec2 quiltPos, float choice_float) {
    float choice = floor(choice_float * ImgsCountAll);
    choice = clamp(choice, 0.0, ImgsCountAll - 1.0);
    vec2 choice_vec = vec2(
        ImgsCountX - mod(choice, ImgsCountX) - 1.0,
        floor(choice / ImgsCountX)
    );
    vec2 reciprocals = vec2(1.0 / ImgsCountX, 1.0 / ImgsCountY);
    return (choice_vec + quiltPos) * reciprocals;
}

// Sample the quilt with a specific subpixel bias (single discrete view)
vec4 get_color(vec2 lcdPos, vec2 quiltPos, float bias) {
    float choice_float = get_choice_float(lcdPos, bias);
    vec2 sel_pos = get_uv_from_choice(quiltPos, choice_float);
    return texture(quiltTex, sel_pos);
}

// Catmull-Rom weights for t in [0,1] (smooth C1 at 0 and 1)
void catmull_rom_weights(float t, out float w0, out float w1, out float w2, out float w3) {
    float t2 = t * t;
    float t3 = t2 * t;
    w0 = -0.5*t + t2 - 0.5*t3;
    w1 = 1.0 - 2.5*t2 + 1.5*t3;
    w2 = 0.5*t + 2.0*t2 - 1.5*t3;
    w3 = -0.5*t2 + 0.5*t3;
}

// Sample one view by index (0..39)
vec4 sample_view(vec2 quiltPos, float view_index) {
    float choice_float = (view_index + 0.5) / ImgsCountAll;
    vec2 uv = get_uv_from_choice(quiltPos, choice_float);
    return texture(quiltTex, uv);
}

// 4-view Catmull-Rom: smooth first derivative at row boundaries (7->8, 15->16, ...)
vec4 get_color_cubic(vec2 lcdPos, vec2 quiltPos, float bias) {
    float choice_float = get_choice_float(lcdPos, bias);
    float v_cont = choice_float * ImgsCountAll;
    float v1 = floor(clamp(v_cont, 0.0, ImgsCountAll - 1.0));
    float t = fract(v_cont);

    float v0 = max(0.0, v1 - 1.0);
    float v2 = min(ImgsCountAll - 1.0, v1 + 1.0);
    float v3 = min(ImgsCountAll - 1.0, v1 + 2.0);

    float w0, w1, w2, w3;
    catmull_rom_weights(t, w0, w1, w2, w3);

    vec4 c0 = sample_view(quiltPos, v0);
    vec4 c1 = sample_view(quiltPos, v1);
    vec4 c2 = sample_view(quiltPos, v2);
    vec4 c3 = sample_view(quiltPos, v3);
    return w0*c0 + w1*c1 + w2*c2 + w3*c3;
}

// 2-view blend with optional smoothstep (ease-in-out) so transition isn't linear
vec4 get_color_blended(vec2 lcdPos, vec2 quiltPos, float bias) {
    float choice_float = get_choice_float(lcdPos, bias);
    float v_cont = choice_float * ImgsCountAll;
    float v0 = floor(clamp(v_cont, 0.0, ImgsCountAll - 1.0));
    float v1 = min(v0 + 1.0, ImgsCountAll - 1.0);
    float t_raw = fract(v_cont);
    // Ease-in-out: gentler at 0 and 1, so less "snap" at the boundary
    float t = smoothstep(0.15, 0.85, t_raw) * ViewBlend;

    vec2 uv0 = get_uv_from_choice(quiltPos, (v0 + 0.5) / ImgsCountAll);
    vec2 uv1 = get_uv_from_choice(quiltPos, (v1 + 0.5) / ImgsCountAll);
    vec4 c0 = texture(quiltTex, uv0);
    vec4 c1 = texture(quiltTex, uv1);
    return mix(c0, c1, t);
}

void main() {
    vec2 lp = lcdCoord;
    vec2 qp = fragTexCoord;

    // 2-view (smoothstep) or 4-view Catmull-Rom; mix only when CubicBlend in (0.01, 0.99)
    vec4 color;
    if (CubicBlend < 0.01) {
        color = get_color_blended(lp, qp, 0.0);
        color.g = get_color_blended(lp, qp, 1.0).g;
        color.b = get_color_blended(lp, qp, 2.0).b;
    } else if (CubicBlend > 0.99) {
        color = get_color_cubic(lp, qp, 0.0);
        color.g = get_color_cubic(lp, qp, 1.0).g;
        color.b = get_color_cubic(lp, qp, 2.0).b;
    } else {
        vec4 cb = get_color_blended(lp, qp, 0.0);
        vec4 cc = get_color_cubic(lp, qp, 0.0);
        color = mix(cb, cc, CubicBlend);
        cb = get_color_blended(lp, qp, 1.0);
        cc = get_color_cubic(lp, qp, 1.0);
        color.g = mix(cb.g, cc.g, CubicBlend);
        cb = get_color_blended(lp, qp, 2.0);
        cc = get_color_cubic(lp, qp, 2.0);
        color.b = mix(cb.b, cc.b, CubicBlend);
    }
    color.a = 1.0;
    // Gamma correction (CubeVi MultiView.shader)
    float g = max(Gamma, 0.01);
    FragColor = vec4(pow(color.rgb, vec3(1.0 / g)), color.a);
}
