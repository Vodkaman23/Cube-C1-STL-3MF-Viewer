#version 330

// Multi-mode background shader
// mode 0: vertical gradient
// mode 1: radial gradient (spotlight / vignette)
// mode 2: studio floor (gradient + horizon line + soft shadow)
// mode 3: three-tone (top, mid, bottom with soft transitions)

in vec2 v_uv;
out vec4 out_color;

uniform vec3 bg_top;
uniform vec3 bg_bottom;
uniform vec3 bg_accent;     // third color for radial center / studio floor / mid band
uniform int bg_mode;        // 0=gradient, 1=radial, 2=studio, 3=three-tone

void main() {
    vec3 color;

    if (bg_mode == 1) {
        // Radial: bright center spot, dark edges (spotlight effect)
        vec2 center = vec2(0.5, 0.45);
        float d = length(v_uv - center);
        float t = smoothstep(0.0, 0.7, d);
        color = mix(bg_accent, bg_bottom, t);
    }
    else if (bg_mode == 2) {
        // Studio floor: upper dark gradient, horizon glow, lower floor
        float horizon = 0.35;
        float glow_width = 0.06;
        if (v_uv.y > horizon + glow_width) {
            // Sky/wall region
            float t = smoothstep(horizon + glow_width, 1.0, v_uv.y);
            color = mix(bg_accent, bg_top, t);
        } else if (v_uv.y > horizon - glow_width) {
            // Horizon glow band
            float t = (v_uv.y - (horizon - glow_width)) / (2.0 * glow_width);
            t = smoothstep(0.0, 1.0, t);
            color = mix(bg_bottom, bg_accent, t);
        } else {
            // Floor region with distance fade
            float t = v_uv.y / (horizon - glow_width);
            t = smoothstep(0.0, 1.0, t);
            color = mix(bg_bottom * 0.7, bg_bottom, t);
        }
    }
    else if (bg_mode == 3) {
        // Three-tone: bottom -> accent (mid) -> top
        float t_low = smoothstep(0.0, 0.45, v_uv.y);
        float t_high = smoothstep(0.45, 1.0, v_uv.y);
        color = mix(bg_bottom, bg_accent, t_low);
        color = mix(color, bg_top, t_high);
    }
    else {
        // Default: simple vertical gradient with slight ease
        float t = smoothstep(0.0, 1.0, v_uv.y);
        color = mix(bg_bottom, bg_top, t);
    }

    out_color = vec4(color, 1.0);
}
