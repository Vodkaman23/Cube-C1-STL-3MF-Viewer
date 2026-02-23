#version 330

// PBR-lite fragment shader: 3-point lighting + specular + rim + AO + env reflection
// Enhanced for lenticular displays where lighting and subtle effects sell the 3D.

in vec3 frag_normal;
in vec3 frag_position;
in vec3 frag_eye_pos;
in vec3 frag_color;     // per-vertex color from 3MF (or white if none)

out vec4 out_color;

// Material
uniform vec3 model_color;      // base albedo (uniform color)
uniform float has_vertex_colors; // 0 = use model_color, 1 = use vertex colors
uniform float metallic;        // 0 = plastic, 1 = metal
uniform float roughness;       // 0 = mirror, 1 = matte

// Lights (key, fill, back)
uniform vec3 light_dir_key;
uniform vec3 light_color_key;
uniform vec3 light_dir_fill;
uniform vec3 light_color_fill;
uniform vec3 light_dir_back;
uniform vec3 light_color_back;

// Environment
uniform vec3 ambient_color;
uniform float rim_strength;     // 0 = no rim, 1 = strong edge glow

// Effects
uniform float ao_strength;      // 0 = no AO, 1 = full cavity darkening
uniform float env_reflect;      // 0 = no reflection, 1 = full environment reflection

// Blinn-Phong specular with roughness-derived exponent
vec3 calc_light(vec3 L, vec3 light_col, vec3 N, vec3 V, vec3 albedo) {
    float NdotL = max(dot(N, L), 0.0);

    // Diffuse
    vec3 diffuse = NdotL * light_col * albedo;

    // Specular (Blinn-Phong)
    vec3 H = normalize(L + V);
    float NdotH = max(dot(N, H), 0.0);
    float spec_power = mix(256.0, 4.0, roughness * roughness);
    float spec = pow(NdotH, spec_power) * NdotL;

    // Metallic: specular takes albedo color; plastic: specular is white
    vec3 spec_color = mix(vec3(0.04), albedo, metallic);
    vec3 specular = spec * light_col * spec_color;

    // Energy conservation: reduce diffuse as metallic increases
    diffuse *= (1.0 - metallic * 0.7);

    return diffuse + specular;
}

// Simple fake AO from normal curvature (cavity darkening)
// Uses the dot between normal and up/down to darken crevices
float fake_ao(vec3 N, vec3 V) {
    float NdotV = max(dot(N, V), 0.0);
    // Cavity: dark where normal faces away from view (undercuts, grooves)
    float cavity = pow(NdotV, 0.5);
    // Soften so it's not too harsh
    return mix(1.0, cavity, ao_strength * 0.6);
}

// Fake environment reflection (cubemap-less: uses normal direction for color)
vec3 fake_env_reflect(vec3 N, vec3 V, vec3 albedo) {
    vec3 R = reflect(-V, N);

    // Simple sky-ground gradient based on reflection direction
    float sky = R.y * 0.5 + 0.5;  // 0 = ground, 1 = sky
    vec3 sky_color = vec3(0.35, 0.45, 0.65);
    vec3 ground_color = vec3(0.15, 0.12, 0.10);
    vec3 env_color = mix(ground_color, sky_color, smoothstep(0.3, 0.7, sky));

    // Fresnel: more reflection at glancing angles
    float fresnel = pow(1.0 - max(dot(N, V), 0.0), 4.0);
    float reflect_amount = mix(0.04, 1.0, fresnel) * env_reflect;

    // Metallic surfaces reflect with albedo tint
    vec3 reflect_tint = mix(vec3(1.0), albedo, metallic);
    return env_color * reflect_tint * reflect_amount;
}

void main() {
    vec3 N = normalize(frag_normal);
    vec3 V = normalize(frag_eye_pos - frag_position);

    // Flip normal if back-facing (two-sided lighting for thin STL walls)
    if (!gl_FrontFacing) N = -N;

    // Use vertex color from 3MF if available, otherwise uniform model_color
    vec3 albedo = mix(model_color, frag_color, has_vertex_colors);

    // 3-point lighting
    vec3 color = vec3(0.0);
    color += calc_light(normalize(light_dir_key),  light_color_key,  N, V, albedo);
    color += calc_light(normalize(light_dir_fill), light_color_fill, N, V, albedo);
    color += calc_light(normalize(light_dir_back), light_color_back, N, V, albedo);

    // Ambient / hemisphere
    float hemi = 0.5 + 0.5 * N.y;
    color += ambient_color * albedo * hemi;

    // Fake AO (cavity darkening)
    color *= fake_ao(N, V);

    // Environment reflection
    color += fake_env_reflect(N, V, albedo);

    // Rim light (Fresnel-ish edge glow -- really sells the 3D on lenticular)
    float rim = 1.0 - max(dot(N, V), 0.0);
    rim = pow(rim, 3.0) * rim_strength;
    color += rim * light_color_key * 0.5;

    // Tone mapping (Reinhard) to avoid blown-out highlights
    color = color / (color + vec3(1.0));

    out_color = vec4(color, 1.0);
}
